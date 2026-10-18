#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the capture tool's argument handling, planning and file rotation.

    python3 tests/test_can_capture.py
    python3 -m unittest discover -s tests -v

No network and no paho: everything here runs before the tool touches a broker.
That is deliberate - the decisions that decide whether a capture is *useful*
all happen up front:

* which topics get subscribed (your car, or every car on the broker);
* whether the session-takeover guard fires, since the failure it prevents is
  Home Assistant's own connection being evicted mid-capture;
* whether a long capture rolls to a new part instead of hitting the byte cap
  and stopping early.

The last one is worth a test on its own because "the file rotated" and "the
capture silently stopped" look identical in a summary line.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import xpeng_can_capture as cap  # noqa: E402
import xpeng_ha_credentials as creds  # noqa: E402

DEVICE_ID = "deadbeefdeadbeefdeadbeefdeadbeef"


def make_entry_file(directory: str) -> str:
    store = {
        "version": 2,
        "data": {
            "entries": [
                {
                    "entry_id": "ABC123",
                    "domain": "xpeng",
                    "title": "XPENG 123456",
                    "unique_id": "L1NNSGHAXSB123456",
                    "data": {
                        "client_id": f"xmart:app:123456:{DEVICE_ID}",
                        "password": "cached",
                        "server": "iot-app-global.xiaopeng.com:8885",
                        "vin": "L1NNSGHAXSB123456",
                        "login_tokens": {"accessToken": "tok", "uid": "123456"},
                        "tenant_data": {
                            "deviceId": DEVICE_ID,
                            "keys": {"1": base64.b64encode(b"0123456789abcdef").decode()},
                        },
                    },
                    "options": {"configured_device_id": DEVICE_ID},
                }
            ]
        },
    }
    path = os.path.join(directory, "core.config_entries")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(store, handle)
    return path


def pair() -> creds.CredentialPair:
    return creds.CredentialPair(
        tokens={"accessToken": "tok"}, tenant={"keys": {"1": "x"}},
        device_id=DEVICE_ID, vin="L1NNSGHAXSB123456", uid="123456",
    )


def args_for(*argv: str):
    return cap.build_parser().parse_args(list(argv))


class PlanTopics(unittest.TestCase):
    def test_default_subscribes_your_own_user_channels(self) -> None:
        topics = cap.plan_topics(pair(), args_for("--dry-run"))
        self.assertIn("Xmart/Msg/User/123456", topics)
        self.assertIn("Xmart/MsgV2/User/123456", topics)

    def test_default_includes_the_can_candidates(self) -> None:
        topics = cap.plan_topics(pair(), args_for("--dry-run"))
        self.assertIn("Xmart/Car/+/State/CanPack1s", topics)
        self.assertIn("Xmart/Tbox/+/CanPack1s", topics)

    def test_vid_narrows_only_the_car_topics(self) -> None:
        topics = cap.plan_topics(pair(), args_for("--dry-run", "--vid", "94434"))
        self.assertIn("Xmart/Car/94434/State/CanPack1s", topics)
        self.assertIn("Xmart/Car/94434/CanPackV2/Repub", topics)
        self.assertNotIn("Xmart/Car/+/State/CanPack1s", topics)
        # The T-box family is keyed by SIM, not by vid, so it stays wide.
        self.assertIn("Xmart/Tbox/+/CanPack1s", topics)

    def test_no_can_candidates_leaves_only_explicit_topics(self) -> None:
        topics = cap.plan_topics(pair(), args_for("--dry-run", "--no-can-candidates", "-w", "Xmart/x"))
        self.assertEqual(topics, ["Xmart/x", "Xmart/Msg/User/123456", "Xmart/MsgV2/User/123456"])

    def test_duplicates_are_dropped_and_order_kept(self) -> None:
        topics = cap.plan_topics(
            pair(),
            args_for("--dry-run", "-w", "Xmart/Msg/User/123456", "-w", "Xmart/Car/+/State/CanPack1s"),
        )
        self.assertEqual(len(topics), len(set(topics)))
        self.assertEqual(topics[0], "Xmart/Msg/User/123456")

    def test_wide_topics_named_as_wide(self) -> None:
        topics = cap.plan_topics(pair(), args_for("--dry-run", "-w", "#"))
        self.assertIn("#", cap.wide_topics(topics))
        self.assertIn("Xmart/Car/+/State/CanPack1s", cap.wide_topics(topics))
        self.assertNotIn("Xmart/Msg/User/123456", cap.wide_topics(topics))

    def test_shared_subscriptions_are_not_defaulted(self) -> None:
        # Subscribing to a $queue group takes messages away from the other
        # consumers in it, so it must never appear unasked.
        topics = cap.plan_topics(pair(), args_for("--dry-run"))
        self.assertFalse([t for t in topics if "$queue" in t or "$share" in t])


class CliGuards(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.entry_file = make_entry_file(self.dir.name)

    def test_dry_run_needs_no_takeover_flag_and_no_network(self) -> None:
        out = self.dir.name
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = cap.main(["--dry-run", "--entry-file", self.entry_file, "--out", out])
        self.assertEqual(code, 0)
        # The plan has to name the broker-scoped topics, because that is the
        # decision the operator is being asked to make.
        self.assertIn("Xmart/Car/+/State/CanPack1s", printed.getvalue())

    def test_default_mode_needs_no_consent(self) -> None:
        # The default derives its own client id, so it cannot evict anything: the
        # worst case is that the broker refuses it.
        self.assertEqual(cap.takeover_refusal(args_for("--dry-run")), "")
        self.assertEqual(
            cap.takeover_refusal(args_for("--dry-run", "--client-id-mode", "unique")), ""
        )

    def test_shared_mode_requires_consent(self) -> None:
        refusal = cap.takeover_refusal(args_for("--dry-run", "--client-id-mode", "shared"))
        self.assertIn("--confirm-session-takeover", refusal)
        self.assertEqual(
            cap.takeover_refusal(
                args_for("--dry-run", "--client-id-mode", "shared", "--confirm-session-takeover")
            ),
            "",
        )

    def test_shared_mode_is_refused_before_a_socket_is_opened(self) -> None:
        code = cap.main([
            "--entry-file", self.entry_file,
            "--out", os.path.join(self.dir.name, "c.xpgcan"),
            "--client-id-mode", "shared",
            "--duration", "1",
        ])
        self.assertEqual(code, 3)
        self.assertFalse(os.path.exists(os.path.join(self.dir.name, "c.xpgcan")))


class CredentialPrecedence(unittest.TestCase):
    """pair_dir is the inter-app hand-off; it must win only when it is complete."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.pair_dir = os.path.join(self.dir.name, "xpeng-secrets")
        os.makedirs(self.pair_dir)
        self.entry_file = make_entry_file(self.dir.name)

    def _write_pair(self) -> None:
        with open(os.path.join(self.pair_dir, "xpeng_tokens.json"), "w", encoding="utf-8") as fh:
            json.dump({"json": {"accessToken": "from-pair-dir", "uid": "555"}}, fh)
        with open(os.path.join(self.pair_dir, "xpeng_tenant.json"), "w", encoding="utf-8") as fh:
            json.dump({"deviceId": "c" * 32, "keys": {"1": "AAAA"}}, fh)

    def test_incomplete_pair_dir_falls_through_to_the_entry(self) -> None:
        # Only the token half present: that is not a pair, and using it would fail
        # later with a much worse message.
        with open(os.path.join(self.pair_dir, "xpeng_tokens.json"), "w", encoding="utf-8") as fh:
            json.dump({"json": {"accessToken": "half"}}, fh)
        self.assertFalse(creds.pair_dir_present(self.pair_dir))
        pair = cap.resolve_pair(args_for(
            "--dry-run", "--entry-file", self.entry_file, "--pair-dir", self.pair_dir))
        self.assertIn("config entry", pair.origin)

    def test_complete_pair_dir_wins_over_the_entry(self) -> None:
        self._write_pair()
        pair = cap.resolve_pair(args_for(
            "--dry-run", "--entry-file", self.entry_file, "--pair-dir", self.pair_dir))
        self.assertIn("pair_dir", pair.origin)
        self.assertEqual(pair.uid, "555")

    def test_no_pair_dir_flag_ignores_it(self) -> None:
        self._write_pair()
        pair = cap.resolve_pair(args_for(
            "--dry-run", "--entry-file", self.entry_file,
            "--pair-dir", self.pair_dir, "--no-pair-dir"))
        self.assertIn("config entry", pair.origin)

    def test_explicit_tokens_beat_pair_dir(self) -> None:
        self._write_pair()
        tokens = os.path.join(self.dir.name, "t.json")
        tenant = os.path.join(self.dir.name, "te.json")
        with open(tokens, "w", encoding="utf-8") as fh:
            json.dump({"accessToken": "explicit", "uid": "777"}, fh)
        with open(tenant, "w", encoding="utf-8") as fh:
            json.dump({"deviceId": "d" * 32, "keys": {"1": "AAAA"}}, fh)
        pair = cap.resolve_pair(args_for(
            "--dry-run", "--entry-file", self.entry_file, "--pair-dir", self.pair_dir,
            "--tokens", tokens, "--tenant", tenant))
        self.assertEqual(pair.uid, "777")


class ConnectionIdentity(unittest.TestCase):
    """The client id is the whole coexistence question, so it gets its own tests."""

    def _recorder(self, mode: str):
        args = args_for("--dry-run", "--client-id-mode", mode)
        recorder = cap.Recorder(pair(), args, ["Xmart/test"])
        recorder._creds = {"clientId": "xmart:app:123456:abc123", "username": ""}
        return recorder

    def test_unique_derives_a_different_client_id(self) -> None:
        client_id, username = self._recorder("unique")._connection_identity()
        self.assertNotEqual(client_id, "xmart:app:123456:abc123")
        self.assertTrue(client_id.startswith("xmart:app:123456:abc123"))
        # The username is what the broker authorises, so it must not change.
        self.assertEqual(username, "xmart:app:123456:abc123")

    def test_unique_is_stable_across_rotations(self) -> None:
        recorder = self._recorder("unique")
        first = recorder._connection_identity()
        recorder._creds["clientId"] = "xmart:app:123456:def456"
        second = recorder._connection_identity()
        # A fresh suffix per rotation would open a new session every 45s.
        self.assertEqual(first[0].split("-cap")[1], second[0].split("-cap")[1])

    def test_shared_reproduces_the_integration_identity(self) -> None:
        client_id, username = self._recorder("shared")._connection_identity()
        self.assertEqual(client_id, "xmart:app:123456:abc123")
        self.assertEqual(username, "xmart:app:123456:abc123")

    def test_minted_username_is_preferred_when_the_api_returns_one(self) -> None:
        recorder = self._recorder("unique")
        recorder._creds["username"] = "xmart:app:123456:abc123"
        _client_id, username = recorder._connection_identity()
        self.assertEqual(username, "xmart:app:123456:abc123")

    def test_missing_entry_file_exits_two(self) -> None:
        code = cap.main(["--dry-run", "--entry-file", "/nope/core.config_entries"])
        self.assertEqual(code, 2)

    def test_tokens_without_tenant_is_refused(self) -> None:
        code = cap.main(["--dry-run", "--tokens", "/tmp/t.json"])
        self.assertEqual(code, 2)


class Rotation(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def test_rolls_to_a_second_part_instead_of_stopping(self) -> None:
        out = os.path.join(self.dir.name, "c.xpgcan")
        args = args_for("--out", out, "--rotate-mb", "0.00001", "--max-mb", "100")
        recorder = cap.Recorder(pair(), args, ["Xmart/test"])
        self.addCleanup(recorder.journal.close if recorder.journal else lambda: None)
        recorder._open_journal({"tool": "test"})
        self.addCleanup(lambda: recorder.journal and not recorder.journal.closed and recorder.journal.close())
        for index in range(4):
            recorder.journal.message("Xmart/test", bytes(range(index + 1)))
        recorder._roll_if_needed()
        self.assertEqual(recorder._part, 2)
        self.assertTrue(os.path.exists(out))
        self.assertTrue(os.path.exists(os.path.join(self.dir.name, "c-part2.xpgcan")))
        # Part 1 is closed cleanly, with the reason recorded in its own index.
        first = json.load(open(out + ".index.json", encoding="utf-8"))
        self.assertEqual(first["reason"], "rotated")

    def test_no_rotation_when_disabled(self) -> None:
        out = os.path.join(self.dir.name, "c.xpgcan")
        args = args_for("--out", out, "--rotate-mb", "0")
        recorder = cap.Recorder(pair(), args, ["Xmart/test"])
        self.addCleanup(recorder.journal.close if recorder.journal else lambda: None)
        recorder._open_journal({"tool": "test"})
        self.addCleanup(lambda: recorder.journal and not recorder.journal.closed and recorder.journal.close())
        for index in range(20):
            recorder.journal.message("Xmart/test", bytes(range(index + 1)))
        recorder._roll_if_needed()
        self.assertEqual(recorder._part, 1)

    def test_first_part_keeps_the_requested_name(self) -> None:
        out = os.path.join(self.dir.name, "named.xpgcan")
        args = args_for("--out", out)
        recorder = cap.Recorder(pair(), args, ["Xmart/test"])
        self.addCleanup(recorder.journal.close if recorder.journal else lambda: None)
        recorder._open_journal({"tool": "test"})
        self.addCleanup(lambda: recorder.journal and not recorder.journal.closed and recorder.journal.close())
        self.assertEqual(recorder._parts, [out])
        self.assertEqual(recorder.journal._meta["journal_version"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
