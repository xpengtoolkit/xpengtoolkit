# Android command audit — 24 October 2026

This audit compares the integration with one European P7+ (cloud model code FJ)
and the official Android XPENG 3.16.0 app. Findings are model-specific unless
the protocol or automated test establishes a more general property.

## Method

1. The owner authorized tests with the vehicle parked, empty and with clearance
   around its trunk and windows. The vehicle was unplugged.
2. The official split APK was installed in an API 30 x86_64 emulator with ARM64
   translation. Its native wrapper failed before login: it selected an x86 helper
   for the translated process, and selecting the ARM64 helper instead returned
   JNI_ERR. No account credentials were entered in the emulator. These are not
   emulator-based successful vehicle tests.
3. The fallback was the owner's already authenticated physical Android phone,
   connected through previously paired ADB. Bluetooth was disabled, and the app
   showed “Bluetooth not enabled”, isolating cloud controls from the digital key.
4. For each reversible control, issue a request, inspect measured telemetry and
   the official app's independent readback, then restore the original setting.
   Test seat levels individually. Allow trunk/window movement to finish.
5. Temporarily capture this integration's own request/response envelopes to
   private local files. Compare identifiers and payload structure, not merely
   successful MQTT publication. Use locally available Android SDK handlers to
   form specific hypotheses, then check their effect against the app.
6. Reproduce code defects with synthetic offline fixtures and test the production
   sender, receiver, permission gate and entity methods. No raw captures, APKs,
   app source, credentials, VINs or locations are included in this contribution.

An accepted response is not independent physical verification. In particular,
horn, light flashes and power-outlet load were not independently observed at the
vehicle. App animations and measured state are identified separately below.

## Findings and corrections

### Exact response correlation

A/C requests were applied while Home Assistant reported a timeout. The vehicle
generated a new `msg_id` but placed the original outgoing ID in `msg_ref`.
Matching that exact reference resolves both acceptance and refusal correctly.
The subsystem and acknowledgement type must still match. Explicit stale,
malformed or conflicting references are rejected; they cannot fall back to
matching a newer command's echo. No subsystem-only fallback is restored.

### Passenger seat position

Position 0 returned code 8 and left passenger heating/ventilation unchanged on
the tested FJ. Position 2 selected levels 1, 2, 3 and Off in both the reported
passenger fields and the official app's corresponding seat graphic. Apply this
exception only to FJ; retain the previously measured positions on other models.
Resolve that model exception when sending, because model discovery can finish
after entity construction. A regression test covers that delayed discovery.
Seat and steering selects now await confirmed sends and propagate rejection or
timeout. A rejected preparatory Off stops the seat sequence; its conservative
ordering and delay are otherwise preserved.

### Trunk state

Opening and closing worked in the app, but the cover continued reading Closed
from `instant.tail.tail_status`. The current door snapshot reports
`trunk_ajar_st_new`: the observed open value was 5 and closed value was 1.
Prefer the newer field using the SDK's known open/closed values. Unknown values
remain unknown; missing/negative newer values retain the legacy tail fallback.
Merge successful door and steering snapshots into their existing sections and
shared MQTT baseline without advancing full-snapshot freshness.

### Silent Mode

The app exposes Silent Mode while Pet Mode is active. Service 3, command 17,
values 1/0 changed both `hvac_silent_mode` and the app's toggle. Add one native
switch and a card control. Enable through the explicit model/cloud flag or
`HVAC_SILENT_MODE`; the measured FJ profile supplies support, and explicit cloud
overrides win. Unknown models gain no inferred hardware support. The switch has
all seven supported translations and a stable English entity ID.

### Parking-light false success

The existing service-5 command-4 requests received no response on this car,
although the switch briefly displayed the requested state. Wait for an exactly
correlated successful response before showing the existing assumed command
state. A missing response now reports a timeout. This does not establish a
working parking-light command for the P7+; none is guessed from the Flash action.

### Service authorization follow-up

The maintainer's previous review noted that a user controlling vehicle B could
authorize a legacy global service that actually dispatched to the first entry,
vehicle A. Offline tests reproduced the mismatch. Scope authorization to the
runtime config entry selected by the handler, including explicit entity targets.
Legacy global services retain their first-entry routing; this is not a new
multi-vehicle targeting API. Administrators and context-free automations retain
their existing semantics. Remove the obsolete fallback comment as requested.

## Live coverage and limits

| Control | Observation |
| --- | --- |
| A/C power, fractional target, LO/HI | App and measured state matched; tested 15.5, 32.5 and restored 24°C. Changing target from Off also starts A/C in the official app; documentation now states this vehicle behavior. |
| Maximum heating/cooling, Refresh, Sterilisation, A/C cleaning | On/off responses and corresponding app modes matched. |
| Windscreen and rear defrosters | On/off matched the app and measured fields. |
| Pet Mode | Enable/disable matched the app on this occasion; its earlier Rear Seat Reminder restriction remains conditional. |
| Silent Mode | Command 17 on/off matched the app toggle and measured HVAC field while Pet Mode was active. |
| Four seats: heating and ventilation | Levels 1–3 and Off matched reported fields and app graphics; passenger used the verified FJ position-2 correction. |
| Steering heating | Low, Medium, High and Off matched telemetry and the app indicator. |
| Doors | Unlock/lock matched the app and measured lock state. |
| Windows | Ventilation and close matched the app and positions; full cloud opening remains unadvertised. |
| Trunk | The corrected native cover and app both reported Open after opening and Closed after closing. |
| Charge limit | 85% appeared in the app; restored 80%. |
| Charging while unplugged | Start rejected with 10401 and stop with 10402, surfaced as errors. This does not validate actual charging. |
| Manual charge flap | Motor controls remain withheld; no hardware support is inferred. |
| Horn and Flash | Responses accepted; sound/light effect not independently verified. |
| Parking lights | No command response; working mapping remains unverified, with false success corrected. |
| Trunk power and timer | Responses reported `powerSwitch` 1/0 and timer 60/5/0; no outlet load was tested. Restored Off/0. |
| X-Guard | Responses reported video setting 1/0. Sensitivity requests 1/3 remained reported as 0; changing sensitivity is not validated and no replacement encoding or hardware inference is made. |
| Wake, force update and X-Guard query | Invoked successfully while the vehicle was already awake; a real sleep-to-wake transition was not tested. |
| HVAC schedules | Listing succeeded. A disabled future appointment creation was rejected; the schedule list stayed empty. Successful create/update/delete remains unvalidated on this account. |

The app also offers AC current and charging/preheat schedules without native
controls here. Those require further protocol, capability and charging-condition
validation. Navigation, arbitrary scenes, firmware installation, summon/movement
and digital-key-only controls were not used as incidental physical tests.

## Regression and compatibility checks

- Exact-reference acceptance/refusal, stale/ambiguous references, wrong subsystem,
  conflicting echoed fields, and the real publish/wait/receive path.
- Runtime service authorization bound to the actual vehicle entry.
- FJ-only passenger exception, including delayed model discovery; legacy mappings and off-first behavior preserved.
- New trunk field precedence, unknown/sentinel handling and unchanged freshness.
- Silent Mode payload, support/permissions, translated naming and card dispatch.
- Parking-light timeout/refusal cannot update the assumed switch state.
- Existing unique IDs, evcc climate/status interfaces, card opt-in and managed
  resource ownership remain covered by the repository suite.

Validation: 66/66 isolated test scripts, 1,046 unittest tests (three optional
skips), 30 card tests, translation checker with zero errors, and documentation
build passed.

The 0.6.29 candidate was installed in Home Assistant. Final native smoke tests
confirmed every passenger heat/ventilation level and Off, steering heating,
Pet/Silent Mode on/off, and trunk Open/Closed against the app. The parking-light
negative case surfaced its timeout without a false state change. All 110 existing
entity IDs were retained, including the evcc climate/status entities; Silent Mode
adds one entity. The bundled card remains managed through the existing opt-in.

Comfort/access settings were restored: A/C Off at 24°C, seats and steering Off,
climate modes Off, doors locked, trunk/windows closed, charge limit 80%, X-Guard
and trunk power Off, trunk timer zero. No new HVAC schedule was created. Temporary
remote capture files and the phone keep-awake helper were removed/stopped, and
the test emulator was shut down. Private diagnostic files remain outside git.
