// The Inventory and Finished tabs, and the spool card both draw.
//
// Installed on the panel by `mixIn` (./mixin.js, ADR-0010); `this` is the panel.

import { esc } from "../i18n.js";
import { fill, grams } from "./format.js";
import { spoolRing } from "./spool-ring.js";

/** The CSS class per confidence level; the words themselves come from the table. */
const CONFIDENCE_CLASS = { HIGH: "high", MEDIUM: "med", LOW: "low" };

export class InventoryViews {
  // -- inventory ---------------------------------------------------------------------

  /** Run the pass, keep the outcome for the strip, then refresh what it changed. */
  async _syncTrays() {
    try {
      this._sync = await this.call("trays/sync");
      await this.refresh();
    } catch (error) {
      this._error = error.message || String(error);
      this.render();
    }
  }

  inventoryView() {
    const t = this._t;
    // No actions while there are no spools: the one thing to do is the empty state's own
    // call to action, and offering it twice would teach that they differ.
    if (!this._spools.length) {
      return this.shell(
        "",
        `<section class="stack">
          ${this.syncStrip()}
          <div class="empty teach">
            <h2>${t("inv.emptyTitle")}</h2>
            <p>${t("inv.emptyBody")}</p>
            <button class="primary" data-action="dialog" data-id="new-spool">${t("inv.emptyCta")}</button>
            <p class="muted small">${t("inv.emptyLoaded")}
              <button class="link" data-action="sync-trays">${t("inv.sync")}</button>
              ${t("inv.emptyLoadedTail")}</p>
          </div>
        </section>`,
      );
    }

    const stat = (key, value, alert) =>
      `<div class="stat"><div class="k">${key}</div><div class="v ${alert ? "alert" : ""}">${value}</div></div>`;

    // The Inventory shows what can still print. A depleted spool is a real object — the
    // sensors keep counting it, the AMS view keeps drawing it in its tray — but it is not
    // stock to choose from, so it lives in the Finished tab instead of sinking to the
    // bottom of this grid for ever.
    const holding = this._spools.filter((s) => s.state !== "DEPLETED");

    // The two buttons lead the view rather than following the summary card, which is where
    // docs/06 §6.2 has always drawn them and where a pinned row has to be anyway. The
    // summary is a figure to read, not a control to reach: it scrolls with the spools.
    return this.shell(
      `<div class="bar">
        <button class="primary" data-action="dialog" data-id="new-spool">${t("inv.newSpool")}</button>
        <button data-action="sync-trays">${t("inv.sync")}</button>
      </div>`,
      `<section class="stack">
        <div class="card summary">
          ${stat(t("inv.totalStock"), esc(grams(this._stock?.total_g ?? 0)))}
          ${stat(t("inv.spools"), esc(this._stock?.spool_count ?? 0))}
          ${stat(t("inv.needsWeighing"), esc(this._stock?.needs_weighing ?? 0), this._stock?.needs_weighing)}
        </div>
        ${this.syncStrip()}
        ${
          holding.length
            ? `<div class="grid">${holding.map((s) => this.spoolCard(s)).join("")}</div>`
            : `<div class="empty"><p>${t("inv.allFinished")}</p></div>`
        }
      </section>`,
    );
  }
  // -- finished ----------------------------------------------------------------------

  /**
   * The past tense of the Inventory: spools whose filament is gone — run out, or thrown
   * away (docs/14 §14.4.4's terms, applied one tab over). Rendered with the same cards
   * as the Inventory, so opening a spool's history and its actions work here exactly as
   * they do there — this is a different question over the same objects, not a different
   * kind of object.
   */
  finishedView() {
    const t = this._t;
    const spools = this._finished;
    if (!spools) {
      // Nothing yet: the first read is in flight, or it failed and the error bar above
      // has already said what happened.
      return this.shell(
        "",
        this._finishedLoading ? `<div class="empty">${t("app.loading")}</div>` : "",
      );
    }
    if (!spools.length) {
      return this.shell(
        "",
        `<div class="empty teach">
          <h2>${t("fin.emptyTitle")}</h2>
          <p>${t("fin.emptyBody")}</p>
        </div>`,
      );
    }
    return this.shell(
      "",
      `<section class="stack">
        <p class="muted small">${t("fin.body")}</p>
        <div class="grid">${spools.map((s) => this.spoolCard(s)).join("")}</div>
      </section>`,
    );
  }

  /** The last sync's outcome, one line per slot the printer reported. Transient. */
  syncStrip() {
    const t = this._t;
    const sync = this._sync;
    if (!sync) return "";
    const dismiss = `<button class="sync-dismiss" data-action="sync-dismiss">${t("act.dismiss")}</button>`;
    if (sync.dormant) {
      // The honest no-printer answer — not a spinner, not four invented empty slots.
      return `
        <div class="card sync-strip">
          <div class="sync-head"><b>${t("sync.dormantTitle")}</b>${dismiss}</div>
          <p class="muted small">${t("sync.dormantBody")}</p>
        </div>`;
    }
    if (!sync.slots.length) {
      return `
        <div class="card sync-strip">
          <div class="sync-head"><b>${t("sync.noTraysTitle")}</b>${dismiss}</div>
          <p class="muted small">${t("sync.noTraysBody")}</p>
        </div>`;
    }
    const rows = sync.slots.map((o) => this.syncRow(o)).join("");
    return `
      <div class="card sync-strip">
        <div class="sync-head"><b>${t("sync.doneTitle")}</b>${dismiss}</div>
        ${rows}
      </div>`;
  }

  syncRow(outcome) {
    const t = this._t;
    const slot = `<span class="sync-slot">${t("sync.slot", { slot: outcome.slot })}</span>`;
    const hints = [outcome.name_hint, outcome.material_hint].filter(Boolean).map(esc).join(" · ");
    const swatch = outcome.colour_hint
      ? `<span class="sync-dot" style="background:${esc(outcome.colour_hint)}"></span>`
      : "";
    switch (outcome.status) {
      case "empty":
        return `<div class="sync-row">${slot}<span class="muted">${t("sync.empty")}</span></div>`;
      case "mounted":
        return `<div class="sync-row">${slot}${swatch}<span>${esc(outcome.spool_name)}</span>
          <span class="muted">${t("sync.mounted")}</span></div>`;
      case "detected":
        return `<div class="sync-row">${slot}${swatch}<span>${esc(outcome.spool_name)}</span>
          <span class="muted">${t("sync.detected")}</span></div>`;
      case "no_tag":
        // The hints are already escaped, so they go in through `fill` rather than as a
        // parameter — which would double-encode a name carrying an ampersand.
        return `<div class="sync-row">${slot}${swatch}<span class="muted">${
          hints ? fill(t("sync.noTagHints"), "hints", hints) : t("sync.noTag")
        }</span></div>`;
      case "ambiguous_tag":
        return `<div class="sync-row">${slot}${swatch}<span class="muted">${t("sync.ambiguous", {
          tag: outcome.tag_uid,
        })}</span></div>`;
      case "unknown_tag":
        return `<div class="sync-row unknown">${slot}${swatch}
          <span>${
            hints
              ? fill(t("sync.unknownTagHints", { tag: outcome.tag_uid }), "hints", hints)
              : t("sync.unknownTag", { tag: outcome.tag_uid })
          }</span>
          <span class="muted">${t("sync.notInInventory")}</span>
          <button data-action="sync-register" data-slot="${esc(outcome.slot)}">${t("sync.register")}</button>
        </div>`;
      default:
        return `<div class="sync-row">${slot}<span class="muted">${esc(outcome.status)}</span></div>`;
    }
  }

  spoolCard(spool) {
    const t = this._t;
    const sealed = spool.state === "SEALED";
    const depleted = spool.state === "DEPLETED";
    // A sealed spool is full by construction and a depleted one is empty by construction,
    // so each gets the word instead of a percentage nobody needs to read. The middle case
    // is the only one where the figure carries information.
    const gauge = sealed
      ? `<span class="chip">${t("inv.sealed")}</span>`
      : depleted
        ? `<span class="chip">${this.stateLabel(spool.state)}</span>`
        : `<span class="pct">${spool.percentage}%</span>`;
    return `
      <article class="card spool ${spool.has_anomaly ? "anomaly" : ""} ${depleted ? "depleted" : ""}"
        data-action="open" data-id="${esc(spool.id)}">
        <span class="shim" aria-hidden="true"></span>
        <div class="swatch" style="background:${esc(spool.colour)}"></div>
        <div class="spool-art">
          <span class="hatch" aria-hidden="true"></span>
          ${spoolRing("card", sealed ? 100 : spool.percentage, spool.colour)}
          <div class="ring-mid">
            <span class="ring-pct">${sealed ? 100 : spool.percentage}<small>%</small></span>
          </div>
        </div>
        <div class="spool-body">
          <div class="spool-head">
            <div class="spool-id">
              <div class="name">${esc(spool.name)}</div>
              <div class="sub">${esc(spool.material)}${spool.vendor ? ` · ${esc(spool.vendor)}` : ""}</div>
            </div>
            ${this.spoolMenu(spool)}
          </div>
          <div class="big">${spool.balance_g}<small> g</small></div>
          <div class="foot">
            ${gauge}
            ${this.confidenceChip(spool.confidence)}
            <span class="muted">· ${this.locationLabel(spool.location)}</span>
          </div>
          ${spool.needs_weighing ? `<div class="cta">${t("inv.weighThis")}</div>` : ""}
        </div>
      </article>`;
  }

  /**
   * The spool action rail, collapsed (docs/06 §6.5, docs/16 §16.10).
   *
   * One control, at the two sizes a spool is drawn small — the inventory card and an AMS
   * tray. Neither has room for a labelled row and neither should be made to grow one, so
   * the rail folds into the glyph docs/06 §6.5 has drawn since its first draft, and opens
   * as a sheet listing exactly what the detail view lays out in full.
   *
   * It sits *in* the card's header row rather than over its corner. The floating glyph it
   * replaces belonged to no grid, overlapped the name at narrow widths, and could not be
   * given a tap target without covering the text it sat on.
   */
  spoolMenu(spool) {
    const label = this._t("act.spoolActions");
    return `<button class="spool-menu" data-action="spool-actions" data-id="${esc(spool.id)}"
      title="${label}" aria-label="${label}" aria-haspopup="dialog">⋮</button>`;
  }

  /**
   * Whether *mark as finished* is offered at all.
   *
   * It reconciles the spool to zero, so it needs something to reconcile away: a spool
   * already at zero would be refused by the use case for recording nothing, and a retired
   * one would be refused for being retired. The panel does not ask a question whose answer
   * it already knows — the same rule `rowActions` follows.
   */
  _finishable(spool) {
    if (!spool || spool.state === "DISCARDED" || spool.state === "DELETED") return false;
    return Number(spool.balance_exact_g ?? 0) > 0;
  }

  /**
   * The confidence dot and its word (docs/02 §2.6). `suffix` spells out "confidence"
   * after it, which the detail view wants and a crowded card does not.
   *
   * The word is already a table result, so it goes in through `fill` rather than as a
   * parameter — the same rule every other spliced fragment in this file follows.
   */
  confidenceChip(level, suffix = false) {
    const word = this._t(`conf.${level}`);
    const text = suffix ? fill(this._t("conf.suffix"), "level", word) : word;
    return `<span class="conf ${CONFIDENCE_CLASS[level] ?? ""}"><i></i>${text}</span>`;
  }

  /**
   * The badge and the two lines that explain it, as docs/06 §6.5 draws them: the reason
   * beside the chip, the anchor under it.
   *
   * A level on its own is a colour that changes for reasons the reader cannot see — and
   * LOW is reached two ways, so the badge alone cannot even say which rule fired. The
   * reason names it; the anchor says *since when*, and whether that anchor is a weighing
   * or a registration, because the two are different promises.
   *
   * Every fact here was measured server-side (`ConfidenceBasis`, `application/query.py`)
   * over the same window the level was evaluated on, so the sentence cannot describe a
   * spool the badge does not. What is left in the panel is choosing strings — the only
   * part of the explanation that belongs in the layer with no test harness (docs/14 §14.8).
   *
   * `when()` returns an already-safe fragment, so it goes in through `fill`; the two
   * figures are wire numbers and go in as parameters, where `t` escapes them.
   */
  confidenceBlock(spool) {
    const t = this._t;
    const chip = this.confidenceChip(spool.confidence, true);
    const basis = spool.confidence_basis;
    if (!basis) return `<div class="foot">${chip}</div>`;

    let reason;
    if (basis.estimates_since) {
      reason = fill(t("conf.why.estimate"), "when", this.when(basis.latest_estimate_at));
    } else if (basis.consumed_since_g > 0) {
      reason = t("conf.why.drawn", {
        grams: basis.consumed_since_g,
        pct: basis.consumed_since_pct,
      });
    } else {
      reason = t("conf.why.nothing");
    }

    // A history with no anchor at all names none rather than inventing one — the same
    // honesty the rest of the panel shows a figure the printer did not report. An anchor
    // type the table does not know renders escaped rather than as its own key, exactly as
    // `movementLabel` does: a key is a code constant, and an anchor type is wire data.
    const key = `conf.anchor.${basis.anchor ?? "NONE"}`;
    const template = t(key);
    const anchor =
      template === key
        ? esc(basis.anchor)
        : fill(template, "when", this.when(basis.anchored_at));

    return `
      <div class="foot">${chip}<span class="muted">· ${reason}</span></div>
      <div class="conf-anchor">${anchor}</div>`;
  }
}
