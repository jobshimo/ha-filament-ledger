import assert from "node:assert/strict";
import { it } from "node:test";
import { panelWith } from "./harness.mjs";
import { notificationDestinations, BLUEPRINT_PATH } from "../../custom_components/filament_ledger/www/panel/notifications.js";

function notificationPanel() {
  const panel = panelWith({_hass: {user: {id:"user", is_admin:true}, services: {notify: {phone: {fields:{message:{required:true}}}, send_message:{}}}, states:{}},
    _notificationDraft: {id:"retained-rule-id", alias:"Test rule", scope:"all", spools:[], destination:"service:notify.phone", phase:"review"}});
  panel.render = () => {};
  panel._ensureBlueprint = async () => {};
  return panel;
}

it("does not treat generic notify.send_message as a destination", () => {
  const destinations = notificationDestinations({services:{notify:{send_message:{}, unsupported:{fields:{extra:{required:true}}}}},states:{"notify.phone":{state:"unknown",attributes:{}}}});
  assert.equal(destinations.length, 2);
  assert.ok(destinations.every(d => !d.available));
  assert.equal(destinations.find(d => d.id === "entity:notify.phone").service, "notify.send_message");
});

it("retains the native ID after a lost save reply and reads back before accepting activation", async () => {
  const panel = notificationPanel();
  let saved, writes = 0;
  panel._hass.callApi = async (method, path, body) => {
    assert.ok(path.endsWith("retained-rule-id"));
    if (method === "GET") { if (!saved) throw {status_code:404}; return saved; }
    writes++; saved = body; throw new Error("Reply lost");
  };
  await panel._saveNotification();
  assert.equal(panel._notificationDraft.id, "retained-rule-id");
  assert.equal(panel._notificationDraft.phase, "review");
  await panel._saveNotification();
  assert.equal(writes, 1);
  assert.equal(panel._notificationDraft.phase, "activating");
  panel._hass.states["automation.rule"] = {entity_id:"automation.rule",state:"on",attributes:{id:"retained-rule-id"}};
  panel._checkNotificationActivation();
  assert.equal(panel._notificationDraft.phase, "active");
});

it("preserves conflicting advanced configuration and missing scope", async () => {
  const panel = notificationPanel(); let writes = 0;
  panel._hass.callApi = async method => { if (method === "GET") return {id:"retained-rule-id",use_blueprint:{path:BLUEPRINT_PATH,input:{actions:[{action:"light.turn_on"}]}}}; writes++; };
  await panel._saveNotification();
  assert.equal(writes, 0);
  assert.match(panel._notificationError, /preserved/);
  panel._notificationDraft.scope = "selected";
  panel._notificationDraft.spools = ["binary_sensor.removed"];
  assert.throws(() => panel._notificationConfig(), /available spool/);
});

it("TEST calls only the selected notification action and never emits a production event", async () => {
  const panel = notificationPanel(); const calls = [];
  panel.call = () => { throw new Error("Production command forbidden"); };
  panel._hass.callService = async (...args) => calls.push(args);
  await panel._testNotification();
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].slice(0,2), ["notify","phone"]);
  assert.match(calls[0][2].message, /\[TEST\]/);
  assert.equal(panel._notificationTestAccepted, true);
});

it("verifies the whole installed blueprint and preserves a changed condition", async () => {
  const {readFile} = await import("node:fs/promises");
  const contract = JSON.parse(await readFile(new URL("../../custom_components/filament_ledger/www/blueprints/low_stock_v1.contract.json",import.meta.url),"utf8"));
  const savedFetch = globalThis.fetch;
  globalThis.fetch = async url => ({ok:true,text:async()=>"fixture yaml",json:async()=>contract});
  try {
    const panel = notificationPanel();
    delete panel._ensureBlueprint;
    const calls=[];
    panel._nativeWS=async(type,payload)=>{
      calls.push({type,payload});
      if(type==="blueprint/list") return {[BLUEPRINT_PATH]:{metadata:contract.metadata}};
      return {substituted_config:{...contract.substituted_config,conditions:[]}};
    };
    await assert.rejects(()=>panel._ensureBlueprint(),/preserved/);
    assert.ok(!calls.some(c=>c.type==="blueprint/save"));
  } finally {globalThis.fetch=savedFetch;}
});
