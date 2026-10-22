"""Vehicle command services for the XPENG integration."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import voluptuous as vol

from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .climate_commands import power_payload, send_climate, temperature_payload
from .charge_port import controls_enabled
from .command_gate import async_guard_service
from .command_policy import default_policy, service_group
from .const import COMMAND_NOT_SENT_MESSAGE, DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_CLIMATE_ON = "climate_on"
SERVICE_CLIMATE_OFF = "climate_off"
SERVICE_CLOSE_WINDOWS = "close_windows"
SERVICE_VENT_WINDOWS = "vent_windows"
SERVICE_SENTINEL_ON = "sentinel_on"
SERVICE_SENTINEL_OFF = "sentinel_off"
SERVICE_TRUNK_POWER_ON = "trunk_power_on"
SERVICE_TRUNK_POWER_OFF = "trunk_power_off"
SERVICE_FLASH_LIGHTS = "flash_lights"
SERVICE_HONK_HORN = "honk_horn"
SERVICE_FORCE_UPDATE = "force_update"
SERVICE_LOCK = "lock"
SERVICE_UNLOCK = "unlock"
SERVICE_START_CHARGE = "start_charge"
SERVICE_STOP_CHARGE = "stop_charge"
SERVICE_OPEN_TRUNK = "open_trunk"
SERVICE_CLOSE_TRUNK = "close_trunk"
SERVICE_OPEN_CHARGE_PORT = "open_charge_port"
SERVICE_CLOSE_CHARGE_PORT = "close_charge_port"
SERVICE_SET_CHARGE_LIMIT = "set_charge_limit"
SERVICE_DEFROST_ON = "defrost_on"
SERVICE_DEFROST_OFF = "defrost_off"
SERVICE_OPEN_WINDOWS = "open_windows"
SERVICE_EXECUTE_SCENE = "execute_scene"
SERVICE_TOGGLE_SCENE = "toggle_scene"
SERVICE_CLEAR_STUCK_COMMANDS = "clear_stuck_commands"
SERVICE_CAPTURE_SNAPSHOT = "capture_snapshot"
SERVICE_CHARGING_HINT = "charging_hint"
SERVICE_CHECK_OTA = "check_ota"
SERVICE_TRIGGER_OTA = "trigger_ota"
SERVICE_CANCEL_OTA = "cancel_ota"
SERVICE_AUTO_UPGRADE_ON = "auto_upgrade_on"
SERVICE_AUTO_UPGRADE_OFF = "auto_upgrade_off"
SERVICE_OTA_FEEDBACK = "ota_feedback"
SERVICE_SEND_RAW_MQTT = "send_raw_mqtt"
SERVICE_CREATE_HVAC_SCHEDULE = "create_hvac_schedule"
SERVICE_UPDATE_HVAC_SCHEDULE = "update_hvac_schedule"
SERVICE_DELETE_HVAC_SCHEDULE = "delete_hvac_schedule"
SERVICE_LIST_HVAC_SCHEDULES = "list_hvac_schedules"
SERVICE_SEND_NAVIGATION = "send_navigation"
SERVICE_FORCE_CALENDAR_SYNC = "force_calendar_sync"

DEFAULT_HVAC_ACTION_TYPE = "AIR_CONDITION_OPEN"

CREATE_HVAC_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("start_time"): cv.string,
        vol.Optional("days_of_week", default=[]): vol.All(
            cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=1, max=7))]
        ),
        vol.Optional("enabled", default=True): cv.boolean,
        vol.Optional("action_type", default=DEFAULT_HVAC_ACTION_TYPE): cv.string,
        vol.Optional("time_zone"): cv.string,
    }
)

UPDATE_HVAC_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("appointment_id"): cv.string,
        vol.Required("start_time"): cv.string,
        vol.Optional("days_of_week", default=[]): vol.All(
            cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=1, max=7))]
        ),
        vol.Optional("enabled", default=True): cv.boolean,
        vol.Optional("action_type", default=DEFAULT_HVAC_ACTION_TYPE): cv.string,
        vol.Optional("time_zone"): cv.string,
    }
)

DELETE_HVAC_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("appointment_id"): cv.string,
        vol.Optional("time_zone"): cv.string,
    }
)


def _days_to_bitmask(days: list[int]) -> int:
    """Convert HA-style 1=Mon..7=Sun list into bit 0=Mon..bit 6=Sun bitmask."""
    mask = 0
    for d in days:
        if 1 <= d <= 7:
            mask |= 1 << (d - 1)
    return mask


def _normalize_start_time(value: str) -> str:
    """Validate HH:MM string and return zero-padded form."""
    parts = value.split(":")
    if len(parts) != 2:
        raise vol.Invalid("start_time must be in HH:MM format")
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise vol.Invalid("start_time hour 0-23, minute 0-59")
    return f"{hh:02d}:{mm:02d}"

CLIMATE_ON_SCHEMA = vol.Schema(
    {
        vol.Optional("temperature", default=22.0): vol.Coerce(float),
        vol.Optional("duration", default=15): vol.Coerce(int),
    }
)

SENTINEL_ON_SCHEMA = vol.Schema(
    {
        vol.Optional("sensitivity", default=2): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=3)
        ),
    }
)

TRUNK_POWER_ON_SCHEMA = vol.Schema(
    {
        vol.Optional("duration", default=60): vol.Coerce(int),
    }
)

SET_CHARGE_LIMIT_SCHEMA = vol.Schema(
    {
        vol.Required("limit"): vol.All(vol.Coerce(int), vol.Range(min=50, max=100)),
    }
)

EXECUTE_SCENE_SCHEMA = vol.Schema(
    {
        vol.Required("scene_id"): cv.string,
    }
)

TOGGLE_SCENE_SCHEMA = vol.Schema(
    {
        vol.Required("scene_id"): cv.string,
        vol.Required("active"): cv.boolean,
    }
)

TRIGGER_OTA_SCHEMA = vol.Schema(
    {
        vol.Required("upgrade_id"): vol.Coerce(int),
    }
)

CANCEL_OTA_SCHEMA = vol.Schema(
    {
        vol.Required("upgrade_id"): vol.Coerce(int),
    }
)

OTA_FEEDBACK_SCHEMA = vol.Schema(
    {
        vol.Required("upgrade_id"): vol.Coerce(int),
        vol.Required("status"): vol.Coerce(int),
    }
)

CHARGING_HINT_SCHEMA = vol.Schema(
    {
        # 0 clears the hint; the cap keeps a stuck automation from holding the
        # fast cadence for a whole day.
        vol.Optional("duration_minutes", default=30): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=240)
        ),
    }
)


SEND_RAW_MQTT_SCHEMA = vol.Schema(
    {
        vol.Required("service_type"): vol.Coerce(int),
        vol.Optional("msg_type", default=2): vol.Coerce(int),
        vol.Optional("msg_content", default="{}"): cv.string,
    }
)


SEND_NAVIGATION_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required("latitude"): vol.Coerce(float),
        vol.Required("longitude"): vol.Coerce(float),
        vol.Optional("label"): cv.string,
        vol.Optional("poi_id"): cv.string,
        vol.Optional("msg_type"): cv.string,
    }
)


def _get_entry_data(hass: HomeAssistant) -> dict[str, Any] | None:
    """Get the first XPENG config entry data."""
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        _LOGGER.error("No XPENG integration configured")
        return None
    entry_id = next(iter(entries))
    return entries[entry_id]


def _charge_port_controls_enabled(hass: HomeAssistant) -> bool:
    """Respect the owner option and known model support; unknown stays allowed."""
    entry_data = _get_entry_data(hass) or {}
    entry = entry_data.get("entry")
    from .vehicle_capabilities import from_coordinator

    return (controls_enabled(getattr(entry, "options", None) or {})
            and from_coordinator(entry_data.get("coordinator")).support("charge_port_control") is not False)


def _build_snapshot(coordinator: Any, reason: str) -> dict[str, Any]:
    """Collect a snapshot of every coordinator-tracked field worth diffing."""
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "vin": coordinator.vin,
        "xos_version": coordinator.xos_version,
        "vehicle_type_code": coordinator.vehicle_type_code,
        "series_name": coordinator.series_name,
        "series_code": coordinator.series_code,
        "vehicle_type_name": coordinator.vehicle_type_name,
        "diagram_version": coordinator.diagram_version,
        "vin_decoded": (
            coordinator.vin_info.to_dict() if coordinator.vin_info is not None else None
        ),
        "cdu_id": coordinator.cdu_id,
        "cdu_a_or_b_flag": coordinator.cdu_a_or_b_flag,
        "cdu_parts_no_eight2ten": coordinator.cdu_parts_no_eight2ten,
        "remote_capabilities": coordinator.remote_capabilities,
        "ota_upgrade_info": coordinator.ota_upgrade_info,
        "ota_auto_upgrade_status": coordinator.ota_auto_upgrade_status,
        "ota_version_info": coordinator.ota_version_info,
        "vehicle_extension": coordinator.vehicle_extension,
        "maintenance_data": coordinator.maintenance_data,
        "energy_report": coordinator.energy_report,
        "hvac_schedules": coordinator.hvac_schedules,
        "hvac_next_start_time": coordinator.hvac_next_start_time,
        "left_comfort_preon_seconds": coordinator.left_comfort_preon_seconds,
        "beta_popup": coordinator.beta_popup,
        "xcombos": coordinator.xcombos,
        "vehicle_image_url": coordinator.vehicle_image_url,
        "vehicle_appearance": coordinator.vehicle_appearance,
    }


def _write_snapshot(config_dir: str, snapshot: dict[str, Any]) -> str:
    """Persist a snapshot dict to <config>/xpeng_snapshots/<vin>_<ts>.json."""
    dir_path = os.path.join(config_dir, "xpeng_snapshots")
    os.makedirs(dir_path, exist_ok=True)
    ts = snapshot["captured_at"].replace(":", "-").replace(".", "-")
    vin = snapshot.get("vin") or "unknown"
    fname = f"{vin}_{ts}.json"
    path = os.path.join(dir_path, fname)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, default=str)
    return path


def _send(
    hass: HomeAssistant,
    service_type: int,
    msg_content: dict,
    msg_type: int = 2,
) -> None:
    """Helper to send a command via the stored MQTT client.

    Raises when it never went out. It used to return quietly, so a service that
    published nothing still reported success and the only evidence was a warning
    in the log - which is how "I pressed Wake and nothing happened" stayed
    invisible in the UI and in automation traces (measured 2026-10-16: the lock
    command was dropped twice while the client was reconnecting).
    """
    entry_data = _get_entry_data(hass)
    if not entry_data:
        raise HomeAssistantError(
            "No live XPENG config entry to send this command from"
        )
    mqtt_client = entry_data["mqtt_client"]
    coordinator = entry_data["coordinator"]
    vin = coordinator.vin
    if not mqtt_client.send_command(vin, service_type, msg_content, msg_type):
        raise HomeAssistantError(COMMAND_NOT_SENT_MESSAGE)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register XPENG vehicle command services."""

    async def handle_climate_on(call: ServiceCall) -> None:
        data = _get_entry_data(hass)
        if not data:
            raise HomeAssistantError("No live XPENG config entry")
        c, client = data["coordinator"], data["mqtt_client"]
        try:
            target = temperature_payload(
                c, call.data.get("temperature", 22.0), call.data.get("duration", 15)
            )
        except (TypeError, ValueError, OverflowError):
            raise HomeAssistantError("Invalid climate target temperature") from None
        await send_climate(hass, client, c, target)
        await send_climate(
            hass, client, c, power_payload(c, True, call.data.get("duration", 15))
        )

    async def handle_climate_off(call: ServiceCall) -> None:
        data = _get_entry_data(hass)
        if not data:
            raise HomeAssistantError("No live XPENG config entry")
        await send_climate(
            hass, data["mqtt_client"], data["coordinator"],
            power_payload(data["coordinator"], False),
        )

    async def handle_close_windows(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send, hass, 12, {"cmd_type": 1, "cmd_value": 1}
        )

    async def handle_vent_windows(call: ServiceCall) -> None:
        # The only window command verified to do anything: it vents. The old
        # payload here was the charge-port close command.
        await hass.async_add_executor_job(
            _send, hass, 12, {"cmd_type": 1, "cmd_value": 2}
        )

    async def handle_sentinel_on(call: ServiceCall) -> None:
        sensitivity = call.data.get("sensitivity", 2)
        await hass.async_add_executor_job(
            _send,
            hass,
            8,
            {
                "action": 2,
                "settings": [
                    {"code": "SENTINEL_MODE_VIDEO", "value": 1},
                    {"code": "SENTINEL_MODE_SENSITIVITY", "value": sensitivity},
                ],
            },
        )

    async def handle_sentinel_off(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send,
            hass,
            8,
            {
                "action": 2,
                "settings": [
                    {"code": "SENTINEL_MODE_VIDEO", "value": 0},
                    {"code": "SENTINEL_MODE_SENSITIVITY", "value": 0},
                ],
            },
        )

    async def handle_trunk_power_on(call: ServiceCall) -> None:
        duration = call.data.get("duration", 60)
        await hass.async_add_executor_job(
            _send, hass, 13, {"action": 2, "timeToPowerOffInMin": duration}
        )

    async def handle_trunk_power_off(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send, hass, 13, {"action": 3, "timeToPowerOffInMin": 0}
        )

    async def handle_flash_lights(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send, hass, 5, {"cmd_type": 1, "cmd_value": 1}
        )

    async def handle_honk_horn(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send, hass, 6, {"cmd_type": 1, "cmd_value": 1}
        )

    async def handle_force_update(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send, hass, 19, {}, 4
        )

    async def handle_charging_hint(call: ServiceCall) -> None:
        """Let an external charger say a session may be starting.

        Polling only, and deliberately the one thing this cannot do is wake the
        car: the hint raises the *cadence* and the periodic query stays passive
        while only the hint is set (see ``_send_periodic_query``). That is the
        whole point - a charging car is awake, so asking it often is enough, and a
        wrong hint then costs cloud round trips instead of the car's 12V battery.
        """
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        minutes = int(call.data.get("duration_minutes", 30))
        entry_data["coordinator"].set_charging_hint(minutes * 60)
        _LOGGER.info(
            "Charging hint %s for %d minutes (polling cadence raised; the car is "
            "not woken by this)",
            "cleared" if minutes == 0 else "armed",
            minutes,
        )

    async def handle_lock(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send, hass, 9, {"cmd_type": 1}
        )

    async def handle_unlock(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send, hass, 9, {"cmd_type": 0}
        )

    async def handle_start_charge(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send, hass, 4, {"cmd_type": 1, "cmd_value": 1}
        )

    async def handle_stop_charge(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send, hass, 4, {"cmd_type": 1, "cmd_value": 0}
        )

    async def handle_open_trunk(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send, hass, 9, {"cmd_type": 2, "cmd_value": 1}
        )

    async def handle_close_trunk(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send, hass, 9, {"cmd_type": 2, "cmd_value": 3}
        )

    async def handle_open_charge_port(call: ServiceCall) -> None:
        if not _charge_port_controls_enabled(hass):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="charge_port_control_disabled",
            )
        await hass.async_add_executor_job(
            _send, hass, 4, {"cmd_type": 3, "cmd_value": 1}
        )

    async def handle_close_charge_port(call: ServiceCall) -> None:
        if not _charge_port_controls_enabled(hass):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="charge_port_control_disabled",
            )
        await hass.async_add_executor_job(
            _send, hass, 4, {"cmd_type": 3, "cmd_value": 0}
        )

    async def handle_set_charge_limit(call: ServiceCall) -> None:
        limit = call.data["limit"]
        await hass.async_add_executor_job(
            _send, hass, 4, {"cmd_type": 2, "cmd_value": limit}
        )

    async def handle_defrost_on(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send, hass, 3, {"cmd_type": 4, "cmd_value": 1}
        )

    async def handle_defrost_off(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send, hass, 3, {"cmd_type": 4, "cmd_value": 0}
        )

    async def handle_open_windows(call: ServiceCall) -> None:
        await hass.async_add_executor_job(
            _send, hass, 12, {"cmd_type": 1, "cmd_value": 2}
        )

    async def handle_execute_scene(call: ServiceCall) -> None:
        scene_id = call.data["scene_id"]
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        coordinator = entry_data["coordinator"]
        if not entry:
            _LOGGER.error("No config entry available for scene execution")
            return
        from .xpeng_api import execute_xcombo_scene
        from .__init__ import _load_api_context
        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials available for scene execution")
            return
        await hass.async_add_executor_job(
            execute_xcombo_scene, None, tenant, tokens, scene_id, coordinator.vin
        )

    async def handle_toggle_scene(call: ServiceCall) -> None:
        scene_id = call.data["scene_id"]
        active = call.data["active"]
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        coordinator = entry_data["coordinator"]
        if not entry:
            _LOGGER.error("No config entry available for scene toggle")
            return
        from .xpeng_api import toggle_xcombo_scene
        from .__init__ import _load_api_context
        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials available for scene toggle")
            return
        await hass.async_add_executor_job(
            toggle_xcombo_scene, None, tenant, tokens, scene_id, coordinator.vin, active
        )

    async def _do_capture_snapshot(reason: str) -> str | None:
        """Build and persist a snapshot. Returns path or None."""
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return None
        coordinator = entry_data["coordinator"]
        snapshot = _build_snapshot(coordinator, reason)
        try:
            path = await hass.async_add_executor_job(
                _write_snapshot, hass.config.config_dir, snapshot
            )
            _LOGGER.info("XPENG snapshot saved (%s): %s", reason, path)
            return path
        except Exception as err:
            _LOGGER.warning("Failed to write snapshot: %s", err)
            return None

    async def handle_capture_snapshot(call: ServiceCall) -> None:
        reason = call.data.get("reason", "manual")
        await _do_capture_snapshot(reason)

    @callback
    def _on_xos_version_changed(event: Event) -> None:
        """Auto-capture before/after XOS update."""
        old_v = event.data.get("old_version")
        new_v = event.data.get("new_version")
        reason = f"xos_changed_{old_v}_to_{new_v}"
        hass.async_create_task(_do_capture_snapshot(reason))

    hass.bus.async_listen("xpeng_xos_version_changed", _on_xos_version_changed)

    async def handle_clear_stuck_commands(call: ServiceCall) -> None:
        """Clear the user's stuck-command queue server-side.

        Use after code:12 ("Vehicle controls in used") to allow new commands.
        """
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        if not entry:
            _LOGGER.error("No config entry available for clear_stuck_commands")
            return
        from .xpeng_api import clear_vehicle_commands
        from .__init__ import _load_api_context
        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials for clear_stuck_commands")
            return
        try:
            result = await hass.async_add_executor_job(
                clear_vehicle_commands, None, tenant, tokens
            )
            _LOGGER.info("Cleared stuck vehicle command queue: %s", result)
        except Exception as err:
            _LOGGER.warning("clear_stuck_commands failed: %s", err)

    async def handle_check_ota(call: ServiceCall) -> None:
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        coordinator = entry_data["coordinator"]
        if not entry:
            _LOGGER.error("No config entry available for OTA check")
            return
        from .xpeng_api import get_ota_upgrade_info, get_auto_upgrade_status, get_ota_version
        from .__init__ import _load_api_context
        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials for OTA check")
            return
        vin = coordinator.vin
        rest_data: dict[str, Any] = {}
        try:
            info = await hass.async_add_executor_job(
                get_ota_upgrade_info, None, tenant, tokens, vin, 0
            )
            if isinstance(info, dict):
                rest_data["ota_upgrade_info"] = info
        except Exception as err:
            _LOGGER.warning("OTA upgrade info failed: %s", err)
        try:
            auto = await hass.async_add_executor_job(
                get_auto_upgrade_status, None, tenant, tokens, vin
            )
            if isinstance(auto, dict):
                rest_data["ota_auto_upgrade_status"] = auto
        except Exception as err:
            _LOGGER.warning("OTA auto-upgrade status failed: %s", err)
        try:
            ver = await hass.async_add_executor_job(
                get_ota_version, None, tenant, tokens, vin
            )
            if isinstance(ver, dict):
                rest_data["ota_version_info"] = ver
        except Exception as err:
            _LOGGER.warning("OTA version info failed: %s", err)
        if rest_data:
            coordinator.set_rest_data(rest_data)

    async def handle_trigger_ota(call: ServiceCall) -> None:
        upgrade_id = call.data["upgrade_id"]
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        coordinator = entry_data["coordinator"]
        if not entry:
            _LOGGER.error("No config entry available for OTA trigger")
            return
        from .xpeng_api import trigger_ota_upgrade
        from .__init__ import _load_api_context
        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials for OTA trigger")
            return
        result = await hass.async_add_executor_job(
            trigger_ota_upgrade, None, tenant, tokens, coordinator.vin, upgrade_id
        )
        _LOGGER.info("OTA upgrade triggered: upgrade_id=%s result=%s", upgrade_id, result)

    async def handle_cancel_ota(call: ServiceCall) -> None:
        upgrade_id = call.data["upgrade_id"]
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        coordinator = entry_data["coordinator"]
        if not entry:
            _LOGGER.error("No config entry available for OTA cancel")
            return
        from .xpeng_api import cancel_ota_schedule
        from .__init__ import _load_api_context
        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials for OTA cancel")
            return
        result = await hass.async_add_executor_job(
            cancel_ota_schedule, None, tenant, tokens, coordinator.vin, upgrade_id
        )
        _LOGGER.info("OTA schedule cancelled: upgrade_id=%s result=%s", upgrade_id, result)

    async def handle_auto_upgrade_on(call: ServiceCall) -> None:
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        coordinator = entry_data["coordinator"]
        if not entry:
            _LOGGER.error("No config entry available for auto-upgrade")
            return
        from .xpeng_api import set_auto_upgrade
        from .__init__ import _load_api_context
        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials for auto-upgrade")
            return
        result = await hass.async_add_executor_job(
            set_auto_upgrade, None, tenant, tokens, coordinator.vin, True
        )
        _LOGGER.info("Auto-upgrade enabled: result=%s", result)

    async def handle_auto_upgrade_off(call: ServiceCall) -> None:
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        coordinator = entry_data["coordinator"]
        if not entry:
            _LOGGER.error("No config entry available for auto-upgrade")
            return
        from .xpeng_api import set_auto_upgrade
        from .__init__ import _load_api_context
        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials for auto-upgrade")
            return
        result = await hass.async_add_executor_job(
            set_auto_upgrade, None, tenant, tokens, coordinator.vin, False
        )
        _LOGGER.info("Auto-upgrade disabled: result=%s", result)

    async def handle_ota_feedback(call: ServiceCall) -> None:
        upgrade_id = call.data["upgrade_id"]
        status = call.data["status"]
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        coordinator = entry_data["coordinator"]
        if not entry:
            _LOGGER.error("No config entry available for OTA feedback")
            return
        from .xpeng_api import send_ota_feedback
        from .__init__ import _load_api_context
        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials for OTA feedback")
            return
        result = await hass.async_add_executor_job(
            send_ota_feedback, None, tenant, tokens, coordinator.vin, upgrade_id, status
        )
        _LOGGER.info("OTA feedback sent: upgrade_id=%s status=%s result=%s", upgrade_id, status, result)

    async def handle_send_raw_mqtt(call: ServiceCall) -> None:
        service_type = call.data["service_type"]
        msg_type = call.data.get("msg_type", 2)
        msg_content_str = call.data.get("msg_content", "{}")
        try:
            msg_content = json.loads(msg_content_str)
        except (json.JSONDecodeError, TypeError) as err:
            _LOGGER.error("Invalid JSON in msg_content: %s", err)
            return
        _LOGGER.info(
            "Sending raw MQTT: service_type=%s, msg_type=%s, msg_content=%s",
            service_type, msg_type, msg_content,
        )
        await hass.async_add_executor_job(
            _send, hass, service_type, msg_content, msg_type
        )

    async def handle_create_hvac_schedule(call: ServiceCall) -> None:
        start_time = _normalize_start_time(call.data["start_time"])
        days = call.data.get("days_of_week", []) or []
        enabled = call.data.get("enabled", True)
        action_type = call.data.get("action_type", DEFAULT_HVAC_ACTION_TYPE)
        time_zone = call.data.get("time_zone")
        schedule_mask = _days_to_bitmask(days)
        cycle_type = 1 if schedule_mask else 0
        is_valid = 1 if enabled else 0
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        coordinator = entry_data["coordinator"]
        if not entry:
            _LOGGER.error("No config entry available for create_hvac_schedule")
            return
        from .xpeng_api import create_aircondition_appointment
        from .__init__ import _load_api_context
        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials for create_hvac_schedule")
            return
        try:
            result = await hass.async_add_executor_job(
                create_aircondition_appointment,
                None, tenant, tokens, coordinator.vin,
                action_type, start_time, cycle_type, schedule_mask, is_valid,
                time_zone, 1,
            )
            _LOGGER.info(
                "Created HVAC schedule: start_time=%s schedule=%s cycle=%s valid=%s result=%s",
                start_time, schedule_mask, cycle_type, is_valid, result,
            )
        except Exception as err:
            _LOGGER.warning("create_hvac_schedule failed: %s", err)

    async def handle_update_hvac_schedule(call: ServiceCall) -> None:
        appointment_id = call.data["appointment_id"]
        start_time = _normalize_start_time(call.data["start_time"])
        days = call.data.get("days_of_week", []) or []
        enabled = call.data.get("enabled", True)
        action_type = call.data.get("action_type", DEFAULT_HVAC_ACTION_TYPE)
        time_zone = call.data.get("time_zone")
        schedule_mask = _days_to_bitmask(days)
        cycle_type = 1 if schedule_mask else 0
        is_valid = 1 if enabled else 0
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        coordinator = entry_data["coordinator"]
        if not entry:
            _LOGGER.error("No config entry available for update_hvac_schedule")
            return
        from .xpeng_api import update_aircondition_appointment
        from .__init__ import _load_api_context
        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials for update_hvac_schedule")
            return
        try:
            result = await hass.async_add_executor_job(
                update_aircondition_appointment,
                None, tenant, tokens, coordinator.vin,
                appointment_id, action_type, start_time, cycle_type,
                schedule_mask, is_valid, time_zone, "UPDATE",
            )
            _LOGGER.info(
                "Updated HVAC schedule %s: start_time=%s schedule=%s result=%s",
                appointment_id, start_time, schedule_mask, result,
            )
        except Exception as err:
            _LOGGER.warning("update_hvac_schedule failed: %s", err)

    async def handle_delete_hvac_schedule(call: ServiceCall) -> None:
        appointment_id = call.data["appointment_id"]
        time_zone = call.data.get("time_zone")
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        coordinator = entry_data["coordinator"]
        if not entry:
            _LOGGER.error("No config entry available for delete_hvac_schedule")
            return
        from .xpeng_api import delete_aircondition_appointment
        from .__init__ import _load_api_context
        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials for delete_hvac_schedule")
            return
        try:
            result = await hass.async_add_executor_job(
                delete_aircondition_appointment,
                None, tenant, tokens, coordinator.vin, appointment_id, time_zone,
            )
            _LOGGER.info(
                "Deleted HVAC schedule %s: result=%s", appointment_id, result,
            )
        except Exception as err:
            _LOGGER.warning("delete_hvac_schedule failed: %s", err)

    async def handle_list_hvac_schedules(call: ServiceCall) -> None:
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        coordinator = entry_data["coordinator"]
        if not entry:
            _LOGGER.error("No config entry available for list_hvac_schedules")
            return
        from .xpeng_api import list_aircondition_appointments
        from .__init__ import _load_api_context
        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials for list_hvac_schedules")
            return
        try:
            result = await hass.async_add_executor_job(
                list_aircondition_appointments,
                None, tenant, tokens, coordinator.vin, "AIR_CONDITION", None,
            )
            _LOGGER.info("HVAC schedules: %s", result)
            # Push the fetched schedules back into the coordinator so the
            # HVAC schedule calendar entity refreshes immediately (instead of
            # waiting for the next REST poll, which can be up to 1h when parked).
            if isinstance(result, dict):
                items = result.get("list") or []
                if isinstance(items, list):
                    nxt = result.get("nextStartTime")
                    coordinator.set_rest_data({
                        "hvac_schedules": items,
                        "hvac_next_start_time": nxt if isinstance(nxt, str) else None,
                    })
                    _LOGGER.info("Refreshed calendar with %d HVAC schedule(s)", len(items))
        except Exception as err:
            _LOGGER.warning("list_hvac_schedules failed: %s", err)

    async def handle_send_navigation(call: ServiceCall) -> None:
        name = call.data["name"]
        latitude = float(call.data["latitude"])
        longitude = float(call.data["longitude"])
        label = call.data.get("label")
        poi_id = call.data.get("poi_id")
        msg_type = call.data.get("msg_type")
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        coordinator = entry_data["coordinator"]
        if not entry:
            _LOGGER.error("No config entry available for send_navigation")
            return
        from .xpeng_api import send_to_vehicle
        from .__init__ import _load_api_context

        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials for send_navigation")
            return
        try:
            result = await hass.async_add_executor_job(
                send_to_vehicle,
                None, tenant, tokens, coordinator.vin,
                name, latitude, longitude, label, poi_id, msg_type,
            )
            _LOGGER.info(
                "Sent navigation to vehicle: name=%s lat=%s lon=%s result=%s",
                name, latitude, longitude, result,
            )
        except Exception as err:
            _LOGGER.warning("send_navigation failed: %s", err)

    async def handle_force_calendar_sync(call: ServiceCall) -> None:
        entry_data = _get_entry_data(hass)
        if not entry_data:
            return
        entry = entry_data.get("entry")
        coordinator = entry_data["coordinator"]
        if not entry:
            _LOGGER.error("No config entry available for force_calendar_sync")
            return
        from .xpeng_api import report_schedule_sync_status, get_schedule_sync_metadata
        from .__init__ import _load_api_context

        tokens, tenant = _load_api_context(entry)
        if not tokens or not tenant:
            _LOGGER.error("No API credentials for force_calendar_sync")
            return
        try:
            result = await hass.async_add_executor_job(
                report_schedule_sync_status,
                None, tenant, tokens, coordinator.vin, 1,
            )
            _LOGGER.info("Calendar sync enabled: %s", result)
            meta = await hass.async_add_executor_job(
                get_schedule_sync_metadata,
                None, tenant, tokens, coordinator.vin,
            )
            _LOGGER.info("Calendar sync metadata: %s", meta)
        except Exception as err:
            _LOGGER.warning("force_calendar_sync failed: %s", err)

    def _register(service: str, handler: Any, schema: Any = None) -> None:
        """Register a vehicle command behind the command policy.

        Every command service is registered through here, so there is exactly
        one place a new service can forget to declare its group. It cannot stay
        forgotten: ``tests/test_command_policy.py`` enumerates the registered
        services and fails on any name that ``command_policy.SERVICE_GROUPS``
        does not cover.

        The group is looked up once at registration; the *decision* is made per
        call, against the entry's current options, so changing the mode takes
        effect on reload without re-registering anything.
        """
        group = service_group(service)

        async def _gated(call: ServiceCall) -> None:
            entry_data = _get_entry_data(hass) or {}
            policy = entry_data.get("command_policy") or default_policy()
            await async_guard_service(hass, call, service, group, policy)
            await handler(call)

        if schema is None:
            hass.services.async_register(DOMAIN, service, _gated)
        else:
            hass.services.async_register(DOMAIN, service, _gated, schema=schema)

    _register(SERVICE_CLIMATE_ON, handle_climate_on, CLIMATE_ON_SCHEMA)
    _register(SERVICE_CLIMATE_OFF, handle_climate_off)
    _register(SERVICE_CLOSE_WINDOWS, handle_close_windows)
    _register(SERVICE_VENT_WINDOWS, handle_vent_windows)
    _register(SERVICE_SENTINEL_ON, handle_sentinel_on, SENTINEL_ON_SCHEMA)
    _register(SERVICE_SENTINEL_OFF, handle_sentinel_off)
    _register(SERVICE_TRUNK_POWER_ON, handle_trunk_power_on, TRUNK_POWER_ON_SCHEMA)
    _register(SERVICE_TRUNK_POWER_OFF, handle_trunk_power_off)
    _register(SERVICE_FLASH_LIGHTS, handle_flash_lights)
    _register(SERVICE_HONK_HORN, handle_honk_horn)
    _register(SERVICE_FORCE_UPDATE, handle_force_update)
    _register(SERVICE_CHARGING_HINT, handle_charging_hint, CHARGING_HINT_SCHEMA)
    _register(SERVICE_LOCK, handle_lock)
    _register(SERVICE_UNLOCK, handle_unlock)
    _register(SERVICE_START_CHARGE, handle_start_charge)
    _register(SERVICE_STOP_CHARGE, handle_stop_charge)
    _register(SERVICE_OPEN_TRUNK, handle_open_trunk)
    _register(SERVICE_CLOSE_TRUNK, handle_close_trunk)
    _register(SERVICE_OPEN_CHARGE_PORT, handle_open_charge_port)
    _register(SERVICE_CLOSE_CHARGE_PORT, handle_close_charge_port)
    _register(SERVICE_SET_CHARGE_LIMIT, handle_set_charge_limit, SET_CHARGE_LIMIT_SCHEMA)
    _register(SERVICE_DEFROST_ON, handle_defrost_on)
    _register(SERVICE_DEFROST_OFF, handle_defrost_off)
    _register(SERVICE_OPEN_WINDOWS, handle_open_windows)
    _register(SERVICE_EXECUTE_SCENE, handle_execute_scene, EXECUTE_SCENE_SCHEMA)
    _register(SERVICE_TOGGLE_SCENE, handle_toggle_scene, TOGGLE_SCENE_SCHEMA)
    _register(SERVICE_CLEAR_STUCK_COMMANDS, handle_clear_stuck_commands)
    _register(SERVICE_CAPTURE_SNAPSHOT, handle_capture_snapshot)
    _register(SERVICE_CHECK_OTA, handle_check_ota)
    _register(SERVICE_TRIGGER_OTA, handle_trigger_ota, TRIGGER_OTA_SCHEMA)
    _register(SERVICE_CANCEL_OTA, handle_cancel_ota, CANCEL_OTA_SCHEMA)
    _register(SERVICE_AUTO_UPGRADE_ON, handle_auto_upgrade_on)
    _register(SERVICE_AUTO_UPGRADE_OFF, handle_auto_upgrade_off)
    _register(SERVICE_OTA_FEEDBACK, handle_ota_feedback, OTA_FEEDBACK_SCHEMA)
    _register(SERVICE_SEND_RAW_MQTT, handle_send_raw_mqtt, SEND_RAW_MQTT_SCHEMA)
    _register(SERVICE_CREATE_HVAC_SCHEDULE, handle_create_hvac_schedule, CREATE_HVAC_SCHEDULE_SCHEMA)
    _register(SERVICE_UPDATE_HVAC_SCHEDULE, handle_update_hvac_schedule, UPDATE_HVAC_SCHEDULE_SCHEMA)
    _register(SERVICE_DELETE_HVAC_SCHEDULE, handle_delete_hvac_schedule, DELETE_HVAC_SCHEDULE_SCHEMA)
    _register(SERVICE_LIST_HVAC_SCHEDULES, handle_list_hvac_schedules)
    _register(SERVICE_SEND_NAVIGATION, handle_send_navigation, SEND_NAVIGATION_SCHEMA)
    _register(SERVICE_FORCE_CALENDAR_SYNC, handle_force_calendar_sync)


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload XPENG services."""
    for service in (
        SERVICE_CLIMATE_ON,
        SERVICE_CLIMATE_OFF,
        SERVICE_CLOSE_WINDOWS,
        SERVICE_VENT_WINDOWS,
        SERVICE_SENTINEL_ON,
        SERVICE_SENTINEL_OFF,
        SERVICE_TRUNK_POWER_ON,
        SERVICE_TRUNK_POWER_OFF,
        SERVICE_FLASH_LIGHTS,
        SERVICE_HONK_HORN,
        SERVICE_FORCE_UPDATE,
        SERVICE_CHARGING_HINT,
        SERVICE_LOCK,
        SERVICE_UNLOCK,
        SERVICE_START_CHARGE,
        SERVICE_STOP_CHARGE,
        SERVICE_OPEN_TRUNK,
        SERVICE_CLOSE_TRUNK,
        SERVICE_OPEN_CHARGE_PORT,
        SERVICE_CLOSE_CHARGE_PORT,
        SERVICE_SET_CHARGE_LIMIT,
        SERVICE_DEFROST_ON,
        SERVICE_DEFROST_OFF,
        SERVICE_OPEN_WINDOWS,
        SERVICE_EXECUTE_SCENE,
        SERVICE_TOGGLE_SCENE,
        SERVICE_CLEAR_STUCK_COMMANDS,
        SERVICE_CAPTURE_SNAPSHOT,
        SERVICE_CHECK_OTA,
        SERVICE_TRIGGER_OTA,
        SERVICE_CANCEL_OTA,
        SERVICE_AUTO_UPGRADE_ON,
        SERVICE_AUTO_UPGRADE_OFF,
        SERVICE_OTA_FEEDBACK,
        SERVICE_SEND_RAW_MQTT,
        SERVICE_CREATE_HVAC_SCHEDULE,
        SERVICE_UPDATE_HVAC_SCHEDULE,
        SERVICE_DELETE_HVAC_SCHEDULE,
        SERVICE_LIST_HVAC_SCHEDULES,
        SERVICE_SEND_NAVIGATION,
        SERVICE_FORCE_CALENDAR_SYNC,
    ):
        hass.services.async_remove(DOMAIN, service)