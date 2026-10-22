#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for login-expiry detection in create_mqtt_account. Standard library only.

    python3 tests/test_mqtt_account_expiry.py
    python3 -m unittest discover -s tests -v

This is the guard that the 2026-10-06 outage needed and did not have. The
transport is stubbed so the *placement* of the check can be tested: the expiry
payload carries `data: None`, which would otherwise fall through to the generic
"Unexpected response data" ValueError and be indistinguishable from a malformed
response — which is how it stayed invisible for 41 minutes.

What this cannot prove is what the server actually returns when a session
expires; that was never logged, because it was logged at DEBUG. It proves that
if it returns the documented expiry code, the guard fires in the right place and
takes precedence.
"""

from __future__ import annotations

import importlib.util
# Load the stdlib module before the component directory can shadow it.
import calendar  # noqa: F401
import json
import pathlib
# Cache the stdlib extension before component select.py can shadow it.
import select  # noqa: F401
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402

API_PATH = _component.module_path("xpeng_api.py")


def _stub_cryptography() -> None:
    """Stand in for `cryptography`, which xpeng_api imports at module scope."""
    try:
        import cryptography  # noqa: F401
        return
    except ImportError:
        pass

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    class _Any(types.ModuleType):
        def __getattr__(self, name):
            child = _Any(f"{self.__name__}.{name}")
            setattr(self, name, child)
            return child

    def fake_import(name, *args, **kwargs):
        if name == "cryptography" or name.startswith("cryptography."):
            return _Any(name)
        return real_import(name, *args, **kwargs)

    if isinstance(__builtins__, dict):
        __builtins__["__import__"] = fake_import
    else:
        __builtins__.__import__ = fake_import


def _load_api():
    api_dir = str(API_PATH.parent)
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    spec = importlib.util.spec_from_file_location("xpeng_api_expiry_under_test", API_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_stub_cryptography()
api = _load_api()


class ExpiryDetection(unittest.TestCase):
    def setUp(self):
        # Signing decrypts the access token, and the transport is what we are
        # replacing, so both are stubbed: the test is about the response handling.
        self._real_headers = api.build_mqtt_signed_headers
        self._real_send = api._send_api_request
        api.build_mqtt_signed_headers = lambda *a, **k: ({}, {})
        self.tenant = {"_key_pool": []}
        self.tokens = {"accessToken": "unused-because-headers-are-stubbed"}

    def tearDown(self):
        api.build_mqtt_signed_headers = self._real_headers
        api._send_api_request = self._real_send

    def _respond(self, payload: dict | str, status: int = 200) -> None:
        body = payload if isinstance(payload, str) else json.dumps(payload)
        api._send_api_request = lambda *a, **k: {"status": status, "body": body}

    def test_the_observed_expiry_payload_raises_login_expired(self):
        """The exact payload get_vehicles returned in the real outage."""
        self._respond(
            {
                "code": 12010101,
                "data": None,
                "msg": "Login status expired. Please log in again",
                "desc": "Login status expired. Please log in again",
            }
        )
        with self.assertRaises(api.LoginExpiredError) as ctx:
            api.create_mqtt_account(None, self.tenant, self.tokens)
        self.assertEqual(ctx.exception.code, 12010101)
        self.assertIn("Login status expired", str(ctx.exception))

    def test_the_expiry_check_beats_the_malformed_response_error(self):
        """This is the whole fix: it must not be a generic ValueError.

        With `data: None`, the old code raised "Unexpected response data: None",
        which is what made an expired login look like a bad response.
        """
        self._respond({"code": 12010101, "data": None, "msg": "Login status expired"})
        try:
            api.create_mqtt_account(None, self.tenant, self.tokens)
        except api.LoginExpiredError:
            pass
        except ValueError as err:  # pragma: no cover - the regression
            self.fail(f"raised a generic ValueError instead: {err}")
        else:  # pragma: no cover - the regression
            self.fail("no error raised")

    def test_the_second_code_also_raises_login_expired(self):
        self._respond({"code": 12010104, "data": None, "msg": "expired"})
        with self.assertRaises(api.LoginExpiredError) as ctx:
            api.create_mqtt_account(None, self.tenant, self.tokens)
        self.assertEqual(ctx.exception.code, 12010104)

    def test_an_unrelated_error_code_does_not_raise_login_expired(self):
        """Other failures must not trigger a token refresh and retry."""
        self._respond({"code": 21310001, "data": None, "msg": "not configured"})
        with self.assertRaises(ValueError) as ctx:
            api.create_mqtt_account(None, self.tenant, self.tokens)
        self.assertNotIsInstance(ctx.exception, api.LoginExpiredError)

    def test_http_403_with_the_expiry_body_raises_login_expired(self):
        """The shape the server ACTUALLY uses.

        Observed 2026-10-06 21:46:
            createMqttAccount HTTP 403: {"code":12010101,"data":null,
                                         "msg":"Login status expired..."}
        Expiry arrives as HTTP 403 carrying the code in the body, so a code
        check placed after the status check never runs. That is why the evening
        failure managed 53 consecutive refreshes over ~40 minutes without the
        recovery firing, and was only rescued when the REST path refreshed the
        tokens for its own reasons.
        """
        self._respond(
            {
                "code": 12010101,
                "data": None,
                "msg": "Login status expired. Please log in again",
                "desc": "Login status expired. Please log in again",
            },
            status=403,
        )
        with self.assertRaises(api.LoginExpiredError) as ctx:
            api.create_mqtt_account(None, self.tenant, self.tokens)
        self.assertEqual(ctx.exception.code, 12010101)

    def test_a_403_without_an_expiry_body_stays_a_connection_error(self):
        """Don't let real HTTP failures masquerade as an expired login.

        Parsing the body before the status check must not turn an unparseable
        error page into a token refresh and retry.
        """
        self._respond("<html>403 Forbidden</html>", status=403)
        with self.assertRaises(ConnectionError) as ctx:
            api.create_mqtt_account(None, self.tenant, self.tokens)
        self.assertNotIsInstance(ctx.exception, api.LoginExpiredError)

    def test_an_empty_body_does_not_crash_the_expiry_check(self):
        self._respond("", status=500)
        with self.assertRaises(ConnectionError):
            api.create_mqtt_account(None, self.tenant, self.tokens)


    def test_a_healthy_response_still_returns_credentials(self):
        """The guard must not break the working path."""
        self._respond(
            {
                "code": 0,
                "data": {
                    "mqttAccount": {
                        "clientId": "xmart:app:1:abc",
                        "username": "u",
                        "password": "p",
                        "serverSsl": "s",
                        "expireTime": 1791264882751,
                    }
                },
            }
        )
        out = api.create_mqtt_account(None, self.tenant, self.tokens)
        self.assertEqual(out["clientId"], "xmart:app:1:abc")
        self.assertEqual(out["password"], "p")


if __name__ == "__main__":
    unittest.main(verbosity=2)
