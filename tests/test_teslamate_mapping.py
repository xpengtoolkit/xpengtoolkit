#!/usr/bin/env python3
"""The Tesla-facing shape of XPENG telemetry, pinned against TeslaMate's rules.

    python3 tests/test_teslamate_mapping.py
    python3 -m unittest discover -s tests -v

TeslaMate's car process is unforgiving about this payload. Every claim asserted
here is a rule read out of teslamate 4.4.0's code:

* all five state sections must be present (`vehicle.ex`'s acceptance guard),
  otherwise the fetch is discarded as "incomplete";
* `drive_state.timestamp`/`charge_state.timestamp` must be **millisecond
  integers** (`parse_timestamp/1` does `DateTime.from_unix!(ts, :millisecond)`);
* `latitude`/`longitude` must be numbers or the `positions` insert raises and
  the car's logger crashes;
* distances are **miles** and are converted again at DB write, so km here is a
  silent 1.609x corruption; temperatures are **°C**, tyre pressure is **bar**;
* `shift_state` may be null/P/R/N/D - the streaming decoder turns an empty field
  into nil, but a literal "" reaching the driving branch raises
  `CaseClauseError`;
* a charging session is opened only for `Starting`/`Charging` and closed by
  anything else;
* the stream value is 13 comma-separated fields in a fixed order, and a nil
  `power` means "fake online" to TeslaMate.

The duplicated shift/charging tables are pinned against the integration's own
`const.py` and `charge_port.py` by parsing them as literals, so this test needs
no Home Assistant install.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import json
import pathlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _component  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PKG = _component.component_dir()


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("xpengtest.charge_current", PKG / "charge_current.py")
mapping = _load("xpengtest.teslamate_mapping", PKG / "teslamate_mapping.py")
charge_port = _load("xpengtest.charge_port", PKG / "charge_port.py")


def _literal_assignment(path: pathlib.Path, name: str):
    """A module-level literal assignment out of a file, without importing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


#: A complete merged MQTT snapshot, shaped like mqtt_client.py produces it.
#: Numbers are the G6's units: km, km/h, °C, kPa, kW.
PARKED = {
    "vin": "L1NNSGHA0SB000000",
    "msg_content": {
        "instant": {
            "charge": {
                "battery_soc": 64,
                "max_range_charge": 90,
                "charging_state": 15,
                "charge_gun_status": 0,
                "charge_connector_status": 7,
                "slow_charge_port_status": 2,
                "time_to_complete_charge_min": 0,
                "electric_current_limit": 16,
            },
            "odometer": {
                "total_mileage": 12345.6,
                "avalible_driving_distance": 412.0,
            },
            "drive": {"shift_state": 3},
            "ext": {"vehicleMode": 0},
            "hvac": {
                "hvac_on": 0,
                "hvac_inner_temp_float": 21.5,
                "hvac_temp_float": 22.0,
                "hvac_wind_speed": 0,
            },
            "door": {
                "driver_door_lock_st": 1,
                "driver_door_ajar_st": 0,
                "passenger_door_ajar_st": 0,
                "rear_left_door_ajar_st": 0,
                "rear_right_door_ajar_st": 0,
                "bonnet_ajar_st": 0,
            },
            "tail": {"tail_status": 0},
            "tpms": {
                "tpms_pressure_fl": 250.0,
                "tpms_pressure_fr": 251.0,
                "tpms_pressure_rl": 249.0,
                "tpms_pressure_rr": 248.0,
            },
        },
        "interval": {
            "drive": {"latitude": -33.8688, "longitude": 151.2093,
                      "speed": 0, "angle": 90},
            "charge": {"power": 0, "voltage": 0, "electricity": 0},
        },
    },
}

CHARGING = json.loads(json.dumps(PARKED))
CHARGING["msg_content"]["instant"]["charge"].update(
    {"charging_state": 2, "charge_gun_status": 4}
)
CHARGING["msg_content"]["interval"]["charge"].update(
    {"power": 11.2, "voltage": 232.0, "electricity": 48.0}
)

DRIVING = json.loads(json.dumps(PARKED))
DRIVING["msg_content"]["instant"]["drive"].update({"shift_state": 1})
DRIVING["msg_content"]["instant"]["ext"].update({"vehicleMode": 2})
DRIVING["msg_content"]["interval"]["drive"].update({"speed": 60.0})


def telemetry(raw, **overrides):
    values = dict(
        vin="L1NNSGHA0SB000000",
        name="G6",
        series="G6",
        raw=raw["msg_content"],
        now_ms=1_700_000_000_000,
        data_ms=1_700_000_000_000,
        data_age_seconds=0.0,
    )
    values.update(overrides)
    return mapping.Telemetry(**values)


class TestTheDuplicatedTables(unittest.TestCase):
    """The mapping's copies of const/charge_port tables must not drift."""

    def test_shift_states_match_const(self):
        self.assertEqual(
            dict(mapping.SHIFT_STATES),
            _literal_assignment(PKG / "const.py", "SHIFT_STATES"),
        )

    def test_vehicle_modes_are_used_by_mode_fallback(self):
        modes = _literal_assignment(PKG / "const.py", "VEHICLE_MODES")
        self.assertEqual(modes[2], "Driving")
        self.assertEqual(mapping.VEHICLE_MODE_DRIVING, 2)

    def test_charging_codes_match_charge_port(self):
        states = _literal_assignment(PKG / "charge_port.py", "CHARGING_STATES")
        self.assertEqual(mapping.CHARGING_STATE_CHARGING, 2)
        self.assertEqual(mapping.CHARGING_STATE_UNLABELLED, charge_port.CHARGING_STATE_UNLABELLED)
        self.assertEqual(
            mapping.CHARGING_STATES_PLUGGED,
            charge_port.CHARGING_STATES_PLUGGED,
        )
        self.assertEqual(mapping.CHARGE_PORT_CONNECTED,
                         charge_port.CHARGE_PORT_CONNECTED)
        self.assertEqual(states[2], "Charging")

    def test_cable_attachment_agrees_with_charge_port(self):
        cases = [
            {"charging_state": 2},
            {"charging_state": 1},
            {"charging_state": 3},
            {"charging_state": 15, "charge_gun_status": 0,
             "charge_connector_status": 7, "slow_charge_port_status": 2},
            {"charging_state": 15, "charge_gun_status": 4,
             "charge_connector_status": 7},
            {"charging_state": 15, "slow_charge_port_status": 2},
            {"charging_state": 15, "slow_charge_port_status": 0},
            {},
        ]
        for case in cases:
            self.assertEqual(
                mapping.charge_cable_attached(case),
                charge_port.charge_cable_attached(case),
                case,
            )

    def test_schedule_armed_agrees_with_charge_port(self):
        for case in ({"repeat_type": 0}, {"repeat_type": 2}, {}):
            self.assertEqual(
                mapping.charge_schedule_armed(case),
                charge_port.charge_schedule_armed(case),
                case,
            )


class TestUnits(unittest.TestCase):
    """Tesla's API units, not XPENG's."""

    def test_distance_is_miles(self):
        self.assertAlmostEqual(mapping.km_to_miles(412.0, 2), 256.0, places=1)
        self.assertIsNone(mapping.km_to_miles(None))

    def test_speed_is_mph(self):
        self.assertAlmostEqual(mapping.kmh_to_mph(60.0, 1), 37.3, places=1)
        self.assertIsNone(mapping.kmh_to_mph(None))

    def test_tpms_is_bar(self):
        self.assertEqual(mapping.kpa_to_bar(250.0), 2.5)

    def test_temperature_stays_celsius(self):
        data = mapping.build_vehicle_data(
            telemetry(DRIVING), ids=mapping.stable_ids("L1NNSGHA0SB000000"),
            state="online",
        )
        # TeslaMate stores °C and converts for display; converting here would
        # make the dashboard show 21.5 C as -6 C.
        self.assertEqual(data["climate_state"]["inside_temp"], 21.5)

    def test_round_half_up(self):
        self.assertEqual(mapping.round_half_up(2.5), 3)
        self.assertEqual(mapping.round_half_up(2.4), 2)


class TestStableIds(unittest.TestCase):
    """TeslaMate stores eid/vid in integer columns and uses id in every URL."""

    def test_ids_are_stable_distinct_ints(self):
        first = mapping.stable_ids("L1NNSGHA0SB000000")
        second = mapping.stable_ids("L1NNSGHA0SB000000")
        other = mapping.stable_ids("L1NNSGHA0SB000001")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertNotEqual(first[0], first[1])
        for value in (*first, *other):
            self.assertIsInstance(value, int)
            self.assertLess(value, 2**31)
            self.assertGreater(value, 0)


class TestShiftState(unittest.TestCase):
    """null|P|D|N|R only; never "" (that raises in TeslaMate's driving branch)."""

    def test_measured_codes(self):
        for code, expected in ((0, "N"), (1, "D"), (2, "R"), (3, "P"), (4, "P")):
            self.assertEqual(
                mapping.shift_state({"instant": {"drive": {"shift_state": code}}}),
                expected,
            )

    def test_driving_mode_fills_a_missing_gear(self):
        raw = {"instant": {"ext": {"vehicleMode": 2}}}
        self.assertEqual(mapping.shift_state(raw), "D")

    def test_parked_mode_falls_back_to_p(self):
        raw = {"instant": {"ext": {"vehicleMode": 0}}}
        self.assertEqual(mapping.shift_state(raw), "P")

    def test_unknown_is_null(self):
        self.assertIsNone(mapping.shift_state({}))
        self.assertIsNone(mapping.shift_state({"instant": {}}))


class TestChargingState(unittest.TestCase):
    """Only actually-charging maps to Charging; anything else ends a session."""

    def test_current_limit_is_amperes_not_the_protocol_index(self):
        for raw, amperes in ((1, 6), (11, 16), (20, 25), (27, 32), (0, None)):
            with self.subTest(raw=raw):
                data = json.loads(json.dumps(PARKED))
                data["msg_content"]["instant"]["charge"]["electric_current_limit"] = raw
                state = mapping.build_charge_state(telemetry(data))
                self.assertEqual(state["charge_current_request"], amperes)
                self.assertEqual(state["charge_current_request_max"], amperes)

    def test_charging(self):
        raw = CHARGING["msg_content"]
        self.assertTrue(mapping.is_charging(raw))
        self.assertEqual(mapping.tesla_charging_state(raw), "Charging")

    def test_unplugged_car_measuring_15_is_disconnected(self):
        # Measured on a G6 2026-10-17 with no cable: state 15, gun 0,
        # connector 7, both port fields 2 for nine hours.
        self.assertFalse(mapping.is_charging(PARKED["msg_content"]))
        self.assertEqual(mapping.tesla_charging_state(PARKED["msg_content"]),
                         "Disconnected")

    def test_plugged_waiting_for_a_plan_is_stopped(self):
        raw = json.loads(json.dumps(PARKED["msg_content"]))
        raw["instant"]["charge"].update(
            {"charging_state": 15, "charge_gun_status": 4, "repeat_type": 2}
        )
        self.assertEqual(mapping.tesla_charging_state(raw), "Stopped")
        state = mapping.build_charge_state(telemetry({"msg_content": raw}))
        self.assertTrue(state["charge_port_door_open"])
        self.assertTrue(state["scheduled_charging_pending"])

    def test_complete_ends_the_session(self):
        raw = json.loads(json.dumps(CHARGING["msg_content"]))
        raw["instant"]["charge"]["charging_state"] = 3
        self.assertEqual(mapping.tesla_charging_state(raw), "Complete")

    def test_power_alone_counts_as_charging(self):
        raw = json.loads(json.dumps(PARKED["msg_content"]))
        raw["instant"]["charge"].pop("charging_state")
        raw["interval"]["charge"]["power"] = 3.0
        self.assertTrue(mapping.is_charging(raw))

    def test_force_charging_keeps_a_session_across_a_code_blip(self):
        # The car alternates 2 <-> 15 mid-session while power keeps flowing.
        raw = json.loads(json.dumps(PARKED["msg_content"]))
        raw["interval"]["charge"]["power"] = 0
        snapshot = telemetry({"msg_content": raw}, force_charging=True)
        state = mapping.build_charge_state(snapshot)
        self.assertEqual(state["charging_state"], "Charging")


class TestVehicleState(unittest.TestCase):
    """online/asleep/offline only - TeslaMate has no fourth state."""

    def test_driving_is_online_even_with_old_data(self):
        self.assertEqual(
            mapping.tesla_vehicle_state(
                data_age_seconds=10_000, mqtt_connected=True,
                charging=False, driving=True,
            ),
            "online",
        )

    def test_charging_is_online(self):
        self.assertEqual(
            mapping.tesla_vehicle_state(
                data_age_seconds=10_000, mqtt_connected=True,
                charging=True, driving=False,
            ),
            "online",
        )

    def test_quiet_socket_is_offline(self):
        self.assertEqual(
            mapping.tesla_vehicle_state(
                data_age_seconds=5, mqtt_connected=False,
                charging=False, driving=False,
            ),
            "offline",
        )

    def test_long_silence_is_asleep(self):
        self.assertEqual(
            mapping.tesla_vehicle_state(
                data_age_seconds=mapping.ASLEEP_SECONDS + 1, mqtt_connected=True,
                charging=False, driving=False,
            ),
            "asleep",
        )

    def test_recent_data_is_online(self):
        self.assertEqual(
            mapping.tesla_vehicle_state(
                data_age_seconds=60, mqtt_connected=True,
                charging=False, driving=False,
            ),
            "online",
        )


class TestVehicleDataShape(unittest.TestCase):
    """The payload TeslaMate accepts, and what it will not accept."""

    def _data(self, raw, **kwargs):
        return mapping.build_vehicle_data(
            telemetry(raw, **kwargs),
            ids=mapping.stable_ids("L1NNSGHA0SB000000"),
            state="online",
        )

    def test_all_five_sections_are_present(self):
        data = self._data(PARKED)
        for section in (
            "drive_state",
            "charge_state",
            "climate_state",
            "vehicle_state",
            "vehicle_config",
        ):
            self.assertIsInstance(data[section], dict, section)
            self.assertTrue(data[section], section)

    def test_timestamps_are_millisecond_integers(self):
        data = self._data(PARKED)
        for section in (
            "drive_state",
            "charge_state",
            "climate_state",
            "vehicle_state",
            "vehicle_config",
        ):
            timestamp = data[section]["timestamp"]
            self.assertIsInstance(timestamp, int, section)
            # > 2001 in milliseconds, i.e. not seconds that lost their zeros.
            self.assertGreater(timestamp, 1_000_000_000_000, section)

    def test_identity_ids_are_integers(self):
        data = self._data(PARKED)
        self.assertIsInstance(data["id"], int)
        self.assertIsInstance(data["vehicle_id"], int)
        self.assertEqual(data["vin"], "L1NNSGHA0SB000000")
        self.assertEqual(data["state"], "online")

    def test_json_serialisable(self):
        # TeslaMate's JSON middleware has to be able to decode every field.
        json.dumps(self._data(CHARGING))

    def test_distance_and_speed_units(self):
        data = self._data(DRIVING)
        self.assertAlmostEqual(data["vehicle_state"]["odometer"], 7671.2, places=1)
        self.assertAlmostEqual(data["drive_state"]["speed"], 37.3, places=1)
        self.assertNotIn(data["drive_state"]["speed"], (60.0, 60))

    def test_tyre_pressure_is_bar(self):
        state = self._data(PARKED)["vehicle_state"]
        self.assertEqual(state["tpms_pressure_fl"], 2.5)
        self.assertEqual(state["tpms_pressure_rr"], 2.5)

    def test_shift_state_is_an_allowed_value(self):
        self.assertEqual(self._data(PARKED)["drive_state"]["shift_state"], "P")
        self.assertEqual(self._data(DRIVING)["drive_state"]["shift_state"], "D")

    def test_position_is_always_numeric(self):
        data = self._data(PARKED)
        self.assertIsInstance(data["drive_state"]["latitude"], float)
        self.assertIsInstance(data["drive_state"]["longitude"], float)

    def test_last_known_position_is_carried_forward(self):
        # A partial frame (a sleeping car's stub) must not produce (0, 0) or nil.
        raw = json.loads(json.dumps(PARKED))
        raw["msg_content"]["interval"]["drive"] = {"speed": 0}
        snapshot = telemetry(
            raw,
            fallback_latitude=-33.9,
            fallback_longitude=151.2,
        )
        data = mapping.build_drive_state(snapshot)
        self.assertEqual(data["latitude"], -33.9)
        self.assertEqual(data["longitude"], 151.2)

    def test_charger_phases_is_null(self):
        # positions/charges have a DB check constraint: phases > 0 or null.
        self.assertIsNone(self._data(CHARGING)["charge_state"]["charger_phases"])

    def test_charging_power_is_negative_in_drive_state(self):
        data = self._data(CHARGING)
        self.assertLess(data["drive_state"]["power"], 0)
        self.assertAlmostEqual(data["charge_state"]["charger_power"], 11.2)

    def test_parked_power_is_zero_not_positive(self):
        # can_fall_asleep() treats positive drive power as "in use", keeping the
        # car online forever; a parked car must not report motor use.
        data = self._data(PARKED)
        self.assertEqual(data["drive_state"]["power"], 0)

    def test_vehicle_config_is_not_nil(self):
        # identify/1 is matched with {:ok, attrs} = identify(vehicle); a nil
        # config on an online transition crashes the car's logger.
        config = self._data(PARKED)["vehicle_config"]
        self.assertEqual(config["car_type"], "xpeng")
        self.assertEqual(config["trim_badging"], "G6")

    def test_locked_and_doors_are_real_values(self):
        state = self._data(PARKED)["vehicle_state"]
        self.assertIs(state["locked"], True)
        self.assertIs(state["sentry_mode"], False)
        for key in ("df", "pf", "dr", "pr", "ft"):
            self.assertIsInstance(state[key], int, key)

    def test_soc_and_range(self):
        data = self._data(PARKED)
        self.assertEqual(data["charge_state"]["battery_level"], 64)
        self.assertEqual(data["charge_state"]["usable_battery_level"], 64)
        self.assertAlmostEqual(data["charge_state"]["battery_range"], 256.0,
                               places=1)

    def test_energy_added_is_passed_through(self):
        data = mapping.build_vehicle_data(
            telemetry(CHARGING),
            ids=mapping.stable_ids("L1NNSGHA0SB000000"),
            state="online",
            energy_added_kwh=12.5,
            range_added_miles=40.0,
        )
        self.assertEqual(data["charge_state"]["charge_energy_added"], 12.5)
        self.assertEqual(data["charge_state"]["charge_miles_added_rated"], 40.0)


class TestStreamValue(unittest.TestCase):
    """The 13-field comma string, decoded the way TeslaMate decodes it."""

    COLUMNS = ("time", *mapping.STREAM_COLUMNS)

    def _decode(self, value: str) -> dict:
        """Mirror TeslaApi.Stream.decode_frame!/Data.into! field by field."""
        parts = value.split(",")
        assert len(parts) == 13, parts
        raw = dict(zip(self.COLUMNS, parts))
        return {
            key: (None if raw[key] == "" else raw[key])
            for key in self.COLUMNS
        }

    def test_thirteen_fields_in_order(self):
        fields = mapping.stream_value(telemetry(DRIVING)).split(",")
        self.assertEqual(len(fields), 13)
        self.assertEqual(mapping.STREAM_COLUMNS[0], "speed")
        self.assertEqual(mapping.STREAM_COLUMNS[-1], "heading")

    def test_column_list_matches_teslamate_subscription(self):
        self.assertEqual(
            mapping.subscribe_value(),
            "speed,odometer,soc,elevation,est_heading,est_lat,est_lng,power,"
            "shift_state,range,est_range,heading",
        )

    def test_values_decode(self):
        decoded = self._decode(mapping.stream_value(telemetry(DRIVING)))
        self.assertEqual(int(decoded["time"]), 1_700_000_000_000)
        self.assertAlmostEqual(float(decoded["est_lat"]), -33.8688, places=4)
        self.assertAlmostEqual(float(decoded["est_lng"]), 151.2093, places=4)
        self.assertEqual(decoded["shift_state"], "D")
        self.assertEqual(int(decoded["soc"]), 64)
        # power is always numeric: nil is TeslaMate's "fake online" signal.
        self.assertIsNotNone(decoded["power"])
        self.assertIsInstance(int(decoded["power"]), int)

    def test_parked_stream_reports_power_zero(self):
        decoded = self._decode(mapping.stream_value(telemetry(PARKED)))
        self.assertEqual(decoded["shift_state"], "P")
        self.assertEqual(int(decoded["power"]), 0)

    def test_charging_stream_power_is_negative(self):
        decoded = self._decode(mapping.stream_value(telemetry(CHARGING)))
        self.assertLess(int(decoded["power"]), 0)

    def test_empty_shift_state_would_be_nil_not_empty(self):
        # TeslaMate turns "" into nil; the mapping must never emit a literal
        # empty *vehicle_data* shift_state (that raises), but "" in a stream
        # frame decodes to nil, which is the allowed "unknown".
        raw = {"instant": {}, "interval": {}}
        decoded = self._decode(mapping.stream_value(telemetry({"msg_content": raw})))
        self.assertIsNone(decoded["shift_state"])


class TestPartialPayloads(unittest.TestCase):
    """MQTT frames are partial; no shape may raise inside the builders.

    ``instant`` and ``interval`` arrive on different topics, a sleeping car's
    stub carries almost nothing, and a command ack merges only a few keys. A
    builder that raises on such a frame would take down the aiohttp handler and
    with it the whole fetch; TeslaMate would log an error and retry forever.
    """

    def _mutate(self, node, rng):
        if not isinstance(node, dict):
            return node
        out = {}
        for key, value in node.items():
            roll = rng.random()
            if roll < 0.25:
                continue
            if roll < 0.35:
                out[key] = None
            elif roll < 0.45:
                out[key] = rng.choice(["", "x", True, False, [], {}])
            else:
                out[key] = self._mutate(value, rng)
        return out

    def test_random_partial_payloads_do_not_raise(self):
        import random

        rng = random.Random(7)
        ids = mapping.stable_ids("L1NNSGHA0SB000000")
        for _ in range(500):
            raw = self._mutate(PARKED["msg_content"], rng)
            snapshot = mapping.Telemetry(
                vin="L1NNSGHA0SB000000",
                raw=raw,
                now_ms=1_700_000_000_000,
                data_ms=1_700_000_000_000,
                data_age_seconds=0.0,
            )
            data = mapping.build_vehicle_data(snapshot, state="online", ids=ids)
            json.dumps(data)
            mapping.stream_value(snapshot)

    def test_no_data_at_all(self):
        snapshot = mapping.Telemetry(vin="V", raw={}, now_ms=1, data_ms=1)
        data = mapping.build_vehicle_data(
            snapshot, state="offline", ids=mapping.stable_ids("V")
        )
        json.dumps(data)
        self.assertEqual(
            len(mapping.stream_value(snapshot).split(",")), 13
        )


class TestCoordinatorAdapter(unittest.TestCase):
    """snapshot_from_coordinator reads real coordinator attributes only."""

    #: Attributes the adapter reads off the coordinator.
    NEEDED = (
        "vin",
        "data",
        "last_data_timestamp",
        "poll_status",
        "weather",
        "vehicle_appearance",
        "usable_battery_kwh",
        "xos_version",
        "nick_name",
        "series_name",
        "vehicle_type_name",
    )

    def test_coordinator_still_defines_every_attribute_read(self):
        # Cheap but decisive: the adapter is duck-typed, so a coordinator rename
        # would otherwise degrade silently to defaults (wrong name, no weather,
        # no silence) with nothing failing.
        source = (PKG / "coordinator.py").read_text(encoding="utf-8")
        assigned = set(
            re.findall(
                r"self\.([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=\n]*)?=",
                source,
            )
        )
        for attribute in self.NEEDED:
            self.assertIn(attribute, assigned, f"coordinator no longer sets {attribute}")

    def test_snapshot_reads_the_live_values(self):
        from datetime import datetime, timedelta, timezone
        import types

        now = datetime.now(timezone.utc)
        payload = {"msg_content": PARKED["msg_content"]}
        coordinator = types.SimpleNamespace(
            vin="L1NNSGHA0SB000000",
            data=payload,
            last_data_timestamp=now,
            # The integration's watchdog clock wins over the timestamp age.
            poll_status={"meaningful_silence_seconds": 42.0, "mqtt_connected": True},
            weather={"realtimeTemperature": 17.5},
            vehicle_appearance={"colorName": "Silver"},
            usable_battery_kwh=80.0,
            xos_version="XOS 5.6.0",
            nick_name="G6",
            series_name="G6",
            vehicle_type_name=None,
        )
        snapshot = mapping.snapshot_from_coordinator(coordinator)
        self.assertEqual(snapshot.vin, "L1NNSGHA0SB000000")
        self.assertEqual(snapshot.name, "G6")
        self.assertEqual(snapshot.series, "G6")
        self.assertEqual(snapshot.exterior_color, "Silver")
        self.assertIs(snapshot.raw, payload["msg_content"])
        self.assertEqual(snapshot.data_age_seconds, 42.0)
        self.assertTrue(snapshot.mqtt_connected)
        self.assertTrue(snapshot.has_data)
        self.assertEqual(snapshot.usable_kwh, 80.0)
        self.assertEqual(snapshot.car_version, "XOS 5.6.0")
        self.assertEqual(snapshot.outside_temp_c, 17.5)
        self.assertEqual(snapshot.data_ms, int(now.timestamp() * 1000))

    def test_restored_cache_has_data_but_no_silence_entry(self):
        from datetime import datetime, timedelta, timezone
        import types

        old = datetime.now(timezone.utc) - timedelta(hours=3)
        coordinator = types.SimpleNamespace(
            vin="L1NNSGHA0SB000000",
            data={"msg_content": {}},
            last_data_timestamp=old,
            poll_status={},
        )
        snapshot = mapping.snapshot_from_coordinator(coordinator)
        self.assertTrue(snapshot.has_data)
        self.assertGreater(snapshot.data_age_seconds, 2 * 60 * 60)
        self.assertEqual(
            mapping.tesla_vehicle_state(
                data_age_seconds=snapshot.data_age_seconds,
                mqtt_connected=snapshot.mqtt_connected,
                charging=False,
                driving=False,
            ),
            "asleep",
        )

    def test_never_loaded_coordinator_is_offline(self):
        import types

        coordinator = types.SimpleNamespace(vin="L1NNSGHA0SB000000")
        snapshot = mapping.snapshot_from_coordinator(coordinator)
        self.assertFalse(snapshot.has_data)
        self.assertIsNone(snapshot.data_age_seconds)
        self.assertTrue(snapshot.mqtt_connected)



if __name__ == "__main__":
    unittest.main(verbosity=2)
