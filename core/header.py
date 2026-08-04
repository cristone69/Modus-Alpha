# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

_original_draw_tool_settings = None
_entries = []


def _get_preferences(context):
    addon_id = __package__.partition('.core')[0]
    addon = context.preferences.addons.get(addon_id)
    return addon.preferences if addon else None


def register_entry(entry_id, draw, order=100):
    unregister_entry(entry_id)
    _entries.append((order, entry_id, draw))
    _entries.sort(key=lambda item: (item[0], item[1]))


def unregister_entry(entry_id):
    global _entries
    _entries = [item for item in _entries if item[1] != entry_id]


def _draw_modus(layout, context):
    if context.area is None or context.area.type != 'VIEW_3D':
        return

    preferences = _get_preferences(context)
    if preferences is None or not preferences.show_tool_header_controls:
        return

    visible = [item for item in _entries if item[2](None, context, probe=True)]
    if not visible:
        return

    row = layout.row(align=True)
    row.separator(factor=0.5)
    for _order, _entry_id, draw in visible:
        draw(row, context, probe=False)


def _draw_tool_settings_with_modus(self, context):
    if _original_draw_tool_settings is not None:
        _original_draw_tool_settings(self, context)
    _draw_modus(self.layout, context)


def install():
    global _original_draw_tool_settings
    header_type = bpy.types.VIEW3D_HT_tool_header
    if _original_draw_tool_settings is None:
        _original_draw_tool_settings = header_type.draw_tool_settings
    header_type.draw_tool_settings = _draw_tool_settings_with_modus


def uninstall():
    global _original_draw_tool_settings
    header_type = bpy.types.VIEW3D_HT_tool_header
    if _original_draw_tool_settings is not None:
        header_type.draw_tool_settings = _original_draw_tool_settings
        _original_draw_tool_settings = None
    _entries.clear()
