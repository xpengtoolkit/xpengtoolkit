"""Binary sensor platform for the XPENG integration."""
from __future__ import annotations

import time
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import health
from .charge_port import charge_cable_state
from .const import (
    BINARY_SENSOR_DESCRIPTIONS,
    CONF_CLIENT_ID,
    CONF_MQTT_REFRESH_INTERVAL,
    DEFAULT_MQTT_REFRESH_INTERVAL,
    DOMAIN,
    XPengBinarySensorEntityDescription,
)
from .coordinator import XPengDataCoordinator
from .entity import XPengEntity
from .entity_layout import include_legacy_entity
from .command_policy import default_policy

#: How much of the MQTT clientId the diagnostics show. The id is
#: `<deviceId>:<...>:<userId>` and the deviceId is the identity this install
#: signs and mints with, so the tail is withheld even here.
CLIENT_ID_KEEP = 26


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XPENG binary sensor entities."""
    # The whole per-entry bundle, not just the coordinator: the health entities
    # below report the credential loop and the paho session, neither of which
    # reaches the coordinator. See `__init__.py` for what the bundle holds.
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: XPengDataCoordinator = entry_data["coordinator"]

    # plugged_in is built by its own class below: its answer depends on how long
    # the car has been quiet as well as on the payload, and the generic sensor can
    # only see the payload.
    plugged_in_description = next(
        description
        for description in BINARY_SENSOR_DESCRIPTIONS
        if description.key == "plugged_in"
    )
    entities: list[BinarySensorEntity] = [
        XPengBinarySensor(coordinator, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
        if description is not plugged_in_description
        and include_legacy_entity(
            entry,
            entry_data.get("command_policy") or default_policy(),
            "binary_sensor",
            description.key,
        )
    ]
    # Not vehicle telemetry: dedicated classes, because their values come from
    # the refresh state / MQTT client rather than from the MQTT payload.
    entities.append(
        XPengPluggedInBinarySensor(coordinator, plugged_in_description)
    )
    entities.append(XPengCredentialsValidBinarySensor(coordinator, entry_data))
    entities.append(XPengMqttConnectedBinarySensor(coordinator, entry_data))
    async_add_entities(entities)


class XPengBinarySensor(XPengEntity, BinarySensorEntity):
    """Representation of an XPENG binary sensor."""

    entity_description: XPengBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: XPengDataCoordinator,
        description: XPengBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"xpeng_{self._vin}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        raw = self._get_nested_value(self.entity_description.value_path)
        if raw is None:
            return None

        # Custom value function takes precedence
        if self.entity_description.value_fn is not None:
            return self.entity_description.value_fn(raw)

        # Simple on_value comparison
        if self.entity_description.on_value is not None:
            return raw == self.entity_description.on_value

        # Default: truthy check
        return bool(raw)


class _PolledHealthBinarySensor:
    """A health binary sensor that Home Assistant polls itself.

    The same two overrides, for the same reasons, as ``XPengPolledSensor`` in
    ``sensor.py``:

    * ``should_poll`` — ``CoordinatorEntity`` defines it as a ``cached_property``
      returning False, so the usual ``_attr_should_poll`` never reaches it.
    * ``async_update`` — the inherited one calls
      ``coordinator.async_request_refresh()``, which is right for a poll-based
      coordinator and actively harmful for this one. It is push-based with no
      ``_async_update_data`` to run, so the request fails,
      ``last_update_success`` flips to False, and ``CoordinatorEntity.available``
      returns exactly that flag — so asking the coordinator to refresh would take
      *every* entity in the integration unavailable, not just the asker.

    It must come FIRST in the base-class list, so its ``should_poll`` wins over
    the one ``CoordinatorEntity`` supplies further down the MRO. Polling is not
    optional here: a credential age and a message age both keep climbing while
    nothing else happens, and a push-based coordinator has no reason to wake a
    health entity — unpolled, it would report whatever age it had when some
    unrelated message last arrived.
    """

    @property
    def should_poll(self) -> bool:
        return True

    async def async_update(self) -> None:
        """No-op on purpose — see the class docstring."""


def _age_seconds(now: float, timestamp: Any) -> int:
    """Whole seconds since one of the MQTT client's clocks, or 0.

    The arithmetic lives in ``health.age_seconds`` so that the numbers these
    attributes publish are covered by the health tests; this only supplies the
    timestamps. Note what a 0 means: the client primes both clocks at
    construction, so 0 is "nothing has arrived yet this session" as much as it is
    "arrived this second" — ``last_data_update`` (from the coordinator) is the
    unambiguous answer to that question.
    """
    return health.age_seconds(now, timestamp)


class XPengPluggedInBinarySensor(_PolledHealthBinarySensor, XPengBinarySensor):
    """``plugged_in``, with the car's silence taken into account.

    The description's ``value_fn`` is still ``charge_cable_state``; this class
    supplies the second half of its input, how long the car has gone without
    reporting anything real. The distinction only matters for a car waiting on an
    armed charge plan, which is the one state where this model's payload says
    nothing about the cable (see ``charge_port.py`` for the measurements).

    Polled for the same reason the health sensors are: the answer changes with the
    clock, not with a message. A car that goes quiet crosses the silence threshold
    without anything arriving to say so, and an unpolled entity would sit on the
    last answer - which is precisely the "off" this was written to stop claiming.
    """

    @property
    def is_on(self) -> bool | None:
        raw = self._get_nested_value(self.entity_description.value_path)
        return charge_cable_state(raw, silent_seconds=self._silent_seconds())

    def _silent_seconds(self) -> float | None:
        """How long since the car last sent something meaningful, or None."""
        status = self.coordinator.poll_status or {}
        silence = status.get("meaningful_silence_seconds")
        if silence is None:
            return None
        try:
            return float(silence)
        except (TypeError, ValueError):
            return None


class XPengCredentialsValidBinarySensor(
    _PolledHealthBinarySensor, XPengEntity, BinarySensorEntity
):
    """Whether the MQTT credential mint/refresh loop is healthy.

    This is the diagnostic the integration lacked. When a credential dies the
    push stream stops and every vehicle entity freezes at its last value — which
    is indistinguishable from a parked car, because "nothing arrived" is the
    normal state of an asleep vehicle. OFF here means "the values you can see are
    not being refreshed", whatever the car is doing.

    ON requires all three of: a successful refresh on record, that success being
    no older than ``3 * mqtt_refresh_interval``, and no consecutive failures. The
    rule itself lives in ``health.credentials_health`` — stdlib only, unit-tested
    — and is called rather than reimplemented, so what the tests pin is what
    ships.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "credentials_valid"
    _attr_icon = "mdi:key-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: XPengDataCoordinator, entry_data: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self._entry_data = entry_data
        self._attr_unique_id = f"xpeng_{self._vin}_credentials_valid"

    # ── Inputs, any of which may be absent in a half-built or unloaded entry.

    def _refresh_state(self) -> dict[str, Any]:
        state = self._entry_data.get("refresh_state")
        return state if isinstance(state, dict) else {}

    def _consecutive_failures(self) -> int:
        # Via health.as_float rather than int(): the value is written by another
        # thread, and int() on an inf or NaN raises while Home Assistant is
        # asking for this entity's state.
        return int(health.as_float(self._refresh_state().get(
            "consecutive_failures"
        ), 0.0) or 0.0)

    def _refresh_interval(self) -> int:
        """The interval the refresh timer was actually built with.

        Read through the same expression ``async_setup_entry`` uses to build the
        timer, rather than carried separately in the bundle, so the staleness
        bound cannot drift away from the schedule it describes.
        """
        entry = self._entry_data.get("entry")
        options = getattr(entry, "options", None) or {}
        interval = health.as_float(
            options.get(CONF_MQTT_REFRESH_INTERVAL, DEFAULT_MQTT_REFRESH_INTERVAL)
        )
        return DEFAULT_MQTT_REFRESH_INTERVAL if interval is None else int(interval)

    def _connected(self) -> bool:
        """Whether the MQTT client currently holds a session.

        Credentials mint on demand now, so while this is true there are no fresh
        mints to stamp the sensor with; the connection itself is the evidence.
        """
        client = self._entry_data.get("mqtt_client")
        return bool(getattr(client, "connected", False)) if client else False

    def _health(self) -> dict[str, Any]:
        state = self._refresh_state()
        return health.credentials_health(
            now=time.time(),
            last_success=state.get("last_success_at"),
            consecutive_failures=state.get("consecutive_failures"),
            refresh_interval=self._refresh_interval(),
            connected=self._connected(),
        )

    @property
    def is_on(self) -> bool:
        return bool(self._health()["valid"])

    def _account(self) -> str | None:
        """Which account these credentials belong to — email, else numeric id."""
        email = str(getattr(self.coordinator, "account_email", "") or "").strip()
        if email:
            return email
        uid = str(getattr(self.coordinator, "account_uid", "") or "").strip()
        return uid or None

    def _client_id(self) -> str | None:
        entry = self._entry_data.get("entry")
        data = getattr(entry, "data", None) or {}
        client_id = str(data.get(CONF_CLIENT_ID, "") or "").strip()
        return client_id[:CLIENT_ID_KEEP] or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._refresh_state()
        result = self._health()
        return {
            # ISO 8601, or None when nothing has succeeded yet: "I do not know"
            # is the honest answer, and a made-up time would be worse than none.
            "last_success": health.iso(state.get("last_success_at")),
            "age_s": result["age_s"],
            "stale_after_s": result["stale_after_s"],
            "consecutive_failures": self._consecutive_failures(),
            "last_error": state.get("last_error"),
            "account": self._account(),
            "client_id": self._client_id(),
            # Exposed because an OFF sensor with no explanation is the complaint
            # this feature exists to answer: the reason string is the payload of
            # the decision function above.
            "reason": result["reason"],
        }


class XPengMqttConnectedBinarySensor(
    _PolledHealthBinarySensor, XPengEntity, BinarySensorEntity
):
    """Whether the paho session to XPENG's broker is up.

    Read from the client, never from the coordinator: the coordinator only knows
    when data last arrived, and a perfectly healthy connection carries nothing
    for hours — that is what a sleeping car does. Keeping this as its own entity
    separates two facts that fail independently: credentials can be valid while
    the socket is down, and a socket can be up on a password about to expire with
    no refresh loop behind it.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "mqtt_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:lan-connect"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: XPengDataCoordinator, entry_data: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self._entry_data = entry_data
        self._attr_unique_id = f"xpeng_{self._vin}_mqtt_connected"

    def _client(self) -> Any | None:
        return self._entry_data.get("mqtt_client")

    @property
    def is_on(self) -> bool:
        """OFF, not unknown, when there is no client to ask.

        A missing client is a definite "not connected": nothing can be
        delivering data. ``False`` is the useful answer, and it is what this
        entity would say one tick later anyway.
        """
        return bool(getattr(self._client(), "connected", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        client = self._client()
        now = time.time()
        return {
            "connected": self.is_on,
            "last_state_message_age_s": _age_seconds(
                now, getattr(client, "last_state_msg_ts", None)
            ),
            "last_message_age_s": _age_seconds(
                now, getattr(client, "last_msg_ts", None)
            ),
        }
