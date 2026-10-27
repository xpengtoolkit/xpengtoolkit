/* XPENG Vehicle Card v0.1.9 · No dependencies. Uses existing Home Assistant entities. */
(() => {
  const VERSION = "0.1.9";
  const ROLES = {
    image: ["image", "vehicle_picture"], battery: ["sensor", "battery"], range: ["sensor", "range"],
    range_type: ["sensor", "range_type"], lock: ["lock", "door_lock"], trunk: ["cover", "trunk"],
    windows: ["cover", "windows"], charge_port: ["cover", "charge_port"], climate: ["climate", "climate"],
    horn: ["button", "honk_horn"], flash: ["button", "flash_lights"], wake: ["button", "wake_vehicle"],
    refresh: ["button", "force_update"], parking_lights: ["switch", "parking_lights"],
    guard: ["switch", "x_guard"], charging: ["switch", "charging"], charge_limit: ["number", "charge_limit"],
    charge_state: ["sensor", "charging_state"], charge_power: ["sensor", "charge_power"],
    charge_time: ["sensor", "charge_time_remaining"], plugged_in: ["binary_sensor", "plugged_in"],
    cabin: ["sensor", "cabin_temperature"], outside: ["sensor", "weather_at_vehicle"],
    odometer: ["sensor", "odometer"], updated: ["sensor", "last_data_update"],
    connected: ["binary_sensor", "mqtt_connected"], feedback: ["sensor", "command_feedback"],
    steering: ["select", "steering_wheel_heating"], doors: ["binary_sensor", "doors"],
    bonnet: ["binary_sensor", "bonnet"], driver_door: ["binary_sensor", "driver_door"],
    passenger_door: ["binary_sensor", "passenger_door"], rear_left_door: ["binary_sensor", "rear_left_door"],
    rear_right_door: ["binary_sensor", "rear_right_door"], software: ["sensor", "xos_version"],
    tyre_fl: ["sensor", "tyre_pressure_front_left"], tyre_fr: ["sensor", "tyre_pressure_front_right"],
    tyre_rl: ["sensor", "tyre_pressure_rear_left"], tyre_rr: ["sensor", "tyre_pressure_rear_right"],
  };
  const CLIMATE_MODES = [
    ["max_heating","Maximum heating","weather-sunny","maximum_heating"],
    ["max_cooling","Maximum cooling","snowflake","maximum_cooling"],
    ["refresh_air","Refresh","fan","refresh"],
    ["sterilisation","Sterilisation","shield-sun","sterilisation"],
    ["ac_cleaning","A/C cleaning","air-filter","a_c_cleaning"],
    ["rear_defrost","Rear window defroster","car-defrost-rear","rear_window_defroster"],
    ["pet_mode","Pet mode","dog","pet_mode"],
    ["silent_mode","Silent mode","weather-night","silent_mode"],
  ];
  const MODE_KEYS = {refresh_air:"refresh"};
  for(const [key] of CLIMATE_MODES)ROLES[key]=["switch","climate_"+(MODE_KEYS[key]||key)];
  ROLES.schedules=["sensor","hvac_schedules"];
  const SEATS = [["driver", "Driver"], ["passenger", "Passenger"], ["left_rear", "Rear left"], ["right_rear", "Rear right"]];
  for (const [seat] of SEATS) for (const kind of ["heating", "ventilation"]) ROLES[`${seat}_${kind}`] = ["select", `${seat}_seat_${kind}`];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const numeric = (v) => v !== null && v !== undefined && v !== "" && Number.isFinite(Number(v)) ? Number(v) : null;
  const known = (s) => s && !["unknown", "unavailable", ""].includes(s.state);
  const icon = (name) => `<ha-icon icon="mdi:${esc(name)}"></ha-icon>`;
  const css = `
    .mode.pet{grid-column:1/-1}.mode.pet .action{flex-direction:row;min-height:65px}.mode .action{width:100%}.mode>.small{width:100%;margin-top:5px}.schedule-record{border-bottom:1px solid var(--xp-line);padding:12px 0;font-size:12px}.schedule-form{border-top:1px solid var(--xp-line);margin-top:15px;padding-top:15px}.schedule-days{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0;font-size:11px}.schedule-days label{display:flex;align-items:center;gap:3px}.schedules input[type=time]{background:var(--xp-soft);color:var(--xp-text);border:1px solid var(--xp-line);border-radius:9px;padding:9px;font:inherit}.schedules input[type=checkbox]{accent-color:var(--xp-accent)}
    :host{display:block;container-type:inline-size;--xp-bg:#f8fafc;--xp-surface:#fff;--xp-soft:#eef2f6;--xp-text:#18222b;--xp-muted:#667480;--xp-line:#e2e8ed;--xp-accent:#536d2a;--xp-tint:#eaf1dc;--xp-blue:#2775ad;--xp-hero:linear-gradient(160deg,#dcebf5 0%,#eef4f7 58%,#f8fafc 100%);font-family:var(--paper-font-body1_-_font-family,Inter,system-ui,sans-serif)}
    :host([dark]){--xp-bg:#131c23;--xp-surface:#1c2932;--xp-soft:#26343e;--xp-text:#edf3f7;--xp-muted:#a6b6c2;--xp-line:#30404b;--xp-accent:#bedc88;--xp-tint:#2c3d28;--xp-blue:#8acaff;--xp-hero:linear-gradient(155deg,#294352,#1c2d38 65%,#131c23)}
    *{box-sizing:border-box}ha-card{background:var(--xp-bg);color:var(--xp-text);border:1px solid var(--xp-line);border-radius:28px;overflow:hidden;box-shadow:0 12px 40px #102c4410;position:relative}
    button,input,select{font:inherit}button{cursor:pointer;color:inherit;border:0}button:disabled{cursor:default;opacity:.38}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid var(--xp-blue);outline-offset:3px}button:hover:not(:disabled){filter:brightness(.96)}ha-icon{--mdc-icon-size:23px;flex-shrink:0}h2,h3,p{margin:0}h2{font-size:21px;font-weight:600;letter-spacing:-.6px}h3{font-size:15px;font-weight:600}.muted{color:var(--xp-muted);font-size:12px}.eyebrow{font-size:10px;font-weight:600;letter-spacing:2.5px;color:var(--xp-muted);text-transform:uppercase}.hero{background:var(--xp-hero);padding:26px 26px 0}.top{display:flex;justify-content:space-between;align-items:center;gap:12px}.brand{display:flex;gap:12px;align-items:center}.mark{width:30px;height:23px;display:grid;place-items:center;font-size:30px;line-height:1;font-weight:300;transform:scaleX(1.35)}.cloud{display:flex;gap:5px;align-items:center;font-size:10px;color:var(--xp-muted);white-space:nowrap}.dot{width:5px;height:5px;border-radius:50%;background:#8ca45b}.dot.offline{background:#c28e50}.range-row{display:flex;align-items:end;justify-content:space-between;margin-top:26px}.range-value{font-size:47px;line-height:1;font-weight:500;letter-spacing:-2px}.range-unit{font-size:15px;margin-left:6px;color:var(--xp-muted);letter-spacing:0}.range-caption{font-size:10px;color:var(--xp-muted);margin-top:7px;text-transform:uppercase;letter-spacing:1.2px}.battery{text-align:right;min-width:90px}.battery-value{font-size:18px;font-weight:500}.battery-track{height:4px;border-radius:4px;width:88px;background:var(--xp-line);margin:9px 0 5px;overflow:hidden}.battery-fill{height:100%;background:#8ea758;width:var(--level,0%)}.vehicle{height:214px;display:grid;place-items:center;margin:0 -16px;position:relative}.vehicle img{width:100%;height:100%;object-fit:contain;position:absolute;inset:0;mix-blend-mode:multiply}.vehicle .fallback{opacity:.13;--mdc-icon-size:180px}:host([dark]) .vehicle img{mix-blend-mode:normal;border-radius:18px}.hero-status{display:flex;gap:7px;justify-content:center;flex-wrap:wrap;padding-bottom:20px}.pill{display:inline-flex;align-items:center;gap:5px;border-radius:20px;padding:6px 10px;background:color-mix(in srgb,var(--xp-surface) 75%,transparent);font-size:11px}.pill ha-icon{--mdc-icon-size:14px}.pill.warn{color:#ac5b2f}.hero-bottom{display:flex;justify-content:space-between;gap:8px;padding:0 0 20px;font-size:11px;color:var(--xp-muted)}
    .workspace{padding:0 22px 20px;min-width:0}.tabs{display:flex;gap:6px;border-bottom:1px solid var(--xp-line);margin-bottom:21px}.tab{background:none;padding:18px 3px 14px;flex:1;font-size:12px;color:var(--xp-muted);border-bottom:2px solid transparent}.tab[aria-selected=true]{border-color:var(--xp-text);color:var(--xp-text);font-weight:600}.section-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:18px 0 12px}.section-head:first-child{margin-top:0}.actions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}.action{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;min-height:87px;border-radius:17px;background:var(--xp-surface);border:1px solid var(--xp-line);font-size:11px;position:relative;text-align:center;padding:12px 6px}.action .caption{font-size:10px;color:var(--xp-muted)}.action.active{color:var(--xp-accent);background:var(--xp-tint);border-color:color-mix(in srgb,var(--xp-accent) 24%,var(--xp-line))}.action.soft{min-height:70px;background:transparent}.panel{background:var(--xp-surface);border:1px solid var(--xp-line);border-radius:18px;padding:16px;margin-bottom:12px}.row{display:flex;justify-content:space-between;align-items:center;gap:10px}.row+.row{margin-top:14px}.state{font-size:12px;color:var(--xp-muted);text-align:right}.inline-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}.small{background:var(--xp-soft);padding:9px 13px;border-radius:11px;font-size:12px;min-height:36px}.small.active{background:var(--xp-tint);color:var(--xp-accent)}.small.primary{background:var(--xp-text);color:var(--xp-surface)}.empty{padding:30px 10px;color:var(--xp-muted);font-size:13px;text-align:center}.feedback{margin-top:20px;border-top:1px solid var(--xp-line);padding-top:12px;font-size:11px;color:var(--xp-muted);display:flex;gap:8px;align-items:start;line-height:1.5}.feedback ha-icon{--mdc-icon-size:15px;margin-top:1px}.feedback.error{color:#bb523f}.feedback time{display:block;font-size:10px;opacity:.8}.notice{font-size:12px;padding:11px 13px;margin-bottom:14px;background:var(--xp-tint);border-radius:12px;line-height:1.5}.notice.error{background:#b44b3d19;color:#bc5140}.note{font-size:11px;line-height:1.6;color:var(--xp-muted);margin:10px 0}.climate-target{display:flex;align-items:center;justify-content:center;gap:22px;margin:24px 0}.temperature{font-size:48px;letter-spacing:-2px;font-weight:400;line-height:1}.temperature small{font-size:20px;color:var(--xp-muted);letter-spacing:0}.round{width:40px;height:40px;border-radius:50%;background:var(--xp-soft);font-size:21px}.temperature-label{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--xp-muted);text-align:center;margin-top:8px}.power{background:var(--xp-soft);width:38px;height:38px;border-radius:50%;display:grid;place-items:center}.power.active{background:var(--xp-tint);color:var(--xp-accent)}.select-row{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:12px;margin-top:14px}.select-row select{max-width:55%;background:var(--xp-soft);color:var(--xp-text);border:0;border-radius:10px;padding:9px;font-size:12px}.seats{display:grid;grid-template-columns:1fr 1fr;gap:9px}.seat{padding:14px 11px;margin:0}.seat-title{display:flex;align-items:center;gap:6px;font-size:12px;margin-bottom:14px}.seat-title ha-icon{--mdc-icon-size:17px;color:var(--xp-muted)}.levels{display:flex;gap:3px;margin-top:7px}.level{flex:1;min-width:0;min-height:32px;border-radius:8px;background:var(--xp-soft);font-size:10px;padding:5px 1px}.level[aria-pressed=true]{background:var(--xp-tint);color:var(--xp-accent)}.level.heat[aria-pressed=true]{background:#c8792820;color:#bc682a}.level.vent[aria-pressed=true]{background:#348bb51c;color:var(--xp-blue)}.seat-kind{font-size:10px;color:var(--xp-muted);display:flex;align-items:center;gap:4px;margin-top:11px}.seat-kind ha-icon{--mdc-icon-size:14px}.charge-value{font-size:30px;letter-spacing:-1px}.charge-value small{font-size:14px;letter-spacing:0;color:var(--xp-muted)}input[type=range]{width:100%;accent-color:#8ea758;margin:19px 0 10px;min-height:26px;cursor:pointer}.range-labels{display:flex;justify-content:space-between;font-size:10px;color:var(--xp-muted)}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:10px}.metric{padding:15px;border-radius:14px;background:var(--xp-soft)}.metric-value{font-size:18px;margin-top:7px}.status-list .row{border-bottom:1px solid var(--xp-line);padding:11px 0;margin:0;font-size:12px}.status-list .row:last-child{border:0}.tyres{display:grid;grid-template-columns:1fr 1fr;gap:10px}.tyre{border-radius:14px;background:var(--xp-soft);padding:13px;text-align:center}.tyre strong{font-size:17px;font-weight:500}.tyre small{display:block;margin:5px 0;font-size:10px;color:var(--xp-muted)}.modal-backdrop{position:absolute;inset:0;background:#15202a77;display:flex;align-items:center;justify-content:center;z-index:5;padding:24px;backdrop-filter:blur(4px)}.modal{background:var(--xp-surface);border-radius:22px;padding:24px;max-width:330px;box-shadow:0 10px 60px #0003}.modal p{font-size:13px;line-height:1.7;margin:15px 0;color:var(--xp-muted)}.modal .inline-actions{justify-content:flex-end}
    @container (min-width:760px){.layout{display:grid;grid-template-columns:minmax(300px,.95fr) minmax(360px,1.05fr)}.hero{padding:34px 30px 10px;display:flex;flex-direction:column}.vehicle{flex:1;min-height:270px;max-height:370px;margin:10px -20px}.range-row{margin-top:38px}.hero-status{margin-top:auto}.workspace{padding:4px 26px 23px;min-height:570px}.range-value{font-size:58px}.range-caption{font-size:11px}.hero-bottom{padding-bottom:13px}.tabs{margin-bottom:25px}}
    @container (max-width:360px){.hero{padding:20px 18px 0}.workspace{padding:0 14px 16px}.vehicle{height:180px}.range-value{font-size:41px}.action{font-size:10px}.seat{padding:12px 8px}.tabs{gap:0}.tab{font-size:11px}.cloud{font-size:9px}.level{font-size:9px}}
    :host([compact]) .layout{display:block}:host([compact]) .hero{padding:18px 18px 0;display:block}:host([compact]) .range-row{margin-top:14px}:host([compact]) .range-value{font-size:36px}:host([compact]) .range-caption{font-size:9px;margin-top:5px}:host([compact]) .vehicle{height:142px;min-height:0;margin:-12px -8px -6px}:host([compact]) .hero-status{padding-bottom:9px;gap:5px}:host([compact]) .pill{font-size:10px;padding:5px 8px}:host([compact]) .hero-bottom{font-size:10px;padding-bottom:10px}:host([compact]) .workspace{padding:0 15px 15px;min-height:0}:host([compact]) .tabs{margin-bottom:14px}:host([compact]) .tab{padding-top:14px;padding-bottom:11px}:host([compact]) .action{min-height:72px;gap:6px;padding:10px 5px}:host([compact]) .action ha-icon{--mdc-icon-size:22px}:host([compact]) .feedback{margin-top:14px;padding-top:10px}.modal .panel{border:0;padding:0;margin-top:18px}.modal .panel .row{display:none}.modal{width:100%}.level{min-height:36px}
    .temperature-input{width:105px;background:none;color:inherit;font:inherit;letter-spacing:inherit;border:0;outline-offset:3px;text-align:center;padding:0;appearance:textfield;-moz-appearance:textfield}.temperature-input::-webkit-inner-spin-button,.temperature-input::-webkit-outer-spin-button{appearance:none;margin:0}
    @media(prefers-reduced-motion:no-preference){button{transition:background .16s,opacity .16s,filter .16s}}
  `;

  class XPengVehicleCard extends HTMLElement {
    constructor() {
      super(); this.attachShadow({mode:"open"}); this._tab = "controls"; this._actions = new Map();
      this.shadowRoot.addEventListener("click", (ev) => {
        const b = ev.target.closest("button"); if (!b || b.disabled) return;
        if(b.dataset.scheduleToggle!==undefined){this._showSchedules=!this._showSchedules;this._render();return;}
        if(b.dataset.scheduleEdit!==undefined){this._editSchedule(b.dataset.scheduleEdit);return;}
        if(b.dataset.scheduleReset!==undefined){this._scheduleDraft=null;this._render();return;}
        if (b.dataset.tab) { this._tab = b.dataset.tab; this._message = null; this._render(); return; }
        if (b.dataset.panel) { this._panel = b.dataset.panel; this._render(); queueMicrotask(() => this.shadowRoot.querySelector("[data-panel-close]")?.focus()); return; }
        if (b.dataset.panelClose !== undefined) { this._panel = null; this._render(); return; }
        if (b.dataset.cancel !== undefined) { this._confirmation = null; this._render(); return; }
        if (b.dataset.confirm !== undefined) { const a = this._confirmation; this._confirmation = null; if (a) this._send(a); return; }
        const a = this._actions.get(b.dataset.action); if (a) this._run(a);
      });
      this.shadowRoot.addEventListener("change", (ev) => {
        if(ev.target.dataset.scheduleField||ev.target.dataset.scheduleDay){
          this._scheduleDraft=this._scheduleDraft||{start_time:"07:30",days:[],enabled:true};
          const field=ev.target.dataset.scheduleField;
          if(field)this._scheduleDraft[field]=field==="enabled"?ev.target.checked:ev.target.value;
          else {const day=Number(ev.target.dataset.scheduleDay);this._scheduleDraft.days=[...new Set(this._scheduleDraft.days.filter(d=>d!==day).concat(ev.target.checked?[day]:[]))];}
          this._render();return;
        }
        const key = ev.target.dataset.key; if (!key) return;
        if (key === "charge_limit") this._send({key, service:"number.set_value", data:{value:Number(ev.target.value)}, label:"Set charge limit"});
        if (key === "temperature") this._send({key:"climate", service:"climate.set_temperature", data:{temperature:this._temperatureExtremes()[ev.target.value.trim().toUpperCase()]??Number(ev.target.value)}, label:"Set target temperature"});
        if (key === "fan_mode") this._send({key:"climate", service:"climate.set_fan_mode", data:{fan_mode:ev.target.value}, label:"Set fan speed"});
        if (key === "preset_mode") this._send({key:"climate", service:"climate.set_preset_mode", data:{preset_mode:ev.target.value}, label:"Set climate preset"});
      });
      this.shadowRoot.addEventListener("input", (ev) => {
        if (ev.target.dataset.key === "charge_limit") this.shadowRoot.querySelector("[data-limit-preview]").textContent = `${ev.target.value}%`;
      });
      this.shadowRoot.addEventListener("focusout", () => { if (this._deferred) { this._deferred = false; queueMicrotask(() => this._render()); } });
      this.shadowRoot.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape" && (this._confirmation || this._panel)) { this._confirmation = null; this._panel = null; this._render(); }
        if (ev.key === "Tab" && (this._confirmation || this._panel)) {
          const buttons=[...this.shadowRoot.querySelectorAll('.modal button:not(:disabled)')];
          if(buttons.length && ev.shiftKey && this.shadowRoot.activeElement===buttons[0]) { ev.preventDefault();buttons.at(-1).focus(); }
          else if(buttons.length && !ev.shiftKey && this.shadowRoot.activeElement===buttons.at(-1)) { ev.preventDefault();buttons[0].focus(); }
        }
      });
    }
    setConfig(config) {
      if (config.entity_prefix && !/^[a-z0-9_]+$/.test(config.entity_prefix)) throw new Error("entity_prefix must be an entity object-ID prefix, for example xpeng_p7");
      for (const [key, value] of Object.entries(config.entities || {})) {
        if (!ROLES[key]) throw new Error(`Unknown entity role: ${key}`);
        if (value && (typeof value !== "string" || !value.startsWith(`${ROLES[key][0]}.`) || !/^[a-z_]+\.[a-z0-9_]+$/.test(value))) throw new Error(`Invalid ${key} entity`);
      }
      if (config.image_entity && !/^image\.[a-z0-9_]+$/.test(config.image_entity)) throw new Error("Choose an image entity");
      this._scheduleDraft=null;
      this.config = {...config, entities:{...(config.entities || {})}}; this._snapshot = null; this._render();
    }
    set hass(hass) {
      this._hass = hass; if (!this.config) return;
      const next = Object.keys(ROLES).map(k => this._entity(k));
      const dark = this.config.theme === "dark" || (this.config.theme !== "light" && !!hass.themes?.darkMode);
      if (this._snapshot && this._dark === dark && next.every((s,i) => s === this._snapshot[i])) return;
      this._snapshot = next; this._dark = dark;
      if (this.shadowRoot.activeElement?.matches("input,select")) { this._deferred = true; return; }
      this._render();
    }
    get hass() { return this._hass; }
    _prefix() {
      if (this.config.entity_prefix) return this.config.entity_prefix;
      const image = this.config.image_entity || this.config.entities.image;
      if (image?.endsWith("_vehicle_picture")) return image.slice(6, -16);
      const images = Object.keys(this._hass?.states || {}).filter(id => /^image\.xpeng_.+_vehicle_picture$/.test(id));
      return images.length === 1 ? images[0].slice(6, -16) : null;
    }
    _id(key) {
      if (!this.config || !ROLES[key]) return null;
      if (Object.hasOwn(this.config.entities, key)) return this.config.entities[key] || null;
      if (key === "image" && this.config.image_entity) return this.config.image_entity;
      const [domain, suffix] = ROLES[key];
      const imageId = this.config.image_entity || this.config.entities.image;
      const registry = this._hass?.entities || {};
      const device = imageId && registry[imageId]?.device_id;
      if (device) {
        const match = Object.values(registry).find(e => e.platform === "xpeng" && e.device_id === device && e.entity_id?.startsWith(domain + ".") && e.unique_id?.endsWith("_" + suffix));
        if (match) return match.entity_id;
      }
      const prefix = this._prefix(); return prefix ? `${domain}.${prefix}_${CLIMATE_MODES.find(m=>m[0]===key)?.[3]||suffix}` : null;
    }
    _entity(key) { return this._hass?.states[this._id(key)] || null; }
    _available(key) { const e = this._entity(key); return !!e && e.state !== "unavailable"; }
    _state(key, fallback = "Unknown") {
      const e = this._entity(key);
      // Parking lights have no measured feedback; default only their display.
      if (key === "parking_lights" && e?.state === "unknown") return "off";
      return known(e) ? e.state : fallback;
    }
    _formatState(key, digits = 0) {
      const e = this._entity(key), n = numeric(e?.state);
      return known(e) ? (n === null ? e.state : n.toLocaleString(this._hass?.locale?.language, {maximumFractionDigits:digits})) : "—";
    }
    _display(key, digits = 0) {
      const e = this._entity(key);
      return `${this._formatState(key,digits)}${known(e) && e.attributes.unit_of_measurement ? " " + e.attributes.unit_of_measurement : ""}`;
    }
    _features(key, bit) { return this._available(key) && !!(Number(this._entity(key)?.attributes.supported_features || 0) & bit); }
    _human(key) {
      const s = this._state(key); return ({on:"On",off:"Off",locked:"Locked",unlocked:"Unlocked",open:"Open",closed:"Closed",locking:"Locking…",unlocking:"Unlocking…",opening:"Opening…",closing:"Closing…"})[s] || s;
    }
    _action(key, service, data = {}, label, confirm = false) { return {key, service, data, label, confirm}; }
    _btn(label, glyph, action, options = {}) {
      const id = String(this._actions.size); this._actions.set(id, action);
      const disabled = options.disabled || this._busy || !this._available(action.key);
      return `<button type="button" class="${options.small ? "small" : "action"}${options.active ? " active" : ""}${options.soft ? " soft" : ""}" data-action="${id}" ${disabled ? "disabled" : ""} ${options.pressed ? `aria-pressed="${!!options.active}"` : ""}>${glyph ? icon(glyph) : ""}<span>${esc(label)}</span>${options.caption ? `<span class="caption">${esc(options.caption)}</span>` : ""}</button>`;
    }
    _run(a) {
      if (this._busy) return;
      const trunkOpen = a.key === "trunk" && a.service === "cover.open_cover";
      const confirm = a.key === "trunk" ? trunkOpen : a.confirm && this.config.confirm_actions !== false;
      if (confirm) { this._confirmation = {...a, entityId:this._id(a.key),message:trunkOpen?"Check there is enough space above and behind the car before opening the trunk.":"This sends a command to your car."}; this._render(); queueMicrotask(() => this.shadowRoot.querySelector("[data-cancel]")?.focus()); }
      else this._send(a);
    }
    _validate(a) {
      const e = this._entity(a.key), id = this._id(a.key);
      if (!e || e.state === "unavailable" || a.entityId && a.entityId !== id) throw new Error("This vehicle control is unavailable.");
      const [domain, service] = a.service.split(".");
      if(CLIMATE_MODES.some(m=>m[0]===a.key)&&e.attributes.control_allowed===false)throw new Error("This climate mode is read-only or unsupported.");
      if(a.key==="schedules"&&domain==="xpeng"){
        if(!["create_hvac_schedule","update_hvac_schedule","delete_hvac_schedule","list_hvac_schedules"].includes(service)||!this._hass.services?.xpeng?.[service])throw new Error("This schedule action is unavailable.");
        if(!this._features("climate",128)||a.data.vehicle_entity_id!==this._id("climate"))throw new Error("This vehicle’s schedules are read-only or the selected vehicle changed.");
        if(["create_hvac_schedule","update_hvac_schedule"].includes(service)){
          if(!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(a.data.start_time)||!Array.isArray(a.data.days_of_week)||a.data.days_of_week.some(d=>!Number.isInteger(d)||d<1||d>7))throw new Error("Choose a valid time and weekdays.");
        }
        if(["update_hvac_schedule","delete_hvac_schedule"].includes(service)&&!this._scheduleRecords().some(x=>String(x.appointment_id)===String(a.data.appointment_id)))throw new Error("This schedule no longer exists.");
        return {id,domain,service,targetless:true};
      }
      if (domain !== ROLES[a.key][0] || !this._hass.services?.[domain]?.[service]) throw new Error("This Home Assistant action is unavailable.");
      if (domain === "cover" && !this._features(a.key, service === "open_cover" ? 1 : 2)) throw new Error("This cover action is not enabled for your car.");
      if (domain === "climate") {
        const required = {turn_on:128,turn_off:256,set_temperature:1,set_fan_mode:8,set_preset_mode:16}[service];
        if (!required || !(Number(e.attributes.supported_features) & required)) throw new Error("This climate action is not supported by the vehicle entity.");
      }
      if (domain === "lock" && !["locked","unlocked"].includes(e.state)) throw new Error("Wait until the lock state is known.");
      if (service === "select_option" && !e.attributes.options?.includes(a.data.option)) throw new Error("That option is not available.");
      if (service === "set_value" || service === "set_temperature") {
        const v = numeric(a.data.value ?? a.data.temperature), min = numeric(e.attributes.min ?? e.attributes.min_temp), max = numeric(e.attributes.max ?? e.attributes.max_temp);
        const step = numeric(e.attributes.step ?? e.attributes.target_temp_step);
        if (v === null || min === null || max === null || v < min || v > max || step && Math.abs((v-min)/step-Math.round((v-min)/step)) > .001) throw new Error("Choose a value within the entity's supported range.");
      }
      if (service === "set_fan_mode" && !e.attributes.fan_modes?.includes(a.data.fan_mode)) throw new Error("Unsupported fan speed.");
      if (service === "set_preset_mode" && !e.attributes.preset_modes?.includes(a.data.preset_mode)) throw new Error("Unsupported climate preset.");
      return {id,domain,service};
    }
    async _send(a) {
      if (this._busy) return;
      try {
        const {id,domain,service,targetless} = this._validate(a);
        this._panel = null; this._busy = true; this._message = {text:`${a.label || "Command"} · sending…`}; this._render();
        await this._hass.callService(domain, service, {...a.data}, targetless?undefined:{entity_id:id});
        this._message = {text:"Request sent. Vehicle feedback and measured state appear below."};
      } catch (error) { this._message = {text:error.message || "The vehicle command failed.", error:true}; }
      finally { this._busy = false; this._render(); }
    }
    _age(value) {
      const ms = Date.parse(value); if (!Number.isFinite(ms)) return "No snapshot yet";
      const min = Math.max(0,Math.floor((Date.now()-ms)/60000)); return min < 1 ? "Just now" : min < 60 ? `${min} min ago` : min < 1440 ? `${Math.floor(min/60)} h ${min%60} min ago` : `${Math.floor(min/1440)} d ago`;
    }
    _hero() {
      const range = this._entity("range"), battery = numeric(this._entity("battery")?.state), locked = this._state("lock");
      const image = this._entity("image")?.attributes.entity_picture;
      const img = image && /^(\/|https?:\/\/)/.test(image) ? (this._hass.hassUrl ? this._hass.hassUrl(image) : image) : null;
      return `<aside class="hero"><div class="top"><div class="brand"><span class="mark" aria-hidden="true">×</span><div><div class="eyebrow">XPENG</div><h2>${esc(this.config.name || "My XPENG")}</h2></div></div><div class="cloud"><i class="dot ${this._state("connected") === "on" ? "" : "offline"}"></i>${this._state("connected") === "on" ? "Cloud connected" : "Cloud offline"}</div></div>
        <div class="range-row"><div><span class="range-value">${esc(this._formatState("range",1))}<span class="range-unit">${esc(range?.attributes.unit_of_measurement || "km")}</span></span><div class="range-caption">${esc(this._state("range_type", "Estimated"))} range</div></div><div class="battery"><div class="battery-value">${battery === null ? "—" : esc(battery)}<small> %</small></div><div class="battery-track"><div class="battery-fill" style="--level:${Math.max(0,Math.min(100,battery||0))}%"></div></div><span class="muted">Battery</span></div></div>
        <div class="vehicle">${img ? `<img src="${esc(img)}" alt="${esc(this.config.name || "XPENG vehicle")}" referrerpolicy="no-referrer">` : `<ha-icon class="fallback" icon="mdi:car-side"></ha-icon>`}</div>
        <div class="hero-status"><span class="pill ${locked === "unlocked" ? "warn" : ""}">${icon(locked === "locked" ? "lock-outline" : "lock-open-outline")}${esc(this._human("lock"))}</span>${this._entity("windows") ? `<span class="pill ${this._state("windows") === "open" ? "warn" : ""}">${icon("car-door")}${this._state("windows") === "closed" ? "Windows closed" : this._state("windows") === "open" ? "Windows open / vented" : "Windows " + esc(this._human("windows").toLowerCase())}</span>` : ""}${this._entity("climate") ? `<span class="pill">${icon("thermometer")}${esc(this._display("cabin"))} inside</span>` : ""}</div>
        <div class="hero-bottom"><span>Vehicle snapshot</span><span>${esc(this._age(this._entity("updated")?.state))}</span></div></aside>`;
    }
    _coverPanel(key, title, openLabel) {
      if (!this._entity(key) || !this._features(key,3)) return "";
      return `<div class="panel"><div class="row"><h3>${esc(title)}</h3><span class="state">${esc(this._human(key))}</span></div><div class="inline-actions">${this._features(key,1) ? this._btn(openLabel,"",this._action(key,"cover.open_cover",{},openLabel + " " + title.toLowerCase(),true),{small:true}) : ""}${this._features(key,2) ? this._btn("Close","",this._action(key,"cover.close_cover",{},"Close " + title.toLowerCase()),{small:true}) : ""}</div>${key === "windows" ? '<p class="note">Ventilation and close. Full opening is not exposed by this integration.</p>' : ""}</div>`;
    }
    _switchPanel(key, title) {
      if (!this._entity(key)) return "";
      return `<div class="panel"><div class="row"><h3>${esc(title)}</h3><span class="state">${esc(this._human(key))}</span></div><div class="inline-actions">${["on","off"].map(s=>this._btn(s === "on" ? "On" : "Off","",this._action(key,`switch.turn_${s}`,{},`${title} ${s}`),{small:true,active:this._state(key)===s})).join("")}</div></div>`;
    }
    _controls() {
      if (this.config.compact !== false) return this._compactControls();
      const locked = this._state("lock"), c = this._entity("climate"), buttons = [];
      if (this._entity("lock")) buttons.push(this._btn(locked === "locked" ? "Unlock" : locked === "unlocked" ? "Lock" : "Door lock",locked === "locked" ? "lock-outline" : "lock-open-outline",this._action("lock",locked === "locked" ? "lock.unlock" : "lock.lock",{},locked === "locked" ? "Unlock doors" : "Lock doors",locked === "locked"),{disabled:!["locked","unlocked"].includes(locked),caption:this._human("lock")}));
      if (c) buttons.push(this._btn(c.state === "off" ? "A/C on" : "A/C off","air-conditioner",this._action("climate",c.state === "off" ? "climate.turn_on" : "climate.turn_off",{},"Air conditioning"),{active:known(c)&&c.state!=="off",disabled:!known(c)||!Number(c.attributes.supported_features),caption:this._display("cabin")}));
      for (const [key,label,glyph] of [["horn","Horn","bullhorn-outline"],["flash","Flash lights","car-light-high"],["wake","Wake vehicle","power"],["refresh","Refresh data","refresh"]]) if (this._entity(key)) buttons.push(this._btn(label,glyph,this._action(key,"button.press",{},label,key === "horn"||key === "wake"),{soft:key === "wake"||key === "refresh"}));
      return `<div class="section-head"><h3>Quick controls</h3><span class="muted">${esc(this._state("charge_state", ""))}</span></div><div class="actions">${buttons.join("")}</div><div class="section-head"><h3>Access</h3></div>${this._coverPanel("trunk","Trunk","Open")}${this._coverPanel("windows","Windows","Vent")}${this._coverPanel("charge_port","Charge port","Open")}<div class="section-head"><h3>Lights & security</h3></div>${this._switchPanel("parking_lights","Parking lights")}${this._switchPanel("guard","X-Guard")}`;
    }
    _panelButton(key,label,glyph) {
      return `<button class="action" type="button" data-panel="${key}" ${this._busy||!this._available(key)?"disabled":""}>${icon(glyph)}<span>${esc(label)}</span><span class="caption">${esc(this._human(key))}</span></button>`;
    }
    _compactControls() {
      const buttons=[], locked=this._state("lock"), c=this._entity("climate");
      if(this._entity("lock"))buttons.push(this._btn(locked==="locked"?"Unlock":"Lock",locked==="locked"?"lock-outline":"lock-open-outline",this._action("lock",locked==="locked"?"lock.unlock":"lock.lock",{},locked==="locked"?"Unlock doors":"Lock doors",locked==="locked"),{disabled:!["locked","unlocked"].includes(locked),caption:this._human("lock")}));
      for(const[key,label,glyph]of [["windows","Windows","car-door"],["trunk","Trunk","car-back"]])if(this._features(key,3))buttons.push(this._panelButton(key,label,glyph));
      if(c)buttons.push(this._btn(c.state==="off"?"A/C on":"A/C off","air-conditioner",this._action("climate",c.state==="off"?"climate.turn_on":"climate.turn_off",{},"Air conditioning"),{disabled:!known(c)||!Number(c.attributes.supported_features),active:known(c)&&c.state!=="off"}));
      for(const[key,label,glyph]of [["horn","Horn","bullhorn-outline"],["flash","Flash lights","car-light-high"]])if(this._entity(key))buttons.push(this._btn(label,glyph,this._action(key,"button.press",{},label,key==="horn")));
      for(const[key,label,glyph]of [["parking_lights","Parking lights","car-parking-lights"],["guard","X-Guard","shield-car"]])if(this._entity(key))buttons.push(this._panelButton(key,label,glyph));
      if(this._entity("refresh"))buttons.push(this._btn("Refresh","refresh",this._action("refresh","button.press",{},"Refresh data")));
      if(this._features("charge_port",3))buttons.push(this._panelButton("charge_port","Charge port","ev-plug-type2"));
      return `<div class="actions">${buttons.join("")}</div>`;
    }
    _panelContent() {
      const k=this._panel,titles={windows:"Windows",trunk:"Trunk",charge_port:"Charge port",parking_lights:"Parking lights",guard:"X-Guard"};
      return `<div class="modal-backdrop"><div class="modal" role="dialog" aria-modal="true" aria-label="${titles[k]}"><div class="row"><h3>${titles[k]}</h3><span class="state">${esc(this._human(k))}</span></div>${k==="parking_lights"||k==="guard"?this._switchPanel(k,titles[k]):this._coverPanel(k,titles[k],k==="windows"?"Vent":"Open")}<div class="inline-actions"><button class="small" type="button" data-panel-close>Done</button></div></div></div>`;
    }
    _levels(key, label, kind) {
      const e = this._entity(key); if (!e) return "";
      const opts = Array.isArray(e.attributes.options) ? e.attributes.options : [];
      return `<div class="seat-kind">${icon(kind === "vent" ? "fan" : "heat-wave")}${esc(label)} <span>${esc(this._human(key))}</span></div><div class="levels">${opts.map((option,i)=>{
        const id=String(this._actions.size);this._actions.set(id,this._action(key,"select.select_option",{option},label));
        return `<button type="button" class="level ${kind || ""}" data-action="${id}" aria-label="${esc(label + ": " + option)}" aria-pressed="${e.state===option}" ${this._busy||!this._available(key)?"disabled":""}>${esc(i===0&&/^off$/i.test(option)?"Off":opts.length<=4?option.replace(/^(Heat|Ventilation) /,""):option)}</button>`;
      }).join("")}</div>`;
    }
    _temperatureExtremes() {
      const e=this._entity("climate"),advertised=e?.attributes.temperature_extremes;
      const low=numeric(e?.attributes.min_temp),high=numeric(e?.attributes.max_temp);
      // Native limits are already converted to the Home Assistant display unit.
      return advertised&&Object.hasOwn(advertised,"LO")&&Object.hasOwn(advertised,"HI")&&low!==null&&high!==null?{LO:low,HI:high}:{};
    }
    _temperatureShortcuts(writable) {
      const target=numeric(this._entity("climate")?.attributes.temperature);
      return `<div class="inline-actions">${Object.entries(this._temperatureExtremes()).map(([label,value])=>this._btn(label,"",this._action("climate","climate.set_temperature",{temperature:value},`Set ${label}`),{small:true,active:target===value,disabled:!writable})).join("")}</div>`;
    }
    _climateModes() {
      const modes=CLIMATE_MODES.filter(([key])=>this._entity(key));
      if(!modes.length)return "";
      return `<div class="section-head"><h3>Climate modes</h3></div><div class="actions mode-grid">${modes.map(([key,label,glyph])=>{
        const e=this._entity(key),on=e.state==="on",writable=e.attributes.control_allowed!==false;
        const a=this._action(key,on?"switch.turn_off":"switch.turn_on",{},`${on?"Stop":"Start"} ${label}`,!on);
        return `<div class="mode${key==="pet_mode"?" pet":""}">${this._btn(label,glyph,a,{active:on,pressed:known(e),caption:this._human(key),disabled:!writable})}${!known(e)?this._btn("Stop","",this._action(key,"switch.turn_off",{},`Stop ${label}`),{small:true,disabled:!writable}):""}</div>`;
      }).join("")}</div>`;
    }
    _scheduleRecords() {
      const values=this._entity("schedules")?.attributes.schedules;
      return Array.isArray(values)?values.filter(x=>x&&x.appointment_id!==null&&x.appointment_id!==undefined):[];
    }
    _editSchedule(id) {
      const item=this._scheduleRecords().find(x=>String(x.appointment_id)===id);
      if(!item)return;
      this._scheduleDraft={appointment_id:id,start_time:item.start_time||"07:30",days:Array.from({length:7},(_,i)=>i+1).filter(d=>(Number(item.schedule_bitmask)||0)&(1<<(d-1))),enabled:!!item.is_valid,action_type:item.action_type||"AIR_CONDITION_OPEN"};
      this._render();
    }
    _scheduleAction(operation,item) {
      const data={vehicle_entity_id:this._id("climate")};
      let label="Refresh schedules";
      if(operation==="delete") {data.appointment_id=String(item.appointment_id);label="Delete schedule";}
      if(operation==="save") {
        const draft=this._scheduleDraft||{start_time:"07:30",days:[],enabled:true};
        operation=draft.appointment_id!==undefined?"update":"create";
        Object.assign(data,{start_time:draft.start_time,days_of_week:[...draft.days].sort((a,b)=>a-b),enabled:!!draft.enabled,action_type:draft.action_type||"AIR_CONDITION_OPEN"});
        if(operation==="update")data.appointment_id=draft.appointment_id;
        label=operation==="update"?"Save schedule":"Create schedule";
      }
      return this._action("schedules",`xpeng.${operation}_hvac_${operation==="list"?"schedules":"schedule"}`,data,label,operation!=="list");
    }
    _schedules() {
      if(!this._showSchedules||!this._entity("schedules"))return "";
      const draft=this._scheduleDraft||{start_time:"07:30",days:[],enabled:true};
      const writable=!!(Number(this._entity("climate")?.attributes.supported_features)&128);
      const zone=this._hass.config?.time_zone||"Home Assistant time";
      const records=this._scheduleRecords();
      return `<div class="panel schedules"><div class="row"><h3>A/C schedules</h3>${this._btn("Refresh","refresh",this._scheduleAction("list"),{small:true,disabled:!writable})}</div><p class="note">Times use ${esc(zone)}. Starts A/C using the vehicle’s saved settings. No days selected means run once.</p>${records.length?records.map(item=>`<div class="schedule-record"><div><strong>${esc(item.start_time||"Unknown time")}</strong> · ${item.is_valid?"Enabled":"Disabled"}<div class="note">${esc(item.days?.length?item.days.join(", "):"Once")}${item.action_type&&item.action_type!=="AIR_CONDITION_OPEN"?` · ${esc(item.action_type)}`:""}</div></div><div class="inline-actions"><button type="button" class="small" data-schedule-edit="${esc(item.appointment_id)}" ${!writable||this._busy?"disabled":""}>Edit</button>${this._btn("Delete","delete-outline",this._scheduleAction("delete",item),{small:true,disabled:!writable})}</div></div>`).join(""):'<p class="note">No schedules reported.</p>'}<div class="schedule-form"><h3>${draft.appointment_id!==undefined?"Edit schedule":"New schedule"}</h3><label class="select-row">Start time<input type="time" data-schedule-field="start_time" aria-label="Schedule start time" value="${esc(draft.start_time)}" ${!writable||this._busy?"disabled":""}></label><div class="schedule-days">${["Mon","Tue","Wed","Thu","Fri","Sat","Sun"].map((day,i)=>`<label><input type="checkbox" data-schedule-day="${i+1}" ${draft.days.includes(i+1)?"checked":""} ${!writable||this._busy?"disabled":""}>${day}</label>`).join("")}</div><label class="select-row">Enabled<input type="checkbox" data-schedule-field="enabled" ${draft.enabled?"checked":""} ${!writable||this._busy?"disabled":""}></label><div class="inline-actions">${this._btn(draft.appointment_id!==undefined?"Save":"Create","calendar-plus",this._scheduleAction("save"),{small:true,disabled:!writable})}${draft.appointment_id!==undefined?'<button type="button" class="small" data-schedule-reset>Cancel edit</button>':""}</div></div></div>`;
    }

    _climate() {
      const e = this._entity("climate"); let main = "";
      if (e) {
        const t=numeric(e.attributes.temperature), min=numeric(e.attributes.min_temp), max=numeric(e.attributes.max_temp), step=numeric(e.attributes.target_temp_step)||.5, writable=this._available("climate")&&!!Number(e.attributes.supported_features);
        const targetBtn=(delta,label)=>{ const id=String(this._actions.size);const value=t===null?null:Math.max(min,Math.min(max,min+Math.round((t+delta-min)/step)*step));this._actions.set(id,this._action("climate","climate.set_temperature",{temperature:value},"Set temperature"));return `<button class="round" type="button" data-action="${id}" aria-label="${label}" ${!writable||this._busy||t===null||min===null||max===null||(delta>0&&t>=max)||(delta<0&&t<=min)?"disabled":""}>${delta<0?"−":"+"}</button>`;};
        const special=Object.entries(this._temperatureExtremes()).find(([,value])=>Math.abs((t??NaN)-value)<.001)?.[0];
        main=`<div class="panel"><div class="row"><div><h3>Cabin climate</h3><span class="muted">Inside ${esc(this._display("cabin"))} · Outside ${esc(this._display("outside"))}</span></div>${this._btn(e.state==="off"?"Start":"Stop","power",this._action("climate",e.state==="off"?"climate.turn_on":"climate.turn_off",{},"Air conditioning"),{small:true,active:known(e)&&e.state!=="off",disabled:!writable||!known(e)})}</div><div class="climate-target">${targetBtn(-step,"Decrease target temperature")}<div><div class="temperature"><input class="temperature-input" type="${special?"text":"number"}" data-key="temperature" aria-label="Target temperature" min="${min??16}" max="${max??32}" step="${step}" value="${special??t??""}" placeholder="—" ${!writable||this._busy||!(Number(e.attributes.supported_features)&1)?"disabled":""}><small>${special?"":esc(this._hass.config?.unit_system?.temperature || "°C")}</small></div><div class="temperature-label">Target temperature</div></div>${targetBtn(step,"Increase target temperature")}</div>${this._temperatureShortcuts(writable)}${this._climateSelect("fan_mode","Fan speed",e.attributes.fan_modes,e.attributes.fan_mode,writable&&!!(Number(e.attributes.supported_features)&8))}${this._frontDefrost(writable)}</div>`;
      }
      const seats=SEATS.filter(([seat])=>this._entity(`${seat}_heating`)||this._entity(`${seat}_ventilation`));
      const schedule=this._entity("schedules")?`<div class="section-head"><h3>Planning</h3><button type="button" class="small" data-schedule-toggle aria-expanded="${!!this._showSchedules}">Schedule</button></div>${this._schedules()}`:"";
      return main + this._climateModes() + schedule + (seats.length?`<div class="section-head"><h3>Seat comfort</h3><span class="muted">Measured levels</span></div><div class="seats">${seats.map(([seat,label])=>`<div class="panel seat"><div class="seat-title">${icon("car-seat")}${label}</div>${this._levels(`${seat}_heating`,label+" heat","heat")}${this._levels(`${seat}_ventilation`,label+" ventilation","vent")}</div>`).join("")}</div>`:"")+(this._entity("steering")?`<div class="section-head"><h3>Steering wheel</h3>${icon("steering")}</div><div class="panel">${this._levels("steering","Steering wheel heat","heat")}</div>`:"") || '<div class="empty">No climate or seat entities available.</div>';
    }
    _frontDefrost(writable) {
      const e=this._entity("climate"),options=e?.attributes.preset_modes;
      if(!Array.isArray(options)||!options.includes("none")||!options.includes("defrost"))return "";
      const value=e.attributes.preset_mode,on=value==="defrost",measured=known(e)&&["none","defrost"].includes(value);
      const button=(enabled)=>this._btn(enabled?"Turn on":"Turn off","car-defrost-front",this._action("climate","climate.set_preset_mode",{preset_mode:enabled?"defrost":"none"},`${enabled?"Start":"Stop"} windscreen defroster`),{small:true,active:measured&&on,pressed:measured,disabled:!writable||!(Number(e.attributes.supported_features)&16)});
      return `<div class="select-row"><span>Windscreen defroster <span class="muted">${measured?(on?"On":"Off"):"Unknown"}</span></span><div class="inline-actions">${button(measured?!on:true)}${!measured?button(false):""}</div></div>`;
    }
    _climateSelect(key,label,options,value,enabled) {
      if (!Array.isArray(options)||!options.length) return "";
      return `<label class="select-row">${esc(label)}<select data-key="${key}" ${this._busy||!enabled?"disabled":""}><option value="" disabled ${!options.includes(value)?"selected":""}>Unknown</option>${options.map(o=>`<option value="${esc(o)}" ${o===value?"selected":""}>${esc(o==="none"?"Off":o==="defrost"?"Defrost":o)}</option>`).join("")}</select></label>`;
    }
    _charging() {
      const e=this._entity("charge_limit"), value=numeric(e?.state), a=e?.attributes||{}, min=numeric(a.min), max=numeric(a.max), step=numeric(a.step);
      const valid=known(e)&&value!==null&&min!==null&&max!==null&&step!==null&&step>0;
      return `<div class="section-head"><h3>Charging</h3><span class="muted">${esc(this._state("charge_state"))}</span></div><div class="metrics"><div class="metric"><span class="muted">Battery</span><div class="metric-value">${esc(this._display("battery"))}</div></div><div class="metric"><span class="muted">Charge power</span><div class="metric-value">${esc(this._display("charge_power",1))}</div></div></div>${e?`<div class="panel" style="margin-top:12px"><div class="row"><h3>Charge limit</h3><div class="charge-value" data-limit-preview>${value===null?"—":esc(value)}<small> %</small></div></div><input type="range" data-key="charge_limit" aria-label="Charge limit" min="${min??0}" max="${max??100}" step="${step??1}" value="${value??min??0}" ${!valid||!this._available("charge_limit")||this._busy?"disabled":""}><div class="range-labels"><span>${min??"—"}%</span><span>Release to apply</span><span>${max??"—"}%</span></div></div>`:""}${this._switchPanel("charging","Charging control")}<p class="note">Start requires a connected charger and vehicle readiness. This controls the car; your evcc charging plan stays managed by evcc.</p>${this._coverPanel("charge_port","Charge port","Open")}<div class="status-list">${this._statusRow("plugged_in","Cable",{"on":"Connected","off":"Disconnected"})}${this._statusRow("charge_time","Time remaining")}</div>`;
    }
    _statusRow(key,label,map) {if(!this._entity(key))return "";return `<div class="row"><span>${esc(label)}</span><span class="state">${esc(map?map[this._state(key)]||"Unknown":this._display(key,1))}</span></div>`;}
    _status() {
      return `<div class="section-head"><h3>Vehicle status</h3><span class="muted">${esc(this._display("odometer"))}</span></div><div class="panel status-list">${this._statusRow("lock","Door lock",{locked:"Locked",unlocked:"Unlocked"})}${[["driver_door","Driver door"],["passenger_door","Passenger door"],["rear_left_door","Rear left door"],["rear_right_door","Rear right door"],["bonnet","Bonnet"]].map(([k,l])=>this._statusRow(k,l,{on:"Open",off:"Closed"})).join("")}${this._statusRow("trunk","Trunk")}${this._statusRow("windows","Windows")}${this._statusRow("cabin","Cabin temperature")}${this._statusRow("outside","Outside temperature")}</div><div class="section-head"><h3>Tyre pressures</h3><span class="muted">Latest reported</span></div><div class="tyres">${[["tyre_fl","Front left"],["tyre_fr","Front right"],["tyre_rl","Rear left"],["tyre_rr","Rear right"]].filter(([k])=>this._entity(k)).map(([k,l])=>`<div class="tyre"><small>${l}</small><strong>${esc(this._display(k,this._entity(k)?.attributes.unit_of_measurement === "bar" ? 2 : 1))}</strong></div>`).join("")}</div><div class="panel status-list" style="margin-top:12px">${this._statusRow("software","Software")}<div class="row"><span>Vehicle snapshot</span><span class="state">${esc(this._age(this._entity("updated")?.state))}</span></div></div><div class="inline-actions">${this._entity("wake")?this._btn("Wake vehicle","power",this._action("wake","button.press",{},"Wake vehicle",true),{small:true}):""}</div>`;
    }
    _render() {
      if (!this.config||!this._hass) return;
      this.toggleAttribute("dark",this.config.theme==="dark"||(this.config.theme!=="light"&&!!this._hass.themes?.darkMode)); this.toggleAttribute("compact",this.config.compact!==false);this._actions.clear();
      if (!this._prefix()&&!this.config.image_entity&&!Object.keys(this.config.entities).length) { this.shadowRoot.innerHTML=`<style>${css}</style><ha-card><div class="empty">Choose a vehicle image entity or entity_prefix in the card editor. Multiple cars need an explicit selection.</div></ha-card>`;return; }
      const feedback=this._entity("feedback"), rejected=known(feedback)&&feedback.attributes.code!==undefined&&Number(feedback.attributes.code)!==200;
      const body=({controls:()=>this._controls(),climate:()=>this._climate(),charging:()=>this._charging(),status:()=>this._status()})[this._tab]();
      this.shadowRoot.innerHTML=`<style>${css}</style><ha-card><div class="layout">${this._hero()}<main class="workspace"><nav class="tabs" role="tablist" aria-label="Vehicle sections">${["controls","climate","charging","status"].map(t=>`<button class="tab" type="button" role="tab" aria-selected="${t===this._tab}" data-tab="${t}">${t[0].toUpperCase()+t.slice(1)}</button>`).join("")}</nav>${this._message?`<div class="notice ${this._message.error?"error":""}" role="status">${esc(this._message.text)}</div>`:""}<section role="tabpanel" aria-label="${this._tab}">${body}</section>${known(feedback)?`<div class="feedback ${rejected?"error":""}">${icon(rejected?"alert-circle-outline":"message-check-outline")}<div>Last vehicle response: ${esc(feedback.state)}<time>${esc(this._age(feedback.last_updated))}</time></div></div>`:""}</main></div>${this._confirmation?`<div class="modal-backdrop"><div class="modal" role="dialog" aria-modal="true" aria-label="Confirm vehicle action"><h3>${esc(this._confirmation.label)}?</h3><p>${esc(this._confirmation.message || "This sends a command to your car.")}</p><div class="inline-actions"><button class="small" type="button" data-cancel>Cancel</button><button class="small primary" type="button" data-confirm>Confirm</button></div></div></div>`:this._panel?this._panelContent():""}</ha-card>`;
    }
    getCardSize() { return 9; }
    getGridOptions() { return {columns:12,rows:"auto",min_columns:6}; }
    static getConfigElement() { return document.createElement("xpeng-vehicle-card-editor"); }
    static getStubConfig(hass) {
      const images=Object.keys(hass?.states||{}).filter(id=>/^image\.xpeng_.+_vehicle_picture$/.test(id));
      return {name:"My XPENG",compact:true,...(images.length===1?{image_entity:images[0]}:{})};
    }
  }
  class XPengVehicleCardEditor extends HTMLElement {
    setConfig(config) { this.config={theme:"auto",confirm_actions:true,compact:true,...config};const form=this.querySelector("ha-form");if(form)form.data=this.config;else this._render(); }
    set hass(hass) { this._hass=hass;const form=this.querySelector("ha-form");if(form)form.hass=hass;else this._render(); }
    _render() {
      if(!this.config||!this._hass||this.querySelector("ha-form"))return;
      const form=document.createElement("ha-form");form.hass=this._hass;form.data=this.config;
      form.schema=[{name:"name",selector:{text:{}}},{name:"image_entity",selector:{entity:{domain:"image"}}},{name:"entity_prefix",selector:{text:{}}},{name:"theme",selector:{select:{options:["auto","light","dark"],mode:"dropdown"}}},{name:"compact",selector:{boolean:{}}},{name:"confirm_actions",selector:{boolean:{}}}];
      form.computeLabel=({name})=>({name:"Vehicle name",image_entity:"Vehicle image",entity_prefix:"Entity prefix (optional, e.g. xpeng_p7)",theme:"Appearance",compact:"Compact dashboard card",confirm_actions:"Confirm unlock, horn, wake and opening actions"})[name];
      form.addEventListener("value-changed",ev=>{this.config={...this.config,...ev.detail.value};this.dispatchEvent(new CustomEvent("config-changed",{detail:{config:this.config},bubbles:true,composed:true}));});
      const help=document.createElement("p");help.textContent="Uses this vehicle's existing XPENG entities. Use the YAML entities map to override renamed entities or set a role to false to hide it.";help.style.cssText="font-size:12px;line-height:1.6;color:var(--secondary-text-color)";this.append(form,help);
    }
  }
  if(!customElements.get("xpeng-vehicle-card"))customElements.define("xpeng-vehicle-card",XPengVehicleCard);
  if(!customElements.get("xpeng-vehicle-card-editor"))customElements.define("xpeng-vehicle-card-editor",XPengVehicleCardEditor);
  window.customCards=window.customCards||[];
  if(!window.customCards.some(c=>c.type==="xpeng-vehicle-card"))window.customCards.push({type:"xpeng-vehicle-card",name:"XPENG Vehicle",description:"App-inspired vehicle image, access, climate, seats, charging and status.",preview:true});
  console.info(`XPENG VEHICLE CARD ${VERSION}`);
})();
