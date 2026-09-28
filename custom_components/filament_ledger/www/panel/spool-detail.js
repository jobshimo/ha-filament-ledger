// One spool in full: its figures, its location and its history.
//
// Installed on the panel by `mixIn` (./mixin.js, ADR-0010); `this` is the panel.

import { esc } from "../i18n.js";
import { signed } from "./format.js";
import { spoolRing } from "./spool-ring.js";

export class SpoolDetailViews {
  // -- spool detail ------------------------------------------------------------------

  detailView() {
    const t = this._t;
    const spool = this._detail;
    const deleted = spool.state === "DELETED";
    const rows = spool.history
      .map(
        (line) => `
        <tr class="${line.voided ? "voided" : ""}">
          <td class="when">${this.when(line.occurred_at)}</td>
          <td class="what">${this.movementLabel(line.type, "mv")}${
            line.voided ? `<b class="chip-void">${t("history.deleted")}</b>` : ""
          }
            <span>${line.note ? `${esc(line.note)} · ` : ""}${this.sourceLabel(line.source)}</span>
          </td>
          <td class="amt ${line.amount_g < 0 ? "minus" : "plus"}">${signed(line.amount_g)}</td>
          <td class="bal">${line.balance_after_g}</td>
          <!-- The X is offered even on a retired spool: it is how a whole-spool discard
               is undone, and how an entry on a deleted spool is voided without
               restitution (docs/14 §14.4.1). The modal is where the branch is taken.
               The reassign arrow is not — see rowActions for why it has no such
               branch. -->
          <td class="acts">${this.rowActions(line)}</td>
        </tr>`,
      )
      .join("");

    const sum = spool.history
      .slice()
      .reverse()
      .map((l) => signed(l.amount_g))
      .join(" ")
      .replace(/^\+ /, "");

    // Back is the whole action row, and deliberately only Back. The weigh/adjust/discard
    // bar belongs under the hero card, where docs/06 §6.5 draws it and where it reads as
    // acting on the spool above it; pinning it would move it above the spool it acts on.
    // Back is already the topmost element, so pinning it reorders nothing and keeps the
    // way out of a fifty-row history one tap away.
    return this.shell(
      `<button class="link" data-action="back">${t("detail.back")}</button>`,
      `<section class="stack">
        <div class="card detail">
          <!-- Seen face-on: the winding, the core hole, and the figure in the middle. The
               card shows the same spool small; this is the same object, larger, not a
               different drawing of it. -->
          <div class="detail-art" style="--coil:${esc(spool.colour)}">
            <span class="coil-base" aria-hidden="true"></span>
            <span class="coil-wind" aria-hidden="true"></span>
            <span class="coil-depth" aria-hidden="true"></span>
            ${spoolRing("hero", spool.percentage, spool.colour)}
            <div class="ring-mid">
              <span class="ring-pct hero">${spool.percentage}<small>%</small></span>
            </div>
          </div>
          <div class="meta">
            <h2>${esc(spool.name)}</h2>
            <div class="big">${spool.balance_g}<small> ${t("detail.ofOpening", {
              opening: spool.opening_weight_g,
            })}</small></div>
            <div class="barline">
              <div class="track"><i style="width:${spool.percentage}%;background:${esc(spool.colour)}"></i></div>
              <span class="pct">${spool.percentage}%</span>
            </div>
            <div class="facts">${esc(spool.material)}${spool.vendor ? ` · ${esc(spool.vendor)}` : ""} · ${esc(spool.colour)}</div>
            <div class="facts">${this.locationLabel(spool.location)} · ${this.stateLabel(spool.state)}${
              spool.tag_uid ? ` · ${t("dlg.tag")} ${esc(spool.tag_uid)}` : ""
            }</div>
            ${this.confidenceBlock(spool)}
          </div>
        </div>

        <!-- The spool action rail, expanded (docs/16 §16.10). Four corrective actions
             first, then the two that end the spool's life, set apart at the end of the
             row: correcting a number is a claim the history below has to justify, and
             ending a spool is a statement about the object in the user's hand. The same
             two are what an inventory card's collapsed rail offers, because they are the
             two that need nothing but the spool. -->
        ${
          deleted
            ? `<div class="note">
                 ${t("detail.deletedNote")}
                 <div class="bar" style="margin-top:10px">
                   <button class="primary" data-action="restore-spool" data-id="${esc(spool.id)}">${t("detail.restoreSpool")}</button>
                 </div>
               </div>`
            : `<div class="bar sp-rail">
                 <button class="primary" data-action="dialog" data-id="weigh">${t("detail.weigh")}</button>
                 <button data-action="dialog" data-id="adjust">${t("detail.adjust")}</button>
                 <button data-action="dialog" data-id="discard">${t("act.discard")}</button>
                 <button data-action="dialog" data-id="edit-spool">${t("detail.edit")}</button>
                 <span class="sp-life">
                   ${
                     this._finishable(spool)
                       ? `<button data-action="spool-finish" data-id="${esc(spool.id)}">${t("detail.finish")}</button>`
                       : ""
                   }
                   <button class="danger" data-action="spool-intent" data-id="${esc(spool.id)}">${t("detail.remove")}</button>
                 </span>
               </div>`
        }

        <div class="card ledger-wrap">
          <h3>${t("detail.heading")}</h3>
          <div class="scroll">
            <table class="ledger">
              <thead><tr>
                <th>${t("history.colWhen")}</th><th>${t("history.colEntry")}</th>
                <th class="r">${t("history.colAmount")}</th>
                <th class="r">${t("history.colBalance")}</th>
                <th class="r">${t("history.colCorrect")}</th>
              </tr></thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
          <div class="checksum">${esc(sum)} = <b>${spool.balance_exact_g.toFixed(1)} g</b></div>
          <p class="muted small">${t("detail.foot")}</p>
        </div>
      </section>`,
    );
  }
}
