# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from .actions import draw_mode_tools

class MODUS_OT_call_quick_menu(bpy.types.Operator):
    bl_idname='modus.call_quick_menu'; bl_label='Modus Menu'; bl_description='Open the Modus menu for the current mode'
    @classmethod
    def poll(cls, context): return context.mode in {'EDIT_MESH','OBJECT'}
    def invoke(self, context, _event): return context.window_manager.invoke_popup(self, width=210)
    def execute(self, _context): return {'FINISHED'}
    def draw(self, context): draw_mode_tools(self.layout, context, 'MENU')

class VIEW3D_PT_modus_tools(bpy.types.Panel):
    bl_label='Tools'; bl_idname='VIEW3D_PT_modus_tools'; bl_space_type='VIEW_3D'; bl_region_type='UI'; bl_category='Modus'; bl_order=10
    @classmethod
    def poll(cls, context): return context.mode in {'EDIT_MESH','OBJECT'}
    def draw(self, context): draw_mode_tools(self.layout, context, 'PANEL')

CLASSES=(MODUS_OT_call_quick_menu, VIEW3D_PT_modus_tools)
