"""Constants for the XPENG integration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntityDescription,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfPower,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)

import json as _json
from datetime import datetime as _dt, timezone as _tz

from .charge_port import CHARGE_PORT_CONTROLS_OPTION

DOMAIN: Final = "xpeng"

#: What a caller is told when a command could not be published. Shared so the
#: services, the buttons and the lock all say the same thing: a command that was
#: never sent is not a command that was sent, and the difference was invisible
#: while the only evidence was a log line (see services.py::_send).
COMMAND_NOT_SENT_MESSAGE: Final = (
    "Command not sent: the XPENG MQTT connection is down, so nothing reached "
    "the car. Check 'MQTT connected' and try again."
)


def _safe_float(v: Any) -> float:
    """Safely convert a value to float, returning 0 on failure."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_ota(v: Any) -> dict:
    """Parse the OTA field which can be a JSON string or a dict."""
    if not v:
        return {}
    if isinstance(v, str):
        try:
            return _json.loads(v)
        except (ValueError, TypeError):
            return {}
    if isinstance(v, dict):
        return v
    return {}


def _parse_ota_status(v: Any) -> str:
    """Parse OTA status code to human-readable string."""
    ota = _parse_ota(v)
    status = ota.get("status")
    if status is None:
        return "Unknown"
    return {
        0: "No update", 1: "Available", 2: "Downloading",
        3: "Ready", 4: "Installing", 99: "Up to date",
    }.get(status, f"Unknown ({status})")


def _parse_ota_timestamp(v: Any) -> Any:
    """Parse the OTA timestamp into a UTC datetime for a `timestamp` sensor.

    It used to be a pre-formatted local string; the entity is a `timestamp`
    sensor now, so Home Assistant localises it and can age it (same reason as
    `last_data_update`). `None` means unknown, never a sentinel string.
    """
    ota = _parse_ota(v)
    ts = ota.get("timestamp")
    if not ts:
        return None
    try:
        return _dt.fromtimestamp(ts / 1000, tz=_tz.utc)
    except (ValueError, TypeError, OSError):
        return None

# Config keys
CONF_CLIENT_ID: Final = "client_id"
CONF_PASSWORD: Final = "password"
CONF_SERVER: Final = "server"
CONF_VIN: Final = "vin"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_LOCAL_MQTT: Final = "local_mqtt"
CONF_TOKEN_FILE: Final = "token_file"
CONF_TENANT_FILE: Final = "tenant_file"
CONF_CERT_DIR: Final = "cert_dir"
# Per-install device identity. Minted on first run and persisted in the entry
# options; the option name matches what tools/configure_ha_entry.py already
# writes, so installs configured that way keep the same device id.
CONF_CONFIGURED_DEVICE_ID: Final = "configured_device_id"
CONF_MQTT_REFRESH_INTERVAL: Final = "mqtt_refresh_interval"
CONF_XPENG_EMAIL: Final = "xpeng_email"
CONF_XPENG_PASSWORD: Final = "xpeng_password"
CONF_REFRESH_METHOD: Final = "refresh_method"
CONF_LOGIN_TOKENS: Final = "login_tokens"
CONF_TENANT_DATA: Final = "tenant_data"
CONF_REST_SCAN_INTERVAL: Final = "rest_scan_interval"
CONF_AUTH_METHOD: Final = "auth_method"
#: When credentials last arrived, as epoch seconds. Written by every path that
#: installs a token + tenant pair - the renewal app's ``import_credentials`` call,
#: the Repairs card, a reauth, a fresh install - and read back by
#: ``sensor.xpeng_<car>_last_renewal``. Deliberately in entry *data* rather than in
#: memory: a restart must not erase the only honest answer to "when did the
#: credentials last change", and the app's own /healthz counters are not something
#: a dashboard can show.
CONF_LAST_RENEWAL: Final = "last_renewal"

# Auth methods for CONF_AUTH_METHOD. "email" is the classic
# email+password+email-code flow (captcha-gated at pwdLogin). "google" is the
# Sign-in-with-Google path (no password, no captcha) — see google_login.py.
# "import" is a token pair minted outside Home Assistant by the onboarding tool.
AUTH_METHOD_EMAIL: Final = "email"
AUTH_METHOD_GOOGLE: Final = "google"
#: Lives here, not in config_flow.py, because repairs.py imports it from const:
#: defining it in the flow module makes `from .const import AUTH_METHOD_IMPORT`
#: raise ImportError, and Home Assistant answers an unimportable repairs
#: platform by substituting its own no-op ConfirmRepairFlow — the card then
#: renders with no input fields, "fixes" the issue by deleting it, and leaves
#: the credentials dead.
AUTH_METHOD_IMPORT: Final = "import"
CONF_MQTT_LISTENER_ENABLED: Final = "mqtt_listener_enabled"
CONF_MQTT_LISTENER_TOPICS: Final = "mqtt_listener_topics"
CONF_MQTT_TOPIC_DISCOVERY: Final = "mqtt_topic_discovery"
CONF_CAPTURE_ENABLED: Final = "capture_enabled"
CONF_CAPTURE_PATH: Final = "capture_path"
CONF_CAPTURE_TOPICS: Final = "capture_topics"
CONF_PARKED_SCAN_INTERVAL: Final = "parked_scan_interval"
CONF_ICCID: Final = "iccid"
CONF_USABLE_BATTERY_KWH: Final = "usable_battery_kwh"
#: Whether this car has motorized charge-port controls. The key lives in
#: charge_port.py (a pure module) so ``controls_enabled`` can be tested without
#: Home Assistant; re-exported here because options keys are spelled in const.
CONF_CHARGE_PORT_CONTROLS: Final = CHARGE_PORT_CONTROLS_OPTION

# Command permissions. The per-group option keys ARE the group names
# ("access", "charging", ...) - see command_policy.ALL_GROUPS - so the shape
# the options form writes and the shape the policy reads are the same one.
# These two are the only keys that are not a group name.
CONF_COMMAND_MODE: Final = "command_mode"
CONF_ALLOW_WAKE: Final = "allow_wake"
# TeslaMate provider (teslamate_provider.py). Off by default: it opens a port.
CONF_TESLAMATE_ENABLED: Final = "teslamate_provider_enabled"
CONF_TESLAMATE_PORT: Final = "teslamate_provider_port"
CONF_TESLAMATE_TOKEN: Final = "teslamate_provider_token"

# Defaults
DEFAULT_SCAN_INTERVAL: Final = 60
DEFAULT_SERVER: Final = "iot-app-global.xiaopeng.com:8885"
DEFAULT_MQTT_REFRESH_INTERVAL: Final = 45  # seconds
DEFAULT_TOKEN_FILE: Final = "/config/custom_components/xpeng/xpeng_tokens.json"
DEFAULT_TENANT_FILE: Final = "/config/custom_components/xpeng/xpeng_tenant.json"
DEFAULT_CERT_DIR: Final = "/config/custom_components/xpeng/certs"

#: Where a capture is written by default. ``/share`` rather than ``/config`` on
#: purpose: a config-directory capture is carried into every backup and sits in the
#: directory Home Assistant watches, and ``/share`` is the conventional place for
#: exactly this. Installs without ``/share`` (Container) fall back to
#: ``<config>/xpeng-can`` - see capture.open_recorder().
DEFAULT_CAPTURE_PATH: Final = "/share/xpeng-can"
#: Empty means "not minted yet"; __init__.py mints one and persists it.
DEFAULT_CONFIGURED_DEVICE_ID: Final = ""
DEFAULT_REST_SCAN_INTERVAL: Final = 300  # seconds
DEFAULT_PARKED_SCAN_INTERVAL: Final = 3600  # seconds (1 hour when parked)
# Usable pack size used to convert the parked SOC drop into a kWh estimate for
# the "Parked consumption" sensor. Default is the G6 Long Range NMC pack
# (~87.5 kWh usable). Override in the integration options for other variants
# (e.g. G6 Standard Range LFP ≈ 66 kWh).
DEFAULT_USABLE_BATTERY_KWH: Final = 87.5
#: What a **new** entry starts at - the default selection on the setup screen and
#: in the renewal app. Safe Plus: it is what most installs are for (watch the car,
#: charge it) while leaving unlocking, the trunk, OTA and raw commands off.
#:
#: This is NOT the fallback for an entry with no stored policy. That lives in
#: command_policy.normalize_groups and is FULL, deliberately: an install that
#: predates this option must not silently lose a capability its automations rely
#: on. Two different questions - "what should a new one be?" and "what was this
#: one already doing?" - and answering both with one constant would break the
#: second.
DEFAULT_COMMAND_MODE: Final = "safe_plus"

#: Port the optional TeslaMate provider listens on. 8200 rather than 8123 (Home
#: Assistant) or 4000 (TeslaMate's own web UI) so neither can shadow the other.
DEFAULT_TESLAMATE_PORT: Final = 8200

# MQTT topics
MQTT_SEND_TOPIC: Final = "Xmart/Send/User/{user_id}"
MQTT_RECV_TOPIC: Final = "Xmart/Msg/User/{user_id}"

# Known XPENG MQTT topic candidates for discovery
MQTT_DISCOVERY_CANDIDATES: Final = (
    "Xmart/Car/+/State/CanPack1s",
    "Xmart/Tbox/+/CanPack1s",
    "Xmart/TboxD21B/+/CanPack1s",
    "Xmart/Tbox/+/StatPack",
    "Xmart/Client/Connect/+",
    "Xmart/Log/statPack/Repub/+",
    "Xmart/Car/+/CanPackV2/Repub",
    "Xmart/Send/Emq/Dial",
)

# Charging states, charge-port states and the cable test live in
# ``charge_port.py``: a pure module, so the mapping can be tested without Home
# Assistant. Imported here because the entity descriptions below are built from
# them.
from .charge_port import (
    CHARGE_PORT_STATES,
    CHARGE_STATE_OPTIONS,
    charge_cable_state,
    charge_port_state,
    charging_state_label,
)


# Platforms
PLATFORMS: Final = ["sensor", "binary_sensor", "device_tracker", "button", "switch", "number", "image", "climate", "lock", "select", "calendar", "cover"]


# Shift state mapping
SHIFT_STATES: Final = {
    0: "N",
    1: "D",
    2: "R",
    3: "P",
    4: "P",
}

# Vehicle mode mapping
VEHICLE_MODES: Final = {
    0: "Parked",
    1: "Standby",
    2: "Driving",
}

# Range estimation standard mapping. Derived from XPENG SystemUI APK
# (CarController.getDriveDistance switch on getEnduranceMileageMode), with
# +1 offset for the cloud's 1-based encoding vs the VCU's 0-based mode int.
RANGE_TYPES: Final = {
    1: "NEDC",
    2: "WLTP",
    3: "CLTC",
    4: "Dynamic",
}


# ── Sensor Descriptions ─────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class XPengSensorEntityDescription(SensorEntityDescription):
    """Describe an XPENG sensor."""

    value_path: str = ""
    value_fn: Any = None


SENSOR_DESCRIPTIONS: tuple[XPengSensorEntityDescription, ...] = (
    # === Battery & Charging ===
    XPengSensorEntityDescription(
        key="battery_soc",
        translation_key="battery_soc",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_path="msg_content.instant.charge.battery_soc",
    ),
    XPengSensorEntityDescription(
        key="range",
        translation_key="range",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_path="msg_content.instant.odometer.avalible_driving_distance",
        icon="mdi:map-marker-distance",
    ),
    XPengSensorEntityDescription(
        key="odometer",
        translation_key="odometer",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_path="msg_content.instant.odometer.total_mileage",
        icon="mdi:counter",
    ),
    XPengSensorEntityDescription(
        key="charging_state",
        translation_key="charging_state",
        value_path="msg_content.instant.charge",
        icon="mdi:ev-station",
        device_class=SensorDeviceClass.ENUM,
        options=list(CHARGE_STATE_OPTIONS),
        # Code 15 has no trustworthy label of its own (see charge_port.py). It is
        # answered from the car's charge plan when that is armed ("Scheduled") and
        # from the cable otherwise - never guessed from the code.
        value_fn=charging_state_label,
    ),
    XPengSensorEntityDescription(
        key="charge_power",
        translation_key="charge_power",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_path="msg_content.interval.charge.power",
    ),
    XPengSensorEntityDescription(
        key="charge_voltage",
        translation_key="charge_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_path="msg_content.interval.charge.voltage",
    ),
    XPengSensorEntityDescription(
        key="charge_current",
        translation_key="charge_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_path="msg_content.interval.charge.electricity",
    ),
    XPengSensorEntityDescription(
        key="charge_rate",
        translation_key="charge_rate",
        state_class=SensorStateClass.MEASUREMENT,
        value_path="msg_content.interval.charge.charge_rate",
        icon="mdi:speedometer",
    ),
    XPengSensorEntityDescription(
        key="discharge_capacity",
        translation_key="discharge_capacity",
        value_path="msg_content.interval.charge.discharge_capacity",
        icon="mdi:battery-minus",
    ),
    XPengSensorEntityDescription(
        key="charge_limit",
        translation_key="charge_limit",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_path="msg_content.instant.charge.electric_current_limit",
        icon="mdi:battery-charging-high",
    ),
    XPengSensorEntityDescription(
        key="max_charge",
        translation_key="max_charge",
        native_unit_of_measurement=PERCENTAGE,
        value_path="msg_content.instant.charge.max_range_charge",
        icon="mdi:battery-charging-100",
    ),
    XPengSensorEntityDescription(
        key="charge_time_remaining",
        translation_key="charge_time_remaining",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_path="msg_content.instant.charge.time_to_complete_charge_min",
        icon="mdi:timer-sand",
    ),
    # There is deliberately no ``fast_charge_port`` sensor. Export cars (G6, and
    # the G9 as sold outside China) have one combined CCS2 port, and the car's
    # ``fast_charge_port_status`` is not a plug signal on them: measured on a G6
    # 2026-10-16, it read 2 through flap-open, flap-closed and both fast-port
    # commands. The open/close fast-port commands are refused by the car outright
    # (10404/10405), so both the sensor and the buttons were fiction.
    #
    # ``slow_charge_port_status`` turned out not to be trustworthy either: with no
    # cable attached on 2026-10-17 it read 2 for nine hours straight. The display
    # state therefore comes from ``charge_port_state``, which needs the gun to see
    # a cable before it will say "Connected".
    XPengSensorEntityDescription(
        key="slow_charge_port",
        translation_key="slow_charge_port",
        value_path="msg_content.instant.charge",
        icon="mdi:ev-plug-type2",
        device_class=SensorDeviceClass.ENUM,
        options=list(CHARGE_PORT_STATES.values()),
        value_fn=charge_port_state,
    ),
    XPengSensorEntityDescription(
        key="fast_charge_added_range",
        translation_key="fast_charge_added_range",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_path="msg_content.instant.charge.fast_charge_add_mileage",
        icon="mdi:lightning-bolt",
    ),
    XPengSensorEntityDescription(
        key="charge_schedule_start",
        translation_key="charge_schedule_start",
        value_path="msg_content.instant.charge.reserve_charge_start_time",
        icon="mdi:clock-start",
    ),
    XPengSensorEntityDescription(
        key="charge_schedule_end",
        translation_key="charge_schedule_end",
        value_path="msg_content.instant.charge.reserve_charge_end_time",
        icon="mdi:clock-end",
    ),
    # === Temperature & Climate ===
    XPengSensorEntityDescription(
        key="cabin_temperature",
        translation_key="cabin_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_path="msg_content.instant.hvac.hvac_inner_temp_float",
    ),
    XPengSensorEntityDescription(
        key="set_temperature",
        translation_key="set_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_path="msg_content.instant.hvac.hvac_temp_float",
        icon="mdi:thermostat",
    ),
    XPengSensorEntityDescription(
        key="hvac_fan_speed",
        translation_key="hvac_fan_speed",
        value_path="msg_content.instant.hvac.hvac_wind_speed",
        icon="mdi:fan",
    ),
    # === Location & Driving ===
    XPengSensorEntityDescription(
        key="latitude",
        translation_key="latitude",
        value_path="msg_content.interval.drive.latitude",
        icon="mdi:crosshairs-gps",
    ),
    XPengSensorEntityDescription(
        key="longitude",
        translation_key="longitude",
        value_path="msg_content.interval.drive.longitude",
        icon="mdi:crosshairs-gps",
    ),
    XPengSensorEntityDescription(
        key="speed",
        translation_key="speed",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        value_path="msg_content.interval.drive.speed",
        value_fn=lambda v: round(float(v), 1) if v is not None else None,
    ),
    XPengSensorEntityDescription(
        key="heading",
        translation_key="heading",
        value_path="msg_content.interval.drive.angle",
        icon="mdi:compass",
        value_fn=lambda v: round(float(v), 1) if v is not None else None,
    ),
    XPengSensorEntityDescription(
        key="shift_state",
        translation_key="shift_state",
        value_path="msg_content.instant.drive.shift_state",
        icon="mdi:car-shift-pattern",
        device_class=SensorDeviceClass.ENUM,
        options=list(SHIFT_STATES.values()),
        value_fn=lambda v: SHIFT_STATES.get(v, str(v)),
    ),
    XPengSensorEntityDescription(
        key="vehicle_mode",
        translation_key="vehicle_mode",
        value_path="msg_content.instant.ext.vehicleMode",
        icon="mdi:car",
        device_class=SensorDeviceClass.ENUM,
        options=list(VEHICLE_MODES.values()),
        value_fn=lambda v: VEHICLE_MODES.get(v, str(v)),
    ),
    XPengSensorEntityDescription(
        key="car_power_state",
        translation_key="car_power_state",
        value_path="msg_content.instant.power.car_power_state",
        icon="mdi:power",
    ),
    # === Tyre Pressure ===
    XPengSensorEntityDescription(
        key="tyre_pressure_fl",
        translation_key="tyre_pressure_fl",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_path="msg_content.instant.tpms.tpms_pressure_fl",
        icon="mdi:car-tire-alert",
    ),
    XPengSensorEntityDescription(
        key="tyre_pressure_fr",
        translation_key="tyre_pressure_fr",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_path="msg_content.instant.tpms.tpms_pressure_fr",
        icon="mdi:car-tire-alert",
    ),
    XPengSensorEntityDescription(
        key="tyre_pressure_rl",
        translation_key="tyre_pressure_rl",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_path="msg_content.instant.tpms.tpms_pressure_rl",
        icon="mdi:car-tire-alert",
    ),
    XPengSensorEntityDescription(
        key="tyre_pressure_rr",
        translation_key="tyre_pressure_rr",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_path="msg_content.instant.tpms.tpms_pressure_rr",
        icon="mdi:car-tire-alert",
    ),
    # === Seats & Comfort ===
    XPengSensorEntityDescription(
        key="driver_seat_heat",
        translation_key="driver_seat_heat",
        value_path="msg_content.instant.seat.driver_seat_heat",
        icon="mdi:car-seat-heater",
    ),
    XPengSensorEntityDescription(
        key="passenger_seat_heat",
        translation_key="passenger_seat_heat",
        value_path="msg_content.instant.seat.passenger_seat_heat",
        icon="mdi:car-seat-heater",
    ),

    # The car reports each seat function separately, and reports the rear seats too.
    # These names come from the payload itself (the same `seat` group the heating
    # fields are in, documented by the read-only xpeng-mac-bridge reference) rather
    # than from guesswork: `driver_seat_ventilation` is a different field from
    # `driver_seat_heat`, which is why a ventilation command never showed up here.
    XPengSensorEntityDescription(
        key="driver_seat_ventilation",
        translation_key="driver_seat_ventilation",
        value_path="msg_content.instant.seat.driver_seat_ventilation",
        icon="mdi:car-seat",
    ),
    XPengSensorEntityDescription(
        key="passenger_seat_ventilation",
        translation_key="passenger_seat_ventilation",
        value_path="msg_content.instant.seat.passenger_seat_ventilation",
        icon="mdi:car-seat",
    ),
    XPengSensorEntityDescription(
        key="left_rear_seat_heat",
        translation_key="left_rear_seat_heat",
        value_path="msg_content.instant.seat.left_rear_seat_heat",
        icon="mdi:car-seat-heater",
    ),
    XPengSensorEntityDescription(
        key="right_rear_seat_heat",
        translation_key="right_rear_seat_heat",
        value_path="msg_content.instant.seat.right_rear_seat_heat",
        icon="mdi:car-seat-heater",
    ),
    XPengSensorEntityDescription(
        key="left_rear_seat_ventilation",
        translation_key="left_rear_seat_ventilation",
        value_path="msg_content.instant.seat.left_rear_seat_ventilation",
        icon="mdi:car-seat",
    ),
    XPengSensorEntityDescription(
        key="right_rear_seat_ventilation",
        translation_key="right_rear_seat_ventilation",
        value_path="msg_content.instant.seat.right_rear_seat_ventilation",
        icon="mdi:car-seat",
    ),
    XPengSensorEntityDescription(
        key="steering_wheel_heat",
        translation_key="steering_wheel_heat",
        value_path="msg_content.instant.steerWheel.steering_wheel_heat",
        icon="mdi:steering",
    ),
    # === Trunk Power ===
    XPengSensorEntityDescription(
        key="trunk_power_timer",
        translation_key="trunk_power_timer",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_path="msg_content.instant.trunkPower.timeToPowerOffInMin",
        icon="mdi:timer-outline",
    ),
    # === Last Charge Statistics ===
    XPengSensorEntityDescription(
        key="last_charge_energy",
        translation_key="last_charge_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        value_path="msg_content.instant.chargeStatistics.usageElectricity",
        icon="mdi:lightning-bolt",
        value_fn=lambda v: round(float(v), 1) if v is not None else None,
    ),
    XPengSensorEntityDescription(
        key="last_charge_range",
        translation_key="last_charge_range",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_path="msg_content.instant.chargeStatistics.addedRange",
        icon="mdi:map-marker-distance",
    ),
    XPengSensorEntityDescription(
        key="last_charge_duration",
        translation_key="last_charge_duration",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_path="msg_content.instant.chargeStatistics.chargeTime",
        icon="mdi:timer-outline",
        value_fn=lambda v: round(float(v) / 60000) if v is not None else None,
    ),
    # === Sentinel ===
    XPengSensorEntityDescription(
        key="sentinel_sensitivity",
        translation_key="sentinel_sensitivity",
        value_path="msg_content.instant.ext.sentinel_mode_sensitivity",
        icon="mdi:cctv",
    ),
    XPengSensorEntityDescription(
        key="ota_status",
        translation_key="ota_status",
        value_path="msg_content.digital_vehicle.ota",
        icon="mdi:cellphone-arrow-down",
        value_fn=lambda v: _parse_ota_status(v),
    ),
    XPengSensorEntityDescription(
        key="ota_last_checked",
        translation_key="ota_last_checked",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_path="msg_content.digital_vehicle.ota",
        icon="mdi:clock-check-outline",
        value_fn=lambda v: _parse_ota_timestamp(v),
    ),
    # === HVAC Quick Modes ===
    XPengSensorEntityDescription(
        key="hvac_quick_warm",
        translation_key="hvac_quick_warm",
        value_path="msg_content.instant.hvac.hvac_quick_warm_mode",
        icon="mdi:radiator",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    XPengSensorEntityDescription(
        key="hvac_quick_cool",
        translation_key="hvac_quick_cool",
        value_path="msg_content.instant.hvac.hvac_quick_cool_mode",
        icon="mdi:snowflake",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # === Range & Misc ===
    XPengSensorEntityDescription(
        key="range_type",
        translation_key="range_type",
        value_path="msg_content.instant.odometer.range_type",
        icon="mdi:map-marker-distance",
        device_class=SensorDeviceClass.ENUM,
        options=list(RANGE_TYPES.values()),
        value_fn=lambda v: RANGE_TYPES.get(v, f"Unknown ({v})"),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


# ── Binary Sensor Descriptions ──────────────────────────────


@dataclass(frozen=True, kw_only=True)
class XPengBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describe an XPENG binary sensor."""

    value_path: str = ""
    value_fn: Any = None
    on_value: Any = None


# MQTT reports window position 0-100, where 100 is fully closed (empirically
# confirmed: a close-windows reply reported all four as 100 with the windows
# shut). Only a real 0-99 position means open, so anything unparseable is
# treated as closed rather than open - a missing or differently-scaled value
# must never manufacture an "open" reading. REST supplies these as 0/1 flags,
# and a `False` would otherwise coerce to 0 and read as fully open.
_WINDOW_POSITION_KEYS: Final = (
    "front_left_window_position",
    "front_right_window_position",
    "rear_left_window_position",
    "rear_right_window_position",
)


def any_window_open(v: Any) -> bool:
    """True only if a window position is a genuine 0-99 value."""
    if not isinstance(v, dict):
        return False
    for key in _WINDOW_POSITION_KEYS:
        raw = v.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            position = int(float(raw))
        except (TypeError, ValueError):
            continue
        if position < 100:
            return True
    return False


BINARY_SENSOR_DESCRIPTIONS: tuple[XPengBinarySensorEntityDescription, ...] = (
    # === Lock ===
    XPengBinarySensorEntityDescription(
        key="locked",
        translation_key="locked",
        device_class=BinarySensorDeviceClass.LOCK,
        value_path="msg_content.instant.door.driver_door_lock_st",
        # HA lock binary sensor: ON = unlocked, OFF = locked
        # driver_door_lock_st: 1 = locked, 0 = unlocked
        value_fn=lambda v: v != 1 if v is not None else None,
    ),
    # === Charging ===
    XPengBinarySensorEntityDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_path="msg_content",
        value_fn=lambda v: (
            # charging_state 2 = car-initiated charging
            # OR interval charge power > 0 = external charging (e.g. EVCC)
            v.get("instant", {}).get("charge", {}).get("charging_state") == 2
            or _safe_float(
                v.get("interval", {}).get("charge", {}).get("power", 0)
            ) > 0
        ) if isinstance(v, dict) else False,
    ),
    XPengBinarySensorEntityDescription(
        key="plugged_in",
        translation_key="plugged_in",
        device_class=BinarySensorDeviceClass.PLUG,
        value_path="msg_content.instant.charge",
        # The port-status fields cannot answer this on an export car: measured on
        # a G6 on 2026-10-17, with no cable attached, slow read 2 ("Connected")
        # for nine hours and fast reads 2 permanently. Reading the gun instead is
        # what stopped this sensor sitting ON with nothing plugged in.
        #
        # It is tri-state on purpose: while the car waits for an armed charge plan
        # it reports nothing at all about the cable (plugged and unplugged are the
        # same payload), so the honest answer there is "unknown" rather than "off".
        value_fn=charge_cable_state,
    ),
    # === Climate ===
    XPengBinarySensorEntityDescription(
        key="hvac_on",
        translation_key="hvac_on",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_path="msg_content.instant.hvac.hvac_on",
        on_value=1,
        icon="mdi:air-conditioner",
    ),
    XPengBinarySensorEntityDescription(
        key="hvac_auto",
        translation_key="hvac_auto",
        value_path="msg_content.instant.hvac.hvac_auto",
        on_value=1,
        icon="mdi:air-conditioner",
    ),
    XPengBinarySensorEntityDescription(
        key="defrost",
        translation_key="defrost",
        value_path="msg_content.instant.hvac.hvac_defrost",
        on_value=1,
        icon="mdi:car-defrost-front",
    ),
    XPengBinarySensorEntityDescription(
        key="mirror_heat",
        translation_key="mirror_heat",
        value_path="msg_content.instant.hvac.hvac_mirror_heat_mode",
        on_value=1,
        icon="mdi:car-side",
    ),
    # === Doors ===
    XPengBinarySensorEntityDescription(
        key="any_door_open",
        translation_key="any_door_open",
        device_class=BinarySensorDeviceClass.DOOR,
        value_path="msg_content.instant",
        value_fn=lambda v: (
            any(
                v.get("door", {}).get(k, 0) == 1
                for k in (
                    "driver_door_ajar_st",
                    "passenger_door_ajar_st",
                    "rear_left_door_ajar_st",
                    "rear_right_door_ajar_st",
                    "bonnet_ajar_st",
                )
            )
            or v.get("tail", {}).get("tail_status", 0) == 1
            if isinstance(v, dict) else False
        ),
    ),
    XPengBinarySensorEntityDescription(
        key="driver_door",
        translation_key="driver_door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_path="msg_content.instant.door.driver_door_ajar_st",
        on_value=1,
    ),
    XPengBinarySensorEntityDescription(
        key="passenger_door",
        translation_key="passenger_door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_path="msg_content.instant.door.passenger_door_ajar_st",
        on_value=1,
    ),
    XPengBinarySensorEntityDescription(
        key="rear_left_door",
        translation_key="rear_left_door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_path="msg_content.instant.door.rear_left_door_ajar_st",
        on_value=1,
    ),
    XPengBinarySensorEntityDescription(
        key="rear_right_door",
        translation_key="rear_right_door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_path="msg_content.instant.door.rear_right_door_ajar_st",
        on_value=1,
    ),
    XPengBinarySensorEntityDescription(
        key="trunk",
        translation_key="trunk",
        device_class=BinarySensorDeviceClass.DOOR,
        value_path="msg_content.instant.tail.tail_status",
        on_value=1,
        icon="mdi:car-back",
    ),
    XPengBinarySensorEntityDescription(
        key="bonnet",
        translation_key="bonnet",
        device_class=BinarySensorDeviceClass.DOOR,
        value_path="msg_content.instant.door.bonnet_ajar_st",
        on_value=1,
        icon="mdi:car",
    ),
    # === Windows ===
    XPengBinarySensorEntityDescription(
        key="any_window_open",
        translation_key="any_window_open",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_path="msg_content.instant.window",
        value_fn=any_window_open,
    ),
    # === Sentinel (same as X-Guard — soldier_camera_status) ===
    XPengBinarySensorEntityDescription(
        key="sentinel_mode",
        translation_key="sentinel_mode",
        value_path="msg_content.instant.ext.soldier_camera_status",
        on_value=1,
        icon="mdi:cctv",
    ),
    # === Trunk Power ===
    XPengBinarySensorEntityDescription(
        key="trunk_power",
        translation_key="trunk_power",
        value_path="msg_content.instant.trunkPower.powerSwitch",
        on_value=1,
        icon="mdi:power-plug",
    ),
    # === X-Guard (soldier_camera_status = actual on/off state) ===
    XPengBinarySensorEntityDescription(
        key="xguard",
        translation_key="xguard",
        value_path="msg_content.instant.ext.soldier_camera_status",
        on_value=1,
        icon="mdi:cctv",
    ),
    # === Temporary Lock ===
    XPengBinarySensorEntityDescription(
        key="temp_lock",
        translation_key="temp_lock",
        device_class=BinarySensorDeviceClass.LOCK,
        value_path="msg_content.instant.door.temp_lock_st",
        on_value=1,
        icon="mdi:lock-clock",
    ),
)

#: How often to poll the message centre for new notifications.
#: Not tied to the vehicle poll: notifications are a server-side list that
#: changes independently of the car, and one small request is cheap.
CONF_NOTIFICATION_POLL_INTERVAL: Final = "notification_poll_interval"
#: Message Centre notification polling. 300s is the shipped default and the
#: interval this was measured at; a poll cannot be faster than this, so lowering
#: it is the only lever (at the cost of six API requests per poll).
DEFAULT_NOTIFICATION_POLL_INTERVAL: Final = 300
MIN_NOTIFICATION_POLL_INTERVAL: Final = 60
#: Kept for the call site that predates the option.
NOTIFICATION_POLL_INTERVAL: Final = DEFAULT_NOTIFICATION_POLL_INTERVAL
