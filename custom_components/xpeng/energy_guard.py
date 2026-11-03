"""An older snapshot is not energy data - and a parked car's SoC cannot rise.

The battery sensor flapped between two values for hours, on two different cars,
for two different reasons that share one fact: state of charge only falls while
a car is parked and idle, so a reading that rises is a snapshot from before the
drain rather than a new measurement.

Measured on the reference G6, 2026-10-23 17:49-20:04 (Australia/Sydney). The
last push carrying charge/odometer arrived at 17:49:42 with SOC 50. The push
fan-out then wedged; the car answered queries with acks. The REST widget
fallback - the live value, polled every ``parked_scan_interval`` - wrote 49 at
19:27:10, 19:42:56 and 19:58:41 ("Merged widget_state ... MQTT silent for
515s/561s/606s", and the recorder's 19:27:10.220718 row matches the log line to
the millisecond). Between those merges, charge-less partial frames made the
MQTT client push its own baseline - still the frozen 50 - through
``set_vehicle_data``, so the sensor went 49 -> 50 -> 49 -> 50 every ~15 min for
an hour. ``replay_guard.is_replay`` could not see it: the odometer is frozen
while the car is parked, and the guard needs it to fall.

The G9 report of the same day is the same fight with a longer stall: a high
value frozen on the history graph for hours, notched down to a slowly declining
one. There the replay arrives as a whole snapshot rather than a baseline
re-broadcast, and the odometer is equally uninformative.

So this module adds the one signal the energy itself carries - direction - and
the acceptance path rejects a state whose state of charge rises while the car
is parked and not charging. Rejecting it rather than carrying the held value
forward matters: a refused state is kept out of the "a message carried data"
clock, which is what lets the silence watchdog escalate to the force-upload
that heals a wedged fan-out (the same reason ``replay_guard.py`` refuses a
falling odometer rather than repairing it).

The tolerance is ``SOC_ROUNDING_TOLERANCE_PCT``: the resolution of the value,
and the size of the BMS wobble seen while sitting, so a reading that merely
rounds differently is not called a replay. While charging, and whenever the
parked verdict is not known, no verdict is returned - charging is one legitimate
reason for a parked car's SoC to rise.

It is not the only one, and issue #23 (2026-11-03) is the case that proves it. A
scheduled charge that starts *and* finishes between two live frames is invisible
to both: the held frame predates the session and reads Scheduled, the candidate
postdates it and reads Connected with zero power, so no frame ever says
"charging". The reported X9 sat at 24 % for 90 minutes after charging to 70 %
overnight, because the watchdog's own force-upload produced exactly that pair of
frames and the guard refused it three times. A cable in the port is therefore
accepted as the second reason, read from the candidate - see
``parked_soc_replay`` for why the held state cannot answer it.
"""

from __future__ import annotations

from typing import Any

#: The resolution of ``battery_soc``. A rise within this many percent is inside
#: the noise (rounding, a BMS recalibration while sitting), not a replay.
SOC_ROUNDING_TOLERANCE_PCT = 1.0


def _as_float(value: Any) -> float | None:
    """A number, or None when the value is not one (bools are not numbers)."""
    if value is None or isinstance(value, bool):
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


def _dig(container: Any, *keys: str) -> Any:
    node = container
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def soc_of(state: Any) -> float | None:
    """The state of charge in a coordinator state (``msg_content.instant...``)."""
    return _as_float(_dig(state, "msg_content", "instant", "charge", "battery_soc"))


def state_is_charging(state: Any) -> bool:
    """Whether a state says a charge session is running.

    The same two signals the coordinator's ``is_charging`` uses: the car's own
    ``charging_state`` 2, and non-zero ``interval.charge.power``, which catches
    a charger the car has not admitted to yet (EVCC and friends).
    """
    if not isinstance(state, dict):
        return False
    content = state.get("msg_content")
    if not isinstance(content, dict):
        return False
    instant = content.get("instant")
    if isinstance(instant, dict) and _dig(instant, "charge", "charging_state") == 2:
        return True
    interval = content.get("interval")
    if isinstance(interval, dict):
        power = _as_float(_dig(interval, "charge", "power"))
        if power is not None and power > 0:
            return True
    return False


def charge_section(state: Any) -> dict:
    """The ``msg_content.instant.charge`` block of a coordinator state, or {}.

    Kept here rather than reached for from ``charge_port`` because this module is
    loaded standalone by path in the tests (no package context), and a relative
    import would make that load fail.
    """
    content = _dig(state, "msg_content")
    charge = _dig(content, "instant", "charge") if isinstance(content, dict) else None
    return charge if isinstance(charge, dict) else {}


def parked_soc_replay(
    candidate: Any,
    held: Any,
    *,
    parked: bool | None,
    charging: bool,
    plugged_in: bool | None = None,
    tolerance: float = SOC_ROUNDING_TOLERANCE_PCT,
) -> bool:
    """Whether *candidate* is an older snapshot of a parked, idle car.

    No verdict unless the caller has already established that the car was
    parked and not charging - the conditions under which state of charge can
    only fall. ``charging`` should account for the candidate as well as the
    held state, so a frame that starts a charge session is never refused.

    ``plugged_in`` is the second legitimate reason for a rise, and issue #23 is
    what made it necessary: a scheduled session that **starts and finishes
    between two live frames** never appears as charging in either one. The held
    frame still says Scheduled, the candidate says Connected with the session
    over, and 46 % of driving range arrived while nobody was watching. A cable in
    the port is the physical evidence that a charge was possible, so a rise is
    not called a replay while one is attached. Measured on the reported payloads
    (held 24 % / state 15 + plan armed; candidate 70 % / state 1).

    Read it from the **candidate**, never from the held state: with a charge plan
    armed and the car asleep, the two are byte-for-byte identical whether or not
    a cable is in (measured 2026-10-17 - gun 0, connector 7, ports 2/2, state 15
    unplugged), so ``charge_port.charge_cable_state`` deliberately answers
    ``None`` there. The candidate is the frame that just woke the car, and it
    reports the cable honestly. ``None`` means no evidence, which refuses as
    before rather than excusing a rise.

    This does not weaken the wedge protection the guard exists for: a fan-out
    resending a genuinely older snapshot carries a *lower* SOC, which fails the
    comparison below regardless of the cable.
    """
    if parked is not True or charging:
        return False
    if plugged_in:
        return False
    held_soc = soc_of(held)
    candidate_soc = soc_of(candidate)
    if held_soc is None or candidate_soc is None:
        return False
    return candidate_soc > held_soc + tolerance
