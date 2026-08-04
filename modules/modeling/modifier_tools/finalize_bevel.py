# SPDX-License-Identifier: GPL-3.0-or-later
"""Complete Bevel apply and n-gon repair operation in one self-contained module."""

import time

import bpy
import bmesh
import blf
from mathutils import Vector
from mathutils.kdtree import KDTree


# -----------------------------------------------------------------------------
# Viewport warning overlay
# -----------------------------------------------------------------------------

_VIEWPORT_WARNING_HANDLER = None
_VIEWPORT_WARNING_TOKEN = 0
_VIEWPORT_WARNING_LINES = ()
_VIEWPORT_WARNING_EXPIRES = 0.0

PROFILE_TOLERANCE = 1.0e-6
SUPPORTED_BEVEL_SEGMENTS = 2
SUPPORTED_OUTER_MITER = 'MITER_ARC'
SUPPORTED_PROFILE = 1.0


def _tag_all_view3d_regions_for_redraw():
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _draw_viewport_warning():
    if time.monotonic() >= _VIEWPORT_WARNING_EXPIRES:
        return

    region = bpy.context.region
    if region is None:
        return

    font_id = 0
    font_size = 18
    line_gap = 7
    padding_x = 16
    padding_y = 11

    try:
        blf.size(font_id, font_size)
    except TypeError:
        blf.size(font_id, font_size, 72)

    widths = [blf.dimensions(font_id, line)[0] for line in _VIEWPORT_WARNING_LINES]
    if not widths:
        return

    line_height = blf.dimensions(font_id, 'Ag')[1]
    box_width = max(widths) + padding_x * 2
    box_height = len(_VIEWPORT_WARNING_LINES) * line_height + (len(_VIEWPORT_WARNING_LINES) - 1) * line_gap + padding_y * 2
    left = max(12.0, (region.width - box_width) * 0.5)
    top = region.height - 72.0

    # Draw a dark translucent backdrop using a large block glyph. This avoids
    # adding GPU state code while keeping the warning readable over the mesh.
    try:
        blf.enable(font_id, blf.SHADOW)
        blf.shadow(font_id, 5, 0.0, 0.0, 0.0, 0.9)
        blf.shadow_offset(font_id, 2, -2)
    except (AttributeError, TypeError):
        pass

    blf.color(font_id, 1.0, 0.56, 0.12, 1.0)
    y = top - padding_y - line_height
    for line, width in zip(_VIEWPORT_WARNING_LINES, widths):
        x = left + (box_width - width) * 0.5
        blf.position(font_id, x, y, 0)
        blf.draw(font_id, line)
        y -= line_height + line_gap

    try:
        blf.disable(font_id, blf.SHADOW)
    except AttributeError:
        pass


def clear_viewport_warning():
    global _VIEWPORT_WARNING_HANDLER, _VIEWPORT_WARNING_LINES
    if _VIEWPORT_WARNING_HANDLER is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(
                _VIEWPORT_WARNING_HANDLER,
                'WINDOW',
            )
        except (ReferenceError, ValueError):
            pass
        _VIEWPORT_WARNING_HANDLER = None
    _VIEWPORT_WARNING_LINES = ()
    _tag_all_view3d_regions_for_redraw()


def show_viewport_warning(lines, duration=6.0):
    """Display a prominent temporary warning in every 3D Viewport."""
    global _VIEWPORT_WARNING_HANDLER
    global _VIEWPORT_WARNING_TOKEN
    global _VIEWPORT_WARNING_LINES
    global _VIEWPORT_WARNING_EXPIRES

    clear_viewport_warning()
    _VIEWPORT_WARNING_TOKEN += 1
    token = _VIEWPORT_WARNING_TOKEN
    _VIEWPORT_WARNING_LINES = tuple(str(line) for line in lines if line)
    _VIEWPORT_WARNING_EXPIRES = time.monotonic() + max(1.0, float(duration))

    if not _VIEWPORT_WARNING_LINES:
        return

    _VIEWPORT_WARNING_HANDLER = bpy.types.SpaceView3D.draw_handler_add(
        _draw_viewport_warning,
        (),
        'WINDOW',
        'POST_PIXEL',
    )
    _tag_all_view3d_regions_for_redraw()

    def remove_when_expired():
        if token != _VIEWPORT_WARNING_TOKEN:
            return None
        if time.monotonic() < _VIEWPORT_WARNING_EXPIRES:
            _tag_all_view3d_regions_for_redraw()
            return 0.1
        clear_viewport_warning()
        return None

    bpy.app.timers.register(remove_when_expired, first_interval=0.1)


# -----------------------------------------------------------------------------
# Shared object and mesh utilities
# -----------------------------------------------------------------------------

def get_active_mesh_object(context):
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return None
    return obj


def get_first_bevel_modifier(obj):
    for modifier in obj.modifiers:
        if modifier.type == 'BEVEL':
            return modifier
    return None


def prepare_active_object(context, obj):
    if obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    for selected in list(context.selected_objects):
        selected.select_set(False)

    obj.select_set(True)
    context.view_layer.objects.active = obj


def apply_first_bevel_modifier(context, obj):
    modifier = get_first_bevel_modifier(obj)
    if modifier is None:
        raise RuntimeError("The active object has no Bevel modifier.")

    modifier_name = modifier.name
    prepare_active_object(context, obj)

    result = bpy.ops.object.modifier_apply(modifier=modifier_name)
    if 'FINISHED' not in result:
        raise RuntimeError("Blender could not apply the first Bevel modifier.")

    return modifier_name


def apply_bevel_modifiers_in_stack_order(context, obj):
    """Apply every Bevel modifier in current stack order and return their names."""
    names = [modifier.name for modifier in bevel_modifiers(obj)]
    if not names:
        raise RuntimeError("The active object has no Bevel modifier.")

    applied = []
    for modifier_name in names:
        prepare_active_object(context, obj)
        result = bpy.ops.object.modifier_apply(modifier=modifier_name)
        if 'FINISHED' not in result:
            applied_text = ", ".join(f"'{name}'" for name in applied) or "none"
            raise RuntimeError(
                f"Blender could not apply Bevel modifier '{modifier_name}'. "
                f"Already applied: {applied_text}."
            )
        applied.append(modifier_name)

    return applied


def edge_key(edge):
    return tuple(sorted((edge.verts[0].index, edge.verts[1].index)))


def vertices_share_edge(first, second):
    return any(edge.other_vert(first) is second for edge in first.link_edges)


def normalized_or_none(vector):
    if vector.length_squared <= 1.0e-20:
        return None
    return vector.normalized()


def mesh_scale_tolerance(coordinates):
    if not coordinates:
        return 1.0e-8

    minimum = Vector(coordinates[0])
    maximum = Vector(coordinates[0])

    for coordinate in coordinates[1:]:
        minimum.x = min(minimum.x, coordinate.x)
        minimum.y = min(minimum.y, coordinate.y)
        minimum.z = min(minimum.z, coordinate.z)
        maximum.x = max(maximum.x, coordinate.x)
        maximum.y = max(maximum.y, coordinate.y)
        maximum.z = max(maximum.z, coordinate.z)

    diagonal = (maximum - minimum).length
    return max(diagonal * 1.0e-7, 1.0e-8)


def build_point_tree(coordinates):
    tree = KDTree(len(coordinates))
    for index, coordinate in enumerate(coordinates):
        tree.insert(coordinate, index)
    tree.balance()
    return tree


def find_vertex_index(tree, coordinate, tolerance):
    _nearest_coordinate, index, distance = tree.find(coordinate)
    if distance > tolerance:
        return None
    return index


def point_segment_distance(point, first, second):
    segment = second - first
    length_squared = segment.length_squared

    if length_squared <= 1.0e-20:
        return (point - first).length

    factor = (point - first).dot(segment) / length_squared
    factor = max(0.0, min(1.0, factor))
    closest = first + segment * factor
    return (point - closest).length


def incident_edges_in_face(face, vertex):
    for loop in face.loops:
        if loop.vert is vertex:
            return loop.link_loop_prev.edge, loop.edge
    return None, None


def face_neighbor_at_vertex(face, vertex, excluded_vertex):
    for loop in face.loops:
        if loop.vert is not vertex:
            continue

        previous_vertex = loop.link_loop_prev.vert
        next_vertex = loop.link_loop_next.vert

        if previous_vertex is excluded_vertex:
            return next_vertex
        if next_vertex is excluded_vertex:
            return previous_vertex

    return None


def topology_counts(bm):
    """Return face counts used to validate a speculative local repair."""
    triangles = 0
    quads = 0
    ngons = 0

    for face in bm.faces:
        if not face.is_valid:
            continue
        side_count = len(face.verts)
        if side_count == 3:
            triangles += 1
        elif side_count == 4:
            quads += 1
        elif side_count > 4:
            ngons += 1

    return triangles, quads, ngons


def bevel_modifiers(obj):
    """Return Bevel modifiers in their current stack order."""
    return [modifier for modifier in obj.modifiers if modifier.type == 'BEVEL']


def clear_all_bevel_edge_weights(mesh):
    """Set every bevel edge weight to zero after the modifier is applied."""
    attribute = mesh.attributes.get('bevel_weight_edge')
    if attribute is None or attribute.domain != 'EDGE':
        return 0

    count = len(attribute.data)
    if count:
        try:
            attribute.data.foreach_set('value', [0.0] * count)
        except (AttributeError, TypeError, ValueError):
            for item in attribute.data:
                item.value = 0.0
    mesh.update()
    return count


def collect_beveled_edges_for_seams(mesh, beveled_keys):
    """Record every beveled source edge as a future center-line UV seam.

    Existing seams on those source edges are cleared before modifier application
    so Blender cannot propagate them onto unwanted bevel boundary edges. After
    topology repair, every recorded bevel path is marked on the post-bevel center
    line. Unrelated source seams are left untouched.
    """
    records = []
    for edge in mesh.edges:
        key = tuple(sorted(edge.vertices))
        if key not in beveled_keys:
            continue

        start = mesh.vertices[edge.vertices[0]].co.copy()
        end = mesh.vertices[edge.vertices[1]].co.copy()
        vector = end - start
        length = vector.length
        if length <= 1.0e-12:
            continue

        records.append({
            "start": start,
            "end": end,
            "direction": vector / length,
            "length": length,
        })
        if edge.use_seam:
            edge.use_seam = False

    if records:
        mesh.update()
    return records


def _edge_distance_to_source_line(edge, record):
    """Return geometric matching data for a post-bevel edge."""
    start = record["start"]
    direction = record["direction"]
    length = record["length"]

    edge_vector = edge.verts[1].co - edge.verts[0].co
    edge_length = edge_vector.length
    if edge_length <= 1.0e-12:
        return None

    alignment = abs(edge_vector.normalized().dot(direction))
    midpoint = (edge.verts[0].co + edge.verts[1].co) * 0.5
    relative = midpoint - start
    projection = relative.dot(direction)
    perpendicular = (relative - direction * projection).length

    first_projection = (edge.verts[0].co - start).dot(direction)
    second_projection = (edge.verts[1].co - start).dot(direction)
    interval_min = min(first_projection, second_projection)
    interval_max = max(first_projection, second_projection)
    overlap = min(interval_max, length) - max(interval_min, 0.0)

    return alignment, perpendicular, projection, overlap, edge_length


def build_edge_midpoint_tree(bm):
    """Build one spatial lookup for post-Bevel edge midpoints."""
    bm.edges.ensure_lookup_table()
    tree = KDTree(len(bm.edges))
    for edge in bm.edges:
        midpoint = (edge.verts[0].co + edge.verts[1].co) * 0.5
        tree.insert(midpoint, edge.index)
    tree.balance()
    return tree


def mark_beveled_center_edges_as_seams(bm, seam_records, base_tolerance):
    """Mark center-line descendants of every recorded beveled source edge.

    A single midpoint KD-tree limits each source path to nearby result edges.
    The original direction, overlap, and nearest-collinear-layer tests remain
    unchanged so this is a lookup optimization rather than a behavior change.
    """
    if not seam_records:
        return 0, 0

    bm.edges.ensure_lookup_table()
    midpoint_tree = build_edge_midpoint_tree(bm)
    marked_edges = set()
    restored_paths = 0

    for record in seam_records:
        length = record["length"]
        line_tolerance = max(base_tolerance * 20.0, length * 1.0e-6, 1.0e-8)
        projection_tolerance = max(base_tolerance * 40.0, length * 1.0e-5)
        source_midpoint = (record["start"] + record["end"]) * 0.5
        search_radius = (
            length * 0.5
            + projection_tolerance
            + line_tolerance
        )
        candidates = []

        for _coordinate, edge_index, _distance in midpoint_tree.find_range(
            source_midpoint,
            search_radius,
        ):
            if edge_index >= len(bm.edges):
                continue
            edge = bm.edges[edge_index]
            if not edge.is_valid:
                continue

            match = _edge_distance_to_source_line(edge, record)
            if match is None:
                continue
            alignment, perpendicular, projection, overlap, edge_length = match
            if alignment < 0.9999:
                continue
            if overlap <= -projection_tolerance:
                continue
            if projection < -projection_tolerance or projection > length + projection_tolerance:
                continue
            candidates.append((perpendicular, edge_length, edge))

        if not candidates:
            continue

        minimum_distance = min(item[0] for item in candidates)
        selected = [
            edge for perpendicular, _edge_length, edge in candidates
            if perpendicular <= minimum_distance + line_tolerance
        ]
        if not selected:
            continue

        for edge in selected:
            edge.seam = True
            marked_edges.add(edge)
        restored_paths += 1

    return restored_paths, len(marked_edges)

# -----------------------------------------------------------------------------
# Bevel source analysis
# -----------------------------------------------------------------------------

def mesh_attribute_values(mesh, attribute_name, expected_domain):
    """
    Return float values from a named mesh attribute when its domain matches.

    Blender's Bevel modifier normally uses:
      - bevel_weight_edge on the EDGE domain
      - bevel_weight_vert on the POINT domain
    """
    if not attribute_name:
        return None

    attribute = mesh.attributes.get(attribute_name)
    if attribute is None:
        return None
    if attribute.domain != expected_domain:
        return None

    values = []
    for item in attribute.data:
        value = getattr(item, "value", None)
        if value is None:
            return None
        values.append(float(value))

    return values


def vertex_group_weights(obj, group_name, invert=False):
    group = obj.vertex_groups.get(group_name)
    if group is None:
        return None

    weights = []
    for vertex in obj.data.vertices:
        try:
            weight = float(group.weight(vertex.index))
        except RuntimeError:
            weight = 0.0

        if invert:
            weight = 1.0 - weight

        weights.append(max(0.0, min(1.0, weight)))

    return weights


def collect_beveled_edge_keys(obj, modifier):
    """
    Determine the source edges directly from the Bevel modifier's own
    limitation method instead of inferring them from evaluated geometry.

    No validation gate is added: the function simply reads the modifier that
    the user supplied and returns the edges that method selects.
    """
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    method = getattr(modifier, "limit_method", "NONE")
    affect = getattr(modifier, "affect", "EDGES")
    selected = set()
    detail = method

    # These two cleanup scenarios are edge-bevel patterns. If the modifier is
    # in vertex-only mode, use incident edges as a best-effort source map.
    vertex_mode = affect == 'VERTICES'

    if method == 'NONE':
        if vertex_mode:
            selected_vertices = set(bm.verts)
            for edge in bm.edges:
                if edge.verts[0] in selected_vertices or edge.verts[1] in selected_vertices:
                    selected.add(edge_key(edge))
        else:
            selected = {edge_key(edge) for edge in bm.edges}

    elif method == 'ANGLE':
        angle_limit = float(getattr(modifier, "angle_limit", 0.0))
        detail = f"ANGLE {angle_limit:.6f} rad"

        for edge in bm.edges:
            if len(edge.link_faces) != 2:
                continue

            try:
                face_angle = edge.calc_face_angle(0.0)
            except TypeError:
                face_angle = edge.calc_face_angle()

            if face_angle > angle_limit + 1.0e-7:
                selected.add(edge_key(edge))

    elif method == 'WEIGHT':
        if vertex_mode:
            attribute_name = getattr(
                modifier,
                "vertex_weight",
                "bevel_weight_vert",
            )
            values = mesh_attribute_values(
                mesh,
                attribute_name,
                'POINT',
            )
            detail = f"WEIGHT vertex attribute '{attribute_name}'"

            if values is not None:
                selected_vertices = {
                    index
                    for index, value in enumerate(values)
                    if value > 1.0e-6
                }
                for edge in bm.edges:
                    if (
                        edge.verts[0].index in selected_vertices
                        or edge.verts[1].index in selected_vertices
                    ):
                        selected.add(edge_key(edge))
        else:
            attribute_name = getattr(
                modifier,
                "edge_weight",
                "bevel_weight_edge",
            )
            values = mesh_attribute_values(
                mesh,
                attribute_name,
                'EDGE',
            )
            detail = f"WEIGHT edge attribute '{attribute_name}'"

            if values is not None:
                for edge in bm.edges:
                    if edge.index < len(values) and values[edge.index] > 1.0e-6:
                        selected.add(edge_key(edge))

    elif method == 'VGROUP':
        group_name = getattr(modifier, "vertex_group", "")
        invert = bool(getattr(modifier, "invert_vertex_group", False))
        weights = vertex_group_weights(
            obj,
            group_name,
            invert=invert,
        )
        detail = (
            f"VGROUP '{group_name}'"
            + (" inverted" if invert else "")
        )

        if weights is not None:
            if vertex_mode:
                selected_vertices = {
                    index
                    for index, weight in enumerate(weights)
                    if weight > 1.0e-6
                }
                for edge in bm.edges:
                    if (
                        edge.verts[0].index in selected_vertices
                        or edge.verts[1].index in selected_vertices
                    ):
                        selected.add(edge_key(edge))
            else:
                for edge in bm.edges:
                    first_weight = weights[edge.verts[0].index]
                    second_weight = weights[edge.verts[1].index]
                    if first_weight > 1.0e-6 and second_weight > 1.0e-6:
                        selected.add(edge_key(edge))

    bm.free()
    return selected, len(mesh.edges), f"{affect}/{detail}"

# -----------------------------------------------------------------------------
# Scenario 1: post-Bevel spoke reconstruction
# -----------------------------------------------------------------------------

def scenario_one_endpoint_data(vertex, spoke, beveled_keys):
    """Return the through-bevel pair at one endpoint of an unselected spoke."""
    if len(spoke.link_faces) != 2:
        return None

    first_face, second_face = spoke.link_faces
    if len(first_face.verts) != 4 or len(second_face.verts) != 4:
        return None

    first_incident = incident_edges_in_face(first_face, vertex)
    second_incident = incident_edges_in_face(second_face, vertex)

    first_other = next(
        (
            edge for edge in first_incident
            if edge is not None and edge is not spoke
        ),
        None,
    )
    second_other = next(
        (
            edge for edge in second_incident
            if edge is not None and edge is not spoke
        ),
        None,
    )

    if first_other is None or second_other is None:
        return None
    if first_other is second_other:
        return None
    if edge_key(first_other) not in beveled_keys:
        return None
    if edge_key(second_other) not in beveled_keys:
        return None

    first_direction = normalized_or_none(
        first_other.other_vert(vertex).co - vertex.co
    )
    second_direction = normalized_or_none(
        second_other.other_vert(vertex).co - vertex.co
    )
    if first_direction is None or second_direction is None:
        return None

    # Scenario 1 is a through-running bevel chain rather than a corner.
    if first_direction.dot(second_direction) > -0.15:
        return None

    return first_other, second_other


def collect_scenario_one_sources(mesh, beveled_keys):
    """
    Detect ordinary Scenario 1 spokes and the trapped one-quad-strip variant.

    Ordinary case:
    - one end of an unselected spoke meets a through-running bevel chain;
    - the opposite endpoint survives the bevel and anchors the repair.

    Trapped-strip case:
    - both ends of the same unselected spoke meet through-running bevel chains;
    - the bevel removes both original anchor vertices, so the ordinary repair
      cannot match either end;
    - this is recorded once and repaired later from an inserted midpoint.

    Both adjacent source faces must be quads, preserving the original safety
    rule that excludes preexisting n-gons.
    """
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    sources = []
    trapped_sources = []

    for spoke in bm.edges:
        if edge_key(spoke) in beveled_keys:
            continue
        if len(spoke.link_faces) != 2:
            continue
        if any(len(face.verts) != 4 for face in spoke.link_faces):
            continue

        first_vertex, second_vertex = spoke.verts
        first_endpoint = scenario_one_endpoint_data(
            first_vertex, spoke, beveled_keys
        )
        second_endpoint = scenario_one_endpoint_data(
            second_vertex, spoke, beveled_keys
        )

        if first_endpoint is not None and second_endpoint is not None:
            vector = second_vertex.co - first_vertex.co
            if vector.length_squared <= 1.0e-20:
                continue

            def endpoint_alignment(vertex, endpoint_data):
                first_edge, second_edge = endpoint_data
                first_direction = normalized_or_none(
                    first_edge.other_vert(vertex).co - vertex.co
                )
                second_direction = normalized_or_none(
                    second_edge.other_vert(vertex).co - vertex.co
                )
                if first_direction is None or second_direction is None:
                    return -1.0
                return first_direction.dot(second_direction)

            first_alignment = endpoint_alignment(first_vertex, first_endpoint)
            second_alignment = endpoint_alignment(second_vertex, second_endpoint)

            # A completely straight selected chain at both ends is an ordinary
            # quad strip, not the center junction this special case targets.
            # Requiring a genuine direction change at one end prevents the
            # false positives shown by parallel horizontal bevel rows.
            if max(first_alignment, second_alignment) <= -0.9995:
                continue

            trapped_sources.append({
                "first": first_vertex.co.copy(),
                "second": second_vertex.co.copy(),
                "direction": vector.normalized(),
                "distance": vector.length,
                "midpoint": (first_vertex.co + second_vertex.co) * 0.5,
                "first_alignment": first_alignment,
                "second_alignment": second_alignment,
            })
            continue

        for junction, destination, endpoint_data in (
            (first_vertex, second_vertex, first_endpoint),
            (second_vertex, first_vertex, second_endpoint),
        ):
            if endpoint_data is None:
                continue

            vector = junction.co - destination.co
            if vector.length_squared <= 1.0e-20:
                continue

            sources.append({
                "junction": junction.co.copy(),
                "destination": destination.co.copy(),
                "direction": vector.normalized(),
                "distance": vector.length,
            })

    bm.free()
    return sources, trapped_sources


def locate_scenario_one_middle(
    destination,
    source,
    junction_coordinate,
    distance_scale,
):
    best = None

    for edge in destination.link_edges:
        if len(edge.link_faces) != 2:
            continue

        candidate = edge.other_vert(destination)
        candidate_vector = candidate.co - destination.co
        candidate_direction = normalized_or_none(candidate_vector)

        if candidate_direction is None:
            continue

        alignment = candidate_direction.dot(source["direction"])
        if alignment < 0.70:
            continue

        junction_distance = (candidate.co - junction_coordinate).length
        normalized_junction_distance = junction_distance / max(
            distance_scale,
            1.0e-12,
        )

        if normalized_junction_distance > 0.85:
            continue

        score = (
            alignment * 5.0
            - normalized_junction_distance * 2.0
            - abs(candidate_vector.length - source["distance"])
            / max(source["distance"], 1.0e-12)
            * 0.25
        )

        if best is None or score > best[0]:
            best = (score, edge, candidate)

    if best is None:
        return None, None

    return best[1], best[2]


def repair_scenario_one_candidate(bm, middle_edge, middle, destination):
    """
    Add the two outer lines, then dissolve the old middle line.
    """
    if not middle_edge.is_valid or len(middle_edge.link_faces) != 2:
        return False

    linked_faces = list(middle_edge.link_faces)

    # These two faces are the descendants of the two source quads.
    # They should now have more than four sides.
    if any(len(face.verts) <= 4 for face in linked_faces):
        return False

    outer_vertices = [
        face_neighbor_at_vertex(face, middle, destination)
        for face in linked_faces
    ]

    if any(vertex is None for vertex in outer_vertices):
        return False

    first_outer, second_outer = outer_vertices

    if first_outer is second_outer:
        return False
    if vertices_share_edge(first_outer, destination):
        return False
    if vertices_share_edge(second_outer, destination):
        return False

    try:
        first_result = bmesh.ops.connect_verts(
            bm,
            verts=[first_outer, destination],
            check_degenerate=True,
        )
    except (RuntimeError, ValueError):
        return False

    first_edges = [
        edge for edge in first_result.get("edges", [])
        if edge.is_valid
    ]
    if not first_edges:
        return False

    try:
        second_result = bmesh.ops.connect_verts(
            bm,
            verts=[second_outer, destination],
            check_degenerate=True,
        )
    except (RuntimeError, ValueError):
        second_result = {}

    second_edges = [
        edge for edge in second_result.get("edges", [])
        if edge.is_valid
    ]

    if not second_edges:
        if first_edges:
            bmesh.ops.dissolve_edges(
                bm,
                edges=first_edges,
                use_verts=False,
                use_face_split=False,
            )
        return False

    if not middle_edge.is_valid:
        return False

    try:
        bmesh.ops.dissolve_edges(
            bm,
            edges=[middle_edge],
            use_verts=False,
            use_face_split=False,
        )
    except (RuntimeError, ValueError):
        added_edges = [
            edge for edge in first_edges + second_edges
            if edge.is_valid
        ]
        if added_edges:
            bmesh.ops.dissolve_edges(
                bm,
                edges=added_edges,
                use_verts=False,
                use_face_split=False,
            )
        return False

    return True


def locate_trapped_scenario_one_edge(bm, source):
    """Locate the surviving center edge of a spoke beveled at both ends."""
    best = None
    source_length = max(source["distance"], 1.0e-12)

    for edge in bm.edges:
        if not edge.is_valid or len(edge.link_faces) != 2:
            continue

        vector = edge.verts[1].co - edge.verts[0].co
        direction = normalized_or_none(vector)
        if direction is None:
            continue

        alignment = abs(direction.dot(source["direction"]))
        if alignment < 0.82:
            continue

        center = (edge.verts[0].co + edge.verts[1].co) * 0.5
        center_offset = (center - source["midpoint"]).length / source_length
        if center_offset > 0.30:
            continue

        line_offset = point_segment_distance(
            center,
            source["first"],
            source["second"],
        ) / source_length
        if line_offset > 0.12:
            continue

        # A bevel at both ends shortens this edge but leaves it spanning the
        # middle of the original spoke.
        length_ratio = vector.length / source_length
        if length_ratio < 0.08 or length_ratio > 1.05:
            continue

        score = alignment * 5.0 - center_offset * 4.0 - line_offset * 6.0
        if best is None or score > best[0]:
            best = (score, edge)

    return None if best is None else best[1]


def build_validated_trapped_scenario_one_result(bm, source):
    """
    Test the trapped-strip repair on a temporary BMesh.

    The repair is accepted only when it removes at least one n-gon and does
    not create any new triangles. This prevents already-correct bevel quads
    from being cut into the two innocent triangles seen in the production
    mesh, while still allowing the intended two-long-quad solution.

    Returns a replacement BMesh on success, otherwise None.
    """
    before_triangles, _before_quads, before_ngons = topology_counts(bm)
    trial = bm.copy()
    trial.verts.ensure_lookup_table()
    trial.edges.ensure_lookup_table()
    trial.faces.ensure_lookup_table()

    if not repair_trapped_scenario_one_candidate(trial, source):
        trial.free()
        return None

    trial.normal_update()
    after_triangles, _after_quads, after_ngons = topology_counts(trial)

    if after_triangles > before_triangles:
        trial.free()
        return None
    if after_ngons >= before_ngons:
        trial.free()
        return None

    return trial


def repair_trapped_scenario_one_candidate(bm, source):
    """
    Solve the one-quad strip whose two endpoints are consumed by bevels.

    No midpoint is inserted. The surviving shortened spoke is repaired once,
    using the endpoint on the straighter bevel chain as the destination and
    the endpoint on the turning chain as the middle vertex. This creates the
    two long quads of the intended result instead of a central pole.
    """
    middle_edge = locate_trapped_scenario_one_edge(bm, source)
    if middle_edge is None or not middle_edge.is_valid:
        return False

    first_post, second_post = middle_edge.verts

    # Match the two surviving edge endpoints to the original spoke endpoints.
    direct_cost = (
        (first_post.co - source["first"]).length
        + (second_post.co - source["second"]).length
    )
    swapped_cost = (
        (first_post.co - source["second"]).length
        + (second_post.co - source["first"]).length
    )
    if direct_cost <= swapped_cost:
        post_for_first, post_for_second = first_post, second_post
    else:
        post_for_first, post_for_second = second_post, first_post

    # The desired anchor is on the straighter selected chain. The turning end
    # supplies the middle vertex whose old spoke edge is replaced by two outer
    # connections.
    if source["first_alignment"] <= source["second_alignment"]:
        destination = post_for_first
        middle = post_for_second
    else:
        destination = post_for_second
        middle = post_for_first

    return repair_scenario_one_candidate(
        bm,
        middle_edge,
        middle,
        destination,
    )

# -----------------------------------------------------------------------------
# Scenario 2: pre-split and post-Bevel cleanup
# -----------------------------------------------------------------------------

def collect_scenario_two_pairs(bm, beveled_keys):
    """
    Find a quad opposite a corner formed by inferred beveled edges.

    At the candidate corner:
    - the quad's two incident edges are not beveled;
    - at least three other incident edges are inferred bevel edges, making a
      genuine beveled T-junction rather than a simple two-edge bend;
    - the beveled edges include a corner rather than only straight continuation.

    The quad is split from the corner to its opposite vertex before beveling.
    Requiring a quad means preexisting n-gons are ignored.
    """
    candidates_by_face = {}

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    for corner in bm.verts:
        local_beveled_edges = [
            edge for edge in corner.link_edges
            if edge_key(edge) in beveled_keys
        ]

        # Scenario 2 is specifically a beveled T-junction. A normal bend has
        # only two bevel-target edges and must not pre-split the opposite quad;
        # otherwise the temporary diagonal can survive as two innocent
        # triangles on an uninterrupted bevel.
        if len(local_beveled_edges) < 3:
            continue

        # Confirm that at least one pair of inferred bevel edges actually
        # makes a corner instead of continuing almost straight through.
        has_corner_pair = False

        for first_index in range(len(local_beveled_edges)):
            first_edge = local_beveled_edges[first_index]
            first_direction = normalized_or_none(
                first_edge.other_vert(corner).co - corner.co
            )
            if first_direction is None:
                continue

            for second_index in range(
                first_index + 1,
                len(local_beveled_edges),
            ):
                second_edge = local_beveled_edges[second_index]
                second_direction = normalized_or_none(
                    second_edge.other_vert(corner).co - corner.co
                )
                if second_direction is None:
                    continue

                dot = first_direction.dot(second_direction)

                # Reject almost straight-through and almost overlapping pairs.
                if -0.92 < dot < 0.92:
                    has_corner_pair = True
                    break

            if has_corner_pair:
                break

        if not has_corner_pair:
            continue

        for face in corner.link_faces:
            if len(face.verts) != 4:
                continue

            first_incident, second_incident = incident_edges_in_face(
                face,
                corner,
            )

            if first_incident is None or second_incident is None:
                continue

            # The target quad lies in the sector opposite the two beveled
            # corner edges. Its own two corner edges are therefore not beveled.
            if edge_key(first_incident) in beveled_keys:
                continue
            if edge_key(second_incident) in beveled_keys:
                continue

            corner_loop = next(
                (loop for loop in face.loops if loop.vert is corner),
                None,
            )
            if corner_loop is None:
                continue

            opposite = corner_loop.link_loop_next.link_loop_next.vert

            if opposite is corner or vertices_share_edge(corner, opposite):
                continue

            candidates_by_face.setdefault(face, []).append(
                (corner, opposite)
            )

    planned_pairs = []
    ambiguous_faces = 0

    for face, pairs in candidates_by_face.items():
        unique_pairs = {}
        for corner, opposite in pairs:
            key = tuple(sorted((corner.index, opposite.index)))
            unique_pairs[key] = (corner, opposite)

        if len(unique_pairs) != 1:
            ambiguous_faces += 1
            continue

        planned_pairs.append(next(iter(unique_pairs.values())))

    return planned_pairs, ambiguous_faces


def split_scenario_two_quads(mesh, beveled_keys):
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    planned_pairs, ambiguous_faces = collect_scenario_two_pairs(
        bm,
        beveled_keys,
    )

    split_records = []

    for corner, opposite in planned_pairs:
        if not corner.is_valid or not opposite.is_valid:
            continue
        if vertices_share_edge(corner, opposite):
            continue

        shared_quads = [
            face
            for face in set(corner.link_faces).intersection(
                opposite.link_faces
            )
            if face.is_valid and len(face.verts) == 4
        ]

        if len(shared_quads) != 1:
            continue

        diagonal = corner.co - opposite.co
        if diagonal.length_squared <= 1.0e-20:
            continue

        try:
            result = bmesh.ops.connect_verts(
                bm,
                verts=[corner, opposite],
                check_degenerate=True,
            )
        except (RuntimeError, ValueError):
            continue

        created_edges = [
            edge for edge in result.get("edges", [])
            if edge.is_valid
        ]
        if not created_edges:
            continue

        source_face = shared_quads[0]
        split_records.append({
            "corner": corner.co.copy(),
            "opposite": opposite.co.copy(),
            "direction": diagonal.normalized(),
            "distance": diagonal.length,
            "face_center": source_face.calc_center_median().copy(),
            "face_vertices": tuple(vertex.co.copy() for vertex in source_face.verts),
        })

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    return len(planned_pairs), split_records, ambiguous_faces


def repair_scenario_two_triangle_pairs(bm, split_records):
    """
    Convert genuine post-bevel triangle pairs into quads.

    The pre-bevel Scenario 2 diagonal normally becomes quad topology after the
    Bevel modifier is applied. Any unwanted triangles that remain are repaired
    only when two triangles share one internal edge and their combined boundary
    contains exactly four unique vertices. Joining that pair removes the shared
    diagonal without merging either triangle into a larger neighboring face,
    so the result is a quad rather than an n-gon.
    """
    candidate_pairs = []
    used_faces = set()
    matched_triangles = 0

    for record in split_records:
        segment_length = max(record["distance"], 1.0e-12)
        region_radius = segment_length * 0.40
        endpoint_radius = segment_length * 0.55

        for edge in bm.edges:
            if not edge.is_valid or len(edge.link_faces) != 2:
                continue

            first, second = edge.link_faces
            if not first.is_valid or not second.is_valid:
                continue
            if len(first.verts) != 3 or len(second.verts) != 3:
                continue
            if id(first) in used_faces or id(second) in used_faces:
                continue

            unique_verts = set(first.verts) | set(second.verts)
            if len(unique_verts) != 4:
                continue

            center = (first.calc_center_median() + second.calc_center_median()) * 0.5
            line_distance = point_segment_distance(
                center,
                record["opposite"],
                record["corner"],
            )
            if line_distance > region_radius:
                continue

            nearest_endpoint = min(
                (center - record["corner"]).length,
                (center - record["opposite"]).length,
            )
            if nearest_endpoint > endpoint_radius:
                continue

            # The shared edge must be an interior diagonal of the four-vertex
            # patch. join_triangles removes exactly this edge and preserves the
            # four outer boundary edges as a quad.
            candidate_pairs.append((first, second))
            used_faces.add(id(first))
            used_faces.add(id(second))
            matched_triangles += 2

    joined = 0
    for first, second in candidate_pairs:
        if not first.is_valid or not second.is_valid:
            continue
        try:
            result = bmesh.ops.join_triangles(
                bm,
                faces=[first, second],
                cmp_seam=False,
                cmp_sharp=False,
                cmp_uvs=False,
                cmp_vcols=False,
                cmp_materials=False,
                angle_face_threshold=3.141592653589793,
                angle_shape_threshold=3.141592653589793,
            )
        except (RuntimeError, ValueError, TypeError):
            continue

        if result.get("faces"):
            joined += 1

    return matched_triangles, joined

# -----------------------------------------------------------------------------
# Scenario 2 fallback: malformed triangle/n-gon repair
# -----------------------------------------------------------------------------

def collect_scenario_two_fallback_edges(bm, split_records):
    """Find the post-bevel Scenario 2 fallback signature.

    A failed Scenario 2 corner produces one triangle beside one n-gon.  The
    edge shared by those two faces is the faulty bevel-created connection.  A
    valid repair is possible only when dissolving that edge creates a
    six-vertex patch, which can be split into exactly two quads.
    """
    candidates = []
    seen = set()

    for edge in bm.edges:
        if not edge.is_valid or len(edge.link_faces) != 2:
            continue

        first, second = edge.link_faces
        sizes = sorted((len(first.verts), len(second.verts)))
        if sizes[0] != 3 or sizes[1] <= 4:
            continue

        # Two quads joined across one diagonal have six unique boundary
        # vertices.  Reject larger triangle+n-gon patches because no single
        # replacement edge can turn them into exactly two quads.
        unique_verts = set(first.verts) | set(second.verts)
        if len(unique_verts) != 6:
            continue

        edge_center = (edge.verts[0].co + edge.verts[1].co) * 0.5
        patch_center = (
            first.calc_center_median() + second.calc_center_median()
        ) * 0.5

        best_record = None
        best_score = None
        for record in split_records:
            scale = max(record["distance"], 1.0e-12)

            # The malformed corner must remain inside the original Scenario 2
            # quad region.  Use both the recorded face center and diagonal
            # segment so this works on skewed and non-planar production meshes.
            center_error = (patch_center - record["face_center"]).length / scale
            line_error = point_segment_distance(
                edge_center,
                record["opposite"],
                record["corner"],
            ) / scale
            endpoint_error = min(
                (edge_center - record["corner"]).length,
                (edge_center - record["opposite"]).length,
            ) / scale

            score = center_error + line_error * 0.65 + endpoint_error * 0.20
            if center_error > 1.05 or line_error > 0.70:
                continue
            if best_score is None or score < best_score:
                best_score = score
                best_record = record

        if best_record is None:
            continue

        key = tuple(sorted((edge.verts[0].index, edge.verts[1].index)))
        if key in seen:
            continue
        seen.add(key)
        candidates.append((edge, best_record))

    return candidates


def trial_scenario_two_fallback_repair(bm, edge_index):
    """Return the best replacement diagonal for a triangle+n-gon patch.

    The faulty shared edge is dissolved first.  Every legal diagonal of the
    resulting six-sided face is tested.  A result qualifies only if the local
    patch becomes exactly two quads and the global triangle/n-gon counts both
    decrease.
    """
    base = bm.copy()
    base.verts.ensure_lookup_table()
    base.edges.ensure_lookup_table()
    base.faces.ensure_lookup_table()

    if edge_index >= len(base.edges):
        base.free()
        return None

    bad_edge = base.edges[edge_index]
    if not bad_edge.is_valid or len(bad_edge.link_faces) != 2:
        base.free()
        return None

    linked = list(bad_edge.link_faces)
    if sorted(len(face.verts) for face in linked)[0] != 3:
        base.free()
        return None
    if len(set(linked[0].verts) | set(linked[1].verts)) != 6:
        base.free()
        return None

    before_triangles, before_quads, before_ngons = topology_counts(base)

    patch_coordinates = [vertex.co.copy() for vertex in (set(linked[0].verts) | set(linked[1].verts))]

    try:
        result = bmesh.ops.dissolve_edges(
            base,
            edges=[bad_edge],
            use_verts=False,
            use_face_split=False,
        )
    except (RuntimeError, ValueError):
        base.free()
        return None

    merged_faces = [
        face for face in result.get("region", [])
        if getattr(face, "is_valid", False) and len(face.verts) == 6
    ]
    if not merged_faces:
        # Blender versions differ in the dissolve result dictionary. Match the
        # six-sided face by the six original patch coordinates rather than by
        # assuming it is the only six-sided face in the whole production mesh.
        coordinate_tolerance = max(
            ((patch_coordinates[0] - coordinate).length for coordinate in patch_coordinates[1:]),
            default=1.0,
        ) * 1.0e-6
        for face in base.faces:
            if not face.is_valid or len(face.verts) != 6:
                continue
            if all(
                min((vertex.co - coordinate).length for vertex in face.verts) <= coordinate_tolerance
                for coordinate in patch_coordinates
            ):
                merged_faces.append(face)
    if len(merged_faces) != 1:
        base.free()
        return None

    merged = merged_faces[0]
    boundary = list(merged.verts)
    if len(boundary) != 6:
        base.free()
        return None

    # Save coordinates because copied BMesh indices are not needed by the real
    # mesh and coordinates survive the speculative operation reliably.
    possible_pairs = []
    for i, first in enumerate(boundary):
        for j in range(i + 1, len(boundary)):
            second = boundary[j]
            if vertices_share_edge(first, second):
                continue
            possible_pairs.append((first.co.copy(), second.co.copy()))

    base.free()
    best = None

    for first_co, second_co in possible_pairs:
        trial = bm.copy()
        trial.verts.ensure_lookup_table()
        trial.edges.ensure_lookup_table()
        trial.faces.ensure_lookup_table()
        if edge_index >= len(trial.edges):
            trial.free()
            continue

        edge = trial.edges[edge_index]
        if not edge.is_valid or len(edge.link_faces) != 2:
            trial.free()
            continue

        try:
            dissolve_result = bmesh.ops.dissolve_edges(
                trial,
                edges=[edge],
                use_verts=False,
                use_face_split=False,
            )
        except (RuntimeError, ValueError):
            trial.free()
            continue

        first = min(trial.verts, key=lambda vert: (vert.co - first_co).length, default=None)
        second = min(trial.verts, key=lambda vert: (vert.co - second_co).length, default=None)
        if first is None or second is None or first is second or vertices_share_edge(first, second):
            trial.free()
            continue
        if len(set(first.link_faces).intersection(second.link_faces)) != 1:
            trial.free()
            continue

        try:
            connect_result = bmesh.ops.connect_verts(
                trial,
                verts=[first, second],
                check_degenerate=True,
            )
        except (RuntimeError, ValueError):
            trial.free()
            continue

        new_edges = [item for item in connect_result.get("edges", []) if item.is_valid]
        new_faces = [item for item in connect_result.get("faces", []) if item.is_valid]
        if len(new_edges) != 1:
            trial.free()
            continue

        # Depending on Blender version, connect_verts may not return faces.
        # The two faces linked to the new diagonal are the authoritative patch.
        patch_faces = list(new_edges[0].link_faces)
        if len(patch_faces) != 2 or any(len(face.verts) != 4 for face in patch_faces):
            trial.free()
            continue

        trial.normal_update()
        after_triangles, after_quads, after_ngons = topology_counts(trial)
        new_length = new_edges[0].calc_length()

        valid = (
            after_triangles == before_triangles - 1
            and after_ngons == before_ngons - 1
            and after_quads >= before_quads + 2
        )
        if not valid:
            trial.free()
            continue

        score = new_length
        if best is None or score < best[0]:
            best = (score, first_co, second_co)
        trial.free()

    if best is None:
        return None
    return {
        "first": best[1],
        "second": best[2],
    }


def commit_scenario_two_fallback_repair(bm, edge, first_coordinate, second_coordinate):
    """Replace one verified triangle+n-gon edge with its quad-producing diagonal."""
    if not edge.is_valid or len(edge.link_faces) != 2:
        return False

    try:
        bmesh.ops.dissolve_edges(
            bm,
            edges=[edge],
            use_verts=False,
            use_face_split=False,
        )
    except (RuntimeError, ValueError):
        return False

    first = min(bm.verts, key=lambda vert: (vert.co - first_coordinate).length, default=None)
    second = min(bm.verts, key=lambda vert: (vert.co - second_coordinate).length, default=None)
    if first is None or second is None or first is second or vertices_share_edge(first, second):
        return False
    if len(set(first.link_faces).intersection(second.link_faces)) != 1:
        return False

    try:
        result = bmesh.ops.connect_verts(
            bm,
            verts=[first, second],
            check_degenerate=True,
        )
    except (RuntimeError, ValueError):
        return False

    new_edges = [item for item in result.get("edges", []) if item.is_valid]
    if len(new_edges) != 1:
        return False
    return (
        len(new_edges[0].link_faces) == 2
        and all(len(face.verts) == 4 for face in new_edges[0].link_faces)
    )


def repair_scenario_two_fallback_candidates(bm, split_records):
    """Repair Scenario 2 corners that bevel into one triangle plus one n-gon."""
    if not split_records:
        return 0, 0

    matched = 0
    repaired = 0
    processed_centers = []

    while True:
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        candidates = collect_scenario_two_fallback_edges(bm, split_records)
        candidate = None
        for edge, record in candidates:
            center = (edge.verts[0].co + edge.verts[1].co) * 0.5
            scale = max(record["distance"], 1.0e-12)
            if any((center - old_center).length <= scale * 1.0e-5 for old_center in processed_centers):
                continue
            candidate = (edge, record, center.copy())
            break

        if candidate is None:
            break

        edge, record, center = candidate
        processed_centers.append(center)
        matched += 1

        trial = trial_scenario_two_fallback_repair(bm, edge.index)
        if trial is None:
            continue

        # Relocate the same malformed edge after the trial copy was discarded.
        current_edge = min(
            (
                item for item in bm.edges
                if item.is_valid
                and len(item.link_faces) == 2
                and sorted(len(face.verts) for face in item.link_faces)[0] == 3
                and sorted(len(face.verts) for face in item.link_faces)[1] > 4
            ),
            key=lambda item: (((item.verts[0].co + item.verts[1].co) * 0.5) - center).length,
            default=None,
        )
        if current_edge is None:
            continue

        if commit_scenario_two_fallback_repair(
            bm,
            current_edge,
            trial["first"],
            trial["second"],
        ):
            repaired += 1

    return matched, repaired


# -----------------------------------------------------------------------------
# Experimental T-junction diagonal reroute
# -----------------------------------------------------------------------------

def _weighted_corner_edges(vertex, diagonal, linked_faces, beveled_keys):
    """Return the two weighted face-boundary edges forming a corner, or None."""
    local_edges = []
    for face in linked_faces:
        for edge in incident_edges_in_face(face, vertex):
            if edge is not None and edge is not diagonal and edge not in local_edges:
                local_edges.append(edge)

    if len(local_edges) != 2:
        return None
    if any(edge_key(edge) not in beveled_keys for edge in local_edges):
        return None

    directions = [
        normalized_or_none(edge.other_vert(vertex).co - vertex.co)
        for edge in local_edges
    ]
    if any(direction is None for direction in directions):
        return None

    # A through-running weighted chain belongs to Scenario 1. This case needs
    # an actual weighted corner at the end of the inherited diagonal.
    if directions[0].dot(directions[1]) <= -0.15:
        return None
    return local_edges


def _is_stable_diagonal_junction(vertex, diagonal, beveled_keys):
    """
    Accept an inserted-loop intersection as the stable end of the diagonal.

    The destination must continue the diagonal approximately straight through
    the vertex and also have at least two non-diagonal side edges. This keeps
    the rule narrow while allowing a support loop to split the original
    diagonal into multiple segments.
    """
    diagonal_direction = normalized_or_none(diagonal.other_vert(vertex).co - vertex.co)
    if diagonal_direction is None:
        return False

    continuation_found = False
    side_edges = 0
    for edge in vertex.link_edges:
        if edge is diagonal or edge_key(edge) in beveled_keys:
            continue
        direction = normalized_or_none(edge.other_vert(vertex).co - vertex.co)
        if direction is None:
            continue
        alignment = diagonal_direction.dot(direction)
        if alignment <= -0.985:
            continuation_found = True
        elif abs(alignment) < 0.94:
            side_edges += 1

    return continuation_found and side_edges >= 2


def collect_t_junction_diagonal_sources(mesh, beveled_keys, bevel_width):
    """
    Record the local inherited diagonal segment beside a beveled T corner.

    The opposite endpoint may be either another weighted corner (the original
    supported case) or a stable pass-through junction created when an extra
    loop splits the diagonal. The latter allows the same local repair to work
    without depending on the diagonal reaching the distant outer corner.
    """
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    records = []
    seen = set()
    for diagonal in bm.edges:
        if edge_key(diagonal) in beveled_keys:
            continue
        if len(diagonal.link_faces) != 2:
            continue
        if any(len(face.verts) != 4 for face in diagonal.link_faces):
            continue

        endpoint_kinds = []
        endpoint_scales = []
        for vertex in diagonal.verts:
            corner_edges = _weighted_corner_edges(
                vertex, diagonal, diagonal.link_faces, beveled_keys
            )
            if corner_edges is not None:
                endpoint_kinds.append('CORNER')
                endpoint_scales.append(min(edge.calc_length() for edge in corner_edges))
            elif _is_stable_diagonal_junction(vertex, diagonal, beveled_keys):
                endpoint_kinds.append('JUNCTION')
                endpoint_scales.append(min(
                    edge.calc_length() for edge in vertex.link_edges if edge is not diagonal
                ))
            else:
                endpoint_kinds.append(None)
                endpoint_scales.append(0.0)

        # At least one endpoint must be the beveled corner being reconstructed.
        # The other can be another corner or an inserted-loop junction.
        if 'CORNER' not in endpoint_kinds:
            continue
        if any(kind is None for kind in endpoint_kinds):
            continue
        if endpoint_kinds.count('JUNCTION') > 1:
            continue

        diagonal_length = diagonal.calc_length()
        if diagonal_length <= 1.0e-12:
            continue

        key = tuple(sorted(tuple(round(value, 9) for value in vertex.co) for vertex in diagonal.verts))
        if key in seen:
            continue
        seen.add(key)

        search_radius = max(
            float(bevel_width) * 8.0,
            min(scale for scale in endpoint_scales if scale > 0.0) * 0.35,
        )
        search_radius = min(search_radius, diagonal_length * 0.65)

        # Put the beveled corner first when only one endpoint is a corner. The
        # repair tests both orientations, but this makes diagnostics consistent.
        if endpoint_kinds[0] == 'CORNER' or endpoint_kinds[1] != 'CORNER':
            first_vertex, second_vertex = diagonal.verts
        else:
            second_vertex, first_vertex = diagonal.verts

        records.append({
            "first": first_vertex.co.copy(),
            "second": second_vertex.co.copy(),
            "distance": diagonal_length,
            "search_radius": max(search_radius, diagonal_length * 1.0e-5),
            "destination_kind": endpoint_kinds[1] if first_vertex is diagonal.verts[0] else endpoint_kinds[0],
        })

    bm.free()
    return records


def _face_irregularity_score(bm):
    """Lower is better; quads are free and larger deviations cost more."""
    penalty = 0
    triangles = 0
    ngons = 0
    for face in bm.faces:
        sides = len(face.verts)
        if sides == 4:
            continue
        penalty += (abs(sides - 4) + 1) ** 2
        if sides == 3:
            triangles += 1
        elif sides > 4:
            ngons += 1
    return penalty, triangles, ngons


def _vertices_near_coordinate(bm, coordinate, radius, minimum_count=2, limit=10):
    radius_squared = radius * radius
    nearby = [
        vertex for vertex in bm.verts
        if vertex.is_valid and (vertex.co - coordinate).length_squared <= radius_squared
    ]
    if len(nearby) >= minimum_count:
        return sorted(
            nearby,
            key=lambda vertex: (vertex.co - coordinate).length_squared,
        )[:limit]

    # Arc miters can move the replacement vertices farther from the original
    # corner than the nominal bevel width suggests. Fall back to the nearest
    # local vertices rather than failing the match outright.
    return sorted(
        (vertex for vertex in bm.verts if vertex.is_valid),
        key=lambda vertex: (vertex.co - coordinate).length_squared,
    )[:limit]


def _ordered_patch_boundary(bridge_edge):
    """Return the ordered outer boundary of the two faces sharing bridge_edge."""
    if not bridge_edge.is_valid or len(bridge_edge.link_faces) != 2:
        return None

    boundary_edges = []
    for face in bridge_edge.link_faces:
        for edge in face.edges:
            if edge is bridge_edge:
                continue
            if edge not in boundary_edges:
                boundary_edges.append(edge)

    # The union must be one simple polygon: every boundary vertex has degree 2.
    adjacency = {}
    for edge in boundary_edges:
        for vertex in edge.verts:
            adjacency.setdefault(vertex, []).append(edge)
    if not adjacency or any(len(edges) != 2 for edges in adjacency.values()):
        return None

    first = min(adjacency, key=lambda vertex: vertex.index)
    ordered = [first]
    previous_edge = None
    current = first

    for _ in range(len(adjacency) + 1):
        choices = [edge for edge in adjacency[current] if edge is not previous_edge]
        if not choices:
            return None
        edge = choices[0]
        nxt = edge.other_vert(current)
        if nxt is first:
            return ordered if len(ordered) == len(adjacency) else None
        if nxt in ordered:
            return None
        ordered.append(nxt)
        previous_edge = edge
        current = nxt

    return None


def _cyclic_adjacent(first, second, count):
    return (first - second) % count in {1, count - 1}


def _diagonals_cross(first, second, third, fourth, count):
    """Return whether two polygon-index diagonals cross in the polygon interior."""
    if len({first, second, third, fourth}) < 4:
        return False

    def between(value, start, end):
        if start < end:
            return start < value < end
        return value > start or value < end

    return between(third, first, second) != between(fourth, first, second) and \
           between(first, third, fourth) != between(second, third, fourth)


def _quadrangulation_diagonal_sets(vertex_count):
    """Enumerate non-crossing diagonal sets capable of partitioning into quads."""
    if vertex_count < 4 or vertex_count % 2:
        return []
    required = (vertex_count - 4) // 2
    if required == 0:
        return [tuple()]

    diagonals = [
        (first, second)
        for first in range(vertex_count)
        for second in range(first + 1, vertex_count)
        if not _cyclic_adjacent(first, second, vertex_count)
    ]

    from itertools import combinations
    results = []
    for candidate in combinations(diagonals, required):
        if any(
            _diagonals_cross(*candidate[a], *candidate[b], vertex_count)
            for a in range(len(candidate))
            for b in range(a + 1, len(candidate))
        ):
            continue
        results.append(candidate)
    return results


def _trial_t_junction_patch(bm, bridge_edge, boundary_vertices, diagonal_pairs):
    """Dissolve the inherited bridge and rebuild the merged patch with diagonals."""
    trial = bm.copy()
    trial.verts.ensure_lookup_table()
    trial.edges.ensure_lookup_table()
    trial.faces.ensure_lookup_table()

    bridge_index = bridge_edge.index
    boundary_indices = [vertex.index for vertex in boundary_vertices]
    if bridge_index >= len(trial.edges) or any(
        index >= len(trial.verts) for index in boundary_indices
    ):
        trial.free()
        return None

    trial_bridge = trial.edges[bridge_index]
    if not trial_bridge.is_valid or len(trial_bridge.link_faces) != 2:
        trial.free()
        return None

    old_faces = list(trial_bridge.link_faces)
    try:
        bmesh.ops.dissolve_edges(
            trial,
            edges=[trial_bridge],
            use_verts=False,
            use_face_split=False,
        )
    except (RuntimeError, ValueError):
        trial.free()
        return None

    for first_position, second_position in diagonal_pairs:
        first_vertex = trial.verts[boundary_indices[first_position]]
        second_vertex = trial.verts[boundary_indices[second_position]]
        if not first_vertex.is_valid or not second_vertex.is_valid:
            trial.free()
            return None
        existing = next(
            (
                edge for edge in first_vertex.link_edges
                if edge.is_valid and edge.other_vert(first_vertex) is second_vertex
            ),
            None,
        )
        if existing is not None:
            trial.free()
            return None
        try:
            result = bmesh.ops.connect_verts(
                trial,
                verts=[first_vertex, second_vertex],
                check_degenerate=True,
            )
        except (RuntimeError, ValueError):
            trial.free()
            return None
        if not any(edge.is_valid for edge in result.get("edges", [])):
            trial.free()
            return None

    trial.normal_update()

    # The rebuilt local patch must consist entirely of the expected quads.
    boundary_set = {trial.verts[index] for index in boundary_indices}
    local_faces = {
        face
        for vertex in boundary_set
        for face in vertex.link_faces
        if face.is_valid and set(face.verts).issubset(boundary_set)
    }
    expected_faces = len(diagonal_pairs) + 1
    if len(local_faces) != expected_faces or any(
        len(face.verts) != 4 for face in local_faces
    ):
        trial.free()
        return None

    return trial


def _trial_t_junction_outer_connections(bm, bridge_edge, middle, destination):
    """Try the Scenario 1-style outer-edge repair on a copied BMesh."""
    trial = bm.copy()
    trial.verts.ensure_lookup_table()
    trial.edges.ensure_lookup_table()
    trial.faces.ensure_lookup_table()

    bridge_index = bridge_edge.index
    middle_index = middle.index
    destination_index = destination.index
    if (
        bridge_index >= len(trial.edges)
        or middle_index >= len(trial.verts)
        or destination_index >= len(trial.verts)
    ):
        trial.free()
        return None

    trial_bridge = trial.edges[bridge_index]
    trial_middle = trial.verts[middle_index]
    trial_destination = trial.verts[destination_index]
    if not repair_scenario_one_candidate(
        trial,
        trial_bridge,
        trial_middle,
        trial_destination,
    ):
        trial.free()
        return None

    trial.normal_update()
    return trial


def repair_t_junction_diagonals(bm, records):
    """
    Repair the T-junction by using the same topology operation as Scenario 1:
    create the two outer connections from the diagonal's destination endpoint,
    then dissolve the inherited center diagonal.
    """
    matched = 0
    repaired = 0

    for record in records:
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        first_group = _vertices_near_coordinate(
            bm, record["first"], record["search_radius"]
        )
        second_group = _vertices_near_coordinate(
            bm, record["second"], record["search_radius"]
        )
        if len(first_group) < 2 or len(second_group) < 2:
            continue

        first_ids = {vertex.index for vertex in first_group}
        second_ids = {vertex.index for vertex in second_group}
        bridge_edges = [
            edge for edge in bm.edges
            if edge.is_valid
            and len(edge.link_faces) == 2
            and (
                (edge.verts[0].index in first_ids and edge.verts[1].index in second_ids)
                or (edge.verts[1].index in first_ids and edge.verts[0].index in second_ids)
            )
        ]
        if not bridge_edges:
            continue

        matched += 1
        best = None
        baseline_penalty = _face_irregularity_score(bm)

        for bridge in bridge_edges:
            for middle, destination in (
                (bridge.verts[0], bridge.verts[1]),
                (bridge.verts[1], bridge.verts[0]),
            ):
                trial = _trial_t_junction_outer_connections(
                    bm,
                    bridge,
                    middle,
                    destination,
                )
                if trial is None:
                    continue

                penalty = _face_irregularity_score(trial)
                added_length = 0.0
                for face in destination.link_faces:
                    outer = face_neighbor_at_vertex(face, middle, destination)
                    if outer is not None:
                        added_length += (outer.co - destination.co).length

                # Prefer fewer irregular faces first, then the compact repair.
                rank = (penalty, added_length)
                if best is None or rank < best[0]:
                    if best is not None:
                        best[1].free()
                    best = (rank, trial)
                else:
                    trial.free()

        if best is None:
            continue

        # The operation must not make the global face-type score worse.
        if best[0][0] > baseline_penalty:
            best[1].free()
            continue

        replacement = best[1]
        bm.free()
        bm = replacement
        repaired += 1

    return bm, matched, repaired

# -----------------------------------------------------------------------------
# Operation validation, reporting, and Blender operator
# -----------------------------------------------------------------------------

def collect_repair_skip_reasons(modifiers):
    """Return unsupported setup reasons without modifying the object."""
    modifier = modifiers[0]
    reasons = []

    if len(modifiers) > 1:
        reasons.append(
            f"found {len(modifiers)} Bevel modifiers (exactly one is required)"
        )
    if modifier.miter_outer != SUPPORTED_OUTER_MITER:
        reasons.append("outer miter is not Arc")
    if modifier.segments != SUPPORTED_BEVEL_SEGMENTS:
        reasons.append(
            f"segments is {modifier.segments}, not {SUPPORTED_BEVEL_SEGMENTS}"
        )

    return reasons


def format_success_report(
    modifier_name,
    source_method,
    beveled_edge_count,
    original_edge_count,
    cleared_edge_count,
    scenario_one_source_count,
    scenario_one_matched,
    scenario_one_fixed,
    scenario_one_trapped_count,
    scenario_one_trapped_fixed,
    scenario_two_candidate_count,
    scenario_two_split_count,
    scenario_two_ambiguous_count,
    scenario_two_faulty_matched,
    scenario_two_faulty_joined,
    scenario_two_fallback_matched,
    scenario_two_fallback_repaired,
    t_junction_candidate_count=0,
    t_junction_matched=0,
    t_junction_repaired=0,
    seam_source_count=0,
    seam_path_count=0,
    seam_edge_count=0,
    analysis_time=0.0,
    bevel_apply_time=0.0,
    scenario_one_time=0.0,
    scenario_two_time=0.0,
    t_junction_time=0.0,
    uv_seam_time=0.0,
    cleanup_time=0.0,
    total_time=0.0,
):
    """Build the final operator report without changing operation state."""
    return (
        f"Applied '{modifier_name}' once. Source: {source_method}; "
        f"bevel edges: {beveled_edge_count}/{original_edge_count}; "
        f"cleared bevel marks on {cleared_edge_count} edges. "
        f"Scenario 1: {scenario_one_source_count} ordinary eligible, "
        f"{scenario_one_matched} matched, {scenario_one_fixed} fixed; "
        f"{scenario_one_trapped_count} trapped strips, "
        f"{scenario_one_trapped_fixed} fixed. "
        f"Scenario 2: {scenario_two_candidate_count} eligible, "
        f"{scenario_two_split_count} split, "
        f"{scenario_two_ambiguous_count} ambiguous, "
        f"{scenario_two_faulty_matched} faulty post-bevel triangles matched, "
        f"{scenario_two_faulty_joined} triangle pairs joined into quads. "
        f"Scenario 2 fallback: {scenario_two_fallback_matched} "
        f"malformed triangle/n-gon corners matched, "
        f"{scenario_two_fallback_repaired} rerouted to local connections. "
        f"T-junction repair: {t_junction_candidate_count} eligible, "
        f"{t_junction_matched} matched, {t_junction_repaired} rerouted. "
        f"UV seams: {seam_source_count} bevel paths, "
        f"{seam_path_count} restored, {seam_edge_count} center edges marked. "
        f"Timing — analysis + pre-split: {analysis_time:.4f}s, "
        f"Bevel apply: {bevel_apply_time:.4f}s, "
        f"Scenario 1 repair: {scenario_one_time:.4f}s, "
        f"Scenario 2 repair: {scenario_two_time:.4f}s, "
        f"T-junction repair: {t_junction_time:.4f}s, "
        f"UV seam marking: {uv_seam_time:.4f}s, "
        f"mesh write + cleanup: {cleanup_time:.4f}s; "
        f"total: {total_time:.4f}s."
    )


class MODUS_OT_finalize_bevel(bpy.types.Operator):
    bl_idname = "modus.apply_bevel"
    bl_label = "Finalize Bevel"
    bl_description = (
        "Apply Bevel modifiers, repair supported n-gon junctions, clear bevel marks, "
        "and optionally mark bevel center lines as UV seams"
    )
    bl_options = {'REGISTER', 'UNDO'}

    mark_uvs: bpy.props.BoolProperty(
        name="Mark UVs",
        description=(
            "Mark the middle line of every beveled source edge as a UV seam "
            "after applying the supported two-segment bevel"
        ),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return get_active_mesh_object(context) is not None

    def execute(self, context):
        total_start = time.perf_counter()
        obj = get_active_mesh_object(context)
        if obj is None:
            self.report({'ERROR'}, "Select one mesh object.")
            return {'CANCELLED'}

        prepare_active_object(context, obj)

        modifiers = bevel_modifiers(obj)
        if not modifiers:
            self.report({'ERROR'}, "The active object has no Bevel modifier.")
            return {'CANCELLED'}

        modifier = modifiers[0]
        skip_reasons = collect_repair_skip_reasons(modifiers)
        profile_warning = (
            abs(float(modifier.profile) - SUPPORTED_PROFILE)
            > PROFILE_TOLERANCE
        )

        # Unsupported configurations still apply the Bevel modifier(s), but none
        # of the topology probing or repair logic is allowed to touch the mesh.
        if skip_reasons:
            try:
                if len(modifiers) > 1:
                    applied_names = apply_bevel_modifiers_in_stack_order(context, obj)
                else:
                    applied_names = [apply_first_bevel_modifier(context, obj)]
            except RuntimeError as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}

            cleared_edges = clear_all_bevel_edge_weights(obj.data)
            applied_text = ", ".join(f"'{name}'" for name in applied_names)
            warning_detail = "; ".join(skip_reasons)
            if self.mark_uvs:
                warning_detail += "; UV seam preservation requires the supported single two-segment Bevel setup"
            warning_message = (
                f"Applied Bevel modifier(s) {applied_text}, but skipped all "
                f"n-gon operations: {warning_detail}. "
                f"Cleared bevel marks on {cleared_edges} edges."
            )
            self.report({'WARNING'}, warning_message)
            show_viewport_warning(
                (
                    "BEVEL APPLIED — N-GON REPAIR SKIPPED",
                    warning_detail,
                ),
                duration=7.0,
            )
            return {'FINISHED'}

        if profile_warning:
            profile_message = (
                f"Bevel profile is {modifier.profile:g}, not 1.0. "
                "The n-gon operation will still be attempted."
            )
            self.report({'WARNING'}, profile_message)
            show_viewport_warning(
                (
                    "BEVEL PROFILE WARNING",
                    f"Profile is {modifier.profile:g}; attempting n-gon repair.",
                ),
                duration=6.0,
            )

        original_coordinates = [
            vertex.co.copy() for vertex in obj.data.vertices
        ]
        post_lookup_tolerance = mesh_scale_tolerance(original_coordinates)

        beveled_keys, original_edge_count, source_method = (
            collect_beveled_edge_keys(obj, modifier)
        )

        seam_records = []
        if self.mark_uvs:
            seam_records = collect_beveled_edges_for_seams(
                obj.data,
                beveled_keys,
            )

        (
            scenario_one_sources,
            scenario_one_trapped_sources,
        ) = collect_scenario_one_sources(
            obj.data,
            beveled_keys,
        )

        t_junction_records = collect_t_junction_diagonal_sources(
            obj.data,
            beveled_keys,
            modifier.width,
        )

        (
            scenario_two_candidate_count,
            scenario_two_records,
            scenario_two_ambiguous_count,
        ) = split_scenario_two_quads(
            obj.data,
            beveled_keys,
        )

        analysis_end = time.perf_counter()

        bevel_apply_start = analysis_end
        try:
            modifier_name = apply_first_bevel_modifier(context, obj)
        except RuntimeError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

        bevel_apply_end = time.perf_counter()

        scenario_one_start = bevel_apply_end
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        post_coordinates = [vertex.co.copy() for vertex in bm.verts]
        post_tree = build_point_tree(post_coordinates)

        scenario_one_fixed = 0
        scenario_one_matched = 0
        scenario_one_trapped_fixed = 0
        used_middle_edges = set()

        for source in scenario_one_trapped_sources:
            trial_bm = build_validated_trapped_scenario_one_result(bm, source)
            if trial_bm is None:
                continue

            bm.free()
            bm = trial_bm
            scenario_one_trapped_fixed += 1

        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        post_coordinates = [vertex.co.copy() for vertex in bm.verts]
        post_tree = build_point_tree(post_coordinates)

        for source in scenario_one_sources:
            destination_index = find_vertex_index(
                post_tree,
                source["destination"],
                post_lookup_tolerance,
            )
            if destination_index is None:
                continue

            bm.verts.ensure_lookup_table()
            if destination_index >= len(bm.verts):
                continue

            destination = bm.verts[destination_index]
            middle_edge, middle = locate_scenario_one_middle(
                destination,
                source,
                source["junction"],
                source["distance"],
            )
            if middle_edge is None or middle is None:
                continue

            middle_key = tuple(sorted((
                middle_edge.verts[0].index,
                middle_edge.verts[1].index,
            )))
            if middle_key in used_middle_edges:
                continue

            scenario_one_matched += 1
            if repair_scenario_one_candidate(
                bm,
                middle_edge,
                middle,
                destination,
            ):
                scenario_one_fixed += 1
                used_middle_edges.add(middle_key)

        scenario_one_end = time.perf_counter()
        scenario_two_start = scenario_one_end

        (
            scenario_two_faulty_matched,
            scenario_two_faulty_joined,
        ) = repair_scenario_two_triangle_pairs(
            bm,
            scenario_two_records,
        )

        (
            scenario_two_fallback_matched,
            scenario_two_fallback_repaired,
        ) = repair_scenario_two_fallback_candidates(
            bm,
            scenario_two_records,
        )

        scenario_two_end = time.perf_counter()
        t_junction_start = scenario_two_end
        (
            bm,
            t_junction_matched,
            t_junction_repaired,
        ) = repair_t_junction_diagonals(
            bm,
            t_junction_records,
        )
        t_junction_end = time.perf_counter()
        uv_seam_start = t_junction_end

        seam_path_count, seam_edge_count = mark_beveled_center_edges_as_seams(
            bm,
            seam_records,
            post_lookup_tolerance,
        )

        uv_seam_end = time.perf_counter()
        cleanup_start = uv_seam_end

        bm.normal_update()
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        cleared_edges = clear_all_bevel_edge_weights(obj.data)
        cleanup_end = time.perf_counter()

        self.report(
            {'INFO'},
            format_success_report(
                modifier_name=modifier_name,
                source_method=source_method,
                beveled_edge_count=len(beveled_keys),
                original_edge_count=original_edge_count,
                cleared_edge_count=cleared_edges,
                scenario_one_source_count=len(scenario_one_sources),
                scenario_one_matched=scenario_one_matched,
                scenario_one_fixed=scenario_one_fixed,
                scenario_one_trapped_count=len(scenario_one_trapped_sources),
                scenario_one_trapped_fixed=scenario_one_trapped_fixed,
                scenario_two_candidate_count=scenario_two_candidate_count,
                scenario_two_split_count=len(scenario_two_records),
                scenario_two_ambiguous_count=scenario_two_ambiguous_count,
                scenario_two_faulty_matched=scenario_two_faulty_matched,
                scenario_two_faulty_joined=scenario_two_faulty_joined,
                scenario_two_fallback_matched=scenario_two_fallback_matched,
                scenario_two_fallback_repaired=scenario_two_fallback_repaired,
                t_junction_candidate_count=len(t_junction_records),
                t_junction_matched=t_junction_matched,
                t_junction_repaired=t_junction_repaired,
                seam_source_count=len(seam_records),
                seam_path_count=seam_path_count,
                seam_edge_count=seam_edge_count,
                analysis_time=analysis_end - total_start,
                bevel_apply_time=bevel_apply_end - bevel_apply_start,
                scenario_one_time=scenario_one_end - scenario_one_start,
                scenario_two_time=scenario_two_end - scenario_two_start,
                t_junction_time=t_junction_end - t_junction_start,
                uv_seam_time=uv_seam_end - uv_seam_start,
                cleanup_time=cleanup_end - cleanup_start,
                total_time=cleanup_end - total_start,
            ),
        )
        return {'FINISHED'}


CLASSES = (MODUS_OT_finalize_bevel,)
