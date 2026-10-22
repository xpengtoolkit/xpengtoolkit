"""Compact controls must retain evcc's typed Home Assistant interfaces."""

import ast
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest

PKG = Path(__file__).resolve().parents[1] / "custom_components/xpeng"
package = ModuleType("custom_components.xpeng")
package.__path__ = [str(PKG)]
sys.modules.setdefault("custom_components", ModuleType("custom_components"))
sys.modules[package.__name__] = package


def load(name):
    spec = importlib.util.spec_from_file_location(
        package.__name__ + "." + name, PKG / (name + ".py")
    )
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


policy = load("command_policy")
layout = load("entity_layout")


class EvccCompatibility(unittest.TestCase):
    def test_evcc_interfaces_survive_compact_mode_under_every_policy(self):
        entry = SimpleNamespace(version=2, options={"legacy_entities": False})
        required = {
            ("sensor", "battery_soc"),
            ("sensor", "range"),
            ("number", "charge_limit_control"),
            ("binary_sensor", "hvac_on"),
            ("button", "wake_vehicle"),
            ("sensor", "charging_state"),
            ("sensor", "charge_power"),
            ("sensor", "odometer"),
            ("sensor", "last_data_update"),
            ("binary_sensor", "plugged_in"),
        }
        for preset in ("full", "safe", "read_only"):
            p = policy.CommandPolicy.from_preset(preset)
            for domain, key in required:
                with self.subTest(preset=preset, domain=domain, key=key):
                    self.assertTrue(layout.include_legacy_entity(entry, p, domain, key))
        self.assertFalse(
            layout.include_legacy_entity(
                entry,
                policy.CommandPolicy.from_preset("full"),
                "sensor",
                "hvac_fan_speed",
            )
        )

    def test_registry_cleanup_does_not_remove_existing_climate_status(self):
        old = SimpleNamespace(
            entity_id="binary_sensor.my_custom_climate",
            unique_id="xpeng_SYNTHETIC_hvac_on",
            config_entry_id="entry",
        )

        class Registry:
            def async_get_entity_id(self, domain, platform, unique_id):
                return old.entity_id if unique_id == old.unique_id else None

            def async_get(self, eid):
                return old

            def async_remove(self, eid):
                raise AssertionError("evcc interface was removed")

        helpers = ModuleType("homeassistant.helpers")
        helpers.entity_registry = SimpleNamespace(async_get=lambda hass: Registry())
        sys.modules["homeassistant"] = ModuleType("homeassistant")
        sys.modules[helpers.__name__] = helpers
        layout.prepare_compact_registry(
            object(),
            SimpleNamespace(
                entry_id="entry", version=2, options={"legacy_entities": False}
            ),
            "SYNTHETIC",
            policy.CommandPolicy.from_preset("full"),
        )

    def test_climate_status_remains_measured_binary_on_off(self):
        tree = ast.parse((PKG / "binary_sensor.py").read_text())
        cls = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "XPengBinarySensor"
        )
        fn = next(
            n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "is_on"
        )
        ns = {}
        fn.decorator_list = []
        exec(
            compile(ast.Module(body=[fn], type_ignores=[]), "climate_status", "exec"),
            ns,
        )
        e = SimpleNamespace(
            entity_description=SimpleNamespace(
                value_path="hvac", value_fn=None, on_value=1
            )
        )
        for raw, expected in ((None, None), (0, False), (1, True)):
            e._get_nested_value = lambda path, value=raw: value
            self.assertIs(ns["is_on"](e), expected)


if __name__ == "__main__":
    unittest.main()
