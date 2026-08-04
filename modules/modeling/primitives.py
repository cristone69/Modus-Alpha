# SPDX-License-Identifier: GPL-3.0-or-later
import math

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy_extras.object_utils import AddObjectHelper, object_data_add

from .clean_up.operators import relax_bmesh_vertices


def _get_vertices(self):
    """Return the user-facing even segment count from hidden ID storage."""
    return int(self.get("_mt_vertices", 16))


def _set_vertices(self, value):
    """Snap arrow and typed changes to even values in the change direction."""
    previous = int(self.get("_mt_vertices", 16))
    value = min(128, max(8, int(value)))
    if value % 2:
        value += 1 if value > previous else -1
    self["_mt_vertices"] = min(128, max(8, value))


def _finish(context, operator, name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    return object_data_add(context, mesh, operator=operator)


def _finish_with_bevel_weights(context, operator, name, bm):
    """Convert to Mesh, then mark the two cap perimeter loops safely.

    Mesh attributes are used after BMesh conversion so edge-weight assignment
    cannot be affected by invalidated BMesh references.
    """
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()

    attribute = mesh.attributes.get("bevel_weight_edge")
    if attribute is None:
        attribute = mesh.attributes.new(
            name="bevel_weight_edge",
            type="FLOAT",
            domain="EDGE",
        )

    # The side wall is created first. Its first and last horizontal edge loops
    # are the cap perimeters. Identify them geometrically to remain independent
    # of BMesh edge ordering after cap generation.
    if mesh.vertices:
        z_values = [vertex.co.z for vertex in mesh.vertices]
        min_z = min(z_values)
        max_z = max(z_values)
        epsilon = max(1.0e-6, abs(max_z - min_z) * 1.0e-6)

        for edge in mesh.edges:
            first_z = mesh.vertices[edge.vertices[0]].co.z
            second_z = mesh.vertices[edge.vertices[1]].co.z
            on_bottom = abs(first_z - min_z) <= epsilon and abs(second_z - min_z) <= epsilon
            on_top = abs(first_z - max_z) <= epsilon and abs(second_z - max_z) <= epsilon
            if on_bottom or on_top:
                attribute.data[edge.index].value = 1.0

    mesh.update()
    return object_data_add(context, mesh, operator=operator)


def _ring_edges(ring):
    """Recover current valid edges joining an ordered vertex ring.

    Avoid ``bm.edges.get`` here. After topology-changing operations Blender can
    leave its edge lookup cache referring to an invalidated BMEdge until the
    lookup tables are rebuilt. Walking each vertex's live link edges is slower
    but reliable for these small primitive boundary loops.
    """
    result = []
    count = len(ring)

    for index, vertex in enumerate(ring):
        if not vertex.is_valid:
            raise RuntimeError("A cap perimeter vertex became invalid")

        next_vertex = ring[(index + 1) % count]
        edge = next(
            (
                candidate
                for candidate in vertex.link_edges
                if candidate.is_valid and next_vertex in candidate.verts
            ),
            None,
        )
        if edge is None:
            raise RuntimeError("Could not recover a cap perimeter edge")
        result.append(edge)

    return result


def _grid_fill_cap(bm, ring, span, offset):
    """Create an all-quad rectangular grid inside one circular boundary.

    Blender's low-level ``bmesh.ops.grid_fill`` accepts two edge loops and
    does not expose the Edit Mode Grid Fill tool's Span/Offset parameters.
    This builds the equivalent single-boundary rectangular patch directly.
    """
    count = len(ring)
    half = count // 2
    columns = min(max(1, span), half - 1)
    rows = half - columns

    # Rotate which boundary vertex becomes the first corner. This is the same
    # practical role as Grid Fill's Offset setting.
    ordered = [ring[(index + offset) % count] for index in range(count)]

    # Walk the perimeter of a (columns x rows) quad grid. The coordinate order
    # contains exactly 2 * (columns + rows) entries, matching the open ring.
    perimeter_coords = []
    perimeter_coords.extend((x, 0) for x in range(columns + 1))
    perimeter_coords.extend((columns, y) for y in range(1, rows + 1))
    perimeter_coords.extend((x, rows) for x in range(columns - 1, -1, -1))
    perimeter_coords.extend((0, y) for y in range(rows - 1, 0, -1))

    if len(perimeter_coords) != count:
        raise RuntimeError("Invalid Grid Span for this segment count")

    grid = {}
    for coord, vertex in zip(perimeter_coords, ordered):
        grid[coord] = vertex

    bottom = [grid[(x, 0)].co.copy() for x in range(columns + 1)]
    top = [grid[(x, rows)].co.copy() for x in range(columns + 1)]
    left = [grid[(0, y)].co.copy() for y in range(rows + 1)]
    right = [grid[(columns, y)].co.copy() for y in range(rows + 1)]

    corner_00 = bottom[0]
    corner_10 = bottom[-1]
    corner_01 = top[0]
    corner_11 = top[-1]

    # Coons-patch interpolation keeps every boundary vertex fixed while
    # distributing the internal grid smoothly across the circular cap.
    for y in range(1, rows):
        v = y / rows
        for x in range(1, columns):
            u = x / columns
            boundary_blend = (
                bottom[x] * (1.0 - v)
                + top[x] * v
                + left[y] * (1.0 - u)
                + right[y] * u
            )
            corner_blend = (
                corner_00 * ((1.0 - u) * (1.0 - v))
                + corner_10 * (u * (1.0 - v))
                + corner_01 * ((1.0 - u) * v)
                + corner_11 * (u * v)
            )
            grid[(x, y)] = bm.verts.new(boundary_blend - corner_blend)

    faces = []
    for y in range(rows):
        for x in range(columns):
            faces.append(
                bm.faces.new(
                    (
                        grid[(x, y)],
                        grid[(x + 1, y)],
                        grid[(x + 1, y + 1)],
                        grid[(x, y + 1)],
                    )
                )
            )

    return faces


def _build_cap(bm, ring, span, offset, inset_enabled, radius):
    """Build one cap and return its faces plus movable Relax vertices.

    When inset is enabled, create a smaller concentric ring and bridge it to
    the original perimeter. This produces a true support loop on the cap and
    never involves side-wall faces.
    """
    working_ring = ring
    faces = []

    if inset_enabled:
        inset_amount = max(radius * 0.002, 1.0e-8)
        inset_radius = max(radius - inset_amount, radius * 0.001)
        scale = inset_radius / radius if radius > 1.0e-12 else 1.0
        inner_ring = [
            bm.verts.new((vertex.co.x * scale, vertex.co.y * scale, vertex.co.z))
            for vertex in ring
        ]

        count = len(ring)
        for index in range(count):
            next_index = (index + 1) % count
            faces.append(
                bm.faces.new(
                    (
                        ring[index],
                        ring[next_index],
                        inner_ring[next_index],
                        inner_ring[index],
                    )
                )
            )
        working_ring = inner_ring

    grid_faces = _grid_fill_cap(bm, working_ring, span, offset)
    faces.extend(grid_faces)

    movable_vertices = {vertex for face in faces for vertex in face.verts}
    movable_vertices.difference_update(ring)
    return faces, movable_vertices



def _resolve_added_object(context, candidate, mesh):
    """Resolve the object created for *mesh* across Blender helper variants."""
    if candidate is not None and getattr(candidate, "data", None) is mesh:
        return candidate
    for obj in (context.view_layer.objects.active, context.object):
        if obj is not None and getattr(obj, "data", None) is mesh:
            return obj
    return next((obj for obj in context.view_layer.objects if getattr(obj, "data", None) is mesh), None)


def _generate_quad_cylinder_uvs(obj):
    """Generate static primitive-style UVs without leaving seam flags.

    The layout is based on normalized local topology, so radius, depth and
    object rotation do not change the UV template. The wall occupies the upper
    half; top and bottom caps occupy the lower-left and lower-right quarters.
    """
    if obj is None or obj.type != "MESH":
        return

    mesh = obj.data
    uv_layer = mesh.uv_layers.get("UVMap") or mesh.uv_layers.new(name="UVMap")
    if not mesh.vertices:
        return

    z_values = [vertex.co.z for vertex in mesh.vertices]
    min_z, max_z = min(z_values), max(z_values)
    height = max(max_z - min_z, 1.0e-12)
    radius = max((math.hypot(vertex.co.x, vertex.co.y) for vertex in mesh.vertices), default=1.0)
    radius = max(radius, 1.0e-12)
    epsilon = max(1.0e-7, height * 1.0e-6)

    for polygon in mesh.polygons:
        coords = [mesh.vertices[index].co for index in polygon.vertices]
        is_top = all(abs(co.z - max_z) <= epsilon for co in coords)
        is_bottom = all(abs(co.z - min_z) <= epsilon for co in coords)

        if is_top or is_bottom:
            center_u = 0.25 if is_top else 0.75
            center_v = 0.25
            scale = 0.23
            for loop_index in polygon.loop_indices:
                co = mesh.vertices[mesh.loops[loop_index].vertex_index].co
                uv_layer.data[loop_index].uv = (
                    center_u + scale * (co.x / radius),
                    center_v + scale * (co.y / radius),
                )
            continue

        raw_u = []
        loop_data = []
        for loop_index in polygon.loop_indices:
            co = mesh.vertices[mesh.loops[loop_index].vertex_index].co
            u = (math.atan2(co.y, co.x) / (2.0 * math.pi)) % 1.0
            raw_u.append(u)
            loop_data.append((loop_index, co))

        # Keep faces crossing the angular seam continuous in UV space.
        if raw_u and max(raw_u) - min(raw_u) > 0.5:
            raw_u = [u + 1.0 if u < 0.5 else u for u in raw_u]

        for (loop_index, co), u in zip(loop_data, raw_u):
            uv_layer.data[loop_index].uv = (
                0.02 + 0.96 * u,
                0.52 + 0.46 * ((co.z - min_z) / height),
            )

    for edge in mesh.edges:
        edge.use_seam = False
    mesh.update()

def _generate_quad_sphere_uvs(mesh):
    """Create a deterministic six-island cube projection without bpy.ops.

    This avoids Edit Mode/operator-context failures in Blender 5.2 and keeps
    the UV layout stable across sphere size, object alignment, and subdivision.
    """
    if mesh is None or not mesh.vertices or not mesh.polygons:
        return

    uv_layer = mesh.uv_layers.get("UVMap") or mesh.uv_layers.new(name="UVMap")
    radius = max((vertex.co.length for vertex in mesh.vertices), default=1.0)
    radius = max(radius, 1.0e-12)

    # Six cube faces packed into a 3 x 2 atlas.
    tiles = {
        ("X", 1): (0, 0),
        ("X", -1): (1, 0),
        ("Y", 1): (2, 0),
        ("Y", -1): (0, 1),
        ("Z", 1): (1, 1),
        ("Z", -1): (2, 1),
    }
    pad = 0.02
    usable_u = (1.0 - 2.0 * pad) / 3.0
    usable_v = (1.0 - 2.0 * pad) / 2.0

    for polygon in mesh.polygons:
        normal = polygon.normal
        components = (abs(normal.x), abs(normal.y), abs(normal.z))
        axis_index = components.index(max(components))
        axis = "XYZ"[axis_index]
        sign = 1 if normal[axis_index] >= 0.0 else -1
        tile_x, tile_y = tiles[(axis, sign)]

        for loop_index in polygon.loop_indices:
            co = mesh.vertices[mesh.loops[loop_index].vertex_index].co / radius
            if axis == "X":
                local_u, local_v = co.y, co.z
                if sign < 0:
                    local_u = -local_u
            elif axis == "Y":
                local_u, local_v = -co.x, co.z
                if sign < 0:
                    local_u = -local_u
            else:
                local_u, local_v = co.x, co.y
                if sign < 0:
                    local_v = -local_v

            local_u = max(0.0, min(1.0, local_u * 0.5 + 0.5))
            local_v = max(0.0, min(1.0, local_v * 0.5 + 0.5))
            uv_layer.data[loop_index].uv = (
                pad + (tile_x + local_u) * usable_u,
                pad + (tile_y + local_v) * usable_v,
            )

    for edge in mesh.edges:
        edge.use_seam = False
    mesh.update()


class MODUS_OT_add_quad_cylinder(bpy.types.Operator, AddObjectHelper):
    bl_idname = "mesh.modus_quad_cylinder"
    bl_label = "Quad Cylinder"
    bl_options = {"REGISTER", "UNDO"}

    vertices: IntProperty(
        name="Vertices",
        min=8,
        max=128,
        default=16,
        get=_get_vertices,
        set=_set_vertices,
    )
    radius: FloatProperty(name="Radius", min=0.0001, default=1.0)
    depth: FloatProperty(name="Depth", min=0.0001, default=2.0)
    horizontal_loops: IntProperty(
        name="Horizontal Loops",
        min=0,
        max=64,
        default=0,
    )
    edge_treatment: EnumProperty(
        name="Bevel",
        items=(
            ("NONE", "None", "Do not mark or bevel the cap perimeter"),
            ("WEIGHT", "Bevel Weight", "Assign bevel weight 1.0"),
            ("CREASE", "Crease", "Assign subdivision crease weight 0.55"),
            ("BEVEL", "Geometry Bevel", "Create a two-segment geometric bevel"),
        ),
        default="NONE",
    )
    bevel_width: FloatProperty(
        name="Bevel Width",
        min=0.0,
        default=0.1,
    )
    inset_caps: BoolProperty(
        name="Inset Caps",
        description=(
            "Add a tiny support loop to the top and bottom caps before Relax"
        ),
        default=False,
    )
    smooth_shading: BoolProperty(
        name="Smooth Shading",
        description="Shade the cylinder smoothly",
        default=False,
    )
    add_subdivision_modifier: BoolProperty(
        name="Subdivision Modifier",
        description="Add a Subdivision Surface modifier",
        default=False,
    )
    subdivision_levels: IntProperty(
        name="Subdivision Levels",
        min=0,
        max=6,
        default=2,
    )
    add_bevel_modifier: BoolProperty(
        name="Bevel Modifier",
        description="Add a Bevel modifier that uses the cap-edge bevel weights",
        default=True,
    )

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "vertices")
        layout.prop(self, "radius")
        layout.prop(self, "depth")
        layout.prop(self, "horizontal_loops")
        layout.prop(self, "edge_treatment")
        if self.edge_treatment == "BEVEL":
            layout.prop(self, "bevel_width")
        elif self.edge_treatment == "WEIGHT":
            layout.prop(self, "add_bevel_modifier")
            if self.add_bevel_modifier:
                layout.prop(self, "bevel_width")
        layout.prop(self, "inset_caps")
        layout.prop(self, "smooth_shading")
        layout.prop(self, "add_subdivision_modifier")
        if self.add_subdivision_modifier:
            layout.prop(self, "subdivision_levels")

        layout.separator()
        layout.prop(self, "align")
        layout.prop(self, "location")
        layout.prop(self, "rotation")

    def execute(self, context):
        segment_count = max(4, self.vertices + (self.vertices % 2))
        ring_count = self.horizontal_loops + 2

        # Use a conservative balanced rectangular cap layout. These controls
        # remain internal while the automatic symmetry rules are being tested.
        quarter = max(2, segment_count // 4)
        grid_span = quarter if quarter % 2 == 0 else quarter - 1
        grid_span = min(max(1, grid_span), segment_count // 2 - 1)
        grid_offset = -(grid_span // 2)

        bm = bmesh.new()
        rings = []

        try:
            # Build only the side wall. The two end loops deliberately remain
            # open so Grid Fill can construct both caps afterward.
            # Counts that are 2 modulo 4 use the same cap layout as the
            # preceding multiple of four, but need a quarter-turn phase so the
            # resulting grid aligns symmetrically with the local X/Y axes.
            phase = math.pi * 0.5 if segment_count % 4 == 2 else 0.0

            for ring_index in range(ring_count):
                z = (
                    -self.depth * 0.5
                    + self.depth * ring_index / (ring_count - 1)
                )
                ring = [
                    bm.verts.new(
                        (
                            self.radius * math.cos(phase + 2.0 * math.pi * i / segment_count),
                            self.radius * math.sin(phase + 2.0 * math.pi * i / segment_count),
                            z,
                        )
                    )
                    for i in range(segment_count)
                ]
                rings.append(ring)

            side_faces = []
            for ring_index in range(ring_count - 1):
                lower = rings[ring_index]
                upper = rings[ring_index + 1]

                for index in range(segment_count):
                    next_index = (index + 1) % segment_count
                    side_faces.append(
                        bm.faces.new(
                            (
                                lower[index],
                                lower[next_index],
                                upper[next_index],
                                upper[index],
                            )
                        )
                    )

            # Bevel weights are assigned while the cylinder is still open.
            # Cap construction then reuses these existing boundary edges, avoiding
            # any need to rediscover them after topology-changing operations.
            if self.edge_treatment in {"WEIGHT", "CREASE"}:
                layer_name = (
                    "bevel_weight_edge"
                    if self.edge_treatment == "WEIGHT"
                    else "crease_edge"
                )
                edge_value = 1.0 if self.edge_treatment == "WEIGHT" else 0.55
                weight_layer = bm.edges.layers.float.get(layer_name)
                if weight_layer is None:
                    weight_layer = bm.edges.layers.float.new(layer_name)
                for edge in _ring_edges(rings[0]) + _ring_edges(rings[-1]):
                    edge[weight_layer] = edge_value

            # Offset is mirrored on the bottom because its outward-facing loop
            # has the opposite winding. This keeps both cap patterns identical.
            bottom_faces, bottom_movable = _build_cap(
                bm,
                list(reversed(rings[0])),
                grid_span,
                (-grid_offset) % segment_count,
                self.inset_caps,
                self.radius,
            )
            top_faces, top_movable = _build_cap(
                bm,
                rings[-1],
                grid_span,
                grid_offset,
                self.inset_caps,
                self.radius,
            )

            # Relax always runs. With inset disabled it relaxes the cap grid;
            # with inset enabled it also relaxes the new inner support ring.
            cap_relax_vertices = bottom_movable | top_movable

            # The original circular perimeter is pinned. Every other vertex
            # belonging to either cap may move. Surface preservation is off
            # because the generated caps are planar.
            boundary_vertices = set(rings[0]) | set(rings[-1])
            relax_bmesh_vertices(
                bm,
                [
                    vertex
                    for vertex in cap_relax_vertices
                    if vertex.is_valid
                ],
                pinned_vertices=boundary_vertices,
                iterations=20,
                strength=1.0,
                preserve_surface=False,
            )

            if self.edge_treatment == "BEVEL" and self.bevel_width > 0.0:
                # Geometry bevel still needs the final live perimeter edges.
                perimeter = _ring_edges(rings[0]) + _ring_edges(rings[-1])
                bmesh.ops.bevel(
                    bm,
                    geom=perimeter,
                    offset=self.bevel_width,
                    segments=2,
                    profile=1.0,
                    affect="EDGES",
                    clamp_overlap=True,
                )

            bmesh.ops.recalc_face_normals(bm, faces=[face for face in bm.faces if face.is_valid])

            mesh = bpy.data.meshes.new("Quad Cylinder")
            bm.to_mesh(mesh)
            bm.free()
            bm = None
            candidate = object_data_add(context, mesh, operator=self)
            obj = _resolve_added_object(context, candidate, mesh)
            _generate_quad_cylinder_uvs(obj)
            if self.smooth_shading:
                for polygon in mesh.polygons:
                    polygon.use_smooth = True
                mesh.update()

            if self.edge_treatment == "WEIGHT" and self.add_bevel_modifier:
                bevel = obj.modifiers.new(name="Bevel", type="BEVEL")
                bevel.limit_method = "WEIGHT"
                bevel.width = self.bevel_width
                bevel.segments = 2
                bevel.profile = 1.0
                bevel.miter_outer = "MITER_ARC"

            if self.add_subdivision_modifier:
                subdivision = obj.modifiers.new(name="Subdivision", type="SUBSURF")
                subdivision.subdivision_type = "CATMULL_CLARK"
                subdivision.levels = self.subdivision_levels
                subdivision.render_levels = self.subdivision_levels

        except Exception as exc:
            if bm is not None:
                bm.free()
            self.report({"ERROR"}, f"Quad Cylinder failed: {exc}")
            return {"CANCELLED"}

        return {"FINISHED"}


class MODUS_OT_add_quad_sphere(bpy.types.Operator, AddObjectHelper):
    bl_idname = "mesh.modus_quad_sphere"
    bl_label = "Quad Sphere"
    bl_options = {"REGISTER", "UNDO"}

    subdivisions: IntProperty(
        name="Subdivisions",
        min=1,
        max=5,
        default=2,
    )
    size: FloatProperty(name="Size", min=0.0001, default=2.0)
    smooth_shading: BoolProperty(
        name="Smooth Shading",
        description="Shade the sphere smoothly",
        default=False,
    )
    add_subdivision_modifier: BoolProperty(
        name="Subdivision Modifier",
        description="Add a Subdivision Surface modifier",
        default=False,
    )
    subdivision_levels: IntProperty(
        name="Subdivision Levels",
        min=0,
        max=6,
        default=2,
    )

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "subdivisions")
        layout.prop(self, "size")
        layout.prop(self, "smooth_shading")
        layout.prop(self, "add_subdivision_modifier")
        if self.add_subdivision_modifier:
            layout.prop(self, "subdivision_levels")
        layout.separator()
        layout.prop(self, "align")
        layout.prop(self, "location")
        layout.prop(self, "rotation")

    def execute(self, context):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=2.0)
        bmesh.ops.subdivide_edges(
            bm,
            edges=bm.edges[:],
            cuts=1,
            use_grid_fill=True,
        )
        for vertex in bm.verts:
            if vertex.co.length:
                vertex.co = vertex.co.normalized()

        for _ in range(1, self.subdivisions):
            bmesh.ops.subdivide_edges(
                bm,
                edges=bm.edges[:],
                cuts=1,
                use_grid_fill=True,
            )
            for vertex in bm.verts:
                if vertex.co.length:
                    vertex.co = vertex.co.normalized()

        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        scale_factor = self.size * 0.5
        for vertex in bm.verts:
            vertex.co *= scale_factor

        mesh = bpy.data.meshes.new("Quad Sphere")
        bm.to_mesh(mesh)
        bm.free()
        _generate_quad_sphere_uvs(mesh)
        if self.smooth_shading:
            for polygon in mesh.polygons:
                polygon.use_smooth = True
            mesh.update()
        candidate = object_data_add(context, mesh, operator=self)
        obj = _resolve_added_object(context, candidate, mesh)
        if self.add_subdivision_modifier:
            subdivision = obj.modifiers.new(name="Subdivision", type="SUBSURF")
            subdivision.subdivision_type = "CATMULL_CLARK"
            subdivision.levels = self.subdivision_levels
            subdivision.render_levels = self.subdivision_levels
        return {"FINISHED"}


def draw_add_menu(self, _context):
    self.layout.separator()
    self.layout.operator(
        MODUS_OT_add_quad_cylinder.bl_idname,
        icon="MESH_CYLINDER",
    )
    self.layout.operator(
        MODUS_OT_add_quad_sphere.bl_idname,
        icon="MESH_UVSPHERE",
    )


CLASSES = (
    MODUS_OT_add_quad_cylinder,
    MODUS_OT_add_quad_sphere,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_mesh_add.append(draw_add_menu)


def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(draw_add_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
