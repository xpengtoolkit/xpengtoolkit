# Unattended renewal

XPENG retires a session about two hours after the login that created it, and a
refresh cannot extend it (measured: 2h12m, 2h03m, ~2h, on two accounts, with the
refresh loop healthy right up to the moment the server ended it). One install has
since run for **days** without an expiry, so treat the two hours as the common case
rather than a rule, and see [the device identity](#4-the-device-identity). So an install
that is meant to run for weeks has to *log in again* before that happens — which
normally needs a human to drag a slider.

Two apps remove the human, and an automation decides when:

```
automation (schedule)  ->  rest_command  ->  XPENG credential renewal (worker)
                                              |  POST /solve   -> slider solver
                                              |  pwdLogin + guardian -> new pair
                                              `  xpeng.import_credentials (service)
```

The automation is the clock and the policy. The worker holds the account password
and does the privileged part. The integration validates the result before anything
is swapped, exactly as the Repairs card does.

> Home Assistant renamed add-ons to **apps** in 2026.2. The store, the options and
> everything below are the same thing under the new name.

## 1. Install the two apps

Both are needed, and both are ordinary apps:

* **XPENG slider solver** — `xpengtoolkit/slider-solver`, solves the login captcha.
* **XPENG credential renewal** — this repository. Settings -> Apps -> Install app
  -> three-dot menu -> Repositories -> add
  `https://github.com/xpengtoolkit/xpengtoolkit`, then install it.

A second car on its own XPENG account installs the repository's second slot,
**XPENG credential renewal (2nd car)**: the same worker and the same published image,
with host port `8091` and `pair_dir` `/config/xpeng-secrets-2` as its defaults. Give
it the second account's email and password and leave `entry_id` empty — the pair's VIN
selects its own config entry. The first slot's defaults are port `8090` and
`/config/xpeng-secrets`.

Configure the worker:

| Option | Meaning |
|---|---|
| `email` / `password` | the account this install renews. A second account, never the one in the app: XPENG keeps one session per account, so the app logging in evicts this one. |
| `device_id` | optional, and worth pinning — see [the device identity](#4-the-device-identity) below. Leave empty and the first renewal's device id is remembered and reused, so the identity at least does not change every two hours. |
| `solver_url` | where the solver app listens. Defaults to `http://571f4c18-xpeng-slider-solver:8080`, the solver's container hostname as installed from the store. |
| `solver_token` | the solver's `solver_token`, if you set one. |
| `renew_token` | what the automation presents to `POST /renew`. **Set this** — the worker holds your password. |
| `notify_on_success` | send a persistent notification after every successful renewal (default `true`). Failures still notify after two consecutive attempts. |
| `pair_dir` | where the fresh pair is written for Home Assistant to read; `/config/xpeng-secrets` by default. |
| `entry_id` | target one config entry by its id instead of the entry whose title matches this pair's VIN. Only needed when more than one XPENG entry exists — two cars on two accounts — or when the entry was renamed. A pair whose VIN matches no entry creates a new one rather than landing in the only existing entry. |
| `command_mode` | what the integration may do to the car when this app creates the entry (`read_only`, `safe`, `safe_plus`, `full`; `charging` is the old name for `safe_plus`). Defaults to `safe_plus` - see the README's Command permissions section |

Give the pair directory a second thought: it is live credentials on disk, readable
by the Core process. `chmod 700` on the directory, and do not put it in a share.

### If `solver_url` does not resolve

This is the one setting that depends on how the solver was installed, and getting
it wrong looks like "the slider never gets solved":

| How the solver was installed | `solver_url` |
|---|---|
| from the store, `xpengtoolkit/slider-solver` | `http://571f4c18-xpeng-slider-solver:8080` (the default) |
| as a local app folder named `slider_solver` | `http://local-xpeng-slider-solver:8080` |
| anything else | `http://<your-ha-host-ip>:8080`, e.g. `http://192.168.1.10:8080` |

**It is the hostname that resolves, not the slug** — hyphens, not underscores.
The slug forms (`571f4c18_xpeng_slider_solver`,
`local_xpeng_slider_solver`) fail with `No address associated with hostname`;
those were the names people were told to use, and the shipped default, which is
why this is worth stating plainly. The plain LAN address always works, so it is
the safe answer when in doubt. Changing this option restarts the app.

**The sidebar panel is not the API.** `solver_url` is wherever `/solve` lives: the
solver's own port, **8080**. The ingress panel URL the Home Assistant UI shows you
(`http://<your-ha-host>:8123/app/571f4c18_xpeng_slider_solver`) resolves, which is what
makes it the most natural wrong answer - but that route is GET-only, so the tool's
`POST /solve` comes back `405: Method Not Allowed`. That reads as a broken solver rather
than a wrong address, and it cost a live install three days (reported 2026-10-13). The
renewal app now rewrites that `/app/` form to the container hostname and says so in its
log and on `/healthz`; a URL carrying a signed ingress token has nothing to derive, so it
is left alone and reported instead.

## 2. First run: the app creates the config entry

A renewal *imports* the pair it minted, and importing needs somewhere to import it to.
On a brand-new install there is no XPENG entry yet - so the app creates one, by driving
the config flow with the pair it just minted. The first run finishes the install;
nothing is pasted and nothing is typed.

**It cannot be done through `xpeng.import_credentials`**, which is the obvious fix and
does not work. Measured on an install with no config entry (2026-10-11):

* the `xpeng` service domain is **absent** from `/api/services` - the integration's
  component-level setup runs only once an entry exists, so there is no
  `import_credentials` to call;
* starting a config flow does not load it either.

The config flow *is* loaded on demand, and creating the entry through it is what loads
the component and registers the service for every renewal after the first one.

### The VIN

The VIN decides the entity ids, and the imported pair carries it: the onboarding tool
records it in the token file - read from `/biz/v5/vehicle/v2/myVehicles` straight after
the login - so normally there is nothing to set. A pair written before that, or by
hand, carries none: the flow's import form then asks for it, or set the app's **`vin`**
option and let the app supply it.

Signing in inside Home Assistant is the case that genuinely needs it typed, because
there is no pair to read one from. Only the **Google** and **email** screens ask for a
VIN; the first screen asks the sign-in method alone (0.4.25).

### A disabled entry is not an install

**Delete an old entry; disabling it is not removing it.** Home Assistant hides disabled
entries from **Settings -> Devices & services**, but the app's own check still sees them
and the flow still counts them, so an install that disabled its old entry looks finished:

* the bootstrap is skipped, and `/healthz` reports the next renewal an interval away
  rather than a failure that never happened;
* the disabled entry keeps its `unique_id`, which is the VIN, so the flow answers
  `already_configured` and refuses to create the replacement. The VIN is unique to one
  car, and an entry that is merely switched off keeps its claim on it.

App **0.1.17** counts a disabled entry as no entry, so the first run bootstraps anyway.
That does not make disabling safe: the flow still refuses while the old entry owns the
VIN, so remove the entry (the three-dot menu on it -> **Delete**) when moving between
installs.

### When it does not

* `the entry was not created` with `already_configured` in the response - an entry for
  this VIN already exists, usually an older one that was **disabled** rather than
  deleted. Home Assistant hides it; the flow still counts it. Remove it and run again.
* `no VIN to create the config entry with` - as above: set `vin`, or add the
  integration by hand once.
* `could not start the config flow` / `the entry was not created` - the response names
  the step that refused. The usual cause is `pair_dir` not being under `/config`: the
  flow reads those paths **inside the Home Assistant container**, which cannot see the
  app's own filesystem.
* `import_credentials returned 400: No XPENG config entry to import credentials into` -
  the app only calls that service when an entry already exists, so this means the entry
  disappeared between the check and the call. Run it again.

The whole attempt is written to `<pair_dir>-last-run.log` next to the app's
`pair_dir` - `/config/xpeng-secrets-last-run.log` for the first slot,
`/config/xpeng-secrets-2-last-run.log` for the second.

The app itself needs permission to call that service; it asks for
`homeassistant_api` in its own config, which is what makes the import (and its
failure notifications) possible. If you ever see `401 Unauthorized` from the
import step rather than a 400, that grant is what is missing.

## 3. Home Assistant side (optional)

**The app renews on its own timer, so none of this is required.** `renew_interval`
(6000s by default) decides when, and every `invalid_poll_interval` (300s) it asks the
integration whether the credentials have already gone - so a session that dies early is
replaced within a few minutes. That was the one thing an outside scheduler could do
that a plain timer cannot, and section 2's bootstrap means the first renewal needs no
trigger either.

What an automation adds is **control**: Home Assistant deciding when to renew, rather
than the app. If you want that, set **`renew_interval: 0`** on the app first, so the two
are not both renewing.

**Visibility does not need any of it.** `sensor.xpeng_<the last six characters of your
VIN>_last_renewal` is a timestamp set by every path that installs a token + tenant pair -
the app's `import_credentials` call, the Repairs card, a reauth, and the flow the app
drives on a first install. It lives in the config entry, so a restart does not reset it,
and it is a real timestamp rather than a formatted string, which is what makes it usable
on a dashboard or in a condition. The app's own answer to the same question
(`/healthz`'s `next_run_s` and `last_reason`) is not something a dashboard can show.

> **Two things trip people up here.** The sensor is
> `binary_sensor.xpeng_<the last six characters of your VIN>_credentials_valid` - for the
> VIN `L1NNSGHA3SB123456` that is `binary_sensor.xpeng_123456_credentials_valid`. Find
> yours under **Settings -> Devices & Services -> Entities**, searching for
> `credentials_valid`, and use exactly what you find; the placeholder is not a name
> anything ships with. The `input_datetime` block below is also not decoration: the
> automation reads it as its clock. If `input_datetime.xpeng_last_renewal` is not in your
> entities, either the block never reached `configuration.yaml` or Home Assistant has not
> been restarted since - and the automation will do nothing until it is. See *Avoiding the
> helper* below if you would rather not create it at all.

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
cap, and the failure state for the case where it did not.

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
      entity_id: binary_sensor.xpeng_<your_car>_credentials_valid
      to: "off"
      for: "00:02:00"
      id: invalid
  conditions: []
  actions:
    # The interval is checked INSIDE the action, not as an automation condition,
    # so that the already-broken case is not made to wait it out: while the
    # credentials are invalid the car is unreachable, and gating that path would
    # leave it that way for up to a whole interval.
    - if:
        - condition: or
          conditions:
            - condition: state
              entity_id: binary_sensor.xpeng_<your_car>_credentials_valid
              state: "off"
              for: "00:02:00"
            - condition: template
              value_template: >-
                {{ states('input_datetime.xpeng_last_renewal')
                   in ['unknown', 'unavailable']
                   or (now().timestamp()
                       - state_attr('input_datetime.xpeng_last_renewal', 'timestamp')) > 6000 }}
      then:
        # Order is the failure policy. A non-2xx answer (the worker returns 502
        # for a failed renewal, 401 for a bad token) raises, an action error
        # stops the sequence, and the stamp below is therefore skipped — so a
        # failed renewal is retried on the next trigger instead of being
        # suppressed for a whole interval.
        - action: rest_command.xpeng_renew
        - action: input_datetime.set_datetime
          target:
            entity_id: input_datetime.xpeng_last_renewal
          data:
            timestamp: "{{ now().timestamp() }}"
```

That order is not cosmetic and it is worth keeping. Verified on a live install:
with the worker answering `401` the helper kept its previous value, and with a
`200` it was stamped.

### Avoiding the helper

The helper exists to give the automation a clock and to put that clock on a dashboard. If
you would rather not declare anything in `configuration.yaml`, an automation can use its
own `last_triggered` instead - replace the `template` condition with:

```yaml
            - condition: template
              value_template: >-
                {{ this.attributes.last_triggered is none
                   or (now() - this.attributes.last_triggered).total_seconds() > 6000 }}
```

and delete the final `input_datetime.set_datetime` action. `last_triggered` is `none` on
the first run, which is what the first line covers, so the first renewal happens as soon
as the automation is enabled. The trade is visibility: the helper is an entity you can put
on a dashboard, while `last_triggered` is only visible in the automation's attributes.

### Why a helper, and 6000 seconds

`credentials_valid` carries `age_s`, but that is the time since the last *mint* —
and with mint-on-demand a long-lived session has no recent mint at all, so it can be
minutes or hours old while perfectly healthy — not the age of the session, and
useless for this.
What matters is when the last **login** happened, and a renewal *is* a login, so
"last successful renewal" is exactly the right clock. Stamping `input_datetime` on
success is what makes that visible in Home Assistant - the app's own answer is
`/healthz`'s `next_run_s` and `last_reason`, which no dashboard can show.

The app's `renew_interval` and this automation share the number for the same reason:
it has to sit inside the cap with room to retry.

6000 seconds is 100 minutes: 20 minutes inside the measured ~2 hours, which leaves
room for a retry if something fails. The automation re-fires every 10 minutes while
the condition holds, so a failure retries on its own.

A renewal is now invisible to the rest of the integration **by design**: when the
pair is for the same car, `import_credentials` stages the new MQTT account on the
running client instead of reloading the config entry, so no entity blips and only
`last_renewal` moves. Builds before 0.5.0 reloaded the entry *twice* per renewal
(the entry-update listener and the import both reloaded), which left every entity
`unavailable` for a few seconds every 100 minutes - what looked like sensors
flapping.

## 4. The device identity

The app logs in as a *device*, and which device id that is turns out to matter more
than the schedule. Measured on 2026-10-10 on one install:

* with a freshly minted random device id, `credentials_valid` went invalid every
  ~2h07m–2h27m — the cap;
* after re-onboarding the same account with the device id already used by the
  phone, the session stayed valid for **3h44m and counting**, with no `12010202`
  ("Login status has expired") anywhere in the log;
* an install running on a device id taken from its phone has since gone **days**
  without an expiry.

So the two hours is not a clock on the session: it is what a *previously unseen*
identity gets. A session bound to an identity XPENG has seen before outlives one
bound to a brand-new identity. Practical consequences:

* The 100-minute cycle is correct either way, and it is what makes a fresh install
  work: a new install mints a new id and gets ~2 hours.
* If you want the longer sessions, set `device_id` explicitly to an id that
  install has used before (32 lowercase hex characters) and leave it set.
* Whatever you do, do not let it change on every renewal — that is what the app's
  remembered id in `/data/device_id.txt` prevents.

## 5. Most entities `unknown`: a stale MQTT client

Symptoms: credentials are valid, renewal works, the car is plainly bound to the
account (the API lists it) — and most entities sit at `unknown`, flapping between
roughly 20 and all 124, with the log showing a steady trickle of

```
XPENG MQTT connection refused: rc=Not authorized (rc_str=Not authorized)
```

**Cause: two MQTT clients for the same account, competing.** Each carries a
different `clientId`, so the broker authorises one and refuses the other; each
refusal triggers a credential refresh and a client rebuild, and every rebuild
resets the entities — so the counts flap and data never accumulates. Confirmed on a
live install:

```
19x  xmart:app:10225403:4dc51b29c3dee8b8ea6f7648ce407ffa   <- current
 4x  xmart:app:10225403:e45f264d72d0b022a94e664fbc2615b4   <- stale
live paho clients: 2
```

The usual way to end up here is **changing the device id on an install that already
had one** (e.g. setting `device_id` after it was already minted, which changes the
`clientId`): the integration does not tear the old client down when the clientId
changes. The code already guards against stacking clients with the *same* id; a
changed id slips past it.

**Fix: restart Home Assistant.** A config-entry reload is **not** enough — the stale
client survives it. Measured, before and after a restart:

| | before | after |
|---|---|---|
| live paho clients | 2 | **1** |
| entities unknown | 97 / 124 | **20 / 124** |
| `last_data_update` | `No data` | live |
| `polling_status` | `Reconnecting` | `Parked` |

Once it has settled it stays healthy — headless, with the app signed out, which is
the point: this has nothing to do with the app.

**Keep one device id per install.** If you must change it, expect to restart Home
Assistant afterwards.

### What this is not

Ruled out by measurement, so it is not worth re-treading:

* **not a user or session cap** — switching another Home Assistant for the same car
  off entirely, and signing a phone out of the app, both changed nothing;
* **not the app needing to be signed in** — the healthy state above was measured
  *with the app signed out*. An earlier revision of this section claimed the account
  had to be signed into the app; that was wrong, and a coincidence: a churning
  install produces intermittent good windows (19/124, 34/124, 84/124 observed both
  before and after an app login), and the app login happened to land in one;
* **not the credentials or the vehicle binding** — `myVehicles` returns the car for
  the account (`vehicleTypeCode FA`, `vehicleTypeName F30`).

### Two measurement traps in this area

* **`sensor.<car>_mqtt_listener = No messages` is not a data indicator.** A healthy
  install with live data reports exactly that, because the data arrives over REST.
* **Log levels differ per host**, so counting INFO/DEBUG lines across two installs
  proves nothing unless both are set to the same level. Counts like "0 MQTT accounts
  obtained" are an artifact of a `warning` level, not evidence.

## 6. When it goes wrong

Read these in order:

1. **`/config/xpeng-secrets-last-run.log`** — the complete output of the last
   renewal, written by the app on every run (it sits next to the app's `pair_dir`,
   so the second slot writes `/config/xpeng-secrets-2-last-run.log`). It contains
   the tool's own progress
   lines and whatever the browser or the API said, so it is usually the whole
   answer. Treat it as sensitive: it describes the account's login attempts.
2. **`GET http://<your-ha-host>:8090/healthz`** — renewals, failures, consecutive
   failures, and the last outcome including what `import_credentials` returned.
3. **The HA log**, for `homeassistant.components.rest_command` errors: they name
   the status the worker answered with.
4. **Most entities `unknown` while the credentials are healthy** — that is the
   competing-MQTT-client case in
   [section 5](#5-most-entities-unknown-a-stale-mqtt-client), not a credential
   problem. Look for `MQTT connection refused: rc=Not authorized`, then count the
   distinct `clientId`s.

The worker raises a persistent notification after **two consecutive failures**, and
one after each successful renewal unless `notify_on_success` is false. The success
card is the repetitive one on an install that renews on its own timer; failures keep
notifying either way.
Note that Home Assistant 2026.10 no longer exposes persistent notifications as
`persistent_notification.*` entities, so you will not find them with a template or
in the state list — look in the notifications panel in the UI.

## 7. Testing it without waiting two hours

Open the interval on purpose rather than shortening the code: set
`input_datetime.xpeng_last_renewal` to a time more than 6000 seconds ago (Developer
Tools -> Actions -> `input_datetime.set_datetime`), then run the automation from
Settings -> Automations. A successful cycle stamps the helper back to now; a failed
one leaves it alone, which is the behaviour you want to confirm.

Because the interval is checked inside the action, "Run" in the UI works for this.
An earlier revision gated on the trigger id instead, which silently did nothing
when run by hand — worth knowing if you copy the YAML from somewhere older.

## 8. Being told, instead of looking

Everything above is something you go and read. This is the version that comes to you: it
watches the integration's own verdict on itself and the credential renewal app's failure
notice, and says nothing while things are well.

**Import the blueprint** - one click, and it stays updatable from source:

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub%2Ecom%2Fxpengtoolkit%2Fxpengtoolkit%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fxpengtoolkit%2Fhealth_check%2Eyaml)

Or paste this URL into **Settings -> Automations -> Blueprints -> Import blueprint**:

```
https://github.com/xpengtoolkit/xpengtoolkit/blob/main/blueprints/automation/xpengtoolkit/health_check.yaml
```

Create an automation from it and answer five questions:

| Input | What to pick |
|---|---|
| Credentials valid | `binary_sensor.xpeng_<vin6>_credentials_valid` |
| MQTT connected | `binary_sensor.xpeng_<vin6>_mqtt_connected` |
| Polling status | `sensor.xpeng_<vin6>_polling_status` |
| Action on a problem | your notifier, or leave the default |
| Thresholds | 20 / 30 / 45 minutes, and whether to watch the renewal app |

`<vin6>` is the last six characters of your VIN - the entity ids are built from it.

### Notifications, including Pushover

**The default needs nothing configured.** It writes a persistent notification, so the
automation is useful on an install with no phone notifier at all - which is exactly what it
was tested on. To send it to a phone, point the action input at your own notifier:

```yaml
- action: notify.send_message
  target:
    entity_id: notify.your_phone
  data:
    title: XPENG health
    message: "{{ message }}"
```

**Pushover** works the same way, once Pushover itself is configured. It has no config flow,
so it lives in `configuration.yaml`:

```yaml
notify:
  - platform: pushover
    name: pushover
    api_key: !secret pushover_api_key
    user_key: !secret pushover_user_key
```

Then set the blueprint's action to:

```yaml
- action: notify.pushover
  data:
    title: XPENG health
    message: "{{ message }}"
    priority: 0        # Pushover's own scale; -2 quietest, 2 needs acknowledgement
```

`{{ message }}` is what the blueprint works out from whichever check fired, and
`{{ trigger.id }}` names it: `credentials`, `mqtt`, `polling`, `renewal`, `recovered` or
`mqtt_recovered`. Both are available to any action you put there.

### What it checks, and the version floor

| Check | Fires when | Why that window |
|---|---|---|
| Credentials valid | `off` for 20 minutes | Covers a renewal (about a minute) and a session being replaced |
| MQTT connected | `off` for 30 minutes | The broker, not the car: blips while parked are normal |
| Polling status | `Reconnecting` for 45 minutes | `Parked` is the healthy idle state and stays silent |
| The renewal app | its persistent notification appears | It posts that after two consecutive failed renewals, so this forwards it |

Needs **Home Assistant 2026.6** or newer (the per-trigger `note:` fields) and integration
**0.4.4+**, which is where the health binary sensors arrived. An older install carries
`credential_age`, `last_data_update` and `polling_status` instead - on one of those, feed the
blueprint the polling sensor and turn the renewal check off. A `for:` clock restarts on every
Home Assistant restart or momentary `unavailable`, which is fine here: it delays an alert
rather than losing it.

### Two things learned the hard way

* **Editing a blueprint needs a Home Assistant restart.** Blueprints are cached: an edited
  file is not re-read for validation until then. A first draft of this one kept being
  rejected for a fault that had already been fixed on disk until the box was restarted.
* **An automation's entity id comes from its `alias`, not its `id`.** Two automations whose
  aliases differ only in punctuation can surprise you when you go looking for one by id.
