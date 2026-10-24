# Set up the XPENG Vehicle card {#vehicle-card}

This tutorial takes you from the integration you already run to a working
**XPENG Vehicle** card on a dashboard: enable the optional card, add it, choose
the car it shows, and configure it. Allow about ten minutes.

You need:

- Home Assistant **2024.7** or newer (the card uses the asynchronous
  static-resource API).
- Integration **0.6.26** or newer, with a vehicle configured.
- Command permissions that allow whatever you expect the card to do - the card
  shows the entities that exist, and a group you have disabled is absent. A new
  install starts at **Safe Plus**, so unlocking, OTA and the V2L outlet are not
  on the card until you widen them; see
  [Command permissions](../reference/command-permissions).

The card is **optional and off by default**. It is not a separate repository,
needs no extra cards or helpers, and creates no dashboard or sidebar page of its
own. It uses your existing XPENG entities and vehicle image, and it never rewrites
a dashboard layout.

## 1. Enable it in the integration

1. Go to **Settings → Devices & Services** and open the **XPENG** integration.
2. Press **Configure**.
3. Turn on **Install and manage the XPENG Vehicle dashboard card**.
4. Submit the form.

That is the consent step. Enabling it lets the integration register one shared
static resource for the bundled JavaScript and manage the matching Lovelace
resource. If you update the integration through HACS, also restart Home
Assistant at this point so the new files are loaded.

What the toggle does and does not do:

- In the normal **storage-managed** dashboard mode it creates or updates one
  resource. If you previously installed the card by hand at
  `/local/xpeng-vehicle-card/xpeng-vehicle-card.js`, that exact resource is
  migrated in place (keeping one ID) and exact duplicates are removed.
- In **YAML-resource** mode it does not edit your YAML. It loads the bundled
  module through Home Assistant's frontend API instead, so remove your old manual
  XPENG card resource from YAML once to avoid loading the old copy as well.
- It is per vehicle entry, but one opted-in vehicle is enough: several cars share
  the one resource. Turning the toggle off for every vehicle, or removing the
  last opted-in vehicle, removes the managed resource.
- It never creates, edits or rearranges a dashboard, and uninstalling the card
  later does not touch your car entities.

Windows, doors and the lock are still controlled by the integration's command
permission presets, not by this toggle.

## 2. Add the card to a dashboard

Refresh the browser, then:

1. Open the dashboard you want the card on and press **Edit dashboard**.
2. Press **Add card**.
3. Search for **XPENG Vehicle** (it has a preview) and pick it.

The visual editor appears with these fields:

| Field | Meaning |
| --- | --- |
| **Vehicle name** | The heading the card shows. Cosmetic only. |
| **Vehicle image** | The `image.xpeng_<vehicle>_vehicle_picture` entity for this car. Required when more than one car exists. |
| **Entity prefix** | Optional. The entity object-ID prefix, e.g. `xpeng_p7`, if you prefer prefix lookup to the image entity. |
| **Appearance** | `auto` follows the dashboard theme; or force `light` / `dark`. |
| **Compact dashboard card** | On by default: controls open in small panels inside the card. Turn it off for an expanded layout. |
| **Confirm unlock, horn, wake and opening actions** | On by default. Trunk **opening** always confirms regardless, with a clearance reminder. |

In a **sections** dashboard the card asks for a 12-column slot (minimum 6);
in the masonry layout it reports nine rows of height. It works in any
dashboard type. The same card in YAML:

```yaml
type: custom:xpeng-vehicle-card
name: P7+
image_entity: image.xpeng_p7_vehicle_picture
entity_prefix: xpeng_p7
theme: auto
compact: true
confirm_actions: true
```

## 3. Choose the car, especially with several cars

With **one** standard XPENG vehicle image in your entity registry the card can
select it automatically. With more than one car you must say which car the card
shows; the card refuses ambiguous auto-discovery and displays *"Choose a vehicle
image entity or entity_prefix in the card editor"*. Set **Vehicle image** or
**Entity prefix** explicitly, and add one card per car - all cars share one
registered resource, so this costs nothing.

Entity prefix rules: lowercase letters, digits and underscores, and it is an
entity object-ID prefix, not a device name (`xpeng_p7` for
`sensor.xpeng_p7_battery`, not `P7+`).

## 4. What the card contains

Four tabs over the vehicle image:

- **Controls** - lock/unlock, horn, flash, wake, refresh, trunk, window
  ventilation/closing, parking lights and X-Guard. Charge-port buttons appear
  only when their cover advertises motorized actions.
- **Climate** - A/C on/off, target temperature, front defrost, seat
  heating/ventilation and steering-wheel heating. Capability-gated modes
  (maximum heating/cooling, Refresh, Sterilisation, A/C cleaning, rear defrost,
  Pet mode, Silent Mode) appear only when the integration reports that switch. LO/HI shortcuts
  appear only when the climate entity advertises those endpoints. Changing a
  temperature does not switch A/C on.
- **Charging** - measured battery, power and charging state, the charge-limit
  slider, and explicit start/stop charging.
- **Status** - doors, bonnet, trunk and windows, temperatures, tyre pressures,
  odometer and software version.

Below the tabs, the card shows the last vehicle response, so a refused command can
be told apart from one that succeeded. Commands go through Home Assistant services
with entity targets: they respect the integration's permission presets and your
Home Assistant user's own entity permissions, and rendering the card never sends a
command.

The HVAC schedule editor under **Climate** lists, creates, edits and deletes
appointments for the card's selected vehicle. Times use Home Assistant's timezone;
no selected weekdays means "run once".

## 5. Renamed entities and hiding controls

The card resolves the standard entity names from its image entity or prefix. If
you renamed entities and the registry no longer lets it resolve them, override
individual roles in YAML with the `entities` map. Set a role to `false` to hide
it:

```yaml
type: custom:xpeng-vehicle-card
image_entity: image.xpeng_p7_vehicle_picture
entities:
  lock: lock.my_car_doors
  driver_heating: select.my_driver_seat
  horn: false
```

The role keys are the entries in `ROLES` near the top of the bundled JavaScript,
plus the seat roles `driver_heating`, `driver_ventilation`, `passenger_heating`,
`passenger_ventilation`, `left_rear_heating`, `left_rear_ventilation`,
`right_rear_heating` and `right_rear_ventilation`. The visual editor keeps
advanced YAML options intact.

## 6. Keep it updated, or remove it

- **Bundled install (recommended):** integration updates carry the card too. The
  resource URL includes a hash of the shipped JavaScript, so a changed file gets a
  fresh browser cache URL; refresh open browser sessions after an update.
- **Manual install (older integrations):** copy
  `custom_components/xpeng/www/xpeng-vehicle-card.js` to
  `/config/www/xpeng-vehicle-card/`, register it as a **JavaScript module**
  resource, and bump the resource's `?v=` value whenever you update the file.
- **Turn it off:** disable the Configure toggle. With no opted-in vehicle left,
  the managed resource is removed. Removing the card from a dashboard is done in
  the normal dashboard editor and is independent of the integration.
- **Uninstall:** removing the last opted-in vehicle entry removes the managed
  resource automatically. A manual install, or the old legacy copy in
  `/config/www/xpeng-vehicle-card/`, is yours to delete - remove it under
  **Settings → Dashboards → Resources** as well.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| **XPENG Vehicle** is not in the card picker | Enable the Configure toggle, then refresh the browser with a hard reload (Ctrl/Cmd+Shift+R). Make sure the integration is 0.6.26 or newer. |
| *Choose a vehicle image entity or entity_prefix* in the card | More than one XPENG car exists. Set **Vehicle image** or **Entity prefix** in the card editor. |
| Some controls are missing | Check [Command permissions](../reference/command-permissions): a disabled group is absent, a read-only group refuses commands. Model capability gating can also hide controls the car does not report. |
| The card looks like an older version after a HACS update | Hard-refresh the browser. The bundled resource URL changes with the file hash; a stale tab keeps the old module until reloaded. |
| The card loads twice / an old copy keeps appearing | You probably still have a manual XPENG resource in YAML or under Settings → Dashboards → Resources. Remove the old one; the built-in install manages its own. |
| Unlock or OTA is missing | Expected on the default **Safe Plus** preset - widen the command permissions if you want them. |
| Card commands show an error inline | That is the vehicle or the service refusing: the text is the integration's error, and measured telemetry remains the source of truth. The same command from Developer Tools will behave the same way. |

The full card behaviour, including manual installation and the role list, is in
the [card guide](https://github.com/xpengtoolkit/xpengtoolkit/blob/main/lovelace/xpeng-vehicle-card/README.md).
