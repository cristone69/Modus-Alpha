# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import bpy
from bpy.app.handlers import persistent

from . import model, operators
from .core import filter_engine, toolbar
from .ui import preferences as preferences_ui


@persistent
def _load_post(_unused):
    filter_engine.schedule_apply()


_CLASSES = model.CLASSES + operators.CLASSES


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    toolbar.install()
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)


def activate():
    filter_engine.schedule_apply()


def unregister():
    filter_engine.shutdown()
    toolbar.uninstall()
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
