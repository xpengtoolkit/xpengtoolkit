#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ABRP telemetry: what goes out, how often, and what never goes out.

    python3 tests/test_abrp.py
    python3 -m unittest discover -s tests -v

Why this exists. Sending telemetry to a third party is the one thing this
integration does that leaves Home Assistant's network, so the payload is pinned
field by field: units (ABRP takes kW, km/h, km, kPa), signs (energy going *in*
is negative), and the values that must be dropped rather than sent (-1 SoC from
a sleeping car, a NaN, a 0,0 position). The cadence is pinned because it is the
only brake on the request rate: the car pushes a frame every ~5 s and ABRP is
fed at most every 10 s while driving, 60 s while parked.

The reporter is pinned separately: it must never raise, must log a failure once
per run of failures instead of every minute, and must never write the token to
the log - the user pastes it into a form, not a file, and a support log is where
a secret leaks from.

Loaded by path: abrp.py has no Home Assistant import, so no car and no HA stub
are needed. aiohttp is supplied by the caller, which is why these tests can use
a five-line fake session on a stock Python.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _component  # noqa: E402

MODULE_PATH = _component.module_path("abrp.py")

_spec = importlib.util.spec_from_file_location("abrp_under_test", MODULE_PATH)
assert _spec and _spec.loader
abrp = importlib.util.module_from_spec(_spec)
sys.modules["abrp_under_test"] = abrp
_spec.loader.exec_module(abrp)

TOKEN = "11111111-2222-3333-4444-555555555555"


def state(*, charging=False, power=0.0, speed=48.2):
    """A coordinator state shaped like the MQTT client's (``msg_content`` root)."""
    instant = {
        "charge": {"battery_soc": 62, "charging_state": 2 if charging else 1},
        "odometer": {
            "total_mileage": 12345.6,
            "avalible_driving_distance": 320.4,
        },
        "hvac": {"hvac_inner_temp_float": 21.5, "hvac_temp_float": 22.0},
        "tpms": {
            "tpms_pressure_fl": 240,
            "tpms_pressure_fr": 241,
            "tpms_pressure_rl": 242,
            "tpms_pressure_rr": 243,
        },
        "drive": {"shift_state": 3},
        "ext": {"vehicleMode": 0},
    }
    interval = {
        "drive": {
            "speed": speed,
            "latitude": -33.9,
            "longitude": 151.2,
            "angle": 145.0,
        },
        "charge": {"power": power, "voltage": 400 if charging else 0,
                   "electricity": 28 if charging else 0},
    }
    return {"vin": "L1NNSGHAXSB123456", "msg_content": {"instant": instant, "interval": interval}}


class TelemetryTests(unittest.TestCase):
    """One coordinator state in, one ``tlm`` object out."""

    def test_a_driving_car_sends_the_high_priority_fields(self):
        tlm = abrp.telemetry(state(), sampled_at=1700000000.9, capacity_kwh=87.5, parked=False)
        self.assertEqual(tlm["utc"], 1700000000)
        self.assertEqual(tlm["soc"], 62.0)
        self.assertEqual(tlm["speed"], 48.2)
        self.assertEqual(tlm["lat"], -33.9)
        self.assertEqual(tlm["lon"], 151.2)
        self.assertEqual(tlm["heading"], 145.0)
        self.assertEqual(tlm["odometer"], 12345.6)
        self.assertEqual(tlm["est_battery_range"], 320.4)
        self.assertEqual(tlm["is_charging"], 0)
        self.assertEqual(tlm["is_parked"], 0)
        self.assertEqual(tlm["capacity"], 87.5)
        self.assertEqual(tlm["soe"], 54.2)  # 62 % of 87.5 kWh
        self.assertEqual(tlm["cabin_temp"], 21.5)
        self.assertEqual(tlm["hvac_setpoint"], 22.0)
        self.assertEqual(tlm["tire_pressure_fl"], 240.0)

    def test_soe_is_only_sent_with_both_terms(self):
        self.assertNotIn("soe", abrp.telemetry(state(), sampled_at=1))
        no_soc = state()
        no_soc["msg_content"]["instant"]["charge"]["battery_soc"] = -1
        tlm = abrp.telemetry(no_soc, sampled_at=1, capacity_kwh=87.5)
        self.assertIn("capacity", tlm)
        self.assertNotIn("soe", tlm)

    def test_no_power_field_while_driving(self):
        """XPENG does not measure motor power; a fabricated 0 would teach ABRP's
        consumption calibration that the car uses no energy."""
        tlm = abrp.telemetry(state(), sampled_at=1, parked=False)
        self.assertNotIn("power", tlm)
        self.assertNotIn("is_dcfc", tlm)

    def test_charging_power_and_current_are_negative(self):
        tlm = abrp.telemetry(
            state(charging=True, power=11.2), sampled_at=1, parked=True
        )
        self.assertEqual(tlm["is_charging"], 1)
        self.assertEqual(tlm["is_parked"], 1)
        self.assertEqual(tlm["power"], -11.2)
        self.assertEqual(tlm["current"], -28.0)
        self.assertEqual(tlm["voltage"], 400.0)
        self.assertEqual(tlm["is_dcfc"], 0)

    def test_dc_charging_is_flagged(self):
        tlm = abrp.telemetry(state(charging=True, power=120.0), sampled_at=1)
        self.assertEqual(tlm["is_dcfc"], 1)
        self.assertEqual(tlm["power"], -120.0)

    def test_power_alone_is_enough_to_say_charging(self):
        """A frame without a charge code but with charger power is still charging."""
        raw = state(power=7.5)
        del raw["msg_content"]["instant"]["charge"]
        tlm = abrp.telemetry(raw, sampled_at=1)
        self.assertEqual(tlm["is_charging"], 1)
        self.assertEqual(tlm["power"], -7.5)

    def test_placeholders_are_dropped_not_sent(self):
        """A sleeping car's -1 % SoC and negative range are "not reported"."""
        raw = state()
        raw["msg_content"]["instant"]["charge"]["battery_soc"] = -1
        raw["msg_content"]["instant"]["odometer"]["avalible_driving_distance"] = -1
        raw["msg_content"]["instant"]["odometer"]["total_mileage"] = -1
        tlm = abrp.telemetry(raw, sampled_at=1)
        self.assertNotIn("soc", tlm)
        self.assertNotIn("est_battery_range", tlm)
        self.assertNotIn("odometer", tlm)

    def test_a_soc_above_100_is_not_a_reading(self):
        raw = state()
        raw["msg_content"]["instant"]["charge"]["battery_soc"] = 140
        self.assertNotIn("soc", abrp.telemetry(raw, sampled_at=1))

    def test_nan_inf_and_booleans_are_not_numbers(self):
        raw = state()
        raw["msg_content"]["instant"]["charge"]["battery_soc"] = float("nan")
        raw["msg_content"]["interval"]["drive"]["speed"] = float("inf")
        raw["msg_content"]["instant"]["odometer"]["total_mileage"] = True
        tlm = abrp.telemetry(raw, sampled_at=1)
        self.assertNotIn("soc", tlm)
        self.assertNotIn("speed", tlm)
        self.assertNotIn("odometer", tlm)

    def test_a_zero_position_is_not_a_fix(self):
        """0,0 is the "no GPS yet" value, not a point in the Gulf of Guinea."""
        raw = state()
        raw["msg_content"]["interval"]["drive"].update(latitude=0, longitude=0)
        tlm = abrp.telemetry(raw, sampled_at=1)
        self.assertNotIn("lat", tlm)
        self.assertNotIn("lon", tlm)

    def test_an_impossible_position_is_dropped(self):
        raw = state()
        raw["msg_content"]["interval"]["drive"].update(latitude=93.0, longitude=151.2)
        self.assertNotIn("lat", abrp.telemetry(raw, sampled_at=1))

    def test_parked_verdict_comes_from_the_caller(self):
        """ABRP and the dashboard must not disagree about the parked state."""
        raw = state()
        self.assertEqual(abrp.telemetry(raw, sampled_at=1, parked=None).get("is_parked"), None)
        self.assertEqual(abrp.telemetry(raw, sampled_at=1, parked=True)["is_parked"], 1)
        self.assertEqual(abrp.telemetry(raw, sampled_at=1, parked=False)["is_parked"], 0)

    def test_no_state_yields_no_payload(self):
        self.assertEqual(abrp.telemetry({}, sampled_at=1), {})
        self.assertEqual(abrp.telemetry({"msg_content": None}, sampled_at=1), {})
        self.assertEqual(abrp.telemetry("nonsense", sampled_at=1), {})

    def test_a_timestamp_alone_is_not_a_data_point(self):
        raw = {"msg_content": {"instant": {}, "interval": {}}}
        self.assertEqual(abrp.telemetry(raw, sampled_at=1), {})

    def test_the_cadence_follows_the_parked_verdict(self):
        self.assertEqual(abrp.interval_for({"is_parked": 1}), abrp.PARKED_INTERVAL_S)
        self.assertEqual(abrp.interval_for({"is_parked": 0}), abrp.MOVING_INTERVAL_S)
        self.assertEqual(abrp.interval_for({}), abrp.MOVING_INTERVAL_S)
        # 10 s is ABRP's own calibration guidance; 60 s is the parked keepalive.
        self.assertEqual(abrp.MOVING_INTERVAL_S, 10.0)
        self.assertEqual(abrp.PARKED_INTERVAL_S, 60.0)

    def test_only_an_abrp_shaped_token_is_accepted(self):
        self.assertTrue(abrp.valid_token(TOKEN))
        self.assertTrue(abrp.valid_token(f"  {TOKEN}\n"))
        for bad in ("", "   ", "not a token", "short", "x" * 200, "token\nwith\nnewlines"):
            self.assertFalse(abrp.valid_token(bad), bad)


class _Response:
    def __init__(self, status=200, body=None, text=""):
        self.status = status
        self._body = {"status": "ok"} if body is None else body
        self._text = text

    async def json(self):
        return self._body

    async def text(self):
        return self._text


class _Post:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc_info):
        return False


class FakeSession:
    """A five-line aiohttp stand-in that records what the reporter sent."""

    def __init__(self, response=None):
        self.response = response or _Response()
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Post(self.response)


class FakeHass:
    def __init__(self):
        self.tasks = []

    def async_create_task(self, coro):
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task

    async def drain(self):
        if self.tasks:
            await asyncio.gather(*self.tasks)
            self.tasks.clear()


class FakeCoordinator:
    def __init__(self, data=None, *, restored=False, parked=False, capacity=87.5):
        self.data = data
        self.state_is_restored = restored
        self.vehicle_parked = parked
        self.usable_battery_kwh = capacity
        self.last_data_timestamp = datetime.fromtimestamp(1700000000, tz=timezone.utc)
        self.listeners = []

    def async_add_listener(self, callback):
        self.listeners.append(callback)

        def remove():
            if callback in self.listeners:
                self.listeners.remove(callback)

        return remove

    def push(self, data):
        self.data = data
        for callback in list(self.listeners):
            callback()


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class ReporterTests(unittest.IsolatedAsyncioTestCase):
    def reporter(self, coordinator, session, clock):
        return abrp.AbrpReporter(
            FakeHass(), coordinator, TOKEN, session=session, clock=clock
        )

    async def test_it_pushes_the_state_it_is_started_with(self):
        session, clock = FakeSession(), Clock()
        coordinator = FakeCoordinator(state(), parked=False)
        hass = FakeHass()
        reporter = abrp.AbrpReporter(
            hass, coordinator, TOKEN, session=session, clock=clock
        )
        reporter.start()
        self.assertTrue(reporter.started)
        self.assertEqual(len(coordinator.listeners), 1)
        await hass.drain()

        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertEqual(url, abrp.ABRP_ENDPOINT)
        self.assertEqual(kwargs["params"], {"token": TOKEN, "api_key": abrp.ABRP_API_KEY})
        self.assertEqual(kwargs["json"]["tlm"]["soc"], 62.0)
        # The sample's own arrival time, not the HTTP call's: a parked car's
        # last frame can be an hour old and ABRP must not read it as "now".
        self.assertEqual(kwargs["json"]["tlm"]["utc"], 1700000000)
        self.assertEqual(reporter.sent, 1)
        self.assertIsNone(reporter.last_error)

    async def test_it_respects_the_cadence(self):
        session, clock = FakeSession(), Clock()
        coordinator = FakeCoordinator(state(), parked=False)
        hass = FakeHass()
        reporter = abrp.AbrpReporter(hass, coordinator, TOKEN, session=session, clock=clock)
        reporter.start()
        await hass.drain()
        clock.advance(5)
        coordinator.push(state())
        await hass.drain()
        self.assertEqual(len(session.calls), 1, "a second push inside 10 s")
        clock.advance(6)
        coordinator.push(state())
        await hass.drain()
        self.assertEqual(len(session.calls), 2, "no push after the window")

    async def test_a_parked_car_is_pushed_once_a_minute(self):
        session, clock = FakeSession(), Clock()
        coordinator = FakeCoordinator(state(), parked=True)
        hass = FakeHass()
        reporter = abrp.AbrpReporter(hass, coordinator, TOKEN, session=session, clock=clock)
        reporter.start()
        await hass.drain()
        clock.advance(30)
        coordinator.push(state())
        await hass.drain()
        self.assertEqual(len(session.calls), 1)
        clock.advance(31)
        coordinator.push(state())
        await hass.drain()
        self.assertEqual(len(session.calls), 2)

    async def test_state_restored_from_disk_is_not_sent(self):
        """Nothing live this session: do not tell ABRP the car is there."""
        session, clock = FakeSession(), Clock()
        coordinator = FakeCoordinator(state(), restored=True)
        hass = FakeHass()
        reporter = abrp.AbrpReporter(hass, coordinator, TOKEN, session=session, clock=clock)
        reporter.start()
        await hass.drain()
        self.assertEqual(session.calls, [])

    async def test_stop_unsubscribes(self):
        session, clock = FakeSession(), Clock()
        coordinator = FakeCoordinator(state())
        hass = FakeHass()
        reporter = abrp.AbrpReporter(hass, coordinator, TOKEN, session=session, clock=clock)
        reporter.start()
        await hass.drain()
        reporter.stop()
        self.assertFalse(reporter.started)
        self.assertEqual(coordinator.listeners, [])
        coordinator.push(state())
        await hass.drain()
        self.assertEqual(len(session.calls), 1)

    async def test_a_rejected_update_is_logged_once_and_never_raised(self):
        session = FakeSession(_Response(body={"status": "error", "errortext": "bad"}))
        clock = Clock()
        coordinator = FakeCoordinator(state())
        hass = FakeHass()
        reporter = abrp.AbrpReporter(hass, coordinator, TOKEN, session=session, clock=clock)
        with self.assertLogs(abrp._LOGGER, level="WARNING") as logs:
            reporter.start()
            await hass.drain()
            clock.advance(11)
            coordinator.push(state())
            await hass.drain()
        warnings = [line for line in logs.output if line.startswith("WARNING")]
        self.assertEqual(len(warnings), 1, "a repeating failure must not flood the log")
        self.assertEqual(reporter.sent, 0)
        self.assertIn("ABRP rejected", reporter.last_error)

    async def test_an_http_error_body_is_reported(self):
        session = FakeSession(_Response(status=500, text="upstream is having a day"))
        clock = Clock()
        reporter = self.reporter(FakeCoordinator(state()), session, clock)
        hass = reporter._hass
        with self.assertLogs(abrp._LOGGER, level="WARNING") as logs:
            reporter.start()
            await hass.drain()
        self.assertIn("HTTP 500", " ".join(logs.output))

    async def test_the_token_never_reaches_the_log(self):
        """The one string a support log must not contain."""
        session = FakeSession(
            _Response(body={"status": "error", "errortext": f"unknown token {TOKEN}"})
        )
        clock = Clock()
        reporter = self.reporter(FakeCoordinator(state()), session, clock)
        hass = reporter._hass
        with self.assertLogs(abrp._LOGGER, level="WARNING") as logs:
            reporter.start()
            await hass.drain()
        output = "\n".join(logs.output)
        self.assertNotIn(TOKEN, output)
        self.assertNotIn(abrp.ABRP_API_KEY, output)
        self.assertIn("***", output)

    async def test_exceptions_are_caught_and_redacted(self):
        class ExplodingSession:
            def post(self, url, **kwargs):
                raise RuntimeError(f"cannot reach {url}?token={TOKEN}")

        clock = Clock()
        reporter = self.reporter(FakeCoordinator(state()), ExplodingSession(), clock)
        hass = reporter._hass
        with self.assertLogs(abrp._LOGGER, level="WARNING") as logs:
            reporter.start()
            await hass.drain()
        output = "\n".join(logs.output)
        self.assertNotIn(TOKEN, output)
        self.assertIn("RuntimeError", output)

    async def test_a_broken_listener_never_breaks_the_coordinator(self):
        """notify() runs inside the coordinator's update path: it must not raise."""
        class ExplodingClock:
            def __call__(self):
                raise RuntimeError("boom")

        session = FakeSession()
        coordinator = FakeCoordinator(state())
        reporter = abrp.AbrpReporter(
            FakeHass(), coordinator, TOKEN, session=session, clock=ExplodingClock()
        )
        with self.assertLogs(abrp._LOGGER, level="DEBUG"):
            reporter.start()  # subscribes, calls notify() -> the clock raises
        self.assertEqual(session.calls, [])
        coordinator.push(state())  # the coordinator keeps working
        self.assertEqual(len(coordinator.listeners), 1)

    async def test_a_failure_does_not_stop_later_successes(self):
        session = FakeSession(_Response(body={"status": "error"}))
        clock = Clock()
        reporter = self.reporter(FakeCoordinator(state()), session, clock)
        hass = reporter._hass
        with self.assertLogs(abrp._LOGGER, level="WARNING"):
            reporter.start()
            await hass.drain()
        session.response = _Response()
        clock.advance(11)
        reporter.notify()
        await hass.drain()
        self.assertEqual(reporter.sent, 1)
        self.assertIsNone(reporter.last_error)

    async def test_missing(self):
        """ABRP's ``missing`` answer is informational, not a failure."""
        session = FakeSession(_Response(body={"status": "ok", "missing": "power"}))
        clock = Clock()
        reporter = self.reporter(FakeCoordinator(state()), session, clock)
        reporter.start()
        await reporter._hass.drain()
        self.assertEqual(reporter.sent, 1)
        self.assertIsNone(reporter.last_error)


class FactoryTests(unittest.TestCase):
    def test_no_token_means_no_reporter(self):
        for token in ("", "   ", None):
            self.assertIsNone(
                abrp.start_abrp_reporter(FakeHass(), FakeCoordinator(), token)
            )

    def test_a_token_that_cannot_be_one_is_refused_out_loud(self):
        with self.assertLogs(abrp._LOGGER, level="WARNING") as logs:
            self.assertIsNone(
                abrp.start_abrp_reporter(FakeHass(), FakeCoordinator(), "not a token")
            )
        self.assertIn("does not look like", "\n".join(logs.output))

    def test_the_reporter_is_only_created_for_a_plausible_token(self):
        """The factory imports Home Assistant's session, so this cannot run here;
        the source is pinned instead (the options flow validates the token, and
        this is the second line of defence)."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        start = source.index("def start_abrp_reporter(")
        body = source[start:start + 900]
        self.assertIn("if not valid_token(cleaned):", body)
        self.assertIn("async_get_clientsession(hass)", body)
        self.assertIn("return None", body)


class WiringTests(unittest.TestCase):
    """The integration has to start and stop the reporter, and nothing else."""

    INIT = (_component.component_dir() / "__init__.py").read_text(encoding="utf-8")
    CONST = (_component.component_dir() / "const.py").read_text(encoding="utf-8")

    def test_the_option_key_is_declared(self):
        self.assertIn('CONF_ABRP_TOKEN: Final = "abrp_token"', self.CONST)

    def test_setup_starts_it_only_when_configured(self):
        self.assertIn(
            'abrp_token = str(entry.options.get(CONF_ABRP_TOKEN, "") or "").strip()',
            self.INIT,
        )
        start = self.INIT.index("abrp_token = str(entry.options.get(")
        body = self.INIT[start:start + 500]
        self.assertIn("if abrp_token:", body)
        self.assertIn("start_abrp_reporter(", body)

    def test_unload_and_replacement_stop_it(self):
        unload = self.INIT.index("async def async_unload_entry(")
        self.assertIn('data.get("abrp_reporter")', self.INIT[unload:])
        replace = self.INIT.index("previous_provider = previous.get(")
        self.assertIn(
            'previous.get("abrp_reporter")', self.INIT[replace:replace + 2500]
        )


if __name__ == "__main__":
    unittest.main()
