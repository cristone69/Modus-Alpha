# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import blf
import bmesh
import bpy
import gpu
from bpy.props import BoolProperty, FloatProperty
from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_location_3d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector


_AXIS_COLORS = {
    'X': (0.95, 0.25, 0.25, 1.0),
    'Y': (0.35, 0.85, 0.35, 1.0),
    'Z': (0.35, 0.55, 1.0, 1.0),
}


class MODUS_OT_symmetrize(bpy.types.Operator):
    bl_idname = 'modus.symmetrize'
    bl_label = 'Symmetrize'
    bl_description = 'Flick toward an object-local axis to symmetrize from the opposite side'
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}

    threshold: FloatProperty(
        name='Threshold',
        description='Distance from the symmetry plane used to merge center vertices',
        default=0.00001,
        min=0.0,
        soft_max=0.01,
        precision=6,
    )
    selected_only: BoolProperty(
        name='Selected Only',
        description='Apply only to selected mesh elements',
        default=False,
    )
    delete_half: BoolProperty(
        name='Delete Half',
        description='Delete the destination half instead of symmetrizing onto it',
        default=False,
    )
    center_tris_to_quads: BoolProperty(
        name='Center Tris to Quads',
        description='Remove center-plane faces and dissolve eligible triangle-pair edges on the active symmetry plane',
        default=False,
    )

    _handle = None
    _handle_3d = None
    _area = None
    _start = Vector((0.0, 0.0))
    _gesture_start = Vector((0.0, 0.0))
    _text_anchor = Vector((0.0, 0.0))
    _mouse = Vector((0.0, 0.0))
    _screen_axes = None
    _world_axes = None
    _axis_anchor_3d = None
    _axis_world_length = 1.0
    _choice = None
    _radius = 90.0
    _flick_distance = 108.0

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'EDIT_MESH'
            and context.active_object is not None
            and context.active_object.type == 'MESH'
            and context.area is not None
            and context.area.type == 'VIEW_3D'
        )

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, 'selected_only', toggle=True)
        layout.prop(self, 'delete_half', toggle=True)
        layout.prop(self, 'center_tris_to_quads', toggle=True)
        layout.prop(self, 'threshold')

    def invoke(self, context, event):
        if not self.poll(context):
            return {'CANCELLED'}

        self._area = context.area
        self._gesture_start = Vector((event.mouse_region_x, event.mouse_region_y))
        # Spawn the complete flick interface at the invocation cursor. The
        # compass remains fixed there while its directions follow the active
        # object's local X, Y, and Z axes.
        self._start = self._gesture_start.copy()
        self._text_anchor = self._start.copy()
        self._choice = None
        scale = context.preferences.system.ui_scale
        self._radius = 90.0 * scale
        self._flick_distance = 108.0 * scale

        prefs = context.preferences.addons.get(__package__.partition('.modules')[0])
        prefs = prefs.preferences if prefs else None
        if prefs is not None:
            self.center_tris_to_quads = prefs.symmetrize_center_tris_to_quads

        # Re-evaluate the default every time the flick operator is invoked.
        # Any selected mesh component means Selected Only; an empty selection
        # means the whole mesh. The redo panel can still override this after.
        if prefs is not None and not prefs.symmetrize_context_scope:
            self.selected_only = prefs.symmetrize_default_scope == 'SELECTED'
        else:
            self.selected_only = self._has_mesh_selection(context)

        self._screen_axes = self._project_local_axes(context)
        if not self._screen_axes:
            self.report({'WARNING'}, 'Flick Symmetrize could not build a usable viewport axis guide')
            return {'CANCELLED'}
        self._mouse = self._start.copy()

        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_hud, (), 'WINDOW', 'POST_PIXEL'
        )
        self._handle_3d = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_gizmo_3d, (), 'WINDOW', 'POST_VIEW'
        )
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if context.area != self._area:
            return self._cancel(context)

        if event.type == 'MOUSEMOVE':
            # Keep the compass anchored to the invocation cursor while
            # re-projecting the object's local axes for the current view.
            self._screen_axes = self._project_local_axes(context)
            if not self._screen_axes:
                return self._cancel(context)
            self._mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            delta = self._mouse - self._start
            if delta.length > 2.0:
                direction = delta.normalized()
                self._choice = max(
                    self._screen_axes,
                    key=lambda item: direction.dot(item[2]),
                )

            if delta.length >= self._flick_distance and self._choice:
                return self._finish(context)

            context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        if event.type == 'S' and event.value == 'PRESS':
            self.selected_only = not self.selected_only
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        if event.type in {'X', 'D'} and event.value == 'PRESS':
            self.delete_half = not self.delete_half
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        if event.type == 'C' and event.value == 'PRESS':
            self.center_tris_to_quads = not self.center_tris_to_quads
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        if event.type in {'LEFTMOUSE', 'SPACE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            if self._choice:
                return self._finish(context)
            return {'RUNNING_MODAL'}

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            return self._cancel(context)

        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}

        return {'RUNNING_MODAL'}

    def execute(self, context):
        direction = getattr(self, '_execute_direction', None)
        if not direction:
            return {'CANCELLED'}

        try:
            if not self.selected_only:
                bpy.ops.mesh.select_all(action='SELECT')

            # Delete Half intentionally runs the native Symmetrize operation
            # first. This cleans and aligns the center seam before the chosen
            # destination half is removed.
            result = bpy.ops.mesh.symmetrize(
                direction=direction,
                threshold=self.threshold,
            )
            if 'FINISHED' not in result:
                return {'CANCELLED'}

            if self.center_tris_to_quads:
                self._center_tris_to_quads(context, direction)

            if self.delete_half:
                result = self._delete_destination_half(context, direction)

            if not self.selected_only:
                bpy.ops.mesh.select_all(action='DESELECT')

            return result if 'FINISHED' in result else {'CANCELLED'}
        except (RuntimeError, TypeError, ValueError) as exc:
            self.report({'WARNING'}, f'Symmetrize failed: {exc}')
            return {'CANCELLED'}


    def _center_tris_to_quads(self, context, direction):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        bm.edges.ensure_lookup_table()

        _source_side, axis = direction.split('_', 1)
        axis_index = {'X': 0, 'Y': 1, 'Z': 2}[axis]
        epsilon = max(self.threshold, 1.0e-6)
        # A capped half can contain faces directly on the symmetry plane.
        # Native Symmetrize mirrors those caps too, leaving a hidden internal
        # wall. Quad cleanup intentionally removes every eligible center-plane
        # face before repairing triangle pairs along the seam.
        center_faces = []
        for face in bm.faces:
            if not face.is_valid:
                continue
            if self.selected_only and not face.select:
                continue
            if all(abs(vert.co[axis_index]) <= epsilon for vert in face.verts):
                center_faces.append(face)

        if center_faces:
            bmesh.ops.delete(bm, geom=center_faces, context='FACES_ONLY')
            bm.edges.ensure_lookup_table()

        eligible = []

        for edge in bm.edges:
            if not edge.is_valid or len(edge.link_faces) != 2:
                continue
            if self.selected_only and not edge.select:
                continue
            face_a, face_b = edge.link_faces
            if len(face_a.verts) != 3 or len(face_b.verts) != 3:
                continue
            if any(abs(vert.co[axis_index]) > epsilon for vert in edge.verts):
                continue

            boundary_verts = set(face_a.verts) | set(face_b.verts)
            if len(boundary_verts) != 4:
                continue
            eligible.append(edge)

        if eligible:
            bmesh.ops.dissolve_edges(
                bm,
                edges=eligible,
                use_verts=False,
                use_face_split=False,
            )

        if center_faces or eligible:
            bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)

    def _has_mesh_selection(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        return (
            any(vert.select for vert in bm.verts)
            or any(edge.select for edge in bm.edges)
            or any(face.select for face in bm.faces)
        )

    def _delete_destination_half(self, context, direction):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()

        source_side, axis = direction.split('_', 1)
        axis_index = {'X': 0, 'Y': 1, 'Z': 2}[axis]
        destination_positive = source_side == 'NEGATIVE'

        candidates = [vert for vert in bm.verts if vert.select] if self.selected_only else list(bm.verts)
        delete_verts = []

        for vert in candidates:
            coordinate = vert.co[axis_index]
            if abs(coordinate) <= self.threshold:
                vert.co[axis_index] = 0.0
                continue

            if (destination_positive and coordinate > 0.0) or (not destination_positive and coordinate < 0.0):
                delete_verts.append(vert)

        if delete_verts:
            bmesh.ops.delete(bm, geom=delete_verts, context='VERTS')

        bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=bool(delete_verts))
        return {'FINISHED'}

    def _finish(self, context):
        axis, sign, _screen_vector = self._choice
        # Flicking toward a side means preserving the opposite side and
        # symmetrizing it toward the flick direction, matching the familiar
        # six-direction flick workflow.
        source = 'NEGATIVE' if sign > 0 else 'POSITIVE'
        self._execute_direction = f'{source}_{axis}'
        self._remove_handler(context)
        return self.execute(context)

    def _cancel(self, context):
        self._remove_handler(context)
        return {'CANCELLED'}

    def _remove_handler(self, context):
        if self._handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            except (ReferenceError, ValueError):
                pass
            self._handle = None
        if self._handle_3d is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle_3d, 'WINDOW')
            except (ReferenceError, ValueError):
                pass
            self._handle_3d = None
        if context.area:
            context.area.tag_redraw()

    def _project_local_axes(self, context):
        obj = context.active_object
        if obj is None or context.region is None or context.region_data is None:
            return None

        # Build the guide at the invocation cursor, using the object's origin
        # only as a depth reference. This remains valid even when the object
        # origin itself is outside the viewport, which previously caused the
        # operator to cancel silently before the HUD appeared.
        try:
            anchor_3d = region_2d_to_location_3d(
                context.region,
                context.region_data,
                self._start,
                obj.matrix_world.translation,
            )
            pixel_offset_3d = region_2d_to_location_3d(
                context.region,
                context.region_data,
                self._start + Vector((self._radius, 0.0)),
                anchor_3d,
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

        axis_world_length = max((pixel_offset_3d - anchor_3d).length, 1.0e-6)
        rotation = obj.matrix_world.to_quaternion()
        local_axes = (
            ('X', rotation @ Vector((1.0, 0.0, 0.0))),
            ('Y', rotation @ Vector((0.0, 1.0, 0.0))),
            ('Z', rotation @ Vector((0.0, 0.0, 1.0))),
        )

        self._axis_anchor_3d = anchor_3d
        self._axis_world_length = axis_world_length
        self._world_axes = []

        origin_2d = location_3d_to_region_2d(
            context.region,
            context.region_data,
            anchor_3d,
            default=self._start,
        )
        origin_2d = Vector(origin_2d)

        axes = []
        for axis, world_axis in local_axes:
            if world_axis.length_squared == 0.0:
                continue
            world_axis.normalize()
            self._world_axes.append((axis, 1, world_axis.copy()))
            self._world_axes.append((axis, -1, -world_axis.copy()))

            endpoint_2d = location_3d_to_region_2d(
                context.region,
                context.region_data,
                anchor_3d + world_axis * axis_world_length,
                default=origin_2d,
            )
            screen = Vector(endpoint_2d) - origin_2d
            if screen.length < 0.001:
                continue
            screen.normalize()
            axes.append((axis, 1, screen))
            axes.append((axis, -1, -screen))

        return axes

    def _draw_gizmo_3d(self):
        context = bpy.context
        if (
            context.area != self._area
            or not self._world_axes
            or self._axis_anchor_3d is None
        ):
            return

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')

        for axis, sign, world_axis in self._world_axes:
            selected = self._choice and self._choice[0] == axis and self._choice[1] == sign
            color = _AXIS_COLORS[axis]
            alpha = 1.0 if selected else (0.85 if sign > 0 else 0.28)
            width = 6.0 if selected else (3.5 if sign > 0 else 2.0)
            endpoint = self._axis_anchor_3d + world_axis * self._axis_world_length

            shader.bind()
            shader.uniform_float('color', (color[0], color[1], color[2], alpha))
            gpu.state.line_width_set(width)
            batch_for_shader(
                shader,
                'LINES',
                {'pos': (self._axis_anchor_3d, endpoint)},
            ).draw(shader)

        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')

    def _draw_hud(self):
        context = bpy.context
        if context.area != self._area or not self._screen_axes:
            return

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')

        # The axis lines are drawn in 3D. Keep compact signed labels in the
        # pixel HUD so every flick target remains readable at any zoom level.
        label_font = 0
        label_size = max(12, int(14 * context.preferences.system.ui_scale))
        blf.size(label_font, label_size)
        for axis, sign, screen in self._screen_axes:
            end = self._start + screen * self._radius
            selected = self._choice and self._choice[0] == axis and self._choice[1] == sign
            color = _AXIS_COLORS[axis]
            label = f'{"+" if sign > 0 else "−"}{axis}'
            width, height = blf.dimensions(label_font, label)
            blf.position(label_font, end.x - width * 0.5, end.y - height * 0.5, 0)
            blf.color(
                label_font,
                color[0],
                color[1],
                color[2],
                1.0 if selected else 0.72,
            )
            blf.draw(label_font, label)

        delta = self._mouse - self._start
        if delta.length:
            shader.bind()
            shader.uniform_float('color', (1.0, 1.0, 1.0, 0.8))
            gpu.state.line_width_set(3.0)
            batch_for_shader(shader, 'LINES', {'pos': (self._start, self._mouse)}).draw(shader)

        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')

        scale = context.preferences.system.ui_scale
        mode_name = 'Delete Half' if self.delete_half else 'Symmetrize'
        title = mode_name
        if self._choice:
            axis, sign, _screen = self._choice
            title = f'{mode_name}  {"+" if sign > 0 else "−"}{axis}'
        if self.selected_only:
            title += '  •  Selected'

        font_id = 0
        text_size = max(16, int(20 * scale))
        line_gap = 7 * scale
        quad_state = 'On' if self.center_tris_to_quads else 'Off'
        lines = (
            title,
            'X: Delete half',
            'S: Selected only',
            f'C: Quad cleanup {quad_state}',
            'Esc: Cancel',
        )

        blf.size(font_id, text_size)
        dimensions = [blf.dimensions(font_id, line) for line in lines]
        panel_width = max(width for width, _height in dimensions)
        line_height = max(height for _width, height in dimensions)
        panel_height = len(lines) * line_height + (len(lines) - 1) * line_gap
        # Place the settings on whichever side has room, then clamp the
        # panel vertically so invoking near a viewport edge remains readable.
        padding = 9 * scale
        side_gap = 24 * scale
        right_x = self._text_anchor.x + self._radius + side_gap
        left_x = self._text_anchor.x - self._radius - side_gap - panel_width
        available_width = context.region.width if context.region else 0
        available_height = context.region.height if context.region else 0

        if available_width and right_x + panel_width + padding > available_width:
            text_x = left_x
        else:
            text_x = right_x
        if available_width:
            text_x = min(
                max(text_x, padding),
                max(padding, available_width - panel_width - padding),
            )

        panel_top = self._text_anchor.y + panel_height * 0.5
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

    def _draw_text_backdrop(self, shader, x, y, width, height, padding):
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
