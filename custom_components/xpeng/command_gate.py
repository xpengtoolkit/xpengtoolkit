"""Enforce the command policy, and make a denial visible.

``command_policy.py`` decides *whether* something is allowed; this module is
the half that touches Home Assistant: it reads the caller, raises, logs, and
fires an event so a blocked command is something the user can automate on
rather than a silent nothing.

There are two independent checks, and keeping them apart is the point:

**The capability ceiling** (the mode) applies to *every* caller, including
calls with no user context - a Supervisor token, the renewal app, a script
using a long-lived token. It is the install's policy for what this integration
may do to the car at all, and a leaked token is the likeliest way someone gets
to it.

**The caller check** is Home Assistant's own permission model: a logged-in
user must have ``control`` on at least one XPENG entity to use an XPENG
service. Calls with no user context pass through, because the renewal app has
no user - it holds a Supervisor token - and refusing it would break unattended
renewal.

The integration's services take no ``entity_id`` (``xpeng.unlock`` has no
targets at all), so Home Assistant's per-entity permission checks never run for
them. That is exactly the hole the caller check fills. It is deliberately
written here instead of using ``homeassistant.helpers.service.verify_domain_control``
because that helper's signature changed in HA 2026.1 (``(hass, domain)`` became
``(domain)``) and this integration still supports 2024.7.0.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, Unauthorized, UnknownUser
from homeassistant.helpers import entity_registry as er

from .command_policy import (
    ACTION_COMMAND,
    ACTION_WAKE,
    CommandPolicy,
    GROUP_LABELS,
    WAKE_SERVICES,
    denial_reason,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

#: Denied commands fire this on the event bus, with the service/entity, group
#: and mode. This is the useful half of the "someone tried something" story:
#: an automation can alert on it, where a log line can only be read later.
EVENT_COMMAND_DENIED = f"{DOMAIN}_command_denied"

#: Where the user goes to change their mind, in every refusal message.
_HOWTO = (
    "Change it in Settings > Devices & Services > XPENG > Configure > "
    "Command permissions."
)

#: One warning per (service-or-entity, group, mode). An automation that retries
#: every minute must not fill the log; the first occurrence is the interesting
#: one and the event carries the rest.
_WARNED: set[tuple[str, str, str]] = set()


def policy_from_options(options: Any) -> CommandPolicy:
    """The policy an entry's options describe (absent -> FULL)."""
    return CommandPolicy.from_options(options)


def policy_for_entry(entry: ConfigEntry) -> CommandPolicy:
    """The policy of a config entry, tolerating published entries with none."""
    return CommandPolicy.from_options(getattr(entry, "options", None))


def _fire_denied(
    hass: HomeAssistant,
    *,
    policy: CommandPolicy,
    group: str,
    action: str,
    what: str,
    entity_id: str | None = None,
    user_id: str | None = None,
    service: str | None = None,
) -> None:
    """Record the refusal: one warning, then debug, plus an event every time."""
    key = (what, group, policy.mode)
    reason = denial_reason(policy, group, action)
    is_wake_only = action == ACTION_WAKE and policy.allows(group)
    if key not in _WARNED:
        _WARNED.add(key)
        _LOGGER.warning(
            "Refused %s: %s. Caller: %s. Further refusals of this command are"
            " logged at debug level. %s",
            what,
            reason,
            user_id or "no user context (automation, script or token)",
            _HOWTO,
        )
    else:
        _LOGGER.debug("Refused %s: %s", what, reason)

    data: dict[str, Any] = {
        "what": what,
        "group": group,
        "group_label": GROUP_LABELS.get(group, group),
        "mode": policy.mode,
        "permission": policy.permission(group),
        "action": action,
        "reason": reason,
    }
    if entity_id is not None:
        data["entity_id"] = entity_id
    if service is not None:
        data["service"] = service
    if user_id is not None:
        data["user_id"] = user_id
    hass.bus.async_fire(EVENT_COMMAND_DENIED, data)


async def async_verify_caller_control(
    hass: HomeAssistant, call: ServiceCall, *, entry_id: str | None = None,
) -> None:
    """Require caller control on the entry that receives this service call.

    Mirrors what Home Assistant's own entity service plumbing does for
    entity-targeted calls, which the XPENG services cannot benefit from because
    they do not use native entity targeting. The registered service supplies
    its resolved entry ID, so control of another car cannot authorize this one.
    No user context means an automation,
    script, Supervisor token or the renewal app: allowed, deliberately.
    """
    user_id = call.context.user_id
    if not user_id:
        return

    user = await hass.auth.async_get_user(user_id)
    if user is None:
        raise UnknownUser(context=call.context, permission=POLICY_CONTROL)

    if user.is_admin:
        return

    registry = er.async_get(hass)
    target = call.data.get("vehicle_entity_id")
    if target:
        entity = registry.async_get(target)
        if (
            entity is not None
            and entity.platform == DOMAIN
            and (entry_id is None or entity.config_entry_id == entry_id)
            and user.permissions.check_entity(target, POLICY_CONTROL)
        ):
            return
        raise Unauthorized(
            context=call.context, permission=POLICY_CONTROL, user_id=user_id
        )
    for entity in registry.entities.values():
        if entity.platform != DOMAIN:
            continue
        if entry_id is not None and entity.config_entry_id != entry_id:
            continue
        if user.permissions.check_entity(entity.entity_id, POLICY_CONTROL):
            return

    raise Unauthorized(
        context=call.context,
        permission=POLICY_CONTROL,
        user_id=user_id,
    )


async def async_guard_service(
    hass: HomeAssistant,
    call: ServiceCall,
    service: str,
    group: str,
    policy: CommandPolicy,
    *,
    entry_id: str | None = None,
) -> None:
    """Raise unless this service call is permitted. Call before doing any work.

    ``group`` of None means the service is exempt from the ceiling (credential
    import); the caller check still applies.
    """
    await async_verify_caller_control(hass, call, entry_id=entry_id)

    if group is None:
        return

    action = ACTION_WAKE if service in WAKE_SERVICES else ACTION_COMMAND
    if policy.action_allowed(group, action):
        return

    what = f"{DOMAIN}.{service}"
    _fire_denied(
        hass,
        policy=policy,
        group=group,
        action=action,
        what=what,
        user_id=call.context.user_id,
        service=service,
    )
    raise HomeAssistantError(
        f"'{what}' is blocked by this integration's command permissions: "
        f"{denial_reason(policy, group, action)}. {_HOWTO}"
    )


def guard_entity(
    hass: HomeAssistant,
    entity_id: str,
    group: str,
    policy: CommandPolicy,
    action: str = ACTION_COMMAND,
) -> None:
    """Raise unless an entity command is permitted.

    Synchronous on purpose: entity command methods are already async, but the
    decision needs no I/O, and keeping it sync means it can also be called from
    synchronous properties (the capability reduction in ``climate.py``).
    """
    if policy.action_allowed(group, action):
        return

    _fire_denied(
        hass,
        policy=policy,
        group=group,
        action=action,
        what=entity_id,
        entity_id=entity_id,
    )
    raise HomeAssistantError(
        f"'{entity_id}' is read-only or disabled by this integration's command "
        f"permissions: {denial_reason(policy, group, action)}. {_HOWTO}"
    )


# Entity denials carry no ``user_id``: an entity command method is called from
# core's service plumbing and has no ``ServiceCall`` to read a context from. It
# is not needed for the decision - core already enforces per-entity ``control``
# for entity-targeted services - so the event simply reports the entity and the
# group, and the caller check above covers the services that lack a target.
