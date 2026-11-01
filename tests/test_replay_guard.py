#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A replayed vehicle state is recognised by the odometer going backwards.

    python3 tests/test_replay_guard.py
    python3 -m unittest discover -s tests -v

Why this exists. On the live install 2026-10-21 the MQTT push fanout wedged
after the car parked at 13:41 and kept replaying the pre-drive snapshot
(52%, 277 km, odometer 62210) for the next two and a half hours, while the REST
fallback kept writing the truth (47%, 257 km, odometer 62234) every ~15 min.
The replay was indistinguishable from data, so it overwrote the fresher value
one minute after each merge and kept the watchdog's staleness clock fresh, and
the single force-upload that heals a wedged fanout never fired.

``total_mileage`` only ever increases, so a state whose odometer is below the
one already accepted is an older snapshot being delivered again. These tests
pin that rule and the pieces that read it out of each payload shape.

Loaded by path like watchdog_policy.py: the module has no Home Assistant import,
so the decision is testable without a car.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402

COMPONENT = _component.component_dir()
MODULE_PATH = _component.module_path("replay_guard.py")

_spec = importlib.util.spec_from_file_location("replay_guard_under_test", MODULE_PATH)
assert _spec and _spec.loader
replay_guard = importlib.util.module_from_spec(_spec)
sys.modules["replay_guard_under_test"] = replay_guard
_spec.loader.exec_module(replay_guard)


def _state(odometer, *, instant_extra=None):
    instant = {"odometer": {"total_mileage": odometer}}
    if instant_extra:
        instant.update(instant_extra)
    return {"msg_content": {"instant": instant}}


class TheLiveIncidentIsRecognised(unittest.TestCase):
    """The exact numbers from the 2026-10-21 measurement."""

    def test_the_pre_drive_snapshot_is_a_replay(self):
        self.assertTrue(replay_guard.is_replay(62210, 62234))

    def test_the_fresher_snapshot_is_not(self):
        self.assertFalse(replay_guard.is_replay(62234, 62210))

    def test_an_equal_odometer_is_not_a_replay(self):
        """Same snapshot again is not *older*; upstream dedupe handles repeats."""
        self.assertFalse(replay_guard.is_replay(62234, 62234))


class TheVerdictUsesTheOdometer(unittest.TestCase):
    def test_a_higher_odometer_is_new_data(self):
        self.assertFalse(replay_guard.is_replay(62235, 62234))

    def test_rounding_noise_is_not_a_replay(self):
        """Whole kilometres, so a sub-kilometre dip is noise, not a snapshot."""
        self.assertFalse(replay_guard.is_replay(62233.6, 62234))

    def test_more_than_the_tolerance_is_a_replay(self):
        self.assertTrue(replay_guard.is_replay(62233.4, 62234))

    def test_no_verdict_never_blocks_data(self):
        """An unreadable odometer must not stop real data being accepted."""
        for candidate, held in ((None, 62234), (62234, None), (None, None)):
            self.assertFalse(replay_guard.is_replay(candidate, held))

    def test_zero_is_a_value_not_a_missing_reading(self):
        self.assertFalse(replay_guard.is_replay(0.0, 0.0))

    def test_a_zero_candidate_is_a_replay_of_a_moving_car(self):
        self.assertTrue(replay_guard.is_replay(0.0, 62234))


class ASentinelIsNotAReplay(unittest.TestCase):
    """Issue #21: ``odometer -1.0`` is "no data", not "an older snapshot".

    A two-car install's second entry answered ``-1.0`` for eight hours while the
    first worked. The replay rule matched it every time (it is behind any held
    odometer) and the log blamed a replay, which sent the report the wrong way.
    """

    def test_a_negative_odometer_is_a_sentinel(self):
        for value in (-1.0, -1, -0.5, -62234.0):
            self.assertTrue(replay_guard.is_sentinel_odometer(value))

    def test_zero_is_a_sentinel_too(self):
        """A real reading is positive; 0 is what an empty payload leaves."""
        self.assertTrue(replay_guard.is_sentinel_odometer(0.0))

    def test_a_real_reading_is_not(self):
        for value in (0.5, 1.0, 6482.0, 62234):
            self.assertFalse(replay_guard.is_sentinel_odometer(value))

    def test_no_reading_is_not_a_sentinel(self):
        """Missing stays missing: the merge gate still has to see None."""
        for value in (None,):
            self.assertFalse(replay_guard.is_sentinel_odometer(value))

    def test_a_sentinel_would_otherwise_look_like_a_replay(self):
        """The two questions must stay separate - this is why the order matters."""
        self.assertTrue(replay_guard.is_replay(-1.0, 6482.0))
        self.assertTrue(replay_guard.is_sentinel_odometer(-1.0))

    def test_the_coordinator_asks_about_the_sentinel_first(self):
        """Pinned at the call site: order is the fix, not the helper."""
        coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
        sentinel_at = coordinator.index("if is_sentinel_odometer(widget_odometer):")
        replay_at = coordinator.index("if is_replay(widget_odometer, self.max_odometer):")
        self.assertLess(sentinel_at, replay_at)


class TheReadersUnderstandBothShapes(unittest.TestCase):
    def test_state_path(self):
        self.assertEqual(replay_guard.odometer_from_state(_state(62234)), 62234)

    def test_widget_shape(self):
        widget = {"odometer": {"total_mileage": 62234}, "charge": {"battery_soc": 47}}
        self.assertEqual(replay_guard.odometer_from_widget(widget), 62234)

    def test_stringified_readings(self):
        """The REST side has been seen to stringify numbers."""
        self.assertEqual(
            replay_guard.odometer_from_state(_state(" 62234 ")), 62234
        )

    def test_missing_or_empty_readings(self):
        for payload in ({}, {"msg_content": {}}, _state(None), _state(""), _state(False)):
            self.assertIsNone(replay_guard.odometer_from_state(payload))
        self.assertIsNone(replay_guard.odometer_from_widget({}))

    def test_the_integration_actually_consults_it(self):
        """Pinned at the call sites, so the module cannot become dead code.

        Both directions matter: a rejected push must not replace the state, and
        a rejected push must not stamp the watchdog's "data arrived" clock.
        The decision that reaches that clock has to be made on the MQTT thread
        (``__init__._on_vehicle_state``), not deferred to the event loop, so it
        is pinned separately from the coordinator's own check.
        """
        coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
        self.assertIn("from .replay_guard import", coordinator)
        self.assertIn("is_replay(odometer, self.max_odometer)", coordinator)
        self.assertIn("is_replay(widget_odometer, self.max_odometer)", coordinator)

        init = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("from .replay_guard import", init)
        self.assertIn("if is_replay(odometer, coordinator.max_odometer):", init)
        self.assertIn("def _on_vehicle_state(data: dict[str, Any]) -> bool:", init)

        mqtt = (COMPONENT / "mqtt_client.py").read_text(encoding="utf-8")
        self.assertIn("self._state_callback(vehicle_data) is not False", mqtt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
