# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import bpy
from bpy.props import CollectionProperty, PointerProperty, StringProperty


class MODUS_PG_isolate_object(bpy.types.PropertyGroup):
    obj: PointerProperty(type=bpy.types.Object)


class MODUS_PG_isolate_level(bpy.types.PropertyGroup):
    name: StringProperty()
    hidden_objects: CollectionProperty(type=MODUS_PG_isolate_object)


class MODUS_PG_isolate_stack(bpy.types.PropertyGroup):
    area_id: StringProperty()
    levels: CollectionProperty(type=MODUS_PG_isolate_level)


CLASSES = (
    MODUS_PG_isolate_object,
    MODUS_PG_isolate_level,
    MODUS_PG_isolate_stack,
)
