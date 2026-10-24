"""English entity names, the only names entity ids may be derived from.

Home Assistant derives an entity's object id from the entity name in the active
UI language - for the Latin-script languages, which includes every language this
integration translates. ``XPengEntity.suggested_object_id`` returns the English
name instead, so ``sensor.xpeng_<vin>_battery`` is the id in every language while
only the friendly name is translated.

The names live in this literal rather than being read from
``translations/en.json`` at runtime: the lookup happens while entities are added,
on the event loop, and a blocking file read there is exactly what Home Assistant's
loop protection reports. ``test_english_names_match_the_translation_file`` keeps
the literal in step with ``en.json``; add or rename an entity name there and in
``strings.json``, then update this dictionary (the test prints the difference).

The loader lives here rather than in ``entity.py`` so it can be exercised by a
plain-interpreter test: ``entity.py`` imports Home Assistant, this module does
not.
"""
from __future__ import annotations

ENGLISH_ENTITY_NAMES: dict[str, dict[str, str]] = {
    "sensor": {
        "battery_soc": "Battery",
        "range": "Range",
        "odometer": "Odometer",
        "charging_state": "Charging state",
        "charge_power": "Charge power",
        "charge_voltage": "Charge voltage",
        "charge_current": "Charge current",
        "charge_rate": "Charge rate",
        "discharge_capacity": "Discharge capacity",
        "charge_limit": "Charge current limit",
        "max_charge": "Max charge",
        "charge_time_remaining": "Charge time remaining",
        "slow_charge_port": "Charge port",
        "fast_charge_added_range": "Fast charge added range",
        "charge_schedule_start": "Charge schedule start",
        "charge_schedule_end": "Charge schedule end",
        "cabin_temperature": "Cabin temperature",
        "set_temperature": "Set temperature",
        "hvac_fan_speed": "HVAC fan speed",
        "latitude": "Latitude",
        "longitude": "Longitude",
        "speed": "Speed",
        "heading": "Heading",
        "shift_state": "Shift state",
        "vehicle_mode": "Vehicle mode",
        "car_power_state": "Power state",
        "tyre_pressure_fl": "Tyre pressure front left",
        "tyre_pressure_fr": "Tyre pressure front right",
        "tyre_pressure_rl": "Tyre pressure rear left",
        "tyre_pressure_rr": "Tyre pressure rear right",
        "driver_seat_heat": "Driver seat heating",
        "passenger_seat_heat": "Passenger seat heating",
        "steering_wheel_heat": "Steering wheel heating",
        "trunk_power_timer": "Trunk power timer",
        "last_charge_energy": "Last charge energy",
        "last_charge_range": "Last charge range added",
        "last_charge_duration": "Last charge duration",
        "sentinel_sensitivity": "Sentinel sensitivity",
        "ota_status": "OTA status",
        "ota_last_checked": "OTA last checked",
        "hvac_quick_warm": "HVAC quick warm",
        "hvac_quick_cool": "HVAC quick cool",
        "range_type": "Range type",
        "credential_age": "Credential age",
        "last_renewal": "Last renewal",
        "last_update": "Last data update",
        "last_data_update": "Last data timestamp",
        "xos_version": "XOS version",
        "mqtt_listener": "MQTT listener",
        "mqtt_discovery": "MQTT discovery",
        "capture": "Raw capture",
        "command_feedback": "Command feedback",
        "last_notification": "Last notification",
        "maintenance": "Next maintenance",
        "xcombos": "X-Combos",
        "parked_consumption": "Parked consumption",
        "ota_upgrade": "OTA update",
        "beta_program": "Beta program",
        "remote_capabilities": "Remote capabilities",
        "vehicle_extension": "Vehicle extension",
        "left_comfort_preon_seconds": "Left-comfort preon countdown",
        "hvac_schedules": "HVAC schedules",
        "cdu_id": "CDU ID",
        "cdu_partition": "CDU partition",
        "vehicle_identity": "Vehicle identity",
        "iccid": "TBox ICCID",
        "weather": "Weather at vehicle",
        "polling_status": "Polling status",
        "next_poll": "Next poll",
        "account": "Account",
        "driver_seat_ventilation": "Driver seat ventilation",
        "passenger_seat_ventilation": "Passenger seat ventilation",
        "left_rear_seat_heat": "Left rear seat heating",
        "right_rear_seat_heat": "Right rear seat heating",
        "left_rear_seat_ventilation": "Left rear seat ventilation",
        "right_rear_seat_ventilation": "Right rear seat ventilation",
    },
    "calendar": {
        "hvac_schedule_calendar": "HVAC schedule",
    },
    "binary_sensor": {
        "locked": "Lock",
        "charging": "Charging",
        "plugged_in": "Plugged in",
        "hvac_on": "Climate",
        "hvac_auto": "Climate auto",
        "defrost": "Defrost",
        "mirror_heat": "Mirror heating",
        "any_door_open": "Doors",
        "driver_door": "Driver door",
        "passenger_door": "Passenger door",
        "rear_left_door": "Rear left door",
        "rear_right_door": "Rear right door",
        "trunk": "Trunk",
        "bonnet": "Bonnet",
        "any_window_open": "Windows",
        "sentinel_mode": "Sentinel mode",
        "trunk_power": "Trunk power",
        "xguard": "X-Guard camera",
        "temp_lock": "Temporary lock",
        "credentials_valid": "Credentials valid",
        "mqtt_connected": "MQTT connected",
    },
    "device_tracker": {
        "location": "Location",
    },
    "button": {
        "flash_lights": "Flash lights",
        "honk_horn": "Honk horn",
        "close_windows": "Close windows",
        "vent_windows": "Vent windows",
        "force_update": "Force update",
        "wake_vehicle": "Wake vehicle",
        "query_xguard": "Query X-Guard",
        "open_trunk": "Open trunk",
        "close_trunk": "Close trunk",
        "start_charge": "Start charging",
        "stop_charge": "Stop charging",
        "open_charge_port": "Open charge port",
        "close_charge_port": "Close charge port",
        "open_windows": "Open windows",
    },
    "switch": {
        "sentinel_switch": "X-Guard",
        "trunk_power_switch": "Trunk power",
        "defrost_switch": "Front defrost",
        "parking_lights_switch": "Parking lights",
        "auto_upgrade_switch": "OTA auto-upgrade",
        "charging": "Charging",
        "climate_max_heating": "Maximum heating",
        "climate_max_cooling": "Maximum cooling",
        "climate_refresh": "Refresh",
        "climate_sterilisation": "Sterilisation",
        "climate_ac_cleaning": "A/C cleaning",
        "climate_rear_defrost": "Rear window defroster",
        "climate_pet_mode": "Pet mode",
        "climate_silent_mode": "Silent mode",
    },
    "number": {
        "sentinel_sensitivity_control": "Sentinel sensitivity",
        "trunk_power_timer_control": "Trunk power timer",
        "charge_limit_control": "Charge limit",
        "fan_speed_control": "Fan speed",
    },
    "climate": {
        "cabin_climate": "Climate",
    },
    "lock": {
        "door_lock": "Door lock",
    },
    "select": {
        "driver_seat_heat_select": "Driver seat heating",
        "passenger_seat_heat_select": "Passenger seat heating",
        "steering_wheel_heat_select": "Steering wheel heating",
        "driver_seat_ventilation_select": "Driver seat ventilation",
        "passenger_seat_ventilation_select": "Passenger seat ventilation",
        "left_rear_seat_heat_select": "Left rear seat heating",
        "right_rear_seat_heat_select": "Right rear seat heating",
        "left_rear_seat_ventilation_select": "Left rear seat ventilation",
        "right_rear_seat_ventilation_select": "Right rear seat ventilation",
    },
    "image": {
        "vehicle_picture": "Vehicle picture",
    },
    "cover": {
        "trunk": "Trunk",
        "windows": "Windows",
        "charge_port": "Charge port",
    },
}


def english_name(platform: str | None, translation_key: str | None) -> str | None:
    """The English name for ``platform``/``translation_key``, if it has one."""
    if not platform or not translation_key:
        return None
    return ENGLISH_ENTITY_NAMES.get(platform, {}).get(translation_key)
