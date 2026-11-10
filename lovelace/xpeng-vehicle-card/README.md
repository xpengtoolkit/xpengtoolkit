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
/local/xpeng-vehicle-card/xpeng-vehicle-card.js?v=0.2.1
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
navigation: true
confirm_navigation: true
```

`theme` supports `auto`, `light`, and `dark`. Compact mode is the default: access and lights open small panels inside the card. Set `compact: false` for an expanded layout. Confirmations default to enabled, and sending a destination asks for confirmation too - set `confirm_navigation: false` to send destinations and saved routes without the prompt. Add the card to any existing dashboard using the card picker entry **XPENG Vehicle**. No separate dashboard or sidebar page is required. The card also supports sections grid sizing.

## Included controls

* **Controls:** door lock/unlock, horn, flash, wake, refresh, trunk, window ventilation/close, parking lights and X-Guard. Charge-port buttons appear only when their cover advertises motorized actions.
* **Climate:** A/C on/off, editable target temperature, front defrost, all exposed seat heating/ventilation levels, and steering-wheel heating. Capability-gated switches add maximum heating/cooling, Refresh, Sterilisation, A/C cleaning, rear defrost and Pet mode. Cabin fan speed appears only if the integration advertises a valid mapping.
* **Scheduling:** an inline editor under Climate lists, creates, edits and deletes vehicle HVAC appointments. Times use Home Assistant’s timezone; the vehicle’s saved A/C settings apply. No selected weekdays means run once. Schedule calls target this card’s selected vehicle and report backend errors.
* **Charging:** measured battery/power/state, charge-limit slider using the entity's min/max/step, and explicit start/stop charging actions.
* **Status:** doors, bonnet, trunk/windows, temperatures, tyre pressures, odometer and software.
* **Navigate** (off by default; set `navigation: true`): the car's current position, a destination form and saved routes. Sends `xpeng.send_navigation`; the car plans the route itself. A Google Maps link can fill the coordinates. Inline Google Places search additionally needs a Google API key in `secrets.yaml`; see [Navigation and saved routes](#navigation-and-saved-routes).

Missing entities are omitted, unavailable controls are disabled, and unknown measured values remain unknown. Parking lights default to Off in the card when their switch state is unknown, since this command has no measured MQTT feedback. Reported On/Off states take precedence; the display default does not change the entity state or send a command. Unknown stateless buttons remain usable. Window **Vent** calls the integration's existing `cover.open_cover`; no full-opening or position command is invented. The cloud connection indicator describes Home Assistant's MQTT connection, not whether the vehicle is awake. The vehicle snapshot age is separate from connectivity.

LO/HI shortcuts appear only when the climate entity explicitly advertises those endpoints. Their values follow the native entity’s converted limits, so Fahrenheit displays send the correct display-unit target. Temperature changes do not automatically start A/C. App modes require integration 0.6.26 or newer. The P7+ owner verified the six non-Pet modes and both defrosters. Pet mode reports the vehicle’s Rear Seat Reminder restriction when remote activation is refused; successful Pet activation remains unverified on this car.

Commands use Home Assistant services with entity targets. They do not call XPENG directly or alter evcc configuration. Success means the service request completed; measured telemetry and the last vehicle response remain visible. Errors are shown inline. Trunk opening always requires confirmation with a clearance reminder, including when `confirm_actions: false`; trunk closing sends immediately without confirmation. Other opening actions, unlocking, horn and waking prompt for confirmation by default. Card rendering never sends commands.

Navigation is the exception to entity targets: it calls `xpeng.send_navigation`
with the selected car's `vehicle_entity_id` in the service data, because that
service takes no entity target.

## Navigation and saved routes

The **Navigate** tab is **off by default**; set `navigation: true` in the card
configuration (or the card editor) to show it.

It sends a destination to the car with the integration's
`xpeng.send_navigation` service - the same cloud call as the official app's
**Send Address to Vehicle**. It accepts a name, latitude and longitude, an
optional label, and a Google Maps link that fills the fields. It is a
**destination push, not a route push**: the car plans the route itself from its
current position. The head unit's **Send Address to Vehicle** setting must be
on, and the car model must support address sharing.

**Send to car** and the **Send** buttons on saved routes ask for confirmation
first, naming the destination and its coordinates, so a stray tap cannot push a
destination to the car. Turn that off with `confirm_navigation: false` (or the
editor's **Confirm before sending a destination to the car** switch). This
switch is separate from `confirm_actions`: disabling the other confirmations
leaves navigation prompts in place.

Saved routes are named destinations. Routes added in the card are stored in this
browser's `localStorage`, keyed by the vehicle; they are not synced between
devices and are not part of Home Assistant backups. To pin routes on every
device, list them in the card's YAML:

```yaml
type: custom:xpeng-vehicle-card
entity_prefix: xpeng_800783
routes:
  - name: Work
    latitude: -33.8688
    longitude: 151.2093
  - name: School
    latitude: -33.9019
    longitude: 151.1931
    label: School
```

`routes:` entries are read-only in the card (edit the YAML to change them);
routes added in the card can be deleted. Up to 20 browser routes are kept.
Google Maps short links (`maps.app.goo.gl/...`) cannot be expanded in the
browser; open the link once and paste the full `google.com/maps` URL. A full
link must contain coordinates - `!3d...!4d...`, `@latitude,longitude`, or
`destination=latitude,longitude`.

### Search and share links

The **Search** button uses an optional server-side Google Places lookup when
`rest_command.google_places_search` exists. Results appear in the card with
**Use** and **Share**; **Use** fills the destination and **Share** copies the
place's Google Maps URL. Without that command, **Search Google Maps** opens the
official `google.com/maps/search/?api=1&query=...` URL in a new tab; pick a
place, copy its URL, paste it into the link field, and press **Fill from link**.

#### Google API keys (optional features)

Two independent optional Google features use different keys; both APIs are
billable:

| Feature | Key | Where it lives | Required API | Restrictions |
|---|---|---|---|---|
| Inline place search | Server key | `secrets.yaml` as `google_places_api_key` | **Places API (New)** | Application restrictions **None** (or the HA host's egress IP); API restrictions **Places API (New)** |
| Map / route preview | Browser key | card option `google_maps_embed_key` | **Maps Embed API** | HTTP referrers for the HA origin; API restrictions **Maps Embed API** only |

**Not required:** Geocoding API, Routes API, Directions API, Maps JavaScript
API, or Maps Static API. Search uses Places Text Search (New); the map is a
Maps Embed iframe; the car plans its own route.

**Google Cloud setup**

1. Create or select a project and enable billing.
2. Enable **Places API (New)** and **Maps Embed API** (they are separate
   services in the API library).
3. Create the **server key**: Application restrictions **None** (or the HA
   host's public egress IP), API restrictions **Places API (New)**. Do **not**
   add HTTP referrer restrictions - Google rejects referrer-restricted keys
   server-side.
4. Create the **browser key**: Application restrictions **HTTP referrers**
   (for example `http://192.168.1.88:8123/*` plus any HTTPS origin), API
   restrictions **Maps Embed API** only.
5. Put the server key in `secrets.yaml`, the browser key in the card YAML, then
   restart Home Assistant after changing `secrets.yaml` or
   `configuration.yaml`.

Inline search is optional because it needs the billable server key. **The key
must be in `secrets.yaml`** (as `google_places_api_key`); keep it server-side
and expose it only to HA's REST command:

```yaml
# secrets.yaml
google_places_api_key: "YOUR_KEY"

# configuration.yaml
rest_command:
  google_places_search:
    url: "https://places.googleapis.com/v1/places:searchText"
    method: POST
    headers:
      Content-Type: "application/json"
      X-Goog-Api-Key: !secret google_places_api_key
      X-Goog-FieldMask: "places.displayName,places.formattedAddress,places.location,places.id,places.googleMapsUri,places.types"
    content_type: "application/json"
    payload: >-
      {"textQuery":{{ query | tojson }},"maxResultCount":{{ limit | default(5) | int }},"languageCode":"en"}
    timeout: 20
```

The card calls the command with `return_response`, so the server key never
reaches the browser. A referrer-restricted browser key will not work
server-side; use the server key described above.

**Copy share link** copies the official Google Maps URL for the typed
destination - coordinates when present, otherwise the name. Each saved route and
the car position also have **Copy link**. On a plain-HTTP HA install the
Clipboard API is unavailable, so the card falls back to a hidden textarea and
`execCommand("copy")`; if both fail it shows the URL so it can be copied
manually.

### Route map (optional)

Set `google_maps_embed_key` to a **Google Maps Embed API browser key** to show a
map on the Navigate tab: the car position alone, or a route preview when a
destination has coordinates. The route is Google's preview; the car's built-in
navigation plans its own route, and the map is never sent to the car.

Use a **separate browser key**, not the server Places key:

- **Application restrictions:** HTTP referrers, e.g.
  `http://192.168.1.88:8123/*` (add your HTTPS origin if you use one).
- **API restrictions:** Maps Embed API only.

The browser key is visible to dashboard users. Without it the map is omitted and
the card shows a note.

**Troubleshooting**

- `REQUEST_DENIED` or "API keys with referer restrictions cannot be used with
  this API": the server key has an HTTP referrer restriction; remove it.
- "This API is not activated": enable **Places API (New)** or **Maps Embed
  API** in that project.
- "The provided API key is invalid": wrong project/key, or the key is restricted
  to APIs the card does not use.
- Search returns no places: check billing, the Places API restriction, and the
  query. Without the REST command the card falls back to a Google Maps link.
- Map is blank: check the browser key's HTTP referrer and Maps Embed API
  restriction, and that the dashboard is not blocked by an ad-blocker.
- `rest_command.google_places_search` not found: add it to
  `configuration.yaml` and restart Home Assistant.

While the Navigate tab is open, background vehicle GPS/state updates do **not**
redraw the tab, because that would reload the Google iframe. Press **Refresh map
with current location** on the map panel (or **Refresh** in the Navigation
header): the card requests a fresh vehicle update, waits briefly, then redraws
with the latest position. This can take a few seconds and needs the car online;
if it stays asleep, use **Wake vehicle** under Controls first. Switching tabs or
a search/send action also redraws.

```yaml
type: custom:xpeng-vehicle-card
entity_prefix: xpeng_800783
navigation: true
google_maps_embed_key: "YOUR_BROWSER_KEY"
```

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
