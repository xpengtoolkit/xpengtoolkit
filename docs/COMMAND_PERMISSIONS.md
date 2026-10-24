# Command permissions - what the integration may do to the car

Several global `xpeng.*` services, such as `xpeng.unlock`, resolve the first
configured vehicle and do not take an entity target. Native entity services
(`cover.close_cover`, climate and switch actions) use `target.entity_id` and Home
Assistant's entity permissions, in addition to the integration policy. The four
HVAC schedule services take `vehicle_entity_id`; it is required for multiple
vehicles and checked against the caller's permissions and the selected entry's
policy. The policy also applies to automations and callers without a user.

## The three states

| State | What it means |
|---|---|
| **Full** | The command runs. |
| **Read only** | The entity is created and keeps reporting state; every command it can send is refused. For climate this renders properly - the controls are withdrawn rather than left to fail. For a `button` there is no state to keep, so Read only behaves as Disabled. |
| **Disabled** | The entities are not created, and the group's services are refused. |

## Presets

The setup screen asks for one of four presets. **Configure -> Command permissions**
changes it later, and *Custom* there opens the per-group matrix.

| Preset | Groups left on | What it is for |
|---|---|---|
| **Read only** | queries | Watching the car. Nothing actuates it. |
| **Safe** | + comfort, lights, securing | Comfort and locking, but the car cannot be opened or charged remotely. |
| **Safe Plus** *(default for a new entry)* | + charging, power | Watching, pre-conditioning and charging - the common case - with unlocking, the trunk and windows, OTA and raw commands left off. |
| **Full access** | everything | What the integration did before this option existed, including unlocking. |

## Capability groups

| Capability group | Services | Entities you will see |
|---|---|---|
| **Queries and data** | `capture_snapshot`, `charging_hint`, `check_ota`, `clear_stuck_commands`, `force_calendar_sync`, `force_update`, `list_hvac_schedules`, `send_navigation` | Force update, Wake vehicle, Query X-Guard (buttons) |
| **Comfort and climate** | `climate_off`, `climate_on`, `create_hvac_schedule`, `defrost_off`, `defrost_on`, `delete_hvac_schedule`, `update_hvac_schedule` | Climate (including front defrost); capability-gated climate-mode switches including Pet mode and Sterilisation; seat heating/ventilation and steering-wheel selects; HVAC schedule switches. Raw fan telemetry remains; fan control is withheld. Legacy front-defrost switch is optional. |
| **Lights and alerts** | `flash_lights`, `honk_horn` | Flash lights, Honk horn (buttons); Parking lights (switch) |
| **Charging** | `close_charge_port`, `open_charge_port`, `set_charge_limit`, `start_charge`, `stop_charge` | Charging switch, charge-limit number and charge-port cover. Motorized actions require model support and the owner option. Paired charge/port buttons are legacy-only. |
| **Securing actions** | `close_trunk`, `close_windows`, `lock`, `sentinel_on` | Door lock (locking), trunk/window covers (closing), X-Guard (arming), sentinel-sensitivity number; corresponding legacy buttons when enabled. Window close uses service 12 and waits for acknowledgement. |
| **Access actions** | `open_trunk`, `open_windows`, `sentinel_off`, `unlock`, `vent_windows` | Door lock (unlocking), trunk/window covers (opening or ventilation), X-Guard (disarming); corresponding legacy buttons when enabled. Window opening means ventilation, not full opening. |
| **Accessory power** | `trunk_power_off`, `trunk_power_on` | Trunk power - the V2L outlet (switch); Trunk power timer (number) |
| **Firmware and OTA** | `auto_upgrade_off`, `auto_upgrade_on`, `cancel_ota`, `ota_feedback`, `trigger_ota` | OTA auto-upgrade (switch) |
| **Advanced and raw commands** | `execute_scene`, `send_raw_mqtt`, `toggle_scene` | none - these are services only |

Groups are split by **what an action does to the car, not by subsystem**, which is why
the door lock, the windows, the trunk and X-Guard each appear twice. Locking, closing
and arming only ever *reduce* access; unlocking, opening and disarming hand the car
over. A "Safe" install can therefore lock itself while a leaked token still cannot open
the doors.

`send_raw_mqtt` and the X-Combo scenes live in **Advanced and raw commands** because a
raw command can express every other group - including `unlock` - and a scene is an
opaque server-side program. Keeping that group at Full makes every other setting
advisory.

The service column above is generated from `command_policy.SERVICE_GROUPS`, and
`tests/test_command_policy.py` fails if this file and the code disagree.

**Windows keep separate opening and securing permissions.** `open_windows` and
`vent_windows` use service 12, cmd_type 1, cmd_value 2 (ventilation). The close
action uses the official Android handler's service 12, cmd_type 1, cmd_value 1.
A P7+ owner physically confirmed ventilation and closing on 22 October 2026. Full opening is unverified.
The old charge-port payload is never used for a window action.

**Charge-port controls are an owner's answer, not a permission.** A car with a manual
flap cannot act on `open_charge_port` / `close_charge_port`; turning off the
**Motorized charge-port controls** option stops the two buttons being created and
makes both services raise a clear error, while the charge-port state sensor is
unchanged. It is on by default, so nothing changes for a car with a motorized flap.

## Setting it

* **During setup** - the last screen before the entry is created has **Command
  permissions** and **Allow the integration to wake the car**.
* **Later** - **Settings -> Devices & Services -> XPENG -> Configure**. The first screen
  opens with the preset pre-filled, so it is one dropdown; choosing *Custom* opens a
  second screen with one dropdown per capability group.
* **If the renewal app created the entry** - the app's own `command_mode` option
  (`read_only`, `safe`, `safe_plus`, `full`) is what it passes when it creates the
  entry. `charging` is accepted as an older name for `safe_plus`.
* It is **per config entry**, so a two-car install sets each independently, and it
  takes effect when the entry reloads - a few seconds, no restart.

## When a command is refused

The refusal is loud rather than silent: the service call raises with the group, the
state and where to change it, and one warning per command goes to the log. Every
refusal also fires an event, so an attempt can be alerted on:

```yaml
trigger:
  - platform: event
    event_type: xpeng_command_denied
action:
  - service: notify.mobile_app_your_phone
    data:
      message: >-
        Blocked {{ trigger.event.data.what }} ({{ trigger.event.data.group_label }},
        mode {{ trigger.event.data.mode }}): {{ trigger.event.data.reason }}
```

The event carries `what` (service or entity id), `group`, `group_label`, `mode`,
`permission`, `action`, `reason`, and `user_id` when the caller had one.

## Allow wake

Waking a sleeping car costs a little of its 12V battery, so it is a separate toggle
rather than part of any group. With it off the integration is strictly passive: it
sends queries only, and the sensors are staler for it. The watchdog honours it too,
degrading to a passive query instead of a force-upload. Charge and drive polls are
unaffected - the car is already awake.

## What this does *not* protect against

The reassuring version would be a lie:

* It does **not** stop an administrator. Whoever can open **Configure** can change the
  mode, and whoever can read or write your Home Assistant configuration files can
  change it without you.
* It is **not** a per-user permission. It is the install's ceiling. A logged-in user
  must additionally be allowed to control an XPENG entity belonging to the receiving config entry (Home Assistant's
  own groups do that part); calls with no user at all - automations, scripts, the
  renewal app, a token - cannot be checked that way, which is exactly why the ceiling
  applies to them.
* The **account credentials are the same at every level.** The mode limits what this
  integration will *do*, not what the account *could* do.

What it does buy: an assistant or a dashboard that cannot unlock your car, an
automation that cannot fire a raw command by accident, and a token that stops being a
set of car keys.

## Upgrading, and what an existing install keeps

* An entry with **no stored policy resolves to Full access**. That is deliberate: an
  install that predates this option must not silently lose a capability its automations
  rely on.
* **Safe Plus** is the default only for a **new** entry (and for the renewal app
  creating one).
* A stored idea of the mode is never trusted on its own - the group values are
  authoritative, and the preset shown is derived from them, so the two cannot drift.

## Climate modes and acknowledgement limits

Pet mode, Sterilisation and the other climate modes intentionally use the
**Comfort and climate** group, so supported modes are reachable under Safe and
Safe Plus. Choose Read only/Disabled for that group if remote climate operation
should be withheld. Vehicle safety restrictions, including Rear Seat Reminder
for Pet mode, remain enforced by the car.

Confirmed controls require a matching message ID or an unambiguous echoed
`cmd_type` and `cmd_value` in the same subsystem. A generic response for another
command cannot confirm them. Firmware that rewrites the ID and omits that
identity can still update telemetry, but the command times out rather than
claiming success. Acceptance does not by itself prove physical movement.

Window ventilation/closing was checked on a P7+; G6/G9 and other models still
need their own physical validation. Legacy window buttons/services now use the
same acknowledgement checks as the window cover. Charge-limit, charging,
charge-port and legacy defrost controls also use that confirmed path.
