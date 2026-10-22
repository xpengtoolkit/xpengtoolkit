# Services

The integration registers **43 services** under the `xpeng` domain: 42 vehicle
commands and [`xpeng.import_credentials`](#credential-import). The vehicle
commands are fire-and-forget MQTT or cloud calls — they return as soon as the
command has been sent, not when the car has acted, so a state change appears on
the next poll and the writable entities read optimistically for about 30 s (see
[Entities](./entities)).

None of the vehicle commands take a `target` or `entity_id`. Each resolves the
integration's **first config entry**, which is the single configured vehicle on a
one-car install; on a multi-vehicle install all of them act on that one entry.
`xpeng.import_credentials` is the exception: it accepts an `entry_id`.

::: info
The bounds in the field tables are the **selector** bounds — what the service form
in the UI enforces. Only `sentinel_on.sensitivity` and `set_charge_limit.limit` are
also range-validated by the service schema, so a YAML call can pass a value
outside the other bounds and the integration will not stop it.
:::

Every service is registered when the first config entry is set up, and every one
is removed again when the last entry is unloaded.

## Calling services

From an automation or script, use an action:

```yaml
- alias: Cabin pre-condition before the school run
  triggers:
    - trigger: time
      at: "07:20:00"
  conditions:
    - condition: state
      entity_id: binary_sensor.xpeng_<vehicle>_charging
      state: "off"
  actions:
    - action: xpeng.climate_on
      data:
        temperature: 21.5
        duration: 20
```

From a shell or an external tool, call the REST API with a long-lived token:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"temperature": 21.5, "duration": 20}' \
  http://your-ha-host:8123/api/services/xpeng/climate_on
```

`rest_command` works the same way and is how the renewal app is driven from an
automation; see [Credential import](#credential-import) and
[Unattended renewal](../guide/unattended-renewal).

Every service below belongs to a **capability group**, and is refused unless that group
is at Full access - a ceiling that applies to automations, scripts, voice assistants and
tokens alike. `xpeng.import_credentials` is the one exemption. See
[Command permissions](../reference/command-permissions) for the groups and for what a
refusal looks like.

## Climate and comfort

### climate_on

Turns the cabin climate control on and schedules it to stop, so it cannot be left
running indefinitely.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `temperature` | no | `22.0` | Target temperature in °C; `16`–`32`, step `0.5` |
| `duration` | no | `15` | Run time in minutes; `1`–`60` |

```yaml
service: xpeng.climate_on
data:
  temperature: 21.5
  duration: 20
```

### climate_off

Turns climate control off. No fields.

```yaml
service: xpeng.climate_off
```

### defrost_on

Turns on front-windscreen defrost. No fields.

### defrost_off

Turns off front-windscreen defrost. No fields.

```yaml
service: xpeng.defrost_on
```

## Windows, doors and body

| Service | Effect |
| --- | --- |
| `close_windows` | Official app close command (service 12, cmd_type 1, value 1). Physically confirmed on a P7+ (22 October 2026) |
| `vent_windows` | Vents all windows. This is the only window command verified to work, and the name is the accurate one |
| `open_windows` | Also sends the verified command above, which **vents** — full opening was not observed |
| `open_trunk` | Opens the vehicle trunk |
| `close_trunk` | Closes the vehicle trunk |
| `lock` | Locks the doors |
| `unlock` | Unlocks the doors |

None of these takes a field. They are the same commands the corresponding
`button` and `lock` entities issue; `lock` is worth preferring for the door lock,
because it reports the state back.

Window opening/ventilation uses service 12, cmd_type 1, value 2. Closing uses
value 1 from the official Android handler. The old charge-port payloads are not
window commands. A P7+ owner physically confirmed ventilation and closing on 22 October 2026;
full opening has not been verified.

```yaml
service: xpeng.vent_windows
```

```yaml
service: xpeng.open_trunk
```

## Charging

### start_charge

Starts a charge on whichever connector is plugged in. No fields.

### stop_charge

Stops an in-progress charge. No fields.

```yaml
service: xpeng.start_charge
```

### set_charge_limit

Sets the battery charge limit — the percentage at which the car stops charging.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `limit` | **yes** | – | Target charge percentage; `50`–`100`, step `5` |

```yaml
service: xpeng.set_charge_limit
data:
  limit: 80
```

### open_charge_port / close_charge_port

Open and close the charge-port flap. No fields. Export cars have one combined
CCS2 port, so this is the only port command: the car refuses the fast-port
variant (`cmd_type 4`) with `10404` / `10405`, and the integration no longer
offers it.

Both services raise a clear error when **Motorized charge-port controls** is
turned off in the options — the setting for a car with a manual flap, which cannot
act on these commands. The charge-port state sensor keeps reporting either way.

```yaml
service: xpeng.open_charge_port
```

### trunk_power_on

Enables the trunk power outlet (V2L) with an auto-off timer.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `duration` | no | `60` | Auto-off timer in minutes; `1`–`1440` |

```yaml
service: xpeng.trunk_power_on
data:
  duration: 120
```

### trunk_power_off

Disables the trunk power outlet. No fields.

The auto-off timer is what stops the outlet if nothing else does, so set it to
something short unless the load needs longer.

## Security and sentinel

### sentinel_on

Enables sentinel (sentry) mode.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `sensitivity` | no | `2` | Detection sensitivity level; `0`–`3` |

```yaml
service: xpeng.sentinel_on
data:
  sensitivity: 2
```

### sentinel_off

Disables sentinel mode. No fields.

Sentinel mode is the vehicle's own parked-surveillance feature, and
`binary_sensor.xpeng_<vehicle>_sentinel_mode` reports whether it is armed — see
[Entities](./entities).

## Lights and horn

### flash_lights

Flashes the vehicle lights. No fields.

### honk_horn

Sounds the vehicle horn. No fields.

```yaml
service: xpeng.flash_lights
```

These are the two commands worth putting behind a confirmation or a presence
condition — both are audible and visible from outside the car.

## X-Combo scenes

Both services act on the account's X-Combo smart scenes, which live in the cloud
rather than on the vehicle. The scene ids come from the X-Combo entities and
attributes (see [Entities](./entities)).

### execute_scene

Executes (one-key start) a scene.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `scene_id` | **yes** | – | The X-Combo scene id, as a string |

```yaml
service: xpeng.execute_scene
data:
  scene_id: "12345"
```

### toggle_scene

Activates or deactivates a scene.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `scene_id` | **yes** | – | The X-Combo scene id, as a string |
| `active` | **yes** | – | `true` to activate, `false` to deactivate |

```yaml
service: xpeng.toggle_scene
data:
  scene_id: "12345"
  active: true
```

Both need working app-tier credentials; with file-based tokens and no API access
they log an error and do nothing.

## OTA and software

### check_ota

Asks the cloud for available OTA updates and refreshes the OTA sensor data. No
fields. Useful after a release announcement, rather than waiting for the next
scheduled check.

### trigger_ota

Starts an OTA upgrade on the vehicle.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `upgrade_id` | **yes** | – | Upgrade id from the OTA upgrade sensor's attributes; integer `≥ 1` |

```yaml
service: xpeng.trigger_ota
data:
  upgrade_id: 12345
```

::: warning
This installs software on the car. Read the upgrade id from the OTA sensor
attributes, make sure the car is parked and has charge, and do not run it in a
loop.
:::

### cancel_ota

Cancels a scheduled OTA upgrade.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `upgrade_id` | **yes** | – | Upgrade id of the scheduled update; integer `≥ 1` |

### auto_upgrade_on

Enables automatic OTA upgrades. No fields.

### auto_upgrade_off

Disables automatic OTA upgrades. No fields.

### ota_feedback

Sends OTA upgrade status feedback to the server — this is what the head unit
normally reports.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `upgrade_id` | **yes** | – | Upgrade id to report on; integer `≥ 1` |
| `status` | **yes** | `0` (as documented) | Status code; `0` is success, integer `≥ 0` |

The field declares both `required: true` and a default of `0`, which is
contradictory: the code schema requires `status`, so supply it explicitly.

```yaml
service: xpeng.ota_feedback
data:
  upgrade_id: 12345
  status: 0
```

## Navigation and calendar

### send_navigation

Sends a destination card to the vehicle's head unit — the cloud equivalent of the
official app's "send POI to car" feature.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `name` | **yes** | – | Destination name shown on the vehicle screen |
| `latitude` | **yes** | – | Destination latitude, WGS84; `-90`–`90` |
| `longitude` | **yes** | – | Destination longitude, WGS84; `-180`–`180` |
| `label` | no | – | Optional category label for the card |
| `poi_id` | no | – | Optional POI id from a POI search |
| `msg_type` | no | – | Optional message type sent to the server; omit to leave it out |

```yaml
service: xpeng.send_navigation
data:
  name: Home
  latitude: 51.5074
  longitude: -0.1278
  label: Home
```

### force_calendar_sync

Triggers the vehicle's phone-calendar schedule sync — the same action as the
sync button on the head unit. It re-asserts the calendar sync authorisation and
refreshes the schedule metadata from the XPENG assistant service. No fields.

### create_hvac_schedule

Creates a scheduled HVAC (climate) appointment on the vehicle. An empty
`days_of_week` means a one-shot appointment at the next occurrence.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `start_time` | **yes** | – | Local start time, `HH:MM` (24 h) |
| `days_of_week` | no | `[]` | Weekday numbers `1`=Mon…`7`=Sun for a recurring schedule; empty = one-shot |
| `enabled` | no | `true` | Whether the schedule starts active |
| `action_type` | no | `AIR_CONDITION_OPEN` | Backend `actionType` string (advanced) |
| `time_zone` | no | – | Optional IANA time zone, e.g. `Europe/London` |

```yaml
service: xpeng.create_hvac_schedule
data:
  start_time: "07:30"
  days_of_week: [1, 2, 3, 4, 5]
  enabled: true
```

### update_hvac_schedule

Updates an existing appointment by id. Same fields as `create_hvac_schedule`,
plus the id of the appointment to change.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `appointment_id` | **yes** | – | `appointmentId` returned by create/list |
| `start_time` | **yes** | – | Local start time, `HH:MM` (24 h) |
| `days_of_week` | no | `[]` | Weekday numbers `1`–`7`; empty = one-shot |
| `enabled` | no | `true` | Whether the schedule is active |
| `action_type` | no | `AIR_CONDITION_OPEN` | Backend `actionType` string (advanced) |
| `time_zone` | no | – | Optional IANA time zone |

```yaml
service: xpeng.update_hvac_schedule
data:
  appointment_id: "<appointment-id>"
  start_time: "08:00"
  days_of_week: [1, 2, 3, 4, 5]
```

### delete_hvac_schedule

Deletes an appointment by id.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `appointment_id` | **yes** | – | `appointmentId` returned by create/list |
| `time_zone` | no | – | Optional IANA time zone |

```yaml
service: xpeng.delete_hvac_schedule
data:
  appointment_id: "<appointment-id>"
```

### list_hvac_schedules

Fetches the current list of HVAC appointments and **logs it** — the response is
written to the Home Assistant log (search for `HVAC schedules`) rather than
returned, so it is not a service to call from an automation that needs the data.
No fields. The `calendar` entity and the per-appointment switches are the entity
view of the same list.

## Diagnostics and maintenance

### force_update

Forces a vehicle state update query. No fields.

It is a **passive** query (`msg_type: 4`), not a force-upload: a sleeping car
answers it with a stub without waking. The commands that do wake a car are the
active-state force-uploads and the watchdog escalation, not this service, so it is
safe to call when a value looks stale.

```yaml
service: xpeng.force_update
```

### capture_snapshot

Writes a JSON snapshot of every coordinator-tracked field to
`<config>/xpeng_snapshots/<vin>_<timestamp>.json`.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `reason` | no | `manual` | Free-form label stored in the snapshot file, e.g. `pre_xos_update` |

```yaml
service: xpeng.capture_snapshot
data:
  reason: pre_xos_update
```

A snapshot is also captured automatically when an XOS version change is detected,
so each OTA produces a before/after pair to diff. The files contain the VIN and
the decoded VIN descriptor.

### clear_stuck_commands

Clears the server-side pending vehicle command queue for your own account — the
fix for `code:12 "Vehicle controls in used"`, which is what a rapid burst of
commands leaves behind. It calls REST `vehicleCmdClear` and affects only your
account. No fields.

```yaml
service: xpeng.clear_stuck_commands
```

### send_raw_mqtt

Sends an arbitrary MQTT command to the vehicle. Advanced/debug use only.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `service_type` | **yes** | – | MQTT `service_type` number, e.g. `34` for `CMD_TO_SCREEN`, `19` for a state query; selector `0`–`50` |
| `msg_type` | no | `2` | Message type; `2` = control command, `4` = query; selector `0`–`10` |
| `msg_content` | no | `{}` | JSON string for `msg_content` |

```yaml
service: xpeng.send_raw_mqtt
data:
  service_type: 19
  msg_type: 2
  msg_content: '{"cmd_type": 1, "cmd_value": 1}'
```

::: warning
There is no validation of `msg_content` against the car's expectations. An
unknown `service_type` or a malformed payload can wake the car or leave a command
pending server-side — which is exactly the state `clear_stuck_commands` exists to
clear.
:::

## Credential import

### import_credentials

Validates a freshly minted `xpeng_tokens.json` / `xpeng_tenant.json` pair and
installs it into an existing config entry. It is the same validated path the
**Repairs** card uses, which is the point: an unattended renewal driven by an
automation is not a weaker path than a human clicking **Fix**. The renewal app
drives exactly this service.

**A renewal does not reload the entry.** When the pair is for the same car — the
same VIN and the same tenant `deviceId` — the new MQTT account is staged on the
running client and it reconnects, so no entity is torn down: the values stay put
and only `sensor.xpeng_<car>_last_renewal` moves. The entry is reloaded only when
the pair describes a different car or device id, because the VIN is part of every
entity id and the tenant's `deviceId` is the key the pair is sealed with.

The pair is proven before anything is replaced: the integration mints MQTT
credentials against the live API, so a stale token file, a tenant from a
different onboarding run, or a mismatched pair is refused and the working
credentials stay in place. The tenant's `deviceId` is adopted over whatever the
entry had, because the key pool is sealed against `md5(deviceId)`.

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `tokens_path` | no\* | – | Path to `xpeng_tokens.json` on the Home Assistant host (read inside the HA container) |
| `tenant_path` | no\* | – | Path to `xpeng_tenant.json` on the Home Assistant host |
| `tokens_json` | no\* | – | The whole `xpeng_tokens.json` file as a string, instead of a path |
| `tenant_json` | no\* | – | The whole `xpeng_tenant.json` file as a string, instead of a path |
| `vin` | no | the entry's VIN, else the VIN in the token file | VIN to store on the entry |
| `entry_id` | no | the first XPENG entry | Which config entry to import into |

\* For each half of the pair, supply either the JSON contents or a path. Missing
both is the error `invalid_tokens` / `invalid_tenant`; an unreadable path is
`invalid_tokens_path` / `invalid_tenant_path`.

```yaml
service: xpeng.import_credentials
data:
  tokens_path: /config/xpeng-secrets/xpeng_tokens.json
  tenant_path: /config/xpeng-secrets/xpeng_tenant.json
```

These paths are resolved **on the Home Assistant host**, so a pair written by an
app into its own container filesystem is not visible unless `pair_dir` is mapped
under `/config`. The pair is live credentials: `chmod 700` the directory and keep
it out of any share. See
[Credentials and the pair directory](./app-options#credentials-and-the-pair-directory).

#### Return value

The service is registered with `SupportsResponse.ONLY`, so a call **must** ask
for the response. Over the REST API that means the `?return_response` query
parameter:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tokens_path": "/config/xpeng-secrets/xpeng_tokens.json",
       "tenant_path": "/config/xpeng-secrets/xpeng_tenant.json"}' \
  "http://your-ha-host:8123/api/services/xpeng/import_credentials?return_response"
```

Home Assistant answers with its usual service envelope, and the import result is
in `service_response`:

```json
{
  "changed_states": [],
  "service_response": {
    "entry_id": "<entry-id>",
    "vin": "<17-character-vin>",
    "device_id": "<32-hex-device-id>",
    "account": "owner@example.com",
    "client_id": "xmart:app:<uid>:<device-uuid>"
  }
}
```

`account` is the address recorded in the token file, and is `null` for a pair
from an onboarding run that did not record one. `device_id` and `client_id` are
the credential material the new session is bound to — do not log them, publish
them to a dashboard, or paste them into a forum.

A call without `?return_response` is refused by Home Assistant with HTTP `400`
and *"Service call requires responses but caller did not ask for responses"*, not
by the integration.

#### The first entry

`xpeng.import_credentials` **cannot create the first config entry**, and it is not
registered until one exists: component-level setup runs only once an entry is
set up, so on a brand-new install the `xpeng` service domain is absent from
`/api/services` and a call to it fails with *"No XPENG config entry to import
credentials into"* (HTTP `400`).

The bootstrap is the **renewal app's** job, and it happens in front of this
service: on a run with no entry, the app drives the config flow with the pair it
just minted (start a flow for the `xpeng` handler, then submit the `import_tokens`
step) and its `POST /renew` answers `{"ok": true, "bootstrapped": "<VIN>", ...}`
instead of an `imported` result. That call also loads the component and registers
this service, so every later renewal uses `import_credentials` normally. See
[Unattended renewal](../guide/unattended-renewal) and
[App options](./app-options).
