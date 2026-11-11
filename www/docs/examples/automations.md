# Automations

## Renew the credentials from Home Assistant (optional)

![The renewal automation](../images/ha-automation.png)


**The renewal app keeps its own timer**, so this automation is not required to
keep an install alive. What it adds is Home Assistant-side visibility: a helper
that shows when a renewal last ran, and a trigger you can see and edit in the UI.

If you add it, set **`renew_interval: 0`** on the app first, so that the app and
the automation are not both renewing.

`configuration.yaml`:

```yaml
rest_command:
  xpeng_renew:
    url: http://<your-ha-host>:8090/renew
    method: POST
    headers:
      Authorization: !secret xpeng_renew_auth     # "Bearer <renew_token>"
    timeout: 300

input_datetime:
  xpeng_last_renewal:
    name: XPENG last renewal
    has_date: true
    has_time: true
```

Then the automation. Two triggers on purpose: a schedule that stays ahead of the
session cap, and the failure state for the case where it did not.

```yaml
- alias: XPENG renew credentials
  id: xpeng_renew_credentials
  mode: single
  max_exceeded: silent
  triggers:
    # Fires every 10 minutes; the interval condition is what makes it ~100 minutes.
    - trigger: time_pattern
      minutes: "/10"
      id: periodic
    # Or immediately, if the current session is already gone.
    - trigger: state
      entity_id: binary_sensor.xpeng_<vehicle>_credentials_valid
      to: "off"
      for: "00:02:00"
      id: invalid
  conditions: []
  actions:
    # The interval is checked INSIDE the action, not as an automation condition,
    # so that an already-broken session is not made to wait it out.
    - if:
        - condition: or
          conditions:
            - condition: state
              entity_id: binary_sensor.xpeng_<vehicle>_credentials_valid
              state: "off"
              for: "00:02:00"
            - condition: template
              value_template: >-
                {{ states('input_datetime.xpeng_last_renewal')
                   in ['unknown', 'unavailable']
                   or (now().timestamp()
                       - state_attr('input_datetime.xpeng_last_renewal', 'timestamp')) > 6000 }}
      then:
        # Order is the failure policy: a non-2xx answer raises, an action error
        # stops the sequence, and the stamp is skipped — so a failed renewal is
        # retried on the next trigger instead of being suppressed for an interval.
        - action: rest_command.xpeng_renew
        - action: input_datetime.set_datetime
          target:
            entity_id: input_datetime.xpeng_last_renewal
          data:
            timestamp: "{{ now().timestamp() }}"
```

That order is not cosmetic. Verified on a live install: with the app answering
`401` the helper kept its previous value, and with a `200` it was stamped.

A stored trace shows the same run read back — the trigger, and the steps that followed it:

![The renewal automation's trace](../images/ha-automation-trace.png)

::: warning
`condition: trigger` inside an action's `if` raises without a trigger context, so
a manual **Run** silently does nothing. Gate on the entity's real state, as above
— that works both from the trigger and by hand.
:::

## Act on a notification

XPENG's notifications arrive as a bus event and as a sensor. They are **not
interchangeable for capture**:

- **`xpeng_notification`** fires once per new Message Centre item, across all six
  categories. Its `data` carries the flattened record — `category`, `title`,
  `message`, `msg_type`, `msg_id`, `timestamp`, `delay_s`, `source` and `raw`.
  This is the trigger to use when every message must be recorded.
- **`sensor.xpeng_<vehicle>_last_notification`** is easier to see and test in the
  UI, and exposes the same fields as attributes — but its **state is the message
  text**, so a second notification with the same text produces no state change.
  Two identical X-Combo messages therefore look like one firing.

The event trigger below keeps every firing, text included, in the Logbook — that
is also how you prove a notification carried the text you configured:

```yaml
- alias: XPENG notification
  triggers:
    - trigger: event
      event_type: xpeng_notification
  conditions:
    - condition: template
      value_template: "{{ trigger.event.data.category == 'XPMOBILE-VEH' }}"
  actions:
    - action: notify.persistent_notification
      data:
        title: "{{ trigger.event.data.title or 'XPENG notification' }}"
        message: "{{ trigger.event.data.message }}"
    - action: logbook.log
      data:
        name: "XPENG {{ trigger.event.data.category }}"
        message: >-
          {{ trigger.event.data.message }} | msg_id={{ trigger.event.data.msg_id }}
          claimed_at={{ trigger.event.data.timestamp }}
```

Triggering on the sensor's state is easier to build in the UI. Every field the
event carries is also a top-level attribute, so the same category filter works:

```yaml
- alias: XPENG sentry notification
  triggers:
    - trigger: state
      entity_id: sensor.xpeng_<vehicle>_last_notification
      not_from:
        - unknown
        - unavailable
  conditions:
    - condition: template
      value_template: "{{ trigger.to_state.attributes.category == 'XPMOBILE-VEH' }}"
  actions:
    - action: notify.mobile_app_your_phone
      data:
        message: "{{ trigger.to_state.state }}"
```

::: tip
Step-by-step setup, the full field list and the troubleshooting checklist are in
[Capture XPENG notifications](../tutorials/notification-capture).
:::

Only **new** notifications fire: the first poll after a restart records the
existing inbox as a baseline, so a restart does not replay the Message Centre into
your automations. A rare `msg_type=3` push has no `category` (it is not a Message
Centre item), so drop the category condition if you want those too. See
[Notifications](../guide/notifications).

## Announce on the car screen

There is no text channel to the car, but `xpeng.send_navigation` is close
enough: it pushes a destination card to the head unit, nothing is routed until
somebody taps **Navigate**, and dismissing it with **X** is one tap. Use the
destination name as the message.

```yaml
- alias: Garage door announcement on the car screen
  id: garage_door_announcement
  triggers:
    - trigger: state
      entity_id: cover.garage_door
      to: open
      id: opened
    - trigger: state
      entity_id: cover.garage_door
      to: closed
      id: closed
  actions:
    - action: notify.mobile_app_your_phone
      data:
        message: "Garage door {{ 'open' if trigger.id == 'opened' else 'closed' }}"
    - action: xpeng.send_navigation
      continue_on_error: true
      data:
        vehicle_entity_id: device_tracker.xpeng_<vehicle>_location
        name: "Garage Door {{ 'Open' if trigger.id == 'opened' else 'Close' }}"
        label: Home
        latitude: "{{ state_attr('zone.home', 'latitude') }}"
        longitude: "{{ state_attr('zone.home', 'longitude') }}"
```

The coordinates are `zone.home` rather than the door on purpose: the name is
the announcement, and home is a harmless place for the car to route to if the
card is tapped by accident. `continue_on_error: true` keeps the rest of the
action list running when the cloud call fails — an expired session is the
common cause.

The service is in the **Queries and data** group, which every preset leaves on,
so this works even on **Read only**. The head unit's **Send Address to
Vehicle** setting must be enabled, and the model must support address sharing.
See [Services](../reference/services#send-navigation) for every field.

## Tell me when the session dies

`credentials_valid` is the integration's own verdict, and it carries the reason.

```yaml
- alias: XPENG session lost
  triggers:
    - trigger: state
      entity_id: binary_sensor.xpeng_<vehicle>_credentials_valid
      to: "off"
      for: "00:05:00"
  actions:
    - action: notify.persistent_notification
      data:
        title: XPENG credentials invalid
        message: >-
          Reason {{ state_attr('binary_sensor.xpeng_<vehicle>_credentials_valid', 'reason') }},
          {{ state_attr('binary_sensor.xpeng_<vehicle>_credentials_valid', 'consecutive_failures') }}
          consecutive failures. Check the renewal app's run log.
```

Five minutes, not two: the renewal app polls for invalidity every 300 seconds and
may already be fixing it.

## Charge limit before a long drive

The charge limit is a real control on the car, so this works whether or not the
car is plugged in at the time.

```yaml
- alias: XPENG charge limit 100 for the trip
  triggers:
    - trigger: time
      at: "21:00:00"
  conditions:
    - condition: state
      entity_id: calendar.work
      state: "on"
  actions:
    - action: xpeng.set_charge_limit
      data:
        limit: 100
```

## Actions worth knowing

- `xpeng.force_update` asks the car for a fresh frame. It is a **passive** query,
  so it does not wake a sleeping car — which also means it will not produce data
  from one.
- `xpeng.clear_stuck_commands` clears the server-side pending command queue. Use
  it when the car answers `code:12` ("Vehicle controls in used") after a burst of
  commands.
- `xpeng.capture_snapshot` writes every tracked field to
  `<config>/xpeng_snapshots/`, which is useful across an OTA update.
- `xpeng.execute_scene` and `xpeng.toggle_scene` sit in the **Advanced and raw
  commands** group, which is disabled on a new install — and a *local* X-Combo
  cannot be one-key started from the cloud anyway, so a scene only runs from the
  car or by firing it in the app. Do not build a test on `execute_scene`; see
  [Command permissions](../reference/command-permissions).
- Every actuating service is refused when its capability group is **Read only**
  or **Disabled**. The charge-limit example above needs the **Charging** group on;
  the default Safe Plus preset includes it.

The full list, with every field, is on [Services](../reference/services).
