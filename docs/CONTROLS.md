# Consolidated controls and capability discovery

New config entries use covers for trunk, windows and charge port, a charging
switch, and one climate controller for temperature and front defrost. State
comes from vehicle telemetry; acknowledgement is not treated as
physical movement. Invalid selections, rejected commands and missing responses
raise visible errors. Existing command-policy permissions apply to each direction.

## Existing installations

Config-entry migration enables **Keep legacy buttons and duplicate sensors** for
existing users. Update automation references before turning it off. Compact setup
preserves the former primary sensor's object ID (the part after the domain),
name, icon, area, labels and aliases on its replacement. Already customized
controllers are left alone.
Only explicitly replaced duplicates are removed. Turning legacy mode on registers
their original unique IDs again, allowing Home Assistant to recover retained
deleted-entry metadata. History is not purged.

evcc's SOC/range, charge-limit, climate binary sensor, wake, status/power,
odometer, cable-presence and timestamp interfaces remain available. A climate
controller does not replace the on/off binary sensor required by `climater`.
See [the evcc guide](../www/docs/guide/evcc.md).

## Protocol evidence

XPENG Android 3.12.0's MQTT handler uses service 12, message type 2, cmd_type 1:
value 2 vents the windows; value 1 closes them. A P7+ owner physically confirmed
both through this integration on 22 October 2026. Full opening, target positions
and stop/motion commands are not advertised. The old service-4 payload controlled
the charge flap. Charging start/stop remains service 4, cmd_type 1, value 1/0.
Code 10401 means charging could not start; it does not imply unsupported hardware.

Climate power on/off and a 22 C target were physically confirmed on the P7+ on
22 October 2026. Capability flags select the separate power and temperature
commands. The old fan command actually sets integer temperature and is withheld;
raw fan telemetry is retained. See [climate command corrections](CLIMATE_COMMANDS.md).

## Feature support

Software capabilities, allowlisted myVehicles.function hardware flags, explicit
Android 3.12.0 model facts and the read-only /ocm/app/config/get overlay are kept
separate. The cloud overlay wins over model facts. Missing fields stay unknown.

- Seat controls use the model-selected hardware/model or software/hardware path.
- Rear ventilation uses model isSupportSeatBackWind or hardware REAR_SEAT_WIND.
- Steering heat uses WHEEL_HEAT/WHEEL_HEAT_DM, with a positive software fallback.
- Sentinel combines its software flag, AVM hardware and non-fleet configuration.
- Charge-flap motor control uses isSupportChargePortControl; state stays visible.

Only proven unsupported controls are integration-disabled. User/config-entry
disabling and unique IDs are retained. Known support enables integration-disabled
optional controls; unknown support preserves previous defaults. Cable readiness,
sleeping vehicles, missing telemetry and command rejection codes are not hardware
capability evidence. Model facts and app behavior may differ on newer firmware.
