"""Feature discovery must not confuse missing data or readiness with support."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest

PKG = Path(__file__).resolve().parents[1] / "custom_components/xpeng"
package = ModuleType("custom_components.xpeng")
package.__path__ = [str(PKG)]
sys.modules.setdefault("custom_components", ModuleType("custom_components"))
sys.modules["custom_components.xpeng"] = package


def load(name):
    spec = importlib.util.spec_from_file_location(
        "custom_components.xpeng." + name, PKG / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cap = load("vehicle_capabilities")


class Features(unittest.TestCase):
    def test_missing_and_malformed_data_never_hide_features(self):
        for software in (None, {}, [], {"capabilities": []}, {"code": 500}, [True]):
            c = cap.VehicleCapabilities(software)
            self.assertEqual(set(c.decisions().values()), {"unknown"})

    def test_model_facts_distinguish_manual_and_motorized_flaps(self):
        self.assertFalse(cap.model_flags("FJ", "F57a")["isSupportChargePortControl"])
        self.assertTrue(cap.model_flags("HA", "H93A")["isSupportChargePortControl"])
        self.assertEqual(cap.model_flags("future_model", None), {})

    def test_cloud_overrides_model_and_unknown_hardware_stays_unknown(self):
        c = SimpleNamespace(
            vehicle_type_code="FJ",
            vehicle_type_name="F57a",
            vehicle_local_config={"isSupportChargePortControl": True},
        )
        self.assertTrue(cap.from_coordinator(c).support("charge_port_control"))
        self.assertIsNone(cap.from_coordinator(c).support("rear_seat_ventilation"))
        c.vehicle_hardware = {"REAR_SEAT_WIND": False}
        self.assertFalse(cap.from_coordinator(c).support("rear_seat_ventilation"))
        c.vehicle_hardware["REAR_SEAT_WIND"] = True
        self.assertTrue(cap.from_coordinator(c).support("rear_seat_ventilation"))

    def test_optional_seat_needs_the_correct_software_and_hardware(self):
        c = cap.VehicleCapabilities(
            ["DRIVER_SEAT_HEAT_SOFTWARE"],
            {"DRIVER_SEAT_HEAT_HARDWARE": False},
            {"isSeatDoubleCheck": True},
        )
        self.assertFalse(c.support("driver_seat_heat"))
        c.hardware["DRIVER_SEAT_HEAT_HARDWARE"] = True
        self.assertTrue(c.support("driver_seat_heat"))
        c.local = {}
        self.assertIsNone(c.support("driver_seat_heat"))

    def test_legacy_seats_use_model_or_hardware(self):
        c = cap.VehicleCapabilities(
            None,
            {"REAR_SEAT_HEAT": False},
            {"isSeatDoubleCheck": False, "isSupportSeatBackHeat": False},
        )
        self.assertFalse(c.support("rear_seat_heat"))
        c.hardware["REAR_SEAT_HEAT"] = True
        self.assertTrue(c.support("rear_seat_heat"))

    def test_sentinel_combines_software_and_hardware(self):
        c = cap.VehicleCapabilities(
            ["SENTINEL_MODE"], {"AVM": True, "BIG_CUSTOMER": False}
        )
        self.assertTrue(c.support("sentinel"))
        c.hardware["BIG_CUSTOMER"] = True
        self.assertFalse(c.support("sentinel"))

    def test_no_error_code_charger_readiness_or_telemetry_is_a_gate(self):
        c = SimpleNamespace(data={"charging_state": 0}, command_code=10401)
        features = cap.from_coordinator(c)
        self.assertIsNone(features.support("charging"))
        self.assertNotIn("charging_switch", cap.ENTITY_FEATURES)
        self.assertNotIn("windows", cap.ENTITY_FEATURES)
        self.assertNotIn("trunk", cap.ENTITY_FEATURES)

    def test_only_allowlisted_booleans_are_retained(self):
        data = {
            "AVM": "false",
            "BIG_CUSTOMER": "true",
            "WHEEL_HEAT": 0,
            "vin": "synthetic",
            "accessToken": "synthetic",
            "FRIDGE": "unknown",
        }
        self.assertEqual(
            cap.normalize_flags(data, cap.HARDWARE_KEYS),
            {"AVM": False, "BIG_CUSTOMER": True, "WHEEL_HEAT": False},
        )


class Registry(unittest.TestCase):
    def test_unsupported_disabled_supported_restored_and_user_choices_preserved(self):
        records = [
            SimpleNamespace(
                entity_id=str(i),
                platform="xpeng",
                unique_id="xpeng_SYNTHETIC_sentinel_switch",
                disabled_by=disabled,
            )
            for i, disabled in enumerate((None, "user", "config_entry", "integration"))
        ]
        records.append(
            SimpleNamespace(
                entity_id="other",
                platform="other",
                unique_id=records[0].unique_id,
                disabled_by=None,
            )
        )

        class FakeRegistry:
            def async_update_entity(self, entity_id, **kwargs):
                next(r for r in records if r.entity_id == entity_id).__dict__.update(
                    kwargs
                )

        registry = FakeRegistry()
        er = SimpleNamespace(
            async_get=lambda hass: registry,
            async_entries_for_config_entry=lambda reg, entry: records,
            RegistryEntryDisabler=SimpleNamespace(INTEGRATION="integration"),
        )
        helpers = ModuleType("homeassistant.helpers")
        helpers.entity_registry = er
        sys.modules["homeassistant"] = ModuleType("homeassistant")
        sys.modules["homeassistant.helpers"] = helpers
        sync = load("capability_registry").sync_capability_entities
        c = SimpleNamespace(vin="SYNTHETIC", remote_capabilities=["OTHER_FLAG"])
        sync(object(), SimpleNamespace(entry_id="entry"), c)
        self.assertEqual(
            [r.disabled_by for r in records],
            ["integration", "user", "config_entry", "integration", None],
        )
        c.remote_capabilities = None
        sync(object(), SimpleNamespace(entry_id="entry"), c)
        self.assertEqual(records[0].disabled_by, "integration")
        c.remote_capabilities = ["SENTINEL_MODE"]
        c.vehicle_hardware = {"AVM": True, "BIG_CUSTOMER": False}
        sync(object(), SimpleNamespace(entry_id="entry"), c)
        self.assertEqual(
            [r.disabled_by for r in records], [None, "user", "config_entry", None, None]
        )


if __name__ == "__main__":
    unittest.main()
