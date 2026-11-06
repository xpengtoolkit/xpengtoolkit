"""Recognise a reading that means "the sensor has not answered".

Two shapes are measured, on two different cars.

**The wake frame** (P7+ live install ".88", 2026-10-17/18): the first state frame
after the car is woken carries ``instant.tpms`` with all four pressures at ``0``
- and, in the same frame, ``instant.hvac.hvac_inner_temp_float`` at ``87.5``.
The next frame, one to two seconds later, carries the real values. Home
Assistant recorded the zeros, so a tyre pressure dropped to 0 kPa and
reinflated two seconds later, at 03:00:25, 06:22:36 and 08:03:39 on 18 Oct and
05:57:15 on 19 Oct (Australia/Sydney). The first three each follow a credential
renewal by 33-61 s - a session rebuild, which sends the wake - and the fourth
follows a stalled fan-out being escalated with the same kind of force-upload.
Every dashboard drawing that history showed a tyre going flat.

**The same placeholders on a G6, without a following frame** (issue #25,
2026-11-06): ``87.5`` °C cabin temperature again, and a single tyre at ~``700``
kPa for one sample while the other three sat at 228-244. Two things differ
there, and both matter. The tyre sentinel is *high*, not zero - 7 bar is not a
car tyre - and it came alone rather than as a set of four. And the cabin
temperature did not correct itself a second later: the wake frame was the last
frame before the car slept, so ``87.5`` became the held state for hours and
reached the climate card and the TeslaMate provider through the same state tree.

So: the readings that mean "no answer" are encoded, not absent, and never
plausibly. A tyre is not at 0 kPa, is not below it, and is not at 7 bar; a cabin
is not at 87.5 °C. Those are dropped in favour of the last real reading, which
is what the README promises - an entity holds its last known value when the car
does not report - and which also keeps the placeholder out of the recorder and
out of the TeslaMate provider.

Two deliberate limits, because a guard that guesses is how real data starts
disappearing:

* A *low* pressure is still a reading. A slow puncture at 140 kPa must be
  published, so only the impossible end of the range is dropped, and the same
  reasoning applies upwards at 450 kPa: above any tyre's rating, below the
  7 bar sentinel, so nothing real is clipped.
* Only 87.5 °C and above is treated as a cabin placeholder. A sun-baked car
  genuinely reaches 60-70 °C, so the bound sits above that, not in the middle of
  the possible range.

Deliberately no Home Assistant import, like replay_guard.py and
watchdog_policy.py: the decision is small and is tested without a car.
"""

from __future__ import annotations

from typing import Any

#: The four pressures in ``instant.tpms``.
TPMS_PRESSURE_KEYS: tuple[str, ...] = (
    "tpms_pressure_fl",
    "tpms_pressure_fr",
    "tpms_pressure_rl",
    "tpms_pressure_rr",
)

#: Above this a tyre pressure is not a reading. Passenger-car tyres run
#: 200-350 kPa and the measured placeholder is ~700, so 450 drops the impossible
#: without clipping anything a pump could legitimately produce.
TPMS_PRESSURE_MAX_KPA: float = 450.0

#: The cabin-temperature placeholder measured on both cars, in °C.
CABIN_TEMP_SENTINEL_C: float = 87.5

#: The field that carries it in ``instant.hvac``.
CABIN_TEMP_KEY: str = "hvac_inner_temp_float"


def _as_number(value: Any) -> float | None:
    """*value* as a float, or ``None`` when it is not recognisably numeric."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def is_unreported_pressure(value: Any) -> bool:
    """Whether *value* is a TPMS placeholder rather than a pressure.

    ``0``, a negative, an empty string and ``None`` all mean "no reading yet",
    as does anything above :data:`TPMS_PRESSURE_MAX_KPA`. A non-numeric string
    that is not empty is left alone: the point is to drop the values that are
    provably impossible, not to second-guess a payload shape never seen here.
    """
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    number = _as_number(value)
    if number is None:
        return False
    return number <= 0 or number > TPMS_PRESSURE_MAX_KPA


def is_unreported_temperature(value: Any) -> bool:
    """Whether *value* is the cabin-temperature placeholder rather than a reading.

    ``87.5`` and above, and ``None``. A hot parked cabin is real and is left
    alone; see this module's docstring for why the bound is where it is.
    """
    if value is None:
        return True
    number = _as_number(value)
    if number is None:
        return False
    return number >= CABIN_TEMP_SENTINEL_C


def _previous_section(previous_content: Any, section: str) -> dict[str, Any]:
    """The named ``instant`` section of the last accepted state, or an empty one."""
    if not isinstance(previous_content, dict):
        return {}
    instant = previous_content.get("instant")
    if not isinstance(instant, dict):
        return {}
    found = instant.get(section)
    return found if isinstance(found, dict) else {}


def _repair_map(
    section: dict[str, Any],
    placeholder,
    previous: dict[str, Any],
) -> tuple[dict[str, Any], bool, bool]:
    """Repair *section* against *previous*, returning ``(copy, changed, kept)``.

    A placeholder is replaced by the value held for the same key, and dropped
    when nothing real was ever held. ``kept`` says whether anything survived,
    which is what lets the caller remove a section that never held a reading
    rather than publishing an empty one.
    """
    repaired: dict[str, Any] = {}
    changed = False
    kept = False
    for key, value in section.items():
        if not placeholder(value):
            repaired[key] = value
            kept = True
            continue
        changed = True
        carried = previous.get(key)
        if not placeholder(carried):
            repaired[key] = carried
            kept = True
    return repaired, changed, kept


def with_previous_unreported(instant: Any, previous_content: Any = None) -> Any:
    """Return *instant* with unreported readings replaced by the last real ones.

    Repairs the four tyre pressures and the cabin temperature. *previous_content*
    is the ``msg_content`` of the last accepted state, which supplies the value
    to carry forward; when the car was never heard from, the placeholder is
    dropped instead so the entity reads unknown rather than 0 kPa / 87.5 °C.

    The same *instant* object comes back when every reading is real, so the
    caller can skip rebuilding the state by identity alone.
    """
    if not isinstance(instant, dict):
        return instant

    result = instant

    tpms = instant.get("tpms")
    if isinstance(tpms, dict):
        repaired, changed, kept = _repair_map(
            tpms, is_unreported_pressure, _previous_section(previous_content, "tpms")
        )
        if changed:
            result = dict(result)
            if kept:
                result["tpms"] = repaired
            else:
                # Nothing has ever been reported for this car: absent, not 0.
                result.pop("tpms", None)

    hvac = instant.get("hvac")
    if isinstance(hvac, dict) and CABIN_TEMP_KEY in hvac:
        repaired, changed, kept = _repair_map(
            {CABIN_TEMP_KEY: hvac[CABIN_TEMP_KEY]},
            is_unreported_temperature,
            _previous_section(previous_content, "hvac"),
        )
        if changed:
            hvac_repaired = dict(hvac)
            if kept:
                hvac_repaired[CABIN_TEMP_KEY] = repaired[CABIN_TEMP_KEY]
            else:
                hvac_repaired.pop(CABIN_TEMP_KEY, None)
            result = dict(result)
            if hvac_repaired:
                result["hvac"] = hvac_repaired
            else:
                # The temperature was the only field this frame carried and
                # nothing real was ever held: absent, not an empty section.
                result.pop("hvac", None)

    return result
