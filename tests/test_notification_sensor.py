#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for XPengNotificationSensor state/attribute derivation.

    python3 tests/test_notification_sensor.py

`msg_type=3` push notifications already fire the `xpeng_notification` bus
event, but an event with no entity is invisible until someone hand-writes an
automation. `XPengNotificationSensor` exposes the same data as a readable
sensor. These tests pin its behaviour, including the deliberate choice that an
empty or absent notification reads as `unknown` rather than a meaningless
"received".

Home Assistant is not installed here and must not need to be, so `sensor.py` is
loaded by path with import-only stubs - the same approach as
`test_poll_status.py` and `test_scene_api.py`.
"""

from __future__ import annotations

import collections
import importlib.util
from datetime import datetime, timedelta
import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402

_SRC = _component.module_path("sensor.py")


def _load_sensor_module():
    """Load sensor.py with import-only Home Assistant stubs."""

    class _Auto:
        """Stands in for HA enums: any attribute resolves to its own name."""

        def __getattr__(self, name: str) -> str:
            return name

    class _EntityCategory:
        DIAGNOSTIC = "diagnostic"
        CONFIG = "config"

    class _Any:
        def __init__(self, *args, **kwargs) -> None:
            pass

    def _mod(name: str, **attrs):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
        return module

    _mod("homeassistant")
    _mod("homeassistant.components")
    _mod("homeassistant.components.sensor",
         SensorDeviceClass=_Auto(), SensorEntity=object, SensorStateClass=_Auto())
    _mod("homeassistant.config_entries", ConfigEntry=_Any)
    _mod("homeassistant.const", EntityCategory=_EntityCategory,
         UnitOfTemperature=_Auto(), UnitOfTime=_Auto())
    _mod("homeassistant.core", HomeAssistant=_Any, callback=lambda fn: fn)
    helpers = _mod("homeassistant.helpers")
    helpers.entity_registry = _mod("homeassistant.helpers.entity_registry")
    _mod("homeassistant.helpers.entity_platform", AddEntitiesCallback=_Any)
    _mod("homeassistant.helpers.event", async_track_time_change=_Any,
         async_track_time_interval=_Any)

    pkg = "custom_components.xpeng"
    _mod("custom_components")
    _mod(pkg, __path__=[str(_component.component_dir())])
    _mod(f"{pkg}.const", DOMAIN="xpeng", SENSOR_DESCRIPTIONS=(),
         XPengSensorEntityDescription=_Any)
    _mod(f"{pkg}.coordinator", XPengDataCoordinator=_Any)
    error_spec = importlib.util.spec_from_file_location(
        f"{pkg}.xpeng_errors", _component.module_path("xpeng_errors.py")
    )
    error_module = importlib.util.module_from_spec(error_spec)
    sys.modules[error_spec.name] = error_module
    error_spec.loader.exec_module(error_module)
    # The real module, not a stub: poll_status is Home Assistant-free by design, and
    # sensor.py now calls its delay arithmetic, so stubbing it would test nothing and
    # hand the attributes a non-numeric stand-in.
    _poll_spec = importlib.util.spec_from_file_location(
        f"{pkg}.poll_status", _component.module_path("poll_status.py")
    )
    assert _poll_spec and _poll_spec.loader
    _poll = importlib.util.module_from_spec(_poll_spec)
    sys.modules[f"{pkg}.poll_status"] = _poll
    _poll_spec.loader.exec_module(_poll)

    class _XPengEntity:
        def __init__(self, coordinator) -> None:
            self.coordinator = coordinator

        @property
        def _vin(self) -> str:
            return "L1NNSGHA1SB000001"

    _mod(f"{pkg}.entity", XPengEntity=_XPengEntity)
    _mod(f"{pkg}.vin_descriptor", mask_vin=lambda vin: vin[:3] + "***")

    spec = importlib.util.spec_from_file_location(f"{pkg}.sensor", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg}.sensor"] = module
    spec.loader.exec_module(module)
    return module


_sensor = _load_sensor_module()

# The notification sensor was added in the shipping copy; the live/private tree
# does not have it yet. Skip rather than die at import time, so this file is
# runnable against either tree.
if not hasattr(_sensor, "XPengNotificationSensor"):
    # Exit 0 rather than raising: unittest only honours SkipTest raised *inside*
    # a test, so a module-level raise makes the file fail and the run go red for
    # a feature that simply is not in this tree.
    print(
        f"SKIP: XPengNotificationSensor is not implemented in "
        f"{_component.component_dir().name}"
    )
    raise SystemExit(0)

XPengNotificationSensor = _sensor.XPengNotificationSensor


class _FakeCoordinator:
    def __init__(self, notes=(), count=0) -> None:
        self.notifications = collections.deque(notes, maxlen=50)
        self.notification_count = count


class TestNotificationSensor(unittest.TestCase):
    """State and attribute derivation for the notification sensor."""

    def test_no_notifications_is_unknown(self):
        ent = XPengNotificationSensor(_FakeCoordinator())
        self.assertIsNone(ent.native_value)
        self.assertEqual(ent.extra_state_attributes["count"], 0)

    def test_message_is_the_state(self):
        ent = XPengNotificationSensor(_FakeCoordinator(
            [{"message": "Windows open", "code": 200, "service_type": 12}], 1))
        self.assertEqual(ent.native_value, "Windows open")
        attrs = ent.extra_state_attributes
        self.assertEqual(attrs["count"], 1)
        self.assertEqual(attrs["service_type"], 12)
        self.assertEqual(attrs["code"], 200)

    def test_code_only_falls_back_to_code(self):
        ent = XPengNotificationSensor(_FakeCoordinator([{"code": 404}], 3))
        self.assertEqual(ent.native_value, "code 404")
        self.assertEqual(ent.extra_state_attributes["count"], 3)

    def test_empty_notification_dict_is_unknown(self):
        """An empty dict is falsy -> unknown.

        Asserting "received" would put a meaningless state on a sensor that
        exists to show what the car actually said.
        """
        ent = XPengNotificationSensor(_FakeCoordinator([{}], 1))
        self.assertIsNone(ent.native_value)

    def test_latest_notification_wins(self):
        ent = XPengNotificationSensor(_FakeCoordinator(
            [{"message": "old"}, {"message": "new"}], 2))
        self.assertEqual(ent.native_value, "new")
        self.assertEqual(ent.extra_state_attributes["message"], "new")

    def test_recent_history_is_capped_at_five(self):
        notes = [{"message": f"n{i}"} for i in range(9)]
        ent = XPengNotificationSensor(_FakeCoordinator(notes, 9))
        recent = ent.extra_state_attributes["recent"]
        self.assertEqual(len(recent), 5)
        self.assertEqual(recent[-1]["message"], "n8")

    def test_tolerates_coordinator_without_notification_attrs(self):
        """Must not explode against an older coordinator."""

        class _Bare:
            pass

        ent = XPengNotificationSensor(_Bare())
        self.assertIsNone(ent.native_value)
        self.assertEqual(ent.extra_state_attributes["count"], 0)
        self.assertEqual(ent.extra_state_attributes["recent"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

    def test_the_delay_is_reported_with_the_collection_time(self):
        """The whole point of the sensor for a polled source.

        Notifications are not pushed: they are polled, so the delay is the interval
        plus whatever the API took. It used to be two timestamps to subtract by hand,
        against a collection time that only the recorder held.
        """
        sent = datetime.now() - timedelta(minutes=4)
        ent = XPengNotificationSensor(_FakeCoordinator(
            [{"message": "Charging started", "timestamp": sent.isoformat()}], 1))
        attrs = ent.extra_state_attributes
        self.assertIn("delay_s", attrs)
        self.assertIn("collected_at", attrs)
        self.assertAlmostEqual(attrs["delay_s"], 240, delta=10)

    def test_an_unusable_timestamp_reports_no_delay(self):
        """A wrong delay is worse than none: it would be used to tune the interval."""
        ent = XPengNotificationSensor(_FakeCoordinator(
            [{"message": "x", "timestamp": "not a time"}], 1))
        self.assertIsNone(ent.extra_state_attributes["delay_s"])
