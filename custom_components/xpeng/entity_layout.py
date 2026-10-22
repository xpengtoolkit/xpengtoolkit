"""Compact entity layout and an explicit, reversible legacy migration."""

from __future__ import annotations

from .command_policy import (
    GROUP_ACCESS,
    GROUP_CHARGING,
    GROUP_COMFORT,
    GROUP_POWER,
    GROUP_SECURING,
)

CONF_LEGACY_ENTITIES = "legacy_entities"

# Stable data interfaces used by evcc's Home Assistant vehicle template.
# A climate entity reports an HVAC mode, while evcc's climater expects a
# binary_sensor with on/off states. Compact UI layout must not remove that API.
EVCC_ENTITIES = frozenset(
    {
        ("sensor", "battery_soc"),
        ("sensor", "range"),
        ("number", "charge_limit_control"),
        ("binary_sensor", "hvac_on"),
        ("button", "wake_vehicle"),
        ("sensor", "charging_state"),
        ("sensor", "charge_power"),
        ("sensor", "odometer"),
        ("sensor", "last_data_update"),
        ("binary_sensor", "plugged_in"),
    }
)

# platform/key -> (replacement platform, unique-id suffix, capability groups)
# Only identical state is folded into a controller. Detailed charging state,
# physical cable presence, individual doors and cabin measurements stay separate.
REPLACEMENTS = {
    ("sensor", "max_charge"): ("number", "charge_limit_control", (GROUP_CHARGING,)),
    ("sensor", "slow_charge_port"): ("cover", "charge_port_cover", (GROUP_CHARGING,)),
    ("sensor", "set_temperature"): ("climate", "cabin_climate", (GROUP_COMFORT,)),
    ("sensor", "hvac_fan_speed"): ("climate", "cabin_climate", (GROUP_COMFORT,)),
    ("sensor", "trunk_power_timer"): (
        "number",
        "trunk_power_timer_control",
        (GROUP_POWER,),
    ),
    ("sensor", "sentinel_sensitivity"): (
        "number",
        "sentinel_sensitivity_control",
        (GROUP_SECURING,),
    ),
    ("binary_sensor", "locked"): ("lock", "door_lock", (GROUP_ACCESS, GROUP_SECURING)),
    ("binary_sensor", "charging"): ("switch", "charging_switch", (GROUP_CHARGING,)),
    ("binary_sensor", "hvac_on"): ("climate", "cabin_climate", (GROUP_COMFORT,)),
    ("binary_sensor", "defrost"): ("climate", "cabin_climate", (GROUP_COMFORT,)),
    ("binary_sensor", "trunk"): (
        "cover",
        "trunk_cover",
        (GROUP_ACCESS, GROUP_SECURING),
    ),
    ("binary_sensor", "any_window_open"): (
        "cover",
        "windows_cover",
        (GROUP_ACCESS, GROUP_SECURING),
    ),
    ("binary_sensor", "sentinel_mode"): (
        "switch",
        "sentinel_switch",
        (GROUP_ACCESS, GROUP_SECURING),
    ),
    ("binary_sensor", "xguard"): (
        "switch",
        "sentinel_switch",
        (GROUP_ACCESS, GROUP_SECURING),
    ),
    ("binary_sensor", "trunk_power"): ("switch", "trunk_power_switch", (GROUP_POWER,)),
}
REPLACEMENTS[("number", "fan_speed_control")] = (
    "climate",
    "cabin_climate",
    (GROUP_COMFORT,),
)
REPLACEMENTS[("switch", "defrost_switch")] = (
    "climate",
    "cabin_climate",
    (GROUP_COMFORT,),
)

for _function in (
    "driver_seat_heat",
    "passenger_seat_heat",
    "driver_seat_ventilation",
    "passenger_seat_ventilation",
    "left_rear_seat_heat",
    "right_rear_seat_heat",
    "left_rear_seat_ventilation",
    "right_rear_seat_ventilation",
    "steering_wheel_heat",
):
    REPLACEMENTS[("sensor", _function)] = (
        "select",
        f"{_function}_select",
        (GROUP_COMFORT,),
    )

for _button, _target in {
    "open_trunk": ("cover", "trunk_cover", (GROUP_ACCESS, GROUP_SECURING)),
    "close_trunk": ("cover", "trunk_cover", (GROUP_ACCESS, GROUP_SECURING)),
    "open_windows": ("cover", "windows_cover", (GROUP_ACCESS, GROUP_SECURING)),
    "close_windows": ("cover", "windows_cover", (GROUP_ACCESS, GROUP_SECURING)),
    "vent_windows": ("cover", "windows_cover", (GROUP_ACCESS, GROUP_SECURING)),
    "open_charge_port": ("cover", "charge_port_cover", (GROUP_CHARGING,)),
    "close_charge_port": ("cover", "charge_port_cover", (GROUP_CHARGING,)),
    "start_charge": ("switch", "charging_switch", (GROUP_CHARGING,)),
    "stop_charge": ("switch", "charging_switch", (GROUP_CHARGING,)),
}.items():
    REPLACEMENTS[("button", _button)] = _target


def legacy_entities_enabled(entry) -> bool:
    """Keep existing entities unless an owner deliberately selects compact mode."""
    options = getattr(entry, "options", {}) or {}
    return bool(options.get(CONF_LEGACY_ENTITIES, getattr(entry, "version", 1) < 2))


def include_legacy_entity(entry, policy, platform: str, key: str) -> bool:
    """Retain raw state when its replacement is disabled by command policy."""
    if (platform, key) in EVCC_ENTITIES:
        return True
    replacement = REPLACEMENTS.get((platform, key))
    return (
        legacy_entities_enabled(entry)
        or replacement is None
        or not any(policy.exposes(group) for group in replacement[2])
    )


def prepare_compact_registry(hass, entry, vin: str, policy) -> None:
    """Preserve primary entity naming and remove only explicitly superseded IDs.

    Home Assistant retains deleted registry metadata, so selecting legacy mode
    again can restore those entries. History is not purged. Existing installations
    migrate with legacy mode enabled; this cleanup is an explicit options change.
    """
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    # Seed each new controller from its former primary state entity, preserving
    # a renamed object ID (e.g. xpeng_p7_trunk), area, labels and user overrides.
    for old_platform, old_key in (
        ("binary_sensor", "trunk"),
        ("binary_sensor", "any_window_open"),
        ("sensor", "slow_charge_port"),
        ("binary_sensor", "charging"),
    ):
        domain, suffix, groups = REPLACEMENTS[(old_platform, old_key)]
        if not any(policy.exposes(group) for group in groups):
            continue
        old_id = registry.async_get_entity_id(
            old_platform, "xpeng", f"xpeng_{vin}_{old_key}"
        )
        if old_id is None or registry.async_get_entity_id(
            domain, "xpeng", f"xpeng_{vin}_{suffix}"
        ):
            continue
        old = registry.async_get(old_id)
        if old is None or old.config_entry_id != entry.entry_id:
            continue
        new = registry.async_get_or_create(
            domain,
            "xpeng",
            f"xpeng_{vin}_{suffix}",
            config_entry=entry,
            device_id=old.device_id,
            suggested_object_id=old_id.split(".", 1)[1],
        )
        registry.async_update_entity(
            new.entity_id,
            name=old.name,
            icon=old.icon,
            area_id=old.area_id,
            labels=old.labels,
            aliases=old.aliases,
        )
    # Seed the new names even while legacy entities are still enabled. This
    # avoids creating VIN-based controller IDs during the compatibility restart
    # and then losing the old prefix when the owner selects compact mode.
    if legacy_entities_enabled(entry):
        return
    for platform, key in REPLACEMENTS:
        if include_legacy_entity(entry, policy, platform, key):
            continue
        old_id = registry.async_get_entity_id(platform, "xpeng", f"xpeng_{vin}_{key}")
        if old_id is not None:
            old = registry.async_get(old_id)
            if old is not None and old.config_entry_id == entry.entry_id:
                registry.async_remove(old_id)
