# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import bpy

from ..model import get_preferences

_installed = False


def draw_toolbar(self, context):
    prefs = get_preferences(context)
    if (
        prefs is None
        or not prefs.show_tool_header_controls
        or not prefs.npanel_feature_enabled
    ):
        return
    layout = self.layout
    row = layout.row(align=True)
    row.operator(
        'modus.npanel_toggle_filter',
        text='',
        icon='FILTER',
        depress=prefs.npanel_filtering_enabled,
    )
    if prefs.npanel_filtering_enabled:
        for index, category in enumerate(prefs.npanel_categories):
            op = row.operator(
                'modus.npanel_toggle_category',
                text='',
                icon=category.icon,
                depress=category.enabled,
            )
            op.category_index = index


def install() -> None:
    global _installed
    if _installed:
        return
    try:
        bpy.types.VIEW3D_HT_tool_header.prepend(draw_toolbar)
        _installed = True
    except (AttributeError, RuntimeError):
        _installed = False


def uninstall() -> None:
    global _installed
    try:
        bpy.types.VIEW3D_HT_tool_header.remove(draw_toolbar)
    except (AttributeError, RuntimeError):
        pass
    _installed = False
