#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the MQTT client's side of raw capture.

    python3 tests/test_capture_wiring.py
    python3 -m unittest discover -s tests -v

``capture.py`` tests the recorder; this tests the two places the recorder is
plugged into, because those are where a capture silently does nothing:

* the client must hand the sink the **bytes**, before anything decodes them -
  the listener's old path decoded every payload as UTF-8 with
  ``errors="replace"``, which is lossy for exactly the binary frames a capture
  exists for;
* the expensive half of ``_handle_listener_message`` must not run when only
  capture is on. 0.4.30 proved that per-frame decode is load; this checks it is
  actually skipped rather than merely moved.

Loaded by path with a paho stub: ``mqtt_client`` imports paho, which is not
installed on a development host, but it does not import Home Assistant.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
# Cache the stdlib extension before component select.py can shadow it.
import select  # noqa: F401
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "custom_components" / "xpeng"))
import _component  # noqa: E402


def _stub_paho() -> None:
    try:
        import paho.mqtt.client  # noqa: F401
        return
    except ImportError:
        pass

    class _Any(types.ModuleType):
        def __getattr__(self, name):
            child = _Any(f"{self.__name__}.{name}")
            setattr(self, name, child)
            return child

    paho = _Any("paho")
    mqtt = _Any("paho.mqtt")
    client = _Any("paho.mqtt.client")
    mqtt.client = client
    paho.mqtt = mqtt
    sys.modules.setdefault("paho", paho)
    sys.modules.setdefault("paho.mqtt", mqtt)
    sys.modules.setdefault("paho.mqtt.client", client)


_stub_paho()
_SPEC = importlib.util.spec_from_file_location(
    "mqtt_client_under_test", _component.module_path("mqtt_client.py")
)
mqtt_client = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mqtt_client
_SPEC.loader.exec_module(mqtt_client)


def make_client() -> object:
    return mqtt_client.XPengMQTTClient(
        client_id="xmart:app:1:2",
        password="p",
        server="broker:8885",
        user_id="1",
        cert_dir="/tmp/does-not-need-to-exist",
    )


BINARY = bytes(range(256))  # not UTF-8, and not JSON: a CAN pack in shape if not in fact


class CaptureSink(unittest.TestCase):
    def test_the_sink_receives_the_exact_bytes(self) -> None:
        client = make_client()
        seen: list[tuple] = []
        client.set_capture_callback(lambda topic, payload, qos, retain: seen.append(
            (topic, payload, qos, retain)
        ))
        client._handle_listener_message("Xmart/Car/94434/State/CanPack1s", BINARY, qos=1, retain=True)
        self.assertEqual(len(seen), 1)
        topic, payload, qos, retain = seen[0]
        self.assertEqual(topic, "Xmart/Car/94434/State/CanPack1s")
        # Byte for byte: a decode with errors="replace" would have destroyed this.
        self.assertEqual(payload, BINARY)
        self.assertEqual((qos, retain), (1, True))

    def test_a_raising_sink_does_not_take_the_connection_down(self) -> None:
        client = make_client()

        def explode(*_args):
            raise RuntimeError("capture bug")

        client.set_capture_callback(explode)
        # Must not propagate: this runs on paho's network thread, where an escaping
        # exception kills the loop and takes the whole integration's MQTT with it.
        # It must not vanish either, so it is logged.
        with self.assertLogs(mqtt_client._LOGGER, level="ERROR") as captured:
            client._handle_listener_message("t", BINARY)
        self.assertIn("capture bug", "\n".join(captured.output))

    def test_no_sink_is_fine(self) -> None:
        make_client()._handle_listener_message("t", b"hello")

    def test_the_sink_is_offered_binary_before_anything_else_runs(self) -> None:
        client = make_client()
        client.set_capture_callback(lambda *args: None)
        client._handle_listener_message("t", BINARY)
        # The decode-and-log half never ran: no topic was announced, which is the
        # observable side effect of reaching the logging block.
        self.assertEqual(client._listener_logged_topics, set())


class LazyDecode(unittest.TestCase):
    """Capture-only installs must not pay the per-frame decode at all."""

    def test_listener_still_gets_a_decoded_string(self) -> None:
        client = make_client()
        got: list[tuple] = []
        client.set_listener_callback(lambda topic, payload: got.append((topic, payload)))
        client._handle_listener_message("t", b'{"msg_type": 1}')
        self.assertEqual(got, [("t", '{"msg_type": 1}')])
        # ...and it announced the topic, so the logging block did run.
        self.assertIn("t", client._listener_logged_topics)

    def test_binary_is_skipped_when_only_capture_is_on(self) -> None:
        client = make_client()
        client.set_capture_callback(lambda *args: None)
        client._handle_listener_message("t", BINARY)
        self.assertEqual(client._listener_logged_topics, set())

    def test_json_is_still_examined_when_only_capture_is_on(self) -> None:
        # A push notification can arrive on a captured topic, so a JSON payload
        # must keep reaching the notification path even with no listener callback.
        client = make_client()
        notifications: list[dict] = []
        client.set_notification_callback(notifications.append)
        client.set_capture_callback(lambda *args: None)
        client._handle_listener_message(
            "t", json.dumps({"msg_type": 3, "service_type": 19, "msg_content": {"a": 1}}).encode()
        )
        self.assertEqual(len(notifications), 1)


class ListenerTopics(unittest.TestCase):
    def test_capture_topics_are_subscribed_without_the_listener(self) -> None:
        client = make_client()
        client.configure_listener(
            enabled=True,
            topics=[],
            discovery_enabled=False,
            extra_topics=["Xmart/Car/+/State/CanPack1s"],
        )
        self.assertIn("Xmart/Car/+/State/CanPack1s", client._listener_topics)

    def test_extra_topics_do_not_duplicate_discovery_candidates(self) -> None:
        client = make_client()
        client.configure_listener(
            enabled=True,
            topics=[],
            discovery_enabled=True,
            discovery_candidates=("Xmart/Car/+/State/CanPack1s",),
            extra_topics=["Xmart/Car/+/State/CanPack1s"],
        )
        self.assertEqual(
            client._listener_topics.count("Xmart/Car/+/State/CanPack1s"), 1
        )

    def test_disabled_listener_does_not_subscribe(self) -> None:
        client = make_client()
        client.configure_listener(enabled=False, topics=[], discovery_enabled=False)
        self.assertFalse(client._listener_enabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
