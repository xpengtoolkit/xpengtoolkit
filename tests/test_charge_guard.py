#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A parked car's -1 % state of charge is "not reported", not a battery at -1.

    python3 tests/test_charge_guard.py
    python3 -m unittest discover -s tests -v

Why this exists. Issue #24 (Xpengtk 0.6.41, X9, 2026-11-05): four times between
02:53 and 08:56 the push fanout delivered ``instant.charge.battery_soc`` as -1
- ``interval.charge.charge_rate`` with it - while ``range`` in the same frame
was correct, and while the official app showed the real 24 %. A plain sensor
publishes any number it is handed (sensor.py only filters sentinels for
SensorDeviceClass.ENUM), so the dashboard drew the battery at -1 % and the
recorder kept it.

The rule pinned here is narrow on purpose: only a *negative* value means "not
reported". 0 % and 0 A are real readings and must survive, and the rest of the
frame - issue #24's valid range above all - must be left alone. With nothing
real ever reported the placeholder is dropped rather than published, which is
what makes the entity read unknown instead of -1.

Loaded by path like replay_guard.py and sentinel_guard.py: the module has no
Home Assistant import, so the decision is testable without a car.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402

COMPONENT = _component.component_dir()
MODULE_PATH = _component.module_path("charge_guard.py")

_spec = importlib.util.spec_from_file_location("charge_guard_under_test", MODULE_PATH)
assert _spec and _spec.loader
charge_guard = importlib.util.module_from_spec(_spec)
sys.modules["charge_guard_under_test"] = charge_guard
_spec.loader.exec_module(charge_guard)


def _state(soc=None, rate=None, *, range_km=None, odometer=None, extra_instant=None):
    """A coordinator state carrying a charge section, and whatever else passed."""
    charge = {}
    if soc is not None:
        charge["battery_soc"] = soc
    if extra_instant:
        charge.update(extra_instant)
    interval_charge = {} if rate is None else {"charge_rate": rate}
    instant = {}
    if charge:
        instant["charge"] = charge
    if range_km is not None:
        instant["odometer"] = {"range": range_km}
    if odometer is not None:
        instant["odometer"] = odometer
    content = {"instant": instant}
    if interval_charge:
        content["interval"] = {"charge": interval_charge}
    return {"vin": "L1NNSGHA0SB126544", "msg_content": content}


REAL = _state(23, 7, range_km=161)
PARKED = _state(-1, -1, range_km=161)


class ThePlaceholderIsReplacedByTheLastRealReading(unittest.TestCase):
    """The measured 2026-11-05 frame: SOC and rate -1, range correct."""

    def test_the_state_of_charge_holds_instead_of_dropping_to_minus_one(self):
        repaired = charge_guard.with_previous_charge(PARKED, REAL)
        self.assertEqual(
            repaired["msg_content"]["instant"]["charge"]["battery_soc"], 23
        )

    def test_the_charge_rate_holds_too(self):
        repaired = charge_guard.with_previous_charge(PARKED, REAL)
        self.assertEqual(repaired["msg_content"]["interval"]["charge"]["charge_rate"], 7)

    def test_the_valid_range_in_the_same_frame_survives(self):
        """Dropping the frame would trade a wrong reading for a stale one."""
        repaired = charge_guard.with_previous_charge(PARKED, REAL)
        self.assertEqual(
            repaired["msg_content"]["instant"]["odometer"]["range"], 161
        )

    def test_the_rest_of_the_state_is_untouched(self):
        repaired = charge_guard.with_previous_charge(PARKED, REAL)
        self.assertEqual(repaired["vin"], REAL["vin"])

    def test_a_real_reading_is_returned_untouched_by_identity(self):
        self.assertIs(charge_guard.with_previous_charge(REAL, REAL), REAL)

    def test_the_held_state_is_not_mutated(self):
        charge_guard.with_previous_charge(PARKED, REAL)
        self.assertEqual(PARKED["msg_content"]["instant"]["charge"]["battery_soc"], -1)


class ZeroIsAReading(unittest.TestCase):
    """A genuine collapse to 0 % must stay visible; only negatives are placeholders."""

    def test_zero_percent_is_published(self):
        empty = _state(0, 0)
        repaired = charge_guard.with_previous_charge(empty, REAL)
        self.assertIs(repaired, empty)

    def test_a_negative_rate_is_a_placeholder_but_a_positive_is_not(self):
        self.assertTrue(charge_guard.is_placeholder(-1))
        self.assertFalse(charge_guard.is_placeholder(0))
        self.assertFalse(charge_guard.is_placeholder(0.5))
        self.assertFalse(charge_guard.is_placeholder(23))
        self.assertFalse(charge_guard.is_placeholder(True))


class WhenNothingRealWasEverReported(unittest.TestCase):
    def test_the_placeholder_is_dropped_rather_than_published(self):
        repaired = charge_guard.with_previous_charge(PARKED, None)
        self.assertNotIn(
            "battery_soc", repaired["msg_content"]["instant"]["charge"]
        )

    def test_a_previous_placeholder_is_not_carried_forward(self):
        repaired = charge_guard.with_previous_charge(PARKED, PARKED)
        self.assertNotIn(
            "battery_soc", repaired["msg_content"]["instant"]["charge"]
        )

    def test_the_other_section_still_drops_its_own_placeholder(self):
        repaired = charge_guard.with_previous_charge(PARKED, None)
        self.assertNotIn("charge_rate", repaired["msg_content"]["interval"]["charge"])


class OddPayloadsAreLeftAlone(unittest.TestCase):
    def test_no_msg_content(self):
        state = {"vin": "L1NNSGHA0SB126544"}
        self.assertIs(charge_guard.with_previous_charge(state, REAL), state)

    def test_no_charge_section(self):
        state = {"msg_content": {"instant": {"odometer": {"range": 161}}}}
        self.assertIs(charge_guard.with_previous_charge(state, REAL), state)

    def test_charge_is_not_a_dict(self):
        state = {"msg_content": {"instant": {"charge": None}}}
        self.assertIs(charge_guard.with_previous_charge(state, REAL), state)

    def test_a_non_dict_state_is_returned_as_is(self):
        self.assertIsNone(charge_guard.with_previous_charge(None, REAL))
        self.assertEqual(charge_guard.with_previous_charge("nope", REAL), "nope")

    def test_a_non_numeric_sentinel_is_not_guessed_at(self):
        self.assertFalse(charge_guard.is_placeholder("--"))
        self.assertFalse(charge_guard.is_placeholder(None))
        self.assertTrue(charge_guard.is_placeholder("-1"))


class TheIntegrationActuallyUsesIt(unittest.TestCase):
    """Pinned at the call site, so the module cannot become dead code."""

    def test_the_coordinator_repairs_charge_placeholders_before_storing(self):
        coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
        self.assertIn("from .charge_guard import with_previous_charge", coordinator)
        self.assertIn(
            "data = with_previous_charge(data, self._last_full_data)", coordinator
        )
        # Before the state is adopted, not after: nothing downstream may see -1.
        call = coordinator.index(
            "data = with_previous_charge(data, self._last_full_data)"
        )
        adopted = coordinator.index("self._last_full_data = data", call)
        self.assertLess(call, adopted)

    def test_both_ingest_paths_and_the_restore_are_repaired(self):
        """The push, the REST widget merge, and a cache written before this."""
        coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
        self.assertIn(
            "data = with_previous_charge(data, self._last_full_data)", coordinator
        )
        self.assertIn(
            "merged = with_previous_charge(merged, self._last_full_data)", coordinator
        )
        self.assertIn("data = with_previous_charge(data, None)", coordinator)
        # The widget merge must hand the repaired content to the MQTT client's
        # baseline, or the next partial merges onto the -1 and pushes it back.
        self.assertIn(
            'msg_content = merged.get("msg_content", msg_content)', coordinator
        )


if __name__ == "__main__":
    unittest.main()
