"""Translate XPENG telemetry into the Tesla shapes TeslaMate consumes.

TeslaMate 4.x can be pointed at a third-party "API provider" instead of Tesla:
``TESLA_API_HOST`` supplies ``/api/1/products``, ``/api/1/vehicles/:id`` and
``/api/1/vehicles/:id/vehicle_data``; ``TESLA_AUTH_HOST``/``TESLA_AUTH_PATH``
supply the token endpoint; and ``TESLA_WSS_HOST`` supplies the old-style
streaming websocket. ``teslamate_provider.py`` serves those routes; this module
is the part that knows what to put in them.

Two rules shape everything here:

* **Tesla's units, not XPENG's.** TeslaMate converts miles/mph to km and stores
  km, °C and bar (see ``TeslaMate.Convert`` and the ``convert_*`` SQL functions
  in its migrations). So range and odometer go out in **miles**, speed in
  **mph**, temperatures in **°C** and tyre pressure in **bar**. Sending values
  already converted would be converted a second time.
* **Every section is always present and timestamped.** TeslaMate's vehicle
  process matches on the full ``drive_state``/``charge_state``/
  ``climate_state``/``vehicle_state``/``vehicle_config`` set and inserts a
  position at drive and charge start. A missing section gets the fetch result
  discarded ("incomplete fetch result"); a missing ``latitude``/``timestamp``
  makes the position insert fail and crashes the car's logger. So the builders
  below always emit the whole shape, carrying the last known position forward
  when a frame happens not to include one.

Pure: no Home Assistant import, so it is testable with plain ``python3``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .charge_current import current_limit_amperes

#: TeslaMate converts miles->km with 1.60934; use the same factor so the stored
#: km values round-trip to what the car reported.
KM_PER_MILE = 1.609344

#: 1 mph = 1.609344 km/h.
KM_PER_MPH = KM_PER_MILE

#: XPENG reports tyre pressure in kPa; TeslaMate's DB stores bar (its Grafana
#: ``convert_tire_pressure`` multiplies by 14.5038 to display psi).
KPA_PER_BAR = 100.0

#: The Tesla streaming column order, exactly as TeslaMate subscribes to it
#: (``TeslaApi.Stream.@columns``). The update frame is these prefixed by
#: ``time``, comma separated.
STREAM_COLUMNS: tuple[str, ...] = (
    "speed",
    "odometer",
    "soc",
    "elevation",
    "est_heading",
    "est_lat",
    "est_lng",
    "power",
    "shift_state",
    "range",
    "est_range",
    "heading",
)

#: Shift lever positions, from ``const.SHIFT_STATES``. 3 and 4 both mean P.
SHIFT_STATES: Mapping[int, str] = {0: "N", 1: "D", 2: "R", 3: "P", 4: "P"}

#: ``ext.vehicleMode`` with the car being driven (``const.VEHICLE_MODES``).
VEHICLE_MODE_DRIVING = 2

#: ``charge.charging_state`` codes, from ``charge_port.CHARGING_STATES``.
CHARGING_STATE_DISCONNECTED = 0
CHARGING_STATE_CONNECTED = 1
CHARGING_STATE_CHARGING = 2
CHARGING_STATE_COMPLETE = 3
CHARGING_STATE_ERROR = 4
CHARGING_STATE_IDLE = 8
CHARGING_STATE_UNLABELLED = 15

#: Codes that cannot happen without a cable in the port.
CHARGING_STATES_PLUGGED = (
    CHARGING_STATE_CONNECTED,
    CHARGING_STATE_CHARGING,
    CHARGING_STATE_COMPLETE,
)

#: ``charge_gun_status`` with nothing in the port.
CHARGE_GUN_EMPTY = 0

#: ``charge_connector_status`` values that mean a connector is present.
CHARGE_CONNECTOR_ATTACHED = (1, 2, 3, 4, 5, 6)

#: The ``slow_charge_port_status`` value that is *supposed* to mean "connected".
CHARGE_PORT_CONNECTED = 2

#: A parked car that has not produced real data for this long is treated as
#: asleep. The integration's passive parked query defaults to once an hour
#: (``DEFAULT_PARKED_SCAN_INTERVAL``) and a sleeping car answers it with a stub
#: that carries no data, so "no real data for two hours" is the point where
#: TeslaMate should stop asking for ``vehicle_data`` and let the car sleep.
ASLEEP_SECONDS = 2 * 60 * 60

#: Below this charger power a session is not plausible, matching the
#: ``binary_sensor.charging`` rule (code 2 or interval power > 0).
CHARGING_POWER_FLOOR_KW = 0.2

#: A charge session is only counted as DC above this power. Not a measurement
#: of the car: a heuristic, documented as one in docs/TESLAMATE.md.
DC_POWER_THRESHOLD_KW = 20.0

#: Tesla's charging_state strings TeslaMate special-cases. Sessions are only
#: opened for ``Starting``/``Charging`` and closed by anything else.
TESLA_CHARGING = "Charging"
TESLA_COMPLETE = "Complete"
TESLA_STOPPED = "Stopped"
TESLA_DISCONNECTED = "Disconnected"


# ── small readers ───────────────────────────────────────────


def dig(data: Any, *path: str, default: Any = None) -> Any:
    """Walk nested dicts, returning ``default`` instead of raising.

    XPENG payloads are partial by design: ``instant`` and ``interval`` arrive on
    different topics and a sleeping car's stub carries almost nothing.
    """
    node: Any = data
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            return default
        node = node[key]
    return node


def as_float(value: Any) -> float | None:
    """A float, or None for anything that is not a number.

    Booleans are rejected on purpose: ``True`` would otherwise become 1.0 and
    turn a missing reading into a plausible measurement.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    """An int, or None. Accepts integral floats and float-shaped strings."""
    number = as_float(value)
    return None if number is None else int(number)


def as_bool(value: Any) -> bool | None:
    """A boolean, or None when the field is absent."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return None


def km_to_miles(km: float | None, precision: int = 2) -> float | None:
    """Kilometres -> miles, the unit Tesla's API uses for distances."""
    if km is None:
        return None
    return round(km / KM_PER_MILE, precision)


def kmh_to_mph(kmh: float | None, precision: int = 1) -> float | None:
    """Kilometres per hour -> miles per hour."""
    if kmh is None:
        return None
    return round(kmh / KM_PER_MPH, precision)


def kpa_to_bar(kpa: float | None, precision: int = 1) -> float | None:
    """Kilopascals -> bar, the unit TeslaMate stores tyre pressure in."""
    if kpa is None:
        return None
    return round(kpa / KPA_PER_BAR, precision)


def round_half_up(value: float) -> int:
    """Round a non-negative number, halves away from zero.

    ``round()`` is banker's rounding; 2.5 mph should read 3, not 2, because
    TeslaMate's streaming client displays the number as a whole mph.
    """
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


# ── derived XPENG state ─────────────────────────────────────


def charge_section(raw: Any) -> Mapping[str, Any]:
    """``msg_content.instant.charge`` (the merged charge snapshot)."""
    section = dig(raw, "instant", "charge", default={})
    return section if isinstance(section, Mapping) else {}


def interval(raw: Any) -> Mapping[str, Any]:
    """``msg_content.interval`` (latitude/speed/charge power)."""
    section = dig(raw, "interval", default={})
    return section if isinstance(section, Mapping) else {}


def charge_cable_attached(charge: Mapping[str, Any]) -> bool:
    """Is a cable in the port? Mirrors ``charge_port.charge_cable_attached``.

    Duplicated rather than imported so this module stays free of the entity
    layer; ``tests/test_teslamate_mapping.py`` pins the two against each other.
    """
    if charge.get("charging_state") in CHARGING_STATES_PLUGGED:
        return True
    gun = charge.get("charge_gun_status")
    connector = charge.get("charge_connector_status")
    if gun is None and connector is None:
        return charge.get("slow_charge_port_status") == CHARGE_PORT_CONNECTED
    if gun is not None and gun != CHARGE_GUN_EMPTY:
        return True
    return connector in CHARGE_CONNECTOR_ATTACHED


def charge_schedule_armed(charge: Mapping[str, Any]) -> bool:
    """Is the car's own charge plan switched on? (``repeat_type != 0``.)"""
    repeat = charge.get("repeat_type")
    return repeat is not None and repeat != 0


def shift_state(raw: Any) -> str | None:
    """The Tesla shift lever state: ``P``, ``R``, ``N``, ``D``, or None.

    ``instant.drive.shift_state`` is authoritative when it arrives. A car that
    is plainly driving (``vehicleMode == 2``) but whose gear field is missing
    from this payload is reported as ``D``: a missing field must not end a
    drive TeslaMate is recording. Parked likewise falls back to ``P``.
    """
    code = as_int(dig(raw, "instant", "drive", "shift_state"))
    if code in SHIFT_STATES:
        return SHIFT_STATES[code]
    mode = as_int(dig(raw, "instant", "ext", "vehicleMode"))
    if mode == VEHICLE_MODE_DRIVING:
        return "D"
    if mode == 0:
        return "P"
    return None


def is_driving(raw: Any) -> bool:
    """Is the car being driven? Used for the online/asleep decision."""
    return shift_state(raw) in ("D", "R", "N") or (
        as_int(dig(raw, "instant", "ext", "vehicleMode")) == VEHICLE_MODE_DRIVING
    )


def charge_power_kw(raw: Any) -> float | None:
    """Charger power the car is drawing, in kW."""
    return as_float(dig(raw, "interval", "charge", "power"))


def is_charging(raw: Any) -> bool:
    """The same rule as ``binary_sensor.charging``: state code 2, or power > 0."""
    charge = charge_section(raw)
    if as_int(charge.get("charging_state")) == CHARGING_STATE_CHARGING:
        return True
    power = charge_power_kw(raw)
    return power is not None and power > CHARGING_POWER_FLOOR_KW


def tesla_charging_state(raw: Any) -> str | None:
    """Map XPENG's charge code onto the Tesla strings TeslaMate understands.

    TeslaMate opens a charging process only for ``Starting``/``Charging`` and
    closes it on anything else, so the mapping's job is to say "charging" only
    while energy is actually going in. ``Connected``/``Idle`` (a cable is in,
    nothing is drawing - including a car waiting for its charge plan) becomes
    ``Stopped``; only an absent cable becomes ``Disconnected``.
    """
    charge = charge_section(raw)
    code = as_int(charge.get("charging_state"))
    if code is None:
        # No charge snapshot at all. Power is the one other piece of evidence.
        power = charge_power_kw(raw)
        if power is not None and power > CHARGING_POWER_FLOOR_KW:
            return TESLA_CHARGING
        return None
    if code == CHARGING_STATE_CHARGING:
        return TESLA_CHARGING
    if code == CHARGING_STATE_COMPLETE:
        return TESLA_COMPLETE
    if code == CHARGING_STATE_DISCONNECTED:
        return TESLA_DISCONNECTED
    if code in (
        CHARGING_STATE_CONNECTED,
        CHARGING_STATE_IDLE,
        CHARGING_STATE_ERROR,
        CHARGING_STATE_UNLABELLED,
    ):
        return TESLA_STOPPED if charge_cable_attached(charge) else TESLA_DISCONNECTED
    # An unobserved code: say "Stopped" only if the cable says something is
    # there, never claim a session.
    return TESLA_STOPPED if charge_cable_attached(charge) else TESLA_DISCONNECTED


def tesla_vehicle_state(
    *,
    data_age_seconds: float | None,
    mqtt_connected: bool,
    charging: bool,
    driving: bool,
) -> str:
    """Tesla's ``state`` for the vehicle: ``online``, ``asleep`` or ``offline``.

    TeslaMate only asks for ``vehicle_data`` while it believes the car is
    reachable; in ``asleep``/``offline`` it polls the cheap vehicle object every
    30 seconds and waits. That is exactly the behaviour a parked XPENG needs,
    because the integration's passive query cadence is minutes-to-an-hour and a
    sleeping car answers with a stub.

    * a driving or charging car is online, whatever the age of the last frame;
    * a disconnected cloud socket is ``offline`` (nothing can be learned);
    * a parked car with no real data for ``ASLEEP_SECONDS`` is ``asleep``.
    """
    if charging or driving:
        return "online"
    if not mqtt_connected:
        return "offline"
    if data_age_seconds is not None and data_age_seconds > ASLEEP_SECONDS:
        return "asleep"
    return "online"


# ── stable Tesla ids ────────────────────────────────────────


def stable_ids(vin: str) -> tuple[int, int]:
    """Stable ``(id, vehicle_id)`` integers for a VIN.

    TeslaMate stores both in ``integer`` columns (``cars.eid``/``vid``) and uses
    ``id`` in every URL, so they have to be integers, stable across restarts,
    and different from each other. A CRC of the VIN is all three; the two salts
    keep the pair distinct. 31 bits keeps them inside a signed PostgreSQL
    ``integer`` on every platform.
    """
    import zlib

    def one(salt: str) -> int:
        return (zlib.crc32(f"{salt}:{vin}".encode("utf-8")) & 0x7FFFFFFF) or 1

    return one("eid"), one("vid")


# ── snapshot ────────────────────────────────────────────────


@dataclass(frozen=True)
class Telemetry:
    """Everything the builders need about one car at one moment.

    The provider builds this from the coordinator; tests build it by hand, which
    is what keeps the mapping testable without Home Assistant.
    """

    vin: str
    name: str | None = None
    series: str | None = None
    exterior_color: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)
    rest: Mapping[str, Any] = field(default_factory=dict)
    now_ms: int = 0
    data_ms: int = 0
    data_age_seconds: float | None = None
    mqtt_connected: bool = True
    usable_kwh: float = 87.5
    car_version: str | None = None
    outside_temp_c: float | None = None
    #: False only when nothing has ever been received for this VIN (a fresh
    #: install before the first MQTT frame). Distinguishes "stale" from "never".
    has_data: bool = True
    #: Filled by the provider with its own last-known position.
    fallback_latitude: float | None = None
    fallback_longitude: float | None = None
    #: Set by the provider to keep a session open across a state-code blip.
    #: None means "read the raw payload"; True forces ``Charging``.
    force_charging: bool | None = None


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _timestamp_ms(snapshot: Telemetry) -> int:
    """Non-zero, non-decreasing timestamp for every section.

    TeslaMate discards a fetch whose ``drive_state.timestamp`` goes backwards
    and dates state rows from it, so the provider keeps it monotonic; a
    restored-from-cache snapshot can be older than "now", so ``data_ms`` wins.
    """
    return int(snapshot.data_ms or snapshot.now_ms or _now_ms())


def _position(snapshot: Telemetry) -> tuple[float, float]:
    """Latitude/longitude, carried forward when the frame has none.

    A position is mandatory in TeslaMate's ``positions`` table; without one the
    insert raises and TeslaMate's car process crashes. The provider therefore
    remembers the last position it served, and this is the last-resort value
    used only before a car has ever reported one.
    """
    lat = as_float(dig(snapshot.raw, "interval", "drive", "latitude"))
    lon = as_float(dig(snapshot.raw, "interval", "drive", "longitude"))
    if lat is None:
        lat = snapshot.fallback_latitude
    if lon is None:
        lon = snapshot.fallback_longitude
    return (lat if lat is not None else 0.0, lon if lon is not None else 0.0)


def _power_kw(snapshot: Telemetry) -> int:
    """Drive power in kW, negative while charging (Tesla's convention).

    XPENG does not expose instantaneous motor power to this integration, so a
    driving car reports 0 rather than a fabricated estimate. Charging does have
    a measurement (``interval.charge.power``) and is reported negative, which is
    what TeslaMate's own charging detection expects.
    """
    if snapshot.force_charging or is_charging(snapshot.raw):
        power = charge_power_kw(snapshot.raw) or 0.0
        return -round_half_up(power)
    return 0


def _battery(snapshot: Telemetry) -> tuple[int | None, float | None]:
    """(state of charge %, available range in miles)."""
    soc = as_int(dig(snapshot.raw, "instant", "charge", "battery_soc"))
    range_km = as_float(
        dig(snapshot.raw, "instant", "odometer", "avalible_driving_distance")
    )
    return soc, km_to_miles(range_km)


def build_drive_state(snapshot: Telemetry) -> dict[str, Any]:
    """Tesla's ``drive_state``."""
    latitude, longitude = _position(snapshot)
    heading = as_float(dig(snapshot.raw, "interval", "drive", "angle"))
    speed = kmh_to_mph(as_float(dig(snapshot.raw, "interval", "drive", "speed")))
    timestamp = _timestamp_ms(snapshot)
    return {
        "latitude": latitude,
        "longitude": longitude,
        "heading": heading,
        "speed": speed,
        "power": _power_kw(snapshot),
        "shift_state": shift_state(snapshot.raw),
        "timestamp": timestamp,
        "gps_as_of": timestamp,
        "native_latitude": latitude,
        "native_longitude": longitude,
        "native_location_supported": 1,
        "native_type": "WGS",
        "active_route_destination": None,
        "active_route_latitude": None,
        "active_route_longitude": None,
        "active_route_energy_at_arrival": None,
        "active_route_miles_to_arrival": None,
        "active_route_minutes_to_arrival": None,
        "active_route_traffic_minutes_delay": None,
    }


def build_charge_state(
    snapshot: Telemetry,
    *,
    energy_added_kwh: float | None = None,
    range_added_miles: float | None = None,
) -> dict[str, Any]:
    """Tesla's ``charge_state``.

    ``charge_energy_added`` is the one value XPENG does not publish for the live
    session (``chargeStatistics.usageElectricity`` describes the previous one),
    so the provider tracks the session and passes the estimate in - without it
    TeslaMate's charging statistics all read nil.
    """
    charge = charge_section(snapshot.raw)
    soc, range_miles = _battery(snapshot)
    power = charge_power_kw(snapshot.raw)
    plugged = charge_cable_attached(charge)
    tesla_state = tesla_charging_state(snapshot.raw)
    if snapshot.force_charging:
        # The provider's grace period: the car's code can alternate between
        # Charging and 15 mid-session (measured on a G6), and ending a TeslaMate
        # session on that blip would split one charge into two.
        tesla_state = TESLA_CHARGING
    fast = bool(power is not None and power >= DC_POWER_THRESHOLD_KW)
    limit = as_int(charge.get("max_range_charge"))
    current = as_float(dig(snapshot.raw, "interval", "charge", "electricity"))
    voltage = as_float(dig(snapshot.raw, "interval", "charge", "voltage"))
    remaining_min = as_float(charge.get("time_to_complete_charge_min"))
    timestamp = _timestamp_ms(snapshot)
    return {
        "battery_level": soc,
        "usable_battery_level": soc,
        "battery_range": range_miles,
        "ideal_battery_range": range_miles,
        "est_battery_range": range_miles,
        "charge_limit_soc": limit,
        "charge_limit_soc_std": limit,
        "charge_limit_soc_min": limit,
        "charge_limit_soc_max": limit,
        "charging_state": tesla_state,
        "charge_energy_added": energy_added_kwh,
        "charge_miles_added_rated": range_added_miles,
        "charge_miles_added_ideal": range_added_miles,
        "charger_power": power if power is not None else 0.0,
        "charger_voltage": voltage,
        "charger_actual_current": current,
        "charger_phases": None,
        "charge_rate": None,
        # TeslaMate's summary and can_fall_asleep() read these; a car waiting
        # for its plan is plugged in, not charging.
        "charge_port_door_open": plugged,
        "charge_port_latch": "Engaged" if plugged else "Disengaged",
        "fast_charger_present": fast,
        "fast_charger_type": "CCS" if fast else None,
        "fast_charger_brand": "XPENG" if fast else None,
        "conn_charge_cable": "CCS" if plugged else None,
        "scheduled_charging_pending": charge_schedule_armed(charge),
        "scheduled_charging_start_time": None,
        "time_to_full_charge": (
            round(remaining_min / 60.0, 2) if remaining_min is not None else None
        ),
        "battery_heater_on": False,
        "battery_heater": False,
        "battery_heater_no_power": False,
        "not_enough_power_to_heat": False,
        "trip_charging": False,
        "charge_enable_request": tesla_state == TESLA_CHARGING,
        "user_charge_enable_request": tesla_state == TESLA_CHARGING,
        "charge_current_request": current_limit_amperes(charge.get("electric_current_limit")),
        "charge_current_request_max": current_limit_amperes(charge.get("electric_current_limit")),
        "charge_to_max_range": bool(limit is not None and limit >= 100),
        "max_range_charge_counter": 0,
        "managed_charging_active": None,
        "managed_charging_start_time": None,
        "managed_charging_user_canceled": None,
        "charge_port_cold_weather_mode": None,
        "timestamp": timestamp,
    }


def _level(value: Any) -> int | None:
    """Seat heat/ventilation as Tesla's 0-3 level."""
    return as_int(value)


def build_climate_state(snapshot: Telemetry) -> dict[str, Any]:
    """Tesla's ``climate_state``.

    Temperatures stay in °C - TeslaMate stores Celsius and converts for display
    (``convert_celsius`` in its migrations), unlike distances which are stored
    in km and therefore have to arrive in miles.
    """
    hvac = dig(snapshot.raw, "instant", "hvac", default={})
    hvac = hvac if isinstance(hvac, Mapping) else {}
    seat = dig(snapshot.raw, "instant", "seat", default={})
    seat = seat if isinstance(seat, Mapping) else {}
    inside = as_float(hvac.get("hvac_inner_temp_float"))
    setting = as_float(hvac.get("hvac_temp_float"))
    fan = as_int(hvac.get("hvac_wind_speed"))
    timestamp = _timestamp_ms(snapshot)
    return {
        "inside_temp": inside,
        "outside_temp": snapshot.outside_temp_c,
        "is_climate_on": as_bool(hvac.get("hvac_on")) is True,
        "is_auto_conditioning_on": as_bool(hvac.get("hvac_auto")),
        "is_preconditioning": False,
        "is_front_defroster_on": as_bool(hvac.get("hvac_defrost")) is True,
        "is_rear_defroster_on": False,
        "driver_temp_setting": setting,
        "passenger_temp_setting": setting,
        "fan_status": fan,
        "side_mirror_heaters": as_bool(hvac.get("hvac_mirror_heat_mode")),
        "steering_wheel_heater": _level(
            dig(snapshot.raw, "instant", "steerWheel", "steering_wheel_heat")
        ),
        "seat_heater_left": _level(seat.get("driver_seat_heat")),
        "seat_heater_right": _level(seat.get("passenger_seat_heat")),
        "seat_heater_rear_left": _level(seat.get("left_rear_seat_heat")),
        "seat_heater_rear_right": _level(seat.get("right_rear_seat_heat")),
        "seat_heater_rear_center": None,
        "seat_heater_rear_left_back": None,
        "seat_heater_rear_right_back": None,
        "climate_keeper_mode": None,
        "battery_heater": False,
        "battery_heater_no_power": False,
        "remote_heater_control_enabled": None,
        "defrost_mode": None,
        "max_avail_temp": None,
        "min_avail_temp": None,
        "left_temp_direction": None,
        "right_temp_direction": None,
        "smart_preconditioning": None,
        "wiper_blade_heater": None,
        "timestamp": timestamp,
    }


def build_vehicle_state(snapshot: Telemetry) -> dict[str, Any]:
    """Tesla's ``vehicle_state``.

    ``can_fall_asleep`` treats any open door/trunk or a positive drive power as
    "still in use", so the door flags map straight from the car's ajar fields.
    """
    door = dig(snapshot.raw, "instant", "door", default={})
    door = door if isinstance(door, Mapping) else {}
    tail = dig(snapshot.raw, "instant", "tail", default={})
    tail = tail if isinstance(tail, Mapping) else {}
    tpms = dig(snapshot.raw, "instant", "tpms", default={})
    tpms = tpms if isinstance(tpms, Mapping) else {}
    odometer_km = as_float(dig(snapshot.raw, "instant", "odometer", "total_mileage"))
    locked = as_bool(door.get("driver_door_lock_st"))
    sentry = as_bool(dig(snapshot.raw, "instant", "ext", "soldier_camera_status"))
    timestamp = _timestamp_ms(snapshot)
    return {
        "odometer": km_to_miles(odometer_km, 2),
        "locked": locked is True,
        "sentry_mode": sentry is True,
        "df": as_int(door.get("driver_door_ajar_st")),
        "pf": as_int(door.get("passenger_door_ajar_st")),
        "dr": as_int(door.get("rear_left_door_ajar_st")),
        "pr": as_int(door.get("rear_right_door_ajar_st")),
        "ft": as_int(tail.get("tail_status")),
        "rt": None,
        "fr": as_int(door.get("bonnet_ajar_st")),
        "tpms_pressure_fl": kpa_to_bar(as_float(tpms.get("tpms_pressure_fl"))),
        "tpms_pressure_fr": kpa_to_bar(as_float(tpms.get("tpms_pressure_fr"))),
        "tpms_pressure_rl": kpa_to_bar(as_float(tpms.get("tpms_pressure_rl"))),
        "tpms_pressure_rr": kpa_to_bar(as_float(tpms.get("tpms_pressure_rr"))),
        "tpms_soft_warning_fl": None,
        "tpms_soft_warning_fr": None,
        "tpms_soft_warning_rl": None,
        "tpms_soft_warning_rr": None,
        "fd_window": 0,
        "fp_window": 0,
        "rd_window": 0,
        "rp_window": 0,
        "car_version": snapshot.car_version,
        "vehicle_name": snapshot.name,
        "is_user_present": None,
        "service_mode": False,
        "center_display_state": None,
        "sun_roof_state": None,
        "sun_roof_installed": False,
        "sun_roof_percent_open": None,
        "software_update": None,
        "media_state": None,
        "timestamp": timestamp,
    }


def build_vehicle_config(snapshot: Telemetry) -> dict[str, Any]:
    """Tesla's ``vehicle_config``.

    TeslaMate's ``identify/1`` matches on ``car_type`` for its model name and
    treats an unknown type as "no model", which only hides one line in the UI.
    The important part is that this object is not nil: ``identify/1`` is matched
    with ``{:ok, attrs} = identify(vehicle)`` on every online transition, so an
    absent config crashes the car's logger.
    """
    return {
        "car_type": "xpeng",
        "trim_badging": (snapshot.series or "").upper() or None,
        "exterior_color": snapshot.exterior_color,
        "wheel_type": None,
        "spoiler_type": None,
        "roof_color": None,
        "seat_type": None,
        "rear_seat_heaters": None,
        "rear_seat_type": None,
        "third_row_seats": None,
        "sun_roof_installed": 0,
        "has_air_suspension": False,
        "has_ludicrous_mode": False,
        "motorized_charge_port": True,
        "plg": None,
        "rhd": None,
        "eu_vehicle": None,
        "charge_port_type": "CCS",
        "car_special_type": "base",
        "perf_config": None,
        "key_version": None,
        "can_accept_navigation_requests": False,
        "can_actuate_trunks": True,
        "timestamp": _timestamp_ms(snapshot),
    }


def build_vehicle(
    snapshot: Telemetry, *, state: str, ids: tuple[int, int]
) -> dict[str, Any]:
    """The vehicle object (products list, ``/vehicles/:id`` and ``vehicle_data``).

    ``id`` and ``vehicle_id`` are integers because TeslaMate stores them in
    integer columns; both are CRC-derived from the VIN so they survive restarts.
    """
    eid, vid = ids
    return {
        "id": eid,
        "vehicle_id": vid,
        "vin": snapshot.vin,
        "display_name": snapshot.name or snapshot.series or "XPENG",
        "state": state,
        "api_version": 1,
        "in_service": False,
        "option_codes": "",
        "color": None,
        "access_type": "OWNER",
        "calendar_enabled": False,
        "tokens": [],
        "backseat_token": None,
        "backseat_token_updated_at": None,
        "charge_state": None,
        "climate_state": None,
        "drive_state": None,
        "gui_settings": None,
        "vehicle_config": build_vehicle_config(snapshot),
        "vehicle_state": None,
    }


def build_vehicle_data(
    snapshot: Telemetry,
    *,
    state: str,
    ids: tuple[int, int],
    energy_added_kwh: float | None = None,
    range_added_miles: float | None = None,
) -> dict[str, Any]:
    """The full ``vehicle_data`` response body.

    TeslaMate's fetch handler matches all five sections at once; anything less
    is logged as an incomplete fetch and the car never leaves the offline state.
    """
    vehicle = build_vehicle(snapshot, state=state, ids=ids)
    vehicle["charge_state"] = build_charge_state(
        snapshot,
        energy_added_kwh=energy_added_kwh,
        range_added_miles=range_added_miles,
    )
    vehicle["climate_state"] = build_climate_state(snapshot)
    vehicle["drive_state"] = build_drive_state(snapshot)
    vehicle["vehicle_state"] = build_vehicle_state(snapshot)
    vehicle["gui_settings"] = {
        "gui_distance_units": "mi/hr",
        "gui_temperature_units": "F",
        "gui_charge_rate_units": "mi/hr",
        "gui_24_hour_time": True,
        "gui_range_display": "Rated",
        "timestamp": _timestamp_ms(snapshot),
    }
    return vehicle


# ── Home Assistant coordinator adapter ─────────────────────


def _first_present(mapping: Any, keys: Iterable[str]) -> str | None:
    if not isinstance(mapping, Mapping):
        return None
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def snapshot_from_coordinator(coordinator: Any) -> Telemetry:
    """Build a :class:`Telemetry` from an ``XPengDataCoordinator``.

    Reads only attributes the coordinator already maintains - no Home Assistant
    import and no I/O, so it is testable with a plain object. The attribute set
    is pinned by ``tests/test_teslamate_mapping.py`` against ``coordinator.py``.

    ``data_age_seconds`` is the same "meaningful silence" the integration's
    watchdog measures: time since a message actually carried data, not since the
    topic last said anything, so a sleeping car's stub does not look healthy.
    """
    now = time.time()
    poll = getattr(coordinator, "poll_status", None) or {}
    last = getattr(coordinator, "last_data_timestamp", None)
    if not isinstance(last, datetime):
        last = None
    last_epoch = last.timestamp() if last is not None else None
    silence = poll.get("meaningful_silence_seconds")
    if silence is None and last_epoch is not None:
        silence = now - last_epoch
    weather = getattr(coordinator, "weather", None) or {}
    appearance = getattr(coordinator, "vehicle_appearance", None) or {}
    payload = getattr(coordinator, "data", None) or {}
    # coordinator.data is {"vin", "msg_content": {...}}; the builders read the
    # msg_content tree ("instant"/"interval"/"digital_vehicle"). Accept a bare
    # msg_content too, which is what the tests and cached snapshots hand over.
    msg_content = payload.get("msg_content") if isinstance(payload, Mapping) else None
    raw = msg_content if isinstance(msg_content, Mapping) else payload
    vin = coordinator.vin
    name = (
        getattr(coordinator, "nick_name", None)
        or getattr(coordinator, "series_name", None)
        or f"XPENG {vin[-6:]}"
    )
    return Telemetry(
        vin=vin,
        name=name,
        series=getattr(coordinator, "series_name", None)
        or getattr(coordinator, "vehicle_type_name", None),
        exterior_color=_first_present(
            appearance,
            ("colour", "color", "colorName", "exteriorColor", "colourName"),
        ),
        raw=raw,
        now_ms=int(now * 1000),
        data_ms=int(last_epoch * 1000) if last_epoch is not None else 0,
        data_age_seconds=silence,
        has_data=bool(raw) or last_epoch is not None,
        mqtt_connected=bool(poll.get("mqtt_connected", True)),
        usable_kwh=float(getattr(coordinator, "usable_battery_kwh", 87.5) or 87.5),
        car_version=getattr(coordinator, "xos_version", None),
        outside_temp_c=as_float(weather.get("realtimeTemperature")),
    )


# ── streaming ───────────────────────────────────────────────


def stream_value(snapshot: Telemetry) -> str:
    """One ``data:update`` payload, in ``STREAM_COLUMNS`` order.

    TeslaMate's stream client splits this on commas and parses each column with
    ``Integer.parse``/``Float.parse``; empty strings become nil. ``power`` is
    deliberately always numeric: a nil power is TeslaMate's "fake online"
    signal and it would refuse to treat a real wake-up as one.
    """
    soc, range_miles = _battery(snapshot)
    latitude, longitude = _position(snapshot)
    heading = as_float(dig(snapshot.raw, "interval", "drive", "angle"))
    speed_mph = kmh_to_mph(as_float(dig(snapshot.raw, "interval", "drive", "speed")))
    odometer_km = as_float(dig(snapshot.raw, "instant", "odometer", "total_mileage"))
    shift = shift_state(snapshot.raw) or ""
    values: dict[str, str] = {
        "time": str(_timestamp_ms(snapshot)),
        "speed": "" if speed_mph is None else str(round_half_up(speed_mph)),
        "odometer": "" if odometer_km is None else str(km_to_miles(odometer_km, 2)),
        "soc": "" if soc is None else str(soc),
        "elevation": "",
        "est_heading": "" if heading is None else str(round_half_up(heading)),
        "est_lat": str(latitude),
        "est_lng": str(longitude),
        "power": str(_power_kw(snapshot)),
        "shift_state": shift,
        "range": "" if range_miles is None else str(round_half_up(range_miles)),
        "est_range": "" if range_miles is None else str(round_half_up(range_miles)),
        "heading": "" if heading is None else str(round_half_up(heading)),
    }
    return ",".join(values[column] for column in ("time", *STREAM_COLUMNS))


def frame(
    msg_type: str,
    *,
    tag: str | None = None,
    value: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """A streaming websocket frame (a JSON object, sent as text)."""
    payload: dict[str, Any] = {"msg_type": msg_type}
    if tag is not None:
        payload["tag"] = tag
    if value is not None:
        payload["value"] = value
    payload.update(extra)
    return payload


def subscribe_value(columns: Iterable[str] | None = None) -> str:
    """The ``value`` of a ``data:subscribe_oauth`` frame (what we advertise)."""
    columns = tuple(columns) if columns is not None else STREAM_COLUMNS
    return ",".join(columns)
