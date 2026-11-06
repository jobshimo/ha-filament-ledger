import assert from "node:assert/strict";
import { it } from "node:test";
import { panelWith } from "./harness.mjs";

it("keeps save disabled until the current server preview succeeds", async () => {
  const panel = panelWith({ _dialog: {kind: "new-spool"} });
  const html = panel.newSpoolForm();
  clearTimeout(panel._previewTimer);
  assert.match(html, /type="submit" disabled/);
  assert.match(html, /Partly used/);
  panel.call = async (command, payload) => {
    assert.equal(command, "spools/preview_registration");
    return { opening_weight_g: 1000, opening_basis: payload.opening_basis };
  };
  await panel._previewRegistration();
  assert.doesNotMatch(panel.newSpoolForm(), /type="submit" disabled/);
});

it("an older preview cannot validate changed inputs, and failure preserves the draft", async () => {
  const panel = panelWith({_dialog: {kind: "new-spool"}});
  panel._registrationDraft(); clearTimeout(panel._previewTimer);
  let resolve;
  panel.call = () => new Promise(r => {resolve = r;});
  const pending = panel._previewRegistration();
  panel._dialog.registration.opening_weight_g = "420";
  panel._previewRevision++;
  resolve({opening_weight_g: 1000}); await pending;
  assert.equal(panel._preview, null);
  panel.call = async () => { throw new Error("Offline"); };
  await panel._previewRegistration();
  assert.equal(panel._dialog.registration.opening_weight_g, "420");
  assert.match(panel.newSpoolForm(), /type="submit" disabled/);
});

it("a lost create reply reuses the draft ID and does not submit twice concurrently", async () => {
  const panel = panelWith({_dialog: {kind: "new-spool"}});
  const draft = panel._registrationDraft(); clearTimeout(panel._previewTimer);
  panel._preview = {input: JSON.stringify(panel._quantityPayload(draft))};
  const ids = []; let fail;
  panel.call = async (command, payload) => {
    ids.push(payload.request_id);
    return new Promise((_, reject) => {fail = reject;});
  };
  panel.render = () => {};
  const first = panel._submitRegistration();
  await panel._submitRegistration(); assert.equal(ids.length, 1);
  fail(new Error("Connection lost")); await first;
  const retry = panel._submitRegistration();
  fail(new Error("Connection lost")); await retry;
  assert.deepEqual(ids, [draft.request_id, draft.request_id]);
});
