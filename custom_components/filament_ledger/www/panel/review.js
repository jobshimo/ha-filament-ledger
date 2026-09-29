// The review queue: each interrupted print and how its grams are charged.
//
// Installed on the panel by `mixIn` (./mixin.js, ADR-0010); `this` is the panel.

import { esc } from "../i18n.js";
import { fill, grams, hms, holderWord, round1, typedGrams } from "./format.js";

/**
 * The tray one review row is about, read back off the element that rendered it.
 *
 * The parts travel together because a tray takes all of them to name — the review card
 * renders exactly what the backend froze, and the approval sends exactly that back.
 * `feed` says which kind of position it is: an AMS tray is `printer` + `ams` + `slot`,
 * and the printer's own external spool (direct feed) is `printer` + `holder`, with `ams`
 * and `slot` sent as **null** rather than omitted, so the entry always has the same keys
 * and the backend never has to guess whether a missing slot means "external" or "forgot".
 * `holder` is the mirror of that: null for a tray, and the holder's number for a feed,
 * because a dual-nozzle machine has two and *the external spool* names neither.
 *
 * `ams`, `slot` and `holder` are numbers on the wire and come out of `dataset` as strings,
 * so they go back as numbers; the schema would coerce them, but a payload that reads as
 * the data it describes is worth the calls. An external line renders the tray half as
 * empty strings, and `Number("")` is 0 — a slot that does not exist — which is why the
 * branch is on the feed and not on the strings.
 */
const trayRef = (element) => {
  const feed = element.dataset.feed === "external" ? "external" : "ams";
  return feed === "external"
    ? {
        printer: element.dataset.printer,
        ams: null,
        slot: null,
        holder: Number(element.dataset.holder) || 1,
        feed,
      }
    : {
        printer: element.dataset.printer,
        ams: Number(element.dataset.ams),
        slot: Number(element.dataset.slot),
        holder: null,
        feed,
      };
};

export class ReviewViews {
  // -- review ------------------------------------------------------------------------

  reviewView() {
    const t = this._t;
    if (!this._reviews.length) {
      return this.shell(
        "",
        `<div class="empty teach">
          <h2>${t("review.emptyTitle")}</h2>
          <p>${t("review.emptyBody")}</p>
          <p class="muted">${t("review.emptyFoot")}</p>
        </div>`,
      );
    }

    // Newest first (docs/06 §6.3): the backend serves oldest first, the card stack leads
    // with the doubt the user most recently created. ISO timestamps sort lexically.
    const cards = this._reviews
      .slice()
      .sort((a, b) => String(b.opened_at).localeCompare(String(a.opened_at)))
      .map((review) => this.reviewCard(review))
      .join("");
    // The count is a caption, not a control, so it scrolls with the cards it counts. Each
    // card carries its own Approve and Dismiss, beside the figures they commit.
    return this.shell(
      "",
      `<section class="stack">
        <div class="muted">${t("review.pending", { count: this._reviews.length })}</div>
        ${cards}
      </section>`,
    );
  }

  reviewCard(review) {
    const t = this._t;
    const failed = review.job_state === "FAILED";
    // NONE doubles as the explicit no-consumption-data flag when every frozen figure is
    // zero (domain/value/review.py): that review renders the distinct no-data card, not
    // an estimator line — a zero the user was told about, not one the system invented.
    const noData =
      review.estimator === "NONE" && review.lines.every((line) => line.estimated_g === 0);

    const metaBits = [this.when(review.opened_at)];
    if (review.job_state === "FINISHED") {
      metaBits.push(t("review.completed"));
    } else if (review.layer_reached != null && review.total_layers != null) {
      const figures = { layer: review.layer_reached, total: review.total_layers };
      metaBits.push(
        review.progress_pct != null
          ? t("review.stoppedAtLayerPct", { ...figures, pct: review.progress_pct })
          : t("review.stoppedAtLayer", figures),
      );
    } else if (review.progress_pct != null) {
      metaBits.push(t("review.stoppedAtPct", { pct: review.progress_pct }));
    }

    // The raw facts, verbatim (docs/06 §6.3): the HMS quad is searchable, the title holds
    // the untouched integer, and `gcode_state` travels unparaphrased next to it.
    const rawBits = [];
    // String-or-null on the wire — 64-bit codes exceed a JSON number's exact range.
    // "0" is the printer's no-error value, hidden exactly as the integer 0 was.
    if (review.raw_print_error != null && review.raw_print_error !== "0") {
      rawBits.push(
        `${t("review.printerError")} <span class="rv-hms" title="${t("review.rawErrorTitle", {
          code: review.raw_print_error,
        })}">${esc(hms(review.raw_print_error))}</span>`,
      );
    }
    if (review.raw_gcode_state) {
      rawBits.push(t("review.printerReported", { state: review.raw_gcode_state }));
    }

    const estimator = t(`est.${review.estimator}`);
    const banner = noData
      ? `<div class="rv-nodata">
           <div class="t">${t("review.noDataTitle")}</div>
           <div class="muted small">${t("review.noDataBody")}</div>
         </div>`
      : `<div class="rv-est">${
          estimator === `est.${review.estimator}` ? esc(review.estimator) : estimator
        }</div>`;

    const rows = review.lines.map((line) => this.reviewTray(line)).join("");
    const total =
      review.lines.length > 1
        ? `<div class="rv-total">${t("review.total", {
            grams: review.estimated_total_g.toFixed(1),
          })}</div>`
        : "";

    // Approve starts disabled whenever a non-zero tray charges nothing — the button and
    // the domain rule (02 §2.3) must never disagree about what is legal. A tray freezes
    // with at most one charge, so nothing can start out partly attributed; the running
    // remainder in `_syncReviewCard` is what watches for that as the user types.
    // Named by feed and slot, not by slot alone: the external spool has no slot number and
    // the hint has to call it what the card calls it.
    const blockedTrays = review.lines
      .filter((line) => !line.charges.length && line.estimated_g !== 0)
      .map((line) => ({ feed: line.feed ?? "ams", slot: line.slot }));
    const blocked = blockedTrays.length > 0;

    return `
      <article class="card rv-card" data-id="${esc(review.id)}">
        <div class="rv-head">
          <span class="rv-ico">${failed ? "⛔" : "⚠"}</span>
          <span class="rv-name">${esc(review.job_display_name ?? review.job_name)}</span>
          <span class="rv-state">${esc(review.job_state)}</span>
        </div>
        <div class="sub">${metaBits.join(" · ")}</div>
        ${rawBits.length ? `<div class="sub">${rawBits.join(" · ")}</div>` : ""}
        ${banner}
        <div class="rv-rows">${rows}${total}</div>
        <div class="rv-weigh">
          <span>${noData ? t("review.weighedSpools") : t("review.weighedWaste")}</span>
          <input class="rv-weighed num" type="number" min="0" step="0.1"> g
          <button data-action="review-distribute">${t("review.distribute")}</button>
        </div>
        <label class="rv-notewrap">${t("act.note")}
          <input class="rv-note" placeholder="${t("act.optional")}">
        </label>
        <div class="rv-actions">
          <button data-action="review-dismiss" data-id="${esc(review.id)}">${t("act.dismiss")}</button>
          <button class="primary rv-approve" data-action="review-approve" data-id="${esc(review.id)}"
            ${blocked ? "disabled" : ""}>${t("review.approve")}</button>
        </div>
        <div class="rv-hint muted small" ${blocked ? "" : "hidden"}>${this._approveHint(blockedTrays)}</div>
      </article>`;
  }

  /**
   * One tray: the figure the printer reported for it, and the spools it is charged to
   * (docs/06 §6.3).
   *
   * The two are separate rows because they are separate facts. A tray's amount is one
   * number — the printer reports one per tray and can report nothing else — while its
   * attribution is a list, because a spool that empties mid-print and is replaced in the
   * same tray leaves that one number belonging to two spools.
   *
   * With one charge the tray reads exactly as it always has: a swatch, a name, and the
   * tray's own figure, because with one charge the two numbers are the same number and
   * showing it twice would invite them to disagree. `[ + Add spool ]` is what reveals the
   * per-charge fields, and `data-frozen` is what the collapsed row renders off — the spool
   * the review froze, so a tray that has been split and unsplit comes back to a picker
   * rather than to a name it can no longer change.
   *
   * A line is one of two feeds. `ams` is a tray, named by `ams` + `slot`; `external` is
   * the printer's own spool holder, which has neither. The feed rides on the element as
   * `data-feed` so `trayRef` can send the line back in the shape it arrived in, and the
   * label says "External spool" rather than a slot number that does not exist. A line
   * with no `feed` at all is a tray — the shape every review had before the external
   * spool was a place a print could draw from.
   */
  /**
   * How many holders the machine a review line names has — for the wording, nothing more.
   *
   * A card for a machine that has gone away, or one opened before the first glance
   * arrived, answers one, and the row then reads *External spool*: the sentence a reader
   * has always seen, rather than a *(left)* that implies a right nobody can see.
   */
  _reviewHolderCount(printer) {
    return this._amsHolders(printer ?? null).length;
  }

  reviewTray(line) {
    const t = this._t;
    const external = line.feed === "external";
    const frozen = line.charges.length === 1 ? line.charges[0].spool_id : "";
    const charges = line.charges.length
      ? line.charges.map((c) => ({ spool_id: c.spool_id, amount: c.amount_g.toFixed(1) }))
      : // A tray the review froze without a spool still gets a row: the amount is known,
        // the spool is not, and the user is the one who knows which it was (docs/06 §6.3).
        [{ spool_id: "", amount: "" }];

    return `
      <div class="rv-tray" data-feed="${external ? "external" : "ams"}"
        data-printer="${esc(line.printer)}" data-ams="${esc(line.ams)}"
        data-slot="${esc(line.slot)}" data-holder="${esc(line.holder ?? "")}"
        data-orig="${esc(line.estimated_g)}"
        data-frozen="${esc(frozen)}">
        <div class="rv-row">
          <span class="rv-slot">${
            external
              ? t(holderWord(line.holder, this._reviewHolderCount(line.printer)))
              : t("ams.slot", { slot: line.slot })
          }</span>
          <input class="rv-amt num" type="number" min="0" step="0.1"
            value="${esc(line.estimated_g.toFixed(1))}"> g
        </div>
        <div class="rv-charges">${this.reviewCharges(charges, frozen)}</div>
        <div class="rv-trayfoot">
          <button class="link" data-action="review-add">${t("review.addSpool")}</button>
          <span class="rv-left"></span>
        </div>
      </div>`;
  }

  /**
   * A tray's charge rows. One row is the collapsed form; two or more is the split.
   *
   * Rebuilt whole whenever a charge is added or removed, from values read back out of the
   * DOM, so the panel keeps one renderer for both densities — the alternative is markup
   * that is assembled in one place and patched in another, which is how the two drift.
   *
   * Choosing the spool goes through the layered picker, never a `<select>`. The dropdown
   * this row used to carry was judged not intuitive — a list of names with no colour and
   * no ring, on a phone at the printer — and the picker that shipped in v2.7.3 is the one
   * surface this panel has for choosing a spool: the mount dialog uses it, the reassign
   * form uses it, and a review row that looked different would be the third way to do
   * the same thing. What survives of the old control is its contract: a hidden `.rv-pick`
   * holds the chosen id, so `_trayCharges`, `_syncReviewCard` and the approval payload
   * read exactly what they always read. The button beside it is the field's face, and
   * `_pickSpool` patches that face in place when the choice is made.
   */
  reviewCharges(charges, frozen) {
    const t = this._t;
    const single = charges.length === 1;
    return charges
      .map((charge) => {
        // Named off the *unfiltered* list: a spool retired since the review opened is
        // still the spool this tray froze, and calling it unknown would hide the very
        // fact the user needs in order to understand the refusal that follows. The picker
        // itself filters the retired out (`_openSpoolPicker`, docs/14 §14.4.5).
        const spool = charge.spool_id
          ? this._spools.find((s) => s.id === charge.spool_id)
          : null;
        const named = single && charge.spool_id && charge.spool_id === frozen;
        // The face: the spool's own card once one is chosen, otherwise the invitation.
        // "Change" appears only when there is something to change from.
        const face = spool
          ? this.spoolChoiceBody(spool)
          : `<span class="sf-empty">${charge.spool_id ? t("review.unknownSpool") : t("review.chooseSpool")}</span>`;
        const who = named
          ? `<span class="rv-dot" style="background:${esc(spool?.colour ?? "transparent")}"></span>
             <span class="rv-spool">${spool ? esc(spool.name) : t("review.unknownSpool")}</span>`
          : `<span class="rv-warn">${charge.spool_id ? "" : "⚠"}</span>
             <span class="rv-pickline">${single ? t("review.whichSpool") : ""}
               <input type="hidden" class="rv-pick" value="${esc(charge.spool_id)}">
               <button type="button" class="mount-choice spool-field rv-choose" data-action="open-spool-picker">
                 <span class="sf-card">${face}</span>
                 <span class="sf-change">${spool ? t("act.change") : ""}</span>
               </button>
             </span>`;
        // The per-charge figure and its two buttons exist only in the split: with one
        // charge the tray's own figure is the charge's figure, by the invariant.
        const share = single
          ? ""
          : `<input class="rv-share num" type="number" min="0" step="0.1" value="${esc(charge.amount)}"> g
             <button class="link" data-action="review-rest"
               title="${t("review.loadRestTitle")}">${t("review.loadRest")}</button>
             <button class="rowact" data-action="review-drop"
               title="${t("review.dropChargeTitle")}">×</button>`;
        return `<div class="rv-charge${charge.spool_id ? "" : " unresolved"}">${who}${share}</div>`;
      })
      .join("");
  }

  /**
   * A tray's charges as the DOM currently holds them.
   *
   * The collapsed row carries no figure of its own, so it reports the tray's — which is
   * what the domain does with a single charge, and saying it here keeps the remainder, the
   * hint and the approval payload reading one shape rather than three.
   */
  _trayCharges(tray) {
    const rows = [...tray.querySelectorAll(".rv-charge")];
    const trayAmount = tray.querySelector(".rv-amt").value;
    return rows.map((row) => {
      const pick = row.querySelector(".rv-pick");
      const share = row.querySelector(".rv-share");
      return {
        spool_id: pick ? pick.value : tray.dataset.frozen,
        amount: share ? share.value : trayAmount,
      };
    });
  }

  /**
   * Why Approve is disabled, naming the positions (docs/06 §6.3).
   *
   * Built as markup here and as `textContent` in `_syncReviewCard`; the sentence is one
   * key either way, so the two can never say different things about the same card.
   */
  _approveHint(trays) {
    if (!trays.length) return "";
    return fill(this._t("review.blockedHint"), "slots", this._slotList(trays));
  }

  /**
   * The positions a hint is about, as prose: *slot 1 and slot 3*, or *slot 1 and the
   * external spool*. Each entry is `{ feed, slot }` — the external spool has no slot
   * number, so it is named by what it is rather than by a number it does not have.
   */
  _slotList(trays) {
    return trays.map((tray) => this._trayWord(tray)).join(this._t("act.and"));
  }

  /** One position, mid-sentence: *slot 3*, *the external spool*, *the second one*. */
  _trayWord(tray) {
    if (tray.feed !== "external") return this._t("review.slotWord", { slot: tray.slot });
    // Only the second holder earns a numeral, the rule every other surface follows: a
    // machine with one holder has no second position to be told apart from.
    return this._t(
      Number(tray.holder) === 2 ? "review.externalWord2" : "review.externalWord",
    );
  }

  /**
   * Re-derive the card's totals, remainders, hint and Approve state from its inputs, in
   * place — a render() per keystroke would steal the focus mid-number.
   *
   * The remainder is the whole of *load the rest*: a tray's charges must add up to what
   * that tray confirms (docs/02 §2.3), so what is left to charge is a subtraction, and
   * the button below merely performs it. Approve is disabled while any tray is short —
   * the button and the domain rule must never disagree about what is legal.
   */
  _syncReviewCard(card) {
    const t = this._t;
    let total = 0;
    let invalid = false;
    const unattributed = [];
    const unbalanced = [];
    for (const tray of card.querySelectorAll(".rv-tray")) {
      const leftEl = tray.querySelector(".rv-left");
      // Cleared first: a tray whose amount has just become unreadable has no remainder to
      // state, and leaving the last one standing would be a figure about nothing.
      leftEl.textContent = "";
      const amount = typedGrams(tray.querySelector(".rv-amt").value);
      if (amount === null) {
        invalid = true;
        continue;
      }
      total += amount;

      let attributed = 0;
      let missing = false;
      for (const charge of this._trayCharges(tray)) {
        const share = typedGrams(charge.amount);
        if (share === null) {
          invalid = true;
          continue;
        }
        attributed += share;
        if (share !== 0 && !charge.spool_id) missing = true;
      }
      // The position, by feed, slot and holder, so the hint can name each direct feed as
      // itself rather than calling both of a dual-nozzle machine's holders one thing.
      const which = {
        feed: tray.dataset.feed,
        slot: tray.dataset.slot,
        holder: tray.dataset.holder,
      };
      if (missing) unattributed.push(which);

      const left = round1(amount - attributed);
      if (left !== 0) unbalanced.push(which);
      leftEl.textContent =
        left > 0
          ? t("review.remaining", { grams: left.toFixed(1) })
          : left < 0
            ? t("review.overCharged", { grams: (-left).toFixed(1) })
            : "";
    }

    const totalEl = card.querySelector(".rv-total b");
    if (totalEl) totalEl.textContent = total.toFixed(1);

    const blocked = invalid || unattributed.length > 0 || unbalanced.length > 0;
    card.querySelector(".rv-approve").disabled = blocked;
    const hint = card.querySelector(".rv-hint");
    hint.hidden = !blocked;
    hint.textContent = unattributed.length
      ? this._approveHint(unattributed)
      : unbalanced.length
        ? fill(t("review.remainderHint"), "slots", this._slotList(unbalanced))
        : t("review.invalidAmounts");
  }

  /**
   * Give a tray a second spool (docs/06 §6.3).
   *
   * The charge rows are rebuilt from what the DOM currently holds rather than re-rendered
   * from the wire, so everything already typed into this tray survives — and the new row
   * starts empty, because a row seeded with the remainder would leave **[ Load the rest ]**
   * with nothing to say the first time it is offered.
   */
  _addCharge(tray) {
    const charges = this._trayCharges(tray);
    charges.push({ spool_id: "", amount: "" });
    this._renderCharges(tray, charges);
  }

  /** Take a spool off a tray. Only ever offered on a split, so one row always remains. */
  _dropCharge(button) {
    const tray = button.closest(".rv-tray");
    const rows = [...tray.querySelectorAll(".rv-charge")];
    const charges = this._trayCharges(tray);
    charges.splice(rows.indexOf(button.closest(".rv-charge")), 1);
    this._renderCharges(tray, charges);
  }

  /**
   * Charge this spool everything the tray has not attributed yet — the subtraction the
   * invariant makes obvious, so the user does not do it on a phone at the printer.
   *
   * Clamped at zero: an over-charged tray already says so beside the button, and a
   * negative charge is refused by the domain rather than quietly turned into a credit.
   */
  _loadRest(button) {
    const tray = button.closest(".rv-tray");
    const row = button.closest(".rv-charge");
    const index = [...tray.querySelectorAll(".rv-charge")].indexOf(row);
    const amount = typedGrams(tray.querySelector(".rv-amt").value);
    if (amount === null) return;
    const others = this._trayCharges(tray).reduce(
      (sum, charge, i) => (i === index ? sum : sum + (typedGrams(charge.amount) ?? 0)),
      0,
    );
    row.querySelector(".rv-share").value = Math.max(0, round1(amount - others)).toFixed(1);
    this._syncReviewCard(tray.closest(".rv-card"));
  }

  _renderCharges(tray, charges) {
    tray.querySelector(".rv-charges").innerHTML = this.reviewCharges(charges, tray.dataset.frozen);
    this._syncReviewCard(tray.closest(".rv-card"));
  }

  /**
   * Split the weighed total across the **trays** in the same proportion as the frozen
   * estimates (docs/06 §6.3) — a click, not arithmetic. With one tray it replaces the
   * value outright, which is the same rule with one term. When every estimate is zero —
   * the no-data card — the spec names no proportion, so the split is even: the honest
   * default when nothing distinguishes the slots.
   *
   * The sibling of **[ Load the rest ]**, and deliberately the same idea: the panel does
   * the arithmetic the user would otherwise do at the printer. This one divides one
   * measured total across trays by proportion; that one divides one tray's amount across
   * its spools by subtraction. Neither invents a figure — both only redistribute one the
   * user supplied.
   *
   * Rounding: cumulative, one decimal like every movement — row i gets
   * round1(cumulative share through i) − round1(cumulative share through i−1). The
   * rounded cumulative totals telescope, so the rows sum to round1(total) BY
   * CONSTRUCTION, and each row stays within 0.1 of its fair share. Rounding each row
   * independently cannot give that guarantee: every row can round up at once, and the
   * rows then claim more than the scale read (shares 33.06 + 33.06 + 33.06 + 0.02 of
   * 99.2 would become 33.1 + 33.1 + 33.1 + 0.0 = 99.3). Cumulative totals only grow
   * when shares are non-negative, so no clamp is needed either.
   */
  _distribute(card) {
    const weighed = card.querySelector(".rv-weighed");
    const total = weighed.value === "" ? NaN : Number(weighed.value);
    if (!Number.isFinite(total) || total < 0) return;

    const trays = [...card.querySelectorAll(".rv-tray")];
    const basis = trays.map((tray) => Number(tray.dataset.orig) || 0);
    const basisTotal = basis.reduce((a, b) => a + b, 0);
    const shares =
      basisTotal > 0 ? basis.map((b) => b / basisTotal) : basis.map(() => 1 / trays.length);

    let cumShare = 0;
    let cumRounded = 0;
    trays.forEach((tray, i) => {
      cumShare += shares[i];
      // The last tray closes on exactly 1, so float drift in the running share can never
      // leave the sum a tenth short of — or past — what the scale read.
      const next = round1(total * (i === trays.length - 1 ? 1 : cumShare));
      tray.querySelector(".rv-amt").value = round1(next - cumRounded).toFixed(1);
      cumRounded = next;
    });
    this._syncReviewCard(card);
  }

  /**
   * Approve with only the overrides the user actually changed: `amounts` carries a tray
   * only when its value differs from what the card DISPLAYED — the estimate seeded into
   * the input at one decimal — and `assign` only the pickers with a choice. The
   * comparison must round `data-orig` the same way the seed did (`toFixed(1)`):
   * `data-orig` keeps the full-precision estimate for Distribute's basis, and comparing
   * the one-decimal input against it would flag every untouched tray as edited whenever
   * the estimate carries sub-0.1 g precision, silently replacing the frozen estimate
   * with its rounded display value server-side. Untouched trays are omitted, so the
   * backend charges the full-precision frozen estimate. An input cleared to empty reads
   * as 0, sent iff 0 differs from the displayed seed — clearing a non-zero tray is a
   * deliberate "this slot consumed nothing". JSON object keys are strings; the schema's
   * Coerce(int) reads them as slots.
   *
   * A **split** tray is the one exception to that omission, and it has to be: its charges
   * are the one-decimal figures the user typed, and the backend refuses an approval whose
   * charges do not add up to the tray's amount. Sending the split without the amount would
   * measure those tenths against a frozen estimate carrying more precision and fail on a
   * hundredth of a gram the user cannot see, let alone act on. So a split tray confirms
   * exactly what the card showed — which is also what the user decided, row by row.
   *
   * `assign` rather than a one-entry `charges` for the collapsed picker, deliberately: the
   * shorthand gives the tray whole to the chosen spool at whatever precision the backend
   * already holds, and a charge list would round it on the way past.
   */
  _approveReview(card, reviewId) {
    const payload = { review_id: reviewId };
    // Lists of per-tray entries, not objects keyed by slot: a tray takes several parts to
    // name and a JSON key holds one. Each entry repeats the position the card rendered,
    // read straight back off the element the review's own line built — `trayRef` puts
    // `feed` on every entry, and an external line goes back with `ams` and `slot` null.
    const amounts = [];
    const assign = [];
    const charges = [];
    for (const tray of card.querySelectorAll(".rv-tray")) {
      const ref = trayRef(tray);
      const value = typedGrams(tray.querySelector(".rv-amt").value);
      const seeded = Number(Number(tray.dataset.orig).toFixed(1));
      const rows = this._trayCharges(tray);
      const split = rows.length > 1;
      if (value !== null && (value !== seeded || split)) amounts.push({ ...ref, amount_g: value });

      if (split) {
        charges.push({
          ...ref,
          charges: rows
            .filter((charge) => charge.spool_id)
            .map((charge) => ({
              spool_id: charge.spool_id,
              amount_g: typedGrams(charge.amount) ?? 0,
            })),
        });
      } else if (rows[0].spool_id && rows[0].spool_id !== tray.dataset.frozen) {
        assign.push({ ...ref, spool_id: rows[0].spool_id });
      }
    }
    if (amounts.length) payload.amounts = amounts;
    if (assign.length) payload.assign = assign;
    if (charges.length) payload.charges = charges;
    const note = card.querySelector(".rv-note").value.trim();
    if (note) payload.note = note;
    this.guarded(() => this.call("reviews/approve", payload));
  }
}
