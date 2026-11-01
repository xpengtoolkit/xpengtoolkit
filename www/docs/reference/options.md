# Configuration

**Configure → XPENG Options** is a single form, and a typical install needs
nothing changed on it. Every option has a working default; the polling intervals,
the MQTT listener and the certificate directory are only worth revisiting for a
specific reason, and the two legacy file options are for installs that keep the
credential pair on disk instead of on the config entry.

![The XPENG options form](../images/ha-options.png)

Reached from _Settings → Devices & services → XPENG → **Configure**_. Saving the
form reloads the config entry, so the new intervals and the reconnected MQTT
client take effect immediately. Saving also re-validates the certificate
directory (see [Certificate directory](#certificate-directory)).

## Summary

| Option | Label in the form | Default | Accepted |
| --- | --- | --- | --- |
| `scan_interval` | MQTT query interval (seconds) | `60` | `10`–`3600` |
| `parked_scan_interval` | Parked query interval (seconds) | `3600` | `60`–`7200` |
| `mqtt_refresh_interval` | MQTT credential safety-net interval (seconds) | `45` | `10`–`300` |
| `notification_poll_interval` | Message Centre poll interval (seconds) | `300` | `60`–`3600` |
| `rest_scan_interval` | REST API poll interval (seconds) | `300` | `60`–`3600` |
| `mqtt_listener_enabled` | Enable MQTT listener | off | on / off |
| `mqtt_listener_topics` | MQTT topics to subscribe | empty | free text, one topic per line or comma-separated |
| `mqtt_topic_discovery` | Enable MQTT topic discovery | off | on / off |
| `capture_enabled` | Record raw MQTT frames to a file | off | on / off |
| `capture_path` | Capture directory | `/share/xpeng-can` | absolute path |
| `capture_topics` | Topics to capture | empty | free text, one per line or comma-separated |
| `token_file` | Login token file (legacy) | empty | absolute path |
| `tenant_file` | Tenant cache file (legacy) | `/config/custom_components/xpeng/xpeng_tenant.json` | absolute path |
| `cert_dir` | Certificate directory | `/config/custom_components/xpeng/certs` | absolute path holding three PEM files |
| `command_mode` | Command permissions | `safe_plus` on a new entry; Full access when unset | `read_only` / `safe` / `safe_plus` / `full` / `custom` |
| `allow_wake` | Allow the integration to wake the car | on | on / off |
| `charge_port_controls` | Motorized charge-port controls (off = manual flap) | on | on / off |
| `tyre_pressure_unit` | Tyre pressure unit | Home Assistant unit system | Home Assistant unit system / kPa / psi / bar |
| `iccid` | TBox SIM ICCID | empty | free text, diagnostic only |
| `usable_battery_kwh` | Usable battery capacity (kWh) | `87.5` | `10`–`150` |

Ranges are the `vol.Range(...)` bounds enforced in `config_flow.py`, and the help
text quotes the same numbers. Where the code and the translations disagree, that
is noted in the option's own section below.

## Tyre pressure units

The four tyre-pressure sensors report **kPa**. Home Assistant already converts any
pressure sensor for display — its table covers kPa, psi, bar, hPa, Pa, mbar, inHg,
mmHg and atm — so this option only chooses what the four sensors should default
to:

| Choice in the form | Effect |
| --- | --- |
| **Home Assistant unit system** | No unit is suggested; your unit system decides (Settings → System → General). This is the default and is what previous versions did. |
| **kPa** / **psi** / **bar** | The four sensors display in that unit. The native value stays kPa; the conversion is Home Assistant's. |

A unit chosen on an **individual** entity still wins: Settings → Devices & Services
→ Entities → the pressure entity → Settings → **Unit of measurement**. That choice
is stored in the entity registry, so it survives restarts and renames, and turning
this option off does not overwrite it. Recorded history is converted with the
display unit, so changing either setting does not break long-term statistics.

## Polling intervals

Four independent schedules run at once — the MQTT query, the credential
safety net, the Message Centre poll and the REST poll. None of them wakes a
sleeping car on its own: only `scan_interval` can produce a force-upload, and only
while the car is charging or driving (so it is already awake). Waking a sleeping
car is the separate escalation described under [Fixed timers](#fixed-timers).

| Option | Paces | Default / accepted | Can wake the car? |
| --- | --- | --- | --- |
| `scan_interval` | the fast MQTT query — force-upload while charging/driving, passive otherwise; also the cadence a charging hint raises polling to | 60 s / 10–3600 | force-upload only while already active, and only with `allow_wake` |
| `parked_scan_interval` | the parked MQTT query, and the REST poll while parked | 3600 s / 60–7200 | no |
| `mqtt_refresh_interval` | retrying the credential mint while disconnected, and the `credentials_valid` staleness bound (3 ×) | 45 s / 10–300 | no |
| `notification_poll_interval` | the Message Centre poll (six requests per poll) | 300 s / 60–3600 | no |
| `rest_scan_interval` | the REST poll for image, XOS version, energy report and the supplementary widgets; also the widget-only fallback poll while the push stream is silent | 300 s / 60–3600 | no |

The integration publishes what each is doing now — see
`sensor.xpeng_<vehicle>_polling_status` and
`sensor.xpeng_<vehicle>_next_poll` on [Entities](./entities).

### `scan_interval`

How often the vehicle state is queried over MQTT while the car is **active** —
charging, or driving. Default **60 s**, accepted **10–3600 s**.

While active the query is a `cmd_type: 2` **force-upload**, not a passive query:
the car is already awake, so this wakes nothing, and it is the only reliable way
to get a fresh full state because XPENG's autonomous push stream and cloud fanout
stall without warning. The timer ticks on this interval in every state, so it is
also the granularity at which a parked query can be sent (below).

### `parked_scan_interval`

How often the car is queried while it is **parked and idle**. Default **3600 s**
(one hour), accepted **60–7200 s**.

A parked car is **never force-woken**. The integration sends one *passive* state
query per parked interval and lets the car sleep; a sleeping car answers that
query with a stub, which is enough to keep the entities from looking stale and is
not a fault. The interval is therefore a trade-off between how promptly a parked
car's values refresh and how much the car is disturbed — and this option also
slows the **REST** poll, which uses `rest_scan_interval` while active and this
value while parked. Raising it to `7200` halves both.

The **widget-only fallback poll** is the exception: while the push stream is
silent past its five-minute gate, that one read keeps the `rest_scan_interval`
cadence regardless of this option (see [`rest_scan_interval`](#rest_scan_interval)).
It is what bounds how long a car that starts driving during a wedged fan-out can
stay unnoticed, so it deliberately does not slow down with the car.

::: info Parked/Silent flapping is normal
The quiet interval and the silence threshold are deliberately different units of
scale. `parked_scan_interval` is measured in hours; the silence threshold is
**300 s**, and the watchdog that reads it runs every 60 s. A parked car that
answered its hourly query at 09:00 therefore looks `Silent` from about 09:05
until the next query, at which point the status sensor returns to `Parked`. Both
readings are correct: `Parked` is the cadence, `Silent` is "the state topic has
been quiet past the threshold".

The reconnect that `Silent` triggers is cloud-side only and never wakes the car.
Waking it is a separate escalation that needs **30 minutes of unbroken silence**
first, and is capped at one force-upload per **12 hours** — so a car parked and
asleep all day is woken at most twice, and one that answers the passive query is
never woken by this path at all. A car that was **moving** when the data stopped
is the one exception: the force-upload cannot wake it, so that escalation skips
the 12-hour budget and is limited to one per 10 minutes and one per episode of
motion instead.
:::

### `mqtt_refresh_interval`

The **safety-net** interval for MQTT credentials. Default **45 s**, accepted
**10–300 s**.

Fresh credentials are minted **on demand**: immediately before a connection, when
the one in hand has expired (the broker's credentials live 60-90 s). That is what
the official app does - `hasAccount()` gates the mint and a live session is never
rotated - and it is why a healthy install now makes one API call per reconnect
rather than one every 45 s. This interval covers the cases where nothing is going
to reconnect soon: while the session is down, the mint is retried at this cadence,
and the interval also sets the age bound used by `binary_sensor.xpeng_<vehicle>_credentials_valid`
(3 × this value). The loop only runs when the integration has app-tier
credentials; a token file without them leaves it disabled. There is no reason to
lower it.

### `rest_scan_interval`

How often the REST API is polled for the data MQTT does not carry — the vehicle
image, the XOS version, the energy report and the rest of the supplementary
widgets. Default **300 s**, accepted **60–3600 s**.

The REST poll follows the car's state: this interval while active/driving,
`parked_scan_interval` while parked. Notifications are on their own timer —
`notification_poll_interval` — and are not affected by this option.

Since **0.6.38** this interval also paces the **widget-only fallback poll**. When
the last energy-carrying MQTT frame is older than the five-minute merge gate, the
integration reads just `/biz/v5/widget/vehicle/state` every `rest_scan_interval`,
instead of waiting for the heavy REST poll's parked cadence. That is what makes
the fallback's motion evidence arrive within minutes: before it, a car that
started driving right after the hourly REST poll was invisible for up to an hour
(issue #3, 2026-10-27). It is a cloud read, it does not wake the car, and it does
not fetch any of the other REST endpoints.

### `notification_poll_interval`

How often XPENG's Message Centre is polled for notifications — the list behind
the car's notification bell (charging finished, software update, service
messages). Default **300 s**, accepted **60–3600 s**.

Each poll is **six REST requests**, one per notification category, which is why
it has a floor of a minute rather than being free to set to zero: below that the
rate limit, not the car, would decide the delay. Nothing is pushed to the
integration; a notification cannot arrive sooner than this interval.

`sensor.xpeng_<vehicle>_last_notification` reports the latest message, and its
`delay_s` attribute is the measured time between the message's `createTime` and
the poll that picked it up — the honest way to answer "is this interval too slow
for me?". It is independent of `rest_scan_interval`: that one feeds the image,
XOS version and energy widgets, this one feeds notifications, and changing one
does not move the other.

## MQTT listener

The primary vehicle-state topic is always subscribed. These three options add
*extra* subscriptions for diagnostics and reverse-engineering; none of them
affects the entities built from the primary topic.

### `mqtt_listener_enabled`

Subscribes the additional topics. Default **off**. With it off,
`mqtt_listener_topics` and `mqtt_topic_discovery` are stored but not used — the
listener is not configured and nothing extra is subscribed.

### `mqtt_listener_topics`

The extra topics to subscribe, one per line or comma-separated. Default empty.
MQTT wildcards (`+` and `#`) are supported, and each topic is trimmed; topics
longer than 512 characters are dropped. Subscriptions are attempted at connect
time and the result of each one is recorded, so a topic the broker refuses is
visible rather than silently missing.

`{device_id}` and `{user_id}` are expanded from this install's identity - the
device id the broker authenticated (the fourth segment of the MQTT `clientId`) and
the numeric account uid - so an identity-scoped topic can be written once and
survive a credential refresh. For example the app also subscribes a
**device-scoped push channel**:

```text
Xp/Msg/Push/{device_id}
```

Watch `sensor.xpeng_<vehicle>_mqtt_listener`: each subscription's SUBACK is
recorded there as `ACCEPTED` or `REJECTED`, which answers whether this account is
granted a topic without waiting for a message. The payload on that channel is a
push notification (a `messageId` plus a `content` envelope), not vehicle state, so
it is for discovery - it is not parsed into entities.

### `mqtt_topic_discovery`

Tries a fixed list of known XPENG broker topics in addition to the ones typed
above — the CAN packs (`Xmart/Car/+/State/CanPack1s`, `Xmart/Tbox/+/CanPack1s`,
`Xmart/TboxD21B/+/CanPack1s`, the `$local/$queue/…` variants), T-box status
(`Xmart/Tbox/+/StatPack`), client connect events, CDU log statistics and
`Xmart/Car/+/CanPackV2/Repub`. Default **off**.

This is for finding out what the broker will hand out on your account; the payloads
are not parsed into entities. What actually arrived is reported by
`sensor.xpeng_<vehicle>_mqtt_listener` (a count, the last topic and payload, and a
rolling log), each message is also fired on the bus as `xpeng_mqtt_message`, and
the accepted/refused result of every subscription is reported by
`sensor.xpeng_<vehicle>_mqtt_discovery`. Note that the listener sensor reads
`No messages` on a healthy install, because vehicle data arrives over the REST
poll (see [Entities](./entities)).

## Raw capture

The listener above is a *diagnostic*: it decodes each frame, keeps a 4 KB preview,
fires an event and drives two sensors. That is the right shape for "is anything
arriving on this topic?" and the wrong shape for keeping the car's CAN stream, so
capture is a separate pair of options that write the frames out unread.

### `capture_enabled`

Subscribes to the capture topics and writes every frame to a binary journal for
offline decoding. Default **off**.

Independently of `mqtt_listener_enabled` - capture needs none of the listener's
machinery, and an install can run either one alone. Two things follow from that,
and they are the reason it is built this way:

- **Nothing is decoded on the way in.** The frame is handed to a writer thread as
  bytes, exactly as the broker delivered them. `tools/xpeng_can_decode.py` reads
  the file afterwards, with no broker, no credentials and no car, so a decoder can
  be rewritten without capturing again.
- **The per-frame cost is bounded.** The frame is put on a fixed-size queue and
  the callback returns; a single writer thread does the framing, file I/O and
  rotation. If the disk cannot keep up the queue fills and frames are **dropped
  and counted** rather than queued without limit - which is what makes
  `mqtt_listener_topics: "#"` survivable, where the per-frame decode and entity
  update of 0.4.30 were not.

The drop count is on `sensor.xpeng_<vehicle>_capture`, together with the frames
written, the current file and the queue depth. It matters: a capture that silently
lost a third of the stream would be worse than no capture at all.

Reading a capture:

```bash
python3 tools/xpeng_can_decode.py summary capture.xpgcan
python3 tools/xpeng_can_decode.py shapes  capture.xpgcan --topic CanPack1s
```

### `capture_path`

Where captures are written. Default `/share/xpeng-can`.

`/share` rather than `/config` deliberately: a capture inside the config directory
is carried into every backup and sits in the directory Home Assistant watches.
The directory is created if it does not exist, and an install without `/share` (a
Container install) falls back to `<config>/xpeng-can`. If neither can be written
the integration logs the error and carries on without capture, because the car's
entities are the primary job.

### `capture_topics`

The topics to record, one per line or comma-separated. MQTT wildcards (`+` and
`#`) are supported. Default **empty**, which means the known CAN topics - the same
candidates `mqtt_topic_discovery` tries. Typing anything here replaces that list
entirely.

Note that `Xmart/Car/+/...` is every car the broker will hand you, not yours. The
summary reports each car id it finds, so a capture that contains another vehicle's
traffic says so.

## Legacy credential files

An in-Home-Assistant login — **Import tokens from the onboarding tool**, Google or
email — stores the token and tenant pair on the config entry itself, and that pair
always wins. The two file options are the fallback for installs that keep a pair
on disk from the command-line tooling. Both are ignored unless the token file
actually exists.

### `token_file`

Path to a `xpeng_tokens.json` written by the CLI login tooling. Default **empty**
(the option is not pre-filled). When set and present, the integration loads the
pair from `token_file` plus `tenant_file` for its app-tier API calls, and the
entry's own stored pair takes precedence when it has one.

::: info
`const.py` also defines `DEFAULT_TOKEN_FILE`
(`/config/custom_components/xpeng/xpeng_tokens.json`), but the options form
defaults this field to empty and does not use that constant. `tenant_file` does
default to its `DEFAULT_TENANT_FILE`.
:::

### `tenant_file`

Path to a cached `xpeng_tenant.json`. Default
`/config/custom_components/xpeng/xpeng_tenant.json`, as the help text says. If the
file is missing or unreadable it is fetched and written here, so a file-based
install heals itself on the next API call.

The tenant carries the `deviceId` the token pair was sealed against. A tenant
from a different onboarding run than the tokens is rejected, and an install whose
entry is configured with one device id while the tenant records another fails
every API call with `12010104 "Login status expired"` until it is re-onboarded.

## Certificate directory

### `cert_dir`

Absolute path to the directory holding the mTLS material for the XPENG broker.
Default `/config/custom_components/xpeng/certs`, which is the directory the
integration ships in. Resolution order:

1. this option, when it is set to a non-empty path;
2. otherwise the default, `/config/custom_components/xpeng/certs` — the same
   directory as the integration's bundled `certs/`, which is why the default
   works on a fresh install.

The path is used exactly as typed: there is no `~` expansion, and an absolute
path is expected. The directory must contain all three files —

| File | Purpose |
| --- | --- |
| `ca-chain.crt` | XPENG CA chain; the broker's certificate is verified against it, with hostname checking on |
| `client-cert-0.pem` | the client certificate |
| `client-key.pem` | the matching private key |

— and **saving the form with a non-empty value validates them**. If any is
missing the form is re-rendered with the error *"One or more certificate files
are missing from that directory. It must contain ca-chain.crt,
client-cert-0.pem and client-key.pem."* and nothing is saved. A blank value is
accepted and means "use the default directory". The same directory is published
to the REST client, so the two channels use one copy of the material, and a
missing file at setup time is reported as a setup failure rather than a silent
connection error.

::: warning
`ca-chain.crt` is a public trust anchor, but `client-cert-0.pem` and
`client-key.pem` are not: they are credentials. Keep them inside `/config`, out
of any share, and do not paste them into a dashboard or a log.
:::

## Vehicle identity and capacity

### `iccid`

The TBox SIM's ICCID, if you want it in Home Assistant. Default empty. It is
purely diagnostic: no integration behaviour reads it.

There is **no app-tier API that exposes the ICCID**, so nothing can discover it
for you. Read it on the car's head unit with:

```text
*#9925*131#
```

Enter the digits here and the integration creates
`sensor.xpeng_<vehicle>_tbox_iccid` for it; with the option empty that entity
does not exist at all. The value is stored in the config entry and in the
recorder database, so treat it as you would any other vehicle identifier.

### `usable_battery_kwh`

The usable pack size used to turn an observed state-of-charge drop into an energy
estimate while the car is parked and idle. Default **87.5** kWh, accepted
**10–150**. Each detected 1% drop contributes `usable_battery_kwh / 100` to the
parked-consumption sensors.

The car reports a percentage, not kilowatt-hours, so this number is what makes
`sensor.xpeng_<vehicle>_parked_consumption` meaningful for your trim. It is asked
on the setup screen too, so a new install can set it without opening Configure.

**A VIN cannot pick this for you.** A XPENG VIN encodes the model *family* only
(see [Entities](./entities)), and the family is not the pack: the G6 has been sold
with four, the G9 with four more. Pick the row you own — the figures are the
**usable** capacity, which is what this option means, and they are keyed by the
number each pack is commonly known by (source of record: EV Database):

| Known as | Installed | Usable — use this | Variant |
| --- | --- | --- | --- |
| `66.0` | 66.0 | **65.3** | G6 SR (MY24) LFP |
| `68.5` | 68.5 | **67.8** | G6 SR (MY25) LFP |
| `80.8` | 80.8 | **80.0** | G6 LR / Performance (MY25) LFP |
| `87.5` | 92.0 | **87.5** *(default)* | G6 LR (MY24) NMC |
| `78.2` | 78.2 | **75.8** | G9 SR (MY23) LFP |
| `79.0` | 79.0 | **78.2** | G9 SR (MY25) LFP |
| `93.1` | 93.1 | **92.2** | G9 LR (MY25 on) LFP |
| `98.0` | 98.0 | **93.1** | G9 LR (MY23-24) NMC |

The default is this project's own car (a G6 LR on the MY24 NMC pack), which is why
it is not a G9 figure. Model years are the ones each pack is commonly sold under, and the
G9's NMC pack ran from launch - so it is labelled MY23-24 rather than pinned to one year,
since which year a car is called depends on the market it was sold in. **The figure your
car is quoted with is the reliable discriminator**: a G9 sold as `98 kWh` wants 93.1, one
sold as `93.1 kWh` wants 92.2. A value that is too high overstates parked drain and one
that is too low understates it; nothing else in the integration depends on the figure, and the
estimate is also bounded per hour and per step in the coordinator so that a
mis-set value cannot produce an absurd reading.

## Command permissions

The first two fields on the form decide what the integration may do to the car at all.
`command_mode` is a preset - **Read only**, **Safe**, **Safe Plus** or **Full access**
- and choosing **Custom** opens a second screen with one dropdown per capability group.
`allow_wake` is separate because waking a sleeping car costs a little of its 12V
battery. With it off the integration sends passive queries only: the watchdog's
30-minute escalation becomes a passive query, the cold-start snapshot does not
happen, and `button.xpeng_<vehicle>_wake_vehicle` is refused — that button is also
what an EVCC vehicle layout presses as its `wakeup:`. Charge and drive polls are
unaffected, because the car is already awake.

| Setting | A new entry starts at | An entry with no stored value |
| --- | --- | --- |
| `command_mode` | `safe_plus` | **Full access** |
| `allow_wake` | on | on |
| `charge_port_controls` | on | on |

### `charge_port_controls`

Whether this car has motorized charge-port controls. It is on by default, so nothing
changes unless it is turned off — and turning it off is the right answer for a car
with a **manual flap**, which cannot act on the open/close commands. With it off the
two charge-port buttons are not created and the `xpeng.open_charge_port` /
`xpeng.close_charge_port` services raise a clear error, while
`sensor.xpeng_<vehicle>_slow_charge_port` keeps reporting state.

There is no telemetry field that says which kind of flap a car has, so this is an
owner's answer rather than something the integration can infer. Reported against a
P7+ with a manual flap (2026-10-22); other cars answer the commands.

The second column is the upgrade guarantee: an install that predates the option keeps
Full access, so an update never takes charging or unlocking away from an automation
that relies on it. See [Command permissions](./command-permissions) for the groups,
what each one covers, and what the mode does *not* protect against.

## Fixed timers

These are constants, not options — listed so the numbers in the sections above
have a source, and so "what wakes my car?" has one answer in one place.

| Behaviour | Value |
| --- | --- |
| Watchdog tick | `60 s` |
| Silence before the status reads `Silent` (watchdog suspects a stuck socket) | `300 s` |
| Minimum gap between watchdog reconnects, car active | `600 s` |
| Minimum gap between watchdog reconnects, car parked | `1800 s` |
| Unbroken silence before an escalating force-upload | `1800 s` (30 min) |
| Minimum gap between escalating force-uploads | `43200 s` (12 h) |
| Minimum gap between force-upload escalations justified by motion (the last state said the car was moving, so it cannot be woken) | `600 s` (10 min), and one per episode of motion |
| Quiet CONNECT grace after a session takeover (a renewal login) | `300 s` |
| Settle window after the integration's own reconnect | `120 s` |
| Settle retry delays | `10 s`, `20 s` |
| Mint-on-demand expiry margin | `15 s` |

**No configurable timer wakes the car.** The periodic MQTT queries are passive
unless the car is already charging or driving. A sleeping car is only ever woken
by one of three actions, all gated by `allow_wake`: the cold-start snapshot on a
Home Assistant start, the 30-minute silence escalation (capped at one per 12 h),
and an explicit press of `button.xpeng_<vehicle>_wake_vehicle`.

See also [Services](./services) for the commands that act on the car, and
[App options](./app-options) for the two companion apps.
