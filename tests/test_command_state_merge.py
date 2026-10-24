#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A command's own acknowledgement is a state update. Standard library only.

    python3 tests/test_command_state_merge.py
    python3 -m unittest discover -s tests -v

Measured on a live car 2026-10-13, driving the charge-limit entity from Home
Assistant. The command went out at 20:51:01:

    Sent command: service_type=4 msg_content={'cmd_type': 2, 'cmd_value': 90}

and the answer arrived 1.4 seconds later:

    Command response: service_type=4 msg_type=1 content={
        "msg": "Erfolgreich", "code": 200, "charge_status": 15,
        "slow_charge_port_status": 2, "fast_charge_port_status": 2,
        "max_range_charge": 90}

Those keys are the ones ``instant.charge`` already holds, which is what makes
the acknowledgement usable as the confirmation: the charge limit entity reads
``msg_content.instant.charge.max_range_charge``, so folding the answer in is
what makes a change visible without waking the car.

Before that, the entity showed whatever the last full snapshot said. A drag that
landed in one of the watchdog's reconnect windows was dropped entirely (there is
exactly one ``Cannot send command: not connected`` in the log and no
``service_type=4`` publish at all), and the card re-rendered the old number.
What is pinned here is the merging half; ``number.py`` is not importable without
a full Home Assistant, so the entity's read path is asserted structurally.

``coordinator.py`` is loaded with stubbed Home Assistant modules, because the
behaviour under test is a method on a class that inherits one.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402

PKG_DIR = _component.component_dir()
_component.assert_is_default_target()


# -- a Home Assistant just big enough to import coordinator.py -------------


class _CoordinatorBase:
    """Stand-in for DataUpdateCoordinator (subscriptable, never constructed)."""

    def __class_getitem__(cls, item):  # noqa: N805
        return cls

    def __init__(self, *args, **kwargs):
        pass

    def async_set_updated_data(self, data):
        self.data = data


def _install_ha_stubs():
    ha = types.ModuleType("homeassistant")

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    core.callback = lambda fn: fn

    helpers = types.ModuleType("homeassistant.helpers")
    storage = types.ModuleType("homeassistant.helpers.storage")
    storage.Store = type("Store", (), {})
    upd = types.ModuleType("homeassistant.helpers.update_coordinator")
    upd.DataUpdateCoordinator = _CoordinatorBase

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.storage"] = storage
    sys.modules["homeassistant.helpers.update_coordinator"] = upd
    ha.core = core
    ha.helpers = helpers


def _load_coordinator():
    """Load the real coordinator.py against the stubs above."""
    _install_ha_stubs()

    pkg = types.ModuleType("custom_components")
    pkg.__path__ = []
    xpkg = types.ModuleType("custom_components.xpeng")
    xpkg.__path__ = [str(PKG_DIR)]
    sys.modules["custom_components"] = pkg
    sys.modules["custom_components.xpeng"] = xpkg

    # The real const.py needs HA's entity-description dataclasses; the
    # coordinator only wants DOMAIN out of it.
    const = types.ModuleType("custom_components.xpeng.const")
    const.DOMAIN = "xpeng"
    sys.modules["custom_components.xpeng.const"] = const

    for name in ("command_policy", "vin_descriptor", "coordinator"):
        spec = importlib.util.spec_from_file_location(
            f"custom_components.xpeng.{name}", PKG_DIR / f"{name}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"custom_components.xpeng.{name}"] = mod
        spec.loader.exec_module(mod)

    return sys.modules["custom_components.xpeng.coordinator"]


coordinator = _load_coordinator()
CHARGE = coordinator.CHARGE_SERVICE_TYPE


def _coordinator_with(cached):
    """An instance with just the attributes the merge touches."""
    coord = object.__new__(coordinator.XPengDataCoordinator)
    coord.vin = "L1NNSGHA3SB123456"
    coord._last_full_data = cached
    coord._baseline_sink = None
    return coord


def _cached_state():
    return {
        "msg_content": {
            "instant": {
                "charge": {
                    "battery_soc": 23,
                    "max_range_charge": 90,
                    "charging_state": "Scheduled",
                },
                "door": {"driver_door_lock_st": 1},
            },
            "interval": {"charge": {"power": 0}},
        },
        "vin": "L1NNSGHA3SB123456",
    }


def _charge_ack(**content):
    """The shape mqtt_client._fire_command_feedback hands the coordinator."""
    body = {"msg": "Erfolgreich", "code": 200}
    body.update(content)
    return {
        "service_type": CHARGE,
        "msg_type": 1,
        "msg_id": "11103488591791885061448008",
        "code": 200,
        "message": "Erfolgreich",
        "content": body,
    }


#: The exact payload the live car answered a set-charge-limit with.
LIVE_ACK = dict(
    charge_status=15,
    slow_charge_port_status=2,
    fast_charge_port_status=2,
    battery_keep_warm_status=0,
    max_range_charge=90,
)


class TestAChargeAckUpdatesTheState(unittest.TestCase):
    def test_the_acknowledgement_moves_the_charge_limit(self):
        coord = _coordinator_with(_cached_state())
        self.assertTrue(
            coord._merge_command_state(
                _charge_ack(**{**LIVE_ACK, "max_range_charge": 100})
            )
        )

        charge = coord._last_full_data["msg_content"]["instant"]["charge"]
        self.assertEqual(charge["max_range_charge"], 100)
        # Every other field the car reported comes with it...
        self.assertEqual(charge["charge_status"], 15)
        self.assertEqual(charge["slow_charge_port_status"], 2)
        self.assertEqual(charge["fast_charge_port_status"], 2)
        self.assertEqual(charge["battery_keep_warm_status"], 0)

    def test_fields_the_ack_does_not_carry_are_kept(self):
        coord = _coordinator_with(_cached_state())
        coord._merge_command_state(_charge_ack(max_range_charge=100))

        instant = coord._last_full_data["msg_content"]["instant"]
        self.assertEqual(instant["charge"]["battery_soc"], 23)
        self.assertEqual(instant["charge"]["charging_state"], "Scheduled")
        # Other sections and the interval block are untouched.
        self.assertEqual(instant["door"], {"driver_door_lock_st": 1})
        self.assertEqual(
            coord._last_full_data["msg_content"]["interval"],
            {"charge": {"power": 0}},
        )
        self.assertEqual(coord._last_full_data["vin"], coord.vin)

    def test_the_entity_read_path_answers_the_new_limit(self):
        """``XPengChargeLimit.native_value`` walks exactly this path."""
        coord = _coordinator_with(_cached_state())
        coord._merge_command_state(_charge_ack(max_range_charge=95))

        value = coord._last_full_data["msg_content"]["instant"]["charge"].get(
            "max_range_charge"
        )
        self.assertEqual(value, 95)

    def test_code_and_msg_are_not_merged_as_charge_fields(self):
        coord = _coordinator_with(_cached_state())
        coord._merge_command_state(_charge_ack(max_range_charge=100))

        charge = coord._last_full_data["msg_content"]["instant"]["charge"]
        self.assertNotIn("code", charge)
        self.assertNotIn("msg", charge)

    def test_the_cached_state_object_is_replaced_not_mutated_in_place(self):
        """The coordinator re-publishes by identity; a new dict is the signal."""
        cached = _cached_state()
        coord = _coordinator_with(cached)
        coord._merge_command_state(_charge_ack(max_range_charge=100))

        self.assertIsNot(coord._last_full_data, cached)
        self.assertEqual(
            cached["msg_content"]["instant"]["charge"]["max_range_charge"], 90
        )


class TestWindowFeedback(unittest.TestCase):
    def test_window_positions_merge_without_advancing_snapshot_time(self):
        original = _cached_state()
        original["timestamp"] = 123
        coord = _coordinator_with(original)
        body = {
            f"{side}_window_position": value
            for side, value in (
                ("front_left", 90),
                ("front_right", 90),
                ("rear_left", 82),
                ("rear_right", 82),
            )
        }
        ack = {"service_type": 12, "code": 200, "content": {"code": 200, **body}}
        self.assertTrue(coord._merge_command_state(ack))
        self.assertEqual(
            coord._last_full_data["msg_content"]["instant"]["window"], body
        )
        self.assertEqual(coord._last_full_data["timestamp"], 123)
        self.assertNotIn("window", original["msg_content"]["instant"])
        self.assertEqual(
            coord._last_full_data["msg_content"]["instant"]["charge"],
            original["msg_content"]["instant"]["charge"],
        )
        ack["code"] = 8
        self.assertFalse(coord._merge_command_state(ack))


class TestDoorAndSteeringFeedback(unittest.TestCase):
    def test_measured_access_and_steering_fields_update_the_shared_baseline_only(self):
        for service, section, field, value in ((9, "door", "trunk_ajar_st_new", 5),
                                                (31, "steerWheel", "steering_wheel_heat", 3)):
            with self.subTest(service=service):
                original = _cached_state()
                original["timestamp"] = 123
                coord = _coordinator_with(original)
                coord.last_data_timestamp = 456
                adopted = []
                coord._baseline_sink = adopted.append
                ack = {"service_type": service, "code": 200, "content": {"code": 200, field: value}}
                self.assertTrue(coord._merge_command_state(ack))
                self.assertEqual(coord._last_full_data["msg_content"]["instant"][section][field], value)
                self.assertEqual(coord._last_full_data["timestamp"], 123)
                self.assertEqual(coord.last_data_timestamp, 456)
                self.assertIs(adopted[0], coord._last_full_data["msg_content"])
                self.assertNotIn(field, original["msg_content"]["instant"].get(section, {}))
                self.assertEqual(coord._last_full_data["msg_content"]["instant"]["charge"], original["msg_content"]["instant"]["charge"])
                ack["code"] = 8
                ack["content"][field] = 0
                self.assertFalse(coord._merge_command_state(ack))
                self.assertEqual(coord._last_full_data["msg_content"]["instant"][section][field], value)


class TestClimateResponseUpdatesMeasuredState(unittest.TestCase):
    def test_climate_reply_updates_only_reported_fields_without_a_new_snapshot_time(self):
        original = _cached_state()
        original["timestamp"] = 123
        original["msg_content"]["instant"]["hvac"] = {
            "hvac_on": 0, "hvac_temp_float": 22.5, "hvac_inner_temp_float": 26,
        }
        coord = _coordinator_with(original)
        ack = {"service_type": 3, "code": 200, "content": {
            "code": 200, "msg": "Succeeded", "hvac_on": 1, "hvac_temp_float": 24.5,
        }}
        self.assertTrue(coord._merge_command_state(ack))
        hvac = coord._last_full_data["msg_content"]["instant"]["hvac"]
        self.assertEqual(hvac, {"hvac_on": 1, "hvac_temp_float": 24.5, "hvac_inner_temp_float": 26})
        self.assertEqual(coord._last_full_data["timestamp"], 123)
        self.assertEqual(original["msg_content"]["instant"]["hvac"]["hvac_on"], 0)
        ack["code"] = 8
        ack["content"]["hvac_temp_float"] = 30
        self.assertFalse(coord._merge_command_state(ack))
        self.assertEqual(hvac["hvac_temp_float"], 24.5)


class TestWhatIsNotMerged(unittest.TestCase):
    def test_a_refusal_is_not_state(self):
        coord = _coordinator_with(_cached_state())
        ack = _charge_ack(max_range_charge=100)
        ack["code"] = 500
        ack["content"]["code"] = 500
        self.assertFalse(coord._merge_command_state(ack))
        self.assertEqual(
            coord._last_full_data["msg_content"]["instant"]["charge"][
                "max_range_charge"
            ],
            90,
        )

    def test_another_subsystems_response_is_not_charge_state(self):
        """Service 9 (door) answers with door fields, not charge fields."""
        coord = _coordinator_with(_cached_state())
        ack = _charge_ack(driver_door_lock_st=0, trunk_ajar_st_new=5)
        ack["service_type"] = 9
        self.assertTrue(coord._merge_command_state(ack))
        self.assertEqual(coord._last_full_data["msg_content"]["instant"]["door"]["trunk_ajar_st_new"], 5)
        self.assertEqual(
            coord._last_full_data["msg_content"]["instant"]["charge"][
                "max_range_charge"
            ],
            90,
        )

    def test_a_response_with_nothing_but_a_status_is_not_merged(self):
        coord = _coordinator_with(_cached_state())
        ack = _charge_ack()
        ack["content"] = {"code": 200, "msg": "Erfolgreich"}
        self.assertFalse(coord._merge_command_state(ack))

    def test_a_malformed_response_is_not_merged(self):
        coord = _coordinator_with(_cached_state())
        ack = _charge_ack(max_range_charge=100)
        ack["content"] = "Erfolgreich"
        self.assertFalse(coord._merge_command_state(ack))

    def test_nothing_cached_means_nothing_to_merge_into(self):
        """A command ack must not become the first 'full' snapshot.

        Sensors read `msg_content.instant.*`; publishing a snapshot built from
        one command's answer would blank every field that command did not
        mention.
        """
        coord = _coordinator_with(None)
        self.assertFalse(coord._merge_command_state(_charge_ack(max_range_charge=100)))
        self.assertFalse(coord._last_full_data)


class TestOnlyTheChargeSubsystem(unittest.TestCase):
    def test_the_charge_service_type_is_four(self):
        """Pinned against services.py: service_type 4 is the charge port and
        the charge limit (cmd_type 2), which is why its ack carries the
        instant.charge fields."""
        self.assertEqual(CHARGE, 4)

    def test_the_live_acknowledgement_is_recognised(self):
        coord = _coordinator_with(_cached_state())
        self.assertTrue(coord._merge_command_state(_charge_ack(**LIVE_ACK)))
        charge = coord._last_full_data["msg_content"]["instant"]["charge"]
        self.assertEqual(charge["max_range_charge"], 90)
        self.assertEqual(charge["charge_status"], 15)


class _Bus:
    """Just enough HA bus to count what the listener fires."""

    def __init__(self):
        self.events = []

    def async_fire(self, event, payload):
        self.events.append((event, payload))


class _Hass:
    def __init__(self):
        self.bus = _Bus()


def _seat_ack(**content):
    """The shape a live car reported for ``service_type 30`` on 2026-10-14.

    A *flat* seat snapshot - not nested under ``instant.seat`` the way a state payload
    carries it - which is why nothing recognised it as state until it was merged.
    """
    body = {"msg": "Erfolgreich", "code": 200}
    body.update(content)
    return {
        "service_type": coordinator.SEAT_SERVICE_TYPE,
        "msg_type": 1,
        "msg_id": "2289610185002692965174179197389713371555",
        "code": 200,
        "message": "Erfolgreich",
        "content": body,
    }


#: The exact seat block the live car pushed, verbatim.
LIVE_SEAT_ACK = dict(
    trd_row_middle_seat_heat=0,
    trd_row_right_seat_heat=0,
    left_rear_seat_ventilation=0,
    left_rear_seat_heat=1,
    right_rear_seat_heat=0,
    trd_row_left_seat_heat=0,
    right_rear_seat_ventilation=0,
    driver_seat_ventilation=0,
    driver_seat_heat=0,
    passenger_seat_heat=1,
    passenger_seat_ventilation=0,
)


class TestASeatPushUpdatesTheState(unittest.TestCase):
    """A seat changed anywhere - XPENG's app, the car's own controls, another session -
    arrives as service_type 30 carrying the seat block.

    Until this was merged those messages were logged and dropped, which is the bug
    reported on 0.4.31: the select kept reading Off while the car was heating, and
    because re-picking the option it already shows is not a change, the seat could not be
    switched off from Home Assistant either.
    """

    def test_the_seat_service_type_is_thirty(self):
        self.assertEqual(coordinator.SEAT_SERVICE_TYPE, 30)

    def test_the_seat_snapshot_lands_in_instant_seat(self):
        coord = _coordinator_with(_cached_state())
        self.assertTrue(coord._merge_command_state(_seat_ack(**LIVE_SEAT_ACK)))

        seat = coord._last_full_data["msg_content"]["instant"]["seat"]
        self.assertEqual(seat["left_rear_seat_heat"], 1)
        self.assertEqual(seat["passenger_seat_heat"], 1)
        self.assertEqual(seat["right_rear_seat_heat"], 0)
        self.assertEqual(seat["left_rear_seat_ventilation"], 0)

    def test_the_entity_read_path_answers_the_new_level(self):
        """``left_rear_seat_heating`` reads exactly this path."""
        coord = _coordinator_with(_cached_state())
        coord._merge_command_state(_seat_ack(**LIVE_SEAT_ACK))

        value = coord._last_full_data["msg_content"]["instant"]["seat"].get(
            "left_rear_seat_heat"
        )
        self.assertEqual(value, 1)

    def test_charge_and_door_state_are_untouched_by_a_seat_push(self):
        coord = _coordinator_with(_cached_state())
        coord._merge_command_state(_seat_ack(**LIVE_SEAT_ACK))

        instant = coord._last_full_data["msg_content"]["instant"]
        self.assertEqual(instant["charge"]["max_range_charge"], 90)
        self.assertEqual(instant["charge"]["battery_soc"], 23)
        self.assertEqual(instant["door"], {"driver_door_lock_st": 1})

    def test_code_and_msg_are_not_merged_as_seat_fields(self):
        coord = _coordinator_with(_cached_state())
        coord._merge_command_state(_seat_ack(**LIVE_SEAT_ACK))

        seat = coord._last_full_data["msg_content"]["instant"]["seat"]
        self.assertNotIn("code", seat)
        self.assertNotIn("msg", seat)

    def test_a_seat_turning_off_is_also_merged(self):
        """The direction that matters for switching a seat off from HA."""
        coord = _coordinator_with(_cached_state())
        coord._merge_command_state(_seat_ack(**LIVE_SEAT_ACK))
        coord._merge_command_state(
            _seat_ack(**{**LIVE_SEAT_ACK, "left_rear_seat_heat": 0})
        )

        seat = coord._last_full_data["msg_content"]["instant"]["seat"]
        self.assertEqual(seat["left_rear_seat_heat"], 0)
        # The seat that did not change keeps its value.
        self.assertEqual(seat["passenger_seat_heat"], 1)


class CommandBaseline(unittest.TestCase):
    def test_ack_updates_the_broker_baseline_before_the_next_partial(self):
        from test_mqtt_state_delivery import _client, _Msg
        client, delivered = _client()
        coord = _coordinator_with(_cached_state())
        coord._baseline_sink = client.adopt_state
        coord.last_data_timestamp = 123.0
        for subsystem, content in ((3, {"hvac_temp": 22.0}),
                                   (12, {"front_left_window_position": 100})):
            self.assertTrue(coord._merge_command_state({
                "service_type": subsystem, "code": 200, "content": content,
            }))
        self.assertEqual(client._last_full_content, coord._last_full_data["msg_content"])
        self.assertEqual(coord.last_data_timestamp, 123.0)
        self.assertEqual(delivered, [])
        client._on_message(None, None, _Msg({
            "service_type": 19, "msg_type": 1,
            "msg_content": {"code": 200, "instant": {"door": {"passenger_door_lock_st": 0}}},
        }))
        self.assertEqual(len(delivered), 1)
        instant = delivered[0]["msg_content"]["instant"]
        self.assertEqual(instant["hvac"]["hvac_temp"], 22.0)
        self.assertEqual(instant["window"]["front_left_window_position"], 100)


class TestTheListenerDoesNotFlood(unittest.TestCase):
    """``#`` plus discovery is a firehose - the discovery candidates include the car's
    1 Hz CAN broadcast - and the expensive half used to run per message: an HA event plus
    a full coordinator update, which re-renders every entity and writes recorder rows.
    That is what wedged a user's Home Assistant on 0.4.30.
    """

    def _coordinator(self):
        coord = object.__new__(coordinator.XPengDataCoordinator)
        coord.listener_last_topic = None
        coord.listener_last_payload = None
        coord.listener_last_timestamp = None
        coord.listener_message_count = 0
        coord.listener_log = []
        coord._last_listener_update = 0.0
        coord._last_full_data = {"msg_content": {}}
        coord.hass = _Hass()
        coord.updates = []
        coord.async_set_updated_data = coord.updates.append
        return coord

    def test_a_burst_costs_one_event_and_one_refresh(self):
        coord = self._coordinator()
        for _ in range(500):
            coord.set_listener_message("Xmart/Car/9/State/CanPack1s", "x" * 16)

        # Nothing is lost from the diagnostics...
        self.assertEqual(coord.listener_message_count, 500)
        self.assertEqual(len(coord.listener_log), 500)
        # ...but the expensive half runs once, not 500 times.
        self.assertEqual(len(coord.hass.bus.events), 1)
        self.assertEqual(len(coord.updates), 1)

    def test_the_last_message_is_always_recorded(self):
        coord = self._coordinator()
        for index in range(50):
            coord.set_listener_message("a/b", "payload-%d" % index)

        self.assertEqual(coord.listener_last_topic, "a/b")
        self.assertEqual(coord.listener_last_payload, "payload-49")

    def test_the_interval_is_a_positive_bound(self):
        self.assertGreater(coordinator.LISTENER_UPDATE_INTERVAL, 0)


class TestPetModeResponseUpdatesMeasuredState(unittest.TestCase):
    def test_pet_status_uses_its_own_section_without_advancing_snapshot_time(self):
        original = _cached_state()
        original["timestamp"] = 123
        original["msg_content"]["instant"]["protectMode"] = {"protect_mode_status": 0}
        coord = _coordinator_with(original)
        ack = {"service_type": 33, "code": 200, "content": {"protect_mode_status": 1}}
        self.assertTrue(coord._merge_command_state(ack))
        self.assertEqual(coord._last_full_data["msg_content"]["instant"]["protectMode"], {"protect_mode_status": 1})
        self.assertEqual(coord._last_full_data["timestamp"], 123)
        self.assertEqual(original["msg_content"]["instant"]["protectMode"]["protect_mode_status"], 0)
        ack["content"]["protect_mode_status"] = 2
        ack["code"] = 8
        self.assertFalse(coord._merge_command_state(ack))
        self.assertEqual(coord._last_full_data["msg_content"]["instant"]["protectMode"]["protect_mode_status"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
