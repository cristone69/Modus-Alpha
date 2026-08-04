# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from .operators import MODUS_OT_loop_rotate
from .keymaps import KEYMAP_DEFINITIONS
_CLASSES = (MODUS_OT_loop_rotate,)
def register():
    for cls in _CLASSES: bpy.utils.register_class(cls)
def unregister():
    for cls in reversed(_CLASSES): bpy.utils.unregister_class(cls)
