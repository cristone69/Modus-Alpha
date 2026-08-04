# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import bpy

_ADDON_KEYMAPS = []
_DEFINITIONS = ()

_SHORTCUT_FIELDS = (
    'type',
    'value',
    'any',
    'shift',
    'ctrl',
    'alt',
    'oskey',
    'key_modifier',
    'active',
    'repeat',
)


def configure(definitions):
    global _DEFINITIONS
    _DEFINITIONS = tuple(definitions)


def register():
    # Defensive cleanup makes reloads deterministic and avoids duplicate addon
    # keymap items after a partial extension reload.
    if _ADDON_KEYMAPS:
        unregister()

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon if wm else None
    if kc is None:
        return

    for definition in _DEFINITIONS:
        km = kc.keymaps.new(
            name=definition['keymap'],
            space_type=definition.get('space_type', 'EMPTY'),
            region_type=definition.get('region_type', 'WINDOW'),
        )
        keymap_args = dict(
            type=definition['type'],
            value=definition.get('value', 'PRESS'),
            any=definition.get('any', False),
            shift=definition.get('shift', False),
            ctrl=definition.get('ctrl', False),
            alt=definition.get('alt', False),
            oskey=definition.get('oskey', False),
            key_modifier=definition.get('key_modifier', 'NONE'),
        )
        try:
            kmi = km.keymap_items.new(
                definition['idname'],
                head=definition.get('head', False),
                **keymap_args,
            )
        except TypeError:
            # Compatibility fallback for Blender builds that do not expose the
            # optional head argument in the Python API.
            kmi = km.keymap_items.new(definition['idname'], **keymap_args)
        for name, value in definition.get('properties', {}).items():
            setattr(kmi.properties, name, value)
        _ADDON_KEYMAPS.append((km, kmi))


def unregister():
    for km, kmi in reversed(_ADDON_KEYMAPS):
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _ADDON_KEYMAPS.clear()


def draw_preferences(layout, context):
    import rna_keymap_ui

    wm = context.window_manager
    kc = wm.keyconfigs.user if wm else None
    if kc is None:
        layout.label(text='User key configuration is unavailable.', icon='ERROR')
        return

    previous_group = None
    for definition in _DEFINITIONS:
        group = definition.get('group')
        if previous_group is not None and group != previous_group:
            layout.separator(factor=0.35)
        previous_group = group

        km = kc.keymaps.get(definition['keymap'])
        label = definition['label']
        if not km:
            layout.label(text=f'{label}: keymap unavailable', icon='ERROR')
            continue

        match = _find_matching_item(km, definition)
        if match:
            row = layout.row()
            row.label(text=label)
            rna_keymap_ui.draw_kmi([], kc, km, match, row, 0)
        else:
            layout.label(text=f'{label}: keymap entry missing', icon='ERROR')


def _find_matching_item(km, definition):
    expected = definition.get('properties', {})
    for kmi in km.keymap_items:
        if kmi.idname != definition['idname']:
            continue
        if all(getattr(kmi.properties, name, None) == value for name, value in expected.items()):
            return kmi
    return None


def _definition_key(definition) -> str:
    properties = definition.get('properties', {})
    prop_key = '|'.join(f'{name}={properties[name]!r}' for name in sorted(properties))
    return f"{definition['keymap']}|{definition['idname']}|{prop_key}"


def serialize_user_keymaps() -> list[dict]:
    """Return the current user-facing shortcuts for Modus keymaps."""
    wm = getattr(bpy.context, 'window_manager', None)
    kc = wm.keyconfigs.user if wm else None
    if kc is None:
        return []

    result = []
    for definition in _DEFINITIONS:
        km = kc.keymaps.get(definition['keymap'])
        if km is None:
            continue
        kmi = _find_matching_item(km, definition)
        if kmi is None:
            continue

        shortcut = {
            'key': _definition_key(definition),
            'keymap': definition['keymap'],
            'idname': definition['idname'],
            'properties': dict(definition.get('properties', {})),
        }
        for field in _SHORTCUT_FIELDS:
            if hasattr(kmi, field):
                value = getattr(kmi, field)
                if isinstance(value, (str, bool, int, float)):
                    shortcut[field] = value
        result.append(shortcut)
    return result



def reset_user_keymaps_to_defaults() -> int:
    """Restore Modus shortcuts to the definitions shipped with the add-on."""
    wm = getattr(bpy.context, 'window_manager', None)
    kc = wm.keyconfigs.user if wm else None
    if kc is None:
        return 0

    reset_count = 0
    for definition in _DEFINITIONS:
        km = kc.keymaps.get(definition['keymap'])
        if km is None:
            continue
        kmi = _find_matching_item(km, definition)
        if kmi is None:
            continue

        defaults = {
            'type': definition['type'],
            'value': definition.get('value', 'PRESS'),
            'any': definition.get('any', False),
            'shift': definition.get('shift', False),
            'ctrl': definition.get('ctrl', False),
            'alt': definition.get('alt', False),
            'oskey': definition.get('oskey', False),
            'key_modifier': definition.get('key_modifier', 'NONE'),
            'active': True,
            'repeat': definition.get('repeat', False),
        }
        for field, value in defaults.items():
            if not hasattr(kmi, field):
                continue
            try:
                setattr(kmi, field, value)
            except (AttributeError, TypeError, ValueError):
                pass
        reset_count += 1
    return reset_count

def apply_user_keymaps(saved_items) -> int:
    """Apply serialized Modus shortcuts to Blender's user key configuration."""
    if not isinstance(saved_items, list):
        return 0

    wm = getattr(bpy.context, 'window_manager', None)
    kc = wm.keyconfigs.user if wm else None
    if kc is None:
        return 0

    definitions_by_key = {_definition_key(definition): definition for definition in _DEFINITIONS}
    applied = 0
    for saved in saved_items:
        if not isinstance(saved, dict):
            continue
        definition = definitions_by_key.get(saved.get('key', ''))
        if definition is None:
            continue
        km = kc.keymaps.get(definition['keymap'])
        if km is None:
            continue
        kmi = _find_matching_item(km, definition)
        if kmi is None:
            continue

        changed = False
        for field in _SHORTCUT_FIELDS:
            if field not in saved or not hasattr(kmi, field):
                continue
            try:
                setattr(kmi, field, saved[field])
                changed = True
            except (AttributeError, TypeError, ValueError):
                continue
        if changed:
            applied += 1
    return applied
