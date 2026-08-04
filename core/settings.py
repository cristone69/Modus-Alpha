# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import bpy
from bpy.props import StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper, ImportHelper

ADDON_ID = __package__.partition('.core')[0]
SCHEMA_VERSION = 3
ADDON_VERSION = '1.1.10'
_CONFIG_DIRECTORY_NAME = 'modus'
_CONFIG_FILE_NAME = 'settings.json'

_PREFERENCE_DEFAULTS = {
    'show_tool_header_controls': True,
    'npanel_feature_enabled': True,
    'npanel_filtering_enabled': False,
    'npanel_hide_uncategorized': True,
    'uv_grid_resolution': '4096',
    'uv_grid_style': 'UV_GRID',
    'symmetrize_center_tris_to_quads': False,
    'symmetrize_context_scope': True,
    'symmetrize_default_scope': 'ALL',
    'topology_tri_color': [0.08, 0.80, 0.18, 0.40],
    'topology_ngon_color': [0.015, 0.055, 0.22, 0.68],
}
_PREFERENCE_FIELDS = tuple(_PREFERENCE_DEFAULTS)

_loading = False
_save_pending = False
_deleted_since_last_change = False
_last_keymap_signature = ''
_watcher_running = False


def config_directory(create: bool = False) -> Path:
    path = bpy.utils.user_resource(
        'CONFIG',
        path=_CONFIG_DIRECTORY_NAME,
        create=create,
    )
    return Path(path)


def config_file_path(create_directory: bool = False) -> Path:
    return config_directory(create=create_directory) / _CONFIG_FILE_NAME


def config_exists() -> bool:
    try:
        return config_file_path().is_file()
    except (OSError, RuntimeError):
        return False


def get_preferences(context=None):
    context = context or bpy.context
    preferences = getattr(context, 'preferences', None)
    if preferences is None:
        return None
    addon = preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


def is_loading() -> bool:
    return _loading


def _json_safe(value: Any):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def snapshot(prefs=None) -> dict:
    prefs = prefs or get_preferences()
    if prefs is None:
        raise RuntimeError('Modus preferences are unavailable')

    from . import keymaps

    categories = []
    for category in prefs.npanel_categories:
        categories.append({
            'name': category.name,
            'icon': category.icon,
            'enabled': bool(category.enabled),
            'expanded': bool(category.expanded),
            'active_tab_index': int(category.active_tab_index),
            'tabs': [tab.name for tab in category.tabs if tab.name],
        })

    return {
        'schema_version': SCHEMA_VERSION,
        'addon_version': ADDON_VERSION,
        'preferences': {
            field: _json_safe(getattr(prefs, field))
            for field in _PREFERENCE_FIELDS
        },
        'npanel_active_category_index': int(prefs.npanel_active_category_index),
        'npanel_categories': categories,
        'keymaps': keymaps.serialize_user_keymaps(),
    }


def default_data() -> dict:
    return {
        'schema_version': SCHEMA_VERSION,
        'addon_version': ADDON_VERSION,
        'preferences': dict(_PREFERENCE_DEFAULTS),
        'npanel_active_category_index': 0,
        'npanel_categories': [],
        'keymaps': [],
    }


def _normalize(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ValueError('Settings file must contain a JSON object')

    schema_version = data.get('schema_version', 1)
    if not isinstance(schema_version, int):
        raise ValueError('Invalid settings schema version')
    if schema_version > SCHEMA_VERSION:
        raise ValueError(
            f'Settings schema {schema_version} is newer than supported schema {SCHEMA_VERSION}'
        )

    normalized = default_data()
    source_preferences = data.get('preferences', {})
    if isinstance(source_preferences, dict):
        for field, default in _PREFERENCE_DEFAULTS.items():
            value = source_preferences.get(field, default)
            if isinstance(default, bool):
                normalized['preferences'][field] = bool(value)
            elif isinstance(default, list):
                if isinstance(value, (list, tuple)) and len(value) == len(default):
                    try:
                        normalized['preferences'][field] = [
                            max(0.0, min(1.0, float(component)))
                            for component in value
                        ]
                    except (TypeError, ValueError):
                        normalized['preferences'][field] = list(default)
                else:
                    normalized['preferences'][field] = list(default)
            else:
                normalized['preferences'][field] = str(value)

    active_category_index = data.get('npanel_active_category_index', 0)
    if isinstance(active_category_index, int):
        normalized['npanel_active_category_index'] = max(0, active_category_index)

    categories = data.get('npanel_categories', [])
    if not isinstance(categories, list):
        raise ValueError('N-Panel categories must be a list')

    normalized_categories = []
    for item in categories:
        if not isinstance(item, dict):
            continue
        name = item.get('name', 'Category')
        icon = item.get('icon', 'BOOKMARKS')
        tabs = item.get('tabs', [])
        if not isinstance(tabs, list):
            tabs = []
        clean_tabs = []
        seen_tabs = set()
        for tab in tabs:
            if not isinstance(tab, str) or not tab or tab in seen_tabs:
                continue
            seen_tabs.add(tab)
            clean_tabs.append(tab)
        normalized_categories.append({
            'name': str(name) if name else 'Category',
            'icon': str(icon) if icon else 'BOOKMARKS',
            'enabled': bool(item.get('enabled', True)),
            'expanded': bool(item.get('expanded', False)),
            'active_tab_index': max(0, int(item.get('active_tab_index', 0)))
            if isinstance(item.get('active_tab_index', 0), int) else 0,
            'tabs': clean_tabs,
        })
    normalized['npanel_categories'] = normalized_categories

    keymaps = data.get('keymaps', [])
    normalized['keymaps'] = keymaps if isinstance(keymaps, list) else []
    return normalized


def _read_json(filepath: Path) -> dict:
    with filepath.open('r', encoding='utf-8') as handle:
        return _normalize(json.load(handle))


def _write_json(filepath: Path, data: dict) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    temporary = filepath.with_name(f'.{filepath.name}.tmp')
    try:
        with temporary.open('w', encoding='utf-8', newline='\n') as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False, sort_keys=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, filepath)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def apply_data(data: dict, prefs=None, apply_keymaps: bool = True) -> None:
    global _loading, _last_keymap_signature

    prefs = prefs or get_preferences()
    if prefs is None:
        raise RuntimeError('Modus preferences are unavailable')
    normalized = _normalize(data)

    _loading = True
    try:
        for field, value in normalized['preferences'].items():
            setattr(prefs, field, value)

        try:
            from ..modules.n_panel.model import all_icon_identifiers
            valid_icons = set(all_icon_identifiers())
        except (ImportError, AttributeError, RuntimeError):
            valid_icons = {'BOOKMARKS'}

        prefs.npanel_categories.clear()
        for category_data in normalized['npanel_categories']:
            category = prefs.npanel_categories.add()
            category.name = category_data['name']
            icon = category_data['icon']
            category.icon = icon if icon in valid_icons else 'BOOKMARKS'
            category.enabled = category_data['enabled']
            category.expanded = category_data['expanded']
            category.active_tab_index = category_data['active_tab_index']
            for tab_name in category_data['tabs']:
                category.tabs.add().name = tab_name

        prefs.npanel_active_category_index = min(
            normalized['npanel_active_category_index'],
            max(0, len(prefs.npanel_categories) - 1),
        )

        if apply_keymaps:
            from . import keymaps
            keymaps.apply_user_keymaps(normalized['keymaps'])
            _last_keymap_signature = _keymap_signature()
    finally:
        _loading = False

    try:
        from ..modules.n_panel.core import filter_engine
        filter_engine.schedule_apply()
    except (ImportError, AttributeError, RuntimeError):
        pass

    try:
        from . import redraw
        redraw.tag_view3d_redraw()
    except (ImportError, AttributeError, RuntimeError):
        pass


def save_now(filepath: Path | None = None, prefs=None) -> Path:
    global _save_pending, _deleted_since_last_change, _last_keymap_signature

    target = filepath or config_file_path(create_directory=True)
    data = snapshot(prefs)
    _write_json(target, data)
    if filepath is None or target == config_file_path():
        _save_pending = False
        _deleted_since_last_change = False
        _last_keymap_signature = _keymap_signature()
    return target


def load_from_file(filepath: Path, prefs=None, save_as_canonical: bool = False) -> dict:
    data = _read_json(filepath)
    apply_data(data, prefs=prefs, apply_keymaps=True)
    if save_as_canonical:
        save_now(prefs=prefs)
    return data


def load_canonical(prefs=None) -> bool:
    path = config_file_path()
    if not path.is_file():
        return False
    load_from_file(path, prefs=prefs, save_as_canonical=False)
    return True


def reset_to_defaults(prefs=None) -> None:
    global _loading, _last_keymap_signature

    apply_data(default_data(), prefs=prefs, apply_keymaps=False)
    from . import keymaps
    _loading = True
    try:
        keymaps.reset_user_keymaps_to_defaults()
    finally:
        _loading = False
    _last_keymap_signature = _keymap_signature()
    save_now(prefs=prefs)


def delete_saved_configuration() -> bool:
    global _save_pending, _deleted_since_last_change

    _cancel_save_timer()
    path = config_file_path()
    removed = False
    try:
        if path.exists():
            path.unlink()
            removed = True
    except OSError:
        raise
    _save_pending = False
    _deleted_since_last_change = True
    return removed


def _timer_save():
    global _save_pending
    _save_pending = False
    try:
        save_now()
    except Exception as exc:  # Blender should remain usable if disk I/O fails.
        print(f'[Modus] Could not save settings: {exc}')
    return None


def schedule_save(delay: float = 0.25) -> None:
    global _save_pending, _deleted_since_last_change

    if _loading:
        return
    _deleted_since_last_change = False
    if _save_pending:
        return
    _save_pending = True
    try:
        bpy.app.timers.register(_timer_save, first_interval=delay)
    except ValueError:
        _save_pending = False


def _cancel_save_timer() -> None:
    global _save_pending
    try:
        if bpy.app.timers.is_registered(_timer_save):
            bpy.app.timers.unregister(_timer_save)
    except (AttributeError, RuntimeError, ValueError):
        pass
    _save_pending = False


def _keymap_signature() -> str:
    from . import keymaps
    try:
        return json.dumps(keymaps.serialize_user_keymaps(), sort_keys=True, separators=(',', ':'))
    except Exception:
        return ''


def _watch_keymaps():
    global _last_keymap_signature
    signature = _keymap_signature()
    if signature and signature != _last_keymap_signature:
        _last_keymap_signature = signature
        schedule_save()
    return 1.0


def _start_keymap_watcher() -> None:
    global _watcher_running
    if _watcher_running:
        return
    try:
        bpy.app.timers.register(_watch_keymaps, first_interval=1.0, persistent=True)
        _watcher_running = True
    except TypeError:
        bpy.app.timers.register(_watch_keymaps, first_interval=1.0)
        _watcher_running = True
    except ValueError:
        _watcher_running = False


def _stop_keymap_watcher() -> None:
    global _watcher_running
    try:
        if bpy.app.timers.is_registered(_watch_keymaps):
            bpy.app.timers.unregister(_watch_keymaps)
    except (AttributeError, RuntimeError, ValueError):
        pass
    _watcher_running = False



def _quarantine_invalid_configuration(path: Path) -> Path | None:
    if not path.exists():
        return None
    candidate = path.with_name(f'{path.stem}.invalid{path.suffix}')
    number = 2
    while candidate.exists():
        candidate = path.with_name(f'{path.stem}.invalid-{number}{path.suffix}')
        number += 1
    try:
        path.replace(candidate)
        return candidate
    except OSError:
        return None

def activate() -> None:
    global _last_keymap_signature
    prefs = get_preferences()
    if prefs is None:
        return

    try:
        loaded = load_canonical(prefs=prefs)
    except Exception as exc:
        loaded = False
        invalid_path = _quarantine_invalid_configuration(config_file_path())
        if invalid_path is not None:
            print(f'[Modus] Invalid settings moved to: {invalid_path}')
        print(f'[Modus] Could not load settings: {exc}')

    if not loaded:
        try:
            save_now(prefs=prefs)
        except Exception as exc:
            print(f'[Modus] Could not initialize settings: {exc}')

    _last_keymap_signature = _keymap_signature()
    _start_keymap_watcher()


def prepare_unregister() -> None:
    """Flush pending changes before Blender removes preferences and keymaps."""
    _stop_keymap_watcher()
    if _deleted_since_last_change:
        _cancel_save_timer()
        return
    try:
        save_now()
    except Exception as exc:
        print(f'[Modus] Could not save settings during unregister: {exc}')
    _cancel_save_timer()


def shutdown() -> None:
    _stop_keymap_watcher()
    _cancel_save_timer()


class MODUS_OT_settings_export(Operator, ExportHelper):
    bl_idname = 'modus.settings_export'
    bl_label = 'Export Modus Settings'
    bl_description = 'Export Modus preferences, N-Panel categories, and shortcuts'

    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = 'modus_settings.json'
        return super().invoke(context, event)

    def execute(self, context):
        try:
            path = save_now(Path(self.filepath), prefs=get_preferences(context))
        except Exception as exc:
            self.report({'ERROR'}, f'Could not export settings: {exc}')
            return {'CANCELLED'}
        self.report({'INFO'}, f'Exported Modus settings to {path}')
        return {'FINISHED'}


class MODUS_OT_settings_import(Operator, ImportHelper):
    bl_idname = 'modus.settings_import'
    bl_label = 'Import Modus Settings'
    bl_description = 'Import settings and save them as the active Modus configuration'

    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        try:
            load_from_file(
                Path(self.filepath),
                prefs=get_preferences(context),
                save_as_canonical=True,
            )
        except Exception as exc:
            self.report({'ERROR'}, f'Could not import settings: {exc}')
            return {'CANCELLED'}
        self.report({'INFO'}, 'Imported and activated Modus settings')
        return {'FINISHED'}


class MODUS_OT_settings_reset(Operator):
    bl_idname = 'modus.settings_reset'
    bl_label = 'Reset Modus Settings'
    bl_description = 'Reset preferences, N-Panel categories, and saved configuration to defaults'

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, context):
        try:
            reset_to_defaults(prefs=get_preferences(context))
        except Exception as exc:
            self.report({'ERROR'}, f'Could not reset settings: {exc}')
            return {'CANCELLED'}
        self.report({'INFO'}, 'Modus settings reset to defaults')
        return {'FINISHED'}


class MODUS_OT_settings_delete(Operator):
    bl_idname = 'modus.settings_delete'
    bl_label = 'Delete Saved Configuration'
    bl_description = (
        'Delete the persistent settings file; current settings remain active until changed or reloaded'
    )

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, _context):
        try:
            removed = delete_saved_configuration()
        except Exception as exc:
            self.report({'ERROR'}, f'Could not delete saved configuration: {exc}')
            return {'CANCELLED'}
        if removed:
            self.report({'INFO'}, 'Deleted the saved Modus configuration')
        else:
            self.report({'INFO'}, 'No saved Modus configuration was found')
        return {'FINISHED'}


CLASSES = (
    MODUS_OT_settings_export,
    MODUS_OT_settings_import,
    MODUS_OT_settings_reset,
    MODUS_OT_settings_delete,
)
