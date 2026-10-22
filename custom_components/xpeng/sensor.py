"""Sensor platform for the XPENG integration."""
from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_time_interval,
)

from . import poll_status
from .const import DOMAIN, SENSOR_DESCRIPTIONS, XPengSensorEntityDescription
from .coordinator import XPengDataCoordinator
from .entity import XPengEntity
from .entity_layout import include_legacy_entity
from .command_policy import default_policy
from .xpeng_errors import command_response_message
from .vin_descriptor import mask_vin

from datetime import timedelta


#: Unique ids this integration used to create and no longer does. Add here when a
#: sensor is removed: Home Assistant keeps the registry entry when a platform stops
#: creating an entity, so without this an upgraded install keeps a ghost entity that
#: is unavailable forever.
_REMOVED_SENSOR_UNIQUE_IDS: Final = (
    # Removed in 0.6.6. XPENG gates the 7-day consumption figure per account: on a
    # shared (secondary) account the endpoint returns 0.0 with no error while the
    # owner's app shows a real number - verified 2026-10-09, app "16.9 kWh/100km"
    # against an API 0.0. On those accounts it is a permanent zero that can only be
    # explained, not fixed, so the entity is gone.
    "xpeng_{vin}_energy_report",
)


def _prune_removed_entities(hass: HomeAssistant, vin: str) -> None:
    """Drop registry entries for sensors this integration no longer creates."""
    registry = er.async_get(hass)
    for template in _REMOVED_SENSOR_UNIQUE_IDS:
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, template.format(vin=vin)
        )
        if entity_id is not None:
            registry.async_remove(entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XPENG sensor entities."""
    coordinator: XPengDataCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    entities: list[SensorEntity] = [
        XPengSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
        if include_legacy_entity(
            entry,
            getattr(coordinator, "command_policy", None) or default_policy(),
            "sensor",
            description.key,
        )
    ]

    # Add diagnostic sensors
    entities.append(XPengCredentialAgeSensor(coordinator))
    entities.append(XPengLastRenewalSensor(coordinator))
    entities.append(XPengAccountSensor(coordinator))
    entities.append(XPengLastUpdateSensor(coordinator))
    entities.append(XPengLastDataUpdateSensor(coordinator))
    entities.append(XPengPollingStatusSensor(coordinator))
    entities.append(XPengNextPollSensor(coordinator))
    entities.append(XPengXosVersionSensor(coordinator))
    entities.append(XPengMqttListenerSensor(coordinator))
    entities.append(XPengMqttDiscoverySensor(coordinator))
    # Only exists when raw capture is switched on, because the entity's whole job
    # is to report what the recorder is doing - and there is no recorder otherwise.
    capture_recorder = hass.data[DOMAIN][entry.entry_id].get("capture")
    if capture_recorder is not None:
        entities.append(XPengCaptureSensor(coordinator, capture_recorder))
    entities.append(XPengCommandFeedbackSensor(coordinator))
    entities.append(XPengNotificationSensor(coordinator))
    _prune_removed_entities(hass, coordinator.vin)

    # REST API sensors
    entities.append(XPengMaintenanceSensor(coordinator))
    entities.append(XPengXComboSensor(coordinator))
    entities.append(XPengParkedConsumptionSensor(coordinator))
    entities.append(XPengOtaUpgradeSensor(coordinator))
    entities.append(XPengBetaSensor(coordinator))
    entities.append(XPengRemoteCapabilitiesSensor(coordinator))
    entities.append(XPengVehicleExtensionSensor(coordinator))
    entities.append(XPengLeftComfortPreonSensor(coordinator))
    entities.append(XPengHvacSchedulesSensor(coordinator))
    entities.append(XPengCduIdSensor(coordinator))
    entities.append(XPengCduPartitionSensor(coordinator))
    entities.append(XPengWeatherSensor(coordinator))
    entities.append(XPengVehicleIdentitySensor(coordinator))
    # ICCID only exists if the user supplied it (no app-tier API source)
    if coordinator.iccid:
        entities.append(XPengIccidSensor(coordinator))

    async_add_entities(entities)


def _iso(epoch: float) -> str:
    """Local ISO-8601 for an epoch written by the timers in __init__.py.

    They record plain `time.time()` floats rather than datetimes because they run
    on executor threads and in plain callbacks, where building timezone-aware
    objects is the sensor's job, not theirs.
    """
    return datetime.fromtimestamp(
        float(epoch), tz=timezone.utc
    ).astimezone().isoformat()


class XPengSensor(XPengEntity, SensorEntity):
    """Representation of an XPENG sensor."""

    entity_description: XPengSensorEntityDescription

    def __init__(
        self,
        coordinator: XPengDataCoordinator,
        description: XPengSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"xpeng_{self._vin}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        raw = self._get_nested_value(self.entity_description.value_path)
        if raw is None:
            return None
        value = (
            self.entity_description.value_fn(raw)
            if self.entity_description.value_fn is not None else raw
        )
        # HA enum sensors reject any value outside their declared options. The
        # cloud uses sentinels such as -1 before it has a charge-port reading.
        if (
            self.entity_description.device_class == SensorDeviceClass.ENUM
            and value not in (self.entity_description.options or ())
        ):
            return None
        return value


class XPengAccountSensor(XPengEntity, SensorEntity):
    """Which XPENG account these credentials belong to.

    Answers "is this the account I think it is?" without logging in as that
    account elsewhere. The value comes from the config entry, which is where the
    onboarding tool's recorded email is persisted, so it survives reboots and
    survives the token file being deleted or replaced.

    The tokens themselves cannot supply it: an accessToken is a tenant-sealed
    blob whose plaintext is only the teeId and this install's deviceId, and the
    tenant holds keys rather than account data. Entries imported before the tool
    recorded the email fall back to the numeric account id.

    Diagnostic category so it is not surfaced as vehicle telemetry. Note the
    value is the account address in the clear - it will appear in dashboards and
    in the recorder database, which is the point of showing it.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "account"
    _attr_icon = "mdi:account-key"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_account"

    @property
    def native_value(self) -> str | None:
        email = str(getattr(self.coordinator, "account_email", "") or "").strip()
        if email:
            return email
        uid = str(getattr(self.coordinator, "account_uid", "") or "").strip()
        # Prefer the id over "unknown": it still identifies the account, and the
        # attribute below says why the email is missing.
        return uid or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        uid = str(getattr(self.coordinator, "account_uid", "") or "").strip()
        email = str(getattr(self.coordinator, "account_email", "") or "").strip()
        attrs: dict[str, Any] = {}
        if uid:
            attrs["uid"] = uid
        if not email:
            # Actionable rather than mysterious: this is how it gets filled in.
            attrs["email_unknown_reason"] = (
                "these tokens were imported before the onboarding tool recorded "
                "the account email; re-run it, or set the field on the Import "
                "tokens form"
            )
        return attrs


class XPengCredentialAgeSensor(XPengEntity, SensorEntity):
    """Shows how long since credentials were last refreshed."""

    _attr_has_entity_name = True
    _attr_translation_key = "credential_age"
    _attr_icon = "mdi:key-clock"

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_credential_age"

    @property
    def native_value(self) -> str | None:
        ts = self.coordinator.credential_timestamp
        if ts is None:
            return "Unknown"
        delta = datetime.now(timezone.utc) - ts
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ts = self.coordinator.credential_timestamp
        if ts is None:
            return {}
        return {
            "refreshed_at": ts.astimezone().strftime("%d %b %Y %I:%M %p"),
            "age_seconds": int((datetime.now(timezone.utc) - ts).total_seconds()),
        }


class XPengLastRenewalSensor(XPengEntity, SensorEntity):
    """When the credentials were last renewed.

    The renewal app calls ``xpeng.import_credentials`` on every renewal, and every
    other way a pair can arrive (a pasted pair, a reauth, the app bootstrapping a
    first install) writes the same stamp - so this is the one honest clock for "when
    did a renewal last run". It is a timestamp rather than an age so the value stays
    meaningful however long Home Assistant has been up, and it is deliberately not
    `credential_age` above: that one moves every `mqtt_refresh_interval` because the
    broker's short-lived credentials are re-minted, which is a different question.
    Unknown until credentials have arrived at least once on an entry that predates
    this key, or on one that has never been renewed.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "last_renewal"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:key-refresh"

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_last_renewal"

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_renewal

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ts = self.coordinator.last_renewal
        if ts is None:
            return {}
        return {
            "refreshed_at": ts.astimezone().strftime("%d %b %Y %I:%M %p"),
            "age_seconds": int((datetime.now(timezone.utc) - ts).total_seconds()),
        }


class XPengXosVersionSensor(XPengEntity, SensorEntity):
    """Shows the vehicle's XOS software version from REST OTA endpoint."""

    _attr_has_entity_name = True
    _attr_translation_key = "xos_version"
    _attr_icon = "mdi:car-cog"

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_xos_version"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.xos_version


class XPengLastUpdateSensor(XPengEntity, SensorEntity):
    """Shows when vehicle data was last received — as a timestamp.

    This used to return a formatted string ("18 Oct 2026 12:06:56 AM"), which
    Home Assistant cannot localise or age, and which dashboards then rendered in
    whatever wording the writer happened to choose (reported by a user
    2026-10-17). `device_class: timestamp` makes HA do both: it shows the time in
    the reader's own timezone/locale and offers "x minutes ago" itself.

    Kept as its original entity id (`..._last_data_update`) rather than folded
    into the newer ``..._last_data_timestamp``, so existing dashboards and
    automations keep working; both now report the same timestamp. The formatted
    `timestamp`/`age_seconds` attributes are still published for templates that
    read them.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "last_update"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_last_update"

    @property
    def native_value(self) -> datetime | None:
        # A timestamp sensor must hand Home Assistant a datetime (or None for
        # unknown), never a sentinel string.
        return getattr(self.coordinator, "last_data_timestamp", None)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ts = getattr(self.coordinator, "last_data_timestamp", None)
        if ts is None:
            return {}
        age = int((datetime.now(timezone.utc) - ts).total_seconds())
        return {
            "timestamp": ts.isoformat(),
            "age_seconds": age,
        }


class XPengLastDataUpdateSensor(XPengEntity, SensorEntity):
    """When the last vehicle-state MQTT message arrived — as a timestamp.

    A `timestamp` device_class rather than another formatted string: Home
    Assistant then renders it in the reader's own timezone and language, and
    "how long ago" is computed when the page is drawn instead of being frozen
    into the text at write time.

    How this sits next to ``XPengLastUpdateSensor``: both report the same field
    (``coordinator.last_data_timestamp``) and both are now `timestamp` sensors;
    the older entity id is kept because dashboards already use it. This one is
    the entity to build new automations on.

    Note the entity id: ``..._last_data_timestamp``, not ``..._last_data_update``.
    The object id is derived from the translated *name*, and
    ``entity.sensor.last_update`` is already named "Last data update" — so that
    slug, and therefore that entity id, belongs to the older sensor. Naming this
    one the same would have duplicated that name and pushed this entity onto
    ``..._last_data_update_2`` on a fresh install.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "last_data_update"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-digital"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_last_data_update"

    @property
    def native_value(self) -> datetime | None:
        """The time of the last vehicle-state message, or None.

        `None` rather than a sentinel string, so Home Assistant shows `unknown`:
        a timestamp sensor with no timestamp must not claim one.
        """
        # getattr rather than plain attribute access: this is a diagnostic, and
        # a coordinator without the field must read "unknown" rather than break
        # the entity while Home Assistant is writing its state.
        return getattr(self.coordinator, "last_data_timestamp", None)


class XPengPolledSensor(XPengEntity, SensorEntity):
    """An XPENG diagnostic sensor that Home Assistant polls itself.

    Some of these numbers change with no vehicle data involved — a credential
    age keeps ageing, a silence counter keeps climbing, a next-poll time keeps
    moving. Waiting for the coordinator to push would leave them answering a
    live question with a stale answer, and before this the only way they
    refreshed was when the value they report happened to change.

    Polling a `CoordinatorEntity` needs `should_poll` overridden, because
    `CoordinatorEntity` defines it as a cached_property returning False, so the
    usual `_attr_should_poll` never reaches it. The no-op `async_update` these
    also need is inherited from `XPengEntity` — see it for why requesting a
    refresh on this push-based coordinator takes every entity in the integration
    unavailable. Combined with the 30s sensor SCAN_INTERVAL, these refresh often
    enough to stay honest and cheaply enough not to matter.
    """

    @property
    def should_poll(self) -> bool:
        return True


class XPengPollingStatusSensor(XPengPolledSensor):
    """What the integration is doing right now.

    `last_update` says when data last arrived, which cannot distinguish a parked
    car from a wedged cloud fanout — both are just an old timestamp. This says
    which cadence is running and whether anything is coming back, so those two
    look different.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "polling_status"
    _attr_icon = "mdi:sync-circle"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_polling_status"

    def _poll(self) -> dict[str, Any]:
        return self.coordinator.poll_status or {}

    @property
    def native_value(self) -> str | None:
        status = self._poll()
        if not status:
            return None
        return poll_status.describe(
            connected=bool(status.get("mqtt_connected")),
            active=poll_status.is_active(
                self.coordinator.vehicle_parked, self.coordinator.is_charging
            ),
            # Staleness, not socket silence: a sleeping car's stub answers the
            # topic without carrying anything, so reporting that as "not silent"
            # would show Parked while no data had arrived for hours. Falls back to
            # the looser figure only if an older payload lacks the key.
            silence_seconds=float(
                status.get("meaningful_silence_seconds")
                if status.get("meaningful_silence_seconds") is not None
                else status.get("state_silence_seconds") or 0.0
            ),
            silence_threshold=float(status.get("silence_threshold_seconds") or 300.0),
            escalated=bool(status.get("escalated")),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self._poll()
        if not status:
            return {}
        active = poll_status.is_active(
            self.coordinator.vehicle_parked, self.coordinator.is_charging
        )
        interval = poll_status.query_interval_seconds(
            self.coordinator.vehicle_parked,
            self.coordinator.is_charging,
            float(status.get("scan_interval_seconds") or 0.0),
            float(status.get("parked_scan_interval_seconds") or 0.0),
            # Reported, not acted on: the sensor has to describe the cadence the
            # timer is actually using, hint included.
            self.coordinator.external_charging_hint,
        )
        attrs: dict[str, Any] = {
            "cadence": "active" if active else "parked",
            "mqtt_connected": bool(status.get("mqtt_connected")),
            # `seconds_since_data` is the one to watch: it only resets when a
            # message carried something. `state_silence_seconds` resets on a
            # sleeping car's stub too, so it can look healthy while the fan-out
            # has delivered nothing for hours.
            "seconds_since_data": int(
                status.get("meaningful_silence_seconds")
                if status.get("meaningful_silence_seconds") is not None
                else status.get("state_silence_seconds") or 0
            ),
            "state_silence_seconds": int(status.get("state_silence_seconds") or 0),
            "any_msg_age_seconds": int(status.get("any_msg_age_seconds") or 0),
            "mqtt_query_interval_seconds": int(interval),
            "rest_poll_interval_seconds": int(status.get("rest_interval_seconds") or 0),
            "last_query": status.get("last_query_kind"),
            "escalations_this_session": int(status.get("escalations") or 0),
        }
        last_query = status.get("last_query_at")
        if last_query:
            attrs["last_query_at"] = _iso(last_query)
        last_rest = status.get("last_rest_poll_at")
        if last_rest:
            attrs["last_rest_poll_at"] = _iso(last_rest)
        last_escalation = status.get("last_escalation_at")
        if last_escalation:
            attrs["last_escalation_at"] = _iso(last_escalation)
        # The broker's ACCOUNT_KICK_OFF notice is a session event, not a car
        # notification; it is tracked here rather than in Last notification.
        last_takeover = status.get("last_takeover_at")
        if last_takeover:
            attrs["last_takeover_at"] = _iso(last_takeover)
            attrs["account_takeovers"] = int(status.get("account_takeovers") or 0)
        return attrs


class XPengNextPollSensor(XPengPolledSensor):
    """When the next MQTT query is due."""

    _attr_has_entity_name = True
    _attr_translation_key = "next_poll"
    _attr_icon = "mdi:timer-sand"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_next_poll"

    @property
    def native_value(self) -> datetime | None:
        status = self.coordinator.poll_status or {}
        # Before the first query of the session, fall back to the timer's own
        # baseline so a next-poll time exists from the moment the integration
        # starts rather than being blank for a whole parked interval.
        last_query = status.get("last_query_at") or status.get("timer_started_at")
        if not last_query:
            return None
        interval = poll_status.query_interval_seconds(
            self.coordinator.vehicle_parked,
            self.coordinator.is_charging,
            float(status.get("scan_interval_seconds") or 0.0),
            float(status.get("parked_scan_interval_seconds") or 0.0),
        )
        if interval <= 0:
            return None
        return datetime.fromtimestamp(
            poll_status.next_query_at(float(last_query), time.time(), interval),
            tz=timezone.utc,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.coordinator.poll_status or {}
        attrs: dict[str, Any] = {}
        next_rest = status.get("next_rest_poll_at")
        if next_rest:
            attrs["next_rest_poll_at"] = _iso(next_rest)
            attrs["seconds_to_next_rest_poll"] = max(
                0, int(float(next_rest) - time.time())
            )
        # The timer only checks on its own cadence, so a query can be sent up to
        # one tick after it falls due. Reported so the timestamp is not read as
        # a precision it does not have.
        attrs["timer_granularity_seconds"] = int(
            status.get("scan_interval_seconds") or 0
        )
        # The inputs this timestamp was derived from. A next-poll time is only
        # meaningful alongside the interval it was computed with and the send it
        # counts from, and when the cadence flips between active and parked
        # those change under it.
        attrs["query_interval_seconds"] = int(
            poll_status.query_interval_seconds(
                self.coordinator.vehicle_parked,
                self.coordinator.is_charging,
                float(status.get("scan_interval_seconds") or 0.0),
                float(status.get("parked_scan_interval_seconds") or 0.0),
            )
        )
        attrs["last_query"] = status.get("last_query_kind")
        last_query = status.get("last_query_at")
        baseline = status.get("timer_started_at")
        if last_query:
            attrs["last_query_at"] = _iso(last_query)
        if baseline:
            attrs["timer_started_at"] = _iso(baseline)
        attrs["counted_from"] = (
            "last_query_at" if last_query else ("timer_started_at" if baseline else None)
        )
        return attrs


class XPengMqttListenerSensor(XPengEntity, SensorEntity):
    """Shows MQTT listener status with rolling log buffer.

    The 'log' attribute contains a formatted text log of recent MQTT
    events (subscriptions and messages) that can be displayed on a
    dashboard using a Markdown card.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "mqtt_listener"
    _attr_icon = "mdi:antenna"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_mqtt_listener"

    @property
    def native_value(self) -> str | None:
        count = self.coordinator.listener_message_count
        if count == 0:
            return "No messages"
        return f"{count} messages"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "message_count": self.coordinator.listener_message_count,
            "log": "\n".join(self.coordinator.listener_log) or "No events yet",
        }
        if self.coordinator.listener_last_topic:
            attrs["last_topic"] = self.coordinator.listener_last_topic
        if self.coordinator.listener_last_payload:
            attrs["last_payload"] = self.coordinator.listener_last_payload
        if self.coordinator.listener_last_timestamp:
            attrs["last_received"] = (
                self.coordinator.listener_last_timestamp.isoformat()
            )
        return attrs


class XPengCaptureSensor(XPengEntity, SensorEntity):
    """What the raw capture is doing: frames written, frames dropped, which file.

    The drop counter is the reason this entity exists at all. ``capture.py`` drops
    a frame when its queue is full rather than letting the queue grow without
    bound, which is what stops a capture becoming the 0.4.30 failure again - but a
    capture that silently dropped a third of the stream would be worse than no
    capture, so the number is on an entity rather than only in a log line, and it
    is in the state as well as the attributes.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "capture"
    _attr_icon = "mdi:record-recorder"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator, recorder: Any) -> None:
        super().__init__(coordinator)
        self._recorder = recorder
        self._attr_unique_id = f"xpeng_{self._vin}_capture"

    @property
    def native_value(self) -> str:
        stats = self._recorder.stats()
        if not stats["running"]:
            return f"Stopped ({stats['frames']} frames)"
        if stats["dropped"]:
            return f"Recording - {stats['dropped']} dropped"
        return f"Recording - {stats['frames']} frames"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self._recorder.stats())


class XPengMqttDiscoverySensor(XPengEntity, SensorEntity):
    """Shows MQTT topic discovery results."""

    _attr_has_entity_name = True
    _attr_translation_key = "mqtt_discovery"
    _attr_icon = "mdi:magnify"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_mqtt_discovery"

    @property
    def native_value(self) -> str | None:
        accepted = len(self.coordinator.listener_accepted_topics)
        rejected = len(self.coordinator.listener_rejected_topics)
        total = accepted + rejected
        if total == 0:
            return "No discovery"
        return f"{accepted} accepted / {rejected} rejected"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "accepted_count": len(self.coordinator.listener_accepted_topics),
            "rejected_count": len(self.coordinator.listener_rejected_topics),
            "accepted_topics": self.coordinator.listener_accepted_topics,
            "rejected_topics": self.coordinator.listener_rejected_topics,
            "results": self.coordinator.listener_discovery_results,
        }


class XPengMaintenanceSensor(XPengEntity, SensorEntity):
    """Shows next maintenance info from REST API."""

    _attr_has_entity_name = True
    _attr_translation_key = "maintenance"
    _attr_icon = "mdi:car-wrench"

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_maintenance"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.maintenance_data
        if not data:
            return None
        days = data.get("nextMaintenanceDays")
        if days is not None:
            return f"{days} days"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.maintenance_data
        if not data:
            return {}
        attrs: dict[str, Any] = {}
        if data.get("nextMaintenanceDate"):
            attrs["next_date"] = data["nextMaintenanceDate"]
        if data.get("nextMaintenanceDays") is not None:
            attrs["days_remaining"] = data["nextMaintenanceDays"]
        if data.get("nextLeftMileage") is not None:
            attrs["km_remaining"] = data["nextLeftMileage"]
        if data.get("rangeDistance") is not None:
            attrs["range_distance"] = data["rangeDistance"]
        if data.get("rangeDays") is not None:
            attrs["range_days"] = data["rangeDays"]
        if data.get("url"):
            attrs["booking_url"] = data["url"]
        return attrs


class XPengXComboSensor(XPengEntity, SensorEntity):
    """Shows X-Combo (smart automation) scenes from REST API."""

    _attr_has_entity_name = True
    _attr_translation_key = "xcombos"
    _attr_icon = "mdi:robot-outline"

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_xcombos"

    @property
    def native_value(self) -> str | None:
        scenes = self.coordinator.xcombos
        if scenes is None:
            return None
        active = sum(1 for s in scenes if s.get("status") == "RUNNABLE")
        return f"{active} active"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        scenes = self.coordinator.xcombos
        if not scenes:
            return {}
        scene_list = []
        names = []
        for s in scenes:
            name = s.get("name", "Unknown")
            status = s.get("status", "Unknown")
            scene_id = str(s.get("sceneId") or s.get("id") or "")
            names.append(name)
            scene_list.append({
                "id": scene_id,
                "name": name,
                "status": status,
                "active": status == "RUNNABLE",
                "type": s.get("sceneType", ""),
                "description": s.get("desc", ""),
                "icon": s.get("sceneIcon", ""),
            })
        attrs: dict[str, Any] = {
            "scene_count": len(scenes),
            "scene_names": ", ".join(names),
            "scenes": scene_list,
        }
        # Flat attributes for each scene (1-indexed) with id for service calls
        for i, s in enumerate(scene_list, 1):
            status_icon = "on" if s["status"] == "RUNNABLE" else "off"
            attrs[f"scene_{i}"] = f"{s['name']} ({status_icon})"
            attrs[f"scene_{i}_id"] = s["id"]
            attrs[f"scene_{i}_name"] = s["name"]
            attrs[f"scene_{i}_active"] = s["active"]
        return attrs


class XPengParkedConsumptionSensor(XPengEntity, SensorEntity):
    """Estimated energy drained while parked and not charging, reset daily.

    XPENG only reports SOC to whole percent and pushes rarely while the car
    sleeps, so this is a coarse standby-drain estimate: each observed 1% drop
    while parked & idle is converted to kWh using the configured usable pack
    size. Driving/charging is excluded. The accumulator zeroes at local
    midnight; the previous day's total is kept in ``yesterday_kwh``.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "parked_consumption"
    _attr_icon = "mdi:battery-clock"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = "kWh"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_parked_consumption"

    async def async_added_to_hass(self) -> None:
        """Register the local-midnight rollover timer."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._handle_midnight, hour=0, minute=0, second=5
            )
        )

    @callback
    def _handle_midnight(self, now: datetime) -> None:
        if self.coordinator.reset_parked_consumption_daily():
            self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return round(self.coordinator.parked_consumption_kwh, 3)

    @property
    def last_reset(self) -> datetime | None:
        return self.coordinator.parked_consumption_last_reset

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        yesterday = c.parked_consumption_yesterday_kwh
        return {
            "soc_drop_percent_today": round(c.parked_soc_drop_pct, 2),
            "yesterday_kwh": (
                round(yesterday, 3) if yesterday is not None else None
            ),
            "usable_battery_kwh": c.usable_battery_kwh,
            "day": (
                c.parked_consumption_day.isoformat()
                if c.parked_consumption_day
                else None
            ),
            "tracking": c.vehicle_parked is True and not c.is_charging,
        }


class XPengCommandFeedbackSensor(XPengEntity, SensorEntity):
    """Diagnostic sensor showing the last MQTT command response."""

    _attr_has_entity_name = True
    _attr_translation_key = "command_feedback"
    _attr_icon = "mdi:message-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_command_feedback"

    @property
    def native_value(self) -> str | None:
        fb = self.coordinator.last_command_feedback
        if not fb:
            return None
        return command_response_message(fb.get("code"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        fb = self.coordinator.last_command_feedback
        if not fb:
            return {"count": 0}
        return {
            "count": self.coordinator.command_feedback_count,
            "service_type": fb.get("service_type"),
            "msg_type": fb.get("msg_type"),
            "code": fb.get("code"),
            "message": fb.get("message"),
            "msg_id": fb.get("msg_id"),
            "timestamp": fb.get("timestamp"),
            "recent": list(self.coordinator.command_feedbacks)[-5:],
        }


class XPengNotificationSensor(XPengEntity, SensorEntity):
    """Diagnostic sensor showing the most recent XPENG notification.

    Two sources feed this, and they are different things:

    1. `msg_type=3` frames on the MQTT broker, which also fire the
       `xpeng_notification` bus event. Rare.
    2. The app's server-side **Message Centre**, which the coordinator polls.
       This is where the notifications a user actually sees in the app live -
       X-Combo, sentry and similar - and it is the source that carries almost
       everything in practice.

    XPENG does not push these over MQTT and they are not APNs/FCM-only either:
    the message centre holds them and the app reads them back, which makes them
    pollable. That is what `NOTIFICATION_POLL_INTERVAL` drives.

    The bus event is fired for both sources, so an automation can trigger on
    either the event or this sensor's state without caring where it came from.
    `count` stays at 0 until something new arrives after startup, by design -
    the first poll primes a baseline instead of replaying the whole inbox.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "last_notification"
    _attr_icon = "mdi:bell-ring"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_last_notification"

    @property
    def _last(self) -> dict[str, Any] | None:
        notes = getattr(self.coordinator, "notifications", None)
        return notes[-1] if notes else None

    @property
    def native_value(self) -> str | None:
        note = self._last
        if not note:
            return None
        msg = note.get("message")
        if msg:
            return str(msg)
        code = note.get("code")
        if code is not None:
            return f"code {code}"
        return "received"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        notes = getattr(self.coordinator, "notifications", None) or []
        attrs: dict[str, Any] = {
            "count": getattr(self.coordinator, "notification_count", 0),
            "recent": list(notes)[-5:],
        }
        note = self._last
        if note:
            # The delay is the point of this sensor for the messages it carries:
            # a poll cannot beat its own interval, and knowing the real number is
            # what tells you whether tuning it would help. `collected_at` is when
            # we saw it, `timestamp` when XPENG says it happened.
            attrs.update(
                {
                    "service_type": note.get("service_type"),
                    "msg_id": note.get("msg_id"),
                    "code": note.get("code"),
                    "message": note.get("message"),
                    "timestamp": note.get("timestamp"),
                    "source": note.get("source"),
                    "delay_s": poll_status.notification_delay_seconds(
                        note.get("timestamp"), time.time()
                    ),
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        return attrs


class XPengOtaUpgradeSensor(XPengEntity, SensorEntity):
    """Shows OTA upgrade availability and details from REST API."""

    _attr_has_entity_name = True
    _attr_translation_key = "ota_upgrade"
    _attr_icon = "mdi:update"

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_ota_upgrade"

    @property
    def native_value(self) -> str | None:
        info = self.coordinator.ota_upgrade_info
        if not info:
            ver = self.coordinator.ota_version_info
            if ver and ver.get("osVersion"):
                return f"Current: {ver['osVersion']}"
            return None
        version = info.get("osVersion")
        if version:
            return f"Available: {version}"
        return "Up to date"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        # Current version info
        ver = self.coordinator.ota_version_info
        if ver:
            if ver.get("osVersion"):
                attrs["current_version"] = ver["osVersion"]
            if ver.get("osName"):
                attrs["os_name"] = ver["osName"]
            if ver.get("releaseNotesUrl"):
                attrs["release_notes_url"] = ver["releaseNotesUrl"]

        # Upgrade info
        info = self.coordinator.ota_upgrade_info
        if info:
            if info.get("osVersion"):
                attrs["available_version"] = info["osVersion"]
            if info.get("estimateCostMinutes"):
                attrs["estimated_minutes"] = info["estimateCostMinutes"]
            if info.get("releaseNotesUrl"):
                attrs["upgrade_notes_url"] = info["releaseNotesUrl"]
            if info.get("supportCancellingSchedule") is not None:
                attrs["can_cancel_schedule"] = info["supportCancellingSchedule"]
            if info.get("cduVersion"):
                attrs["cdu_version"] = info["cduVersion"]

        # Auto-upgrade status
        auto = self.coordinator.ota_auto_upgrade_status
        if auto:
            status_map = {0: "disabled", 1: "enabled", 2: "dispatching"}
            raw = auto.get("status")
            if raw is not None:
                attrs["auto_upgrade"] = status_map.get(int(raw), str(raw))
            if auto.get("pendingXosVersion"):
                attrs["pending_version"] = auto["pendingXosVersion"]

        return attrs


class XPengBetaSensor(XPengEntity, SensorEntity):
    """Shows beta program availability from REST API."""

    _attr_has_entity_name = True
    _attr_translation_key = "beta_program"
    _attr_icon = "mdi:flask-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_beta_program"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.beta_popup
        if not data:
            return None
        popup_type = data.get("popupType")
        if popup_type is None:
            return "No data"
        type_map = {1: "Upgrade available", 2: "Beta test", 3: "Mandatory update"}
        return type_map.get(int(popup_type), f"Type {popup_type}")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.beta_popup
        if not data:
            return {}
        attrs: dict[str, Any] = {}
        for key in ("popupType", "title", "content", "version",
                     "url", "buttonText", "cancelText"):
            if data.get(key) is not None:
                attrs[key] = data[key]
        return attrs


class XPengRemoteCapabilitiesSensor(XPengEntity, SensorEntity):
    """Shows vehicle remote capabilities from REST API."""

    _attr_has_entity_name = True
    _attr_translation_key = "remote_capabilities"
    _attr_icon = "mdi:car-cog"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_remote_capabilities"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.remote_capabilities
        if not data:
            return None
        caps = data.get("capabilities") if isinstance(data, dict) else data
        if isinstance(caps, list):
            return f"{len(caps)} capabilities"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.remote_capabilities
        if not data:
            return {}
        caps = data.get("capabilities") if isinstance(data, dict) else data
        if not isinstance(caps, list):
            return {"raw": data}
        from .vehicle_capabilities import from_coordinator

        features = from_coordinator(self.coordinator)
        return {
            "count": len(caps),
            "capabilities": caps,
            "hardware": features.hardware,
            "model_configuration": features.local,
            "feature_support": features.decisions(),
        }


class XPengVehicleExtensionSensor(XPengEntity, SensorEntity):
    """Shows vehicle extension messages (alerts/notifications) from REST API."""

    _attr_has_entity_name = True
    _attr_translation_key = "vehicle_extension"
    _attr_icon = "mdi:message-alert-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_vehicle_extension"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.vehicle_extension
        if data is None:
            return None
        if isinstance(data, list):
            return f"{len(data)} messages"
        if isinstance(data, dict):
            count = len(data.get("messages", data.get("list", [])))
            if count:
                return f"{count} messages"
            return "No messages"
        return str(data)[:50]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.vehicle_extension
        if not data:
            return {}
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"messages": data, "count": len(data)}
        return {"raw": str(data)}


class XPengLeftComfortPreonSensor(XPengEntity, SensorEntity):
    """Seconds remaining for XPENG's left-comfort preon (keep-climate) mode."""

    _attr_has_entity_name = True
    _attr_translation_key = "left_comfort_preon_seconds"
    _attr_icon = "mdi:fan-clock"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_left_comfort_preon_seconds"

    @property
    def native_value(self) -> int | None:
        secs = self.coordinator.left_comfort_preon_seconds
        if secs is None:
            return None
        return max(0, int(secs))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        secs = self.coordinator.left_comfort_preon_seconds
        if secs is None:
            return {"active": False}
        return {"active": secs > 0}


def _decode_schedule_bitmask(mask: Any) -> list[str]:
    if not isinstance(mask, int):
        return []
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return [d for i, d in enumerate(days) if mask & (1 << i)]


class XPengHvacSchedulesSensor(XPengEntity, SensorEntity):
    """Count of HVAC scheduled appointments + full list as attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "hvac_schedules"
    _attr_icon = "mdi:calendar-clock"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_hvac_schedules"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.hvac_schedules)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        decoded = []
        for item in self.coordinator.hvac_schedules:
            if not isinstance(item, dict):
                continue
            decoded.append({
                "appointment_id": item.get("appointmentId"),
                "start_time": item.get("startTime"),
                "cycle_type": item.get("cycleType"),
                "days": _decode_schedule_bitmask(item.get("schedule")),
                "schedule_bitmask": item.get("schedule"),
                "is_valid": bool(item.get("isValid")),
                "action_type": item.get("actionType"),
            })
        return {
            "schedules": decoded,
            "next_start_time": self.coordinator.hvac_next_start_time,
        }


class XPengCduIdSensor(XPengEntity, SensorEntity):
    """CDU (head-unit) hardware identifier from vehicleCdu.cduId."""

    _attr_has_entity_name = True
    _attr_translation_key = "cdu_id"
    _attr_icon = "mdi:identifier"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_cdu_id"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.cdu_id

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"parts_no_eight2ten": self.coordinator.cdu_parts_no_eight2ten}


_WEATHER_CONDITION_MAP = {
    "晴": "Sunny",
    "多云": "Cloudy",
    "阴": "Overcast",
    "阵雨": "Showers",
    "雷阵雨": "Thunderstorm",
    "小雨": "Light rain",
    "中雨": "Moderate rain",
    "大雨": "Heavy rain",
    "暴雨": "Rainstorm",
    "雨": "Rain",
    "小雪": "Light snow",
    "中雪": "Moderate snow",
    "大雪": "Heavy snow",
    "雪": "Snow",
    "雾": "Fog",
    "霾": "Haze",
    "大风": "Windy",
    "优": "Excellent",
    "良": "Good",
}


class XPengWeatherSensor(XPengEntity, SensorEntity):
    """Weather at the vehicle location (XPENG assistant service)."""

    _attr_has_entity_name = True
    _attr_translation_key = "weather"
    _attr_icon = "mdi:weather-partly-cloudy"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_weather"

    @property
    def native_value(self) -> float | None:
        w = self.coordinator.weather
        if not w:
            return None
        try:
            return float(w.get("realtimeTemperature"))
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        w = self.coordinator.weather or {}
        cond = w.get("weatherType")
        aqi_type = w.get("realtimeAqiType")
        return {
            "condition": _WEATHER_CONDITION_MAP.get(cond, cond),
            "condition_raw": cond,
            "temp_min": w.get("temperatureMin"),
            "temp_max": w.get("temperatureMax"),
            "real_feel": w.get("realFeelTemperature"),
            "humidity": w.get("realtimeHumidity"),
            "aqi": w.get("realtimeAqi"),
            "aqi_rating": _WEATHER_CONDITION_MAP.get(aqi_type, aqi_type),
            "cloud_cover": w.get("cloudrate"),
            "wind_direction": w.get("windDirection"),
            "wind_speed": w.get("windSpeed"),
            "pressure": w.get("pressure"),
            "visibility": w.get("visibility"),
            "sunrise": w.get("astroSunriseTime"),
            "sunset": w.get("astroSunsetTime"),
        }


class XPengIccidSensor(XPengEntity, SensorEntity):
    """TBox SIM ICCID, from the manual config option (no app-tier API source)."""

    _attr_has_entity_name = True
    _attr_translation_key = "iccid"
    _attr_icon = "mdi:sim"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_iccid"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.iccid


class XPengCduPartitionSensor(XPengEntity, SensorEntity):
    """Raw cduAOrBFlag from vehicleCdu — semantics not defined in source."""

    _attr_has_entity_name = True
    _attr_translation_key = "cdu_partition"
    _attr_icon = "mdi:harddisk"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_cdu_partition"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.cdu_a_or_b_flag


class XPengVehicleIdentitySensor(XPengEntity, SensorEntity):
    """Vehicle identity: offline VIN decode + the manufacturer's own report.

    The state prefers the cloud API's ``seriesName`` because that is the only
    authoritative source for the series/trade name. The VIN's positions 4-8
    identify the model *family* only — trim, battery and drivetrain are NOT
    encoded in a XPENG VIN, so those are deliberately never inferred here.
    All VIN-derived fields are exposed as attributes for cross-checking.

    The full VIN is intentionally NOT exposed as an attribute; use
    ``vin_masked``. The device page already shows the last 6 characters.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "vehicle_identity"
    _attr_icon = "mdi:car-info"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XPengDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"xpeng_{self._vin}_vehicle_identity"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.series_name:
            return self.coordinator.series_name
        info = self.coordinator.vin_info
        return info.display_name if info is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            # Manufacturer self-report — authoritative for series/trim.
            "series_name": self.coordinator.series_name,
            "series_code": self.coordinator.series_code,
            "vehicle_type_name": self.coordinator.vehicle_type_name,
            "nick_name": self.coordinator.nick_name,
            "vehicle_type_code": self.coordinator.vehicle_type_code,
            "diagram_version": self.coordinator.diagram_version,
        }

        info = self.coordinator.vin_info
        if info is None:
            attrs["vin_decode_available"] = False
            return attrs

        attrs.update(
            {
                "vin_decode_available": True,
                "vin_masked": mask_vin(info.vin),
                "wmi": info.wmi,
                "manufacturer": info.manufacturer,
                "vds": info.vds,
                "model_family": info.model_family,
                "body_style": info.body_style,
                "powertrain": info.powertrain,
                "miit_model_code": info.miit_model_code,
                "model_year": info.model_year,
                "model_year_code": info.model_year_code,
                "plant": info.plant,
                "plant_country": info.plant_country,
                "plant_confidence": info.plant_confidence,
                "vin_check_digit_valid": info.check_digit_valid,
                "identity_confidence": info.confidence,
                "vin_table_version": info.table_version,
            }
        )
        if info.vds_evidence:
            attrs["vds_evidence"] = list(info.vds_evidence)
        if info.warnings:
            attrs["vin_warnings"] = list(info.warnings)
        return attrs
