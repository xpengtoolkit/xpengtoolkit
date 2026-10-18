#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The failed-renewal window: a minted pair that could not be delivered.

A renewal logs in, which evicts the integration's MQTT session, and only then mints
and imports the pair. Measured on 2026-10-17: a scheduled renewal ran at 21:48:42
while Home Assistant was restarting, received `502` from the config-flow call, and
reported a failed renewal — leaving the integration evicted and a perfectly good
pair sitting on disk. It stayed dark until someone interfered.

Three things have to be true for that window to close itself:

1. **"Could not ask Home Assistant" is not "no entry"** — a restart must not turn a
   delivery into a bootstrap attempt;
2. **the delivery is retried on its own** (no second login, which would evict the
   session again) for as long as the failure is transient;
3. **a failed attempt schedules the next attempt sooner than the renewal interval** —
   and a manual trigger moves the scheduled one, so it does not fire minutes later
   (that double run is what re-evicted the session mid-restart).

The module is imported by path; it reads its options from the Supervisor at import
time and tolerates their absence, so a bare interpreter is enough.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402

SERVER_PATH = ROOT / "xpeng_renew" / "renew_server.py"


def _load():
    spec = importlib.util.spec_from_file_location("renew_server_failed_test", SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["renew_server_failed_test"] = module
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:  # pragma: no cover - stdlib only today
        raise unittest.SkipTest(f"renew_server.py could not be imported: {exc}")
    return module


server = _load()


class TestNextDelay(unittest.TestCase):
    def test_success_waits_the_interval(self):
        self.assertEqual(server._next_delay({"ok": True}), server.RENEW_INTERVAL)

    def test_a_retryable_delivery_failure_is_retried_soon(self):
        self.assertEqual(
            server._next_delay({"ok": False, "retry_import": True}),
            server.RETRY_IMPORT_SECONDS,
        )

    def test_a_pre_login_failure_waits_longer(self):
        """Nothing was evicted and every attempt costs a slider solve."""
        delay = server._next_delay({"ok": False, "retry_import": False})
        self.assertGreaterEqual(delay, server.RETRY_LOGIN_SECONDS)

    def test_retry_import_is_sooner_than_a_login_retry(self):
        self.assertLess(server.RETRY_IMPORT_SECONDS, server.RETRY_LOGIN_SECONDS)


class TestDelivery(unittest.TestCase):
    def _deliver(self, state, **patches):
        with mock.patch.object(server, "_entry_state", return_value=state):
            for name, value in patches.items():
                setattr(server, name, value)
            return server._deliver_pair("/tmp/pair")

    def test_unreachable_home_assistant_is_retryable_and_does_not_bootstrap(self):
        bootstrap = mock.Mock(return_value=(True, "VIN"))
        result = self._deliver("unknown", _bootstrap_entry=bootstrap)
        self.assertFalse(result["ok"])
        self.assertTrue(result["retry_import"])
        bootstrap.assert_not_called()
        self.assertIn("unreachable", result["error"])

    def test_absent_entry_bootstraps(self):
        bootstrap = mock.Mock(return_value=(True, "L1NNSGHAXSB123456"))
        result = self._deliver("absent", _bootstrap_entry=bootstrap)
        self.assertTrue(result["ok"])
        self.assertEqual(result["bootstrapped"], "L1NNSGHAXSB123456")

    def test_a_failed_bootstrap_is_retryable(self):
        bootstrap = mock.Mock(return_value=(False, "could not start the config flow: 502"))
        result = self._deliver("absent", _bootstrap_entry=bootstrap)
        self.assertFalse(result["ok"])
        self.assertTrue(result["retry_import"])

    def test_a_5xx_import_failure_is_retryable(self):
        service = mock.Mock(return_value=(502, "Bad Gateway"))
        result = self._deliver("present", _call_service=service)
        self.assertFalse(result["ok"])
        self.assertTrue(result["retry_import"])

    def test_a_rejected_pair_needs_a_new_login(self):
        service = mock.Mock(return_value=(400, "invalid_tokens"))
        result = self._deliver("present", _call_service=service)
        self.assertFalse(result["ok"])
        self.assertFalse(result["retry_import"])

    def test_a_successful_import_is_reported(self):
        service = mock.Mock(return_value=(200, '{"entry_id": "E1"}'))
        result = self._deliver("present", _call_service=service)
        self.assertTrue(result["ok"])
        self.assertIn("imported", result)


class TestPendingImport(unittest.TestCase):
    def setUp(self):
        server.STATE["pending_import"] = False
        self._notify = server._notify
        server._notify = mock.Mock()

    def tearDown(self):
        server._notify = self._notify
        server.STATE["pending_import"] = False

    def test_a_retryable_failure_marks_the_pair_pending(self):
        with mock.patch.object(server, "renew", return_value={
            "ok": False, "error": "502", "retry_import": True,
        }):
            result = server._run_locked()
        self.assertFalse(result["ok"])
        self.assertTrue(server.STATE["pending_import"])

    def test_a_rejected_pair_does_not_mark_it_pending(self):
        with mock.patch.object(server, "renew", return_value={
            "ok": False, "error": "400", "retry_import": False,
        }):
            server._run_locked()
        self.assertFalse(server.STATE["pending_import"])

    def test_pending_import_is_delivered_without_a_new_login(self):
        server.STATE["pending_import"] = True
        renew = mock.Mock(side_effect=AssertionError("must not log in again"))
        with mock.patch.object(server, "renew", renew), \
             mock.patch.object(server, "retry_pending_import", return_value={
                 "ok": True, "imported": "{}", "ms": 5,
             }):
            result = server._run_locked()
        self.assertTrue(result["ok"])
        renew.assert_not_called()
        self.assertFalse(server.STATE["pending_import"])

    def test_a_transient_delivery_failure_keeps_it_pending(self):
        server.STATE["pending_import"] = True
        with mock.patch.object(server, "renew", mock.Mock()) as renew, \
             mock.patch.object(server, "retry_pending_import", return_value={
                 "ok": False, "error": "502", "retry_import": True,
             }):
            server._run_locked()
        renew.assert_not_called()
        self.assertTrue(server.STATE["pending_import"])

    def test_a_refused_pair_falls_back_to_a_new_login(self):
        server.STATE["pending_import"] = True
        renewed = {"ok": True, "imported": "{}", "ms": 5}
        with mock.patch.object(server, "renew", return_value=renewed) as renew, \
             mock.patch.object(server, "retry_pending_import", return_value={
                 "ok": False, "error": "400", "retry_import": False,
             }):
            result = server._run_locked()
        renew.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertFalse(server.STATE["pending_import"])


class TestSchedulingWiring(unittest.TestCase):
    def test_manual_trigger_moves_the_scheduled_run(self):
        """Otherwise the timer fires minutes later and logs in a second time."""
        source = SERVER_PATH.read_text(encoding="utf-8")
        handler = source[source.index("def do_POST"):source.index("def log_message")]
        self.assertIn('STATE["next_run"] = time.time() + _next_delay(result)', handler)

    def test_scheduler_uses_the_failure_aware_delay(self):
        source = SERVER_PATH.read_text(encoding="utf-8")
        scheduler = source[source.index("def _scheduler"):source.index("def _record_result")]
        self.assertIn("delay = _next_delay(result)", scheduler)
        self.assertIn('STATE["next_run"] = time.time() + delay', scheduler)

    def test_startup_delivers_a_fresh_pair_instead_of_logging_in_again(self):
        source = SERVER_PATH.read_text(encoding="utf-8")
        scheduler = source[source.index("def _scheduler"):source.index("def _record_result")]
        self.assertIn("_pair_on_disk() and _pair_is_fresh() and _credentials_invalid()", scheduler)
        self.assertIn('STATE["pending_import"] = True', scheduler)


if __name__ == "__main__":
    unittest.main(verbosity=2)
