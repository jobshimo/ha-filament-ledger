// The AMS tab: every unit and holder of every followed machine.
//
// Installed on the panel by `mixIn` (./mixin.js, ADR-0010); `this` is the panel.

import { esc } from "../i18n.js";
import { fill, holderWord, UNIDENTIFIED_PRINTER } from "./format.js";
import { spoolRing } from "./spool-ring.js";

export class AmsViews {
  // -- AMS ---------------------------------------------------------------------------

  /**
   * The machines whose trays this tab has to show, in one canonical order.
   *
   * Two sources, deliberately, and the union is the point. The **followed** set comes from
   * the printer glance: those machines have trays to mount into whether or not anything is
   * in them. The **occupied** set comes from the spools themselves: a ledger migrated from
   * single-printer days holds spools on a machine nobody could name (`printer_adoption`),
   * and a tab that only listed followed machines would hide them — which is how an
   * inventory system starts lying about where a reel is.
   *
   * Followed first, in the backend's own order, so the machine the user prints on does not
   * move when a stale one appears behind it.
   */
  _amsPrinters() {
    const followed = this._printer?.tracking?.printers ?? [];
    // A machine is occupied by whatever is mounted on it, in a tray or on the external
    // holder: a reel on a stale printer's direct feed is as real as one in its AMS.
    const occupied = this._spools
      .filter((s) => s.location.kind === "AMS_SLOT" || s.location.kind === "EXTERNAL_SPOOL")
      .map((s) => s.location.printer);
    return [...new Set([...followed, ...occupied.filter((p) => p != null).sort()])];
  }

  amsView() {
    const t = this._t;
    const printers = this._amsPrinters();
    // No printer, no spools mounted anywhere: one anonymous section, exactly as the tab
    // has always looked. There is nothing to name and nothing to choose between.
    const spaces = printers.length ? printers : [null];
    const followed = new Set(this._printer?.tracking?.printers ?? []);
    // A heading per machine only once there is more than one. A household with one printer
    // sees nothing new, because nothing new is true of it — the same rule the tracking card
    // has followed since v1.4.
    const named = spaces.length > 1;
    const sections = spaces.map((printer) => this.amsSection(printer, named, followed));

    // No action row: mounting and unmounting belong to the slot they act on, and a tray
    // card already carries its own buttons.
    return this.shell(
      "",
      `<section class="stack">
        <div class="note">${t("ams.note")}</div>
        ${sections.join("")}
      </section>`,
    );
  }

  /**
   * What the printer itself reports for one tray, read off the last printer snapshot.
   *
   * The AMS view draws from the ledger, so a tray holding a spool the ledger cannot
   * identify — a chipless third-party reel — would render as plain "Empty" even though
   * the machine is holding it. This lookup lets that card say what is actually there.
   * Null when no snapshot has been taken yet or the tray was not reported: the view
   * renders exactly as before, because an honest extra word must never become a
   * dependency.
   */
  _trayStatus(printer, ams, slot) {
    // **The whole reference, not the slot alone.** Matching on the number would answer
    // AMS 2's tray 1 with AMS 1's status the moment a machine had two units, which is the
    // same ambiguity a bare slot had across two printers before v2.0.
    const machine = this._machineSnapshot(printer);
    return (
      (machine?.trays ?? []).find((tray) => tray.ams === ams && tray.slot === slot)?.status ??
      null
    );
  }

  /** One machine's last glance, or null — `printer` is null in the anonymous-space case. */
  _machineSnapshot(printer) {
    const machines = this._printer?.machines ?? [];
    return printer === null ? (machines[0] ?? null) : machines.find((m) => m.printer === printer);
  }

  /**
   * Which AMS units to draw a block for: what the printer says it has, what the ledger
   * holds spools on, and one as the floor.
   *
   * The same union rule `_amsPrinters` uses for machines, and for its reason. A unit the
   * glance has not mentioned may still hold a reel the ledger recorded — a machine that
   * has gone away, a snapshot not taken yet — and a view that listed only what discovery
   * currently reports would hide it. One is the floor because every machine has an AMS 1
   * to mount into even when nothing has been discovered at all.
   */
  _amsUnits(printer) {
    const reported = this._machineSnapshot(printer)?.ams_units ?? [];
    const held = this._spools
      .filter(
        (s) =>
          s.location.kind === "AMS_SLOT" &&
          (printer === null || s.location.printer === printer) &&
          s.location.ams != null,
      )
      .map((s) => s.location.ams);
    return [...new Set([1, ...reported, ...held])].sort((a, b) => a - b);
  }

  /**
   * Which holders to draw a card for — the same union, one position over.
   *
   * A machine that published no holder sensor still has a holder: every Bambu printer
   * does, and the backend deliberately reports none rather than inventing one
   * (`ReadPrinterState._holders`). The floor is this view's judgement, made here.
   */
  _amsHolders(printer) {
    const reported = (this._machineSnapshot(printer)?.holders ?? []).map((h) => h.holder);
    const held = this._spools
      .filter(
        (s) =>
          s.location.kind === "EXTERNAL_SPOOL" &&
          (printer === null || s.location.printer === printer),
      )
      .map((s) => s.location.holder ?? 1);
    return [...new Set([1, ...reported, ...held])].sort((a, b) => a - b);
  }

  /**
   * One machine's positions: a block per AMS unit, then a card per direct feed.
   *
   * The direct feed is the holder beside the AMS that feeds the extruder directly. It is
   * a place a reel can be and be consumed from, so it gets a card drawn with the same
   * markup as a tray: the same ring, the same buttons, the same empty state with the same
   * [ Mount ]. What differs is only what the backend needs to name it — no slot, a
   * `holder` instead — and the heading, which says what it is rather than a number. A
   * ledger whose spools never report an `EXTERNAL_SPOOL` location simply shows the card
   * empty; nothing is invented to fill it.
   *
   * **A block per unit, and a heading over it only when there is more than one** (v2.9).
   * The same rule the machine heading follows: with one unit the number says nothing the
   * reader did not already know, and a household with a single AMS sees the tab it has
   * always seen.
   *
   * `printer` is null only in the one-anonymous-space case above; the mount buttons then
   * name no printer and the backend resolves the absence, which is the same path a v1
   * automation takes.
   */
  amsSection(printer, named, followed) {
    const units = this._amsUnits(printer);
    const blocks = units.map((ams) => this.amsUnitBlock(printer, ams, units.length > 1));
    return `<div class="ams-space">
      ${named ? this.machineHeading(printer, followed) : ""}
      ${blocks.join("")}
      <div class="trays">${this.holderCards(printer).join("")}</div>
    </div>`;
  }

  /** One AMS unit's four trays, headed by its ordinal once a machine has more than one. */
  amsUnitBlock(printer, ams, numbered) {
    const t = this._t;
    // A location names its printer and its unit, so the match names both — otherwise a
    // spool in another machine's tray 3, or in this machine's *other* unit's tray 3, would
    // appear here as though it were in this one.
    const onThisMachine = (location) => printer === null || location.printer === printer;
    const here = (location) =>
      location.kind === "AMS_SLOT" && onThisMachine(location) && (location.ams ?? 1) === ams;
    const machine = esc(printer ?? "");
    const slots = [1, 2, 3, 4].map((slot) => {
      const spool = this._spools.find((s) => here(s.location) && s.location.slot === slot);
      if (!spool) {
        // "Empty" is the ledger's word, and for a tray holding a chipless reel it is the
        // wrong one: the machine is plainly holding something the ledger cannot identify.
        // The printer snapshot says so, and the card repeats it — the same [ Mount ]
        // button then does for a third-party reel what the chip does for a Bambu one,
        // because consumption already charges by location, not by tag.
        const chipless = this._trayStatus(printer, ams, slot) === "NO_TAG";
        return this.emptyPositionCard(
          t("ams.slot", { slot }),
          t(chipless ? "ams.chipless" : "ams.empty"),
          `<button data-action="mount-slot" data-slot="${slot}" data-ams="${esc(ams)}"
                  data-printer="${machine}">${t("act.mount")}</button>`,
        );
      }
      return this.positionCard(t("ams.slot", { slot }), spool);
    });
    return `<div class="ams-unit">
      ${numbered ? `<h4 class="ams-unit-h">${t("ams.unit", { ams })}</h4>` : ""}
      <div class="trays">${slots.join("")}</div>
    </div>`;
  }

  /**
   * One card per direct feed, named by where it is rather than by a number.
   *
   * With one holder the card reads *External spool*, exactly as it always has. With two —
   * a dual-nozzle machine — *external spool* names neither, so they are told apart as
   * left and right: the user is standing at the machine looking at two holders, and
   * upstream's own indexes are no help to them at all.
   */
  holderCards(printer) {
    const t = this._t;
    const holders = this._amsHolders(printer);
    const machine = esc(printer ?? "");
    const reported = this._machineSnapshot(printer)?.holders ?? [];
    return holders.map((holder) => {
      const heading = t(holderWord(holder, holders.length));
      const spool = this._spools.find(
        (s) =>
          s.location.kind === "EXTERNAL_SPOOL" &&
          (printer === null || s.location.printer === printer) &&
          (s.location.holder ?? 1) === holder,
      );
      if (spool) return this.positionCard(heading, spool);
      // "Empty" is the ledger's word, and the printer may disagree: a reel on the holder
      // that the ledger has no row for is the holder's form of `ams.chipless`, and the
      // [ Mount ] button beside it is how the user says which spool it is. Null — the
      // printer did not say — reads as the ledger's own word, never as an occupied holder.
      const occupied = reported.find((h) => h.holder === holder)?.empty === false;
      return this.emptyPositionCard(
        heading,
        t(occupied ? "ams.occupied" : "ams.empty"),
        `<button data-action="mount-external" data-holder="${esc(holder)}"
                data-printer="${machine}">${t("act.mount")}</button>`,
      );
    });
  }

  /** A position with nothing the ledger knows of in it, and the button that changes that. */
  emptyPositionCard(heading, word, mountButton) {
    return `<div class="card tray empty-tray">
      <div class="n">${heading}</div>
      <div class="muted">${word}</div>
      ${mountButton}
    </div>`;
  }

  /**
   * A position holding a spool — an AMS tray or the external holder, the card is the same.
   *
   * A position keeps showing an empty spool: the reel is still physically loaded, and a
   * slot that emptied itself on screen would be a lie about the machine (docs/06 §6.4).
   */
  positionCard(heading, spool) {
    const t = this._t;
    return `<div class="card tray ${spool.state === "DEPLETED" ? "depleted" : ""}">
      <div class="tray-head">
        <div class="n">${heading}</div>
        ${this.spoolMenu(spool)}
      </div>
      <div class="tray-art">
        ${spoolRing("slot", spool.percentage, spool.colour)}
        <div class="ring-mid">
          <span class="ring-hub" style="background:${esc(spool.colour)}"></span>
        </div>
      </div>
      <div class="name">${esc(spool.name)}</div>
      <div class="big">${spool.balance_g}<small> g</small></div>
      <div class="barline">
        <div class="track"><i style="width:${spool.percentage}%;background:${esc(spool.colour)}"></i></div>
        <span class="pct">${spool.percentage}%</span>
      </div>
      <div class="foot">${this.confidenceChip(spool.confidence)}</div>
      <div class="tray-actions">
        <button data-action="open" data-id="${esc(spool.id)}">${t("act.open")}</button>
        <button data-action="unmount" data-id="${esc(spool.id)}">${t("act.unmount")}</button>
      </div>
    </div>`;
  }

  /**
   * The heading over one machine's trays, and the one line a stale machine needs.
   *
   * A machine holding spools that discovery is *not* currently following is not an error
   * and is not hidden: it is a ledger that was migrated from single-printer days before a
   * second machine appeared, or a printer that has gone away. The spools are real, they are
   * where the ledger last saw them, and the sentence says what to do — move each one onto
   * the machine it is actually in.
   */
  machineHeading(printer, followed) {
    const t = this._t;
    const unnamed = printer === UNIDENTIFIED_PRINTER || printer === null;
    const called = this._machineSnapshot(printer)?.printer_name;
    // What the machine answers to, with the serial beside it — never instead of it. The
    // serial is what every row, tray reference and mount is keyed by, and a heading that
    // showed only a friendly name would leave a reader with two sections and no way to
    // tell which serial either of them is (v2.9).
    const name = unnamed ? t("ams.machineUnnamed") : esc(called || printer);
    const serial = !unnamed && called ? `<span class="muted small">${esc(printer)}</span>` : "";
    const stale = printer !== null && !followed.has(printer);
    return `<div class="ams-head">
      <h3 class="pr-h">${name}</h3>
      ${serial}
      ${stale ? `<p class="muted small">${t("ams.machineStale")}</p>` : ""}
    </div>`;
  }
}
