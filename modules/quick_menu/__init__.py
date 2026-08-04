# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from . import keymaps
from .menu import CLASSES

KEYMAP_DEFINITIONS = keymaps.KEYMAP_DEFINITIONS


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
