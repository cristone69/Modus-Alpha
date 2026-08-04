# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from . import header, keymaps, redraw, settings, timing


def register():
    for cls in settings.CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    settings.shutdown()
    for cls in reversed(settings.CLASSES):
        bpy.utils.unregister_class(cls)
