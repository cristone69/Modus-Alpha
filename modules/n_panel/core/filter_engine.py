# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import bpy

from ..model import get_preferences

_INTERNAL_TABS = {'Item', 'Tool', 'View', 'Edit', 'Options', 'Relations', 'Animation'}
_known_panels: dict[type, str] = {}
_hidden_panels: set[type] = set()
_apply_pending = False
_applying = False


def _is_target_panel(panel_cls: type) -> bool:
    return (
        issubclass(panel_cls, bpy.types.Panel)
        and getattr(panel_cls, 'bl_space_type', None) == 'VIEW_3D'
        and getattr(panel_cls, 'bl_region_type', None) == 'UI'
        and bool(getattr(panel_cls, 'bl_category', ''))
    )


def _discover_registered_panels() -> None:
    for name in dir(bpy.types):
        panel_cls = getattr(bpy.types, name, None)
        if not isinstance(panel_cls, type):
            continue
        try:
            if not _is_target_panel(panel_cls):
                continue
            category = getattr(panel_cls, '_modus_npanel_category', None)
            if not category:
                category = panel_cls.bl_category
                setattr(panel_cls, '_modus_npanel_category', category)
            _known_panels[panel_cls] = category
        except (AttributeError, TypeError, RuntimeError):
            continue


def _is_blender_native_panel(panel_cls: type) -> bool:
    module_name = getattr(panel_cls, '__module__', '')
    return module_name == 'bl_ui' or module_name.startswith('bl_ui.')


def _native_tabs() -> set[str]:
    native = set(_INTERNAL_TABS)
    native.update(
        category
        for panel_cls, category in _known_panels.items()
        if category and _is_blender_native_panel(panel_cls)
    )
    return native


def available_tabs() -> list[str]:
    _discover_registered_panels()
    native_tabs = _native_tabs()
    return sorted({
        category
        for category in _known_panels.values()
        if category and category not in native_tabs
    })


def _assigned_tabs(prefs) -> set[str]:
    return {
        tab.name
        for category in prefs.npanel_categories
        for tab in category.tabs
        if tab.name
    }


def _visible_tabs(prefs) -> set[str]:
    return {
        tab.name
        for category in prefs.npanel_categories
        if category.enabled
        for tab in category.tabs
        if tab.name
    }


def _should_hide(category: str, prefs, assigned: set[str], visible: set[str]) -> bool:
    if category in _native_tabs():
        return False
    if category in assigned:
        return category not in visible
    return prefs.npanel_hide_uncategorized


def _unregister(panel_cls: type) -> None:
    try:
        if getattr(panel_cls, 'is_registered', False):
            bpy.utils.unregister_class(panel_cls)
        _hidden_panels.add(panel_cls)
    except (RuntimeError, ValueError):
        pass


def _register(panel_cls: type) -> None:
    try:
        if not getattr(panel_cls, 'is_registered', False):
            bpy.utils.register_class(panel_cls)
        _hidden_panels.discard(panel_cls)
    except (RuntimeError, ValueError):
        pass


def _depth(panel_cls: type) -> int:
    depth = 0
    parent = getattr(panel_cls, 'bl_parent_id', '')
    seen = set()
    while parent and parent not in seen:
        seen.add(parent)
        depth += 1
        parent_cls = getattr(bpy.types, parent, None)
        parent = getattr(parent_cls, 'bl_parent_id', '') if parent_cls else ''
    return depth


def restore_all() -> None:
    global _applying
    if _applying:
        return
    _applying = True
    try:
        for panel_cls in sorted(tuple(_hidden_panels), key=_depth):
            _register(panel_cls)
        _tag_redraw()
    finally:
        _applying = False


def apply_filter() -> None:
    global _applying
    if _applying:
        return
    prefs = get_preferences()
    if prefs is None:
        return

    _applying = True
    try:
        _discover_registered_panels()
        if not prefs.npanel_feature_enabled or not prefs.npanel_filtering_enabled:
            for panel_cls in sorted(tuple(_hidden_panels), key=_depth):
                _register(panel_cls)
            _tag_redraw()
            return

        assigned = _assigned_tabs(prefs)
        visible = _visible_tabs(prefs)

        to_restore = [
            panel_cls
            for panel_cls in _hidden_panels
            if not _should_hide(_known_panels.get(panel_cls, ''), prefs, assigned, visible)
        ]
        for panel_cls in sorted(to_restore, key=_depth):
            _register(panel_cls)

        to_hide = [
            panel_cls
            for panel_cls, category in _known_panels.items()
            if panel_cls not in _hidden_panels
            and _should_hide(category, prefs, assigned, visible)
        ]
        for panel_cls in sorted(to_hide, key=_depth, reverse=True):
            _unregister(panel_cls)
        _tag_redraw()
    finally:
        _applying = False


def _timer_apply():
    global _apply_pending
    _apply_pending = False
    apply_filter()
    return None


def schedule_apply() -> None:
    global _apply_pending
    if _apply_pending:
        return
    _apply_pending = True
    try:
        bpy.app.timers.register(_timer_apply, first_interval=0.05)
    except ValueError:
        _apply_pending = False


def _tag_redraw() -> None:
    window_manager = getattr(bpy.context, 'window_manager', None)
    if window_manager is None:
        return
    for window in window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def shutdown() -> None:
    global _apply_pending
    restore_all()
    if bpy.app.timers.is_registered(_timer_apply):
        bpy.app.timers.unregister(_timer_apply)
    _apply_pending = False
    _known_panels.clear()
    _hidden_panels.clear()
