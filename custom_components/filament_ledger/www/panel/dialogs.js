// Every dialog: the forms, the spool picker and their shared actions.
//
// Installed on the panel by `mixIn` (./mixin.js, ADR-0010); `this` is the panel.

import { esc } from "../i18n.js";
import { fill, grams, signed, typedGrams } from "./format.js";
import { spoolRing } from "./spool-ring.js";

const MATERIALS = ["PLA", "PETG", "ABS", "ASA", "TPU", "PC", "PA", "PVA", "SUPPORT", "OTHER"];

export class DialogViews {
  // -- dialogs -----------------------------------------------------------------------

  dialog() {
    // Thunks, not strings: only the requested form may run. Building every body
    // eagerly meant opening one dialog executed all of them, and weighForm reads
    // the loaded spool detail — null everywhere outside the detail view, so the
    // whole render threw before any dialog could appear.
    const bodies = {
      "new-spool": () => this.newSpoolForm(),
      weigh: () => this.weighForm(),
      adjust: () => this.adjustForm(),
      finish: () => this.finishForm(),
      discard: () => this.discardForm(),
      mount: () => this.mountForm(),
      "dismiss-review": () => this.dismissReviewForm(),
      "edit-spool": () => this.editSpoolForm(),
      reassign: () => this.reassignForm(),
      "void-movement": () => this.voidMovementForm(),
      "restore-movement": () => this.restoreMovementForm(),
      "spool-actions": () => this.spoolActionsBody(),
      "spool-intent": () => this.spoolIntentBody(),
    };
    const body = bodies[this._dialog.kind];
    return `
      <div class="scrim" data-action="close-dialog">
        <div class="modal">
          ${body ? body() : ""}
        </div>
      </div>`;
  }

  newSpoolForm() {
    const t = this._t;
    const defaults = this._stock?.defaults || { opening_weight_g: 1000, core_weight_g: 250 };
    // Pre-fill from a sync outcome when one opened this dialog (docs/06 §6.4): material,
    // colour, name, tag and — when the tag carried one — the reel's own weight are what
    // the tray reported.
    const hint = this._dialog?.prefill ?? null;
    // The tagged reel's weight wins over the configured default, so registering by hand
    // and letting auto-registration do it produce the same number for the same reel.
    // Still only a pre-fill: `tray_weight` is what the reel held *new*, and somebody
    // registering a half-used spool must be able to type the figure they measured over
    // it. Absent (the tag said nothing) falls back to the default, as it always did.
    const opening = hint?.weight_hint_g ?? defaults.opening_weight_g;
    // A hint outside the list (say "PLA-CF") must not fall through to the browser's
    // default first option — PLA is specific, and wrong, silently. OTHER plus the raw
    // hint in the name field drops nothing the printer said (TrayReading's guarantee).
    const hinted = hint?.material_hint ?? null;
    const material = hinted ? (MATERIALS.includes(hinted) ? hinted : "OTHER") : null;
    const materialOther = hinted && !MATERIALS.includes(hinted) ? hinted : "";
    return `
      <form data-form="new-spool">
        <h3>${t("dlg.registerTitle")}</h3>
        <label>${t("dlg.material")}
          <select name="material">${MATERIALS.map((m) => `<option ${m === material ? "selected" : ""}>${m}</option>`).join("")}</select>
        </label>
        <label>${t("dlg.materialOther")}<input name="material_other" value="${esc(materialOther)}" placeholder="${t("dlg.materialOtherPlaceholder")}"></label>
        <label>${t("dlg.colour")}<input name="colour" value="${esc(hint?.colour_hint || "#000000")}" type="color"></label>
        <label>${t("dlg.openingWeight")}
          <input name="opening_weight_g" type="number" step="0.1" min="1" value="${esc(opening)}" required>
        </label>
        <label>${t("dlg.coreWeight")}
          <input name="core_weight_g" type="number" step="0.1" min="0" value="${defaults.core_weight_g}" required>
          <small>${t("dlg.coreWeightHelp")}</small>
        </label>
        <label>${t("dlg.vendor")}<input name="vendor" placeholder="${t("dlg.vendorPlaceholder")}"></label>
        <label>${t("dlg.label")}<input name="label" value="${esc(hint?.name_hint || "")}" placeholder="${t("dlg.labelPlaceholder")}"></label>
        ${
          hint?.tag_uid
            ? `<input type="hidden" name="tag_uid" value="${esc(hint.tag_uid)}">
        <p class="muted small">${t("dlg.tagFromSlot", { tag: hint.tag_uid, slot: hint.slot })}</p>`
            : ""
        }
        ${this.formActions(t("dlg.register"))}
      </form>`;
  }

  /**
   * Edit details (docs/06 §6.5, docs/14 §14.2). Mirrors the register form's fields and
   * pre-fills every one of them from the loaded detail.
   *
   * **The opening weight is absent, and so is any balance field.** That is not an omission
   * but the point: no endpoint sets a balance, and this dialog does not become the first
   * one. What it offers instead is the correction section below, which writes a movement.
   */
  editSpoolForm() {
    const t = this._t;
    const spool = this._detail;
    // `material` is the display name, which for OTHER *is* the free-text name.
    const other = spool.material_kind === "OTHER" ? spool.material : "";
    return `
      <form data-form="edit-spool" data-core="${esc(spool.core_weight_g)}">
        <h3>${t("dlg.editTitle")}</h3>
        <label>${t("dlg.material")}
          <select name="material">${MATERIALS.map(
            (m) => `<option ${m === spool.material_kind ? "selected" : ""}>${m}</option>`,
          ).join("")}</select>
        </label>
        <label>${t("dlg.materialOther")}<input name="material_other" value="${esc(other)}" placeholder="${t("dlg.materialOtherPlaceholder")}"></label>
        <label>${t("dlg.colour")}<input name="colour" value="${esc(spool.colour)}" type="color"></label>
        <label>${t("dlg.vendor")}<input name="vendor" value="${esc(spool.vendor ?? "")}" placeholder="${t("dlg.vendorPlaceholder")}"></label>
        <label>${t("dlg.label")}<input name="label" value="${esc(spool.label ?? "")}" placeholder="${t("dlg.labelPlaceholder")}"></label>
        <label>${t("dlg.coreWeight")}
          <input name="core_weight_g" type="number" step="0.1" min="0" value="${esc(spool.core_weight_g)}" required>
        </label>
        <p class="muted small">${t("dlg.editClearNote")}</p>
        ${this.editTagField(spool)}
        ${this.editCorrectionSection(spool)}
        ${this.formActions(t("act.save"))}
      </form>`;
  }

  /**
   * The owner's tag rule, rendered: *a tag the printer attached is the printer's
   * statement; a tag I typed is mine to change* (docs/14 §14.2).
   *
   * The DETECTED branch renders no input at all, which is also how the command hears
   * "leave it alone" — absent, not null.
   */
  editTagField(spool) {
    const t = this._t;
    if (spool.tag_source === "DETECTED") {
      return `
        <div class="ed-tag">
          <div class="k">${t("dlg.tag")}</div>
          <div class="ed-tagval">${esc(spool.tag_uid)}</div>
          <small>${t("dlg.tagDetected")}</small>
        </div>`;
    }
    return `
      <label>${t("dlg.tag")}
        <span class="ed-tagrow">
          <input class="ed-taginput" name="tag_uid" value="${esc(spool.tag_uid ?? "")}" placeholder="${t("dlg.tagPlaceholder")}">
          <button type="button" data-action="clear-tag">${t("dlg.tagClear")}</button>
        </span>
        <small>${spool.tag_uid ? t("dlg.tagYours") : t("dlg.tagNone")}</small>
      </label>
      <label class="row"><input name="confirm_duplicate_tag" type="checkbox">
        <span class="small">${t("dlg.tagDuplicate")}</span>
      </label>`;
  }

  /**
   * Weight correction — the only way this dialog can change a number, and it does it by
   * writing a movement, so history explains it (docs/14 §14.2).
   */
  editCorrectionSection(spool) {
    const t = this._t;
    return `
      <div class="ed-corr">
        <div class="k">${t("dlg.correctHeading")}</div>
        <p class="muted small">${t("dlg.correctBody")}</p>
        <label>${t("dlg.setRemaining")}
          <input class="ed-set" name="set_g" type="number" step="0.1" min="0"
            placeholder="${esc(spool.balance_exact_g.toFixed(1))}">
          <small>${t("dlg.setRemainingHelp")}</small>
        </label>
        <label>${t("dlg.addRemove")}
          <input class="ed-delta" name="delta_g" type="number" step="0.1" placeholder="0.0">
          <small>${t("dlg.addRemoveHelp")}</small>
        </label>
        <label>${t("dlg.adjustReason")}
          <input class="ed-reason" name="delta_reason" placeholder="${t("act.why")}" disabled>
          <small>${t("dlg.adjustReasonHelp")}</small>
        </label>
        <p class="ed-hint muted small">${t("dlg.correctNothing")}</p>
      </div>`;
  }

  weighForm() {
    const t = this._t;
    return `
      <form data-form="weigh">
        <h3>${t("dlg.weighTitle")}</h3>
        <p class="muted">${t("dlg.weighBody")}</p>
        <label>${t("dlg.measured")}<input name="measured_g" type="number" step="0.1" min="0" required autofocus></label>
        <label class="row"><input name="includes_core" type="checkbox" checked>
          ${t("dlg.includesCore", { core: this._detail.core_weight_g })}</label>
        <label>${t("act.note")}<input name="note" placeholder="${t("act.optional")}"></label>
        <p class="muted small">${t("dlg.weighFoot")}</p>
        ${this.formActions(t("act.record"))}
      </form>`;
  }

  adjustForm() {
    const t = this._t;
    return `
      <form data-form="adjust">
        <h3>${t("dlg.adjustTitle")}</h3>
        <label>${t("dlg.amount")}<input name="amount_g" type="number" step="0.1" required autofocus>
          <small>${t("dlg.amountHelp")}</small>
        </label>
        <label>${t("act.reason")}<input name="reason" required placeholder="${t("act.why")}"></label>
        <p class="muted small">${t("dlg.adjustFoot")}</p>
        ${this.formActions(t("act.record"))}
      </form>`;
  }

  /**
   * Move a charge to the spool that actually fed the print (docs/14 §14.3).
   *
   * **The modal states what will happen to the grams before anything is sent**, and the
   * figures it prints are the ones the ledger will hold: both legs are the amount in the
   * field, to one decimal, which is the precision a single movement is known to.
   *
   * The field starts at the whole charge, which is what a reassignment has always moved.
   * Typing less is the review card's split reached after the fact — a spool that emptied
   * mid-print and was replaced in the same tray — and the sentence follows the field as
   * it is typed, because a promise about the grams that does not track what is about to
   * be sent is worse than no promise.
   */
  reassignForm() {
    const t = this._t;
    const subject = this._movementSubject(this._dialog.movement_id);
    if (!subject) return this.staleSubject();
    const moved = Math.abs(subject.amount_g).toFixed(1);
    // The same filter the review card's picker applies: a spool that is out of inventory
    // cannot be charged, and the backend refuses it (docs/14 §14.3).
    const candidates = this._spools.filter(
      (s) => s.id !== subject.spool_id && s.state !== "DISCARDED" && s.state !== "DELETED",
    );
    if (!candidates.length) {
      return `<h3>${t("dlg.reassignTitle")}</h3>
        <p class="muted">${t("dlg.reassignNone")}</p>
        ${this.formActions(null)}`;
    }
    return `
      <form data-form="reassign" data-whole="${esc(moved)}" data-spool="${esc(subject.spool_name)}"
        data-exclude="${esc(subject.spool_id)}">
        <h3>${t("dlg.reassignTitle")}</h3>
        <p class="cx-says rs-says">${t("dlg.reassignSays", {
          grams: moved,
          spool: subject.spool_name,
        })}</p>
        <input type="hidden" name="to_spool_id" value="${esc(candidates[0].id)}">
        <p class="muted small">${t("dlg.reassignTo")}</p>
        <button type="button" class="mount-choice spool-field" data-action="open-spool-picker">
          <span class="sf-card">${this.spoolChoiceBody(candidates[0])}</span>
          <span class="sf-change">${t("act.change")}</span>
        </button>
        <label>${t("dlg.reassignAmount")}
          <input class="rs-amount" name="amount_g" type="number" min="0.1" step="0.1"
            max="${esc(moved)}" value="${esc(moved)}">
          <small>${t("dlg.reassignAmountHelp", { grams: moved })}</small>
        </label>
        <label>${t("act.note")}<input name="note" placeholder="${t("act.optional")}"></label>
        <p class="muted small">${t("dlg.reassignFoot")}</p>
        ${this.formActions(t("dlg.reassign"))}
      </form>`;
  }

  /**
   * Keep the reassign modal's promise equal to what the button will send.
   *
   * Patched in place, like the review card and the edit dialog: a render() per keystroke
   * would rebuild the modal and take the focus out of the number being typed. An amount
   * outside the charge leaves the sentence on the last figure that made sense rather than
   * printing a promise the backend is about to refuse.
   */
  _syncReassignForm(form) {
    const whole = Number(form.dataset.whole);
    const typed = typedGrams(form.querySelector(".rs-amount").value);
    if (typed === null || typed === 0 || typed > whole) return;
    form.querySelector(".rs-says").innerHTML = this._t("dlg.reassignSays", {
      grams: typed.toFixed(1),
      spool: form.dataset.spool,
    });
  }

  /**
   * The X on a history row (docs/14 §14.4.1).
   *
   * Three branches, and the retired-spool ones are not a refusal dressed as a choice:
   * grams only return to a spool that is in inventory, so the modal says which route back
   * exists and offers the honest alternative — delete the entry without getting anything
   * back, and say why.
   */
  voidMovementForm() {
    const t = this._t;
    const subject = this._movementSubject(this._dialog.movement_id);
    if (!subject) return this.staleSubject();
    const moved = Math.abs(subject.amount_g).toFixed(1);
    const figures = { grams: moved, spool: subject.spool_name };
    // The owner's sentence, and its honest inverse. Voiding an entry that *added*
    // filament removes those grams again — saying "returns" there would be a lie in the
    // one place the panel is promising exactly what will happen.
    const promise =
      subject.amount_g < 0 ? t("dlg.voidReturns", figures) : t("dlg.voidRemoves", figures);

    if (subject.retirement) return this.voidRetiredForm(subject, figures);
    return `
      <form data-form="void-movement">
        <h3>${t("dlg.voidTitle")}</h3>
        <p class="cx-says">${promise}</p>
        <label>${t("act.reason")}<input name="reason" placeholder="${t("act.optional")}"></label>
        <p class="muted small">${t("dlg.voidFoot")}</p>
        ${this.formActions(t("dlg.voidConfirm"))}
      </form>`;
  }

  voidRetiredForm(subject, figures) {
    const t = this._t;
    const deleted = subject.retirement === "DELETED";
    const explain = deleted
      ? t("dlg.voidDeletedSpool", figures)
      : t("dlg.voidDiscardedSpool", figures);
    const route = deleted
      ? `<button class="primary" type="button" data-action="void-restore-spool"
          data-id="${esc(subject.spool_id)}">${t("dlg.voidRestoreFirst")}</button>`
      : `<p class="muted small">${t("dlg.voidDiscardRoute")}</p>`;
    return `
      <form data-form="void-movement">
        <h3>${t("dlg.voidTitle")}</h3>
        <p class="cx-says">${explain}</p>
        ${route}
        <input type="hidden" name="without_restitution" value="1">
        <label>${t("dlg.voidWhyNothing")}<input name="reason" required placeholder="${t("dlg.voidWhyPlaceholder")}"></label>
        <p class="muted small">${t("dlg.voidNoRestitutionFoot")}</p>
        ${this.formActions(t("dlg.voidNoRestitutionConfirm"))}
      </form>`;
  }

  restoreMovementForm() {
    const t = this._t;
    const entry = (this._trash?.movements ?? []).find(
      (m) => m.movement_id === this._dialog.movement_id,
    );
    if (!entry) return this.staleSubject();
    const figures = { grams: Math.abs(entry.amount_g).toFixed(1), spool: entry.spool_name };
    const promise =
      entry.amount_g < 0 ? t("dlg.restoreDeduct", figures) : t("dlg.restoreAdd", figures);
    return `
      <form data-form="restore-movement">
        <h3>${t("dlg.restoreTitle")}</h3>
        <p class="cx-says">${promise}</p>
        <p class="muted small">${t("dlg.restoreFoot")}</p>
        ${this.formActions(t("act.restore"))}
      </form>`;
  }

  /**
   * The spool a dialog is about, from whichever surface opened it.
   *
   * Resolved on every render rather than captured when the dialog opened, for the same
   * reason `_movementSubject` is: a refresh landing underneath must change what the modal
   * says, and a modal whose subject went away has to admit it rather than quote a figure
   * that is no longer true. The overview is asked first because it is what an inventory
   * card and an AMS tray were drawn from; the Finished list answers for a discarded
   * spool's card, which the overview omits; the loaded detail answers for the one spool
   * neither carries — a deleted one, reached from the Trash.
   */
  _dialogSpool() {
    const id = this._dialog?.spool_id;
    if (id === undefined) return null;
    return (
      this._spools.find((s) => s.id === id) ??
      (this._finished ?? []).find((s) => s.id === id) ??
      (this._detail?.id === id ? this._detail : null)
    );
  }

  /**
   * The spool action rail as a sheet — the collapsed rendering's body (docs/16 §16.10).
   *
   * It carries the two actions that need nothing but the spool, which is why they are the
   * two an inventory card and an AMS tray can offer at all: *this reel is empty* and *this
   * spool is gone*. Weigh, Adjust and Edit are absent on purpose — each of them changes a
   * number the movement history has to justify, so each belongs under that history in the
   * detail view (docs/06 §6.1's rule, applied to a spool rather than to a view).
   *
   * Every row states its consequence in a line, exactly as the retirement modal does, so
   * neither is picked by accident.
   */
  spoolActionsBody() {
    const t = this._t;
    const spool = this._dialogSpool();
    if (!spool) return this.staleSubject();
    const id = esc(spool.id);
    // The state word is already a table result, so it is spliced through `fill` rather
    // than passed as a parameter — the rule every other composed sentence here follows.
    const summary = fill(
      t("dlg.actionsBalance", { grams: spool.balance_g }),
      "state",
      this.stateLabel(spool.state),
    );
    // The heading is the spool itself, escaped as the data it is: the sheet's subject is
    // the object in the user's hand, and the rows below already say what can be done to
    // it. A key whose whole content is a placeholder would translate nothing.
    return `
      <h3>${esc(spool.name)}</h3>
      <p class="muted">${summary}</p>
      ${
        this._finishable(spool)
          ? `<div class="sp-act">
               <button data-action="spool-finish" data-id="${id}">${t("detail.finish")}</button>
               <small>${t("detail.finishHelp")}</small>
             </div>`
          : ""
      }
      <div class="sp-act">
        <button class="danger" data-action="spool-intent" data-id="${id}">${t("detail.remove")}</button>
        <small>${t("detail.removeHelp")}</small>
      </div>
      ${this.formActions(null)}`;
  }

  /**
   * Mark a spool as finished — a reconciliation to zero, and it says so (docs/06 §6.5).
   *
   * **The drift is stated in grams before anything is sent.** The ledger still believes a
   * balance the reel does not have, and the difference is exactly what this writes: a
   * number that can be hundreds of grams, produced by every estimate since the last
   * weighing. Recording a figure that large without showing it first is the one thing this
   * ledger exists not to do — and it is the same promise the reassign and void modals make.
   *
   * The figures are the exact balance rather than the rounded one, to the decimal a single
   * movement is known to (docs/06 §6.8): the whole-gram display belongs to a balance, and
   * this sentence is about the movement.
   */
  finishForm() {
    const t = this._t;
    const spool = this._dialogSpool();
    if (!spool) return this.staleSubject();
    const remaining = Number(spool.balance_exact_g ?? 0);
    return `
      <form data-form="finish">
        <h3>${t("dlg.finishTitle", { name: spool.name })}</h3>
        <p class="cx-says">${t("dlg.finishSays", {
          grams: remaining.toFixed(1),
          delta: signed(-remaining),
        })}</p>
        <p class="muted small">${t("dlg.finishFoot")}</p>
        ${this.formActions(t("dlg.finishConfirm"))}
      </form>`;
  }

  /**
   * Retiring a spool asks what actually happened (docs/14 §14.4.3). Two answers, two
   * different facts about the world, and one line each so neither is picked by accident.
   */
  spoolIntentBody() {
    const t = this._t;
    const spool = this._dialogSpool();
    if (!spool) return this.staleSubject();
    const id = esc(spool.id);
    return `
      <h3>${t("dlg.intentTitle", { name: spool.name })}</h3>
      <p class="muted">${t("dlg.intentAsk")}</p>
      <div class="sp-act">
        <button data-action="intent-discard" data-id="${id}">${t("dlg.intentThrewAway")}</button>
        <small>${t("dlg.intentThrewAwayHelp", { grams: spool.balance_g })}</small>
      </div>
      <div class="sp-act">
        <button data-action="intent-delete" data-id="${id}">${t("dlg.intentMistake")}</button>
        <small>${t("dlg.intentMistakeHelp")}</small>
      </div>
      ${this.formActions(null)}`;
  }

  /**
   * A modal whose subject went away underneath it — a refresh landed while it was open.
   * Says so instead of rendering a blank box or, worse, figures from a stale row.
   */
  staleSubject() {
    return `<h3>${this._t("dlg.staleTitle")}</h3>
      <p class="muted">${this._t("dlg.staleBody")}</p>
      ${this.formActions(null)}`;
  }

  discardForm() {
    const t = this._t;
    const whole = this._dialog?.mode === "whole_spool";
    return `
      <form data-form="discard">
        <h3>${t("dlg.discardTitle")}</h3>
        <label>${t("dlg.discardWhat")}<select name="mode">
          <option value="partial" ${whole ? "" : "selected"}>${t("dlg.discardPartial")}</option>
          <option value="whole_spool" ${whole ? "selected" : ""}>${t("dlg.discardWhole")}</option>
        </select></label>
        <label>${t("dlg.discardAmount")}<input name="amount_g" type="number" step="0.1" min="0"></label>
        <label>${t("act.reason")}<input name="reason" required placeholder="${t("dlg.discardReasonPlaceholder")}"></label>
        ${this.formActions(t("act.discard"))}
      </form>`;
  }

  /**
   * The mount dialog, for a tray or for the external spool — the title is the only line
   * that knows which.
   *
   * A spool already mounted anywhere is not offered, whether it sits in a tray or on the
   * external holder: the empty-state sentence promises *unmount one first*, and a list
   * that quietly let a mounted reel be moved would make that sentence a lie.
   */
  mountForm() {
    const t = this._t;
    const { slot, external } = this._dialog;
    const available = this._spools.filter(
      (s) => s.location.kind !== "AMS_SLOT" && s.location.kind !== "EXTERNAL_SPOOL",
    );
    const title = external ? t("dlg.mountExternalTitle") : t("dlg.mountTitle", { slot });
    if (!available.length) {
      return `<h3>${title}</h3>
        <p class="muted">${t("dlg.mountNone")}</p>
        ${this.formActions(null)}`;
    }
    return `
      <h3>${title}</h3>
      ${this.spoolPickerSections(available, "mount-pick")}
      ${this.formActions(null)}`;
  }

  /** The card's inside — ring, name, material, balance — shared by every spool choice. */
  spoolChoiceBody(spool) {
    return `<span class="mc-art">
        ${spoolRing("slot", spool.percentage, spool.colour)}
        <span class="ring-mid"><span class="ring-hub" style="background:${esc(spool.colour)}"></span></span>
      </span>
      <span class="mc-body">
        <span class="mc-name">${esc(spool.name)}</span>
        <span class="mc-sub">${esc(spool.material)}${spool.vendor ? ` · ${esc(spool.vendor)}` : ""}</span>
        <span class="mc-grams">${spool.balance_g}<small> g</small> · ${spool.percentage}%</span>
      </span>`;
  }

  /**
   * The spool choices, sectioned: what can feed a print leads, and what the ledger says
   * is spent follows under its own heading, dimmed. Spent spools stay choosable because
   * both flows have a legitimate claim on them — the reel that emptied mid-print is
   * exactly the one a reassignment names, and a leftover the scale will correct can be
   * mounted — but choosing one must read as deliberate, never as an accident of
   * sorting. One grid, no scroll of its own: the modal already scrolls, and a second
   * scrollbar inside it is two fights over one wheel.
   */
  spoolPickerSections(candidates, action) {
    const t = this._t;
    const card = (s, spent) =>
      `<button type="button" class="mount-choice ${spent ? "spent" : ""}" data-action="${action}"
          data-id="${esc(s.id)}">${this.spoolChoiceBody(s)}</button>`;
    const usable = candidates.filter((s) => s.state !== "DEPLETED");
    const spent = candidates.filter((s) => s.state === "DEPLETED");
    return `
      ${
        usable.length
          ? `<p class="pick-sec">${t("picker.inventory")}</p>
        <div class="mount-grid">${usable.map((s) => card(s, false)).join("")}</div>`
          : ""
      }
      ${
        spent.length
          ? `<p class="pick-sec">${t("picker.spent")}</p>
        <div class="mount-grid">${spent.map((s) => card(s, true)).join("")}</div>`
          : ""
      }`;
  }

  /**
   * The layered spool picker, over whichever surface asked for it, patched in place.
   *
   * One picker, two hosts. `scope` is either the reassign `form` or one `.rv-charge` row
   * of a review tray — the two places this panel asks *which spool?* mid-edit — and the
   * rule is that both ask with this and nothing else: the `<select>` the review row used
   * to carry was judged not intuitive, and a spool is chosen by its colour and its ring
   * (06 §6.8), which a dropdown cannot draw. The host decides only the title and where
   * the answer is written (`_pickSpool`).
   *
   * Deliberately NOT `this._dialog` and NOT `render()`: a repaint rebuilds the dialog's
   * markup wholesale, and the grams being typed two fields down would not survive it —
   * the same reason `_syncReassignForm` patches instead of rendering, and the same reason
   * a review card is never re-rendered from a handler. The overlay is appended beside the
   * view, the root's delegated listener sees its buttons like any other node's, and
   * picking writes the hidden input and the field's face directly.
   *
   * Retired spools stay out, by either host — charging one is refused by the domain
   * (docs/14 §14.4.5) — and a host may name one more to leave out (`data-exclude`: the
   * reassign form's own spool, which a charge cannot be moved onto).
   */
  _openSpoolPicker(scope) {
    if (!scope) return;
    const t = this._t;
    this._pickerScope = scope;
    const exclude = scope.dataset.exclude;
    const candidates = this._spools.filter(
      (s) => s.id !== exclude && s.state !== "DISCARDED" && s.state !== "DELETED",
    );
    const title = scope.matches("form") ? t("dlg.reassignTo") : t("picker.reviewTitle");
    const layer = document.createElement("div");
    layer.className = "scrim picker-layer";
    layer.dataset.action = "close-picker";
    layer.innerHTML = `<div class="modal picker-modal">
      <h3>${title}</h3>
      ${this.spoolPickerSections(candidates, "picker-pick")}
      <div class="actions"><button type="button" data-action="close-picker">${t("act.cancel")}</button></div>
    </div>`;
    this._root.appendChild(layer);
  }

  _closeSpoolPicker() {
    this._root.querySelector(".picker-layer")?.remove();
    this._pickerScope = null;
  }

  /**
   * Write the choice back into the host that asked, and repaint only its face.
   *
   * The reassign form keeps the id in `to_spool_id`; a review row keeps it in `.rv-pick`,
   * the same hidden field the old dropdown's value lived in, so everything that reads a
   * tray's charges is unchanged. A review row then also drops its *unresolved* mark and
   * re-derives its card — the Approve button and the hint are about exactly this.
   */
  _pickSpool(id) {
    const scope = this._pickerScope;
    const chosen = this._spools.find((s) => s.id === id);
    if (scope && chosen) {
      const isForm = scope.matches("form");
      const input = scope.querySelector(isForm ? "input[name=to_spool_id]" : "input.rv-pick");
      if (input) input.value = id;
      const field = scope.querySelector(".spool-field");
      field.querySelector(".sf-card").innerHTML = this.spoolChoiceBody(chosen);
      field.querySelector(".sf-change").textContent = this._t("act.change");
      if (!isForm) {
        scope.classList.remove("unresolved");
        const warn = scope.querySelector(".rv-warn");
        if (warn) warn.textContent = "";
        this._syncReviewCard(scope.closest(".rv-card"));
      }
    }
    this._closeSpoolPicker();
  }

  dismissReviewForm() {
    const t = this._t;
    return `
      <form data-form="dismiss-review">
        <h3>${t("dlg.dismissTitle")}</h3>
        <p class="muted">${esc(this._dialog.review?.job_display_name ?? this._dialog.review?.job_name ?? "")}</p>
        <label>${t("act.reason")}<input name="note" placeholder="${t("act.optional")}"></label>
        <p class="muted small">${t("dlg.dismissFoot")}</p>
        ${this.formActions(t("act.dismiss"))}
      </form>`;
  }

  formActions(confirmLabel) {
    return `<div class="actions">
      <button type="button" data-action="close-dialog">${this._t("act.cancel")}</button>
      ${confirmLabel ? `<button type="submit" class="primary">${confirmLabel}</button>` : ""}
    </div>`;
  }
}
