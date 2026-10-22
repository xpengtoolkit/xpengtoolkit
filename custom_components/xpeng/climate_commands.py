"""Climate protocol selected from the same capabilities as the official app.

Service 3: 1=power, 2=integer temperature, 3=automatic power, 4=front
defrost, 6=quick cooling, 16=fractional temperature. Command 2 is not fan speed.
"""

from __future__ import annotations

import math
from typing import Any

from .command_execution import async_send_confirmed


def supports(coordinator, capability: str) -> bool:
    data = getattr(coordinator, "remote_capabilities", None)
    values = data.get("capabilities") if isinstance(data, dict) else data
    return isinstance(values, list) and capability in values


def temperature_step(coordinator) -> float:
    return 0.5 if supports(coordinator, "HVAC_TEMP_SUPPORT_FLOAT") else 1.0


def _with_duration(coordinator, content: dict, duration: int | None) -> dict:
    if duration is not None and supports(coordinator, "HVAC_SUPPORT_TIME_SET"):
        content["time"] = duration
    return content


def temperature_payload(coordinator, temperature: Any, duration: int = 15) -> dict:
    if isinstance(temperature, bool):
        raise ValueError("Invalid target temperature")
    target = float(temperature)
    step = temperature_step(coordinator)
    if (
        not math.isfinite(target)
        or not 16 <= target <= 32
        or not math.isclose(target / step, round(target / step))
    ):
        raise ValueError("Invalid target temperature")
    fractional = supports(coordinator, "HVAC_TEMP_SUPPORT_FLOAT")
    return _with_duration(
        coordinator,
        {
            "cmd_type": 16 if fractional else 2,
            "cmd_value": target if fractional else int(target),
        },
        duration,
    )


def power_payload(coordinator, on: bool, duration: int = 15) -> dict:
    # The app's explicit DC/DG model profiles choose the automatic power action.
    # A current cloud boolean overrides that model fact; missing flags do not
    # enable the optional automatic action on other/unknown models.
    local = getattr(coordinator, "vehicle_local_config", None) or {}
    auto = local.get("isSupportAcAutoMode")
    if type(auto) is not bool:
        auto = getattr(coordinator, "vehicle_type_code", None) in {"DC", "DG"}
    if on and auto:
        return {"cmd_type": 3, "cmd_value": 1}
    return _with_duration(
        coordinator, {"cmd_type": 1, "cmd_value": int(on)}, duration if on else 0
    )


async def send_climate(hass, client, coordinator, content: dict) -> None:
    """Surface refusal and timeout; state comes only from measured feedback."""
    await async_send_confirmed(hass, client, coordinator.vin, 3, content)
