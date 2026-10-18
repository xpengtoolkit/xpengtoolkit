#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for reading the integration's credentials into a capture tool.

    python3 tests/test_can_credentials.py
    python3 -m unittest discover -s tests -v

Three things are worth proving here, because each one is a way to produce an
empty capture and call it silence:

* the pair is found in all three places an install keeps it (the entry's own
  copy, the legacy file options, and the cached MQTT account);
* a device-id mismatch is refused rather than attempted - that install answers
  ``12010104`` to every call, so a capture would record nothing at all;
* nothing secret reaches a caller's output. ``describe()`` is printed into the
  capture's own metadata and onto the terminal, and a credential that leaks
  there leaks into every copy of the capture.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import xpeng_ha_credentials as C  # noqa: E402

DEVICE_ID = "deadbeefdeadbeefdeadbeefdeadbeef"
ACCESS_TOKEN = "eyJhbGciOiJIUzI1NiJ9.PAYLOAD.SECRETTAIL"


def keys_pool() -> dict:
    return {"1": base64.b64encode(b"0123456789abcdef").decode("ascii")}


def entry(**overrides) -> dict:
    base = {
        "entry_id": "ABC123",
        "domain": "xpeng",
        "title": "XPENG 123456",
        "unique_id": "L1NNSGHAXSB123456",
        "data": {
            "client_id": f"xmart:app:123456:{DEVICE_ID}",
            "password": "cached-password",
            "server": "iot-app-global.xiaopeng.com:8885",
            "vin": "L1NNSGHAXSB123456",
            "login_tokens": {
                "accessToken": ACCESS_TOKEN,
                "uid": "123456",
                "account_email": "someone@example.com",
            },
            "tenant_data": {"deviceId": DEVICE_ID, "keys": keys_pool()},
        },
        "options": {"configured_device_id": DEVICE_ID},
    }
    data_overrides = overrides.pop("data", None)
    base.update(overrides)
    if data_overrides:
        base["data"].update(data_overrides)
    return base


class PairFromEntry(unittest.TestCase):
    def test_entry_copy_is_used_first(self) -> None:
        pair = C.pair_from_entry(entry())
        self.assertEqual(pair.uid, "123456")
        self.assertEqual(pair.vin, "L1NNSGHAXSB123456")
        self.assertEqual(pair.device_id, DEVICE_ID)
        self.assertIn("login_tokens", pair.origin)
        self.assertTrue(pair.can_mint)

    def test_cached_mqtt_account_is_carried_through(self) -> None:
        pair = C.pair_from_entry(entry())
        self.assertEqual(pair.cached_password, "cached-password")
        self.assertEqual(pair.cached_server, "iot-app-global.xiaopeng.com:8885")

    def test_uid_derived_from_client_id_when_tokens_lack_one(self) -> None:
        tokens = {"accessToken": ACCESS_TOKEN}
        pair = C.pair_from_entry(entry(data={"login_tokens": tokens}))
        self.assertEqual(pair.uid, "123456")

    def test_cannot_mint_without_a_key_pool(self) -> None:
        pair = C.pair_from_entry(entry(data={"tenant_data": {"deviceId": DEVICE_ID}}))
        self.assertFalse(pair.can_mint)

    def test_device_id_mismatch_is_refused(self) -> None:
        other = "f" * 32
        with self.assertRaises(C.CredentialError) as caught:
            C.pair_from_entry(entry(options={"configured_device_id": other}))
        message = str(caught.exception)
        # The code is what makes this diagnosable; it is the exact answer the
        # API gives, and the one a search finds.
        self.assertIn("12010104", message)

    def test_matching_ids_are_not_refused(self) -> None:
        pair = C.pair_from_entry(entry(options={"configured_device_id": DEVICE_ID}))
        self.assertEqual(pair.device_id, DEVICE_ID)


class SecretsStayIn(unittest.TestCase):
    def test_describe_carries_no_secret(self) -> None:
        pair = C.pair_from_entry(entry())
        rendered = json.dumps(pair.describe())
        self.assertNotIn(ACCESS_TOKEN, rendered)
        self.assertNotIn("cached-password", rendered)
        self.assertNotIn(DEVICE_ID, rendered)  # prefix only

    def test_describe_is_safe_for_metadata(self) -> None:
        described = C.pair_from_entry(entry()).describe()
        self.assertEqual(described["vin"], "L1NNSGHAXSB123456")
        self.assertTrue(described["device_id"].endswith("…"))

    def test_auth_probe_uses_the_tail(self) -> None:
        # A token's prefix is identical across mints, so a prefix would not show
        # that anything changed.
        probe = C.pair_from_entry(entry()).auth_probe()
        self.assertIn("TAIL", probe.upper())
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", probe)


class FileFallback(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.config_dir = self.dir.name
        secrets = os.path.join(self.config_dir, "xpeng-secrets")
        os.makedirs(secrets)
        # The onboarding tool writes this wrapper; other paths write the bare object.
        with open(os.path.join(secrets, "tokens.json"), "w", encoding="utf-8") as fh:
            json.dump({"json": {"accessToken": ACCESS_TOKEN, "uid": "123456"}}, fh)
        with open(os.path.join(secrets, "tenant.json"), "w", encoding="utf-8") as fh:
            json.dump({"deviceId": DEVICE_ID, "keys": keys_pool()}, fh)

    def test_container_paths_are_remapped(self) -> None:
        target = entry(
            data={"login_tokens": None, "tenant_data": None},
            options={
                "configured_device_id": DEVICE_ID,
                "token_file": "/config/xpeng-secrets/tokens.json",
                "tenant_file": "/config/xpeng-secrets/tenant.json",
            },
        )
        target["data"].pop("login_tokens")
        target["data"].pop("tenant_data")
        pair = C.pair_from_entry(target, config_dir=self.config_dir)
        self.assertTrue(pair.can_mint)
        self.assertEqual(pair.uid, "123456")
        self.assertIn("token file", pair.origin)

    def test_pair_from_files_directly(self) -> None:
        pair = C.pair_from_files(
            os.path.join(self.config_dir, "xpeng-secrets", "tokens.json"),
            os.path.join(self.config_dir, "xpeng-secrets", "tenant.json"),
        )
        self.assertEqual(pair.device_id, DEVICE_ID)
        self.assertTrue(pair.can_mint)

    def test_missing_file_says_which_one(self) -> None:
        with self.assertRaises(C.CredentialError) as caught:
            C.pair_from_files("/nope/tokens.json", "/nope/tenant.json")
        self.assertIn("tokens.json", str(caught.exception))


class StorageAndSelection(unittest.TestCase):
    def _store(self, entries: list[dict]) -> dict:
        return {"version": 2, "data": {"entries": entries}}

    def test_entries_filtered_by_domain(self) -> None:
        store = self._store([entry(), {"entry_id": "X", "domain": "mqtt"}])
        found = C.entries_from_storage(store)
        self.assertEqual([e["entry_id"] for e in found], ["ABC123"])

    def test_not_a_config_entries_file(self) -> None:
        with self.assertRaises(C.CredentialError):
            C.entries_from_storage({"data": {}})

    def test_no_xpeng_entry(self) -> None:
        with self.assertRaises(C.CredentialError):
            C.select_entry([])

    def test_ambiguous_choice_is_refused_not_guessed(self) -> None:
        second = entry(entry_id="DEF456", title="XPENG 999999")
        with self.assertRaises(C.CredentialError) as caught:
            C.select_entry([entry(), second])
        self.assertIn("--entry-id", str(caught.exception))

    def test_select_by_entry_id(self) -> None:
        second = entry(entry_id="DEF456")
        self.assertEqual(C.select_entry([entry(), second], entry_id="DEF456")["entry_id"], "DEF456")

    def test_unknown_entry_id_lists_the_real_ones(self) -> None:
        with self.assertRaises(C.CredentialError) as caught:
            C.select_entry([entry()], entry_id="NOPE")
        self.assertIn("ABC123", str(caught.exception))

    def test_select_by_vin(self) -> None:
        second = entry(entry_id="DEF456", unique_id="L1NNSGHA9SB999999")
        chosen = C.select_entry([entry(), second], vin="L1NNSGHA9SB999999")
        self.assertEqual(chosen["entry_id"], "DEF456")

    def test_single_entry_needs_no_disambiguation(self) -> None:
        self.assertEqual(C.select_entry([entry()])["entry_id"], "ABC123")


if __name__ == "__main__":
    unittest.main(verbosity=2)
