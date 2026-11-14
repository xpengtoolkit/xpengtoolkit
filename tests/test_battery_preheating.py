#!/usr/bin/env python3
"""Battery preheating: the status sensor and the two commands behind it.

Issue #31 asked for two things the integration did not have. The car reports
``battery_keep_warm_status`` in every service-4 acknowledgement - the same flat
charge block as ``charge_status`` and ``max_range_charge`` - and
``_merge_command_state`` already folds that block into ``instant.charge``, so the
status was being received and then thrown away by having no entity to read it.
``docs/COMMAND_RESEARCH.md`` has carried ``startBatteryWarm``/``stopBatteryWarm``
as service 4, ``cmd_type`` 5, value 1/0 all along, but only ``send_raw_mqtt``
could reach it.

What is pinned here, because each failure is silent:

* the description's ``value_path`` is the field the car actually reports, and
  ``on_value`` is 1 - a wrong path leaves the sensor permanently unknown, which
  looks exactly like a car that never preheats;
* both services send ``cmd_type 5`` with 1 and 0, not the ``cmd_type 1`` pair
  that starts and stops a charge session. The two are neighbours in the same
  service-4 family and an off-by-one there would stop a charge instead of the
  heater;
* the services are registered through the policy gate, declared in the YAML and
  removed on unload, and belong to the charging group.

Run from the repo root:

    python3 tests/test_battery_preheating.py
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import re
import sys
import unittest

PKG = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "xpeng"
CONST = (PKG / "const.py").read_text(encoding="utf-8")
SOURCE = (PKG / "services.py").read_text(encoding="utf-8")
YAML = (PKG / "services.yaml").read_text(encoding="utf-8")

FIELD = "battery_keep_warm_status"

#: Verbatim service-4 acknowledgement, measured 2026-11-03. It is what
#: ``record_command_feedback`` hands the coordinator and what
#: ``_merge_command_state`` folds into ``instant.charge``.
ACK = {
    "msg": "Erfolgreich",
    "code": 200,
    "charge_status": 15,
    "battery_keep_warm_status": 1,
    "slow_charge_port_status": 2,
    "fast_charge_port_status": 2,
    "max_range_charge": 90,
}


def _literal(node: ast.AST):
    """The keyword's value: literals as themselves, enums by their member name.

    ``value_fn`` keywords hold lambdas, which ``literal_eval`` refuses; those are
    not this test's business and come back as ``None``.
    """
    if isinstance(node, ast.Attribute):
        return node.attr
    try:
        return ast.literal_eval(node)
    except ValueError:
        return None


def _descriptions() -> dict[str, dict[str, object]]:
    """``BINARY_SENSOR_DESCRIPTIONS`` as ``{key: {keyword: value}}``.

    ``const.py`` imports Home Assistant, which is not installed for these tests,
    so the tuple is read from the source instead of imported.
    """
    for node in ast.walk(ast.parse(CONST)):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        if getattr(target, "id", None) != "BINARY_SENSOR_DESCRIPTIONS":
            continue
        found = {}
        for element in node.value.elts:
            keywords = {
                keyword.arg: _literal(keyword.value) for keyword in element.keywords
            }
            found[keywords["key"]] = keywords
        return found
    raise AssertionError("BINARY_SENSOR_DESCRIPTIONS is not defined in const.py")


def _handler(name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(ast.parse(SOURCE)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined in services.py")


def _send_call(name: str) -> ast.Call:
    for node in ast.walk(_handler(name)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_send_confirmed":
            return node
    raise AssertionError(f"{name} does not send a confirmed command")


def _yaml_block(name: str) -> str:
    match = re.search(rf"^{name}:\n(.*?)(?=^[a-z_]+:\n|\Z)", YAML, re.M | re.S)
    if not match:
        raise AssertionError(f"{name} is not in services.yaml")
    return match.group(1)


def _policy():
    """Load command_policy.py by path; it imports nothing from Home Assistant."""
    name = "xpeng_command_policy_battery_preheating"
    spec = importlib.util.spec_from_file_location(name, PKG / "command_policy.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class SensorTests(unittest.TestCase):
    def test_the_description_reads_the_field_the_car_reports(self):
        description = _descriptions()["battery_preheating"]
        self.assertEqual(description["translation_key"], "battery_preheating")
        self.assertEqual(
            description["value_path"],
            f"msg_content.instant.charge.{FIELD}",
        )

    def test_the_field_is_one_the_acknowledgement_carries(self):
        """The path must name a key the car actually sends, not a guess."""
        description = _descriptions()["battery_preheating"]
        leaf = str(description["value_path"]).rsplit(".", 1)[-1]
        self.assertIn(leaf, ACK)
        self.assertIsInstance(ACK[leaf], int)

    def test_one_means_preheating(self):
        """1 is on. A truthy default would read the 0 case as off too, but it
        would also read a future value of 2 as on, so the value is pinned."""
        description = _descriptions()["battery_preheating"]
        self.assertEqual(description["on_value"], 1)
        self.assertIsNot(description["on_value"], True)

    def test_it_is_a_running_sensor_not_a_charging_one(self):
        """The battery is not charging because it is being warmed; the car can
        preheat with the pack full, and ``charging`` already reports that."""
        description = _descriptions()["battery_preheating"]
        self.assertEqual(description["device_class"], "RUNNING")


class ServiceTests(unittest.TestCase):
    def _payload(self, name: str) -> tuple[int, dict]:
        call = _send_call(name)
        service_type = ast.literal_eval(call.args[1])
        payload = call.args[2]
        self.assertIsInstance(payload, ast.Dict)
        return service_type, {
            ast.literal_eval(key): ast.literal_eval(value)
            for key, value in zip(payload.keys, payload.values)
        }

    def test_start_sends_cmd_type_five_value_one(self):
        """``startBatteryWarm``. ``cmd_type 1`` is start charge - not this."""
        service_type, payload = self._payload("handle_start_battery_preheating")
        self.assertEqual(service_type, 4)
        self.assertEqual(payload, {"cmd_type": 5, "cmd_value": 1})

    def test_stop_sends_cmd_type_five_value_zero(self):
        service_type, payload = self._payload("handle_stop_battery_preheating")
        self.assertEqual(service_type, 4)
        self.assertEqual(payload, {"cmd_type": 5, "cmd_value": 0})

    def test_each_service_targets_the_vehicle_the_caller_aimed_at(self):
        for name in (
            "handle_start_battery_preheating",
            "handle_stop_battery_preheating",
        ):
            with self.subTest(handler=name):
                source = ast.get_source_segment(SOURCE, _handler(name)) or ""
                self.assertIn("_entry_for_call", source)

    def test_both_are_registered_and_removed(self):
        for constant, handler in (
            ("SERVICE_START_BATTERY_PREHEATING", "handle_start_battery_preheating"),
            ("SERVICE_STOP_BATTERY_PREHEATING", "handle_stop_battery_preheating"),
        ):
            with self.subTest(service=constant):
                self.assertIn(f"{constant} = ", SOURCE)
                self.assertIn(f"_register({constant}, {handler})", SOURCE)
                unload = SOURCE.split("async def async_unload_services")[1]
                self.assertIn(f"{constant},", unload)

    def test_their_policy_group_is_charging(self):
        policy = _policy()
        for name in ("start_battery_preheating", "stop_battery_preheating"):
            with self.subTest(service=name):
                self.assertEqual(
                    policy.service_group(name), policy.GROUP_CHARGING
                )

    def test_the_form_targets_a_vehicle(self):
        for name in ("start_battery_preheating", "stop_battery_preheating"):
            with self.subTest(service=name):
                block = _yaml_block(name)
                self.assertIn("vehicle_entity_id:", block)
                self.assertIn("integration: xpeng", block)


class NamingTests(unittest.TestCase):
    def test_every_language_names_it(self):
        """A missing key falls back to English silently, so parity is checked."""
        for path in sorted((PKG / "translations").glob("*.json")):
            with self.subTest(language=path.stem):
                entity = json.loads(path.read_text(encoding="utf-8"))["entity"]
                self.assertIn("battery_preheating", entity["binary_sensor"])

    def test_the_english_name_feeds_the_entity_id(self):
        """Entity ids are derived from this literal, so it must be there."""
        names = (PKG / "entity_names.py").read_text(encoding="utf-8")
        self.assertIn('"battery_preheating": "Battery preheating"', names)

    def test_strings_json_carries_the_same_key(self):
        for filename in ("strings.json", "translations/en.json"):
            with self.subTest(file=filename):
                entity = json.loads(
                    (PKG / filename).read_text(encoding="utf-8")
                )["entity"]
                self.assertEqual(
                    entity["binary_sensor"]["battery_preheating"]["name"],
                    "Battery preheating",
                )


if __name__ == "__main__":
    unittest.main()
