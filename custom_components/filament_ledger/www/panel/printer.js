// The Printer tab: a read-only glance at each machine.
//
// Installed on the panel by `mixIn` (./mixin.js, ADR-0010); `this` is the panel.

import { esc } from "../i18n.js";
import { fill, hms, holderWord } from "./format.js";

/**
 * What a figure the printer did not report looks like (docs/14 §14.5).
 *
 * A dash, never a zero: a missing figure is not a figure of zero, and rendering one as
 * the other is exactly the optimistic lie this project exists to prevent.
 */
const DASH = "—";

export class PrinterViews {
  // -- printer -----------------------------------------------------------------------

  /**
   * A read-only glance at the machine (docs/14 §14.5).
   *
   * Not a printer UI: control stays a non-goal (N1, docs/01 §1.3), `ha-bambulab` has its
   * own cards, and duplicating them adds risk with no benefit. Every figure the printer
   * did not report renders as a dash rather than a zero — a missing figure is not a
   * figure of zero.
   */
  printerView() {
    const t = this._t;
    if (this._printerLoading && !this._printer) {
      return this.shell("", `<div class="empty">${t("app.loading")}</div>`);
    }
    const state = this._printer;
    if (!state || state.dormant) {
      // The honest no-printer answer, in the voice the sync strip already speaks. No
      // action row with it: refreshing a printer that is not there is not an offer.
      return this.shell(
        "",
        `<div class="empty teach">
          <h2>${t("printer.dormantTitle")}</h2>
          <p>${t("printer.dormantBody")}</p>
          <p class="muted small">${t("printer.dormantFoot")}</p>
        </div>`,
      );
    }

    // A glance has a moment, and the moment is the user's (docs/14 §14.5) — so the one
    // control that takes a fresh one stays where they left it.
    //
    // **A section per machine, not a picker.** The person this tab is for is standing at
    // one of their printers with a part in their hand, and a selector would make them
    // identify their machine by a fifteen-character serial before it told them anything —
    // then remember a choice, which is a wrong default the first time it matters. Sections
    // scroll, and the other machine's answer is already on the screen when they walk over.
    const machines = state.machines ?? [];
    const named = machines.length > 1;
    return this.shell(
      `<div class="bar">
        <button data-action="refresh-printer" ${this._printerLoading ? "disabled" : ""}>${t("printer.refresh")}</button>
      </div>`,
      `<section class="stack">
        ${this.printerTracking(state.tracking)}
        ${machines.map((machine) => this.printerMachine(machine, named)).join("")}
        ${this.printerHours(state.observed_print_time)}
        <p class="muted small">${t("printer.readOnly")}</p>
      </section>`,
    );
  }

  /** One machine's section: what it is called, what it is doing, and what its trays hold. */
  printerMachine(machine, named) {
    // The name beside the serial, for `machineHeading`'s reason: the serial is the
    // identity, and a heading showing only a friendly name would leave a reader with two
    // sections and no way to tell which machine either of them is.
    const heading = machine.printer_name
      ? `<h3 class="pr-h pr-machine-h">${esc(machine.printer_name)}</h3>
         <span class="muted small">${esc(machine.printer)}</span>`
      : `<h3 class="pr-h pr-machine-h">${esc(machine.printer)}</h3>`;
    return `<div class="pr-machine">
      ${named ? heading : ""}
      ${this.printerFacts(machine)}
      ${this.printerError(machine.error)}
      ${this.printerTrays(machine)}
    </div>`;
  }

  printerFacts(state) {
    const t = this._t;
    const progress =
      state.progress_pct == null
        ? DASH
        : `<span class="pr-bar">
             <span class="track"><i style="width:${Number(state.progress_pct)}%"></i></span>
             <span class="pct">${esc(state.progress_pct)}%</span>
           </span>`;
    const layer =
      state.current_layer == null && state.total_layers == null
        ? DASH
        : t("printer.layerOf", {
            current: state.current_layer ?? DASH,
            total: state.total_layers ?? DASH,
          });
    const online = state.online == null ? DASH : t(state.online ? "printer.yes" : "printer.no");
    // Null covers both "the sensor said nothing" and "nothing is printing" — the gateway
    // decides which, so there is no idle case to guess at here (docs/14 §14.5).
    const remaining =
      state.remaining_minutes == null ? DASH : this.duration(state.remaining_minutes);
    return `
      <div class="card pr-facts">
        ${this.printerFact(t("printer.status"), state.status == null ? DASH : esc(state.status))}
        ${this.printerFact(
          t("printer.job"),
          state.job_name == null ? DASH : esc(state.job_display_name ?? state.job_name),
        )}
        ${this.printerFact(t("printer.progress"), progress)}
        ${this.printerFact(t("printer.remaining"), remaining)}
        ${this.printerFact(t("printer.layer"), layer)}
        ${this.printerFact(t("printer.online"), online)}
        ${this.printerFact(
          t("printer.connection"),
          state.connection_mode == null ? DASH : esc(state.connection_mode),
        )}
        ${this.printerFact(
          t("printer.activeFeed"),
          this.activeFeedWord(state.active_feed, (state.holders ?? []).length),
        )}
      </div>`;
  }

  /**
   * Where the machine is drawing from, in the reader's words — *AMS 1 · Slot 2*, or
   * *External spool (right)*.
   *
   * This field showed a dash for every user until v2.9: the backend parsed the sensor's
   * state as an integer and the state is a filament name (docs/12, 2026-09-23). It now
   * arrives as a position, and a position is what is rendered — a bare number would be
   * ambiguous the moment a machine has two units or two holders, which is the same
   * ambiguity the whole release is about.
   */
  activeFeedWord(feed, holderCount) {
    const t = this._t;
    if (!feed) return DASH;
    // No `esc` around the result: `substitute` escapes every value it fills in, and the
    // templates themselves are this project's own strings — the rule every other
    // `printerFact` call on this card follows.
    if (feed.feed === "external") return t(holderWord(feed.holder, holderCount));
    return t("printer.activeFeedAms", {
      ams: feed.ams,
      slot: t("ams.slot", { slot: feed.slot }),
    });
  }

  printerFact(key, value) {
    return `<div class="pr-fact"><div class="k">${key}</div><div class="v">${value}</div></div>`;
  }

  /**
   * The followed machines, as prose: *Workshop A1 (00000000TESTSER)*, or the serial alone.
   *
   * The name is a label and the serial is the identity, so a list that dropped the serial
   * would leave a reader unable to match this sentence to the sections below it — which
   * are the only place a mount, a review or a history row ever names a machine.
   */
  _machineList(printers) {
    return printers
      .map((serial) => {
        const called = this._machineSnapshot(serial)?.printer_name;
        return called ? `${called} (${serial})` : serial;
      })
      .join(", ");
  }

  /**
   * What this ledger is following, and what it found and could not follow.
   *
   * **Rendered only when there is something to say.** One machine, cleanly named, produces
   * nothing here — the section heading above its own facts already names it, and a card
   * repeating that would be chrome. Two or more get the list, because *which machines am I
   * tracking?* stops being obvious the moment the answer is longer than one.
   *
   * `unnamed` is what is left of v1.4's `ignored`: every machine with a readable serial is
   * followed now, so the only thing this ledger passes over is a machine it could not tell
   * apart from another. That is rare enough to be a bug report, which is precisely why it
   * is on a screen rather than in a log.
   */
  printerTracking(tracking) {
    const t = this._t;
    const printers = tracking?.printers ?? [];
    const unnamed = tracking?.unnamed ?? 0;
    if (printers.length < 2 && !unnamed) return "";
    return `
      <div class="card pr-tracking">
        <h3 class="pr-h">${t("printer.trackingHeading")}</h3>
        ${
          printers.length
            ? `<p>${t("printer.trackingFollowing", { serials: this._machineList(printers) })}</p>`
            : ""
        }
        ${unnamed ? `<p class="muted small">${t("printer.trackingUnnamed", { count: unnamed })}</p>` : ""}
      </div>`;
  }

  /**
   * How long this ledger has watched printing happen — never any machine's own hours.
   *
   * No printer reports a lifetime counter, so this total is a sum over the job rows the
   * ledger holds, and the sentence under it says exactly that: how many prints it covers
   * and which day it starts from. A big number with no such line would read as an
   * odometer, which is the fabricated authority this project argues against.
   *
   * Absent rather than zeroed when the ledger has timed nothing, for the reason the Stats
   * card gives: a figure the data cannot support is not improved by drawing a box around it.
   */
  printerHours(observed) {
    if (!observed) return "";
    const t = this._t;
    // One total across every machine, and the sentence under it says so. The job rows
    // written before this ledger recorded which printer ran them name none, so splitting
    // the total per machine would file real hours under a heading nobody could read.
    return `
      <div class="card pr-hours">
        <h3 class="pr-h">${t("printer.hoursHeading")}</h3>
        <div class="v">${this.duration(observed.total_minutes)}</div>
        <p class="muted small">${t("printer.hoursObserved", {
          count: observed.prints,
          since: this.day(observed.since),
        })}</p>
      </div>`;
  }

  /**
   * One date, in the reader's locale. Absolute on purpose, unlike `when()`: this one ends
   * a sentence that begins "since", and "since 12 days ago" is not a date.
   *
   * Returned unescaped because every caller passes it to `t()`, which escapes what it
   * substitutes — escaping here as well would print the entities.
   */
  day(iso) {
    return new Date(iso).toLocaleDateString();
  }

  /**
   * The error, as the searchable HMS quad with the verbatim code in the title — the
   * review card's pattern, and for its reason: the code arrives as a decimal string
   * because a 64-bit HMS value would already be corrupted as a JSON number.
   */
  printerError(error) {
    const t = this._t;
    if (!error) return "";
    if (!error.active) {
      return `<div class="note">${t("printer.noError")}</div>`;
    }
    const code = error.code;
    return `
      <div class="card pr-error">
        <b>${t("printer.errorHeading")}</b>
        ${
          code == null
            ? ""
            : `<span class="rv-hms" title="${t("review.rawErrorTitle", { code })}">${esc(hms(code))}</span>`
        }
      </div>`;
  }

  /**
   * One machine's four-tray strip: what that printer reports beside what the ledger mounted.
   *
   * The per-slot shapes are the sync command's, computed read-only — this tab never runs
   * `DetectSpool`, so looking at it changes nothing (docs/14 §14.5).
   *
   * The ledger side matches on the **whole** tray reference. Matching on the slot number
   * alone would put the other machine's tray 3 spool under this machine's tray 3, which is
   * the exact confusion this release exists to end and would read as authoritative.
   */
  printerTrays(machine) {
    const t = this._t;
    const trays = machine.trays ?? [];
    if (!trays.length) return `<div class="note">${t("printer.noTrays")}</div>`;
    const cards = trays
      .map((tray) => {
        const mounted = this._spools.find(
          (s) =>
            s.location.kind === "AMS_SLOT" &&
            s.location.printer === tray.printer &&
            s.location.slot === tray.slot,
        );
        const swatch = tray.colour_hint
          ? `<div class="reel" style="background:${esc(tray.colour_hint)}"></div>`
          : `<div class="reel empty-reel"></div>`;
        const reported = [tray.name_hint, tray.material_hint].filter(Boolean).map(esc).join(" · ");
        return `<div class="card tray">
          <div class="n">${t("ams.slot", { slot: tray.slot })}</div>
          ${swatch}
          <div class="name">${reported || DASH}</div>
          <div class="muted small">${this.syncStatusWord(tray.status)}</div>
          <div class="muted small">${
            mounted
              ? fill(t("printer.trayLedger"), "spool", esc(mounted.name))
              : t("printer.trayLedgerEmpty")
          }</div>
        </div>`;
      })
      .join("");
    return `
      <div class="pr-trays">
        <h3 class="pr-h">${t("printer.traysHeading")}</h3>
        <div class="trays">${cards}</div>
      </div>`;
  }

  /** The one-word status a slot outcome reads as, reusing the sync strip's vocabulary. */
  syncStatusWord(status) {
    const keys = {
      empty: "sync.empty",
      mounted: "sync.mounted",
      detected: "sync.detected",
      no_tag: "sync.noTag",
      ambiguous_tag: "sync.ambiguous",
      unknown_tag: "sync.notInInventory",
    };
    const key = keys[status];
    return key ? this._t(key) : esc(status);
  }
}
