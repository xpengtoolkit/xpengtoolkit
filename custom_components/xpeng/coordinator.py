"""Data coordinator for the XPENG integration.

This coordinator is a PASSIVE data store. It does NOT override
_async_update_data or manage the MQTT connection. Instead:
- The MQTT client pushes data via set_vehicle_data() from its network thread
- The integration sets up a periodic timer to send queries
- The coordinator just holds the latest full vehicle state
"""
from __future__ import annotations

import logging
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .command_policy import CommandPolicy, default_policy
from .const import DOMAIN
from .drive_evidence import view as drive_view
from .replay_guard import is_replay, odometer_from_state, odometer_from_widget
from .vin_descriptor import decode as decode_vin, mask_vin

_LOGGER = logging.getLogger(__name__)

# Maximum number of log entries to keep in the rolling buffer
_LOG_BUFFER_SIZE = 50

# Persisted-state store: lets sensors show their last known values after an
# HA restart WITHOUT contacting (and potentially waking) the car. The car's
# own next push overwrites this. Bumped version only on incompatible shape.
_STATE_STORE_VERSION = 1
# Debounce disk writes — vehicle data can arrive many times a minute while
# charging or driving.
_STATE_SAVE_DELAY = 30

# Parked-drain plausibility limits — reject SOC steps that carry driving energy.
_MAX_PARKED_DRAIN_RATE_PCT_PER_H = 3.0
_MAX_PARKED_DRAIN_STEP_PCT = 12.0

# `instant.*` sections the REST widget fallback must NEVER merge. The fallback
# exists only to keep *energy* fields fresh while MQTT's push-fanout is stuck
# (see _merge_widget_state_if_mqtt_stale). Body-panel state is a different
# animal: the car pushes it reliably over MQTT whenever it is awake, and the
# REST widget reports it on a different scale (0/1 rather than the 0-100 MQTT
# uses). Merging it overwrote good MQTT positions, so a parked, asleep car
# briefly asserted `any_window_open` until the next frame or reconnect
# restored the truth (observed 2026-10-06 22:20:52 -> 22:21:08, while the
# silence watchdog was reporting state_silence=474s).
_REST_MERGE_EXCLUDED_SECTIONS = frozenset(
    {"window", "door", "ext", "trunkPower"}
)

#: The charge subsystem's ``service_type``. Its command responses carry the
#: ``instant.charge`` fields themselves (measured on a live car 2026-10-13), so
#: the acknowledgement of e.g. ``set_charge_limit`` is also the confirmation
#: that the limit moved - see ``_merge_command_state``.
CHARGE_SERVICE_TYPE = 4

#: The seat subsystem's ``service_type``, and the same story one section over: every
#: ``service_type 30`` message carries a flat seat snapshot in its ``msg_content``
#: (measured 2026-10-14 - see ``docs/SEAT_COMMANDS.md``), whose keys are exactly the ones
#: ``instant.seat`` holds. Until this was merged, those messages were logged and dropped,
#: so a seat switched on from XPENG's app kept reading ``Off`` in Home Assistant and
#: could not be switched off again: the select saw no change, and re-picking ``Off`` is
#: not a change. That is the bug reported on 0.4.31.
SEAT_SERVICE_TYPE = 30

#: Which ``instant`` section a command response's keys belong in, by service type. The
#: response is a status report, not an echo of the request, so its keys land in the same
#: place the sensors read.
COMMAND_STATE_SECTIONS = {
    3: "hvac",  # Climate replies carry the measured HVAC snapshot, not an echo.
    CHARGE_SERVICE_TYPE: "charge",
    SEAT_SERVICE_TYPE: "seat",
    12: "window",
}

#: How often the MQTT listener may fire its event and refresh entities, in seconds.
#: Every message still lands in the counter and the rolling log; this bounds only the
#: expensive half, so that pointing the listener at a firehose degrades the diagnostics
#: instead of Home Assistant - see ``set_listener_message``.
LISTENER_UPDATE_INTERVAL = 1.0


class XPengDataCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Passive coordinator that stores vehicle data pushed from MQTT."""

    def __init__(
        self,
        hass: HomeAssistant,
        vin: str,
        command_policy: CommandPolicy | None = None,
    ) -> None:
        """Initialize the coordinator (no update_interval — push only)."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"XPENG {vin}",
            # No update_interval: we are push-based, not poll-based
        )
        self.vin = vin
        # What this entry is allowed to do to the car (command_policy.py). It
        # lives here because every command entity already holds a coordinator,
        # and an entity command method needs the policy without having to know
        # its own config entry id. Rebuilt from the entry's options on every
        # reload, so a mode change takes effect with the reload it triggers.
        self.command_policy = command_policy or default_policy()
        # What the pollers are doing, for the polling-status sensors. Never
        # pushed through async_set_updated_data: that would present diagnostics
        # as a fresh reading to the REST-merge and staleness logic, and a 60s
        # tick would wake every entity for a number none of them use.
        self.poll_status: dict[str, Any] = {}
        self._last_full_data: dict[str, Any] | None = None
        #: Highest odometer ever accepted from either source. ``total_mileage``
        #: only increases, so a state below this is a replay of an older
        #: snapshot - see replay_guard.py for the live measurement that made
        #: this necessary (2026-10-21: a wedged fanout replayed the pre-drive
        #: state for 2.5h and overwrote the REST fallback's fresher values).
        self.max_odometer: float | None = None
        #: Epoch until which an external charger says a session may be running.
        self._charging_hint_until: float = 0.0
        self.credential_timestamp: datetime | None = None
        # When a token + tenant pair was last *installed* — i.e. a renewal — as
        # opposed to credential_timestamp above, which moves every
        # mqtt_refresh_interval because the broker's short-lived credentials are
        # re-minted. Populated from entry data (CONF_LAST_RENEWAL) so it survives a
        # restart, and read by the last-renewal sensor.
        self.last_renewal: datetime | None = None
        self.last_data_timestamp: datetime | None = None
        # Distinct from last_data_timestamp: only bumped when a message
        # actually carried fresh charge/odometer data, not just any MQTT
        # traffic (partial pings for door/window/ext must not mask staleness
        # of the energy fields — see _merge_widget_state_if_mqtt_stale).
        self.last_energy_data_timestamp: datetime | None = None
        #: When the last accepted state showed the car *moving* (speed, or a
        #: shift other than P). A frame that shows P resets it to 0.0. The
        #: watchdog reads this to justify an escalation a force-upload cannot
        #: turn into a wake - see watchdog_policy.should_escalate_moving and
        #: drive_evidence.py.
        self.last_motion_at: float = 0.0
        # True while the only state we hold was restored from disk (no live
        # push received yet this session).
        self.state_is_restored: bool = False
        self._state_store: Store = Store(
            hass, _STATE_STORE_VERSION, f"{DOMAIN}_state_{vin}"
        )
        # REST API polled data
        self._rest_data: dict[str, Any] = {}
        self.vehicle_image_url: str | None = None
        # The whole `vehicleAppearance` block, not just the picture we picked.
        # The official app renders the car's own trim (wheels, colour) and this
        # entity cannot always match it; keeping the raw block is what makes the
        # difference diagnosable instead of guesswork - it goes into snapshots.
        self.vehicle_appearance: dict[str, Any] = {}
        self.xos_version: str | None = None
        self.vehicle_type_code: str | None = None
        # Manufacturer self-reported identity, from the v2/myVehicles response.
        # This is the AUTHORITATIVE source for series/trim — a XPENG VIN does
        # not encode trim, battery or drivetrain (see vin_descriptor.py).
        self.series_name: str | None = None
        self.series_code: str | None = None
        self.vehicle_type_name: str | None = None
        self.nick_name: str | None = None
        self.diagram_version: Any = None
        # Offline VIN decode: model family, model year, plant, check digit.
        # Computed once here — none of it needs the car or the cloud, so it is
        # available even before the first REST poll or MQTT frame.
        try:
            self.vin_info: Any = decode_vin(vin)
            _LOGGER.debug(
                "VIN decode %s -> %s (MY%s, plant %s, check digit %s)",
                mask_vin(vin),
                self.vin_info.display_name,
                self.vin_info.model_year,
                self.vin_info.plant,
                self.vin_info.check_digit_valid,
            )
            if self.vin_info.warnings:
                _LOGGER.debug("VIN decode notes: %s", list(self.vin_info.warnings))
        except Exception:  # pragma: no cover — decoding must never break setup
            _LOGGER.warning("VIN decode failed for %s", mask_vin(vin), exc_info=True)
            self.vin_info = None
        # Maintenance data
        self.maintenance_data: dict[str, Any] | None = None
        # X-Combo scenes
        self.xcombos: list[dict[str, Any]] | None = None
        # Energy report
        self.energy_report: dict[str, Any] | None = None
        # Weather at vehicle location (assistant service)
        self.weather: dict[str, Any] | None = None
        # REST supplementary state
        self.remote_capabilities: Any = None
        self.vehicle_hardware: dict[str, bool] = {}
        self.vehicle_local_config: dict[str, bool] = {}
        self.vehicle_extension: dict[str, Any] | None = None
        # OTA data
        self.ota_upgrade_info: dict[str, Any] | None = None
        self.ota_auto_upgrade_status: dict[str, Any] | None = None
        self.ota_version_info: dict[str, Any] | None = None
        # Last seen non-zero upgradeId — used to detect new campaign enrolment
        self._last_seen_upgrade_id: int | None = None
        # Beta
        self.beta_popup: dict[str, Any] | None = None
        # Left-comfort preon countdown (seconds remaining; 0 = inactive)
        self.left_comfort_preon_seconds: int | None = None
        # HVAC scheduled appointments (from /v5/appointments/aircondition/list)
        self.hvac_schedules: list[dict[str, Any]] = []
        self.hvac_next_start_time: str | None = None
        # CDU identity (from vehicleCdu in v2/myVehicles response)
        self.cdu_id: str | None = None
        self.cdu_a_or_b_flag: int | None = None
        self.cdu_parts_no_eight2ten: str | None = None
        # TBox SIM ICCID — not available via app-tier API; set from a
        # manual config option (read on-device via *#9925*131#* / getprop).
        self.iccid: str | None = None
        # MQTT Listener data
        self.listener_last_topic: str | None = None
        self.listener_last_payload: str | None = None
        self.listener_last_timestamp: datetime | None = None
        self.listener_message_count: int = 0
        # Monotonic stamp of the last listener event/refresh, so a burst costs one.
        self._last_listener_update: float = 0.0
        self.listener_discovery_results: dict[str, dict[str, Any]] = {}
        self.listener_accepted_topics: list[str] = []
        self.listener_rejected_topics: list[str] = []
        # Rolling log buffer for live log screen
        self.listener_log: deque[str] = deque(maxlen=_LOG_BUFFER_SIZE)
        # Push notifications (msg_type=3 + REST poll)
        self.notifications: deque[dict[str, Any]] = deque(maxlen=_LOG_BUFFER_SIZE)
        self.notification_count: int = 0
        # Track seen REST notification IDs to detect new ones on each poll
        self._seen_rest_notification_ids: set[str] = set()
        self._rest_notifications_primed: bool = False
        # Command feedback (service_type != 19 responses)
        self.command_feedbacks: deque[dict[str, Any]] = deque(maxlen=10)
        self.last_command_feedback: dict[str, Any] | None = None
        self.command_feedback_count: int = 0

        # --- Parked consumption estimate (vampire drain while parked & idle) ---
        # Usable pack size for the SOC%→kWh conversion; overridden from options
        # by __init__.py after construction.
        self.usable_battery_kwh: float = 87.5
        # SOC (%) and time of the last sample taken while parked & not charging.
        # Cleared whenever the car drives or charges so those SOC changes are
        # never attributed to parked drain.
        self._parked_ref_soc: float | None = None
        self._parked_ref_time: datetime | None = None
        self._widget_soc_logged: bool = False
        # Accumulators for the current local day.
        _today = datetime.now().date()
        self.parked_consumption_day: date | None = _today
        self.parked_consumption_kwh: float = 0.0
        self.parked_soc_drop_pct: float = 0.0
        self.parked_consumption_yesterday_kwh: float | None = None
        self.parked_consumption_last_reset: datetime | None = self._day_start(_today)

    async def async_load_cached(self) -> None:
        """Restore the last vehicle state persisted before the last shutdown.

        Called once during setup, before entities are added. This does NOT
        touch the car — it just means sensors show their last known values
        instead of "unknown" after a restart while the car is asleep. Any
        live MQTT push (or REST widget merge) immediately supersedes it.
        """
        try:
            stored = await self._state_store.async_load()
        except Exception:
            _LOGGER.warning("Failed to load cached vehicle state", exc_info=True)
            return
        if not stored or not isinstance(stored, dict):
            return
        pc = stored.get("parked_consumption")
        if isinstance(pc, dict):
            self._restore_parked_consumption(pc)
        data = stored.get("data")
        if not isinstance(data, dict):
            return
        self._last_full_data = data
        self.data = data
        # The restored odometer is a real floor: any push below it is a replay of
        # a snapshot older than the one this install already knew about.
        self.max_odometer = odometer_from_state(data)
        self.state_is_restored = True
        ts = stored.get("ts")
        if ts:
            try:
                self.last_data_timestamp = datetime.fromisoformat(ts)
            except ValueError:
                self.last_data_timestamp = None
        _LOGGER.info(
            "Restored cached vehicle state from %s (car not contacted)", ts,
        )

    @callback
    def _persist_state(self) -> None:
        """Queue a debounced write of the current full state to disk."""
        self._state_store.async_delay_save(
            lambda: {
                "data": self._last_full_data,
                "ts": (
                    self.last_data_timestamp.isoformat()
                    if self.last_data_timestamp
                    else None
                ),
                "parked_consumption": {
                    "day": (
                        self.parked_consumption_day.isoformat()
                        if self.parked_consumption_day
                        else None
                    ),
                    "kwh": round(self.parked_consumption_kwh, 4),
                    "soc_drop": round(self.parked_soc_drop_pct, 3),
                    "yesterday": self.parked_consumption_yesterday_kwh,
                    # Deliberately NOT persisting the SOC reference — a restored
                    # ref could be pre-drive while the first post-restart sample
                    # is post-drive, and the whole delta would be miscounted as
                    # parked drain. Re-seed from the first fresh sample instead.
                },
            },
            _STATE_SAVE_DELAY,
        )

    def set_vehicle_data(self, data: dict[str, Any]) -> bool:
        """Receive full vehicle state from the MQTT thread.

        This is called via hass.loop.call_soon_threadsafe from the MQTT
        network thread, so it runs on the HA event loop.

        Returns False when the state is a replay of an older snapshot, in which
        case nothing is stored. The MQTT client uses that verdict to keep the
        message out of the "data arrived" clock, so a wedged fanout that only
        replays an old state still ages towards the watchdog's force-upload.
        """
        has_fresh_energy_data = data.pop("has_fresh_energy_data", False)
        odometer = odometer_from_state(data)
        if is_replay(odometer, self.max_odometer):
            _LOGGER.warning(
                "Ignoring a replayed vehicle state: odometer %s km is behind the "
                "%s km already held (%s) — the push fanout is resending an older "
                "snapshot; keeping the newer data",
                odometer, self.max_odometer,
                "from the REST fallback" if self._rest_data else "from an earlier push",
            )
            return False
        if odometer is not None:
            self.max_odometer = max(self.max_odometer or odometer, odometer)
        self._last_full_data = data
        self.last_data_timestamp = datetime.now(timezone.utc)
        # Before _update_parked_consumption, which reads vehicle_parked; and
        # before the entities are notified, so a transition is logged with the
        # evidence that caused it (issue #3 diagnostics).
        self.note_drive_evidence(data)
        if has_fresh_energy_data:
            self.last_energy_data_timestamp = self.last_data_timestamp
        self.state_is_restored = False
        # Update the parked-drain estimate before notifying entities (reads the
        # freshly-set _last_full_data via vehicle_parked / is_charging).
        self._update_parked_consumption(data)
        # async_set_updated_data notifies all listening entities
        self.async_set_updated_data(data)
        self._persist_state()
        _LOGGER.debug("Coordinator updated with fresh vehicle data")
        return True

    def set_credential_timestamp(self) -> None:
        """Record when credentials were last set/refreshed."""
        self.credential_timestamp = datetime.now(timezone.utc)

    def set_last_renewal(self, epoch: Any) -> None:
        """Record when a token + tenant pair was last installed.

        Takes the raw entry-data value: epoch seconds written by whatever path
        installed the pair, or ``None``/"" on an entry that predates the key (or was
        never renewed). An unparseable value becomes ``None`` rather than raising -
        a diagnostic entity must not be able to fail entry setup.
        """
        try:
            self.last_renewal = (
                datetime.fromtimestamp(float(epoch), tz=timezone.utc)
                if epoch not in (None, "")
                else None
            )
        except (TypeError, ValueError):
            self.last_renewal = None

    def set_poll_status(self, **fields: Any) -> None:
        """Record what the pollers are doing.

        A merge, not a replacement: each timer knows only its own schedule and
        updates its own keys, so replacing the dict would erase what the other
        two just reported. Nothing is notified — the polling sensors poll.
        """
        self.poll_status.update(fields)

    # ------------------------------------------------------------------
    # Parked consumption estimate
    # ------------------------------------------------------------------
    @staticmethod
    def _day_start(day: date) -> datetime:
        """Local midnight of ``day`` as a timezone-aware datetime."""
        local_tz = datetime.now().astimezone().tzinfo
        return datetime(day.year, day.month, day.day, tzinfo=local_tz)

    @callback
    def reset_parked_consumption_daily(self, *_: Any) -> bool:
        """Roll the parked-consumption accumulator over to a new local day.

        Idempotent — safe to call from both the midnight timer and the
        per-push guard. Returns True only when a rollover actually happened.
        """
        today = datetime.now().date()
        if self.parked_consumption_day == today:
            return False
        if self.parked_consumption_day is not None:
            self.parked_consumption_yesterday_kwh = round(
                self.parked_consumption_kwh, 3
            )
        self.parked_consumption_kwh = 0.0
        self.parked_soc_drop_pct = 0.0
        self.parked_consumption_day = today
        self.parked_consumption_last_reset = self._day_start(today)
        self._persist_state()
        _LOGGER.debug("Parked consumption accumulator reset for %s", today)
        return True

    def _restore_parked_consumption(self, pc: dict[str, Any]) -> None:
        """Restore the accumulator from the persisted state store."""
        day: date | None = None
        if pc.get("day"):
            try:
                day = date.fromisoformat(pc["day"])
            except (ValueError, TypeError):
                day = None
        today = datetime.now().date()
        if day == today:
            self.parked_consumption_day = day
            self.parked_consumption_kwh = float(pc.get("kwh") or 0.0)
            self.parked_soc_drop_pct = float(pc.get("soc_drop") or 0.0)
            self.parked_consumption_yesterday_kwh = pc.get("yesterday")
            self.parked_consumption_last_reset = self._day_start(day)
        elif day == today - timedelta(days=1):
            # HA was down since yesterday — carry yesterday's total forward.
            self.parked_consumption_yesterday_kwh = round(
                float(pc.get("kwh") or 0.0), 3
            )
            self.parked_consumption_day = today
            self.parked_consumption_last_reset = self._day_start(today)
        # Anything older is stale — keep the fresh defaults from __init__.

    def _update_parked_consumption(self, data: dict[str, Any]) -> None:
        """Accumulate estimated energy drained while parked and not charging.

        SOC is only reported to whole percent, so this is a coarse estimate:
        each observed 1% drop while parked & idle is converted to kWh via
        ``usable_battery_kwh``. Charging / driving clears the reference so
        only genuine standby drain is counted. Resets at local midnight.
        """
        try:
            soc: float | None = float(
                data["msg_content"]["instant"]["charge"]["battery_soc"]
            )
        except (KeyError, TypeError, ValueError):
            soc = None
        parked = self.vehicle_parked
        if parked is None and soc is None:
            # Stub frame with neither a drive block nor an SOC — tells us
            # nothing. Don't disturb the reference (a widget sample may have
            # just set it).
            return
        self._accumulate_parked_soc(
            soc,
            parked=parked,
            charging=self.is_charging,
            source="mqtt",
        )

    def observe_widget_soc(self, widget: dict[str, Any]) -> None:
        """Feed the parked-drain estimate from the REST widget poll.

        XPENG's MQTT push-fanout stalls for hours at a time, so the widget
        REST poll is often the only live SOC we get. Consume it here directly,
        independent of the widget→MQTT merge's staleness gate.

        The widget ``charge`` block only carries ``battery_soc`` plus port
        statuses — no ``charging_state``/``power``, and there's no ``drive``
        block or ``ext.vehicleMode``. So we can't tell driving or charging
        apart from the widget alone. That's fine: ``_accumulate_parked_soc``
        only ever *adds* on a downward step and just re-baselines on an upward
        step, so an active charge can't inflate the total. For the
        driving/parked split we defer to the MQTT-derived ``vehicle_parked``
        when it is decisive, and otherwise assume parked.
        """
        if not isinstance(widget, dict):
            return
        charge = widget.get("charge") if isinstance(widget.get("charge"), dict) else {}
        try:
            soc: float | None = float(charge.get("battery_soc"))
        except (TypeError, ValueError):
            soc = None
        if not self._widget_soc_logged:
            self._widget_soc_logged = True
            _LOGGER.info(
                "Widget SOC probe: battery_soc=%r charge_keys=%s",
                charge.get("battery_soc"),
                sorted(charge.keys()),
            )
        if soc is None:
            return
        # Respect an explicit "driving" signal from the last real MQTT state;
        # otherwise treat the sample as parked.
        parked = False if self.vehicle_parked is False else True
        self._accumulate_parked_soc(
            soc, parked=parked, charging=False, source="widget"
        )

    def _accumulate_parked_soc(
        self,
        soc: float | None,
        *,
        parked: bool | None,
        charging: bool,
        source: str,
    ) -> None:
        """Shared core: fold one SOC observation into the daily accumulator.

        SOC is only reported to whole percent, so this is a coarse estimate —
        each observed 1% drop while parked & not charging is converted to kWh
        via ``usable_battery_kwh``. Resets at local midnight. ``parked=None``
        means "parked state unknown from this source" — keep the reference and
        only fold in a drop if we have SOC.
        """
        self.reset_parked_consumption_daily()

        if soc is not None and not 0.0 <= soc <= 100.0:
            soc = None

        now = datetime.now(timezone.utc)

        if parked is False or charging:
            # Leaving the parked-idle window (car about to drive, or plugged
            # in). Capture any drain accrued since the last parked sample
            # before dropping the reference — the only chance to count an
            # overnight sleep drain if no idle sample arrived in between, but
            # only if it looks like genuine standby drain (see rate check).
            if not charging and self._parked_ref_soc is not None and soc is not None:
                drain = self._parked_ref_soc - soc
                if 0.0 < drain and self._plausible_parked_drain(drain, now):
                    self.parked_soc_drop_pct += drain
                    self.parked_consumption_kwh += (
                        drain / 100.0
                    ) * self.usable_battery_kwh
            self._parked_ref_soc = None
            self._parked_ref_time = None
            return

        if soc is None:
            return

        if self._parked_ref_soc is None:
            self._parked_ref_soc = soc
            self._parked_ref_time = now
            return

        delta = self._parked_ref_soc - soc
        if delta <= 0:
            # SOC steady, or rose (measurement noise / BMS recalibration while
            # sitting) — never negative energy; just move the reference.
            if delta < 0:
                self._parked_ref_soc = soc
                self._parked_ref_time = now
            return

        if not self._plausible_parked_drain(delta, now):
            # Too fast for standby drain — a drive almost certainly happened
            # between samples (common when the MQTT fanout was stuck so we
            # never saw the car move). Re-baseline without counting it.
            _LOGGER.info(
                "Parked drain (%s): ignoring %.0f%% step over %.1fh — not "
                "plausible standby drain (likely a drive)",
                source,
                delta,
                (now - self._parked_ref_time).total_seconds() / 3600
                if self._parked_ref_time
                else -1,
            )
            self._parked_ref_soc = soc
            self._parked_ref_time = now
            return

        self.parked_soc_drop_pct += delta
        self.parked_consumption_kwh += (delta / 100.0) * self.usable_battery_kwh
        self._parked_ref_soc = soc
        self._parked_ref_time = now
        _LOGGER.debug(
            "Parked drain (%s): -%.1f%% -> +%.3f kWh (day total %.3f kWh)",
            source,
            delta,
            (delta / 100.0) * self.usable_battery_kwh,
            self.parked_consumption_kwh,
        )

    def _plausible_parked_drain(self, drain_pct: float, now: datetime) -> bool:
        """True if a SOC drop is slow enough to be genuine standby drain.

        A parked G6 loses at most ~2%/h even with X-Guard recording; anything
        materially faster means driving energy is mixed in. Without a known
        elapsed time (shouldn't happen once a reference is set) fall back to a
        small absolute cap.
        """
        if drain_pct > _MAX_PARKED_DRAIN_STEP_PCT:
            return False
        ref_time = self._parked_ref_time
        if ref_time is None:
            return drain_pct <= 2.0
        elapsed_h = (now - ref_time).total_seconds() / 3600
        if elapsed_h <= 0:
            return drain_pct <= 2.0
        return (drain_pct / elapsed_h) <= _MAX_PARKED_DRAIN_RATE_PCT_PER_H

    def _drive_coord(self, key: str) -> float | None:
        """Read latitude/longitude from the last MQTT drive payload."""
        try:
            val = self.data["msg_content"]["interval"]["drive"][key]
        except (KeyError, TypeError):
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    @property
    def latitude(self) -> float | None:
        """Vehicle latitude from the last MQTT state, if known."""
        return self._drive_coord("latitude")

    @property
    def longitude(self) -> float | None:
        """Vehicle longitude from the last MQTT state, if known."""
        return self._drive_coord("longitude")

    def set_rest_data(self, data: dict[str, Any]) -> None:
        """Store REST API polled data and notify entities."""
        self._rest_data.update(data)
        if "xos_version" in data:
            new_xos = data["xos_version"]
            old_xos = self.xos_version
            self.xos_version = new_xos
            if old_xos and new_xos and old_xos != new_xos:
                _LOGGER.warning(
                    "XOS version changed: %s -> %s (firing xpeng_xos_version_changed)",
                    old_xos, new_xos,
                )
                self.hass.bus.async_fire(
                    "xpeng_xos_version_changed",
                    {"vin": self.vin, "old_version": old_xos, "new_version": new_xos},
                )
        if "vehicle_image_url" in data:
            self.vehicle_image_url = data["vehicle_image_url"]
        if "vehicle_appearance" in data:
            self.vehicle_appearance = data["vehicle_appearance"]
        if "vehicle_type_code" in data:
            self.vehicle_type_code = data["vehicle_type_code"]
        # Manufacturer identity (v2/myVehicles). series_name is the only
        # reliable source of the trade name / series — never infer it from VIN.
        for _key in (
            "series_name",
            "series_code",
            "vehicle_type_name",
            "nick_name",
            "diagram_version",
        ):
            if _key in data:
                setattr(self, _key, data[_key])
        if "maintenance" in data:
            self.maintenance_data = data["maintenance"]
        if "xcombos" in data:
            self.xcombos = data["xcombos"]
        if "energy_report" in data:
            self.energy_report = data["energy_report"]
        if "weather" in data:
            self.weather = data["weather"]
        if "remote_capabilities" in data:
            self.remote_capabilities = data["remote_capabilities"]
        for key in ("vehicle_hardware", "vehicle_local_config"):
            values = data.get(key)
            if isinstance(values, dict) and values:
                # Partial REST results must not erase previously known flags.
                setattr(self, key, {**getattr(self, key, {}), **values})
        if "vehicle_extension" in data:
            self.vehicle_extension = data["vehicle_extension"]
        if "widget_state" in data:
            # Feed the parked-drain estimate first — it must see the widget SOC
            # even when the merge below decides MQTT data is "fresh" (stub
            # frames defeat that gate for hours at a time).
            self.observe_widget_soc(data["widget_state"])
            self._merge_widget_state_if_mqtt_stale(data["widget_state"])
        if "ota_upgrade_info" in data:
            self.ota_upgrade_info = data["ota_upgrade_info"]
            self._check_ota_campaign_transition(data["ota_upgrade_info"])
        if "ota_auto_upgrade_status" in data:
            self.ota_auto_upgrade_status = data["ota_auto_upgrade_status"]
        if "ota_version_info" in data:
            self.ota_version_info = data["ota_version_info"]
        if "beta_popup" in data:
            self.beta_popup = data["beta_popup"]
        if "left_comfort_preon_seconds" in data:
            self.left_comfort_preon_seconds = data["left_comfort_preon_seconds"]
        if "hvac_schedules" in data:
            self.hvac_schedules = data["hvac_schedules"]
        if "hvac_next_start_time" in data:
            self.hvac_next_start_time = data["hvac_next_start_time"]
        if "cdu_id" in data:
            self.cdu_id = data["cdu_id"]
        if "cdu_a_or_b_flag" in data:
            self.cdu_a_or_b_flag = data["cdu_a_or_b_flag"]
        if "cdu_parts_no_eight2ten" in data:
            self.cdu_parts_no_eight2ten = data["cdu_parts_no_eight2ten"]
        if "rest_notifications" in data:
            self._process_rest_notifications(data["rest_notifications"])
        _LOGGER.debug(
            "REST data updated: xos=%s, image=%s, vtc=%s",
            self.xos_version,
            bool(self.vehicle_image_url),
            self.vehicle_type_code,
        )
        # Re-notify entities so REST-sourced sensors update
        if self._last_full_data:
            self.async_set_updated_data(self._last_full_data)

    def _merge_widget_state_if_mqtt_stale(self, widget: Any) -> None:
        """REST fallback: push widget_state through as if it were an MQTT update.

        XPENG's MQTT push-fanout intermittently goes silent (server-side stuck
        session-routing — see [[project-xpeng-stuck-session-routing]]) while the
        REST tier stays healthy. The widget endpoint returns data in the same
        shape as MQTT `instant.*` so we can merge it into _last_full_data and
        existing sensors will pick it up automatically via their value_paths.
        Only merge when the last MQTT message carrying real charge/odometer
        data is older than 5 min — otherwise MQTT is the source of truth and
        we don't want to overwrite fresher data with the widget's smaller
        field set. Gated on last_energy_data_timestamp, NOT last_data_timestamp:
        a partial ping that only refreshes e.g. door/window/ext still bumps
        last_data_timestamp (correctly, for the silence watchdog) but must not
        be mistaken for a fresh battery/range reading — that masking bug is
        what let battery_soc/range sit hours stale in practice even though
        the connection looked "not stale" the whole time (2026-09-15). Do NOT
        update either timestamp here; the silence watchdog relies on
        last_data_timestamp tracking true MQTT traffic freshness so it can
        still trigger forced reconnects.
        """
        _LOGGER.info(
            "Merge entry: widget_type=%s top_keys=%s last_energy_data_ts=%s",
            type(widget).__name__,
            list(widget.keys())[:8] if isinstance(widget, dict) else None,
            self.last_energy_data_timestamp.isoformat()
            if self.last_energy_data_timestamp else None,
        )
        if not isinstance(widget, dict):
            return
        now = datetime.now(timezone.utc)
        mqtt_stale = (
            self._last_full_data is None
            or self.last_energy_data_timestamp is None
            or (now - self.last_energy_data_timestamp).total_seconds() > 300
        )
        _LOGGER.info(
            "Merge stale check: mqtt_stale=%s _last_full_data=%s",
            mqtt_stale, "set" if self._last_full_data else "None",
        )
        if not mqtt_stale:
            return
        # The fallback is only better than what we hold while it is *newer*.
        # A widget replaying an older snapshot (same wedged upstream) must not
        # overwrite the fresher MQTT state either - same odometer rule, applied
        # in the other direction.
        widget_odometer = odometer_from_widget(widget)
        if is_replay(widget_odometer, self.max_odometer):
            _LOGGER.warning(
                "Not merging the REST widget: its odometer %s km is behind the "
                "%s km already held — it is replaying an older snapshot",
                widget_odometer, self.max_odometer,
            )
            return
        # widget has top-level keys like charge/hvac/door/window/odometer/ext/fuelTank
        widget_sections = {
            k: v for k, v in widget.items()
            if isinstance(v, dict)
            and k != "timer"
            and k not in _REST_MERGE_EXCLUDED_SECTIONS
        }
        skipped = sorted(
            k for k, v in widget.items()
            if isinstance(v, dict) and k in _REST_MERGE_EXCLUDED_SECTIONS
        )
        if skipped:
            _LOGGER.debug(
                "REST fallback: not merging body-panel sections %s "
                "(MQTT is authoritative for these)", skipped,
            )
        if not widget_sections:
            return
        base = dict(self._last_full_data) if self._last_full_data else {}
        msg_content = dict(base.get("msg_content", {}))
        instant = dict(msg_content.get("instant", {}))
        # Merge: widget fields overlay existing instant — existing fields kept
        # if widget doesn't supply them (widget has fewer fields than MQTT)
        for key, val in widget_sections.items():
            existing = instant.get(key)
            if isinstance(existing, dict):
                instant[key] = {**existing, **val}
            else:
                instant[key] = val
        msg_content["instant"] = instant
        merged = {**base, "msg_content": msg_content, "vin": self.vin}
        self._last_full_data = merged
        # The widget is now the freshest source we hold, so its odometer becomes
        # the replay floor for the pushes that follow (that is what stops the
        # stale push from overwriting this merge 1 minute later).
        if widget_odometer is not None:
            self.max_odometer = max(self.max_odometer or widget_odometer, widget_odometer)
        silence = (
            (now - self.last_data_timestamp).total_seconds()
            if self.last_data_timestamp else -1
        )
        _LOGGER.info(
            "Merged widget_state (REST fallback) into vehicle data — MQTT silent for %.0fs",
            silence,
        )
        # Persist so a subsequent restart keeps these values too.
        self._persist_state()
        # The caller (set_rest_data) does async_set_updated_data after this
        # returns, so we don't double-notify.

    def _check_ota_campaign_transition(self, info: Any) -> None:
        """Detect when XPENG enrols the VIN in an OTA campaign.

        getUpgradeInfo returns an error envelope (e.g. code=14000002) while no
        campaign is open. Once XPENG assigns an upgradeId, the same endpoint
        starts returning real data. Fire xpeng_ota_available once on that
        transition so users can be notified instantly instead of waiting for
        the auto-upgrade rollout wave.
        """
        if not isinstance(info, dict):
            return
        # The body may be either the raw response or already-unwrapped data.
        # Real upgrade info has upgradeId, osVersion, releaseNotesUrl, etc.
        # Error envelopes have only code/msg.
        payload = info.get("data") if isinstance(info.get("data"), dict) else info
        upgrade_id = payload.get("upgradeId") if isinstance(payload, dict) else None
        if not isinstance(upgrade_id, int) or upgrade_id <= 0:
            return
        if upgrade_id == self._last_seen_upgrade_id:
            return
        prev = self._last_seen_upgrade_id
        self._last_seen_upgrade_id = upgrade_id
        event_payload = {
            "vin": self.vin,
            "upgradeId": upgrade_id,
            "previousUpgradeId": prev,
            "osVersion": payload.get("osVersion"),
            "osName": payload.get("osName"),
            "releaseNotesUrl": payload.get("releaseNotesUrl"),
            "estimateCostMinutes": payload.get("estimateCostMinutes"),
            "packageSize": payload.get("packageSize"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _LOGGER.warning(
            "OTA campaign opened for VIN %s: upgradeId=%s target=%s — firing xpeng_ota_available",
            self.vin, upgrade_id, payload.get("osVersion"),
        )
        self.hass.bus.async_fire("xpeng_ota_available", event_payload)

    def _now_str(self) -> str:
        """Return current local time as HH:MM:SS string."""
        return datetime.now().strftime("%H:%M:%S")

    def set_listener_message(self, topic: str, payload: str) -> None:
        """Store an MQTT listener message, append to log, fire event.

        Rate-limited on purpose, because the listener can be pointed at a firehose.
        ``mqtt_listener_topics: "#"`` subscribes to the whole broker, and the discovery
        candidates include ``Xmart/Car/+/State/CanPack1s`` - the car's 1 Hz CAN broadcast.
        Doing the expensive work per message - an HA event plus a full coordinator update,
        which re-renders every entity and writes recorder rows - is enough to wedge Home
        Assistant, which is what happened to a user on 0.4.30 with exactly those two
        settings enabled. Counters and the last message stay exact; only the event and the
        entity refresh are coalesced.
        """
        self.listener_last_topic = topic
        self.listener_last_payload = payload[:4000]  # Truncate large payloads
        self.listener_last_timestamp = datetime.now(timezone.utc)
        self.listener_message_count += 1

        # Append to rolling log buffer
        preview = payload[:200].replace("\n", " ")
        self.listener_log.append(
            f"{self._now_str()} MSG {topic} ({len(payload)}B) {preview}"
        )

        # Coalesce the expensive half. `callback` is scheduled from the paho thread, so a
        # burst arrives as a burst here; one event and one entity refresh per interval is
        # what the diagnostic sensors need, and the count above still tells the whole story.
        now = time.monotonic()
        if now - self._last_listener_update < LISTENER_UPDATE_INTERVAL:
            return
        self._last_listener_update = now

        # Fire HA event for real-time streaming
        self.hass.bus.async_fire(
            "xpeng_mqtt_message",
            {
                "topic": topic,
                "size": len(payload),
                "preview": payload[:500],
                "timestamp": self.listener_last_timestamp.isoformat(),
            },
        )

        _LOGGER.debug(
            "Listener message #%d on %s (%d bytes)",
            self.listener_message_count, topic, len(payload),
        )
        # Notify entities so diagnostic sensors update
        if self._last_full_data:
            self.async_set_updated_data(self._last_full_data)

    def record_subscription_result(
        self, topic: str, accepted: bool, reason: str = ""
    ) -> None:
        """Record whether a topic subscription was accepted or rejected."""
        now = datetime.now(timezone.utc)
        self.listener_discovery_results[topic] = {
            "accepted": accepted,
            "reason": reason,
            "timestamp": now.isoformat(),
        }
        if accepted:
            if topic not in self.listener_accepted_topics:
                self.listener_accepted_topics.append(topic)
            _LOGGER.info("Topic discovery: ACCEPTED %s (%s)", topic, reason)
        else:
            if topic not in self.listener_rejected_topics:
                self.listener_rejected_topics.append(topic)
            _LOGGER.debug("Topic discovery: REJECTED %s (%s)", topic, reason)

        # Append to rolling log buffer
        status = "ACCEPTED" if accepted else "REJECTED"
        reason_str = f" ({reason})" if reason else ""
        self.listener_log.append(
            f"{self._now_str()} SUB {topic} -> {status}{reason_str}"
        )

        # Fire HA event for real-time streaming
        self.hass.bus.async_fire(
            "xpeng_mqtt_discovery",
            {
                "topic": topic,
                "accepted": accepted,
                "reason": reason,
                "timestamp": now.isoformat(),
            },
        )

        # Notify entities so diagnostic sensors update
        if self._last_full_data:
            self.async_set_updated_data(self._last_full_data)

    def _process_rest_notifications(self, payload: Any) -> None:
        """Diff REST notification list against seen IDs and fire events for new ones."""
        items = payload
        if isinstance(payload, dict):
            for key in ("list", "records", "data", "items", "messages"):
                inner = payload.get(key)
                if isinstance(inner, list):
                    items = inner
                    break
        if not isinstance(items, list):
            _LOGGER.debug("REST notifications payload not a list: %s", type(payload).__name__)
            return

        # On the first poll, prime the seen set without firing events.
        # Otherwise every existing notification would fire on integration start.
        if not self._rest_notifications_primed:
            for item in items:
                if isinstance(item, dict):
                    nid = self._notification_id(item)
                    if nid:
                        self._seen_rest_notification_ids.add(nid)
            self._rest_notifications_primed = True
            _LOGGER.info(
                "Primed REST notification baseline: %d existing entries",
                len(self._seen_rest_notification_ids),
            )
            return

        new_count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            nid = self._notification_id(item)
            if not nid or nid in self._seen_rest_notification_ids:
                continue
            self._seen_rest_notification_ids.add(nid)
            notification = dict(item)
            notification["source"] = "rest"
            notification["msg_id"] = nid
            self.record_notification(notification)
            new_count += 1
        if new_count:
            _LOGGER.info("REST poll: %d new notification(s) fired", new_count)

    @staticmethod
    def _notification_id(item: dict[str, Any]) -> str:
        """Extract a stable identifier from a notification item.

        Namespaced by category. Ids come from one sequence per category, so the
        same numeric id can legitimately appear under Vehicle *and* Charging -
        without the prefix the second one would be treated as already-seen and
        silently dropped.
        """
        for key in ("msgId", "messageId", "id", "msg_id", "notificationId"):
            v = item.get(key)
            if v not in (None, ""):
                category = item.get("classification") or item.get("category") or ""
                return f"{category}:{v}" if category else str(v)
        return ""

    def record_notification(self, notification: dict[str, Any]) -> None:
        """Record a push notification (msg_type=3) and fire HA event."""
        now = datetime.now(timezone.utc)
        self.notification_count += 1
        notification["timestamp"] = now.isoformat()
        notification["local_time"] = self._now_str()
        self.notifications.append(notification)

        # Append to listener log buffer too
        stype = notification.get("service_type", "?")
        code = notification.get("code", "?")
        msg = notification.get("message", "")
        self.listener_log.append(
            f"{self._now_str()} NOTIFY svc={stype} code={code} {msg}"
        )

        # Fire HA event for automations
        self.hass.bus.async_fire("xpeng_notification", notification)

        _LOGGER.info(
            "Push notification #%d: service_type=%s code=%s msg=%s",
            self.notification_count, stype, code, msg,
        )

        # Notify entities so sensors update.
        #
        # Unconditional: a notification is a first-class update, and gating this
        # on `_last_full_data` meant a notification could never surface until
        # some unrelated vehicle state had been cached first. Observed on a
        # fresh install - the notification was recorded and the event fired, but
        # the Last-notification sensor stayed at `unknown` because no full poll
        # had landed yet.
        self.async_set_updated_data(self._last_full_data or {})

    def record_command_feedback(self, feedback: dict[str, Any]) -> None:
        """Record a command response and fire xpeng_command_feedback HA event."""
        now = datetime.now(timezone.utc)
        self.command_feedback_count += 1
        feedback = dict(feedback)
        feedback["timestamp"] = now.isoformat()
        feedback["local_time"] = self._now_str()
        self.command_feedbacks.append(feedback)
        self.last_command_feedback = feedback

        stype = feedback.get("service_type", "?")
        code = feedback.get("code", "?")
        msg = feedback.get("message", "")
        self.listener_log.append(
            f"{self._now_str()} CMD svc={stype} code={code} {msg}"
        )

        self.hass.bus.async_fire(
            "xpeng_command_feedback",
            {
                "service_type": stype,
                "msg_type": feedback.get("msg_type"),
                "code": code,
                "message": msg,
                "msg_id": feedback.get("msg_id"),
            },
        )

        _LOGGER.info(
            "Command feedback #%d: service_type=%s code=%s msg=%s",
            self.command_feedback_count, stype, code, msg,
        )

        self._merge_command_state(feedback)

        if self._last_full_data:
            self.async_set_updated_data(self._last_full_data)

    def _merge_command_state(self, feedback: dict[str, Any]) -> bool:
        """Fold a command's own acknowledgement into the cached state.

        The response to a command is a status report, not an echo of the request: its
        ``msg_content`` keys are the ones an ``instant`` section already holds, which is
        what makes the acknowledgement usable as the confirmation.

        The charge, seat and window subsystems report measured fields:

        * ``service_type 4`` (charge) answers ``set_charge_limit`` with
          ``{"charge_status": 15, "slow_charge_port_status": 2, "fast_charge_port_status": 2,
          "max_range_charge": 90}`` — the ``instant.charge`` fields, measured 2026-10-13.
          Without this the Charge limit entity showed whatever the last full snapshot said
          until the car next woke, so a command that *had* been applied read as "the value
          never changed". Merging costs no wake.
        * ``service_type 30`` (seats) carries a flat seat snapshot - ``left_rear_seat_heat``,
          ``passenger_seat_ventilation`` and the rest of ``instant.seat`` - measured
          2026-10-14. This is the *only* place seat changes are reported, so before it was
          merged a seat switched on from XPENG's app kept reading ``Off`` here, and picking
          ``Off`` again is not a change, so it could not be switched off either.

        Timestamps are deliberately untouched: ``last_data_timestamp`` tracks true MQTT
        state traffic and the silence watchdog relies on it.
        """
        section = COMMAND_STATE_SECTIONS.get(feedback.get("service_type"))
        if section is None:
            return False
        if feedback.get("code") not in (200, "200"):
            return False
        content = feedback.get("content")
        if not isinstance(content, dict):
            return False
        fields = {k: v for k, v in content.items() if k not in ("code", "msg")}
        if not fields:
            return False
        # Nothing cached yet: building a state out of a command's answer alone
        # would publish a partial snapshot that every other sensor then reads
        # as its new value.
        if not self._last_full_data:
            return False

        base = dict(self._last_full_data)
        msg_content = dict(base.get("msg_content", {}))
        instant = dict(msg_content.get("instant", {}))
        instant[section] = {**instant.get(section, {}), **fields}
        msg_content["instant"] = instant
        self._last_full_data = {**base, "msg_content": msg_content, "vin": self.vin}
        _LOGGER.debug(
            "Merged service_type %s response into cached state: instant.%s <- %s",
            feedback.get("service_type"), section, sorted(fields),
        )
        return True

    @property
    def vehicle_parked(self) -> bool | None:
        """Return whether the vehicle is parked.

        The rules live in drive_evidence.view(), which also returns the reason
        behind the verdict: speed > 0 means definitely not parked, otherwise a
        shift_state of P means parked and anything else that cannot be read
        means "no evidence" (None). Callers treat None as *not moving* - see
        poll_status.is_active - so this is deliberately conservative.
        """
        return drive_view(self._last_full_data)["parked"]

    @property
    def parking_view(self) -> dict[str, Any]:
        """The parked verdict with the fields and reason behind it.

        For diagnostics only: a user on issue #3 asked which field decided the
        verdict, and the capture they had could not say.
        """
        return drive_view(self._last_full_data)

    def note_drive_evidence(self, data: dict[str, Any]) -> None:
        """Record whether the frame just accepted showed the car moving.

        A car that was moving when the data stopped cannot be woken by a
        force-upload, so this is what lets the watchdog spend one outside the
        12 h sleeping-car budget (watchdog_policy.should_escalate_moving). A
        later frame showing P clears it, so a car that has since parked cannot
        inherit the earlier drive's allowance. A frame with no drive block
        leaves it alone: no evidence is not evidence of parking.
        """
        view = drive_view(data)
        if view["moving"]:
            self.last_motion_at = time.time()
        elif view["parked"]:
            self.last_motion_at = 0.0

    @property
    def external_charging_hint(self) -> bool:
        """Has something outside the car said a charge session may be running?

        Set by the ``xpeng.charging_hint`` service, which is how a charger or its
        integration tells us "current is flowing now" without waiting for the car
        to admit it - this car can take minutes to say so, and says nothing at all
        while it sleeps.

        It raises how often we *ask* and nothing else: the periodic query stays
        passive while only the hint is set (see ``_send_periodic_query``), so a
        wrong hint costs a few cloud round trips and never wakes a sleeping car.
        """
        return time.time() < self._charging_hint_until

    def set_charging_hint(self, seconds: float) -> None:
        """Arm (or clear, with 0) the external charging hint for ``seconds``."""
        self._charging_hint_until = time.time() + max(0.0, float(seconds))

    @property
    def is_charging(self) -> bool:
        """Return whether the vehicle is currently charging.

        Same logic as the ``charging`` binary sensor: charging_state 2 is
        car-initiated charging; interval charge power > 0 catches external
        charging (e.g. EVCC) where charging_state isn't set. Used by the
        periodic-query and watchdog timers to decide their cadence — we poll
        frequently while charging and go quiet otherwise so we don't keep a
        parked car awake.
        """
        if not self._last_full_data:
            return False
        content = self._last_full_data.get("msg_content", {})
        if not isinstance(content, dict):
            return False
        instant = content.get("instant", {})
        if isinstance(instant, dict):
            if instant.get("charge", {}).get("charging_state") == 2:
                return True
        interval = content.get("interval", {})
        if isinstance(interval, dict):
            try:
                if float(interval.get("charge", {}).get("power", 0) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                pass
        return False

    @property
    def last_full_data(self) -> dict[str, Any] | None:
        """Return the last full vehicle state (or None if never received)."""
        return self._last_full_data
