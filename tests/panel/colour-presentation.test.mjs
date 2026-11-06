import assert from "node:assert/strict";
import { it } from "node:test";
import { aSpool, panelWith } from "./harness.mjs";
import { colourSample, colourEditor, syncColourEditor } from "../../custom_components/filament_ledger/www/panel/colour.js";

it("bounds all known samples and labels Unknown separately from real black", () => {
  const panel = panelWith();
  for(const colour of ["#000000FF","#FFFFFFFF","#808080FF","#FFFFE0FF","#FF0000FF","#F4F6F8FF","#11223340"]) {
    const html = colourSample(aSpool({colour_rgba:colour}),panel._t);
    assert.match(html,/class="colour-sample/); assert.ok(html.includes(colour));
  }
  const unknown = colourSample(aSpool({colour_known:false,colour_rgba:null}),panel._t);
  assert.match(unknown,/Unknown colour/); assert.doesNotMatch(unknown,/#000000/);
});


it("offers named colours and a picker in both forms, with no separate known/unknown selector", () => {
  for (const language of ["en", "es", "tr"]) {
    const panel = panelWith({_dialog: {kind: "new-spool"}, _detail: aSpool()}, language);
    for (const html of [panel.newSpoolForm(), panel.editSpoolForm()]) {
      assert.match(html, /type="color" data-colour-picker/);
      assert.match(html, /<option value="#ff0000"/);
      assert.doesNotMatch(html, /<select name="colour_known"/);
      assert.ok(html.includes(({en: "Red", es: "Rojo", tr: "Kırmızı"})[language]));
    }
    clearTimeout(panel._previewTimer);
  }
});

function editorControls(value) {
  const code = {value}, preset = {value: "custom"}, picker = {value: "#000000"}, known = {value: "true"};
  const controls = {"[name='colour']": code, "[data-colour-preset]": preset, "[data-colour-picker]": picker, "[name='colour_known']": known};
  const editor = {querySelector: key => controls[key]};
  for (const control of [code, preset, picker]) control.closest = () => editor;
  return {code, preset, picker, known};
}

it("synchronizes named colours, custom codes, Unknown and picker without dropping alpha", () => {
  const {code, preset, picker, known} = editorControls("#11223340");
  syncColourEditor(code);
  assert.equal(preset.value, "custom");
  assert.equal(picker.value, "#112233");
  picker.value = "#abcdef";
  syncColourEditor(picker);
  assert.equal(code.value, "#abcdef40");
  preset.value = "#ff0000";
  syncColourEditor(preset);
  assert.equal(code.value, "#ff0000");
  assert.equal(known.value, "true");
  preset.value = "";
  syncColourEditor(preset);
  assert.equal(code.value, "");
  assert.equal(known.value, "false");
  code.value = "000000ff";
  syncColourEditor(code);
  assert.equal(preset.value, "#000000");
  assert.equal(known.value, "true");
  const html = colourEditor("#11223340", true, panelWith()._t);
  assert.match(html, /value="#11223340"/);
  assert.match(html, /value="custom" selected/);
});


it("keeps Custom selected when switching from a basic colour", () => {
  const {code, preset} = editorControls("#ff0000");
  preset.value = "custom";
  syncColourEditor(preset);
  assert.equal(preset.value, "custom");
  assert.equal(code.value, "#ff0000");
});

it("sends Unknown as a metadata change without an invalid empty colour update", () => {
  const original = globalThis.FormData;
  globalThis.FormData = class { entries() { return new Map(Object.entries({colour: "", colour_known: "false", core_weight_g: "250", low_stock_threshold_g: "", material: "PLA"})).entries(); } };
  try {
    const panel = panelWith({_detail: aSpool()});
    panel._correctionFrom = () => null;
    let sent;
    panel._submitEdit = payload => { sent = payload; };
    panel._onSubmit({preventDefault() {}, target: {dataset: {form: "edit-spool", core: "250"}}});
    assert.equal(sent.colour_known, false);
    assert.equal(sent.colour, null);
  } finally { globalThis.FormData = original; }
});
