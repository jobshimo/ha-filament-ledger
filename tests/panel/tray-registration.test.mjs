import assert from "node:assert/strict";
import { it } from "node:test";
import { aSpool, panelWith } from "./harness.mjs";

const tray = {printer: "P2", ams: 2, slot: 1, status: "no_tag", tag_uid: null,
  name_hint: "ELAS PLA PRO", material_hint: "PLA", colour_hint: "#F08020",
  colour_hint_rgba: "#F08020FF", weight_hint_g: null};

function panel() {
  return panelWith({_printer: {tracking: {printers: ["P2"]},
    machines: [{printer: "P2", trays: [tray]}]}});
}

it("offers chipless import from Printer, AMS and sync while excluding empty and mounted trays", () => {
  const p = panel();
  for (const html of [p.printerTrays({trays: [tray]}), p.amsUnitBlock("P2", 2, false), p.syncRow(tray)]) {
    assert.match(html, /data-action="(?:register-tray|sync-register)"/);
    assert.match(html, /data-printer="P2"/);
    assert.match(html, /data-ams="2"/);
  }
  for (const status of ["empty", "mounted", "detected", "ambiguous_tag"]) {
    assert.equal(p.trayRegistrationButton({...tray, status}), "");
  }
  p._spools = [aSpool({location: {kind: "AMS_SLOT", printer: "P2", ams: 1, slot: 1}})];
  assert.match(p.trayRegistrationButton(tray), /register-tray/);
  p._spools[0].location.ams = 2;
  assert.equal(p.trayRegistrationButton(tray), "");
});

it("prefills any brand without RFID and keeps the complete destination for create and mount", async () => {
  for (const action of ["register-tray", "sync-register"]) {
    const p = panel();
    p._sync = {slots: [{...tray, printer: "P1", name_hint: "Other reel"},
      {...tray, ams: 1, name_hint: "Other AMS"}, tray]};
    p.render = () => {};
    const target = {dataset: {action, printer: "P2", ams: "2", slot: "1"}};
    p._onClick({target: {closest: () => target}});
    const draft = p._registrationDraft();
    clearTimeout(p._previewTimer);
    assert.equal(draft.label, "ELAS PLA PRO");
    assert.equal(draft.material, "PLA");
    assert.equal(draft.colour, "#F08020FF");
    assert.equal(draft.tag_uid, "");
    assert.equal(draft.vendor, "");
    draft.vendor = "ELAS";
    draft.opening_weight_g = "420";
    p._preview = {input: JSON.stringify(p._quantityPayload(draft))};
    p.call = async (command, payload) => {
      assert.equal(command, "spools/create");
      assert.equal(payload.vendor, "ELAS");
      assert.equal(payload.opening_weight_g, 420);
      assert.equal(payload.tag_uid, undefined);
      return {spool_id: "created"};
    };
    p._assignSaved = async (id) => {
      assert.deepEqual(p._mountPayload(id), {spool_id: "created", printer: "P2", ams: 2, slot: 1,
        expected_source: {kind: "STORAGE"}, expected_destination_spool_id: null});
    };
    await p._submitRegistration();
  }
});

it("keeps missing printer colours unknown rather than importing black as a measurement", () => {
  const p = panelWith({_dialog: {kind: "new-spool", prefill: {...tray,
    colour_hint: null, colour_hint_rgba: null}}});
  assert.equal(p._registrationDraft().colour_known, "false");
  clearTimeout(p._previewTimer);
});
