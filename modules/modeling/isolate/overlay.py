# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import blf
import bpy

from . import state

_DRAW_HANDLE = None


def draw_isolate_level():
    context = bpy.context
    area = context.area
    region = context.region
    space = context.space_data
    scene = context.scene

    if not area or area.type != 'VIEW_3D' or not region or region.type != 'WINDOW':
        return
    if not space or not getattr(space, 'local_view', None) or not scene:
        return

    level = state.level_count(scene, area)
    if level < 1:
        return

    text = f'Isolate Level: {level}'
    scale = context.preferences.system.ui_scale
    font_id = 0
    font_size = max(12, int(14 * scale))
    blf.size(font_id, font_size)
    width, _height = blf.dimensions(font_id, text)

    top_offset = int(12 * scale)
    for ui_region in area.regions:
        if ui_region.type in {'HEADER', 'TOOL_HEADER'} and ui_region.alignment == 'TOP':
            top_offset += ui_region.height

    blf.position(font_id, (region.width - width) / 2, region.height - top_offset - font_size, 0)
    blf.color(font_id, 1.0, 1.0, 1.0, 1.0)
    blf.draw(font_id, text)


def register():
    global _DRAW_HANDLE
    if _DRAW_HANDLE is None:
        _DRAW_HANDLE = bpy.types.SpaceView3D.draw_handler_add(
            draw_isolate_level, (), 'WINDOW', 'POST_PIXEL'
        )


def unregister():
    global _DRAW_HANDLE
    if _DRAW_HANDLE is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_DRAW_HANDLE, 'WINDOW')
        except (ReferenceError, ValueError):
            pass
        _DRAW_HANDLE = None
