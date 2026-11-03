"""Number platform for the XPENG integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .command_policy import (
    GROUP_CHARGING,
    GROUP_COMFORT,
    GROUP_POWER,
    GROUP_SECURING,
    default_policy,
)
from .command_execution import async_send_confirmed
from .const import DOMAIN, UNIT_AMPERE
from .coordinator import XPengDataCoordinator
from .entity import XPengEntity
from .entity_layout import include_legacy_entity
from .mqtt_client import XPengMQTTClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XPENG number entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: XPengDataCoordinator = data["coordinator"]
    mqtt_client: XPengMQTTClient = data["mqtt_client"]

    policy = data.get("command_policy") or default_policy()

    entities = [
        XPengSentinelSensitivity(coordinator, mqtt_client),
        XPengTrunkPowerTimer(coordinator, mqtt_client),
        XPengChargeLimit(coordinator, mqtt_client),
        XPengChargeCurrentLimit(coordinator, mqtt_client),
        XPengFanSpeed(coordinator, mqtt_client),
    ]
    entities = [
        entity for entity in entities
        if not isinstance(entity, XPengFanSpeed)
        or include_legacy_entity(entry, policy, "number", "fan_speed_control")
    ]
    # A number's value is state worth showing, so READ_ONLY keeps it and only
    # DISABLED removes it; ``_guard`` refuses the set.
    async_add_entities(
        [entity for entity in entities if entity._policy_visible(policy)]
    )



class XPengSentinelSensitivity(XPengEntity, NumberEntity):
    """Number to set sentinel mode sensitivity level."""

    _policy_group = GROUP_SECURING
    _attr_translation_key = "sentinel_sensitivity_control"
    _attr_icon = "mdi:cctv"
    _attr_native_min_value = 1
    _attr_native_max_value = 3
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, coordinator: XPengDataCoordinator, mqtt_client: XPengMQTTClient
    ) -> None:
        super().__init__(coordinator)
        self._mqtt_client = mqtt_client
        self._attr_unique_id = f"xpeng_{self._vin}_sentinel_sensitivity_control"

    @property
    def native_value(self) -> int | None:
        if not self.coordinator.data:
            return None
        ext = (
            self.coordinator.data
            .get("msg_content", {})
            .get("instant", {})
            .get("ext", {})
        )
        return ext.get("sentinel_mode_sensitivity") if ext else None

    async def async_set_native_value(self, value: float) -> None:
        self._guard()
        level = int(value)
        video = 1 if level > 0 else 0
        await self.hass.async_add_executor_job(
            self._mqtt_client.send_command,
            self._vin, 8,
            {
                "action": 2,
                "settings": [
                    {"code": "SENTINEL_MODE_VIDEO", "value": video},
                    {"code": "SENTINEL_MODE_SENSITIVITY", "value": level},
                ],
            },
        )


class XPengTrunkPowerTimer(XPengEntity, NumberEntity):
    """Number to set trunk power auto-off timer."""

    _policy_group = GROUP_POWER
    _attr_translation_key = "trunk_power_timer_control"
    _attr_icon = "mdi:timer-outline"
    _attr_native_min_value = 0
    _attr_native_max_value = 1440
    _attr_native_step = 30
    _attr_native_unit_of_measurement = "min"
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: XPengDataCoordinator, mqtt_client: XPengMQTTClient
    ) -> None:
        super().__init__(coordinator)
        self._mqtt_client = mqtt_client
        self._attr_unique_id = f"xpeng_{self._vin}_trunk_power_timer_control"

    @property
    def native_value(self) -> int | None:
        if not self.coordinator.data:
            return None
        tp = (
            self.coordinator.data
            .get("msg_content", {})
            .get("instant", {})
            .get("trunkPower", {})
        )
        return tp.get("timeToPowerOffInMin") if tp else None

    async def async_set_native_value(self, value: float) -> None:
        self._guard()
        minutes = int(value)
        if minutes == 0:
            await self.hass.async_add_executor_job(
                self._mqtt_client.send_command,
                self._vin, 13,
                {"action": 3, "timeToPowerOffInMin": 0},
            )
        else:
            await self.hass.async_add_executor_job(
                self._mqtt_client.send_command,
                self._vin, 13,
                {"action": 2, "timeToPowerOffInMin": minutes},
            )


class XPengChargeLimit(XPengEntity, NumberEntity):
    """Number to set the charge limit (SOC %)."""

    _policy_group = GROUP_CHARGING
    _attr_translation_key = "charge_limit_control"
    _attr_icon = "mdi:battery-charging-100"
    _attr_native_min_value = 50
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, coordinator: XPengDataCoordinator, mqtt_client: XPengMQTTClient
    ) -> None:
        super().__init__(coordinator)
        self._mqtt_client = mqtt_client
        self._attr_unique_id = f"xpeng_{self._vin}_charge_limit_control"

    @property
    def native_value(self) -> int | None:
        if not self.coordinator.data:
            return None
        charge = (
            self.coordinator.data
            .get("msg_content", {})
            .get("instant", {})
            .get("charge", {})
        )
        return charge.get("max_range_charge") if charge else None

    async def async_set_native_value(self, value: float) -> None:
        """Wait for this charge-limit request; measured feedback supplies the value."""
        self._guard()
        await async_send_confirmed(
            self.hass, self._mqtt_client, self._vin, 4,
            {"cmd_type": 2, "cmd_value": int(value)},
        )


class XPengChargeCurrentLimit(XPengEntity, NumberEntity):
    """Number to set the AC charge current limit (amperes).

    A different control from :class:`XPengChargeLimit`, however much the two
    names invite confusion: that one writes ``cmd_type 2`` and carries a
    percentage, this one writes ``cmd_type 7`` and carries amperes. The app's
    own handler is ``setChargeElectricityLimit`` (measured on a live G6
    2026-11-03: 16 A sent, code 200, and ``instant.charge.electric_current_limit``
    moved 27 -> 16 once the car next reported). Before this existed the only way
    to reach it was ``xpeng.send_raw_mqtt``, which publishes without awaiting the
    vehicle's answer - so a rejection looked exactly like success.
    """

    _policy_group = GROUP_CHARGING
    _attr_translation_key = "charge_current_limit_control"
    _attr_icon = "mdi:current-ac"
    _attr_native_min_value = 6
    _attr_native_max_value = 32
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UNIT_AMPERE
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, coordinator: XPengDataCoordinator, mqtt_client: XPengMQTTClient
    ) -> None:
        super().__init__(coordinator)
        self._mqtt_client = mqtt_client
        self._attr_unique_id = f"xpeng_{self._vin}_charge_current_limit_control"

    @property
    def native_value(self) -> int | None:
        if not self.coordinator.data:
            return None
        charge = (
            self.coordinator.data
            .get("msg_content", {})
            .get("instant", {})
            .get("charge", {})
        )
        return charge.get("electric_current_limit") if charge else None

    async def async_set_native_value(self, value: float) -> None:
        """Await the vehicle's answer; telemetry owns the displayed value.

        The acknowledgement for this command does not carry
        ``electric_current_limit`` - service 4 answers with whatever charge
        fields it feels like reporting, and measurement showed the ampere field
        absent - so nothing is folded in here and the number stays at its old
        reading until the car next uploads state. That lag is honest: a
        publication is not a physical change.
        """
        self._guard()
        await async_send_confirmed(
            self.hass, self._mqtt_client, self._vin, 4,
            {"cmd_type": 7, "cmd_value": int(value)},
        )


class XPengFanSpeed(XPengEntity, NumberEntity):
    """Legacy fan entity retained for registry compatibility; commands unverified."""

    _attr_entity_registry_enabled_default = False
    _policy_group = GROUP_COMFORT
    _attr_translation_key = "fan_speed_control"
    _attr_icon = "mdi:fan"
    _attr_native_min_value = 1
    _attr_native_max_value = 8
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, coordinator: XPengDataCoordinator, mqtt_client: XPengMQTTClient
    ) -> None:
        super().__init__(coordinator)
        self._mqtt_client = mqtt_client
        self._attr_unique_id = f"xpeng_{self._vin}_fan_speed_control"

    @property
    def native_value(self) -> int | None:
        if not self.coordinator.data:
            return None
        hvac = (
            self.coordinator.data
            .get("msg_content", {})
            .get("instant", {})
            .get("hvac", {})
        )
        return hvac.get("hvac_wind_speed") if hvac else None

    async def async_set_native_value(self, value: float) -> None:
        self._guard()
        # Service 3 / command 2 is temperature in the official app, not fan speed.
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="fan_speed_unverified"
        )
