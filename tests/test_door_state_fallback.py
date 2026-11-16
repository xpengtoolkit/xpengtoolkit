#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The door section's clock, and the widget fallback allowed to correct it.

Issue #32 (2026-11-16, G9, 0.6.49): after walk-away locking the lock entity
read "unlocked" and the trunk "open" for hours. The car had stopped pushing
*after* the doors closed and *before* it locked itself, so the last
``instant.door`` snapshot it sent (07:10 UTC) was never corrected - the hourly
refresh carried charge, odometer and OTA only. The reporter's measurement of
the persisted state showed exactly that, and the hourly refresh is the REST
widget fallback's merge.

That merge used to drop the whole ``door`` section, because its *window*
positions are on a different scale than MQTT's (0/1 vs 0-100) and merging them
once asserted ``any_window_open`` on a parked car (2026-10-06). The two fields
the lock and trunk read are plain states, not positions, so they are allowed
through - but only while the held door section is stale, and only when the
value is one the entity can interpret. A widget that reports some other scale
must then do nothing rather than flip a security entity.

    python3 tests/test_door_state_fallback.py
    python3 -m unittest discover -s tests -v

``coordinator.py`` is loaded through test_command_state_merge's Home Assistant
stubs, and ``set_vehicle_data`` through test_energy_guard's fuller fixture, so
both halves are exercised against the real module.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402
import test_command_state_merge as _csm  # noqa: E402
import test_energy_guard as _eg  # noqa: E402

_component.assert_is_default_target()

coordinator = _csm.coordinator
door_guard = importlib.import_module("custom_components.xpeng.door_guard")

VIN = "L1NNSGHA3SB123456"


def _ago(seconds: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def _held(lock=0, trunk=5):
    """The state a sleeping car left behind: unlocked, trunk open."""
    return {
        "msg_content": {
            "instant": {
                "charge": {"battery_soc": 49},
                "odometer": {"total_mileage": 62234},
                "door": {
                    "driver_door_lock_st": lock,
                    "trunk_ajar_st_new": trunk,
                    # A window position on MQTT's 0-100 scale. The fallback must
                    # never touch this one.
                    "front_left_window_position": 100,
                },
            }
        },
        "vin": VIN,
    }


def _widget(**sections):
    widget = {
        "odometer": {"total_mileage": 62235},
        "charge": {"battery_soc": 48},
    }
    widget.update(sections)
    return widget


def _coordinator(held=None, *, door_age=4 * 3600, energy_age=3600):
    """A coordinator with just what the widget merge reads."""
    coord = object.__new__(coordinator.XPengDataCoordinator)
    coord.vin = VIN
    coord._last_full_data = held if held is not None else _held()
    coord._baseline_sink = None
    coord.max_odometer = 62234
    coord._rest_data = {}
    coord.last_data_timestamp = _ago(3600)
    coord.last_energy_data_timestamp = _ago(energy_age)
    coord.door_data_timestamp = None if door_age is None else _ago(door_age)
    coord._persist_state = lambda: None
    return coord


def _door_of(coord):
    return coord._last_full_data["msg_content"]["instant"]["door"]


class TheGuardOnlyBelievesReadableStates(unittest.TestCase):
    """A value nobody has measured is not evidence, whoever sent it."""

    def test_lock_states(self):
        self.assertIs(door_guard.lock_is_locked(1), True)
        self.assertIs(door_guard.lock_is_locked(0), False)
        for unreadable in (2, -1, None, "1", 1.0, True):
            self.assertIsNone(door_guard.lock_is_locked(unreadable), unreadable)

    def test_trunk_states(self):
        for value in (0, 1):
            self.assertIs(door_guard.trunk_is_closed(value), True, value)
        for value in (3, 4, 5, 7, 8):
            self.assertIs(door_guard.trunk_is_closed(value), False, value)
        # 2 and 6 have never been observed: unknown, and not "closed".
        for value in (2, 6, -1, 100, None, "5", True):
            self.assertIsNone(door_guard.trunk_is_closed(value), value)

    def test_the_overlay_takes_only_the_two_known_fields(self):
        accepted, refused = door_guard.widget_door_overlay(
            {
                "driver_door_lock_st": 1,
                "trunk_ajar_st_new": 0,
                "front_left_window_position": 0,
                "driver_door_ajar_st": 1,
            }
        )
        self.assertEqual(
            accepted, {"driver_door_lock_st": 1, "trunk_ajar_st_new": 0}
        )
        self.assertEqual(refused, [])

    def test_one_bad_value_does_not_discard_the_other(self):
        accepted, refused = door_guard.widget_door_overlay(
            {"driver_door_lock_st": 1, "trunk_ajar_st_new": 100}
        )
        self.assertEqual(accepted, {"driver_door_lock_st": 1})
        self.assertEqual(refused, ["trunk_ajar_st_new"])

    def test_a_block_that_is_not_a_mapping_is_ignored(self):
        self.assertEqual(door_guard.widget_door_overlay(None), ({}, []))
        self.assertEqual(door_guard.widget_door_overlay([1]), ({}, []))


class TheAgeAttributesTellTheTruthAboutNothing(unittest.TestCase):
    def test_an_unrefreshed_section_has_no_age_at_all(self):
        """No attributes, not a flattering zero: nothing is known."""
        self.assertEqual(door_guard.age_attributes(None), {})

    def test_the_age_counts_from_the_section_not_the_state(self):
        now = datetime(2026, 11, 16, 11, 17, tzinfo=timezone.utc)
        attrs = door_guard.age_attributes(
            datetime(2026, 11, 16, 7, 10, tzinfo=timezone.utc), now=now
        )
        self.assertEqual(attrs["door_state_age_seconds"], 4 * 3600 + 7 * 60)
        self.assertEqual(
            attrs["door_state_updated_at"], "2026-11-16T07:10:00+00:00"
        )

    def test_a_clock_that_ran_backwards_does_not_report_a_negative_age(self):
        now = datetime(2026, 11, 16, 11, 0, tzinfo=timezone.utc)
        attrs = door_guard.age_attributes(
            datetime(2026, 11, 16, 11, 5, tzinfo=timezone.utc), now=now
        )
        self.assertEqual(attrs["door_state_age_seconds"], 0)


class TheWidgetCorrectsAStaleDoorSection(unittest.TestCase):
    """Issue #32: the hourly fallback is the only thing still asking."""

    def test_the_stale_lock_and_trunk_are_corrected(self):
        coord = _coordinator()
        coord._merge_widget_state_if_mqtt_stale(
            _widget(door={"driver_door_lock_st": 1, "trunk_ajar_st_new": 0})
        )
        door = _door_of(coord)
        self.assertEqual(door["driver_door_lock_st"], 1)
        self.assertEqual(door["trunk_ajar_st_new"], 0)

    def test_a_fresh_door_section_is_never_overwritten(self):
        """A door ping a minute ago is better evidence than an hourly poll."""
        coord = _coordinator(door_age=60)
        coord._merge_widget_state_if_mqtt_stale(
            _widget(door={"driver_door_lock_st": 1, "trunk_ajar_st_new": 0})
        )
        door = _door_of(coord)
        self.assertEqual(door["driver_door_lock_st"], 0)
        self.assertEqual(door["trunk_ajar_st_new"], 5)

    def test_an_unknown_age_counts_as_stale(self):
        """Nothing has ever carried the section: there is nothing to protect."""
        coord = _coordinator(door_age=None)
        self.assertIsNone(coord.door_state_age_seconds())
        self.assertTrue(coord._door_section_stale())
        coord._merge_widget_state_if_mqtt_stale(
            _widget(door={"driver_door_lock_st": 1})
        )
        self.assertEqual(_door_of(coord)["driver_door_lock_st"], 1)

    def test_window_positions_stay_excluded(self):
        """The 2026-10-06 regression: window scales are not comparable."""
        coord = _coordinator()
        coord._merge_widget_state_if_mqtt_stale(
            _widget(
                door={
                    "driver_door_lock_st": 1,
                    "front_left_window_position": 0,
                },
                window={"front_left_window_position": 0, "front_right_window_position": 0},
            )
        )
        door = _door_of(coord)
        self.assertEqual(door["front_left_window_position"], 100)
        self.assertEqual(door["driver_door_lock_st"], 1)

    def test_a_widget_on_another_scale_changes_nothing(self):
        """Refused, not misread: the lock entity must not claim "locked"."""
        coord = _coordinator()
        coord._merge_widget_state_if_mqtt_stale(
            _widget(door={"driver_door_lock_st": 100, "trunk_ajar_st_new": 0})
        )
        door = _door_of(coord)
        self.assertEqual(door["driver_door_lock_st"], 0)
        # The one readable field is still taken.
        self.assertEqual(door["trunk_ajar_st_new"], 0)

    def test_the_merge_is_adopted_by_the_mqtt_client(self):
        """Otherwise the next door-less partial restores the stale snapshot."""
        coord = _coordinator()
        adopted = {}
        coord._baseline_sink = lambda content: adopted.update(content)
        coord._merge_widget_state_if_mqtt_stale(
            _widget(door={"driver_door_lock_st": 1})
        )
        self.assertEqual(adopted["instant"]["door"]["driver_door_lock_st"], 1)

    def test_the_door_clock_moves_with_the_overlay(self):
        coord = _coordinator(door_age=4 * 3600)
        coord._merge_widget_state_if_mqtt_stale(
            _widget(door={"driver_door_lock_st": 1})
        )
        self.assertLess(coord.door_state_age_seconds(), 5)
        self.assertFalse(coord._door_section_stale())


class TheDoorClockOnlyMovesForRealDoorData(unittest.TestCase):
    def test_a_frame_carrying_door_data_stamps_it(self):
        coord = _eg._coordinator_with(_eg._state(49))
        frame = _eg._state(49)
        frame["msg_content"]["instant"]["door"] = {"driver_door_lock_st": 1}
        frame["has_door_data"] = True
        self.assertTrue(coord.set_vehicle_data(frame))
        self.assertIsNotNone(coord.door_data_timestamp)
        self.assertLess(coord.door_state_age_seconds(), 5)

    def test_a_frame_without_door_data_leaves_it_alone(self):
        coord = _eg._coordinator_with(_eg._state(49))
        before = _ago(4 * 3600)
        coord.door_data_timestamp = before
        self.assertTrue(coord.set_vehicle_data(_eg._state(48)))
        self.assertEqual(coord.door_data_timestamp, before)

    def test_a_lock_acknowledgement_counts_as_door_data(self):
        """The only door report that arrives on purpose while awake."""
        coord = _csm._coordinator_with(_csm._cached_state())
        self.assertFalse(hasattr(coord, "door_data_timestamp"))
        ack = _csm._charge_ack(driver_door_lock_st=1)
        ack["service_type"] = 9
        self.assertTrue(coord._merge_command_state(ack))
        self.assertIsNotNone(coord.door_data_timestamp)
        self.assertLess(coord.door_state_age_seconds(), 5)

    def test_a_charge_acknowledgement_does_not(self):
        coord = _csm._coordinator_with(_csm._cached_state())
        coord.door_data_timestamp = _ago(4 * 3600)
        before = coord.door_data_timestamp
        self.assertTrue(coord._merge_command_state(_csm._charge_ack(
            max_range_charge=80
        )))
        self.assertEqual(coord.door_data_timestamp, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
