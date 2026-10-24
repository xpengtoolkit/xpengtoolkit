"""New app modes: protocol, permissions, support and measured state regressions."""

from _import_isolation import ImportIsolation
_IMPORTS = ImportIsolation()

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_consolidated_controls import Error, VIN, coordinator, policy, state, switch

_IMPORTS.use(__import__("test_consolidated_controls"))
from custom_components.xpeng.climate_modes import CLIMATE_MODES, mode_payload
from custom_components.xpeng.vehicle_capabilities import (
    CLIMATE_FEATURES,
    VehicleCapabilities,
)


class ModeTests(unittest.IsolatedAsyncioTestCase):
    def make(self, mode, *, flags=None, caps=None, p=None):
        c = coordinator(state(**{mode.section: {mode.state_key: mode.state_off}}), p)
        values = list(caps or [])
        if mode.key == "pet_mode" and flags is None:
            values.append("PROTECT_MODE")
        c.remote_capabilities = {"capabilities": values}
        c.vehicle_type_code = "UNKNOWN"
        c.vehicle_type_name = None
        c.vehicle_local_config = (
            flags if flags is not None else {CLIMATE_FEATURES[mode.key]: True}
        )
        send = Mock(
            return_value={
                "service_type": mode.service,
                "msg_type": 1,
                "msg_content": {"code": 200},
            }
        )
        return (
            switch.XPengClimateModeSwitch(
                c, SimpleNamespace(send_command_wait=send), mode
            ),
            send,
        )

    async def test_every_mode_has_distinct_exact_on_and_off_payloads(self):
        expected = {
            "max_heating": 8,
            "max_cooling": 6,
            "refresh": 11,
            "sterilisation": 9,
            "ac_cleaning": 10,
            "rear_defrost": 14,
            "pet_mode": 1,
            "silent_mode": 17,
        }
        self.assertEqual(len(CLIMATE_MODES), len(expected))
        for mode in CLIMATE_MODES:
            e, send = self.make(mode)
            await e.async_turn_on()
            send.assert_called_with(
                VIN,
                mode.service,
                {"cmd_type": expected[mode.key], "cmd_value": mode.on_value},
                2,
            )
            self.assertFalse(e.is_on)  # Success acknowledgement does not invent state.
            await e.async_turn_off()
            send.assert_called_with(
                VIN,
                mode.service,
                {"cmd_type": expected[mode.key], "cmd_value": mode.off_value},
                2,
            )
            self.assertEqual(e._attr_unique_id, f"xpeng_{VIN}_climate_{mode.key}")

    def test_silent_mode_uses_explicit_support_and_cloud_override(self):
        from custom_components.xpeng.vehicle_capabilities import from_coordinator
        mode = next(m for m in CLIMATE_MODES if m.key == "silent_mode")
        e, _ = self.make(mode, flags={})
        e.coordinator.vehicle_type_code = "FJ"
        self.assertTrue(from_coordinator(e.coordinator).support("silent_mode"))
        e.coordinator.vehicle_local_config = {"isSupportSilentMode": False}
        self.assertFalse(from_coordinator(e.coordinator).support("silent_mode"))
        e.coordinator.vehicle_type_code = "UNKNOWN"
        e.coordinator.vehicle_local_config = {}
        self.assertFalse(from_coordinator(e.coordinator).support("silent_mode"))
        e.coordinator.remote_capabilities = {"capabilities": ["HVAC_SILENT_MODE"]}
        self.assertTrue(from_coordinator(e.coordinator).support("silent_mode"))

    def test_duration_is_only_sent_for_modes_and_vehicles_that_accept_it(self):
        for mode in CLIMATE_MODES:
            e, _ = self.make(mode, caps=["HVAC_SUPPORT_TIME_SET"])
            for on in (True, False):
                payload = mode_payload(e.coordinator, mode, on)
                self.assertEqual(
                    "time" in payload,
                    mode.key in ("max_heating", "max_cooling", "refresh"),
                )
                if "time" in payload:
                    self.assertEqual(payload["time"], 15)

    async def test_unsupported_or_unknown_capabilities_never_publish(self):
        for mode in CLIMATE_MODES:
            for flags in ({}, {CLIMATE_FEATURES[mode.key]: False}):
                e, send = self.make(mode, flags=flags)
                for method in (e.async_turn_on, e.async_turn_off):
                    with self.assertRaises(Error):
                        await method()
                send.assert_not_called()

    async def test_permissions_are_checked_before_support_or_commands(self):
        p = policy.CommandPolicy.from_preset("read_only")
        for mode in CLIMATE_MODES:
            e, send = self.make(mode, p=p)
            for method in (e.async_turn_on, e.async_turn_off):
                with self.assertRaises(Error):
                    await method()
            send.assert_not_called()
            self.assertFalse(e.extra_state_attributes["control_allowed"])

    async def test_rejection_and_timeout_raise_without_changing_measured_state(self):
        e, send = self.make(CLIMATE_MODES[0])
        for result in (
            None,
            {"service_type": 3, "msg_type": 1, "msg_content": {"code": 8}},
        ):
            send.return_value = result
            with self.assertRaises(Error):
                await e.async_turn_on()
            self.assertFalse(e.is_on)

    def test_missing_invalid_and_unrelated_telemetry_does_not_claim_off(self):
        for mode in CLIMATE_MODES:
            e, _ = self.make(mode)
            for value in (None, -1, 1 if mode.key == "pet_mode" else 2, "1", True):
                e.coordinator.data = state(**{mode.section: {mode.state_key: value}})
                self.assertIsNone(e.is_on)
            e.coordinator.data = state(
                **{mode.section: {mode.state_key: mode.state_on, "hvac_on": 0}}
            )
            self.assertTrue(e.is_on)
            e.coordinator.data = state(hvac={"hvac_on": 1})
            self.assertIsNone(e.is_on)

    def test_pet_checking_is_not_off_and_command_off_is_not_the_state_off_code(self):
        mode = next(m for m in CLIMATE_MODES if m.key == "pet_mode")
        e, _ = self.make(mode)
        self.assertFalse(e.is_on)
        self.assertEqual(
            mode_payload(e.coordinator, mode, False), {"cmd_type": 1, "cmd_value": 1}
        )
        e.coordinator.data = state(protectMode={"protect_mode_status": 1})
        self.assertIsNone(e.is_on)
        e.coordinator.data = state(protectMode={"protect_mode_status": 2})
        self.assertTrue(e.is_on)

    async def test_pet_rear_seat_reminder_rejection_is_scoped_to_remote_start(self):
        from custom_components.xpeng.command_execution import VehicleCommandRejected

        mode = next(m for m in CLIMATE_MODES if m.key == "pet_mode")
        e, send = self.make(mode)
        send.return_value = {
            "service_type": 33,
            "msg_type": 1,
            "msg_content": {"code": 19013},
        }
        with self.assertRaises(Error) as exc:
            await e.async_turn_on()
        self.assertEqual(exc.exception.key, "pet_mode_rear_seat_reminder")
        self.assertFalse(e.is_on)
        for service, content in (
            (33, {"cmd_type": 1, "cmd_value": 1}),
            (33, {"cmd_type": 2, "cmd_value": 2}),
            (3, {"cmd_type": 1, "cmd_value": 2}),
        ):
            self.assertEqual(
                VehicleCommandRejected(19013, service, content).key,
                "vehicle_command_rejected",
            )

    def test_app_rapid_temperature_software_flag_and_cloud_booleans(self):
        self.assertTrue(
            VehicleCapabilities(
                ["RAPID_TEMP_UP_AND_DOWN"], {}, {"isSupportFastCold": False}
            ).support("max_cooling")
        )
        self.assertIsNone(
            VehicleCapabilities(None, {}, {"isSupportFastCold": False}).support(
                "max_cooling"
            )
        )
        self.assertFalse(
            VehicleCapabilities(["OTHER"], {}, {"isSupportAirClean": False}).support(
                "ac_cleaning"
            )
        )



_IMPORTS.finish(globals())

if __name__ == "__main__":
    unittest.main()
