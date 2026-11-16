"""Lock platform for the XPENG integration."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .command_policy import GROUP_ACCESS, GROUP_SECURING, default_policy
from .const import COMMAND_NOT_SENT_MESSAGE, DOMAIN
from .coordinator import XPengDataCoordinator
from .door_guard import age_attributes
from .entity import XPengEntity
from .mqtt_client import XPengMQTTClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XPENG lock entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: XPengDataCoordinator = data["coordinator"]
    mqtt_client: XPengMQTTClient = data["mqtt_client"]

    policy = data.get("command_policy") or default_policy()
    lock = XPengLock(coordinator, mqtt_client)
    if lock._policy_visible(policy):
        async_add_entities([lock])


class XPengLock(XPengEntity, LockEntity):
    """Lock entity for the vehicle door lock."""

    # Locking only removes access; unlocking hands the car over. The entity
    # exists while either is exposed, so a "securing only" policy still shows
    # and reports the lock state.
    _policy_group = GROUP_SECURING
    _policy_groups = (GROUP_SECURING, GROUP_ACCESS)
    _attr_translation_key = "door_lock"
    _attr_assumed_state = True  # optimistic — don't bounce on stale state

    def __init__(
        self, coordinator: XPengDataCoordinator, mqtt_client: XPengMQTTClient
    ) -> None:
        super().__init__(coordinator)
        self._mqtt_client = mqtt_client
        self._attr_unique_id = f"xpeng_{self._vin}_door_lock"
        self._optimistic_state: bool | None = None  # True = locked
        self._optimistic_until: float = 0

    @property
    def is_locked(self) -> bool | None:
        if (
            self._optimistic_state is not None
            and time.monotonic() < self._optimistic_until
        ):
            return self._optimistic_state
        self._optimistic_state = None
        if not self.coordinator.data:
            return None
        door = (
            self.coordinator.data
            .get("msg_content", {})
            .get("instant", {})
            .get("door", {})
        )
        val = door.get("driver_door_lock_st")
        if val is None:
            return None
        return val == 1  # 1 = locked

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Age of the door snapshot behind this state (issue #32).

        ``last_data_update`` moves on every push, but the door block is the one
        a parked car stops sending first: after walk-away locking the car is
        already asleep, so this entity can read "unlocked" for hours while the
        rest of the state is minutes old and nothing says which is which. The
        attribute is the section's own clock, not the state's.
        """
        return age_attributes(
            getattr(self.coordinator, "door_data_timestamp", None)
        )

    async def _send_and_refresh(self, msg_content: dict) -> None:
        """Send command then wake car to refresh state.

        The lock command itself must be reported if it never went out: on a
        reconnecting client the publish is dropped (`Cannot send command: not
        connected`) while the entity still flips to its optimistic state, so the
        UI says "locked" for a car that was never asked (measured 2026-10-16).
        The follow-up wake is best effort - it only asks for a state refresh -
        so a failure there is logged, not raised.
        """
        sent = await self.hass.async_add_executor_job(
            self._mqtt_client.send_command, self._vin, 9, msg_content,
        )
        if not sent:
            raise HomeAssistantError(COMMAND_NOT_SENT_MESSAGE)
        # Wait for car to process, then force state refresh
        await asyncio.sleep(5)
        refreshed = await self.hass.async_add_executor_job(
            self._mqtt_client.send_command, self._vin, 19, {"cmd_type": 2}, 2,
        )
        if not refreshed:
            _LOGGER.debug(
                "Lock sent, but the follow-up state refresh was not: the MQTT "
                "client is not connected",
            )

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the vehicle."""
        self._guard(group=GROUP_SECURING)
        self._optimistic_state = True
        self._optimistic_until = time.monotonic() + 30
        self.async_write_ha_state()
        await self._send_and_refresh({"cmd_type": 1})

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the vehicle."""
        self._guard(group=GROUP_ACCESS)
        self._optimistic_state = False
        self._optimistic_until = time.monotonic() + 30
        self.async_write_ha_state()
        await self._send_and_refresh({"cmd_type": 0})
