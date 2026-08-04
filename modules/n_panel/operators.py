# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import bpy

from bpy.props import CollectionProperty, IntProperty, StringProperty
from bpy.types import Operator

from .core.filter_engine import available_tabs, schedule_apply
from .model import MODUS_NPanelTabChoice, all_icon_identifiers, get_preferences
from ...core import settings



class MODUS_OT_npanel_open_icon_picker(Operator):
    bl_idname = 'modus.npanel_open_icon_picker'
    bl_label = 'Choose Category Icon'
    bl_options = {'INTERNAL'}

    category_index: IntProperty()

    def execute(self, context):
        bpy.ops.modus.npanel_icon_picker_popup('INVOKE_DEFAULT', category_index=self.category_index)
        return {'FINISHED'}


class MODUS_OT_npanel_set_icon(Operator):
    bl_idname = 'modus.npanel_set_icon'
    bl_label = 'Set Category Icon'
    bl_options = {'INTERNAL'}

    category_index: IntProperty()
    icon_name: StringProperty()

    def execute(self, context):
        prefs = get_preferences(context)
        if prefs is None or not 0 <= self.category_index < len(prefs.npanel_categories):
            return {'CANCELLED'}
        if self.icon_name not in set(all_icon_identifiers()):
            return {'CANCELLED'}
        prefs.npanel_categories[self.category_index].icon = self.icon_name
        settings.schedule_save()
        return {'FINISHED'}


class MODUS_OT_npanel_icon_picker_popup(Operator):
    bl_idname = 'modus.npanel_icon_picker_popup'
    bl_label = 'Category Icon'
    bl_options = {'INTERNAL'}

    category_index: IntProperty()

    def invoke(self, context, _event):
        return context.window_manager.invoke_popup(self, width=360)

    def draw(self, _context):
        layout = self.layout
        grid = layout.grid_flow(
            row_major=True,
            columns=12,
            even_columns=True,
            even_rows=True,
            align=True,
        )
        for identifier in all_icon_identifiers():
            op = grid.operator(
                'modus.npanel_set_icon',
                text='',
                icon=identifier,
            )
            op.category_index = self.category_index
            op.icon_name = identifier

    def execute(self, _context):
        return {'FINISHED'}


class MODUS_OT_npanel_toggle_filter(Operator):
    bl_idname = 'modus.npanel_toggle_filter'
    bl_label = 'Toggle N-Panel Filtering'
    bl_options = {'INTERNAL'}

    def execute(self, context):
        prefs = get_preferences(context)
        if prefs is None:
            return {'CANCELLED'}
        prefs.npanel_filtering_enabled = not prefs.npanel_filtering_enabled
        schedule_apply()
        settings.schedule_save()
        return {'FINISHED'}


class MODUS_OT_npanel_toggle_category(Operator):
    bl_idname = 'modus.npanel_toggle_category'
    bl_label = 'N-Panel Category'
    bl_options = {'INTERNAL'}

    category_index: IntProperty()

    @classmethod
    def description(cls, context, properties):
        prefs = get_preferences(context)
        index = getattr(properties, 'category_index', -1)
        if prefs is not None and 0 <= index < len(prefs.npanel_categories):
            return prefs.npanel_categories[index].name or 'N-Panel Category'
        return 'N-Panel Category'

    def execute(self, context):
        prefs = get_preferences(context)
        if prefs is None or not 0 <= self.category_index < len(prefs.npanel_categories):
            return {'CANCELLED'}
        category = prefs.npanel_categories[self.category_index]
        category.enabled = not category.enabled
        schedule_apply()
        settings.schedule_save()
        return {'FINISHED'}


class MODUS_OT_npanel_add_category(Operator):
    bl_idname = 'modus.npanel_add_category'
    bl_label = 'Create Category'
    bl_options = {'INTERNAL'}

    def execute(self, context):
        prefs = get_preferences(context)
        if prefs is None:
            return {'CANCELLED'}
        category = prefs.npanel_categories.add()
        existing = {item.name for item in prefs.npanel_categories[:-1]}
        number = 1
        name = 'Category'
        while name in existing:
            number += 1
            name = f'Category {number}'
        category.name = name
        category.expanded = True
        prefs.npanel_active_category_index = len(prefs.npanel_categories) - 1
        settings.schedule_save()
        return {'FINISHED'}


class MODUS_OT_npanel_remove_category(Operator):
    bl_idname = 'modus.npanel_remove_category'
    bl_label = 'Delete Category'
    bl_options = {'INTERNAL'}

    category_index: IntProperty()

    def execute(self, context):
        prefs = get_preferences(context)
        if prefs is None or not 0 <= self.category_index < len(prefs.npanel_categories):
            return {'CANCELLED'}
        prefs.npanel_categories.remove(self.category_index)
        prefs.npanel_active_category_index = min(
            prefs.npanel_active_category_index,
            max(0, len(prefs.npanel_categories) - 1),
        )
        schedule_apply()
        settings.schedule_save()
        return {'FINISHED'}


class MODUS_OT_npanel_move_category(Operator):
    bl_idname = 'modus.npanel_move_category'
    bl_label = 'Move Category'
    bl_options = {'INTERNAL'}

    category_index: IntProperty()
    direction: IntProperty()

    def execute(self, context):
        prefs = get_preferences(context)
        if prefs is None:
            return {'CANCELLED'}
        source = self.category_index
        target = source + self.direction
        if 0 <= source < len(prefs.npanel_categories) and 0 <= target < len(prefs.npanel_categories):
            prefs.npanel_categories.move(source, target)
            settings.schedule_save()
        return {'FINISHED'}


class MODUS_OT_npanel_add_tab(Operator):
    bl_idname = 'modus.npanel_add_tab'
    bl_label = 'Add N-Panel Tabs'
    bl_options = {'INTERNAL'}

    category_index: IntProperty()
    choices: CollectionProperty(type=MODUS_NPanelTabChoice)

    def invoke(self, context, _event):
        prefs = get_preferences(context)
        if prefs is None or not 0 <= self.category_index < len(prefs.npanel_categories):
            return {'CANCELLED'}

        assigned = {tab.name for tab in prefs.npanel_categories[self.category_index].tabs}
        self.choices.clear()
        for tab_name in available_tabs():
            if tab_name not in assigned:
                choice = self.choices.add()
                choice.name = tab_name
                choice.selected = False

        if not self.choices:
            self.report({'INFO'}, 'No unassigned N-Panel tabs found')
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=760)

    def draw(self, _context):
        layout = self.layout
        layout.label(text='Select one or more tabs to add:')
        grid = layout.grid_flow(
            row_major=True,
            columns=4,
            even_columns=True,
            even_rows=False,
            align=True,
        )
        for choice in self.choices:
            row = grid.row(align=True)
            row.prop(choice, 'selected', text=choice.name)

    def execute(self, context):
        prefs = get_preferences(context)
        if prefs is None or not 0 <= self.category_index < len(prefs.npanel_categories):
            return {'CANCELLED'}

        category = prefs.npanel_categories[self.category_index]
        existing = {tab.name for tab in category.tabs}
        added = 0
        for choice in self.choices:
            if choice.selected and choice.name not in existing:
                category.tabs.add().name = choice.name
                existing.add(choice.name)
                added += 1

        if added:
            schedule_apply()
            settings.schedule_save()
            return {'FINISHED'}
        self.report({'INFO'}, 'No tabs selected')
        return {'CANCELLED'}


class MODUS_OT_npanel_remove_tab(Operator):
    bl_idname = 'modus.npanel_remove_tab'
    bl_label = 'Remove N-Panel Tab'
    bl_options = {'INTERNAL'}

    category_index: IntProperty()
    tab_index: IntProperty()

    def execute(self, context):
        prefs = get_preferences(context)
        if prefs is None or not 0 <= self.category_index < len(prefs.npanel_categories):
            return {'CANCELLED'}
        category = prefs.npanel_categories[self.category_index]
        if 0 <= self.tab_index < len(category.tabs):
            category.tabs.remove(self.tab_index)
            schedule_apply()
            settings.schedule_save()
        return {'FINISHED'}


CLASSES = (
    MODUS_OT_npanel_open_icon_picker,
    MODUS_OT_npanel_set_icon,
    MODUS_OT_npanel_icon_picker_popup,
    MODUS_OT_npanel_toggle_filter,
    MODUS_OT_npanel_toggle_category,
    MODUS_OT_npanel_add_category,
    MODUS_OT_npanel_remove_category,
    MODUS_OT_npanel_move_category,
    MODUS_OT_npanel_add_tab,
    MODUS_OT_npanel_remove_tab,
)
