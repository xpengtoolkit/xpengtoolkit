# Fault finding

Symptom first, then cause and fix. Most problems with this integration fall into a
small number of groups, and its own diagnostic entities are the fastest way to tell
them apart — see [Diagnostics](../reference/entities#diagnostics).

## Most entities are unknown

Symptoms: the credentials are valid, renewal works, the car is plainly bound to the
account — and most entities sit at `unknown`, flapping between roughly 20 and all of
them, with a steady trickle of this in the log:

```log
XPENG MQTT connection refused: rc=Not authorized (rc_str=Not authorized)
```

**Cause: two MQTT clients for the same account, competing.** Each carries a
different `clientId` (`xmart:app:<uid>:<deviceId>`), so the broker authorises one
and refuses the other; every refusal triggers a credential refresh and a client
rebuild, and every rebuild resets the entities — so values appear, vanish, and the
counts flap around instead of settling.

The usual way in is **changing `device_id` on an install that already had one**.
That changes the MQTT client id, and the client still using the old one is not
always torn down — so two clients for one account end up fighting. The code already
guards against stacking clients with the *same* id; a changed id slips past it.

**Fix: restart Home Assistant.** A config-entry reload does *not* clear it. The fix
in the integration (0.4.6) stops a previous client when an entry is set up again,
but if you are already in this state, only a restart clears the one that is loose.

Measured on a live install, before and after a restart:

| | before | after |
| --- | --- | --- |
| live paho clients | 2 | **1** |
| entities `unknown` | 97 / 124 | **20 / 124** |
| `last_data_update` | A timestamp plus a small `age_seconds` | `unknown` (no vehicle data yet since start) |
| `polling_status` | `Reconnecting` | `Parked` |

Once it has settled it stays healthy — headless, with the phone app signed out,
which is the point: this has nothing to do with the app.

**Keep one device id per install.** If you must change it, expect to restart Home
Assistant afterwards.

### Measurement traps

Two things reliably mislead when you are debugging this:

- **`sensor.xpeng_<vehicle>_mqtt_listener` reads `No messages` on a perfectly
  healthy install**, because vehicle data arrives over the REST poll rather than
  that topic. Do not read it as "MQTT is dead, so there is no data".
- **Counting client ids in the log tells you about history, not about now.** To see
  which client is live, compare the *newest* timestamp per client id instead of how
  many appear.
- **Log levels differ per host**, so counting INFO/DEBUG lines across two installs
  proves nothing unless both are set to the same level. Counts like "0 MQTT accounts
  obtained" are an artifact of a `warning` level, not evidence.

### What this is not

Ruled out by measurement, so it is not worth re-treading:

- **not a user or session cap** — switching another Home Assistant for the same car
  off entirely, and signing a phone out of the app, both changed nothing;
- **not the app needing to be signed in** — the healthy state above was measured
  *with the app signed out*. A churning install produces intermittent good windows
  (19/124, 34/124 and 84/124 were all observed, both before and after an app
  login), so an app login that appears to help most likely landed in one of them;
- **not the credentials or the vehicle binding** — the API returns the car for the
  account.

## Silent and parked {#silent-and-parked}

`sensor.xpeng_<vehicle>_polling_status` is the one-word answer to "what is the
connection doing?":

| Value | Meaning |
| --- | --- |
| `Active` | Charging or driving: a force-upload is sent on the `scan_interval` cadence. |
| `Parked` | Parked and quiet: one passive query per `parked_scan_interval`. The normal quiet state. |
| `Silent` | Connected, but nothing has arrived for longer than the silence threshold (300 s). This is the state the watchdog reconnects for. |
| `Reconnecting` | The socket is down and is being re-established. |
| `Escalated` | The watchdog has already sent a force-upload after 30 minutes of silence; at most once every 12 hours. A stall where the last accepted state said the car was **moving** bypasses that 12-hour budget — one per 10 minutes, one per episode of motion — because a moving car cannot be woken. |

**`Silent` is not the same as `Parked`.** Because the parked poll interval is
measured in hours while the silence threshold is minutes, a parked car can sit in
`Silent` and have the watchdog reconnect between polls — a value that returns to
`Parked` is not a problem in itself, it is designed behaviour. If a car you know is
awake stays `Silent` alongside `Not authorized` refusals, treat it as the case
above.

## Nothing is updating

Read these in order:

1. **`/config/xpeng-renew-last-run.log`** — the renewal app records the whole
   onboarding run, every step, plus whatever the browser or the API said, rewritten
   on every run. It is the first place to look, and it describes the account's login
   attempts, so treat it as sensitive.
2. **`GET http://<your-ha-host>:8090/healthz`** — renewals, failures and the last
   outcome, including what `xpeng.import_credentials` returned.
3. **The Home Assistant log** — `homeassistant.components.rest_command` errors name
   the status the renewal app answered with.
4. **The entity list** — Developer Tools → States. A healthy, quiet install still
   has some `unknown` values (the fields the car or the account does not report),
   but it settles rather than flapping. If they flap, go back to
   [Most entities are unknown](#most-entities-are-unknown).

::: info
Home Assistant 2026.10 no longer exposes persistent notifications as
`persistent_notification.*` entities, so a template that watches for the renewal
app's failure notification will not find it — look in the notifications panel in
the UI.
:::

## The energy sensor reads zero {#the-energy-sensor-reads-zero}

That is usually not zero consumption but a restricted endpoint on a shared account.
XPENG returns different data depending on the account's relationship to the car, and
it does not always say so:

| Data | Behaviour on a secondary (shared) account |
| --- | --- |
| Energy consumption | Endpoint returns **HTTP 200 with `0.0`** — no error at all |
| Location | Still delivered — the **app** hides it, the API does not |
| Some endpoints | `5000000 用户无权限` / `用户不可见` ("no permission" / "not visible") |

**So a `0.0` on `sensor.xpeng_<vehicle>_energy_consumption_7_day_avg` is not proof
of zero consumption.** That sensor carries a `zero_may_mean_restricted` attribute
when the value is zero, saying so explicitly. If you care about consumption figures,
onboard with the **owner** account.

Worth knowing in the other direction too: location is *not* gated by the API, so
this integration shows the car's position even when the app shows it only to the
owner. That is a deliberate consequence of using the API rather than the UI, not a
bug — but be aware of it if the car is shared.

## Is the install healthy?

A short checklist. Everything here is visible in Home Assistant except the two log
rows, and the staged-credentials line is logged at debug level.

| Check | Healthy | Not healthy |
| --- | --- | --- |
| `binary_sensor.xpeng_<vehicle>_credentials_valid` | `on`, attribute `reason: ok` | `off` with `no successful refresh yet` (never worked since start — points at onboarding), `N consecutive failures` (the broker refused), or `no successful refresh for Ns` (stale) |
| `sensor.xpeng_<vehicle>_credential_age` | Small just after a reconnect; it may climb while a session stays up, because credentials are minted on demand and not rotated on a timer | An age that never resets **together with** `credentials_valid` off: the loop cannot mint at all |
| Debug log, mints | One `MQTT account obtained` per reconnect, and none while a session is healthy | A mint on every cycle with no healthy session in between, or a rotating `clientId` with a reconnect each time |
| `binary_sensor.xpeng_<vehicle>_mqtt_connected` | `on` | `off`: the paho session to XPENG's broker is down |
| `sensor.xpeng_<vehicle>_polling_status` | `Parked` for a quiet car, `Active` while charging or driving | Stuck `Reconnecting`, or `Silent` on a car you know is awake |
| `unknown` / `unavailable` counts | Settled, with a stable handful of `unknown` fields the car does not report (20 of 124 in the measurement) | Flapping between a handful and nearly all of them |
| `Account signed in elsewhere - expecting a credential import` lines | One per renewal (about every 100 min): that is the renewal app logging in, and the import follows within seconds. Not a fault | A takeover with no import after it, or a re-auth card appearing for it |
| Login-expired lines in the log | Occasional warnings such as `Login token refresh failed:` or `Tokens still expired after refresh — triggering reauth`, each followed by a successful refresh | A steady stream of warnings with no success in between: the pair is dead and the Repairs card is the way back. The codes behind them are `12010101`, `12010104` and `12010202` |
| Distinct MQTT client ids | **One** live id: count distinct `xmart:app:...` ids in the log and compare their *newest* timestamps | Two with recent timestamps — the competing-client case above. Restart Home Assistant |

## A control is missing {#a-control-is-missing}

An entity that simply is not there - no unlock button, no OTA switch, no charge-limit
number - is usually **Command permissions**, not a fault. Since 0.4.20 every command
belongs to a capability group that is **Full**, **Read only** or **Disabled**:
Disabled means the entity is never created, and Read only keeps it reporting state but
refuses its commands. A new install starts at **Safe Plus**, which leaves unlocking,
the trunk and windows, OTA and raw commands out on purpose.

Open **Settings → Devices & services → XPENG → Configure**: the first field shows the
preset, and choosing **Custom** opens the per-group matrix. Widen the group you want and
submit - the entry reloads and the entities come back, keeping their entity ids and
their history. [Command permissions](../reference/command-permissions) lists which
service and entity lives in each group.

A *refused* command is different from a missing one, and says so. It raises with the
group and the setting:

```
'xpeng.unlock' is blocked by this integration's command permissions: the 'Access
actions' group is set to disabled in this integration's command permissions (mode:
safe).
```

Every refusal also fires an `xpeng_command_denied` event and writes one warning to the
log, so `grep Refused /config/home-assistant.log` answers "did something try?".
Neither is a credentials problem: if the log shows `Login token refresh failed` as
well, that is the separate case below.

## Window closing and charge-port controls {#window-closing-and-charge-port}

The window open/vent action moves the windows to the ventilation position.
Closing uses the official app's service-12 command. A P7+ owner physically
confirmed ventilation and closing on 22 October 2026. Neither full opening nor arbitrary positions are advertised.

Charge-port motor actions are unavailable when **Motorized charge-port controls**
is off or model configuration identifies a manual flap. The port's measured
state remains available. Charging start/stop is a different operation: it needs
a connected cable and a ready station. Vehicle code 10401 is a charging-start
failure, not evidence that the charge flap is unsupported.

## Getting back to a working login

If the credentials really are dead, the integration raises a **Repairs** card named
after the vehicle, with a **Fix** button that opens the import form directly. Re-run
the onboarding tool and paste the **new** pair — and only that one. Every successful
refresh rotates the refresh token, so any older `xpeng_tokens.json` is already
retired; re-importing it does not bring the old session back, it evicts the live
one, which looks exactly like "the fix did not work". One run of the tool, imported
once.

The in-app email/password re-auth cannot work, because it hits the same captcha. If
the Repairs card is not there — it is deleted when a fix is submitted, and an update
does not put it back — use Home Assistant's own **Authentication expired** prompt,
which opens the same import form for an entry created by **Import tokens**. Both
routes update the existing entry; there is no need to delete and re-add it.

See [Getting Started](./getting-started#the-manual-route) for the command, and
[Unattended renewal](./unattended-renewal) if you would rather the apps did this for
you. The certificates from [Certificates](./certificates) do **not** need redoing
unless XPENG rotates them, which shows up as a TLS or broker authentication failure
rather than a login failure.

### "It is asking me to import the tokens again"

The import screen says what it found, and where it looked, at the bottom of the
description. Read that line before anything else:

* **"Found an existing pair in …"** — the paths below it are filled in, and submitting
  the form unchanged imports that pair.
* **"No token + tenant pair was found in …"** — the credential is *not* on the host, and
  the certificates being in place does not change that. The three `.pem` files are the
  device's mTLS identity; the token + tenant pair is the login. A pair is only ever
  somewhere the integration can read it if the renewal app published it to
  `/config/xpeng-secrets`, or the onboarding tool was told to write there.

A pair that was written **inside the integration directory** is the usual way to end up
here: HACS replaces that directory on install, update and reinstall, so a pair saved
next to the component is gone the next time HACS touches it — and re-downloading the
integration restores code, not credentials. Removing the HACS download as a
troubleshooting step does the same thing deliberately. The onboarding tool now writes
the pair to `/config/xpeng-secrets/` whenever the component it finds *is* Home
Assistant's own, and takes `--out-dir` otherwise.

Nothing on disk can bring a deleted pair back, and an expired session cannot be
revived: re-mint it. The renewal app does that on its own and creates the entry, and
the manual route is one run of the onboarding tool plus a paste. Certificates kept in
`/config/xpeng-certs` are reused either way.
