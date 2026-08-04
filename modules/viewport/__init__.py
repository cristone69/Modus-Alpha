# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from ...core import header


class MODUS_OT_toggle_wireframe_overlay(bpy.types.Operator):
    bl_idname = 'modus.toggle_wireframe_overlay'
    bl_label = 'Toggle Wireframe Overlay'
    bl_description = 'Toggle wireframe display in this 3D viewport'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D' and context.mode in {'OBJECT', 'SCULPT'}

    def execute(self, context):
        overlay = context.space_data.overlay
        overlay.show_wireframes = not overlay.show_wireframes
        context.area.tag_redraw()
        return {'FINISHED'}


class VIEW3D_PT_modus_viewport(bpy.types.Panel):
    bl_label = 'Viewport'
    bl_idname = 'VIEW3D_PT_modus_viewport'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Modus'
    bl_order = 20

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'SCULPT'}

    def draw(self, context):
        layout = self.layout
        layout.prop(context.space_data.overlay, 'show_wireframes', text='Wireframe Overlay')


def _draw_header(row, context, probe=False):
    if context.mode not in {'OBJECT', 'SCULPT'}:
        return False
    if probe:
        return True
    row.operator(
        'modus.toggle_wireframe_overlay',
        text='',
        icon='SHADING_WIRE',
        depress=context.space_data.overlay.show_wireframes,
    )
    return True


_CLASSES = (
    MODUS_OT_toggle_wireframe_overlay,
    VIEW3D_PT_modus_viewport,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    header.register_entry('viewport_wireframe', _draw_header, order=20)


def unregister():
    header.unregister_entry('viewport_wireframe')
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
