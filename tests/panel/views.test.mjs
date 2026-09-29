import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { aSpool, panelWith, visibleText } from "./harness.mjs";

// Each tab lives in its own module (www/panel/, ADR-0010) and reaches the panel through
// `mixIn`. A view that lost an import would throw only when it is drawn, so every tab is
// drawn here from the state a freshly constructed panel starts in.
const TABS = {
  inventoryView: "inventory",
  finishedView: "finished",
  amsView: "ams",
  historyView: "history",
  statsView: "statistics",
  reviewView: "review",
  trashView: "trash",
  printerView: "printer",
  settingsView: "settings",
};

describe("every tab, freshly opened", () => {
  for (const [method, tab] of Object.entries(TABS)) {
    it(`draws the ${tab} tab in both languages`, () => {
      for (const language of ["en", "es"]) {
        // Drawn without throwing, into the panel's frame. Some tabs are deliberately blank
        // before their first read (Finished leaves the error bar to speak), so the frame is
        // what is asserted, not text.
        const html = panelWith({}, language)[method]();

        assert.match(html, /class="view-scroll"/, `${tab} in ${language} left the frame`);
      }
    });
  }
});

describe("one spool in full", () => {
  it("lists its movements with their signed amounts", () => {
    const spool = {
      ...aSpool(),
      history: [
        {
          id: "m1",
          type: "OPENING_BALANCE",
          source: "USER_CONFIRMED",
          amount_g: 750,
          balance_after_g: 750,
          occurred_at: "2026-09-01T10:00:00+00:00",
          note: null,
          voided: false,
        },
        {
          id: "m2",
          type: "PRINT_CONSUMPTION",
          source: "PRINTER_REPORTED",
          amount_g: -110,
          balance_after_g: 640,
          occurred_at: "2026-09-02T10:00:00+00:00",
          note: "bracket <v3>",
          voided: false,
        },
      ],
    };

    const html = panelWith({ _detail: spool }).detailView();
    const text = visibleText(html);

    assert.match(text, /Galaxy Black/);
    assert.match(text, /− 110\.0/);
    assert.match(html, /bracket &lt;v3&gt;/);
  });
});
