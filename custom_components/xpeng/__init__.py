"""The XPENG integration."""
from __future__ import annotations

import functools
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store

from . import poll_status
from .capture import open_recorder
from .command_gate import policy_for_entry
from .energy_guard import soc_of
from .replay_guard import is_replay, odometer_from_state
from .sentinel_guard import with_previous_tpms
from .watchdog_policy import (
    ESCALATE_AFTER_SECONDS,
    ESCALATE_BACKOFF_SECONDS,
    should_escalate,
    should_escalate_moving,
)

from .const import (
    CONF_CAPTURE_ENABLED,
    CONF_CAPTURE_PATH,
    CONF_CAPTURE_TOPICS,
    CONF_CERT_DIR,
    CONF_CLIENT_ID,
    CONF_CONFIGURED_DEVICE_ID,
    CONF_ICCID,
    CONF_LAST_RENEWAL,
    CONF_LOGIN_TOKENS,
    CONF_MQTT_LISTENER_ENABLED,
    CONF_MQTT_LISTENER_TOPICS,
    CONF_MQTT_REFRESH_INTERVAL,
    DEFAULT_NOTIFICATION_POLL_INTERVAL,
    MIN_NOTIFICATION_POLL_INTERVAL,
    NOTIFICATION_POLL_INTERVAL,
    CONF_MQTT_TOPIC_DISCOVERY,
    CONF_NOTIFICATION_POLL_INTERVAL,
    CONF_PARKED_SCAN_INTERVAL,
    CONF_PASSWORD,
    CONF_REST_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SERVER,
    CONF_TENANT_DATA,
    CONF_TESLAMATE_ENABLED,
    CONF_TESLAMATE_PORT,
    CONF_TESLAMATE_TOKEN,
    CONF_TENANT_FILE,
    CONF_TOKEN_FILE,
    CONF_USABLE_BATTERY_KWH,
    CONF_VIN,
    CONF_XPENG_EMAIL,
    DEFAULT_CAPTURE_PATH,
    DEFAULT_CERT_DIR,
    DEFAULT_CONFIGURED_DEVICE_ID,
    DEFAULT_MQTT_REFRESH_INTERVAL,
    DEFAULT_PARKED_SCAN_INTERVAL,
    DEFAULT_REST_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SERVER,
    DEFAULT_TENANT_FILE,
    DEFAULT_TESLAMATE_PORT,
    DEFAULT_USABLE_BATTERY_KWH,
    DOMAIN,
    MQTT_DISCOVERY_CANDIDATES,
    PLATFORMS,
)
from .coordinator import XPengDataCoordinator
from .mqtt_client import XPengMQTTClient
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

# Exponential backoff for auth failures
AUTH_FAILURE_BASE_COOLDOWN = 60  # seconds (doubles each failure)
AUTH_FAILURE_MAX_BACKOFF = 900  # 15 minutes max
AUTH_FAILURE_REAUTH_THRESHOLD = 5  # trigger reauth UI after this many consecutive failures
#: ...and only after this long failing, as well as that many times.
#:
#: Five failures arrive in about five minutes (the retry cooldowns are 45-60s), and
#: an unattended renewal takes longer than that: its validity check runs on a poll
#: and a full login solves the slider captcha, which took 61s on its own when
#: measured on 2026-10-17 (token expired 11:42, card raised 11:50, renewal imported
#: 11:54). Asking before the route that actually works has had its turn produces a
#: re-onboarding card for credentials that fix themselves - which is what the
#: user saw. Ten minutes lets the renewal win the race it should win.
REAUTH_MIN_FAILING_SECONDS = 600

# Stable id for the persistent Repairs card raised when the credentials can no
# longer be renewed. One per domain is enough: it names the affected entry in
# its placeholders, and HA shows a single card per (domain, issue_id).
_REAUTH_ISSUE_ID = "reauth_required"

#: Version of the small store that carries the watchdog's escalation clock across
#: a reload (and a restart). See ``_silence_watchdog``: the 12h gap it enforces is
#: a promise about the car, so it cannot live only in the session's memory.
_WATCHDOG_STORE_VERSION = 1

def _masked_vin(entry: ConfigEntry) -> str:
    """This entry's VIN with its uniquely-identifying tail hidden.

    Falls back to the raw value only if masking is unavailable, because a
    repair card is far more likely to be shared than a log line.
    """
    raw = str(entry.data.get(CONF_VIN, "") or "").strip()
    if not raw:
        return "(unknown)"
    try:
        from .vin_descriptor import mask_vin

        return mask_vin(raw)
    except Exception:  # pragma: no cover - never block the card on this
        return raw

def _raise_reauth_repair(
    hass: HomeAssistant, entry: ConfigEntry, reason: str
) -> None:
    """Raise a persistent Repairs card saying the credentials are dead.

    `async_start_reauth` only surfaces a transient prompt, and for this
    integration that prompt cannot be completed in-app at all: XPENG's slider
    captcha is unsolvable from inside Home Assistant, and an imported entry has
    no stored password. So the user gets a toast that vanishes, while the
    entities stay dead with nothing telling them what to do.

    A Repair issue is persistent, appears in Settings -> Repairs, and carries
    the instructions for the one route that works (re-run the onboarding tool
    and import the new pair). It is marked fixable because `repairs.py` offers
    exactly that import as a flow, so the user can paste the new pair straight
    from the card instead of hunting for the integration's options.

    `is_persistent=True` matters here: without it the card would vanish on
    restart and not come back until the refresh loop failed the threshold again,
    leaving dead entities with nothing explaining why.
    """
    try:
        from homeassistant.helpers import issue_registry as ir
    except Exception:  # pragma: no cover - HA always provides this
        _LOGGER.debug("issue_registry unavailable; skipping repair card")
        return

    translation = (
        "login_expired" if reason == "login_expired" else "credentials_rejected"
    )
    try:
        ir.async_create_issue(
            hass,
            DOMAIN,
            _REAUTH_ISSUE_ID,
            is_fixable=True,
            is_persistent=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key=translation,
            translation_placeholders={
                # Masked, not raw: a Repairs card is exactly the kind of screen
                # that ends up in a screenshot or a support thread. Positions
                # 12-17 are the uniquely identifying part of a VIN (see
                # vin_descriptor.mask_vin), and the entry title already names
                # the vehicle, so nothing is lost by hiding them.
                "vin": _masked_vin(entry),
                "entry": entry.title or entry.entry_id,
            },
            # The repair flow needs to know which entry to update.
            data={"entry_id": entry.entry_id},
        )
        _LOGGER.info(
            "Raised repair issue %r: credentials for %s need re-onboarding",
            _REAUTH_ISSUE_ID,
            entry.title or entry.entry_id,
        )
    except Exception:
        # Never let a UI nicety break credential handling.
        _LOGGER.debug("could not create repair issue", exc_info=True)

def _ha_reauth_issue_id(entry: ConfigEntry) -> str:
    """Home Assistant's own reauth card id for this entry.

    ``entry.async_start_reauth()`` creates an issue of its own, and HA only clears
    it when *its* flow finishes. That is the wrong ending for an unattended
    install: the renewal app imports a fresh token pair on its own schedule, the
    credentials work again, and the card stays (measured 2026-10-17: card raised
    11:50, renewal app imported at 11:54, card still listed minutes later). The id
    is HA's, so it is spelled out here rather than imported — observed verbatim in
    the live registry as ``config_entry_reauth_xpeng_01M4SK7V1B95KZFPGFGZGK0CM9``.

    The *domain* half of the registry key is not ours: see ``_HA_ISSUE_DOMAIN``.
    """
    return f"config_entry_reauth_{DOMAIN}_{entry.entry_id}"


#: Domain Home Assistant files its own ``config_entry_reauth_*`` cards under.
#:
#: ``ConfigEntry.async_start_reauth()`` calls
#: ``ir.async_create_issue(hass, HOMEASSISTANT_DOMAIN,
#: f"config_entry_reauth_{entry.domain}_{entry.entry_id}",
#: ..., issue_domain=entry.domain, ...)``. The issue registry is keyed by the
#: pair (domain, issue_id), and HA uses *its own* domain for that pair, not the
#: integration's — the integration's domain shows up only as ``issue_domain``.
#: Deleting under ``DOMAIN`` therefore matches nothing and silently does nothing,
#: which is exactly what 0.6.11 shipped: it got the id right and never removed
#: the card. Measured on the live install 2026-10-20, three days and many
#: renewals after the card was raised on 2026-10-17: the card was still listed,
#: filed under domain "homeassistant" with ``issue_domain="xpeng"``.
_HA_ISSUE_DOMAIN = "homeassistant"


def _clear_reauth_repair(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the Repairs cards once credentials work again.

    Both of them: ours, and HA's own reauth prompt — each deleted in the domain it
    was created under. Deleting ours alone was why an unattended renewal left a
    "credentials expired, reauthenticate" card behind while the entities were
    already working; deleting HA's under *our* domain was why the card survived
    even after 0.6.11.
    """
    try:
        from homeassistant.helpers import issue_registry as ir
    except Exception:  # pragma: no cover
        return
    for domain, issue_id in (
        (DOMAIN, _REAUTH_ISSUE_ID),
        (_HA_ISSUE_DOMAIN, _ha_reauth_issue_id(entry)),
    ):
        try:
            ir.async_delete_issue(hass, domain, issue_id)
        except Exception:
            _LOGGER.debug(
                "could not delete repair issue %s/%s", domain, issue_id, exc_info=True
            )

def _record_credential_success(refresh_state: dict | None) -> None:
    """Mark the credential loop healthy, for the credentials-valid sensor.

    Called only where a refresh actually produced a usable password, never
    merely where it failed to raise: a loop that runs on schedule and mints
    nothing is precisely the silent failure that sensor exists to expose.
    """
    if not isinstance(refresh_state, dict):
        return
    refresh_state["last_success_at"] = time.time()
    refresh_state["last_error"] = None
    refresh_state["consecutive_failures"] = 0

def _record_credential_failure(refresh_state: dict | None, reason: str) -> None:
    """Note why a refresh attempt failed, for the credentials-valid sensor.

    Deliberately refuses to overwrite a reason already recorded for the same
    attempt: the deeper site knows more than the caller's summary does
    (`_refresh_login_tokens_sync` carries the API's own message), and letting the
    summary win would bury the cause under "refresh returned no password".
    """
    if not isinstance(refresh_state, dict):
        return
    if not refresh_state.get("last_error"):
        refresh_state["last_error"] = reason

def _start_reauth_if_idle(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ask HA to reauthenticate *entry*, unless a reauth prompt is already up.

    This used to be gated on the entry having a stored XPENG email *and*
    password, so an entry imported from a token / MQTT-credential file — which
    stores neither — never started a reauth flow at all. The retry loop kept
    logging "triggering reauth" every cycle while the UI stayed silent for
    days (observed 2026-10-04: 44 warnings, zero prompts, zero data for ~4d).

    The Google sign-in path needs no stored credentials, so every entry can
    reauth; which path is used is decided in `config_flow.async_step_reauth`.
    Calling `async_start_reauth` repeatedly while a flow is already waiting is
    harmless but noisy, so short-circuit when one is in progress.

    Every caller reaches here only once credentials are genuinely unusable, so
    this is also the single place that raises the persistent Repairs card.
    """
    from homeassistant.config_entries import SOURCE_REAUTH

    _raise_reauth_repair(hass, entry, "login_expired")

    entry_id = entry.entry_id
    for flow in hass.config_entries.flow.async_progress():
        context = flow.get("context") or {}
        if (
            context.get("source") == SOURCE_REAUTH
            and context.get("entry_id") == entry_id
        ):
            return
    entry.async_start_reauth(hass)

def _load_api_context(entry: ConfigEntry) -> tuple[dict | None, dict | None]:
    """Load tokens and tenant from the best available source.

    Priority:
      1. Config entry data (from in-HA login)
      2. Token file + tenant file (from CLI login)

    Returns (tokens, tenant) or (None, None).
    """
    # Source 1: config entry stored tokens
    entry_tokens = entry.data.get(CONF_LOGIN_TOKENS)
    entry_tenant_raw = entry.data.get(CONF_TENANT_DATA)
    if entry_tokens and entry_tenant_raw:
        from .xpeng_api import deserialize_tenant

        try:
            tenant = deserialize_tenant(entry_tenant_raw)
            return entry_tokens, tenant
        except Exception as err:
            _LOGGER.warning("Failed to deserialize config entry tenant: %s", err)

    # Source 2: token file + tenant file
    token_file = entry.options.get(CONF_TOKEN_FILE, "").strip()
    tenant_file = entry.options.get(
        CONF_TENANT_FILE, DEFAULT_TENANT_FILE
    ).strip()
    if token_file and os.path.isfile(token_file):
        from .xpeng_api import load_login_tokens, load_tenant_cache, fetch_tenant, save_tenant_cache

        try:
            tokens = load_login_tokens(token_file)
            try:
                tenant = load_tenant_cache(tenant_file)
            except (FileNotFoundError, ValueError, KeyError):
                _LOGGER.info("Fetching fresh tenant data for file-based tokens")
                tenant = fetch_tenant(None)
                save_tenant_cache(tenant_file, tenant)
            return tokens, tenant
        except Exception as err:
            _LOGGER.warning("Failed to load token/tenant files: %s", err)

    return None, None

def _available_tenants(
    entry: ConfigEntry, tenant_file: str
) -> list[tuple[dict, str]]:
    """The tenants this install already has, and where each came from.

    Same priority as `_load_api_context()` — the entry's own copy first, then
    the tenant file — but this never fetches. Setup only needs them to learn
    which device id the credential material was minted under, and stalling on
    the network to answer that is how an install with no working id looks hung.

    An empty list means there is nothing to adopt, which is what a genuine
    first run looks like.
    """
    tenants: list[tuple[dict, str]] = []

    raw = entry.data.get(CONF_TENANT_DATA)
    if isinstance(raw, dict) and raw:
        from .xpeng_api import deserialize_tenant

        try:
            tenants.append(
                (deserialize_tenant(raw), "the tenant in the entry data")
            )
        except Exception:  # noqa: BLE001 - fall through to the tenant file
            _LOGGER.debug("Could not deserialize the entry tenant", exc_info=True)

    if tenant_file:
        from .xpeng_api import load_tenant_cache

        try:
            tenants.append(
                (load_tenant_cache(tenant_file), f"the tenant file {tenant_file}")
            )
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001 - a broken cache is not fatal here
            _LOGGER.debug(
                "Could not read the tenant file %s", tenant_file, exc_info=True
            )

    return tenants

def _tenant_device_id(tenant: dict | None) -> str:
    """The device id a tenant recorded, or "" when it has none."""
    if not isinstance(tenant, dict):
        return ""
    return str(tenant.get("deviceId") or "").strip()

def _tokens_device_id(tokens: dict | None) -> str:
    """The device id a loaded login-token payload records, or "".

    `load_login_tokens()` returns the inner token JSON, which has no top-level
    `deviceId` — there the id survives only inside the MQTT clientId
    (`xmart:app:<uid>:<deviceId>`, see `xpeng_api._get_device_id`). A caller
    that hands over the raw wrapper, or a payload that carries a tenant, has it
    directly.
    """
    if not isinstance(tokens, dict):
        return ""

    tenant = tokens.get("tenant")
    for candidate in (
        tokens.get("deviceId"),
        tenant.get("deviceId") if isinstance(tenant, dict) else "",
    ):
        stored = str(candidate or "").strip()
        if stored:
            return stored

    account = tokens.get("mqttAccount")
    client_id = str(
        (account.get("clientId") if isinstance(account, dict) else "")
        or tokens.get("clientId")
        or ""
    )
    # The clientId is `xmart:app:<uid>:<deviceId>`. Only trust a segment that is
    # the full id length: a malformed clientId would otherwise hand back a
    # fragment, and persisting that is worse than minting a fresh id.
    from .xpeng_api import DEVICE_ID_MAX_LENGTH

    parts = client_id.split(":")
    if len(parts) >= 4 and len(parts[3].strip()) == DEVICE_ID_MAX_LENGTH:
        return parts[3].strip()
    return ""

async def _async_resolve_configured_device_id(
    hass: HomeAssistant,
    entry: ConfigEntry,
    tenant_file: str,
    configured_device_id: str,
) -> str:
    """Resolve this install's device id, persist it, and return it.

    The shipped default deliberately no longer pins a device id, so every
    install needs one of its own — but an EXISTING install must adopt the id its
    credential material already records rather than mint a new one. The tenant's
    key pool is sealed with `md5(device_id)` as its AES key and the request is
    signed as that device, so a tenant can only be used under the id it was
    minted with. Minting over it makes every REST call answer 12010104 "Login
    status expired" - indistinguishable from genuinely dead tokens, and the
    failure this replaced. (An install whose token pair predates this option is
    exactly that case: its tenant records one device id while its options are
    empty.)

    Adoption order: the entry's stored option, else the entry-stored tenant,
    else the tenant file, else the login tokens. A tenant that records no
    `deviceId` is skipped rather than treated as an answer, so an entry written
    by a build whose `_serialize_tenant()` dropped the field still falls through
    to the tenant file instead of minting over a live binding. Only when none of
    those records an id does a genuine first run mint one. A failure here is
    logged but not fatal: setup still proceeds, and the REST path raises its own
    actionable error when it needs the id.
    """
    # File loading (including reads/deserialization) must finish off the loop.
    # Entry persistence below must stay on the loop: async_update_entry is not
    # thread-safe, so offloading this entire resolver would be wrong.
    tenants = await hass.async_add_executor_job(
        _available_tenants, entry, tenant_file
    )

    if configured_device_id:
        _warn_on_device_id_mismatch(entry, configured_device_id, tenants)
        return configured_device_id

    try:
        from .xpeng_api import load_login_tokens, mint_device_id

        configured_device_id = ""
        adopted_from = ""
        for tenant, source in tenants:
            stored = _tenant_device_id(tenant)
            if stored:
                configured_device_id, adopted_from = stored, source
                break

        if not configured_device_id:
            token_file = entry.options.get(CONF_TOKEN_FILE, "").strip()
            if token_file:
                try:
                    tokens = await hass.async_add_executor_job(
                        load_login_tokens, token_file
                    )
                    configured_device_id = _tokens_device_id(tokens)
                except FileNotFoundError:
                    pass
                except Exception:  # noqa: BLE001 - a bad token file is not fatal
                    _LOGGER.debug(
                        "Could not read a device id from %s",
                        token_file,
                        exc_info=True,
                    )
                if configured_device_id:
                    adopted_from = f"the login tokens {token_file}"

        if configured_device_id:
            _LOGGER.info(
                "Adopted existing device id for entry %s: %s (from %s)",
                entry.entry_id,
                configured_device_id,
                adopted_from or "existing credential material",
            )
        else:
            # Nothing anywhere records one: a genuine first run mints.
            configured_device_id = mint_device_id()
            _LOGGER.info(
                "Minted new device id for entry %s: %s",
                entry.entry_id,
                configured_device_id,
            )

        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_CONFIGURED_DEVICE_ID: configured_device_id,
            },
        )
    except Exception:  # noqa: BLE001 - never block setup over an identity
        _LOGGER.warning(
            "Could not resolve a device id for entry %s", entry.entry_id,
            exc_info=True,
        )

    _warn_on_device_id_mismatch(entry, configured_device_id, tenants)
    return configured_device_id

def _warn_on_device_id_mismatch(
    entry: ConfigEntry,
    configured_device_id: str,
    tenants: list[tuple[dict, str]],
) -> None:
    """Say plainly where a tenant and a configured id disagree.

    That pair cannot work: the key pool is sealed with `md5(device_id)` and the
    request is signed as that device, so XPENG answers 12010104 "Login status
    expired" and operators chase phantom credential expiry for days. Every
    available tenant is checked, not just the first. Never repair it
    automatically — the correct id is not knowable from here, and guessing
    rotates a working one.
    """
    if not configured_device_id:
        return
    for tenant, source in tenants:
        tenant_device_id = _tenant_device_id(tenant)
        if tenant_device_id and tenant_device_id != configured_device_id:
            _LOGGER.warning(
                "Device id mismatch for entry %s: the entry is configured as %s "
                "but %s records deviceId %s. Every REST/API call will fail with "
                "12010104 \"Login status expired\" until this install is "
                "re-onboarded, because the tenant's key pool is sealed against "
                "md5(deviceId).",
                entry.entry_id,
                configured_device_id,
                source or "the available tenant",
                tenant_device_id,
            )

async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Keep legacy IDs working for existing users until they opt into compact mode."""
    from .entity_layout import CONF_LEGACY_ENTITIES

    if entry.version > 2:
        return False
    if entry.version < 2:
        options = dict(entry.options)
        options.setdefault(CONF_LEGACY_ENTITIES, True)
        hass.config_entries.async_update_entry(entry, options=options, version=2)
    return True


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register integration-level services and the card (once, not per entry).

    ``xpeng.import_credentials`` exists so a renewal can be driven from an
    automation or a script instead of a human clicking Fix on the Repairs card: it
    runs exactly the same validated import, so an unattended renewal is never a
    weaker path than a manual one. The import is local to avoid a circular import
    (credential_import reads helpers from this module).
    """
    from homeassistant.core import ServiceCall, SupportsResponse
    from homeassistant.exceptions import HomeAssistantError

    from .config_flow import _TokenImportError
    from .credential_import import (
        IMPORT_CREDENTIALS_SCHEMA,
        SERVICE_IMPORT_CREDENTIALS,
        async_import_credentials,
    )

    from .frontend import async_setup_card

    await async_setup_card(hass)

    if hass.services.has_service(DOMAIN, SERVICE_IMPORT_CREDENTIALS):
        return True

    def _entry_for_import(entry_id: str | None) -> ConfigEntry | None:
        if entry_id:
            return hass.config_entries.async_get_entry(entry_id)
        entries = hass.config_entries.async_entries(DOMAIN)
        return entries[0] if entries else None

    async def _handle_import_credentials(call: ServiceCall) -> dict[str, Any]:
        entry = _entry_for_import(call.data.get("entry_id"))
        if entry is None:
            raise HomeAssistantError("No XPENG config entry to import credentials into")
        try:
            return await async_import_credentials(
                hass,
                entry,
                tokens_json=call.data.get("tokens_json", ""),
                tokens_path=call.data.get("tokens_path", ""),
                tenant_json=call.data.get("tenant_json", ""),
                tenant_path=call.data.get("tenant_path", ""),
                vin=call.data.get("vin", ""),
            )
        except _TokenImportError as err:
            # The key names the failure ("invalid_tokens", "invalid_tenant", ...);
            # the payload is never included, because these are live credentials.
            raise HomeAssistantError(
                f"Credential import failed: {err.key}"
            ) from err
        except Exception as err:  # noqa: BLE001 - report a reason, not a traceback
            raise HomeAssistantError(
                f"Credential import failed: {type(err).__name__}"
            ) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_CREDENTIALS,
        _handle_import_credentials,
        schema=IMPORT_CREDENTIALS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    _LOGGER.debug("Registered %s.%s", DOMAIN, SERVICE_IMPORT_CREDENTIALS)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up XPENG from a config entry."""
    from .frontend import async_setup_card

    await async_setup_card(hass)
    client_id = entry.data[CONF_CLIENT_ID]
    password = entry.data[CONF_PASSWORD]
    server = entry.data.get(CONF_SERVER, DEFAULT_SERVER)
    vin = entry.data[CONF_VIN]

    parts = client_id.split(":")
    if len(parts) < 3:
        _LOGGER.error("Invalid client_id format: %s", client_id)
        return False
    user_id = parts[2]

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    parked_scan_interval = entry.options.get(
        CONF_PARKED_SCAN_INTERVAL, DEFAULT_PARKED_SCAN_INTERVAL
    )
    token_file = entry.options.get(CONF_TOKEN_FILE, "").strip()
    tenant_file = entry.options.get(CONF_TENANT_FILE, DEFAULT_TENANT_FILE).strip()
    # The stored option wins only while it still holds the files. A default written when
    # the certificates lived somewhere else outlives the move, and the failure is a
    # FileNotFoundError deep inside the TLS context with no entities to show for it.
    from .xpeng_api import resolve_cert_dir

    stored_cert_dir = entry.options.get(CONF_CERT_DIR, "").strip()
    cert_dir = resolve_cert_dir(stored_cert_dir)
    if stored_cert_dir and cert_dir != stored_cert_dir:
        _LOGGER.warning(
            "Certificates are not in the configured directory %s; using %s instead. "
            "Set the integration's 'Certificate directory' option to %s to make this "
            "permanent.",
            stored_cert_dir, cert_dir, cert_dir,
        )
    configured_device_id = entry.options.get(
        CONF_CONFIGURED_DEVICE_ID, DEFAULT_CONFIGURED_DEVICE_ID
    ).strip()
    mqtt_refresh_interval = entry.options.get(
        CONF_MQTT_REFRESH_INTERVAL, DEFAULT_MQTT_REFRESH_INTERVAL
    )
    rest_scan_interval = entry.options.get(
        CONF_REST_SCAN_INTERVAL, DEFAULT_REST_SCAN_INTERVAL
    )
    # A notification poll is six requests (one per category), so this has a floor
    # rather than being free to set to zero: below a minute the API's rate limiting,
    # not the car, would be deciding the delay.
    notification_poll_interval = max(
        MIN_NOTIFICATION_POLL_INTERVAL,
        int(
            entry.options.get(
                CONF_NOTIFICATION_POLL_INTERVAL, DEFAULT_NOTIFICATION_POLL_INTERVAL
            )
        ),
    )

    # Determine if API operations are available
    has_entry_tokens = bool(
        entry.data.get(CONF_LOGIN_TOKENS) and entry.data.get(CONF_TENANT_DATA)
    )
    has_file_tokens = bool(token_file and os.path.isfile(token_file))
    api_available = has_entry_tokens or has_file_tokens

    # 1. Create coordinator (passive data store)
    # The command policy is resolved once per setup from the entry's options
    # and handed to the coordinator, which is how entities reach it.
    coordinator = XPengDataCoordinator(
        hass, vin=vin, command_policy=policy_for_entry(entry)
    )
    # Which account these credentials belong to, for the account sensor. The
    # tokens cannot answer it (they are a tenant-sealed blob of teeId +
    # deviceId), so it comes from what the onboarding tool recorded, falling
    # back to the email an in-HA login stored on the entry. Absent on entries
    # imported before the tool recorded it - the sensor then reports the uid.
    try:
        _identity_tokens, _ = _load_api_context(entry)
    except Exception:
        _identity_tokens = None
    # The ENTRY is the durable home for this: entry data lives in HA's own
    # storage, so the value survives reboots, and survives the token file being
    # deleted or replaced by a later re-onboard that forgot to record it. The
    # token file is only a fallback for entries written before the tool started
    # recording it (and for a first run before the entry is updated).
    coordinator.account_email = str(
        entry.data.get(CONF_XPENG_EMAIL)
        or (_identity_tokens or {}).get("account_email")
        or ""
    ).strip()
    coordinator.account_uid = str((_identity_tokens or {}).get("uid") or "").strip()
    # Restore last known vehicle state from disk so sensors aren't "unknown"
    # after a restart while the car is asleep — no car contact involved.
    await coordinator.async_load_cached()
    # ICCID has no app-tier API source — populate from manual option if set
    iccid = entry.options.get(CONF_ICCID, "").strip()
    if iccid:
        coordinator.iccid = iccid
    # When the credentials last arrived, for the last-renewal sensor. Read from
    # entry data rather than a clock in this process: a restart must not erase it,
    # and the entry reloads after every import, so this is always current.
    coordinator.set_last_renewal(entry.data.get(CONF_LAST_RENEWAL))
    # Pack size for the parked-consumption kWh estimate (options override).
    try:
        coordinator.usable_battery_kwh = float(
            entry.options.get(
                CONF_USABLE_BATTERY_KWH, DEFAULT_USABLE_BATTERY_KWH
            )
        )
    except (TypeError, ValueError):
        coordinator.usable_battery_kwh = DEFAULT_USABLE_BATTERY_KWH

    # Credential-refresh state (with exponential backoff), for the refresh
    # timers below and for the health entities.
    #
    # Declared here, ahead of the pre-connect mint, because that mint is the
    # first thing that can succeed or fail. Left where it used to be — after the
    # MQTT client was built — the credentials-valid sensor read "no successful
    # refresh yet" for the first ~45s after every restart even though a fresh
    # password had just been minted: a false alarm in exactly the window where a
    # refused first connect is most likely.
    refresh_state: dict[str, Any] = {
        "last_attempt": 0.0,
        "refreshing": False,
        "consecutive_failures": 0,
        # For the credentials-valid diagnostic: when the loop last produced a
        # usable password (a plain `time.time()` float, like the other timer
        # records) and why it last did not.
        "last_success_at": None,
        "last_error": None,
        # When the current run of failures began, for REAUTH_MIN_FAILING_SECONDS.
        "first_failure_at": 0.0,
    }

    # 1b. Mint a FRESH MQTT credential before the first connect.
    #
    # Its expiry rides into the client, so the first connect does not mint again
    # (mint-on-demand only mints when the credential in hand has lapsed).
    initial_expires_at = 0.0
    #
    # The stored `password` comes from the token file's `mqttAccount`, which was
    # minted whenever onboarding last ran. Those expire in 45-90s
    # (`expires_in=72s` observed), so on any restart more than a minute later the
    # very first CONNECT presents a dead password and is refused with
    # "Not authorized". The 45s refresh timer then fixes it, but only after the
    # client has already failed its first attempt - one avoidable refusal per
    # restart, and the reason a freshly restarted instance sits in
    # "Reconnecting" (observed 2026-10-09 on a fresh install).
    #
    # The periodic refresh below already mints-and-stages correctly; this is the
    # same step, moved to the front of setup where the first connect needs it.
    try:
        _fresh = await hass.async_add_executor_job(
            _refresh_creds_sync, entry, refresh_state
        )
        if _fresh and _fresh.get("password"):
            from .mqtt_client import expires_at_from_account

            initial_expires_at = expires_at_from_account(_fresh)
            client_id_new = _fresh.get("clientId") or ""
            if client_id_new:
                client_id = client_id_new
                _parts = client_id_new.split(":")
                if len(_parts) >= 3:
                    user_id = _parts[2]
            password = _fresh["password"]
            server = _fresh.get("serverSsl") or server
            refreshed = _fresh.get("_refreshed_tokens")
            if refreshed:
                hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_LOGIN_TOKENS: refreshed}
                )
            _LOGGER.info(
                "Minted a fresh MQTT credential before the first connect "
                "(expires in ~%ss)",
                _fresh.get("expiresIn", "?"),
            )
    except Exception as err:
        # Never block setup: the refresh timer is still there as the safety net,
        # and the client mints on demand when it needs a credential.
        _record_credential_failure(
            refresh_state, f"startup credential mint failed: {err}"
        )
        _LOGGER.warning(
            "Could not mint a fresh MQTT credential before connecting; "
            "falling back to the stored one", exc_info=True,
        )

    # 2. Create MQTT client
    #
    # The device id the broker authenticated rides in the account clientId
    # (`xmart:app:<uid>:<deviceId>`; the app appends `:2.0:V<version>` before it
    # connects). It is what identity-scoped topics are built from, so the
    # device-scoped push channel the app subscribes can be probed as
    # `Xp/Msg/Push/{device_id}` in the listener option.
    _cid_parts = str(client_id or "").split(":")
    mqtt_device_id = (
        _cid_parts[3].strip() if len(_cid_parts) >= 4 else ""
    ) or configured_device_id
    mqtt_client = XPengMQTTClient(
        client_id=client_id,
        password=password,
        server=server,
        user_id=user_id,
        device_id=mqtt_device_id,
        cert_dir=cert_dir,
        expires_at=initial_expires_at,
    )

    # Mint on demand: the app's model. A credential is minted immediately before
    # a connect when the one in hand has lapsed (``hasAccount()``'s uid +
    # expireTime gate), and a live session is never rotated. That is what keeps
    # the mint count low - and, since every mint/rotation is a chance for the
    # broker to see a superseded password, what keeps the refusals away.
    #
    # The provider runs on the thread that is about to connect (an executor or a
    # timer thread), so it may block; the lock keeps it from racing the timer.
    _mint_lock = threading.Lock()

    def _mint_with_lock() -> dict[str, Any] | None:
        with _mint_lock:
            return _refresh_creds_sync(entry, refresh_state)

    def _persist_minted_credentials(minted: dict[str, Any]) -> None:
        """Write a minted credential to the entry and stamp the sensor (loop)."""
        password_new = str(minted.get("password") or "")
        client_id_new = str(minted.get("clientId") or "")
        if not password_new:
            return
        coordinator.set_credential_timestamp()
        refresh_state["skip_reload"] = True
        data = {**entry.data, CONF_PASSWORD: password_new}
        if client_id_new:
            data[CONF_CLIENT_ID] = client_id_new
        hass.config_entries.async_update_entry(entry, data=data)

    def _mint_for_connect() -> dict[str, Any] | None:
        """Blocking provider the client calls when a connect needs a credential."""
        from .mqtt_client import expires_at_from_account

        minted = _mint_with_lock()
        if not minted or not minted.get("password"):
            return None
        hass.loop.call_soon_threadsafe(_persist_minted_credentials, minted)
        return {
            "clientId": minted.get("clientId", ""),
            "password": minted["password"],
            "serverSsl": minted.get("serverSsl", ""),
            "expires_at": expires_at_from_account(minted),
        }

    mqtt_client.set_credentials_provider(_mint_for_connect)

    # Publish the same directory for the REST client. Its helpers are called
    # with config=None throughout, so without this the REST path would use the
    # packaged certs and fail when the user keeps theirs elsewhere.
    try:
        from .xpeng_api import set_cert_dir
        set_cert_dir(cert_dir or DEFAULT_CERT_DIR)
    except Exception:  # noqa: BLE001 - never block setup over a cert hint
        _LOGGER.debug("Could not publish cert_dir to the REST client", exc_info=True)

    # Resolve this install's device identity once and keep it in the entry
    # options, so every restart and reload signs as the same device.
    configured_device_id = await _async_resolve_configured_device_id(
        hass, entry, tenant_file, configured_device_id
    )

    if configured_device_id and not mqtt_client.device_id:
        # Older 3-field clientId: the resolved identity is the only source.
        mqtt_client.set_device_id(configured_device_id)

    if mqtt_client.device_id:
        _LOGGER.info(
            "XPENG device id %s — device push channel %s (the listener option "
            "accepts Xp/Msg/Push/{device_id})",
            mqtt_client.device_id,
            mqtt_client.push_topic,
        )

    # Publish the same id for the REST client (mirrors set_cert_dir above).
    try:
        from .xpeng_api import set_configured_device_id

        if configured_device_id:
            set_configured_device_id(configured_device_id)
    except Exception:  # noqa: BLE001 - never block setup over an identity hint
        _LOGGER.debug(
            "Could not publish configured_device_id to the REST client",
            exc_info=True,
        )

    # Deliberately no credential timestamp here. It means "the last mint that
    # actually produced a password", and nothing has been minted yet: seeding it
    # at setup made a *failed* startup mint look like a successful refresh -
    # which is precisely what the credential-age sensor is read to rule out.
    # It reads "Unknown" for the seconds until the first refresh cycle lands.

    # Wire MQTT -> coordinator (with parked→driving transition detection)
    def _on_vehicle_state(data: dict[str, Any]) -> bool:
        """Offer an MQTT state to the coordinator, answering "is this data?".

        Runs on the MQTT network thread, and the answer has to be given *here*
        rather than on the event loop: the client stamps its "a message carried
        data" clock on the strength of the return value, and the silence
        watchdog escalates to a force-upload on that clock. The coordinator
        repeats the replay check when it applies the state (it is the one that
        owns the odometer floor, and it is also the guard for the REST widget),
        but only this call can keep a replayed snapshot out of the clock.

        Measured 2026-10-21: a wedged fanout replayed the pre-drive snapshot for
        2.5 h. Every replay looked like data, so the escalation never fired and
        nothing self-healed; the sensors flapped between the replay's 52% and
        the REST fallback's 47% until a human force-uploaded.
        """
        odometer = odometer_from_state(data)
        if is_replay(odometer, coordinator.max_odometer):
            _LOGGER.warning(
                "Ignoring a replayed vehicle state from the push fanout: "
                "odometer %s km is behind the %s km already held",
                odometer, coordinator.max_odometer,
            )
            return False
        # The energy-side replay check. While a car is parked its odometer does
        # not move, so the check above cannot see a replayed snapshot - but a
        # parked, idle car's state of charge cannot rise either. Keeping such a
        # state out of this clock matters for the same reason: a fanout that
        # only resends an older parked snapshot must still age into the
        # watchdog's force-upload (see energy_guard.py for the measurement).
        if coordinator.parked_energy_replay(data):
            _LOGGER.warning(
                "Ignoring a replayed vehicle state from the push fanout: SOC %s%% "
                "is above the %s%% already held while parked — resending an older "
                "snapshot",
                soc_of(data), soc_of(coordinator.last_full_data),
            )
            return False
        was_parked = coordinator.vehicle_parked
        hass.loop.call_soon_threadsafe(
            _handle_vehicle_state, data, was_parked
        )
        return True

    def _log_drive_evidence(
        before: dict[str, Any],
        after: dict[str, Any],
        previous_data_age: float | None,
    ) -> None:
        """Log what a frame said about driving, and any verdict change.

        Issue #3 (2026-10-21): a 45-minute mid-drive stall could not be explained
        because neither the classification inputs nor the verdict itself were
        logged. These lines are the answer to that: the values the classifier
        reads, the verdict and its reason, and GPS as presence/change only -
        never as coordinates.
        """
        gps_before = before.get("gps")
        gps_after = after.get("gps")
        _LOGGER.debug(
            "Drive evidence: parked=%s (%s) shift_state=%r instant_speed=%r "
            "interval_speed=%r gps=%s gps_changed=%s coord_valid=%r",
            after.get("parked"),
            after.get("reason"),
            after.get("shift_state"),
            after.get("instant_speed"),
            after.get("interval_speed"),
            "present" if gps_after else "absent",
            gps_before != gps_after,
            after.get("coord_valid"),
        )
        if before.get("parked") != after.get("parked"):
            _LOGGER.info(
                "Parking verdict %s -> %s (%s)%s",
                before.get("parked"),
                after.get("parked"),
                after.get("reason"),
                (
                    f"; previous data was {previous_data_age:.0f}s old"
                    if previous_data_age is not None
                    else ""
                ),
            )

    def _handle_vehicle_state(data: dict[str, Any], was_parked: bool | None) -> None:
        """Update coordinator and handle parked→driving transition."""
        before = coordinator.parking_view
        previous_timestamp = coordinator.last_data_timestamp
        previous_data_age = (
            (datetime.now(timezone.utc) - previous_timestamp).total_seconds()
            if previous_timestamp is not None
            else None
        )
        coordinator.set_vehicle_data(data)
        _log_drive_evidence(before, coordinator.parking_view, previous_data_age)
        # If car just started moving, force immediate query
        if was_parked and coordinator.vehicle_parked is False:
            _LOGGER.info("Vehicle transitioned from parked to driving — forcing query")
            # async_add_executor_job already schedules the job and returns a
            # Future — do NOT wrap it in async_create_task (expects a coroutine).
            hass.async_add_executor_job(mqtt_client.send_query, vin)

    mqtt_client.set_state_callback(_on_vehicle_state)
    # The REST fallback merge happens in the coordinator, so the client's own
    # partial-merge baseline has to be told about it - otherwise the next
    # charge-less partial re-broadcasts the pre-merge snapshot over it.
    coordinator.set_baseline_sink(mqtt_client.adopt_state)
    # A wake frame carries the tyre pressures as 0 before the TPMS sensors
    # answer; carry the last real readings forward instead of publishing a tyre
    # that reinflates two seconds later (see sentinel_guard.py).
    mqtt_client.set_instant_sanitizer(with_previous_tpms)

    # Wire auth failure -> credential refresh
    def _on_auth_failure() -> None:
        """Called from MQTT thread when broker rejects credentials."""
        # A refusal when the password in hand is already newer than the one the
        # client was built with is not an expired login - paho auto-reconnects
        # with the credentials from client creation, which the ~45s refresh has
        # since replaced. Rebuild with what we already hold: no API call, and
        # none of the cooldown below, which would otherwise spend 45s failing
        # with a password we know is superseded. (Observed 2026-10-07: eight
        # `rc=Not authorized` refusals in twenty minutes while the correct
        # password sat in memory.)
        if mqtt_client.credentials_superseded:
            _LOGGER.info(
                "MQTT auth failed on a superseded password - reconnecting with "
                "the credential already in hand (no API call)"
            )

            async def _do_rotate() -> None:
                await hass.async_add_executor_job(
                    mqtt_client.reconnect, "auth failure: superseded password"
                )

            hass.loop.call_soon_threadsafe(hass.async_create_task, _do_rotate())
            return
        if not api_available:
            _LOGGER.warning(
                "MQTT auth failed and no API credentials are available to "
                "refresh with - re-import a token pair to recover"
            )
            return
        _LOGGER.info("Scheduling credential refresh...")

        async def _do_refresh() -> None:
            await _handle_auth_failure(
                hass, entry, mqtt_client, vin, refresh_state
            )

        hass.loop.call_soon_threadsafe(hass.async_create_task, _do_refresh())

    mqtt_client.set_auth_failure_callback(_on_auth_failure)

    # 3b. Wire push notification callback (msg_type=3)
    def _on_push_notification(notification: dict) -> None:
        # ACCOUNT_KICK_OFF: the account signed in elsewhere. In practice that is
        # the local renewal app, which must log in to mint the next pair and then
        # imports it. Tell the client so it retries quietly instead of minting on
        # every refused CONNECT while the import lands.
        # Imported here rather than at module scope: the test harnesses stub
        # mqtt_client down to the symbols setup itself needs, and this helper is
        # only ever called from the paho thread in a real install.
        from .mqtt_client import is_account_takeover

        if is_account_takeover(notification):
            mqtt_client.note_account_takeover(str(notification.get("message") or ""))
            hass.loop.call_soon_threadsafe(
                _record_account_takeover, dict(notification)
            )
            return
        hass.loop.call_soon_threadsafe(
            coordinator.record_notification, notification
        )

    def _record_account_takeover(notice: dict) -> None:
        """Track a takeover separately from the car's notifications.

        The broker uses the same `msg_type=3` envelope for ACCOUNT_KICK_OFF as for
        real notifications, and the renewal app logs in on this account once per
        renewal - so recording it in the notification list buried the messages the
        user actually wants behind a dozen "Account logged in on another device"
        entries a day (measured 2026-10-18 on `.162`: count 12, every one a
        renewal, and the Last-notification sensor never showed anything else).
        The takeover is still visible where it belongs: its own bus event, the
        INFO line the client logs, and `last_takeover_at` / `account_takeovers` on
        the polling-status sensor.
        """
        now = time.time()
        count = int(coordinator.poll_status.get("account_takeovers") or 0) + 1
        coordinator.set_poll_status(
            last_takeover_at=now,
            account_takeovers=count,
        )
        notice = dict(notice)
        notice["source"] = "mqtt"
        notice["timestamp"] = now
        hass.bus.async_fire("xpeng_account_takeover", notice)
        _LOGGER.info(
            "Account takeover notice #%d recorded (the renewal app logging in; "
            "expecting a credential import)", count,
        )

    mqtt_client.set_notification_callback(_on_push_notification)

    # 3b2. Wire command feedback callback (service_type != 19 responses)
    def _on_command_feedback(feedback: dict) -> None:
        hass.loop.call_soon_threadsafe(
            coordinator.record_command_feedback, feedback
        )

    mqtt_client.set_command_feedback_callback(_on_command_feedback)

    # 3c. Configure MQTT listener and raw capture. They are independent: the
    # listener decodes each frame and drives the diagnostic sensors, capture
    # writes the bytes to a file and drives nothing. See capture.py for why the
    # per-frame work has to stay this small.
    listener_enabled = entry.options.get(CONF_MQTT_LISTENER_ENABLED, False)
    listener_topics_raw = entry.options.get(CONF_MQTT_LISTENER_TOPICS, "")
    discovery_enabled = entry.options.get(CONF_MQTT_TOPIC_DISCOVERY, False)
    capture_enabled = entry.options.get(CONF_CAPTURE_ENABLED, False)

    from .mqtt_client import parse_listener_topics

    listener_topics = parse_listener_topics(
        listener_topics_raw,
        device_id=mqtt_client.device_id,
        user_id=user_id,
    )

    capture_recorder = None
    if capture_enabled:
        # Empty means "the known CAN topics", which is what someone enabling a
        # capture almost always wants; a typed list replaces them entirely.
        capture_topics = parse_listener_topics(
            entry.options.get(CONF_CAPTURE_TOPICS, "")
        ) or list(MQTT_DISCOVERY_CANDIDATES)
        preferred = entry.options.get(CONF_CAPTURE_PATH, DEFAULT_CAPTURE_PATH)
        try:
            capture_recorder, capture_dir = await hass.async_add_executor_job(
                functools.partial(
                    open_recorder,
                    preferred,
                    hass.config.path("xpeng-can"),
                    topics=capture_topics,
                )
            )
        except OSError as err:
            # A capture that cannot be written must not take the integration down:
            # the car's entities are the primary job.
            _LOGGER.error(
                "Raw capture is enabled but could not be started (%s); continuing "
                "without it", err,
            )
            capture_recorder = None
        else:
            mqtt_client.set_capture_callback(capture_recorder.submit)
            _LOGGER.info(
                "Raw capture recording to %s (%d topics); decode it offline with "
                "tools/xpeng_can_decode.py", capture_dir, len(capture_topics),
            )

    if listener_enabled or capture_recorder is not None:
        mqtt_client.configure_listener(
            enabled=True,
            topics=listener_topics,
            discovery_enabled=discovery_enabled,
            discovery_candidates=MQTT_DISCOVERY_CANDIDATES,
            extra_topics=capture_recorder.topics if capture_recorder else (),
        )

    if listener_enabled:
        # Wire listener message callback -> coordinator
        def _on_listener_message(topic: str, payload: str) -> None:
            hass.loop.call_soon_threadsafe(
                coordinator.set_listener_message, topic, payload
            )

        mqtt_client.set_listener_callback(_on_listener_message)

        _LOGGER.info(
            "MQTT listener enabled: %d manual topics, discovery=%s",
            len(listener_topics), discovery_enabled,
        )

    if listener_enabled or capture_recorder is not None:
        # Subscription results go to the coordinator when the listener wants them
        # and into the journal whenever a capture is running. A topic the broker
        # refuses is the single most useful thing a capture can record: from the
        # outside, "refused" and "silent" look identical.
        def _on_subscription_result(
            topic: str, accepted: bool, reason: str
        ) -> None:
            if capture_recorder is not None:
                capture_recorder.note(
                    "subscribe_ok" if accepted else "subscribe_refused",
                    topic=topic,
                    reason=reason,
                )
            if listener_enabled:
                hass.loop.call_soon_threadsafe(
                    coordinator.record_subscription_result, topic, accepted, reason
                )

        mqtt_client.set_subscription_result_callback(_on_subscription_result)

    # 4. Connect
    try:
        await hass.async_add_executor_job(mqtt_client.connect)
    except FileNotFoundError as err:
        _LOGGER.error("Certificate file missing: %s", err)
        return False
    except Exception as err:
        _LOGGER.error("MQTT connect error: %s", err)
        _LOGGER.warning("Will retry in background via paho reconnect")

    # 5. Initial query.
    # On a genuine HA restart (hass not yet running) we send ONE cmd_type:2
    # force-upload to wake the car, so every boot captures a full state snapshot
    # into the persisted cache and serves a complete sensor set. On an
    # integration reload / options change (hass already running) this function
    # re-runs — we must NOT wake the car then, so we send only the passive
    # state query (msg_type=4), which a sleeping car answers with a ~248-byte
    # stub without waking. The periodic timer and watchdog never wake the car.
    query_timer_state: dict[str, float] = {"last_sent": time.time()}
    if not hass.is_running and coordinator.command_policy.can_wake():
        _LOGGER.info("Cold start — one force-upload to wake the car for a full snapshot")
        await hass.async_add_executor_job(
            mqtt_client.send_command, vin, 19, {"cmd_type": 2}, 2
        )
    elif not hass.is_running:
        # Cold start with waking switched off (command permissions ->
        # "allow wake"): a passive query still refreshes what the car will
        # answer while asleep, it just will not produce a full snapshot.
        _LOGGER.info(
            "Cold start — waking is disabled by this integration's command "
            "permissions, sending a passive query instead"
        )
        await hass.async_add_executor_job(mqtt_client.send_query, vin)
    else:
        _LOGGER.info("Reload while running — passive query only, not waking the car")
        await hass.async_add_executor_job(mqtt_client.send_query, vin)

    # 6. Periodic MQTT query — adaptive by car state. The timer ticks every
    # scan_interval; what it sends depends on whether the car is active:
    #   - charging or driving -> cmd_type:2 force-upload every scan_interval.
    #     The car is already awake, so this is NOT a wake — it just forces a
    #     fresh full-state push. Needed because XPENG's autonomous push stream
    #     / cloud fanout frequently stalls, and a passive query does NOT punch
    #     through that (observed 2026-08-30: ~1h of zero data mid-drive until a
    #     force-upload retrieved it).
    #   - parked and idle -> one PASSIVE query per parked_scan_interval, just so
    #     sensors don't look stale. Never a force-upload — let the car sleep and
    #     wait for its own wake-up push.
    # A state transition is picked up within one tick. Credentials stay valid
    # via the separate cloud-side API refresh loop below, independent of this.

    async def _send_periodic_query(_now: Any) -> None:
        now = time.time()
        active = coordinator.is_charging or coordinator.vehicle_parked is False
        # An external charging hint raises the cadence but NOT the kind of query:
        # `active` alone decides whether we may force-upload, because that is the
        # only thing here that can wake a car, and the hint is someone else's word
        # for what the car is doing. Asking a genuinely charging car passively is
        # enough - it is awake, so it answers with a full state (observed
        # 2026-10-17: the reloads' passive queries returned full states the moment
        # the session started, after hours of stubs).
        hint = coordinator.external_charging_hint
        min_gap = poll_status.query_interval_seconds(
            coordinator.vehicle_parked,
            coordinator.is_charging,
            scan_interval,
            parked_scan_interval,
            hint,
        )
        if now - query_timer_state["last_sent"] < min_gap:
            return
        query_timer_state["last_sent"] = now
        _LOGGER.debug(
            "Periodic query: kind=%s interval=%.0fs parked=%s charging=%s "
            "charging_hint=%s",
            "force_upload" if active else "passive",
            min_gap,
            coordinator.vehicle_parked,
            coordinator.is_charging,
            hint,
        )
        try:
            if active:
                await hass.async_add_executor_job(
                    mqtt_client.send_command, vin, 19, {"cmd_type": 2}, 2
                )
            else:
                await hass.async_add_executor_job(mqtt_client.send_query, vin)
        except Exception:
            _LOGGER.debug("Periodic MQTT query failed", exc_info=True)
        coordinator.set_poll_status(
            last_query_at=now,
            last_query_kind="force_upload" if active else "passive",
        )

    cancel_timer = async_track_time_interval(
        hass, _send_periodic_query, timedelta(seconds=scan_interval)
    )

    # Seed the polling-status sensors with the two cadences and the timer's own
    # granularity. Static for the life of the entry, but published here rather
    # than read from options in sensor.py so the sensors report the values the
    # timers were actually built with.
    coordinator.set_poll_status(
        scan_interval_seconds=scan_interval,
        parked_scan_interval_seconds=parked_scan_interval,
        # The periodic timer sets its own `last_sent` to now at setup, so the
        # first query is a whole interval away - an hour, on a parked car.
        # Published under its own key rather than as `last_query_at` because no
        # query has actually been sent yet, and claiming a send that never
        # happened is worse than deriving the next-poll time from when the timer
        # started counting.
        timer_started_at=time.time(),
    )

    # 6b. Proactive API credential refresh
    cancel_api_refresh = None
    if api_available:
        _LOGGER.info(
            "API credential refresh enabled: interval=%ds source=%s",
            mqtt_refresh_interval,
            "config_entry" if has_entry_tokens else "token_file",
        )

        async def _refresh_mqtt_creds(_now: Any) -> None:
            """Safety-net refresh: only when disconnected and out of credential.

            Mint-on-demand moved the ordinary refresh to the connect path, which
            is what the app does. This timer exists for the cases where nothing is
            going to call ``connect()`` soon: a session that is down and stays
            down. While a session is connected with a usable credential there is
            nothing to mint, so it returns without an API call - the difference
            between the old ~80 mints an hour and roughly one per reconnect.
            """
            if mqtt_client.connected or not mqtt_client.credentials_expired:
                # Connected: the next reconnect mints on demand, and rotating
                # now is what produced the refusals. Disconnected with a live
                # credential: nothing to mint yet either.
                return
            try:
                from .mqtt_client import expires_at_from_account

                new_creds = await hass.async_add_executor_job(_mint_with_lock)
                if new_creds and new_creds.get("password"):
                    new_pw = new_creds["password"]
                    new_cid = new_creds.get("clientId", "")
                    # XPENG returns a fresh password every refresh while keeping
                    # the same clientId. MQTT authenticates only at CONNECT — the
                    # existing TCP session stays valid regardless of password
                    # rotation. So we update creds in-place without reconnecting;
                    # the new values are only used on the next reconnect (forced
                    # by auth-failure callback, silence watchdog, or reload).
                    # Reconnecting on every refresh caused a 45s flapping storm
                    # that dropped Full state pushes (observed 2026-06-06).
                    # A minted password IS the success signal, whether or not it
                    # differs from the one already in hand: XPENG normally
                    # rotates it, but "same string" is not a failure, and
                    # stamping this inside the rotation guard below left the
                    # credential age climbing through a perfectly healthy loop.
                    coordinator.set_credential_timestamp()
                    pw_changed = new_pw != mqtt_client._password
                    cid_changed = bool(new_cid) and new_cid != mqtt_client._client_id
                    if pw_changed or cid_changed:
                        mqtt_client.stage_credentials(
                            client_id=new_cid,
                            password=new_pw,
                            expires_at=expires_at_from_account(new_creds),
                        )
                        refresh_state["skip_reload"] = True
                        updated = {**entry.data, CONF_PASSWORD: new_pw}
                        if new_cid:
                            updated[CONF_CLIENT_ID] = new_cid
                        hass.config_entries.async_update_entry(
                            entry, data=updated,
                        )
                        _LOGGER.debug(
                            "API refresh: staged new credentials (pw=%s..., clientId=%s, cid_changed=%s) "
                            "— existing MQTT session kept alive",
                            new_pw[:8],
                            new_cid[:30] if new_cid else "unchanged",
                            cid_changed,
                        )
                        # If clientId actually changed, the existing session is
                        # tied to the old clientId and a reconnect IS required
                        # so the broker sees the new subscriber identity.
                        if cid_changed:
                            _LOGGER.info(
                                "clientId rotated — reconnecting to register new subscriber",
                            )
                            await hass.async_add_executor_job(
                                mqtt_client.reconnect, "clientId rotated"
                            )
                else:
                    # Minted nothing usable. Deliberately does NOT bump
                    # `consecutive_failures`: one failed cycle every 45s is the
                    # transient case `3 * interval` exists to absorb, and
                    # counting it here would make the entity flip off on a single
                    # blip. That counter is reserved for the broker refusing the
                    # credentials (`_handle_auth_failure`), which is evidence
                    # rather than a missed cycle.
                    _record_credential_failure(
                        refresh_state, "API credential refresh returned no password"
                    )
            except Exception as err:
                _LOGGER.debug(
                    "API credential refresh cycle failed", exc_info=True
                )
                _record_credential_failure(
                    refresh_state, f"API credential refresh cycle failed: {err}"
                )

        cancel_api_refresh = async_track_time_interval(
            hass, _refresh_mqtt_creds, timedelta(seconds=mqtt_refresh_interval),
        )
        # Do an initial API refresh immediately
        hass.async_create_task(_refresh_mqtt_creds(None))

    # 6b-ii. Message-centre notification polling.
    #
    # XPENG never delivers these over MQTT (verified: a 150s watch of
    # Xmart/Msg/User/{uid} produced only msg_type=1 state frames, and the
    # per-topic listener saw nothing), and they are not push-only either - the
    # message centre holds them server-side and the app reads them back. That
    # makes them pollable, which is what this does.
    #
    # The API reports "" when there is nothing new, so this is idempotent: the
    # coordinator diffs by id and only fires events for unseen ones.
    if api_available:
        async def _poll_notifications(_now: Any) -> None:
            def _fetch() -> list[dict[str, Any]]:
                from .xpeng_api import (
                    NOTIFICATION_CLASSIFICATIONS,
                    get_notifications,
                    parse_notification_item,
                )

                tokens_, tenant_ = _load_api_context(entry)
                if not tokens_ or not tenant_:
                    return []

                # The API returns ONE category per request, so every category has
                # to be asked for separately. Asking only for XPMOBILE-VEH (as
                # this first did) silently ignores charging, service, order and
                # event messages - a category with nothing in it returns [] and
                # is indistinguishable from a category that was never queried.
                collected: list[dict[str, Any]] = []
                for classification in NOTIFICATION_CLASSIFICATIONS:
                    try:
                        page = get_notifications(
                            None, tenant_, tokens_, classification=classification
                        )
                    except Exception:
                        # One failing category must not lose the others.
                        _LOGGER.debug(
                            "Notification fetch failed for %s",
                            classification, exc_info=True,
                        )
                        continue
                    if not isinstance(page, list):
                        continue
                    for item in page:
                        if not isinstance(item, dict):
                            continue
                        parsed = parse_notification_item(item)
                        # parse_notification_item takes `category` from the
                        # payload; guarantee it is set so the dedupe key stays
                        # unique per category.
                        parsed["category"] = parsed.get("category") or classification
                        parsed["classification"] = classification
                        collected.append(parsed)
                return collected

            try:
                items = await hass.async_add_executor_job(_fetch)
            except Exception as err:
                # A notification poll must never take the integration down, but
                # it must not fail silently either. This was logged at DEBUG,
                # which meant a TLS trust failure - the actual reason no
                # notification ever arrived - was completely invisible at
                # default logging and looked like "the API is quiet".
                #
                # WARNING, but only on change: the poll runs on a timer, so an
                # unconditional warning would repeat forever and bury the log.
                fingerprint = f"{type(err).__name__}: {err}"
                if fingerprint != getattr(_poll_notifications, "_last_err", None):
                    _poll_notifications._last_err = fingerprint
                    _LOGGER.warning(
                        "Notification poll failed (will not repeat until the "
                        "error changes): %s", fingerprint,
                    )
                return
            _poll_notifications._last_err = None
            if items:
                coordinator.set_rest_data({"rest_notifications": items})

        cancel_notifications = async_track_time_interval(
            hass, _poll_notifications, timedelta(seconds=notification_poll_interval)
        )
        entry.async_on_unload(cancel_notifications)
        _LOGGER.info(
            "Notification polling enabled: every %ds", notification_poll_interval
        )
        hass.async_create_task(_poll_notifications(None))

    # 6c. REST data polling — dynamic interval (slows when parked)
    cancel_rest_timer = None
    if api_available:
        _LOGGER.info(
            "REST data polling enabled: interval=%ds parked=%ds",
            rest_scan_interval, parked_scan_interval,
        )
        rest_timer_state: dict[str, Any] = {"cancel": None}

        def _get_rest_interval() -> int:
            """Return REST poll interval based on parked state."""
            if coordinator.vehicle_parked:
                return parked_scan_interval
            return rest_scan_interval

        async def _poll_rest_data(_now: Any) -> None:
            """Poll REST API for supplementary vehicle data, then reschedule."""
            try:
                rest_data = await hass.async_add_executor_job(
                    _fetch_rest_data_sync, entry, vin,
                    coordinator.latitude, coordinator.longitude,
                )
                if rest_data and rest_data.get("_token_expired"):
                    _LOGGER.warning("REST poll detected expired tokens — triggering reauth")
                    _start_reauth_if_idle(hass, entry)
                elif rest_data:
                    # Persist refreshed login tokens to config entry
                    refreshed_tokens = rest_data.pop("_refreshed_tokens", None)
                    if refreshed_tokens:
                        refresh_state["skip_reload"] = True
                        hass.config_entries.async_update_entry(
                            entry,
                            data={**entry.data, CONF_LOGIN_TOKENS: refreshed_tokens},
                        )
                        _LOGGER.info("Persisted refreshed login tokens to config entry")
                    coordinator.set_rest_data(rest_data)
                    from .capability_registry import sync_capability_entities

                    sync_capability_entities(hass, entry, coordinator)
                    _LOGGER.info(
                        "REST poll complete: keys=%s", list(rest_data.keys())
                    )
                else:
                    _LOGGER.warning("REST poll returned no data")
            except Exception:
                _LOGGER.warning("REST data poll failed", exc_info=True)
            # Reschedule with dynamic interval
            interval = _get_rest_interval()
            rest_timer_state["cancel"] = async_call_later(
                hass, interval, _poll_rest_data
            )

        # Start initial REST poll
        rest_timer_state["cancel"] = async_call_later(
            hass, 5, _poll_rest_data  # short delay to let MQTT connect first
        )

        def cancel_rest_timer_fn() -> None:
            if rest_timer_state["cancel"]:
                rest_timer_state["cancel"]()
                rest_timer_state["cancel"] = None

        cancel_rest_timer = cancel_rest_timer_fn

    # 6d. MQTT silence watchdog — force reconnect if broker stops delivering.
    # XPENG's cloud push-fanout intermittently gets stuck (server-side stuck
    # session-routing); opening the app re-subscribes and unsticks it, and we
    # replicate that with disconnect+connect after N seconds of silence.
    # Real outages: 2026-06-05/06 (14h dead), 2026-08-30 (car driven for ~1h
    # with zero pushes because this reconnect was suppressed while parked).
    # This is CLOUD-side only — disconnect, connect and the passive send_query
    # never wake the car — so it is NOT gated on parked/charging state.
    # "Don't wake the car" is enforced by the no-scheduled-cmd_type:2 rule and
    # the adaptive query cadence above, not here.
    # The escalation's clock has to outlive this setup, or "at most one wake per
    # 12h" is only true until the next reload. Measured 2026-10-16: an options
    # change at 22:43 rebuilt this state, and the escalation fired again at 23:13
    # — one hour after the previous wake, not twelve. The session counters reset
    # with the session (that is what "this session" means); the backoff clock is
    # a promise about the car, so it is stored.
    watchdog_store: Store = Store(
        hass, _WATCHDOG_STORE_VERSION, f"{DOMAIN}_watchdog_{vin}"
    )
    try:
        saved_watchdog: dict[str, Any] = await watchdog_store.async_load() or {}
    except Exception:  # noqa: BLE001 - a bad store must not block setup
        _LOGGER.warning(
            "Could not read the watchdog store; the escalation clock starts over",
            exc_info=True,
        )
        saved_watchdog = {}
    watchdog_state: dict[str, float] = {
        "last_reconnect": 0.0,
        "tick_count": 0,
        "last_escalation": float(saved_watchdog.get("last_escalation") or 0.0),
        "escalations": 0,
    }
    SILENCE_THRESHOLD = 300  # silence this long with socket up → suspect stuck
    RECONNECT_BACKOFF = 600  # active/charging: the car is awake, reconnects are cheap
    # Parked, a reconnect is close to pointless and not free: the car answers a
    # stub whether or not we tear the session down, so the rebuild buys nothing
    # (measured 2026-10-17: 59 watchdog reconnects overnight, 50 of the day's
    # refusals, and one full state - from the escalation, not from a reconnect).
    # Cloud-side only either way: a passive query cannot wake a parked car, which
    # is why 30 minutes here costs the car nothing at all.
    PARKED_RECONNECT_BACKOFF = 1800

    async def _silence_watchdog(_now: Any) -> None:
        try:
            watchdog_state["tick_count"] += 1
            # Vehicle-state silence: measured on the PRIMARY topic only, so
            # chatter on the V2 or user listener topics cannot mask a stalled
            # state fan-out (see CAPABILITY_ROADMAP §2.9).
            silence = time.time() - mqtt_client.last_state_msg_ts
            # Any-topic age, diagnostics only — deliberately NOT the trigger.
            since_any = time.time() - mqtt_client.last_msg_ts
            # How long since a message actually CARRIED data, as opposed to how
            # long since the topic said anything. A sleeping car's stub resets
            # `silence` but not this, so a stall that produced only stubs cannot
            # hide behind the reconnect cycle. This is the clock the escalation
            # below is keyed on.
            stale = time.time() - mqtt_client.last_meaningful_state_ts
            # Computed once here because two consumers need the same answer:
            # the status block below publishes it, and the escalation decision
            # at the end of this tick acts on it. A LIVE predicate rather than
            # `bool(last_escalation)` - the sticky form kept reporting
            # "Escalated" for the rest of the session once a single force-upload
            # had been spent, even after normal data resumed. It should mean "a
            # force-upload is pending or was just spent, and nothing has arrived
            # since".
            pending_escalation = should_escalate(
                stale, watchdog_state["last_escalation"], time.time()
            )
            # Last-known motion permits one bounded recovery attempt outside
            # the 12 h sleeping-car budget. It does not prove the car is still
            # moving after the stream has gone quiet. Issue #3, 2026-10-21:
            # 16 refusals on that budget while driving, and 46 minutes of
            # frozen telemetry. See watchdog_policy.should_escalate_moving.
            motion_evidence_at = coordinator.last_motion_at
            motion_escalation = should_escalate_moving(
                stale,
                watchdog_state["last_escalation"],
                time.time(),
                motion_evidence_at,
            )
            escalation_allowed = pending_escalation or motion_escalation
            escalated_now = poll_status.is_escalated(
                stale,
                watchdog_state["last_escalation"],
                time.time(),
                escalate_after=ESCALATE_AFTER_SECONDS,
                escalate_backoff=ESCALATE_BACKOFF_SECONDS,
            )
            # Quiet in steady state: heartbeat every 5 ticks (~5 min), but
            # log every tick once silence starts building (>60s) so we have a
            # full trace if the bug recurs and watchdog fails to fire.
            # Published for the polling-status sensors, which poll themselves
            # and so have no other way to see these. Recorded on every tick,
            # including the quiet ones, because "how long since anything
            # arrived" is only useful if it keeps climbing while nothing does.
            coordinator.set_poll_status(
                mqtt_connected=mqtt_client.connected,
                state_silence_seconds=silence,
                meaningful_silence_seconds=stale,
                any_msg_age_seconds=since_any,
                silence_threshold_seconds=SILENCE_THRESHOLD,
                escalations=int(watchdog_state["escalations"]),
                last_escalation_at=watchdog_state["last_escalation"] or None,
                # A LIVE predicate, not `bool(last_escalation)`: the sticky
                # form kept reporting "Escalated" for the rest of the session
                # once a single force-upload had been spent, even after normal
                # data resumed. `escalated` should mean "a force-upload is
                # pending or was just spent and nothing has arrived since".
                escalated=escalated_now,
            )
            if watchdog_state["tick_count"] % 5 == 0 or silence > 60:
                _LOGGER.info(
                    "Watchdog tick #%d: connected=%s state_silence=%.0fs "
                    "stale=%.0fs any_msg_age=%.0fs threshold=%ss "
                    "since_last_reconnect=%.0fs",
                    int(watchdog_state["tick_count"]),
                    mqtt_client.connected, silence, stale, since_any,
                    SILENCE_THRESHOLD,
                    time.time() - watchdog_state["last_reconnect"],
                )
        except Exception:
            _LOGGER.exception("Watchdog: failed in entry diagnostic")
            return
        # Don't stack on the auth-failure handler — it does its own
        # refresh+reconnect and holds the same reconnect lock.
        if refresh_state.get("refreshing"):
            return
        parked = coordinator.vehicle_parked is not False and not coordinator.is_charging
        reconnect_backoff = (
            PARKED_RECONNECT_BACKOFF if parked else RECONNECT_BACKOFF
        )
        if not mqtt_client.connected:
            since_last = time.time() - watchdog_state["last_reconnect"]
            if since_last < reconnect_backoff:
                return
            _LOGGER.warning(
                "Watchdog: MQTT disconnected (last any-topic message %.0fs ago) "
                "— forcing reconnect", since_any,
            )
            watchdog_state["last_reconnect"] = time.time()
            try:
                did = await hass.async_add_executor_job(
                    mqtt_client.reconnect, "watchdog: disconnected"
                )
                if did:
                    await hass.async_add_executor_job(mqtt_client.send_query, vin)
            except Exception:
                _LOGGER.exception("Watchdog reconnect (disconnected state) failed")
            return
        if silence < SILENCE_THRESHOLD:
            # A parked car answers every query with a stub, so `silence` stays
            # small while no data arrives. Skipping the reconnect is right - an
            # awake-but-idle car needs nothing. Skipping the ESCALATION below is
            # not, and that is what this used to do: it returned here, so a car
            # that answered stubs for half an hour never reached the one
            # force-upload that would have woken it (measured 2026-10-16:
            # state_silence 7s, seconds_since_data 1864s, escalations 0). Fall
            # through instead; the escalation is keyed on staleness, which a stub
            # does not clear, and its 12h backoff bounds the cost of being wrong.
            pass
        elif (
            since_last := time.time() - watchdog_state["last_reconnect"]
        ) < reconnect_backoff:
            _LOGGER.debug(
                "Watchdog: %.0fs silent but last reconnect was %.0fs ago — backing off",
                silence, since_last,
            )
        else:
            # A quiet parked car is expected; absence of messages alone does
            # not establish a cloud routing fault. Keep active/unknown states
            # visible at WARNING and leave disconnected recovery above alone.
            last_reported_state = (
                "charging" if coordinator.is_charging else
                "parked" if coordinator.vehicle_parked is True else
                "moving" if coordinator.vehicle_parked is False else "unknown"
            )
            log_reconnect = (
                _LOGGER.info if last_reported_state == "parked" else _LOGGER.warning
            )
            log_reconnect(
                "Watchdog: %.0fs without a vehicle-state message "
                "(last reported state: %s) — reconnecting and sending a passive query",
                silence, last_reported_state,
            )
            watchdog_state["last_reconnect"] = time.time()
            # Mint before tearing down. The resubscribe is what unsticks the
            # cloud fan-out, so the reconnect has to happen — but the rebuilt
            # client presents whatever password we hold, and the ~45s rotation
            # retires it, which is what produced "Not authorized" refusals. One
            # API call per reconnect (a few an hour) buys a reconnect that is
            # not refused on the way up.
            try:
                fresh = await hass.async_add_executor_job(
                    _refresh_creds_sync, entry, refresh_state
                )
                if fresh and fresh.get("password"):
                    from .mqtt_client import expires_at_from_account

                    new_cid = fresh.get("clientId") or ""
                    mqtt_client.stage_credentials(
                        client_id=new_cid,
                        password=fresh["password"],
                        expires_at=expires_at_from_account(fresh),
                    )
                    # INFO, not DEBUG: this is the evidence that the safety
                    # step before a teardown actually ran.
                    _LOGGER.info(
                        "Minted a fresh credential ahead of the reconnect"
                    )
            except Exception as err:
                _LOGGER.debug("Pre-reconnect credential mint failed", exc_info=True)
                _record_credential_failure(
                    refresh_state, f"pre-reconnect credential mint failed: {err}"
                )
            try:
                did = await hass.async_add_executor_job(
                    mqtt_client.reconnect, f"watchdog: {silence:.0f}s silent"
                )
                if did:
                    # Kick a query so we know whether the fresh session is receiving
                    await hass.async_add_executor_job(mqtt_client.send_query, vin)
            except Exception:
                _LOGGER.exception("Watchdog reconnect failed")

        # Escalate to a single force-upload once passive recovery has clearly
        # failed. Reached whether or not this tick reconnected, so the
        # escalation is not tied to the reconnect cadence. The policy lives in
        # watchdog_policy.py, which imports nothing from HA so it can be tested
        # - the answer wakes a physical car.
        #
        # Keyed on `stale`, not `silence`. `silence` is the reconnect trigger and
        # a stub answers it, so it can be reset every reconnect cycle while no
        # data arrives; staleness only clears when a message carries something.
        now_ts = time.time()
        if not escalation_allowed:
            if escalated_now:
                _LOGGER.debug(
                    "Watchdog: %.1fmin stale but last force-upload was %.0fmin ago "
                    "— holding off (one per %.0fh; last motion evidence %s)",
                    stale / 60,
                    (now_ts - watchdog_state["last_escalation"]) / 60,
                    ESCALATE_BACKOFF_SECONDS / 3600,
                    (
                        f"{(now_ts - coordinator.last_motion_at) / 60:.0f}min ago"
                        if coordinator.last_motion_at
                        else "none"
                    ),
                )
            return
        watchdog_state["last_escalation"] = now_ts
        watchdog_state["escalations"] += 1
        # Written before the send, and independent of it: the backoff exists to
        # bound the wake, so the clock must not depend on the publish succeeding
        # (a refused client is exactly when a wake attempt is most likely to be
        # spent and least likely to have done anything).
        try:
            await watchdog_store.async_save({"last_escalation": now_ts})
        except Exception:  # noqa: BLE001 - never let storage stop the escalation
            _LOGGER.warning(
                "Could not persist the watchdog escalation clock; the next "
                "reload may allow an earlier wake than the 12h backoff promises",
                exc_info=True,
            )
        try:
            if coordinator.command_policy.can_wake():
                if motion_escalation:
                    _LOGGER.warning(
                        "Watchdog: %.0fmin without fresh vehicle data; motion "
                        "evidence %.0fmin old, current motion is unconfirmed — "
                        "sending ONE force-upload (may wake the car; bounded "
                        "motion recovery outside the %.0fh parked-car budget)",
                        stale / 60,
                        (now_ts - motion_evidence_at) / 60,
                        ESCALATE_BACKOFF_SECONDS / 3600,
                    )
                else:
                    _LOGGER.warning(
                        "Watchdog: %.0fmin without fresh vehicle data (state "
                        "topic quiet %.0fs) — sending ONE force-upload "
                        "(may wake the car; at most one per %.0fh without new "
                        "motion evidence)",
                        stale / 60, silence, ESCALATE_BACKOFF_SECONDS / 3600,
                    )
                await hass.async_add_executor_job(
                    mqtt_client.send_command, vin, 19, {"cmd_type": 2}, 2
                )
            else:
                # Escalation exists to wake a sleeping car. With waking
                # switched off, the passive query is the most this may do - it
                # is still worth sending, because a car that is awake answers
                # it, and that is the case the escalation is usually meeting.
                _LOGGER.info(
                    "Watchdog: %.0fmin without fresh vehicle data (state topic "
                    "quiet %.0fs); waking is disabled — sending a passive query",
                    stale / 60, silence,
                )
                await hass.async_add_executor_job(mqtt_client.send_query, vin)
        except Exception:
            _LOGGER.exception("Watchdog escalation force-upload failed")

    cancel_watchdog = async_track_time_interval(
        hass, _silence_watchdog, timedelta(seconds=60),
    )

    # 7. Store for unload
    #
    # Never overwrite a previous client for this entry without stopping it. If
    # this entry is set up again while an earlier client is still live, the
    # assignment below would orphan that client: its paho network thread (named
    # `paho-mqtt-client-<clientId>`) keeps reconnecting under the OLD clientId,
    # and the broker then sees two clients under one XPENG account. It
    # authorises one and refuses the other as "Not authorized"; every refusal
    # triggers a credential refresh and a rebuild, and each rebuild resets the
    # entities - which is how an install ends up flapping between ~20 and 124
    # unknown entities. Measured on a live install 2026-10-11: two clientIds
    # (one stale, from a device-id change), two live paho clients, 97/124
    # unknown. Restarting HA cleared it; a config-entry reload did not.
    #
    # This guard is what makes changing `device_id` on an existing install safe.
    previous = (hass.data.get(DOMAIN) or {}).get(entry.entry_id)
    if previous:
        # A replacing setup must not leave a port-holding provider behind: the
        # new one would fail to bind AND the old one would keep serving stale
        # data forever.
        previous_provider = previous.get("teslamate_provider")
        if previous_provider is not None:
            try:
                await previous_provider.async_stop()
            except Exception:  # noqa: BLE001 - replacing must not raise
                _LOGGER.warning(
                    "Stopping the previous TeslaMate provider failed",
                    exc_info=True,
                )
        previous_client = previous.get("mqtt_client")
        if previous_client is not None and previous_client is not mqtt_client:
            _LOGGER.warning(
                "Entry %s is being set up again while its previous MQTT client "
                "is still live - stopping it so the broker does not see two "
                "clients for one account",
                entry.entry_id,
            )
            # Cancel its timers first: they hold the old client and would keep
            # firing against it (and against this new one) until unload.
            for key in (
                "cancel_timer",
                "cancel_api_refresh",
                "cancel_rest_timer",
                "cancel_watchdog",
            ):
                cancel = previous.get(key)
                if cancel:
                    try:
                        cancel()
                    except Exception:  # noqa: BLE001 - replacing must not raise
                        _LOGGER.debug("could not cancel %s while replacing", key)
            await hass.async_add_executor_job(previous_client.disconnect)
        previous_capture = previous.get("capture")
        if previous_capture is not None:
            # Its writer thread outlives the client otherwise, and two writers in
            # one directory interleave parts under the same timestamped name.
            await hass.async_add_executor_job(previous_capture.stop, 5.0, "replaced")

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "mqtt_client": mqtt_client,
        "entry": entry,
        # Services read it from here (they have no entity to ask), entities
        # read it from the coordinator. One object, resolved from one place.
        "command_policy": coordinator.command_policy,
        "cancel_timer": cancel_timer,
        "cancel_api_refresh": cancel_api_refresh,
        "cancel_rest_timer": cancel_rest_timer,
        "cancel_watchdog": cancel_watchdog,
        "refresh_state": refresh_state,
        # The capture recorder, for the diagnostic sensor and for unload.
        "capture": capture_recorder,
    }

    # 7b. The optional TeslaMate provider: a Tesla-compatible HTTP API on its own
    # port. Off unless enabled in the options; a failure to bind (port already in
    # use) is logged and must not stop the vehicle integration from working.
    hass.data[DOMAIN][entry.entry_id]["teslamate_provider"] = None
    if entry.options.get(CONF_TESLAMATE_ENABLED):
        from .teslamate_provider import (
            ProviderOptions,
            async_start_teslamate_provider,
        )

        hass.data[DOMAIN][entry.entry_id]["teslamate_provider"] = (
            await async_start_teslamate_provider(
                coordinator,
                ProviderOptions(
                    token=str(entry.options.get(CONF_TESLAMATE_TOKEN, "")).strip(),
                    port=int(
                        entry.options.get(
                            CONF_TESLAMATE_PORT, DEFAULT_TESLAMATE_PORT
                        )
                    ),
                ),
            )
        )

    # Compact mode is integration-level: seed renamed controller IDs and retire
    # their old duplicate entries before platforms register the new layout.
    from .entity_layout import prepare_compact_registry
    prepare_compact_registry(hass, entry, coordinator.vin, coordinator.command_policy)

    # 8. Forward platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    from .capability_registry import sync_capability_entities

    sync_capability_entities(hass, entry, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # 9. Register vehicle command services
    await async_setup_services(hass)

    # The API is the only refresh path now that the local Mac/iPhone rig is
    # gone, so there is no second branch to report.
    auto_refresh = (
        f"API (every {mqtt_refresh_interval}s)" if api_available else "disabled"
    )
    _LOGGER.info(
        "XPENG integration ready: VIN=%s interval=%ds parked=%ds auto_refresh=%s rest_poll=%s",
        vin, scan_interval, parked_scan_interval, auto_refresh,
        f"{rest_scan_interval}s" if api_available else "disabled",
    )
    return True

def _refresh_login_tokens_sync(
    entry: ConfigEntry,
    tokens: dict | None,
    tenant: dict | None,
    refresh_state: dict | None = None,
) -> dict | None:
    """Renew the OAuth login tokens and persist them. Returns them, or None.

    The REST path has always done this for itself when it sees 12010101; the MQTT
    credential path did not, and that omission is a total outage: the access token
    ages out, EVERY createMqttAccount call returns 403/12010101 "Login status
    expired", and because the failure is raised before any credential is minted
    the refresh loop can never recover on its own. Observed on a fresh install
    (2026-10-07, .173): MQTT refused for hours while `credential_age` climbed.

    Also note the failure is logged at DEBUG by the caller, so at default logging
    the integration looks merely "silent" rather than broken - which is how this
    stayed undiagnosed.

    `refresh_state` is optional and only ever written for the credentials-valid
    diagnostic (see `health.py`). Failures record a reason; success deliberately
    records nothing, because renewing the login half is not yet the loop working
    — the caller still has to mint an MQTT password with the new tokens, and
    marking success here would show a green sensor while that mint kept failing.
    """
    from .xpeng_api import refresh_login_tokens

    if not tokens or not tenant:
        _record_credential_failure(
            refresh_state, "no login tokens or tenant to refresh with"
        )
        return None
    try:
        new_tokens = refresh_login_tokens(None, tenant, tokens)
    except Exception as err:  # noqa: BLE001 - a refresh failure must not kill setup
        _LOGGER.warning("Login token refresh failed: %s", err)
        _record_credential_failure(refresh_state, f"login token refresh failed: {err}")
        return None
    if not new_tokens or not new_tokens.get("accessToken"):
        _LOGGER.warning("Login token refresh returned no access token")
        _record_credential_failure(
            refresh_state, "login token refresh returned no access token"
        )
        return None

    token_file = entry.options.get(CONF_TOKEN_FILE, "").strip()
    if token_file:
        try:
            import json as _json

            with open(token_file, "w") as f:
                _json.dump({"json": new_tokens}, f, indent=2)
            _LOGGER.info("Refreshed login tokens saved to %s", token_file)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not persist refreshed tokens", exc_info=True)
    _LOGGER.info("Login tokens refreshed successfully via refresh_token")
    return new_tokens

def _refresh_creds_sync(
    entry: ConfigEntry, refresh_state: dict | None = None
) -> dict[str, str] | None:
    """Refresh MQTT credentials using best available token source (blocking).

    Returns the credential dict, plus `_refreshed_tokens` when the login tokens
    had to be renewed on the way - the async caller persists those to the config
    entry, because `_load_api_context` prefers the entry's copy and would
    otherwise keep handing back the stale one.

    This is the credential loop's single success point, so it is where the
    credentials-valid diagnostic is told the loop is working — not the caller,
    where the same fact is decided twice. `refresh_state` is optional so the
    blocking path stays callable (and testable) without it.
    """
    from .xpeng_api import LoginExpiredError, create_mqtt_account

    tokens, tenant = _load_api_context(entry)
    if not tokens or not tenant:
        _record_credential_failure(
            refresh_state, "no login tokens or tenant to mint credentials with"
        )
        return None

    refreshed_tokens: dict | None = None
    try:
        account = create_mqtt_account(None, tenant, tokens)
    except LoginExpiredError as err:
        _LOGGER.info(
            "MQTT credential refresh found an expired login token (%s) - "
            "refreshing tokens and retrying", err,
        )
        refreshed_tokens = _refresh_login_tokens_sync(
            entry, tokens, tenant, refresh_state
        )
        if not refreshed_tokens:
            raise
        # Retry with the new tokens directly rather than re-loading: the entry's
        # copy is the stale one, so a re-load would retry with what just failed.
        account = create_mqtt_account(None, tenant, refreshed_tokens)
    if refreshed_tokens and account is not None:
        # Hand them back so the async caller can persist them to the entry.
        account = {**account, "_refreshed_tokens": refreshed_tokens}

    # Update token file with fresh MQTT account (keeps token file current)
    token_file = entry.options.get(CONF_TOKEN_FILE, "").strip()
    if token_file and os.path.isfile(token_file):
        try:
            import json as _json

            with open(token_file, "r") as f:
                file_data = _json.load(f)
            file_tokens = file_data.get("json", file_data)
            file_tokens["mqttAccount"] = account
            with open(token_file, "w") as f:
                _json.dump({"json": file_tokens}, f, indent=2)
        except Exception:
            pass  # Non-critical — token file update is best-effort

    credentials = {
        "clientId": account.get("clientId", ""),
        "password": account.get("password", ""),
        "serverSsl": account.get("serverSsl", ""),
        "expireTime": account.get("expireTime", ""),
    }

    # Success is a usable password, not merely "returned a dict": every caller
    # treats an empty password as a failed cycle, so recording success here would
    # show a green sensor through a loop that mints nothing and reconnects never.
    if credentials["password"]:
        _record_credential_success(refresh_state)
    else:
        _record_credential_failure(
            refresh_state, "XPENG returned a credential with no password"
        )

    return credentials

def _fetch_rest_data_sync(
    entry: ConfigEntry,
    vin: str,
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict[str, Any] | None:
    """Fetch supplementary REST data (blocking)."""
    from .xpeng_api import (
        get_vehicles, get_ota_status, get_vehicle_state,
        get_maintenance_reminder, get_xcombos, get_xcombo_agg_data,
        get_energy_report, get_weather,
        refresh_login_tokens,
        get_remote_capabilities,
        get_authenticated_data,
        get_ota_upgrade_info, get_auto_upgrade_status, get_ota_version,
        get_beta_popup, get_vehicle_extension,
        get_left_comfort_preon_time,
        list_aircondition_appointments,
    )

    tokens, tenant = _load_api_context(entry)
    if not tokens or not tenant:
        return None

    result: dict[str, Any] = {}

    def _is_token_expired(data: Any) -> bool:
        """Check if API response indicates expired login tokens.

        Delegates to the shared helper rather than repeating the codes: this
        test used to spell out (12010101, 12010104), so a code added to
        `TOKEN_EXPIRED_CODES` was invisible here. 12010202 — what
        /sso/app/v3/token/refresh answers once a session has been retired by a
        later login — was exactly that case.
        """
        from .xpeng_errors import is_token_expired_response

        return is_token_expired_response(data)

    def _try_refresh_tokens() -> bool:
        """Attempt to refresh login tokens. Returns True if successful."""
        nonlocal tokens
        try:
            new_tokens = refresh_login_tokens(None, tenant, tokens)
            if new_tokens and new_tokens.get("accessToken"):
                tokens = new_tokens
                # Signal to async caller to persist to config entry
                result["_refreshed_tokens"] = new_tokens
                # Also persist to token file (best-effort)
                import json as _json
                token_file = entry.options.get("token_file", "").strip()
                if token_file:
                    try:
                        with open(token_file, "w") as f:
                            _json.dump({"json": tokens}, f, indent=2)
                        _LOGGER.info("Refreshed tokens saved to %s", token_file)
                    except Exception:
                        pass
                _LOGGER.info("Login tokens refreshed successfully via refresh_token")
                return True
        except Exception as err:
            _LOGGER.warning("Token refresh failed: %s", err)
        return False

    # 1. Vehicle list — get image URL and vehicle type code
    try:
        vehicles_data = get_vehicles(None, tenant, tokens)
        _LOGGER.info("get_vehicles response type=%s: %s",
                      type(vehicles_data).__name__,
                      str(vehicles_data)[:500] if vehicles_data else "None")
        if _is_token_expired(vehicles_data):
            _LOGGER.info("Login tokens expired — attempting refresh")
            if _try_refresh_tokens():
                vehicles_data = get_vehicles(None, tenant, tokens)
                if _is_token_expired(vehicles_data):
                    _LOGGER.warning("Tokens still expired after refresh — triggering reauth")
                    return {"_token_expired": True}
            else:
                _LOGGER.warning("Token refresh failed — triggering reauth")
                return {"_token_expired": True}
        def _extract_from_vehicle(v: dict) -> None:
            from .vehicle_capabilities import HARDWARE_KEYS, normalize_flags

            hardware = normalize_flags(v.get("function"), HARDWARE_KEYS)
            if hardware:
                result["vehicle_hardware"] = hardware
            vtc = v.get("vehicleTypeCode", "")
            if vtc:
                result["vehicle_type_code"] = vtc
            # Manufacturer-reported identity. This is the authoritative source
            # for the series/trade name: a XPENG VIN's positions 4-8 identify
            # the model FAMILY only — trim, battery and drivetrain are not
            # encoded in the VIN at all (see vin_descriptor.py).
            for api_key, result_key in (
                ("seriesName", "series_name"),
                ("seriesCode", "series_code"),
                ("vehicleSeriesName", "series_name"),
                ("nickName", "nick_name"),
                ("vehicleTypeName", "vehicle_type_name"),
                ("diagramVersion", "diagram_version"),
            ):
                value = v.get(api_key)
                if value not in (None, ""):
                    result.setdefault(result_key, value)
            appearance = v.get("vehicleAppearance") or {}
            if isinstance(appearance, dict) and appearance:
                # Kept whole for diagnostics: the picture we choose is only one
                # of the keys the API offers, and the app may be rendering from
                # another (or from trim data this block does not carry).
                result["vehicle_appearance"] = appearance
            pic = appearance.get("pictureLeft") or appearance.get("picture") or ""
            if pic:
                result["vehicle_image_url"] = pic
            cdu = v.get("vehicleCdu") or {}
            if isinstance(cdu, dict):
                if cdu.get("cduId"):
                    result["cdu_id"] = cdu["cduId"]
                if cdu.get("cduAOrBFlag") is not None:
                    result["cdu_a_or_b_flag"] = cdu["cduAOrBFlag"]
            cpno = v.get("cduPartsNoEight2Ten")
            if cpno:
                result["cdu_parts_no_eight2ten"] = cpno

        if isinstance(vehicles_data, list):
            for v in vehicles_data:
                if v.get("vin") == vin:
                    _extract_from_vehicle(v)
                    break
        elif isinstance(vehicles_data, dict):
            vehicles_list = vehicles_data.get("vehicles") or vehicles_data.get("list") or []
            if isinstance(vehicles_list, list):
                for v in vehicles_list:
                    if v.get("vin") == vin:
                        _extract_from_vehicle(v)
                        break
    except Exception as err:
        _LOGGER.warning("get_vehicles failed: %s", err)

    # 2. OTA status — get XOS version
    try:
        ota_data = get_ota_status(None, tenant, tokens, vin)
        if isinstance(ota_data, dict):
            version = (
                ota_data.get("osVersion")
                or ota_data.get("version")
                or ota_data.get("currentVersion")
                or ota_data.get("xosVersion")
                or ""
            )
            if version:
                result["xos_version"] = str(version)
    except Exception as err:
        _LOGGER.debug("get_ota_status failed: %s", err)

    # 3. Widget vehicle state (if we have vehicle type code)
    vtc = result.get("vehicle_type_code") or ""
    if vtc:
        try:
            state_data = get_vehicle_state(None, tenant, tokens, vin, vtc)
            if isinstance(state_data, dict):
                result["widget_state"] = state_data
        except Exception as err:
            _LOGGER.debug("get_vehicle_state failed: %s", err)

    # 4. Maintenance reminder
    try:
        maint = get_maintenance_reminder(None, tenant, tokens, vin)
        if isinstance(maint, dict) and "nextMaintenanceDays" in maint:
            result["maintenance"] = maint
    except Exception as err:
        _LOGGER.debug("get_maintenance_reminder failed: %s", err)

    # 5. X-Combo scenes
    try:
        scenes = get_xcombos(None, tenant, tokens, vin, vehicle_type=vtc)
        if isinstance(scenes, list):
            result["xcombos"] = scenes
            _LOGGER.info("X-Combo scenes: count=%d names=%s",
                         len(scenes), [s.get("name") for s in scenes])
    except Exception as err:
        _LOGGER.debug("get_xcombos failed: %s", err)

    # 5b. X-Combo aggregate data (debug — discover additional scene info)
    try:
        agg = get_xcombo_agg_data(None, tenant, tokens, vin, vehicle_type=vtc)
        _LOGGER.info("X-Combo agg_data: type=%s data=%s",
                     type(agg).__name__, str(agg)[:500])
    except Exception as err:
        _LOGGER.debug("get_xcombo_agg_data failed: %s", err)

    # 6. Energy report (needs vehicle type code)
    if vtc:
        try:
            energy = get_energy_report(None, tenant, tokens, vin, vtc)
            if isinstance(energy, dict) and "vin" in energy:
                result["energy_report"] = energy
        except Exception as err:
            # WARNING, not DEBUG. A failed energy fetch is a real fault, and
            # at DEBUG it is invisible at default logging - the same mistake
            # that hid the notification TLS failure for an entire session.
            # This endpoint is also permission-gated per account, and the
            # refusal is not always distinguishable from "no data", so the
            # reason needs to be on the record.
            _LOGGER.warning(
                "Energy report unavailable for %s (vehicleTypeCode=%s): %s",
                vin, vtc, err,
            )

    # 6b. Weather at vehicle location (assistant service; needs GPS)
    if latitude is not None and longitude is not None:
        try:
            weather = get_weather(None, tenant, tokens, latitude, longitude)
            if isinstance(weather, dict) and weather.get("realtimeTemperature") is not None:
                result["weather"] = weather
        except Exception as err:
            _LOGGER.debug("get_weather failed: %s", err)

    # 7. Remote capabilities (what commands the car supports)
    try:
        caps = get_remote_capabilities(None, tenant, tokens, vin)
        if caps:
            result["remote_capabilities"] = caps
    except Exception as err:
        _LOGGER.debug("get_remote_capabilities failed: %s", err)

    # The official app overlays model configuration from this read-only API.
    # Retain only explicit feature booleans; absence is unknown, not False.
    if result.get("vehicle_type_code"):
        try:
            from .vehicle_capabilities import LOCAL_KEYS, normalize_flags

            import json as _json

            body = _json.dumps({
                "biz": "app", "module": "onlineVehicle", "vin": vin,
                "vehicleTypeCode": result["vehicle_type_code"],
                "vehicleTypeName": result.get("vehicle_type_name", ""),
            }, separators=(",", ":"))
            local = get_authenticated_data(
                None, tenant, tokens, "/ocm/app/config/get", "POST", body,
            )
            local = normalize_flags(local, LOCAL_KEYS)
            if local:
                result["vehicle_local_config"] = local
        except Exception as err:
            _LOGGER.debug("Vehicle model configuration unavailable: %s", type(err).__name__)

    # 8. OTA upgrade info (available updates)
    try:
        ota_info = get_ota_upgrade_info(None, tenant, tokens, vin, upgrade_id=0)
        if isinstance(ota_info, dict):
            result["ota_upgrade_info"] = ota_info
    except Exception as err:
        _LOGGER.debug("get_ota_upgrade_info failed: %s", err)

    # 9. OTA auto-upgrade status
    try:
        auto_status = get_auto_upgrade_status(None, tenant, tokens, vin)
        if isinstance(auto_status, dict):
            result["ota_auto_upgrade_status"] = auto_status
    except Exception as err:
        _LOGGER.debug("get_auto_upgrade_status failed: %s", err)

    # 10. OTA version info (current OS version via remote v1)
    try:
        ver_info = get_ota_version(None, tenant, tokens, vin)
        if isinstance(ver_info, dict):
            result["ota_version_info"] = ver_info
    except Exception as err:
        _LOGGER.debug("get_ota_version failed: %s", err)

    # 11. Beta popup data
    try:
        beta = get_beta_popup(None, tenant, tokens)
        if isinstance(beta, dict):
            result["beta_popup"] = beta
    except Exception as err:
        _LOGGER.debug("get_beta_popup failed: %s", err)

    # 12. Vehicle extension messages (alerts, notifications)
    try:
        ext = get_vehicle_extension(None, tenant, tokens, vin)
        if ext is not None:
            result["vehicle_extension"] = ext
    except Exception as err:
        _LOGGER.debug("get_vehicle_extension failed: %s", err)

    # 13. Left-comfort preon countdown (seconds remaining)
    try:
        preon = get_left_comfort_preon_time(None, tenant, tokens, vin)
        if isinstance(preon, (int, float)):
            secs = int(preon)
        elif isinstance(preon, str) and preon.lstrip("-").isdigit():
            secs = int(preon)
        elif isinstance(preon, dict) and isinstance(preon.get("data"), (int, float)):
            secs = int(preon["data"])
        else:
            secs = None
        if secs is not None:
            result["left_comfort_preon_seconds"] = max(0, secs) if secs >= 0 else 0
            _LOGGER.info("left-comfort preon time: raw=%s seconds=%s",
                         preon, result["left_comfort_preon_seconds"])
    except Exception as err:
        _LOGGER.debug("get_left_comfort_preon_time failed: %s", err)

    # 14. HVAC scheduled appointments
    try:
        sched = list_aircondition_appointments(None, tenant, tokens, vin)
        if isinstance(sched, dict):
            items = sched.get("list") or []
            if isinstance(items, list):
                result["hvac_schedules"] = items
                nxt = sched.get("nextStartTime")
                result["hvac_next_start_time"] = nxt if isinstance(nxt, str) else None
                _LOGGER.debug("HVAC schedules: count=%d next=%s",
                              len(items), result["hvac_next_start_time"])
    except Exception as err:
        _LOGGER.debug("list_aircondition_appointments failed: %s", err)

    return result if result else None

async def _handle_auth_failure(
    hass: HomeAssistant,
    entry: ConfigEntry,
    mqtt_client: XPengMQTTClient,
    vin: str,
    refresh_state: dict,
) -> None:
    """Handle MQTT auth failure by refreshing credentials via the XPENG API.

    Uses exponential backoff: 60s → 120s → 240s → 480s → 900s (max).
    After AUTH_FAILURE_REAUTH_THRESHOLD consecutive failures, triggers HA reauth UI.
    """
    import time

    # Credentials live on the entry (the import step stores them there), so this
    # is the only source: the local Mac/iPhone extraction rig this used to fall
    # back to has been removed.
    has_api = bool(entry.data.get(CONF_LOGIN_TOKENS))
    method = "API" if has_api else "no credentials"

    now = time.monotonic()
    if refresh_state["refreshing"]:
        _LOGGER.warning("Credential refresh already in progress, skipping")
        return

    # A refused CONNECT right after our own reconnect, or after a takeover notice
    # from the renewal app, is the previous session still ending - not a bad
    # credential. Minting cannot fix it (measured 2026-10-17: these bursts clear
    # in 45-90 s on their own), so the client retries with the credential in hand
    # and we stay out of the way. If the refusals outlast the grace, the client
    # calls back here normally and this guard no longer matches.
    if getattr(mqtt_client, "settling", False):
        _LOGGER.info(
            "MQTT auth failure while the session settles (own reconnect or "
            "renewal import) - not minting"
        )
        return

    # Exponential backoff based on consecutive failures
    failures = refresh_state["consecutive_failures"]
    cooldown = min(
        AUTH_FAILURE_BASE_COOLDOWN * (2 ** failures),
        AUTH_FAILURE_MAX_BACKOFF,
    )
    elapsed = now - refresh_state["last_attempt"]
    if elapsed < cooldown:
        remaining = int(cooldown - elapsed)
        _LOGGER.warning(
            "Credential refresh on cooldown (%ds remaining, %d consecutive failures)",
            remaining, failures,
        )
        return

    refresh_state["refreshing"] = True
    refresh_state["last_attempt"] = now

    _LOGGER.warning(
        "MQTT auth failed — auto-refreshing via %s (attempt #%d)",
        method, failures + 1,
    )

    try:
        # Try API first (config entry tokens or file tokens)
        new_creds = None
        if has_api:
            try:
                new_creds = await hass.async_add_executor_job(
                    _refresh_creds_sync, entry, refresh_state
                )
                if new_creds and new_creds.get("password"):
                    # If the login tokens had expired, they were renewed to get
                    # this far. Persist them, or _load_api_context keeps
                    # preferring the entry's stale copy and every cycle repeats
                    # the whole refresh.
                    refreshed_tokens = new_creds.pop("_refreshed_tokens", None)
                    if refreshed_tokens:
                        refresh_state["skip_reload"] = True
                        hass.config_entries.async_update_entry(
                            entry,
                            data={**entry.data, CONF_LOGIN_TOKENS: refreshed_tokens},
                        )
                        _LOGGER.info(
                            "Persisted refreshed login tokens to config entry"
                        )
            except Exception:
                _LOGGER.debug("API credential refresh failed", exc_info=True)

        if new_creds and new_creds.get("password"):
            new_password = new_creds["password"]
            new_client_id = new_creds.get("clientId", "")
            _LOGGER.info(
                "Got fresh credentials (pw=%s..., clientId=%s), reconnecting",
                new_password[:8],
                new_client_id[:30] if new_client_id else "unchanged",
            )

            # Reset failure counter on success
            refresh_state["consecutive_failures"] = 0
            refresh_state["first_failure_at"] = 0.0

            # Credentials work again, so any Repairs card about them is stale.
            _clear_reauth_repair(hass, entry)

            # And so is any recorded reason for the credentials-valid sensor —
            # this is the one path that both mints a password AND reconnects
            # with it, so it is the strongest success signal there is.
            _record_credential_success(refresh_state)

            # Save new credentials to config
            refresh_state["skip_reload"] = True
            updated_data = {**entry.data, CONF_PASSWORD: new_password}
            if new_client_id:
                updated_data[CONF_CLIENT_ID] = new_client_id
            hass.config_entries.async_update_entry(entry, data=updated_data)

            # Stage new credentials, then reconnect via the client's reconnect
            # lock so this can't collide with the silence watchdog and spawn a
            # second paho client on the same clientId.
            from .mqtt_client import expires_at_from_account

            mqtt_client.stage_credentials(
                client_id=new_client_id,
                password=new_password,
                expires_at=expires_at_from_account(new_creds),
            )
            did = await hass.async_add_executor_job(
                mqtt_client.reconnect, "auth-failure: fresh credentials"
            )
            if did:
                await hass.async_add_executor_job(mqtt_client.send_query, vin)

            # Update credential timestamp
            entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            coord = entry_data.get("coordinator")
            if coord:
                coord.set_credential_timestamp()

            _LOGGER.info("Credential refresh complete — MQTT reconnected")
        else:
            # Increment failure counter
            refresh_state["consecutive_failures"] += 1
            failures = refresh_state["consecutive_failures"]
            if not refresh_state.get("first_failure_at"):
                refresh_state["first_failure_at"] = time.time()
            next_cooldown = min(
                AUTH_FAILURE_BASE_COOLDOWN * (2 ** failures),
                AUTH_FAILURE_MAX_BACKOFF,
            )
            _LOGGER.error(
                "Credential refresh failed — attempt #%d, next retry in %ds",
                failures, next_cooldown,
            )
            # The broker refused and the refresh produced nothing: this is the
            # evidence-backed failure, so it is what the sensor's reason names.
            _record_credential_failure(
                refresh_state, "credential refresh returned no password"
            )

            # Trigger reauth UI after enough consecutive failures *and* enough
            # time for the unattended renewal to have had its turn.
            failing_for = time.time() - (
                refresh_state.get("first_failure_at") or time.time()
            )
            if (
                failures >= AUTH_FAILURE_REAUTH_THRESHOLD
                and failing_for >= REAUTH_MIN_FAILING_SECONDS
            ):
                _LOGGER.warning(
                    "Auth failed %d times over %.0f min — triggering "
                    "re-authentication flow",
                    failures, failing_for / 60,
                )
                _start_reauth_if_idle(hass, entry)
            elif failures >= AUTH_FAILURE_REAUTH_THRESHOLD:
                _LOGGER.info(
                    "Auth failed %d times (%.0fs) — waiting for the renewal app "
                    "before asking for re-onboarding",
                    failures, failing_for,
                )

    except Exception as err:
        refresh_state["consecutive_failures"] += 1
        _record_credential_failure(
            refresh_state, f"credential refresh raised: {err}"
        )
        _LOGGER.exception("Error during credential refresh (attempt #%d)",
                          refresh_state["consecutive_failures"])
    finally:
        refresh_state["refreshing"] = False

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an XPENG config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # pop(..., None): a setup that failed part-way may leave no entry here,
        # and raising on that would skip the teardown below entirely - leaving
        # the MQTT client running under the old clientId. That is the state the
        # replace guard in async_setup_entry also defends against; both are
        # cheap and this is the one that runs on the ordinary reload path.
        data = (hass.data.get(DOMAIN) or {}).pop(entry.entry_id, None)
        if data:
            # Client first. Cancelling timers cannot be allowed to fail loudly
            # enough to skip it, so they come after and are individually guarded.
            client = data.get("mqtt_client")
            if client is not None:
                await hass.async_add_executor_job(client.disconnect)
            # After the client, so the frames already accepted are drained and the
            # capture ends with a clean END record rather than a truncated tail.
            capture_recorder = data.get("capture")
            if capture_recorder is not None:
                capture_summary = await hass.async_add_executor_job(
                    capture_recorder.stop, 10.0, "integration unloaded"
                )
                _LOGGER.info(
                    "Raw capture closed: %d frames, %d dropped, %d part(s)",
                    capture_summary.get("frames", 0),
                    capture_summary.get("dropped", 0),
                    len(capture_summary.get("parts", [])),
                )
            provider = data.get("teslamate_provider")
            if provider is not None:
                try:
                    await provider.async_stop()
                except Exception:  # noqa: BLE001 - unload must not fail on this
                    _LOGGER.warning(
                        "Stopping the TeslaMate provider failed", exc_info=True
                    )
            for key in (
                "cancel_timer",
                "cancel_api_refresh",
                "cancel_rest_timer",
                "cancel_watchdog",
            ):
                cancel = data.get(key)
                if cancel:
                    try:
                        cancel()
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("could not cancel %s on unload", key)
        # Unload services if no more entries
        if not hass.data.get(DOMAIN):
            await async_unload_services(hass)
    return unload_ok

async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update — skip reload if it's just a credential refresh."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    refresh_state = entry_data.get("refresh_state", {})
    if refresh_state.get("skip_reload"):
        refresh_state["skip_reload"] = False
        _LOGGER.debug("Skipping reload — credential refresh only")
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up the shared opt-in card after the last owning vehicle is removed."""
    from .frontend import async_remove_card

    await async_remove_card(hass, entry.entry_id)
