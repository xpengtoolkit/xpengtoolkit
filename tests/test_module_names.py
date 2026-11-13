#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every name a module uses must actually be bound in it.

    python3 tests/test_module_names.py

Why this exists: the ported poll-status sensors shipped referencing
`poll_status.describe(...)` in a module that never imported `poll_status`. The
unit tests passed anyway - they stub the package's submodules, so the sensor
classes loaded and were simply never instantiated, leaving the NameError for a
real Home Assistant to hit on first entity write:

    NameError: name 'poll_status' is not defined

That is invisible to tests which only exercise one class, so this checks the
whole module's binding graph statically instead. It is cheap and catches a whole
family of porting mistakes (missing import, renamed helper, dropped alias).
"""

from __future__ import annotations

import ast
import builtins
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402

#: Modules worth checking: they must import cleanly on their own or are the
#: module that failed. Deliberately not every file - config_flow and friends
#: import Home Assistant, which is absent here - but every import name is still
#: checked, since that is a static property of the source.
MODULES = (
    # __init__.py is the one that actually broke twice: it is the module
    # Home Assistant calls first, so a missing import there takes the whole
    # integration down (NameError, setup aborted, every entity unavailable).
    # The check is static, so loading Home Assistant is not required.
    "__init__.py",
    "sensor.py",
    "binary_sensor.py",
    "switch.py",
    "button.py",
    "number.py",
    "select.py",
    "lock.py",
    "climate.py",
    "calendar.py",
    "image.py",
    "device_tracker.py",
    "coordinator.py",
    "mqtt_client.py",
    # Added with raw capture: capture.py is loaded by path in tests, so a missing
    # import there would only surface in a live Home Assistant.
    "capture.py",
    "can_journal.py",
    "poll_status.py",
    "watchdog_policy.py",
    # ABRP telemetry is also loaded by path in tests; a missing import there
    # would only surface as a failed push in a live Home Assistant.
    "abrp.py",
    "health.py",
    "xpeng_errors.py",
    "vin_descriptor.py",
)

#: Names supplied by Home Assistant at runtime through `__getattr__` tricks or
#: by the test harness; none should appear, so the list is empty on purpose.
ALLOWED_UNBOUND: set[str] = {
    # real module globals provided by the import system, not bound in source
    "__file__", "__name__", "__doc__", "__package__",
}


def _bound_names(tree: ast.AST) -> set[str]:
    """Every name a module binds, at any scope."""
    bound: set[str] = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
        elif isinstance(node, ast.Lambda):
            for a in node.args.args + node.args.kwonlyargs:
                bound.add(a.arg)
    return bound


def _loaded_names(tree: ast.AST) -> set[str]:
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


class TestNoUnboundNames(unittest.TestCase):
    def test_every_loaded_name_is_bound(self):
        problems: dict[str, list[str]] = {}
        for filename in MODULES:
            path = _component.component_dir() / filename
            if not path.is_file():
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            missing = sorted(
                name
                for name in _loaded_names(tree) - _bound_names(tree)
                if name not in ALLOWED_UNBOUND
            )
            if missing:
                problems[filename] = missing

        self.assertEqual(
            problems, {},
            "module(s) reference names they never bind - a NameError waiting for "
            f"a live instance: {problems}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
