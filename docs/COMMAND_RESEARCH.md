# Official app command research

Observed in the international Android XPENG 3.12.0 app, reviewed 2026-10-22. This documents protocol facts, not proprietary implementation code. No APK, credentials, certificates, identifiers or captured traffic belong in the repository.

Source locations: `MqttSendHandler`, `VehicleMsg`, `VehicleAbilityChecker`, `SoftwareConfig`, `HardwareConfig`, `VehicleConfig`, and the explicit model feature booleans. App versions, vehicle firmware, account permissions and transports can differ. Source discovery is not physical verification.

## Confirmed corrections

- Windows: service 12, message type 2, `cmd_type=1`: value 2 vents, value 1 closes. The owner confirmed ventilation in the phone app with Bluetooth off. The owner physically confirmed ventilation and closing through the corrected 0.7.2b1 integration build on 22 October 2026. No full-opening or position command was found in this MQTT window handler.
- Charging: service 4, `cmd_type=1`: value 1 starts, value 0 stops. Existing integration encodings agree. Vehicle rejection 10401 means charging could not start; it is not a permanent unsupported-feature flag.
- Charge flap: service 4, `cmd_type=3`: value 1 opens, value 0 closes. These are separate from charging start/stop and windows.
- Trunk: service 9, `cmd_type=2`: value 1 opens, value 3 closes. Existing owner-tested encodings agree.
- Seat heat and ventilation: service 30, `cmd_type=position`, `cmd_value=1` for heat or 2 for ventilation, `mode=level`. Do not reinterpret action-enum IDs as MQTT values. Existing driver/passenger position choices retain their vehicle evidence.
- AC charge current limit: service 4, `cmd_type=7`, `cmd_value=<amperes>` — the app's `setChargeElectricityLimit`. Physically confirmed on a live G6 on 3 November 2026: `{"cmd_type": 7, "cmd_value": 16}` returned code 200 and moved `instant.charge.electric_current_limit` from 27 to 16 once the car next uploaded state. Two things make this row worth reading twice. It is **not** an A/C power toggle despite what the name suggests — A/C power is service 3, `cmd_type=1`. And its acknowledgement does not carry `electric_current_limit`; the reply held `charge_status`, `battery_keep_warm_status`, both port statuses and `max_range_charge` only, so unlike the percentage limit on `cmd_type=2` no command-state merge can confirm an ampere change and the readback lags until the next upload. Observed values on this car are 16 and 27 A; the 6–32 A range the control offers is the single-phase socket convention, not a bound measured from the vehicle.

## MQTT handler inventory

All simple rows use message type 2. Values describe the app handler and do not enable new controls. “Compound” includes schedules, queries, structured payloads or runtime-selected values requiring further analysis; it does not mean unsupported. BLE-specific key, parking and door handlers are a separate transport and are not exercised by this integration.

| App action | Service | cmd_type | cmd_value |
| --- | --- | --- | --- |
| batteryTempCtrlSwitch | 37 | 1 | Integer.valueOf(isOn ? 1 : 0) |
| closeACCLean | 3 | 10 | 0 |
| closeACProtect | 33 | 1 | 1 |
| closeACSwitch | 3 | 1 | 0 |
| closeBehindSunShade | 12 | 3 | 0 |
| closeDeodorize | 3 | 11 | 0 |
| closeFastChargePort | 4 | 4 | 0 |
| closeFragranceChannel | 3 | 12 | 0 |
| closeFrontDefrost | 3 | 4 | 0 |
| closeFrontMirrorHeat | 3 | 15 | 0 |
| closeFrontSunShade | 12 | 2 | 0 |
| closeHighTemp | 3 | 9 | 0 |
| closeLeaveCarKeepPower | 9 | 12 | 3 |
| closeQuickCool | 3 | 6 | 0 |
| closeQuickWarm | 3 | 8 | 0 |
| closeRearDefrost | 3 | 14 | 0 |
| closeRootWindow | 12 | 5 | 0 |
| closeSkySunShade | 12 | 4 | 0 |
| closeSlowChargePort | 4 | 3 | 0 |
| closeTrunk | 9 | 2 | 3 |
| closeTrunkPower | compound |  |  |
| closeWindow | 12 | 1 | 1 |
| deleteAcSchedule | compound |  |  |
| deleteBatterySchedule | compound |  |  |
| deleteChargeSchedule | compound |  |  |
| deleteIntelligentChargeSchedule | compound |  |  |
| doubleLock | 9 | 4 | default/omitted (unresolved) |
| flash | 5 | 1 | 1 |
| getSentinel | compound |  |  |
| horn | 6 | 1 | 1 |
| inquireCameraStatus | compound |  |  |
| lock | 9 | 1 | default/omitted (unresolved) |
| openACClean | 3 | 10 | 1 |
| openACProtect | 33 | 1 | 2 |
| openACSwitch | 3 | 1 | 1 |
| openAutoACStitch | 3 | 3 | 1 |
| openBehindSunShade | 12 | 3 | 1 |
| openBonnet | 9 | 10 | 1 |
| openDeodorize | 3 | 11 | 1 |
| openFastChargePort | 4 | 4 | 1 |
| openFragranceChannel | 3 | 13 | Integer.valueOf(channel) |
| openFrontDefrost | 3 | 4 | 1 |
| openFrontMirrorHeat | 3 | 15 | 1 |
| openFrontSunShade | 12 | 2 | 1 |
| openHighTemp | 3 | 9 | 1 |
| openLeaveCarKeepPower | 9 | 12 | Integer.valueOf(lockCar ? 1 : 2) |
| openQuickCool | 3 | 6 | 1 |
| openQuickWarm | 3 | 8 | 1 |
| openRearDefrost | 3 | 14 | 1 |
| openRootWindow | 12 | 5 | 1 |
| openSkySunShade | 12 | 4 | 1 |
| openSlowChargePort | 4 | 3 | 1 |
| openTrunk | 9 | 2 | 1 |
| openTrunkPower | compound |  |  |
| openWindow | 12 | 1 | 2 |
| operateCamera | compound |  |  |
| reqAcSchedule | compound |  |  |
| reqBatterySchedule | compound |  |  |
| reqCarInfo | 19 | 1 | default/omitted (unresolved) |
| reqCarInfoImmediately | 19 | 2 | default/omitted (unresolved) |
| reqCarStatusLivePacket | compound |  |  |
| reqChargeSchedule | compound |  |  |
| reqIntelligentChargeSchedule | compound |  |  |
| reqTrunkPower | compound |  |  |
| sendRemoteLivePacket | compound |  |  |
| setACProtectTemperature | 34 | 1 | default/omitted (unresolved) |
| setACTemperature | compound |  |  |
| setChargeElectricityLimit | 4 | 7 | Integer.valueOf(limit) |
| setChargeLimit | 4 | 2 | Integer.valueOf(limit) |
| setFridge | 32 | 2 | Integer.valueOf(status) |
| setFridgeHoldTime | 32 | 4 | Integer.valueOf(time) |
| setFridgeMode | 32 | 1 | Integer.valueOf(mode) |
| setFridgeTemp | 32 | 3 | Integer.valueOf(temp) |
| setSeatHeat | compound |  |  |
| setSeatVentilation | compound |  |  |
| setSteerHeat | 31 | 1 | Integer.valueOf(mode) |
| startBatteryWarm | 4 | 5 | 1 |
| startCharge | 4 | 1 | 1 |
| startDischarge | 4 | 6 | 1 |
| stopBatteryWarm | 4 | 5 | 0 |
| stopCarInfo | 19 | 0 | default/omitted (unresolved) |
| stopCharge | 4 | 1 | 0 |
| stopDischarge | 4 | 6 | 0 |
| superLock | 9 | 11 | 3 |
| superUnlock | 9 | 11 | 1 |
| switchSilentMode | 3 | 17 | Integer.valueOf(isOpen ? 1 : 0) |
| tempLock | 9 | 3 | default/omitted (unresolved) |
| turnOffSentinel | compound |  |  |
| turnOnSentinel | compound |  |  |
| unlock | 9 | 0 | default/omitted (unresolved) |
| unlockAndStart | 9 | 6 | default/omitted (unresolved) |
| unlockFuelCap | 36 | 1 | 0 |
| updateAcSchedule | compound |  |  |
| updateBatterySchedule | compound |  |  |
| updateChargeSchedule | compound |  |  |
| updateIntelligentChargeSchedule | compound |  |  |
| wakeUpXPU | compound |  |  |

## Capability and visibility rules

The integration keeps software capabilities, allowlisted `myVehicles.function` hardware booleans, and model flags separate. The read-only model overlay is POST `/ocm/app/config/get` with `biz=app`, `module=onlineVehicle`, vehicle identity and model codes. The cloud overlay wins over explicit 3.12.0 model facts. Missing fields remain unknown.

- Charge-flap control uses `isSupportChargePortControl`; its cover retains measured state on a manual flap.
- Rear ventilation uses model `isSupportSeatBackWind` or hardware `REAR_SEAT_WIND`.
- Front/rear seat gates use the model-selected legacy hardware/model path or the software-plus-hardware path. An unknown model path is resolved only when both interpretations agree. Decompiled negated seat expressions are not copied as protocol truth.
- Steering heat uses hardware `WHEEL_HEAT`/`WHEEL_HEAT_DM`, with a positive software fallback when hardware is unknown.
- Sentinel requires `SENTINEL_MODE`, AVM hardware and a non-fleet configuration. Fridge/sunshade require their software and hardware flags.
- Unknown support never disables an entity. Proven unsupported entities are disabled through the entity registry; IDs and history remain. User/config-entry disabled entities are preserved. Integration-disabled entities are enabled when support becomes known. Optional rear-ventilation controls retain their old disabled default while support is unknown.
- Charger readiness, missing telemetry, sleeping vehicles and command rejection codes are not hardware capability evidence.

Existing commands not yet mapped to a reliable gate remain available under the configured command policy. Newly discovered operations are documented here, not blindly added as executable controls.
