"""Recognise a vehicle state that is a replay of an older snapshot.

Measured on the live install (.88), 2026-10-21:

    13:41:27  car parked; the last message carrying charge/odometer arrives
    13:41+    the MQTT push fanout wedges and replays the pre-drive snapshot
    13:47-16:10  every ~15 min: the REST widget writes the truth (47%, 257 km,
                 odometer 62234), then a replayed MQTT snapshot overwrites it
                 with the pre-drive state (52%, 277 km, odometer 62210)
    16:16     the replayed state is still what the integration holds (52%)
    16:20     one force-upload gets a real full state back: 47% / 257 km / 62234

The two sources are not equally trustworthy and the difference is visible in
the data: **the odometer**. ``total_mileage`` only ever increases, so a state
whose odometer is below the last one accepted cannot be new - it is an older
snapshot being delivered again. (In the measurement above the replay was 24 km
behind, which is what identified it.)

Rejecting such a state does two things, and both are needed:

  * it stops the replay overwriting fresher data, which is what let the sensor
    flap between 47% and 52% all afternoon, and
  * it keeps the replay out of the "a message carried data" clock, so the
    silence watchdog can escalate to the one force-upload that heals a wedged
    fanout. Before this, a replay was indistinguishable from data, so the
    escalation threshold was never reached and nothing self-healed.

This module deliberately has no Home Assistant import, like watchdog_policy.py:
the decision is small, and it should be testable without a car.
"""

from __future__ import annotations

from typing import Any

#: How far below the held odometer a state may sit and still be plausible.
#: The odometer moves in whole kilometres on this car, so anything more than a
#: rounding wobble below the last accepted value is a replay, not noise.
ODOMETER_TOLERANCE_KM = 0.5

#: An odometer at or below this is not a reading at all: it is the REST widget's
#: "no data" placeholder. Reported on #21 (2026-11-01) by a two-car install whose
#: second entry kept answering ``odometer -1.0`` while the first worked. A real
#: odometer is non-negative, so a genuine reading cannot be mistaken for it - and
#: a sentinel must never be merged, or the odometer sensor would read -1 km.
SENTINEL_ODOMETER_MAX_KM = 0.0


def as_number(value: Any) -> float | None:
    """Coerce an odometer reading, or return None when there is nothing usable.

    XPENG sends these as ints, but the REST side has been seen to stringify
    them, and a missing/empty value must mean "no verdict" rather than zero -
    a zero would look like a replay of every state ever held.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _dig(container: Any, keys: tuple[str, ...]) -> Any:
    node = container
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


#: Where a coordinator state keeps the odometer (the sensor's value_path).
_STATE_PATH = ("msg_content", "instant", "odometer", "total_mileage")

#: The REST widget payload has the sections at the top level instead.
_WIDGET_PATH = ("odometer", "total_mileage")


def odometer_from_state(state: Any) -> float | None:
    """The odometer in a coordinator state (``msg_content.instant.odometer``)."""
    return as_number(_dig(state, _STATE_PATH))


def odometer_from_widget(widget: Any) -> float | None:
    """The odometer in a REST ``widget_state`` payload."""
    return as_number(_dig(widget, _WIDGET_PATH))


def is_replay(
    candidate: float | None,
    held: float | None,
    tolerance: float = ODOMETER_TOLERANCE_KM,
) -> bool:
    """Whether *candidate* is an older snapshot than *held*.

    No verdict (either side unknown) is False: an unreadable odometer must not
    stop real data being accepted.
    """
    if candidate is None or held is None:
        return False
    return candidate < held - tolerance


def is_sentinel_odometer(candidate: float | None) -> bool:
    """Whether *candidate* is XPENG's "no data" placeholder, not a reading.

    This is a different question from :func:`is_replay` and deliberately kept
    separate: a sentinel is *always* below any held value, so the replay test
    answers "older snapshot" for it - which is wrong twice over. It names the
    wrong cause in the log (the 2026-11-01 report chased a replay that was not
    happening) and it hides the actual signal, that the cloud had nothing to
    give this account or vehicle at all.

    Callers must treat it as "no usable payload" and drop it: merging would put
    ``-1`` km on the odometer sensor.
    """
    return candidate is not None and candidate <= SENTINEL_ODOMETER_MAX_KM
