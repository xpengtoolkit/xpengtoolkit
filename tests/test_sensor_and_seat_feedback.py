#!/usr/bin/env python3
"""Exercise entity methods without installing or connecting to Home Assistant."""
import __future__
import ast
import asyncio
from collections import deque
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

COMP = Path(__file__).resolve().parents[1] / "custom_components/xpeng"

class Entity:
    _vin = "TEST_VEHICLE"
    def __init__(self, coordinator):
        self.coordinator = coordinator
    def _get_nested_value(self, path):
        return self.coordinator.raw
    def _guard(self):
        pass

class HAError(Exception):
    def __init__(self, **kwargs):
        self.translation_key = kwargs.get("translation_key")
        super().__init__(self.translation_key)

def load_class(name, filename, **namespace):
    tree = ast.parse((COMP / filename).read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    ns = dict(XPengEntity=Entity, SensorEntity=object, SelectEntity=object,
              SensorDeviceClass=SimpleNamespace(ENUM="enum"),
              EntityCategory=SimpleNamespace(DIAGNOSTIC="diagnostic"),
              GROUP_COMFORT="comfort", DOMAIN="xpeng", asyncio=asyncio,
              HomeAssistantError=HAError, ServiceValidationError=HAError)
    ns.update(namespace)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(COMP / filename), "exec",
                 flags=__future__.annotations.compiler_flag), ns)
    return ns[name]

class EnumSensorValues(unittest.TestCase):
    def sensor(self, raw, device_class="enum", fn=None):
        cls = load_class("XPengSensor", "sensor.py")
        desc = SimpleNamespace(key="test", value_path="test", value_fn=fn,
                               device_class=device_class, options=["Closed", "Open", "Connected"])
        return cls(SimpleNamespace(raw=raw), desc)

    def test_charge_port_sentinel_is_unknown_instead_of_invalid_enum(self):
        self.assertIsNone(self.sensor(-1, fn=lambda v: f"Unknown ({v})").native_value)
    def test_valid_enum_is_preserved(self):
        self.assertEqual(self.sensor("Connected").native_value, "Connected")
    def test_missing_data_stays_unknown(self):
        self.assertIsNone(self.sensor(None).native_value)
    def test_numeric_sensor_is_not_restricted_by_enum_options(self):
        self.assertEqual(self.sensor(74, device_class="battery").native_value, 74)

class SeatPublication(unittest.IsolatedAsyncioTestCase):
    def seat(self, results, *, position=1, model=None):
        calls = []
        def send(*args):
            calls.append(args)
            return results.pop(0)
        async def confirmed(hass, client, vin, subsystem, content):
            result = send(vin, subsystem, content)
            if isinstance(result, Exception):
                raise result
            if not result:
                raise HAError(translation_key="command_not_sent")
        cls = load_class("XPengSeatSelect", "select.py", async_send_confirmed=confirmed)
        entity = cls(SimpleNamespace(vehicle_type_code=model), SimpleNamespace(send_command=send),
                     "driver_seat_heat_select", "mdi:car-seat-heater",
                     position, "seat", ["Off", "Heat 3"], {"Off": (0, 1), "Heat 3": (3, 1)})
        async def executor(fn, *args):
            return fn(*args)
        entity.hass = SimpleNamespace(async_add_executor_job=executor)
        return entity, calls

    async def test_passenger_mapping_uses_the_model_at_send_time(self):
        for model in ("FJ", "EA", "UNKNOWN", None):
            for position in (0, 1, 3, 4):
                with self.subTest(model=model, position=position):
                    seat, calls = self.seat([True], position=position)
                    # The real startup discovers FJ after entity construction.
                    seat.coordinator.vehicle_type_code = model
                    await seat.async_select_option("Off")
                    expected = 2 if model == "FJ" and position == 0 else position
                    self.assertEqual(calls[0][2]["cmd_type"], expected)

    async def test_fj_off_first_and_requested_level_use_the_same_position(self):
        seat, calls = self.seat([True, True], position=0)
        seat.coordinator.vehicle_type_code = "FJ"
        with patch.object(asyncio, "sleep", new_callable=AsyncMock):
            await seat.async_select_option("Heat 3")
        self.assertEqual([c[2] for c in calls], [
            {"cmd_type": 2, "mode": 0, "cmd_value": 1},
            {"cmd_type": 2, "mode": 3, "cmd_value": 1}])

    async def test_invalid_option_never_sends_off(self):
        seat, calls = self.seat([])
        with self.assertRaises(HAError) as raised:
            await seat.async_select_option("Unexpected")
        self.assertEqual(raised.exception.translation_key, "invalid_seat_option")
        self.assertEqual(calls, [])

    async def test_failed_preparatory_publish_stops_the_sequence(self):
        seat, calls = self.seat([False])
        with patch.object(asyncio, "sleep", new_callable=AsyncMock) as sleep:
            with self.assertRaises(HAError) as raised:
                await seat.async_select_option("Heat 3")
        self.assertEqual(raised.exception.translation_key, "command_not_sent")
        self.assertEqual(len(calls), 1)
        sleep.assert_not_awaited()

    async def test_failed_level_publish_is_reported(self):
        seat, calls = self.seat([True, False])
        with patch.object(asyncio, "sleep", new_callable=AsyncMock):
            with self.assertRaises(HAError):
                await seat.async_select_option("Heat 3")
        self.assertEqual([c[2]["mode"] for c in calls], [0, 3])

    async def test_vehicle_refusal_stops_before_the_requested_level(self):
        seat, calls = self.seat([HAError(translation_key="command_rejected")])
        with patch.object(asyncio, "sleep", new_callable=AsyncMock) as sleep:
            with self.assertRaises(HAError) as raised:
                await seat.async_select_option("Heat 3")
        self.assertEqual(raised.exception.translation_key, "command_rejected")
        self.assertEqual(len(calls), 1)
        sleep.assert_not_awaited()

    async def test_timeout_on_requested_level_is_not_reported_as_success(self):
        seat, calls = self.seat([True, HAError(translation_key="command_not_confirmed")])
        with patch.object(asyncio, "sleep", new_callable=AsyncMock):
            with self.assertRaises(HAError) as raised:
                await seat.async_select_option("Heat 3")
        self.assertEqual(raised.exception.translation_key, "command_not_confirmed")
        self.assertEqual([c[2]["mode"] for c in calls], [0, 3])

    async def test_success_preserves_the_measured_command_encoding(self):
        seat, calls = self.seat([True, True])
        with patch.object(asyncio, "sleep", new_callable=AsyncMock):
            await seat.async_select_option("Heat 3")
        self.assertEqual([c[2] for c in calls], [
            {"cmd_type": 1, "mode": 0, "cmd_value": 1},
            {"cmd_type": 1, "mode": 3, "cmd_value": 1}])

spec = importlib.util.spec_from_file_location("feedback_errors", COMP / "xpeng_errors.py")
errors = importlib.util.module_from_spec(spec)
spec.loader.exec_module(errors)

class EnglishFeedback(unittest.TestCase):
    def test_supported_response_codes_have_english_messages(self):
        self.assertEqual(errors.command_response_message(200), "Command accepted")
        self.assertIn("not supported", errors.command_response_message(8))
        self.assertIn("13023", errors.command_response_message(13023))

    def test_sensor_translates_display_and_preserves_vendor_message(self):
        cls = load_class("XPengCommandFeedbackSensor", "sensor.py",
                         command_response_message=errors.command_response_message)
        fb = {"code": 8, "message": "Nicht unterstützt für dieses Fahrzeug"}
        sensor = cls(SimpleNamespace(last_command_feedback=fb, command_feedback_count=1,
                                     command_feedbacks=deque([fb])))
        self.assertIn("not supported", sensor.native_value)
        self.assertEqual(sensor.extra_state_attributes["message"], fb["message"])

if __name__ == "__main__":
    unittest.main()
