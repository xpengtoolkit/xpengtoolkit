# AC charge-current encoding correction

The public number control, `xpeng.set_charge_current`, current-limit sensor and
TeslaMate export all use amperes. XPENG's wire field does not: both service 4,
`cmd_type=7`, and `instant.charge.electric_current_limit` use a **1..27 index**
representing **6..32 A**.

| Requested/displayed amperes | Wire command and reported index |
| --- | --- |
| 6 | 1 |
| 16 | 11 |
| 20 | 15 |
| 27 | 22 |
| 32 | 27 |

Sending subtracts five; displaying telemetry adds five. Conversion occurs at
the entity/service/export boundary, leaving MQTT snapshots and cached wire data
unchanged. Missing, sentinel and out-of-range wire values remain unknown. The
percentage charge limit is a separate command and has no such conversion.

## Evidence

The owner compared Home Assistant requests with the official app on a P7+ on
6 November 2026: the old HA value 20 appeared as 25 A in the app, and 27 appeared
as 32 A. This was a unit-conversion defect, not proof the requests were ignored.

The independently recovered international Android app **3.16.0** agrees:

- `ChargeSettingActivity.AnonymousClass11` uses slider progress 0..26, sends
  progress plus one, and supplies the same index to its label formatter.
- `ChargeSettingActivity.updateACLimitSettingTv` displays the index plus five.
  `updateChargeStatus` sets slider progress from the reported index minus one.
- `ChargeSettingPresenter.L` forwards that index unchanged to
  `ChargeElectricityLimitCmd`, which calls `MqttSendHandler.I0`.
- `MqttSendHandler.I0` places the supplied index unchanged in `cmd_value` with
  service 4, message type 2 and `cmd_type=7`.

Source SHA-256 hashes (provenance only; proprietary source is not distributed):

- `ChargeSettingActivity.java`:
  `be72ad36924708ec696d37ee4c50c0f03867f1a807c15c76cc7f0ceb2573883a`
- `MqttSendHandler.java`:
  `c16db9fbf8dda887674d944ecd11c14fd73273249fd5e398e0da56b0700ddee2`

The older G6 observations recorded raw 6, 16 and 27, incorrectly labelled as
amperes. These correspond to 11, 21 and 32 A in the official app. Raw 32 is
outside the app's wire range, so its acknowledgement followed by an unchanged
limit does not establish a 27 A vehicle ceiling. The recorded roughly 10.8 A
draw at raw 6 is consistent with an 11 A request, rather than evidence for an
unexplained 6 A floor.

## Verification and upgrade

Regression tests exercise all 27 positions, the owner's two examples, both
command surfaces, sensor registration, TeslaMate output, unknown readings and
rejected commands. Tests use mocked commands; the agent sends no car commands.
Code 200 still means acceptance, and the displayed value follows reported
telemetry rather than being set optimistically.

Install a release containing this correction and restart Home Assistant. Existing entity IDs and config
entries are retained. Any automation compensating for the old five-amp error
should remove that compensation. Existing raw MQTT callers continue to supply
the wire index themselves. No additional physical validation of the corrected
build is claimed until the owner checks it.
