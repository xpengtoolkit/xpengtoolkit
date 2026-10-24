# Climate command corrections

The official Android command handler uses service 3 with these commands:

| Action | cmd_type | cmd_value |
| --- | --- | --- |
| Power on/off | 1 | 1 / 0 |
| Automatic power on (explicit model flag) | 3 | 1 |
| Integer target temperature | 2 | Integer degrees Celsius |
| Fractional target temperature | 16 | Floating-point degrees Celsius |
| Front defrost | 4 | 1 / 0 |
| Quick cooling | 6 | 1 / 0 |

`HVAC_TEMP_SUPPORT_FLOAT` selects command 16 and half-degree steps. Other cars
use command 2 and whole-degree steps. `HVAC_SUPPORT_TIME_SET` enables the optional
time field; it is omitted otherwise. The cloud `isSupportAcAutoMode` boolean
overrides the Android model choice for automatic power-on (DC/DG).

The former climate-on/target payload sent a temperature with quick-cooling
command 6. A P7+ returned code 8 instead of changing the target. The old fan-speed
payload used command 2, which the official app uses for integer temperature.
That mapping is withdrawn: native climate no longer advertises fan control,
and the retained legacy number refuses to publish it. No substitute fan command
is guessed. Existing raw fan telemetry remains available when reported.

Power, target and front-defrost requests wait for a vehicle acknowledgement.
Rejections and timeouts raise Home Assistant errors. The climate entity no longer
invents an optimistic on/temperature state. Successful service-3 replies merge
their measured HVAC fields into the existing snapshot without changing the full
snapshot timestamp or unrelated telemetry. Empty success replies invent nothing.

Changing a native target sends only the temperature request, but the vehicle may
start a timed A/C session in response. This also occurs when changing the target
in the official Android app with A/C initially off (P7+, 24 October 2026). The explicit
`xpeng.climate_on` service sets its requested target and then powers on, stopping
if the target is rejected. All existing entity unique IDs and evcc interfaces
are retained.

Protocol evidence: Android command/configuration handlers inspected locally;
P7+ software capabilities and rejection/status responses observed on 22 October
2026. The P7+ owner physically confirmed power on, power off and the 22 C
target with integration 0.7.3b1 on the same day. Manual fan control remains
unverified; other climate actions and models have not been physically validated
by this test.
No private captures, app code, account data or identifiers are included.


## Independent app modes

Each mode has one native switch, combining on/off with measured state. Existing
climate, seat, front-defrost and evcc unique IDs are retained. Independent modes
are not forced into a single mutually exclusive preset selector.

| App label | Service | cmd_type | On / off value | Measured instant section/key |
| --- | --- | --- | --- | --- |
| Maximum heating | 3 | 8 | 1 / 0 | hvac.hvac_quick_warm_mode |
| Maximum cooling | 3 | 6 | 1 / 0 | hvac.hvac_quick_cool_mode |
| Refresh | 3 | 11 | 1 / 0 | hvac.hvac_deodorize_mode |
| Sterilisation | 3 | 9 | 1 / 0 | hvac.hvac_high_temp_mode |
| A/C cleaning | 3 | 10 | 1 / 0 | hvac.hvac_clean_mode |
| Rear window defroster | 3 | 14 | 1 / 0 | hvac.hvac_mirror_heat_mode |
| Pet mode | 33 | 1 | 2 / 1 | protectMode.protect_mode_status |
| Silent mode | 3 | 17 | 1 / 0 | hvac.hvac_silent_mode |

Pet telemetry uses 0=off, 1=checking and 2=on. The checking state remains unknown,
rather than being confused with the stop-command value. Successful Pet responses
merge only their reported fields without advancing full-snapshot freshness.

Duration is included only for quick heating/cooling and Refresh when the vehicle
advertises HVAC_SUPPORT_TIME_SET. Cleaning, sterilisation, rear defrost and Pet
mode use their own app payloads without an invented duration. All requests wait
for vehicle acknowledgement and surface refusals/timeouts without optimistic state.

Quick heating/cooling support follows the app's model flag or RAPID_TEMP_UP_AND_DOWN
software flag. The other service-3 modes follow their explicit model/cloud booleans.
Pet mode requires PROTECT_MODE. New controls with unknown support start disabled;
proven support can enable integration-disabled entities, while user disabling wins.
Command policy and current support are checked again before publishing.

Evidence: official Android SDK command/status handlers inspected locally, plus
the owner's 3.16.0 UI resources explicitly linking air_deodorize to Refresh,
high-temperature treatment to Sterilisation and protect mode to Pet Mode.
The P7+ owner physically confirmed maximum heating/cooling, Refresh,
Sterilisation, A/C cleaning and both defrosters on 22 October 2026.
Pet mode was rejected with code 19013; the official app, with Bluetooth off,
reported that remote activation is blocked while Rear Seat Reminder is enabled.
That specific Pet-start response now explains the vehicle setting in Home Assistant.
Successful Pet activation remains unverified on this car. The setting is not changed
by the integration, and this conditional refusal does not mark Pet mode unsupported.
Other vehicle models still need their own validation. No app source, screenshots or
traffic is included.

## Schedule management

The existing schedule services now accept optional vehicle_entity_id, required
when multiple XPENG entries exist. The selected entity determines both the target
vehicle and the command policy. Existing single-car calls remain compatible.
Successful create/update/delete requests refresh the schedule sensor/calendar;
rejections and refresh failures surface as errors without inventing a new list.
The selected Home Assistant timezone is used unless time_zone is explicitly supplied.
The reusable card can create, edit and delete appointments through these services.
It starts A/C with the vehicle's saved settings; it does not invent temperature
or duration fields the existing appointment API does not provide.


## LO and HI

HVAC_TEMP_RANGE_ADJUSTMENT together with HVAC_TEMP_SUPPORT_FLOAT enables LO=15.5
and HI=32.5 through the ordinary fractional-temperature command. Without both,
the accepted range stays 16-32 C. The native entity exposes the extended limits
and a temperature_extremes attribute for a card to label these sentinel values.
The integration sends no additional power-on or quick-heating/cooling request
when changing the target; the vehicle can nevertheless start A/C, as noted above.

The P7+ owner selected LO then HI in the official app with Bluetooth off on
22 October 2026. Fresh recorded vehicle states showed 15.5 and 32.5, then 22 C and power off. This confirms the app/state
mapping; the owner also confirmed both Home Assistant endpoint shortcuts.


## Correlation, partial changes and validation scope

Responses must match the subsystem and request ID, or unambiguously echo both
`cmd_type` and `cmd_value` when an ID is rewritten. A generic subsystem reply
does not confirm a request; firmware without usable identity will time out even
if telemetry later reports that the command took effect. This deliberately
prefers an honest uncertainty over another command's success or refusal.

`xpeng.climate_on` sets the target before requesting power. If power is rejected
after the target is accepted, the service reports a partial change; it does not
attempt an unrequested rollback or claim the car stayed unchanged.

Pet mode and Sterilisation belong to COMFORT and are allowed by Safe/Safe Plus
only when the vehicle supports them. Vehicle state restrictions remain in force.
Physical checks described here apply to the owner's P7+; G6/G9 and other models
still need independent validation of these commands and acknowledgement identity.

## Android command audit (24 October 2026)

The cloud-only P7+ audit in [ANDROID_COMMAND_AUDIT.md](ANDROID_COMMAND_AUDIT.md)
confirmed Silent mode against the official Android 3.16.0 toggle while Pet mode
was active. Support follows an explicit cloud/model flag or HVAC_SILENT_MODE;
the measured FJ profile enables it, and cloud overrides still win. It uses no
invented duration. The reusable card includes the same measured switch.

The audit also confirmed Pet mode on/off successfully on this occasion. The
earlier Rear Seat Reminder refusal remains a conditional vehicle restriction,
not evidence that this model lacks Pet mode.
