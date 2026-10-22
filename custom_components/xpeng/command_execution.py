"""Wait for the vehicle response instead of treating MQTT publication as success."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN


class VehicleCommandRejected(HomeAssistantError):
    """A vehicle acknowledgement explicitly rejected the requested command."""

    def __init__(self, code: int, service_type: int, content: dict) -> None:
        self.code = code
        super().__init__(
            translation_domain=DOMAIN,
            translation_key=(
                "charging_start_rejected"
                if service_type == 4
                and content.get("cmd_type") == 1
                and content.get("cmd_value") == 1
                and code == 10401
                else "vehicle_command_rejected"
            ),
            translation_placeholders={"code": str(code)},
        )


async def async_send_confirmed(
    hass, client, vin: str, service_type: int, content: dict, msg_type: int = 2
) -> dict[str, Any]:
    """Await acceptance; measured telemetry remains the source of entity state.

    Some vehicle firmware rewrites message IDs, so send_command_wait also matches
    by service type. Acceptance never fabricates a physical state change.
    """
    # Firmware may rewrite message IDs. Keep one request per subsystem in
    # flight so the service-type fallback cannot satisfy a different request.
    locks = vars(client).setdefault("_confirmed_command_locks", {})
    lock = locks.setdefault(service_type, asyncio.Lock())
    async with lock:
        response = await hass.async_add_executor_job(
            client.send_command_wait, vin, service_type, content, msg_type
        )
        if response is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_not_confirmed"
            )
        body = response.get("msg_content") if isinstance(response, dict) else None
        code = body.get("code") if isinstance(body, dict) else None
        if (
            not isinstance(response, dict)
            or response.get("service_type") != service_type
            or response.get("msg_type") != 1
            or type(code) is not int
        ):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_response_invalid"
            )
        if code != 200:
            raise VehicleCommandRejected(code, service_type, content)
        return response
