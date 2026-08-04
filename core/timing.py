# SPDX-License-Identifier: GPL-3.0-or-later
"""Automatic execution timing for Modus operators."""

from __future__ import annotations

import functools
import inspect
import time
from types import ModuleType

import bpy

_INSTRUMENTED = set()


def _label(operator):
    return getattr(operator, 'bl_label', None) or getattr(operator, 'bl_idname', operator.__class__.__name__)


def _log(operator, elapsed):
    message = f"[Modus Timing] {_label(operator)}: {elapsed:.4f}s"
    print(message)


def _wrap_execute(cls):
    original = cls.__dict__.get('execute')
    if original is None or getattr(original, '_modus_timed', False):
        return

    @functools.wraps(original)
    def timed_execute(self, context):
        started = time.perf_counter()
        try:
            return original(self, context)
        finally:
            _log(self, time.perf_counter() - started)

    timed_execute._modus_timed = True
    cls.execute = timed_execute


def _wrap_modal(cls):
    original_invoke = cls.__dict__.get('invoke')
    original_modal = cls.__dict__.get('modal')
    if original_invoke is None or original_modal is None or getattr(original_modal, '_modus_timed', False):
        return

    @functools.wraps(original_invoke)
    def timed_invoke(self, context, event):
        self._modus_timing_started = time.perf_counter()
        result = original_invoke(self, context, event)
        if 'RUNNING_MODAL' not in result:
            _log(self, time.perf_counter() - self._modus_timing_started)
            self._modus_timing_started = None
        return result

    @functools.wraps(original_modal)
    def timed_modal(self, context, event):
        result = original_modal(self, context, event)
        if ('FINISHED' in result or 'CANCELLED' in result) and getattr(self, '_modus_timing_started', None) is not None:
            _log(self, time.perf_counter() - self._modus_timing_started)
            self._modus_timing_started = None
        return result

    timed_invoke._modus_timed = True
    timed_modal._modus_timed = True
    cls.invoke = timed_invoke
    cls.modal = timed_modal


def instrument_operator_class(cls):
    if cls in _INSTRUMENTED or not inspect.isclass(cls) or not issubclass(cls, bpy.types.Operator):
        return
    _INSTRUMENTED.add(cls)
    if 'modal' in cls.__dict__:
        _wrap_modal(cls)
    else:
        _wrap_execute(cls)


def instrument_modules(*roots):
    """Instrument every Modus Operator class reachable from imported modules."""
    visited = set()
    prefixes = {
        module.__name__.rsplit('.', 1)[0]
        for module in roots
        if isinstance(module, ModuleType) and '.' in module.__name__
    }

    def visit(module):
        if not isinstance(module, ModuleType) or module in visited:
            return
        module_name = getattr(module, '__name__', '')
        if not any(module_name == prefix or module_name.startswith(prefix + '.') for prefix in prefixes):
            return
        visited.add(module)
        for value in vars(module).values():
            if inspect.isclass(value):
                try:
                    instrument_operator_class(value)
                except TypeError:
                    pass
            elif isinstance(value, ModuleType):
                visit(value)

    for root in roots:
        visit(root)
