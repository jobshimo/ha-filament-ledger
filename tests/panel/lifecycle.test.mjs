import assert from "node:assert/strict";
import { it } from "node:test";
import { Panel } from "./harness.mjs";

it("reflects language after connection without changing attributes during construction", () => {
  class CheckedPanel extends Panel {
    set lang(value) {
      assert.ok(this.isConnected, "Custom element constructors must not add attributes");
      this._reflectedLanguage = value;
    }
  }
  const panel = new CheckedPanel();
  assert.equal(panel._reflectedLanguage, undefined);
  assert.equal(panel._lang, "en");

  globalThis.window = { addEventListener() {} };
  panel.isConnected = true;
  panel._initAppearance = () => {};
  panel.render = () => {};
  panel.shadowRoot.getElementById = () => ({ addEventListener() {} });
  panel.connectedCallback();
  assert.equal(panel._reflectedLanguage, "en");

  panel._applyLanguage();
  assert.equal(panel._reflectedLanguage, panel._lang);
});
