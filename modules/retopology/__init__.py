# SPDX-License-Identifier: GPL-3.0-or-later
"""Automatic retopology and external quad-engine integration."""

from . import feature

def register():
    feature.register()

def unregister():
    feature.unregister()
