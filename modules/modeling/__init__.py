# SPDX-License-Identifier: GPL-3.0-or-later
"""Core mesh-editing features grouped under one registration domain."""

from . import assign_weights, clean_up, force_loop, isolate, loop_rotate, merge, modifier_tools, origin_to_selected, primitives, symmetrize

_FEATURES = (merge, clean_up, origin_to_selected, modifier_tools, assign_weights, isolate, loop_rotate, force_loop, symmetrize, primitives)
KEYMAP_DEFINITIONS = tuple(
    definition
    for feature in _FEATURES
    for definition in getattr(feature, "KEYMAP_DEFINITIONS", ())
)


def register():
    for feature in _FEATURES:
        feature.register()


def unregister():
    for feature in reversed(_FEATURES):
        feature.unregister()
