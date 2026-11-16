"""Which ``instant.door`` values may be believed, and how old that section is.

Issue #32 (2026-11-16, G9): after walk-away locking the car stops pushing
before the lock happens, so the last `instant.door` it sent still says
"unlocked" and "trunk open" - for hours. The REST widget fallback keeps being
polled and *does* carry a `door` block, but the whole section is excluded from
that merge because its window fields are on a different scale (0/1 rather than
the 0-100 MQTT positions, which once overwrote good positions).

The lock and trunk are plain states, not positions, and they are the only two
fields the lock and trunk entities read from that section. Those two are
allowed through the fallback, and only when the value is one the entity can
interpret: an unrecognised number is no evidence at all, so the held value
stands rather than being replaced by a guess. This module is the single place
that decides, and ``cover.py`` reads the same map, so "the widget says 5" and
"the trunk reads open" cannot drift apart.

No Home Assistant import: it is loaded by path in the tests, like the other
guards (charge_guard, replay_guard, energy_guard).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

#: Trunk states from ``instant.door.trunk_ajar_st_new``. Measured on a live
#: car: closed = 1, open = 5 (docs/ANDROID_COMMAND_AUDIT.md); 3, 4, 7 and 8
#: were seen open elsewhere. 2 and 6 have never been observed, so they are not
#: guessed at - an unknown state reads as unknown, never as "closed".
TRUNK_CLOSED = frozenset({0, 1})
TRUNK_OPEN = frozenset({3, 4, 5, 7, 8})

#: ``driver_door_lock_st``: 1 = locked, 0 = unlocked (see const.py's sensor).
LOCKED = 1
UNLOCKED = 0

#: The ``door`` fields the REST widget may correct while the push stream is
#: silent. Everything else in that section - above all the window positions -
#: stays MQTT's to report (see coordinator._REST_MERGE_EXCLUDED_SECTIONS).
WIDGET_DOOR_FIELDS = ("driver_door_lock_st", "trunk_ajar_st_new")


def _state_int(value: Any) -> int | None:
    """``value`` as a state number, or None when it is not one.

    ``type() is int`` rather than ``isinstance`` so a bool - which would mean
    a flag, not a state - is not silently read as 0/1.
    """
    return value if type(value) is int else None


def lock_is_locked(value: Any) -> bool | None:
    """Whether ``driver_door_lock_st`` says locked, or None when unreadable."""
    number = _state_int(value)
    if number == LOCKED:
        return True
    if number == UNLOCKED:
        return False
    return None


def trunk_is_closed(value: Any) -> bool | None:
    """Whether ``trunk_ajar_st_new`` says closed, or None when unreadable."""
    number = _state_int(value)
    if number in TRUNK_CLOSED:
        return True
    if number in TRUNK_OPEN:
        return False
    return None


def widget_door_overlay(values: Any) -> tuple[dict[str, int], list[str]]:
    """The widget ``door`` fields that may correct the held state.

    Returns the accepted subset and the names of the fields it refused, so the
    caller can log the refusal. A refused field is not overwritten: on a car
    whose widget reports these on some other scale the fallback then does
    nothing, which is the safe end of the trade - worse is a lock entity that
    claims "locked" because it misread a position.
    """
    if not isinstance(values, dict):
        return {}, []
    checks = {
        "driver_door_lock_st": lock_is_locked,
        "trunk_ajar_st_new": trunk_is_closed,
    }
    accepted: dict[str, int] = {}
    refused: list[str] = []
    for field in WIDGET_DOOR_FIELDS:
        if field not in values:
            continue
        value = values[field]
        if checks[field](value) is None:
            refused.append(field)
        else:
            accepted[field] = value
    return accepted, refused


def age_attributes(
    updated_at: datetime | None, now: datetime | None = None
) -> dict[str, Any]:
    """The ``door_state_*`` attributes for a lock or trunk entity.

    ``updated_at`` is when the door section was last actually refreshed (a push
    carrying it, a lock/trunk acknowledgement, or the widget fallback). None -
    nothing carried it yet this session - yields no attributes rather than a
    flattering zero: the entity still reports the value it holds, and a card is
    told that nothing is known about its age.
    """
    if not isinstance(updated_at, datetime):
        return {}
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return {
        "door_state_updated_at": updated_at.isoformat(timespec="seconds"),
        "door_state_age_seconds": max(
            0, int((reference - updated_at).total_seconds())
        ),
    }
