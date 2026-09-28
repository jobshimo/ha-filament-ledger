// The Trash tab: deleted spools and voided movements, and how they come back.
//
// Installed on the panel by `mixIn` (./mixin.js, ADR-0010); `this` is the panel.

import { esc } from "../i18n.js";
import { fill, grams } from "./format.js";

export class TrashViews {
  // -- trash -------------------------------------------------------------------------

  trashView() {
    const t = this._t;
    const trash = this._trash;
    const spools = trash?.spools ?? [];
    const movements = trash?.movements ?? [];
    if (!spools.length && !movements.length) {
      return this.shell(
        "",
        `<div class="empty teach">
          <h2>${t("trash.emptyTitle")}</h2>
          <p>${t("trash.emptyBody")}</p>
        </div>`,
      );
    }

    // No action row: restoring is per row, and there is deliberately no empty-the-trash
    // button to offer (docs/adr/0007 — nothing here is awaiting destruction).
    return this.shell(
      "",
      `<section class="stack">
        ${spools.length ? this.trashSpools(spools) : ""}
        ${movements.length ? this.trashMovements(movements) : ""}
      </section>`,
    );
  }

  trashSpools(spools) {
    const t = this._t;
    const rows = spools
      .map(
        (spool) => `
      <div class="trash-row">
        <span class="hist-dot" style="background:${esc(spool.colour)}"></span>
        <span class="trash-name">${esc(spool.name)}</span>
        <span class="muted small">${fill(
          t("trash.spoolMeta", {
            material: spool.material,
            balance: spool.balance_g,
            count: spool.movement_count,
          }),
          "when",
          this.when(spool.deleted_at),
        )}</span>
        <span class="trash-acts">
          <button data-action="open" data-id="${esc(spool.id)}">${t("act.open")}</button>
          <button class="primary" data-action="restore-spool" data-id="${esc(spool.id)}">${t("act.restore")}</button>
        </span>
      </div>`,
      )
      .join("");
    return `
      <div class="card trash-card">
        <h3>${t("trash.spoolsHeading")}</h3>
        <p class="muted small">${t("trash.spoolsBody")}</p>
        ${rows}
      </div>`;
  }

  trashMovements(movements) {
    const t = this._t;
    const rows = movements.map((entry) => this.trashMovementRow(entry)).join("");
    return `
      <div class="card trash-card">
        <h3>${t("trash.movementsHeading")}</h3>
        <p class="muted small">${t("trash.movementsBody")}</p>
        ${rows}
      </div>`;
  }

  trashMovementRow(entry) {
    const t = this._t;
    const direction = entry.amount_g < 0 ? t("trash.returned") : t("trash.removed");
    const action = entry.restorable
      ? `<button class="primary" data-action="restore-movement" data-id="${esc(entry.movement_id)}">${t("act.restore")}</button>`
      : `<span class="muted small">${this._notRestorable(entry)}</span>`;
    // `label`, `direction` and `when` are already-safe results; only `reason` is raw wire
    // data, and it is the one that goes in as a parameter so `t` escapes it.
    let meta = entry.reason
      ? t("trash.movementMetaReason", { reason: entry.reason })
      : t("trash.movementMeta");
    meta = fill(meta, "label", this.movementLabel(entry.type, "mv"));
    meta = fill(meta, "grams", esc(Math.abs(entry.amount_g).toFixed(1)));
    meta = fill(meta, "direction", direction);
    meta = fill(meta, "when", this.when(entry.voided_at));
    return `
      <div class="trash-row">
        <span class="hist-dot" style="background:${esc(entry.spool_colour)}"></span>
        <span class="trash-name">${esc(entry.spool_name)}</span>
        <span class="muted small">${meta}</span>
        <span class="trash-acts">${action}</span>
      </div>`;
  }

  /**
   * Why a chapter offers an explanation instead of a button (docs/14 §14.4.4).
   *
   * The server computes `restorable`; this only names which of the two rules said no, so
   * the sentence and the decision can never disagree about a third case.
   */
  _notRestorable(entry) {
    const t = this._t;
    if (!entry.had_restitution) return t("trash.noRestitution");
    if (entry.spool_deleted) return t("trash.spoolDeleted");
    return t("trash.spoolDiscarded");
  }
}
