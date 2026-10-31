# App options

Two companion apps remove the human from credential renewal. They are separate
apps with their own options — the integration itself does not read them, and the
renewal app drives the integration through
[`xpeng.import_credentials`](./services#credential-import).

| App | Repository | What it is for |
| --- | --- | --- |
| XPENG credential renewal | `xpengtoolkit/xpengtoolkit` | Logs in again before XPENG retires the session (~2 h after a login), mints a fresh token/tenant pair, and imports it |
| XPENG credential renewal (2nd car) | `xpengtoolkit/xpengtoolkit` | The same worker as a second slot, for a second car on its own account: host port `8091` and `pair_dir` `/config/xpeng-secrets-2` are the only defaults that differ |
| XPENG slider solver | `xpengtoolkit/slider-solver` | Solves the XPENG login slider captcha (NetEase Yidun) headlessly, so the re-login needs no human |

::: info
Home Assistant renamed add-ons to **apps** in 2026.2. The store, the options and
the configuration files are the same thing under the new name.
:::

Neither app is needed to *run* the integration. They are what makes it survive
longer than one session: a session dies about two hours after the login that
created it, and a refresh cannot extend it, so a long-lived install has to log in
again. Without them that is a manual re-onboarding.

Read [Unattended renewal](../guide/unattended-renewal) for the end-to-end flow,
and [Services](./services) for the integration-side service they call.

## XPENG credential renewal

`POST /renew` performs one renewal: it mints a pair with the toolkit's login code
(solving the captcha through the solver), publishes the pair to `pair_dir`, and
imports it. If there is no config entry yet it creates one first — see
[the first entry](./services#the-first-entry).

![The renewal app's configuration form](../images/app-renewal-config.png)

### Options

| Option | Type | Default | Meaning |
| --- | --- | --- | --- |
| `email` | email | empty | The XPENG account this install renews |
| `password` | password | empty | That account's password |
| `device_id` | optional string | empty | 32 lowercase hex characters; pins the login identity |
| `vin` | optional string | empty | 17-character VIN, only needed when the token file carries none |
| `renew_interval` | optional integer (seconds) | `6000` | The app's own renewal timer; `0` disables the scheduler |
| `invalid_poll_interval` | optional integer (seconds) | `300` | How often it asks Home Assistant whether the credentials have already died |
| `solver_url` | URL | `http://571f4c18-xpeng-slider-solver:8080` | Where the solver app listens; see the table below |
| `solver_token` | password | empty | The solver's `solver_token`, if you set one |
| `renew_token` | password | empty | Bearer token that `POST /renew` requires |
| `notify_on_success` | optional boolean | `true` | Send a persistent notification after each successful renewal; failures still notify after two consecutive attempts |
| `pair_dir` | string | `/config/xpeng-secrets` | Where the minted pair is written for Home Assistant to read |
| `entry_id` | optional string | empty | Target one config entry by id; empty selects the entry whose title ends with the pair's VIN suffix |
| `command_mode` | optional list | `safe_plus` | What the integration may do to the car, applied when this app creates the config entry: `read_only`, `safe`, `safe_plus`, `full` (`charging` is the old name for `safe_plus`) |

The renewal app declares no minimum or maximum for `renew_interval` or
`invalid_poll_interval` beyond "integer", so the Supervisor accepts any value.

The **2nd car** slot declares the same options and schema. Only two defaults differ —
`pair_dir` is `/config/xpeng-secrets-2` and the mapped host port is `8091` — and it
pulls the published `ghcr.io/xpengtoolkit/xpeng-renew` image instead of building its
own, so both slots always run the same worker. `tests/test_addon_slots.py` keeps the
two files' versions, options and schema identical.

`command_mode` is only *applied* when the app creates a config entry - an install
that already has one keeps whatever the integration is set to, and changing it
afterwards is the integration's own **Configure** screen. It exists so the
app-driven route, which is the one that creates the entry with no human involved,
does not silently land on Full access. See
[Command permissions](../reference/command-permissions).

### The options that matter

**`email` / `password`** — use a **second** XPENG account, never the one the
phone app uses. XPENG keeps one active session per account, so a renewal with the
app's account evicts the app (and vice versa). Share the car to the second
account from the main account first.

**`renew_token`** — set it. The app listens on port 8090 and holds your account
password; without a token anyone who can reach that port can trigger a login. The
automation and `rest_command` examples send it as `Authorization: Bearer
<renew_token>`, and a wrong or missing token is answered with HTTP `401`.

**`device_id`** — worth pinning. A login is bound to a device identity, and an
identity XPENG has seen before outlasts a freshly generated one (measured: the
~2 h cap versus 3 h 44 m on one install). Set it to an id the install has used
before and **leave it set**; the app remembers the first generated id in
`/data/device_id.txt` so it does not change on every renewal. Changing this option
on an install that already has a different id changes the MQTT clientId, which is
the stale-client trap described in
[Unattended renewal](../guide/unattended-renewal) — the fix for it is a Home
Assistant restart, not a config-entry reload.

**`pair_dir`** — live credentials on disk, read by the Core process. It must be
under `/config`: the config flow's **Import tokens** step resolves those paths
*inside the Home Assistant container*, which cannot see the app's own filesystem.
See [Credentials and the pair directory](#credentials-and-the-pair-directory).

**`renew_interval`** — `6000` s (100 minutes) sits inside the measured ~2 h cap
with room for one retry. `invalid_poll_interval` covers the case where a session
dies early: the app notices within a few minutes instead of waiting out the
interval.

### Environment (standalone Docker) {#environment-standalone-docker}

Outside the Supervisor there is no Configuration tab; set environment variables
instead. They take precedence over `/data/options.json` when both exist, so the
image the app store would build can be run with plain `docker run -e` as it is.

| Option | Environment variable | Default |
| --- | --- | --- |
| `email` | `EMAIL` | empty |
| `password` | `PASSWORD` | empty |
| `device_id` | `DEVICE_ID` | the id in `/data/device_id.txt`, else the first generated one |
| `vin` | `VIN` | empty |
| `renew_interval` | `RENEW_INTERVAL` | `6000` |
| `invalid_poll_interval` | `INVALID_POLL_INTERVAL` | `300` |
| `solver_url` | `SOLVER_URL` | `http://571f4c18-xpeng-slider-solver:8080` |
| `solver_token` | `SOLVER_TOKEN` | empty |
| `renew_token` | `RENEW_TOKEN` | empty |
| `notify_on_success` | `NOTIFY_ON_SUCCESS` | `true` |
| `pair_dir` | `PAIR_DIR` | `/config/xpeng-secrets` |
| `entry_id` | `ENTRY_ID` | empty |
| `command_mode` | `COMMAND_MODE` | `safe_plus` |
| (Core API address) | `CORE_API` | `http://supervisor/core/api` |
| (Core API bearer) | `SUPERVISOR_TOKEN` | empty |

`PORT` (default `8090`) moves the API, and `OPTIONS_PATH` points the app at a
different options file (default `/data/options.json`).

Standalone, `CORE_API` and `SUPERVISOR_TOKEN` replace the `homeassistant_api`
grant described under [Endpoints](#endpoints): point `CORE_API` at the Core API
(`http://<ha-host>:8123/api`) and put a Home Assistant long-lived access token in
`SUPERVISOR_TOKEN`. `pair_dir` has to be a path the Core process can read, and
`/data` has to be writable for `device_id.txt`. The step-by-step, including the
mounts, is [Standalone Docker](../guide/unattended-renewal#standalone-docker).

### Endpoints

The app publishes port `8090` and, under the Supervisor, asks for the
`homeassistant_api` grant, which is what lets it call `xpeng.import_credentials`
through the Supervisor's Core proxy (standalone, `CORE_API` +
`SUPERVISOR_TOKEN`). Without that grant the import step fails with HTTP
`401 Unauthorized` — after a renewal that otherwise succeeded, so it reads as a
credential problem rather than a permissions one.

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/renew` | Requires `Authorization: Bearer <renew_token>` when `renew_token` is set. Answers `200` on success, `502` on a failed renewal, `401` for a bad token |
| `GET` | `/healthz` | Counters, state and the resolved configuration |

A successful renewal answers with the import result:

```json
{
  "ok": true,
  "imported": "{\"changed_states\": [], \"service_response\": {\"entry_id\": \"<entry-id>\", \"vin\": \"<vin>\", \"...\": \"...\"}}",
  "onboarding": "<onboarding summary>",
  "ms": 41320
}
```

`imported` is the Home Assistant response body **as text**, capped at 400
characters by the app, so parse it rather than reading it as a nested object — and
do not assume the longest identifiers arrived intact. A first run that had to
create the entry answers
`{"ok": true, "bootstrapped": "<VIN>", "onboarding": "...", "ms": ...}` instead,
where `bootstrapped` is the VIN the entry was created with.

Every attempt is also written in full to `<pair_dir>-last-run.log` next to the pair
directory (`/config/xpeng-secrets-last-run.log` by default, and
`/config/xpeng-secrets-2-last-run.log` for the second slot), including whatever the
browser and the API said. It describes the account's login attempts, so treat it as
sensitive.

## XPENG slider solver

![The slider solver app's configuration](../images/app-solver-config.png)


The solver renders the real captcha widget at XPENG's origin with the real SDK
and performs the drag in code. Against the live captcha it returns a `validate`
in about 14 seconds, on the second attempt: the first drag measures the widget's
constant pointer dead-zone (the piece lands ~10.5 CSS px short of the pointer
travel) and every later attempt is aimed with the learned bias. Solves are
serialised — one browser, and Yidun risk-scores parallel attempts from one
address.

It is for your own accounts on infrastructure you control. It makes renewing
painless; it does not remove the ~2 h session cap, and it bypasses nothing else —
the account password, the mTLS client certificate and the tenant still have to be
real.

### Options

| Option | Type | Default | Meaning |
| --- | --- | --- | --- |
| `solver_token` | password | empty | If set, every `/solve` call must carry it |
| `solve_timeout_ms` | integer, `5000`–`180000` | `45000` | Timeout per attempt |
| `solve_attempts` | integer, `1`–`10` | `4` | Attempts before giving up |
| `drag_bias_px` | float, `0`–`80` | `10.5` | The widget's constant pointer dead-zone, in CSS pixels |

**`solver_token`** — set it, and give the renewal app the same value as its
`solver_token`. The solver runs a real browser and mints credentials for whatever
account it is pointed at, so an unauthenticated `/solve` reachable from the
network is an account takeover waiting to happen. Do not expose it beyond the
Home Assistant host.

**`drag_bias_px`** — a head start, not a calibration. The solver re-measures the
bias on every solve, so this only saves the first attempt after a start. The
standalone Docker image defaults it to `0` and the app's default is the measured
`10.5`.

### Environment (standalone Docker)

Running the image outside the Supervisor takes environment variables instead of
options, with the same meanings: `PORT` (`8080`), `SOLVER_TOKEN`,
`SOLVE_TIMEOUT_MS` (`45000`), `SOLVE_ATTEMPTS` (`4`), `DRAG_BIAS_PX` (`0`),
`DEBUG_DIR` (`/debug`), `HEADLESS` (`true`) and `XPENG_CAPTCHA_SDK`.

Failed attempts leave screenshots and DOM dumps under the debug directory
(`/data/debug` in the app, persistent across restarts), which is what makes a
selector change diagnosable rather than mysterious.

### Endpoints

The app publishes port `8080` and has an ingress panel in the sidebar, which
renders the same counters as an HTML page.

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/solve` | Body `{"captchaId"?, "mode"?, "timeoutMs"?, "attempts"?}` → `{"ok": true, "validate": "…", "ms": …}` or `{"ok": false, "error": "…"}` |
| `GET` | `/healthz` | Liveness and counters; `/` renders the same as an HTML panel |

Defaults for `/solve`: the **app** captcha id, `mode=embed`, 45 s per attempt,
4 attempts. When `SOLVER_TOKEN` is set, `/solve` requires
`Authorization: Bearer <token>` (or `X-Solver-Token: <token>`).

## solver_url

This is the one setting that depends on how the solver was installed, and getting
it wrong looks like "the slider never gets solved": the renewal fails while trying
to reach `solver_url`, and the app's log records the connection error.

| How the solver was installed | `solver_url` |
| --- | --- |
| From the store, `xpengtoolkit/slider-solver` | `http://571f4c18-xpeng-slider-solver:8080` (the default) |
| As a local app folder named `slider_solver` | `http://local-xpeng-slider-solver:8080` |
| Anything else | `http://your-ha-host:8080` |

::: warning It is the hostname that resolves, not the slug
The hyphenated **container hostname** is what another app can reach. The
underscore slug forms (`571f4c18_xpeng_slider_solver`,
`local_xpeng_slider_solver`) fail with `No address associated with hostname` —
they were the names people were told to use, and one of them was the shipped
default, which is why this is worth stating plainly. When in doubt, the Home
Assistant host's own address always works. Changing this option restarts the app.

**The sidebar panel is not the API.** `solver_url` is wherever `/solve` lives: the
solver's own port, **8080**. The ingress panel URL the Home Assistant UI shows you
(`http://<your-ha-host>:8123/app/571f4c18_xpeng_slider_solver`) resolves, which is what
makes it the most natural wrong answer — but that route is GET-only, so the tool's
`POST /solve` comes back `405: Method Not Allowed`. That reads as a broken solver rather
than a wrong address, and it cost a live install three days (reported 2026-10-13). The
renewal app rewrites that `/app/` form to the container hostname and says so in its log
and on `/healthz`; a URL carrying a signed ingress token has nothing to derive, so it is
reported instead.
:::

## Credentials and the pair directory

Both apps hold credentials, and both need thinking about:

* the **renewal app** holds the XPENG account `email`/`password` and its own
  `renew_token`;
* the **solver** holds `solver_token` and, while it runs, a browser that can mint
  credentials for the account it is pointed at.

Set both tokens, keep both apps off the public internet, and remember that
anything on your LAN that can reach `/renew` can start a login.

`pair_dir` deserves the same care:

```bash
chmod 700 /config/xpeng-secrets
```

The directory holds a live token/tenant pair, readable by the Core process. Keep
it under `/config` (so the config flow can read it), out of any share, and out of
any backup that leaves the house unencrypted. The same applies to the app's
`<pair_dir>-last-run.log` (next to that directory, e.g.
`/config/xpeng-secrets-last-run.log`), which records the login attempts.

## `/healthz`

Both apps expose `GET /healthz`, and it is the first place to look when a renewal
does not happen. The renewal app's answer carries the counters, the scheduler and
the resolved configuration (shape shown with illustrative values):

```json
{
  "ok": true,
  "uptime_s": 8123,
  "renewals": 4,
  "failures": 0,
  "consecutive_failures": 0,
  "running": false,
  "last": { "ok": true, "imported": "{\"changed_states\": [], \"service_response\": {\"vin\": \"<vin>\"}}", "ms": 41320 },
  "scheduler": { "interval_s": 6000, "next_run_s": 4210, "last_reason": "scheduled" },
  "configured": { "email": "owner@example.com", "solver_url": "http://571f4c18-xpeng-slider-solver:8080", "pair_dir": "/config/xpeng-secrets", "device_id": "configured", "auth": "bearer" }
}
```

Note that it echoes the configured account address, so treat the endpoint as
sensitive even when `renew_token` is set — `renew_token` protects `/renew`, not
`/healthz`. The solver's answer is smaller: `ok`, `browser`, `uptimeMs`,
`solves`, `failures`, `queued`, `auth` and `defaultCaptchaId`.

The renewal app raises a persistent notification after **two consecutive
failures**, and one after each successful renewal unless `notify_on_success` is
false - the success card is the repetitive one when the app renews on its own timer.
Home Assistant 2026.10 no longer exposes persistent notifications as
`persistent_notification.*` entities, so look in the notifications panel rather
than in a template.
