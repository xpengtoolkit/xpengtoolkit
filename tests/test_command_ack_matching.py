"""Correlate commands across the real publish/wait/receive path without a car."""
from _import_isolation import ImportIsolation
_IMPORTS = ImportIsolation()

import json
import threading
from types import SimpleNamespace
import unittest

from test_mqtt_state_delivery import _client, _Msg


class AckMatching(unittest.TestCase):
    def make(self, **request):
        c, _ = _client()
        e = {"event": threading.Event(), "result": None, "service_type": 4,
             "request": request or {"cmd_type": 1, "cmd_value": 1}}
        c._pending = {"original-id": e}
        return c, e

    def test_notifications_and_command_echoes_do_not_resolve_waiter(self):
        c, e = self.make()
        for msg_type in (2, 3, 4, None):
            c._resolve_pending({"msg_id": "original-id", "msg_type": msg_type}, 4)
        self.assertFalse(e["event"].is_set())

    def test_first_correlated_reply_wins(self):
        c, e = self.make()
        refusal = {"msg_id": "original-id", "msg_type": 1, "msg_content": {"code": 10401}}
        c._resolve_pending(refusal, 4)
        c._resolve_pending({**refusal, "msg_content": {"code": 200}}, 4)
        self.assertTrue(e["event"].is_set())
        self.assertIs(e["result"], refusal)

    def test_other_subsystem_never_satisfies_even_an_exact_id(self):
        c, e = self.make()
        c._resolve_pending({"msg_id": "original-id", "msg_type": 1}, 12)
        self.assertFalse(e["event"].is_set())

    def test_generic_same_subsystem_reply_cannot_confirm_a_command(self):
        c, e = self.make()
        for code in (200, 10401):
            for msg_id in (None, "foreign-id"):
                c._resolve_pending({"msg_id": msg_id, "msg_type": 1,
                                    "msg_content": {"code": code}}, 4)
        self.assertFalse(e["event"].is_set())

    def test_rewritten_id_requires_both_echoed_request_fields(self):
        c, e = self.make()
        for echo in ({"cmd_type": 1}, {"cmd_value": 1},
                     {"cmd_type": 2, "cmd_value": 1},
                     {"cmd_type": 1, "cmd_value": 0}):
            c._resolve_pending({"msg_id": "rewritten", "msg_type": 1,
                                "msg_content": {"code": 200, **echo}}, 4)
        self.assertFalse(e["event"].is_set())
        c._resolve_pending({"msg_id": "rewritten", "msg_type": 1,
                            "msg_content": {"code": 200, "cmd_type": 1, "cmd_value": 1}}, 4)
        self.assertTrue(e["event"].is_set())

    def test_ambiguous_identical_waiters_do_not_accept_fallback(self):
        c, e = self.make()
        other = {**e, "event": threading.Event()}
        c._pending["second-id"] = other
        c._resolve_pending({"msg_type": 1, "msg_content": {
            "code": 200, "cmd_type": 1, "cmd_value": 1}}, 4)
        self.assertFalse(e["event"].is_set())
        self.assertFalse(other["event"].is_set())

    def test_exact_id_with_explicit_conflicting_request_is_rejected(self):
        c, e = self.make()
        c._resolve_pending({"msg_id": "original-id", "msg_type": 1,
                            "msg_content": {"code": 200, "cmd_type": 2, "cmd_value": 90}}, 4)
        self.assertFalse(e["event"].is_set())

    def test_publish_wait_receive_ignores_foreign_success_and_refusal(self):
        for subsystem in (3, 4, 12):
            with self.subTest(subsystem=subsystem):
                c, _ = _client()
                published, finished = threading.Event(), threading.Event()
                packets, result = [], []

                def publish(topic, payload, qos):
                    packets.append(json.loads(payload))
                    published.set()
                    return SimpleNamespace(rc=0)

                c._connected = True
                c._client = SimpleNamespace(publish=publish)

                def wait():
                    result.append(c.send_command_wait(
                        "SYNTHETIC_VIN", subsystem, {"cmd_type": 1, "cmd_value": 1}, timeout=1.5
                    ))
                    finished.set()

                worker = threading.Thread(target=wait)
                worker.start()
                try:
                    self.assertTrue(published.wait(1))
                    for code in (200, 10401):
                        c._on_message(None, None, _Msg({
                            "service_type": subsystem, "msg_type": 1,
                            "msg_id": "another-command", "msg_content": {"code": code},
                        }))
                        self.assertFalse(finished.wait(0.02))
                    own = {"service_type": subsystem, "msg_type": 1,
                           "msg_id": packets[0]["msg_id"], "msg_content": {"code": 200}}
                    c._on_message(None, None, _Msg(own))
                    self.assertTrue(finished.wait(1))
                    self.assertEqual(result, [own])
                finally:
                    worker.join(2)
                self.assertFalse(worker.is_alive())



    def test_vehicle_response_references_original_request_id(self):
        # P7+ cloud reply: response msg_id is new; msg_ref echoes the sent ID.
        for code in (200, 10401):
            with self.subTest(code=code):
                c, e = self.make()
                reply = {"msg_id": "vehicle-response-id", "msg_ref": "original-id",
                         "msg_type": 1, "msg_content": {"code": code}}
                c._resolve_pending(reply, 4)
                self.assertTrue(e["event"].is_set())
                self.assertIs(e["result"], reply)

    def test_reference_selects_one_of_identical_pending_commands(self):
        c, e = self.make()
        other = {**e, "event": threading.Event()}
        c._pending["second-id"] = other
        c._resolve_pending({"msg_id": "response-id", "msg_ref": "second-id",
                            "msg_type": 1, "msg_content": {"code": 200}}, 4)
        self.assertFalse(e["event"].is_set())
        self.assertTrue(other["event"].is_set())

    def test_stale_reference_never_falls_back_to_matching_echo(self):
        c, e = self.make()
        c._resolve_pending({"msg_id": "response-id", "msg_ref": "expired-id",
                            "msg_type": 1, "msg_content": {
                                "code": 200, "cmd_type": 1, "cmd_value": 1}}, 4)
        self.assertFalse(e["event"].is_set())

    def test_conflicting_pending_identifiers_are_rejected(self):
        c, e = self.make()
        other = {**e, "event": threading.Event()}
        c._pending["second-id"] = other
        c._resolve_pending({"msg_id": "second-id", "msg_ref": "original-id",
                            "msg_type": 1, "msg_content": {"code": 200}}, 4)
        self.assertFalse(e["event"].is_set())
        self.assertFalse(other["event"].is_set())

    def test_reference_does_not_bypass_subsystem_type_or_echo_checks(self):
        c, e = self.make()
        reply = {"msg_id": "response-id", "msg_ref": "original-id",
                 "msg_type": 1, "msg_content": {"code": 200}}
        c._resolve_pending(reply, 12)
        c._resolve_pending({**reply, "msg_type": 3}, 4)
        c._resolve_pending({**reply, "msg_content": {"code": 200, "cmd_value": 0}}, 4)
        self.assertFalse(e["event"].is_set())

    def test_invalid_reference_cannot_fall_back_to_an_exact_message_id(self):
        c, e = self.make()
        for ref in ([], {}, True, False, 1.5, ""):
            c._resolve_pending({"msg_id": "original-id", "msg_ref": ref,
                                "msg_type": 1, "msg_content": {"code": 200}}, 4)
        self.assertFalse(e["event"].is_set())

    def test_publish_wait_receive_accepts_referenced_vehicle_response(self):
        c, _ = _client()
        result = []
        def publish(topic, payload, qos):
            request = json.loads(payload)
            reply = {"service_type": 3, "msg_type": 1, "msg_id": "vehicle-response-id",
                     "msg_ref": request["msg_id"], "msg_content": {"code": 200,
                     "hvac_on": 1, "hvac_temp_float": 24.0}}
            result.append(reply)
            c._on_message(None, None, _Msg(reply))
            return SimpleNamespace(rc=0)
        c._connected = True
        c._client = SimpleNamespace(publish=publish)
        self.assertEqual(c.send_command_wait("SYNTHETIC_VIN", 3,
                         {"cmd_type": 1, "cmd_value": 1}, timeout=0.05), result[0])


_IMPORTS.finish(globals())

if __name__ == "__main__":
    unittest.main()
