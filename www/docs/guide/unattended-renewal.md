# Unattended renewal

XPENG retires a session about two hours after the login that created it, and a
refresh cannot extend it — measured at 2 h 12 m, 2 h 03 m and about 2 h on two
accounts, with the refresh loop healthy right up to the moment the server ended
it. One install has since run for **days** without an expiry, so treat the two
hours as the common case rather than a rule - see
[the device identity](#the-device-identity). An install meant to run for weeks
therefore has to log in again before that happens, and a login normally needs a
human to drag a slider.

Two apps remove the human, and the renewal app decides when:

```text
automation (schedule)  ->  rest_command  ->  XPENG credential renewal (worker)
                                              |  POST /solve   -> slider solver
                                              |  pwdLogin + guardian -> new pair
                                              `  xpeng.import_credentials (service)
```

Where you use an automation it is the clock and the policy. The worker holds the
account password and does the privileged part. The integration validates the
result before anything is swapped, exactly as the Repairs card does.

::: info
Home Assistant renamed add-ons to **apps** in 2026.2. The store, the options and
everything below are the same thing under the new name.
:::

## Why the slider is the only human step

XPENG gates sign-in with its own slider captcha (NetEase Yidun). The server
answers the email/password step with `12040101` (slider demanded) and `12040106`
(`Slider captcha validation failed`) until a human drags it. That drag needs a
real browser window, so it cannot be solved from inside Home Assistant — the
config flow has no way to render or complete it. Everything else about the login
is plain HTTP, which is why a solver is enough to make renewal unattended.

## Install the two apps

Both are ordinary apps, and both are needed:

* **[XPENG slider solver](https://github.com/xpengtoolkit/slider-solver)** — solves
  the login captcha, which is the one human step in onboarding.
* **XPENG credential renewal** — this repository. It mints a fresh pair ahead of
  the two-hour cap and imports it with `xpeng.import_credentials`, the same
  service the Repairs card uses.

**Settings → Apps → Install app → three-dot menu → Repositories**, add this
repository's URL and the solver's, then install both. (Home Assistant renamed
add-ons to **apps** in 2026.2; older releases call the same screen the Add-on
Store.)

The same repository also ships a **second slot** of the renewal app, **XPENG
credential renewal (2nd car)**: the same worker and the same published image, with
host port `8091` and `pair_dir` `/config/xpeng-secrets-2` as its defaults. Install
it only when a second car has its own account — see
[Two cars, two accounts](#two-cars-two-accounts).

![The app store with both XPENG apps](../images/apps-store.png)

The solver is self-hosted and separate. It clears the account owner's own
challenge on a host they control, so keep it private and never expose it without
`SOLVER_TOKEN`.

## The app's options

![The renewal app's configuration form](../images/app-renewal-config.png)

| Option | Meaning |
| --- | --- |
| `email` / `password` | The account this install renews. Use the second, dedicated account, never the one signed into the phone app: XPENG keeps one session per account, so the app logging in evicts this one. |
| `renew_token` | The secret the automation presents to `POST /renew`. **Set it** — the worker holds your password. |
| `solver_url` | Where the solver app listens. Defaults to `http://571f4c18-xpeng-slider-solver:8080`, the solver's container hostname as installed from the store. See [below](#if-solver_url-does-not-resolve). |
| `solver_token` | The solver's `solver_token`, if you set one. |
| `device_id` | Optional, and worth pinning. Leave it blank and the first renewal's device id is remembered in `/data/device_id.txt` and reused, so the identity at least does not change every two hours. Do not change it on a live install — see [the device identity](#the-device-identity). |
| `pair_dir` | Where the fresh pair is written for Home Assistant to read: `/config/xpeng-secrets` by default. |
| `vin` | Only needed if the token file carries no VIN. The onboarding tool records one, so normally leave this blank. |
| `renew_interval` | The app's own renewal timer, in seconds: 6000 by default. Set it to `0` to disable the scheduler and leave triggering to Home Assistant or to you. |
| `invalid_poll_interval` | How often to ask Home Assistant whether the credentials are still valid (300 s), so a session that dies early is replaced within a few minutes. |

Give `pair_dir` a second thought: it is live credentials on disk, readable by the
Core process. `chmod 700` the directory, and do not put it in a share.

### If solver_url does not resolve {#if-solver_url-does-not-resolve}

This is the one setting that depends on how the solver was installed, and getting
it wrong looks like "the slider never gets solved":

| How the solver was installed | `solver_url` |
| --- | --- |
| from the store, `xpengtoolkit/slider-solver` | `http://571f4c18-xpeng-slider-solver:8080` (the default) |
| as a local app folder named `slider_solver` | `http://local-xpeng-slider-solver:8080` |
| anything else | `http://<your-ha-host>:8080` |

**It is the hostname that resolves, not the slug** — hyphens, not underscores. The
slug forms (`571f4c18_xpeng_slider_solver`, `local_xpeng_slider_solver`) fail with
`No address associated with hostname`. The plain host address always works, so it
is the safe answer when in doubt. Changing this option restarts the app.

**The sidebar panel is not the API.** `solver_url` is wherever `/solve` lives: the
solver's own port, **8080**. The ingress panel URL the Home Assistant UI shows you
(`http://<your-ha-host>:8123/app/571f4c18_xpeng_slider_solver`) resolves, which is what
makes it the most natural wrong answer — but that route is GET-only, so the tool's
`POST /solve` comes back `405: Method Not Allowed`. That reads as a broken solver rather
than a wrong address, and it cost a live install three days. The renewal app now rewrites
that `/app/` form to the container hostname and says so in its log and on `/healthz`; a
URL carrying a signed ingress token has nothing to derive, so it is reported instead.

## Standalone Docker (no Supervisor) {#standalone-docker}

Home Assistant **apps** (formerly add-ons) exist only on Home Assistant OS and
Supervised; the Add-on Store does not exist on Container or Core. Both companions
are still available there, because an app is an ordinary container with an options
file: the same two images run as plain Docker containers.

The solver's own README covers its side, including a non-Docker Node route. The
renewal side is below.

Both companion images are **published to GitHub Container Registry** on every
change, for `amd64` and `arm64`, so nothing has to be built by hand:

| Image | Built from |
| --- | --- |
| `ghcr.io/xpengtoolkit/xpeng-renew` | this repository |
| `ghcr.io/xpengtoolkit/xpeng-slider-solver` | `xpengtoolkit/slider-solver` |

The repository root carries a
[`docker-compose.yml`](https://github.com/xpengtoolkit/xpengtoolkit/blob/main/docker-compose.yml)
and a `.env.example` that names every setting:

```bash
git clone https://github.com/xpengtoolkit/xpengtoolkit
cd xpengtoolkit
cp .env.example .env      # account, tokens, mount paths
docker compose pull
docker compose up -d
```

Image tags are the app versions (`:0.1.23`, ...); `:latest` tracks the newest.
Forks publish the renewal image under the lowercase repository owner, for example
`ghcr.io/drazorv/xpeng-renew:0.1.23`. To use a fork build, change the renewal
service's `image` in `docker-compose.yml`; the solver image stays unchanged.
Building from source still works if you would rather not pull a registry image:

```bash
git clone https://github.com/xpengtoolkit/slider-solver
git clone https://github.com/xpengtoolkit/xpengtoolkit
docker build -t xpeng-slider-solver ./slider-solver/slider_solver
docker build -t xpeng-renew ./xpengtoolkit/xpeng_renew
```

The compose file keeps the solver on the internal Docker network (only the
renewal API's port `8090` is published) and uses the same image defaults as the
store install.

The two pieces the Supervisor normally supplies become ordinary settings:

* **`CORE_API`** (default `http://supervisor/core/api`) is where the app sends its
  Home Assistant calls. Standalone, point it at the Core API —
  `http://<ha-host>:8123/api`, or `http://homeassistant:8123/api` when both
  containers share a Docker network.
* **`SUPERVISOR_TOKEN`** is only the bearer for those calls — the name is the
  app's. Put a **long-lived access token from an admin profile** there. Admin
  matters on a fresh install: with no config entry the app *creates* one by
  driving the config flow over the REST API.

Three mounts have to be right, and the first one catches people:

* **`pair_dir` must be the same absolute path inside the Home Assistant
  container.** The pair is not uploaded; the app calls
  `xpeng.import_credentials` with `tokens_path` / `tenant_path`, and Core reads
  those files itself. Mount the host directory into *both* containers at the same
  path — the compose file uses `/config/xpeng-secrets`, which is what `pair_dir`
  defaults to. `chmod 700` the host directory: live credentials.
  If Core sees the pair somewhere else, set **`PAIR_DIR`** in `.env` and the
  compose mount target follows it — for example `/share/xpeng-secrets` when a
  NAS share is mounted under `/share`. That works, but it puts the live token
  pair on a network share; the default local `/config` path is the safer choice.
* **Certificates** at `/config/xpeng-certs` (or
  `/config/custom_components/xpeng/certs`), read-only. The bundled onboarding code
  searches both.
* **`/data`** must be writable: the device identity is remembered in
  `/data/device_id.txt`, and an identity XPENG has seen before outlasts a freshly
  generated one. A named volume is enough.

Everything else is unchanged: the same `/healthz`, the same `POST /renew` guarded
by `renew_token`, and the same 6000 s scheduler — set `RENEW_INTERVAL: "0"` and
drive `/renew` from cron or a `rest_command` if you prefer your own clock. A first
run with no entry still [creates one](#first-run-creates-the-config-entry).

Two things do not carry over from the app: there is no sidebar panel (that is an
ingress feature), and the option names become upper-case environment variables.
The full mapping is in
[App options](../reference/app-options#environment-standalone-docker).

## First run creates the config entry

A renewal *imports* the pair it minted, and importing needs somewhere to import it
to. On a brand-new install there is no XPENG entry yet — so the app creates one,
by driving the config flow with the pair it just minted. The first run finishes the
install; nothing is pasted and nothing is typed.

**It cannot be done through `xpeng.import_credentials`**, which is the obvious fix
and does not work. Measured on an install with no config entry: the `xpeng` service
domain is absent from `/api/services`, because the integration's component-level
setup runs only once an entry exists, so there is no `import_credentials` to call —
and starting a config flow does not load it either. The config flow *is* loaded on
demand, and creating the entry through it is what loads the component and registers
the service for every renewal after the first one.

### The VIN

The VIN decides the entry's entity ids, and the imported pair carries it: the onboarding
tool records it in the token file — read from the vehicle list straight after the login —
so normally there is nothing to set. A pair written before that, or by hand, carries none:
the flow's import form then asks for it, or set the app's **`vin`** option and let the app
supply it.

Signing in inside Home Assistant is the case that genuinely needs it typed, because there
is no pair to read one from. Only the **Google** and **email** screens ask for a VIN; the
first screen asks the sign-in method alone.

### A disabled entry is not an install

**Delete an old entry; disabling it is not removing it.** Home Assistant hides disabled
entries from **Settings → Devices & services**, but the app's own check still sees them
and the flow still counts them, so an install that disabled its old entry looks finished:

* the bootstrap is skipped, and `/healthz` reports the next renewal an interval away
  rather than a failure that never happened;
* the disabled entry keeps its `unique_id`, which is the VIN, so the flow answers
  `already_configured` and refuses to create the replacement. The VIN is unique to one
  car, and an entry that is merely switched off keeps its claim on it.

App **0.1.17** counts a disabled entry as no entry, so the first run bootstraps anyway.
That does not make disabling safe: the flow still refuses while the old entry owns the
VIN, so remove the entry (the three-dot menu on it → **Delete**) when moving between
installs.

### When it does not work

| Message | What it means |
| --- | --- |
| `the entry was not created` … `already_configured` | An entry for this VIN already exists, usually an older one that was **disabled** rather than deleted. Home Assistant hides it; the flow still counts it. Remove it and run again. |
| `no VIN to create the config entry with` | As above: set `vin`, or add the integration by hand once. |
| `could not start the config flow` / `the entry was not created` | The response names the step that refused. The usual cause is `pair_dir` not being under `/config`: the flow reads those paths **inside the Home Assistant container**, which cannot see the app's own filesystem. |
| `import_credentials returned 400: No XPENG config entry to import credentials into` | The app only calls that service when an entry already exists, so this means the entry disappeared between the check and the call. Run it again. |

The whole attempt is written to `<pair_dir>-last-run.log` next to the app's
`pair_dir` - `/config/xpeng-secrets-last-run.log` for the first slot,
`/config/xpeng-secrets-2-last-run.log` for the second.

The app needs permission to call that service; it asks for `homeassistant_api` in
its own config, which is what makes the import and its failure notifications
possible. A `401 Unauthorized` from the import step rather than a 400 means that
grant is missing.

### Two cars, two accounts

XPENG allows **one active session per account**, so two cars need one account each:
share each car to its own dedicated account, never the phone-app account. Home
Assistant then has one XPENG config entry per account, and the two sessions do not
evict each other.

Each account needs its own renewal app instance. Installing **XPENG credential
renewal (2nd car)** from the same repository gives you that: it is the same worker on
the same published image, with the second instance's defaults already applied — host
port `8091` and `pair_dir` `/config/xpeng-secrets-2`. Give it the second account's
`email` and `password` and leave `entry_id` empty.

Whichever route you take for the second (and any further) car, these are what have to
be distinct:

* its own **`pair_dir`** — for example `/config/xpeng-secrets-g9` and
  `/config/xpeng-secrets-x9`, each mounted into Home Assistant at the same absolute
  path. Two instances sharing one directory overwrite each other's pair;
* its own **port** — `8090` for the first slot and `8091` for the second are the
  defaults; plain containers pick the host port explicitly;
* optionally its own **`entry_id`**. Left empty, the app selects the entry whose
  title ends with the pair's VIN suffix (`XPENG <last six>`), so the instances stay
  independent with nothing to copy. A pair whose VIN matches **no** entry is treated
  as belonging to a car Home Assistant does not have yet: it creates a new entry
  instead of being delivered to whichever entry happens to exist. Set `entry_id`
  only when the title is not one the integration wrote, for example after renaming
  an entry.

A third car has no shipped slot: copy the `xpeng_renew_2` folder into `/addons/`,
change its `slug`, keep its `image:`, and give it a third `pair_dir` and port. There
is no second copy of the worker to keep in step, which is the point of the slot being
packaging only.

Commands reach the car you mean, too. Every `xpeng.*` service takes an optional
`vehicle_entity_id` - any entity of that car - so an automation can unlock the
second car or send it a destination. Omit it and the call acts on the first
configured entry, which on this install is the wrong car; see
[Services](../reference/services#choosing-the-vehicle).

The solver can normally be shared: it solves the slider, not the account. One
caveat worth knowing before blaming the accounts: a solver instance keeps **one
Chromium process** for its lifetime and opens a fresh browser context per solve, so
everything below the context — the browser-level fingerprint — is identical for
every account it logs in for. If two accounts on one install keep evicting each
other's sessions (each new login killing the other's within minutes), run one
solver instance per account and compare the session lifetimes; that is the one
shared piece worth splitting before assuming XPENG links the accounts by IP.

The app names the selected entry explicitly in `xpeng.import_credentials`, so a
pair minted for one car cannot land in the other car's entry.

## Home Assistant side (optional)

**The app renews on its own timer, so none of this is required.**
`renew_interval` (6000 s by default) decides when, and every
`invalid_poll_interval` (300 s) the app asks the integration whether the credentials
have already gone — so a session that dies early is replaced within a few minutes.

What an automation adds is **control**: Home Assistant deciding when to renew, rather
than the app. If you want that, set **`renew_interval: 0`** on the app first, so the two
are not both renewing.

**Visibility does not need any of it.**
`sensor.xpeng_<the last six characters of your VIN>_last_renewal` is a timestamp set by
every path that installs a token + tenant pair — the app's `import_credentials` call,
the Repairs card, a reauth, and the flow the app drives on a first install. It lives in
the config entry, so a restart does not reset it, and being a real timestamp rather than
a formatted string is what makes it usable on a dashboard or in a condition. The app's
own answer to the same question (`/healthz`'s `next_run_s` and `last_reason`) is not
something a dashboard can show.

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
      entity_id: binary_sensor.xpeng_<vehicle>_credentials_valid
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

That order is not cosmetic. Verified on a live install: with the worker answering
`401` the helper kept its previous value, and with a `200` it was stamped.

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
success is what makes that visible in Home Assistant; the app's own answer is
`/healthz`'s `next_run_s` and `last_reason`, which no dashboard can show.

6000 seconds is 100 minutes: 20 minutes inside the measured ~2 hours, which leaves
room for a retry if something fails. The automation re-fires every 10 minutes while
the condition holds, so a failure retries on its own.

A renewal is now invisible to the rest of the integration: when the pair is for the
same car, the new MQTT account is staged on the running client and no config entry is
reloaded, so no entity blips and only `last_renewal` moves. Older builds reloaded
(twice, once per reload path) on every cycle, which left every entity `unavailable`
for a few seconds each time.

## The device identity

The app logs in as a *device*, and which device id that is matters more than the
schedule. Measured on one install:

* with a freshly minted random device id, `credentials_valid` went invalid every
  ~2 h 07 m–2 h 27 m — the cap;
* after re-onboarding the same account with the device id already used by the
  phone, the session stayed valid for **3 h 44 m and counting**, with no `12010202`
  ("Login status has expired") anywhere in the log;
* an install running on a device id taken from its phone has since gone **days**
  without an expiry.

So the two hours is not a clock on the session: it is what a *previously unseen*
identity gets. A session bound to an identity XPENG has seen before outlives one
bound to a brand-new identity. Practical consequences:

* The 100-minute cycle is correct either way, and it is what makes a fresh install
  work: a new install mints a new id and gets about two hours.
* If you want the longer sessions, set `device_id` explicitly to an id that install
  has used before (32 lowercase hex characters) and leave it set.
* Whatever you do, do not let it change on every renewal — that is what the app's
  remembered id in `/data/device_id.txt` prevents. Changing it on a live install is
  the trap in [Fault finding](./fault-finding#most-entities-are-unknown).

## Testing without waiting two hours

Open the interval on purpose rather than shortening the code: set
`input_datetime.xpeng_last_renewal` to a time more than 6000 seconds ago
(**Developer Tools → Actions → `input_datetime.set_datetime`**), then run the
automation from **Settings → Automations**. A successful cycle stamps the helper
back to now; a failed one leaves it alone, which is the behaviour you want to
confirm.

Because the interval is checked inside the action, **Run** in the UI works for
this. An earlier revision gated on the trigger id instead, which silently did
nothing when run by hand.

To force a renewal with no automation at all:

```bash
curl -X POST -H "Authorization: Bearer <renew_token>" http://<your-ha-host>:8090/renew
```

## Being told, instead of looking

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

::: v-pre
`{{ message }}` is what the blueprint works out from whichever check fired, and
`{{ trigger.id }}` names it: `credentials`, `mqtt`, `polling`, `renewal`, `recovered` or
`mqtt_recovered`. Both are available to any action you put there.
:::

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
