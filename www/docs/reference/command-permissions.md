# Command permissions

The XPENG account this integration holds can unlock a car, so every install carries a
ceiling on what it is allowed to *do* - independent of who is asking. Every command
belongs to one **capability group**, and every group is set to **Full**, **Read only**
or **Disabled**.

Several global `xpeng.*` services, such as `xpeng.unlock`, resolve the first
configured vehicle and do not take an entity target. Native entity services
(`cover.close_cover`, climate and switch actions) use `target.entity_id` and Home
Assistant's entity permissions, in addition to the integration policy. The four
HVAC schedule services take `vehicle_entity_id`; it is required for multiple
vehicles and checked against the caller's permissions and the selected entry's
policy. The policy also applies to automations and callers without a user.

## The three states

| State | What it means |
| --- | --- |
| **Full** | The command runs. |
| **Read only** | The entity is created and keeps reporting state, and every command it can send is refused. For climate and the lock this renders properly: the controls are withdrawn rather than left to fail. A `button` has no state to keep, so for buttons Read only behaves as Disabled. |
| **Disabled** | The entities are not created, and the group's services are refused. |

## Presets

**Configure -> Command permissions** offers four presets, and *Custom* on the same
screen opens the per-group matrix.

| Preset | Groups left on | What it is for |
| --- | --- | --- |
| **Read only** | queries | Watching the car. Nothing actuates it. |
| **Safe** | + comfort, lights, securing | Comfort and locking, but the car cannot be opened or charged remotely. |
| **Safe Plus** *(default for a new entry)* | + charging, power | Watching, pre-conditioning and charging - the common case - with unlocking, the trunk and windows, OTA and raw commands left off. |
| **Full access** | everything | What the integration did before this option existed, including unlocking. |

An entry with **no stored policy at all resolves to Full access**, so an install that
predates the option keeps behaving exactly as it did; **Safe Plus** is only what a
*new* entry starts at. See [Upgrading](#upgrading).

## Capability groups

| Capability group | Services | Entities |
| --- | --- | --- |
| **Queries and data** | `capture_snapshot`, `charging_hint`, `check_ota`, `clear_stuck_commands`, `force_calendar_sync`, `force_update`, `list_hvac_schedules`, `send_navigation` | `button.*_wake_vehicle`, `button.*_force_update`, `button.*_query_x_guard` |
| **Comfort and climate** | `climate_off`, `climate_on`, `create_hvac_schedule`, `defrost_off`, `defrost_on`, `delete_hvac_schedule`, `update_hvac_schedule` | Climate (including front defrost); capability-gated climate-mode switches including Pet mode and Sterilisation; seat heating/ventilation and steering-wheel selects; HVAC schedule switches. Raw fan telemetry remains; fan control is withheld. Legacy front-defrost switch is optional. |
| **Lights and alerts** | `flash_lights`, `honk_horn` | `button.*_flash_lights`, `button.*_honk_horn`, `switch.*_parking_lights` |
| **Charging** | `close_charge_port`, `open_charge_port`, `set_charge_limit`, `start_charge`, `stop_charge` | Charging switch, charge-limit number and charge-port cover. Motorized actions require model support and the owner option. Paired charge/port buttons are legacy-only. |
| **Securing actions** | `close_trunk`, `close_windows`, `lock`, `sentinel_on` | Door lock (locking), trunk/window covers (closing), X-Guard (arming), sentinel-sensitivity number; corresponding legacy buttons when enabled. Window close uses service 12 and waits for acknowledgement. |
| **Access actions** | `open_trunk`, `open_windows`, `sentinel_off`, `unlock`, `vent_windows` | Door lock (unlocking), trunk/window covers (opening or ventilation), X-Guard (disarming); corresponding legacy buttons when enabled. Window opening means ventilation, not full opening. |
| **Accessory power** | `trunk_power_off`, `trunk_power_on` | `switch.*_trunk_power`, `number.*_trunk_power_timer` |
| **Firmware and OTA** | `auto_upgrade_off`, `auto_upgrade_on`, `cancel_ota`, `ota_feedback`, `trigger_ota` | `switch.*_ota_auto_upgrade` |
| **Advanced and raw commands** | `execute_scene`, `send_raw_mqtt`, `toggle_scene` | none — services only |

Groups are split by **what an action does to the car, not by subsystem**, which is why
the door lock, the windows, the trunk and X-Guard each appear twice. Locking, closing
and arming only ever *reduce* access; unlocking, opening and disarming hand the car
over. A "Safe" install can lock itself while a leaked token still cannot open the
doors.

`send_raw_mqtt` and the X-Combo scenes are in **Advanced and raw commands**, because a
raw command can express every other group - `unlock` included - and a scene is an
opaque server-side program. Leaving that group at Full access makes every other setting
advisory.

## Setting it

* **During setup** - the last screen before the config entry is created carries
  **Command permissions** and **Allow the integration to wake the car**.
* **Later** - _Settings -> Devices & services -> XPENG -> **Configure**_. The command
  permissions are the first two fields, pre-filled with what is currently set, so
  widening or narrowing it is one dropdown and a submit. The rest of the form is
  unchanged and already filled in.
* **When the renewal app creates the entry** - the app's own
  [`command_mode`](./app-options#xpeng-credential-renewal) option is what it passes, so
  an app-driven install lands on Safe Plus rather than silently on Full access.

It is **per config entry**, so a two-car install sets each independently, and it takes
effect when the entry reloads - a few seconds, with no restart.

## When a command is refused

The refusal is loud rather than silent. The call raises with the group, the state and
where to change it:

```
'xpeng.unlock' is blocked by this integration's command permissions: the 'Access
actions' group is set to disabled in this integration's command permissions (mode:
safe). Change it in Settings > Devices & Services > XPENG > Configure > Command
permissions.
```

One warning per command goes to the log, and every refusal also fires an
`xpeng_command_denied` event, so an attempt can be alerted on rather than discovered
later:

```yaml
triggers:
  - trigger: event
    event_type: xpeng_command_denied
actions:
  - action: notify.mobile_app_your_phone
    data:
      message: >-
        Blocked {{ trigger.event.data.what }} ({{ trigger.event.data.group_label }},
        mode {{ trigger.event.data.mode }}): {{ trigger.event.data.reason }}
```

The event carries `what` (the service or entity id), `group`, `group_label`, `mode`,
`permission`, `action`, `reason`, and `user_id` when the caller had one.

## Allow wake

Waking a sleeping car costs a little of its 12V battery, so it is a separate toggle
rather than part of any group. With **Allow the integration to wake the car** off, the
integration is strictly passive: it sends queries only, and the sensors are staler for
it - see [The car is not woken up](../guide/overview#the-car-is-not-woken-up). The
internal watchdog honours it too, degrading to a passive query instead of a
force-upload. Charge and drive polls are unaffected, because the car is already awake.

## What this does not protect against

The reassuring version would be a lie:

* It does **not** stop an administrator. Whoever can open **Configure** can change the
  mode, and whoever can read or write your Home Assistant configuration files can
  change it without you.
* It is **not** a per-user permission. It is the install's ceiling. A logged-in user
  must additionally be allowed to control an XPENG entity belonging to the receiving config entry; that part is Home
  Assistant's own groups. Calls with no user at all cannot be checked that way, which
  is exactly why the ceiling applies to them.
* The **account credentials are the same at every level**. The mode limits what this
  integration will *do*, not what the account *could* do.

What it does buy: an assistant or a dashboard that cannot unlock your car, an
automation that cannot fire a raw command by accident, and a token that stops being a
set of car keys.

## Upgrading

| Situation | Result |
| --- | --- |
| An entry that predates the option, never configured | **Full access** - no capability is taken away from an existing automation |
| A new entry (setup screen, or the renewal app creating one) | **Safe Plus** |
| A stored `command_mode` of `charging` | Resolved as `safe_plus` - the old name for the same bundle |
| A group added in a later version | Full, for that group, until someone changes it |

The preset shown is always derived from the stored group values, so the label and the
behaviour cannot drift apart.

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
