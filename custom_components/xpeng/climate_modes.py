"""Derived official-app service-3 modes; independent measured on/off controls."""

from __future__ import annotations

from dataclasses import dataclass

from .climate_commands import _with_duration


@dataclass(frozen=True)
class ClimateMode:
    key: str
    name: str
    icon: str
    command: int
    state_key: str
    timed: bool = False
    service: int = 3
    section: str = "hvac"
    on_value: int = 1
    off_value: int = 0
    state_on: int = 1
    state_off: int = 0


CLIMATE_MODES = (
    ClimateMode(
        "max_heating",
        "Maximum heating",
        "mdi:weather-sunny",
        8,
        "hvac_quick_warm_mode",
        True,
    ),
    ClimateMode(
        "max_cooling",
        "Maximum cooling",
        "mdi:snowflake",
        6,
        "hvac_quick_cool_mode",
        True,
    ),
    ClimateMode("refresh", "Refresh", "mdi:fan", 11, "hvac_deodorize_mode", True),
    ClimateMode(
        "sterilisation", "Sterilisation", "mdi:shield-sun", 9, "hvac_high_temp_mode"
    ),
    ClimateMode("ac_cleaning", "A/C cleaning", "mdi:air-filter", 10, "hvac_clean_mode"),
    ClimateMode("silent_mode", "Silent mode", "mdi:weather-night", 17, "hvac_silent_mode"),
    ClimateMode(
        "rear_defrost",
        "Rear window defroster",
        "mdi:car-defrost-rear",
        14,
        "hvac_mirror_heat_mode",
    ),
    ClimateMode(
        "pet_mode",
        "Pet mode",
        "mdi:dog",
        1,
        "protect_mode_status",
        service=33,
        section="protectMode",
        on_value=2,
        off_value=1,
        state_on=2,
        state_off=0,
    ),
)


def mode_payload(coordinator, mode: ClimateMode, on: bool) -> dict:
    """Only quick heat/cool and deodorization accept capability-selected time."""
    content = {
        "cmd_type": mode.command,
        "cmd_value": mode.on_value if on else mode.off_value,
    }
    return _with_duration(coordinator, content, 15 if mode.timed else None)


def mode_state(coordinator, mode: ClimateMode) -> bool | None:
    value = (
        (coordinator.data or {})
        .get("msg_content", {})
        .get("instant", {})
        .get(mode.section, {})
        .get(mode.state_key)
    )
    return (
        {mode.state_off: False, mode.state_on: True}.get(value)
        if type(value) is int
        else None
    )
