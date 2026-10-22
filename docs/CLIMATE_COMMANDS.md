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

Changing a native target does not turn on the car's A/C. The explicit
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
