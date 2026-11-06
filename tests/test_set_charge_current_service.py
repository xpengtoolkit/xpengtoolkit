#!/usr/bin/env python3
"""The ampere limit as a *service*, for scripts that drive more than one car.

0.6.39 exposed the AC charge current limit as a number entity. A script aiming a
call at a particular vehicle still had to go through ``number.set_value`` on that
vehicle's entity, which is awkward when the target is computed (a template, a
room, a loop over entries). This service takes the same optional
``vehicle_entity_id`` every other vehicle command takes.

What it must stay identical to is the entity: service 4, ``cmd_type 7``,
``cmd_value`` as the 1..27 index for 6..32 A - not ``cmd_type 2``, the percentage charge
limit and the neighbouring service's job.

Run from the repo root:

    python3 tests/test_set_charge_current_service.py
"""

from __future__ import annotations

import __future__
import ast
import importlib.util
import pathlib
import re
import sys
import unittest

PKG = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "xpeng"
SOURCE = (PKG / "services.py").read_text(encoding="utf-8")
YAML = (PKG / "services.yaml").read_text(encoding="utf-8")


def _policy():
    """Load command_policy.py by path; it imports nothing from Home Assistant."""
    name = "xpeng_command_policy_set_charge_current"
    spec = importlib.util.spec_from_file_location(name, PKG / "command_policy.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _handler() -> ast.AsyncFunctionDef:
    for node in ast.walk(ast.parse(SOURCE)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_set_charge_current":
            return node
    raise AssertionError("handle_set_charge_current is not defined in services.py")


def _call_named(root: ast.AST, name: str) -> ast.Call:
    for node in ast.walk(root):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == name:
            return node
    raise AssertionError(f"no call to {name}")


def _yaml_block(name: str) -> str:
    """The text of one top-level service entry, up to the next one."""
    match = re.search(
        rf"^{name}:\n(.*?)(?=^[a-z_]+:\n|\Z)", YAML, re.M | re.S
    )
    if not match:
        raise AssertionError(f"{name} is not in services.yaml")
    return match.group(1)


class ServiceContractTests(unittest.TestCase):
    def test_the_service_is_the_command_the_entity_sends(self):
        """Amperes on cmd_type 7 - not cmd_type 2, which is the percentage."""
        call = _call_named(_handler(), "_send_confirmed")
        self.assertEqual(ast.literal_eval(call.args[1]), 4)
        payload = call.args[2]
        self.assertIsInstance(payload, ast.Dict)
        self.assertEqual([ast.literal_eval(k) for k in payload.keys],
                         ["cmd_type", "cmd_value"])
        self.assertEqual(ast.literal_eval(payload.values[0]), 7)
        self.assertNotEqual(ast.literal_eval(payload.values[0]), 2)
        value = ast.get_source_segment(SOURCE, payload.values[1]) or ""
        self.assertIn('call.data["current"]', value)

    def test_the_value_comes_from_the_current_field(self):
        """``current`` (amperes) is the field, not ``limit`` (percent)."""
        source = ast.get_source_segment(SOURCE, _handler()) or ""
        self.assertIn('call.data["current"]', source)
        self.assertNotIn('call.data["limit"]', source)

    def test_it_resolves_the_entry_the_caller_aimed_at(self):
        """Multi-car is the reason the service exists: no hardcoded entry."""
        _call_named(_handler(), "_entry_for_call")

    def test_the_schema_is_range_checked(self):
        block = re.search(
            r"SET_CHARGE_CURRENT_SCHEMA = vol\.Schema\((.*?)\n\)\n", SOURCE, re.S
        ).group(1)
        self.assertIn('"current"', block)
        self.assertIn("min=6", block)
        self.assertIn("max=32", block)
        self.assertNotIn('"limit"', block)

    def test_it_is_registered_through_the_policy_gate_and_removed_on_unload(self):
        self.assertIn('SERVICE_SET_CHARGE_CURRENT = "set_charge_current"', SOURCE)
        self.assertIn(
            "_register(SERVICE_SET_CHARGE_CURRENT, handle_set_charge_current, "
            "SET_CHARGE_CURRENT_SCHEMA)",
            SOURCE,
        )
        unload = SOURCE.split("async def async_unload_services")[1]
        self.assertIn("SERVICE_SET_CHARGE_CURRENT,", unload)

    def test_its_policy_group_is_charging(self):
        policy = _policy()
        self.assertEqual(policy.service_group("set_charge_current"), policy.GROUP_CHARGING)


class ServiceFormTests(unittest.TestCase):
    def test_the_form_offers_amperes_and_the_vehicle(self):
        block = _yaml_block("set_charge_current")
        self.assertIn("vehicle_entity_id:", block)
        self.assertIn("current:", block)
        self.assertIn("min: 6", block)
        self.assertIn("max: 32", block)
        self.assertIn('unit_of_measurement: A', block)
        # The neighbouring percentage service keeps its own bounds.
        self.assertNotIn("limit:", block)

    def test_the_description_warns_about_silent_ignores(self):
        """A 200 does not mean applied; the docs for the field must say so."""
        block = _yaml_block("set_charge_current").lower()
        self.assertIn("acknowledges", block)
        self.assertIn("confirm", block)


class ServiceEncodingTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_converts_amperes_and_preserves_vehicle_targeting(self):
        spec = importlib.util.spec_from_file_location("service_current_encoding", PKG / "charge_current.py")
        encoding = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(encoding)
        sent = []
        target = object()

        async def send(hass, subsystem, content, entry):
            sent.append((subsystem, content, entry))

        ns = {"_entry_for_call": lambda hass, call: target,
              "_send_confirmed": send, "hass": object(),
              "current_limit_index": encoding.current_limit_index}
        exec(compile(ast.Module(body=[_handler()], type_ignores=[]), "current-service", "exec",
                     flags=__future__.annotations.compiler_flag), ns)
        from types import SimpleNamespace
        for amperes, raw in ((6, 1), (16, 11), (20, 15), (27, 22), (32, 27)):
            await ns["handle_set_charge_current"](SimpleNamespace(data={"current": amperes}))
            self.assertEqual(sent[-1], (4, {"cmd_type": 7, "cmd_value": raw}, target))


if __name__ == "__main__":
    unittest.main(verbosity=2)
