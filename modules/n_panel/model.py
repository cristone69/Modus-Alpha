# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup

from ...core import redraw, settings

ADDON_ID = __package__.partition('.modules')[0]

_FALLBACK_ICONS = (
    'BOOKMARKS', 'COLLECTION_NEW', 'FILE_FOLDER', 'FILTER', 'MODIFIER',
    'MATERIAL', 'OBJECT_DATA', 'OUTLINER_COLLECTION', 'PREFERENCES',
    'SHADERFX', 'TOOL_SETTINGS', 'WORLD',
)


def _category_update(_self, _context):
    if settings.is_loading():
        return
    settings.schedule_save()
    redraw.tag_view3d_redraw()


def _category_filter_update(_self, _context):
    if settings.is_loading():
        return
    settings.schedule_save()
    from .core import filter_engine
    filter_engine.schedule_apply()


def all_icon_identifiers() -> tuple[str, ...]:
    """Return Blender's complete built-in icon identifier list."""
    try:
        parameter = bpy.types.UILayout.bl_rna.functions['operator'].parameters['icon']
        icons = tuple(item.identifier for item in parameter.enum_items if item.identifier != 'NONE')
        if icons:
            return icons
    except (AttributeError, KeyError, TypeError):
        pass
    return _FALLBACK_ICONS


class MODUS_NPanelTab(PropertyGroup):
    name: StringProperty(name='Tab')


class MODUS_NPanelTabChoice(PropertyGroup):
    name: StringProperty(name='Tab')
    selected: BoolProperty(name='Selected', default=False)


class MODUS_NPanelCategory(PropertyGroup):
    name: StringProperty(name='Name', default='Category', update=_category_update)
    icon: StringProperty(name='Icon', default='BOOKMARKS', update=_category_update)
    enabled: BoolProperty(name='Active', default=True, update=_category_filter_update)
    expanded: BoolProperty(name='Expanded', default=False, update=_category_update)
    tabs: CollectionProperty(type=MODUS_NPanelTab)
    active_tab_index: IntProperty(default=0, update=_category_update)


def get_preferences(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


CLASSES = (
    MODUS_NPanelTab,
    MODUS_NPanelTabChoice,
    MODUS_NPanelCategory,
)
