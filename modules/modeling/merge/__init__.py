# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from . import keymaps, operators

KEYMAP_DEFINITIONS = keymaps.KEYMAP_DEFINITIONS


def register():
    for cls in operators.CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(operators.CLASSES):
        bpy.utils.unregister_class(cls)
