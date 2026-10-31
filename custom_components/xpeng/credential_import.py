"""Import a token + tenant pair into an existing config entry.

The Repairs card and the ``xpeng.import_credentials`` service do the same job, and
both must do it the same way: validate the pair by minting MQTT credentials, refuse
a mismatched tenant, adopt the device identity the tenant carries, then install it.

Installing it is not always a reload. A **renewal** - the same car, the same tenant
device id, only a newer token pair and a freshly minted MQTT account - is applied to
the running client instead. Reloading tears every entity down and creates it again,
so a renewal schedule had the whole integration flapping between ``unavailable`` and
its values, twice per renewal (the entry-update listener reloaded as well as this
function). A pair that names a *different* car or device id still reloads, because
the VIN decides every entity id and the tenant's device id is the key the pair is
sealed with.

The credentials themselves are never logged.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant

from . import _clear_reauth_repair, _record_credential_success
from .config_flow import (
    _import_mqtt_credentials,
    _read_text_file,
    _serialize_tenant,
    _tenant_from_text,
    _TokenImportError,
    _tokens_from_text,
    _vin_from_tokens,
)
from .const import (
    AUTH_METHOD_IMPORT,
    CONF_AUTH_METHOD,
    CONF_CLIENT_ID,
    CONF_CONFIGURED_DEVICE_ID,
    CONF_LAST_RENEWAL,
    CONF_LOGIN_TOKENS,
    CONF_PASSWORD,
    CONF_SERVER,
    CONF_TENANT_DATA,
    CONF_VIN,
    CONF_XPENG_EMAIL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_IMPORT_CREDENTIALS = "import_credentials"

#: Same shape as the repair form: contents or a path for each half of the pair.
IMPORT_CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("tokens_json"): str,
        vol.Optional("tokens_path"): str,
        vol.Optional("tenant_json"): str,
        vol.Optional("tenant_path"): str,
        vol.Optional("vin"): str,
    }
)


def _vin_check(
    entry_vin: str, pair_vin: str, caller_vin: str = ""
) -> tuple[str, str] | None:
    """The ``(pair, target)`` VINs when this pair belongs to another car, else None.

    An import names its target entry and the pair names its car, and nothing used
    to compare the two: a pair from car B imported into car A's entry overwrote
    A's tokens, account, client id and device id, while every response still
    reported A's VIN because the VIN is resolved from the entry first. That is
    how a two-entry install ended up with one account on the other entry's
    entities (issue #11).

    A pair with no readable VIN (an older capture) and an entry with none (created
    before the field existed) are not a mismatch; those keep the old fallbacks.
    """
    entry_vin = str(entry_vin or "").strip().upper()
    pair_vin = str(pair_vin or "").strip().upper()
    caller_vin = str(caller_vin or "").strip().upper()
    if pair_vin and entry_vin and pair_vin != entry_vin:
        return pair_vin, entry_vin
    if pair_vin and caller_vin and pair_vin != caller_vin:
        return pair_vin, caller_vin
    return None


def _identity_moved(entry: Any, resolved_vin: str, device_id: str) -> bool:
    """True when this pair describes a different car than the entry already holds.

    A renewal answers False: same car, same tenant device id, so no entity id and
    no option needs rebuilding. A pair from a different onboarding run answers
    True - the VIN is part of every entity id, and the tenant's device id is what
    the key pool is sealed against (``md5(deviceId)``), so both must be adopted
    through a reload rather than swapped underneath the running entities.
    """
    stored_vin = str(entry.data.get(CONF_VIN) or "").strip().upper()
    configured_id = str(entry.options.get(CONF_CONFIGURED_DEVICE_ID) or "").strip()
    return bool(
        # Anything the entry does not already hold counts as a move: reloading is
        # the safe side of this call, and a renewal never reaches it.
        (resolved_vin and resolved_vin != stored_vin)
        or (device_id and device_id != configured_id)
    )


async def _apply_to_running_client(
    hass: HomeAssistant,
    entry_data: dict[str, Any],
    client: Any,
    *,
    client_id: str,
    password: str,
    server: str,
    vin: str,
    renewed_at: Any,
    expires_at: float = 0.0,
) -> None:
    """Stage a renewed pair on the live MQTT client and reconnect once.

    Everything a reload would rebuild - entity ids, options, the REST timers, the
    entity registry - is still valid when the identity has not moved. Only the
    credentials are new, so only the credentials are applied.
    """
    client.stage_credentials(
        client_id=client_id,
        password=password,
        server=server,
        expires_at=expires_at,
    )
    reconnected = await hass.async_add_executor_job(
        client.reconnect, "credential import: renewed pair"
    )
    if reconnected and vin:
        await hass.async_add_executor_job(client.send_query, vin)

    coordinator = entry_data.get("coordinator")
    if coordinator is not None:
        # Both clocks this arrival moves. Setup normally sets them from the entry
        # after a reload; without one, they are set here or the renewal would be
        # invisible until the next restart.
        coordinator.set_credential_timestamp()
        coordinator.set_last_renewal(renewed_at)
    # The mint above is the strongest success signal there is, so the
    # credentials-valid diagnostic must not keep reporting an older failure until
    # the next rotation happens to clear it.
    _record_credential_success(entry_data.get("refresh_state"))


async def async_import_credentials(
    hass: HomeAssistant,
    entry: Any,
    *,
    tokens_json: str = "",
    tokens_path: str = "",
    tenant_json: str = "",
    tenant_path: str = "",
    vin: str = "",
) -> dict[str, Any]:
    """Validate a pair and install it into ``entry``.

    Applied to the running client when it is the same car, reloaded when it is
    not. Raises ``_TokenImportError`` (``.key`` names the failure) so both callers
    can report a reason without touching the payload.
    """
    tokens_text = (tokens_json or "").strip()
    tenant_text = (tenant_json or "").strip()

    if not tokens_text:
        path = (tokens_path or "").strip()
        if not path:
            raise _TokenImportError(
                "invalid_tokens", "no token file contents or path was given"
            )
        tokens_text = await hass.async_add_executor_job(
            _read_text_file, path, "invalid_tokens_path"
        )
    if not tenant_text:
        path = (tenant_path or "").strip()
        if not path:
            raise _TokenImportError(
                "invalid_tenant", "no tenant file contents or path was given"
            )
        tenant_text = await hass.async_add_executor_job(
            _read_text_file, path, "invalid_tenant_path"
        )

    tokens = _tokens_from_text(tokens_text)
    tenant = _tenant_from_text(tenant_text)
    # Which car this pair is actually for. The entry is the import target, but
    # the pair decides the car (issue #11: the two used to be allowed to
    # disagree, and the entry kept its own VIN while taking the other car's
    # credentials). Checked before the mint, because a mismatched pair must not
    # spend an API call - nor be applied anywhere.
    mismatch = _vin_check(
        str(entry.data.get(CONF_VIN) or ""),
        _vin_from_tokens(tokens),
        vin,
    )
    if mismatch is not None:
        pair_vin, target_vin = mismatch
        raise _TokenImportError(
            "vin_mismatch",
            f"the pair is for VIN {pair_vin} but the import target is VIN {target_vin}",
        )
    # Mints MQTT credentials against the live API: this is what proves the pair is
    # current and belongs together, so a stale file cannot silently replace a
    # working one.
    client_id, password, server = _import_mqtt_credentials(tokens, tenant)
    # The imported pair may carry the mint's expiry (the onboarding tool stores
    # the whole mqttAccount). Zero means "unknown", which the client treats as
    # expired and mints a fresh credential on demand.
    from .mqtt_client import expires_at_from_account

    account = tokens.get("mqttAccount")
    expires_at = expires_at_from_account(account if isinstance(account, dict) else None)

    resolved_vin = (
        (vin or "").strip().upper()
        or entry.data.get(CONF_VIN, "")
        or _vin_from_tokens(tokens)
    )
    device_id = str(tenant.get("deviceId") or "").strip()
    # The clock behind sensor.xpeng_<car>_last_renewal. Computed once and used for
    # both the entry and the running coordinator: a reload would have carried the
    # entry's copy into setup, and without one the coordinator needs the same value.
    renewed_at = time.time()

    # The entry that is already running, if any: its MQTT client is what a renewal
    # has to be applied to, and its refresh_state carries the one-shot flag that
    # stops the entry-update listener adding a second reload.
    live: dict[str, Any] = (hass.data.get(DOMAIN) or {}).get(entry.entry_id) or {}
    client = live.get("mqtt_client")
    reload_needed = client is None or _identity_moved(entry, resolved_vin, device_id)

    refresh_state = live.get("refresh_state")
    if isinstance(refresh_state, dict):
        # One-shot, consumed by _async_update_listener. Without it the listener
        # reloads *and* this function reloads, which is the second half of the
        # flap: two teardowns of every entity per renewal.
        refresh_state["skip_reload"] = True

    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_CLIENT_ID: client_id,
            CONF_PASSWORD: password,
            CONF_SERVER: server,
            CONF_AUTH_METHOD: AUTH_METHOD_IMPORT,
            CONF_LOGIN_TOKENS: tokens,
            # The clock behind sensor.xpeng_<car>_last_renewal. This function is the
            # app's renewal path (and the Repairs card's), so a renewal is exactly
            # what this stamp records - see the sensor for why it matters.
            CONF_LAST_RENEWAL: renewed_at,
            CONF_TENANT_DATA: _serialize_tenant(tenant),
            **({CONF_VIN: resolved_vin} if resolved_vin else {}),
            **(
                {CONF_XPENG_EMAIL: tokens["account_email"]}
                if tokens.get("account_email")
                else {}
            ),
        },
        # The tenant carries the device identity the pair was sealed to, so it wins
        # over any previously stored id.
        options={**entry.options, CONF_CONFIGURED_DEVICE_ID: device_id},
    )
    _clear_reauth_repair(hass, entry)

    if reload_needed:
        await hass.config_entries.async_reload(entry.entry_id)
        _LOGGER.info(
            "Imported credentials for %s (device %s) - reloaded",
            resolved_vin or entry.title,
            device_id[:8] + "..." if device_id else "unknown",
        )
    else:
        await _apply_to_running_client(
            hass,
            live,
            client,
            client_id=client_id,
            password=password,
            server=server,
            vin=resolved_vin,
            renewed_at=renewed_at,
            expires_at=expires_at,
        )
        _LOGGER.info(
            "Imported credentials for %s (device %s) - applied, no reload",
            resolved_vin or entry.title,
            device_id[:8] + "..." if device_id else "unknown",
        )
    return {
        "entry_id": entry.entry_id,
        "vin": resolved_vin,
        "device_id": device_id,
        "account": tokens.get("account_email"),
        "client_id": client_id,
    }


__all__ = [
    "IMPORT_CREDENTIALS_SCHEMA",
    "SERVICE_IMPORT_CREDENTIALS",
    "async_import_credentials",
]
