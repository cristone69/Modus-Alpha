# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from . import keymaps
from .operators import CLASSES, register_overlay, unregister_overlay

KEYMAP_DEFINITIONS = keymaps.KEYMAP_DEFINITIONS


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    register_overlay()


def unregister():
    unregister_overlay()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
