# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.app.handlers import persistent
from bpy.props import CollectionProperty

from . import keymaps, operators, overlay, properties

KEYMAP_DEFINITIONS = keymaps.KEYMAP_DEFINITIONS


@persistent
def _clear_isolate_history_on_load(_dummy):
    # Area pointer IDs are session-only. Never reuse a stack saved in a .blend.
    for scene in bpy.data.scenes:
        stacks = getattr(scene, "modus_isolate_stacks", None)
        if stacks is not None:
            stacks.clear()


def register():
    for cls in properties.CLASSES:
        bpy.utils.register_class(cls)
    for cls in operators.CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.modus_isolate_stacks = CollectionProperty(
        type=properties.MODUS_PG_isolate_stack
    )
    overlay.register()
    if _clear_isolate_history_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_clear_isolate_history_on_load)


def unregister():
    overlay.unregister()
    if _clear_isolate_history_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_clear_isolate_history_on_load)
    if hasattr(bpy.types.Scene, 'modus_isolate_stacks'):
        del bpy.types.Scene.modus_isolate_stacks
    for cls in reversed(operators.CLASSES):
        bpy.utils.unregister_class(cls)
    for cls in reversed(properties.CLASSES):
        bpy.utils.unregister_class(cls)
