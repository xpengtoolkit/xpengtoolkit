# Overview

Xpengtk is a Home Assistant custom integration for XPENG electric vehicles
(domain `xpeng`). It signs in with your own XPENG account, subscribes to the
vehicle's telemetry stream over mutual-TLS MQTT, and exposes the car's state —
and a set of remote commands — as Home Assistant entities and services.

It runs headless: no phone and no official app. With the two helper apps in
[Unattended renewal](./unattended-renewal) it re-mints its own credentials before
the session expires, so nothing has to be done by hand.

::: warning Disclaimer
Not affiliated with, endorsed by, or supported by XPENG. Built by
reverse-engineering the official mobile app; it may break at any app release. Use
your own account and credentials, at your own risk. A third-party client may
affect the official app's live view.
:::

::: warning No credentials, certificates or keys are supplied
This project ships **none** of them. You obtain the mTLS client certificate and
key from your own copy of the official app, and your own account credentials,
by following [Certificates](./certificates). How you obtain them, where you store
them, and any distribution you make of those artefacts, is your own
responsibility and entirely at your own risk.
:::

## Two channels, one account

The integration talks to XPENG's cloud over two independent channels, and they do
different jobs:

| Channel | Endpoint | Needs the client key? | What it does |
| --- | --- | --- | --- |
| MQTT over mutual TLS | `iot-app-global.xiaopeng.com:8885` | Yes | Live vehicle telemetry pushes and remote commands |
| HTTPS REST (mutual TLS) | `apps-global.xiaopeng.com` | Yes | Account login, and minting the short-lived MQTT password on a timer |
| Web SSO | `iot-web-eur` | No | At most energy-report/dashboard data |

Both channels the integration uses are signed with XPENG's app-tier scheme, and
both need the client certificate described in [Certificates](./certificates).

## Data arrives by push, not by polling

There is no `update()` loop for vehicle state. A single long-lived MQTT
connection is opened at startup, and the broker pushes vehicle state onto it. A
passive coordinator receives those frames on the client's network thread and
hands them to Home Assistant, which notifies entities that the data changed. The
coordinator has no update interval; nothing pulls a value out of the cloud on a
schedule.

That is why entities update the instant the car reports something, and why they
hold their last known value when it does not.

That is not the same as "the integration never asks". Three timers run, and none
of them is a pull loop over the state: the **MQTT query timer** asks the car to
upload (the answer still arrives as a push - see the next section and the
[timer options](../reference/options#polling-intervals)), the **REST poll**
fetches the supplementary data MQTT does not carry, and the **Message Centre
poll** fetches notifications. A few **diagnostic sensors** also poll themselves
every 30 s, for values that change with no data arriving — credential age,
polling status, the next-poll estimate.

## The car is not woken up

How often the integration *asks* the car for a fresh frame depends on what the car
is doing:

| Car state | What the integration sends |
| --- | --- |
| Charging, or driving | A force-upload request every `scan_interval` (default 60 s). The car is already awake, so this is not a wake-up — and it is necessary, because the cloud's push fan-out stalls often enough to matter. |
| Parked and idle | One *passive* query per `parked_scan_interval` (default 1 hour). A sleeping car answers that with a small stub without waking. |

A parked car is **never** force-woken on a schedule. The only force-upload sent to
a possibly-sleeping car is a single one on a genuine Home Assistant restart, to
seed the entity values.

Even that can be switched off: **Allow the integration to wake the car**, on the
same Configure screen, makes the integration strictly passive. It sends queries
only, and the watchdog degrades to a passive query instead of a force-upload, at the
price of staler sensors. See
[Command permissions](../reference/command-permissions#allow-wake).

The timing defaults, all options on the integration under
**Settings → Devices & Services → XPENG → Configure**:

| Option | Default | Controls |
| --- | --- | --- |
| MQTT query interval (`scan_interval`) | 60 s | the force-upload cadence while charging or driving |
| Parked query interval (`parked_scan_interval`) | 3600 s | the passive-query cadence while parked |
| MQTT credential safety-net interval (`mqtt_refresh_interval`) | 45 s | retry cadence while disconnected, and the age bound behind `credentials_valid` |
| Message Centre poll interval (`notification_poll_interval`) | 300 s | how often notifications are fetched (six requests per poll) |
| REST API poll interval (`rest_scan_interval`) | 300 s | REST-sourced data such as the vehicle image and XOS version, and — while the push stream is silent — the widget-only fallback poll |
| Certificate directory (`cert_dir`) | `/config/custom_components/xpeng/certs` | where the three mTLS files live |

While the push stream has been silent past the five-minute gate, the integration
also reads just the REST widget on the `rest_scan_interval` cadence (0.6.38).
That is a cloud read, it does not wake the car, and it is what bounds how long a
car that starts driving while the fan-out is wedged can stay unnoticed: minutes
rather than the next hourly poll.

## Stuck sessions get unstuck

XPENG's cloud push fan-out sometimes goes quiet while the TCP connection still
looks perfectly healthy — server-side, not something the client can prevent. A
silence watchdog notices 300 s of no vehicle-state traffic, drops and
re-establishes the connection, and sends a passive query. Reconnects are
serialised behind a lock, because concurrent reconnects produce two clients
sharing one client ID, which evict each other from the broker; setting an entry up
again also stops any client a previous setup left behind.

If nothing meaningful arrives for 30 minutes of unbroken silence, the watchdog
escalates to a single force-upload — which does wake the car — and then waits at
least 12 hours before it may do so again. Both bounds exist so that a wedged
fan-out cannot turn into a car woken on a schedule. One exception: when the last
state the integration accepted said the car was **moving**, the force-upload
cannot wake it, so that escalation is not held to the 12-hour budget — it is
limited to one per 10 minutes and one per episode of motion, which keeps a
mid-drive stall from freezing the sensors for the rest of the trip. See
[Fault finding](./fault-finding#silent-and-parked).

## Credentials rotate, and the identity is per-install

The MQTT password is short-lived (about 60-90 s) and is **minted on demand**: right
before a connection, if the credential in hand has lapsed. That is the app's own
model - a live session is never re-minted, so a healthy install makes one API call
per (re)connect instead of one every 45 s.

If the login token has aged out, the refresh recognises XPENG's
`12010101`/`12010104` "Login status expired" response, renews the token and
retries. If the *refresh* token is dead too, it escalates to reauthentication
rather than retrying forever, and puts a **Repairs** card on the screen.

Each installation also mints its **own device identity** on first run, and that
identity is signed into the requests and embedded in the MQTT client ID. The
credentials XPENG issues are bound to it, so a credential set belongs to the
install that produced it — never copy one between installations. The identity is
visible as the `client_id` attribute of
`binary_sensor.xpeng_<vehicle>_credentials_valid`.

Sessions are capped: XPENG retires one about two hours after the login that
created it, and refreshing cannot extend it. That is what the two helper apps are
for — see [Unattended renewal](./unattended-renewal).

## What you get

Roughly 120 entities across sensors, binary sensors, device tracker, lock,
climate, select, number, switch, button, calendar and image: battery and range,
charging state and charge limits, doors/windows/tyres, location, climate and seat
heating, sentinel/X-Guard, OTA state, and a set of remote commands. The count
varies with the car's configuration, with options such as the TBox ICCID, and with
[Command permissions](../reference/command-permissions): a group set to Disabled is
not created, and one set to Read only keeps its state but refuses its commands. A new
install starts at Safe Plus, so the OTA switch, the trunk-power outlet, unlocking, the
trunk and the windows are absent until it is widened.

A group of diagnostic entities answers a different question — "is the integration
still receiving data?" — and is the first place to look when a value looks wrong.
Both sets are listed in [Entities](../reference/entities), and
[Dashboard examples](../examples/dashboard) has cards built from them.

XPENG's Message Centre notifications are exposed too, as a sensor and a bus event,
because they do not travel over MQTT — see [Notifications](./notifications).

## Requirements

- Home Assistant 2024.7 or newer
- A **dedicated** XPENG account that the car has been shared to — never the
  account on your phone; see
  [Use a dedicated account](./getting-started#use-a-dedicated-account)
- The mTLS certificates, which you extract yourself from an XPENG APK
  3.7.0–3.12.0 (see [Certificates](./certificates)). They are deliberately not
  shipped with this project.

## Why not just use the official app

The official app is the supported way to see and control the car, and this
project does not replace it. What it adds is the same telemetry and the same
commands inside Home Assistant, where they can be recorded, charted and
automated. It runs headless and keeps working while the app is signed out; because
XPENG allows one active session per account, it is designed around a dedicated
account so that it and the phone app do not evict each other.

## Status

Early. It authenticates with your own XPENG account, subscribes to the vehicle
telemetry stream over mutual-TLS MQTT, and exposes sensors, binary sensors and
controls in Home Assistant.

## Related projects

- [xpengtoolkit/slider-solver](https://github.com/xpengtoolkit/slider-solver) —
  a self-hosted solver for XPENG's login slider captcha. It solves the account
  owner's own challenge on a host they control: keep it private, and never expose
  it without `SOLVER_TOKEN`.
- [xpengtoolkit/xpengtoolkit](https://github.com/xpengtoolkit/xpengtoolkit) — this
  project, which also ships the credential-renewal app.
