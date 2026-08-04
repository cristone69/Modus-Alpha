# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from .keymaps import KEYMAP_DEFINITIONS
from .operators import MODUS_OT_symmetrize

_CLASSES = (MODUS_OT_symmetrize,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
