"""What this integration is allowed to do to the car.

The integration exposes 42 command services plus ~30 command entities, and a
XPENG account can unlock a car. Everything that can act on the vehicle is
assigned to exactly one *capability group*, and every group carries one of
three permissions:

``FULL``      the command runs
``READ_ONLY`` the entity still reports state, the command is refused
``DISABLED``  the capability is not created at all

A *preset* is a named mapping of all groups at once. The presets are the only
thing the setup screen asks; the per-group mapping is what the advanced step of
the options flow edits, and it is the single source of truth - a preset is
never stored as behaviour, it is applied to the group map, so the two can never
disagree.

This module is deliberately free of Home Assistant imports. It holds no state
and performs no I/O, so the whole policy is testable without a running HA -
the same trick ``poll_status.py`` and ``watchdog_policy.py`` use. The
HA-facing half (reading options, checking the caller's HA permissions, raising)
lives in ``command_gate.py``.

Two properties are load-bearing and are pinned by tests:

* an **absent** option means ``FULL``. Every install that predates this feature
  keeps behaving exactly as it did.
* ``import_credentials`` is **exempt**. The renewal app calls it with a
  Supervisor token and no user context; gating it would break unattended
  renewal, which is the whole point of the app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# -- Capability groups ----------------------------------------------------
#
# Grouped by *what the action can do to the car*, not by subsystem. That
# distinction is why the door lock appears in two groups: locking only removes
# access, unlocking hands the car over. The same asymmetry applies to windows,
# the trunk and X-Guard, and a subsystem-shaped taxonomy would get all four
# wrong.

GROUP_QUERIES = "queries"
GROUP_COMFORT = "comfort"
GROUP_LIGHTS = "lights"
GROUP_CHARGING = "charging"
GROUP_SECURING = "securing"
GROUP_ACCESS = "access"
GROUP_POWER = "power"
GROUP_FIRMWARE = "firmware"
GROUP_ADVANCED = "advanced"

#: Display/log order. The options flow renders the matrix in this order.
ALL_GROUPS: tuple[str, ...] = (
    GROUP_QUERIES,
    GROUP_COMFORT,
    GROUP_LIGHTS,
    GROUP_CHARGING,
    GROUP_SECURING,
    GROUP_ACCESS,
    GROUP_POWER,
    GROUP_FIRMWARE,
    GROUP_ADVANCED,
)

#: Fallback labels, used in log lines, error messages and events. The user
#: interface gets its names from ``translations/en.json``; these exist so a
#: denial is readable in a log without a translation lookup.
GROUP_LABELS: Mapping[str, str] = {
    GROUP_QUERIES: "Queries and data",
    GROUP_COMFORT: "Comfort and climate",
    GROUP_LIGHTS: "Lights and alerts",
    GROUP_CHARGING: "Charging",
    GROUP_SECURING: "Securing actions",
    GROUP_ACCESS: "Access actions",
    GROUP_POWER: "Accessory power",
    GROUP_FIRMWARE: "Firmware and OTA",
    GROUP_ADVANCED: "Advanced and raw commands",
}

# -- Permissions ----------------------------------------------------------

PERM_FULL = "full"
PERM_READ_ONLY = "read_only"
PERM_DISABLED = "disabled"

ALL_PERMISSIONS: tuple[str, ...] = (PERM_FULL, PERM_READ_ONLY, PERM_DISABLED)


# -- Presets --------------------------------------------------------------

PRESET_READ_ONLY = "read_only"
PRESET_SAFE = "safe"
PRESET_SAFE_PLUS = "safe_plus"
PRESET_FULL = "full"
PRESET_CUSTOM = "custom"

#: Names this bundle has answered to before. ``command_mode`` values written by
#: an older app (or an older options form) must keep resolving to the *same*
#: permissions: an unknown preset falls back to FULL, so forgetting an alias
#: would silently widen someone's ceiling rather than narrow it - the wrong
#: direction for a permission to fail in.
PRESET_ALIASES: Mapping[str, str] = {"charging": PRESET_SAFE_PLUS}

#: The order the UI offers them in, narrowest first. ``preset_for`` walks this,
#: so no two entries may carry the same group map or the reported mode would be
#: whichever happened to come first.
SELECTABLE_PRESETS: tuple[str, ...] = (
    PRESET_READ_ONLY,
    PRESET_SAFE,
    PRESET_SAFE_PLUS,
    PRESET_FULL,
)


def _preset(*full_groups: str) -> dict[str, str]:
    """Build a group map: the named groups are FULL, everything else DISABLED."""
    return {
        group: (PERM_FULL if group in full_groups else PERM_DISABLED)
        for group in ALL_GROUPS
    }


#: The preset table, and the answer to "what does Safe actually include?".
#:
#: * ``read_only`` keeps queries because this car reports fresh data only after
#:   a wake; without them the sensors go stale and the mode looks broken. The
#:   wake itself is covered by the separate ``allow_wake`` toggle.
#: * ``safe`` adds the *securing* direction (lock, close windows, close trunk,
#:   arm X-Guard). Those only reduce access, so they cannot compromise the car,
#:   and leaving them out would mean a "safe" install cannot lock itself.
#: * ``safe_plus`` additionally allows charging and the V2L outlet. It is what
#:   a **new** entry starts at (``const.DEFAULT_COMMAND_MODE``), because it is
#:   what most installs are actually for - watching the car and charging it -
#:   while leaving unlocking, the trunk, OTA and raw commands switched off.
#: * ``full`` is everything. It is *not* the default for a new entry, but it is
#:   what an entry with no stored policy resolves to, which is what keeps an
#:   upgrade from silently removing capability from an existing install.
PRESETS: Mapping[str, Mapping[str, str]] = {
    PRESET_READ_ONLY: _preset(GROUP_QUERIES),
    PRESET_SAFE: _preset(GROUP_QUERIES, GROUP_COMFORT, GROUP_LIGHTS, GROUP_SECURING),
    PRESET_SAFE_PLUS: _preset(
        GROUP_QUERIES,
        GROUP_COMFORT,
        GROUP_LIGHTS,
        GROUP_CHARGING,
        GROUP_SECURING,
        GROUP_POWER,
    ),
    PRESET_FULL: _preset(*ALL_GROUPS),
}


def preset_for(groups: Mapping[str, str]) -> str:
    """Which preset this group map is, or ``custom`` if it is not one."""
    normalized = normalize_groups(groups)
    for name in SELECTABLE_PRESETS:
        if normalize_groups(PRESETS[name]) == normalized:
            return name
    return PRESET_CUSTOM


# -- Normalisation --------------------------------------------------------


def normalize_groups(raw: Mapping[str, Any] | None) -> dict[str, str]:
    """Coerce anything into a complete, valid group map.

    Absent groups become ``FULL``, not ``DISABLED``: an entry that predates
    this option, or a group added in a later version, must not silently lose
    capability. Unknown permission strings are treated the same way, because
    the failure mode of guessing "disabled" is a car that mysteriously will not
    charge.
    """
    raw = raw or {}
    groups: dict[str, str] = {}
    for group in ALL_GROUPS:
        value = raw.get(group, PERM_FULL)
        groups[group] = value if value in ALL_PERMISSIONS else PERM_FULL
    return groups


def default_policy() -> "CommandPolicy":
    """The policy of an entry that has never been configured (current behaviour)."""
    return CommandPolicy(groups=normalize_groups(None), allow_wake=True)


# -- Service -> group -----------------------------------------------------

#: Every command service registered by ``services.py``, and the group that
#: governs it. A test enumerates the registered services and fails if one is
#: missing here, so a future command cannot be silently ungated.
SERVICE_GROUPS: Mapping[str, str] = {
    # Queries and data. These cost nothing physically; they read state, push
    # content at the car's screen, or tidy server-side state.
    "force_update": GROUP_QUERIES,
    # Changes how often we ask, never whether we may wake anything: the queries it
    # raises are passive, so it belongs with the queries and data group.
    "charging_hint": GROUP_QUERIES,
    "check_ota": GROUP_QUERIES,
    "list_hvac_schedules": GROUP_QUERIES,
    "capture_snapshot": GROUP_QUERIES,
    "send_navigation": GROUP_QUERIES,
    "force_calendar_sync": GROUP_QUERIES,
    "clear_stuck_commands": GROUP_QUERIES,
    # Comfort and climate.
    "climate_on": GROUP_COMFORT,
    "climate_off": GROUP_COMFORT,
    "defrost_on": GROUP_COMFORT,
    "defrost_off": GROUP_COMFORT,
    "create_hvac_schedule": GROUP_COMFORT,
    "update_hvac_schedule": GROUP_COMFORT,
    "delete_hvac_schedule": GROUP_COMFORT,
    # Lights and alerts. ``honk_horn`` is the one member that is a nuisance
    # vector rather than a convenience; it is kept here to match the lights
    # surfaces it shares a switch with, and can be split out if asked.
    "flash_lights": GROUP_LIGHTS,
    "honk_horn": GROUP_LIGHTS,
    # Charging.
    "start_charge": GROUP_CHARGING,
    "stop_charge": GROUP_CHARGING,
    "set_charge_limit": GROUP_CHARGING,
    "set_charge_current": GROUP_CHARGING,
    "open_charge_port": GROUP_CHARGING,
    "close_charge_port": GROUP_CHARGING,
    # Securing actions - these only ever reduce access.
    "lock": GROUP_SECURING,
    "close_windows": GROUP_SECURING,
    "close_trunk": GROUP_SECURING,
    "sentinel_on": GROUP_SECURING,
    # Access actions - these are the ones that hand the car over.
    "unlock": GROUP_ACCESS,
    "open_trunk": GROUP_ACCESS,
    "open_windows": GROUP_ACCESS,
    "vent_windows": GROUP_ACCESS,
    "sentinel_off": GROUP_ACCESS,
    # Accessory power (the V2L outlet).
    "trunk_power_on": GROUP_POWER,
    "trunk_power_off": GROUP_POWER,
    # Firmware.
    "trigger_ota": GROUP_FIRMWARE,
    "cancel_ota": GROUP_FIRMWARE,
    "auto_upgrade_on": GROUP_FIRMWARE,
    "auto_upgrade_off": GROUP_FIRMWARE,
    "ota_feedback": GROUP_FIRMWARE,
    # Advanced and raw. ``send_raw_mqtt`` can express *any* command in any
    # other group, including ``unlock``, so it is a universal bypass and lives
    # at the most restricted level. Scenes are opaque server-side programs and
    # get the same treatment.
    "send_raw_mqtt": GROUP_ADVANCED,
    "execute_scene": GROUP_ADVANCED,
    "toggle_scene": GROUP_ADVANCED,
}

#: Services that wake the car (or force a full state upload). These are the
#: only non-passive members of the queries group, so they answer to the
#: separate ``allow_wake`` toggle as well as to the group permission.
WAKE_SERVICES: frozenset[str] = frozenset({"force_update"})

#: Services the command policy never touches. Credential import is the
#: renewal app's only call and it actuates nothing on the car; gating it would
#: break unattended renewal. (It is still covered by the caller-permission
#: check, which passes through no-user-context calls.)
EXEMPT_SERVICES: frozenset[str] = frozenset({"import_credentials"})


# -- Actions --------------------------------------------------------------

#: A normal command: allowed only at ``FULL``.
ACTION_COMMAND = "command"
#: A command that wakes the car: allowed at ``FULL`` and only when
#: ``allow_wake`` is on.
ACTION_WAKE = "wake"


# -- The policy object ----------------------------------------------------


@dataclass(frozen=True)
class CommandPolicy:
    """The resolved policy of one config entry.

    ``groups`` is always complete (see :func:`normalize_groups`), so callers
    never need a default of their own.
    """

    groups: Mapping[str, str] = field(default_factory=lambda: normalize_groups(None))
    allow_wake: bool = True

    # -- construction ----------------------------------------------------

    @classmethod
    def from_options(cls, options: Mapping[str, Any] | None) -> "CommandPolicy":
        """Read the policy out of ``entry.options``.

        Options are written flat (one key per group) so the form the user sees
        and the thing that is stored are the same shape. An entry with no
        policy at all resolves to FULL.
        """
        options = options or {}
        raw = {group: options.get(group) for group in ALL_GROUPS}
        # ``command_mode`` is stored for the UI's benefit only; the groups are
        # authoritative. A stale mode is corrected on read rather than trusted.
        return cls(
            groups=normalize_groups(raw),
            allow_wake=bool(options.get("allow_wake", True)),
        )

    def to_options(self) -> dict[str, Any]:
        """Flatten into the keys ``entry.options`` holds."""
        options: dict[str, Any] = {
            group: self.groups[group] for group in ALL_GROUPS
        }
        options["allow_wake"] = self.allow_wake
        options["command_mode"] = self.mode
        return options

    @classmethod
    def from_preset(cls, preset: str, allow_wake: bool = True) -> "CommandPolicy":
        """Build from a preset name; unknown names fall back to FULL.

        Old names are resolved through ``PRESET_ALIASES`` first, so a stored
        ``charging`` means what it always meant instead of falling through to
        the FULL fallback.
        """
        preset = PRESET_ALIASES.get(preset, preset)
        table = PRESETS.get(preset, PRESETS[PRESET_FULL])
        return cls(groups=normalize_groups(table), allow_wake=allow_wake)

    # -- inspection ------------------------------------------------------

    @property
    def mode(self) -> str:
        """The preset this policy is, or ``custom``."""
        return preset_for(self.groups)

    def permission(self, group: str) -> str:
        return self.groups.get(group, PERM_FULL)

    def allows(self, group: str) -> bool:
        """May a command in this group be sent?"""
        return self.permission(group) == PERM_FULL

    def exposes(self, group: str) -> bool:
        """Should this group's entities exist at all?

        True for both ``FULL`` and ``READ_ONLY``: a read-only entity still has
        a state worth showing. Only ``DISABLED`` removes the entity.
        """
        return self.permission(group) != PERM_DISABLED

    def read_only(self, group: str) -> bool:
        """Entities exist, commands do not."""
        return self.permission(group) == PERM_READ_ONLY

    def action_allowed(self, group: str, action: str = ACTION_COMMAND) -> bool:
        """The one question the gate asks."""
        if not self.allows(group):
            return False
        if action == ACTION_WAKE and not self.allow_wake:
            return False
        return True

    def can_wake(self) -> bool:
        """Used by the coordinator's own wake paths."""
        return self.allow_wake and self.allows(GROUP_QUERIES)


def service_group(service: str) -> str | None:
    """The group governing a registered service, or None if it is exempt."""
    if service in EXEMPT_SERVICES:
        return None
    return SERVICE_GROUPS.get(service)


def is_wake_service(service: str) -> bool:
    return service in WAKE_SERVICES


def denial_reason(policy: CommandPolicy, group: str, action: str) -> str:
    """A sentence a user can act on, used in the raised error and the event."""
    label = GROUP_LABELS.get(group, group)
    if action == ACTION_WAKE and policy.allows(group) and not policy.allow_wake:
        return (
            "waking the car is switched off for this integration "
            "(allow wake is disabled)"
        )
    return (
        f"the '{label}' group is set to {policy.permission(group)} "
        f"in this integration's command permissions (mode: {policy.mode})"
    )
