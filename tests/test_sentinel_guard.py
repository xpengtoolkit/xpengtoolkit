#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Placeholder sensor readings - 0 kPa tyres, 87.5 °C cabin - are not readings.

    python3 tests/test_sentinel_guard.py
    python3 -m unittest discover -s tests -v

Why this exists. On the live install (.88) the first state frame after the car
is woken carried all four tyre pressures as 0 - and the cabin temperature as
87.5 - with the real values arriving one to two seconds later. Home Assistant
recorded the zeros, so a tyre pressure dropped to 0 and reinflated, four times
between 17 and 19 October 2026. A community report has the same shape with a
daily 0 at a fixed time: a wake, and the wake frame's placeholders reaching the
recorder.

Two more shapes came from issue #25 (G6, 2026-11-06): the cabin temperature at
87.5 again - this time held for hours, because the wake frame was the last frame
before the car slept - and one tyre at ~700 kPa for a single sample, high
instead of zero and alone instead of as a set of four.

The rules pinned here are narrow on purpose: 0, a negative, or above 450 kPa is
"no tyre reading", 87.5 °C or above is "no cabin reading", and everything else -
including a genuinely low tyre and a genuinely hot cabin - is a reading and is
published untouched.

Loaded by path like replay_guard.py: the module has no Home Assistant import,
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
MODULE_PATH = _component.module_path("sentinel_guard.py")

_spec = importlib.util.spec_from_file_location("sentinel_guard_under_test", MODULE_PATH)
assert _spec and _spec.loader
sentinel_guard = importlib.util.module_from_spec(_spec)
sys.modules["sentinel_guard_under_test"] = sentinel_guard
_spec.loader.exec_module(sentinel_guard)


def _content(pressures, *, heid=None):
    """A msg_content holding one tpms section, plus anything else passed."""
    instant = {}
    if pressures is not None:
        instant["tpms"] = pressures
    if heid is not None:
        instant["hvac"] = {"hvac_inner_temp_float": heid}
    return {"instant": instant}


REAL = {"tpms_pressure_fl": 266, "tpms_pressure_fr": 267, "tpms_pressure_rl": 274, "tpms_pressure_rr": 275}
WAKE = {"tpms_pressure_fl": 0, "tpms_pressure_fr": 0, "tpms_pressure_rl": 0, "tpms_pressure_rr": 0}


class AWakeFrameDoesNotFlattenTheTyres(unittest.TestCase):
    """The measured 2026-10-17/18 frame: all four pressures 0."""

    def test_the_placeholders_are_replaced_by_the_last_real_readings(self):
        repaired = sentinel_guard.with_previous_unreported(
            _content(WAKE)["instant"], _content(REAL)
        )
        self.assertEqual(repaired["tpms"], REAL)

    def test_a_real_reading_is_returned_untouched_by_identity(self):
        instant = _content(REAL)["instant"]
        self.assertIs(sentinel_guard.with_previous_unreported(instant, _content(REAL)), instant)

    def test_one_flat_placeholder_does_not_hide_the_other_three(self):
        mixed = dict(REAL, tpms_pressure_rl=0)
        repaired = sentinel_guard.with_previous_unreported(
            _content(mixed)["instant"], _content(REAL)
        )
        self.assertEqual(repaired["tpms"], REAL)

    def test_a_low_but_real_pressure_is_still_published(self):
        """A slow puncture is a reading, not a placeholder - do not hide it."""
        low = dict(REAL, tpms_pressure_fl=140)
        repaired = sentinel_guard.with_previous_unreported(
            _content(low)["instant"], _content(REAL)
        )
        self.assertEqual(repaired["tpms"]["tpms_pressure_fl"], 140)

    def test_a_negative_reading_is_a_placeholder_too(self):
        repaired = sentinel_guard.with_previous_unreported(
            _content(dict(REAL, tpms_pressure_fl=-3))["instant"], _content(REAL)
        )
        self.assertEqual(repaired["tpms"], REAL)


class WhenNothingWasEverReported(unittest.TestCase):
    def test_the_placeholders_are_dropped_rather_than_published_as_zero(self):
        repaired = sentinel_guard.with_previous_unreported(_content(WAKE)["instant"], None)
        self.assertNotIn("tpms", repaired)

    def test_a_partial_previous_state_is_still_used(self):
        previous = _content({"tpms_pressure_fl": 266})
        repaired = sentinel_guard.with_previous_unreported(
            _content(dict(REAL, tpms_pressure_fl=0))["instant"], previous
        )
        self.assertEqual(repaired["tpms"]["tpms_pressure_fl"], 266)
        self.assertEqual(repaired["tpms"]["tpms_pressure_fr"], 267)

    def test_the_cabin_placeholder_is_dropped_too(self):
        """Issue #25: 87.5 °C with nothing real held reads unknown, not 87.5."""
        instant = _content(WAKE, heid=87.5)["instant"]
        repaired = sentinel_guard.with_previous_unreported(instant, None)
        self.assertNotIn("tpms", repaired)
        self.assertNotIn("hvac", repaired)

    def test_the_rest_of_the_frame_is_preserved(self):
        """The frames beside the placeholders are not this guard's business."""
        instant = _content(WAKE, heid=87.5)["instant"]
        instant["charge"] = {"battery_soc": 62}
        instant["hvac"]["hvac_temp_float"] = 21.5
        repaired = sentinel_guard.with_previous_unreported(instant, None)
        self.assertEqual(repaired["charge"], {"battery_soc": 62})
        self.assertEqual(repaired["hvac"]["hvac_temp_float"], 21.5)


class OddPayloadsAreLeftAlone(unittest.TestCase):
    def test_no_tpms_section(self):
        instant = {"charge": {"battery_soc": 62}}
        self.assertIs(sentinel_guard.with_previous_unreported(instant, _content(REAL)), instant)

    def test_tpms_is_not_a_dict(self):
        instant = {"tpms": None}
        self.assertIs(sentinel_guard.with_previous_unreported(instant, _content(REAL)), instant)

    def test_an_unparsable_string_is_not_treated_as_zero(self):
        instant = _content(dict(REAL, tpms_pressure_fl="n/a"))["instant"]
        repaired = sentinel_guard.with_previous_unreported(instant, _content(REAL))
        self.assertEqual(repaired["tpms"]["tpms_pressure_fl"], "n/a")

    def test_a_string_zero_is_a_placeholder(self):
        instant = _content(dict(REAL, tpms_pressure_fl="0"))["instant"]
        repaired = sentinel_guard.with_previous_unreported(instant, _content(REAL))
        self.assertEqual(repaired["tpms"]["tpms_pressure_fl"], 266)


class AHighPressureIsAPlaceholderToo(unittest.TestCase):
    """Issue #25's G6 frame: one wheel at ~700 kPa for a single sample."""

    def test_the_spike_is_replaced_by_the_last_real_pressure(self):
        spiked = dict(REAL, tpms_pressure_fr=701)
        repaired = sentinel_guard.with_previous_unreported(
            _content(spiked)["instant"], _content(REAL)
        )
        self.assertEqual(repaired["tpms"], REAL)

    def test_a_pressure_above_the_bound_alone_is_a_placeholder(self):
        """It arrives on one wheel, not four, so the four-at-once rule cannot help."""
        spiked = dict(REAL, tpms_pressure_rl=460)
        repaired = sentinel_guard.with_previous_unreported(
            _content(spiked)["instant"], _content(REAL)
        )
        self.assertEqual(repaired["tpms"]["tpms_pressure_rl"], 274)

    def test_a_high_but_possible_pressure_is_published(self):
        """Towing pressures reach ~350 kPa; only the impossible end is dropped."""
        for high in (350, 450):
            with self.subTest(high=high):
                instant = _content(dict(REAL, tpms_pressure_fl=high))["instant"]
                self.assertIs(
                    sentinel_guard.with_previous_unreported(instant, _content(REAL)),
                    instant,
                )


class TheCabinPlaceholder(unittest.TestCase):
    """87.5 °C, measured on the P7+ and again on the G6 (issue #25)."""

    def test_the_placeholder_is_replaced_by_the_last_real_temperature(self):
        repaired = sentinel_guard.with_previous_unreported(
            _content(None, heid=87.5)["instant"], _content(None, heid=19.5)
        )
        self.assertEqual(repaired["hvac"]["hvac_inner_temp_float"], 19.5)

    def test_a_hot_cabin_is_still_published(self):
        """A sun-baked car reaches 60-70 °C; the bound must sit above that."""
        for hot in (60, 70, 85):
            with self.subTest(hot=hot):
                instant = _content(None, heid=hot)["instant"]
                self.assertIs(
                    sentinel_guard.with_previous_unreported(instant, None), instant
                )

    def test_both_placeholders_in_one_frame_are_repaired_together(self):
        """Issue #25's car shows the tyre and cabin sentinels on the same day."""
        instant = _content(WAKE, heid=87.5)["instant"]
        repaired = sentinel_guard.with_previous_unreported(
            instant, _content(REAL, heid=21)
        )
        self.assertEqual(repaired["tpms"], REAL)
        self.assertEqual(repaired["hvac"]["hvac_inner_temp_float"], 21)

    def test_a_previous_placeholder_is_not_carried_forward(self):
        repaired = sentinel_guard.with_previous_unreported(
            _content(None, heid=87.5)["instant"], _content(None, heid=87.5)
        )
        self.assertNotIn("hvac", repaired)

    def test_the_other_hvac_fields_survive_when_the_temperature_is_dropped(self):
        instant = {"hvac": {"hvac_inner_temp_float": 87.5, "hvac_temp_float": 22}}
        repaired = sentinel_guard.with_previous_unreported(instant, None)
        self.assertNotIn("hvac_inner_temp_float", repaired["hvac"])
        self.assertEqual(repaired["hvac"]["hvac_temp_float"], 22)


class TheIntegrationActuallyConsultsIt(unittest.TestCase):
    """Pinned at the call site, so the module cannot become dead code.

    mqtt_client.py keeps no project imports of its own - tools/ and the tests
    load it as a top-level module - so the repair is injected through a setter
    and wired in by ``__init__.py``. Both halves are pinned here.
    """

    def test_mqtt_client_repairs_the_frame_before_it_is_used(self):
        mqtt = (COMPONENT / "mqtt_client.py").read_text(encoding="utf-8")
        self.assertIn("def set_instant_sanitizer(", mqtt)
        self.assertIn("self._instant_sanitizer is not None", mqtt)
        # ``baseline`` is the previous content, read once under the baseline
        # lock so the REST-fallback adoption cannot move it mid-frame.
        self.assertIn(
            "instant = self._instant_sanitizer(raw_instant, baseline)",
            mqtt,
        )
        # The repaired instant is what the full/partial branches then store, so
        # the call must sit before the full-state classification.
        self.assertLess(
            mqtt.index("self._instant_sanitizer(raw_instant"),
            mqtt.index('has_ext = bool(instant.get("ext"))'),
        )

    def test_the_entrypoint_wires_it_in(self):
        init = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("from .sentinel_guard import with_previous_unreported", init)
        self.assertIn("mqtt_client.set_instant_sanitizer(with_previous_unreported)", init)


if __name__ == "__main__":
    unittest.main(verbosity=2)
