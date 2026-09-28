// The Stats tab: what the history adds up to over a period.
//
// Installed on the panel by `mixIn` (./mixin.js, ADR-0010); `this` is the panel.

import { esc } from "../i18n.js";
import { fill, grams } from "./format.js";

/**
 * The three windows the Stats tab offers, in the order it offers them. The values are the
 * backend's own `StatisticsPeriod` (`application/query.py`), so the panel never invents a
 * period the read model does not know — and the labels come from `stats.period<value>`.
 */
const STATS_PERIODS = ["30d", "90d", "all"];

/**
 * The height of one bar-chart row in SVG user units — label, value and bar together. The
 * charts carry no `viewBox`, so a user unit is a CSS pixel and this is a real height.
 */
const STATS_BAR_ROW = 34;

export class StatisticsViews {
  // -- statistics --------------------------------------------------------------------

  /**
   * What the ledger adds up to, over one period (docs/06 §6.7, docs/15 §15.6).
   *
   * **The panel draws; it does not aggregate.** Every figure below arrives finished from
   * `filament_ledger/statistics`, already obeying the visibility law of docs/14 §14.4.5
   * and already rounded exactly once. There is no arithmetic in this view beyond turning
   * a gram figure into a bar width, which is a drawing concern.
   *
   * The period buttons read `this._statsPeriod`, not the DOM: a render replaces every
   * node in the strip, so a selection stored in the markup would be lost on the next
   * paint — the same reason `_tab` is a field.
   */
  statsView() {
    const t = this._t;
    const stats = this._stats;
    // The period selector is this tab's action row, in all three states: choosing a window
    // is the only thing the view does, and a selector that scrolls away below a page of
    // charts is a selector the reader has to hunt for to change their mind.
    if (!stats) {
      // Nothing yet, for one of two reasons. While the first read is in flight, say so;
      // if it failed, the error bar above has already said what happened, and the period
      // buttons stay live so trying again is one tap rather than a tab round-trip.
      return this.shell(
        this.statsPeriods(),
        this._statsLoading ? `<div class="empty">${t("app.loading")}</div>` : "",
      );
    }

    if (stats.empty) {
      return this.shell(
        this.statsPeriods(),
        `<div class="empty teach">
          <h2>${t("stats.emptyTitle")}</h2>
          <p>${t("stats.emptyBody")}</p>
          <p class="muted small">${t("stats.emptyFoot")}</p>
        </div>`,
      );
    }

    return this.shell(
      this.statsPeriods(),
      `<section class="stack">
        ${this.statsTotals(stats)}
        ${this.statsPrintTime(stats.print_time)}
        ${this.statsChart(t("stats.byColour"), this.statsColourRows(stats.by_colour))}
        ${this.statsChart(t("stats.byMaterial"), this.statsMaterialRows(stats.by_material))}
        ${this.statsOutcomes(stats)}
        ${this.statsTopPrints(stats.top_prints)}
        <p class="muted small">${t("stats.foot")}</p>
      </section>`,
    );
  }

  /** The three windows, as buttons rather than a select: nothing to lose focus on. */
  statsPeriods() {
    const t = this._t;
    const buttons = STATS_PERIODS.map(
      (period) => `
        <button class="st-period ${this._statsPeriod === period ? "on" : ""}"
          data-action="stats-period" data-id="${esc(period)}"
          ${this._statsLoading ? "disabled" : ""}>${t(`stats.period${period}`)}</button>`,
    ).join("");
    return `<div class="bar st-periods">
      <span class="st-periodlabel">${t("stats.periodLabel")}</span>${buttons}
    </div>`;
  }

  statsTotals(stats) {
    const t = this._t;
    const stat = (key, value) =>
      `<div class="stat"><div class="k">${key}</div><div class="v">${value}</div></div>`;
    return `
      <div class="card summary">
        ${stat(t("stats.consumed"), esc(grams(stats.consumed_g)))}
        ${stat(t("stats.wasted"), esc(grams(stats.wasted_g)))}
        ${stat(t("stats.printsFinished"), esc(stats.prints?.finished ?? 0))}
        ${stat(t("stats.reviewsResolved"), esc(stats.reviews?.total ?? 0))}
      </div>`;
  }

  /**
   * Total and average print time — **absent entirely when nothing could be measured.**
   *
   * The backend sends null rather than zeros for a period with no timed print, and this
   * renders nothing at all rather than a card of dashes: a figure the data cannot support
   * is not improved by drawing a box around it (docs/14 §14.5's rule, applied here).
   */
  statsPrintTime(printTime) {
    if (!printTime) return "";
    const t = this._t;
    const fact = (key, value) =>
      `<div class="stat"><div class="k">${key}</div><div class="v">${value}</div></div>`;
    return `
      <div class="card st-time">
        <div class="summary">
          ${fact(t("stats.printTime"), esc(this.duration(printTime.total_minutes)))}
          ${fact(t("stats.printTimeAverage"), esc(this.duration(printTime.average_minutes)))}
        </div>
        <p class="muted small">${t("stats.printTimeAcross", { count: printTime.prints })}</p>
      </div>`;
  }

  /** A whole number of minutes, as hours and minutes. Never a decimal hour. */
  duration(minutes) {
    const total = Math.max(0, Math.round(Number(minutes) || 0));
    const hours = Math.floor(total / 60);
    return hours
      ? this._t("stats.duration", { hours, minutes: total % 60 })
      : this._t("stats.durationMinutes", { minutes: total });
  }

  /** The colour chart's rows, each bar painted in the colour it stands for. */
  statsColourRows(entries) {
    return (entries ?? []).map((entry) => ({
      label: esc(entry.colour),
      grams: entry.grams,
      // The one place a bar's fill is data rather than theme: the user thinks in colours,
      // and a palette of our own would be an invented answer to a question the ledger
      // already knows (docs/06 §6.7 — colour is the primary identifier).
      style: `fill:${esc(entry.colour)}`,
    }));
  }

  statsMaterialRows(entries) {
    return (entries ?? []).map((entry) => ({
      label: esc(entry.material),
      grams: entry.grams,
      style: "",
    }));
  }

  /**
   * One horizontal bar chart, as inline SVG built by hand (ADR-0006 — no chart library,
   * no bundler, ever).
   *
   * There is no `viewBox` on purpose. A rect's `width` may be a percentage, which resolves
   * against the SVG's own box, so the bars reflow with the card while the labels stay at
   * their natural size — a viewBox would scale the text with the width and make it
   * illegible on a phone and oversized on a desktop.
   *
   * Bars are drawn relative to the largest value, not to the total: the question this
   * chart answers is *which colour goes fastest*, and a share-of-total chart answers a
   * different one badly. A non-zero value never renders as an invisible sliver — the
   * minimum width is what keeps a 3 g row from looking like a 0 g row.
   */
  statsChart(heading, rows) {
    const t = this._t;
    if (!rows.length) {
      return this.statsCard(heading, `<p class="muted small">${t("stats.noConsumption")}</p>`);
    }
    const largest = Math.max(...rows.map((row) => Number(row.grams) || 0), 1);
    const bars = rows
      .map((row, index) => {
        const share = Math.max(2, ((Number(row.grams) || 0) / largest) * 100);
        return `
        <g transform="translate(0,${index * STATS_BAR_ROW})">
          <text class="lbl" x="0" y="12">${row.label}</text>
          <text class="val" x="100%" y="12" text-anchor="end">${esc(grams(row.grams))}</text>
          <rect class="trk" x="0" y="19" width="100%" height="9" rx="4.5"></rect>
          <rect class="bar" x="0" y="19" width="${share.toFixed(3)}%" height="9" rx="4.5"
            style="${row.style}"></rect>
        </g>`;
      })
      .join("");
    const svg = `<svg class="chart" width="100%" height="${rows.length * STATS_BAR_ROW}"
      role="img" aria-label="${heading}">${bars}</svg>`;
    return this.statsCard(heading, svg);
  }

  /**
   * How prints ended and how reviews were decided, each as one compact segmented bar.
   *
   * A segmented bar rather than a pie: three shares side by side are read by comparing
   * lengths, which people do accurately, instead of by comparing angles, which they do
   * not. A count of zero contributes no segment at all — an empty segment would need a
   * label pointing at nothing.
   */
  statsOutcomes(stats) {
    const t = this._t;
    const prints = stats.prints ?? {};
    const reviews = stats.reviews ?? {};
    return `
      ${this.statsCard(
        t("stats.outcomes"),
        this.statsSegments(
          [
            { label: t("stats.outcomeFinished"), count: prints.finished ?? 0, tone: "ok" },
            { label: t("stats.outcomeCancelled"), count: prints.cancelled ?? 0, tone: "warn" },
            { label: t("stats.outcomeFailed"), count: prints.failed ?? 0, tone: "bad" },
          ],
          t("stats.outcomes"),
          t("stats.noOutcomes"),
        ),
      )}
      ${this.statsCard(
        t("stats.reviewsHeading"),
        this.statsSegments(
          [
            { label: t("stats.reviewsApproved"), count: reviews.approved ?? 0, tone: "ok" },
            { label: t("stats.reviewsDismissed"), count: reviews.dismissed ?? 0, tone: "warn" },
          ],
          t("stats.reviewsHeading"),
          t("stats.noReviews"),
        ),
      )}`;
  }

  statsSegments(segments, aria, empty) {
    const present = segments.filter((segment) => Number(segment.count) > 0);
    const total = present.reduce((sum, segment) => sum + Number(segment.count), 0);
    if (!total) return `<p class="muted small">${empty}</p>`;
    let offset = 0;
    const rects = present
      .map((segment) => {
        const share = (Number(segment.count) / total) * 100;
        const rect = `<rect class="seg ${segment.tone}" x="${offset.toFixed(3)}%" y="0"
          width="${share.toFixed(3)}%" height="14"></rect>`;
        offset += share;
        return rect;
      })
      .join("");
    const legend = present
      .map(
        (segment) =>
          `<span class="st-key ${segment.tone}"><i></i>${esc(segment.count)} ${segment.label}</span>`,
      )
      .join("");
    return `
      <svg class="chart seg-bar" width="100%" height="14" role="img" aria-label="${aria}">
        ${rects}
      </svg>
      <div class="st-legend">${legend}</div>`;
  }

  /** The heaviest prints of the period, joined to the jobs that consumed them. */
  statsTopPrints(prints) {
    const t = this._t;
    const rows = prints ?? [];
    if (!rows.length) {
      return this.statsCard(t("stats.topPrints"), `<p class="muted small">${t("stats.noTopPrints")}</p>`);
    }
    const body = rows
      .map(
        (row) => `
        <tr>
          <td class="what" title="${esc(row.name)}">${esc(row.display_name ?? row.name)}</td>
          <td class="when" title="${esc(row.started_at)}">${this.when(row.started_at)}</td>
          <td class="amt">${esc(grams(row.grams))}</td>
        </tr>`,
      )
      .join("");
    return this.statsCard(
      t("stats.topPrints"),
      `<div class="scroll">
        <table class="ledger st-top">
          <thead><tr>
            <th>${t("stats.colPrint")}</th><th>${t("stats.colWhen")}</th>
            <th class="r">${t("stats.colFilament")}</th>
          </tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>`,
    );
  }

  statsCard(heading, contents) {
    return `<div class="card st-card"><h3>${heading}</h3>${contents}</div>`;
  }
}
