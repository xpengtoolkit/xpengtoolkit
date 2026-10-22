"""Button platform for the XPENG integration."""
from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .charge_port import controls_enabled
from .command_policy import (
    ACTION_COMMAND,
    ACTION_WAKE,
    GROUP_ACCESS,
    GROUP_CHARGING,
    GROUP_LIGHTS,
    GROUP_QUERIES,
    GROUP_SECURING,
    default_policy,
)
from .const import COMMAND_NOT_SENT_MESSAGE, DOMAIN
from .coordinator import XPengDataCoordinator
from .entity import XPengEntity
from .entity_layout import include_legacy_entity
from .mqtt_client import XPengMQTTClient
from .vehicle_capabilities import from_coordinator

_LOGGER = logging.getLogger(__name__)

#: Buttons that only exist on a car with motorized charge-port controls; see the
#: ``charge_port_controls`` option and charge_port.controls_enabled().
_CHARGE_PORT_BUTTON_KEYS: Final = frozenset({"open_charge_port", "close_charge_port"})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XPENG button entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: XPengDataCoordinator = data["coordinator"]
    mqtt_client: XPengMQTTClient = data["mqtt_client"]

    policy = data.get("command_policy") or default_policy()

    entities = [
        XPengCommandButton(
            coordinator, mqtt_client,
            key="flash_lights",
            group=GROUP_LIGHTS,
            name="Flash lights",
            icon="mdi:car-light-high",
            service_type=5,
            msg_content={"cmd_type": 1, "cmd_value": 1},
        ),
        XPengCommandButton(
            coordinator, mqtt_client,
            key="honk_horn",
            group=GROUP_LIGHTS,
            name="Honk horn",
            icon="mdi:bugle",
            service_type=6,
            msg_content={"cmd_type": 1, "cmd_value": 1},
        ),
        XPengCommandButton(
            coordinator, mqtt_client,
            key="close_windows",
            group=GROUP_SECURING,
            name="Close windows",
            icon="mdi:car-door",
            service_type=12,
            msg_content={"cmd_type": 1, "cmd_value": 1},
        ),
        XPengCommandButton(
            coordinator, mqtt_client,
            key="vent_windows",
            group=GROUP_ACCESS,
            name="Vent windows",
            icon="mdi:car-door",
            service_type=12,
            msg_content={"cmd_type": 1, "cmd_value": 2},
        ),
        XPengCommandButton(
            coordinator, mqtt_client,
            key="force_update",
            group=GROUP_QUERIES,
            action=ACTION_WAKE,
            name="Force update",
            icon="mdi:refresh",
            service_type=19,
            msg_content={},
            msg_type=4,
        ),
        XPengCommandButton(
            coordinator, mqtt_client,
            key="wake_vehicle",
            group=GROUP_QUERIES,
            action=ACTION_WAKE,
            name="Wake vehicle",
            icon="mdi:power",
            service_type=19,
            msg_content={"cmd_type": 2},
            msg_type=2,
        ),
        XPengCommandButton(
            coordinator, mqtt_client,
            key="query_xguard",
            group=GROUP_QUERIES,
            name="Query X-Guard",
            icon="mdi:cctv",
            service_type=8,
            msg_content={"action": 1},
        ),
        # Trunk
        XPengCommandButton(
            coordinator, mqtt_client,
            key="open_trunk",
            group=GROUP_ACCESS,
            name="Open trunk",
            icon="mdi:car-back",
            service_type=9,
            msg_content={"cmd_type": 2, "cmd_value": 1},
        ),
        XPengCommandButton(
            coordinator, mqtt_client,
            key="close_trunk",
            group=GROUP_SECURING,
            name="Close trunk",
            icon="mdi:car-back",
            service_type=9,
            msg_content={"cmd_type": 2, "cmd_value": 3},
        ),
        # Charging
        XPengCommandButton(
            coordinator, mqtt_client,
            key="start_charge",
            group=GROUP_CHARGING,
            name="Start charging",
            icon="mdi:battery-charging",
            service_type=4,
            msg_content={"cmd_type": 1, "cmd_value": 1},
        ),
        XPengCommandButton(
            coordinator, mqtt_client,
            key="stop_charge",
            group=GROUP_CHARGING,
            name="Stop charging",
            icon="mdi:battery-off",
            service_type=4,
            msg_content={"cmd_type": 1, "cmd_value": 0},
        ),
        # Charge port. One button pair, not two: export cars have a single
        # combined CCS2 port, and the car refuses cmd_type 4 on them (10404
        # "could not open" / 10405 "could not close", measured 2026-10-16).
        XPengCommandButton(
            coordinator, mqtt_client,
            key="open_charge_port",
            group=GROUP_CHARGING,
            name="Open charge port",
            icon="mdi:ev-plug-type2",
            service_type=4,
            msg_content={"cmd_type": 3, "cmd_value": 1},
        ),
        XPengCommandButton(
            coordinator, mqtt_client,
            key="close_charge_port",
            group=GROUP_CHARGING,
            name="Close charge port",
            icon="mdi:ev-plug-type2",
            service_type=4,
            msg_content={"cmd_type": 3, "cmd_value": 0},
        ),
        # Windows
        XPengCommandButton(
            coordinator, mqtt_client,
            key="open_windows",
            group=GROUP_ACCESS,
            name="Open windows",
            icon="mdi:car-door",
            service_type=12,
            msg_content={"cmd_type": 1, "cmd_value": 2},
        ),
    ]

    # A button carries no state, so READ_ONLY and DISABLED mean the same thing
    # here: the entity is simply not created. Keeping a button that raises when
    # pressed would be a worse answer than not showing it.
    #
    # Charge-port controls are also an owner's answer, not a permission: a manual
    # flap (measured on a P7+, 2026-10-22) answers the commands with an error or
    # with nothing, and a button that looks like it worked is worse than no
    # button. The port state sensor is unaffected.
    charge_port_controls = controls_enabled(entry.options)
    charge_port_controls = charge_port_controls and from_coordinator(coordinator).support("charge_port_control") is not False
    async_add_entities(
        [
            entity
            for entity in entities
            if entity._policy_group
            and policy.allows(entity._policy_group)
            and include_legacy_entity(
                entry, policy, "button", entity._attr_translation_key
            )
            and (
                charge_port_controls
                or entity._attr_translation_key not in _CHARGE_PORT_BUTTON_KEYS
            )
        ]
    )


class XPengCommandButton(XPengEntity, ButtonEntity):
    """A button that sends an MQTT command to the vehicle."""

    def __init__(
        self,
        coordinator: XPengDataCoordinator,
        mqtt_client: XPengMQTTClient,
        key: str,
        name: str,
        icon: str,
        service_type: int,
        msg_content: dict[str, Any],
        msg_type: int = 2,
        group: str | None = None,
        action: str = ACTION_COMMAND,
    ) -> None:
        super().__init__(coordinator)
        self._policy_group = group
        self._policy_action = action
        self._mqtt_client = mqtt_client
        self._service_type = service_type
        self._msg_content = msg_content
        self._msg_type = msg_type
        self._attr_unique_id = f"xpeng_{self._vin}_{key}"
        self._attr_translation_key = key
        self._attr_name = name
        self._attr_icon = icon

    async def async_press(self) -> None:
        self._guard(self._policy_action)
        sent = await self.hass.async_add_executor_job(
            self._mqtt_client.send_command,
            self._vin,
            self._service_type,
            self._msg_content,
            self._msg_type,
        )
        if not sent:
            # A press that published nothing used to look exactly like one that
            # did: the only trace was a warning in the log. Raise instead, so the
            # UI shows the failure and an automation trace records it.
            raise HomeAssistantError(COMMAND_NOT_SENT_MESSAGE)
