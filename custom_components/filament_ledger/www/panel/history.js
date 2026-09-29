// The History tab: every movement, its filters and its row actions.
//
// Installed on the panel by `mixIn` (./mixin.js, ADR-0010); `this` is the panel.

import { esc } from "../i18n.js";
import { grams, signed } from "./format.js";

/**
 * The two entry types that never leave the history the user sees (docs/14 §14.4.1), so
 * the X is never offered on them. The backend refuses both anyway — this is the panel
 * declining to ask a question it already knows the answer to.
 */
const NOT_VOIDABLE = new Set(["OPENING_BALANCE", "VOID_REVERSAL"]);

/**
 * How long the filter row waits after the last keystroke before it reads.
 *
 * A keystroke is not a round trip. Long enough that typing a word is one query rather than
 * five, short enough that a reader who has stopped typing does not notice waiting.
 */
const FILTER_DEBOUNCE_MS = 300;

/**
 * One end of the history's date filter, as the instant the reader means.
 *
 * Two traps, both silent, and this is the only place either is paid for.
 *
 * A date input yields a bare `YYYY-MM-DD`, and `new Date()` reads a date-only string as
 * **UTC** midnight — so a reader in Madrid asking for the 5th would lose its first two
 * hours to the 4th. The parts are read by hand into a *local* Date instead, and
 * `toISOString` then carries the offset the backend insists on (`_moment`,
 * `infrastructure/ha/websocket_api.py`): a bound without one names a wall clock, and the
 * ledger stores instants.
 *
 * Both bounds are inclusive, so a start is the day's first millisecond and an end is its
 * last. An `until` of midnight would silently drop everything that happened on the day the
 * user named, which is precisely the day they were asking about.
 */
function dayBound(value, end = false) {
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value ?? "");
  if (!parts) return null;
  const [year, month, day] = parts.slice(1).map(Number);
  const moment = end
    ? new Date(year, month - 1, day, 23, 59, 59, 999)
    : new Date(year, month - 1, day);
  return Number.isNaN(moment.getTime()) ? null : moment.toISOString();
}

export class HistoryViews {
  // -- history -----------------------------------------------------------------------

  /**
   * The filter row, as the backend's own filter payload (docs/06 §6.6).
   *
   * Every field is omitted when it is empty, because an absent key is that filter cleared
   * (`_movement_filter`, `infrastructure/ha/websocket_api.py`). An untouched row therefore
   * builds `{}`, which is `NO_FILTERS`, which is the read the history has always run — so
   * *clear every filter* needs no command, no flag and no branch on either side of the
   * wire.
   *
   * The dates leave as instants with an offset and the grams as magnitudes, both of which
   * are the wire's terms rather than the control's: a date input holds a wall-clock day and
   * the schema refuses one, and the backend compares `abs(amount_mg)` so a −84 g print
   * matches *more than 50 g*.
   */
  _filterPayload() {
    const filters = this._filters;
    const payload = {};
    const since = dayBound(filters.since);
    const until = dayBound(filters.until, true);
    if (since) payload.since = since;
    if (until) payload.until = until;
    if (filters.colours.length) payload.colours = filters.colours;
    if (filters.minG !== "") payload.min_g = Number(filters.minG);
    if (filters.maxG !== "") payload.max_g = Number(filters.maxG);
    if (filters.search.trim()) payload.search = filters.search.trim();
    return payload;
  }

  /**
   * Whether the row is narrowing anything — asked of the payload rather than of the fields.
   *
   * One definition, so the sentence under the table, the state of the Clear control and the
   * decision to re-read after a push can never disagree about what counts as filtered. A
   * half-typed date is not a filter until it is a date, and this is why.
   */
  _filtering() {
    return Object.keys(this._filterPayload()).length > 0;
  }

  /**
   * Read the narrowed history. Assigns; it does not paint.
   *
   * `_movements` is always what the History tab shows, filtered or not, so the corrections
   * a row offers resolve against the rows on screen (`_movementSubject`) rather than
   * against a second list kept beside them.
   *
   * The token is monotonic rather than a copy of the filters, for the reason `_loadStats`
   * gives: in a black → grey → black tap sequence the first reply is indistinguishable from
   * the current one by value, so a reordered stale payload could land. Only the latest
   * request may write.
   */
  async _readHistory() {
    const token = (this._filterRequest = (this._filterRequest || 0) + 1);
    try {
      const movements = await this.call("movements", this._filterPayload());
      if (token !== this._filterRequest) return;
      this._movements = movements;
      this._error = null;
    } catch (error) {
      if (token !== this._filterRequest) return;
      this._error = error.message || String(error);
    }
  }

  /** Read the narrowed history and paint it. Every filter change ends up here. */
  async _applyFilters() {
    clearTimeout(this._filterTimer);
    await this._readHistory();
    this.render();
  }

  /**
   * One read per pause, not one per keystroke.
   *
   * Only the typed controls come through here. A colour swatch and *Clear filters* are
   * single deliberate acts with nothing half-finished to protect, so they read at once.
   */
  _debounceFilters() {
    clearTimeout(this._filterTimer);
    this._filterTimer = setTimeout(() => this._applyFilters(), FILTER_DEBOUNCE_MS);
  }

  /**
   * The whole ledger, newest first, narrowed by the row above it (docs/06 §6.6).
   *
   * The longest surface in the panel and the one the shell exists for: the header, the tab
   * strip and the filters stay above it however far down the entries the reader gets, and
   * the table's own column headings stay with them — see the stylesheet for why that took
   * a change of structure rather than one declaration.
   */
  historyView() {
    const t = this._t;
    const filtering = this._filtering();

    if (!this._movements.length) {
      // Two empty histories, and conflating them is how a filter comes to read as data
      // loss. A ledger with nothing in it teaches what will land there and offers no
      // filters, because there is nothing to narrow; a filter that matched nothing keeps
      // its own row, because widening it is the only way out.
      if (!filtering) {
        return this.shell(
          "",
          `<div class="empty teach">
            <h2>${t("history.emptyTitle")}</h2>
            <p>${t("history.emptyBody")}</p>
            <p class="muted">${t("history.emptyFoot")}</p>
          </div>`,
        );
      }
      return this.shell(
        this.historyFilters(),
        `<div class="empty teach">
          <h2>${t("history.noMatchTitle")}</h2>
          <p>${t("history.noMatchBody")}</p>
          <button data-action="filters-clear">${t("history.filterClear")}</button>
        </div>`,
      );
    }

    const rows = this.historyRows();
    return this.shell(
      this.historyFilters(),
      `<div class="card ledger-wrap pinned">
        <h3>${t("history.heading")}</h3>
        <table class="ledger">
          <thead><tr>
            <th>${t("history.colWhen")}</th><th>${t("history.colSpool")}</th>
            <th>${t("history.colEntry")}</th><th class="r">${t("history.colAmount")}</th>
            <th>${t("history.colSource")}</th><th class="r">${t("history.colCorrect")}</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <p class="muted small">${
          filtering
            ? t("history.footFiltered", { count: this._movements.length })
            : t("history.foot", { count: this._movements.length })
        }</p>
      </div>`,
    );
  }

  /**
   * The six controls, in the shell's pinned action row (docs/06 §6.1, §6.6).
   *
   * They belong there and nowhere else: a control that narrows the rows below it must not
   * scroll away with the rows it narrows, which is the same rule that put the ledger's
   * column headings on the pinned list.
   *
   * Every value is read from `this._filters` rather than from the DOM, and the search box's
   * own text is user data on its way back into markup — so it goes through `esc()` exactly
   * as a spool name does. The panel does not get to assume it wrote it.
   *
   * **The row folds on a phone, and the count is what stops that lying.** Six controls at a
   * 44px tap target is 336px of fixed chrome on a 380px-wide panel — measured, and 56% of
   * it, leaving three rows of the ledger the row exists to filter. So a narrow panel gets
   * one control that opens the rest, carrying how many of them are set: a narrowed history
   * behind a folded row would otherwise look like a ledger that had lost its entries. Above
   * that tier the row is a single line and always open, and the stylesheet renders the
   * toggle away rather than the panel deciding a width it cannot measure.
   */
  historyFilters() {
    const t = this._t;
    const filters = this._filters;
    const active = Object.keys(this._filterPayload()).length;
    const bound = (key, label, value) => `
      <input class="hf-g" type="number" min="0" step="0.1" inputmode="decimal"
        data-filter="${key}" data-focus="${key}" value="${esc(value)}"
        aria-label="${label}" placeholder="${label}">`;
    return `
      <button class="hf-toggle" data-action="filters-toggle" data-focus="filters-toggle"
        aria-expanded="${this._filtersOpen}">${t("history.filterToggle")}${
          active
            ? `<span class="hf-count" title="${t("history.filterActive", { count: active })}">${esc(active)}</span>`
            : ""
        }</button>
      <div class="bar hf ${this._filtersOpen ? "" : "shut"}">
        <label class="hf-field hf-wide">
          <span class="hf-k">${t("history.filterSearch")}</span>
          <input class="hf-search" type="search" data-filter="search" data-focus="search"
            value="${esc(filters.search)}" placeholder="${t("history.filterSearchPlaceholder")}"
            title="${t("history.filterSearchHelp")}">
        </label>
        <label class="hf-field">
          <span class="hf-k">${t("history.filterFrom")}</span>
          <input type="date" data-filter="since" data-focus="since" value="${esc(filters.since)}">
        </label>
        <label class="hf-field">
          <span class="hf-k">${t("history.filterTo")}</span>
          <input type="date" data-filter="until" data-focus="until" value="${esc(filters.until)}">
        </label>
        <!-- Not a label: one label names one control, and the two bounds are one question
             with two answers. Each input carries its own accessible name instead. -->
        <div class="hf-field">
          <span class="hf-k" title="${t("history.filterAmountHelp")}">${t("history.filterAmount")}</span>
          <div class="hf-pair">
            ${bound("minG", t("history.filterAtLeast"), filters.minG)}
            ${bound("maxG", t("history.filterAtMost"), filters.maxG)}
          </div>
        </div>
        ${this.historyColours()}
        <button class="hf-clear" data-action="filters-clear" data-focus="clear"
          ${active ? "" : "disabled"}>${t("history.filterClear")}</button>
      </div>`;
  }

  /**
   * One swatch per colour in the inventory, toggled on and off.
   *
   * **Painted with `colour`, filtered on `colour_hex8`** — the display form and the stored
   * form, and the difference matters twice. The swatch has to be the colour the user
   * recognises on the card and in the row, which is what every other swatch in this panel
   * paints (`spoolCard`, `historyRow`, `syncRow`); the filter has to carry the value the
   * ledger actually stored, alpha and all, because that is what the SQL compares.
   *
   * Deduplicated on the stored value, so two spools of the same black offer one swatch. The
   * list is the inventory rather than the colours present in the rows on screen: those
   * narrow as the filter bites, and a control that removes its own options as they are used
   * cannot be undone without clearing everything.
   */
  historyColours() {
    const t = this._t;
    const seen = new Map();
    for (const spool of this._spools) {
      if (spool.colour_hex8 && !seen.has(spool.colour_hex8)) {
        seen.set(spool.colour_hex8, spool.colour);
      }
    }
    if (!seen.size) return "";
    const swatches = [...seen.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([stored, paint]) => {
        const on = this._filters.colours.includes(stored);
        return `<button class="hf-dot ${on ? "on" : ""}" data-action="filters-colour"
          data-id="${esc(stored)}" data-focus="colour-${esc(stored)}"
          style="background:${esc(paint)}" aria-pressed="${on}" title="${esc(paint)}"
          aria-label="${t("history.filterColourOne", { colour: paint })}"></button>`;
      })
      .join("");
    return `
      <div class="hf-field">
        <span class="hf-k" title="${t("history.filterColourHelp")}">${t("history.filterColour")}</span>
        <div class="hf-dots">${swatches}</div>
      </div>`;
  }

  historyRow(m) {
    const t = this._t;
    const detail = [m.job_display_name ?? m.job_name, m.note].filter(Boolean).map(esc).join(" · ");
    const confirmed = m.source === "USER_CONFIRMED";
    return `
      <tr>
        <td class="when" title="${esc(m.occurred_at)}">${this.when(m.occurred_at)}</td>
        <td class="who"><span class="hist-dot" style="background:${esc(m.spool_colour)}"></span>${esc(m.spool_name)}</td>
        <td class="what">${this.movementLabel(m.type)}
          ${detail ? `<span>${detail}</span>` : ""}
        </td>
        <td class="amt ${m.amount_g < 0 ? "minus" : "plus"}">${signed(m.amount_g)}</td>
        <td class="src"><span class="badge ${confirmed ? "user" : "auto"}">${
          confirmed ? t("history.confirmed") : t("history.auto")
        }</span></td>
        <td class="acts">${this.rowActions(m)}</td>
      </tr>`;
  }

  /**
   * The table body, with the consecutive rows of one print gathered under a caption.
   *
   * The grouping is visual and nothing else: the payload's order — newest first, the
   * order the backend serves — is walked exactly as it arrives, and only an *unbroken*
   * run of one `job_id` is gathered, so two prints interleaved in time stay interleaved
   * rather than being quietly re-sorted into a story that did not happen. A movement
   * without a job renders exactly as it always has.
   */
  historyRows() {
    const rows = [];
    let index = 0;
    while (index < this._movements.length) {
      const movement = this._movements[index];
      if (movement.job_id == null) {
        rows.push(this.historyRow(movement));
        index += 1;
        continue;
      }
      const group = [movement];
      while (
        index + group.length < this._movements.length &&
        this._movements[index + group.length].job_id === movement.job_id
      ) {
        group.push(this._movements[index + group.length]);
      }
      rows.push(this.historyGroupHeader(group));
      for (const grouped of group) rows.push(this.historyRow(grouped));
      index += group.length;
    }
    return rows.join("");
  }

  /**
   * One print's caption: its readable name, the net grams of the rows beneath it, and —
   * only when there is more than one — how many there are. The sum is signed exactly as
   * the amounts it sums, because a group holding a print and its correction nets to what
   * actually left the spools.
   */
  historyGroupHeader(group) {
    const t = this._t;
    const name = group[0].job_display_name ?? group[0].job_name;
    const total = group.reduce((sum, m) => sum + m.amount_g, 0);
    const count =
      group.length > 1
        ? `<span class="hj-count">${t("history.groupEntries", { count: group.length })}</span>`
        : "";
    return `
      <tr class="hist-job">
        <td colspan="6">
          <span class="hj-name">${name ? esc(name) : t("history.groupUnnamed")}</span>
          <span class="hj-sum ${total < 0 ? "minus" : "plus"}">${signed(total)}</span>
          ${count}
        </td>
      </tr>`;
  }

  /**
   * The two corrections a history row offers, and when (docs/14 §14.3, §14.4).
   *
   * **[ ⇄ ]** moves a charge to the spool that actually fed the print, so it is offered
   * only where there is a charge — the entry's own direction, which for the correction
   * types is its sign rather than its type's `EITHER`. **[ × ]** deletes the entry from
   * the history the user sees; it is withheld from the two types the backend refuses, so
   * the panel never asks a question whose answer it already knows.
   *
   * A voided row offers neither. Both would be refused, and both would be nonsense: its
   * grams have already gone back.
   *
   * A row on a *retired* spool offers only the X. The ⇄ is withheld rather than given a
   * retired branch of its own, because unlike the X there is no without-restitution
   * variant to offer: a reassignment is a pair, and half a pair is filament invented.
   */
  rowActions(m) {
    const t = this._t;
    if (m.voided) return `<span class="muted small">${t("history.deleted")}</span>`;
    const buttons = [];
    const retired = this._movementSubject(m.movement_id)?.retirement;
    if (m.direction === "DECREASE" && !retired) {
      buttons.push(
        `<button class="rowact" data-action="reassign" data-id="${esc(m.movement_id)}"
          title="${t("history.reassignTitle")}">⇄</button>`,
      );
    }
    if (!NOT_VOIDABLE.has(m.type)) {
      buttons.push(
        `<button class="rowact danger" data-action="void-movement" data-id="${esc(m.movement_id)}"
          title="${t("history.voidTitle")}">×</button>`,
      );
    }
    return buttons.join("");
  }
}
