"""A charge reading that is XPENG's "no data" placeholder, and its repair.

Born from issue #24 (Xpengtk 0.6.41, 2026-11-05, X9): while the car was parked
and asleep, ``instant.charge.battery_soc`` arrived as ``-1`` - and
``interval.charge.charge_rate`` as ``-1`` in the same frame - while ``range``
in that same frame was correct. The official app showed the real 24 % at that
moment, so the value existed and this frame simply did not carry it. The
integration published the ``-1``: on a plain sensor any number passes through
(``sensor.py`` only rejects sentinels for ``SensorDeviceClass.ENUM``), so
between 02:53 and 08:56 that morning the dashboard drew the battery dropping
from a real reading to -1 % and back, four times, and the recorder kept every
one of them.

The rule is the one ``sentinel_guard`` already applies to a wake frame's 0 kPa
tyres and ``replay_guard`` to an odometer of -1 km: a value that cannot be a
reading means "not reported", and the last real reading is carried forward
rather than published. Asking the question before the replay checks matters,
because a negative SOC sits below everything held - the parked-SOC replay guard
would otherwise look at such a frame and describe it as an older snapshot, which
is a different fault with a different fix.

Deliberately narrow, in four ways:

* Only a *negative* value is a placeholder. 0 % is a real, if unhappy, state of
  charge and 0 a real rate, so neither is touched: a genuine collapse to 0 %
  still shows up.
* Only the two fields in the report are repaired. ``interval.charge`` also
  carries ``power``, ``voltage``, ``electricity`` and ``discharge_capacity``;
  nothing yet says the cloud placeholders those, and a guard that guesses at
  payload shapes it has never measured is how real data starts disappearing.
* The rest of the frame survives. That is not incidental here: issue #24's
  frame carries a valid ``range`` alongside the ``-1`` SOC, so refusing the
  frame would trade a wrong reading for a stale one.
* Dropping beats inventing. With no earlier real value the key is removed and
  the sensor reads unknown, rather than ``-1`` being relabelled as data.

``charge_rate`` carries one caveat that belongs where the code can see it. An
earlier note from this project's own data mining (``VEHICLE_DATA.md``: "Charge
rate indicator (-1 when not charging)") suggests a negative there may be the
*idle* encoding rather than a damaged frame. If that is what it is, carrying the
last real rate forward leaves a stale number visible while the car sits idle
instead of the -1 the raw field holds. Carrying forward is still the choice made
here: the alternative is publishing -1, the one value that is certainly not a
rate, and the README promises an entity holds its last known value when the car
does not report. Issue #24 is where this gets settled if a second car reports
the difference.

Deliberately no Home Assistant import, like replay_guard.py and sentinel_guard.py:
the decision is small and is tested without a car.
"""

from __future__ import annotations

from typing import Any

#: The two fields issue #24 measured. Paths are rooted at the coordinator state
#: (which nests ``msg_content``), so they walk the same tree the sensors read.
SOC_PATH: tuple[str, ...] = ("msg_content", "instant", "charge", "battery_soc")
RATE_PATH: tuple[str, ...] = ("msg_content", "interval", "charge", "charge_rate")

#: Sentinel for "remove this leaf", distinct from ``None``, which is a value.
_REMOVE = object()


def _parent(container: Any, path: tuple[str, ...]) -> tuple[dict[str, Any], str] | None:
    """The dict holding the leaf at *path*, with the leaf's own key.

    ``None`` when any step of the path is missing or is not a dict - a shorthand
    for "this frame does not carry that field", which is not something to repair.
    """
    node = container
    for key in path[:-1]:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if not isinstance(node, dict):
        return None
    return node, path[-1]


def is_placeholder(value: Any) -> bool:
    """Whether *value* is a "not reported" placeholder rather than a reading.

    Negative and nothing else. A missing field is not a placeholder - the caller
    has already established that the leaf is there - and anything that is not
    recognisably a number is left alone: the point is to drop the one value that
    is provably impossible, not to second-guess a payload shape never seen here.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value < 0
    if isinstance(value, str):
        try:
            return float(value.strip()) < 0
        except ValueError:
            return False
    return False


def _replace(state: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
    """Return *state* with the leaf at *path* set, or removed when ``_REMOVE``.

    Copies only the dicts along the path, so a frame that needs no repair is
    handed back by identity and the sections beside it are shared, not cloned.
    """
    head = dict(state)
    node = head
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            return state
        node[key] = dict(child)
        node = node[key]
    if value is _REMOVE:
        node.pop(path[-1], None)
    else:
        node[path[-1]] = value
    return head


def with_previous_charge(state: Any, previous: Any = None) -> Any:
    """Return *state* with unreported charge readings replaced by the last real ones.

    *previous* is the last accepted state (the coordinator's ``_last_full_data``)
    and supplies the real value to carry forward. When the car never reported a
    real value the placeholder is removed instead, so the sensor reads unknown
    rather than ``-1``.

    The same *state* object comes back when nothing needs repairing, so the
    caller can skip rebuilding the state by identity alone.
    """
    if not isinstance(state, dict):
        return state

    repaired = state
    for path in (SOC_PATH, RATE_PATH):
        current = _parent(repaired, path)
        if current is None:
            continue
        parent, leaf = current
        if leaf not in parent or not is_placeholder(parent[leaf]):
            continue

        held = _parent(previous, path)
        carried = held[0].get(held[1]) if held is not None else None
        replacement = (
            carried if carried is not None and not is_placeholder(carried) else _REMOVE
        )
        repaired = _replace(repaired, path, replacement)
    return repaired
