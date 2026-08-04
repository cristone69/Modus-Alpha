# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import math

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty


class MODUS_OT_assign_weights_by_angle(bpy.types.Operator):
    bl_idname = 'modus.assign_weights_by_angle'
    bl_label = 'Assign by Angle'
    bl_description = 'Select edges by face angle and assign a bevel weight, crease, seam, or sharp edge'
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(
        name='Type',
        items=(
            ('BEVEL', 'Bevel Weight', 'Assign the current Modus bevel weight'),
            ('CREASE', 'Crease', 'Assign the current Modus crease weight'),
            ('SEAM', 'Seam', 'Mark matching edges as UV seams'),
            ('SHARP', 'Sharp', 'Mark matching edges as sharp'),
        ),
        default='BEVEL',
    )
    angle: FloatProperty(
        name='Angle',
        description='Select edges whose face angle is equal to or greater than this value',
        subtype='ANGLE',
        unit='ROTATION',
        min=0.0,
        max=math.pi,
        default=math.radians(30.0),
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.edit_object is not None

    def execute(self, context):
        settings = context.scene.modus_modeling
        matched = 0
        processed_objects = 0

        for obj in context.objects_in_mode_unique_data:
            if obj.type != 'MESH':
                continue

            bm = bmesh.from_edit_mesh(obj.data)
            bevel_layer = None
            crease_layer = None
            if self.target == 'BEVEL':
                bevel_layer = (
                    bm.edges.layers.float.get('bevel_weight_edge')
                    or bm.edges.layers.float.new('bevel_weight_edge')
                )
            elif self.target == 'CREASE':
                crease_layer = (
                    bm.edges.layers.float.get('crease_edge')
                    or bm.edges.layers.float.new('crease_edge')
                )

            object_matched = 0
            for edge in bm.edges:
                edge.select = False
                if len(edge.link_faces) != 2:
                    continue

                try:
                    face_angle = edge.calc_face_angle(0.0)
                except (ValueError, RuntimeError):
                    continue
                if face_angle + 1.0e-9 < self.angle:
                    continue

                edge.select = True
                if bevel_layer is not None:
                    edge[bevel_layer] = settings.bevel_weight_value
                elif crease_layer is not None:
                    edge[crease_layer] = settings.crease_weight_value
                elif self.target == 'SEAM':
                    edge.seam = True
                else:
                    edge.smooth = False
                object_matched += 1

            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            processed_objects += 1
            matched += object_matched

        if processed_objects == 0:
            self.report({'WARNING'}, 'No editable mesh objects found')
            return {'CANCELLED'}

        target_name = {
            'BEVEL': 'Bevel Weight',
            'CREASE': 'Crease',
            'SEAM': 'Seam',
            'SHARP': 'Sharp',
        }[self.target]
        self.report(
            {'INFO'},
            f'{target_name}: selected and assigned {matched} edge' + ('s' if matched != 1 else '')
            + f' at {math.degrees(self.angle):g}° or greater',
        )
        return {'FINISHED'}


class MODUS_OT_clear_edge_markings(bpy.types.Operator):
    bl_idname = 'modus.clear_edge_markings'
    bl_label = 'Clear Edge Markings'
    bl_description = (
        'Clear the enabled edge marking types from every edge in all mesh objects currently in Edit Mode'
    )
    bl_options = {'REGISTER', 'UNDO'}

    clear_bevel: BoolProperty(
        name='Bevel Weight',
        description='Clear all bevel edge weights',
        default=False,
    )
    clear_crease: BoolProperty(
        name='Crease',
        description='Clear all subdivision crease edge weights',
        default=False,
    )
    clear_seam: BoolProperty(
        name='Seam',
        description='Clear all UV seam markings',
        default=False,
    )
    clear_sharp: BoolProperty(
        name='Sharp',
        description='Clear all sharp edge markings',
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.edit_object is not None

    def invoke(self, context, _event):
        mode = context.scene.modus_modeling.edge_weight_mode
        self.clear_bevel = mode == 'BEVEL'
        self.clear_crease = mode == 'CREASE'
        self.clear_seam = mode == 'SEAM'
        self.clear_sharp = mode == 'SHARP'
        return self.execute(context)

    def draw(self, _context):
        layout = self.layout
        layout.label(text='Clear from every edge:')
        column = layout.column(align=True)
        column.prop(self, 'clear_bevel')
        column.prop(self, 'clear_crease')
        column.prop(self, 'clear_seam')
        column.prop(self, 'clear_sharp')

    def execute(self, context):
        enabled = {
            'BEVEL': self.clear_bevel,
            'CREASE': self.clear_crease,
            'SEAM': self.clear_seam,
            'SHARP': self.clear_sharp,
        }
        changed_by_type = {key: 0 for key in enabled}
        processed_objects = 0

        for obj in context.objects_in_mode_unique_data:
            if obj.type != 'MESH':
                continue

            bm = bmesh.from_edit_mesh(obj.data)
            bevel_layer = (
                bm.edges.layers.float.get('bevel_weight_edge')
                if self.clear_bevel else None
            )
            crease_layer = (
                bm.edges.layers.float.get('crease_edge')
                if self.clear_crease else None
            )
            touched = False

            for edge in bm.edges:
                if bevel_layer is not None and abs(edge[bevel_layer]) > 1.0e-12:
                    edge[bevel_layer] = 0.0
                    changed_by_type['BEVEL'] += 1
                    touched = True
                if crease_layer is not None and abs(edge[crease_layer]) > 1.0e-12:
                    edge[crease_layer] = 0.0
                    changed_by_type['CREASE'] += 1
                    touched = True
                if self.clear_seam and edge.seam:
                    edge.seam = False
                    changed_by_type['SEAM'] += 1
                    touched = True
                if self.clear_sharp and not edge.smooth:
                    edge.smooth = True
                    changed_by_type['SHARP'] += 1
                    touched = True

            if touched:
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            processed_objects += 1

        if processed_objects == 0:
            self.report({'WARNING'}, 'No editable mesh objects found')
            return {'CANCELLED'}

        selected_types = [key for key, is_enabled in enabled.items() if is_enabled]
        if not selected_types:
            self.report({'INFO'}, 'No edge marking types enabled')
            return {'FINISHED'}

        labels = {
            'BEVEL': 'bevel weight',
            'CREASE': 'crease',
            'SEAM': 'seam',
            'SHARP': 'sharp',
        }
        details = ', '.join(
            f"{labels[key]}: {changed_by_type[key]}" for key in selected_types
        )
        total = sum(changed_by_type[key] for key in selected_types)
        self.report(
            {'INFO'},
            f'Cleared {total} edge marking' + ('s' if total != 1 else '') + f' ({details})',
        )
        return {'FINISHED'}


CLASSES = (MODUS_OT_assign_weights_by_angle, MODUS_OT_clear_edge_markings)
