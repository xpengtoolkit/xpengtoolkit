"""Regression coverage for merged controllers, measured state and registry migration."""

from __future__ import annotations

import asyncio
import ast
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from enum import IntFlag
from types import SimpleNamespace
from unittest.mock import Mock

PKG = Path(__file__).resolve().parent.parent / "custom_components/xpeng"


def module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PKG / filename)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


class Error(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args)
        self.key = kwargs.get("translation_key")


class Feature(IntFlag):
    OPEN = 1
    CLOSE = 2


class Base:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._vin = coordinator.vin
        self.hass = SimpleNamespace(async_add_executor_job=self.executor)

    @property
    def _policy(self):
        return self.coordinator.command_policy

    def _guard(self, *args, group=None):
        if not self._policy.allows(group or self._policy_group):
            raise Error("Denied")

    def _policy_visible(self, policy):
        return any(policy.exposes(g) for g in self._policy_groups)

    async def executor(self, fn, *args):
        return fn(*args)


module("custom_components", __path__=[])
module("custom_components.xpeng", __path__=[str(PKG)])
policy = load("custom_components.xpeng.command_policy", "command_policy.py")
layout = load("custom_components.xpeng.entity_layout", "entity_layout.py")
load("custom_components.xpeng.charge_port", "charge_port.py")
module("homeassistant", __path__=[])
module("homeassistant.components", __path__=[])
module(
    "homeassistant.components.cover",
    CoverEntity=type("CoverEntity", (), {}),
    CoverDeviceClass=SimpleNamespace(WINDOW="window", DOOR="door"),
    CoverEntityFeature=Feature,
)
module("homeassistant.components.switch", SwitchEntity=type("SwitchEntity", (), {}))
module("homeassistant.config_entries", ConfigEntry=object)
module("homeassistant.core", HomeAssistant=object)
module("homeassistant.exceptions", HomeAssistantError=Error)
module("homeassistant.helpers", __path__=[])
module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
module("custom_components.xpeng.const", DOMAIN="xpeng")
module("custom_components.xpeng.coordinator", XPengDataCoordinator=object)
module("custom_components.xpeng.entity", XPengEntity=Base)
module("custom_components.xpeng.mqtt_client", XPengMQTTClient=object)
cover = load("custom_components.xpeng.cover", "cover.py")
switch = load("custom_components.xpeng.switch", "switch.py")

VIN = "SYNTHETIC123456789"


def coordinator(data=None, p=None):
    return SimpleNamespace(
        vin=VIN, data=data, command_policy=p or policy.CommandPolicy.from_preset("full")
    )


def entry(legacy=False, version=2):
    return SimpleNamespace(
        entry_id="entry", version=version, options={"legacy_entities": legacy}
    )


def state(**instant):
    return {"msg_content": {"instant": instant}}


class CoverTests(unittest.IsolatedAsyncioTestCase):
    def make(self, key, data=None, p=None, **kwargs):
        client = SimpleNamespace(
            send_command_wait=Mock(
                return_value={
                    "service_type": {"trunk": 9, "windows": 12, "charge_port": 4}[key],
                    "msg_type": 1,
                    "msg_content": {"code": 200},
                }
            )
        )
        e = cover.XPengCover(coordinator(data, p), client, key, **kwargs)
        return e, client.send_command_wait

    async def test_trunk_open_close_use_existing_commands_and_wait_for_report(self):
        e, send = self.make("trunk", state(tail={"tail_status": 0}))
        self.assertTrue(e.is_closed)
        await e.async_open_cover()
        send.assert_called_with(VIN, 9, {"cmd_type": 2, "cmd_value": 1}, 2)
        self.assertTrue(e.is_closed)  # publication does not pretend it opened
        e.coordinator.data = state(tail={"tail_status": 1})
        self.assertFalse(e.is_closed)
        await e.async_close_cover()
        send.assert_called_with(VIN, 9, {"cmd_type": 2, "cmd_value": 3}, 2)

    async def test_securing_only_never_opens_the_trunk(self):
        p = policy.CommandPolicy.from_preset("safe")
        e, send = self.make("trunk", p=p)
        self.assertEqual(e.supported_features, Feature.CLOSE)
        with self.assertRaises(Error):
            await e.async_open_cover()
        send.assert_not_called()
        await e.async_close_cover()
        send.assert_called_once()

    async def test_read_only_withdraws_features_and_blocks_direct_calls(self):
        e, send = self.make("trunk", p=policy.CommandPolicy.from_preset("read_only"))
        self.assertEqual(e.supported_features, 0)
        for method in (e.async_open_cover, e.async_close_cover):
            with self.assertRaises(Error):
                await method()
        send.assert_not_called()

    async def test_unconfirmed_command_is_reported(self):
        e, send = self.make("trunk", state(tail={"tail_status": 0}))
        send.return_value = None
        with self.assertRaises(Error) as exc:
            await e.async_open_cover()
        self.assertEqual(exc.exception.key, "command_not_confirmed")
        self.assertTrue(e.is_closed)

    async def test_window_vent_and_close_never_target_charge_port(self):
        e, send = self.make("windows")
        self.assertEqual(e.supported_features, Feature.OPEN | Feature.CLOSE)
        await e.async_open_cover()
        send.assert_called_once_with(VIN, 12, {"cmd_type": 1, "cmd_value": 2}, 2)
        self.assertEqual(e.extra_state_attributes["open_behavior"], "ventilation")
        await e.async_close_cover()
        send.assert_called_with(VIN, 12, {"cmd_type": 1, "cmd_value": 1}, 2)
        self.assertEqual(send.call_count, 2)
        self.assertTrue(e.extra_state_attributes["close_supported"])
        self.assertTrue(all(call.args[1] == 12 for call in send.call_args_list))

    def test_windows_do_not_invent_closed_state_when_data_is_missing(self):
        e, _ = self.make("windows")
        self.assertIsNone(e.is_closed)
        positions = {
            f"{s}_window_position": 100
            for s in ("front_left", "front_right", "rear_left", "rear_right")
        }
        e.coordinator.data = state(window=positions)
        self.assertTrue(e.is_closed)
        positions.pop("front_left_window_position")
        self.assertIsNone(e.is_closed)
        positions["front_left_window_position"] = 95
        self.assertFalse(e.is_closed)
        for bad in (True, -1, 101, "bad", float("nan")):
            positions["front_left_window_position"] = bad
            self.assertIsNone(e.is_closed)

    async def test_manual_charge_port_keeps_state_without_commands(self):
        e, send = self.make(
            "charge_port",
            state(charge={"slow_charge_port_status": 0}),
            charge_port_controls=False,
        )
        self.assertTrue(e.is_closed)
        self.assertEqual(e.supported_features, 0)
        with self.assertRaises(Error) as exc:
            await e.async_open_cover()
        self.assertEqual(exc.exception.key, "charge_port_control_disabled")
        send.assert_not_called()

    async def test_model_detected_manual_port_keeps_state_but_blocks_motion(self):
        e, send = self.make("charge_port", state(charge={"slow_charge_port_status": 0}))
        e.coordinator.vehicle_type_code = "FJ"
        e.coordinator.vehicle_type_name = "F57a"
        self.assertTrue(e.is_closed)
        self.assertEqual(e.supported_features, 0)
        with self.assertRaises(Error) as exc:
            await e.async_open_cover()
        self.assertEqual(exc.exception.key, "charge_port_control_disabled")
        send.assert_not_called()

    async def test_motorized_port_uses_existing_pair_and_keeps_connected_detail(self):
        e, send = self.make(
            "charge_port",
            state(charge={"charge_gun_status": 4, "slow_charge_port_status": 0}),
        )
        self.assertFalse(e.is_closed)
        self.assertEqual(e.extra_state_attributes["port_state"], "Connected")
        await e.async_open_cover()
        send.assert_called_with(VIN, 4, {"cmd_type": 3, "cmd_value": 1}, 2)
        await e.async_close_cover()
        send.assert_called_with(VIN, 4, {"cmd_type": 3, "cmd_value": 0}, 2)


class ChargingTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_uses_measured_state_and_validates_acceptance(self):
        send = Mock(
            return_value={
                "service_type": 4,
                "msg_type": 1,
                "msg_content": {"code": 200},
            }
        )
        c = coordinator()
        e = switch.XPengChargingSwitch(c, SimpleNamespace(send_command_wait=send))
        self.assertIsNone(e.is_on)
        c.data = state(charge={"charging_state": 0})
        self.assertFalse(e.is_on)
        await e.async_turn_on()
        send.assert_called_with(VIN, 4, {"cmd_type": 1, "cmd_value": 1}, 2)
        self.assertFalse(e.is_on)
        c.data = state(charge={"charging_state": 2})
        self.assertTrue(e.is_on)
        c.data = {
            "msg_content": {
                "instant": {"charge": {"charging_state": 15}},
                "interval": {"charge": {"power": 5.2}},
            }
        }
        self.assertTrue(e.is_on)
        send.return_value = None
        with self.assertRaises(Error):
            await e.async_turn_off()
        send.assert_called_with(VIN, 4, {"cmd_type": 1, "cmd_value": 0}, 2)
        c.command_policy = policy.CommandPolicy.from_preset("read_only")
        count = send.call_count
        with self.assertRaises(Error):
            await e.async_turn_on()
        self.assertEqual(send.call_count, count)

    async def test_rejection_is_visible_and_does_not_turn_charging_on(self):
        send = Mock(
            return_value={
                "service_type": 4,
                "msg_type": 1,
                "msg_content": {"code": 10401},
            }
        )
        e = switch.XPengChargingSwitch(
            coordinator(state(charge={"charging_state": 0})),
            SimpleNamespace(send_command_wait=send),
        )
        with self.assertRaises(Error) as exc:
            await e.async_turn_on()
        self.assertEqual(exc.exception.key, "charging_start_rejected")
        self.assertFalse(e.is_on)
        self.assertEqual(e.extra_state_attributes["charging_state"], "Disconnected")
        self.assertFalse(e.extra_state_attributes["cable_connected"])

    async def test_malformed_response_is_not_acceptance(self):
        responses = [
            False,
            {},
            {"service_type": 12, "msg_type": 1, "msg_content": {"code": 200}},
            {"service_type": 4, "msg_type": 2, "msg_content": {"code": 200}},
            {"service_type": 4, "msg_type": 1, "msg_content": {"code": True}},
            {"service_type": 4, "msg_type": 1, "msg_content": "bad"},
        ]
        for response in responses:
            e = switch.XPengChargingSwitch(
                coordinator(),
                SimpleNamespace(send_command_wait=Mock(return_value=response)),
            )
            with self.subTest(response=response), self.assertRaises(Error) as exc:
                await e.async_turn_off()
            self.assertEqual(exc.exception.key, "command_response_invalid")

    async def test_charge_flap_rejection_is_not_mislabeled_as_session_start(self):
        e, send = CoverTests.make(self, "charge_port")
        send.return_value = {
            "service_type": 4,
            "msg_type": 1,
            "msg_content": {"code": 10401},
        }
        with self.assertRaises(Error) as exc:
            await e.async_open_cover()
        self.assertEqual(exc.exception.key, "vehicle_command_rejected")

    async def test_same_subsystem_commands_wait_their_turn(self):
        started, release = asyncio.Event(), asyncio.Event()
        seen = []

        async def executor(fn, *args):
            seen.append(args[2]["cmd_value"])
            started.set()
            await release.wait()
            return {"service_type": 4, "msg_type": 1, "msg_content": {"code": 200}}

        e = switch.XPengChargingSwitch(
            coordinator(), SimpleNamespace(send_command_wait=Mock())
        )
        e.hass.async_add_executor_job = executor
        first = asyncio.create_task(e.async_turn_on())
        await started.wait()
        second = asyncio.create_task(e.async_turn_off())
        await asyncio.sleep(0)
        self.assertEqual(seen, [1])
        release.set()
        await asyncio.gather(first, second)
        self.assertEqual(seen, [1, 0])


class LayoutTests(unittest.TestCase):
    def test_new_entries_are_compact_old_entries_keep_ids(self):
        self.assertFalse(
            layout.legacy_entities_enabled(SimpleNamespace(version=2, options={}))
        )
        self.assertTrue(
            layout.legacy_entities_enabled(SimpleNamespace(version=1, options={}))
        )
        self.assertTrue(layout.legacy_entities_enabled(entry(True)))
        self.assertFalse(layout.legacy_entities_enabled(entry(False, 1)))

    def test_only_duplicates_are_removed_and_disabled_policy_keeps_state(self):
        e = entry()
        p = policy.CommandPolicy.from_preset("full")
        self.assertFalse(
            layout.include_legacy_entity(e, p, "sensor", "driver_seat_heat")
        )
        self.assertFalse(layout.include_legacy_entity(e, p, "sensor", "max_charge"))
        self.assertTrue(
            layout.include_legacy_entity(e, p, "sensor", "charge_limit")
        )  # amperes, not SOC
        self.assertTrue(
            layout.include_legacy_entity(e, p, "sensor", "cabin_temperature")
        )
        p = policy.CommandPolicy(
            groups=policy.normalize_groups({policy.GROUP_COMFORT: policy.PERM_DISABLED})
        )
        self.assertTrue(
            layout.include_legacy_entity(e, p, "sensor", "driver_seat_heat")
        )

    def test_registry_preserves_naming_and_only_prunes_own_replaced_entities(self):
        records = {}
        old = SimpleNamespace(
            entity_id="binary_sensor.my_car_trunk",
            unique_id=f"xpeng_{VIN}_trunk",
            config_entry_id="entry",
            device_id="device",
            name="Boot",
            icon=None,
            area_id="garage",
            labels={"car"},
            aliases=["Boot"],
        )
        records[old.entity_id] = old
        other = SimpleNamespace(
            entity_id="sensor.other",
            unique_id=f"xpeng_{VIN}_driver_seat_heat",
            config_entry_id="other",
        )
        records[other.entity_id] = other

        class Registry:
            def async_get_entity_id(self, domain, platform, unique_id):
                return next(
                    (
                        k
                        for k, v in records.items()
                        if k.split(".")[0] == domain and v.unique_id == unique_id
                    ),
                    None,
                )

            def async_get(self, eid):
                return records.get(eid)

            def async_get_or_create(self, domain, platform, unique_id, **kwargs):
                eid = f'{domain}.{kwargs["suggested_object_id"]}'
                r = SimpleNamespace(
                    entity_id=eid,
                    unique_id=unique_id,
                    config_entry_id=kwargs["config_entry"].entry_id,
                )
                records[eid] = r
                return r

            def async_update_entity(self, eid, **kwargs):
                records[eid].__dict__.update(kwargs)

            def async_remove(self, eid):
                del records[eid]

        registry = Registry()
        module("homeassistant.helpers.entity_registry", async_get=lambda hass: registry)
        layout.prepare_compact_registry(
            object(), entry(True), VIN, policy.CommandPolicy.from_preset("full")
        )
        self.assertIn(old.entity_id, records)
        self.assertEqual(records["cover.my_car_trunk"].name, "Boot")
        layout.prepare_compact_registry(
            object(), entry(), VIN, policy.CommandPolicy.from_preset("full")
        )
        self.assertNotIn(old.entity_id, records)
        self.assertIn("sensor.other", records)
        new = records["cover.my_car_trunk"]
        self.assertEqual(
            (new.name, new.area_id, new.labels), ("Boot", "garage", {"car"})
        )
        # A second setup leaves an already-renamed controller alone.
        layout.prepare_compact_registry(
            object(), entry(), VIN, policy.CommandPolicy.from_preset("full")
        )
        self.assertEqual(new.name, "Boot")

    def test_entry_migration_preserves_options_and_is_idempotent(self):
        tree = ast.parse((PKG / "__init__.py").read_text())
        fn = next(
            n
            for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "async_migrate_entry"
        )
        ns = {
            "__name__": "custom_components.xpeng.test_migration",
            "HomeAssistant": object,
            "ConfigEntry": object,
        }
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "migration", "exec"), ns)
        e = SimpleNamespace(version=1, options={"scan_interval": 60})

        def update(obj, **kwargs):
            obj.__dict__.update(kwargs)

        h = SimpleNamespace(
            config_entries=SimpleNamespace(async_update_entry=Mock(side_effect=update))
        )
        self.assertTrue(asyncio.run(ns["async_migrate_entry"](h, e)))
        self.assertEqual(e.options, {"scan_interval": 60, "legacy_entities": True})
        self.assertEqual(e.version, 2)
        self.assertTrue(asyncio.run(ns["async_migrate_entry"](h, e)))
        h.config_entries.async_update_entry.assert_called_once()


class LegacyAndOptionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_window_buttons_use_the_correct_subsystem(self):
        module(
            "homeassistant.components.button", ButtonEntity=type("ButtonEntity", (), {})
        )
        sys.modules["custom_components.xpeng.const"].COMMAND_NOT_SENT_MESSAGE = (
            "not sent"
        )
        buttons = load("custom_components.xpeng.button", "button.py")
        send = Mock(return_value=True)
        c = coordinator()
        h = SimpleNamespace(
            data={
                "xpeng": {
                    "entry": {
                        "coordinator": c,
                        "mqtt_client": SimpleNamespace(send_command=send),
                        "command_policy": c.command_policy,
                    }
                }
            }
        )
        entities = []
        await buttons.async_setup_entry(h, entry(True), entities.extend)
        by_key = {e._attr_translation_key: e for e in entities}
        self.assertIn("open_trunk", by_key)
        await by_key["close_windows"].async_press()
        send.assert_called_with(VIN, 12, {"cmd_type": 1, "cmd_value": 1}, 2)
        await by_key["vent_windows"].async_press()
        send.assert_called_with(VIN, 12, {"cmd_type": 1, "cmd_value": 2}, 2)
        compact = []
        await buttons.async_setup_entry(h, entry(False), compact.extend)
        self.assertEqual(
            {e._attr_translation_key for e in compact},
            {
                "flash_lights",
                "honk_horn",
                "force_update",
                "wake_vehicle",
                "query_xguard",
            },
        )

    async def test_legacy_services_cannot_send_charge_port_payload_for_windows(self):
        tree = ast.parse((PKG / "services.py").read_text())
        nodes = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef)
            and n.name in {"handle_close_windows", "handle_vent_windows"}
        ]
        send = Mock()

        async def executor(fn, *args):
            return fn(*args)

        h = SimpleNamespace(async_add_executor_job=executor)
        ns = {
            "hass": h,
            "_send": send,
            "ServiceCall": object,
            "HomeAssistantError": Error,
            "DOMAIN": "xpeng",
        }
        exec(
            compile(ast.Module(body=nodes, type_ignores=[]), "window_services", "exec"),
            ns,
        )
        await ns["handle_close_windows"](None)
        send.assert_called_with(h, 12, {"cmd_type": 1, "cmd_value": 1})
        await ns["handle_vent_windows"](None)
        send.assert_called_with(h, 12, {"cmd_type": 1, "cmd_value": 2})

    async def test_partial_options_preserve_credentials_policy_and_wake_limit(self):
        from typing import Any

        tree = ast.parse((PKG / "config_flow.py").read_text())
        cls = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "XPengOptionsFlow"
        )
        fn = next(
            n
            for n in cls.body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "async_step_init"
        )
        ns = {
            "__name__": "custom_components.xpeng.test_options",
            "Any": Any,
            "ConfigFlowResult": dict,
            "CommandPolicy": policy.CommandPolicy,
            "CONF_CERT_DIR": "cert_dir",
            "CONF_COMMAND_MODE": "command_mode",
            "CONF_ALLOW_WAKE": "allow_wake",
            "DEFAULT_COMMAND_MODE": "safe_plus",
            "PRESET_CUSTOM": policy.PRESET_CUSTOM,
        }
        sys.modules["custom_components.xpeng.mqtt_client"].missing_cert_files = (
            lambda path: []
        )
        exec(
            compile(ast.Module(body=[fn], type_ignores=[]), "options_flow", "exec"), ns
        )
        stored = {
            **policy.CommandPolicy.from_preset("full", allow_wake=False).to_options(),
            "token_file": "/config/private/token.json",
            "scan_interval": 90,
            "legacy_entities": True,
        }
        f = SimpleNamespace(
            config_entry=SimpleNamespace(options=stored),
            _validate_teslamate=lambda inp: {},
            async_create_entry=lambda **kwargs: kwargs,
        )
        result = await ns["async_step_init"](
            f, {"legacy_entities": False, "charge_port_controls": False}
        )
        self.assertEqual(result["data"]["token_file"], stored["token_file"])
        self.assertEqual(result["data"]["scan_interval"], 90)
        self.assertFalse(result["data"]["allow_wake"])
        self.assertFalse(result["data"]["legacy_entities"])


if __name__ == "__main__":
    unittest.main()
