/**
 * Filament Ledger — Home Assistant sidebar panel.
 *
 * A plain custom element. No framework, no bundler, no build step: see
 * docs/adr/0006-vanilla-panel.md. It talks to the backend only through the websocket
 * commands in infrastructure/ha/websocket_api.py, and there is no command that sets a
 * balance — changing one requires a movement, and that is the whole design.
 *
 * Styling uses Home Assistant's own CSS custom properties throughout, so light, dark and
 * custom themes work without a line of per-theme code.
 *
 * **Every user-facing string lives in `i18n.js`** (docs/14 §14.6.1). The acceptance
 * criterion is a panel source with zero user-facing literals in this file, and since
 * there is no JavaScript harness to assert it, the rule is enforced by reading: a quoted
 * sentence outside a `t(...)` call is a review finding.
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
  readLanguageOverride,
  resolveLanguage,
  translator,
  writeLanguageOverride,
} from "./i18n.js";

/** The CSS class per confidence level; the words themselves come from the table. */
const CONFIDENCE_CLASS = { HIGH: "high", MEDIUM: "med", LOW: "low" };

const MATERIALS = ["PLA", "PETG", "ABS", "ASA", "TPU", "PC", "PA", "PVA", "SUPPORT", "OTHER"];

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
 * The two entry types that never leave the history the user sees (docs/14 §14.4.1), so
 * the X is never offered on them. The backend refuses both anyway — this is the panel
 * declining to ask a question it already knows the answer to.
 */
const NOT_VOIDABLE = new Set(["OPENING_BALANCE", "VOID_REVERSAL"]);

/**
 * What a figure the printer did not report looks like (docs/14 §14.5).
 *
 * A dash, never a zero: a missing figure is not a figure of zero, and rendering one as
 * the other is exactly the optimistic lie this project exists to prevent.
 */
const DASH = "—";

/**
 * The reserved serial a location carries when the ledger never recorded which machine it
 * meant (`domain/value/identifiers.py`).
 *
 * Mirrored here because the panel has to *label* it — a heading reading `UNIDENTIFIED` over
 * somebody's spools is a code constant leaking onto a screen, and the sentence that belongs
 * there instead is in `i18n.js` like every other. Nothing is ever *sent* as this value: an
 * absent printer travels as an absent field, and the backend resolves it in the one place
 * that owns the sentinel.
 */
const UNIDENTIFIED_PRINTER = "UNIDENTIFIED";

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

/**
 * How long the filter row waits after the last keystroke before it reads.
 *
 * A keystroke is not a round trip. Long enough that typing a word is one query rather than
 * five, short enough that a reader who has stopped typing does not notice waiting.
 */
const FILTER_DEBOUNCE_MS = 300;

/**
 * The three windows the Stats tab offers, in the order it offers them. The values are the
 * backend's own `StatisticsPeriod` (`application/query.py`), so the panel never invents a
 * period the read model does not know — and the labels come from `stats.period<value>`.
 */
const STATS_PERIODS = ["30d", "90d", "all"];

/** The default window, and the one the tab opens on. */
const DEFAULT_STATS_PERIOD = "30d";

/**
 * The height of one bar-chart row in SVG user units — label, value and bar together. The
 * charts carry no `viewBox`, so a user unit is a CSS pixel and this is a real height.
 */
const STATS_BAR_ROW = 34;

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

const grams = (value) => `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 })} g`;
const signed = (value) => `${value < 0 ? "−" : "+"} ${Math.abs(value).toFixed(1)}`;

/**
 * Round to the tenth, which is the precision a single movement is known to.
 *
 * Every gram figure the review card compares goes through this first. Binary floating
 * point makes 300 − 10 − 289.9 a hair away from 0.1, and a remainder that reads `0.0 g`
 * while the Approve button stays disabled is a card calling the user a liar.
 */
const round1 = (value) => Math.round(value * 10) / 10;

/** A typed gram field as a number, or `null` when it is not one. Blank reads as zero. */
const typedGrams = (raw) => {
  const value = raw === "" ? 0 : Number(raw);
  return Number.isFinite(value) && value >= 0 ? value : null;
};

/**
 * The tray one review row is about, read back off the element that rendered it.
 *
 * The three parts travel together because a tray takes all three to name — the review
 * card renders exactly what the backend froze, and the approval sends exactly that back.
 * `ams` and `slot` are numbers on the wire and come out of `dataset` as strings, so they
 * go back as numbers; the schema would coerce them, but a payload that reads as the data
 * it describes is worth the two calls.
 */
const trayRef = (element) => ({
  printer: element.dataset.printer,
  ams: Number(element.dataset.ams),
  slot: Number(element.dataset.slot),
});

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

/**
 * Put an **already-safe** fragment into a `[[token]]` slot of a translated string.
 *
 * `t()` escapes every parameter it substitutes, which is exactly right for raw wire data
 * and exactly wrong for a value that has already been escaped or is deliberately markup —
 * a second pass would print `&amp;` inside somebody's spool name. This is the other door.
 *
 * The replacement is a *function* on purpose: `String.replace` reads `$&`, `` $` `` and
 * friends in a replacement **string** as back-references, and a backtick is not one of
 * the characters `esc()` neutralises. A replacer function has no such syntax.
 *
 * Global, like `t`'s own substitution: a template may name the same value twice, and
 * filling only the first would leave a visible `[[token]]` behind.
 */
const fill = (template, token, value) =>
  template.replace(new RegExp(`\\[\\[${token}\\]\\]`, "g"), () => value);

/**
 * The verbatim `print_error` as the searchable HMS quad — AABB-CCDD-EEFF-GGHH, sixteen
 * hex digits zero-padded from the 64-bit value. HMS codes are searchable; the user
 * diagnosing a failure needs the real string (docs/06 §6.3). The code arrives as a
 * DECIMAL STRING: HMS codes are 64-bit, a JSON number lands in JS as a double, and any
 * value past 2^53 would already be corrupted before BigInt could see it — BigInt(string)
 * is exact at any magnitude. Formatting is display work: the exact decimal string stays
 * untouched in a title attribute. Anything that is not a plain decimal string within 64
 * bits renders as-is — never reformatted, never invented.
 */
function hms(code) {
  if (typeof code !== "string" || !/^[0-9]+$/.test(code)) return String(code);
  const hex = BigInt(code).toString(16).toUpperCase();
  if (hex.length > 16) return code;
  const quad = hex.padStart(16, "0");
  return `HMS ${quad.slice(0, 4)}-${quad.slice(4, 8)}-${quad.slice(8, 12)}-${quad.slice(12, 16)}`;
}

/**
 * The three sizes the coil is drawn at, and the one place their geometry is written down.
 *
 * `box` is the SVG's own coordinate space and the element's rendered size in CSS pixels —
 * the charts already work this way (`STATS_BAR_ROW`), and a viewBox that matches the pixel
 * box means a stroke width is a real width rather than a number to be scaled in the head.
 */
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

const RING_SIZES = {
  card: { box: 106, r: 46, w: 11 },
  slot: { box: 130, r: 62, w: 5 },
  hero: { box: 178, r: 85, w: 6 },
};

/**
 * How much filament is left, drawn as an arc.
 *
 * Hand-rolled SVG, like every other chart in this panel ([ADR-0006](adr/0006-vanilla-panel.md)
 * admits no library). Two circles: the track, and an arc whose `stroke-dashoffset` is the
 * share of the circumference *not* filled. The arc carries the filament's own colour,
 * because colour is the primary identifier ([06 §6.8](../../../docs/06-ui-spec.md)) and the
 * ring is the largest surface on the card for it to occupy.
 *
 * **This is not the Ring/Profile/3D switcher** ([16 §16.6](../../../docs/16-visual-system.md)
 * scopes that out as a new capability). It is how a spool is drawn, from percentage and
 * colour — two values the ledger already holds.
 *
 * `aria-hidden`, deliberately: the percentage sits beside it as text, and a screen reader
 * reading the same figure twice is worse than one that never saw the decoration.
 */
function spoolRing(size, percentage, colour) {
  const { box, r, w } = RING_SIZES[size];
  const mid = box / 2;
  const circumference = Math.round(2 * Math.PI * r);
  const filled = Math.max(0, Math.min(100, Number(percentage) || 0));
  const offset = Math.round(circumference * (1 - filled / 100));
  // `color` as well as `stroke`, so the glow can be `currentColor` and the two can never
  // drift apart into a ring that shines a colour it is not drawn in.
  return `<svg class="ring" viewBox="0 0 ${box} ${box}" aria-hidden="true"
      style="--ring-circ:${circumference};color:${esc(colour)}">
      <circle class="ring-track" cx="${mid}" cy="${mid}" r="${r}" stroke-width="${w}"></circle>
      <circle class="ring-arc" cx="${mid}" cy="${mid}" r="${r}" stroke-width="${w}"
        stroke-dasharray="${circumference}" stroke-dashoffset="${offset}"></circle>
    </svg>`;
}

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
    const key = `loc.${location?.kind}${location?.printer ? suffix : ""}`;
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
   * The AMS ordinal comes from the glance, because there is one per machine and the backend
   * is what says which.
   */
  _traySpace(printer) {
    const space = {};
    if (printer) space.printer = printer;
    const ams = this._printer?.tracking?.ams;
    if (ams) space.ams = ams;
    return space;
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
        // surface rides the general refresh, and no timer exists (docs/14 §14.5).
        if (id === "printer") this._printerLoading = true;
        if (id === "finished") this._finishedLoading = true;
        if (id === "stats") this._statsLoading = true;
        if (id === "settings") {
          this._settingsLoading = true;
          // The notice belongs to the save that produced it, not to the tab.
          this._settingsSaved = false;
        }
        this.render();
        if (id === "printer") this._loadPrinter();
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
          printer: target.dataset.printer || null,
        };
        this.render();
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
   * Called from exactly two places — opening the tab and pressing Refresh — so the count
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
        this.guarded(() =>
          this.call("spools/mount", {
            spool_id: data.spool_id,
            ...this._traySpace(this._dialog.printer),
            slot: this._dialog.slot,
          }),
        );
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
    const badges = {
      inventory: this._stock?.needs_weighing
        ? `<span class="count">${esc(this._stock.needs_weighing)}</span>`
        : "",
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
    const occupied = this._spools
      .filter((s) => s.location.kind === "AMS_SLOT")
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
   * One machine's four trays.
   *
   * `printer` is null only in the one-anonymous-space case above; the mount button then
   * names no printer and the backend resolves the absence, which is the same path a v1
   * automation takes.
   */
  amsSection(printer, named, followed) {
    const t = this._t;
    // A location names its printer, so the match names it too — otherwise a spool an
    // automation mounted into another machine's tray 3 would appear here as though it were
    // in this one's.
    const here = (location) =>
      location.kind === "AMS_SLOT" && (printer === null || location.printer === printer);
    const slots = [1, 2, 3, 4].map((slot) => {
      const spool = this._spools.find((s) => here(s.location) && s.location.slot === slot);
      if (!spool) {
        return `<div class="card tray empty-tray">
          <div class="n">${t("ams.slot", { slot })}</div>
          <div class="muted">${t("ams.empty")}</div>
          <button data-action="mount-slot" data-slot="${slot}"
                  data-printer="${esc(printer ?? "")}">${t("act.mount")}</button>
        </div>`;
      }
      // A tray keeps showing an empty spool: the reel is still physically loaded, and a
      // slot that emptied itself on screen would be a lie about the machine (docs/06 §6.4).
      return `<div class="card tray ${spool.state === "DEPLETED" ? "depleted" : ""}">
        <div class="tray-head">
          <div class="n">${t("ams.slot", { slot })}</div>
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
    });
    return `<div class="ams-space">
      ${named ? this.machineHeading(printer, followed) : ""}
      <div class="trays">${slots.join("")}</div>
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
    const name = unnamed ? t("ams.machineUnnamed") : esc(printer);
    const stale = printer !== null && !followed.has(printer);
    return `<div class="ams-head">
      <h3 class="pr-h">${name}</h3>
      ${stale ? `<p class="muted small">${t("ams.machineStale")}</p>` : ""}
    </div>`;
  }

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

    const rows = this._movements.map((m) => this.historyRow(m)).join("");
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
    const detail = [m.job_name, m.note].filter(Boolean).map(esc).join(" · ");
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
          <td class="what">${esc(row.name)}</td>
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
    const blockedSlots = review.lines
      .filter((line) => !line.charges.length && line.estimated_g !== 0)
      .map((line) => line.slot);
    const blocked = blockedSlots.length > 0;

    return `
      <article class="card rv-card" data-id="${esc(review.id)}">
        <div class="rv-head">
          <span class="rv-ico">${failed ? "⛔" : "⚠"}</span>
          <span class="rv-name">${esc(review.job_name)}</span>
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
        <div class="rv-hint muted small" ${blocked ? "" : "hidden"}>${this._approveHint(blockedSlots)}</div>
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
   */
  reviewTray(line) {
    const t = this._t;
    const frozen = line.charges.length === 1 ? line.charges[0].spool_id : "";
    const charges = line.charges.length
      ? line.charges.map((c) => ({ spool_id: c.spool_id, amount: c.amount_g.toFixed(1) }))
      : // A tray the review froze without a spool still gets a row: the amount is known,
        // the spool is not, and the user is the one who knows which it was (docs/06 §6.3).
        [{ spool_id: "", amount: "" }];

    return `
      <div class="rv-tray" data-printer="${esc(line.printer)}" data-ams="${esc(line.ams)}"
        data-slot="${esc(line.slot)}" data-orig="${esc(line.estimated_g)}"
        data-frozen="${esc(frozen)}">
        <div class="rv-row">
          <span class="rv-slot">${t("ams.slot", { slot: line.slot })}</span>
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
   */
  reviewCharges(charges, frozen) {
    const t = this._t;
    const single = charges.length === 1;
    // Retired spools stay out of the picker, by either route — charging one is refused by
    // the domain (docs/14 §14.4.5). The overview already omits them; the filter is stated
    // so the rule is visible where the picker is read.
    const spools = this._spools.filter((s) => s.state !== "DISCARDED" && s.state !== "DELETED");
    return charges
      .map((charge) => {
        // Named off the *unfiltered* list: a spool retired since the review opened is
        // still the spool this tray froze, and calling it unknown would hide the very
        // fact the user needs in order to understand the refusal that follows.
        const spool = charge.spool_id
          ? this._spools.find((s) => s.id === charge.spool_id)
          : null;
        const named = single && charge.spool_id && charge.spool_id === frozen;
        const who = named
          ? `<span class="rv-dot" style="background:${esc(spool?.colour ?? "transparent")}"></span>
             <span class="rv-spool">${spool ? esc(spool.name) : t("review.unknownSpool")}</span>`
          : `<span class="rv-warn">${charge.spool_id ? "" : "⚠"}</span>
             <span class="rv-pickline">${single ? t("review.whichSpool") : ""}
               <select class="rv-pick">
                 <option value="">${t("review.chooseSpool")}</option>
                 ${spools
                   .map(
                     (s) =>
                       `<option value="${esc(s.id)}" ${s.id === charge.spool_id ? "selected" : ""}>${esc(s.name)} — ${s.balance_g} g</option>`,
                   )
                   .join("")}
               </select>
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
   * Why Approve is disabled, naming the slots (docs/06 §6.3).
   *
   * Built as markup here and as `textContent` in `_syncReviewCard`; the sentence is one
   * key either way, so the two can never say different things about the same card.
   */
  _approveHint(slots) {
    if (!slots.length) return "";
    return fill(this._t("review.blockedHint"), "slots", this._slotList(slots));
  }

  /** The trays a hint is about, as prose: *slot 1 and slot 3*. */
  _slotList(slots) {
    return slots.map((slot) => this._t("review.slotWord", { slot })).join(this._t("act.and"));
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
      if (missing) unattributed.push(tray.dataset.slot);

      const left = round1(amount - attributed);
      if (left !== 0) unbalanced.push(tray.dataset.slot);
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
    // Lists of per-tray entries, not objects keyed by slot: a tray takes three parts to
    // name and a JSON key holds one. Each entry repeats the tray the card rendered, read
    // straight back off the element the review's own line built.
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
        <p class="muted small">${t("printer.pendingSensors")}</p>
      </section>`,
    );
  }

  /** One machine's section: what it is called, what it is doing, and what its trays hold. */
  printerMachine(machine, named) {
    return `<div class="pr-machine">
      ${named ? `<h3 class="pr-h pr-machine-h">${esc(machine.printer)}</h3>` : ""}
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
        ${this.printerFact(t("printer.job"), state.job_name == null ? DASH : esc(state.job_name))}
        ${this.printerFact(t("printer.progress"), progress)}
        ${this.printerFact(t("printer.remaining"), remaining)}
        ${this.printerFact(t("printer.layer"), layer)}
        ${this.printerFact(t("printer.online"), online)}
        ${this.printerFact(
          t("printer.connection"),
          state.connection_mode == null ? DASH : esc(state.connection_mode),
        )}
        ${this.printerFact(
          t("printer.activeTray"),
          state.active_tray == null ? DASH : esc(state.active_tray),
        )}
      </div>`;
  }

  printerFact(key, value) {
    return `<div class="pr-fact"><div class="k">${key}</div><div class="v">${value}</div></div>`;
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
            ? `<p>${t("printer.trackingFollowing", { serials: printers.join(", ") })}</p>`
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

  // -- settings ----------------------------------------------------------------------

  /**
   * The four config-entry options, plus the per-device language (docs/14 §14.6.4).
   *
   * A non-admin sees the same values read-only with a line explaining why: a hidden tab
   * invites "it's broken", while a labelled read-only one teaches the model.
   */
  settingsView() {
    const t = this._t;
    if (this._settingsLoading && !this._settings) {
      return this.shell("", `<div class="empty">${t("app.loading")}</div>`);
    }
    const admin = Boolean(this._hass?.user?.is_admin);
    // No action row: Save belongs to the form it submits, beside the fields it commits.
    return this.shell(
      "",
      `<section class="stack">
        ${this._settings ? this.settingsForm(this._settings, admin) : ""}
        ${this.languageCard()}
      </section>`,
    );
  }

  settingsForm(settings, admin) {
    const t = this._t;
    const ro = admin ? "" : "disabled";
    const field = (name, label, help, value, attrs) => `
      <label>${label}
        <input name="${name}" type="number" ${attrs} value="${esc(value)}" ${ro} required>
        <small>${help}</small>
      </label>`;
    return `
      <form class="card set-card" data-form="settings">
        <h3 class="pr-h">${t("settings.heading")}</h3>
        ${admin ? "" : `<p class="muted small">${t("settings.readOnly")}</p>`}
        ${field(
          "default_opening_weight",
          t("settings.openingWeight"),
          t("settings.openingWeightHelp"),
          settings.default_opening_weight,
          'min="1" max="10000" step="1"',
        )}
        ${field(
          "default_core_weight",
          t("settings.coreWeight"),
          t("settings.coreWeightHelp"),
          settings.default_core_weight,
          'min="0" max="2000" step="1"',
        )}
        ${field(
          "anomaly_threshold",
          t("settings.anomalyThreshold"),
          t("settings.anomalyThresholdHelp"),
          settings.anomaly_threshold,
          'min="1" max="100" step="1"',
        )}
        <label class="row">
          <input name="auto_mount_on_rfid" type="checkbox" ${settings.auto_mount_on_rfid ? "checked" : ""} ${ro}>
          <span class="small">${t("settings.autoMount")}</span>
        </label>
        <small class="muted">${t("settings.autoMountHelp")}</small>
        ${
          admin
            ? `<p class="muted small">${t("settings.reloadWarning")}</p>
               <div class="actions">
                 <button type="submit" class="primary">${t("settings.save")}</button>
               </div>
               ${this._settingsSaved ? `<p class="saved small">${t("settings.saved")}</p>` : ""}`
            : ""
        }
      </form>`;
  }

  languageCard() {
    const t = this._t;
    const current = readLanguageOverride();
    const option = (value, label) =>
      `<button data-action="set-language" data-lang="${value}"
        class="${(current ?? "") === value ? "primary" : ""}">${label}</button>`;
    return `
      <div class="card set-card">
        <h3 class="pr-h">${t("settings.languageHeading")}</h3>
        <div class="bar">
          ${option("", t("settings.languageAuto"))}
          ${option("en", t("settings.languageEn"))}
          ${option("es", t("settings.languageEs"))}
        </div>
        <small class="muted">${t("settings.languageHelp")}</small>
      </div>`;
  }

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
    const options = this._spools
      .filter((s) => s.id !== subject.spool_id && s.state !== "DISCARDED" && s.state !== "DELETED")
      .map((s) => `<option value="${esc(s.id)}">${esc(s.name)} — ${s.balance_g} g</option>`)
      .join("");
    if (!options) {
      return `<h3>${t("dlg.reassignTitle")}</h3>
        <p class="muted">${t("dlg.reassignNone")}</p>
        ${this.formActions(null)}`;
    }
    return `
      <form data-form="reassign" data-whole="${esc(moved)}" data-spool="${esc(subject.spool_name)}">
        <h3>${t("dlg.reassignTitle")}</h3>
        <p class="cx-says rs-says">${t("dlg.reassignSays", {
          grams: moved,
          spool: subject.spool_name,
        })}</p>
        <label>${t("dlg.reassignTo")}<select name="to_spool_id">${options}</select></label>
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

  mountForm() {
    const t = this._t;
    const slot = this._dialog.slot;
    const available = this._spools.filter((s) => s.location.kind !== "AMS_SLOT");
    if (!available.length) {
      return `<h3>${t("dlg.mountTitle", { slot })}</h3>
        <p class="muted">${t("dlg.mountNone")}</p>
        ${this.formActions(null)}`;
    }
    return `
      <form data-form="mount">
        <h3>${t("dlg.mountTitle", { slot })}</h3>
        <label>${t("dlg.mountSpool")}<select name="spool_id">
          ${available.map((s) => `<option value="${esc(s.id)}">${esc(s.name)} — ${s.balance_g} g</option>`).join("")}
        </select></label>
        ${this.formActions(t("act.mount"))}
      </form>`;
  }

  dismissReviewForm() {
    const t = this._t;
    return `
      <form data-form="dismiss-review">
        <h3>${t("dlg.dismissTitle")}</h3>
        <p class="muted">${esc(this._dialog.review?.job_name ?? "")}</p>
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

/**
 * Exported for `styleguide.html`, which adopts this exact sheet into its own shadow roots so
 * the catalogue and the panel cannot drift apart (16 §16.4). Nothing else imports it, and the
 * panel keeps using it directly.
 */
export const STYLES = `
/* ===================================================================================
   The vocabulary (16 §16.3). Every value lives here once. Nothing below hard-codes a
   colour, a radius or a duration, which is what lets a surface written next month match
   one written today without anybody remembering a hex code.

   Names are semantic, never literal: --fl-bad, not --fl-red. The day a warning stops
   being amber, one line changes and nothing reads as a lie.
   =================================================================================== */
:host {
  display: block;
  /* The viewport, not the wrapper. Home Assistant's panel chain hands down a height that
     is not the screen — measured live: a 996px host on an 854px viewport — so trusting
     height:100% left the panel hanging past the bottom edge, the document as the real
     scroller, and .view-scroll swallowing every wheel and touch over the content while
     it had nothing of its own to scroll (its overscroll containment blocks the chain).
     The host always sits at the viewport's top edge — a custom panel draws its own
     header — so the viewport is the one honest reference. dvh tracks the phone's
     retracting browser chrome; the vh line is the fallback for engines without it. */
  height: 100vh;
  height: 100dvh;
  /* Positioned so the ambient layer has something to be absolute against — see .ambient. */
  position: relative;

  /* The panel does not occupy the viewport — it occupies what Home Assistant's sidebar
     leaves of it, and that changes without the viewport changing at all. Declaring the
     host a container is what lets every rule below ask the panel's own width instead
     (16 §16.2). A media query here would be wrong with the sidebar pinned. */
  container-type: inline-size;
  container-name: panel;

  /* The panel renders its own identity and no longer follows the HA theme (ADR-0008).
     Telling the browser so keeps form controls and scrollbars from arriving in light. */
  color-scheme: dark;

  --fl-font-sans: "Space Grotesk", system-ui, sans-serif;
  --fl-font-mono: "IBM Plex Mono", ui-monospace, "Roboto Mono", Menlo, monospace;

  --fl-bg: #05070a;
  --fl-surface: #0b1016;
  --fl-surface-raised: #0e151d;
  --fl-surface-sunken: #080d13;
  --fl-line: #1f2a36;
  --fl-line-soft: #161f2a;
  --fl-line-strong: #2b3947;

  --fl-ink: #e6edf3;
  --fl-ink-bright: #ffffff;
  --fl-ink-dim: #8b9aab;
  --fl-ink-faint: #6d7f91;

  --fl-accent: #00e0c6;
  --fl-accent-bright: #7ff5e7;
  --fl-accent-soft: rgba(0, 224, 198, .16);
  --fl-accent-line: rgba(0, 224, 198, .42);
  --fl-accent-glow: rgba(0, 224, 198, .14);

  --fl-ok: #3ddc84;
  --fl-ok-soft: rgba(61, 220, 132, .14);
  --fl-warn: #ffb340;
  --fl-warn-soft: rgba(255, 179, 64, .14);
  --fl-bad: #ff8fa3;
  --fl-bad-soft: rgba(255, 84, 112, .16);

  --fl-radius-s: 8px;
  --fl-radius-m: 12px;
  --fl-radius-l: 16px;
  --fl-radius-xl: 18px;

  /* The floor for anything tappable (16 §16.6). A labelled button reaches it through its
     padding and never has to say so; an icon-only control has no label to grow its box, so
     the size has to be declared — and declaring it once is what stops the next one from
     picking a number of its own. */
  --fl-tap: 44px;

  --fl-shadow-1: 0 8px 24px rgba(0, 0, 0, .35);
  --fl-shadow-2: 0 14px 40px rgba(0, 0, 0, .4);

  --fl-ease: cubic-bezier(.2, .8, .2, 1);
  --fl-dur-fast: .2s;
  --fl-dur-base: .25s;
  --fl-dur-slow: .55s;

  background: var(--fl-bg);
  color: var(--fl-ink);
}
* { box-sizing: border-box; }

/* ---- The layout shell (06 §6.1) -----------------------------------------------------
   A flex column filling the host. The header, the tab strip and a view's action row are
   rows of it and therefore cannot move; .view-scroll is the only thing in the panel that
   scrolls vertically. (No backticks anywhere in these comments: STYLES is a template
   literal and one would end the stylesheet — 16 §16.9.)

   A definite height, not a minimum: the scroller's flex basis only resolves to a real box
   if the column it sits in has one, and min-height leaves the column content-sized — the
   whole panel would grow past the host again and the document would scroll as one, which
   is what this replaces.

   Home Assistant does supply one, measured rather than assumed: ha-panel-custom and
   partial-panel-resolver carry no styles at all and are therefore display:inline, so they
   are not block containers, and the host's own height:100% resolves past both of them
   against ha-drawer — the viewport's height (16 §16.2). A host that ever stopped supplying
   one degrades to the single-document scroll this replaced rather than to a broken panel,
   which is the whole reason the header keeps a sticky rule it no longer needs here. */
#root { height: 100%; display: flex; flex-direction: column;
  color: var(--fl-ink); font-family: var(--fl-font-sans);
  background:
    radial-gradient(1100px 520px at 82% -8%, rgba(0, 224, 198, .07), transparent 60%),
    radial-gradient(900px 460px at -6% 4%, rgba(131, 35, 255, .06), transparent 58%),
    var(--fl-bg);
  background-attachment: fixed; }

/* A hairline and a wash, not a coloured slab. The header used to be HA's app bar wearing
   the theme's primary colour; it is now part of the same surface as everything under it.

   The flex rule is what pins it; sticky is the fallback for a host that gives no definite
   height, where the shell collapses back to one scrolling document — see #root. z-index
   applies to a flex item whether or not it is positioned, and it is what keeps the strand
   overhanging the header's bottom edge above the content beneath. */
header { background: linear-gradient(180deg, rgba(11, 16, 22, .92), rgba(5, 7, 10, .72));
  backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
  color: var(--fl-ink); padding: 16px 22px 0; flex: none;
  position: sticky; top: 0; z-index: 5;
  border-bottom: 1px solid var(--fl-line-soft); }
header h1 { margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -.02em; }
.head-top { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.whoami { margin-left: auto; display: flex; align-items: center; gap: 7px; font-size: 12.5px;
  color: var(--fl-ink-dim); }
.who-name { font-weight: 500; }
.who-admin { font-size: 10px; letter-spacing: .08em; text-transform: uppercase; font-weight: 700;
  border: 1px solid var(--fl-accent-line); color: var(--fl-accent-bright);
  border-radius: 999px; padding: 1px 7px; }
nav { display: flex; gap: 4px; overflow-x: auto; scrollbar-width: none; padding-bottom: 10px; }
nav::-webkit-scrollbar { display: none; }
nav button { background: transparent; border: 1px solid transparent; cursor: pointer;
  color: var(--fl-ink-dim); font: inherit; font-size: 14px; font-weight: 600;
  padding: 10px 18px; border-radius: 10px; white-space: nowrap;
  transition: color var(--fl-dur-base) var(--fl-ease), background var(--fl-dur-base) var(--fl-ease),
    border-color var(--fl-dur-base) var(--fl-ease), box-shadow var(--fl-dur-base) var(--fl-ease); }
nav button:hover { color: var(--fl-ink); background: rgba(255, 255, 255, .03); }
nav button.on { color: var(--fl-ink-bright); border-color: var(--fl-accent-line);
  background: linear-gradient(180deg, rgba(0, 224, 198, .18), rgba(0, 224, 198, .05));
  box-shadow: 0 0 22px var(--fl-accent-glow), inset 0 1px 0 rgba(255, 255, 255, .06); }

/* The overflow affordance: a fade at whichever end still has tabs beyond it, toggled from
   the strip's own scroll position. A mask rather than an overlay, because a mask is
   painted over the element's box and therefore stays put while the tabs scroll under it —
   a pseudo-element inside a scrolling container would slide away with the content. The
   classes are set by _paintTabOverflow; with neither, nothing is masked at all. Note the
   absence of backticks in this comment: STYLES is itself a template literal, and one
   backtick in here ends it. */
nav.fade-start { -webkit-mask-image: linear-gradient(to right, transparent, #000 26px);
  mask-image: linear-gradient(to right, transparent, #000 26px); }
nav.fade-end { -webkit-mask-image: linear-gradient(to left, transparent, #000 26px);
  mask-image: linear-gradient(to left, transparent, #000 26px); }
nav.fade-start.fade-end {
  -webkit-mask-image: linear-gradient(to right, transparent, #000 26px, #000 calc(100% - 26px), transparent);
  mask-image: linear-gradient(to right, transparent, #000 26px, #000 calc(100% - 26px), transparent); }
nav .count { display: inline-grid; place-items: center; min-width: 18px; height: 18px; padding: 0 5px;
  margin-left: 7px; border-radius: 9px; background: var(--fl-bad); color: #23070d;
  font-family: var(--fl-font-mono); font-size: 11px; font-weight: 700;
  box-shadow: 0 0 14px rgba(255, 84, 112, .35); }

/* The centred column, and the only place its geometry is written down: the pinned action
   row and the scrolling content are both inside it, so the two cannot drift out of
   alignment when a tier changes the padding.

   The safe-area insets ride on the base rule rather than a later override, so a container
   query can restate the padding without a trailing rule quietly winning back three sides.
   The phone's notch is the panel's problem: its venue is somebody standing at a printer.
   The bottom inset is the exception and lives on .view-scroll — see there.

   The width is stated explicitly, because as a flex item an auto cross-size with auto
   margins resolves to fit-content and would shrink the column to its widest card. */
main { padding: 22px max(22px, env(safe-area-inset-right)) 0 max(22px, env(safe-area-inset-left));
  width: 100%; max-width: 1320px; margin: 0 auto;
  flex: 1; min-height: 0; display: flex; flex-direction: column; }
/* Only on arriving at a view. An update that lands while you are reading one must not
   replay it — see render(). */
main.entering { animation: fl-view var(--fl-dur-slow) var(--fl-ease) both; }

/* The gap is the one .stack already uses, so the pinned row sits the same distance from the
   content as the content's own first two rows sit from each other and the seam does not
   announce itself. A view with no actions emits no such row at all — see shell(). */
.view-bar { flex: none; margin-bottom: 16px; }

/* The zero minimum height is the declaration that makes this scroll: a flex item's
   automatic minimum size is its content, so without it the item grows to fit the list and
   overflows the column instead of scrolling inside it.

   Containing the overscroll stops the end of the list chaining into Home Assistant's own
   scrolling and pull-to-refresh, which on a phone reads as the panel being dragged away
   mid-read.

   A stable scrollbar gutter keeps the reserved width constant whether or not a list is long
   enough to scroll, so registering one more spool cannot shift every card sideways. On a
   phone, where scrollbars are overlays, it reserves nothing.

   The bottom inset rides here rather than on main: inside the scroller it is scrolled *to*
   rather than held beneath, so the last card clears the home indicator at the end of the
   list and costs no height before it. */
.view-scroll { flex: 1; min-height: 0; overflow-y: auto;
  overscroll-behavior: contain; scrollbar-gutter: stable;
  padding-bottom: max(22px, env(safe-area-inset-bottom)); }

.stack { display: flex; flex-direction: column; gap: 16px; }
.card { background: linear-gradient(165deg, var(--fl-surface-raised), #0a0f14);
  border-radius: var(--fl-radius-l); box-shadow: var(--fl-shadow-1);
  border: 1px solid var(--fl-line); }
.muted { color: var(--fl-ink-dim); }
.small { font-size: 12.5px; }

.error { display: flex; gap: 12px; align-items: center; background: var(--fl-bad-soft);
  border: 1px solid rgba(255, 84, 112, .4); color: var(--fl-bad);
  padding: 12px 15px; border-radius: var(--fl-radius-m); margin-bottom: 16px; }
.error button { margin-left: auto; background: transparent; color: inherit;
  border: 1px solid currentColor; padding: 5px 12px; border-radius: var(--fl-radius-s);
  cursor: pointer; font: inherit; }

.empty { padding: 56px 20px; text-align: center; color: var(--fl-ink-dim); }
.empty.teach h2 { color: var(--fl-ink); font-weight: 600; margin: 0 0 10px; letter-spacing: -.01em; }
.empty.teach p { max-width: 46ch; margin: 0 auto 14px; line-height: 1.6; }

button { font: inherit; font-size: 14px; font-weight: 500; padding: 9px 16px;
  border-radius: var(--fl-radius-s); border: 1px solid var(--fl-line-strong);
  background: transparent; color: var(--fl-ink-dim); cursor: pointer;
  transition: color var(--fl-dur-fast) var(--fl-ease), border-color var(--fl-dur-fast) var(--fl-ease),
    background var(--fl-dur-fast) var(--fl-ease); }
button:hover { color: var(--fl-ink); border-color: var(--fl-ink-faint); }
button.primary { color: var(--fl-accent-bright); border-color: var(--fl-accent-line); font-weight: 600;
  background: linear-gradient(180deg, rgba(0, 224, 198, .2), rgba(0, 224, 198, .07));
  box-shadow: 0 0 22px var(--fl-accent-glow); }
button.primary:hover { color: var(--fl-ink-bright); border-color: var(--fl-accent); }
button.link { background: none; border: 0; color: var(--fl-accent); padding: 0; align-self: flex-start; }
button.link:hover { color: var(--fl-accent-bright); }
:where(button, input, select, textarea):focus-visible { outline: 2px solid var(--fl-accent);
  outline-offset: 2px; }
.bar { display: flex; gap: 8px; flex-wrap: wrap; }

/* A hairline grid: one background showing through 1px gaps, rather than nine borders that
   have to agree with each other at every corner. */
.summary { display: flex; flex-wrap: wrap; gap: 1px; background: var(--fl-line);
  border-radius: var(--fl-radius-l); overflow: hidden; }
.stat { padding: 18px 22px; flex: 1 1 150px; background: var(--fl-surface); }
.stat .k { font-size: 11px; letter-spacing: .1em; text-transform: uppercase;
  color: var(--fl-ink-faint); font-weight: 700; }
.stat .v { font-family: var(--fl-font-mono); font-size: 28px; font-weight: 600;
  font-variant-numeric: tabular-nums; margin-top: 4px; letter-spacing: -.02em;
  color: var(--fl-ink-bright); }
.stat .v.alert { color: var(--fl-warn); }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(268px, 1fr)); gap: 16px; }
.spool { display: flex; overflow: hidden; cursor: pointer; position: relative;
  transition: transform var(--fl-dur-base) var(--fl-ease), border-color var(--fl-dur-base) var(--fl-ease),
    box-shadow var(--fl-dur-base) var(--fl-ease); }
.spool:hover { transform: translateY(-2px); border-color: var(--fl-accent-line);
  box-shadow: var(--fl-shadow-2), 0 0 26px var(--fl-accent-glow); }
.spool.anomaly { border-left: 3px solid var(--fl-warn); }
/* The swatch is the primary identifier (06 §6.8), so it glows with its own colour rather
   than sitting as a flat strip: the filament colour is data, and it leads. */
.swatch { width: 12px; flex: none; box-shadow: 0 0 18px -2px currentColor; }

/* ---- The coil ---------------------------------------------------------------------
   An arc of the filament's own colour, at three sizes. Geometry lives in RING_SIZES;
   this is only how it is painted. Not the Ring/Profile/3D switcher — 16 §16.6 keeps that
   out as a new capability; this is how a spool is drawn from data the ledger already has. */
.spool-art { position: relative; width: 106px; height: 106px; flex: none; align-self: center;
  margin: 16px 0 16px 16px; }
.tray-art { position: relative; width: 130px; height: 130px; margin: 8px auto 4px; }
.detail-art { position: relative; width: 178px; height: 178px; flex: none; }
/* The winding, behind the arc: a hatch of fine spokes turning slowly. It is what stops a
   100%-full coil from reading as a flat disc of colour. */
.hatch { position: absolute; inset: 6px; border-radius: 50%;
  background: repeating-conic-gradient(from 0deg,
    rgba(255, 255, 255, .06) 0deg 3deg, transparent 3deg 8deg);
  animation: fl-spin 18s linear infinite; }
.ring { display: block; width: 100%; height: 100%; transform: rotate(-90deg); overflow: visible;
  position: relative; }
.ring-track { fill: none; stroke: var(--fl-line); }
.ring-arc { fill: none; stroke: currentColor; stroke-linecap: round;
  filter: drop-shadow(0 0 7px currentColor);
  animation: fl-arc 1.4s var(--fl-ease) both; }
.ring-mid { position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 4px; pointer-events: none; }
.ring-pct { font-family: var(--fl-font-mono); font-size: 21px; font-weight: 600;
  color: var(--fl-ink-bright); letter-spacing: -.02em; }
.ring-pct small { font-size: 11px; color: var(--fl-ink-faint); margin-left: 1px; }
.ring-pct.hero { font-size: 30px; }
.ring-pct.hero small { font-size: 14px; }

/* ---- The spool, face-on -------------------------------------------------------------
   Three layers under the arc, and together they are why a detail view reads as a physical
   reel rather than as a larger progress ring:

   - the body, with the core hole punched out of its middle;
   - the winding — concentric turns of the filament's own colour, masked away from the
     hole and turning slowly, which is what makes the colour read as material rather than
     as a fill;
   - the depth, an inset shadow so the winding sits inside the reel instead of on it.

   The card shows the same spool small, with a spoke hatch instead: at 106px the turns
   would collapse into a moiré, and a texture that fights its own size is worse than none. */
.coil-base { position: absolute; inset: 0; border-radius: 50%;
  background: radial-gradient(circle, #131b24 26%, #0c1218 27%);
  border: 1px solid var(--fl-line); }
.coil-wind { position: absolute; inset: 12px; border-radius: 50%;
  background: repeating-radial-gradient(circle,
    var(--coil) 0 3px, rgba(0, 0, 0, .7) 3px 6px);
  -webkit-mask-image: radial-gradient(circle, transparent 23%, #000 24%);
  mask-image: radial-gradient(circle, transparent 23%, #000 24%);
  animation: fl-spin 22s linear infinite; }
.coil-depth { position: absolute; inset: 12px; border-radius: 50%;
  box-shadow: inset 0 0 30px rgba(0, 0, 0, .9); }
/* The hub: the physical core the filament is wound on, and a second place the colour
   reads at a glance when the arc is nearly empty. */
.ring-hub { width: 34px; height: 34px; border-radius: 50%; display: block;
  box-shadow: 0 0 20px -4px currentColor, inset 0 1px 0 rgba(255, 255, 255, .16);
  border: 2px solid var(--fl-surface); }
.spool-body { padding: 16px 18px; display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
/* The name and the rail's collapsed control share one row, so the control is part of the
   card's grid rather than floating over its corner — see the rail's own block below. */
.spool-head { display: flex; align-items: center; gap: 8px; }
.spool-id { flex: 1; min-width: 0; }
.name { font-weight: 600; letter-spacing: -.01em; }
.sub { font-size: 12.5px; color: var(--fl-ink-dim); }
.big { font-family: var(--fl-font-mono); font-size: 30px; font-weight: 600;
  font-variant-numeric: tabular-nums; margin: 8px 0 4px; letter-spacing: -.03em;
  color: var(--fl-ink-bright); }
.big small { font-family: var(--fl-font-sans); font-size: 13px; font-weight: 500; color: var(--fl-ink-dim); }
.chip { align-self: flex-start; font-size: 11px; letter-spacing: .06em; text-transform: uppercase;
  font-weight: 600; border: 1px solid var(--fl-line-strong); border-radius: 999px; padding: 2px 10px;
  color: var(--fl-ink-dim); }
.barline { display: flex; align-items: center; gap: 9px; }
.track { flex: 1; height: 6px; border-radius: 3px; background: var(--fl-surface-sunken);
  border: 1px solid var(--fl-line-soft); overflow: hidden; }
.track i { display: block; height: 100%; transform-origin: left;
  animation: fl-bar var(--fl-dur-slow) var(--fl-ease) both; }
.pct { font-family: var(--fl-font-mono); font-size: 12px; color: var(--fl-ink-faint);
  font-variant-numeric: tabular-nums; min-width: 34px; text-align: right; }
.foot { display: flex; gap: 7px; align-items: center; margin-top: 8px; font-size: 12.5px; flex-wrap: wrap; }
.cta { color: var(--fl-warn); font-size: 12.5px; font-weight: 600; }

/* Confidence never rides on colour alone — the dot always sits beside its word (06 §6.8).
   The tint is the second signal, not the only one. */
.conf { display: inline-flex; align-items: center; gap: 6px; font-weight: 600;
  border-radius: 999px; padding: 2px 10px 2px 8px; }
.conf i { width: 8px; height: 8px; border-radius: 50%; box-shadow: 0 0 8px currentColor; }
.conf.high { color: var(--fl-ok); background: var(--fl-ok-soft); } .conf.high i { background: var(--fl-ok); }
.conf.med  { color: var(--fl-warn); background: var(--fl-warn-soft); } .conf.med i  { background: var(--fl-warn); }
.conf.low  { color: var(--fl-bad); background: var(--fl-bad-soft); }   .conf.low i  { background: var(--fl-bad); }
/* The anchor line, under the badge it dates (06 §6.5). Quieter than the reason beside the
   chip: it answers "since when", which is context rather than the finding. */
.conf-anchor { margin-top: 5px; font-size: 12px; color: var(--fl-ink-faint); }

.note { background: var(--fl-surface); border: 1px solid var(--fl-line);
  border-left: 3px solid var(--fl-accent); padding: 12px 16px;
  border-radius: 0 var(--fl-radius-m) var(--fl-radius-m) 0; font-size: 13.5px; color: var(--fl-ink-dim); }

.trays { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; }
.tray { padding: 16px; display: flex; flex-direction: column; gap: 5px; }
.tray-head { display: flex; align-items: center; gap: 8px; }
.tray-head .n { flex: 1; min-width: 0; }
.tray .n { font-size: 10.5px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--fl-ink-faint); font-weight: 700; }
.tray .reel { height: 48px; border-radius: var(--fl-radius-s); margin: 6px 0;
  box-shadow: 0 0 22px -6px currentColor, inset 0 1px 0 rgba(255, 255, 255, .12); }
.tray.empty-tray { align-items: center; justify-content: center; text-align: center; gap: 10px;
  border-style: dashed; border-color: var(--fl-line-strong); background: var(--fl-surface-sunken);
  min-height: 190px; }
.tray-actions { display: flex; gap: 6px; margin-top: 8px; }
.tray-actions button { padding: 6px 10px; font-size: 12.5px; flex: 1; }

.detail { display: flex; gap: 16px; padding: 18px; flex-wrap: wrap; }
.detail .meta { flex: 1 1 220px; min-width: 0; }
.detail h2 { margin: 0 0 4px; font-size: 19px; font-weight: 500; }
.facts { font-size: 12.5px; color: var(--fl-ink-dim); }

.ledger-wrap { padding: 16px 18px 18px; }
.ledger-wrap h3 { margin: 0 0 10px; font-size: 11px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--fl-ink-dim); font-weight: 700; }
.scroll { overflow-x: auto; }
table.ledger { width: 100%; border-collapse: collapse; min-width: 460px; }
table.ledger th { text-align: left; font-size: 10.5px; letter-spacing: .09em; text-transform: uppercase;
  color: var(--fl-ink-dim); font-weight: 700; padding-bottom: 8px; border-bottom: 1px solid var(--fl-line); }
table.ledger th.r, table.ledger td.amt, table.ledger td.bal { text-align: right; }
table.ledger td { padding: 9px 0; border-bottom: 1px solid var(--fl-line); vertical-align: top; }
table.ledger td.when { font-size: 12.5px; color: var(--fl-ink-dim); white-space: nowrap; padding-right: 14px; }
table.ledger td.what { font-size: 13.5px; }
table.ledger td.what span { display: block; font-size: 12px; color: var(--fl-ink-dim); }
table.ledger td.amt, table.ledger td.bal { font-family: var(--fl-font-mono);
  font-variant-numeric: tabular-nums; white-space: nowrap; padding-left: 16px; }
table.ledger td.amt { font-weight: 600; }
table.ledger td.amt.minus { color: var(--fl-bad); }
table.ledger td.amt.plus { color: var(--fl-ok); }
table.ledger td.bal { color: var(--fl-ink-dim); }

/* ---- The ledger's column headings, pinned (06 §6.6) ---------------------------------
   Forty rows down, a column of numbers with no heading over it is a column nobody can
   name. Sticky is the whole mechanism; the structure around it is what took the work.

   position: sticky resolves against the nearest ancestor that scrolls, and the wrapper
   this table used to sit in was one. It carried overflow-x: auto for the phone, and CSS
   computes an overflow of visible to auto the moment the other axis is not visible — so
   the wrapper scrolled in BOTH axes, and a sticky heading dutifully stuck to a scrollport
   whose vertical extent never moved. No error, no warning, and a declaration that reads
   as if it should work (16 §16.9).

   So the wrapper is gone from this one table and the shell's own .view-scroll does both
   jobs, which it was already equipped for: its overflow-y: auto has always computed
   overflow-x to auto beside it. The table overflows it and is panned exactly as it was
   panned before, the reader's horizontal position survives a repaint like the vertical
   one, and the headings now pin to a box that actually scrolls under them.

   The card has to grow with the table or the rows would be painted over bare background
   once panned: fit-content wraps the widest of them, and the 100% minimum keeps a card
   full width on a screen where nothing overflows.

   Separated borders, not collapsed: a collapsed border belongs to the table rather than
   to the cell, so it stays behind with the rows while the heading travels — the line under
   the headings simply detaches and scrolls away. With zero spacing and bottom-only borders
   the two render identically, so this costs nothing but a declaration.

   The background is what the rows pass under. It reads as nothing at all at the top of the
   card, where the gradient is this colour, and separates itself as the card darkens beneath
   — which is honest, because by then it is a pinned bar rather than a heading in flow.

   The other ledger tables keep their wrapper. The spool detail's is one card in a stack, so
   panning the region would drag the hero card sideways with it; the Stats table is bounded
   and short. Neither ever puts a reader out of sight of its headings. */
.ledger-wrap.pinned { width: fit-content; min-width: 100%; }
.ledger-wrap.pinned table.ledger { border-collapse: separate; border-spacing: 0; }
.ledger-wrap.pinned table.ledger th { position: sticky; top: 0; z-index: 1;
  background: var(--fl-surface-raised); padding-top: 6px; }

.checksum { margin-top: 12px; padding: 10px 13px; border-radius: 8px; background: var(--fl-surface-sunken);
  font-family: var(--fl-font-mono); font-size: 12.5px; overflow-x: auto;
  white-space: nowrap; color: var(--fl-ink-dim); }
.checksum b { color: var(--fl-ink); }

.sync-strip { padding: 13px 16px; display: flex; flex-direction: column; gap: 7px;
  border-left: 3px solid var(--fl-accent); }
.sync-head { display: flex; align-items: center; gap: 10px; }
.sync-head b { font-weight: 500; }
.sync-dismiss { margin-left: auto; padding: 4px 10px; font-size: 12.5px; }
.sync-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13.5px; }
.sync-row.unknown { font-weight: 500; }
.sync-row button { padding: 4px 10px; font-size: 12.5px; }
.sync-slot { font-size: 10.5px; letter-spacing: .1em; text-transform: uppercase;
  color: var(--fl-ink-dim); font-weight: 700; min-width: 52px; }
.sync-dot { width: 13px; height: 13px; border-radius: 4px; flex: none;
  border: 1px solid var(--fl-line); }

.hist-dot { display: inline-block; width: 13px; height: 13px; border-radius: 4px;
  border: 1px solid var(--fl-line); margin-right: 7px; vertical-align: -2px; }

/* ---- The History filter row (06 §6.6) -----------------------------------------------
   The shell's pinned action region, styled as one line of controls that wraps. It wraps
   rather than scrolls on purpose: a row that scrolled sideways would hide half its own
   controls behind a gesture nobody would think to make, and unlike the tab strip there is
   no active item to scroll back into view. Two or three lines on a phone is a cost paid
   once, against a table it saves the reader from scrolling.

   The search field is the one that grows, because it is the one holding a sentence. */
.hf { align-items: flex-end; gap: 10px 14px; }
/* Rendered away on anything but a phone, where the row is one line and folding it would
   cost a tap to save nothing. The narrow tier below turns it on. */
.hf-toggle { display: none; align-items: center; gap: 8px; }
/* Accent rather than the tab strip's red: a narrowed list is a state the reader chose, not
   a queue demanding attention, and the two must not look alike. */
.hf-count { display: inline-grid; place-items: center; min-width: 18px; height: 18px;
  padding: 0 5px; border-radius: 9px; background: var(--fl-accent-soft);
  border: 1px solid var(--fl-accent-line); color: var(--fl-accent-bright);
  font-family: var(--fl-font-mono); font-size: 11px; font-weight: 700; }
.hf-field { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.hf-k { font-size: 10.5px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--fl-ink-dim); font-weight: 700; }
.hf input { font: inherit; font-size: 14px; padding: 8px 10px; border-radius: 8px;
  border: 1px solid var(--fl-line); background: var(--fl-surface-sunken);
  color: var(--fl-ink); min-width: 0; }
.hf-wide { flex: 1 1 210px; max-width: 380px; }
.hf-search { width: 100%; }
.hf-pair { display: flex; gap: 6px; }
.hf-g { width: 96px; text-align: right; font-variant-numeric: tabular-nums; }
.hf-dots { display: flex; gap: 6px; flex-wrap: wrap; padding: 2px 0; }
/* A swatch and nothing else: the colour is the label, which is why the accessible name is
   the stored value rather than a word we would have had to invent for it. The ring is the
   selected state, drawn outside the swatch so it never covers the colour it is about. */
.hf-dot { width: 28px; height: 28px; padding: 0; flex: none; border-radius: 9px;
  border: 1px solid var(--fl-line-strong); }
.hf-dot.on { border-color: var(--fl-accent);
  box-shadow: 0 0 0 2px var(--fl-accent-soft), 0 0 16px var(--fl-accent-glow); }
/* Disabled because there is nothing to clear, which is a statement rather than a refusal:
   the control stays in place so the row does not reflow the moment a filter is set. */
.hf-clear:disabled { opacity: .45; cursor: default; }
.hf-clear:disabled:hover { color: var(--fl-ink-dim); border-color: var(--fl-line-strong); }
table.ledger td.who { font-size: 13.5px; white-space: nowrap; padding-right: 14px; }
table.ledger td.src { padding-left: 14px; }
.badge { font-size: 10.5px; letter-spacing: .06em; text-transform: uppercase; font-weight: 700;
  border-radius: 999px; padding: 2px 9px; border: 1px solid var(--fl-line);
  color: var(--fl-ink-dim); white-space: nowrap; }
.badge.user { color: var(--fl-accent); border-color: currentColor; }

.rv-card { padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; }
.rv-head { display: flex; align-items: baseline; gap: 9px; }
.rv-ico { flex: none; }
.rv-name { font-weight: 500; min-width: 0; overflow-wrap: anywhere; }
.rv-state { margin-left: auto; font-size: 11px; letter-spacing: .08em; font-weight: 700;
  color: var(--fl-ink-dim); white-space: nowrap; }
.rv-card .sub { font-size: 12.5px; color: var(--fl-ink-dim); }
.rv-hms { font-family: var(--fl-font-mono); }
.rv-est { font-size: 12.5px; color: var(--fl-ink-dim); font-style: italic; }
.rv-nodata { border-left: 3px solid var(--fl-bad); padding: 8px 12px;
  background: var(--fl-surface-sunken); border-radius: 0 8px 8px 0; }
.rv-nodata .t { font-weight: 500; }
.rv-rows { display: flex; flex-direction: column; gap: 6px; margin: 4px 0; }
/* A tray is its figure and the spools it is charged to, stacked: one number on the tray
   line, one row per spool under it. The indent is what says the charges belong to the
   tray above them rather than to the card. */
.rv-tray { display: flex; flex-direction: column; gap: 4px; }
.rv-tray + .rv-tray { border-top: 1px solid var(--fl-line); padding-top: 8px; }
.rv-row { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.rv-tray > .rv-row { justify-content: space-between; }
.rv-charges { display: flex; flex-direction: column; gap: 4px; padding-left: 14px; }
.rv-charge { display: flex; align-items: center; gap: 9px; flex-wrap: wrap;
  font-size: 13.5px; }
.rv-trayfoot { display: flex; align-items: baseline; gap: 10px; padding-left: 14px;
  flex-wrap: wrap; }
.rv-left { margin-left: auto; font-size: 12.5px; color: var(--fl-warn);
  font-variant-numeric: tabular-nums; }
.rv-dot { width: 14px; height: 14px; border-radius: 4px; flex: none;
  border: 1px solid var(--fl-line); }
.rv-warn { flex: none; width: 14px; text-align: center; }
.rv-spool { flex: 1 1 140px; min-width: 0; }
.rv-slot { font-size: 12px; color: var(--fl-ink-dim); white-space: nowrap; }
input.num { font: inherit; font-size: 14px; width: 88px; padding: 6px 9px; border-radius: 8px;
  border: 1px solid var(--fl-line); background: var(--fl-surface-sunken);
  color: var(--fl-ink); text-align: right; font-variant-numeric: tabular-nums; }
/* The picker sits inside its own charge row now, so it stretches rather than claiming a
   line of its own the way it did when the tray and its one spool shared a row. */
.rv-pickline { flex: 1 1 180px; min-width: 0; font-size: 12.5px;
  color: var(--fl-ink-dim); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.rv-charge button.link { align-self: center; font-size: 12.5px; white-space: nowrap; }
.rv-pick { font: inherit; font-size: 13px; padding: 6px 9px; border-radius: 8px;
  border: 1px solid var(--fl-line); background: var(--fl-surface-sunken);
  color: var(--fl-ink); }
.rv-total { align-self: flex-end; font-size: 13px; color: var(--fl-ink-dim);
  border-top: 1px solid var(--fl-line); padding-top: 5px;
  font-variant-numeric: tabular-nums; }
.rv-total b { color: var(--fl-ink); }
.rv-weigh { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13.5px; }
.rv-weigh button { padding: 6px 12px; font-size: 13px; }
.rv-notewrap { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px;
  color: var(--fl-ink-dim); }
.rv-note { font: inherit; font-size: 14px; padding: 7px 10px; border-radius: 8px;
  border: 1px solid var(--fl-line); background: var(--fl-surface-sunken);
  color: var(--fl-ink); }
.rv-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
.rv-actions .primary:disabled { opacity: .45; cursor: not-allowed; }
.rv-hint { text-align: right; }

.scrim { position: fixed; inset: 0; background: rgba(3, 5, 8, .68); display: grid; place-items: center;
  padding: 16px; z-index: 20; backdrop-filter: blur(3px); -webkit-backdrop-filter: blur(3px); }
.modal { background: linear-gradient(160deg, #0f1720, #0a0f15); border: 1px solid var(--fl-line);
  border-radius: var(--fl-radius-xl); padding: 24px; box-shadow: var(--fl-shadow-2);
  width: min(440px, 100%); max-height: 86vh; overflow-y: auto;
  animation: fl-pop var(--fl-dur-slow) var(--fl-ease) both; }
.modal h3 { margin: 0 0 16px; font-size: 18px; font-weight: 600; letter-spacing: -.01em;
  color: var(--fl-ink-bright); }
.modal form { display: flex; flex-direction: column; gap: 12px; }
.modal label { display: flex; flex-direction: column; gap: 5px; font-size: 13px; color: var(--fl-ink-dim); }
.modal label.row { flex-direction: row; align-items: center; gap: 9px; }
.modal input, .modal select { font: inherit; font-size: 15px; padding: 9px 11px; border-radius: 8px;
  border: 1px solid var(--fl-line); background: var(--fl-surface-sunken);
  color: var(--fl-ink); }
.modal input[type=checkbox] { width: auto; }
.modal input[type=color] { padding: 3px; height: 42px; }
.modal small { color: var(--fl-ink-dim); font-size: 12px; }
.modal .actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 6px; }
.modal input:disabled { opacity: .5; cursor: not-allowed; }

.ed-tag { display: flex; flex-direction: column; gap: 5px; }
.ed-tag .k, .ed-corr .k { font-size: 10.5px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--fl-ink-dim); font-weight: 700; }
.ed-tagval { font-family: var(--fl-font-mono); font-size: 15px;
  color: var(--fl-ink); }
.ed-tagrow { display: flex; gap: 8px; align-items: stretch; }
.ed-tagrow input { flex: 1; min-width: 0; }
.ed-tagrow button { padding: 6px 12px; font-size: 13px; white-space: nowrap; }
.ed-corr { display: flex; flex-direction: column; gap: 12px; padding-top: 14px;
  border-top: 1px solid var(--fl-line); }
.ed-corr p { margin: 0; }

/* ---- The spool action rail (16 §16.10) ---------------------------------------------
   One list of what a spool offers, at two densities. Expanded under the hero card in the
   detail view; collapsed to a single control on an inventory card and an AMS tray, where
   there is no room for a labelled row and none should be made.

   It replaces a floating X that sat over a card's corner, outside the layout, and that
   said "retire this spool" one view away from where the same glyph says "delete this
   entry". Nothing here is positioned absolutely: the control is a flex item in the header
   row it belongs to, and every value below is a token.

   The glyph is the one docs/06 §6.5 has drawn since its first draft. It is optically much
   smaller than its tap box, so the box stays transparent until it is wanted rather than
   drawing a permanent button outline into a dense card.

   The negative margins are how the target reaches --fl-tap without costing the height:
   the box overlaps the card's own padding and its neighbours' leading, because a tap
   target is a region of the screen rather than a block that has to reserve room. A tray
   card is four-across on a desktop and would otherwise pay 30px of header for a glyph. */
.spool-menu { flex: none; min-width: var(--fl-tap); min-height: var(--fl-tap); padding: 0;
  display: inline-flex; align-items: center; justify-content: center;
  margin: -10px -9px -10px 0; border-color: transparent; background: transparent;
  color: var(--fl-ink-faint); font-size: 19px; line-height: 1; }
.spool-menu:hover { color: var(--fl-ink); border-color: var(--fl-line-strong);
  background: var(--fl-surface-sunken); }

/* The two that end a spool's life sit at the far end of the expanded rail, apart from the
   four that only correct a number. Auto margin rather than a rule or a gap: it needs no
   element of its own, and it collapses to nothing when the row wraps. */
.sp-life { display: flex; gap: 8px; margin-left: auto; }

/* Destructive, and scoped to the two surfaces that offer it: a bare .danger would also
   catch the history row's X, which is deliberately quiet until it is hovered. */
.sp-rail .danger, .sp-act .danger { color: var(--fl-bad); border-color: var(--fl-bad-soft); }
.sp-rail .danger:hover, .sp-act .danger:hover { border-color: var(--fl-bad);
  background: var(--fl-bad-soft); }

/* One action, with the sentence that says what it does. The rule above each is what makes
   a sheet of these read as a list of decisions rather than as a row of buttons, and it is
   why neither the retirement modal nor the collapsed rail can be answered by reflex. */
.sp-act { display: flex; flex-direction: column; gap: 5px; padding: 11px 0;
  border-top: 1px solid var(--fl-line); }
.sp-act button { align-self: flex-start; }

/* A spool at zero is still a real object (06 §6.2): it sinks to the end of the inventory
   and dims, but it does not leave — and in the AMS view it must not, because the reel is
   still physically in the tray. The swatch keeps full strength: colour is the identifier,
   and an empty spool is the one most worth recognising before reaching for it. */
.spool.depleted .spool-art, .tray.depleted .tray-art { opacity: .45; }
.spool.depleted .big, .tray.depleted .big { color: var(--fl-ink-dim); }

/* Corrections — docs/14 §14.3, §14.4. */
table.ledger td.acts { text-align: right; white-space: nowrap; padding-left: 10px; }
.rowact { padding: 3px 9px; font-size: 13px; line-height: 1.3; margin-left: 4px;
  color: var(--fl-ink-dim); }
.rowact:hover { color: var(--fl-ink); }
.rowact.danger:hover { color: var(--fl-bad);
  border-color: var(--fl-bad); }

/* A voided row is struck through, never omitted: the detail view is the derivation
   surface, and hiding a row there would break the visible closed sum. */
table.ledger tr.voided td.when, table.ledger tr.voided td.what,
table.ledger tr.voided td.amt { text-decoration: line-through; opacity: .6; }
table.ledger tr.voided td.what span { text-decoration: none; }
.chip-void { display: inline-block; margin-left: 7px; font-size: 10px; font-weight: 700;
  letter-spacing: .08em; text-transform: uppercase; text-decoration: none;
  border-radius: 999px; padding: 1px 8px; color: var(--fl-bad);
  border: 1px solid currentColor; vertical-align: 1px; }

.trash-card { padding: 16px 18px 18px; display: flex; flex-direction: column; gap: 8px; }
.trash-card h3 { margin: 0; font-size: 11px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--fl-ink-dim); font-weight: 700; }
.trash-card p { margin: 0 0 4px; }
.trash-row { display: flex; align-items: center; gap: 9px; flex-wrap: wrap;
  padding: 9px 0; border-top: 1px solid var(--fl-line); font-size: 13.5px; }
.trash-name { font-weight: 500; }
.trash-acts { margin-left: auto; display: flex; gap: 6px; align-items: center; }
.trash-acts button { padding: 5px 12px; font-size: 12.5px; }

/* The sentence a correction modal commits to before anything is sent. */
.cx-says { margin: 0; line-height: 1.6; padding: 11px 13px; border-radius: 8px;
  background: var(--fl-surface-sunken); font-size: 14px; }

/* Printer tab — docs/14 §14.5. A glance, not a printer UI. */
.pr-h { margin: 0 0 10px; font-size: 11px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--fl-ink-dim); font-weight: 700; }
.pr-facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
.pr-fact { padding: 13px 18px; border-right: 1px solid var(--fl-line);
  border-bottom: 1px solid var(--fl-line); min-width: 0; }
.pr-fact .k { font-size: 10.5px; letter-spacing: .1em; text-transform: uppercase;
  color: var(--fl-ink-dim); font-weight: 700; }
.pr-fact .v { font-size: 16px; margin-top: 3px; overflow-wrap: anywhere;
  font-variant-numeric: tabular-nums; }
.pr-bar { display: flex; align-items: center; gap: 8px; }
.pr-bar .track { flex: 1; height: 6px; border-radius: 3px; min-width: 40px;
  background: var(--fl-line); overflow: hidden; display: block; }
.pr-bar .track i { display: block; height: 100%; background: var(--fl-accent); }
.pr-error { padding: 11px 15px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  border-left: 3px solid var(--fl-bad); }
/* The accumulated total, with the sentence that keeps it from reading as an odometer.
   The caveat is not fine print here — it is the difference between a fact and a claim, so
   it sits in the same card as the figure and never below the fold. */
.pr-hours { padding: 15px 18px 16px; }
.pr-hours .v { font-size: 26px; font-variant-numeric: tabular-nums; }
.pr-hours p { margin: 8px 0 0; }
/* Which machines are followed, and any this version could not tell apart. Marked with the
   warning rule rather than the error one: nothing is broken — and the card only exists at
   all when there is a second machine to name or one that could not be named. */
.pr-tracking { padding: 15px 18px 16px; border-left: 3px solid var(--fl-warn); }
.pr-tracking p { margin: 0; }
.pr-tracking p + p { margin-top: 8px; }
.pr-trays { display: flex; flex-direction: column; }
.tray .empty-reel { border: 1px dashed var(--fl-line); background: none; }

/* One machine's section of the tab. The gap is the stack's own, so a second machine reads as
   one more block in the same rhythm rather than as a differently-spaced region; the rule
   above it is what makes a long scroll on a phone say *a different machine starts here*, and
   it is absent for the single-machine case where there is nothing to keep apart. */
.pr-machine { display: flex; flex-direction: column; gap: 16px; }
.pr-machine + .pr-machine { padding-top: 16px; border-top: 1px solid var(--fl-line); }
.pr-machine-h { margin-bottom: 0; font-size: 12.5px; letter-spacing: .06em;
  text-transform: none; color: var(--fl-ink); font-family: var(--fl-font-mono); }

/* One machine's four trays on the AMS tab. Same structure, same reason. */
.ams-space { display: flex; flex-direction: column; gap: 10px; }
.ams-head { display: flex; flex-direction: column; gap: 2px; }
.ams-head .pr-h { margin: 0; text-transform: none; letter-spacing: .06em; font-size: 12.5px;
  color: var(--fl-ink); font-family: var(--fl-font-mono); }
.ams-head p { margin: 0; }

/* Settings tab — docs/14 §14.6.4. */
.set-card { padding: 16px 18px 18px; display: flex; flex-direction: column; gap: 12px; }
.set-card label { display: flex; flex-direction: column; gap: 5px; font-size: 13px;
  color: var(--fl-ink-dim); }
.set-card label.row { flex-direction: row; align-items: center; gap: 9px; }
.set-card input { font: inherit; font-size: 15px; padding: 9px 11px; border-radius: 8px;
  border: 1px solid var(--fl-line); background: var(--fl-surface-sunken);
  color: var(--fl-ink); }
.set-card input[type=checkbox] { width: auto; }
.set-card input:disabled { opacity: .6; cursor: not-allowed; }
.set-card small { color: var(--fl-ink-dim); font-size: 12px; }
.set-card .actions { display: flex; justify-content: flex-end; gap: 8px; }
.saved { color: var(--fl-ok); text-align: right; margin: 0; }

/* Statistics tab — docs/06 §6.7, docs/15 §15.6. Every chart here is hand-rolled inline
   SVG (ADR-0006), themed through the same custom properties as the rest of the panel:
   the only colours that are *data* are the filament swatches, which come from the ledger. */
.st-periods { align-items: center; }
.st-periodlabel { font-size: 10.5px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--fl-ink-dim); font-weight: 700; margin-right: 2px; }
.st-period { padding: 6px 14px; font-size: 13px; }
.st-period.on { background: var(--fl-accent); border-color: var(--fl-accent);
  color: #fff; font-weight: 500; }
.st-period:disabled { opacity: .6; cursor: progress; }
.st-card { padding: 16px 18px 18px; display: flex; flex-direction: column; gap: 10px; }
.st-card h3 { margin: 0; font-size: 11px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--fl-ink-dim); font-weight: 700; }
.st-card p { margin: 0; }
.st-time { padding: 0 0 12px; }
.st-time .summary { border-bottom: 1px solid var(--fl-line); }
.st-time p { margin: 10px 18px 0; }

.chart { display: block; overflow: visible; }
.chart .lbl { font-size: 12.5px; fill: var(--fl-ink); }
.chart .val { font-size: 12.5px; fill: var(--fl-ink-dim);
  font-variant-numeric: tabular-nums; }
.chart .trk { fill: var(--fl-line); }
/* The default bar is the theme's own accent; the colour chart overrides it per bar with
   the stored filament colour. The outline is what keeps white filament visible on a light
   card — a swatch with no edge disappears into the background it is meant to sit on. */
.chart .bar { fill: var(--fl-accent); stroke: var(--fl-line);
  stroke-width: 1; }
.chart .seg.ok { fill: var(--fl-ok); }
.chart .seg.warn { fill: var(--fl-warn); }
.chart .seg.bad { fill: var(--fl-bad); }
.seg-bar { border-radius: 7px; overflow: hidden; }
.st-legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12.5px;
  color: var(--fl-ink-dim); }
.st-key { display: inline-flex; align-items: center; gap: 6px; }
.st-key i { width: 9px; height: 9px; border-radius: 2px; }
.st-key.ok i { background: var(--fl-ok); }
.st-key.warn i { background: var(--fl-warn); }
.st-key.bad i { background: var(--fl-bad); }
table.ledger.st-top { min-width: 320px; }
table.ledger.st-top td.what { overflow-wrap: anywhere; }

/* ===================================================================================
   Motion. Decoration, and it says so: under prefers-reduced-motion every animation below
   is cut to a single frame rather than merely shortened, because a user who asked for
   less motion asked for none of this.
   =================================================================================== */
/* ---- Ambient ----------------------------------------------------------------------
   Motes of filament colour drifting up behind the whole panel, and a strand of filament
   running under the header. Both are fixed-cost: transform and stroke-dashoffset only, so
   nothing reflows and nothing repaints outside its own layer.

   The layer is a sibling of #root and never repainted, so a mote keeps its position across
   every navigation instead of snapping back on each paint. */
/* Absolute against the host, never fixed. Fixed escapes to the viewport — measured doing
   exactly that on a real instance, drifting motes across Home Assistant's sidebar — and
   container-type does not reliably contain it. The host is positioned instead, so the
   layer is bounded by the panel by construction rather than by inference.
   (No backticks in here: STYLES is a template literal and one would end it.) */
.ambient { position: absolute; inset: 0; pointer-events: none; z-index: 0; overflow: hidden; }
.ambient i { position: absolute; bottom: -10px; border-radius: 50%; opacity: 0;
  animation-name: fl-float; animation-timing-function: linear;
  animation-iteration-count: infinite; }
#root { position: relative; z-index: 1; }

.strand { position: absolute; left: 0; right: 0; bottom: -1px; width: 100%; height: 26px;
  pointer-events: none; }
.strand path { fill: none; stroke: var(--fl-accent); stroke-width: 1.4;
  stroke-dasharray: 10 14; opacity: .55; animation: fl-dash 9s linear infinite; }

/* A slow sweep of light across a card. Skewed, so it reads as a highlight travelling over
   a surface rather than a bar sliding past. */
.shim { position: absolute; top: -40%; left: -60%; width: 40%; height: 180%;
  pointer-events: none; transform: skewX(-18deg);
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, .05), transparent);
  animation: fl-shim 6s ease-in-out infinite; }
.grid > .spool:nth-child(2n) .shim { animation-delay: 1.4s; }
.grid > .spool:nth-child(3n) .shim { animation-delay: 2.8s; }
.grid > .spool:nth-child(5n) .shim { animation-delay: 4.1s; }

@keyframes fl-float {
  0% { transform: translate3d(0, 0, 0); opacity: 0; }
  12% { opacity: .7; }
  88% { opacity: .5; }
  100% { transform: translate3d(40px, -120vh, 0); opacity: 0; }
}
@keyframes fl-dash { to { stroke-dashoffset: -600; } }
@keyframes fl-shim { to { transform: translateX(260%) skewX(-18deg); } }
@keyframes fl-spin { to { transform: rotate(360deg); } }
@keyframes fl-view { from { opacity: 0; transform: translateY(12px); } }
@keyframes fl-pop { from { opacity: 0; transform: translateY(20px) scale(.97); } }
@keyframes fl-bar { from { transform: scaleX(0); } }
@keyframes fl-row { from { opacity: 0; transform: translateX(-14px); } }
@keyframes fl-arc { from { stroke-dashoffset: var(--ring-circ); } }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .01ms !important;
    scroll-behavior: auto !important;
  }
}

/* ===================================================================================
   Responsive — to the panel, not to the window (16 §16.2).

   Home Assistant's sidebar takes its width from the same viewport this panel lives in, so
   a media query answers the wrong question: a 900px window with the sidebar open leaves
   the panel about 640px, and @media reports 900. Asking the container is what makes
   pinning and collapsing the sidebar reflow the panel with no reload and no JavaScript.
   =================================================================================== */
@container panel (max-width: 600px) {
  main { padding: 14px max(14px, env(safe-area-inset-right)) 0 max(14px, env(safe-area-inset-left)); }
  .view-scroll { padding-bottom: max(14px, env(safe-area-inset-bottom)); }
  /* Fixed chrome is paid for in the dimension a phone has least of, so the pinned row
     keeps the tighter gap the rest of this tier uses. */
  .view-bar { margin-bottom: 12px; }
  .detail { gap: 12px; }
  /* Tighter tabs, never fewer words. Icons in place of labels would buy a few pixels and
     cost the discoverability the whole strip exists for (docs/06 §6.1). */
  header { padding: 12px 14px 0; }
  header h1 { font-size: 19px; }
  /* 44px minimum on anything tappable: the panel is used one-handed, at a printer. */
  nav button { padding: 12px 13px; font-size: 13.5px; }
  nav .count { margin-left: 5px; }
  .st-period { padding: 10px 12px; }
  .grid { grid-template-columns: 1fr; gap: 12px; }
  .stat { flex-basis: calc(50% - 1px); padding: 15px 16px; }
  .stat .v { font-size: 24px; }
  .big { font-size: 26px; }
  .tray-actions button, .trash-acts button, .rowact { min-height: var(--fl-tap); }
  /* The rail wraps at this width anyway, so the two that end a spool's life take a line of
     their own rather than trailing whichever corrective button happened to end a row. */
  .sp-life { flex-basis: 100%; margin-left: 0; }
  /* The tap floor reaches the filter row too, and the search box takes the width it can:
     this row is used one-handed, at a printer, by somebody typing the name of the part that
     failed. Which is also why the row folds here and nowhere else — six controls at that
     size is 336px of chrome against a 373px scroller, and the ledger would be what gave
     way. */
  .hf input, .hf-clear, .hf-toggle { min-height: var(--fl-tap); }
  .hf-toggle { display: inline-flex; }
  .hf.shut { display: none; }
  .hf { margin-top: 10px; }
  .hf-dot { width: var(--fl-tap); height: var(--fl-tap); }
  .hf-wide { flex-basis: 100%; max-width: none; }
  .modal { padding: 18px; }
}

@container panel (min-width: 1000px) {
  main { padding: 28px max(32px, env(safe-area-inset-right)) 0 max(32px, env(safe-area-inset-left)); }
  .view-scroll { padding-bottom: max(80px, env(safe-area-inset-bottom)); }
}
`;

// Before the element is defined, not from `connectedCallback`: the faces belong to the document
// and the browser can start fetching them while Home Assistant is still deciding to mount a
// panel. It is the same kind of module-level side effect as the line below it.
installFonts();

customElements.define("filament-ledger-panel", FilamentLedgerPanel);
