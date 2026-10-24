"""Device tracker platform for the XPENG integration."""
from __future__ import annotations

import logging
import time

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import XPengDataCoordinator
from .entity import XPengEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XPENG device tracker."""
    coordinator: XPengDataCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    async_add_entities([XPengDeviceTracker(coordinator)])


class XPengDeviceTracker(XPengEntity, TrackerEntity):
    """Represent the XPENG vehicle location as a device tracker."""

    _attr_has_entity_name = True
    _attr_translation_key = "location"
    _attr_icon = "mdi:car"

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        """Initialize the device tracker."""
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_location"

    @property
    def source_type(self) -> SourceType:
        """Return the source type."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Return latitude of the last position that was a real fix."""
        fix = self.coordinator.gps_fix.position
        return fix[0] if fix else None

    @property
    def longitude(self) -> float | None:
        """Return longitude of the last position that was a real fix."""
        fix = self.coordinator.gps_fix.position
        return fix[1] if fix else None

    @property
    def extra_state_attributes(self) -> dict[str, str | float | bool | None]:
        """Return extra attributes.

        ``coord_valid`` and ``fix_age_s`` are the point of this entity: the car
        can report coordinates that are its last known position rather than a
        current fix (``interval.drive.coordValid`` false), and a position whose
        age is not visible is what made "the car is still at home" look like a
        lost GPS. Age is None when no valid fix has been seen yet this session.
        """
        speed_raw = self._get_nested_value("msg_content.interval.drive.speed")
        angle = self._get_nested_value("msg_content.interval.drive.angle")
        coord_type = self._get_nested_value("msg_content.interval.drive.coordType")

        speed = None
        if speed_raw is not None:
            try:
                speed = round(float(speed_raw), 1)
            except (ValueError, TypeError):
                pass

        age = self.coordinator.gps_fix.age_seconds(time.time())
        return {
            "speed": speed,
            "bearing": angle,
            "coord_type": coord_type,
            "coord_valid": self.coordinator.gps_fix.coord_valid,
            "fix_age_s": None if age is None else round(age),
        }
