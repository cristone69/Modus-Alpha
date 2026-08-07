# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import bpy

from . import core
from .modules import modeling, n_panel, procedural_geonodes, quick_menu, retopology, topology, uv_preview, viewport

MODULES = (
    n_panel,
    topology,
    modeling,
    procedural_geonodes,
    quick_menu,
    uv_preview,
    viewport,
    retopology,
)


def _settings_update(_self, _context):
    core.settings.schedule_save()


def _tool_header_update(_self, _context):
    core.redraw.tag_view3d_redraw()
    core.settings.schedule_save()


def _npanel_filter_update(_self, _context):
    n_panel.filter_engine.schedule_apply()
    core.redraw.tag_view3d_redraw()
    core.settings.schedule_save()


class MODUS_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    show_tool_header_controls: bpy.props.BoolProperty(
        name='Tool Header Controls',
        description="Show Modus controls after Blender's left-side Tool Header controls",
        default=True,
        update=_tool_header_update,
    )
    npanel_feature_enabled: bpy.props.BoolProperty(
        name='N-Panel Filtering',
        description='Show the N-Panel filtering feature and its viewport controls',
        default=True,
        update=_npanel_filter_update,
    )
    npanel_filtering_enabled: bpy.props.BoolProperty(
        name='Filter Tabs',
        description='Show only tabs assigned to enabled categories',
        default=False,
        update=_npanel_filter_update,
    )
    npanel_hide_uncategorized: bpy.props.BoolProperty(
        name='Hide Uncategorized Add-on Tabs',
        description='Hide unassigned add-on tabs while filtering is enabled',
        default=True,
        update=_npanel_filter_update,
    )
    topology_tri_color: bpy.props.FloatVectorProperty(
        name='Triangle Color',
        description='Color used for triangles in the live topology highlighter',
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(0.08, 0.80, 0.18, 0.40),
        update=_tool_header_update,
    )
    topology_ngon_color: bpy.props.FloatVectorProperty(
        name='N-gon Color',
        description='Color used for n-gons in the live topology highlighter',
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(0.015, 0.055, 0.22, 0.68),
        update=_tool_header_update,
    )
    uv_grid_resolution: bpy.props.EnumProperty(
        name='UV Grid Resolution',
        items=(('1024', '1K', '1024 × 1024'), ('2048', '2K', '2048 × 2048'), ('4096', '4K', '4096 × 4096'), ('8192', '8K', '8192 × 8192')),
        default='4096',
        update=_settings_update,
    )
    uv_grid_style: bpy.props.EnumProperty(
        name='UV Grid Style',
        items=(('UV_GRID', 'UV Grid', 'Black-and-white UV test grid'), ('COLOR_GRID', 'Color Grid', 'Colored UV test grid')),
        default='UV_GRID',
        update=_settings_update,
    )
    symmetrize_center_tris_to_quads: bpy.props.BoolProperty(
        name='Center Tris to Quads by Default',
        description='Enable center triangle-pair cleanup whenever Flick Symmetrize starts',
        default=False,
        update=_settings_update,
    )
    symmetrize_context_scope: bpy.props.BoolProperty(
        name='Context-Based Selection',
        description='Choose Selected Only automatically from the current mesh selection each time',
        default=True,
        update=_settings_update,
    )
    symmetrize_default_scope: bpy.props.EnumProperty(
        name='Default Scope',
        description='Scope used when context-based selection is disabled',
        items=(
            ('ALL', 'Whole Mesh', 'Symmetrize the whole mesh'),
            ('SELECTED', 'Selected Only', 'Symmetrize selected mesh elements only'),
        ),
        default='ALL',
        update=_settings_update,
    )
    npanel_categories: bpy.props.CollectionProperty(type=n_panel.model.MODUS_NPanelCategory)
    npanel_active_category_index: bpy.props.IntProperty(default=0, update=_settings_update)
    preferences_tab: bpy.props.EnumProperty(
        name='Preferences Tab',
        items=(
            ('GENERAL', 'General', 'General Modus settings'),
            ('KEYMAPS', 'Keymaps', 'Editable Modus shortcuts'),
        ),
        default='GENERAL',
    )

    def draw(self, context):
        layout = self.layout

        tabs = layout.row(align=True)
        tabs.prop(self, 'preferences_tab', expand=True)
        layout.separator()

        if self.preferences_tab == 'GENERAL':
            general = layout.box()
            general.label(text='Interface')
            general.prop(self, 'show_tool_header_controls', text='Show Viewport Header Buttons')
            general.prop(self, 'npanel_feature_enabled', text='N-Panel Filtering')

            topology_box = layout.box()
            topology_box.label(text='Live Topology Highlighter')
            topology_box.prop(self, 'topology_tri_color')
            topology_box.prop(self, 'topology_ngon_color')

            uv_box = layout.box()
            uv_box.label(text='UV Preview')
            uv_box.prop(self, 'uv_grid_resolution')
            uv_box.prop(self, 'uv_grid_style')

            symmetrize_box = layout.box()
            symmetrize_box.label(text='Flick Symmetrize')
            symmetrize_box.prop(self, 'symmetrize_center_tris_to_quads')
            symmetrize_box.prop(self, 'symmetrize_context_scope')
            scope_row = symmetrize_box.row()
            scope_row.enabled = not self.symmetrize_context_scope
            scope_row.prop(self, 'symmetrize_default_scope', expand=True)

            layout.separator()
            n_panel.preferences_ui.draw_preferences(self, context, layout)

            layout.separator()
            persistent = layout.box()
            persistent.label(text='Persistent Settings')
            persistent.label(
                text='Preferences, N-Panel categories, and shortcuts are saved automatically.',
                icon='INFO',
            )
            row = persistent.row(align=True)
            row.operator('modus.settings_export', text='Export Settings', icon='EXPORT')
            row.operator('modus.settings_import', text='Import Settings', icon='IMPORT')

            destructive = persistent.row(align=True)
            destructive.operator('modus.settings_reset', text='Reset Settings', icon='LOOP_BACK')
            destructive.operator(
                'modus.settings_delete',
                text='Delete Saved Configuration',
                icon='TRASH',
            )

            path = str(core.settings.config_file_path())
            persistent.separator()
            persistent.label(text=f'Saved configuration: {path}', icon='FILE')
            if not core.settings.config_exists():
                persistent.label(
                    text='No saved file currently exists. Changing a setting will create it again.',
                    icon='ERROR',
                )

        elif self.preferences_tab == 'KEYMAPS':
            keymap_box = layout.box()
            keymap_box.label(text='Keymaps')
            keymap_box.label(text='Shortcut changes are included in persistent settings.', icon='INFO')
            core.keymaps.draw_preferences(keymap_box.column(align=True), context)


_CLASSES = (MODUS_Preferences,)


def register():
    keymap_definitions = []
    for module in MODULES:
        keymap_definitions.extend(getattr(module, 'KEYMAP_DEFINITIONS', ()))
    core.keymaps.configure(keymap_definitions)
    core.timing.instrument_modules(core, *MODULES)

    core.register()
    for module in MODULES:
        module.register()
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    core.keymaps.register()
    core.settings.activate()
    core.header.install()
    n_panel.activate()


def unregister():
    core.settings.prepare_unregister()
    core.header.uninstall()
    n_panel.filter_engine.restore_all()
    core.keymaps.unregister()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    for module in reversed(MODULES):
        module.unregister()
    core.unregister()
