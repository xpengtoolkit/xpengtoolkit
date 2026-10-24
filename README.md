# Xpengtk

A Home Assistant custom integration for XPENG vehicles (`domain: xpeng`).

Unofficial, reverse-engineered, and not affiliated with XPENG.

**Documentation: <https://xpengtoolkit.github.io/xpengtoolkit/>** — installation,
the certificates, the two renewal apps, every entity and service, dashboard and
automation examples, and what to check when it stops working.

It runs headless - no phone and no official app - and with the two helper apps in
[Unattended renewal](#unattended-renewal) it re-mints its own credentials before the
session expires, so nothing has to be done by hand.

> **No credentials, certificates or keys are supplied with this project.** You
> obtain your own: the mTLS client certificate and key come from your own copy of
> the official app, and the account credentials are your own. How you obtain
> them, where you store them, and any distribution you make of those artefacts,
> is your own responsibility and entirely at your own risk. The instructions are
> in
> [`custom_components/xpeng/certs/README.md`](custom_components/xpeng/certs/README.md),
> and on the rendered site under
> [Certificates](https://xpengtoolkit.github.io/xpengtoolkit/guide/certificates).

## The whole process at a glance

### What "identity" means here - three separate things

This is the part that trips people up, so it is worth being explicit. Nothing
about your car's identity changes; what gets assembled is *this install's* set of
credentials. Three independent pieces, from three different places:

| Piece | What it is | Where it comes from | Who does it |
|---|---|---|---|
| **1. App mTLS certificate + key** | `CN=xp_app_client01`, the same for every XPENG user. Proves *the app*, not you. | Extracted from an XPENG APK | You, once (step 2) |
| **2. Device identity** | A random 32-hex id unique to this install, bound into the signed requests and the MQTT client id | **Minted automatically** by the onboarding tool when it fetches the tenant | Nobody - it is handed to you |
| **3. Account tokens** | Your account's access/refresh token, and the tenant key pool they are sealed with | Minted once: by the renewal app (step 5), or by the onboarding tool on a desktop ([alternative](#alternative-mint-the-first-pair-yourself)) |

You never type a device id anywhere. The tool generates it, and the integration
**adopts** it from the imported tenant rather than minting a new one - that
binding is exactly why the token files and the tenant must be imported together.

**Do not sign the official app in on this account.** A shared car needs no app
visit first - the integration works fully headless - and signing in makes the two
evict each other, because XPENG allows one session per account (see
[Use a dedicated account](#use-a-dedicated-account---do-not-use-your-primary-account)).
If the entities come up empty, that is the stale-client case in
[Troubleshooting](#troubleshooting), not a missing app login.

### The steps

**Before step 1, once:** create a dedicated XPENG account and have your main
account share the car to it (see
[below](#use-a-dedicated-account---do-not-use-your-primary-account)). Accept the
share from the new account if the app asks you to - but **do not leave the
official app signed in as that account**, for the reason given just above.

| # | Step | Where | Roughly how long |
|---|---|---|---|
| **1** | [Download the integration](#installation) - add the HACS custom repository, install, and **restart HA**. This adds nothing under Settings | Home Assistant | 2 min |
| **2** | [Download the repository ZIP](https://github.com/xpengtoolkit/xpengtoolkit) and [extract the certificates](#step-2---extract-the-certificates-outside-home-assistant) from an XPENG APK **3.7.0-3.12.0** | Any computer | 5 min |
| **3** | [Copy the three PEM files onto the Home Assistant host](#step-3---copy-the-certificates-onto-the-home-assistant-host) | Home Assistant | 2 min |
| **4** | [Install the two apps](#step-4---install-the-two-apps) (slider solver + credential renewal) and set their tokens | Home Assistant | 5 min |
| **5** | [Watch it appear](#step-5---watch-it-appear) - the app creates the integration entry and then renews it on its own timer | Home Assistant | 1 min |

**Only step 2 happens outside Home Assistant**, because extracting the certificates
needs the APK. Nothing else does - including the captcha-gated login, which the
solver app clears.

**Steps 4-5 are the whole of it**: the app creates the entry, then keeps it alive on
its own timer. Without them the integration still works, but only until XPENG retires
the session - about two hours later on the accounts we measured, though not always (see
[Unattended renewal](docs/UNATTENDED_RENEWAL.md)) - and then a fresh pair has to be
pasted in by hand.
If you would rather not run the apps at all, or
not hand them the account password before you have seen the integration work,
[the alternative](#alternative-mint-the-first-pair-yourself) mints the
pair on a desktop instead and replaces steps 4-5 - it needs Chrome, Node and one
manual slider drag.

**Step 1 puts the files on the host; it does not add the integration.** The restart is
what registers it, and going to **Settings -> Devices & Services -> Add Integration**
at that point opens a setup form asking for a token pair that does not exist until step
5 - the trap this note exists for. Adding the entry is either automatic (the renewal app
in step 4 drives that same flow and creates it) or, on
[the alternative route](#create-the-entry-from-those-files), the last thing you do once
you have a pair to paste.

Before you start, two things that catch people out:

- **Use a dedicated XPENG account, never your primary one** - see
  [below](#use-a-dedicated-account---do-not-use-your-primary-account).
- **Download an XPENG APK 3.12.0 or older on purpose.** From 3.13.0 onward the
  certificate is no longer extractable from the package.

## Before you install - read this first

**You can get the mTLS client key from the Android APK, offline, with no
jailbreak, no Frida and no phone hacking at all.** You need an XPENG APK build
between **3.7.0 and 3.12.0**, and one command produces all three files - see
[step 2](#step-2---extract-the-certificates-outside-home-assistant).

Why the key matters, and what each channel needs:

| Channel | Needs the client key? | What it can do |
|---|---|---|
| MQTT broker (`iot-app-global`) | **Yes** | Real-time telemetry and vehicle control |
| App REST API (`apps-global`) | **Yes** | Account login, mints the MQTT credentials |
| Web SSO (`iot-web-eur`) | No | **At most** energy-report/dashboard data |

**One hard limit:** this APK route works for **3.7.0 through 3.12.0**. From
**3.13.0** onward XPENG ships an encrypted keystore with the identity supplied as
runtime configuration, and the APK alone is no longer enough. The break is
exactly between 3.12.0 and 3.13.0 - so download an older build on purpose.

The client certificate and key are **not** distributed with this project; you
extract them from your own copy of the official app. See
[`custom_components/xpeng/certs/README.md`](custom_components/xpeng/certs/README.md)
for the full detail, including the version boundary that decides which APK build
you need.

If you cannot obtain the key, this integration is not usable - but for most
people the APK route above now makes that a non-issue.

## Status

Early. It authenticates with your own XPENG account, subscribes to the vehicle
telemetry stream over mutual-TLS MQTT, and exposes sensors, binary sensors and
controls in Home Assistant.

## How it works

### Two channels, one account

The integration talks to XPENG's cloud over two independent channels, and they
do different jobs:

- **MQTT over mutual TLS** (`iot-app-global.xiaopeng.com:8885`) - the live
  vehicle channel. It carries telemetry pushes *and* remote commands. This is
  where the client certificate you supplied is actually used.
- **HTTPS REST** (`apps-global.xiaopeng.com`, mutual TLS too) - the control
  plane. It performs account login, and it mints the short-lived MQTT password
  on a timer.

Both are signed with XPENG's app-tier scheme, and both need the client key.

### Data arrives by push, not by polling

There is no `update()` loop for vehicle state. A single long-lived MQTT
connection is opened at startup, and the broker pushes vehicle state onto it. A
passive coordinator receives those frames on the client's network thread and
hands them to Home Assistant, which notifies entities that the data changed. The
coordinator has no update interval; nothing pulls a value out of the cloud on a
schedule.

That is why entities update the instant the car reports something, and why they
hold their last known value when it doesn't.

That is not the same as "the integration never asks": three timers run, and none
of them is a pull loop over the state.

- The **MQTT query timer** sends the car a request to upload (`scan_interval`
  while it is awake, `parked_scan_interval` while parked). The answer still
  arrives as a broker push - the timer asks, it does not collect.
- The **REST poll** fetches what MQTT does not carry - vehicle image, XOS
  version, energy report and similar supplements - every `rest_scan_interval`
  while active, slowing to the parked interval when parked.
- The **Message Centre poll** fetches notifications every
  `notification_poll_interval`.

A handful of **diagnostic sensors** also poll themselves every 30 s, because
their values change with no data arriving at all: credential age, polling
status, the next-poll estimate.

The REST poll also carries a **fallback for the energy fields**. When the push
stream has been silent for more than five minutes, the widget snapshot's state
of charge, range and charge-port statuses are merged in, so the battery and
range sensors keep moving while the fan-out is stuck. That merge is the fresher
source, and since 0.6.25 the MQTT client's own partial-merge baseline is updated
with it - otherwise a later charge-less ping re-broadcast the frozen snapshot
and put the value back, which is what made the battery sensor flip between two
values every `parked_scan_interval`.

### The car does not get woken up

How often the integration *asks* the car for a fresh frame depends on what the
car is doing:

| Car state | What the integration sends |
|---|---|
| Charging, or driving | A force-upload request every `scan_interval` (default 60s). The car is already awake, so this is not a wake-up - and it is necessary, because the cloud's push fan-out stalls often enough to matter. |
| Parked and idle | One *passive* query per `parked_scan_interval` (default 1 hour). A sleeping car answers that with a small stub without waking. |

A parked car is **never** force-woken on a schedule. The only force-upload sent
to a possibly-sleeping car is a single one on a genuine Home Assistant restart,
to seed the entity values.

### Stuck sessions get unstuck

XPENG's cloud push fan-out sometimes goes quiet while the TCP connection still
looks perfectly healthy - server-side, not something the client can prevent.
A silence watchdog notices 300s of no vehicle-state traffic, drops and
re-establishes the connection, and sends a passive query. Reconnects are
serialised behind a lock, because concurrent reconnects produce two clients
sharing one client ID that evict each other from the broker - and setting an
entry up again now stops any client a previous setup left behind, since two
clients under one account is the failure described in
[Troubleshooting](#troubleshooting).

If the fan-out stays wedged and nothing meaningful arrives for 30 minutes, the
watchdog escalates to one `cmd_type: 2` force-upload - the only thing that
punches through a stalled fan-out - and then waits 12 hours before it may spend
another, so a wedged cloud cannot turn into a car woken on a schedule. One
exception: when the last state accepted said the car was **moving**, the
force-upload cannot wake anything, so that escalation skips the 12-hour budget -
one per 10 minutes, one per episode of motion - which is what keeps a mid-drive
stall from freezing the sensors for the rest of the trip.

While the push stream is wedged the REST widget fallback is the only thing still
being polled, so it is also watched for movement: an odometer ahead of the last
accepted push, or its own drive/gear state saying the car is not in P, counts as
motion evidence (0.6.27). That lets the bounded escalation above fire while the
pusher is stuck, instead of the 12-hour sleeping-car budget holding it - the case
behind issue #3 on 2026-10-23 was 426 minutes without meaningful data while the
REST odometer advanced 67 km. A `Standby` vehicle mode is not movement: the car
is parked, so the sleeping-car budget still applies.

### Credentials rotate, and the identity is per-install

The MQTT password is short-lived (about 60-90 s). It is **minted on demand**:
immediately before a connection, if the one in hand has lapsed - the same gate the
official app uses - and an established session is never re-minted.
`mqtt_refresh_interval` (default 45 s) is the retry cadence while disconnected and
the staleness bound behind `binary_sensor.xpeng_<vehicle>_credentials_valid`, not a
rotation timer.

Each installation also mints its **own device identity** on first run, and that
identity is signed into the requests and embedded in the MQTT client ID. The
credentials XPENG issues are bound to it, so **never copy a credential set
between installations** - see the note in Setup.

If the login token has aged out, the refresh recognises XPENG's
`12010101`/`12010104` "Login status expired" response, renews the token, and
retries. If the *refresh* token is dead too, it escalates to reauthentication
rather than retrying forever.

### What you get

Roughly 118 entities across sensors, binary sensors, device tracker, lock,
climate, select, number, switch, button, calendar and image - battery and range,
charging state and charge limits, doors/windows/tyres, location, climate and
seat heating, sentinel/X-Guard, OTA state, and a set of remote commands.

The same telemetry can also be served to
[TeslaMate](https://github.com/teslamate-org/teslamate) as its API provider, which
logs the car as if it were a Tesla - drives, charging sessions and positions
included. It is off by default; see
[docs/TESLAMATE.md](docs/TESLAMATE.md). For a more custom setup,
[XpengMate](https://github.com/xpengtoolkit/xpengmate) is an XPENG-native fork of
TeslaMate with a history browser and 24 pre-provisioned Grafana dashboards; it
relays this provider rather than talking to the car itself.

The sensors, binary sensors, tracker, calendar and image are always created. The
**command** entities - lock, climate, select, number, switch and button - depend on
[Command permissions](#command-permissions---what-the-integration-may-do-to-the-car):
a group that is Disabled is not created at all, and one that is Read only keeps its
state but refuses its commands. A new install starts at **Safe Plus**, so OTA, the V2L
outlet and unlocking are absent until you widen it.

### Languages

The Home Assistant UI strings ship in **German, Dutch, Norwegian Bokmål, Danish,
Swedish and French** alongside English: config flow, options, entity names, repair
issues and error messages follow the language Home Assistant is set to (a restart
picks up a change). Entity ids stay English in every language - a German install
still gets `sensor.xpeng_<vehicle>_battery`, so the automations and examples in
these docs work unchanged. Config-flow dropdown labels, free-form sensor states and
this README are still English; see the [languages guide](https://xpengtoolkit.github.io/xpengtoolkit/guide/translations).
Translation files live in
[`custom_components/xpeng/translations/`](custom_components/xpeng/translations);
`python3 tools/check_translations.py` validates them.

### Consolidated controls

New entries use native covers for trunk/windows/charge port and a charging switch.
Front defrost joins the climate controller. Existing entries keep
their legacy entities until **Keep legacy buttons and duplicate sensors** is
turned off. evcc's original climate binary sensor and other typed interfaces
remain available in compact mode. See [controls and migration](docs/CONTROLS.md),
[evcc compatibility](docs/EVCC.md) and [protocol research](docs/COMMAND_RESEARCH.md).

### Reusable dashboard card

From **0.6.26**, the optional **XPENG Vehicle** card ships with the integration.
Configure at least one vehicle, then opt in under **Configure → Install and manage
the XPENG Vehicle dashboard card** (off by default). Refresh the browser and use
**Edit dashboard → Add card → XPENG Vehicle** in any dashboard.

Opting in allows the integration to register/update one shared resource and
migrate matching legacy local resources. Turning it off for every vehicle, or
removing the last opted-in vehicle, removes the managed resource. Dashboard
layouts are never edited. See the [card guide](lovelace/xpeng-vehicle-card/README.md)
for YAML resources and manual installation. Requires Home Assistant 2024.7.
A step-by-step
[tutorial](https://xpengtoolkit.github.io/xpengtoolkit/tutorials/vehicle-card)
covers enabling the card, adding it to a dashboard, choosing the car, several
vehicles and renamed entities, and troubleshooting.
Read the [0.6.26 upgrade and downgrade notes](docs/RELEASE_NOTES_0.6.26.md) before upgrading.

### Climate protocol and feedback

Independent app modes use one measured switch each, including maximum heating/
cooling, cleaning, rear defrost, Pet mode and Silent Mode. Optional support follows each car’s
capabilities; unsupported controls are disabled. Schedule services can target a
specific vehicle, report errors and refresh the calendar after edits.

Climate power and target temperature use the official app's capability-selected
commands. Vehicle replies update measured climate state; refusals and timeouts
are reported as errors. The former fan-speed mapping is withheld because it
uses the integer-temperature command. See [climate command corrections](docs/CLIMATE_COMMANDS.md).

### Notifications

XPENG's notifications do **not** arrive over MQTT and are not pushed to this
integration. They live server-side in the app's **Message Centre**, and this
integration polls that list. A new one appears within one poll interval as:

- **`sensor.<vehicle>_last_notification`** - state is the message text, with
  `count`, `title`, `category`, `msg_type`, `timestamp`, `read`, `msg_id`, and
  `delay_s` / `collected_at` in the attributes
- **`xpeng_notification`** - a bus event carrying the same flattened fields

**How late was it?** `delay_s` is `timestamp` (when XPENG recorded the message)
minus `collected_at` (when the poll saw it), so the answer is on the sensor rather
than being two timestamps to subtract by hand. It cannot be better than the poll
interval - the interval, plus however long the API took - so when `delay_s` sits
near the interval, the interval is the thing to change:

**Settings -> Devices & Services -> XPENG -> Configure -> Message Centre poll
interval**, default **300 s**, floor 60 s. Every poll is **six requests**, one per
category, so lowering it multiplies the API traffic; the floor is there because
below a minute the rate limit, not the car, would decide the delay.

The Message Centre groups messages into categories, and the API returns **one
category per request**. All six are polled, and every message is tagged with the
category it came from (`category` / `classification` attributes), so a single
sensor covers everything:

| Category | Code | Typically holds |
|---|---|---|
| Vehicle Notification | `XPMOBILE-VEH` | X-Combo, sentry, window/door events |
| XPENG Notification | `XPMOBILE-PUS` | Account and system messages |
| Charging Notification | `XPMOBILE-CHA` | Charge start/stop, completion |
| Service Notification | `XPMOBILE-SER` | Servicing and maintenance |
| Order Notification | `XPMOBILE-ORD` | Orders and purchases |
| Event Notification | `XPMOBILE-JOY` | Campaigns and events |

In practice **Vehicle Notification** is the busy one, and the rest are usually
empty - but an empty category and an unqueried category look identical over the
API (both return `[]`), so all six are queried rather than assumed.

**Triggering automations.** Either form works:

```yaml
automation:
  - alias: X-Combo notification
    triggers:
      - trigger: event
        event_type: xpeng_notification
    conditions:
      - condition: template
        value_template: "{{ trigger.event.data.category == 'XPMOBILE-VEH' }}"
    actions:
      - action: notify.persistent_notification
        data:
          message: "{{ trigger.event.data.message }}"
```

or trigger on the sensor's state directly, which is easier to build in the UI.

Two behaviours worth knowing before you rely on this:

- **Only new notifications fire.** The first poll after a restart records the
  existing inbox as a baseline *without* firing, so a restart does not replay
  the whole Message Centre into your automations.
- **Anything that arrives while Home Assistant is down is absorbed into that
  baseline** and will not trigger. If you need those, they are still in
  `recent` and the `count` attribute after startup.

## Requirements

- Home Assistant 2024.7 or newer
- A **dedicated** XPENG account - see below
- The integration's **mTLS certificates**, which you extract yourself - see
  [`custom_components/xpeng/certs/README.md`](custom_components/xpeng/certs/README.md).
  They are deliberately **not** shipped here.

## Use a dedicated account - do not use your primary account

**Create a new XPENG account and share your car to it. Do not sign this
integration in with the account you use on your phone.**

XPENG allows **one active session per account**. This integration signs in as a
second client, so it and the official app fight over that session: your phone app
gets logged out over and over, and the integration gets logged out whenever you
open the app. That is not a bug that can be fixed here - it is how the account
model behaves.

So, once:

1. Create a **new XPENG account** (a second email address works fine).
2. In the **official app**, signed in as your main account, **share the car** to
   that new account (Vehicle -> sharing / "share to another user"; the wording
   varies by region).
3. Accept the share from the new account.
4. Sign this integration in with the **new** account only.

The new account needs no phone number and is never used anywhere else. Your main
account stays logged in on your phone, and this integration keeps its own
session. If you have already pointed the integration at your primary account,
remove the entry and add it again with the dedicated one.

## Installation

This is step 1 of [the process above](#the-steps). The other four steps are
under [Setup](#setup). It downloads the code and restarts Home Assistant; **it adds
nothing under Settings** - the entry appears in step 5, or you add it yourself on
[the alternative route](#alternative-mint-the-first-pair-yourself).

### HACS (custom repository)

1. HACS -> Integrations -> three-dot menu -> **Custom repositories**
2. Add `https://github.com/xpengtoolkit/xpengtoolkit` as an **Integration**
3. Install **Xpengtk**, then restart Home Assistant

### Manual

Copy `custom_components/xpeng/` into your Home Assistant `config/custom_components/`
directory and restart. That is step 1 by hand, and like the HACS route it adds nothing
under Settings: the entry appears in step 5 when the app creates it, or
[by hand](#create-the-entry-from-those-files) once you have a pair.

## Setup

This section covers **steps 2 to 5** of the summary above; step 1 is
[Installation](#installation).

**Sign in with your dedicated account, never your primary one** (see above).

The **only** step that happens outside Home Assistant is extracting the
certificates, because that needs the APK. Everything else, including the login
that XPENG gates with its slider captcha, is done here - the solver app clears
that challenge, which is why the app route needs no desktop.

### Step 2 - extract the certificates (outside Home Assistant)

First get the repository onto the machine you are doing this on. On the GitHub
page, **Code -> Download ZIP**, then unzip it. Do not download single files out
of it - the tools import code from the integration, so the layout has to stay
intact:

```
xpengtoolkit-main/          <- GitHub names the unzipped folder like this
├── custom_components/xpeng/   the integration (HACS installs this)
└── tools/                     the scripts you run in steps 2 and 3
```

**Run every command below from that top folder.** The commonest mistake is
downloading `tools/` on its own - it will not run, and it is what makes the
`tools/...` versus `custom_components/...` paths confusing.

Then one command against an XPENG APK build **3.7.0-3.12.0** gives you the three
PEM files:

```bash
pip install pyjks cryptography
python3 tools/xpeng_apk_certs.py --apk XPENG_3.12.0_APKPure.xapk --out ./certs
```

That writes `ca-chain.crt`, `client-cert-0.pem` and `client-key.pem`, and checks
that the key matches the certificate before declaring success. Exit code `2`
means it found the certificate but could not recover the key - which is what a
3.13.0-or-newer package looks like, so use an older build.

Full detail: [`custom_components/xpeng/certs/README.md`](custom_components/xpeng/certs/README.md)
which also carries the extraction steps for each platform.
On Windows, [`docs/WINDOWS_WSL_SETUP.md`](docs/WINDOWS_WSL_SETUP.md) walks this
whole process through WSL.

### Step 3 - copy the certificates onto the Home Assistant host

Step 2 ran on your own computer; the integration runs on Home Assistant, so the
three files have to get there. Put them **outside** the integration folder:

```
/config/xpeng-certs/
├── ca-chain.crt
├── client-cert-0.pem
└── client-key.pem
```

`/config/xpeng-certs` is what the rest of this README assumes. Any path works
except `custom_components/xpeng/certs/`: that is the default, but it sits inside
the integration, which an update replaces - your keys would go with it.

Getting them there is up to you and your setup: the **Samba share**, the **File
editor** app, `scp`, or anything else that can write to `/config`. Then check they
arrived:

```bash
ls -l /config/xpeng-certs      # three files
```

These are live credentials, so set the directory to `700` and the files to `600`,
and do not leave the folder exposed in a share.

**Nothing needs configuring for either location.** The integration looks for
`ca-chain.crt` in `/config/xpeng-certs`, then in its own `certs/` directory, and
uses the first one that has it. Only if you keep the three files somewhere else do
you need to say so: *Settings -> Devices & Services -> XPENG -> Configure ->
**Certificate directory***. That form validates the directory, and refuses to save
unless all three files are in it.


### Alternative: mint the first pair yourself

Take this route if you would rather not hand the account password to an app before you
have seen the integration work - or if you cannot run the solver. It needs a desktop
machine (Mac/Linux/Windows with Node and a Chrome install), and it replaces **steps
4-5** with the two below: you mint the pair, then create the entry from it by hand.
With no app installed nothing renews it, so that stays yours to do until you come back
and install the apps at step 4.

Run this from the top folder of the repository you unzipped in step 2. It opens a
browser window; **drag the slider once**. It then writes two files:

```bash
python3 tools/onboard_email_login.py \
    --email YOUR_SECONDARY_EMAIL --save --mqtt
```

It writes **`xpeng_tokens.json`** and **`xpeng_tenant.json`** next to the integration
files the run used - `custom_components/xpeng/` in a checkout. Note the plural -
`xpeng_tokens.json`. The exception is a run **on the Home Assistant host against the
HACS-managed copy**: HACS replaces `/config/custom_components/xpeng` on every update, so
a pair written there would not survive one. The tool detects that directory and writes
to `/config/xpeng-secrets/` instead - the same directory the renewal app publishes to,
and the first one the import form looks in. `--out-dir PATH` chooses the destination
explicitly, and the tool prints where it wrote either way.

See [`tools/README.md`](tools/README.md) for dependencies and options.

#### Skipping the drag entirely

That slider gesture is the one human step, and it is also the reason an expired
session cannot renew itself. A hosted solver removes it:

```bash
python3 tools/onboard_email_login.py \
    --email YOUR_SECONDARY_EMAIL \
    --captcha-solver http://your-solver-host:8080 --save --mqtt
```

With `--captcha-solver` no browser opens here: the tool asks the service to clear
the slider and gets the same `validate` a human drag would produce. Everything
after that - the `drag/validate` POST, the guardian token, the `pwdLogin` retry -
is unchanged. Only `guardian_token.mjs` is still needed locally, so a caller that
only has the solver can run without Playwright or Chrome at all.

The service is self-hosted and separate: see
[xpengtoolkit/slider-solver](https://github.com/xpengtoolkit/slider-solver). There are two
ways to run the **same container**, and they are alternatives rather than two components:
as a Home Assistant **app**, which is the usual route on HAOS and what
[Unattended renewal](docs/UNATTENDED_RENEWAL.md) installs, or as a **standalone Docker
image** on any other host (`docker run -e SOLVER_TOKEN=... -p 8080:8080`) if you are not
on HAOS or Supervised. It needs no human: it measures the slider and submits the answer
itself. It solves the account owner's own challenge on a host they control - keep it
private, and never expose it without `SOLVER_TOKEN`.

That is what makes renewal unattended: with the whole flow headless, an install
can mint fresh tokens before its session ends rather than waiting for the Repairs
card. Sessions last about two hours from login on the installs we measured, so a
renewal scheduled comfortably inside that window keeps an install alive indefinitely -
and one install has since run for days without an expiry, so the trigger is not simply
elapsed time.

Two things happen here that people do not expect:

- **This is where your device identity is created.** The tool mints a random
  32-hex id for this install and stores it in the tenant. You do not choose it,
  type it, or need to know it - but it is why the two files belong together.
- The tool also stores the **tenant**: the key pool your tokens are sealed with.
  It is not reproducible, so it must be imported alongside the tokens.

Why this step exists, and cannot be folded into Home Assistant: the only
human-only part of XPENG's login is that slider gesture. Everything else is
plain HTTP.

#### Create the entry from those files

**Settings -> Devices & Services -> Add Integration -> Xpengtk**, then choose
**Import tokens** and paste the contents of both files: open each one in a text editor
and copy the whole thing, JSON wrapper and all. The paths are read inside the Home
Assistant container, so they cannot see a file sitting on your desktop - pasting is what
works here.

No captcha, no password, and it works for accounts that are captcha-gated. The
integration **adopts the device id from the imported tenant** rather than minting a new
one, so if you ever see it mint a fresh id instead, the tenant did not come through and
the credentials will be rejected.

### Step 4 - install the two apps

This is the part that makes renewal unattended, and it is where the two companion
apps come in:

* **[XPENG slider solver](https://github.com/xpengtoolkit/slider-solver)** - clears
  the login captcha, which is the one human step in onboarding.
* **XPENG credential renewal** - this repository. It mints a fresh pair ahead of the
  two-hour cap and imports it with `xpeng.import_credentials`, the same service the
  Repairs card uses.

**Settings -> Apps -> Install app -> three-dot menu -> Repositories**, add this
repository's URL and the solver's, then install both. (Home Assistant renamed
add-ons to **apps** in 2026.2; older releases call the same screen the Add-on
Store.)

Then configure the renewal app:

| Option | Value |
|---|---|
| `email` / `password` | the dedicated account - never the one signed into the phone app |
| `renew_token` | any secret. **Set it** - the worker holds your password, and this is what the automation presents |
| `solver_url` | the default if the solver came from the store; otherwise see the [solver_url table](docs/UNATTENDED_RENEWAL.md#if-solver_url-does-not-resolve) |
| `solver_token` | the solver's token, if you set one |
| `device_id` | leave blank and the first renewal pins it, or set it yourself. Do not change it afterwards - [it is the stale-client trap](#most-entities-are-unknown) |
| `pair_dir` | `/config/xpeng-secrets` by default. `chmod 700` it |
| `command_mode` | what the integration may do to the car, applied when the app creates the entry on a fresh install: `read_only`, `safe`, `safe_plus` (**default**), or `full` (`charging` is the old name for `safe_plus`). See [Command permissions](#command-permissions---what-the-integration-may-do-to-the-car) |

The pair directory is live credentials on disk: `chmod 700`, and keep it out of any
share.

#### Standalone Docker (no Supervisor)

Home Assistant **apps** (formerly add-ons) exist only on HAOS and Supervised - the
Add-on Store does not exist on Container or Core. Both companions still work there,
because an app is an ordinary container with an options file. Build the two images
once (no prebuilt images are published; the Supervisor normally builds them on an
HAOS box):

```bash
git clone https://github.com/xpengtoolkit/slider-solver
git clone https://github.com/xpengtoolkit/xpengtoolkit
docker build -t xpeng-slider-solver ./slider-solver/slider_solver
docker build -t xpeng-renew ./xpengtoolkit/xpeng_renew
```

```yaml
services:
  xpeng-slider-solver:
    image: xpeng-slider-solver
    restart: unless-stopped
    ports: ["8080:8080"]
    environment:
      SOLVER_TOKEN: "change-me"                  # same value in the renewal app

  xpeng-renew:
    image: xpeng-renew
    restart: unless-stopped
    depends_on: [xpeng-slider-solver]
    ports: ["8090:8090"]                         # POST /renew, GET /healthz
    environment:
      EMAIL: "dedicated-account@example.com"     # never the phone-app account
      PASSWORD: "that account's password"
      SOLVER_URL: "http://xpeng-slider-solver:8080"
      SOLVER_TOKEN: "change-me"
      RENEW_TOKEN: "a-second-secret"             # bearer for POST /renew
      PAIR_DIR: "/config/xpeng-secrets"
      CORE_API: "http://homeassistant:8123/api"
      SUPERVISOR_TOKEN: "<Home Assistant long-lived token>"
      COMMAND_MODE: "safe_plus"
    volumes:
      - ./ha-config/xpeng-secrets:/config/xpeng-secrets
      - ./ha-config/xpeng-certs:/config/xpeng-certs:ro
      - xpeng-renew-data:/data

volumes:
  xpeng-renew-data:
```

The app's options become upper-case environment variables. Two of them replace
Supervisor plumbing: `CORE_API` (default `http://supervisor/core/api`) points at the
Core API, and `SUPERVISOR_TOKEN` is just the bearer - a **long-lived access token
from an admin profile**, which also lets a fresh install create the entry by driving
the config flow. Three mounts have to be right:

* **`pair_dir` must be the same absolute path inside the Home Assistant container.**
  The pair is not uploaded: `xpeng.import_credentials` is called with
  `tokens_path`/`tenant_path` and Core reads those files itself, so mount the host
  directory into *both* containers at the same path. `chmod 700` it - live
  credentials.
* **Certificates** at `/config/xpeng-certs` (or
  `/config/custom_components/xpeng/certs`), read-only. The bundled onboarding code
  looks in both.
* **`/data`** must be writable: the device identity is pinned in
  `/data/device_id.txt`, and a remembered identity outlasts a freshly generated one.

Everything else is unchanged: `/healthz`, `POST /renew` with `RENEW_TOKEN`, and the
6000 s scheduler (`RENEW_INTERVAL=0` disables it, for triggering from cron or an HA
`rest_command`). There is no sidebar panel outside HAOS - that is an app ingress
feature. The walkthrough and the full environment-variable table are in the docs:
[Standalone Docker](https://xpengtoolkit.github.io/xpengtoolkit/guide/unattended-renewal#standalone-docker)
and [App options](https://xpengtoolkit.github.io/xpengtoolkit/reference/app-options#environment-standalone-docker).

### Step 5 - watch it appear

There is nothing to do here. When the app starts it looks for the integration, finds
no entry yet, mints a pair (the solver clears the slider), and creates the entry from
that pair. Within a minute or so **Settings -> Devices & Services** shows
`XPENG <last six of your VIN>` and the entities fill in.

**Moving from an older copy? Remove its entry rather than disabling it.** Home Assistant
hides disabled entries from **Settings -> Devices & Services**, but the disabled entry
still holds the VIN as its `unique_id`, so the flow refuses to create the replacement -
and the app's own *is there an entry?* check counted it too, which is why 0.1.17 of the
app treats a disabled entry as no entry and bootstraps anyway.

The entry is created with the command permissions the app was configured with
(`command_mode`, Safe Plus unless you changed it). Answering the same question by
hand - **Settings -> Devices & Services -> XPENG -> Configure -> Command
permissions** - asks it as a preset, with a *Custom* option for the per-group matrix.
See [Command permissions](#command-permissions---what-the-integration-may-do-to-the-car).

From then on it renews itself - every `renew_interval` (6000s by default, comfortably
inside the two-hour cap), and sooner if Home Assistant reports the credentials are
already invalid. There is no automation to add and no helper to create:
`sensor.xpeng_<last six of your VIN>_last_renewal` is that clock as an entity, stamped by
every renewal and kept in the config entry, so it survives a restart and can go straight
on a dashboard.

`GET http://<your-ha-host>:8090/healthz` reports the counters, when the next renewal
is due and the last outcome; `/config/xpeng-renew-last-run.log` has the whole of the
last attempt. To force one now:

```bash
curl -X POST -H "Authorization: Bearer <renew_token>" http://<your-ha-host>:8090/renew
```

If nothing appears, the run log says why. The usual cause is a `solver_url` that does
not resolve - see [the table](docs/UNATTENDED_RENEWAL.md#if-solver_url-does-not-resolve).

If you would rather Home Assistant drove renewals - for a last-run helper you can put
on a dashboard, or to trigger them yourself - set **`renew_interval: 0`** on the app and
use the [optional automation](docs/UNATTENDED_RENEWAL.md#3-home-assistant-side-optional).

### If you are asked to re-authenticate later

The integration renews its login token on its own. If it cannot - for example
the refresh token expired because the car was offline for a long time - it says
so in **Settings -> Repairs**, with a card named after the vehicle:

> **XPENG login expired for XPENG 123456 (VIN L1NNS…)**

The card explains the situation and offers a **Fix** button that opens the
import form directly, so a fresh pair can be pasted without hunting through
settings. What it asks for:

1. Re-run the onboarding tool from [the alternative](#alternative-mint-the-first-pair-yourself)
   and drag the slider once.
2. Paste the new `xpeng_tokens.json` and `xpeng_tenant.json` into the card.

**Import the pair you just produced, and only that one.** Every successful
refresh rotates the refresh token, so any older `xpeng_tokens.json` - including
the one that was working yesterday - is already retired. Re-importing it does
not bring the old session back, it evicts the live one, which looks exactly like
"the fix did not work". One run of the tool, imported once.

If the card is not there - it is deleted when a fix is submitted, and an update
does not put it back - use Home Assistant's own prompt instead: the
**Authentication expired for XPENG …** notification opens the same import form
for an entry that was created by **Import tokens**. Both routes update the
existing entry; there is no need to delete and re-add it.

The card clears itself once the new pair works. The in-app email/password
re-auth cannot work, because it hits the same captcha.

The certificates from step 2 do **not** need redoing unless XPENG rotates them,
which shows up as a TLS or broker authentication failure rather than a login
failure.

### Notes

Each installation mints its **own device identity**, and the credentials issued
are bound to it, so a pair belongs to whichever machine and
install it was produced for. That is why the pair is imported together - the
tenant records the identity the tokens were sealed against. Do not mix tokens
from one run with a tenant from another.

### The message centre uses a different certificate authority

Worth knowing if you ever chase a notification problem. `apps-global.xiaopeng.com`
is signed by the XPENG CA chain shipped with this integration, so that endpoint is
verified against the pinned chain alone.

`msgcenter-oversea-eur.xiaopeng.com` is **not** - it sits behind Aliyun's WAF and
serves a public certificate. The integration therefore adds the system CA store
*in addition to* the pinned chain for that endpoint only. Pinning it to the XPENG
chain as well made every message-centre call fail with

```
SSL: CERTIFICATE_VERIFY_FAILED - unable to get local issuer certificate
```

while the vehicle API kept working, which is a confusing combination to debug.
Verification is not weakened for anything else: `check_hostname` and
`CERT_REQUIRED` still apply, and the XPENG-chain endpoints are still pinned.

### Some data is gated to the owner account

XPENG returns different data depending on the account's relationship to the car,
and it does not always say so:

| Data | Behaviour on a secondary (shared) account |
|---|---|
| Energy consumption | Endpoint returns **HTTP 200 with `0.0`** - no error at all |
| Location | Still delivered - the **app** hides it, the API does not |
| Some endpoints | `5000000 用户无权限` / `用户不可见` ("no permission" / "not visible") |

Measured 2026-10-09: the owner's app showed **`16.9 kWh/100km` - 7-day average
energy consumption**, while `/flow/cv2/app/vehicle/energy/simpleReport` returned
exactly `avgTripEnergyPowerOf7Days: 0.0` for the secondary account.

**So a `0.0` on the energy sensor is not proof of zero consumption.** That sensor
carries a `zero_may_mean_restricted` attribute when the value is zero, saying so
explicitly. If you care about consumption figures, onboard with the **owner**
account.

Worth knowing in the other direction too: location is *not* gated by the API, so
this integration will show a car's position even when the app shows it only to
the owner. That is a deliberate consequence of using the API rather than the UI,
not a bug - but be aware of it if the car is shared.

### Which account is this?

There is a diagnostic **Account** sensor showing the XPENG account the stored
credentials belong to. It is useful when you run more than one account, or after
a re-onboard, to confirm which one is actually connected.

The tokens cannot answer this themselves — an access token is a tenant-sealed
blob that contains only the teeId and this install's device id, and the tenant
holds keys rather than account details. So the email is recorded at the moment it
is known: the onboarding tool writes it into the token file, and the import step
stores it on the config entry, which is what survives a reboot.

Entries imported before the tool recorded it have no email; the sensor then shows
the numeric account id and an attribute explaining that re-running the onboarding
tool (or filling in the field on the **Import tokens** form) will supply it.

Note the value is the full account address, which is the point of showing it — so
it will appear in dashboards and in the recorder database.

## Unattended renewal

XPENG retires a session about two hours after the login that created it on the installs
we measured - not invariably, one has run for days - and a refresh cannot extend it: the
refresh loop keeps working right up to the moment the server ends the session. Two apps take the human out of the loop:

* **[XPENG slider solver](https://github.com/xpengtoolkit/slider-solver)** - solves
  the login captcha. That slider is the only human step in onboarding, so this is
  what makes an automated login possible at all.
* **XPENG credential renewal** - this repository. It mints a fresh pair with the
  toolkit's own login code and imports it through the integration's
  `xpeng.import_credentials` service, which validates the pair by minting MQTT
  credentials, exactly as the Repairs card does.

[Steps 4-5](#step-4---install-the-two-apps) install the apps and mint the first
pair with the solver, and it renews itself from then on. Three things are easy to miss:

- **The first run creates the integration entry.** On a fresh install there is nothing
  to import into, so the app drives the config flow and creates the entry from the pair
  it just minted - nothing is pasted, and there is no failure to expect. See
  [`docs/UNATTENDED_RENEWAL.md` §2](docs/UNATTENDED_RENEWAL.md#2-first-run-the-app-creates-the-config-entry).
- **Set `solver_url` if the solver was not installed from the store.** The default
  names the store install's container hostname
  (`http://571f4c18-xpeng-slider-solver:8080`). A local-folder install is
  `http://local-xpeng-slider-solver:8080`, and the Home Assistant host's own
  address always works. The underscore slug form (`local_xpeng_slider_solver`)
  does **not** resolve - hyphens do.
- **Keep one `device_id`.** Set it once and leave it; changing it on a live
  install is the stale-client trap in [Troubleshooting](#troubleshooting).

**Both apps hold credentials** - set their tokens, and keep the pair directory out
of any share.

The service is useful by hand too: a credential swap no longer has to wait for the
Repairs card to appear first (it is raised only after the broker refuses, or on the
hourly poll - up to an hour late).

```yaml
service: xpeng.import_credentials
data:
  tokens_path: /config/xpeng-secrets/xpeng_tokens.json
  tenant_path: /config/xpeng-secrets/xpeng_tenant.json
```

## Troubleshooting

### Most entities are `unknown`

The usual cause, and the one worth ruling out first, is that **two MQTT clients are
connected under one XPENG account.** The broker authorises one and refuses the other as
`Not authorized`; every refusal triggers a credential refresh and a client rebuild,
and each rebuild resets the entities - so values appear, vanish, and the count
flaps around instead of settling.

```
XPENG MQTT connection refused: rc=Not authorized (rc_str=Not authorized)
```

The usual way in is **changing `device_id` on an install that already had one**.
That changes the MQTT client id, and the client still using the old one is not
always torn down - so two clients for one account end up fighting.

**Fix: restart Home Assistant.** A config-entry reload does *not* clear it. The fix
in the integration (0.4.6) stops a previous client when an entry is set up again,
but if you are already in this state, only a restart clears the one that is loose.

Two things that reliably mislead when you are debugging this:

- **`sensor.<vehicle>_mqtt_listener` reads `No messages` on a perfectly healthy
  install**, because vehicle data arrives over the REST poll rather than that
  topic. Do not read it as "MQTT is dead, so there is no data".
- **Counting client ids in the log tells you about history, not about now.** To
  see which client is live, compare the *newest* timestamp per client id instead
  of how many appear.

### `Polling status` is `Silent` on a parked car

`Silent` means "connected, but nothing has arrived for longer than the silence
threshold" - the state the watchdog reconnects for. It is not the same as
`Parked`, which is the normal quiet state. Because the parked poll interval is
measured in hours while the silence threshold is minutes, a parked car can sit in
`Silent` and have the watchdog reconnect between polls; a value that returns to
`Parked` is not a problem in itself. If a car you know is awake stays `Silent`
alongside `Not authorized` refusals, treat it as the case above.

### The car's location is stale after parking, or after a drive

The tracker only knows where the car last **told** it. Position arrives in the car's
own telemetry (`interval.drive.latitude/longitude`) - never from the REST poll - and
a car that has gone to sleep stops sending it. A parked car is deliberately never
force-woken on a schedule, so the last position is kept until something makes the
car report again:

- **The car wakes by itself** (a charge, a command, its own schedule) and the
  position updates.
- **Press `Wake vehicle`** - that request makes the car upload a full state. `Force
  update` will not help: it asks passively, and a sleeping car answers with a stub.
- **The silence watchdog** does it once automatically after 30 minutes without data,
  capped at one force-upload per 12 hours (0.6.20+ adds a second allowance while the
  car was moving). If that budget was spent earlier, a stalled session can leave the
  map stale until you wake it.

Two attributes on the tracker answer "is this position real?": `coord_valid` is the
car's own GPS-validity flag, and `fix_age_s` is the age of the last position that was
an actual fix. Since 0.6.24, a frame whose coordinates are the car's last known
position (`coordValid` false) no longer moves the car - the last valid position is
kept and flagged, instead of a stale coordinate being published as a live one.

If the position did not move **even while driving**, the frames were not arriving at
all: XPENG's cloud push fan-out stalls while the socket still looks healthy, and
`Last data update` will be stale too. Enable debug logging for
`custom_components.xpeng` and look for `Drive evidence:` lines - `gps=` says whether
a frame carried coordinates and `coord_valid=` whether the car considered them a
current fix.

### The battery percentage and the range flip between two values

A sawtooth on the battery (or range) history - alternating by a percent or two
on a fixed rhythm - is a data artefact, not the pack. State of charge is
reported to whole percent, and while the push stream is quiet two readings of it
exist: the last one a push carried, and the live one from the REST widget
fallback. Before 0.6.25 a charge-less MQTT frame re-broadcast the frozen push
value over the fallback's merge, so the sensors ping-ponged. Now:

- the fallback merge is what the MQTT client's partial-merge baseline holds, so
  a later ping cannot undo it;
- a state whose SoC is more than one percent above what is held, while the car
  is parked and not charging, is refused as an older snapshot - a parked, idle
  car's charge can only fall - and it is refused as *data*, so a fan-out that
  only resends it still ages into the watchdog's force-upload.

Watch for `Ignoring a vehicle state from the push fanout: SOC ... while parked`
in the log; what is left on the graph after that is the real trend at
whole-percent resolution. A rise of more than one percent while parked and not
charging does not happen - if one appears, that line names it.

### Nothing is updating, and the log does not say why

- **The renewal app records the whole onboarding run** - every step, plus whatever
  the browser or the API said - in `/config/xpeng-renew-last-run.log`, rewritten on
  every run. It is the first place to look, and it describes the account's login
  attempts, so treat it as sensitive.
- **`GET http://<your-ha-host>:8090/healthz`** reports renewals, failures and the
  last outcome, including what `xpeng.import_credentials` returned.
- **The Home Assistant log** shows `homeassistant.components.rest_command` errors,
  which name the status the renewal app answered with.

Note that Home Assistant 2026.10 no longer exposes persistent notifications as
`persistent_notification.*` entities, so a template that watches for the renewal
app's failure notification will not find it - look in the notifications panel.

### The seat heating turns on the wrong seat

Fixed in **0.4.12**, and worth knowing on an older build: seat commands carry
`cmd_type` **1 = driver, 0 = passenger**, and the two were swapped. The values had been
assigned on a right-hand-drive car - where the driver's seat is the right one - by
reading which state field moved rather than the car's own meaning, so in a
left-hand-drive car "passenger" heated the driver's seat.

### Seat heating: what `mode` means, and the 7-50 second trap

For `service_type 30`, `cmd_value` selects the **function** (1 = heating, 2 = ventilation)
and `mode` selects the **level** (0 = off, 1/2/3 = bar 1/2/3). This was got wrong twice,
in a way worth recording, because the wrong reading is the more convincing one:

- an earlier measurement read `mode` as a *momentary press* that toggles - "the same
  payload sent three times read back `1, 0, 1`" - and so concluded `mode 3` turned the
  heating **off**, and stopped offering a third level;
- every one of those readings was taken about 2 s after the send, and a seat command
  takes **7-50 s** to be applied and reported (`43, 45, 46, 50 s` on the rear seats of a
  parked car). They were showing the state from *before* the command arrived;
- and because only the *heating* side was re-measured when that was corrected, ventilation
  kept two levels for a release longer than it should have. `cmd_type 1, mode 3,
  cmd_value 2` reads back 3 on fresh data exactly as heating does.

Measured properly, `mode 3` from off reports 3, for both functions. The select entities
still turn the seat off before selecting a level, because that is the only order measured
to reproduce and a level change *from another level* (2 -> 3) has never been measured.

| Entity | Options | State |
|---|---|---|
| Driver / passenger **seat heating** | `Off`, `Heat 1`, `Heat 2`, `Heat 3` | reads `seat.driver_seat_heat` |
| Driver / passenger **seat ventilation** | `Off`, `Ventilation 1`, `Ventilation 2`, `Ventilation 3` | reads `seat.driver_seat_ventilation` |
| Left / right **rear** seat heating | `Off`, `Heat 1`, `Heat 2`, `Heat 3` | `cmd_type` 3 / 4, reads `seat.left_rear_seat_heat` |
| Left / right **rear** seat ventilation | `Off`, `Ventilation 1`, `Ventilation 2`, `Ventilation 3` | `cmd_type` 3 / 4, reads `seat.left_rear_seat_ventilation` - **registered disabled**, because the hardware is optional; enable it if your car has it |
| **Steering wheel heating** | `Off`, `Low`, `Medium`, `High` | service 31, its own value space, unmeasured |

> **Corrected 2026-10-14, and one bug left open.** The rear seats' `cmd_type` values *are*
> known (`3` = left rear, `4` = right rear), so the rear seats are controls now rather
> than sensors only, and `mode 3` is a real third level. What is still broken: the
> **passenger's** heating cannot be switched off - `cmd_type 0, mode 0, cmd_value 1` is
> refused with `code 8` ("Nicht unterstützt für dieses Fahrzeug") - and nothing measured
> turns it off, so that entity's `Off` does nothing rather than being guessed at. See
> [`docs/SEAT_COMMANDS.md`](docs/SEAT_COMMANDS.md) for the measurements and the evidence.

### A sleeping car, and why a seat command can look like it did nothing

The car reports its state only when something wakes it (`service_type 19`, `cmd_type 2`
forces an upload), and it goes back to sleep within a minute or two. While it is asleep a
command may still be accepted and applied - the cloud answers `Erfolgreich` - but the
state fields will not change until the next wake. Two traps follow, and both cost real
time here:

- **`last_data_update` is the freshness signal, not the seat sensors' `last_updated`.**
  Home Assistant only moves `last_updated` when a state's *value* changes, so a seat
  sensor that has been `0` all along keeps an old timestamp no matter how much data
  arrives. An old seat timestamp is not evidence that nothing arrived.
- **Wake before concluding anything.** If `sensor.<vehicle>_last_data_update` is old,
  wake the car and read again: more than one "failed" command in testing had in fact
  been applied, with the stale reading taken for a failure.

This is also what "most values only propagated after pressing Wake vehicle" means: the
values were not late, the car was asleep.

`cmd_value 0` is **not** off - it returns `Nicht unterstützt für dieses Fahrzeug`
("not supported for this vehicle"), which is the error you get pressing Off on a build
with the old map.

### The energy sensor reads `0.0`

That is usually not zero consumption but a restricted endpoint on a shared account
- see [Some data is gated to the owner account](#some-data-is-gated-to-the-owner-account).

## Command permissions - what the integration may do to the car

Every command this integration can send belongs to one **capability group**, and
each group is set to **Full** (commands run), **Read only** (the entities and their
state stay, commands are refused) or **Disabled** (the entities are not created at
all). The setup screen asks which of four presets you want (**Safe Plus** by default);
**Configure -> Command permissions** changes it later, and *Custom* there opens the
per-group matrix. An install that predates this option keeps **Full access** until
someone changes it - the new default applies to new entries only, so an upgrade never
takes a capability away from an existing automation.

The full reference - every group, the services and entities it covers, how to set it,
and what it does *not* protect against - is in
**[docs/COMMAND_PERMISSIONS.md](docs/COMMAND_PERMISSIONS.md)**.

| Preset | What it allows |
|---|---|
| **Read only** | Status and queries only - nothing that actuates the car. |
| **Safe** | The above, plus comfort and climate, lights, and the *securing* actions: locking, closing windows and the trunk, arming X-Guard. |
| **Safe Plus** *(default)* | Safe, plus charging (start/stop, charge limit, both port doors) and the V2L outlet. This is what a new install starts at: the car can be watched, pre-conditioned and charged, but not unlocked, opened, re-flashed or commanded raw. |
| **Full access** | Everything: unlocking, opening the trunk or windows, disarming X-Guard, firmware/OTA and raw commands. What the integration always did before this option existed. |

Groups are split by **what an action does to the car**, not by subsystem. That is
why the door lock, the windows, the trunk and X-Guard each appear twice: locking,
closing and arming only ever *reduce* access, so they are safe to allow, while
unlocking, opening and disarming hand the car over. A "Safe" install can therefore
lock itself, and a leaked token still cannot open the doors.

The groups are: queries and data; comfort and climate; lights and alerts; charging;
securing actions; access actions; accessory power; firmware and OTA; and advanced and raw
commands. [docs/COMMAND_PERMISSIONS.md](docs/COMMAND_PERMISSIONS.md#capability-groups)
lists which services and entities fall in each - the table there is generated from the
code and a test fails if the two disagree.

**Allow wake** is a separate toggle. Waking a sleeping car costs a little of its 12V
battery and is only used to pull a fresh full state; with it off the integration is
strictly passive and sends queries only, at the price of staler sensors. The internal
watchdog honours it too, degrading to a passive query instead of a force-upload.

**Window and charge-port controls are separate.** Window opening vents. The
0.6.26 release uses the official app's service-12 close command. A P7+ owner
confirmed physical ventilation and closing on 22 October 2026. Full opening and arbitrary positions are not advertised.
Manual charge flaps retain state without motorized commands when known from
model configuration or when the owner turns off **Motorized charge-port controls**.

### What this does *not* protect against

Be clear about the boundary, because the reassuring version would be a lie:

- It does **not** stop an administrator. Whoever can open **Configure** can change
  the mode, and whoever can read or write your Home Assistant configuration files
  can change it without you.
- It is **not** a per-user permission. Home Assistant's own user/groups permissions
  cover entities you already expose; XPENG's commands are *target-less* services
  (there is no `entity_id` in `xpeng.unlock` for Home Assistant to check), so the
  integration also verifies that a logged-in caller may control at least one XPENG
  entity. That check is what a non-admin user meets; the mode is what everything
  else - automations, scripts, voice assistants, dashboards, long-lived tokens -
  meets. A leaked token is the likeliest way in, which is why the ceiling applies to
  callers with no user at all as well.
- The **account credentials are the same at every level.** The mode limits what this
  integration will *do*, not what the account *could* do.

What it does buy: an assistant or dashboard that cannot unlock your car, an
automation that cannot fire a raw command by accident, and a token that stops being
a set of car keys.

### A denied command is not silent

Refusals raise with the group, the state and where to change it, log one warning per
command, and fire an `xpeng_command_denied` event - so a blocked attempt can be alerted
on rather than discovered later. The event fields and a ready-made automation are in
[docs/COMMAND_PERMISSIONS.md](docs/COMMAND_PERMISSIONS.md#when-a-command-is-refused).

## Options

- **Certificate directory** - where your mTLS files live (defaults to the
  integration's `certs/` folder)
- **Command permissions** - the presets (Safe Plus by default for a new entry, Full
  access for an entry that predates the option) or the per-group matrix described above
- **Allow wake** - whether the integration may wake a sleeping car
- **Motorized charge-port controls** - on by default. Turn it off for a car with a
  manual charge flap: the open/close charge-port buttons and services are then not
  offered, because the car cannot act on them; the charge-port state sensor is
  unchanged. Reported against a P7+ (2026-10-22)
- **Usable battery capacity** - the pack size behind the parked-consumption estimate,
  in kWh, asked on the setup screen as well. A VIN identifies the model *family*, not
  the pack, so this is a choice: G6 65.3 / 67.8 / 80.0 / **87.5 (default)** and
  G9 75.8 / 78.2 / 92.2 / 93.1 usable kWh. The
  [options page](https://xpengtoolkit.github.io/xpengtoolkit/reference/options#usable_battery_kwh)
  carries the same table with each pack's installed figure and variant. The
  TeslaMate provider's charge-energy estimate uses it too
- **TeslaMate API provider** - off by default. Runs a Tesla-compatible API on the
  chosen port (default `8200`) with a generated bearer token, so TeslaMate can log
  this car. Read-only. Setup and the exact TeslaMate environment variables are in
  [docs/TESLAMATE.md](docs/TESLAMATE.md)

## Disclaimer

Not affiliated with, endorsed by, or supported by XPENG. Built by
reverse-engineering the official mobile app; it may break at any app release.
Use your own account and credentials, at your own risk. A third-party client may
affect the official app's live view.
