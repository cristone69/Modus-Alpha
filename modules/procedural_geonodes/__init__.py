bl_info = {
    "name": "Map to Faces",
    "author": "Cris",
    "version": (0, 15, 1),
    "blender": (5, 0, 0),
    "location": "View3D > Shift+Q > Map to Faces",
    "description": "Map a component across quad, triangle, or fan surface cells with optional surface subdivision",
    "category": "Object",
}

import bpy
from bpy.types import Operator


GROUP_NAME = "Map to Faces"
ATTR_CORNER_VERTS = tuple(f"mtf_internal_corner_vertex_{i}" for i in range(4))
ATTR_VERTEX_SCALE = "mtf_internal_vertex_scale"
ATTR_U = "mtf_internal_u"
ATTR_V = "mtf_internal_v"
ATTR_Z = "mtf_internal_z"
ATTR_GUIDE_NORMAL = "mtf_internal_guide_normal"

def _socket(collection, name, fallback=None):
    for socket in collection:
        if socket.name == name:
            return socket
    if fallback is not None and len(collection) > fallback:
        return collection[fallback]
    raise KeyError(f"Socket not found: {name}")

def _new_interface_socket(group, name, in_out, socket_type, parent=None):
    kwargs = {"name": name, "in_out": in_out, "socket_type": socket_type}
    if parent is not None:
        kwargs["parent"] = parent
    socket = group.interface.new_socket(**kwargs)
    # Blender 5.x may infer field/attribute behavior from downstream links.
    # Modifier controls in this extension must remain ordinary editable values.
    if in_out == "INPUT" and socket_type != "NodeSocketGeometry":
        if hasattr(socket, "hide_in_modifier"):
            socket.hide_in_modifier = False
        if hasattr(socket, "hide_value"):
            socket.hide_value = False
        if hasattr(socket, "force_non_field"):
            socket.force_non_field = True
    return socket

def _base_group(name, component, target_material=None):
    group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    if hasattr(group, "is_modifier"):
        group.is_modifier = True
    group.description = "Map to Faces modifier graph. Open the nested groups to inspect each conceptual stage."

    _new_interface_socket(group, "Geometry", "INPUT", "NodeSocketGeometry")

    # Keep smoothing as the first visible modifier control because it changes
    # the guide surface used by every later mapping stage.
    smooth_level = _new_interface_socket(group, "Surface Smooth Level", "INPUT", "NodeSocketInt")
    smooth_level.default_value = 0
    smooth_level.min_value = 0
    smooth_level.max_value = 6
    smooth_level.description = "Smooth the deformation guide while preserving one component per mapping cell."

    component_panel = group.interface.new_panel(name="Component")
    component_socket = _new_interface_socket(group, "Component", "INPUT", "NodeSocketObject", component_panel)
    component_socket.default_value = component
    component_socket.description = "Component object mapped across the target cells."

    mapping_shape = _new_interface_socket(group, "Mapping Shape", "INPUT", "NodeSocketMenu", component_panel)
    mapping_shape.description = "Choose Quad, Triangle, or Fan mapping."

    subdivision = _new_interface_socket(group, "Subdivision Level", "INPUT", "NodeSocketInt", component_panel)
    subdivision.default_value = 0
    subdivision.min_value = 0
    subdivision.max_value = 6
    subdivision.description = "Subdivide the target into more mapping cells before component placement."

    mapping_panel = group.interface.new_panel(name="Mapping")
    coverage = _new_interface_socket(group, "Coverage", "INPUT", "NodeSocketFloat", mapping_panel)
    coverage.default_value = 1.0
    coverage.min_value = 0.0
    coverage.max_value = 10.0
    coverage.description = "Scale the mapped component inward or outward around each cell center."

    thickness = _new_interface_socket(group, "Thickness", "INPUT", "NodeSocketFloat", mapping_panel)
    thickness.default_value = 1.0
    thickness.min_value = -10.0
    thickness.max_value = 10.0
    thickness.description = "Scale the component's local Z depth after surface mapping."

    offset = _new_interface_socket(group, "Normal Offset", "INPUT", "NodeSocketFloat", mapping_panel)
    offset.default_value = 0.0
    offset.description = "Move the mapped result along the interpolated surface normal."

    output_panel = group.interface.new_panel(name="Output")
    use_material_override = _new_interface_socket(group, "Use Material Override", "INPUT", "NodeSocketBool", output_panel)
    use_material_override.default_value = False
    use_material_override.description = "Replace all component materials with the Override Material."

    override_material = _new_interface_socket(group, "Override Material", "INPUT", "NodeSocketMaterial", output_panel)
    override_material.default_value = None
    override_material.description = "Material assigned when Use Material Override is enabled."

    merge_mesh = _new_interface_socket(group, "Merge Mesh", "INPUT", "NodeSocketBool", output_panel)
    merge_mesh.default_value = False
    merge_mesh.description = "Weld nearby vertices. This may reduce viewport performance."

    merge_distance = _new_interface_socket(group, "Merge Distance", "INPUT", "NodeSocketFloat", output_panel)
    merge_distance.default_value = 0.0001
    merge_distance.min_value = 0.0
    merge_distance.max_value = 1.0
    merge_distance.description = "Maximum distance used to weld overlapping vertices when Merge Mesh is enabled."

    transfer_normals = _new_interface_socket(group, "Transfer Surface Normals", "INPUT", "NodeSocketBool", output_panel)
    transfer_normals.default_value = False
    transfer_normals.description = "Copy target-surface normals onto mapped pieces to hide shading seams."

    _new_interface_socket(group, "Geometry", "OUTPUT", "NodeSocketGeometry")

    nodes = group.nodes
    group_input = nodes.new("NodeGroupInput")
    group_input.location = (-1450, 80)
    group_input.label = "Modifier Inputs"
    group_input.width = 220

    group_output = nodes.new("NodeGroupOutput")
    group_output.location = (800, 80)
    group_output.label = "Mapped Geometry"
    group_output.width = 190

    return group, group_input, group_output

def _connect_or_set(links, socket, value):
    if hasattr(value, "is_output"):
        links.new(value, socket)
    else:
        socket.default_value = value

def _math(nodes, links, operation, a, b=None, location=(0, 0), label=""):
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    node.location = location
    node.label = label
    _connect_or_set(links, node.inputs[0], a)
    if b is not None:
        _connect_or_set(links, node.inputs[1], b)
    return _socket(node.outputs, "Value", 0)

def _vector_math(nodes, links, operation, a, b=None, location=(0, 0), label=""):
    node = nodes.new("ShaderNodeVectorMath")
    node.operation = operation
    node.location = location
    node.label = label
    _connect_or_set(links, node.inputs[0], a)
    if b is not None:
        _connect_or_set(links, node.inputs[1], b)
    return _socket(node.outputs, "Vector", 0)

def _vector_scale(nodes, links, vector, scale, location=(0, 0), label=""):
    node = nodes.new("ShaderNodeVectorMath")
    node.operation = "SCALE"
    node.location = location
    node.label = label
    links.new(vector, _socket(node.inputs, "Vector", 0))
    _connect_or_set(links, _socket(node.inputs, "Scale", 3), scale)
    return _socket(node.outputs, "Vector", 0)

def _vector_distance(nodes, links, a, b, location=(0, 0), label=""):
    node = nodes.new("ShaderNodeVectorMath")
    node.operation = "DISTANCE"
    node.location = location
    node.label = label
    links.new(a, node.inputs[0])
    links.new(b, node.inputs[1])
    return _socket(node.outputs, "Value", 1)

def _active_typed_socket(collection, name, socket_type):
    """Find the active socket on nodes with duplicate hidden socket names."""
    matches = [socket for socket in collection if socket.name == name and socket.type == socket_type]
    for socket in matches:
        if not getattr(socket, "hide", False):
            return socket
    if matches:
        return matches[-1]
    raise KeyError(f"Active {socket_type} socket not found: {name}")

def _mix_vector(nodes, links, a, b, factor, location=(0, 0), label=""):
    node = nodes.new("ShaderNodeMix")
    node.data_type = "VECTOR"
    node.factor_mode = "UNIFORM"
    node.blend_type = "MIX"
    node.clamp_factor = False
    node.clamp_result = False
    node.location = location
    node.label = label
    _connect_or_set(links, _active_typed_socket(node.inputs, "Factor", "VALUE"), factor)
    _connect_or_set(links, _active_typed_socket(node.inputs, "A", "VECTOR"), a)
    _connect_or_set(links, _active_typed_socket(node.inputs, "B", "VECTOR"), b)
    return _active_typed_socket(node.outputs, "Result", "VECTOR")

def _mix_float(nodes, links, a, b, factor, location=(0, 0), label=""):
    node = nodes.new("ShaderNodeMix")
    node.data_type = "FLOAT"
    node.factor_mode = "UNIFORM"
    node.blend_type = "MIX"
    node.clamp_factor = False
    node.clamp_result = False
    node.location = location
    node.label = label
    _connect_or_set(links, _active_typed_socket(node.inputs, "Factor", "VALUE"), factor)
    float_inputs = [socket for socket in node.inputs if socket.name in {"A", "B"} and socket.type == "VALUE" and not getattr(socket, "hide", False)]
    if len(float_inputs) < 2:
        float_inputs = [socket for socket in node.inputs if socket.name in {"A", "B"} and socket.type == "VALUE"][-2:]
    a_socket = next((socket for socket in float_inputs if socket.name == "A"), None)
    b_socket = next((socket for socket in float_inputs if socket.name == "B"), None)
    if a_socket is None or b_socket is None:
        raise KeyError("Active float A/B sockets not found on Mix node")
    _connect_or_set(links, a_socket, a)
    _connect_or_set(links, b_socket, b)
    return _active_typed_socket(node.outputs, "Result", "VALUE")

def _named_attribute(nodes, data_type, name, location=(0, 0), label=""):
    node = nodes.new("GeometryNodeInputNamedAttribute")
    node.data_type = data_type
    node.location = location
    node.label = label or name
    _socket(node.inputs, "Name").default_value = name
    return _socket(node.outputs, "Attribute", 0)

def _store_named_attribute(nodes, links, geometry, data_type, domain, name, value, location=(0, 0), label=""):
    node = nodes.new("GeometryNodeStoreNamedAttribute")
    node.data_type = data_type
    node.domain = domain
    node.location = location
    node.label = label or name
    links.new(geometry, _socket(node.inputs, "Geometry"))
    _socket(node.inputs, "Name").default_value = name
    _connect_or_set(links, _socket(node.inputs, "Value"), value)
    return _socket(node.outputs, "Geometry")

def _sample_vector(nodes, links, geometry, field, index, location=(0, 0), label=""):
    node = nodes.new("GeometryNodeSampleIndex")
    node.data_type = "FLOAT_VECTOR"
    node.domain = "POINT"
    if hasattr(node, "clamp"):
        node.clamp = False
    node.location = location
    node.label = label
    links.new(geometry, _socket(node.inputs, "Geometry"))
    links.new(field, _socket(node.inputs, "Value"))
    links.new(index, _socket(node.inputs, "Index"))
    return _socket(node.outputs, "Value", 0)

def _sample_float(nodes, links, geometry, field, index, location=(0, 0), label=""):
    node = nodes.new("GeometryNodeSampleIndex")
    node.data_type = "FLOAT"
    node.domain = "POINT"
    if hasattr(node, "clamp"):
        node.clamp = False
    node.location = location
    node.label = label
    links.new(geometry, _socket(node.inputs, "Geometry"))
    links.new(field, _socket(node.inputs, "Value"))
    links.new(index, _socket(node.inputs, "Index"))
    return _socket(node.outputs, "Value", 0)

def _build_face_metadata(nodes, links, geometry_socket, x=-1950, y=450):
    """Store quad-corner indices and Tissue-style shared vertex scale."""
    index = nodes.new("GeometryNodeInputIndex")
    index.location = (x, y + 300)

    face_area = nodes.new("GeometryNodeInputMeshFaceArea")
    face_area.location = (x, y + 120)
    face_area.label = "Target Face Area"

    # Tissue averages the areas of all faces connected to each generator
    # vertex. Converting Face Area to the Point domain performs the same
    # topology-aware average in Geometry Nodes.
    area_on_points = nodes.new("GeometryNodeFieldOnDomain")
    area_on_points.data_type = "FLOAT"
    area_on_points.domain = "POINT"
    area_on_points.location = (x + 220, y + 120)
    area_on_points.label = "Average Adjacent Face Area"
    links.new(_socket(face_area.outputs, "Area", 0), _socket(area_on_points.inputs, "Value", 0))

    vertex_scale = _math(
        nodes, links, "SQRT", _socket(area_on_points.outputs, "Value", 0), None,
        (x + 440, y + 120), "Shared Vertex Scale"
    )

    geometry = _store_named_attribute(
        nodes, links, geometry_socket, "FLOAT", "POINT", ATTR_VERTEX_SCALE,
        vertex_scale, (x + 660, y + 120), "Store Shared Vertex Scale"
    )

    total_socket = None
    for corner_number, attr_name in enumerate(ATTR_CORNER_VERTS):
        corner = nodes.new("GeometryNodeCornersOfFace")
        corner.location = (x + 20, y - 170 - corner_number * 180)
        corner.label = f"Face Corner {corner_number}"
        links.new(_socket(index.outputs, "Index"), _socket(corner.inputs, "Face Index"))
        _socket(corner.inputs, "Sort Index").default_value = corner_number
        if total_socket is None:
            total_socket = _socket(corner.outputs, "Total")

        vertex = nodes.new("GeometryNodeVertexOfCorner")
        vertex.location = (x + 250, y - 170 - corner_number * 180)
        vertex.label = f"Corner {corner_number} Vertex"
        links.new(_socket(corner.outputs, "Corner Index"), _socket(vertex.inputs, "Corner Index"))

        geometry = _store_named_attribute(
            nodes, links, geometry, "INT", "FACE", attr_name,
            _socket(vertex.outputs, "Vertex Index"),
            (x + 880 + corner_number * 210, y - 170 - corner_number * 180),
            f"Store Corner {corner_number} Vertex"
        )

    return geometry, total_socket

def _quad_face_points(nodes, links, target_geometry, total_socket, location=(-900, 200)):
    compare = nodes.new("ShaderNodeMath")
    compare.operation = "COMPARE"
    compare.location = (location[0] - 230, location[1] - 180)
    compare.label = "Quad Faces Only"
    links.new(total_socket, compare.inputs[0])
    compare.inputs[1].default_value = 4.0
    compare.inputs[2].default_value = 0.1

    mesh_to_points = nodes.new("GeometryNodeMeshToPoints")
    mesh_to_points.location = location
    mesh_to_points.label = "One Point per Quad"
    if hasattr(mesh_to_points, "mode"):
        mesh_to_points.mode = "FACES"
    links.new(target_geometry, _socket(mesh_to_points.inputs, "Mesh"))
    links.new(_socket(compare.outputs, "Value", 0), _socket(mesh_to_points.inputs, "Selection"))
    return _socket(mesh_to_points.outputs, "Points")

def _prepare_component_no_pretransform(nodes, links, component_socket, subdivision_socket=None, location=(-1800, 900)):
    """Store normalized component coordinates without moving the component first.

    The final mapping Set Position replaces every realized vertex position, so
    the intermediate normalized component position is theoretically redundant.
    """
    object_info = nodes.new("GeometryNodeObjectInfo")
    object_info.location = location
    object_info.label = "Exposed Component"
    object_info.transform_space = "ORIGINAL"
    links.new(component_socket, _socket(object_info.inputs, "Object"))
    component_geometry = _socket(object_info.outputs, "Geometry")

    if subdivision_socket is not None:
        subdivide = nodes.new("GeometryNodeSubdivideMesh")
        subdivide.location = (location[0] + 220, location[1] - 360)
        subdivide.label = "Smooth Component Grid"
        links.new(component_geometry, _socket(subdivide.inputs, "Mesh"))
        links.new(subdivision_socket, _socket(subdivide.inputs, "Level"))
        component_geometry = _socket(subdivide.outputs, "Mesh")

    bbox = nodes.new("GeometryNodeBoundBox")
    bbox.location = (location[0] + 230, location[1] + 160)
    links.new(component_geometry, _socket(bbox.inputs, "Geometry"))

    position = nodes.new("GeometryNodeInputPosition")
    position.location = (location[0] + 230, location[1] - 120)

    separate_position = nodes.new("ShaderNodeSeparateXYZ")
    separate_position.location = (location[0] + 430, location[1] - 120)
    links.new(_socket(position.outputs, "Position"), _socket(separate_position.inputs, "Vector"))

    separate_min = nodes.new("ShaderNodeSeparateXYZ")
    separate_min.location = (location[0] + 430, location[1] + 210)
    links.new(_socket(bbox.outputs, "Min"), _socket(separate_min.inputs, "Vector"))

    separate_max = nodes.new("ShaderNodeSeparateXYZ")
    separate_max.location = (location[0] + 430, location[1] + 390)
    links.new(_socket(bbox.outputs, "Max"), _socket(separate_max.inputs, "Vector"))

    size_x = _math(
        nodes, links, "SUBTRACT", _socket(separate_max.outputs, "X"), _socket(separate_min.outputs, "X"),
        (location[0] + 650, location[1] + 390), "Component Width"
    )
    size_y = _math(
        nodes, links, "SUBTRACT", _socket(separate_max.outputs, "Y"), _socket(separate_min.outputs, "Y"),
        (location[0] + 650, location[1] + 300), "Component Height"
    )
    safe_x = _math(nodes, links, "MAXIMUM", size_x, 0.000001, (location[0] + 850, location[1] + 390), "Safe Width")
    safe_y = _math(nodes, links, "MAXIMUM", size_y, 0.000001, (location[0] + 850, location[1] + 300), "Safe Height")

    x_from_min = _math(
        nodes, links, "SUBTRACT", _socket(separate_position.outputs, "X"), _socket(separate_min.outputs, "X"),
        (location[0] + 650, location[1] + 70), "X from Bounds"
    )
    y_from_min = _math(
        nodes, links, "SUBTRACT", _socket(separate_position.outputs, "Y"), _socket(separate_min.outputs, "Y"),
        (location[0] + 650, location[1] - 20), "Y from Bounds"
    )
    u = _math(nodes, links, "DIVIDE", x_from_min, safe_x, (location[0] + 1050, location[1] + 70), "Normalized U")
    v = _math(nodes, links, "DIVIDE", y_from_min, safe_y, (location[0] + 1050, location[1] - 20), "Normalized V")

    component_area = _math(nodes, links, "MULTIPLY", safe_x, safe_y, (location[0] + 1050, location[1] + 300), "Component Bounds Area")
    component_scale = _math(nodes, links, "SQRT", component_area, None, (location[0] + 1240, location[1] + 300), "Component Reference Size")
    z = _math(
        nodes, links, "DIVIDE", _socket(separate_position.outputs, "Z"), component_scale,
        (location[0] + 1240, location[1] - 110), "Normalized Z"
    )

    geometry = _store_named_attribute(
        nodes, links, component_geometry, "FLOAT", "POINT", ATTR_U, u,
        (location[0] + 1260, location[1] + 100), "Store Component U"
    )
    geometry = _store_named_attribute(
        nodes, links, geometry, "FLOAT", "POINT", ATTR_V, v,
        (location[0] + 1480, location[1] + 20), "Store Component V"
    )
    geometry = _store_named_attribute(
        nodes, links, geometry, "FLOAT", "POINT", ATTR_Z, z,
        (location[0] + 1700, location[1] - 60), "Store Component Z"
    )
    return geometry

def _read_corner_indices(nodes, location=(-300, 700)):
    return [
        _named_attribute(nodes, "INT", name, (location[0], location[1] - i * 120), f"Corner {i} Vertex Index")
        for i, name in enumerate(ATTR_CORNER_VERTS)
    ]

def _sample_corners(nodes, links, target_geometry, indices, location=(0, 700), include_normals=False):
    source_position = nodes.new("GeometryNodeInputPosition")
    source_position.location = (location[0] - 220, location[1] + 100)
    source_normal = None
    if include_normals:
        source_normal = nodes.new("GeometryNodeInputNormal")
        source_normal.location = (location[0] - 220, location[1] - 520)

    positions = []
    normals = []
    for i, index in enumerate(indices):
        positions.append(
            _sample_vector(
                nodes, links, target_geometry, _socket(source_position.outputs, "Position"), index,
                (location[0] + i * 210, location[1]), f"Corner {i} Position"
            )
        )
        if include_normals:
            normals.append(
                _sample_vector(
                    nodes, links, target_geometry, _socket(source_normal.outputs, "Normal"), index,
                    (location[0] + i * 210, location[1] - 620), f"Corner {i} Normal"
                )
            )
    return positions, normals

def _coverage_coordinate(nodes, links, coordinate, coverage, location=(0, 0), label=""):
    centered = _math(nodes, links, "SUBTRACT", coordinate, 0.5, location, f"{label} Center")
    scaled = _math(nodes, links, "MULTIPLY", centered, coverage, (location[0] + 190, location[1]), f"{label} Coverage")
    return _math(nodes, links, "ADD", scaled, 0.5, (location[0] + 380, location[1]), label)

def _new_frame(nodes, label, description=""):
    frame = nodes.new("NodeFrame")
    frame.label = label
    frame.name = label
    frame.label_size = 24
    frame.location = (0, 0)
    if description:
        frame.label = f"{label} — {description}"
    return frame

def _parent_nodes(frame, node_list):
    for node in node_list:
        if node is frame or node.bl_idname == "NodeFrame":
            continue
        node.parent = frame

def _organize_deformed_nodes(nodes, group_input, group_output):
    """Group the generated graph into editable, clearly named logic stages."""
    frames = {
        "target": _new_frame(nodes, "01 Target Face Analysis", "quad corners and shared scale"),
        "component": _new_frame(nodes, "02 Component Preparation", "bounds normalization and stored UVZ"),
        "instances": _new_frame(nodes, "03 Instance Components", "one component per quad, then realize"),
        "coordinates": _new_frame(nodes, "04 Read Mapping Data", "coverage, corners, normals and scales"),
        "position": _new_frame(nodes, "05 Bilinear Position Math", "interpolate the quad surface"),
        "normal": _new_frame(nodes, "06 Bilinear Normal Math", "interpolate target vertex normals"),
        "scale": _new_frame(nodes, "07 Adaptive Scale Math", "Tissue-style shared vertex scale"),
        "height": _new_frame(nodes, "08 Height and Offset", "thickness plus normal displacement"),
        "output": _new_frame(nodes, "09 Final Geometry", "set position and optional merge"),
    }

    buckets = {key: [] for key in frames}
    output_labels = {
        "Tissue-Style Bilinear Mapping",
        "Optional Weld for Subdivision",
        "Merge Mesh (Realized Geometry)",
    }
    position_labels = {
        "Bottom Edge Position", "Bottom Edge Position Difference", "Bottom Edge Position Factor",
        "Top Edge Position", "Top Edge Position Difference", "Top Edge Position Factor",
        "Bilinear Surface Position", "Bilinear Surface Position Difference", "Bilinear Surface Position Factor",
    }
    normal_labels = {
        "Bottom Edge Normal", "Bottom Edge Normal Difference", "Bottom Edge Normal Factor",
        "Top Edge Normal", "Top Edge Normal Difference", "Top Edge Normal Factor",
        "Bilinear Normal", "Bilinear Normal Difference", "Bilinear Normal Factor",
    }
    scale_terms = ("Scale", "Shared Scale")
    height_labels = {
        "Adaptive Component Z", "Thickness", "Surface Height",
        "Normal Displacement", "Final Mapped Position",
    }

    for node in list(nodes):
        if node in frames.values() or node in {group_input, group_output}:
            continue
        label = node.label or node.name
        x, y = node.location

        if label in output_labels:
            buckets["output"].append(node)
        elif label in height_labels:
            buckets["height"].append(node)
        elif label in position_labels:
            buckets["position"].append(node)
        elif label in normal_labels:
            buckets["normal"].append(node)
        elif any(term in label for term in scale_terms) and not label.startswith("Store Shared"):
            buckets["scale"].append(node)
        elif label in {"One Component per Quad", "Realize for Deformation"}:
            buckets["instances"].append(node)
        elif y >= 700 and x < 0:
            buckets["component"].append(node)
        elif x <= -500 and y < 700:
            buckets["target"].append(node)
        else:
            buckets["coordinates"].append(node)

    for key, node_list in buckets.items():
        _parent_nodes(frames[key], node_list)

    group_input.label = "Modifier Inputs"
    group_output.label = "Mapped Geometry Output"

INTERNAL_PREFIX = "MTF 0.12.3"


def _internal_group(name):
    return bpy.data.node_groups.get(f"{INTERNAL_PREFIX} · {name}")


def _create_internal_group(name, description=""):
    full_name = f"{INTERNAL_PREFIX} · {name}"
    existing = bpy.data.node_groups.get(full_name)
    if existing is not None:
        return existing, False
    group = bpy.data.node_groups.new(full_name, "GeometryNodeTree")
    group.description = description
    return group, True


def _group_node(nodes, node_tree, location, label, width=220):
    node = nodes.new("GeometryNodeGroup")
    node.node_tree = node_tree
    node.location = location
    node.label = label
    node.name = label
    node.width = width
    return node


def _frame(nodes, label, location, description=""):
    node = nodes.new("NodeFrame")
    node.label = label if not description else f"{label} — {description}"
    node.name = label
    node.label_size = 26
    node.location = location
    return node


def _parent_at(node, frame, relative_location):
    node.parent = frame
    node.location = relative_location


def _configure_menu_items(node, labels):
    """Configure a Menu Switch across the Blender 5.x RNA variants."""
    items = getattr(node, "enum_items", None)
    if items is None:
        definition = getattr(node, "enum_definition", None)
        items = getattr(definition, "enum_items", None)
    if items is None:
        raise RuntimeError("Blender did not expose Menu Switch items")

    try:
        items.clear()
    except Exception:
        while len(items):
            items.remove(items[-1])

    for label in labels:
        try:
            item = items.new(label)
        except TypeError:
            item = items.new()
        if item is None and len(items):
            item = items[-1]
        if item is None:
            raise RuntimeError(f"Could not create Menu Switch item: {label}")
        if getattr(item, "name", None) != label:
            item.name = label


def _build_surface_subdivision_group():
    group, created = _create_internal_group(
        "01 Surface Subdivision",
        "Optionally subdivide the target before selecting the mapping shape.",
    )
    if not created:
        return group

    _new_interface_socket(group, "Geometry", "INPUT", "NodeSocketGeometry")
    level = _new_interface_socket(group, "Subdivision Level", "INPUT", "NodeSocketInt")
    level.default_value = 0
    level.min_value = 0
    level.max_value = 6
    _new_interface_socket(group, "Geometry", "OUTPUT", "NodeSocketGeometry")

    nodes, links = group.nodes, group.links
    inp = nodes.new("NodeGroupInput")
    inp.location = (-420, 40)
    inp.width = 190
    subdivide = nodes.new("GeometryNodeSubdivideMesh")
    subdivide.location = (-100, 40)
    subdivide.label = "Procedural Surface Density"
    links.new(_socket(inp.outputs, "Geometry"), _socket(subdivide.inputs, "Mesh"))
    links.new(_socket(inp.outputs, "Subdivision Level"), _socket(subdivide.inputs, "Level"))
    out = nodes.new("NodeGroupOutput")
    out.location = (220, 40)
    out.width = 170
    links.new(_socket(subdivide.outputs, "Mesh"), _socket(out.inputs, "Geometry"))
    return group


def _build_mapping_shape_group():
    group, created = _create_internal_group(
        "02 Mapping Shape",
        "Prepare quad cells, triangulated cells, or radial fan wedges before common mapping.",
    )
    if not created:
        return group

    _new_interface_socket(group, "Geometry", "INPUT", "NodeSocketGeometry")
    _new_interface_socket(group, "Mapping Shape", "INPUT", "NodeSocketMenu")
    _new_interface_socket(group, "Geometry", "OUTPUT", "NodeSocketGeometry")

    nodes, links = group.nodes, group.links
    inp = nodes.new("NodeGroupInput")
    inp.location = (-900, 40)
    inp.width = 200
    out = nodes.new("NodeGroupOutput")
    out.location = (830, 40)
    out.width = 180

    quad_frame = _frame(nodes, "Quad", (-650, 350), "use source quads directly")
    triangle_frame = _frame(nodes, "Triangle", (-650, 20), "triangulate source faces")
    fan_frame = _frame(nodes, "Fan", (-650, -390), "poke each polygon into radial wedges")

    quad_reroute = nodes.new("NodeReroute")
    quad_reroute.label = "Quad Surface"
    links.new(_socket(inp.outputs, "Geometry"), quad_reroute.inputs[0])
    _parent_at(quad_reroute, quad_frame, (80, 100))

    triangulate = nodes.new("GeometryNodeTriangulate")
    triangulate.label = "Triangulate Faces"
    if hasattr(triangulate, "quad_method"):
        triangulate.quad_method = "FIXED"
    if hasattr(triangulate, "ngon_method"):
        triangulate.ngon_method = "BEAUTY"
    links.new(_socket(inp.outputs, "Geometry"), _socket(triangulate.inputs, "Mesh"))
    if any(socket.name == "Selection" for socket in triangulate.inputs):
        _socket(triangulate.inputs, "Selection").default_value = True
    if any(socket.name == "Minimum Vertices" for socket in triangulate.inputs):
        _socket(triangulate.inputs, "Minimum Vertices").default_value = 4
    _parent_at(triangulate, triangle_frame, (70, 100))

    extrude = nodes.new("GeometryNodeExtrudeMesh")
    extrude.mode = "FACES"
    extrude.label = "Extrude Individual Faces"
    links.new(_socket(inp.outputs, "Geometry"), _socket(extrude.inputs, "Mesh"))
    _socket(extrude.inputs, "Selection").default_value = True
    if any(socket.name == "Offset" for socket in extrude.inputs):
        _socket(extrude.inputs, "Offset").default_value = (0.0, 0.0, 0.0)
    if any(socket.name == "Offset Scale" for socket in extrude.inputs):
        _socket(extrude.inputs, "Offset Scale").default_value = 0.0
    _socket(extrude.inputs, "Individual").default_value = True

    scale = nodes.new("GeometryNodeScaleElements")
    scale.domain = "FACE"
    scale.label = "Collapse Tops to Centers"
    links.new(_socket(extrude.outputs, "Mesh"), _socket(scale.inputs, "Geometry"))
    links.new(_socket(extrude.outputs, "Top"), _socket(scale.inputs, "Selection"))
    _socket(scale.inputs, "Scale").default_value = 0.0

    delete = nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = "FACE"
    if hasattr(delete, "mode"):
        delete.mode = "ALL"
    delete.label = "Delete Center Caps"
    links.new(_socket(scale.outputs, "Geometry"), _socket(delete.inputs, "Geometry"))
    links.new(_socket(extrude.outputs, "Top"), _socket(delete.inputs, "Selection"))

    merge = nodes.new("GeometryNodeMergeByDistance")
    merge.label = "Merge Fan Centers"
    if hasattr(merge, "mode"):
        merge.mode = "ALL"
    links.new(_socket(delete.outputs, "Geometry"), _socket(merge.inputs, "Geometry"))
    _socket(merge.inputs, "Selection").default_value = True
    _socket(merge.inputs, "Distance").default_value = 0.000001

    _parent_at(extrude, fan_frame, (30, 180))
    _parent_at(scale, fan_frame, (270, 180))
    _parent_at(delete, fan_frame, (510, 180))
    _parent_at(merge, fan_frame, (750, 180))

    menu = nodes.new("GeometryNodeMenuSwitch")
    menu.data_type = "GEOMETRY"
    menu.location = (500, 40)
    menu.label = "Quad / Triangle / Fan"
    _configure_menu_items(menu, ("Quad", "Triangle", "Fan"))
    links.new(_socket(inp.outputs, "Mapping Shape"), _socket(menu.inputs, "Menu"))
    links.new(quad_reroute.outputs[0], _socket(menu.inputs, "Quad"))
    links.new(_socket(triangulate.outputs, "Mesh"), _socket(menu.inputs, "Triangle"))
    links.new(_socket(merge.outputs, "Geometry"), _socket(menu.inputs, "Fan"))
    links.new(_socket(menu.outputs, "Output"), _socket(out.inputs, "Geometry"))
    return group


def _build_bilinear_vector_group():
    group, created = _create_internal_group(
        "Bilinear Vector",
        "Interpolate four vector corner values using normalized U and V coordinates.",
    )
    if not created:
        return group

    for name in ("P0", "P1", "P2", "P3"):
        _new_interface_socket(group, name, "INPUT", "NodeSocketVector")
    _new_interface_socket(group, "U", "INPUT", "NodeSocketFloat")
    _new_interface_socket(group, "V", "INPUT", "NodeSocketFloat")
    _new_interface_socket(group, "Result", "OUTPUT", "NodeSocketVector")

    nodes, links = group.nodes, group.links
    inp = nodes.new("NodeGroupInput")
    inp.location = (-520, 40)
    inp.width = 190
    out = nodes.new("NodeGroupOutput")
    out.location = (430, 40)
    out.width = 170

    bottom = _mix_vector(
        nodes, links,
        _socket(inp.outputs, "P0"), _socket(inp.outputs, "P1"), _socket(inp.outputs, "U"),
        (-230, 170), "Bottom Edge",
    )
    top = _mix_vector(
        nodes, links,
        _socket(inp.outputs, "P3"), _socket(inp.outputs, "P2"), _socket(inp.outputs, "U"),
        (-230, -90), "Top Edge",
    )
    result = _mix_vector(nodes, links, bottom, top, _socket(inp.outputs, "V"), (120, 40), "Across Face")
    links.new(result, _socket(out.inputs, "Result"))
    return group


def _build_bilinear_float_group():
    group, created = _create_internal_group(
        "Bilinear Float",
        "Interpolate four scalar corner values using normalized U and V coordinates.",
    )
    if not created:
        return group

    for name in ("S0", "S1", "S2", "S3", "U", "V"):
        _new_interface_socket(group, name, "INPUT", "NodeSocketFloat")
    _new_interface_socket(group, "Result", "OUTPUT", "NodeSocketFloat")

    nodes, links = group.nodes, group.links
    inp = nodes.new("NodeGroupInput")
    inp.location = (-520, 40)
    inp.width = 190
    out = nodes.new("NodeGroupOutput")
    out.location = (430, 40)
    out.width = 170

    bottom = _mix_float(
        nodes, links,
        _socket(inp.outputs, "S0"), _socket(inp.outputs, "S1"), _socket(inp.outputs, "U"),
        (-230, 170), "Bottom Edge",
    )
    top = _mix_float(
        nodes, links,
        _socket(inp.outputs, "S3"), _socket(inp.outputs, "S2"), _socket(inp.outputs, "U"),
        (-230, -90), "Top Edge",
    )
    result = _mix_float(nodes, links, bottom, top, _socket(inp.outputs, "V"), (120, 40), "Across Face")
    links.new(result, _socket(out.inputs, "Result"))
    return group


def _build_coverage_group():
    group, created = _create_internal_group(
        "Coverage Coordinates",
        "Scale normalized U and V around the center of each mapped face.",
    )
    if not created:
        return group

    _new_interface_socket(group, "U", "INPUT", "NodeSocketFloat")
    _new_interface_socket(group, "V", "INPUT", "NodeSocketFloat")
    _new_interface_socket(group, "Coverage", "INPUT", "NodeSocketFloat")
    _new_interface_socket(group, "Mapped U", "OUTPUT", "NodeSocketFloat")
    _new_interface_socket(group, "Mapped V", "OUTPUT", "NodeSocketFloat")

    nodes, links = group.nodes, group.links
    inp = nodes.new("NodeGroupInput")
    inp.location = (-650, 50)
    inp.width = 180
    out = nodes.new("NodeGroupOutput")
    out.location = (610, 50)
    out.width = 170

    u = _coverage_coordinate(nodes, links, _socket(inp.outputs, "U"), _socket(inp.outputs, "Coverage"), (-360, 160), "Mapped U")
    v = _coverage_coordinate(nodes, links, _socket(inp.outputs, "V"), _socket(inp.outputs, "Coverage"), (-360, -80), "Mapped V")
    links.new(u, _socket(out.inputs, "Mapped U"))
    links.new(v, _socket(out.inputs, "Mapped V"))
    return group


def _build_target_analysis_group():
    group, created = _create_internal_group(
        "03 Analyze Mapping Cells",
        "Store ordered corners and shared vertex scale for quad or triangular mapping cells.",
    )
    if not created:
        return group

    _new_interface_socket(group, "Geometry", "INPUT", "NodeSocketGeometry")
    _new_interface_socket(group, "Annotated Target", "OUTPUT", "NodeSocketGeometry")
    _new_interface_socket(group, "Cell Points", "OUTPUT", "NodeSocketGeometry")

    nodes, links = group.nodes, group.links
    inp = nodes.new("NodeGroupInput")
    inp.location = (-1750, 40)
    inp.width = 190
    out = nodes.new("NodeGroupOutput")
    out.location = (1190, 40)
    out.width = 200

    scale_frame = _frame(nodes, "Shared Surface Scale", (-1470, 430), "average adjacent cell area")
    corners_frame = _frame(nodes, "Ordered Cell Corners", (-1470, -250), "triangles duplicate their apex as corner three")
    filter_frame = _frame(nodes, "Cell Selection", (780, 40), "one point per triangle or quad")

    face_area = nodes.new("GeometryNodeInputMeshFaceArea")
    face_area.label = "Cell Area"
    area_on_points = nodes.new("GeometryNodeFieldOnDomain")
    area_on_points.data_type = "FLOAT"
    area_on_points.domain = "POINT"
    area_on_points.label = "Average on Vertices"
    links.new(_socket(face_area.outputs, "Area", 0), _socket(area_on_points.inputs, "Value", 0))
    vertex_scale = _math(nodes, links, "SQRT", _socket(area_on_points.outputs, "Value", 0), None, (0, 0), "Shared Vertex Scale")
    store_scale = nodes.new("GeometryNodeStoreNamedAttribute")
    store_scale.data_type = "FLOAT"
    store_scale.domain = "POINT"
    store_scale.label = "Store Shared Scale"
    _socket(store_scale.inputs, "Name").default_value = ATTR_VERTEX_SCALE
    links.new(_socket(inp.outputs, "Geometry"), _socket(store_scale.inputs, "Geometry"))
    links.new(vertex_scale, _socket(store_scale.inputs, "Value"))

    _parent_at(face_area, scale_frame, (30, 170))
    _parent_at(area_on_points, scale_frame, (240, 170))
    _parent_at(vertex_scale.node, scale_frame, (470, 170))
    _parent_at(store_scale, scale_frame, (700, 150))

    index = nodes.new("GeometryNodeInputIndex")
    index.label = "Face Index"
    _parent_at(index, corners_frame, (20, 330))

    corners = []
    vertices = []
    total_socket = None
    for i in range(4):
        x = 210 + i * 290
        corner = nodes.new("GeometryNodeCornersOfFace")
        corner.label = f"Corner {i}"
        links.new(_socket(index.outputs, "Index"), _socket(corner.inputs, "Face Index"))
        _socket(corner.inputs, "Sort Index").default_value = i
        if total_socket is None:
            total_socket = _socket(corner.outputs, "Total")
        vertex = nodes.new("GeometryNodeVertexOfCorner")
        vertex.label = f"Vertex {i}"
        links.new(_socket(corner.outputs, "Corner Index"), _socket(vertex.inputs, "Corner Index"))
        corners.append(corner)
        vertices.append(vertex)
        _parent_at(corner, corners_frame, (x, 330))
        _parent_at(vertex, corners_frame, (x, 130))

    is_triangle = nodes.new("ShaderNodeMath")
    is_triangle.operation = "COMPARE"
    is_triangle.label = "Triangle"
    links.new(total_socket, is_triangle.inputs[0])
    is_triangle.inputs[1].default_value = 3.0
    is_triangle.inputs[2].default_value = 0.1
    _parent_at(is_triangle, corners_frame, (1110, 330))

    corner3_switch = nodes.new("GeometryNodeSwitch")
    corner3_switch.input_type = "INT"
    corner3_switch.label = "Triangle Apex or Quad Corner 3"
    links.new(_socket(is_triangle.outputs, "Value", 0), _socket(corner3_switch.inputs, "Switch"))
    links.new(_socket(vertices[3].outputs, "Vertex Index"), _socket(corner3_switch.inputs, "False"))
    links.new(_socket(vertices[2].outputs, "Vertex Index"), _socket(corner3_switch.inputs, "True"))
    _parent_at(corner3_switch, corners_frame, (1110, 120))

    geometry_socket = _socket(store_scale.outputs, "Geometry")
    for i, attr_name in enumerate(ATTR_CORNER_VERTS):
        store = nodes.new("GeometryNodeStoreNamedAttribute")
        store.data_type = "INT"
        store.domain = "FACE"
        store.label = f"Store Corner {i}"
        _socket(store.inputs, "Name").default_value = attr_name
        links.new(geometry_socket, _socket(store.inputs, "Geometry"))
        value = _socket(corner3_switch.outputs, "Output") if i == 3 else _socket(vertices[i].outputs, "Vertex Index")
        links.new(value, _socket(store.inputs, "Value"))
        geometry_socket = _socket(store.outputs, "Geometry")
        _parent_at(store, corners_frame, (210 + i * 290, -100))

    is_quad = nodes.new("ShaderNodeMath")
    is_quad.operation = "COMPARE"
    is_quad.label = "Quad"
    links.new(total_socket, is_quad.inputs[0])
    is_quad.inputs[1].default_value = 4.0
    is_quad.inputs[2].default_value = 0.1

    valid_sum = nodes.new("ShaderNodeMath")
    valid_sum.operation = "ADD"
    valid_sum.label = "Triangle or Quad"
    links.new(_socket(is_triangle.outputs, "Value", 0), valid_sum.inputs[0])
    links.new(_socket(is_quad.outputs, "Value", 0), valid_sum.inputs[1])

    points = nodes.new("GeometryNodeMeshToPoints")
    points.label = "One Point per Mapping Cell"
    if hasattr(points, "mode"):
        points.mode = "FACES"
    links.new(geometry_socket, _socket(points.inputs, "Mesh"))
    links.new(_socket(valid_sum.outputs, "Value", 0), _socket(points.inputs, "Selection"))

    _parent_at(is_quad, filter_frame, (30, 170))
    _parent_at(valid_sum, filter_frame, (250, 120))
    _parent_at(points, filter_frame, (480, 100))

    links.new(geometry_socket, _socket(out.inputs, "Annotated Target"))
    links.new(_socket(points.outputs, "Points"), _socket(out.inputs, "Cell Points"))
    return group

def _build_component_preparation_group():
    group, created = _create_internal_group(
        "02 Prepare Component v0.13.0",
        "Optionally subdivide the component, then normalize X/Y bounds to U/V and store local Z for surface height.",
    )
    if not created:
        return group

    _new_interface_socket(group, "Component", "INPUT", "NodeSocketObject")
    component_subdivision = _new_interface_socket(group, "Component Subdivision", "INPUT", "NodeSocketInt")
    component_subdivision.default_value = 0
    component_subdivision.min_value = 0
    component_subdivision.max_value = 6
    _new_interface_socket(group, "Prepared Component", "OUTPUT", "NodeSocketGeometry")

    nodes, links = group.nodes, group.links
    inp = nodes.new("NodeGroupInput")
    inp.location = (-1650, 40)
    inp.width = 190
    out = nodes.new("NodeGroupOutput")
    out.location = (1180, 40)
    out.width = 210

    geometry = _prepare_component_no_pretransform(
        nodes, links, _socket(inp.outputs, "Component"),
        _socket(inp.outputs, "Component Subdivision"), (-1370, 150)
    )
    links.new(geometry, _socket(out.inputs, "Prepared Component"))

    read_frame = _frame(nodes, "Read Component", (-1400, 180), "geometry, bounds and position")
    normalize_frame = _frame(nodes, "Normalize Coordinates", (-800, 180), "convert local X/Y/Z into mapping fields")
    store_frame = _frame(nodes, "Store Mapping Data", (-120, 120), "U, V and height attributes")

    for node in list(nodes):
        if node in {inp, out, read_frame, normalize_frame, store_frame} or node.bl_idname == "NodeFrame":
            continue
        x, y = node.location
        if x < -850:
            frame = read_frame
        elif x < -150:
            frame = normalize_frame
        else:
            frame = store_frame
        absolute_x, absolute_y = x, y
        node.parent = frame
        node.location = (absolute_x - frame.location.x, absolute_y - frame.location.y)
    return group


def _build_instance_group():
    group, created = _create_internal_group(
        "04 Create Cell Copies",
        "Instance one prepared component on each mapping cell and realize it for deformation.",
    )
    if not created:
        return group

    _new_interface_socket(group, "Cell Points", "INPUT", "NodeSocketGeometry")
    _new_interface_socket(group, "Prepared Component", "INPUT", "NodeSocketGeometry")
    _new_interface_socket(group, "Geometry", "OUTPUT", "NodeSocketGeometry")

    nodes, links = group.nodes, group.links
    inp = nodes.new("NodeGroupInput")
    inp.location = (-520, 40)
    inp.width = 200
    instance = nodes.new("GeometryNodeInstanceOnPoints")
    instance.location = (-210, 40)
    instance.label = "One Copy per Cell"
    links.new(_socket(inp.outputs, "Cell Points"), _socket(instance.inputs, "Points"))
    links.new(_socket(inp.outputs, "Prepared Component"), _socket(instance.inputs, "Instance"))
    realize = nodes.new("GeometryNodeRealizeInstances")
    realize.location = (70, 40)
    realize.label = "Realize for Deformation"
    links.new(_socket(instance.outputs, "Instances"), _socket(realize.inputs, "Geometry"))
    out = nodes.new("NodeGroupOutput")
    out.location = (350, 40)
    out.width = 170
    links.new(_socket(realize.outputs, "Geometry"), _socket(out.inputs, "Geometry"))
    return group


def _build_read_quad_data_group():
    group, created = _create_internal_group(
        "Read Cell Mapping Data",
        "Read four ordered target positions, normals and shared scale values; triangles use a duplicated apex.",
    )
    if not created:
        return group

    _new_interface_socket(group, "Target Geometry", "INPUT", "NodeSocketGeometry")
    for i in range(4):
        _new_interface_socket(group, f"P{i}", "OUTPUT", "NodeSocketVector")
    for i in range(4):
        _new_interface_socket(group, f"N{i}", "OUTPUT", "NodeSocketVector")
    for i in range(4):
        _new_interface_socket(group, f"S{i}", "OUTPUT", "NodeSocketFloat")

    nodes, links = group.nodes, group.links
    inp = nodes.new("NodeGroupInput")
    inp.location = (-1450, 80)
    inp.width = 190
    out = nodes.new("NodeGroupOutput")
    out.location = (760, 80)
    out.width = 190

    position = nodes.new("GeometryNodeInputPosition")
    position.location = (-1450, 420)
    position.label = "Target Position Field"
    normal = nodes.new("GeometryNodeInputNormal")
    normal.location = (-1450, 250)
    normal.label = "Target Normal Field"
    scale = _named_attribute(nodes, "FLOAT", ATTR_VERTEX_SCALE, (-1450, 80), "Target Shared Scale")

    frames = []
    for i in range(4):
        frame = _frame(nodes, f"Corner {i}", (-1120 + i * 430, 40), "position, normal and scale")
        frames.append(frame)
        index = _named_attribute(nodes, "INT", ATTR_CORNER_VERTS[i], (0, 0), f"Corner {i} Vertex")
        p = _sample_vector(nodes, links, _socket(inp.outputs, "Target Geometry"), _socket(position.outputs, "Position"), index, (0, 0), f"P{i}")
        n = _sample_vector(nodes, links, _socket(inp.outputs, "Target Geometry"), _socket(normal.outputs, "Normal"), index, (0, 0), f"N{i}")
        s = _sample_float(nodes, links, _socket(inp.outputs, "Target Geometry"), scale, index, (0, 0), f"S{i}")
        _parent_at(index.node, frame, (25, 330))
        _parent_at(p.node, frame, (25, 110))
        _parent_at(n.node, frame, (25, -120))
        _parent_at(s.node, frame, (25, -350))
        links.new(p, _socket(out.inputs, f"P{i}"))
        links.new(n, _socket(out.inputs, f"N{i}"))
        links.new(s, _socket(out.inputs, f"S{i}"))
    return group


def _build_mapping_group():
    group, created = _create_internal_group(
        "05 Deform to Mapping Cell v0.12.5",
        "Apply bilinear quad mapping or degenerate-quad triangle/fan mapping with shared adaptive scale.",
    )
    if not created:
        return group

    _new_interface_socket(group, "Target Geometry", "INPUT", "NodeSocketGeometry")
    _new_interface_socket(group, "Geometry", "INPUT", "NodeSocketGeometry")
    _new_interface_socket(group, "Coverage", "INPUT", "NodeSocketFloat")
    _new_interface_socket(group, "Thickness", "INPUT", "NodeSocketFloat")
    _new_interface_socket(group, "Normal Offset", "INPUT", "NodeSocketFloat")
    _new_interface_socket(group, "Geometry", "OUTPUT", "NodeSocketGeometry")

    nodes, links = group.nodes, group.links
    inp = nodes.new("NodeGroupInput")
    inp.location = (-1500, 80)
    inp.width = 210
    out = nodes.new("NodeGroupOutput")
    out.location = (1760, 80)
    out.width = 180

    input_frame = _frame(nodes, "Mapping Coordinates", (-1210, 300), "component U, V and local height")
    interpolation_frame = _frame(nodes, "Bilinear Surface Mapping", (-420, 120), "position, normal and shared scale")
    height_frame = _frame(nodes, "Height and Offset", (520, 40), "normal displacement")

    u = _named_attribute(nodes, "FLOAT", ATTR_U, (0, 0), "Component U")
    v = _named_attribute(nodes, "FLOAT", ATTR_V, (0, 0), "Component V")
    z = _named_attribute(nodes, "FLOAT", ATTR_Z, (0, 0), "Component Z")
    _parent_at(u.node, input_frame, (20, 260))
    _parent_at(v.node, input_frame, (20, 90))
    _parent_at(z.node, input_frame, (20, -80))

    coverage_group = _build_coverage_group()
    coverage = _group_node(nodes, coverage_group, (0, 0), "Coverage", 210)
    _parent_at(coverage, input_frame, (260, 130))
    links.new(u, _socket(coverage.inputs, "U"))
    links.new(v, _socket(coverage.inputs, "V"))
    links.new(_socket(inp.outputs, "Coverage"), _socket(coverage.inputs, "Coverage"))
    mapped_u = _socket(coverage.outputs, "Mapped U")
    mapped_v = _socket(coverage.outputs, "Mapped V")

    read_group = _build_read_quad_data_group()
    read = _group_node(nodes, read_group, (-930, -270), "Read Cell Data", 240)
    links.new(_socket(inp.outputs, "Target Geometry"), _socket(read.inputs, "Target Geometry"))

    vector_group = _build_bilinear_vector_group()
    float_group = _build_bilinear_float_group()
    position_map = _group_node(nodes, vector_group, (0, 0), "Bilinear Position", 230)
    normal_map = _group_node(nodes, vector_group, (0, 0), "Bilinear Normal", 230)
    scale_map = _group_node(nodes, float_group, (0, 0), "Bilinear Shared Scale", 230)
    _parent_at(position_map, interpolation_frame, (20, 320))
    _parent_at(normal_map, interpolation_frame, (20, 40))
    _parent_at(scale_map, interpolation_frame, (20, -240))

    for i in range(4):
        links.new(_socket(read.outputs, f"P{i}"), _socket(position_map.inputs, f"P{i}"))
        links.new(_socket(read.outputs, f"N{i}"), _socket(normal_map.inputs, f"P{i}"))
        links.new(_socket(read.outputs, f"S{i}"), _socket(scale_map.inputs, f"S{i}"))
    for node in (position_map, normal_map, scale_map):
        links.new(mapped_u, _socket(node.inputs, "U"))
        links.new(mapped_v, _socket(node.inputs, "V"))

    adaptive_z = _math(nodes, links, "MULTIPLY", z, _socket(scale_map.outputs, "Result"), (0, 0), "Adaptive Component Z")
    thickness_z = _math(nodes, links, "MULTIPLY", adaptive_z, _socket(inp.outputs, "Thickness"), (0, 0), "Thickness")
    total_offset = _math(nodes, links, "ADD", thickness_z, _socket(inp.outputs, "Normal Offset"), (0, 0), "Surface Height")
    displacement = _vector_scale(nodes, links, _socket(normal_map.outputs, "Result"), total_offset, (0, 0), "Normal Displacement")
    final_position = _vector_math(nodes, links, "ADD", _socket(position_map.outputs, "Result"), displacement, (0, 0), "Final Position")

    for node, pos in (
        (adaptive_z.node, (20, 300)),
        (thickness_z.node, (240, 300)),
        (total_offset.node, (460, 300)),
        (displacement.node, (680, 180)),
        (final_position.node, (900, 180)),
    ):
        _parent_at(node, height_frame, pos)

    set_position = nodes.new("GeometryNodeSetPosition")
    set_position.location = (1370, 120)
    set_position.label = "Set Mapped Position"
    links.new(_socket(inp.outputs, "Geometry"), _socket(set_position.inputs, "Geometry"))
    links.new(final_position, _socket(set_position.inputs, "Position"))

    # Preserve the continuous guide-surface normal as an internal point attribute.
    # Shading is applied later in Finalize Geometry so this working deformation
    # graph remains independent from Blender's custom-normal node API.
    normalized_guide = _vector_math(
        nodes, links, "NORMALIZE", _socket(normal_map.outputs, "Result"), None,
        (1370, -130), "Normalize Guide Normal"
    )
    store_normal = nodes.new("GeometryNodeStoreNamedAttribute")
    store_normal.location = (1600, 120)
    store_normal.label = "Store Guide Normal"
    store_normal.data_type = "FLOAT_VECTOR"
    store_normal.domain = "POINT"
    links.new(_socket(set_position.outputs, "Geometry"), _socket(store_normal.inputs, "Geometry"))
    _socket(store_normal.inputs, "Selection").default_value = True
    _socket(store_normal.inputs, "Name").default_value = ATTR_GUIDE_NORMAL
    links.new(normalized_guide, _socket(store_normal.inputs, "Value"))

    links.new(_socket(store_normal.outputs, "Geometry"), _socket(out.inputs, "Geometry"))
    return group


def _build_finalize_group():
    group, created = _create_internal_group(
        "06 Finalize Geometry v0.12.6",
        "Optionally repair shading with guide normals and optionally weld mapped vertices.",
    )
    if not created:
        return group

    _new_interface_socket(group, "Geometry", "INPUT", "NodeSocketGeometry")
    _new_interface_socket(group, "Transfer Surface Normals", "INPUT", "NodeSocketBool")
    _new_interface_socket(group, "Merge Mesh", "INPUT", "NodeSocketBool")
    _new_interface_socket(group, "Merge Distance", "INPUT", "NodeSocketFloat")
    _new_interface_socket(group, "Geometry", "OUTPUT", "NodeSocketGeometry")

    nodes, links = group.nodes, group.links
    inp = nodes.new("NodeGroupInput")
    inp.location = (-760, 40)
    inp.width = 210

    merge = nodes.new("GeometryNodeMergeByDistance")
    merge.location = (-490, -150)
    merge.label = "Optional Weld"
    links.new(_socket(inp.outputs, "Geometry"), _socket(merge.inputs, "Geometry"))
    _socket(merge.inputs, "Selection").default_value = True
    links.new(_socket(inp.outputs, "Merge Distance"), _socket(merge.inputs, "Distance"))

    merge_switch = nodes.new("GeometryNodeSwitch")
    merge_switch.input_type = "GEOMETRY"
    merge_switch.location = (-230, 40)
    merge_switch.label = "Choose Fast or Welded Output"
    links.new(_socket(inp.outputs, "Merge Mesh"), _socket(merge_switch.inputs, "Switch"))
    links.new(_socket(inp.outputs, "Geometry"), _socket(merge_switch.inputs, "False"))
    links.new(_socket(merge.outputs, "Geometry"), _socket(merge_switch.inputs, "True"))

    guide_normal = nodes.new("GeometryNodeInputNamedAttribute")
    guide_normal.data_type = "FLOAT_VECTOR"
    guide_normal.location = (-220, -260)
    guide_normal.label = "Stored Guide Normal"
    _socket(guide_normal.inputs, "Name").default_value = ATTR_GUIDE_NORMAL

    set_normal = nodes.new("GeometryNodeSetMeshNormal")
    set_normal.location = (40, -40)
    set_normal.label = "Transfer Surface Normals"
    # Blender 5.x exposes the custom vector only in Free mode.
    if hasattr(set_normal, "mode"):
        set_normal.mode = "FREE"
    if hasattr(set_normal, "domain"):
        set_normal.domain = "POINT"
    links.new(_socket(merge_switch.outputs, "Output"), _socket(set_normal.inputs, "Mesh", 0))
    normal_input = next((socket for socket in set_normal.inputs if socket.name == "Custom Normal"), None)
    if normal_input is None:
        raise KeyError("Socket not found: Custom Normal")
    links.new(_socket(guide_normal.outputs, "Attribute"), normal_input)

    shading_switch = nodes.new("GeometryNodeSwitch")
    shading_switch.input_type = "GEOMETRY"
    shading_switch.location = (330, 40)
    shading_switch.label = "Transfer Surface Normals"
    links.new(_socket(inp.outputs, "Transfer Surface Normals"), _socket(shading_switch.inputs, "Switch"))
    links.new(_socket(merge_switch.outputs, "Output"), _socket(shading_switch.inputs, "False"))
    links.new(_socket(set_normal.outputs, "Mesh", 0), _socket(shading_switch.inputs, "True"))

    out = nodes.new("NodeGroupOutput")
    out.location = (610, 40)
    out.width = 170
    links.new(_socket(shading_switch.outputs, "Output"), _socket(out.inputs, "Geometry"))
    return group



def _build_smooth_guide_group():
    group, created = _create_internal_group(
        "Smooth Mapping Guide v0.14.2",
        "Create a Catmull-Clark guide surface without changing the mapping-cell grid.",
    )
    if not created:
        return group

    _new_interface_socket(group, "Geometry", "INPUT", "NodeSocketGeometry")
    level = _new_interface_socket(group, "Smooth Level", "INPUT", "NodeSocketInt")
    level.default_value = 2
    level.min_value = 0
    level.max_value = 6
    _new_interface_socket(group, "Geometry", "OUTPUT", "NodeSocketGeometry")

    nodes, links = group.nodes, group.links
    inp = nodes.new("NodeGroupInput")
    inp.location = (-430, 40)
    inp.width = 190
    smooth = nodes.new("GeometryNodeSubdivisionSurface")
    smooth.location = (-100, 40)
    smooth.label = "Smooth Guide Only"
    links.new(_socket(inp.outputs, "Geometry"), _socket(smooth.inputs, "Mesh", 0))
    links.new(_socket(inp.outputs, "Smooth Level"), _socket(smooth.inputs, "Level", 1))
    out = nodes.new("NodeGroupOutput")
    out.location = (240, 40)
    links.new(_socket(smooth.outputs, "Mesh", 0), _socket(out.inputs, "Geometry"))
    return group


def _build_smooth_mapping_group():
    group, created = _create_internal_group(
        "05 Deform to Smooth Mapping Cell v0.14.2",
        "Map one component per original cell, but conform its base to a separately smoothed guide surface.",
    )
    if not created:
        return group

    _new_interface_socket(group, "Target Geometry", "INPUT", "NodeSocketGeometry")
    _new_interface_socket(group, "Smooth Guide", "INPUT", "NodeSocketGeometry")
    _new_interface_socket(group, "Geometry", "INPUT", "NodeSocketGeometry")
    _new_interface_socket(group, "Coverage", "INPUT", "NodeSocketFloat")
    _new_interface_socket(group, "Thickness", "INPUT", "NodeSocketFloat")
    _new_interface_socket(group, "Normal Offset", "INPUT", "NodeSocketFloat")
    _new_interface_socket(group, "Geometry", "OUTPUT", "NodeSocketGeometry")

    nodes, links = group.nodes, group.links
    inp = nodes.new("NodeGroupInput")
    inp.location = (-1600, 80)
    inp.width = 220
    out = nodes.new("NodeGroupOutput")
    out.location = (2020, 80)
    out.width = 190

    u = _named_attribute(nodes, "FLOAT", ATTR_U, (-1370, 330), "Component U")
    v = _named_attribute(nodes, "FLOAT", ATTR_V, (-1370, 170), "Component V")
    z = _named_attribute(nodes, "FLOAT", ATTR_Z, (-1370, 10), "Component Z")

    coverage = _group_node(nodes, _build_coverage_group(), (-1100, 230), "Coverage", 210)
    links.new(u, _socket(coverage.inputs, "U"))
    links.new(v, _socket(coverage.inputs, "V"))
    links.new(_socket(inp.outputs, "Coverage"), _socket(coverage.inputs, "Coverage"))

    read = _group_node(nodes, _build_read_quad_data_group(), (-1090, -200), "Read Cell Data", 240)
    links.new(_socket(inp.outputs, "Target Geometry"), _socket(read.inputs, "Target Geometry"))

    vector_group = _build_bilinear_vector_group()
    float_group = _build_bilinear_float_group()
    position_map = _group_node(nodes, vector_group, (-650, 350), "Flat Cell Position", 230)
    normal_map = _group_node(nodes, vector_group, (-650, 70), "Guide Normal", 230)
    scale_map = _group_node(nodes, float_group, (-650, -210), "Shared Scale", 230)
    for i in range(4):
        links.new(_socket(read.outputs, f"P{i}"), _socket(position_map.inputs, f"P{i}"))
        links.new(_socket(read.outputs, f"N{i}"), _socket(normal_map.inputs, f"P{i}"))
        links.new(_socket(read.outputs, f"S{i}"), _socket(scale_map.inputs, f"S{i}"))
    for node in (position_map, normal_map, scale_map):
        links.new(_socket(coverage.outputs, "Mapped U"), _socket(node.inputs, "U"))
        links.new(_socket(coverage.outputs, "Mapped V"), _socket(node.inputs, "V"))

    proximity = nodes.new("GeometryNodeProximity")
    proximity.target_element = "FACES"
    proximity.location = (-300, 350)
    proximity.label = "Conform Base to Smooth Guide"

    # Geometry Proximity socket labels changed across Blender releases. Never use
    # positional fallbacks here: a wrong fallback silently links geometry to a
    # vector socket and leaves the subgroup full of warnings.
    target_input = next(
        (socket for socket in proximity.inputs if socket.name in {"Geometry", "Target"}),
        None,
    )
    source_input = next(
        (socket for socket in proximity.inputs if socket.name in {"Sample Position", "Source Position", "Position"}),
        None,
    )
    nearest_output = next(
        (socket for socket in proximity.outputs if socket.name in {"Position", "Nearest Position"}),
        None,
    )
    if target_input is None:
        raise KeyError("Geometry Proximity geometry input not found")
    if source_input is None:
        raise KeyError("Geometry Proximity sample-position input not found")
    if nearest_output is None:
        raise KeyError("Geometry Proximity nearest-position output not found")

    links.new(_socket(inp.outputs, "Smooth Guide"), target_input)
    links.new(_socket(position_map.outputs, "Result"), source_input)

    adaptive_z = _math(nodes, links, "MULTIPLY", z, _socket(scale_map.outputs, "Result"), (-260, -80), "Adaptive Z")
    thickness_z = _math(nodes, links, "MULTIPLY", adaptive_z, _socket(inp.outputs, "Thickness"), (-40, -80), "Thickness")
    total_offset = _math(nodes, links, "ADD", thickness_z, _socket(inp.outputs, "Normal Offset"), (180, -80), "Surface Height")
    displacement = _vector_scale(nodes, links, _socket(normal_map.outputs, "Result"), total_offset, (410, 20), "Normal Displacement")
    final_position = _vector_math(nodes, links, "ADD", nearest_output, displacement, (670, 180), "Smooth Final Position")

    set_position = nodes.new("GeometryNodeSetPosition")
    set_position.location = (980, 150)
    set_position.label = "Set Smooth Mapped Position"
    links.new(_socket(inp.outputs, "Geometry"), _socket(set_position.inputs, "Geometry"))
    links.new(final_position, _socket(set_position.inputs, "Position"))

    normalized_guide = _vector_math(nodes, links, "NORMALIZE", _socket(normal_map.outputs, "Result"), None, (980, -100), "Normalize Guide Normal")
    store_normal = nodes.new("GeometryNodeStoreNamedAttribute")
    store_normal.location = (1270, 150)
    store_normal.label = "Store Guide Normal"
    store_normal.data_type = "FLOAT_VECTOR"
    store_normal.domain = "POINT"
    links.new(_socket(set_position.outputs, "Geometry"), _socket(store_normal.inputs, "Geometry"))
    _socket(store_normal.inputs, "Selection").default_value = True
    _socket(store_normal.inputs, "Name").default_value = ATTR_GUIDE_NORMAL
    links.new(normalized_guide, _socket(store_normal.inputs, "Value"))
    links.new(_socket(store_normal.outputs, "Geometry"), _socket(out.inputs, "Geometry"))
    return group


def _build_smooth_map_to_faces_group(component=None, target_material=None):
    group, group_input, group_output = _base_group(GROUP_NAME, component, target_material)

    nodes, links = group.nodes, group.links
    surface = _group_node(nodes, _build_surface_subdivision_group(), (-1100, 220), "Surface Subdivision", 230)
    shape = _group_node(nodes, _build_mapping_shape_group(), (-820, 220), "Choose Mapping Shape", 240)
    smooth = _group_node(nodes, _build_smooth_guide_group(), (-520, 450), "Smooth Guide Surface", 240)
    target = _group_node(nodes, _build_target_analysis_group(), (-520, 150), "Analyze Mapping Cells", 240)
    component_node = _group_node(nodes, _build_component_preparation_group(), (-820, -180), "Prepare Component", 240)
    copies = _group_node(nodes, _build_instance_group(), (-220, -110), "Create Cell Copies", 230)
    mapping = _group_node(nodes, _build_smooth_mapping_group(), (80, 160), "Deform to Smooth Surface", 270)
    finalize = _group_node(nodes, _build_finalize_group(), (430, 160), "Finalize Geometry", 230)

    links.new(_socket(group_input.outputs, "Geometry"), _socket(surface.inputs, "Geometry"))
    links.new(_socket(group_input.outputs, "Subdivision Level"), _socket(surface.inputs, "Subdivision Level"))
    links.new(_socket(surface.outputs, "Geometry"), _socket(shape.inputs, "Geometry"))
    links.new(_socket(group_input.outputs, "Mapping Shape"), _socket(shape.inputs, "Mapping Shape"))
    links.new(_socket(shape.outputs, "Geometry"), _socket(target.inputs, "Geometry"))
    links.new(_socket(shape.outputs, "Geometry"), _socket(smooth.inputs, "Geometry"))
    links.new(_socket(group_input.outputs, "Surface Smooth Level"), _socket(smooth.inputs, "Smooth Level"))

    links.new(_socket(group_input.outputs, "Component"), _socket(component_node.inputs, "Component"))
    _socket(component_node.inputs, "Component Subdivision").default_value = 0
    links.new(_socket(target.outputs, "Cell Points"), _socket(copies.inputs, "Cell Points"))
    links.new(_socket(component_node.outputs, "Prepared Component"), _socket(copies.inputs, "Prepared Component"))

    links.new(_socket(target.outputs, "Annotated Target"), _socket(mapping.inputs, "Target Geometry"))
    links.new(_socket(smooth.outputs, "Geometry"), _socket(mapping.inputs, "Smooth Guide"))
    links.new(_socket(copies.outputs, "Geometry"), _socket(mapping.inputs, "Geometry"))
    for name in ("Coverage", "Thickness", "Normal Offset"):
        links.new(_socket(group_input.outputs, name), _socket(mapping.inputs, name))

    links.new(_socket(mapping.outputs, "Geometry"), _socket(finalize.inputs, "Geometry"))
    for name in ("Transfer Surface Normals", "Merge Mesh", "Merge Distance"):
        links.new(_socket(group_input.outputs, name), _socket(finalize.inputs, name))

    set_material = nodes.new("GeometryNodeSetMaterial")
    set_material.location = (700, -80)
    links.new(_socket(finalize.outputs, "Geometry"), _socket(set_material.inputs, "Geometry"))
    _socket(set_material.inputs, "Selection").default_value = True
    links.new(_socket(group_input.outputs, "Override Material"), _socket(set_material.inputs, "Material"))
    material_switch = nodes.new("GeometryNodeSwitch")
    material_switch.input_type = "GEOMETRY"
    material_switch.location = (980, 100)
    links.new(_socket(group_input.outputs, "Use Material Override"), _socket(material_switch.inputs, "Switch"))
    links.new(_socket(finalize.outputs, "Geometry"), _socket(material_switch.inputs, "False"))
    links.new(_socket(set_material.outputs, "Geometry"), _socket(material_switch.inputs, "True"))
    links.new(_socket(material_switch.outputs, "Output"), _socket(group_output.inputs, "Geometry"))
    group.description = "Map to Faces: one component per mapping cell, conformed to an optionally smoothed guide surface."
    return group

def _build_map_to_faces_group(component=None, target_material=None):
    """Build the organized multi-shape mapping graph."""
    group, group_input, group_output = _base_group(GROUP_NAME, component, target_material)
    nodes, links = group.nodes, group.links

    surface_frame = _frame(nodes, "01 Prepare Mapping Surface", (-1120, 0), "subdivision and mapping shape")
    component_frame = _frame(nodes, "02 Prepare Component", (-520, -260), "normalize component coordinates")
    map_frame = _frame(nodes, "03 Build and Map", (-300, 80), "analyze cells, copy and deform")
    finish_frame = _frame(nodes, "04 Output", (560, 80), "optional weld")

    subdivision_stage = _group_node(nodes, _build_surface_subdivision_group(), (0, 0), "Surface Subdivision", 240)
    shape = _group_node(nodes, _build_mapping_shape_group(), (0, 0), "Choose Mapping Shape", 250)
    target = _group_node(nodes, _build_target_analysis_group(), (0, 0), "Analyze Mapping Cells", 250)
    component_node = _group_node(nodes, _build_component_preparation_group(), (0, 0), "Prepare Component", 240)
    copies = _group_node(nodes, _build_instance_group(), (0, 0), "Create Cell Copies", 240)
    mapping = _group_node(nodes, _build_mapping_group(), (0, 0), "Deform to Mapping Cells", 260)
    finalize = _group_node(nodes, _build_finalize_group(), (0, 0), "Finalize Geometry", 230)

    _parent_at(subdivision_stage, surface_frame, (30, 160))
    _parent_at(shape, surface_frame, (320, 160))
    _parent_at(component_node, component_frame, (30, 100))
    _parent_at(target, map_frame, (30, 230))
    _parent_at(copies, map_frame, (330, 230))
    _parent_at(mapping, map_frame, (630, 230))
    _parent_at(finalize, finish_frame, (30, 100))

    links.new(_socket(group_input.outputs, "Geometry"), _socket(subdivision_stage.inputs, "Geometry"))
    links.new(_socket(group_input.outputs, "Subdivision Level"), _socket(subdivision_stage.inputs, "Subdivision Level"))
    links.new(_socket(subdivision_stage.outputs, "Geometry"), _socket(shape.inputs, "Geometry"))
    links.new(_socket(group_input.outputs, "Mapping Shape"), _socket(shape.inputs, "Mapping Shape"))
    links.new(_socket(shape.outputs, "Geometry"), _socket(target.inputs, "Geometry"))

    # Keep the component exposed as a real modifier input in every workflow.
    # The selected object is assigned to the interface default only after the
    # modifier and node group have been created, so Blender has initialized the
    # socket before the Object pointer is written.
    component_input = _socket(component_node.inputs, "Component")
    links.new(_socket(group_input.outputs, "Component"), component_input)
    links.new(_socket(group_input.outputs, "Component Subdivision"), _socket(component_node.inputs, "Component Subdivision"))

    links.new(_socket(target.outputs, "Cell Points"), _socket(copies.inputs, "Cell Points"))
    links.new(_socket(component_node.outputs, "Prepared Component"), _socket(copies.inputs, "Prepared Component"))

    links.new(_socket(target.outputs, "Annotated Target"), _socket(mapping.inputs, "Target Geometry"))
    links.new(_socket(copies.outputs, "Geometry"), _socket(mapping.inputs, "Geometry"))
    links.new(_socket(group_input.outputs, "Coverage"), _socket(mapping.inputs, "Coverage"))
    links.new(_socket(group_input.outputs, "Thickness"), _socket(mapping.inputs, "Thickness"))
    links.new(_socket(group_input.outputs, "Normal Offset"), _socket(mapping.inputs, "Normal Offset"))

    links.new(_socket(mapping.outputs, "Geometry"), _socket(finalize.inputs, "Geometry"))
    links.new(_socket(group_input.outputs, "Transfer Surface Normals"), _socket(finalize.inputs, "Transfer Surface Normals"))
    links.new(_socket(group_input.outputs, "Merge Mesh"), _socket(finalize.inputs, "Merge Mesh"))
    links.new(_socket(group_input.outputs, "Merge Distance"), _socket(finalize.inputs, "Merge Distance"))

    # Optional explicit material override. Leaving it disabled preserves the
    # component's existing material assignments.
    set_material = nodes.new("GeometryNodeSetMaterial")
    set_material.location = (790, -90)
    set_material.label = "Override Material"
    links.new(_socket(finalize.outputs, "Geometry"), _socket(set_material.inputs, "Geometry"))
    _socket(set_material.inputs, "Selection").default_value = True
    links.new(_socket(group_input.outputs, "Override Material"), _socket(set_material.inputs, "Material"))

    material_switch = nodes.new("GeometryNodeSwitch")
    material_switch.input_type = "GEOMETRY"
    material_switch.location = (1060, 80)
    material_switch.label = "Use Material Override"
    links.new(_socket(group_input.outputs, "Use Material Override"), _socket(material_switch.inputs, "Switch"))
    links.new(_socket(finalize.outputs, "Geometry"), _socket(material_switch.inputs, "False"))
    links.new(_socket(set_material.outputs, "Geometry"), _socket(material_switch.inputs, "True"))
    links.new(_socket(material_switch.outputs, "Output"), _socket(group_output.inputs, "Geometry"))

    group.description = (
        "Map to Faces 0.13.0: quad, triangle and fan mapping with target-surface subdivision, "
        "component smoothing subdivision, optional material override, and surface-normal transfer."
    )
    try:
        group.interface_update(bpy.context)
    except Exception:
        pass
    return group

def _active_target(context):
    target = context.view_layer.objects.active
    if target is None or target.type != "MESH":
        return None, "The active object must be a mesh."
    return target, None


def _assign_modifier(target, group):
    modifier = target.modifiers.new("Map to Faces", "NODES")
    try:
        modifier.node_group = group
    except Exception:
        target.modifiers.remove(modifier)
        if group.users == 0:
            bpy.data.node_groups.remove(group)
        raise
    return modifier


class MTF_OT_add_map_to_faces(Operator):
    bl_idname = "modus.map_to_faces"
    bl_label = "Map to Faces"
    bl_description = "Add Map to Faces to the active mesh with an empty Component slot"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        target, error = _active_target(context)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}

        try:
            group = _build_smooth_map_to_faces_group(None, None)
            _assign_modifier(target, group)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not create Map to Faces: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Added Map to Faces to {target.name}; assign a Component in the modifier.")
        return {"FINISHED"}



classes = (MTF_OT_add_map_to_faces,)



def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

