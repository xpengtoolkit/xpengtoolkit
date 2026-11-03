#!/usr/bin/env python3
"""Pin the ampere-limit control that only ``send_raw_mqtt`` could reach before.

Two facts this file protects, both measured on a live G6 (VIN tail 800783) on
2026-11-03:

* ``service_type 4 / cmd_type 7`` sets the **AC charge current limit in
  amperes** - the app's ``setChargeElectricityLimit``. Sending
  ``{"cmd_type": 7, "cmd_value": 16}`` returned code 200 and moved
  ``instant.charge.electric_current_limit`` from 27 to 16 once the car next
  uploaded state. It is *not* an A/C power toggle, which is what its name
  invites people to assume; A/C power is ``service 3 / cmd_type 1``.
* That command's acknowledgement does **not** carry ``electric_current_limit``.
  The reply was ``{"charge_status": 15, "slow_charge_port_status": 2,
  "fast_charge_port_status": 2, "max_range_charge": 90,
  "battery_keep_warm_status": 0}`` - so folding a service-4 answer into cached
  state cannot confirm an ampere change, and the entity must keep reading the
  telemetry field instead of pretending the value moved.

The trap the class exists to avoid: ``number.xpeng_<vin>_charge_limit`` sounds
like the same control and is not. It writes ``cmd_type 2`` and takes a
percentage (50-100). Reusing its translation key, unit or command would either
silently set the wrong thing or make two entities fight over one id.

Run from the repo root:

    python3 tests/test_charge_current_limit.py
"""

from __future__ import annotations

from _import_isolation import ImportIsolation
_IMPORTS = ImportIsolation()

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from types import SimpleNamespace

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


class Base:
    """Stand-in for XPengEntity: enough policy surface to exercise the guard."""

    #: Mirrors entity.XPengEntity._policy_groups: empty means "use _policy_group".
    _policy_groups: tuple = ()

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
        groups = self._policy_groups or (
            (self._policy_group,) if self._policy_group else ()
        )
        return not groups or any(policy.exposes(g) for g in groups)

    async def executor(self, fn, *args):
        return fn(*args)


module("custom_components", __path__=[])
module("custom_components.xpeng", __path__=[str(PKG)])
policy = load("custom_components.xpeng.command_policy", "command_policy.py")
load("custom_components.xpeng.entity_layout", "entity_layout.py")
load("custom_components.xpeng.charge_port", "charge_port.py")

module("homeassistant", __path__=[])
module("homeassistant.components", __path__=[])
module(
    "homeassistant.components.number",
    NumberEntity=type("NumberEntity", (), {}),
    NumberMode=SimpleNamespace(SLIDER="slider", BOX="box"),
)
module("homeassistant.config_entries", ConfigEntry=object)
module("homeassistant.core", HomeAssistant=object)
module("homeassistant.exceptions", HomeAssistantError=Error)
module("homeassistant.helpers", __path__=[])
module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
module("custom_components.xpeng.const", DOMAIN="xpeng", UNIT_AMPERE="A")
module("custom_components.xpeng.coordinator", XPengDataCoordinator=object)
module("custom_components.xpeng.entity", XPengEntity=Base)
module("custom_components.xpeng.mqtt_client", XPengMQTTClient=object)
execution = module("custom_components.xpeng.command_execution")

#: Recorded calls, so a test can assert what was sent without a real client.
SENT = []


async def _record(hass, client, vin, service_type, content, msg_type=2):
    SENT.append((vin, service_type, content, msg_type))
    if client.fail_with is not None:
        raise client.fail_with
    return {"service_type": service_type, "msg_type": 1,
            "msg_content": {"code": 200}}


execution.async_send_confirmed = _record
number = load("custom_components.xpeng.number", "number.py")

VIN = "SYNTHETIC123456789"

#: The charge section as the car reports it around an ampere-limit change.
BEFORE = {"electric_current_limit": 27, "max_range_charge": 90}
AFTER = {"electric_current_limit": 16, "max_range_charge": 90}

#: Verbatim acknowledgement for cmd_type 7, measured 2026-11-03. Note what is
#: missing: no electric_current_limit.
ACK = {
    "msg": "Erfolgreich",
    "code": 200,
    "charge_status": 15,
    "battery_keep_warm_status": 0,
    "slow_charge_port_status": 2,
    "fast_charge_port_status": 2,
    "max_range_charge": 90,
}


def coordinator(data=None, p=None):
    return SimpleNamespace(
        vin=VIN, data=data, command_policy=p or policy.CommandPolicy.from_preset("full")
    )


def state(charge):
    return {"msg_content": {"instant": {"charge": charge}}}


def client(fail_with=None):
    """A stand-in MQTT client; only the failure knob is ever read."""
    return SimpleNamespace(fail_with=fail_with)


def make(value=AFTER, p=None):
    return number.XPengChargeCurrentLimit(coordinator(state(value), p), client())


class ChargeCurrentLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SENT.clear()

    def test_it_is_a_separate_control_from_the_percentage_limit(self):
        """Distinct translation key, unique id, command and unit."""
        amps = make()
        pct = number.XPengChargeLimit(coordinator(state(AFTER)), client())
        self.assertNotEqual(amps._attr_translation_key, pct._attr_translation_key)
        self.assertNotEqual(amps._attr_unique_id, pct._attr_unique_id)
        self.assertEqual(amps._attr_native_unit_of_measurement, "A")
        self.assertEqual(pct._attr_native_unit_of_measurement, "%")

    async def test_the_two_controls_send_different_commands(self):
        """The name trap: same subsystem, different cmd_type, different units."""
        amps = make()
        await amps.async_set_native_value(16)
        pct = number.XPengChargeLimit(coordinator(state(AFTER)), client())
        await pct.async_set_native_value(80)
        self.assertIn((VIN, 4, {"cmd_type": 7, "cmd_value": 16}, 2), SENT)
        self.assertIn((VIN, 4, {"cmd_type": 2, "cmd_value": 80}, 2), SENT)
        self.assertEqual(len(SENT), 2)

    def test_acknowledgement_omits_the_ampere_field(self):
        """Why the entity cannot confirm an ampere change from the reply.

        Verbatim service-4 answer to ``cmd_type 7``, measured 2026-11-03. It
        reports percentages and port states, never ``electric_current_limit``,
        so ``_merge_command_state`` has nothing to fold in for this control.
        """
        self.assertNotIn("electric_current_limit", ACK)
        self.assertEqual(ACK["max_range_charge"], 90)
        self.assertEqual(ACK["code"], 200)

    def test_reads_the_measured_ampere_field(self):
        self.assertEqual(make().native_value, 16)
        self.assertEqual(make(value=BEFORE).native_value, 27)

    def test_no_state_means_no_invented_value(self):
        e = number.XPengChargeCurrentLimit(coordinator(None), client())
        self.assertIsNone(e.native_value)

    def test_empty_charge_section_is_not_zero_amps(self):
        e = number.XPengChargeCurrentLimit(
            coordinator({"msg_content": {"instant": {}}}), client()
        )
        self.assertIsNone(e.native_value)

    async def test_sets_amperes_with_cmd_type_7(self):
        e = make()
        await e.async_set_native_value(16)
        self.assertEqual(SENT, [(VIN, 4, {"cmd_type": 7, "cmd_value": 16}, 2)])

    async def test_the_value_follows_telemetry_not_the_publication(self):
        """A successful send must not make the entity claim the new amps."""
        e = make(value=BEFORE)
        await e.async_set_native_value(16)
        # The ack carries no electric_current_limit, so nothing merged it:
        # the entity still reads 27 until the car uploads state.
        self.assertEqual(e.native_value, 27)
        e.coordinator.data = state(AFTER)
        self.assertEqual(e.native_value, 16)

    async def test_refusal_propagates_instead_of_looking_like_success(self):
        """The whole point of the entity over send_raw_mqtt: errors surface."""
        for refusal in (Error("nope"), Error("rejected", translation_key="x")):
            e = make()
            e._mqtt_client = client(fail_with=refusal)
            with self.assertRaises(Error):
                await e.async_set_native_value(16)

    async def test_read_only_policy_refuses_without_sending(self):
        e = make(p=policy.CommandPolicy.from_preset("read_only"))
        with self.assertRaises(Error):
            await e.async_set_native_value(16)
        self.assertEqual(SENT, [])

    def test_read_only_permission_still_creates_the_entity(self):
        """A number's value is worth showing, so READ_ONLY keeps it visible.

        Only DISABLED withdraws the entity. The ``read_only`` *preset* disables
        every group but queries, so it is not the case to test here - this uses
        a custom policy with charging at read-only.
        """
        ro = policy.CommandPolicy(
            groups={**policy.PRESETS[policy.PRESET_FULL],
                    policy.GROUP_CHARGING: policy.PERM_READ_ONLY}
        )
        e = make(p=ro)
        self.assertTrue(e._policy_visible(ro))

    def test_disabled_charging_withdraws_the_entity(self):
        p = policy.CommandPolicy(
            groups={**policy.PRESETS[policy.PRESET_FULL],
                    policy.GROUP_CHARGING: policy.PERM_DISABLED}
        )
        self.assertFalse(make(p=p)._policy_visible(p))


    def test_range_is_a_plausible_ac_window(self):
        e = make()
        self.assertLess(e._attr_native_min_value, e._attr_native_max_value)
        self.assertEqual(e._attr_native_step, 1)
        # 6..32 A covers single-phase 16 A/32 A sockets and the observed 27 A.
        for observed in (16, 27):
            self.assertGreaterEqual(observed, e._attr_native_min_value)
            self.assertLessEqual(observed, e._attr_native_max_value)


class PlatformRegistrationTests(unittest.TestCase):
    """The entity has to be constructed by the platform, not just defined."""

    def source(self):
        return (PKG / "number.py").read_text()

    def test_it_is_instantiated_in_async_setup_entry(self):
        src = self.source()
        self.assertIn("XPengChargeCurrentLimit(coordinator, mqtt_client)", src)

    def test_translation_key_exists_in_every_language(self):
        import json

        en = json.loads((PKG / "translations/en.json").read_text())
        keys = en["entity"]["number"]
        self.assertIn("charge_current_limit_control", keys)
        for name in ("strings.json",):
            other = json.loads((PKG / name).read_text())
            self.assertEqual(
                other["entity"]["number"]["charge_current_limit_control"],
                keys["charge_current_limit_control"],
            )
        for lang in ("de", "nl", "nb", "da", "sv", "fr"):
            d = json.loads((PKG / f"translations/{lang}.json").read_text())
            self.assertIn("charge_current_limit_control", d["entity"]["number"])

    def test_english_name_table_agrees_with_en_json(self):
        names = load("custom_components.xpeng.entity_names", "entity_names.py")
        self.assertEqual(
            names.ENGLISH_ENTITY_NAMES["number"]["charge_current_limit_control"],
            "Charge current limit",
        )

    def test_the_shared_name_does_not_collide_with_the_sensor(self):
        """The number and the sensor are both "Charge current limit" by design.

        ``sensor.xpeng_<vin>_charge_limit`` (whose English name is already
        "Charge current limit") and this control carry the same displayed name,
        so what has to differ is the id: the registry entry keys on
        ``unique_id``, not on the name, and a collision there would make one of
        the two entities un-addable.
        """
        names = load("custom_components.xpeng.entity_names", "entity_names.py")
        self.assertEqual(
            names.ENGLISH_ENTITY_NAMES["sensor"]["charge_limit"],
            names.ENGLISH_ENTITY_NAMES["number"]["charge_current_limit_control"],
        )
        # unique_ids: xpeng_<vin>_charge_limit vs xpeng_<vin>_charge_current_limit_control
        sensor_uid = f"xpeng_{VIN}_charge_limit"
        number_uid = f"xpeng_{VIN}_charge_current_limit_control"
        self.assertNotEqual(sensor_uid, number_uid)
        # The object id HA suggests comes from the English name, which is shared,
        # so it is the platform that separates them - hence both must exist.
        self.assertEqual(make()._attr_unique_id, number_uid)


_IMPORTS.finish(globals())

if __name__ == "__main__":
    unittest.main()
