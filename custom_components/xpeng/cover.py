"""Stateful trunk, window and charge-port controls."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .charge_port import charge_port_state, controls_enabled
from .command_execution import async_send_confirmed
from .command_policy import GROUP_ACCESS, GROUP_CHARGING, GROUP_SECURING, default_policy
from .const import DOMAIN
from .entity import XPengEntity
from .vehicle_capabilities import from_coordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    policy = data.get("command_policy") or default_policy()
    entities = [
        XPengCover(
            data["coordinator"],
            data["mqtt_client"],
            key,
            charge_port_controls=controls_enabled(entry.options),
        )
        for key in ("trunk", "windows", "charge_port")
    ]
    async_add_entities(
        [entity for entity in entities if entity._policy_visible(policy)]
    )


class XPengCover(XPengEntity, CoverEntity):
    """One control and one measured state, with per-direction permissions."""

    _policy_group = GROUP_SECURING
    _attr_assumed_state = False
    _attr_is_opening = None
    _attr_is_closing = None

    def __init__(
        self, coordinator, mqtt_client, key: str, *, charge_port_controls: bool = True
    ) -> None:
        super().__init__(coordinator)
        self._mqtt_client = mqtt_client
        self._key = key
        self._controls_enabled = key != "charge_port" or charge_port_controls
        self._attr_unique_id = f"xpeng_{self._vin}_{key}_cover"
        self._attr_translation_key = key
        self._attr_name = {
            "trunk": "Trunk",
            "windows": "Windows",
            "charge_port": "Charge port",
        }[key]
        self._attr_device_class = (
            CoverDeviceClass.WINDOW if key == "windows" else CoverDeviceClass.DOOR
        )
        self._attr_icon = {
            "trunk": "mdi:car-back",
            "windows": "mdi:car-door",
            "charge_port": "mdi:ev-plug-type2",
        }[key]
        self._open_group = GROUP_CHARGING if key == "charge_port" else GROUP_ACCESS
        self._close_group = GROUP_CHARGING if key == "charge_port" else GROUP_SECURING
        self._policy_groups = (self._open_group, self._close_group)
        # Window commands match XPENG Android 3.12.0 MqttSendHandler.
        # Its openWindow action is ventilation; full opening is not advertised.
        self._open_command = {
            "trunk": (9, {"cmd_type": 2, "cmd_value": 1}),
            "windows": (12, {"cmd_type": 1, "cmd_value": 2}),
            "charge_port": (4, {"cmd_type": 3, "cmd_value": 1}),
        }[key]
        self._close_command = {
            "trunk": (9, {"cmd_type": 2, "cmd_value": 3}),
            "windows": (12, {"cmd_type": 1, "cmd_value": 1}),
            "charge_port": (4, {"cmd_type": 3, "cmd_value": 0}),
        }[key]

    def _instant(self) -> dict:
        data = self.coordinator.data or {}
        return data.get("msg_content", {}).get("instant", {})

    @property
    def _can_control(self) -> bool:
        return self._controls_enabled and (
            self._key != "charge_port"
            or from_coordinator(self.coordinator).support("charge_port_control")
            is not False
        )

    @property
    def supported_features(self) -> CoverEntityFeature:
        features = CoverEntityFeature(0)
        if not self._can_control:
            return features
        if self._policy.allows(self._open_group):
            features |= CoverEntityFeature.OPEN
        if self._close_command is not None and self._policy.allows(self._close_group):
            features |= CoverEntityFeature.CLOSE
        return features

    @property
    def is_closed(self) -> bool | None:
        instant = self._instant()
        if self._key == "trunk":
            value = instant.get("tail", {}).get("tail_status")
            return {0: True, 1: False}.get(value)
        if self._key == "charge_port":
            return {"Closed": True, "Open": False, "Connected": False}.get(
                charge_port_state(instant.get("charge"))
            )
        positions = self._window_positions()
        if any(position < 100 for position in positions.values()):
            return False
        return True if len(positions) == 4 else None

    def _window_positions(self) -> dict[str, float]:
        positions = {}
        window = self._instant().get("window", {})
        for side in ("front_left", "front_right", "rear_left", "rear_right"):
            raw = window.get(f"{side}_window_position")
            if raw is None or isinstance(raw, bool):
                continue
            try:
                value = float(raw)
            except (ValueError, TypeError):
                continue
            if 0 <= value <= 100:
                positions[side] = value
        return positions

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self._key == "charge_port":
            return {"port_state": charge_port_state(self._instant().get("charge"))}
        if self._key == "windows":
            return {
                "window_positions_closed_percent": self._window_positions(),
                "open_behavior": "ventilation",
                "close_supported": True,
            }
        return {}

    async def _async_send(self, command) -> None:
        if not self._can_control:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="charge_port_control_disabled",
            )
        service_type, content = command
        await async_send_confirmed(
            self.hass, self._mqtt_client, self._vin, service_type, content
        )

    async def async_open_cover(self, **kwargs: Any) -> None:
        self._guard(group=self._open_group)
        await self._async_send(self._open_command)

    async def async_close_cover(self, **kwargs: Any) -> None:
        self._guard(group=self._close_group)
        if self._close_command is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="window_command_unverified"
            )
        await self._async_send(self._close_command)
