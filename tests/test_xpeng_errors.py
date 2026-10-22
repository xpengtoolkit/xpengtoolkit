#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the shared login-expiry handling. Standard library only.

    python3 tests/test_xpeng_errors.py
    python3 -m unittest discover -s tests -v

This module exists because of a real failure: on 2026-10-06 the MQTT credential
refresh could not tell "log in again" apart from "malformed response", swallowed
both at DEBUG, and stopped minting credentials for 41 minutes. The only visible
symptom was a credential age climbing to 40m.

Loaded by path: importing it as `custom_components.xpeng.xpeng_errors` would
execute the package __init__, which imports Home Assistant.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402

_MODULE_PATH = _component.module_path("xpeng_errors.py")
_spec = importlib.util.spec_from_file_location("xpeng_errors", _MODULE_PATH)
assert _spec and _spec.loader
errors = importlib.util.module_from_spec(_spec)
sys.modules["xpeng_errors"] = errors
_spec.loader.exec_module(errors)


class TestExpiryCodes(unittest.TestCase):
    def test_the_observed_code_is_recognised(self):
        """12010101 is what the API actually answered when this was written."""
        self.assertIn(12010101, errors.TOKEN_EXPIRED_CODES)

    def test_the_second_code_is_still_recognised(self):
        """The REST path treated 12010104 as expired before this module existed.

        Narrowing to only the code seen in the wild would silently change that
        behaviour, so both are kept.
        """
        self.assertIn(12010104, errors.TOKEN_EXPIRED_CODES)

    def test_the_session_retirement_code_is_recognised(self):
        """12010202 is what token/refresh answers for a retired session.

        Observed 2026-10-10 (HTTP 401, "Login status has expired, please login
        again") for a pair minted hours earlier, on an install whose session had
        been evicted by a later login on the same account. The same dead token
        earns 12010101 on an ordinary endpoint, which is why this one was missed:
        without it the refresh path retried a dead session every 45 s forever.
        """
        self.assertIn(12010202, errors.TOKEN_EXPIRED_CODES)


class TestIsTokenExpiredResponse(unittest.TestCase):
    def test_recognises_the_expiry_payload(self):
        """The exact shape the API returned, from the log."""
        self.assertTrue(
            errors.is_token_expired_response(
                {
                    "code": 12010101,
                    "data": None,
                    "msg": "Login status expired. Please log in again",
                    "desc": "Login status expired. Please log in again",
                }
            )
        )

    def test_recognises_the_refresh_session_retirement_payload(self):
        """The exact body /sso/app/v3/token/refresh returned, from the wire."""
        self.assertTrue(
            errors.is_token_expired_response(
                {
                    "code": 12010202,
                    "data": None,
                    "msg": "Anmeldestatus abgelaufen. Bitte melden Sie sich erneut an",
                    "desc": "Login status has expired, please login again",
                }
            )
        )

    def test_a_successful_payload_is_not_expired(self):
        self.assertFalse(
            errors.is_token_expired_response({"code": 0, "data": {"ok": True}})
        )

    def test_an_unrelated_error_code_is_not_expired(self):
        """Other failures must NOT trigger a token refresh and retry."""
        self.assertFalse(errors.is_token_expired_response({"code": 21310001}))

    def test_tolerates_shapes_that_are_not_dicts(self):
        """A false negative here is what caused the outage, so be permissive."""
        for payload in (None, [], "12010101", 12010101, {"data": None}):
            with self.subTest(payload=payload):
                self.assertFalse(errors.is_token_expired_response(payload))

    def test_a_string_code_is_not_silently_accepted(self):
        """Codes arrive as integers; a string is a different (unknown) shape."""
        self.assertFalse(errors.is_token_expired_response({"code": "12010101"}))


class TestChargingFeedback(unittest.TestCase):
    def test_10401_is_start_failure_not_hardware_unsupported(self):
        self.assertEqual(errors.command_response_message(10401),
                         "Charging could not be started (code 10401)")


class TestLoginExpiredError(unittest.TestCase):
    def test_carries_the_code_and_message(self):
        err = errors.LoginExpiredError(12010101, "Login status expired")
        self.assertEqual(err.code, 12010101)
        self.assertEqual(err.message, "Login status expired")
        self.assertIn("12010101", str(err))
        self.assertIn("Login status expired", str(err))

    def test_message_is_optional(self):
        err = errors.LoginExpiredError(12010104)
        self.assertEqual(err.code, 12010104)
        self.assertIn("12010104", str(err))

    def test_is_distinct_from_a_plain_value_error(self):
        """The distinction is the fix: a generic error was swallowed at DEBUG."""
        self.assertIsInstance(errors.LoginExpiredError(12010101), Exception)
        self.assertNotIsInstance(errors.LoginExpiredError(12010101), ValueError)


if __name__ == "__main__":
    unittest.main(verbosity=2)
