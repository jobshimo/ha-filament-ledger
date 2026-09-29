import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { aSpool, panelWith, visibleText } from "./harness.mjs";

describe("the inventory", () => {
  it("teaches how to start when there are no spools", () => {
    const html = panelWith({ _spools: [] }).inventoryView();

    assert.match(visibleText(html), /No spools yet/i);
    assert.match(html, /data-action="dialog" data-id="new-spool"/);
  });

  it("shows each spool's name, material, vendor and balance", () => {
    const panel = panelWith({
      _spools: [aSpool()],
      _stock: { total_g: 640, spool_count: 1, needs_weighing: 0 },
    });

    const text = visibleText(panel.inventoryView());

    assert.match(text, /Galaxy Black/);
    assert.match(text, /PLA · Bambu Lab/);
    assert.match(text, /640 g/);
  });

  it("leaves a finished spool to the Finished tab and keeps the rest", () => {
    const panel = panelWith({
      _spools: [
        aSpool({ id: "empty", name: "Used Up", state: "DEPLETED", percentage: 0 }),
        aSpool({ id: "live", name: "Still Printing" }),
      ],
      _stock: { total_g: 640, spool_count: 2, needs_weighing: 0 },
    });

    const text = visibleText(panel.inventoryView());

    assert.doesNotMatch(text, /Used Up/);
    assert.match(text, /Still Printing/);
  });

  it("escapes what the user typed, so a label cannot inject markup", () => {
    const panel = panelWith({
      _spools: [aSpool({ name: '<img src=x onerror="alert(1)">', vendor: "<b>V</b>" })],
      _stock: { total_g: 640, spool_count: 1, needs_weighing: 0 },
    });

    const html = panel.inventoryView();

    assert.doesNotMatch(html, /<img src=x/);
    assert.doesNotMatch(html, /<b>V<\/b>/);
    assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  });

  it("asks for a weighing when the ledger is unsure", () => {
    const panel = panelWith({
      _spools: [aSpool({ needs_weighing: true, confidence: "LOW" })],
      _stock: { total_g: 640, spool_count: 1, needs_weighing: 1 },
    });

    assert.match(visibleText(panel.inventoryView()), /Weigh/i);
  });

  it("speaks Spanish when the language is Spanish", () => {
    const html = panelWith({ _spools: [] }, "es").inventoryView();

    assert.match(visibleText(html), /Todavía no hay bobinas/);
    assert.doesNotMatch(visibleText(html), /No spools yet/i);
  });
});

describe("the statistics", () => {
  it("says it is loading and holds the period buttons while the first read is in flight", () => {
    const panel = panelWith({ _stats: null, _statsLoading: true, _statsPeriod: "30d" });

    const html = panel.statsView();

    assert.match(visibleText(html), /Loading/);
    assert.equal((html.match(/data-action="stats-period"/g) ?? []).length, 3);
    assert.match(html, /data-id="30d"\s+disabled/);
  });

  it("explains an empty ledger instead of drawing empty charts", () => {
    const panel = panelWith({ _stats: { empty: true }, _statsLoading: false, _statsPeriod: "90d" });

    const html = panel.statsView();

    assert.doesNotMatch(html, /<svg/);
    assert.match(html, /class="st-period on"\s+data-action="stats-period" data-id="90d"/);
  });
});
