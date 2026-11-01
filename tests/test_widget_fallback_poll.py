#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The fast widget fallback poll (issue #3) is built and torn down correctly.

Until 0.6.37 the only widget read rode inside the heavy REST poll, which is
parked-gated at ``parked_scan_interval`` (3600s) - so while the push stream was
wedged, a car that started driving was invisible for up to an hour. The
2026-10-27 log showed the 6-9 minute detections were the hourly poll's phase,
not the 5-minute merge gate (issue #3, comment by the reporter).

The fix is a poll that asks for the widget ALONE, at ``rest_scan_interval``,
and only while ``mqtt_energy_stale()`` says the push stream is the thing that is
missing. These pin the three properties that make that safe:

* it asks only the widget (the heavy REST poll must stay hourly while parked);
* the gate comes first, so a healthy stream costs no requests;
* it reschedules at the REST interval and is cancelled with the other timers,
  and a tick that was already running when the entry unloaded does not
  resurrect the timer.

    python3 tests/test_widget_fallback_poll.py
    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402

COMP = _component.component_dir()
INIT = (COMP / "__init__.py").read_text(encoding="utf-8")


def _poll_body() -> str:
    start = INIT.index("async def _poll_widget_fallback(")
    end = INIT.index("\n        def cancel_widget_fallback_fn(", start)
    return INIT[start:end]


class ThePollUsesTheFallbacksOwnRules(unittest.TestCase):
    def test_it_asks_only_the_widget(self):
        body = _poll_body()
        self.assertIn("_fetch_widget_state_sync", body)
        self.assertIn('coordinator.set_rest_data({"widget_state": widget})', body)
        self.assertNotIn(
            "_fetch_rest_data_sync",
            body,
            "the heavy REST poll must stay hourly while parked",
        )

    def test_it_only_runs_while_the_stream_is_silent(self):
        body = _poll_body()
        self.assertIn("coordinator.mqtt_energy_stale(", body)
        self.assertLess(
            body.index("mqtt_energy_stale"),
            body.index("_fetch_widget_state_sync"),
            "the gate must be checked before the request",
        )

    def test_it_runs_at_the_rest_interval_not_the_parked_one(self):
        body = _poll_body()
        self.assertIn("rest_scan_interval, _poll_widget_fallback", body)
        self.assertNotIn("parked_scan_interval, _poll_widget_fallback", body)

    def test_it_skips_until_the_vehicle_type_code_is_known(self):
        body = _poll_body()
        self.assertIn("vehicle_type_code", body)
        self.assertIn("Widget fallback poll skipped", body)

    def test_a_running_tick_cannot_resurrect_a_stopped_timer(self):
        body = _poll_body()
        self.assertIn('if not widget_timer_state["stopped"]:', body)
        start = INIT.index("def cancel_widget_fallback_fn(")
        end = INIT.index("cancel_widget_fallback = cancel_widget_fallback_fn", start)
        self.assertIn('widget_timer_state["stopped"] = True', INIT[start:end])


class TheSingleReadIsCheap(unittest.TestCase):
    def test_it_reads_the_widget_with_the_vehicle_type_code(self):
        start = INIT.index("def _fetch_widget_state_sync(")
        end = INIT.index("\ndef _fetch_rest_data_sync(", start)
        body = INIT[start:end]
        self.assertIn(
            "get_vehicle_state(None, tenant, tokens, vin, vehicle_type_code", body
        )
        self.assertIn("_load_api_context(entry)", body)

    def test_it_does_not_refresh_tokens_itself(self):
        """A second refresher racing the credential loop is worse than a skip."""
        start = INIT.index("def _fetch_widget_state_sync(")
        end = INIT.index("\ndef _fetch_rest_data_sync(", start)
        body = INIT[start:end]
        for forbidden in ("refresh_login_tokens", "_refresh_creds_sync", "_start_reauth"):
            self.assertNotIn(forbidden, body)


class ItIsTornDownWithTheOtherTimers(unittest.TestCase):
    def test_the_cancel_handle_is_registered(self):
        self.assertIn('"cancel_widget_fallback": cancel_widget_fallback', INIT)

    def test_the_replace_path_and_the_unload_path_both_cancel_it(self):
        # One registration in the data dict plus one entry in each of the two
        # cancel loops (setup replacing a live client, and unload).
        self.assertEqual(INIT.count('"cancel_widget_fallback"'), 3)


if __name__ == "__main__":
    unittest.main()
