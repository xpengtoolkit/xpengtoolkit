// Isolated tests. callService is a recorder; these tests cannot reach a car.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const registry = new Map();
class Element {
  attachShadow() { this.shadowRoot={innerHTML:"",listeners:{},addEventListener(type,fn){this.listeners[type]=fn;},querySelector(){return null;},activeElement:null}; }
  toggleAttribute() {}
}
vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../../custom_components/xpeng/www/xpeng-vehicle-card.js"),"utf8"), {
  HTMLElement:Element, customElements:{get:k=>registry.get(k),define:(k,v)=>registry.set(k,v)},
  window:{}, console:{info(){}}, queueMicrotask, CustomEvent:class{},
});
const Card = registry.get("xpeng-vehicle-card");
const state = (value, attributes={}) => ({state:String(value),attributes,last_updated:new Date().toISOString()});
function setup(prefix="xpeng_test") {
  const calls=[];
  const hass={states:{},services:{},themes:{darkMode:false},config:{unit_system:{temperature:"°C"}},
    callService:async(...args)=>calls.push(args),hassUrl:u=>u};
  const put=(domain,suffix,value,attrs)=>{hass.states[`${domain}.${prefix}_${suffix}`]=state(value,attrs);};
  for (const [domain,names] of Object.entries({button:["press"],cover:["open_cover","close_cover"],lock:["lock","unlock"],climate:["turn_on","turn_off","set_temperature","set_fan_mode","set_preset_mode"],select:["select_option"],number:["set_value"],switch:["turn_on","turn_off"]})) hass.services[domain]=Object.fromEntries(names.map(n=>[n,{}]));
  put("image","vehicle_picture","2026-10-22",{entity_picture:"/api/image_proxy/image.test?token=private"});
  put("sensor","battery",71,{unit_of_measurement:"%"});put("sensor","range",439,{unit_of_measurement:"km"});
  put("sensor","charging_state","Disconnected");put("sensor","last_data_update",new Date().toISOString());
  put("lock","door_lock","locked");put("cover","trunk","closed",{supported_features:3});put("cover","windows","open",{supported_features:3});
  put("cover","charge_port","unknown",{supported_features:0});
  put("climate","climate","off",{supported_features:409,min_temp:16,max_temp:32,target_temp_step:.5,temperature:22,fan_modes:["1","2","3","4","5","6","7","8"],fan_mode:"2",preset_modes:["none","defrost"],preset_mode:"none"});
  put("number","charge_limit",80,{min:50,max:100,step:5});
  for(const suffix of ["honk_horn","flash_lights","wake_vehicle","force_update"])put("button",suffix,"unknown");
  for(const suffix of ["parking_lights","x_guard","charging"])put("switch",suffix,"unknown");
  for(const seat of ["driver","passenger","left_rear","right_rear"]) {
    put("select",`${seat}_seat_heating`,"Off",{options:["Off","Heat 1","Heat 2","Heat 3"]});
    put("select",`${seat}_seat_ventilation`,"Off",{options:["Off","Ventilation 1","Ventilation 2","Ventilation 3"]});
  }
  put("select","steering_wheel_heating","Off",{options:["Off","Low","Medium","High"]});
  const card=new Card();card.setConfig({image_entity:`image.${prefix}_vehicle_picture`,name:"P7+",compact:false});card.hass=hass;
  return {card,hass,calls,put};
}
let passed=0;
async function test(name,fn) {await fn();passed++;console.log(`PASS ${name}`);}
(async()=>{
  await test("vehicle image anchors prefix and no commands run during any render",()=>{
    const {card,calls}=setup();assert.equal(card._prefix(),"xpeng_test");assert.equal(card._id("lock"),"lock.xpeng_test_door_lock");
    for(const tab of ["controls","climate","charging","status"]){card._tab=tab;card._render();assert.ok(card.shadowRoot.innerHTML.includes('role="tabpanel"'));}assert.equal(calls.length,0);
  });
  await test("compact shortcuts open local panels without sending commands",()=>{
    const {card,calls}=setup();card.setConfig({entity_prefix:"xpeng_test",compact:true});
    assert.ok(card.shadowRoot.innerHTML.includes('data-panel="windows"'));assert.ok(card.shadowRoot.innerHTML.includes('data-panel="trunk"'));assert.ok(!card.shadowRoot.innerHTML.includes('data-panel="charge_port"'));
    card._panel="windows";card._render();assert.ok(card.shadowRoot.innerHTML.includes('aria-label="Windows"'));assert.ok(card.shadowRoot.innerHTML.includes(">Vent<"));assert.ok([...card._actions.values()].some(a=>a.key==="windows"&&a.service==="cover.close_cover"));assert.equal(calls.length,0);
  });
  await test("out-of-range measured target never turns an increase into a decrease",()=>{
    const {card}=setup();card._entity("climate").attributes.temperature=32.5;card._tab="climate";card._render();assert.match(card.shadowRoot.innerHTML,/aria-label="Increase target temperature" disabled/);
  });
  await test("all requested controls render; charge-flap motors and full opening do not",()=>{
    const {card}=setup();const html=card.shadowRoot.innerHTML;for(const label of ["Horn","Unlock","Windows","Vent","Trunk","Parking lights","A/C on","Flash lights"])assert.ok(html.includes(label),label);
    assert.ok(!html.includes("<h3>Charge port</h3>"));assert.ok(![...card._actions.values()].some(a=>a.key==="charge_port"));
    card._tab="climate";card._render();for(const word of ["Driver","Passenger","Rear left","Rear right","Steering wheel"])assert.ok(card.shadowRoot.innerHTML.includes(word));
  });
  await test("window vent and close target native cover actions",async()=>{
    const {card,calls}=setup();await card._send(card._action("windows","cover.open_cover",{},"Vent"));await card._send(card._action("windows","cover.close_cover",{},"Close"));
    assert.equal(calls.length,2);assert.equal(calls[0][1],"open_cover");assert.equal(calls[1][1],"close_cover");assert.equal(calls[0][3].entity_id,"cover.xpeng_test_windows");
  });
  await test("directional cover permissions checked again before dispatch",async()=>{
    const {card,calls,put}=setup();put("cover","windows","closed",{supported_features:2});await card._send(card._action("windows","cover.open_cover",{},"Vent"));assert.equal(calls.length,0);assert.equal(card._message.error,true);
    await card._send(card._action("charge_port","cover.close_cover",{},"Close"));assert.equal(calls.length,0);
  });
  await test("unknown stateless buttons stay usable; unknown lock never toggles",async()=>{
    const {card,calls,put}=setup();await card._send(card._action("horn","button.press",{},"Horn"));assert.equal(calls.length,1);
    put("lock","door_lock","unknown");await card._send(card._action("lock","lock.unlock",{},"Unlock"));assert.equal(calls.length,1);
  });
  await test("opening/horn/unlock asks for confirmation without a command",()=>{
    const {card,calls}=setup();const unlock=[...card._actions.values()].find(a=>a.key==="lock");card._run(unlock);assert.equal(calls.length,0);assert.ok(card._confirmation);assert.ok(card.shadowRoot.innerHTML.includes('role="dialog"'));
  });
  await test("trunk opening always confirms clearance and closing dispatches directly",async()=>{
    for(const compact of [false,true])for(const confirm_actions of [false,true]){
      const {card,calls}=setup();card.setConfig({entity_prefix:"xpeng_test",compact,confirm_actions});
      let pending;const send=card._send.bind(card);card._send=a=>pending=send(a);
      const show=()=>{if(compact)card._panel="trunk";card._render();};
      const action=service=>[...card._actions].find(([,a])=>a.key==="trunk"&&a.service===service)[0];
      const click=dataset=>card.shadowRoot.listeners.click({target:{closest:()=>({dataset})}});
      show();click({action:action("cover.open_cover")});
      assert.equal(calls.length,0);assert.ok(card._confirmation);assert.match(card.shadowRoot.innerHTML,/space above and behind/);
      click({cancel:""});assert.equal(card._confirmation,null);assert.equal(calls.length,0);
      click({action:action("cover.open_cover")});click({confirm:""});await pending;
      assert.equal(calls.length,1);assert.equal(calls[0][1],"open_cover");assert.equal(calls[0][3].entity_id,"cover.xpeng_test_trunk");
      show();click({action:action("cover.close_cover")});await pending;
      assert.equal(card._confirmation,null);assert.equal(calls.length,2);assert.equal(calls[1][1],"close_cover");assert.equal(calls[1][3].entity_id,"cover.xpeng_test_trunk");
    }
  });
  await test("charging uses explicit on/off even when measured state is unknown",async()=>{
    const {card,calls}=setup();await card._send(card._action("charging","switch.turn_on",{},"Start"));await card._send(card._action("charging","switch.turn_off",{},"Stop"));assert.equal(calls[0][1],"turn_on");assert.equal(calls[1][1],"turn_off");assert.equal(card._entity("charging").state,"unknown");
  });
  await test("charge-limit domain, range and step are enforced",async()=>{
    const {card,calls}=setup();for(const value of [0,49,83,101,NaN])await card._send(card._action("charge_limit","number.set_value",{value},"Limit"));assert.equal(calls.length,0);
    await card._send(card._action("charge_limit","number.set_value",{value:85},"Limit"));assert.equal(calls.length,1);assert.equal(calls[0][2].value,85);assert.equal(calls[0][3].entity_id,"number.xpeng_test_charge_limit");
  });
  await test("seat/steering options and climate fan/presets match entity metadata",async()=>{
    const {card,calls}=setup();for(const [key,option]of [["driver_heating","Heat 3"],["right_rear_ventilation","Ventilation 2"],["steering","Medium"]])await card._send(card._action(key,"select.select_option",{option},"Level"));
    await card._send(card._action("steering","select.select_option",{option:"Heat 9"},"Bad"));assert.equal(calls.length,3);
    await card._send(card._action("climate","climate.set_fan_mode",{fan_mode:"8"},"Fan"));await card._send(card._action("climate","climate.set_preset_mode",{preset_mode:"defrost"},"Defrost"));assert.equal(calls.length,5);
  });
  await test("read-only climate and unavailable entities cannot issue commands",async()=>{
    const {card,calls,put}=setup();put("climate","climate","off",{supported_features:0});await card._send(card._action("climate","climate.turn_on",{},"Start"));
    put("button","honk_horn","unavailable");await card._send(card._action("horn","button.press",{},"Horn"));assert.equal(calls.length,0);
  });
  await test("withdrawn climate features cannot call the service even if globally registered",async()=>{
    const {card,calls}=setup();card._entity("climate").attributes.supported_features=401;
    await card._send(card._action("climate","climate.set_fan_mode",{fan_mode:"3"},"Fan"));
    assert.equal(calls.length,0);assert.equal(card._message.error,true);
  });
  await test("temperature can be entered directly with the entity's min max and step",()=>{
    const {card,calls}=setup();card._tab="climate";card._render();
    assert.match(card.shadowRoot.innerHTML,/data-key="temperature" aria-label="Target temperature" min="16" max="32" step="0.5"/);
    assert.equal(calls.length,0);
  });
  await test("service refusals surface and measured state is never made optimistic",async()=>{
    const {card,hass}=setup();hass.callService=async()=>{throw new Error("Charging could not be started (code 10401)");};await card._send(card._action("charging","switch.turn_on",{},"Start"));assert.ok(card._message.text.includes("10401"));assert.equal(card._message.error,true);assert.equal(card._entity("charging").state,"unknown");assert.equal(card._busy,false);
  });
  await test("concurrent requests are suppressed while a response is pending",async()=>{
    const {card,hass,calls}=setup();let finish;hass.callService=(...args)=>{calls.push(args);return new Promise(resolve=>{finish=resolve;});};const first=card._send(card._action("horn","button.press",{},"Horn"));await card._send(card._action("flash","button.press",{},"Flash"));assert.equal(calls.length,1);finish();await first;
  });
  await test("renamed entities stay anchored to the selected car's registry device",()=>{
    const {card,hass}=setup();hass.entities={"image.xpeng_test_vehicle_picture":{device_id:"carA"},"lock.renamed_doors":{entity_id:"lock.renamed_doors",platform:"xpeng",device_id:"carA",unique_id:"xpeng_PRIVATE_door_lock"},"lock.other_car":{entity_id:"lock.other_car",platform:"xpeng",device_id:"carB",unique_id:"xpeng_OTHER_door_lock"}};
    assert.equal(card._id("lock"),"lock.renamed_doors");card.setConfig({entity_prefix:"xpeng_test",entities:{horn:false,lock:"lock.renamed_doors"}});assert.equal(card._id("horn"),null);assert.equal(card._id("lock"),"lock.renamed_doors");
  });
  await test("multiple auto-discovered cars require an explicit selection",()=>{
    const {card,hass}=setup();hass.states["image.xpeng_second_vehicle_picture"]=state("test");card.setConfig({});assert.equal(card._prefix(),null);assert.ok(card.shadowRoot.innerHTML.includes("Choose a vehicle"));
  });
  await test("configuration rejects mismatched domains and escapes display text",()=>{
    const {card}=setup();assert.throws(()=>card.setConfig({entities:{horn:"lock.bad"}}));card.setConfig({entity_prefix:"xpeng_test",name:'<img src=x onerror="bad()">'});assert.ok(card.shadowRoot.innerHTML.includes("&lt;img"));assert.ok(!card.shadowRoot.innerHTML.includes('<img src=x onerror="bad()">'));
  });
  await test("new climate modes render without requests and use one measured switch each",async()=>{
    const {card,calls,put}=setup();
    for(const suffix of ["maximum_heating","maximum_cooling","refresh","sterilisation","a_c_cleaning","rear_window_defroster","silent_mode"])put("switch",suffix,"off",{control_allowed:true});
    card._tab="climate";card._render();assert.equal(calls.length,0);
    for(const key of ["max_heating","max_cooling","refresh_air","sterilisation","ac_cleaning","rear_defrost","silent_mode"]){const a=[...card._actions.values()].find(a=>a.key===key);assert.equal(a.service,"switch.turn_on");assert.equal(a.confirm,true);}
    const start=[...card._actions.values()].find(a=>a.key==="max_cooling");await card._send(start);assert.equal(calls.length,1);assert.equal(card._entity("max_cooling").state,"off");
    put("switch","maximum_cooling","on");card._render();assert.equal([...card._actions.values()].find(a=>a.key==="max_cooling").service,"switch.turn_off");
    const silent=[...card._actions.values()].find(a=>a.key==="silent_mode");await card._send(silent);assert.equal(calls[1][3].entity_id,"switch.xpeng_test_silent_mode");assert.equal(card._entity("silent_mode").state,"off");
    put("switch","silent_mode","on",{control_allowed:true});card._render();assert.equal([...card._actions.values()].find(a=>a.key==="silent_mode").service,"switch.turn_off");
  });
  await test("missing modes disappear and unknown measured modes retain explicit stop",()=>{
    const {card,calls,put}=setup();put("switch","maximum_heating","unknown");card._tab="climate";card._render();
    assert.equal([...card._actions.values()].filter(a=>a.key==="max_heating").length,2);
    assert.equal([...card._actions.values()].filter(a=>a.key==="max_cooling").length,0);assert.equal(calls.length,0);
  });
  await test("schedule panel renders scoped records and form without requests",()=>{
    const {card,calls,hass,put}=setup();hass.config.time_zone="Europe/Athens";put("sensor","hvac_schedules",1,{schedules:[{appointment_id:"a",start_time:"07:30",days:["Mon"],schedule_bitmask:1,is_valid:true}]});
    card._tab="climate";card._showSchedules=true;card._render();assert.match(card.shadowRoot.innerHTML,/A\/C schedules/);assert.match(card.shadowRoot.innerHTML,/Europe\/Athens/);assert.match(card.shadowRoot.innerHTML,/Schedule start time/);assert.equal(calls.length,0);
  });
  await test("schedule requests target selected car and do not use sensor entity targets",async()=>{
    const {card,calls,hass,put}=setup();put("sensor","hvac_schedules",0,{schedules:[]});hass.services.xpeng={create_hvac_schedule:{}};
    card._scheduleDraft={start_time:"08:10",days:[5,1],enabled:true};await card._send(card._scheduleAction("save"));
    assert.equal(calls.length,1);assert.equal(calls[0][0],"xpeng");assert.equal(calls[0][1],"create_hvac_schedule");assert.equal(calls[0][2].vehicle_entity_id,"climate.xpeng_test_climate");assert.equal(calls[0][2].days_of_week.join(","),"1,5");assert.equal(calls[0][3],undefined);
  });
  await test("schedule validation rejects stale cars invalid times days and missing records",async()=>{
    const {card,calls,hass,put}=setup();put("sensor","hvac_schedules",0,{schedules:[]});hass.services.xpeng={create_hvac_schedule:{},delete_hvac_schedule:{}};
    for(const draft of [{start_time:"25:00",days:[],enabled:true},{start_time:"08:00",days:[8],enabled:true}]){card._scheduleDraft=draft;await card._send(card._scheduleAction("save"));}
    card._scheduleDraft={start_time:"08:00",days:[],enabled:true};const a=card._scheduleAction("save");a.data.vehicle_entity_id="climate.other";await card._send(a);
    await card._send(card._scheduleAction("delete",{appointment_id:"missing"}));assert.equal(calls.length,0);
    card._entity("climate").attributes.supported_features=0;await card._send(card._scheduleAction("save"));assert.equal(calls.length,0);
  });
  await test("editing preserves schedule id action days and disabled state",()=>{
    const {card,put}=setup();put("sensor","hvac_schedules",1,{schedules:[{appointment_id:42,start_time:"20:30",schedule_bitmask:65,is_valid:false,action_type:"EXISTING_ACTION"}]});
    card._editSchedule("42");const a=card._scheduleAction("save");assert.equal(a.service,"xpeng.update_hvac_schedule");assert.equal(a.data.appointment_id,"42");assert.equal(a.data.action_type,"EXISTING_ACTION");assert.equal(a.data.enabled,false);assert.equal(a.data.days_of_week.join(","),"1,7");
    assert.equal(card._scheduleAction("delete",{appointment_id:42}).confirm,true);
  });

  await test("Pet Mode uses its measured switch and rejects withdrawn control permissions",async()=>{
    const {card,calls,put}=setup();put("switch","pet_mode","off",{control_allowed:true});card._tab="climate";card._render();
    const a=[...card._actions.values()].find(a=>a.key==="pet_mode");assert.equal(a.service,"switch.turn_on");await card._send(a);assert.equal(calls.length,1);assert.equal(calls[0][3].entity_id,"switch.xpeng_test_pet_mode");
    card._entity("pet_mode").attributes.control_allowed=false;await card._send(a);assert.equal(calls.length,1);
  });
  await test("confirmed temperature endpoints render as LO HI and use native target requests",async()=>{
    const {card,calls}=setup();Object.assign(card._entity("climate").attributes,{temperature:32.5,min_temp:15.5,max_temp:32.5,temperature_extremes:{LO:15.5,HI:32.5}});card._tab="climate";card._render();
    assert.match(card.shadowRoot.innerHTML,/value="HI"/);const lo=[...card._actions.values()].find(a=>a.label==="Set LO");assert.equal(lo.data.temperature,15.5);await card._send(lo);assert.equal(calls[0][2].temperature,15.5);
    card.shadowRoot.listeners.change({target:{dataset:{key:"temperature"},value:"HI"}});await Promise.resolve();assert.equal(calls[1][2].temperature,32.5);
  });
  await test("endpoint shortcuts require metadata and use converted display-unit limits",async()=>{
    const {card,calls,hass}=setup();assert.equal(Object.keys(card._temperatureExtremes()).length,0);hass.config.unit_system.temperature="°F";
    Object.assign(card._entity("climate").attributes,{temperature:90.5,min_temp:59.9,max_temp:90.5,target_temp_step:.9,temperature_extremes:{LO:15.5,HI:32.5}});
    card._tab="climate";card._render();const hi=[...card._actions.values()].find(a=>a.label==="Set HI");await card._send(hi);assert.equal(calls[0][2].temperature,90.5);
  });
  await test("windscreen defroster toggles native presets using measured state",async()=>{
    const {card,calls}=setup();card._tab="climate";card._render();
    assert.match(card.shadowRoot.innerHTML,/Windscreen defroster/);assert.ok(!card.shadowRoot.innerHTML.includes('data-key="preset_mode"'));
    const action=()=>[...card._actions.values()].filter(a=>a.service==="climate.set_preset_mode");
    assert.equal(action().length,1);assert.equal(action()[0].data.preset_mode,"defrost");await card._send(action()[0]);
    assert.equal(calls[0][2].preset_mode,"defrost");assert.equal(card._entity("climate").attributes.preset_mode,"none");
    card._entity("climate").attributes.preset_mode="defrost";card._render();assert.equal(action()[0].data.preset_mode,"none");
    await card._send(action()[0]);assert.equal(calls[1][2].preset_mode,"none");
    card._entity("climate").attributes.preset_mode=null;card._render();assert.equal(action().length,2);
    card._entity("climate").attributes.supported_features=0;await card._send(action()[0]);assert.equal(calls.length,2);
  });
  console.log(`${passed} tests passed. No network calls were made.`);
})().catch(error=>{console.error(error);process.exitCode=1;});
