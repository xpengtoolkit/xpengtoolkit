#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A parked car's SoC cannot rise, and a fallback merge must not be undone.

    python3 tests/test_energy_guard.py
    python3 -m unittest discover -s tests -v

Two measurements, one rule each, both from the reference install on
2026-10-23 (see energy_guard.py for the full story):

* 19:27:10 the REST fallback merged the live 49% into a state whose last push
  had frozen at 50% (17:49:42). Every ~15 min a charge-less partial then
  re-broadcast the MQTT client's own pre-merge baseline and put the 50 back;
  the battery and range sensors flipped for an hour. The merge has to teach
  the client's baseline what it decided - pinned below against the real
  mqtt_client, and again as a coordinator-level adoption.
* A G9 that sat parked all day showed a high value frozen for hours with
  notches down to a declining one, because the fan-out replayed the pre-park
  snapshot. The odometer guard cannot see that replay (a parked car's odometer
  does not move), so the energy itself has to answer it: while the car is
  parked and not charging, a state of charge above the held one is older
  snapshot, not data.

``coordinator.py`` is loaded with stubbed Home Assistant modules, as in
``test_command_state_merge.py``, because the guards under test are methods on
a class that inherits one. ``energy_guard`` keeps no Home Assistant import and
is loaded by path.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402

PKG_DIR = _component.component_dir()
_component.assert_is_default_target()


# -- energy_guard, by path (no Home Assistant import) -----------------------

_SPEC = importlib.util.spec_from_file_location(
    "energy_guard_under_test", _component.module_path("energy_guard.py")
)
assert _SPEC and _SPEC.loader
energy_guard = importlib.util.module_from_spec(_SPEC)
sys.modules["energy_guard_under_test"] = energy_guard
_SPEC.loader.exec_module(energy_guard)


def _state(soc=None, *, parked=True, charging=None, power=None, odo=62234):
    """A coordinator-shaped state, with the fields each rule reads."""
    instant: dict = {}
    if odo is not None:
        instant["odometer"] = {"total_mileage": odo}
    if parked is None:
        drive = {}
    elif parked:
        drive = {"shift_state": 4, "speed": 0}
    else:
        drive = {"shift_state": 2, "speed": 55}
    instant["drive"] = drive
    charge: dict = {}
    if soc is not None:
        charge["battery_soc"] = soc
    if charging is True:
        charge["charging_state"] = 2
    if charging is False:
        charge["charging_state"] = 1
    if charge:
        instant["charge"] = charge
    content: dict = {"instant": instant}
    if power is not None:
        content["interval"] = {"charge": {"power": power}}
    return {"msg_content": content, "vin": "TESTVIN0000000000"}


class TheChargeDetectorReadsBothSignals(unittest.TestCase):
    def test_charging_state_two_is_charging(self):
        self.assertTrue(energy_guard.state_is_charging(_state(49, charging=True)))

    def test_positive_charge_power_is_charging(self):
        """EVCC and friends can charge before the car admits a session."""
        self.assertTrue(energy_guard.state_is_charging(_state(49, power="7.2")))

    def test_zero_power_is_not_charging(self):
        self.assertFalse(energy_guard.state_is_charging(_state(49, power=0)))

    def test_no_state_is_not_charging(self):
        for state in (None, {}, {"msg_content": None}):
            with self.subTest(state=state):
                self.assertFalse(energy_guard.state_is_charging(state))


class AParkedRiseIsAnOlderSnapshot(unittest.TestCase):
    def _replay(self, candidate, held, **kwargs):
        return energy_guard.parked_soc_replay(
            candidate, held, parked=True, charging=False, **kwargs
        )

    def test_the_g9_shape_is_a_replay(self):
        """Held 94 after the fallback merge; the fanout replays 96.5 -> 97."""
        self.assertTrue(self._replay(_state(97), _state(94)))

    def test_a_one_percent_rise_is_inside_the_noise(self):
        """Whole-percent resolution (and a BMS wobble while sitting)."""
        self.assertFalse(self._replay(_state(95), _state(94)))
        self.assertFalse(self._replay(_state(94), _state(94)))

    def test_a_fall_is_not_a_replay(self):
        self.assertFalse(self._replay(_state(93), _state(94)))

    def test_charging_is_the_legitimate_reason_to_rise(self):
        self.assertFalse(
            energy_guard.parked_soc_replay(
                _state(60), _state(55), parked=True, charging=True
            )
        )

    def test_no_parked_verdict_means_no_verdict(self):
        """No evidence of parking is not evidence of parking."""
        for parked in (False, None):
            with self.subTest(parked=parked):
                self.assertFalse(
                    energy_guard.parked_soc_replay(
                        _state(97), _state(94), parked=parked, charging=False
                    )
                )

    def test_a_moving_car_can_regen(self):
        """While driving the guard must stay out of the way."""
        candidate = _state(60, parked=False)
        held = _state(58, parked=False)
        self.assertFalse(
            energy_guard.parked_soc_replay(
                candidate, held, parked=None, charging=False
            )
        )

    def test_missing_values_mean_no_verdict(self):
        for candidate, held in (
            (_state(None), _state(94)),
            (_state(97), _state(None)),
            (None, _state(94)),
            (_state(97), None),
        ):
            with self.subTest(candidate=candidate, held=held):
                self.assertFalse(self._replay(candidate, held))


# -- coordinator.py with a Home Assistant just big enough to import it ------


class _CoordinatorBase:
    """Stand-in for DataUpdateCoordinator (subscriptable, never constructed)."""

    def __class_getitem__(cls, item):  # noqa: N805
        return cls

    def __init__(self, *args, **kwargs):
        pass

    def async_set_updated_data(self, data):
        self.data = data


def _install_ha_stubs():
    ha = types.ModuleType("homeassistant")

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    core.callback = lambda fn: fn

    helpers = types.ModuleType("homeassistant.helpers")
    storage = types.ModuleType("homeassistant.helpers.storage")
    storage.Store = type("Store", (), {})
    upd = types.ModuleType("homeassistant.helpers.update_coordinator")
    upd.DataUpdateCoordinator = _CoordinatorBase

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.storage"] = storage
    sys.modules["homeassistant.helpers.update_coordinator"] = upd
    ha.core = core
    ha.helpers = helpers


def _load_coordinator():
    """Load the real coordinator.py against the stubs above."""
    _install_ha_stubs()

    pkg = types.ModuleType("custom_components")
    pkg.__path__ = []
    xpkg = types.ModuleType("custom_components.xpeng")
    xpkg.__path__ = [str(PKG_DIR)]
    sys.modules["custom_components"] = pkg
    sys.modules["custom_components.xpeng"] = xpkg

    # The real const.py needs HA's entity-description dataclasses; the
    # coordinator only wants DOMAIN out of it.
    const = types.ModuleType("custom_components.xpeng.const")
    const.DOMAIN = "xpeng"
    sys.modules["custom_components.xpeng.const"] = const

    for name in (
        "command_policy",
        "vin_descriptor",
        "drive_evidence",
        "replay_guard",
        "energy_guard",
        "coordinator",
    ):
        spec = importlib.util.spec_from_file_location(
            f"custom_components.xpeng.{name}", PKG_DIR / f"{name}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"custom_components.xpeng.{name}"] = mod
        spec.loader.exec_module(mod)

    return sys.modules["custom_components.xpeng.coordinator"]


coordinator = _load_coordinator()


def _coordinator_with(held, *, parked_energy=None):
    """An instance with just the attributes the guards touch."""
    coord = object.__new__(coordinator.XPengDataCoordinator)
    coord.vin = "TESTVIN0000000000"
    coord._last_full_data = held
    coord.max_odometer = None
    coord._rest_data = {}
    coord._baseline_sink = parked_energy
    coord.last_data_timestamp = None
    coord.last_energy_data_timestamp = None
    coord.state_is_restored = False
    coord._charging_hint_until = 0.0
    coord.gps_fix = coordinator.FixTracker()
    coord.last_motion_at = 0.0
    coord._persist_state = lambda: None
    coord._update_parked_consumption = lambda data: None
    return coord


class TheParkedGuardRunsAtTheAcceptancePoint(unittest.TestCase):
    def test_the_replayed_snapshot_is_refused(self):
        held = _state(94)
        coord = _coordinator_with(held)
        refused = coord.set_vehicle_data(_state(97))
        self.assertFalse(refused)
        self.assertEqual(coord._last_full_data["msg_content"]["instant"]["charge"]["battery_soc"], 94)

    def test_a_rounding_step_is_still_accepted(self):
        coord = _coordinator_with(_state(94))
        self.assertTrue(coord.set_vehicle_data(_state(95)))

    def test_a_falling_reading_is_still_accepted(self):
        coord = _coordinator_with(_state(94))
        self.assertTrue(coord.set_vehicle_data(_state(92)))

    def test_a_charging_frame_is_still_accepted(self):
        """The one legitimate rise on a parked car."""
        coord = _coordinator_with(_state(54))
        self.assertTrue(coord.set_vehicle_data(_state(58, charging=True)))

    def test_evcc_power_counts_as_charging(self):
        coord = _coordinator_with(_state(54))
        self.assertTrue(coord.set_vehicle_data(_state(58, power=7.2)))

    def test_an_external_charging_hint_counts_as_charging(self):
        """xpeng.charging_hint is how a charger says "current is flowing now"
        while the car still reports itself idle."""
        coord = _coordinator_with(_state(54))
        coord.set_charging_hint(600)
        self.assertTrue(coord.set_vehicle_data(_state(58)))

    def test_a_moving_car_is_not_guarded(self):
        """Rejecting stopped frames is bad enough; do not stop a drive."""
        coord = _coordinator_with(_state(58, parked=False))
        self.assertTrue(coord.set_vehicle_data(_state(62, parked=False)))

    def test_a_parked_car_with_no_soc_before_is_not_guarded(self):
        coord = _coordinator_with(_state(None))
        self.assertTrue(coord.set_vehicle_data(_state(97)))

    def test_the_first_state_after_a_restart_is_not_guarded(self):
        """The restored cache is not a measurement of "now" - the car may have
        charged while Home Assistant was down."""
        coord = _coordinator_with(_state(94))
        coord.state_is_restored = True
        self.assertTrue(coord.set_vehicle_data(_state(97)))

    def test_the_odometer_replay_check_still_runs_first(self):
        held = _state(52, odo=62234)
        coord = _coordinator_with(held)
        coord.max_odometer = 62234
        self.assertFalse(coord.set_vehicle_data(_state(60, odo=62210)))


class TheFallbackGateHasOneDefinition(unittest.TestCase):
    """The 5-minute gate is shared: the merge and the fast poll must agree.

    The fast widget poll (issue #3, 2026-10-27) only spends a request while the
    merge gate is open. If the two ever drift apart the poll either asks for
    data its own merge will refuse, or never asks at all - and the detection
    bound silently goes back to one parked interval.
    """

    def test_a_fresh_energy_frame_closes_the_gate(self):
        coord = _coordinator_with(_state(60))
        coord.last_energy_data_timestamp = datetime.now(timezone.utc)
        self.assertFalse(coord.mqtt_energy_stale())

    def test_silence_past_the_threshold_opens_it(self):
        coord = _coordinator_with(_state(60))
        coord.last_energy_data_timestamp = (
            datetime.now(timezone.utc) - timedelta(minutes=6)
        )
        self.assertTrue(coord.mqtt_energy_stale())

    def test_nothing_accepted_yet_opens_it(self):
        self.assertTrue(_coordinator_with(None).mqtt_energy_stale())

    def test_the_merge_uses_the_shared_decision(self):
        source = (_component.component_dir() / "coordinator.py").read_text(
            encoding="utf-8"
        )
        start = source.index("def _merge_widget_state_if_mqtt_stale(")
        end = source.index("\n    def ", start + 10)
        body = source[start:end]
        self.assertIn("self.mqtt_energy_stale()", body)
        self.assertNotIn("> 300", body, "the gate must not be re-hardcoded")


class TheMergeTeachesTheMqttBaseline(unittest.TestCase):
    def test_the_adopted_content_is_the_merged_state(self):
        held = _state(50)
        adopted: list = []
        coord = _coordinator_with(held, parked_energy=adopted.append)
        coord.last_energy_data_timestamp = datetime.now(timezone.utc) - timedelta(hours=2)
        coord.last_data_timestamp = datetime.now(timezone.utc)
        coord._merge_widget_state_if_mqtt_stale(
            {"charge": {"battery_soc": 49}, "odometer": {"total_mileage": 62234}}
        )
        self.assertEqual(len(adopted), 1)
        content = adopted[0]
        self.assertEqual(
            content["instant"]["charge"]["battery_soc"],
            49,
            "the client's partial baseline must learn the live fallback value",
        )
        self.assertEqual(content["instant"]["odometer"]["total_mileage"], 62234)

    def test_nothing_is_adopted_when_the_merge_gate_is_closed(self):
        adopted: list = []
        coord = _coordinator_with(_state(50), parked_energy=adopted.append)
        coord.last_energy_data_timestamp = datetime.now(timezone.utc)
        coord.last_data_timestamp = datetime.now(timezone.utc)
        coord._merge_widget_state_if_mqtt_stale(
            {"charge": {"battery_soc": 49}, "odometer": {"total_mileage": 62234}}
        )
        self.assertEqual(adopted, [])


class TheRestFallbackIsMotionEvidence(unittest.TestCase):
    """Issue #3: a wedged fan-out must not hide a moving car from the watchdog.

    The fallback is the only thing still reporting when the state topic is
    silent; its odometer or drive state is what turns "426 minutes stale while
    the last accepted state said parked" into one bounded force-upload.
    """

    def _merge(self, held, widget):
        coord = _coordinator_with(held)
        coord.last_energy_data_timestamp = datetime.now(timezone.utc) - timedelta(hours=2)
        coord.last_data_timestamp = datetime.now(timezone.utc)
        coord._merge_widget_state_if_mqtt_stale(widget)
        return coord

    def test_an_odometer_advance_arms_the_motion_escalation(self):
        coord = self._merge(
            _state(60, odo=62234), {"odometer": {"total_mileage": 62301}}
        )
        self.assertGreater(coord.last_motion_at, 0.0)

    def test_a_drive_mode_that_is_not_park_arms_it(self):
        coord = self._merge(_state(60), {"drive": {"shift_state": 1}})
        self.assertGreater(coord.last_motion_at, 0.0)

    def test_a_parked_snapshot_leaves_it_alone(self):
        coord = self._merge(
            _state(60, odo=62234),
            {"odometer": {"total_mileage": 62234}, "ext": {"vehicleMode": 0}},
        )
        self.assertEqual(coord.last_motion_at, 0.0)

    def test_a_closed_gate_does_not_arm_it(self):
        coord = _coordinator_with(_state(60, odo=62234))
        coord.last_energy_data_timestamp = datetime.now(timezone.utc)
        coord.last_data_timestamp = datetime.now(timezone.utc)
        coord._merge_widget_state_if_mqtt_stale(
            {"odometer": {"total_mileage": 62301}}
        )
        self.assertEqual(coord.last_motion_at, 0.0)


class ASentinelWidgetIsNotAReplayAndIsNotMerged(unittest.TestCase):
    """Issue #21: ``odometer -1.0`` from the REST widget.

    The widget answers with ``-1`` when it has no authorised state for the
    account/vehicle. Two things must hold: it must not be described as a replay
    (the reported install chased that for eight hours), and it must not reach
    the sensors, because a merged ``-1`` would read as -1 km.
    """

    def _merge(self, held, widget):
        coord = _coordinator_with(held)
        coord.last_energy_data_timestamp = datetime.now(timezone.utc) - timedelta(hours=2)
        coord.last_data_timestamp = datetime.now(timezone.utc)
        coord._merge_widget_state_if_mqtt_stale(widget)
        return coord

    def test_the_placeholder_never_reaches_the_odometer_sensor(self):
        coord = self._merge(
            _state(60, odo=6482),
            {"odometer": {"total_mileage": -1.0}, "charge": {"battery_soc": 41}},
        )
        instant = coord._last_full_data["msg_content"]["instant"]
        self.assertEqual(instant["odometer"]["total_mileage"], 6482)
        self.assertEqual(instant["charge"]["battery_soc"], 60)

    def test_it_does_not_become_motion_evidence(self):
        coord = self._merge(
            _state(60, odo=6482), {"odometer": {"total_mileage": -1.0}}
        )
        self.assertEqual(coord.last_motion_at, 0.0)

    def test_a_real_reading_still_merges(self):
        """The sentinel branch must not swallow the ordinary case."""
        coord = self._merge(
            _state(60, odo=6482),
            {"odometer": {"total_mileage": 6483}, "charge": {"battery_soc": 41}},
        )
        instant = coord._last_full_data["msg_content"]["instant"]
        self.assertEqual(instant["odometer"]["total_mileage"], 6483)
        self.assertEqual(instant["charge"]["battery_soc"], 41)


class TheWiringIsPresent(unittest.TestCase):
    """The guards only work if the integration actually calls them."""

    def setUp(self):
        self.init = (_component.component_dir() / "__init__.py").read_text(encoding="utf-8")

    def test_the_baseline_sink_is_wired_to_the_mqtt_client(self):
        self.assertIn("coordinator.set_baseline_sink(mqtt_client.adopt_state)", self.init)

    def test_the_energy_replay_check_is_wired_into_the_mqtt_path(self):
        self.assertIn("coordinator.parked_energy_replay(data)", self.init)

    def test_the_rest_fallback_is_wired_into_the_watchdog_evidence(self):
        coord_src = (_component.component_dir() / "coordinator.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("rest_motion_evidence(widget, self._last_full_data)", coord_src)
        self.assertIn("REST fallback shows the car moving", coord_src)
        self.assertIn("self.last_motion_at = time.time()", coord_src)

    def test_the_mqtt_client_exposes_adopt_state(self):
        client = (_component.component_dir() / "mqtt_client.py").read_text(encoding="utf-8")
        self.assertIn("def adopt_state(self, content", client)
        self.assertIn("self._baseline_lock = threading.Lock()", client)


if __name__ == "__main__":
    unittest.main()
