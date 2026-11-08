"""Official-app climate protocol, measured feedback and refusal regressions."""

from _import_isolation import ImportIsolation
_IMPORTS = ImportIsolation()

from enum import IntFlag
from pathlib import Path
import ast
import asyncio
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_consolidated_controls import (
    Error,
    VIN,
    coordinator,
    entry,
    layout,
    load,
    module,
    policy,
    state,
    switch,
)

_IMPORTS.use(__import__("test_consolidated_controls"))


class Features(IntFlag):
    TARGET_TEMPERATURE = 1
    TURN_ON = 128
    TURN_OFF = 256
    FAN_MODE = 8
    PRESET_MODE = 16


class _MockClimateEntity:
    @property
    def current_temperature(self):
        return getattr(self, "native_current_temperature", None)

    @property
    def target_temperature(self):
        return getattr(self, "native_target_temperature", None)


module(
    "homeassistant.components.climate",
    ATTR_TEMPERATURE="temperature",
    ClimateEntity=_MockClimateEntity,
    ClimateEntityFeature=Features,
    HVACAction=SimpleNamespace(
        OFF="off", IDLE="idle", HEATING="heating", COOLING="cooling"
    ),
    HVACMode=SimpleNamespace(OFF="off", HEAT_COOL="heat_cool"),
)
module("homeassistant.components.climate.const", PRESET_NONE="none")
module("homeassistant.const", UnitOfTemperature=SimpleNamespace(CELSIUS="C"))
module(
    "homeassistant.components.number",
    NumberEntity=type("NumberEntity", (), {}),
    NumberMode=SimpleNamespace(SLIDER="slider", BOX="box"),
)
sys.modules["homeassistant.exceptions"].ServiceValidationError = Error
climate = load("custom_components.xpeng.climate", "climate.py")
commands = load("custom_components.xpeng.climate_commands", "climate_commands.py")
number = load("custom_components.xpeng.number", "number.py")


class ClimateTests(unittest.IsolatedAsyncioTestCase):
    def make(self, caps=None, p=None):
        send = Mock(
            return_value={
                "service_type": 3,
                "msg_type": 1,
                "msg_content": {"code": 200},
            }
        )
        c = coordinator(
            state(
                hvac={
                    "hvac_on": 0,
                    "hvac_temp_float": 22.5,
                    "hvac_inner_temp_float": 26,
                    "hvac_defrost": 0,
                }
            ),
            p,
        )
        c.remote_capabilities = {"capabilities": caps or []}
        c.vehicle_type_code = "FJ"
        c.vehicle_local_config = {}
        client = SimpleNamespace(send_command_wait=send)
        return climate.XPengClimate(c, client), send

    async def test_power_is_not_quick_cooling_and_never_fabricates_a_state(self):
        e, send = self.make(["HVAC_TEMP_SUPPORT_FLOAT", "HVAC_SUPPORT_TIME_SET"])
        await e.async_turn_on()
        send.assert_called_with(VIN, 3, {"cmd_type": 1, "cmd_value": 1, "time": 15}, 2)
        self.assertEqual(e.hvac_mode, "off")
        self.assertEqual(e.native_target_temperature, 22.5)
        self.assertEqual(e.native_current_temperature, 26)
        self.assertEqual(e.target_temperature, 22.5)
        self.assertEqual(e.current_temperature, 26)
        e.coordinator.data = state(hvac={"hvac_on": 1, "hvac_temp_float": 22})
        self.assertEqual(e.hvac_mode, "heat_cool")
        await e.async_turn_off()
        send.assert_called_with(VIN, 3, {"cmd_type": 1, "cmd_value": 0, "time": 0}, 2)
        self.assertEqual(e.hvac_mode, "heat_cool")

    async def test_fractional_targets_use_command_16_and_feature_selected_duration(
        self,
    ):
        e, send = self.make(["HVAC_TEMP_SUPPORT_FLOAT", "HVAC_SUPPORT_TIME_SET"])
        await e.async_set_temperature(temperature=23.5)
        send.assert_called_once_with(
            VIN, 3, {"cmd_type": 16, "cmd_value": 23.5, "time": 15}, 2
        )
        self.assertEqual(e.target_temperature_step, 0.5)
        self.assertEqual(e.target_temperature, 22.5)
        self.assertEqual(e.hvac_mode, "off")
        e.coordinator.remote_capabilities = {
            "capabilities": ["HVAC_TEMP_SUPPORT_FLOAT"]
        }
        await e.async_set_temperature(temperature=24)
        send.assert_called_with(VIN, 3, {"cmd_type": 16, "cmd_value": 24.0}, 2)

    async def test_integer_targets_use_command_2_without_guessing_fractional_support(
        self,
    ):
        e, send = self.make()
        await e.async_set_temperature(temperature=23)
        send.assert_called_with(VIN, 3, {"cmd_type": 2, "cmd_value": 23}, 2)
        self.assertEqual(type(send.call_args.args[2]["cmd_value"]), int)
        self.assertEqual(e.target_temperature_step, 1)
        with self.assertRaises(Error):
            await e.async_set_temperature(temperature=23.5)
        self.assertEqual(send.call_count, 1)

    async def test_lo_hi_require_both_fractional_and_range_capabilities(self):
        e, send = self.make(["HVAC_TEMP_SUPPORT_FLOAT", "HVAC_TEMP_RANGE_ADJUSTMENT"])
        self.assertEqual((e.min_temp, e.max_temp), (15.5, 32.5))
        self.assertEqual(
            e.extra_state_attributes, {"temperature_extremes": {"LO": 15.5, "HI": 32.5}}
        )
        for value in (15.5, 32.5):
            await e.async_set_temperature(temperature=value)
            send.assert_called_with(VIN, 3, {"cmd_type": 16, "cmd_value": value}, 2)
        for caps in (["HVAC_TEMP_SUPPORT_FLOAT"], ["HVAC_TEMP_RANGE_ADJUSTMENT"], []):
            e, send = self.make(caps)
            self.assertEqual((e.min_temp, e.max_temp), (16, 32))
            self.assertEqual(e.extra_state_attributes, {})
            for value in (15.5, 32.5):
                with self.assertRaises(Error):
                    await e.async_set_temperature(temperature=value)
            send.assert_not_called()

    async def test_automatic_power_matches_app_model_profile_and_cloud_override(self):
        e, send = self.make(["HVAC_SUPPORT_TIME_SET"])
        e.coordinator.vehicle_type_code = "DC"
        await e.async_turn_on()
        send.assert_called_with(VIN, 3, {"cmd_type": 3, "cmd_value": 1}, 2)
        e.coordinator.vehicle_local_config = {"isSupportAcAutoMode": False}
        await e.async_turn_on()
        send.assert_called_with(VIN, 3, {"cmd_type": 1, "cmd_value": 1, "time": 15}, 2)
        e.coordinator.vehicle_type_code = "FJ"
        e.coordinator.vehicle_local_config = {"isSupportAcAutoMode": True}
        await e.async_turn_on()
        send.assert_called_with(VIN, 3, {"cmd_type": 3, "cmd_value": 1}, 2)

    async def test_unverified_fan_paths_cannot_send_temperature_commands(self):
        e, send = self.make()
        self.assertFalse(e.supported_features & Features.FAN_MODE)
        with self.assertRaises(Error) as exc:
            await e.async_set_fan_mode("3")
        self.assertEqual(exc.exception.key, "fan_speed_unverified")
        old = number.XPengFanSpeed(e.coordinator, e._mqtt_client)
        with self.assertRaises(Error) as exc:
            await old.async_set_native_value(3)
        self.assertEqual(exc.exception.key, "fan_speed_unverified")
        send.assert_not_called()

    async def test_rejections_timeouts_and_malformed_replies_do_not_become_success(
        self,
    ):
        e, send = self.make(["HVAC_TEMP_SUPPORT_FLOAT"])
        for reply, key in [
            (None, "command_not_confirmed"),
            (
                {"service_type": 3, "msg_type": 1, "msg_content": {"code": 8}},
                "vehicle_command_rejected",
            ),
            (
                {"service_type": 3, "msg_type": 1, "msg_content": {"code": 420}},
                "vehicle_command_rejected",
            ),
            (
                {"service_type": 4, "msg_type": 1, "msg_content": {"code": 200}},
                "command_response_invalid",
            ),
        ]:
            send.return_value = reply
            for act in (
                e.async_turn_on,
                e.async_turn_off,
                lambda: e.async_set_temperature(temperature=22),
                lambda: e.async_set_preset_mode("defrost"),
            ):
                with self.assertRaises(Error) as exc:
                    await act()
                self.assertEqual(exc.exception.key, key)
                self.assertEqual(e.hvac_mode, "off")
                self.assertEqual(e.target_temperature, 22.5)

    async def test_defrost_retains_the_official_app_payload_and_waits_for_ack(self):
        e, send = self.make()
        self.assertEqual(e.preset_mode, "none")
        await e.async_set_preset_mode("defrost")
        send.assert_called_with(VIN, 3, {"cmd_type": 4, "cmd_value": 1}, 2)
        self.assertEqual(e.preset_mode, "none")
        e.coordinator.data = state(hvac={"hvac_defrost": 1})
        self.assertEqual(e.preset_mode, "defrost")
        await e.async_set_preset_mode("none")
        send.assert_called_with(VIN, 3, {"cmd_type": 4, "cmd_value": 0}, 2)

    async def test_invalid_targets_modes_and_read_only_never_publish(self):
        e, send = self.make(["HVAC_TEMP_SUPPORT_FLOAT"])
        for value in (True, float("nan"), float("inf"), "bad", 15.5, 32.5, 22.3):
            with self.assertRaises(Error):
                await e.async_set_temperature(temperature=value)
        with self.assertRaises(Error):
            await e.async_set_hvac_mode("cool")
        with self.assertRaises(Error):
            await e.async_set_preset_mode("eco")
        e.coordinator.command_policy = policy.CommandPolicy.from_preset("read_only")
        self.assertEqual(e.supported_features, 0)
        for method in (
            e.async_turn_on,
            e.async_turn_off,
            lambda: e.async_set_temperature(temperature=22),
            lambda: e.async_set_preset_mode("defrost"),
        ):
            with self.assertRaises(Error):
                await method()
        send.assert_not_called()

    def test_unknown_mode_and_missing_temperatures_stay_unknown(self):
        e, _ = self.make()
        e.coordinator.data = state(hvac={})
        self.assertIsNone(e.hvac_mode)
        self.assertIsNone(e.hvac_action)
        self.assertIsNone(e.target_temperature)
        self.assertIsNone(e.current_temperature)
        e.coordinator.data = state(hvac={"hvac_temp": 22, "hvac_inner_temp": 25})
        self.assertEqual(e.target_temperature, 22)
        self.assertEqual(e.current_temperature, 25)

    async def test_integration_services_use_the_same_capability_aware_protocol(self):
        # Execute the registered handler bodies with a fake HA connection, not
        # a second implementation of those handlers or a live vehicle service.
        e, send = self.make(["HVAC_TEMP_SUPPORT_FLOAT", "HVAC_SUPPORT_TIME_SET"])
        tree = ast.parse(
            (
                Path(__file__).resolve().parent.parent
                / "custom_components/xpeng/services.py"
            ).read_text(encoding="utf-8")
        )
        setup = next(
            n
            for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "async_setup_services"
        )
        handlers = [
            n
            for n in setup.body
            if isinstance(n, ast.AsyncFunctionDef)
            and n.name in ("handle_climate_on", "handle_climate_off")
        ]
        env = {
            "ServiceCall": object,
            "DOMAIN": "xpeng",
            "HomeAssistantError": Error,
            "hass": e.hass,
            "_entry_for_call": lambda hass, call: {
                "coordinator": e.coordinator,
                "mqtt_client": e._mqtt_client,
            },
            "send_climate": commands.send_climate,
            "power_payload": commands.power_payload,
            "temperature_payload": commands.temperature_payload,
        }
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=handlers, type_ignores=[])),
                "services.py",
                "exec",
            ),
            env,
        )
        await env["handle_climate_on"](
            SimpleNamespace(data={"temperature": 24.5, "duration": 20})
        )
        self.assertEqual(
            [c.args[2] for c in send.call_args_list],
            [
                {"cmd_type": 16, "cmd_value": 24.5, "time": 20},
                {"cmd_type": 1, "cmd_value": 1, "time": 20},
            ],
        )
        send.reset_mock()
        send.return_value = {
            "service_type": 3,
            "msg_type": 1,
            "msg_content": {"code": 8},
        }
        with self.assertRaises(Error):
            await env["handle_climate_on"](SimpleNamespace(data={"temperature": 24.5}))
        self.assertEqual(
            send.call_count, 1
        )  # A rejected target must not proceed to power-on.
        send.reset_mock()
        send.side_effect = [
            {"service_type": 3, "msg_type": 1, "msg_content": {"code": 200}},
            {"service_type": 3, "msg_type": 1, "msg_content": {"code": 8}},
        ]
        with self.assertRaises(Error) as partial:
            await env["handle_climate_on"](SimpleNamespace(data={"temperature": 24.5}))
        self.assertEqual(partial.exception.key, "climate_start_partial")
        self.assertEqual(send.call_count, 2)  # No hidden rollback after power refusal.

    async def test_legacy_defrost_refusal_preserves_measured_state(self):
        c = coordinator(state(hvac={"hvac_defrost": 0}))
        send = Mock(return_value={"service_type": 3, "msg_type": 1, "msg_content": {"code": 8}})
        e = switch.XPengDefrostSwitch(c, SimpleNamespace(send_command_wait=send))
        with self.assertRaises(Error):
            await e.async_turn_on()
        self.assertFalse(e.is_on)
        self.assertFalse(e._attr_assumed_state)

    async def test_charge_limit_refusal_does_not_change_reported_limit(self):
        c = coordinator(state(charge={"max_range_charge": 80}))
        send = Mock(return_value={"service_type": 4, "msg_type": 1, "msg_content": {"code": 10401}})
        e = number.XPengChargeLimit(c, SimpleNamespace(send_command_wait=send))
        with self.assertRaises(Error):
            await e.async_set_native_value(90)
        self.assertEqual(e.native_value, 80)

    def test_compact_migration_preserves_existing_controller_ids(self):
        p = policy.CommandPolicy.from_preset("full")
        self.assertEqual(
            layout.REPLACEMENTS[("number", "fan_speed_control")][:2],
            ("climate", "cabin_climate"),
        )
        self.assertFalse(
            layout.include_legacy_entity(entry(), p, "number", "fan_speed_control")
        )



_IMPORTS.finish(globals())

if __name__ == "__main__":
    unittest.main()
