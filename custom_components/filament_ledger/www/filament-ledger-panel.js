/**
 * Filament Ledger — Home Assistant sidebar panel.
 *
 * A plain custom element. No framework, no bundler, no build step: see
 * docs/adr/0006-vanilla-panel.md. It talks to the backend only through the websocket
 * commands in infrastructure/ha/websocket_api.py, and there is no command that sets a
 * balance — changing one requires a movement, and that is the whole design.
 *
 * This module is the element itself — lifecycle, the live subscription, event dispatch and
 * the frame every view renders into. Each tab's rendering lives in `www/panel/<tab>.js` and
 * is installed on the element by `mixIn` below (docs/adr/0010-the-panel-has-tests.md), so
 * those methods keep `this` and call each other exactly as they did when they lived here.
 *
 * Styling uses Home Assistant's own CSS custom properties throughout, so light, dark and
 * custom themes work without a line of per-theme code.
 *
 * **Every user-facing string lives in `i18n.js`** (docs/14 §14.6.1). The acceptance
 * criterion is a panel source with zero user-facing literals, here or in `www/panel/`, and
 * the rule is enforced by reading: a quoted sentence outside a `t(...)` call is a review
 * finding.
 *
 * Two escaping rules, and they do not overlap:
 *
 * - Anything interpolated from the wire — a name, a note, a reason, a job name — goes
 *   through `esc()` at its call site, exactly as it always has.
 * - A `t(...)` result is **already safe and is never wrapped in `esc()`**: `t` escapes
 *   every parameter it substitutes, and several templates carry `<b>` on purpose, so
 *   escaping the template would print the tags.
 */

import {
  esc,
  resolveLanguage,
  translator,
  writeLanguageOverride,
} from "./i18n.js";
import { UNIDENTIFIED_PRINTER, fill, grams, signed } from "./panel/format.js";
import { STYLES } from "./panel/styles.js";
import { mixIn } from "./panel/mixin.js";
import { AmsViews } from "./panel/ams.js";
import { DialogViews } from "./panel/dialogs.js";
import { HistoryViews } from "./panel/history.js";
import { InventoryViews } from "./panel/inventory.js";
import { PrinterViews } from "./panel/printer.js";
import { ReviewViews } from "./panel/review.js";
import { SettingsViews } from "./panel/settings.js";
import { SpoolDetailViews } from "./panel/spool-detail.js";
import { StatisticsViews } from "./panel/statistics.js";
import { TrashViews } from "./panel/trash.js";

const TABS = [
  "inventory",
  "history",
  // Beside History, because the two answer the same question at two zoom levels: History
  // is every entry, Stats is what those entries add up to (docs/06 §6.7).
  "stats",
  "review",
  "ams",
  // Between AMS and Trash: a glance at the machine sits with the daily surfaces, and the
  // correction ones sit behind them (docs/14 §14.4.4, §14.5).
  "printer",
  // Beside Trash, because both are the past tense of the inventory: Finished holds the
  // spools whose filament is gone, Trash holds the ones that were never really here.
  "finished",
  "trash",
  // Last. Configuration is the least-frequent surface (docs/14 §14.6.4).
  "settings",
];

/**
 * The empty filter set, and therefore the whole history (docs/06 §6.6).
 *
 * Mirrors `NO_FILTERS` (`domain/port/repositories.py`) deliberately: *clear every filter* is
 * this value rather than a flag, so it is a special case in neither half of the system. The
 * panel builds a payload from it, every field comes out absent, and the backend reads an
 * absent field as that filter cleared — which is the unfiltered read it has always run.
 *
 * A factory rather than a shared constant: the colours are a list, and one object handed to
 * every reset would carry one afternoon's choices into the next.
 */
const noHistoryFilters = () => ({
  since: "",
  until: "",
  colours: [],
  minG: "",
  maxG: "",
  search: "",
});

/** The default window, and the one the tab opens on. */
const DEFAULT_STATS_PERIOD = "30d";

/**
 * How wide the fade at each end of the tab strip is, and the slack below which the strip
 * counts as scrolled to that end. One pixel of slack absorbs the sub-pixel scroll offsets
 * a zoomed browser produces, which would otherwise leave a fade showing at a hard end.
 */
const TAB_FADE_SLACK = 1;

/**
 * The typefaces, and the one rule about them that fails silently if broken.
 *
 * **A `@font-face` declared inside a shadow root is ignored.** Font faces resolve against the
 * document, and a shadow tree is deliberately not allowed to define one — otherwise a component
 * could redefine another's fonts and encapsulation would leak through the font stack. Putting
 * these in `STYLES` produces no error and no warning; the text simply renders in the fallback,
 * which reads as a font that failed to load rather than a rule that was never honoured
 * ([16 §16.2](../../../docs/16-visual-system.md)).
 *
 * So the faces are written into `document.head` instead. Everything else stays in the shadow
 * root, where it belongs.
 *
 * Space Grotesk is a **variable** font: one file per subset spans 400–700, which is why a
 * single rule carries a weight *range* rather than four rules carrying four files. IBM Plex
 * Mono is static, so it gets one file per weight. Only latin and latin-ext ship — the panel
 * speaks English and Spanish, and cyrillic would be 60 KB nobody renders.
 *
 * Paths are resolved from `import.meta.url` rather than from a hard-coded `/filament_ledger_static`,
 * so the fonts follow the module wherever it is served from.
 */
const FONT_STYLE_ID = "filament-ledger-fonts";

const LATIN =
  "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, " +
  "U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD";

const LATIN_EXT =
  "U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, " +
  "U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, " +
  "U+2C60-2C7F, U+A720-A7FF";

const FONT_FACES = [
  { family: "Space Grotesk", weight: "400 700", file: "space-grotesk-latin.woff2", range: LATIN },
  { family: "Space Grotesk", weight: "400 700", file: "space-grotesk-latin-ext.woff2", range: LATIN_EXT },
  { family: "IBM Plex Mono", weight: "400", file: "ibm-plex-mono-400-latin.woff2", range: LATIN },
  { family: "IBM Plex Mono", weight: "400", file: "ibm-plex-mono-400-latin-ext.woff2", range: LATIN_EXT },
  { family: "IBM Plex Mono", weight: "500", file: "ibm-plex-mono-500-latin.woff2", range: LATIN },
  { family: "IBM Plex Mono", weight: "500", file: "ibm-plex-mono-500-latin-ext.woff2", range: LATIN_EXT },
  { family: "IBM Plex Mono", weight: "600", file: "ibm-plex-mono-600-latin.woff2", range: LATIN },
  { family: "IBM Plex Mono", weight: "600", file: "ibm-plex-mono-600-latin-ext.woff2", range: LATIN_EXT },
];

/**
 * Declare the faces on the document, once.
 *
 * Guarded by id: the browser executes a module once per URL, but a guard costs one line and
 * makes a second execution harmless rather than a duplicated stylesheet.
 *
 * `font-display: swap` on purpose — the panel's job is to show a number to somebody standing at
 * a printer, and text they cannot read for 300 ms is worse than text in the wrong face.
 */
function installFonts() {
  if (document.getElementById(FONT_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = FONT_STYLE_ID;
  style.textContent = FONT_FACES.map(
    ({ family, weight, file, range }) => `@font-face {
  font-family: "${family}";
  font-style: normal;
  font-weight: ${weight};
  font-display: swap;
  src: url("${new URL(`fonts/${file}`, import.meta.url).href}") format("woff2");
  unicode-range: ${range};
}`,
  ).join("\n");
  document.head.appendChild(style);
}

/**
 * Eight motes of filament colour drifting up behind everything.
 *
 * Written out as data rather than eight hand-tuned divs: position, size, colour and the
 * two timings. The negative delays are what matter — without them all eight would start
 * at the bottom together on the first paint and arrive as a wave, which reads as a loading
 * animation rather than as something that was already happening.
 *
 * `pointer-events: none` on the layer, `aria-hidden` on it: decoration is not content, and
 * it must never intercept a tap meant for a spool.
 */
const MOTES = [
  { x: 8, size: 3, colour: "#00e0c6", dur: 17, delay: -2 },
  { x: 21, size: 2, colour: "#ff8a3d", dur: 23, delay: -7 },
  { x: 34, size: 4, colour: "#8323ff", dur: 19, delay: -12 },
  { x: 47, size: 2, colour: "#00e0c6", dur: 26, delay: -3 },
  { x: 58, size: 3, colour: "#ffb340", dur: 21, delay: -15 },
  { x: 71, size: 2, colour: "#00e0c6", dur: 29, delay: -9 },
  { x: 83, size: 3, colour: "#e11d48", dur: 24, delay: -19 },
  { x: 93, size: 2, colour: "#ff8a3d", dur: 18, delay: -5 },
];

const AMBIENT = `<div class="ambient" aria-hidden="true">${MOTES.map(
  (m) =>
    `<i style="left:${m.x}%;width:${m.size}px;height:${m.size}px;background:${m.colour};
      box-shadow:0 0 ${m.size * 3}px ${m.colour};
      animation-duration:${m.dur}s;animation-delay:${m.delay}s"></i>`,
).join("")}</div>`;

/**
 * The panel does not decide when it is stale. The backend tells it.
 *
 * One subscription, and the integration pushes a payload whenever the ledger changes or
 * the printer's own entities do. No polling, no interval, and no comparing of `hass`
 * objects between assignments: the two things that can change what this panel shows are
 * both known on the server, and the server is what says so
 * (`infrastructure/ha/websocket_api.py`).
 *
 * The seventeen event names this file used to carry went with it. They lived here because
 * the client was deciding what mattered. It is not, any more, so there is no second list
 * to drift out of step with the bridge.
 */
const SUBSCRIBE = "filament_ledger/subscribe";

class FilamentLedgerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "inventory";
    this._spools = [];
    this._stock = null;
    this._reviews = [];
    this._movements = [];
    // Deleted spools and open void chapters — a view over facts that already exist, not
    // a holding pen for rows awaiting destruction (docs/adr/0007).
    this._trash = null;
    this._detail = null;
    this._error = null;
    this._loading = true;
    this._dialog = null;
    // The last sync's per-slot outcome. Transient by design: dismissed by hand, replaced
    // by the next sync, dropped on a tab change — a report of a moment, not state.
    this._sync = null;
    // The printer glance and the settings, each fetched only when its tab is opened and
    // when its own button asks (docs/14 §14.5): no timer, and never on the general
    // refresh — a glance has a moment, and the moment is the user's.
    this._printer = null;
    this._printerLoading = false;
    // The Finished tab's list, fetched on the same terms as the printer glance: once per
    // opening, no timer, never on the general refresh. Spools whose filament is gone
    // change only when the user changes them, so the moment of the read is the user's.
    this._finished = null;
    this._finishedLoading = false;
    this._settings = null;
    this._settingsLoading = false;
    this._settingsSaved = false;
    // The Stats tab, fetched the same way and for the same reason: a period's figures are
    // a question the user asked, not something to recompute on every ledger refresh. The
    // chosen period is a state field exactly like `_tab` — the innerHTML re-render throws
    // the buttons away on every paint, so the selection has to live somewhere the DOM is
    // rebuilt *from* rather than in the DOM itself.
    this._stats = null;
    this._statsLoading = false;
    this._statsPeriod = DEFAULT_STATS_PERIOD;
    // The History tab's filter row, and a field for exactly the reason the period above is
    // one: the innerHTML re-render throws every control away on every paint, so a selection
    // held in the DOM would last until the next update arrived. It outlives a tab change
    // too — a filter is a question the user asked, and walking to the AMS tab to check a
    // slot is not withdrawing it. Only *Clear filters* clears them (docs/06 §6.6).
    this._filters = noHistoryFilters();
    this._filterTimer = null;
    // Whether the row is unfolded, which only a narrow panel ever asks: six controls at a
    // 44px tap target is more fixed chrome than a phone can spare, and the stylesheet keeps
    // the row open unconditionally above that tier. A field rather than the DOM's own
    // state, for the reason everything else here is one — the paint replaces it.
    this._filtersOpen = false;
    // One window listener for the whole lifetime of the element, bound once here so
    // `disconnectedCallback` can remove the very function `connectedCallback` added.
    // The tab strip is recreated on every render; `window` is not, and a listener added
    // per render would accumulate one copy per navigation.
    this._onViewportResize = () => this._paintTabOverflow();
    // Live updates: the unsubscribe callbacks Home Assistant hands back, the debounce
    // timers, and the flag that remembers an update held back while the user was typing.
    this._unsubscribe = null;
    this._subscribing = false;
    this._liveDeferred = false;
    // Which tab the last paint drew, so the entry animation runs on a change of view and
    // not on every update that arrives while you are looking at one.
    this._painted = null;
    // Resolved once here so the very first paint is already in the right language; `set
    // hass` re-resolves as soon as the profile is known.
    this._applyLanguage();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    // The subscription is the first load as well as the live one: it pushes the current
    // state on open, so there is no separate set of startup reads that could disagree with
    // what arrives a moment later.
    //
    // Nothing happens on later assignments. Home Assistant hands over a new `hass` whenever
    // anything in the house changes, and treating that as a signal about *this* integration
    // is how a panel ends up polling while insisting it does not.
    if (first) {
      this._applyLanguage();
      this._subscribeLive();
    }
  }

  get hass() {
    return this._hass;
  }

  /** The override, else the Home Assistant profile, else English (docs/14 §14.6.1). */
  _applyLanguage() {
    this._lang = resolveLanguage(this._hass);
    this._t = translator(this._lang);
  }

  /**
   * When a movement happened, in words.
   *
   * A method rather than a module function because the words are translated, and the
   * language is a property of this panel instance rather than of the module.
   */
  when(iso) {
    if (!iso) return "";
    const t = this._t;
    const then = new Date(iso);
    const days = Math.floor((Date.now() - then.getTime()) / 86400000);
    if (days <= 0) {
      return t("time.today", {
        time: then.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      });
    }
    if (days === 1) return t("time.yesterday");
    if (days < 30) return t("time.daysAgo", { days });
    return esc(then.toLocaleDateString());
  }

  /**
   * How a movement type reads, in the two registers docs/06 §6.6 distinguishes.
   *
   * `hist.*` is the terse global-table wording, `mv.*` the fuller one a single spool's
   * own history uses. Both are derived from the wire's `type` rather than from its
   * pre-rendered `label`, because the label the backend computes is English and the
   * column has to speak the reader's language.
   *
   * A type neither table knows renders verbatim rather than as its own key: the wire
   * value is the honest answer, and inventing a word for it would be worse.
   */
  movementLabel(type, scope = "hist") {
    const key = `${scope}.${type}`;
    const label = this._t(key);
    return label === key ? esc(type) : label;
  }

  /** *confirmed by you* or *automatic* — provenance for the reader (docs/02 §2.4). */
  sourceLabel(source) {
    const key = `src.${source}`;
    const label = this._t(key);
    return label === key ? esc(source) : label;
  }

  /**
   * Where a spool is, rebuilt from the `kind`/`slot` pair rather than printed from the
   * wire's pre-rendered `label`.
   *
   * `describe_location` sends both (`application/query.py`); the label is English, and
   * the pair is the data it was built from. Rebuilding is the same move `movementLabel`
   * makes for the same reason — a read model is data, and the sentence around it belongs
   * to the reader.
   */
  locationLabel(location) {
    // The machine is named only once more than one holds spools. *AMS slot 3* is a complete
    // address in a one-printer household and a serial beside it would be fifteen characters
    // the reader has to look past on every card; with two machines the same three words stop
    // saying where anything is (docs/06 §6.4, amended v2.0).
    const suffix = this._amsPrinters().length > 1 ? "_ON" : "";
    // The second holder is a different place from the first, so it is a different key.
    // Only the second is numbered: a machine with one holder has nothing to distinguish,
    // and every label already on a card for it reads exactly as it always has.
    const second = location?.kind === "EXTERNAL_SPOOL" && location?.holder === 2 ? "_2" : "";
    const key = `loc.${location?.kind}${second}${location?.printer ? suffix : ""}`;
    const label = this._t(key, {
      slot: location?.slot,
      printer:
        location?.printer === UNIDENTIFIED_PRINTER
          ? this._t("ams.machineUnnamed")
          : location?.printer,
    });
    return label === key ? esc(location?.label ?? "") : label;
  }

  /**
   * The tray space one mount acts in: the machine the caller named, and the AMS unit.
   *
   * `printer` is whatever the AMS section that raised the dialog was showing — the panel
   * knows which machine's tray 3 the user tapped, and with several machines nothing else
   * does. It is **omitted** when that section had no name to give (the glance has not
   * arrived, or discovery named nobody), and the backend reads an absent printer as the
   * tray space this ledger follows, which is the same answer decided in the one place that
   * owns the sentinel (`websocket_api._TRAY`). Inventing a name here would be the panel
   * deciding what an unidentified printer is called.
   *
   * **The AMS ordinal comes from the card the user tapped**, not from a ledger-wide
   * constant. It used to come from `tracking.ams`, which named the one unit this ledger
   * followed; a machine now states its own units and a mount into the second one has to
   * say so, or every reel on it would be recorded in the first (v2.9). Omitted when the
   * card had no ordinal to give, and the backend then reads the first — the same answer
   * `_TRAY` has given an absent `ams` since v2.0.
   */
  _traySpace(printer, ams) {
    const space = {};
    if (printer) space.printer = printer;
    if (ams) space.ams = Number(ams);
    return space;
  }

  /**
   * What one mount sends, for whichever position the open dialog was raised on.
   *
   * Two positions, one command. An AMS tray names its `slot` inside the tray space above.
   * The printer's external spool — the direct feed beside the AMS — has no slot and no AMS
   * unit, so it sends `external: true` in place of the slot, the machine from the tray
   * space, and `holder` for which of the machine's feeds it is: an `ams` ordinal on a
   * position that is not in the AMS would be a claim about a unit the spool is not in,
   * while a holder left unsaid on a dual-nozzle machine would land every reel on the
   * first. Both paths (`mount-pick` and the form submit) build from here so they cannot
   * disagree about the shape.
   */
  _mountPayload(spoolId) {
    const dialog = this._dialog ?? {};
    const space = this._traySpace(dialog.printer, dialog.ams);
    if (dialog.external) {
      const payload = { spool_id: spoolId, external: true };
      if (space.printer) payload.printer = space.printer;
      if (dialog.holder) payload.holder = Number(dialog.holder);
      return payload;
    }
    return { spool_id: spoolId, ...space, slot: dialog.slot };
  }

  /** *active*, *sealed*, *discarded*, *deleted* — the derived state, in words. */
  stateLabel(state) {
    const key = `state.${state}`;
    const label = this._t(key);
    return label === key ? esc(String(state).toLowerCase()) : label;
  }

  connectedCallback() {
    // The ambient layer is a sibling of #root, not part of it. The panel repaints by
    // replacing #root's innerHTML on every navigation (ADR-0006), and a drifting particle
    // rebuilt on every tab change would snap back to the bottom each time. Set once here,
    // it drifts across the whole session and nobody sees a seam.
    this.shadowRoot.innerHTML =
      `<style>${STYLES}</style>${AMBIENT}<div id="root"></div>`;
    this._root = this.shadowRoot.getElementById("root");
    this._root.addEventListener("click", (event) => this._onClick(event));
    this._root.addEventListener("submit", (event) => this._onSubmit(event));
    // Review cards are edited in place — a full re-render per keystroke would steal the
    // focus mid-number — so edits patch the card directly instead of going through render().
    this._root.addEventListener("input", (event) => this._onInput(event));
    // Leaving a field is the other moment a held update may land. Deferred by a tick
    // because `activeElement` has not moved yet while `focusout` is dispatching — asking
    // _busy() now would still see the field being left as the focused one.
    this._root.addEventListener("focusout", () => setTimeout(() => this._releaseLive(), 0));
    // Passive: this listener only reads geometry and toggles two classes, and saying so
    // lets the browser keep scrolling off the main thread.
    window.addEventListener("resize", this._onViewportResize, { passive: true });
    this.render();
  }

  disconnectedCallback() {
    window.removeEventListener("resize", this._onViewportResize);
    // A pending filter read would fire into a panel that no longer has a root to paint.
    clearTimeout(this._filterTimer);
    // Home Assistant keeps one websocket for the whole frontend. A subscription this panel
    // opened and did not close outlives the panel and keeps a read model being computed for
    // a view nobody is looking at, once more per navigation away and back.
    if (this._unsubscribe) this._unsubscribe();
    this._unsubscribe = null;
  }

  // -- live --------------------------------------------------------------------------

  /**
   * Open the subscription, once (docs/06 §6.8).
   *
   * It resolves asynchronously, so it checks on arrival whether the panel is still
   * connected: navigating away during setup would otherwise leave a live subscription with
   * nothing left to close it.
   *
   * A subscription that cannot be opened costs liveness, never correctness — every action
   * the user takes still refreshes on its own. Putting an error bar over a working ledger
   * because a socket was unhappy would be the worse failure.
   */
  _subscribeLive() {
    const connection = this._hass?.connection;
    if (!connection || this._unsubscribe || this._subscribing) return;
    this._subscribing = true;
    connection
      .subscribeMessage((payload) => this._pushed(payload), { type: SUBSCRIBE })
      .then((unsubscribe) => {
        this._subscribing = false;
        if (this.isConnected) this._unsubscribe = unsubscribe;
        else unsubscribe();
      })
      .catch(() => {
        this._subscribing = false;
      });
  }

  /**
   * Apply what the backend pushed.
   *
   * Nothing is fetched here. The payload *is* the new state, computed once on the server
   * for whoever is listening, rather than five queries per panel per change.
   *
   * Held, never dropped, while the user is mid-task: the panel repaints by replacing markup
   * wholesale (ADR-0006), so applying an update over an open dialog or a focused field
   * would discard what was typed and move the caret. A stale number is a smaller wrong than
   * a number that ate what somebody was typing into it. **The History tab's search box is a
   * field like any other**, so a print finishing mid-word is held by the same rule and
   * needs no mechanism of its own.
   */
  _pushed(payload) {
    if (!payload) return;
    if (payload.kind === "printer") this._printer = payload.printer;
    else {
      this._spools = payload.spools;
      this._stock = payload.stock;
      this._reviews = payload.reviews;
      // The unfiltered history: the payload is computed once on the server for everyone
      // listening, so it cannot know this panel's filter row. `_repaint` narrows it again
      // before it is painted.
      this._movements = payload.movements;
      this._trash = payload.trash;
    }
    this._loading = false;
    this._error = null;
    if (this._busy()) {
      this._liveDeferred = true;
      return;
    }
    this._liveDeferred = false;
    this._repaint();
  }

  /**
   * Show what has already arrived.
   *
   * The detail view is the one surface a push cannot fill: it is one spool's whole history,
   * asked for by opening it. Its summary moved in the payload, so it is re-read here — the
   * only fetch left on the live path, and only while that view is open.
   *
   * A narrowed history is the second: the payload carries the whole one, and applying it
   * over an active filter row would quietly widen a view the reader had narrowed. Re-read
   * here for the same reason and on the same terms — only when there is something to
   * narrow, so an unfiltered panel still costs the live path nothing.
   */
  async _repaint() {
    if (this._detail) {
      try {
        this._detail = await this.call("spools/get", { spool_id: this._detail.id });
      } catch {
        // A spool deleted from another browser: fall back to the list rather than an error.
        this._detail = null;
      }
    }
    if (this._filtering()) await this._readHistory();
    this.render();
  }

  /** True while the user is mid-task and a repaint would interrupt them. */
  _busy() {
    if (this._dialog) return true;
    // A tray the user has split is an edit in progress even with nothing focused: the
    // extra charge rows exist only in the DOM, and a repaint would throw them away along
    // with every figure typed into them. A review always arrives with at most one charge
    // per tray, so a second row is always the user's own work. Same judgement as the
    // dialog above — a held update is recoverable, a discarded decision is not.
    if (this.shadowRoot.querySelector(".rv-charge + .rv-charge")) return true;
    // The layered spool picker is a decision half-made: it is appended beside the view
    // rather than kept in `_dialog`, so the check above does not see it, and a repaint
    // would tear it out from under the finger that opened it.
    if (this.shadowRoot.querySelector(".picker-layer")) return true;
    // A spool chosen for a review tray lives only in that row's hidden input until Approve
    // sends it. Nothing is focused once the picker closes, so without this the very next
    // held update would quietly put the frozen (or empty) spool back.
    for (const tray of this.shadowRoot.querySelectorAll(".rv-tray")) {
      const pick = tray.querySelector(".rv-pick");
      if (pick && pick.value !== tray.dataset.frozen) return true;
    }
    const focused = this.shadowRoot.activeElement;
    return Boolean(focused && /^(INPUT|SELECT|TEXTAREA)$/.test(focused.tagName));
  }

  /** Called wherever a dialog closes or an edit ends, to show an update held back. */
  _releaseLive() {
    if (this._liveDeferred && !this._busy()) {
      this._liveDeferred = false;
      this._repaint();
    }
  }

  /**
   * Keep the tab strip usable on a phone, after every render.
   *
   * The panel repaints by replacing `innerHTML` (ADR-0006), so every navigation builds a
   * brand-new strip scrolled hard to the left — on a narrow screen the tab the user just
   * tapped could end up off-screen, highlighted where nobody can see it. Two things fix
   * that, and both have to happen after *every* paint because the nodes are new every
   * time:
   *
   * 1. The active tab is brought into view, centred, **instantly**. A smooth scroll would
   *    animate on every single navigation, which reads as jitter rather than as polish.
   * 2. A fade is shown at whichever end still has tabs beyond it, so the strip admits
   *    there is more to see. `scroll` is listened for on the strip itself — a node that is
   *    discarded with the next `innerHTML` swap, so nothing accumulates — while the
   *    viewport's `resize` goes to the single listener registered in `connectedCallback`,
   *    which calls back through `_paintTabOverflow` and always finds the current strip.
   */
  _syncTabStrip() {
    const nav = this._root?.querySelector("nav");
    if (!nav) return;
    const active = nav.querySelector("button.on");
    if (active) {
      try {
        // `block: "nearest"` so a horizontal correction never scrolls the page vertically.
        active.scrollIntoView({ block: "nearest", inline: "center", behavior: "instant" });
      } catch {
        // An engine with no `scrollIntoView`, or one that rejects `instant` as an unknown
        // enum member, gets the same centring by arithmetic. Caught rather than
        // feature-detected because the failure mode is a thrown `TypeError` from inside
        // `render()`, which would take the whole paint down with it.
        nav.scrollLeft = active.offsetLeft - (nav.clientWidth - active.offsetWidth) / 2;
      }
    }
    nav.addEventListener("scroll", this._onViewportResize, { passive: true });
    this._paintTabOverflow();
  }

  /** Show a fade at each end that still has tabs beyond it. Cheap enough to run on scroll. */
  _paintTabOverflow() {
    const nav = this._root?.querySelector("nav");
    if (!nav) return;
    const furthest = nav.scrollWidth - nav.clientWidth;
    nav.classList.toggle("fade-start", nav.scrollLeft > TAB_FADE_SLACK);
    nav.classList.toggle("fade-end", nav.scrollLeft < furthest - TAB_FADE_SLACK);
  }

  async call(type, payload = {}) {
    return this.hass.callWS({ type: `filament_ledger/${type}`, ...payload });
  }

  async refresh() {
    try {
      this._error = null;
      const [spools, stock, reviews, movements, trash] = await Promise.all([
        this.call("spools/list"),
        this.call("stock"),
        this.call("reviews/list"),
        // Narrowed by whatever the filter row holds, which for an untouched one is nothing
        // at all: the payload is empty and the backend runs the read it always ran.
        this.call("movements", this._filterPayload()),
        this.call("trash"),
      ]);
      this._spools = spools;
      this._stock = stock;
      // The one source of truth for everything review-shaped: the cards, the tab badge
      // and the "n pending" count all read this list.
      this._reviews = reviews;
      // Already newest first from the backend, already joined to spool and job names.
      this._movements = movements;
      // Fetched on every refresh rather than on opening the tab: a correction made in
      // one view has to be visible in the other immediately, and the Trash is
      // human-sized by construction.
      this._trash = trash;
      // The Finished list follows the Trash's policy once it exists at all — an action
      // taken from that tab must be visible there immediately. Null means the tab was
      // never opened, and the refresh keeps it that way rather than paying for a view
      // nobody asked for.
      if (this._finished) this._finished = await this.call("spools/finished");
      if (this._detail) this._detail = await this.call("spools/get", { spool_id: this._detail.id });
    } catch (error) {
      this._error = error.message || String(error);
    } finally {
      this._loading = false;
      this.render();
    }
  }

  async guarded(work) {
    try {
      await work();
      this._dialog = null;
      await this.refresh();
    } catch (error) {
      this._error = error.message || String(error);
      this.render();
    }
  }

  // -- events ------------------------------------------------------------------------

  _onClick(event) {
    const target = event.target.closest("[data-action]");
    if (!target) return;
    const { action, id, slot } = target.dataset;

    switch (action) {
      case "tab":
        this._tab = id;
        this._detail = null;
        this._sync = null;
        // Exactly one command per opening, and none at all for the other tabs: neither
        // surface rides the general refresh, and no timer exists (docs/14 §14.5). The
        // AMS tab takes the same printer snapshot — without the loading flag, because
        // its cards render from the ledger and the snapshot only sharpens their words
        // (see _trayStatus).
        if (id === "printer") this._printerLoading = true;
        if (id === "finished") this._finishedLoading = true;
        if (id === "stats") this._statsLoading = true;
        if (id === "settings") {
          this._settingsLoading = true;
          // The notice belongs to the save that produced it, not to the tab.
          this._settingsSaved = false;
        }
        this.render();
        if (id === "printer" || id === "ams") this._loadPrinter();
        if (id === "finished") this._loadFinished();
        if (id === "stats") this._loadStats();
        if (id === "settings") this._loadSettings();
        break;
      case "refresh-printer":
        this._printerLoading = true;
        this.render();
        this._loadPrinter();
        break;
      case "stats-period":
        // The selection lives on the instance, not in the DOM the next render replaces.
        // Re-picking the period already shown is a deliberate refresh, not a no-op.
        this._statsPeriod = id;
        this._statsLoading = true;
        this.render();
        this._loadStats();
        break;
      case "filters-toggle":
        this._filtersOpen = !this._filtersOpen;
        this.render();
        break;
      case "filters-colour": {
        // Toggled by replacement rather than in place: `_filters` is read on every paint,
        // and a list mutated behind the object it hangs from is a list the next render has
        // no reason to notice.
        const colours = this._filters.colours.includes(id)
          ? this._filters.colours.filter((colour) => colour !== id)
          : [...this._filters.colours, id];
        this._filters = { ...this._filters, colours };
        // Paint the swatch's new state now and the rows when they arrive, exactly as the
        // period buttons do: the control answers immediately, the table catches up.
        this.render();
        this._applyFilters();
        break;
      }
      case "filters-clear":
        // The empty value object, which builds the empty payload, which is the unfiltered
        // read. Clearing is not a command here because it is not one on the wire either.
        this._filters = noHistoryFilters();
        this.render();
        this._applyFilters();
        break;
      case "set-language":
        // A device preference, not ledger state: no backend call, and the panel repaints
        // in the new language immediately (docs/14 §14.6.1).
        writeLanguageOverride(target.dataset.lang);
        this._applyLanguage();
        this.render();
        break;
      case "sync-trays":
        this._syncTrays();
        break;
      case "sync-dismiss":
        this._sync = null;
        this.render();
        break;
      case "sync-register": {
        // The existing create dialog, pre-filled with everything the tray reported —
        // the user confirms the one number the RFID cannot know (docs/06 §6.4).
        const outcome = (this._sync?.slots ?? []).find((o) => String(o.slot) === slot);
        this._dialog = { kind: "new-spool", prefill: outcome ?? null, fromSync: true };
        this.render();
        break;
      }
      case "open":
        this.guarded(async () => {
          this._detail = await this.call("spools/get", { spool_id: id });
        });
        break;
      case "back":
        this._detail = null;
        this.render();
        break;
      case "dismiss-error":
        this._error = null;
        this.render();
        break;
      case "dialog":
        // `spool_id` travels beside the loaded detail so a dialog can also be opened
        // from a place that has no detail loaded — the intent modal's discard path is
        // reached from an inventory card (docs/14 §14.4.3).
        this._dialog = { kind: id, spool: this._detail, spool_id: this._detail?.id };
        this.render();
        break;
      case "reassign":
        // Resolved at render time from `movement_id`, never from a snapshot taken now:
        // a refresh between opening and confirming must change what the modal says.
        this._dialog = { kind: "reassign", movement_id: id };
        this.render();
        break;
      case "void-movement":
        this._dialog = { kind: "void-movement", movement_id: id };
        this.render();
        break;
      case "restore-movement":
        this._dialog = { kind: "restore-movement", movement_id: id };
        this.render();
        break;
      case "spool-actions":
        // The collapsed rail (docs/16 §16.10). It carries the spool's id because the
        // surfaces that offer it — an inventory card, an AMS tray — have no loaded detail
        // to fall back on, and every body it opens resolves its subject from that id.
        this._dialog = { kind: "spool-actions", spool_id: id };
        this.render();
        break;
      case "spool-finish":
        // One action for both densities, so the rail's expanded and collapsed renderings
        // cannot drift into two ways of asking the same thing.
        this._dialog = { kind: "finish", spool_id: id };
        this.render();
        break;
      case "spool-intent":
        // Retirement asks what actually happened, and the two answers are different facts
        // about the world (docs/14 §14.4.3).
        this._dialog = { kind: "spool-intent", spool_id: id };
        this.render();
        break;
      case "intent-discard":
        // "Thrown away" hands over to the existing DISCARD flow, unchanged — pre-set to
        // the whole spool, because that is the question the X asked.
        this._dialog = { kind: "discard", spool_id: id, mode: "whole_spool" };
        this.render();
        break;
      case "intent-delete":
        this.guarded(() => this.call("spools/delete", { spool_id: id }));
        break;
      case "restore-spool":
        this.guarded(() => this.call("spools/restore", { spool_id: id }));
        break;
      case "void-restore-spool":
        // "Restore the spool first" — the branch offered when the grams have nowhere to
        // return to. The modal reopens on the same entry, now able to give them back.
        this._restoreSpoolThenVoid(id);
        break;
      case "close-dialog":
        // The scrim carries `close-dialog` so the dark area closes the dialog. A click
        // *inside* the modal has no nearer [data-action] unless it landed on a button, so
        // it resolves to the scrim too — and must not close anything.
        //
        // The guard lives here, in the dispatcher, because the markup already proved it
        // cannot host this rule safely: the modal used to carry an inline
        // `onclick="event.stopPropagation()"`, which kept in-modal clicks off the scrim by
        // killing the bubble outright — so no click originating inside a dialog ever
        // reached this listener, and every [ Cancel ] in the panel was dead. Submit still
        // worked because it is a different event type, which is exactly why the defect
        // read as if the markup worked. No inline handler may be reintroduced anywhere in
        // this file; they bypass the one dispatch path the panel has.
        if (target.matches(".scrim") && event.target.closest(".modal")) break;
        this._dialog = null;
        this.render();
        // A live update that arrived while this dialog was open was held rather than
        // dropped; the surface is idle again, so let it land.
        this._releaseLive();
        break;
      case "unmount":
        this.guarded(() => this.call("spools/unmount", { spool_id: id }));
        break;
      case "mount-slot":
        // The machine comes off the section the button was drawn in, because that is the
        // only place that knows which printer's tray 3 was tapped. Empty means the section
        // had no name to give, and an absent printer is what the backend resolves.
        this._dialog = {
          kind: "mount",
          slot: Number(slot),
          // Which AMS unit the tapped card belongs to. A machine can have several, and a
          // mount that named none would land every reel in the first (v2.9).
          ams: Number(target.dataset.ams) || null,
          printer: target.dataset.printer || null,
        };
        this.render();
        break;
      case "mount-external":
        // The direct feed: the spool holder beside the AMS that feeds the extruder
        // directly. Same dialog, same picker, no slot — `external` is what the payload
        // carries in the slot's place, and `holder` says which of them on a machine that
        // has two (`_mountPayload`).
        this._dialog = {
          kind: "mount",
          external: true,
          slot: null,
          holder: Number(target.dataset.holder) || null,
          printer: target.dataset.printer || null,
        };
        this.render();
        break;
      case "mount-pick":
        // The tap IS the choice: `guarded` closes the dialog and refreshes on success,
        // and an error keeps the message in the bar — the same contract every form
        // submit in this file follows.
        this.guarded(() => this.call("spools/mount", this._mountPayload(id)));
        break;
      case "open-spool-picker":
        // The picker serves two hosts: the reassign form and one charge row of a review
        // tray. The nearest of the two is the scope the choice is written back into.
        this._openSpoolPicker(target.closest("form, .rv-charge"));
        break;
      case "picker-pick":
        this._pickSpool(id);
        break;
      case "close-picker":
        // Same guard the dialog's scrim carries: a click inside the layered modal that
        // landed on nothing actionable resolves to this scrim and must not close it.
        if (target.matches(".scrim") && event.target.closest(".picker-modal")) break;
        this._closeSpoolPicker();
        break;
      case "review-distribute":
        this._distribute(target.closest(".rv-card"));
        break;
      // The three that edit a tray's attribution in place (docs/06 §6.3). None of them
      // re-renders the view: a render() here would rebuild every card and drop every
      // figure the user has typed into the others.
      case "review-add":
        this._addCharge(target.closest(".rv-tray"));
        break;
      case "review-drop":
        this._dropCharge(target);
        break;
      case "review-rest":
        this._loadRest(target);
        break;
      case "review-approve":
        this._approveReview(target.closest(".rv-card"), id);
        break;
      case "review-dismiss":
        this._dialog = { kind: "dismiss-review", review: this._reviews.find((r) => r.id === id) };
        this.render();
        break;
      case "clear-tag": {
        // The clear affordance for an editable tag: emptying the field is what asks the
        // backend to clear it (null on the wire), so this only has to empty the field.
        // Patched in place rather than re-rendered — a render() here would rebuild the
        // whole dialog and drop everything else the user has typed into it.
        //
        // It is also the release's regression guard for §14.1: an in-modal [data-action]
        // button that is *not* Cancel, dispatching through the one listener. It could not
        // have worked before the inline handler came off the modal.
        const input = this._root.querySelector(".ed-taginput");
        if (input) {
          input.value = "";
          input.focus();
        }
        break;
      }
      default:
        break;
    }
  }

  /**
   * One printer glance (docs/14 §14.5).
   *
   * Called on opening the Printer or AMS tab and on pressing Refresh — so the count
   * of calls is the count of the user's own requests. Reading writes nothing, which is
   * why this deliberately does not go through `guarded`: there is no ledger change for a
   * `refresh()` to pick up.
   */
  async _loadPrinter() {
    try {
      this._printer = await this.call("printer/state");
      this._error = null;
    } catch (error) {
      this._error = error.message || String(error);
    }
    this._printerLoading = false;
    this.render();
  }

  /**
   * One period's statistics (docs/15 §15.6).
   *
   * Called on opening the tab and on every period change, and nowhere else — the figures
   * are a question the user asked, and recomputing them on every ledger refresh would put
   * a full-ledger aggregation behind every button press in the panel. Reading writes
   * nothing, so this deliberately does not go through `guarded`.
   *
   * The period travels as a parameter and is applied **server-side**: filtering in the
   * browser would mean shipping the whole ledger and re-implementing the visibility law
   * of docs/14 §14.4.5 in the one layer this project cannot test (docs/14 §14.8).
   */
  async _loadStats() {
    // A monotonic token, not the period value: in an A→B→A tap sequence the first A's
    // reply is indistinguishable from the current A's by value alone, so a reordered
    // stale payload could land. Only the latest request may write.
    const token = (this._statsRequest = (this._statsRequest || 0) + 1);
    try {
      const stats = await this.call("statistics", { period: this._statsPeriod });
      if (token !== this._statsRequest) return;
      this._stats = stats;
      this._error = null;
    } catch (error) {
      if (token !== this._statsRequest) return;
      this._error = error.message || String(error);
    }
    this._statsLoading = false;
    this.render();
  }

  /**
   * The Finished list, read on opening the tab and nowhere else — the printer glance's
   * terms, for the printer glance's reason: these spools change only when the user
   * changes one, so the read belongs to the moment the tab was opened. Reading writes
   * nothing, so this deliberately does not go through `guarded`.
   */
  async _loadFinished() {
    try {
      this._finished = await this.call("spools/finished");
      this._error = null;
    } catch (error) {
      this._error = error.message || String(error);
    }
    this._finishedLoading = false;
    this.render();
  }

  /** The config entry's four options, read on opening the tab. Readable by anyone. */
  async _loadSettings() {
    try {
      this._settings = await this.call("settings/get");
      this._error = null;
    } catch (error) {
      this._error = error.message || String(error);
    }
    this._settingsLoading = false;
    this.render();
  }

  /**
   * Save the options, which reloads the entry (docs/14 §14.6.4).
   *
   * The saved values are folded into the local copy rather than re-fetched: the write
   * fires the update listener and Home Assistant reloads this integration, so a
   * `settings/get` sent immediately afterwards can land in the window where the entry is
   * not loaded and answer "Filament Ledger is not set up" — an alarming message for an
   * operation that just succeeded. The schema accepted these exact values, so echoing
   * them is not a guess, and the notice tells the user what the reload is.
   */
  async _saveSettings(changes) {
    try {
      await this.call("settings/update", changes);
      this._settings = { ...this._settings, ...changes };
      this._settingsSaved = true;
      this._error = null;
    } catch (error) {
      this._settingsSaved = false;
      this._error = error.message || String(error);
    }
    this.render();
  }

  /**
   * Restore the spool, then reopen the void modal on the same entry.
   *
   * The two are separate commands and the API has no transaction spanning them, so the
   * panel does not pretend otherwise — but the user asked one question, and landing them
   * back on the modal they came from with the restitution branch now available is what
   * answering it looks like.
   */
  async _restoreSpoolThenVoid(spoolId) {
    const movementId = this._dialog?.movement_id;
    try {
      await this.call("spools/restore", { spool_id: spoolId });
    } catch (error) {
      this._error = error.message || String(error);
      this.render();
      return;
    }
    await this.refresh();
    if (movementId) this._dialog = { kind: "void-movement", movement_id: movementId };
    this.render();
  }

  /**
   * Everything a correction modal needs about one entry, from whichever table it was
   * clicked in. Resolved fresh on every render, so a modal left open across a refresh
   * states the current figures rather than the ones it was born with.
   *
   * `retirement` is how the void modal picks its branch (docs/14 §14.4.1). In the global
   * table it is derived from the spool's absence from the overview: that list carries
   * neither discarded nor deleted spools, and a deleted spool's movements are hidden from
   * the global history entirely — so an absent spool there is a discarded one. In the
   * detail view the loaded state says it outright.
   */
  _movementSubject(movementId) {
    const row = this._movements.find((m) => m.movement_id === movementId);
    if (row) {
      const spool = this._spools.find((s) => s.id === row.spool_id);
      return {
        movement_id: row.movement_id,
        amount_g: row.amount_g,
        label: this.movementLabel(row.type),
        type: row.type,
        spool_id: row.spool_id,
        spool_name: row.spool_name,
        retirement: spool ? null : "DISCARDED",
      };
    }
    const line = (this._detail?.history ?? []).find((l) => l.movement_id === movementId);
    if (!line) return null;
    const state = this._detail.state;
    return {
      movement_id: line.movement_id,
      amount_g: line.amount_g,
      label: line.label,
      type: line.type,
      spool_id: this._detail.id,
      spool_name: this._detail.name,
      retirement: state === "DELETED" || state === "DISCARDED" ? state : null,
    };
  }

  _onInput(event) {
    // The History filter row. The value moves to the instance — the DOM it was typed into
    // is replaced on the next paint — and the read is debounced, because a keystroke is not
    // a round trip. `input` rather than `change` covers all four typed controls with one
    // branch: a date picker, a number spinner and a search box all raise it.
    const filter = event.target.closest("[data-filter]");
    if (filter) {
      this._filters = { ...this._filters, [filter.dataset.filter]: filter.value };
      this._debounceFilters();
      return;
    }
    const card = event.target.closest(".rv-card");
    if (card) {
      this._syncReviewCard(card);
      return;
    }
    // The edit dialog's correction section patches itself in place for the same reason
    // the review card does: a render() per keystroke steals the focus mid-number.
    const form = event.target.closest("form[data-form='edit-spool']");
    if (form) this._syncEditForm(form);
    // Same discipline, and the same reason: the reassign modal promises what it is about
    // to send, so the promise has to follow the amount as it is typed.
    const reassign = event.target.closest("form[data-form='reassign']");
    if (reassign) this._syncReassignForm(reassign);
  }

  _onSubmit(event) {
    event.preventDefault();
    const form = event.target;
    const data = Object.fromEntries(new FormData(form).entries());
    // The dialog's own subject wins over the loaded detail: the discard flow is now also
    // reachable from an inventory card, where no detail is loaded (docs/14 §14.4.3).
    const spoolId = this._dialog?.spool_id ?? this._detail?.id;

    switch (form.dataset.form) {
      case "new-spool": {
        const fromSync = Boolean(this._dialog?.fromSync);
        this.guarded(async () => {
          await this.call("spools/create", {
            material: data.material,
            material_other: data.material_other || undefined,
            colour: data.colour,
            opening_weight_g: Number(data.opening_weight_g),
            core_weight_g: Number(data.core_weight_g),
            vendor: data.vendor || null,
            label: data.label || null,
            tag_uid: data.tag_uid || undefined,
            // The one path whose tag came off a tray reading rather than off the
            // keyboard, so the one path that records DETECTED — and the edit dialog
            // then refuses to let that tag drift from the physical spool. Everywhere
            // else the field is omitted and the backend records MANUAL.
            tag_source: fromSync && data.tag_uid ? "DETECTED" : undefined,
          });
          // Registered from the outcome strip: re-run the pass so the new tag mounts
          // and the strip reports the slot as it now is, instead of going stale.
          if (fromSync) this._sync = await this.call("trays/sync");
        });
        break;
      }
      case "edit-spool": {
        const spool = this._detail;
        if (!spool) break;
        const update = {
          spool_id: spool.id,
          // Null reads as "leave unchanged" for every field here except the tag — the
          // shipped command's semantics, kept deliberately (docs/14 §14.2).
          label: data.label || null,
          vendor: data.vendor || null,
          colour: data.colour,
          material: data.material,
          material_other: data.material_other || undefined,
          // Sent only when it actually changed. The wire carries `core_weight_g` rounded
          // to whole grams — the serialiser's rule, because a kitchen scale reads to the
          // gram — so echoing the seeded value back would quietly round a 250.5 g reel to
          // 250 on every unrelated edit. Omitted means unchanged, which is the honest
          // answer for a field the user did not touch. Same discipline as the review
          // card's untouched rows.
          core_weight_g:
            data.core_weight_g === form.dataset.core ? undefined : Number(data.core_weight_g),
        };
        // A DETECTED tag renders no input, so the field is *absent* — which is what
        // leaves it alone. Null, the value an emptied field sends, is what clears an
        // editable one. Absent and null differ here and nowhere else in this command.
        if (spool.tag_source !== "DETECTED") {
          update.tag_uid = (data.tag_uid || "").trim() || null;
          if (data.confirm_duplicate_tag === "on") update.confirm_duplicate_tag = true;
        }
        this._submitEdit(update, this._correctionFrom(data, spool));
        break;
      }
      case "weigh":
        this.guarded(() =>
          this.call("spools/reconcile", {
            spool_id: spoolId,
            measured_g: Number(data.measured_g),
            includes_core: data.includes_core === "on",
            note: data.note || null,
          }),
        );
        break;
      // Finishing a spool is a reconciliation to zero, and deliberately nothing else
      // (docs/06 §6.5). A whole-spool discard would book the remainder as waste, which
      // filament that was printed is not; a consumption would charge a print that never
      // ran. What the user is asserting is a measurement — the reel is empty — so it goes
      // through the measurement path, and the delta that falls out is the accumulated
      // drift of every estimate since the last weighing, recorded where it can be read.
      //
      // `includes_core: false` for the same reason the edit dialog's absolute restatement
      // sends it: zero net is not zero gross. Zero as a *scale reading* would have the
      // reel subtracted from it and reconcile the spool to minus its own core.
      case "finish":
        this.guarded(() =>
          this.call("spools/reconcile", {
            spool_id: spoolId,
            measured_g: 0,
            includes_core: false,
            // Written into the ledger, so it keeps the language of the panel that wrote
            // it — the same rule the edit dialog's correction note follows.
            note: this._t("dlg.finishNote"),
          }),
        );
        break;
      case "adjust":
        this.guarded(() =>
          this.call("spools/adjust", {
            spool_id: spoolId,
            amount_g: Number(data.amount_g),
            reason: data.reason,
          }),
        );
        break;
      case "discard":
        this.guarded(() =>
          this.call("spools/discard", {
            spool_id: spoolId,
            mode: data.mode,
            amount_g: data.mode === "partial" ? Number(data.amount_g) : undefined,
            reason: data.reason,
          }),
        );
        break;
      case "mount":
        this.guarded(() => this.call("spools/mount", this._mountPayload(data.spool_id)));
        break;
      case "dismiss-review":
        this.guarded(() =>
          this.call("reviews/dismiss", {
            review_id: this._dialog.review.id,
            note: data.note || null,
          }),
        );
        break;
      case "reassign":
        this.guarded(() =>
          this.call("movements/reassign", {
            movement_id: this._dialog.movement_id,
            to_spool_id: data.to_spool_id,
            // Omitted when the field still holds the whole charge, so the backend moves
            // the entry's own magnitude at full precision rather than the tenth the
            // field displays — the same rule the review card's untouched trays follow.
            amount_g:
              data.amount_g && data.amount_g !== form.dataset.whole
                ? Number(data.amount_g)
                : undefined,
            note: data.note || null,
          }),
        );
        break;
      case "void-movement":
        this.guarded(() =>
          this.call("movements/void", {
            movement_id: this._dialog.movement_id,
            reason: data.reason || null,
            // Only ever sent as an explicit true, and only from the branch that renders
            // it: the server refuses a restitution void on a retired spool rather than
            // downgrading one silently, so the panel must never guess this flag.
            without_restitution: data.without_restitution === "1" ? true : undefined,
          }),
        );
        break;
      case "restore-movement":
        this.guarded(() =>
          this.call("movements/restore", { movement_id: this._dialog.movement_id }),
        );
        break;
      case "settings":
        // Every field, every time: the command takes any subset, and sending the whole
        // form is what makes "what the tab shows" and "what the entry holds" the same
        // four numbers after a save.
        this._saveSettings({
          default_opening_weight: Number(data.default_opening_weight),
          default_core_weight: Number(data.default_core_weight),
          anomaly_threshold: Number(data.anomaly_threshold),
          auto_mount_on_rfid: data.auto_mount_on_rfid === "on",
        });
        break;
      // Restoring a *spool* needs no form: the Trash row and the deleted spool's detail
      // both carry the whole question in one button, so it dispatches through `_onClick`.
      default:
        break;
    }
  }

  /**
   * Which correction, if any, the edit dialog's weight section asks for (docs/14 §14.2).
   *
   * An **absolute restatement** is a reconciliation, because that is what UC-08 is: making
   * the ledger equal a number the user asserts, with the delta recorded and visible. It is
   * sent with `includes_core: false` — the field asks for remaining *filament*, not for a
   * scale reading, so there is no reel to subtract.
   *
   * A **relative fix** is an adjustment, and adjustments take a reason: an unexplained one
   * is indistinguishable from a bug.
   *
   * Both empty means no correction call at all. Never both: the two fields disable each
   * other while typing, so one movement is the most this dialog can ever write.
   */
  _correctionFrom(data, spool) {
    const stated = (data.set_g || "").trim();
    if (stated !== "") {
      return {
        command: "spools/reconcile",
        payload: {
          spool_id: spool.id,
          measured_g: Number(stated),
          includes_core: false,
          // Written into the ledger, so it keeps the language of the panel that wrote
          // it — exactly like a hand-typed reason, and for the same reason: the note has
          // to be readable by the person who caused it.
          note: this._t("dlg.editCorrectionNote"),
        },
      };
    }
    const delta = (data.delta_g || "").trim();
    if (delta !== "") {
      return {
        command: "spools/adjust",
        payload: {
          spool_id: spool.id,
          amount_g: Number(delta),
          reason: (data.delta_reason || "").trim(),
        },
      };
    }
    return null;
  }

  /**
   * The metadata edit, then the correction — two commands, in that order.
   *
   * They are two independent facts and the API has no transaction that spans them, so the
   * dialog does not pretend otherwise: if the correction is refused, the metadata edit
   * stands and the dialog stays open showing why the movement did not land. `refresh()`
   * clears `_error` on entry, which is why the message is re-applied after it — the dialog
   * must re-render from the *saved* spool, not from the stale one it was opened with.
   */
  async _submitEdit(update, correction) {
    try {
      await this.call("spools/update", update);
    } catch (error) {
      // Nothing was written: the dialog keeps what the user typed, and says why.
      this._error = error.message || String(error);
      this.render();
      return;
    }
    if (!correction) {
      this._dialog = null;
      await this.refresh();
      return;
    }
    try {
      await this.call(correction.command, correction.payload);
      this._dialog = null;
      await this.refresh();
    } catch (error) {
      const message = error.message || String(error);
      await this.refresh();
      this._error = message;
      this.render();
    }
  }

  /**
   * Re-derive the correction section from its own inputs, in place.
   *
   * Two fields say one thing two ways, so whichever the user started, the other steps
   * aside — a disabled input is not submitted, which is how "never both" becomes true of
   * the payload and not merely of the wording. The hint states the movement that will be
   * written, in grams, before anything is sent.
   */
  _syncEditForm(form) {
    const set = form.querySelector(".ed-set");
    const delta = form.querySelector(".ed-delta");
    const reason = form.querySelector(".ed-reason");
    const hint = form.querySelector(".ed-hint");
    if (!set || !delta || !reason || !hint) return;

    const stated = set.value.trim();
    const relative = delta.value.trim();
    set.disabled = relative !== "";
    delta.disabled = stated !== "";
    reason.disabled = relative === "";
    reason.required = relative !== "";

    const t = this._t;
    const current = Number(this._detail?.balance_exact_g ?? 0);
    // `textContent`, not markup: these three carry no tags, and the numbers they
    // interpolate are numbers — so the escaping `t` applies is invisible either way.
    if (stated !== "" && Number.isFinite(Number(stated))) {
      const change = Math.round((Number(stated) - current) * 10) / 10;
      hint.textContent = t("dlg.correctReconcile", {
        delta: signed(change),
        from: current.toFixed(1),
        to: Number(stated).toFixed(1),
      });
    } else if (relative !== "" && Number.isFinite(Number(relative))) {
      const change = Math.round(Number(relative) * 10) / 10;
      hint.textContent = t("dlg.correctAdjust", {
        delta: signed(change),
        after: (current + change).toFixed(1),
      });
    } else {
      hint.textContent = t("dlg.correctNothing");
    }
  }

  // -- rendering ---------------------------------------------------------------------

  /**
   * The one region of the panel that scrolls, or null before the first paint.
   *
   * Queried rather than held, because the element it names is destroyed and rebuilt on
   * every paint (ADR-0006). A field would go stale exactly once per render, which is the
   * hardest kind of stale to notice.
   *
   * It scrolls in both axes and always has: an `overflow-y` of `auto` computes the
   * unspecified `overflow-x` to `auto` as well. The History tab is the first surface to
   * rely on that rather than merely survive it — see the stylesheet.
   */
  get _scroller() {
    return this._root?.querySelector(".view-scroll") ?? null;
  }

  /**
   * Which control in the filter row has focus, and where the caret sits inside it.
   *
   * The row is pinned, not exempt: it is rebuilt with everything else on every paint
   * (ADR-0006), so a paint landing mid-entry destroys the control being used. A push cannot
   * cause one — `_busy()` holds those back while any field has focus — but the filtered
   * read the row itself asks for can, and by construction it always lands mid-entry. So it
   * is put back after the paint, like every other thing this panel measures (docs/16
   * §16.9). It is the only region of the panel that needs this: every other control either
   * lives in a dialog, or patches itself in place precisely so no render can reach it.
   *
   * `data-focus` names a control across paints; `data-filter` names the field it writes.
   * They are separate because the swatches and *Clear filters* have the first and not the
   * second — pressing a button that then vanishes from under the keyboard is the same
   * defect as a stolen caret, arriving through a different door.
   *
   * The selection is read behind a guard rather than a feature test: a number or date input
   * *throws* on `selectionStart` in some engines and answers null in others, and an
   * exception here would take the whole paint down from inside `render()` — the same reason
   * `_syncTabStrip` catches around `scrollIntoView`.
   */
  _focused() {
    const control = this.shadowRoot.activeElement;
    const key = control?.dataset?.focus;
    if (!key) return null;
    try {
      return { key, start: control.selectionStart, end: control.selectionEnd };
    } catch {
      return { key, start: null, end: null };
    }
  }

  _restoreFocus(focused) {
    if (!focused) return;
    const control = this._root.querySelector(`[data-focus="${focused.key}"]`);
    if (!control) return;
    // Without `preventScroll` the browser would scroll the new control into view and undo
    // the position restored a line earlier — the fix would break the thing beside it.
    control.focus({ preventScroll: true });
    if (focused.start === null) return;
    try {
      control.setSelectionRange(focused.start, focused.end);
    } catch {
      // A control with no selection to restore. It has its focus back, which is the half
      // that decides whether the next keystroke lands anywhere.
    }
  }

  render() {
    if (!this._root) return;
    const t = this._t;
    // The entry animation belongs to *arriving somewhere*, not to painting. Every paint
    // replaces the markup wholesale (ADR-0006), so animating unconditionally replayed a
    // half-second fade over the whole view on every update — which is what a live panel
    // looks like when it flickers. Now it runs on a change of view and nowhere else.
    const view = this._detail ? `detail:${this._detail.id}` : this._tab;
    const entering = view !== this._painted;
    this._painted = view;
    // Where the reader had got to, read while the scroller that knows it still exists.
    //
    // One flag governs both halves, because they are the same distinction: arriving
    // somewhere is animated and opens at the top, being repainted where you already are is
    // neither. Without this a push from the backend — a print finishing while somebody is
    // reading row forty — throws them back to the top, and a live panel that does that is
    // worse than one that never updates at all (docs/06 §6.1).
    //
    // Sideways too, and for the same reason: on a phone the ledger is wider than the panel
    // and is panned to reach its last column, so a repaint that reset only the vertical
    // half would leave the reader looking at the columns they had scrolled away from.
    const offset = entering ? 0 : (this._scroller?.scrollTop ?? 0);
    const sideways = entering ? 0 : (this._scroller?.scrollLeft ?? 0);
    const focused = this._focused();
    this._root.innerHTML = `
      ${this.header()}
      <main class="${entering ? "entering" : ""}">
        ${this._error ? this.errorBar() : ""}
        ${this._loading ? this.shell("", `<div class="empty">${t("app.loading")}</div>`) : this.body()}
      </main>
      ${this._dialog ? this.dialog() : ""}
    `;
    // Both of these after the paint and never before: the nodes they measure and move are
    // the ones the line above has just built. The browser clamps the offset to the new
    // maximum on its own, so a repaint that shortened the list lands at its end rather
    // than out of range.
    const scroller = this._scroller;
    if (scroller) {
      scroller.scrollTop = offset;
      scroller.scrollLeft = sideways;
    }
    this._restoreFocus(focused);
    this._syncTabStrip();
    const main = this._root.querySelector("main.entering");
    if (main) this._settleAnimation(main, () => main.classList.remove("entering"));
    const modal = this._root.querySelector(".modal");
    if (modal) this._settleAnimation(modal, () => (modal.style.animation = "none"));
  }

  /**
   * Strip a finished entry animation off the element it decorated.
   *
   * The class stayed on forever, and that was the mobile scroll bug: `fl-view`'s first
   * frame carries a transform, and WebKit refuses touch-scrolling inside an ancestor it
   * still considers animated — so the first view painted on a phone would not pan until
   * some repaint dropped the class. The animation is an arrival, so once it has played
   * the element must be indistinguishable from one that never animated.
   *
   * `animationend` bubbles, and the view is full of shorter child animations (bars, rows)
   * that would end first — the target check is what keeps them from cutting the entry
   * short. The timer is the fallback for the ends that never fire: a tab backgrounded
   * mid-animation, an engine that dropped the event. Both paths converge on `undo`, which
   * must be idempotent — and removing a class or overwriting an inline style is.
   */
  _settleAnimation(el, undo) {
    const done = (event) => {
      if (event && event.target !== el) return;
      el.removeEventListener("animationend", done);
      clearTimeout(timer);
      undo();
    };
    const timer = setTimeout(done, 700);
    el.addEventListener("animationend", done);
  }

  /**
   * The layout shell every view is built from (docs/06 §6.1).
   *
   * Two regions under the header, and only the second one moves: the actions a view offers
   * stay put while its content scrolls beneath them. The panel is used standing at a
   * printer, where reaching a control means scrolling back up one-handed with a failed part
   * in the other hand — so the controls do not go anywhere.
   *
   * **A view with no actions renders no row at all**, rather than an empty one. An action
   * region that is present-but-empty costs its margin on every tab that has nothing to put
   * in it, and vertical space is scarcest on the device this panel exists for.
   *
   * Both arguments are already-safe markup — a view's own template, not wire data — so
   * neither is escaped here. The escaping happens where the data is interpolated, as it
   * does everywhere else in this file.
   */
  shell(actions, content) {
    return `
      ${actions ? `<div class="view-bar">${actions}</div>` : ""}
      <div class="view-scroll">${content}</div>`;
  }

  /**
   * The header: product, who Home Assistant says is standing at the panel, and the tabs.
   *
   * The account line is forward-looking and worth stating (docs/14 §14.6.3): the panel is
   * deliberately not admin-only, because weighing a spool is not an administrative act,
   * so several household users share one surface. Showing the identity readies the ground
   * for actor attribution in v1.1 and costs one line today.
   */
  header() {
    const t = this._t;
    // Only Review carries one, and the distinction is what a badge means. Review is a
    // queue: the number is how many decisions are waiting, acting on them clears it, and
    // reaching zero is the point. *Needs weighing* was neither — it is derived state, it
    // cannot be cleared by working through it, and as a bare number in the same style it
    // claimed to be a queue while answering no question the reader could ask of it. The
    // same figure is already on the Inventory summary card one line below, under the
    // words that say what it counts.
    const badges = {
      review: this._reviews.length ? `<span class="count">${esc(this._reviews.length)}</span>` : "",
    };
    return `
      <header>
        <!-- A strand of filament running the width of the header, travelling slowly. The
             dash pattern is the animation: only stroke-dashoffset moves, so the browser
             never reflows anything to draw it. -->
        <svg class="strand" viewBox="0 0 1200 26" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0 20 C 180 20, 240 6, 420 6 S 700 22, 900 12 S 1100 4, 1200 8"></path>
        </svg>
        <div class="head-top">
          <h1>${t("app.title")}</h1>
          ${this.account()}
        </div>
        <nav>
          ${TABS.map(
            (tab) => `
            <button data-action="tab" data-id="${tab}" class="${this._tab === tab && !this._detail ? "on" : ""}">
              ${t(`tab.${tab}`)}${badges[tab] || ""}
            </button>`,
          ).join("")}
        </nav>
      </header>`;
  }

  account() {
    const user = this._hass?.user;
    if (!user?.name) return "";
    return `<div class="whoami">
      <span class="who-name">${esc(user.name)}</span>
      ${user.is_admin ? `<span class="who-admin">${this._t("app.adminBadge")}</span>` : ""}
    </div>`;
  }

  errorBar() {
    return `<div class="error">
      <span>${esc(this._error)}</span>
      <button data-action="dismiss-error">${this._t("act.dismiss")}</button>
    </div>`;
  }

  body() {
    if (this._detail) return this.detailView();
    if (this._tab === "history") return this.historyView();
    if (this._tab === "stats") return this.statsView();
    if (this._tab === "review") return this.reviewView();
    if (this._tab === "ams") return this.amsView();
    if (this._tab === "printer") return this.printerView();
    if (this._tab === "finished") return this.finishedView();
    if (this._tab === "trash") return this.trashView();
    if (this._tab === "settings") return this.settingsView();
    return this.inventoryView();
  }
}

// The views, one module per tab (www/panel/, ADR-0010). Installed before the element is
// defined, so no instance ever exists without them.
mixIn(
  FilamentLedgerPanel,
  InventoryViews,
  AmsViews,
  HistoryViews,
  StatisticsViews,
  ReviewViews,
  SpoolDetailViews,
  TrashViews,
  PrinterViews,
  SettingsViews,
  DialogViews,
);

// Before the element is defined, not from `connectedCallback`: the faces belong to the document
// and the browser can start fetching them while Home Assistant is still deciding to mount a
// panel. It is the same kind of module-level side effect as the line below it.
installFonts();

// Guarded, because Home Assistant loads a fresh module per integration version while the
// registry keeps the name for the life of the page. Re-defining threw, the throw was the
// only effect, and the tab silently kept rendering with the previous version's class —
// an error that changed nothing except planting a red herring in the log (observed
// 2026-08-31, 2.6.2 → 2.7.0). The guard keeps the same behaviour minus the noise; only a
// page refresh hands the tag to the new class, with or without it.
if (!customElements.get("filament-ledger-panel")) {
  customElements.define("filament-ledger-panel", FilamentLedgerPanel);
}
