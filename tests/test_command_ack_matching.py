"""Command responses must not be mistaken for notifications or overwritten."""

import threading
import unittest

from test_mqtt_state_delivery import _client


class AckMatching(unittest.TestCase):
    def make(self):
        c, _ = _client()
        e = {"event": threading.Event(), "result": None, "service_type": 4}
        c._pending = {"original-id": e}
        return c, e

    def test_notifications_and_command_echoes_do_not_resolve_waiter(self):
        c, e = self.make()
        for msg_type in (2, 3, 4, None):
            c._resolve_pending({"msg_id": "original-id", "msg_type": msg_type}, 4)
        self.assertFalse(e["event"].is_set())

    def test_first_reply_wins_even_if_a_second_arrives_before_waiter_wakes(self):
        c, e = self.make()
        refusal = {
            "msg_id": "rewritten-id",
            "msg_type": 1,
            "msg_content": {"code": 10401},
        }
        c._resolve_pending(refusal, 4)
        c._resolve_pending(
            {"msg_id": "original-id", "msg_type": 1, "msg_content": {"code": 200}}, 4
        )
        self.assertTrue(e["event"].is_set())
        self.assertIs(e["result"], refusal)

    def test_other_subsystem_does_not_satisfy_fallback(self):
        c, e = self.make()
        c._resolve_pending({"msg_type": 1}, 12)
        self.assertFalse(e["event"].is_set())


if __name__ == "__main__":
    unittest.main()
