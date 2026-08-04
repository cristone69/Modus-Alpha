# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from . import keymaps
from .finalize_bevel import CLASSES as FINALIZE_BEVEL_CLASSES, clear_viewport_warning
from .operators import CLASSES as TOOL_CLASSES, register_selection_tracker, unregister_selection_tracker, MODUS_ModelingSettings

CLASSES = TOOL_CLASSES + FINALIZE_BEVEL_CLASSES
KEYMAP_DEFINITIONS = keymaps.KEYMAP_DEFINITIONS


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.modus_modeling = bpy.props.PointerProperty(type=MODUS_ModelingSettings)
    register_selection_tracker()


def unregister():
    clear_viewport_warning()
    unregister_selection_tracker()
    if hasattr(bpy.types.Scene, 'modus_modeling'):
        del bpy.types.Scene.modus_modeling
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
