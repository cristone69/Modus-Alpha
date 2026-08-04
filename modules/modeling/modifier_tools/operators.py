# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import bmesh
import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, FloatProperty


_MIRROR_NAMES = {
    'X': 'Modus Mirror X',
    'Y': 'Modus Mirror Y',
    'Z': 'Modus Mirror Z',
}

_BEVEL_NAME = 'Modus Bevel'
_CUTTER_COLLECTION_NAME = 'Cutters'
_SELECTION_TRACKER_OWNER = object()
_SELECTION_ORDER = []


def _selected_mesh_objects(context):
    return [obj for obj in context.selected_objects if obj.type == 'MESH']


def _record_selection_order():
    context = bpy.context
    if (
        not context
        or getattr(context, 'mode', None) != 'OBJECT'
        or getattr(context, 'view_layer', None) is None
    ):
        return

    selected = _selected_mesh_objects(context)
    selected_by_pointer = {obj.as_pointer(): obj for obj in selected}
    _SELECTION_ORDER[:] = [
        pointer for pointer in _SELECTION_ORDER
        if pointer in selected_by_pointer
    ]

    known = set(_SELECTION_ORDER)
    added = [obj for obj in selected if obj.as_pointer() not in known]
    active = context.view_layer.objects.active
    active_pointer = (
        active.as_pointer()
        if active and active.type == 'MESH' and active.select_get()
        else None
    )
    added_pointers = {obj.as_pointer() for obj in added}

    for obj in added:
        if obj.as_pointer() != active_pointer:
            _SELECTION_ORDER.append(obj.as_pointer())
    if active_pointer in added_pointers:
        _SELECTION_ORDER.append(active_pointer)


def _ordered_selected_mesh_objects(context):
    _record_selection_order()
    selected = _selected_mesh_objects(context)
    selected_by_pointer = {obj.as_pointer(): obj for obj in selected}
    ordered = [
        selected_by_pointer[pointer]
        for pointer in _SELECTION_ORDER
        if pointer in selected_by_pointer
    ]
    ordered_pointers = {obj.as_pointer() for obj in ordered}
    ordered.extend(
        obj for obj in selected
        if obj.as_pointer() not in ordered_pointers
    )
    return ordered


def _collection_contains(root, wanted):
    if root is wanted:
        return True
    return any(_collection_contains(child, wanted) for child in root.children)


def _get_cutter_collection(scene):
    collection = bpy.data.collections.get(_CUTTER_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(_CUTTER_COLLECTION_NAME)

    if not _collection_contains(scene.collection, collection):
        scene.collection.children.link(collection)

    collection.hide_render = True
    return collection


def _move_to_cutter_collection(obj, collection):
    if collection.objects.get(obj.name) is None:
        collection.objects.link(obj)

    for current_collection in list(obj.users_collection):
        if current_collection is not collection:
            current_collection.objects.unlink(obj)

    obj.display_type = 'WIRE'


def _apply_modifier_types(
    context,
    modifier_types,
    remove_missing_booleans=False,
):
    applied = 0
    failed = 0
    removed = 0
    previous_active = context.view_layer.objects.active

    for obj in _selected_mesh_objects(context):
        modifier_names = []
        for modifier in list(obj.modifiers):
            if modifier.type not in modifier_types:
                continue

            missing_operand = (
                modifier.type == 'BOOLEAN'
                and (
                    (
                        modifier.operand_type == 'OBJECT'
                        and modifier.object is None
                    )
                    or (
                        modifier.operand_type == 'COLLECTION'
                        and modifier.collection is None
                    )
                )
            )
            if remove_missing_booleans and missing_operand:
                obj.modifiers.remove(modifier)
                removed += 1
                continue

            modifier_names.append(modifier.name)

        context.view_layer.objects.active = obj
        for modifier_name in modifier_names:
            if obj.modifiers.get(modifier_name) is None:
                continue

            try:
                with context.temp_override(object=obj, active_object=obj):
                    result = bpy.ops.object.modifier_apply(modifier=modifier_name)
                if 'FINISHED' in result:
                    applied += 1
                else:
                    failed += 1
            except RuntimeError:
                failed += 1

    if previous_active and previous_active.name in context.view_layer.objects:
        context.view_layer.objects.active = previous_active

    return applied, failed, removed


@persistent
def _restore_selection_tracker(_filepath):
    register_selection_tracker()


def register_selection_tracker():
    bpy.msgbus.clear_by_owner(_SELECTION_TRACKER_OWNER)
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.LayerObjects, 'active'),
        owner=_SELECTION_TRACKER_OWNER,
        args=(),
        notify=_record_selection_order,
        options={'PERSISTENT'},
    )

    if _restore_selection_tracker not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_restore_selection_tracker)

    _SELECTION_ORDER.clear()
    _record_selection_order()


def unregister_selection_tracker():
    bpy.msgbus.clear_by_owner(_SELECTION_TRACKER_OWNER)
    if _restore_selection_tracker in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_restore_selection_tracker)
    _SELECTION_ORDER.clear()


class MODUS_OT_toggle_mirror_axis(bpy.types.Operator):
    bl_idname = 'modus.toggle_mirror_axis'
    bl_label = 'Toggle Mirror Axis'
    bl_description = 'Add or remove a cage-visible Mirror modifier on the chosen axis'
    bl_options = {'REGISTER', 'UNDO'}

    axis: EnumProperty(
        name='Axis',
        items=(
            ('X', 'X', 'Mirror across the local X axis'),
            ('Y', 'Y', 'Mirror across the local Y axis'),
            ('Z', 'Z', 'Mirror across the local Z axis'),
        ),
        default='X',
    )

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'EDIT_MESH'
            and context.edit_object is not None
            and context.edit_object.type == 'MESH'
        )

    def execute(self, context):
        obj = context.edit_object
        name = _MIRROR_NAMES[self.axis]
        existing = obj.modifiers.get(name)

        if existing and existing.type == 'MIRROR':
            obj.modifiers.remove(existing)
            return {'FINISHED'}

        modifier = obj.modifiers.new(name=name, type='MIRROR')
        modifier.use_axis = tuple(axis == self.axis for axis in ('X', 'Y', 'Z'))
        modifier.use_mirror_merge = True
        modifier.merge_threshold = 0.00001
        modifier.use_clip = True
        modifier.show_in_editmode = True
        modifier.show_on_cage = True
        return {'FINISHED'}


class MODUS_ModelingSettings(bpy.types.PropertyGroup):
    edge_weight_mode: EnumProperty(
        name='Mode',
        items=(
            ('BEVEL', 'Bevel', 'Assign bevel edge weights'),
            ('CREASE', 'Crease', 'Assign subdivision crease weights'),
            ('SEAM', 'Seam', 'Mark UV seams'),
            ('SHARP', 'Sharp', 'Mark sharp edges'),
        ),
        default='BEVEL',
    )
    bevel_weight_value: FloatProperty(name='Bevel Weight', min=0.0, max=1.0, default=1.0)
    crease_weight_value: FloatProperty(name='Crease Weight', min=0.0, max=1.0, default=0.55)


def _set_active_edge_weight(context, clear=False):
    settings = context.scene.modus_modeling
    mode = settings.edge_weight_mode
    changed = 0

    for obj in context.objects_in_mode_unique_data:
        if obj.type != 'MESH':
            continue

        bm = bmesh.from_edit_mesh(obj.data)
        layer = None
        value = None
        if mode == 'BEVEL':
            layer = bm.edges.layers.float.get('bevel_weight_edge') or bm.edges.layers.float.new('bevel_weight_edge')
            value = 0.0 if clear else settings.bevel_weight_value
        elif mode == 'CREASE':
            layer = bm.edges.layers.float.get('crease_edge') or bm.edges.layers.float.new('crease_edge')
            value = 0.0 if clear else settings.crease_weight_value

        touched = False
        for edge in bm.edges:
            if not edge.select:
                continue
            if layer is not None:
                edge[layer] = value
            elif mode == 'SEAM':
                edge.seam = not clear
            else:  # SHARP
                edge.smooth = clear
            changed += 1
            touched = True

        if touched:
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

    return changed, mode, value

class MODUS_OT_assign_edge_weight(bpy.types.Operator):
    bl_idname='modus.assign_edge_weight'; bl_label='Assign Edge Attribute'; bl_description='Assign the active bevel, crease, seam, or sharp setting to selected edges'; bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls, context): return context.mode=='EDIT_MESH' and context.edit_object is not None
    def execute(self, context):
        changed, mode, value=_set_active_edge_weight(context, False)
        if not changed: return {'CANCELLED'}
        if mode in {'BEVEL', 'CREASE'}:
            message = f'Set {mode.lower()} weight to {value:g}'
        else:
            message = f'Marked {mode.lower()}'
        self.report({'INFO'}, message + f' on {changed} edge' + ('s' if changed != 1 else ''))
        return {'FINISHED'}

class MODUS_OT_clear_edge_weight(bpy.types.Operator):
    bl_idname='modus.clear_edge_weight'; bl_label='Clear Edge Attribute'; bl_description='Clear the active bevel, crease, seam, or sharp setting from selected edges'; bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls, context): return context.mode=='EDIT_MESH' and context.edit_object is not None
    def execute(self, context):
        changed, mode, _value=_set_active_edge_weight(context, True)
        if not changed: return {'CANCELLED'}
        suffix = ' weight' if mode in {'BEVEL', 'CREASE'} else ''
        self.report({'INFO'}, f'Cleared {mode.lower()}{suffix} on {changed} edge' + ('s' if changed != 1 else ''))
        return {'FINISHED'}



class MODUS_OT_add_bevel(bpy.types.Operator):
    bl_idname = 'modus.add_bevel'
    bl_label = 'Add Bevel'
    bl_description = 'Add a Bevel modifier configured for bevel weights; existing Modus Bevel modifiers are preserved'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(_selected_mesh_objects(context))

    def execute(self, context):
        added = 0
        for obj in _selected_mesh_objects(context):
            existing = obj.modifiers.get(_BEVEL_NAME)
            if existing and existing.type == 'BEVEL':
                continue

            modifier = obj.modifiers.new(name=_BEVEL_NAME, type='BEVEL')
            modifier.segments = 2
            modifier.limit_method = 'WEIGHT'
            modifier.profile = 1.0
            modifier.miter_outer = 'MITER_ARC'
            modifier.show_in_editmode = False
            added += 1

        if added:
            self.report({'INFO'}, f'Added Bevel to {added} object' + ('s' if added != 1 else ''))
        return {'FINISHED'}


class MODUS_OT_apply_mirror_boolean(bpy.types.Operator):
    bl_idname = 'modus.apply_mirror_boolean'
    bl_label = 'Apply Mirror and Boolean'
    bl_description = 'Apply Mirror and Boolean modifiers in their current stack order'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(_selected_mesh_objects(context))

    def execute(self, context):
        applied, failed, removed = _apply_modifier_types(
            context,
            {'MIRROR', 'BOOLEAN'},
            remove_missing_booleans=True,
        )
        if not applied and not failed and not removed:
            self.report({'INFO'}, 'No Mirror or Boolean modifiers to apply')
            return {'CANCELLED'}

        parts = []
        if applied:
            parts.append(
                f'Applied {applied} Mirror/Boolean modifier'
                + ('s' if applied != 1 else '')
            )
        if removed:
            parts.append(
                f'removed {removed} Boolean modifier'
                + ('s with missing operands' if removed != 1 else ' with a missing operand')
            )
        if failed:
            parts.append(f'{failed} could not be applied')
        self.report({'WARNING'} if failed else {'INFO'}, '; '.join(parts))
        return {'FINISHED'}



class MODUS_OT_quick_boolean(bpy.types.Operator):
    bl_idname = 'modus.quick_boolean'
    bl_label = 'Quick Boolean'
    bl_description = 'Add Boolean modifiers to the first selected object using the other selected objects as cutters'
    bl_options = {'REGISTER', 'UNDO'}

    operation: EnumProperty(
        name='Operation',
        items=(
            ('DIFFERENCE', 'Difference', 'Subtract the cutters from the target'),
            ('UNION', 'Union', 'Combine the cutters with the target'),
            ('INTERSECT', 'Intersect', 'Keep only overlapping volume'),
        ),
        default='DIFFERENCE',
    )
    solver: EnumProperty(
        name='Solver',
        items=(
            ('EXACT', 'Exact', 'Robust solver with the best support for overlapping geometry'),
            ('MANIFOLD', 'Manifold', 'Fast solver for manifold meshes'),
            ('FLOAT', 'Float', 'Fast floating-point solver with fewer guarantees'),
        ),
        default='EXACT',
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and len(_selected_mesh_objects(context)) >= 2

    def execute(self, context):
        objects = _ordered_selected_mesh_objects(context)
        if len(objects) < 2:
            self.report({'WARNING'}, 'Select a target first, then one or more cutter objects')
            return {'CANCELLED'}

        target, *cutters = objects
        cutter_collection = _get_cutter_collection(context.scene)

        for cutter in cutters:
            modifier = target.modifiers.new(
                name=f'Boolean — {cutter.name}',
                type='BOOLEAN',
            )
            modifier.operand_type = 'OBJECT'
            modifier.object = cutter
            modifier.operation = self.operation
            modifier.solver = self.solver
            _move_to_cutter_collection(cutter, cutter_collection)

        noun = 'modifier' if len(cutters) == 1 else 'modifiers'
        self.report(
            {'INFO'},
            f'Added {len(cutters)} Boolean {noun} to {target.name}',
        )
        return {'FINISHED'}


CLASSES = (
    MODUS_OT_toggle_mirror_axis,
    MODUS_ModelingSettings,
    MODUS_OT_assign_edge_weight,
    MODUS_OT_clear_edge_weight,
    MODUS_OT_add_bevel,
    MODUS_OT_apply_mirror_boolean,
    MODUS_OT_quick_boolean,
)
