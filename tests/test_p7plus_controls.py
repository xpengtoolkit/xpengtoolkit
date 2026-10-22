#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Window and charge-port controls, after the P7+ measurements.

    python3 tests/test_p7plus_controls.py
    python3 -m unittest discover -s tests -v

Why this exists. Measured on a P7+ (2026-10-22, reported in the DrazorV fork):
the window buttons and services were publishing the *charge-port* commands.
``close_windows`` sent service_type 4 / cmd_type 3 / cmd_value 1, byte-identical
to ``open_charge_port``; ``vent_windows`` sent the close-charge-port payload.
Neither moved a window. The only window command observed to do anything is
service_type 12 / cmd_type 1 / cmd_value 2, and it *vents*.

So this pins three things:
  * the verified window command is the one sent, for both the buttons and the
    ``xpeng.vent_windows`` service;
  * window closing uses the recovered official app command, not a charge-port command;
  * a car with a manual flap can turn the charge-port controls off, and the
    buttons, the services and the strings all honour that one answer.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402

COMPONENT = _component.component_dir()
REPO = COMPONENT.parent.parent
_spec = importlib.util.spec_from_file_location(
    "charge_port_under_test", _component.module_path("charge_port.py")
)
assert _spec and _spec.loader
charge_port = importlib.util.module_from_spec(_spec)
sys.modules["charge_port_under_test"] = charge_port
_spec.loader.exec_module(charge_port)

BUTTON = (COMPONENT / "button.py").read_text(encoding="utf-8")
SERVICES = (COMPONENT / "services.py").read_text(encoding="utf-8")
CONST = (COMPONENT / "const.py").read_text(encoding="utf-8")
FLOW = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")


def _section(source: str, start: str, end: str) -> str:
    """The text between two markers; fails the test rather than returning junk."""
    first = source.index(start)
    return source[first:source.index(end, first)]


class WindowCommands(unittest.TestCase):
    """The verified window command, and no guessed closing command."""

    def test_the_vent_button_sends_the_verified_command(self):
        block = _section(BUTTON, 'key="vent_windows"', "XPengCommandButton(")
        self.assertIn("service_type=12", block)
        self.assertIn('{"cmd_type": 1, "cmd_value": 2}', block)
        self.assertNotIn("service_type=4", block)

    def test_the_vent_service_sends_the_verified_command(self):
        block = _section(SERVICES, "handle_vent_windows", "handle_sentinel_on")
        self.assertIn("_send, hass, 12, {\"cmd_type\": 1, \"cmd_value\": 2}", block)
        self.assertNotIn("cmd_type\": 3", block)

    def test_window_close_uses_the_official_handler_not_the_charge_port(self):
        block = _section(BUTTON, 'key="close_windows"', "XPengCommandButton(")
        self.assertIn("service_type=12", block)
        self.assertIn('{"cmd_type": 1, "cmd_value": 1}', block)
        self.assertNotIn("service_type=4", block)
        block = _section(SERVICES, "handle_close_windows", "handle_vent_windows")
        self.assertIn('_send, hass, 12, {"cmd_type": 1, "cmd_value": 1}', block)
        self.assertNotIn('"cmd_type": 3', block)

    def test_open_windows_is_unchanged(self):
        """The one command that already worked; it is what vent now reuses."""
        block = _section(BUTTON, 'key="open_windows"', "]\n")
        self.assertIn("service_type=12", block)
        self.assertIn('{"cmd_type": 1, "cmd_value": 2}', block)


class ChargePortControlsOption(unittest.TestCase):
    """One owner-supplied answer, honoured by entities, services and strings."""

    def test_default_is_on_and_only_an_explicit_off_turns_it_off(self):
        self.assertTrue(charge_port.controls_enabled(None))
        self.assertTrue(charge_port.controls_enabled({}))
        self.assertTrue(charge_port.controls_enabled({"charge_port_controls": True}))
        self.assertTrue(charge_port.controls_enabled({"charge_port_controls": "yes"}))
        self.assertFalse(charge_port.controls_enabled({"charge_port_controls": False}))
        for off in ("0", "false", "no", "off", ""):
            with self.subTest(off=off):
                self.assertFalse(
                    charge_port.controls_enabled({"charge_port_controls": off})
                )

    def test_the_option_key_is_spelled_once(self):
        """const.py re-exports the pure module's key, so they cannot drift."""
        self.assertIn("from .charge_port import CHARGE_PORT_CONTROLS_OPTION", CONST)
        self.assertIn(
            "CONF_CHARGE_PORT_CONTROLS: Final = CHARGE_PORT_CONTROLS_OPTION", CONST
        )
        self.assertEqual(charge_port.CHARGE_PORT_CONTROLS_OPTION, "charge_port_controls")

    def test_the_buttons_are_not_created_when_it_is_off(self):
        self.assertIn("from .charge_port import controls_enabled", BUTTON)
        self.assertIn("charge_port_controls = controls_enabled(entry.options)", BUTTON)
        self.assertIn("_CHARGE_PORT_BUTTON_KEYS", BUTTON)
        self.assertIn('"open_charge_port", "close_charge_port"', BUTTON)

    def test_the_services_refuse_when_it_is_off(self):
        self.assertIn("from .charge_port import controls_enabled", SERVICES)
        self.assertIn("def _charge_port_controls_enabled(hass", SERVICES)
        self.assertEqual(
            SERVICES.count('translation_key="charge_port_control_disabled"'), 2,
            "both charge-port services must refuse with the same message",
        )

    def test_the_option_is_in_the_configure_form(self):
        self.assertIn("CONF_CHARGE_PORT_CONTROLS", FLOW)
        self.assertIn("default=bool(current.get(CONF_CHARGE_PORT_CONTROLS, True))", FLOW)

    def test_the_strings_exist_in_both_files(self):
        for name in ("strings.json", "translations/en.json"):
            data = json.loads((COMPONENT / name).read_text())
            init = data["options"]["step"]["init"]
            with self.subTest(name):
                self.assertIn("charge_port_controls", init["data"])
                self.assertIn("charge_port_controls", init["data_description"])
                self.assertIn("window_command_unverified", data["exceptions"])
                self.assertIn("charge_port_control_disabled", data["exceptions"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
