# EVCC

[EVCC](https://evcc.io) can use this integration as its vehicle interface through
EVCC's `homeassistant` template: EVCC reads the state of Home Assistant entities
and can press a button entity when it needs the car awake. That gives EVCC real
state of charge and range for load management, and gives this integration a
wake-up path when the car's telematics have gone to sleep.

## EVCC vehicle

```yaml
vehicles:
  - name: g6p
    type: template
    template: homeassistant
    title: G6P
    icon: car
    capacity: 87.5        # your pack's usable kWh
    phases: 3
    uri: http://homeassistant.local:8123
    token: !secret ha_token      # long-lived access token (read-only is enough)
    soc: sensor.xpeng_<vehicle>_battery
    range: sensor.xpeng_<vehicle>_range

    # optional, read-only
    limitsoc: number.xpeng_<vehicle>_charge_limit
    climater: binary_sensor.xpeng_<vehicle>_climate

    # optional, writeable — press this when the car needs waking
    wakeup: button.xpeng_<vehicle>_wake_vehicle
```

`<vehicle>` is the same suffix the integration uses for its entity ids (the
masked VIN tail, e.g. `123456`). Every entity above exists on a default install:

| EVCC key | Entity | Notes |
|---|---|---|
| `soc` | `sensor.xpeng_<vehicle>_battery` | the pack's state of charge in % |
| `range` | `sensor.xpeng_<vehicle>_range` | the car's own remaining range |
| `limitsoc` | `number.xpeng_<vehicle>_charge_limit` | writes the car's charge limit |
| `climater` | `binary_sensor.xpeng_<vehicle>_climate` | preconditioning on/off |
| `wakeup` | `button.xpeng_<vehicle>_wake_vehicle` | sends the car's wake/force-upload command |

Compact mode retains every interface listed above, including the original climate
binary sensor. evcc's `climater` requires on/off states; the native climate entity
reports HVAC modes and cannot replace it. Existing unique IDs remain unchanged,
so Home Assistant can recover a climate sensor previously removed by compact
cleanup. Check the entity ID if its deleted-entry metadata has expired.

Create the token under **Profile → Security → Long-lived access tokens**. A
read-only token is enough for `soc`/`range`/`climater`; writing `limitsoc` or
pressing `wakeup` needs a token that can call services (the default is fine).

`wakeup` matters more than it looks. A parked car lets its telematics sleep, and
while it sleeps the cloud answers state queries with an empty stub — which is why
a dashboard can show yesterday's SOC until something wakes the car. EVCC presses
the wake button when it needs a reading, so the state you see in EVCC (and in
Home Assistant, once the car has uploaded) is real rather than last week's.

## Faster plug detection: `xpeng.charging_hint`

The integration polls a parked car hourly (or every 60 s while it knows the car
is charging). A charger knows sooner than the cloud does, so when a session
starts, tell the integration to poll like a charging car for a while:

```yaml
automation:
  - alias: XPENG charging hint while the charger is connected
    triggers:
      - trigger: state
        entity_id: binary_sensor.evcc_connected      # EVCC loadpoint, via MQTT
        to: "on"
      - trigger: numeric_state
        entity_id: sensor.evcc_charge_power          # or any positive power
        above: 0.1
    actions:
      - action: xpeng.charging_hint
        data:
          duration_minutes: 120
    mode: restart
```

The hint raises the polling cadence; it deliberately does **not** wake the car
(a wrong hint then costs cloud round-trips, not the 12 V battery). It is safe to
call repeatedly — each call re-arms the window.

If EVCC is not publishing its loadpoint entities into Home Assistant, use the
`wakeup` button above instead: it is the same "the car is needed now" signal, and
EVCC already presses it on its own schedule.

## Notify on charge start

The reliable trigger is the charging state itself, not a push notification:

```yaml
automation:
  - alias: XPENG charging started
    triggers:
      - trigger: state
        entity_id: sensor.xpeng_<vehicle>_charging_state
        to: "Charging"
    conditions:
      # ignore a restarted session replaying a stale state
      - condition: template
        value_template: >-
          {{ now() - states('sensor.xpeng_<vehicle>_last_data_update') | as_datetime
             < timedelta(minutes: 15) }}
    actions:
      - action: notify.mobile_app_your_phone
        data:
          title: Charging started
          message: >-
            {{ states('sensor.xpeng_<vehicle>_charge_power') | float(0) | round(1) }} kW ·
            {{ states('sensor.xpeng_<vehicle>_battery') }} % ·
            {{ states('sensor.xpeng_<vehicle>_range') }} km
    mode: single
```

Add a second automation with `to: "Complete"` for the end of the session. The
`last_data_update` condition uses the timestamp entity directly (it is a
`device_class: timestamp`, so `as_datetime` works on it) — without it a Home
Assistant restart can replay an old "Charging" state and notify you about a
session that ended yesterday.
