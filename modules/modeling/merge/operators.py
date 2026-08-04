# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import bmesh
import bpy
from bpy.props import EnumProperty


class MODUS_OT_merge(bpy.types.Operator):
    bl_idname = 'modus.merge'
    bl_label = 'Merge'
    bl_description = 'Run Blender merge with a dedicated shortcut'
    bl_options = {'REGISTER', 'UNDO'}

    merge_type: EnumProperty(
        name='Merge Type',
        items=(
            ('LAST', 'Last', 'Merge selected vertices at the last-selected vertex'),
            ('CENTER', 'Center', 'Merge selected vertices at their center'),
        ),
        default='LAST',
    )

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'EDIT_MESH'
            and context.active_object is not None
            and context.active_object.type == 'MESH'
        )

    @classmethod
    def description(cls, _context, properties):
        if properties.merge_type == 'CENTER':
            return 'Merge selected vertices at center'
        return 'Merge selected vertices at the last-selected vertex'

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        bm.select_history.validate()

        selected_count = sum(1 for vert in bm.verts if vert.select)
        if selected_count < 2:
            return {'CANCELLED'}

        if self.merge_type == 'LAST':
            # Blender only makes its native "At Last" merge available when the
            # active selection-history element is a selected vertex. Box/select-all
            # style selections have no unambiguous last vertex, so quietly cancel.
            active = bm.select_history.active
            if not isinstance(active, bmesh.types.BMVert) or not active.select:
                return {'CANCELLED'}

        try:
            # Use Blender's native operator. This preserves exactly the same
            # topology, UV handling, active-element behavior, and undo behavior
            # as choosing At Last / At Center from Blender's Merge menu.
            bpy.ops.mesh.merge(type=self.merge_type, uvs=True)
        except (RuntimeError, TypeError, ValueError):
            # Invalid or ambiguous selections should behave like an unavailable
            # native menu entry: nothing happens and no report is shown.
            return {'CANCELLED'}

        return {'FINISHED'}


CLASSES = (MODUS_OT_merge,)
