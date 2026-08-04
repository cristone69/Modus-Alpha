# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations


def area_id(area):
    return str(area.as_pointer()) if area else ''


def find_stack(scene, area, *, create=False):
    key = area_id(area)
    if not key or not hasattr(scene, 'modus_isolate_stacks'):
        return None

    for stack in scene.modus_isolate_stacks:
        if stack.area_id == key:
            return stack

    if create:
        stack = scene.modus_isolate_stacks.add()
        stack.area_id = key
        return stack
    return None


def remove_stack(scene, area):
    key = area_id(area)
    for index, stack in enumerate(scene.modus_isolate_stacks):
        if stack.area_id == key:
            scene.modus_isolate_stacks.remove(index)
            return


def clear_stack(scene, area):
    stack = find_stack(scene, area)
    if stack:
        stack.levels.clear()
        remove_stack(scene, area)


def level_count(scene, area):
    stack = find_stack(scene, area)
    return len(stack.levels) if stack else 0
