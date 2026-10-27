# XPENG Vehicle Card

A standalone Lovelace card for the XPENG Toolkit integration. No extra cards, dependencies, helpers, tokens or vehicle API clients are required. It uses the existing Home Assistant entities and vehicle image.

The visual direction comes from the [official XPENG app screenshots](https://play.google.com/store/apps/details?id=com.xiaopeng.globalcarinfo): pale blue vehicle backdrop, prominent range, restrained icons and green accents. This is an independent Home Assistant card.

A step-by-step walkthrough - enabling the card, adding it, choosing the car,
several vehicles, renamed entities and troubleshooting - is in the
docs site: [Set up the XPENG Vehicle card](https://xpengtoolkit.github.io/xpengtoolkit/tutorials/vehicle-card).

## Install with the integration (0.6.26 and later)

The card is bundled in the integration at
[`custom_components/xpeng/www/xpeng-vehicle-card.js`](../../custom_components/xpeng/www/xpeng-vehicle-card.js).
Install/update **xpengtoolkit/xpengtoolkit** through HACS and restart Home
Assistant. After configuring a vehicle, open **Configure** and enable **Install
and manage the XPENG Vehicle dashboard card**. This is optional and off by default.
Refresh the browser, then select **Edit dashboard → Add card → XPENG Vehicle**.
No separate card repository or file copy is needed. No dashboard or sidebar page
is created, and existing dashboard card configurations are never rewritten.

Enabling the option permits shared resource registration and migration of the
exact legacy local card URL described below. Turning it off for all vehicles or
removing the last opted-in vehicle removes the managed resource. For a manual
installation instead, leave the option off and follow the manual instructions.

Range is shown with up to one decimal place. Tyre pressures use up to two decimals
for bar and one for other pressure units, preserving the sensor's selected unit.

Integration updates also update the card. Its resource URL contains a hash of
the shipped JavaScript, so changed files get a fresh browser cache URL. Multiple
vehicles share one resource; add a separate card for each vehicle.

Existing storage-managed `/local/xpeng-vehicle-card/xpeng-vehicle-card.js`
resources migrate after opt-in, preserving one resource ID and removing exact
duplicates. Existing dashboard card configurations stay valid. The old file in
`www/xpeng-vehicle-card/` is left untouched; it can be removed after migration.

YAML-managed resource lists are left untouched: the integration loads the bundled
module through Home Assistant's frontend API. Remove an old manually configured
XPENG card resource from YAML once to avoid also loading the old copy. Refresh
the browser after that change. Other card resources are unaffected.

### Manual card installation (older integrations)

Download the JavaScript from the path above and copy it to
`/config/www/xpeng-vehicle-card/`. Register a **JavaScript module** resource:

```text
/local/xpeng-vehicle-card/xpeng-vehicle-card.js?v=0.1.7
```

Add the card using its visual editor or YAML:

```yaml
type: custom:xpeng-vehicle-card
name: P7+
image_entity: image.xpeng_p7_vehicle_picture
entity_prefix: xpeng_p7
theme: auto
compact: true
confirm_actions: true
```

`theme` supports `auto`, `light`, and `dark`. Compact mode is the default: access and lights open small panels inside the card. Set `compact: false` for an expanded layout. Confirmations default to enabled. Add the card to any existing dashboard using the card picker entry **XPENG Vehicle**. No separate dashboard or sidebar page is required. The card also supports sections grid sizing.

## Included controls

* **Controls:** door lock/unlock, horn, flash, wake, refresh, trunk, window ventilation/close, parking lights and X-Guard. Charge-port buttons appear only when their cover advertises motorized actions.
* **Climate:** A/C on/off, editable target temperature, front defrost, all exposed seat heating/ventilation levels, and steering-wheel heating. Capability-gated switches add maximum heating/cooling, Refresh, Sterilisation, A/C cleaning, rear defrost and Pet mode. Cabin fan speed appears only if the integration advertises a valid mapping.
* **Scheduling:** an inline editor under Climate lists, creates, edits and deletes vehicle HVAC appointments. Times use Home Assistant’s timezone; the vehicle’s saved A/C settings apply. No selected weekdays means run once. Schedule calls target this card’s selected vehicle and report backend errors.
* **Charging:** measured battery/power/state, charge-limit slider using the entity's min/max/step, and explicit start/stop charging actions.
* **Status:** doors, bonnet, trunk/windows, temperatures, tyre pressures, odometer and software.

Missing entities are omitted, unavailable controls are disabled, and unknown measured values remain unknown. Parking lights default to Off in the card when their switch state is unknown, since this command has no measured MQTT feedback. Reported On/Off states take precedence; the display default does not change the entity state or send a command. Unknown stateless buttons remain usable. Window **Vent** calls the integration's existing `cover.open_cover`; no full-opening or position command is invented. The cloud connection indicator describes Home Assistant's MQTT connection, not whether the vehicle is awake. The vehicle snapshot age is separate from connectivity.

LO/HI shortcuts appear only when the climate entity explicitly advertises those endpoints. Their values follow the native entity’s converted limits, so Fahrenheit displays send the correct display-unit target. Temperature changes do not automatically start A/C. App modes require integration 0.6.26 or newer. The P7+ owner verified the six non-Pet modes and both defrosters. Pet mode reports the vehicle’s Rear Seat Reminder restriction when remote activation is refused; successful Pet activation remains unverified on this car.

Commands use Home Assistant services with entity targets. They do not call XPENG directly or alter evcc configuration. Success means the service request completed; measured telemetry and the last vehicle response remain visible. Errors are shown inline. Trunk opening always requires confirmation with a clearance reminder, including when `confirm_actions: false`; trunk closing sends immediately without confirmation. Other opening actions, unlocking, horn and waking prompt for confirmation by default. Card rendering never sends commands.

## Renamed entities and multiple cars

Choose `image_entity` or `entity_prefix` explicitly for each vehicle. If only one standard XPENG image exists, it can be selected automatically. The card refuses ambiguous auto-discovery. When Home Assistant's frontend entity registry includes unique IDs, renamed entities are resolved on the selected image's device. Set explicit role overrides for any custom names:

```yaml
entities:
  lock: lock.my_car_doors
  driver_heating: select.my_driver_seat
  horn: false
```

Roles are the keys in `ROLES` near the top of the JavaScript file, plus `driver_heating`, `driver_ventilation`, `passenger_heating`, `passenger_ventilation`, `left_rear_heating`, `left_rear_ventilation`, `right_rear_heating`, and `right_rear_ventilation`. A role set to `false` is hidden. The editor keeps advanced YAML options intact.

## Validation and updates

Run `node test-card.cjs`. These isolated tests stub the Home Assistant connection; they never issue real car commands. Visual verification should include a phone-width and a desktop-width view and each tab. Do not test physical actions without the owner's involvement.

Bundled installations update the cache URL automatically; refresh open browser sessions after an integration update. For manual installations, bump the resource's `?v=` value when updating JavaScript. To stop using the card, remove it from your dashboard. If uninstalling the integration, also remove its XPENG card resource from Settings → Dashboards → Resources (and any legacy copied directory). Vehicle entities and dashboard card configuration are managed independently.
