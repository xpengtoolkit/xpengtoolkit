"""Forward XPENG telemetry to A Better Route Planner (ABRP).

ABRP plans a route from its own consumption model and refines that model while a
route is planned, from live telemetry. Its Telemetry API is a single call::

    POST https://api.iternio.com/1/tlm/send?token=<user token>
         {"tlm": {"utc": ..., "soc": ..., "speed": ..., ...}}

Two credentials are involved and they are not the same thing:

* the **user token** identifies one ABRP user and one vehicle. The user gets it
  from the app: your car → *Edit connection* → *Link* under **Generic**. It is
  the optional field on the options screen; blank means "do not send".
* the **API key** identifies the *application* sending the data. It is free
  (contact@iternio.com) and it is not a per-user secret. ``ABRP_API_KEY`` below
  is the community telemetry key, accepted for any user token. Registering an
  Xpengtk-owned key and swapping the constant is the only change needed; the
  key is sent as ``api_key``, which is what the ABRP docs and the community
  bridges (teslamate-abrp, CarConnectivity) use.

What this can and cannot say. XPENG does not expose motor power or elevation to
this integration (see docs/TESLAMATE.md), so a driving car sends **no** ``power``
field at all rather than a fabricated 0 - a 0 would teach ABRP's consumption
calibration that the car uses no energy. Charging power is measured
(``interval.charge.power``) and goes out negative, ABRP's sign for energy going
in. SoC, range, odometer, speed, position, heading, cabin temperature, tyre
pressures and the pack capacity are real readings.

``utc`` is the time the *car's* state arrived, not the time of the HTTP call: a
parked car's last frame can be an hour old, and stamping it with "now" would
tell ABRP the car was there more recently than it was.

Deliberately no Home Assistant import (aiohttp and the shared session are
supplied by the caller), like replay_guard.py and sentinel_guard.py: the payload
mapping, the cadence and the reporter are testable without a car.
"""

from __future__ import annotations

import logging
import math
import re
import time
from typing import Any, Callable, Final, Mapping

_LOGGER = logging.getLogger(__name__)

#: The Telemetry API endpoint. POST carries the payload as JSON; the community
#: bridges use the same shape and ABRP documents ``tlm`` as a JSON object.
ABRP_ENDPOINT: Final = "https://api.iternio.com/1/tlm/send"

#: Application key, not a user secret. ABRP authorises the *app* this way and
#: binds the user token to a vehicle separately. This is the shared telemetry
#: key the community bridges have shipped for years; a key registered for
#: Xpengtk itself replaces it without any other change (the maintainers' own
#: key is free - contact@iternio.com).
ABRP_API_KEY: Final = "d49234a5-2553-454e-8321-3fe30b49aa64"

#: HTTP timeout. ABRP is a third party on the far side of the internet and this
#: runs inside Home Assistant's event loop; an unbounded request would be a leak.
ABRP_SEND_TIMEOUT: Final = 15.0

#: Minimum seconds between two pushes. 10 s is ABRP's own guidance for the
#: consumption calibration it asks for ("at least once per 10 seconds"), and the
#: car pushes a frame every ~5 s while driving, so this halves the requests
#: without losing a data point it needs. Parked is 60 s: nothing changes while
#: parked, and the car only reports about once an hour anyway, so the bound is
#: an upper limit rather than a keepalive timer.
MOVING_INTERVAL_S: Final = 10.0
PARKED_INTERVAL_S: Final = 60.0

#: Same evidence ``binary_sensor.charging`` uses: state code 2, or charger
#: power above this floor. XPENG reports charger power in kW.
CHARGING_POWER_FLOOR_KW: Final = 0.2

#: A charge session above this power is DC, below it AC. A heuristic, not a
#: car measurement - the same one docs/TESLAMATE.md records (``is_dcfc``).
DC_POWER_THRESHOLD_KW: Final = 20.0

#: ``instant.charge.charging_state`` value for "charging".
CHARGING_STATE_CHARGING: Final = 2

#: ABRP tokens are UUID-shaped. Accept a little more than a UUID (any 16-64
#: characters of letters, digits and dashes) so a future token format is not
#: refused by this check, but reject a pasted sentence or a mangled copy.
_TOKEN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z-]{15,63}$")


def valid_token(token: Any) -> bool:
    """Whether *token* looks like an ABRP user token.

    Format only: this cannot prove the token exists, and asking ABRP would make
    the options screen depend on a third party being up.
    """
    return bool(_TOKEN.match(str(token or "").strip()))


def _dig(container: Any, *keys: str) -> Any:
    node = container
    for key in keys:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def _number(value: Any) -> float | None:
    """A finite float, or None. Booleans are not numbers, and NaN/inf are not
    telemetry: either would travel through json as ``null``/a rejected body."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _rounded(key: str, value: float | None, *, digits: int = 1) -> dict[str, Any]:
    return {} if value is None else {key: round(value, digits)}


def telemetry(
    state: Any,
    *,
    sampled_at: float,
    capacity_kwh: float | None = None,
    parked: bool | None = None,
) -> dict[str, Any]:
    """The ``tlm`` object for one coordinator state, or ``{}`` when there is none.

    ``sampled_at`` is epoch seconds for the state's arrival (the coordinator's
    ``last_data_timestamp``). ``parked`` is the coordinator's own verdict, passed
    in rather than re-derived here, so ABRP and the dashboard cannot disagree
    about whether the car is parked.
    """
    content = state.get("msg_content") if isinstance(state, Mapping) else None
    if not isinstance(content, Mapping):
        return {}

    instant = content.get("instant") if isinstance(content.get("instant"), Mapping) else {}
    interval = content.get("interval") if isinstance(content.get("interval"), Mapping) else {}

    payload: dict[str, Any] = {"utc": int(sampled_at)}

    soc = _number(_dig(instant, "charge", "battery_soc"))
    if soc is not None and 0 <= soc <= 100:
        payload.update(_rounded("soc", soc))

    range_km = _number(_dig(instant, "odometer", "avalible_driving_distance"))
    if range_km is not None and range_km >= 0:
        payload.update(_rounded("est_battery_range", range_km))

    odometer = _number(_dig(instant, "odometer", "total_mileage"))
    if odometer is not None and odometer >= 0:
        payload.update(_rounded("odometer", odometer))

    speed = _number(_dig(interval, "drive", "speed"))
    if speed is not None and speed >= 0:
        payload.update(_rounded("speed", speed))

    heading = _number(_dig(interval, "drive", "angle"))
    if heading is not None and 0 <= heading < 360:
        payload.update(_rounded("heading", heading))

    latitude = _number(_dig(interval, "drive", "latitude"))
    longitude = _number(_dig(interval, "drive", "longitude"))
    if (
        latitude is not None
        and longitude is not None
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
        and not (latitude == 0 and longitude == 0)
    ):
        payload["lat"] = latitude
        payload["lon"] = longitude

    power = _number(_dig(interval, "charge", "power"))
    charge_code = _number(_dig(instant, "charge", "charging_state"))
    charging = charge_code == CHARGING_STATE_CHARGING or (
        power is not None and power > CHARGING_POWER_FLOOR_KW
    )
    if charge_code is not None or power is not None:
        payload["is_charging"] = 1 if charging else 0
    if charging:
        if power is not None and power > CHARGING_POWER_FLOOR_KW:
            # ABRP's sign convention: output positive, input negative. XPENG
            # reports the charger's input as a positive number.
            payload["power"] = -round(power, 2)
            payload["is_dcfc"] = 1 if power >= DC_POWER_THRESHOLD_KW else 0
        current = _number(_dig(interval, "charge", "electricity"))
        if current is not None and current > 0:
            payload["current"] = -round(current, 2)
        voltage = _number(_dig(interval, "charge", "voltage"))
        if voltage is not None and voltage > 0:
            payload.update(_rounded("voltage", voltage))

    if parked is not None:
        payload["is_parked"] = 1 if parked else 0

    cabin = _number(_dig(instant, "hvac", "hvac_inner_temp_float"))
    if cabin is not None and -40 <= cabin <= 60:
        payload.update(_rounded("cabin_temp", cabin))
    setpoint = _number(_dig(instant, "hvac", "hvac_temp_float"))
    if setpoint is not None and -40 <= setpoint <= 60:
        payload.update(_rounded("hvac_setpoint", setpoint))

    for source, field in (
        ("tpms_pressure_fl", "tire_pressure_fl"),
        ("tpms_pressure_fr", "tire_pressure_fr"),
        ("tpms_pressure_rl", "tire_pressure_rl"),
        ("tpms_pressure_rr", "tire_pressure_rr"),
    ):
        pressure = _number(_dig(instant, "tpms", source))
        if pressure is not None and pressure > 0:
            payload.update(_rounded(field, pressure))

    if capacity_kwh is not None:
        capacity = _number(capacity_kwh)
        if capacity is not None and capacity > 0:
            payload.update(_rounded("capacity", capacity))
            # ABRP documents soe as SoC * capacity, and both terms are already
            # in hand; sending it saves ABRP having to infer a pack from SoC.
            if "soc" in payload:
                payload["soe"] = round(payload["soc"] * capacity / 100.0, 1)

    # Only a timestamp is not a data point: do not send "the car still exists,
    # at an unknown place" every minute.
    return payload if len(payload) > 1 else {}


def interval_for(payload: Mapping[str, Any]) -> float:
    """How long to wait before the next push, from what this one said."""
    return PARKED_INTERVAL_S if payload.get("is_parked") == 1 else MOVING_INTERVAL_S


class AbrpReporter:
    """Push each accepted vehicle state to ABRP, respecting the cadence.

    Listens to the coordinator instead of being called by the MQTT client: every
    accepted frame, and the REST-fallback merge, arrives through
    ``async_set_updated_data``, so one subscription sees both. The listener
    fires on every update; ``_next_push`` turns that into the 10 s / 60 s
    cadence.

    Restored-from-disk state is skipped until the car reports a frame of its
    own. A REST merge updates the values but carries no new arrival time -- the
    ``utc`` would be the last push's -- so ABRP would be told fresh values are
    older than they are, or old values are current. After a restart the first
    push is therefore the first frame the car sends, not the cached copy.

    Failures are logged once per run of failures and never raised: ABRP being
    down must not affect the car, the entities or Home Assistant's log volume.
    """

    def __init__(
        self,
        hass: Any,
        coordinator: Any,
        token: str,
        *,
        session: Any,
        api_key: str = ABRP_API_KEY,
        clock: Callable[[], float] | None = None,
        timeout: float = ABRP_SEND_TIMEOUT,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self.token = token
        self._api_key = api_key
        self._session = session
        self._clock = clock or time.monotonic
        self._timeout = timeout
        self._unsub: Callable[[], None] | None = None
        self._next_push = 0.0
        self._consecutive_failures = 0
        # Diagnostics, for a log line and a test; no entity reads these.
        self.last_success: float | None = None
        self.last_error: str | None = None
        self.sent = 0

    @property
    def started(self) -> bool:
        return self._unsub is not None

    def start(self) -> None:
        """Subscribe, and try the state already held (a reload has one)."""
        if self._unsub is not None:
            return
        self._unsub = self._coordinator.async_add_listener(self.notify)
        self.notify()

    def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def notify(self) -> None:
        """A coordinator update: push it now if the cadence allows."""
        try:
            self._notify()
        except Exception:  # noqa: BLE001
            # This runs inside the coordinator's update path, which every
            # entity depends on. A bug here must never raise into it: the car's
            # data matters, an ABRP push does not.
            _LOGGER.debug("ABRP push skipped after an unexpected error", exc_info=True)

    def _notify(self) -> None:
        state = getattr(self._coordinator, "data", None)
        # Restored from disk: no honest arrival time exists yet, so pushing it
        # would stamp stale values as current. Wait for the car's own frame.
        if not state or getattr(self._coordinator, "state_is_restored", False):
            return
        now = self._clock()
        if now < self._next_push:
            return
        sampled = getattr(self._coordinator, "last_data_timestamp", None)
        sampled_at = sampled.timestamp() if sampled is not None else now
        payload = telemetry(
            state,
            sampled_at=sampled_at,
            capacity_kwh=getattr(self._coordinator, "usable_battery_kwh", None),
            parked=getattr(self._coordinator, "vehicle_parked", None),
        )
        if not payload:
            return
        self._next_push = now + interval_for(payload)
        self._hass.async_create_task(self._async_send(payload))

    def _redact(self, text: Any) -> str:
        """Never log either credential, whatever an error or a body contains."""
        cleaned = str(text)
        for secret in (self.token, self._api_key):
            if secret:
                cleaned = cleaned.replace(secret, "***")
        return cleaned

    def _failed(self, message: str) -> None:
        self.last_error = message
        if self._consecutive_failures == 0:
            _LOGGER.warning("ABRP telemetry push failed: %s", message)
        else:
            _LOGGER.debug("ABRP telemetry push failed again: %s", message)
        self._consecutive_failures += 1

    async def _async_send(self, payload: Mapping[str, Any]) -> bool:
        """One POST. Returns whether ABRP accepted it."""
        try:
            async with self._session.post(
                ABRP_ENDPOINT,
                params={"token": self.token, "api_key": self._api_key},
                json={"tlm": dict(payload)},
                timeout=self._timeout,
            ) as response:
                status = response.status
                if status != 200:
                    body = await response.text()
                    self._failed(f"HTTP {status}: {self._redact(body)[:200]}")
                    return False
                data = await response.json()
        except Exception as err:  # noqa: BLE001 - a third party must not raise out
            self._failed(self._redact(f"{type(err).__name__}: {err}")[:300])
            return False

        if not isinstance(data, dict) or data.get("status") != "ok":
            self._failed(self._redact(f"ABRP rejected the update: {data!r}")[:300])
            return False

        self._consecutive_failures = 0
        self.last_error = None
        self.last_success = time.time()
        self.sent += 1
        _LOGGER.debug(
            "ABRP telemetry sent (soc=%s, charging=%s, parked=%s)%s",
            payload.get("soc"),
            payload.get("is_charging"),
            payload.get("is_parked"),
            f", ABRP wants {data['missing']}" if data.get("missing") else "",
        )
        return True


def start_abrp_reporter(hass: Any, coordinator: Any, token: Any) -> AbrpReporter | None:
    """Create and start the reporter, or ``None`` when no token is configured.

    The shared Home Assistant aiohttp session is used (and owned by Home
    Assistant), so nothing here has to close a connection pool on unload.
    """
    cleaned = str(token or "").strip()
    if not cleaned:
        return None
    if not valid_token(cleaned):
        _LOGGER.warning(
            "Ignoring the ABRP token in the options: it does not look like an "
            "ABRP user token (%d characters). Open the integration options and "
            "paste it again.",
            len(cleaned),
        )
        return None
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    reporter = AbrpReporter(
        hass, coordinator, cleaned, session=async_get_clientsession(hass)
    )
    reporter.start()
    return reporter
