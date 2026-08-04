# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import bmesh
import bpy
from bpy.props import EnumProperty

from . import state


class MODUS_OT_isolate(bpy.types.Operator):
    bl_idname = 'modus.isolate'
    bl_label = 'Isolate'
    bl_description = 'Frame the selection or use nested object isolation'
    bl_options = {'REGISTER', 'UNDO'}

    method: EnumProperty(
        name='Method',
        items=(
            ('VIEW_SELECTED', 'View Selected', 'Frame the current selection'),
            ('LOCAL_VIEW', 'Nested Isolate', 'Push or restore a nested isolation level'),
        ),
        default='VIEW_SELECTED',
    )

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def execute(self, context):
        if self.method == 'VIEW_SELECTED':
            return self._view_selected(context)
        return self._nested_isolate(context)

    def _view_selected(self, context):
        if context.mode == 'OBJECT':
            op = bpy.ops.view3d.view_selected if context.selected_objects else bpy.ops.view3d.view_all
            op('INVOKE_DEFAULT')
            return {'FINISHED'}

        if context.mode == 'EDIT_MESH' and context.active_object:
            bm = bmesh.from_edit_mesh(context.active_object.data)
            if any(vert.select for vert in bm.verts):
                bpy.ops.view3d.view_selected('INVOKE_DEFAULT')
                return {'FINISHED'}

            selected_before = [vert for vert in bm.verts if vert.select]
            for vert in bm.verts:
                vert.select_set(True)
            bm.select_flush(True)
            bmesh.update_edit_mesh(context.active_object.data, loop_triangles=False, destructive=False)
            bpy.ops.view3d.view_selected('INVOKE_DEFAULT')
            for vert in bm.verts:
                vert.select_set(vert in selected_before)
            bm.select_flush(False)
            bmesh.update_edit_mesh(context.active_object.data, loop_triangles=False, destructive=False)
            return {'FINISHED'}

        bpy.ops.view3d.view_selected('INVOKE_DEFAULT')
        return {'FINISHED'}

    def _nested_isolate(self, context):
        if context.mode != 'OBJECT':
            self.report({'INFO'}, 'Nested Isolate is available in Object Mode')
            return {'CANCELLED'}

        scene = context.scene
        area = context.area
        view = context.space_data
        selected = list(context.selected_objects)
        visible = list(context.visible_objects)
        stack = state.find_stack(scene, area)

        # If Local View was exited outside Modus, discard only this
        # viewport's stale stack rather than affecting other 3D views.
        if not view.local_view and stack:
            state.clear_stack(scene, area)
            stack = None

        if view.local_view:
            if selected and set(selected) != set(visible):
                hidden = [obj for obj in visible if obj not in selected]
                if not hidden:
                    return {'CANCELLED'}

                for obj in hidden:
                    obj.local_view_set(view, False)

                stack = stack or state.find_stack(scene, area, create=True)
                level = stack.levels.add()
                level.name = f'Isolate Level {len(stack.levels)}'
                for obj in hidden:
                    level.hidden_objects.add().obj = obj

                context.view_layer.objects.active = selected[0]
                _deselect_all_except(selected)
                _tag_redraw(context)
                return {'FINISHED'}

            if stack and stack.levels:
                level = stack.levels[-1]
                if len(stack.levels) == 1:
                    bpy.ops.view3d.localview(frame_selected=False)
                    state.clear_stack(scene, area)
                else:
                    for entry in level.hidden_objects:
                        if entry.obj:
                            entry.obj.local_view_set(view, True)
                    stack.levels.remove(len(stack.levels) - 1)

                _deselect_all(context)
                _tag_redraw(context)
                return {'FINISHED'}

            bpy.ops.view3d.localview(frame_selected=False)
            state.clear_stack(scene, area)
            _tag_redraw(context)
            return {'FINISHED'}

        if not selected:
            self.report({'INFO'}, 'Select one or more objects to isolate')
            return {'CANCELLED'}

        hidden = [obj for obj in visible if obj not in selected]
        if not hidden:
            self.report({'INFO'}, 'All visible objects are already selected')
            return {'CANCELLED'}

        state.clear_stack(scene, area)
        bpy.ops.view3d.localview(frame_selected=False)

        stack = state.find_stack(scene, area, create=True)
        level = stack.levels.add()
        level.name = 'Isolate Level 1'
        for obj in hidden:
            level.hidden_objects.add().obj = obj

        context.view_layer.objects.active = selected[0]
        _deselect_all_except(selected)
        _tag_redraw(context)
        return {'FINISHED'}


def _deselect_all(context):
    for obj in context.selected_objects:
        obj.select_set(False)


def _deselect_all_except(objects):
    keep = set(objects)
    for obj in bpy.context.selected_objects:
        if obj not in keep:
            obj.select_set(False)


def _tag_redraw(context):
    if context.area:
        context.area.tag_redraw()


CLASSES = (MODUS_OT_isolate,)
