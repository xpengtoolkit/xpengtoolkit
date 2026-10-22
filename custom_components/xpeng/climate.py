"""Climate platform for the XPENG integration."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate.const import PRESET_NONE
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .climate_commands import (
    power_payload,
    send_climate,
    temperature_payload,
    temperature_step,
)
from .command_policy import GROUP_COMFORT, default_policy
from .const import DOMAIN
from .coordinator import XPengDataCoordinator
from .entity import XPengEntity
from .mqtt_client import XPengMQTTClient


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XPENG climate entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: XPengDataCoordinator = data["coordinator"]
    mqtt_client: XPengMQTTClient = data["mqtt_client"]
    policy = data.get("command_policy") or default_policy()
    if policy.exposes(GROUP_COMFORT):
        async_add_entities([XPengClimate(coordinator, mqtt_client)])


class XPengClimate(XPengEntity, ClimateEntity):
    """Measured cabin climate with capability-selected, confirmed commands."""

    _attr_name = "Climate"
    _attr_icon = "mdi:air-conditioner"
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL]
    _policy_group = GROUP_COMFORT
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 16.0
    _attr_max_temp = 32.0
    _attr_preset_modes = [PRESET_NONE, "defrost"]

    def __init__(
        self, coordinator: XPengDataCoordinator, mqtt_client: XPengMQTTClient
    ) -> None:
        super().__init__(coordinator)
        self._mqtt_client = mqtt_client
        self._attr_unique_id = f"xpeng_{self._vin}_cabin_climate"

    def _get_hvac_value(self, key: str) -> Any:
        """Get a value from the HVAC data dict."""
        if not self.coordinator.data:
            return None
        return (
            self.coordinator.data.get("msg_content", {})
            .get("instant", {})
            .get("hvac", {})
            .get(key)
        )

    def _temperature(self, key: str) -> float | None:
        value = self._get_hvac_value(key)
        if isinstance(value, bool):
            return None
        try:
            value = float(value)
            return value if math.isfinite(value) else None
        except (TypeError, ValueError):
            return None

    @property
    def current_temperature(self) -> float | None:
        value = self._temperature("hvac_inner_temp_float")
        return value if value is not None else self._temperature("hvac_inner_temp")

    @property
    def target_temperature(self) -> float | None:
        value = self._temperature("hvac_temp_float")
        return value if value is not None else self._temperature("hvac_temp")

    @property
    def target_temperature_step(self) -> float:
        return temperature_step(self.coordinator)

    @property
    def hvac_mode(self) -> HVACMode | None:
        return {0: HVACMode.OFF, 1: HVACMode.HEAT_COOL}.get(
            self._get_hvac_value("hvac_on")
        )

    @property
    def hvac_action(self) -> HVACAction | None:
        if self.hvac_mode is None:
            return None
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        current, target = self.current_temperature, self.target_temperature
        if current is None or target is None:
            return HVACAction.IDLE
        if current < target - 0.3:
            return HVACAction.HEATING
        if current > target + 0.3:
            return HVACAction.COOLING
        return HVACAction.IDLE

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Withdraw the controls when this entity is read-only.

        Climate is one of only two domains where Home Assistant can render
        "no control" by *removing* the control instead of leaving a button that
        raises, so read-only mode here gives the state without an actionable
        card - which is exactly what the mode is for. The switch, number and
        select platforms have no such API; they guard the command instead.
        """
        if not self._policy.allows(GROUP_COMFORT):
            return ClimateEntityFeature(0)
        return self._attr_supported_features

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        elif hvac_mode == HVACMode.HEAT_COOL:
            await self.async_turn_on()
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_climate_option",
                translation_placeholders={"option": str(hvac_mode)},
            )

    async def async_turn_on(self) -> None:
        """Use the app's power command without inventing a temperature change."""
        self._guard()
        await send_climate(
            self.hass,
            self._mqtt_client,
            self.coordinator,
            power_payload(self.coordinator, True),
        )

    async def async_turn_off(self) -> None:
        self._guard()
        await send_climate(
            self.hass,
            self._mqtt_client,
            self.coordinator,
            power_payload(self.coordinator, False),
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target, independently of whether the cabin A/C is on."""
        self._guard()
        value = kwargs.get(ATTR_TEMPERATURE)
        if value is None:
            return
        try:
            content = temperature_payload(self.coordinator, value)
        except (TypeError, ValueError, OverflowError):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_climate_option",
                translation_placeholders={"option": str(value)},
            ) from None
        await send_climate(self.hass, self._mqtt_client, self.coordinator, content)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self._guard()
        # The old command 2 sets integer temperature. Do not send it as fan speed.
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="fan_speed_unverified"
        )

    @property
    def preset_mode(self) -> str | None:
        return {0: PRESET_NONE, 1: "defrost"}.get(self._get_hvac_value("hvac_defrost"))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        self._guard()
        if preset_mode not in self._attr_preset_modes:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_climate_option",
                translation_placeholders={"option": str(preset_mode)},
            )
        await send_climate(
            self.hass,
            self._mqtt_client,
            self.coordinator,
            {"cmd_type": 4, "cmd_value": int(preset_mode == "defrost")},
        )
