# X-Combo actions and conditions — the complete catalogue

Every action and condition the XPENG X-Combo editor can build, read from the
`personal-tailor` scene resource centre that the phone app itself uses. This
includes resources that are **not reachable from every editing context in the
app** — the app only offers the remote-safe subset when you build a
cloud-executed scene, and two triggers exist *only* in that remote context.

Captured 2026-10-17 against VIN `VIN …123456` (F30 / G9, `carType: FA`,
sale region AU, **XOS 5.9.5.8909**). The menu is identical for every
`carType` tried (FA, F30, G6, G9, X9, P7, D21, H93, E28A, E38, G02, F57A,
D02, D03), so this list is the global XOS 5.x X-Combo vocabulary, not a
per-model one.

## Summary

| Context | Categories | Actions |
| --- | --- | --- |
| **Local scene** (runs on the car) | 17 | **77** |
| **Remote scene** (runs in the cloud) | 6 | 21 (a subset of the 77) |

| Context | Categories | Conditions |
| --- | --- | --- |
| **Local scene trigger** | 13 | **31** |
| **Remote scene trigger** | 5 | 6 (of which **2 exist only remotely**) |
| **If / until sub-condition**, local | 12 | 31 (local triggers minus *tap-to-run*, plus *delay*) |
| **If / until sub-condition**, remote | 5 | 9 |

Union of distinct vocabulary: **77 actions**, **34 conditions** (31 local +
`igOffTrigger` + `guardAlarm` + the `delayTimePoint` sub-condition).

The 17 action categories: Advanced / flow control · Voice assistant · Doors & windows · Lights · Seats · Climate (HVAC) · Driving · Display · Audio · Device connections · Vehicle control · Trunk power · Charging · Sentry mode · 360 camera · Navigation · Send notification.

The 14 condition categories: Tap to run · Time · Enter / leave vehicle · Doors · Lights · Seat belts · Driving · Vehicle control · Device connections · Charging & range · Sentry mode · Environment · Navigation & location · Vehicle locked & ignition off.

## Where this list comes from

The editor is entirely server-driven: the app binary holds no menu list (only
bespoke dialogs for a handful of `nodeId`s such as *broadcast* and *time point*),
so it asks this API and renders whatever comes back. Three endpoints hold the
vocabulary:

```text
POST /personal-tailor/v3/scene/menus
body: {"vin": "<vin>", "carType": "<vehicleTypeCode>", "tecPoint": [<class codes>]}

  tecPoint: ["action_local", "action_remote"]         -> 17 action categories / 77 items
  tecPoint: ["action_remote"]                       ->  6 categories / 21 remote-safe actions
  tecPoint: ["trigger_local", "trigger_remote"]      -> 13 trigger categories / 31 items
  tecPoint: ["trigger_remote"]                      ->  5 categories /  6 remote triggers
  tecPoint: ["trigger_sub_local"]                   -> If/until conditions for local scenes
  tecPoint: ["trigger_sub_remote"]                  -> If/until conditions for remote scenes

POST /personal-tailor/v3/scene/menuDetail
body: {"vin": "<vin>", "carType": "<vehicleTypeCode>", "id": "<menu item id>"}

POST /personal-tailor/v3/scene/paramDetail
body: {"vin": "<vin>", "carType": "<vehicleTypeCode>", "id": "<parameter id>"}
```

Each menu item names the raw car command / signal it emits (`tecPoint`), so the
same data doubles as the *authoritative* list of XOS command URIs. `carType`
is the vehicle's `vehicleTypeCode` (from `/biz/v5/vehicle/v2/myVehicles`),
not its marketing name. Note `carType` is the JSON key — sending
`vehicleType` fails validation.

## How to read the tables

- **Plane** — where the item can be used: *Local only* (car-executed scenes),
  *Local + remote* (also allowed in cloud scenes), or — for conditions —
  which of `Local` / `Remote` / `If/until` menus include it.
- **Command / signal** — the `tecPoint` the item resolves to. `command://…`
  is an XOS car command, `logic://` a vehicle-setting logic flag, `ID_*` a
  raw CAN/signal identifier, `trigger.*` a cloud event, `bizcom://` a
  business command (navigation), `remote.send.message` an app push.
- **Parameters** — the argument names and value ranges the editor exposes.

---

## Actions (77)

### Local scenes — all 77

| Action | UI name (de) | nodeId | Plane | Parameters | Command / signal |
| --- | --- | --- | --- | --- | --- |
| **Advanced / flow control** | *Erweitert* | | | | |
| Repeat until condition is met | Bis die Bedingung erfüllt ist | `microScene` | Local + remote | — | — |
| If condition is met | Wenn die Bedingung erfüllt ist | `ifModule` | Local + remote | — | — |
| Wait | Warten | `waitDouble` | Local + remote | `delay`: 0–59 s, step 1; `delay_minute`: 0–59 min, step 1 | `delay` |
| Stop this X-Combo | Ausführung dieser Combo beenden | `stopRule` | Local + remote | — | `stop` |
| **Voice assistant** | *Sprachassistent* | | | | |
| XPENG voice announcement (Xiaop) | XPENG-Ankündigung | `broadcast` | Local only | — | — |
| **Doors & windows** | *Tür- & Fensterverriegelung* | | | | |
| Trunk | Kofferraum | `trunkSwitch` | Local only | — | `command://control.trunk.open`<br>`command://control.trunk.close` |
| Window switch | Fensterschalter | `windowSwitch` | Local only | `percent`: 21 values: all closed … all open | `command://control.window.driver.open`<br>`command://control.window.passenger.open`<br>`command://control.window.left.rear.open`<br>_(+1)_ |
| Window ventilation | Fensterbelüftung | `windowAiriness` | Local only | — | `command://control.windows.ventilate.on`<br>`command://control.windows.ventilate.off` |
| All door locks | Alle Türverriegelungen | `allDoorLocks` | Local only | — | `command://control.door.lock.on`<br>`command://control.door.lock.off` |
| Child safety lock | Kindersicherung | `childSafetyLock` | Local only | — | `command://control.child.safety.locks.on`<br>`command://control.child.safety.locks.off` |
| Window lock | Fensterverriegelung | `—` | Local only | — | `setWindowLockState` |
| Disable auto-lock on departure | Automatische Verriegelung bei Abfahrt deaktivieren | `disableLeavingLock` | Local only | — | `setBcmPollingLockDisableReq` |
| **Lights** | *Scheinwerfer* | | | | |
| Headlight control | Scheinwerfersteuerung | `headlightSwitch` | Local only | — | `command://control.light.auto.off`<br>`command://control.light.position.on`<br>`command://control.light.low.on`<br>_(+1)_ |
| Rear fog light | Nebelschlussleuchten | `rearFogLightSwitch` | Local only | — | `command://control.rear.mist.light.on`<br>`command://control.rear.mist.light.off` |
| Reading lights | Leseleuchten | `readingLightSwitch` | Local only | `onOff`: On / Off | `setFrontLeftDomeLightSwitchStatus`<br>`setFrontRightDomeLightSwitchStatus`<br>`setRearLeftDomeLightSwitchStatus`<br>_(+1)_ |
| Ambient light switch | Schalter Umgebungslicht | `ambientLightSwitch` | Local only | — | `command://control.light.atmosphere.on`<br>`command://control.light.atmosphere.off` |
| Ambient light brightness | Helligkeit Umgebungslicht | `ambientLightBrightness` | Local only | `number`: 10–100 %, step 1 | `command://control.light.atmosphere.brightness.set` |
| Ambient light mode & colour | Umgebungslichtmodus & -farbe | `ambientLightMode` | Local only | — | — |
| **Seats** | *Sitz* | | | | |
| Driver habit profile switch | Fahrerpräferenzschalter | `carHabitsIdSwitch` | Local only | — | `command://user.car.habit` |
| Driver seat position | Fahrersitzposition | `masterSeatSetting` | Local only | `target`: 91 values: 0 (hinterste Position) … 90 (forward); `target`: 66 values: 20 (back) … 85 (forward); `target`: 101 values: 0 (lowest) … 100 (max); `target`: 101 values: 0（最低） … 100（最高） | `command://control.seat.move.back`<br>`command://control.seat.backrest.move.back`<br>`command://control.seat.move.down`<br>_(+1)_ |
| Passenger seat memory | Beifahrersitzspeicher | `seatsMemory` | Local only | — | `command://control.seat.memory.config` |
| Passenger seat position | Beifahrersitzposition | `copilotSeatSetting` | Local only | `target`: 91 values: 0 (fully back) … 90 (Vorne); `target`: 66 values: 20 (back) … 85 (forward); `target`: 101 values: 0 (lowest) … 100 (max) | `command://control.psn.seat.move.back`<br>`command://control.psn.seat.backrest.move.back`<br>`command://control.psn.seat.move.down` |
| Seat reset | Sitz zurücksetzen | `seat restoration` | Local only | — | `command://control.seat.recovery` |
| Seat ventilation | Sitzbelüftung | `seatAirinessSetting` | Local + remote | — | `command://ac.seat.ventilate.off`<br>`command://ac.seat.ventilate.driver.set`<br>`setPassengerSeatBlowLevel` |
| Seat heating | Sitzheizung | `seatHeatingSetting` | Local + remote | `level`: Off / level 1 / level 2 / level 3 | `command://ac.seat.heating.close`<br>`command://ac.seat.heat.drive.set`<br>`setBcmPsnSeatHeatLevel`<br>_(+2)_ |
| **Climate (HVAC)** | *Klimaanlage* | | | | |
| Front A/C power | Klimaanlagenschalter vorn | `airConditioningStatus` | Local + remote | `enable`: On / Off | `setHvacPowerMode` |
| Front A/C AUTO | Vordere Klimaanlage AUTO | `autoAirConditionerSwitch` | Local + remote | `enable`: On / Off | `setHvacAutoMode` |
| A/C compressor (AC) | Klimaanlage | `airConditioningACStatus` | Local + remote | `enable`: On / Off | `setHvacTempAcMode` |
| Temperature | Temperatur | `airConditionerTempSetting` | Local + remote | `level`: 19 values: LO … HI | `setHvacTempDriverValue`<br>`setHvacTempPsnValue` |
| Front fan speed | Lüftergeschwindigkeit vorn | `airConditioningVolumeSetting` | Local + remote | `level`: 10 values: level 1 … level 10 | `setHvacWindSpeedLevel` |
| Air circulation | Luftzirkulation | `circulationSetting` | Local + remote | `mode`: recirculation / fresh air | `setHvacCirculationMode` |
| Rapid cooling | Schnellkühlung | `rapidCoolingSwitch` | Local + remote | `enable`: On / Off | `setWarpSpeedCoolingSwitch` |
| Rapid heating | Schnellheizung | `rapidHeatingSwitch` | Local + remote | `enable`: On / Off | `setWarpSpeedWarmingSwitch` |
| Intelligent odour removal | Intelligente Geruchsbeseitigung | `intelligentDeodorizationSwitch` | Local only | `enable`: On / Off | `setDeodorizeSwitch` |
| Air purification | Luftreinigung | `airPurificationSwitch` | Local + remote | `enable`: On / Off | `setHvacQualityPurgeMode` |
| Eco mode | Energiesparmodus | `energySavingModeSwitch` | Local only | `enable`: On / Off | `setEconMode` |
| Front airflow mode | Gebläsemodus vorn | `blowingModeSetting` | Local only | — | `command://ac.blow.head.on`<br>`command://ac.blow.foot.on`<br>`command://ac.blow.window.on` |
| Front defog | Beschlag entfernen vorn | `frontDefoggingSwitch` | Local + remote | `enable`: On / Off | `setHvacFrontDefrostMode` |
| Rear defog | Beschlag entfernen hinten | `backDefoggingSwitch` | Local + remote | `enable`: On / Off | `setBcmBackMirrorHeatMode` |
| Heated mirrors | Beheizte Spiegel | `heatedMirrorsSwitch` | Local + remote | `enable`: On / Off | `setBcmBackMirrorHeatMode` |
| Steering wheel heating | Lenkradheizung | `steeringWheel Heated` | Local only | — | `command://ac.steering.heating.off`<br>`command://ac.steering.heating.set` |
| Air outlet switch | Lüftungsschalter | `airOutletSwitch` | Local only | — | `command://ac.wind.outlet.on`<br>`command://ac.wind.outlet.off` |
| Driver air direction | Klimaanlage Fahrer | `masterWindDirectionSetting` | Local only | — | `command://ac.wind.unidirection`<br>`command://ac.wind.mirror.on`<br>`command://ac.wind.front`<br>_(+2)_ |
| Passenger air direction | Klimaanlage Beifahrer | `copilotWindDirectionSetting` | Local only | — | `command://ac.wind.unidirection`<br>`command://ac.wind.mirror.on`<br>`command://ac.wind.front`<br>_(+2)_ |
| Swing (auto-sweep) mode | Swing-Modus | `SweepTheWholeCar` | Local only | — | `command://ac.wind.auto.sweep.on` |
| **Driving** | *Fahren* | | | | |
| Drive mode | Fahrmodus | `drivingModeSetting` | Local only | — | `command://control.modes.driving.comfort`<br>`command://control.modes.driving.normal`<br>`command://control.modes.driving.sport`<br>_(+3)_ |
| Regenerative braking | Energierückgewinnung | `energyRecovery` | Local only | — | `command://control.xpedal.on`<br>`command://control.energy.recycle.high`<br>`command://control.energy.recycle.medium`<br>_(+1)_ |
| Steering assist | Lenkassistent | `steeringWheelAssist` | Local only | — | `command://control.modes.steering.soft`<br>`command://control.modes.steering.normal`<br>`command://control.modes.steering.sport` |
| Driver monitoring alert | Warnmeldung von der Fahrerzustandserkennung | `driverStatusAlert` | Local only | — | `logic://carcontrol.xpilot.safety.dsm_voice_state` |
| Emergency lane keeping | Notfall-Spurhalteassistent | `emergencyLaneKeepingReminders` | Local only | — | `logic://carcontrol.xpilot.safety.overseas_elk` |
| Intelligent speed-limit alert | Warnton intelligenter Assistent Geschwindigkeitsbegrenzung | `SpeedingReminders` | Local only | — | `logic://carcontrol.xpilot.safety.isla_alert` |
| **Display** | *Anzeige* | | | | |
| Screen on/off | Bildschirmanzeige-Schalter | `screenSwitch` | Local only | — | `command://system.polit.screen.on`<br>`command://system.polit.screen.off` |
| Screen brightness | Bildschirmhelligkeitseinstellung | `brightnessSwitch` | Local only | `target`: 1–100 %, step 1 | `command://system.screen.brightness.set.percent`<br>`command://system.icm.brightness.set.percent` |
| Automatic screen brightness | Automatischer Bildschirmschalter | `screenBrightness` | Local only | — | `system.polit.screen.brightness.auto.on`<br>`system.polit.screen.brightness.auto.off`<br>`system.icm.brightness.auto.on`<br>_(+1)_ |
| Day/night mode | Tag-/Nachtmodus | `screenTheme` | Local only | — | `command://system.theme.mode.auto`<br>`command://system.theme.mode.day`<br>`command://system.theme.mode.night` |
| **Audio** | *Stimme* | | | | |
| Volume | Lautstärke | `volumeSetting` | Local only | `change_value`: 0–19, step 1; `change_value`: 0–30, step 1 | `command://system.volume.set` |
| Sound stage position | Klangfeld-Einstellung | `soundPositionSetting` | Local only | `direction`: front centre / whole car centre / rear centre | `command://system.music.sound.direction.set` |
| Sound mode | Klangmodus | `soundFeaturesSetting` | Local only | `style`: dynamic / original / cinema | `command://system.music.sound.effect.style.set` |
| Driver audio mode | Fahrer-Audiomodus | `masterDriverAudioModeSetting` | Local only | `mode`: shared mode / driving / private mode | `command://system.headrest.model.set` |
| **Device connections** | *Geräteanschluss* | | | | |
| Bluetooth switch | Bluetooth-Schalter | `bluetoothSwitch` | Local only | — | `command://system.pilot.bluetooth.on`<br>`command://system.pilot.bluetooth.off` |
| Bluetooth connected device | Bluetooth-verbundene Geräte | `bluetoothConnectsDevice` | Local only | — | `command://control.bluetooth.connect` |
| Car hotspot | Fahrzeug-Hotspot | `carHotspotEnable` | Local only | — | `command://control.hotspot.status` |
| **Vehicle control** | *Fahrzeugsteuerung* | | | | |
| Side mirrors | Seitenspiegel | `rearviewMirrorsSetting` | Local only | `type`: unfold / fold | `setRearViewMirror` |
| Horn | Hupe | `setWhistle` | Local + remote | — | `setBcmCpdAlarmHornOnceReq` |
| Driver-side wireless charging | Kabelloses Laden auf Fahrerseite | `mobileDevicesWirelessChargingSwitch` | Local only | — | `command://control.wireless.charging.on`<br>`command://control.wireless.charging.off` |
| **Trunk power** | *Kofferraum-Stromversorgung* | | | | |
| Trunk power delayed-off | Kofferraum-Stromverzögerung Aus | `delayOutage` | Local only | — | `command://charge.trankpower.power.on`<br>`command://charge.trankpower.power.off` |
| Trunk power off cycle & duration | Kofferraum-Stromverzögerung Aus Zyklus & Dauer | `setPowerOffPeriodAndDuration` | Local only | — | `command://charge.trunk.power.set` |
| **Charging** | *Energie wird geladen* | | | | |
| Charge port cover | Abdeckung Ladeanschluss | `chargingPortOperation` | Local only | — | `command://charge.direct.port.on`<br>`command://charge.direct.port.off` |
| End charging | Ladevorgang Ende | `endCharge` | Local only | — | `stopCharge` |
| Charge limit | Einstellung Ladebegrenzung | `chargingLimitSetting` | Local only | `req`: 50–100 ％, step 1 | `setTboxChargeLimitValue` |
| **Sentry mode** | *Sentry-Modus* | | | | |
| Sentry mode | Sentry-Modus | `sentinelMode` | Local + remote | — | `setCarGuardModeLevel` |
| **360 camera** | *360° Kamera* | | | | |
| Open camera view | Bild öffnen | `openImage` | Local only | — | `command://camera.front.on`<br>`command://camera.rear.on.new`<br>`command://camera.left.on`<br>_(+3)_ |
| Open 3D camera view | 3D-Bild öffnen | `open3DImage` | Local only | — | `command://camera.display.three.d.front`<br>`command://camera.display.three.d.rear`<br>`command://camera.display.three.d.left`<br>_(+5)_ |
| Close camera view | Bild schließen | `closingImagePage` | Local only | — | `gui.page.close.camera` |
| **Navigation** | *Navigation* | | | | |
| Navigation destination | Navigationsziel | `navigationDestination` | Local only | — | `bizcom://private.custom.navi.control.start` |
| End navigation | Navigation beenden | `exitNavigation` | Local only | — | `bizcom://ainavi.control.close` |
| **Send notification** | *Benachrichtigung senden* | | | | |
| Send phone notification | Telefonbenachrichtigung senden | `sendMobileNotifications` | Local + remote | — | `remote.send.message` |

### Remote (cloud) scenes — the 21 the app offers there

Same resources, selected by `tecPoint=["action_remote"]`. Everything outside
this table is **greyed out / absent in the app while you edit a remote scene**.

| Action | nodeId | Parameters |
| --- | --- | --- |
| Repeat until condition is met | `microScene` | — |
| If condition is met | `ifModule` | — |
| Wait | `waitDouble` | `delay`: 0–59 s, step 1; `delay_minute`: 0–59 min, step 1 |
| Stop this X-Combo | `stopRule` | — |
| Seat ventilation | `seatAirinessSetting` | — |
| Seat heating | `seatHeatingSetting` | `level`: Off / level 1 / level 2 / level 3 |
| Front A/C power | `airConditioningStatus` | `enable`: On / Off |
| Front A/C AUTO | `autoAirConditionerSwitch` | `enable`: On / Off |
| A/C compressor (AC) | `airConditioningACStatus` | `enable`: On / Off |
| Temperature | `airConditionerTempSetting` | `level`: 19 values: LO … HI |
| Front fan speed | `airConditioningVolumeSetting` | `level`: 10 values: level 1 … level 10 |
| Air circulation | `circulationSetting` | `mode`: recirculation / fresh air |
| Rapid cooling | `rapidCoolingSwitch` | `enable`: On / Off |
| Rapid heating | `rapidHeatingSwitch` | `enable`: On / Off |
| Air purification | `airPurificationSwitch` | `enable`: On / Off |
| Front defog | `frontDefoggingSwitch` | `enable`: On / Off |
| Rear defog | `backDefoggingSwitch` | `enable`: On / Off |
| Heated mirrors | `heatedMirrorsSwitch` | `enable`: On / Off |
| Horn | `setWhistle` | — |
| Sentry mode | `sentinelMode` | — |
| Send phone notification | `sendMobileNotifications` | — |

---

## Conditions / triggers (34 distinct)

### Every trigger / condition, with the context it is available in

| Condition | UI name (de) | nodeId | Context | Parameters | Signal |
| --- | --- | --- | --- | --- | --- |
| **Tap to run** | *Zum Auslösen tippen* | | | | |
| Tap the run button | Wird nur durch Antippen der Ausführen-Taste ausgelöst | `skipAllTriggers` | Local + Remote | — | `trigger.click` |
| **Time** | *Zeit* | | | | |
| Time point | Zeit | `timePoint` | Local + Remote + If/until | — | `trigger.time.point` |
| Delay | Verzögerung | `delayTimePoint` | If/until | 1–60 Minuten später, step 1 | `trigger.time.delay` |
| **Enter / leave vehicle** | *Einsteigen ins Fahrzeug und Verlassen des Fahrzeugs* | | | | |
| Enter / leave vehicle | Einsteigen ins Fahrzeug und Verlassen des Fahrzeugs | `onOffCar` | Local + If/until | — | `ID_INTELLIGENT_DRIVER_ENTER`<br>`ID_INTELLIGENT_DRIVER_EXIT`<br>`ID_INTELLIGENT_PSN_ENTER`<br>_(+5)_ |
| **Doors** | *Tür* | | | | |
| Door | Tür | `carDoorsSwitch` | Local + If/until | open / closed | `ID_BCM_1ST_LEFT_DOOR_STATE`<br>`ID_BCM_1ST_RIGHT_DOOR_STATE`<br>`ID_BCM_2ND_LEFT_DOOR_STATE`<br>_(+1)_ |
| Child safety lock | Kindersicherung | `childSafetyLock` | Local + If/until | unlocked / locked | `ID_BCM_LCHILDLOCK`<br>`ID_BCM_RCHILDLOCK` |
| **Lights** | *Scheinwerfer* | | | | |
| Rear fog light | Nebelschlussleuchten | `fogLights` | Local + If/until | On / Off | `ID_BCM_REAR_FOG_LAMP_ON` |
| Turn signal | Blinker | `bcmRturnLamp` | Local + If/until | On / Off | `ID_BCM_LTURN_LAMP_SW_ST`<br>`ID_BCM_RTURN_LAMP_SW_ST` |
| Hazard lights | Warnblinkanlage | `doubleFlashLights` | Local + If/until | On / Off | `ID_CPD_ALARM_HAZARD_LIGHT` |
| **Seat belts** | *Sicherheitsgurt* | | | | |
| Driver habit profile switch | Fahrerpräferenzschalter | `carHabitsIdSwitch` | Local + If/until | — | `trigger.user.car.habit` |
| Passenger seat memory switch | Speicherschalter für Beifahrersitz | `seatMemorySwitch` | Local + If/until | — | `trigger.seat.memory.switch` |
| Seat belt | Sicherheitsgurt | `seatBelt` | Local + If/until | fastened / not fastened | `ID_BCM_DRVSEAT_BELTSBR_WARNING`<br>`ID_BCM_PSNGRSEAT_BELTSBR_WARNING`<br>`ID_BCM_2NDLEFTSEAT_BELTSBR_WARNING`<br>_(+2)_ |
| **Driving** | *Fahren* | | | | |
| Speed reaches | Geschwindigkeit erreicht | `speedRanges` | Local + If/until | 0–180 km/h, step 5 | `ID_VCU_RAW_CAR_SPEED` |
| Gear | Gangschaltung | `switchGear` | Local + If/until | D / N / R / P | `ID_VCU_GEAR_LEVEL` |
| **Vehicle control** | *Fahrzeugsteuerung* | | | | |
| Wiper stalk | Wischerschalter | `wipersSpeedSetting` | Local + If/until | OFF / AUTO / LO / HI | `ID_BCM_WIPERSPDSW_ST` |
| Side mirrors | Seitenspiegel | `rearviewMirrors` | Local + If/until | Folded / Unfolded | `ID_BCM_LEFT_REAR_MIRROR_STATE` |
| **Device connections** | *Geräteanschluss* | | | | |
| Bluetooth | Bluetooth | `bluetoothSwitch` | Local + If/until | — | `trigger.bluetooth.open`<br>`trigger.bluetooth.close` |
| Wired screen projection | Kabelgebundene Projektion | `screenCasting` | Local + If/until | — | `trigger.wired.app.play`<br>`trigger.wired.app.stop` |
| **Charging & range** | *Laden & Reichweite* | | | | |
| Charging cable plugged/unplugged | Ladekabel einstecken/ausstecken | `chargingPlug` | Local + If/until | plugged in / unplugged | `ID_VCU_CHARGE_GUN_STATUS` |
| Charging state | Ladezustand | `chargingStatus` | Local + If/until | charging starts / charging ends | `ID_VCU_CHARGE_STATUS` |
| Remaining electric range reaches | Verbleibende elektrische Reichweite erreicht | `drivingDistanceTrigger` | Local + If/until | 0–1000 km, step 50 | `ID_VCU_DSTBAT_DISP` |
| Charged range reaches | Ladereichweite erreicht | `chargingLifeReachesTrigger` | Local + Remote + If/until | 0–1000 km, step 50 | `ID_VCU_CHARGE_ENDURANCE` |
| Estimated time to full | Geschätzte verbleibende Ladezeit | `chargeRemainingTime` | Local + Remote + If/until | 0–419, step 1 | `<`<br>`ID_VCU_CHARGE_COMPLETE_TIME` |
| **Sentry mode** | *Sentry-Modus* | | | | |
| Sentry mode | Sentry-Modus | `sentryMode` | Local + If/until | On / Off | `ID_TBOX_SOLDIER_CAMERASW` |
| Sentry mode triggered (alarm) | Wenn der Sentry-Modus ausgelöst wird | `guardAlarm` | Remote + If/until | — | `ID_GUARD_ALARM_LEVEL` |
| **Environment** | *Umgebung* | | | | |
| Cabin temperature reaches | Innenraumtemperatur erreicht | `innerCarTemp` | Local + If/until | -10–60 ℃, step 1 | `ID_HVAC_INNER_TEMPERATURE` |
| Cabin air quality | Luftqualität im Innenraum | `inCarAirQuality` | Local + If/until | good / moderate / poor / bad | `==`<br>`!=`<br>`ID_HVAC_QUALITY_INNER_LEVEL` |
| Outside temperature reaches | Außentemperatur erreicht | `outsideTemperatureAt` | Local + If/until | -40–45 ℃, step 1 | `ID_ENV_OUTSIDE_TEMPERATURE` |
| Ambient light level | Ambientebeleuchtung | `brightnessLevelTrigger` | Local + If/until | dark / garage / tunnel / evening / overcast / daytime outdoors | `trigger.brightness.level.enter`<br>`trigger.brightness.level.exit` |
| **Navigation & location** | *Navigation & Standort* | | | | |
| Vehicle arrives | Fahrzeugankunft | `vehicleArrives` | Local + If/until | — | `trigger.navi.arrival.destination` |
| Vehicle leaves | Fahrzeugabfahrt | `vehicleLeaves` | Local + If/until | — | `trigger.navi.leave.destination` |
| Estimated arrival time (navigation) | Geschätzte Ankunftszeit (Navigation) | `navigationTimeOfArrival` | Local + If/until | — | `<`<br>`trigger.navi.estimate.arrival.time` |
| Estimated distance to destination (navigation) | Geschätzte Entfernung zum Ziel (Navigation) | `navigationDistanceOfArrival` | Local + If/until | 200–1000 m, step 50 | `<`<br>`trigger.navi.estimate.arrival.distance` |
| **Vehicle locked & ignition off** | *Fahrzeug verriegelt & Zündung ausgeschaltet* | | | | |
| Vehicle locked & ignition off | Bei verriegeltem Fahrzeug & ausgeschalteter Zündung | `igOffTrigger` | Remote + If/until | — | `trigger.ig.off` |

### Remote scene triggers — 6, including 2 that never appear locally

| Condition | UI name (de) | nodeId | Also local? | Signal |
| --- | --- | --- | --- | --- |
| Tap the run button | Wird nur durch Antippen der Ausführen-Taste ausgelöst | `skipAllTriggers` | yes | `trigger.click` |
| Time point | Zeit | `timePoint` | yes | `trigger.time.point` |
| Charged range reaches | Ladereichweite erreicht | `chargingLifeReachesTrigger` | yes | `ID_VCU_CHARGE_ENDURANCE` |
| Estimated time to full | Geschätzte verbleibende Ladezeit | `chargeRemainingTime` | yes | `<`<br>`ID_VCU_CHARGE_COMPLETE_TIME` |
| Sentry mode triggered (alarm) | Wenn der Sentry-Modus ausgelöst wird | `guardAlarm` | **no — remote only** | `ID_GUARD_ALARM_LEVEL` |
| Vehicle locked & ignition off | Bei verriegeltem Fahrzeug & ausgeschalteter Zündung | `igOffTrigger` | **no — remote only** | `trigger.ig.off` |

`igOffTrigger` (*vehicle locked & ignition off*) and `guardAlarm` (*sentry mode
triggered*) exist **only** in the remote trigger menu. The phone app's local
scene editor cannot select them at all.

---

## What is *not* reachable from the app

Three concrete gaps, all reproduced from the API:

1. **Remote-only conditions.** `igOffTrigger` (vehicle locked & ignition off)
   and `guardAlarm` (sentry alarm) are in the remote menu only; they cannot be
   used as a trigger of a car-executed scene.
2. **The local-only action set in remote scenes.** 56 of the 77 actions are
   `action_local` only — seats memory, windows, mirrors, drive mode, display,
   audio, navigation, camera, charge-port control, etc. They are simply not
   offered when the scene runs in the cloud.
3. **Sub-conditions inside if/until.** The *if condition is met* / *repeat
   until condition is met* blocks take their own menu (`trigger_sub_*`),
   which differs slightly from the top-level trigger menu: it drops
   *tap-to-run* and adds a pure *delay* condition.

Two further reasons an item can be absent from your app even though it is in
this catalogue — both are per-resource metadata on the `menuDetail` record:

- `limitVersion`: per-model OS version window (e.g. sentry mode requires
  `F30BEU ≥ 5.5.1`). Your car is `XOS 5.9.5.8909`, so every window in this
  capture is satisfied.
- `label`: release/feature tags (`0131新增`, `OS5.6`, `恢复场景原子资源`, …),
  some of which flag restoration-only resources the editor hides.

## Notes on the vocabulary

- The four *Advanced* actions (`microScene`, `ifModule`, `waitDouble`,
  `stopRule`) are flow control, not car commands — they are what make a
  combo a small program.
- All 77 actions and 31 local conditions are marked `action_local` /
  `trigger_local`: a local scene executes on the car. Only 21/6 are also
  cleared for cloud execution.
- Parameter ranges are the editor's own enums: `Ein/Aus` (On/Off), seat levels
  0–3, A/C temperature `15.5` = LO, `16`–`32` °C in 1 °C steps, `32.5` = HI,
  windows 0–100 % in 5 % steps, speed 0–180 km/h in 5 km/h steps.
- Screens/panels show German because the account's language is German; the
  `nodeNameKey`/`name` fields carry the Chinese source strings, and
  `language: en` headers are ignored by this service.

---

_Machine-readable copy: `xcombo_catalog.json` beside this file. Generated
from live API responses; no guesses, no app-side allowlists._
