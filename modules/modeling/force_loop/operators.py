# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import bpy
import bmesh
import blf
import gpu
from bpy.props import BoolProperty, FloatProperty, IntProperty
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

_EPS = 1e-6


def _project(region, rv3d, obj, co):
    return view3d_utils.location_3d_to_region_2d(region, rv3d, obj.matrix_world @ co)


def _nearest_projected_edge(context, obj, bm, mouse, max_distance=48.0):
    region, rv3d = context.region, context.region_data
    best = None
    best_t = 0.5
    best_d = max_distance
    for edge in bm.edges:
        if edge.hide:
            continue
        a = _project(region, rv3d, obj, edge.verts[0].co)
        b = _project(region, rv3d, obj, edge.verts[1].co)
        if a is None or b is None:
            continue
        ab = b - a
        denom = ab.length_squared
        if denom <= 1e-12:
            t = 0.5
            point = (a + b) * 0.5
        else:
            t = max(0.0, min(1.0, (mouse - a).dot(ab) / denom))
            point = a + ab * t
        d = (mouse - point).length
        if d < best_d:
            best = edge
            best_t = t
            best_d = d
    return best, best_t


def _edge_screen(context, obj, edge):
    a = _project(context.region, context.region_data, obj, edge.verts[0].co)
    b = _project(context.region, context.region_data, obj, edge.verts[1].co)
    return a, b


def _plane_edge_intersection(plane_point: Vector, plane_normal: Vector, edge):
    """Return (edge_parameter, local_point) where an edge crosses a 3D plane."""
    a = edge.verts[0].co
    b = edge.verts[1].co
    da = plane_normal.dot(a - plane_point)
    db = plane_normal.dot(b - plane_point)

    # An edge lying in the plane is ambiguous and should not be used as a traversal exit.
    if abs(da) <= _EPS and abs(db) <= _EPS:
        return None
    if da > _EPS and db > _EPS:
        return None
    if da < -_EPS and db < -_EPS:
        return None

    denom = da - db
    if abs(denom) <= _EPS:
        return None
    t = da / denom
    if t < -_EPS or t > 1.0 + _EPS:
        return None
    t = max(0.0, min(1.0, t))
    return t, a.lerp(b, t)


def _face_crossings(face, plane_point, plane_normal):
    hits = []
    for edge in face.edges:
        hit = _plane_edge_intersection(plane_point, plane_normal, edge)
        if hit is None:
            continue
        edge_t, point = hit
        hits.append((edge, edge_t, point))

    # Collapse duplicate intersections at polygon vertices. Prefer the edge hit
    # furthest from an endpoint because it is numerically more stable.
    unique = []
    for hit in hits:
        replaced = False
        for i, old in enumerate(unique):
            if (hit[2] - old[2]).length <= 1e-5:
                old_margin = min(old[1], 1.0 - old[1])
                new_margin = min(hit[1], 1.0 - hit[1])
                if new_margin > old_margin:
                    unique[i] = hit
                replaced = True
                break
        if not replaced:
            unique.append(hit)
    return unique


def _walk_side(start_face, start_edge, start_edge_t, plane_point, plane_normal, visited):
    path = []
    face = start_face
    entry_edge = start_edge
    entry_t = start_edge_t

    for _ in range(10000):
        if face is None or not face.is_valid or face in visited:
            break
        crossings = _face_crossings(face, plane_point, plane_normal)
        if len(crossings) < 2:
            break

        entry_hit = next((hit for hit in crossings if hit[0] == entry_edge), None)
        if entry_hit is None:
            expected = entry_edge.verts[0].co.lerp(entry_edge.verts[1].co, entry_t)
            entry_hit = min(crossings, key=lambda h: (h[2] - expected).length)

        candidates = [hit for hit in crossings if hit[0] != entry_hit[0]]
        if not candidates:
            break

        # For concave n-gons the plane can cross a face more than twice. Continue
        # through the intersection furthest from the entry point to retain one
        # penetrating chain rather than branching.
        exit_hit = max(candidates, key=lambda h: (h[2] - entry_hit[2]).length_squared)
        if exit_hit[0] == entry_edge:
            break

        visited.add(face)
        path.append((face, entry_hit, exit_hit))

        next_faces = [f for f in exit_hit[0].link_faces if f != face and f not in visited]
        if not next_faces:
            break
        face = next_faces[0]
        entry_edge = exit_hit[0]
        entry_t = exit_hit[1]

    return path


def _solve_plane_path(start_edge, start_t, plane_point, plane_normal):
    visited = set()
    sides = []
    for face in list(start_edge.link_faces)[:2]:
        sides.append(_walk_side(face, start_edge, start_t, plane_point, plane_normal, visited))
    if not sides:
        return []
    if len(sides) == 1:
        return sides[0]
    return list(reversed(sides[0])) + sides[1]


def _path_from_edge(obj, start_edge, start_t):
    """Build the local cutting plane and connected path for one target edge."""
    local_edge_vec = start_edge.verts[1].co - start_edge.verts[0].co
    if local_edge_vec.length_squared <= 1e-12:
        return None

    # Define perpendicularity in world space. Convert the world-space edge
    # direction into the equivalent local-space plane normal using M^T so
    # rotated and non-uniformly scaled objects still receive a true 3D plane
    # perpendicular to the visible target edge.
    world_edge_vec = obj.matrix_world.to_3x3() @ local_edge_vec
    if world_edge_vec.length_squared <= 1e-12:
        return None

    world_plane_normal = world_edge_vec.normalized()
    plane_normal = (obj.matrix_world.to_3x3().transposed() @ world_plane_normal).normalized()
    plane_point = start_edge.verts[0].co.lerp(start_edge.verts[1].co, start_t)
    path = _solve_plane_path(start_edge, start_t, plane_point, plane_normal)
    return plane_point, plane_normal, path


def _split_edge_at(edge, edge_t):
    # edge_split uses a fraction measured from the supplied vertex.
    edge_t = max(0.0001, min(0.9999, edge_t))
    _new_edge, new_vert = bmesh.utils.edge_split(edge, edge.verts[0], edge_t)
    return new_vert


def _apply_path(obj, bm, path):
    requests = {}
    for _face, entry_hit, exit_hit in path:
        for edge, edge_t, _point in (entry_hit, exit_hit):
            if edge.is_valid and edge not in requests:
                requests[edge] = edge_t

    split_verts = {}
    for edge, edge_t in list(requests.items()):
        if not edge.is_valid:
            continue
        try:
            split_verts[edge] = _split_edge_at(edge, edge_t)
        except (ValueError, ReferenceError, RuntimeError):
            continue

    connected = 0
    for face, entry_hit, exit_hit in path:
        v1 = split_verts.get(entry_hit[0])
        v2 = split_verts.get(exit_hit[0])
        if not (v1 and v2 and v1.is_valid and v2.is_valid and v1 != v2):
            continue
        try:
            result = bmesh.ops.connect_verts(bm, verts=[v1, v2], check_degenerate=True)
            if result.get('edges'):
                connected += 1
        except (ValueError, ReferenceError, RuntimeError):
            continue

    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    return connected


class MODUS_OT_force_loop(bpy.types.Operator):
    bl_idname = 'modus.force_loop'
    bl_label = 'Force Loop'
    bl_description = 'Cut with a 3D plane perpendicular to the hovered edge through connected quads, triangles, and n-gons'
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    center_on_edge: BoolProperty(
        name='Center at Target Edge',
        description='Place the forced loop at the exact midpoint of the target edge instead of the cursor position',
        default=False,
    )
    target_edge_index: IntProperty(default=-1, options={'HIDDEN'})
    target_vert_a: IntProperty(default=-1, options={'HIDDEN'})
    target_vert_b: IntProperty(default=-1, options={'HIDDEN'})
    target_factor: FloatProperty(default=0.5, min=0.0, max=1.0, options={'HIDDEN'})

    _handle = None

    @classmethod
    def poll(cls, context):
        obj = context.edit_object
        return obj is not None and obj.type == 'MESH' and context.area and context.area.type == 'VIEW_3D'

    def draw(self, _context):
        self.layout.prop(self, 'center_on_edge', toggle=True)

    def invoke(self, context, event):
        self.obj = context.edit_object
        self.bm = bmesh.from_edit_mesh(self.obj.data)
        self.bm.edges.ensure_lookup_table()
        self.bm.edges.index_update()
        self.mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        self.start_edge = None
        self.start_t = 0.5
        self.hovered_t = 0.5
        self.plane_point = Vector((0.0, 0.0, 0.0))
        self.plane_normal = Vector((1.0, 0.0, 0.0))
        self.path = []
        self._update(context)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw, (context,), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        if context.area:
            context.area.tag_redraw()

    def cancel(self, context):
        self._finish(context)

    def _update(self, context):
        self.start_edge, hovered_t = _nearest_projected_edge(context, self.obj, self.bm, self.mouse)
        self.path = []
        if not self.start_edge:
            return
        self.hovered_t = hovered_t
        self.start_t = 0.5 if self.center_on_edge else self.hovered_t
        result = _path_from_edge(self.obj, self.start_edge, self.start_t)
        if result is None:
            return
        self.plane_point, self.plane_normal, self.path = result

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            self.mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            self._update(context)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'C' and event.value == 'PRESS':
            self.center_on_edge = not self.center_on_edge
            self._update(context)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self._finish(context)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if not self.path:
                self.report({'WARNING'}, 'Force Loop could not find a connected straight path')
                self._finish(context)
                return {'CANCELLED'}
            self.bm.verts.index_update()
            self.bm.edges.index_update()
            self.target_edge_index = self.start_edge.index
            self.target_vert_a = self.start_edge.verts[0].index
            self.target_vert_b = self.start_edge.verts[1].index
            # Preserve the cursor-derived position separately so the redo panel
            # can switch Center off and restore the original placement.
            self.target_factor = self.hovered_t
            self._finish(context)
            return self.execute(context)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        obj = context.edit_object
        if obj is None or obj.type != 'MESH':
            self.report({'WARNING'}, 'Force Loop requires an active mesh in Edit Mode')
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        start_edge = None
        if (
            0 <= self.target_vert_a < len(bm.verts)
            and 0 <= self.target_vert_b < len(bm.verts)
        ):
            vert_a = bm.verts[self.target_vert_a]
            vert_b = bm.verts[self.target_vert_b]
            start_edge = next(
                (edge for edge in vert_a.link_edges if vert_b in edge.verts),
                None,
            )
        if start_edge is None and 0 <= self.target_edge_index < len(bm.edges):
            start_edge = bm.edges[self.target_edge_index]
        if start_edge is None:
            self.report({'WARNING'}, 'Force Loop target edge is no longer available')
            return {'CANCELLED'}

        start_t = 0.5 if self.center_on_edge else self.target_factor
        result = _path_from_edge(obj, start_edge, start_t)
        if result is None:
            self.report({'WARNING'}, 'Force Loop could not calculate a valid cutting plane')
            return {'CANCELLED'}

        _plane_point, _plane_normal, path = result
        if not path:
            self.report({'WARNING'}, 'Force Loop could not find a connected straight path')
            return {'CANCELLED'}

        count = _apply_path(obj, bm, path)
        self.report({'INFO'}, f'Force Loop created {count} plane-cut connected segments')
        return {'FINISHED'}

    def _draw(self, context):
        if not self.start_edge:
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            self._draw_hud(context, shader)
            return
        coords = []
        for _face, entry_hit, exit_hit in self.path:
            a = _project(context.region, context.region_data, self.obj, entry_hit[2])
            b = _project(context.region, context.region_data, self.obj, exit_hit[2])
            if a is not None and b is not None:
                coords.extend(((a.x, a.y), (b.x, b.y)))

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')

        # Show the selected/hovered source edge so the 90-degree relationship is unambiguous.
        edge_a, edge_b = _edge_screen(context, self.obj, self.start_edge)
        if edge_a is not None and edge_b is not None:
            gpu.state.line_width_set(3.0)
            edge_batch = batch_for_shader(shader, 'LINES', {
                'pos': ((edge_a.x, edge_a.y), (edge_b.x, edge_b.y)),
            })
            shader.bind()
            shader.uniform_float('color', (0.3, 0.75, 1.0, 0.9))
            edge_batch.draw(shader)

        # Show the 3D plane's cross direction on the starting surface. The plane
        # normal follows the target edge, so this direction is perpendicular to it.
        start_face = self.start_edge.link_faces[0] if self.start_edge.link_faces else None
        if start_face is not None:
            cross_dir = start_face.normal.cross(self.plane_normal)
            if cross_dir.length_squared > 1e-12:
                cross_dir.normalize()
                scale = max(1.0, self.obj.dimensions.length) * 2.0
                guide_a = _project(context.region, context.region_data, self.obj, self.plane_point - cross_dir * scale)
                guide_b = _project(context.region, context.region_data, self.obj, self.plane_point + cross_dir * scale)
                if guide_a is not None and guide_b is not None:
                    gpu.state.line_width_set(2.0)
                    guide_batch = batch_for_shader(shader, 'LINES', {
                        'pos': ((guide_a.x, guide_a.y), (guide_b.x, guide_b.y)),
                    })
                    shader.bind()
                    shader.uniform_float('color', (1.0, 0.55, 0.08, 0.32))
                    guide_batch.draw(shader)

        if coords:
            gpu.state.line_width_set(4.0)
            batch = batch_for_shader(shader, 'LINES', {'pos': coords})
            shader.bind()
            shader.uniform_float('color', (1.0, 0.55, 0.08, 0.95))
            batch.draw(shader)

        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')

        self._draw_hud(context, shader)

    def _draw_hud(self, context, shader):
        scale = context.preferences.system.ui_scale
        center_state = 'On' if self.center_on_edge else 'Off'
        title = 'FORCE LOOP'
        if self.center_on_edge:
            title += '  •  CENTERED'

        lines = (
            title,
            *(() if self.start_edge else ('Move cursor near an edge',)),
            f'C: Center at target edge  {center_state}',
            'LMB: Confirm',
            'Esc / RMB: Cancel',
        )

        font_id = 0
        text_size = max(16, int(20 * scale))
        line_gap = 7 * scale
        blf.size(font_id, text_size)
        dimensions = [blf.dimensions(font_id, line) for line in lines]
        panel_width = max(width for width, _height in dimensions)
        line_height = max(height for _width, height in dimensions)
        panel_height = len(lines) * line_height + (len(lines) - 1) * line_gap

        padding = 9 * scale
        side_gap = 24 * scale
        text_x = self.mouse.x + side_gap
        available_width = context.region.width if context.region else 0
        available_height = context.region.height if context.region else 0

        if available_width and text_x + panel_width + padding > available_width:
            text_x = self.mouse.x - side_gap - panel_width
        if available_width:
            text_x = min(
                max(text_x, padding),
                max(padding, available_width - panel_width - padding),
            )

        panel_top = self.mouse.y + panel_height * 0.5
        if available_height:
            panel_top = min(
                max(panel_top, panel_height + padding),
                available_height - padding,
            )
        text_y = panel_top - line_height
        panel_bottom = panel_top - panel_height

        self._draw_text_backdrop(
            shader,
            text_x,
            panel_bottom,
            panel_width,
            panel_height,
            padding,
        )

        current_y = text_y
        for index, line in enumerate(lines):
            alpha = 1.0 if index == 0 else 0.82
            blf.position(font_id, text_x, current_y, 0)
            blf.color(font_id, 1.0, 1.0, 1.0, alpha)
            blf.draw(font_id, line)
            current_y -= line_height + line_gap

    @staticmethod
    def _draw_text_backdrop(shader, x, y, width, height, padding):
        left = x - padding
        right = x + width + padding
        bottom = y - padding * 0.65
        top = y + height + padding * 0.65
        shader.bind()
        shader.uniform_float('color', (0.0, 0.0, 0.0, 0.40))
        gpu.state.blend_set('ALPHA')
        batch_for_shader(
            shader,
            'TRIS',
            {'pos': ((left, bottom), (right, bottom), (right, top), (left, bottom), (right, top), (left, top))},
        ).draw(shader)
        gpu.state.blend_set('NONE')
