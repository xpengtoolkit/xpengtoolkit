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

# The cable evidence the #23 escape hatch reads comes from charge_port, which is
# pure for the same reason energy_guard is, so it loads by path too. Loading it
# under its package name keeps coordinator.py's `from .charge_port import ...`
# working when that module is imported below with Home Assistant stubbed.
_CP_SPEC = importlib.util.spec_from_file_location(
    "custom_components.xpeng.charge_port", _component.module_path("charge_port.py")
)
assert _CP_SPEC and _CP_SPEC.loader
charge_port = importlib.util.module_from_spec(_CP_SPEC)
sys.modules["custom_components.xpeng.charge_port"] = charge_port
_CP_SPEC.loader.exec_module(charge_port)


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


# -- issue #23: a scheduled charge invisible to both frames -----------------


def _scheduled_held(soc=24):
    """The frame held before an overnight scheduled charge, verbatim from #23.

    ``charging_state`` 15 with ``repeat_type`` 2 is what this car reports while it
    waits for its plan - the same payload ``plugged_in`` deliberately refuses to
    read a cable from, because asleep and armed it reports gun 0 / connector 7 /
    ports 2/2 whether or not a cable is in (measured 2026-10-17).
    """
    return {
        "msg_content": {
            "instant": {
                "odometer": {"total_mileage": 41234},
                "drive": {"shift_state": 4, "speed": 0},
                "charge": {
                    "battery_soc": soc,
                    "charging_state": 15,
                    "repeat_type": 2,
                    "reserve_charge_start_time": "00:00",
                    "reserve_charge_end_time": "05:00",
                    "charge_gun_status": 0,
                    "charge_connector_status": 7,
                    "slow_charge_port_status": 2,
                    "fast_charge_port_status": 2,
                },
            }
        },
        "vin": "TESTVIN0000000000",
    }


def _session_finished(soc=70, *, state=1, gun=4):
    """The force-upload's answer after the session: charged, cable still in.

    State 1 ("Connected") with gun 4 is what the car says once the plan is done
    and the wake has it talking again - the same transition #23 describes, and
    the one measured on 2026-10-17 when switching the plan off moved gun 0 -> 4.
    """
    return {
        "msg_content": {
            "instant": {
                "odometer": {"total_mileage": 41234},
                "drive": {"shift_state": 4, "speed": 0},
                "charge": {
                    "battery_soc": soc,
                    "charging_state": state,
                    "repeat_type": 2,
                    "charge_gun_status": gun,
                    "charge_connector_status": 7,
                    "slow_charge_port_status": 2,
                },
            },
            "interval": {"charge": {"power": 0}},
        },
        "vin": "TESTVIN0000000000",
    }


class AScheduledChargeIsNotAReplay(unittest.TestCase):
    """Issue #23 (Quatronimo, 2026-11-03), on an X9 with a 00:00-05:00 plan.

    The car charged 24 % -> 70 % overnight (23.5 kWh over 300 min, all of it
    correct in the payload) and the integration sat at 24 % for 90 minutes. The
    watchdog's own force-upload produced the right answer at 05:35 and the guard
    refused it three times, because no frame in the pair ever said "charging":
    the held one predates the session (Scheduled) and the candidate postdates it
    (Connected, power 0). A refusal also keeps the frame out of the watchdog's
    data clock, and the 12 h escalation backoff means nothing self-heals - hence
    "SOC stuck until reload", which is the part that makes this worth fixing
    rather than shrugging off as a rare flap.
    """

    def _replay(self, candidate, held, **kwargs):
        return energy_guard.parked_soc_replay(
            candidate, held, parked=True, charging=False, **kwargs
        )

    def test_the_reported_pair_is_refused_without_the_cable_test(self):
        """The bug, pinned first: this is what 0.6.39 did."""
        self.assertTrue(
            self._replay(_session_finished(), _scheduled_held())
        )

    def test_a_cable_in_the_candidate_frame_accepts_the_rise(self):
        cand = _session_finished()
        self.assertTrue(charge_port.charge_cable_attached(
            energy_guard.charge_section(cand)
        ))
        self.assertFalse(
            self._replay(cand, _scheduled_held(), plugged_in=True)
        )

    def test_the_coordinator_accepts_the_reported_pair(self):
        """End to end through the real acceptance point, not just the helper."""
        coord = _coordinator_with(_scheduled_held())
        self.assertTrue(coord.set_vehicle_data(_session_finished()))
        self.assertEqual(
            coord._last_full_data["msg_content"]["instant"]["charge"]["battery_soc"],
            70,
        )

    def test_the_held_cable_is_never_the_evidence(self):
        """Reading the plug from the *held* frame would still answer "unknown".

        With a plan armed and the car asleep, plugged and unplugged are
        byte-for-byte identical, so ``charge_cable_state`` refuses the third case
        there. This asserts the guard does not depend on that field agreeing.
        """
        held_charge = energy_guard.charge_section(_scheduled_held())
        self.assertIsNone(charge_port.charge_cable_state(held_charge))
        self.assertFalse(charge_port.charge_cable_attached(held_charge))

    def test_an_unplugged_post_session_frame_is_still_refused(self):
        """Cable gone and the session invisible: nothing excuses the rise."""
        cand = _session_finished(gun=0, state=0)
        self.assertFalse(
            charge_port.charge_cable_attached(energy_guard.charge_section(cand))
        )
        self.assertTrue(self._replay(cand, _scheduled_held(), plugged_in=False))

    def test_idle_state_after_a_session_is_a_known_gap(self):
        """charging_state 8 (Idle) reports no cable, so #23 stays refused there.

        Recorded rather than papered over: if a car posts its finished session as
        Idle with the gun empty, the cable test cannot rescue it and the SOC
        freezes again. No capture shows this yet, so it is not guessed at.
        """
        cand = _session_finished(state=8, gun=0)
        self.assertFalse(
            charge_port.charge_cable_attached(energy_guard.charge_section(cand))
        )
        self.assertTrue(self._replay(cand, _scheduled_held(), plugged_in=False))

    def test_no_plugged_in_argument_keeps_the_old_behaviour(self):
        """Callers that do not say get the pre-#23 rule, not a free pass."""
        self.assertTrue(self._replay(_session_finished(), _scheduled_held()))

    def test_a_lower_replay_is_accepted_regardless_of_the_cable(self):
        """The wedge protection compares direction, and the cable cannot flip it.

        A fan-out resending an older snapshot carries a *lower* SOC, so it fails
        the rise test on its own merits; the cable only ever stops a rise being
        called a replay.
        """
        self.assertFalse(
            self._replay(_session_finished(soc=52), _session_finished(soc=58),
                         plugged_in=True)
        )

    def test_the_g9_wedge_case_survives_the_change(self):
        """2026-10-23: a high value frozen for hours by a replayed snapshot.

        That car was unplugged and idle all day, so the new escape hatch must not
        apply to it. This is the measurement energy_guard.py was written for.
        """
        self.assertTrue(
            self._replay(_state(97), _state(94), plugged_in=False)
        )

    def test_a_complete_state_counts_as_plugged(self):
        """State 3 ("Complete") is in CHARGING_STATES_PLUGGED: cable still in."""
        cand = _session_finished(state=3)
        self.assertTrue(
            charge_port.charge_cable_attached(energy_guard.charge_section(cand))
        )
        self.assertFalse(self._replay(cand, _scheduled_held(), plugged_in=True))


class TheWatchdogAnswerIsNotMarkedSpecial(unittest.TestCase):
    """Why #23 could not be fixed by exempting the force-upload response.

    The reporter's suggestion 3 asked for exactly that. It is not implementable
    where the guard runs: mqtt_client routes the answer to a ``cmd_type`` 2
    force-upload through the same state callback as a spontaneous push, and the
    only flag it adds (``has_fresh_energy_data``) is set for any frame carrying a
    charge or odometer block. Nothing distinguishes "the car answered our question
    a second ago" from "the car volunteered", so the cable is the evidence that
    has to do the work.
    """

    def source(self):
        return (PKG_DIR / "mqtt_client.py").read_text()

    def test_the_response_carries_no_request_correlation(self):
        """The state payload has exactly three keys, none of them a correlation.

        Parsed from the AST rather than sliced out of the source, so a reformat
        cannot quietly turn this into a test of nothing.
        """
        import ast

        tree = ast.parse(self.source())
        shapes = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = [
                k.value
                for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            ]
            if "has_fresh_energy_data" in keys:
                shapes.add(tuple(sorted(keys)))
        self.assertEqual(
            shapes,
            {("has_fresh_energy_data", "msg_content", "vin")},
            "the state payload gained or lost a key",
        )


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
