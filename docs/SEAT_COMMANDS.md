# Seat command encoding — measured

`service_type 30` (CONTROL_SEAT) controlling seat heating, including the rear row.
Everything below was **measured on a car**, not inferred: a G6 (RHD), via HA host
`192.168.1.162` on 2026-10-14,
using `xpeng.send_raw_mqtt` with the car's own state messages as the readback.

This supersedes two things the integration currently encodes, both of which were
artifacts of reading the state too early:

* `mode 3` is **level 3**, not "off". The old note ("mode 3 is accepted and turns the
  heating off, so it is not a level here") was read ~2 s after the send, inside the
  car's own apply/report latency.
* The rear seats' `cmd_type` **is** known now (`3` and `4`), so they can stop being
  sensors-only.

---

## 1. Wire format

| | |
|---|---|
| Publish topic | `Xmart/Send/User/{uid}` — QoS 1 |
| Receive | `Xmart/Msg/User/{uid}`, `Xmart/MsgV2/User/{uid}` |
| `service_type` | `30` |
| `msg_type` | `2` for a command; `1` on the car's push |
| Envelope | `{"target_id": <VIN>, "msg_id": …, "protocol_ver": "2.0", "msg_content": {…}, "service_type": 30, "msg_type": 2}` |
| Code path | `mqtt_client.send_command(vin, 30, {"cmd_type": …, "mode": …, "cmd_value": …})` |

`msg_content` has exactly three fields:

```json
{"cmd_type": 3, "mode": 1, "cmd_value": 1}
```

* `cmd_type` — **which seat**
* `cmd_value` — **which function**: `1` = heating, `2` = ventilation
* `mode` — **level**: `0` = off, `1`/`2`/`3` = bar 1/2/3

## 2. The measured matrix

| Seat | `cmd_type` | Heat (`cmd_value 1`) | Ventilation (`cmd_value 2`) |
|---|---|---|---|
| Driver | `1` | measured, levels 0–3 | measured, levels 0–3 |
| Passenger | `0` | measured, levels 0–3 | measured, levels 0–3 |
| **Left rear** (behind the passenger, RHD) | **`3`** | **measured, levels 0–3** | **measured on a G9**, levels 0–3 — a G6 has no hardware |
| **Right rear** (behind the driver) | **`4`** | **measured, levels 0–3** | `cmd_value 2` — inferred from the left, not separately measured |
| Third row (6-seat models) | ? | fields exist (`trd_row_*_seat_heat`), unmeasured | — |
| Steering wheel | service `31`, separate value space | 0–3 | — |

Seats that responded to nothing, and must not be used:

| `cmd_type` | Result |
|---|---|
| `2` | accepted (`code 200`), and the front-passenger field moved 46 s after the send. Unexplained and unnecessary — the front seats are `0`/`1`. |
| `5` | rejected: `code 13023`, no state content. |

**Left/right is the car's own naming, not the app's.** The app's car-picture screen
labels these two "Seat Behind the Passenger" / "Seat Behind the Driver"
(`air_seat_direction_back_left/right`), and on an RHD car the seat behind the passenger
is the left-hand one. The integration's field names — `left_rear_seat_heat`,
`right_rear_seat_heat` — match the returned `cmd_type` directly.

The head unit names them the same way, independently of the app: its decompiled sources
carry per-side action constants — `AC_LEFT_REAR_SEAT_HEAT_OPEN/CLOSE/UP/DOWN/SET` and the
`AC_RIGHT_REAR_SEAT_HEAT_*` set — alongside `AC_SUPPORT_REAR_SEAT_HEAT` (the car-side
capability flag behind the app's `isSupportSeatBackHeat`), `AC_SEAT_HEAT_LV` and
`AC_SEAT_BLOW_LV`. So `cmd_type 3` = **left** rear is the car's own left, not a label
inherited from the app's driver/passenger wording. Those constants are head-unit action
IDs, not the MQTT `cmd_type` — the numbers above come from the measurements, not from
this list.

## 3. Evidence

The car does not answer immediately, so each row below is a send paired with the
transition that followed it, from `sensor.xpeng_<vin-slug>_left/right_rear_seat_heating`
(fresh data throughout, `last_data_timestamp` advancing):

```
cmd_type=3  mode=1  cmd_value=1   sent 10:47:11  ->  left_rear   0 -> 1  at 10:47:56   (45 s)
cmd_type=3  mode=0  cmd_value=1   sent 10:52:50  ->  left_rear   1 -> 0  at 10:53:40   (50 s)
cmd_type=4  mode=1  cmd_value=1   sent 10:47:59  ->  right_rear  0 -> 1  at 10:48:49   (50 s)
cmd_type=4  mode=0  cmd_value=1   sent 10:50:25  ->  right_rear  1 -> 0  at 10:51:08   (43 s)
cmd_type=5  mode=1  cmd_value=1   sent 10:48:41  ->  code 13023, nothing moved
```

Rear ventilation, reported by a G9 owner on the same day (2026-10-14) after the rear heat
rows above were measured — the payload from §1 with `cmd_value` 2:

```
cmd_type=3  mode=1  cmd_value=2   ->  "ventilation level 1 on left rear"   (G9, in the car)
```

The same payload on the G6 moves nothing: it reports the `*_rear_seat_ventilation` fields
but has no rear ventilation hardware.

Front seat, level 3, with the latency respected:

```
baseline (cmd_type=1)             heat=0  (last changed 10:30:52)
cmd_type=1  mode=3  cmd_value=1   sent 10:56:01  ->  driver_seat_heating = 3 at 10:56:08
cmd_type=1  mode=0  cmd_value=1   sent 10:56:08  ->  driver_seat_heating = 0 at 10:57:04
```

**Ventilation level 3**, measured the same way on 2026-10-14 — this is what moved the
front ventilation selects off two levels. Nothing was believed until the car's own
freshness signal advanced past the read taken before the command:

```
baseline (cmd_type=1)  driver_seat_ventilation=0  last_data_timestamp 11:52:12
cmd_type=1  mode=3  cmd_value=2   sent 11:52:2x  ->  accepted (HTTP 200)
   driver_seat_ventilation = 3  at 11:52:32  (last_data_timestamp advanced 11:52:12 -> 11:52:32)
   the car's own response: code 200 "Erfolgreich",
   content {... "driver_seat_ventilation": 3, "driver_seat_heat": 0, ...}
cmd_type=1  mode=0  cmd_value=2   sent 11:52:3x  ->  driver_seat_ventilation = 0 at 11:52:40
```

`AC_SEAT_BLOW_LV` in the head unit's constants (see §2) is the same shape as
`AC_SEAT_HEAT_LV`, which is consistent with ventilation reaching a third level; the
reading above is what settles it.

Each rear seat has a **matched on/off pair** on the same `cmd_type`, and the seat that
was not addressed did not move — that pairing is what makes this a mapping rather than a
coincidence.

The car's own push can also be read per-command from
`sensor.<vin>_command_feedback`: its `recent` list holds the last few `service_type 30`
messages with a timestamp, a response `code` and a full seat snapshot in `content`.

**Those messages are the only place seat changes are reported**, and until 0.4.32 they
were logged and dropped — only `service_type 19` reached the coordinator. A seat switched
on from XPENG's app therefore kept reading `Off` here, and because re-picking the option an
entity already shows is not a change, the seat could not be switched off from Home
Assistant either. From 0.4.32 the seat block in a `service_type 30` message is merged into
`instant.seat`, the same way `service_type 4`'s acknowledgement is merged into
`instant.charge`.

## 4. Timing — read this before writing any optimistic state

**A seat command takes 7–50 seconds to be applied and reported.** Observed: 43, 45, 46,
50 s on the rear seats while the car was parked, and 7 s on the driver's seat while it was
already awake and pushing.

Consequences for the integration:

1. **Never set the entity's state from the command you just sent.** Write nothing; wait
   for the car's `msg_content.instant.seat.*` field to arrive. `current_option` should
   keep reflecting the last report, which is the `select.py` behaviour today.
2. **The "off, then press, with a 2 s gap" dance in `select.py` rests on a superseded
   model.** It exists because `mode` was believed to be a momentary press that toggles
   ("the same payload sent three times read back 1, 0, 1"). Every one of those readings
   was taken inside the latency window, so they were showing the state from before the
   command. The evidence now is that `mode` **selects** the level: `mode=3` from off gives
   3, `mode=0` gives off. Re-measure before simplifying, but expect the two-step dance to
   be unnecessary — and note it currently costs an extra command per level change.
3. `mode 0` is a working "off" for `cmd_type 1/3/4`. It is **not** for the front
   passenger: `cmd_type 0, mode 0, cmd_value 1` returns
   `code 8: "Nicht unterstützt für dieses Fahrzeug"` on this car. That is a live bug in the
   passenger select's "Off" today.

## 5. What to change

Applied in **0.4.29** (heat level 3 and the two rear heat controls) and **0.4.30** (the
ventilation third level, after the measurement in §3). What follows is the change itself,
kept because it is the record of what these measurements imply.

### `select.py`

Add two entities alongside the existing seat selects, and a third level to both
functions' options:

```python
SEAT_HEAT_OPTIONS = ["Off", "Heat 1", "Heat 2", "Heat 3"]
SEAT_HEAT_COMMANDS = {"Off": (0, 1), "Heat 1": (1, 1), "Heat 2": (2, 1), "Heat 3": (3, 1)}
SEAT_VENT_OPTIONS = ["Off", "Ventilation 1", "Ventilation 2", "Ventilation 3"]
SEAT_VENT_COMMANDS = {"Off": (0, 2), "Ventilation 1": (1, 2),
                      "Ventilation 2": (2, 2), "Ventilation 3": (3, 2)}
```

Ventilation was left at two levels when the heat third level landed, because only the
heat side had been re-measured. The measurement in §3 is what closed it - do not infer
one function's levels from the other's without it.

```python
XPengSeatSelect(
    coordinator, mqtt_client,
    key="left_rear_seat_heat_select",
    name="Left rear seat heating",
    icon="mdi:car-seat-heater",
    cmd_type=3,
    value_path="msg_content.instant.seat.left_rear_seat_heat",
    options=SEAT_HEAT_OPTIONS,
    commands=SEAT_HEAT_COMMANDS,
),
XPengSeatSelect(
    coordinator, mqtt_client,
    key="right_rear_seat_heat_select",
    name="Right rear seat heating",
    icon="mdi:car-seat-heater",
    cmd_type=4,
    value_path="msg_content.instant.seat.right_rear_seat_heat",
    options=SEAT_HEAT_OPTIONS,
    commands=SEAT_HEAT_COMMANDS,
),
```

`_policy_group` is already `GROUP_COMFORT` on `XPengSeatSelect`, so command permissions
come along for free. The matching state sensors already exist in `const.py`
(`left_rear_seat_heat`, `right_rear_seat_heat`, …).

**Rear ventilation is measured now; whether a car has it is the open question.** The G9
result above settles the encoding, so the reason 0.4.29 shipped no rear ventilation
controls no longer applies — the hardware *presence* does, and the integration cannot read
it:

* `/biz/v5/vehicle/remoteCapabilities`, already fetched and exposed as
  `sensor.<vin>_remote_capabilities`, is a list of **software** feature flags. The G6's 32
  entries contain no seat flag of any kind, so it cannot gate this.
* The app's own `isSupportSeatBackWind` exists, but only in its bundled per-model config,
  and the app data mining already found those flags contradicting the hardware
  (`function.REAR_SEAT_HEAT: true` with `REAR_SEAT_HEAT_HARDWARE: false`), so they are not a
  gate to trust either.

So the two entities ship, and the entity registry decides. `XPengSeatSelect` takes an
`enabled_by_default` argument, and the rear ventilation pair passes `False` for it:

```python
XPengSeatSelect(..., key="left_rear_seat_ventilation_select", cmd_type=3,
                value_path="msg_content.instant.seat.left_rear_seat_ventilation",
                options=SEAT_VENT_OPTIONS, commands=SEAT_VENT_COMMANDS,
                enabled_by_default=False)
```

A G9 owner enables two entities once; a G6 owner never sees a control that does nothing,
which was the original objection. An options-flow toggle would also work, but it is
per-entry where the registry flag is per-entity and needs no migration.

Both rear heat controls stay enabled, because both were measured on the G6 these numbers
came from. The right rear ventilation entity is the one piece resting on inference rather
than its own measurement.

### Translations

`strings.json` and `translations/en.json`, in `entity.select`:

```json
"left_rear_seat_heat_select": {"name": "Left rear seat heating"},
"right_rear_seat_heat_select": {"name": "Right rear seat heating"}
```

(Other locales fall back to English until translated.)

### Tests that must flip — they currently assert the old model

| Test | Now |
|---|---|
| `test_level_three_is_not_offered` | **inverted**: `mode 3` is level 3. Assert `SEAT_HEAT_COMMANDS["Heat 3"] == (3, 1)`. |
| `test_the_rear_seats_are_sensors_not_controls` | **inverted**: rear heat selects now exist with `cmd_type` 3 and 4. Keep asserting that no rear *ventilation* select exists. |
| `test_every_level_change_turns_the_seat_off_first` | dead code — it is defined after the `if __name__ == "__main__":` block, so it never runs. Either fix its position or delete it along with the two-step logic. |
| `test_no_offered_option_uses_a_refused_value` | still valid for `cmd_value` (only 1 and 2 are accepted). |

### Documentation

* `README.md` seat row: the rear seats are no longer "sensors only".
* `tools/README.md` + `tools/probe_seat.py`: `REAR_CMD_TYPE_CANDIDATES` should record
  `3 = left rear, 4 = right rear` as measured, keeping `--scan-cmd-types` for other cars.

## 6. Re-verifying on another car

```bash
python3 tools/probe_seat.py --seat rear-left --scan-cmd-types   # candidates 2,3,4,5
python3 tools/probe_seat.py --seat rear-left --cmd-type 3 --sequence
```

Two guards are built into the tool and both matter:

* **Freshness gate.** It wakes the car and retries until the car's own
  `last_data_timestamp` advances past the previous read, and treats `unavailable` as no
  data. A reading taken inside the 7–50 s window proves nothing, and the tool now says so
  instead of printing a confident `moved: nothing`.
* **Session preflight.** It refuses to run when `sensor.<vin>_polling_status` is
  `Reconnecting`. XPENG keeps **one session per account**, and a host stuck reconnecting
  produces restored zeros that look exactly like seats switching off — one such run on
  `192.168.1.236` produced two phantom readings before the guard existed.

If the car's state fields are ever suspected of lagging reality, the per-command
readback is `sensor.<vin>_command_feedback` → `attributes.recent[-1].content`, which
carries the seat snapshot attached to that specific message.

## 7. Open

* **Third-row heat** (`trd_row_left/middle/right_seat_heat`) — fields exist on 6-seat
  models, `cmd_type` unmeasured.
* **`cmd_type 2`** — accepted, moved the front-passenger field once. Left alone.
* **Which cars have rear ventilation** — the encoding is measured (G9, §3), but no API the
  integration already reads says whether a given VIN has the hardware. Hence the
  disabled-by-default entities in §5 rather than a capability gate.
* **Level latency** — whether a level change from level 2 to level 3 (rather than from
  off) behaves differently has not been measured. With `mode` now reading as a selector it
  should not, but the old "pressing level 2 straight from level 1 read 0" observation is
  unexplained and may have been latency too.

Closed since this list was written: **ventilation level 3** — measured, §3. It was never
on this list, and that omission is the whole cost: heating got its third level in 0.4.29
while ventilation stayed at two, purely because the first measurement recorded heating's
range explicitly and ventilation's as bare "measured". Anything a measurement leaves
unstated belongs here, even when it looks like it follows from its neighbour.

## P7+ FJ exception verified on 24 October 2026

The legacy passenger position 0 returned code 8 and left both passenger functions
unchanged on the tested P7+ FJ. Position 2 selected heating and ventilation levels
1, 2, 3 and Off; each level matched the official Android 3.16.0 seat graphic and
reported passenger field, with Bluetooth disabled. Native passenger controls use
2 only for FJ; other model positions remain unchanged. Seat and steering selects
now await a correlated reply and surface refusals/timeouts, including stopping
the off-first sequence if its preparatory request fails. The conservative
off-first ordering and delay remain unchanged. See [the audit](ANDROID_COMMAND_AUDIT.md).
