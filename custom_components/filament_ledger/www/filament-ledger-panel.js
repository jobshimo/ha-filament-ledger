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
    // One window listener for the whole lifetime of the element, bound once here so
    // `disconnectedCallback` can remove the very function `connectedCallback` added.
    // The tab strip is recreated on every render; `window` is not, and a listener added
    // per render would accumulate one copy per navigation.
    this._onViewportResize = () => this._paintTabOverflow();
    // Resolved once here so the very first paint is already in the right language; `set
    // hass` re-resolves as soon as the profile is known.
    this._applyLanguage();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._applyLanguage();
      this.refresh();
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
    const key = `loc.${location?.kind}`;
    const label = this._t(key, { slot: location?.slot });
    return label === key ? esc(location?.label ?? "") : label;
  }

  /** *active*, *sealed*, *discarded*, *deleted* — the derived state, in words. */
  stateLabel(state) {
    const key = `state.${state}`;
    const label = this._t(key);
    return label === key ? esc(String(state).toLowerCase()) : label;
  }

  connectedCallback() {
    this.shadowRoot.innerHTML = `<style>${STYLES}</style><div id="root"></div>`;
    this._root = this.shadowRoot.getElementById("root");
    this._root.addEventListener("click", (event) => this._onClick(event));
    this._root.addEventListener("submit", (event) => this._onSubmit(event));
    // Review cards are edited in place — a full re-render per keystroke would steal the
    // focus mid-number — so edits patch the card directly instead of going through render().
    this._root.addEventListener("input", (event) => this._onInput(event));
    // Passive: this listener only reads geometry and toggles two classes, and saying so
    // lets the browser keep scrolling off the main thread.
    window.addEventListener("resize", this._onViewportResize, { passive: true });
    this.render();
  }

  disconnectedCallback() {
    window.removeEventListener("resize", this._onViewportResize);
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
        this.call("movements"),
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
        if (id === "stats") this._statsLoading = true;
        if (id === "settings") {
          this._settingsLoading = true;
          // The notice belongs to the save that produced it, not to the tab.
          this._settingsSaved = false;
        }
        this.render();
        if (id === "printer") this._loadPrinter();
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
      case "spool-intent":
        // The X on a spool asks what actually happened, and the two answers are
        // different facts about the world (docs/14 §14.4.3).
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
        break;
      case "unmount":
        this.guarded(() => this.call("spools/unmount", { spool_id: id }));
        break;
      case "mount-slot":
        this._dialog = { kind: "mount", slot: Number(slot) };
        this.render();
        break;
      case "review-distribute":
        this._distribute(target.closest(".rv-card"));
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
    const card = event.target.closest(".rv-card");
    if (card) {
      this._syncReviewCard(card);
      return;
    }
    // The edit dialog's correction section patches itself in place for the same reason
    // the review card does: a render() per keystroke steals the focus mid-number.
    const form = event.target.closest("form[data-form='edit-spool']");
    if (form) this._syncEditForm(form);
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
          this.call("spools/mount", { spool_id: data.spool_id, slot: this._dialog.slot }),
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

  render() {
    if (!this._root) return;
    const t = this._t;
    this._root.innerHTML = `
      ${this.header()}
      <main>
        ${this._error ? this.errorBar() : ""}
        ${this._loading ? `<div class="empty">${t("app.loading")}</div>` : this.body()}
      </main>
      ${this._dialog ? this.dialog() : ""}
    `;
    // After the paint, never before: the strip being measured has to exist first.
    this._syncTabStrip();
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
    if (!this._spools.length) {
      return `
        <section class="stack">
          ${this.syncStrip()}
          <div class="empty teach">
            <h2>${t("inv.emptyTitle")}</h2>
            <p>${t("inv.emptyBody")}</p>
            <button class="primary" data-action="dialog" data-id="new-spool">${t("inv.emptyCta")}</button>
            <p class="muted small">${t("inv.emptyLoaded")}
              <button class="link" data-action="sync-trays">${t("inv.sync")}</button>
              ${t("inv.emptyLoadedTail")}</p>
          </div>
        </section>`;
    }

    const stat = (key, value, alert) =>
      `<div class="stat"><div class="k">${key}</div><div class="v ${alert ? "alert" : ""}">${value}</div></div>`;

    return `
      <section class="stack">
        <div class="card summary">
          ${stat(t("inv.totalStock"), esc(grams(this._stock?.total_g ?? 0)))}
          ${stat(t("inv.spools"), esc(this._stock?.spool_count ?? 0))}
          ${stat(t("inv.needsWeighing"), esc(this._stock?.needs_weighing ?? 0), this._stock?.needs_weighing)}
        </div>
        <div class="bar">
          <button class="primary" data-action="dialog" data-id="new-spool">${t("inv.newSpool")}</button>
          <button data-action="sync-trays">${t("inv.sync")}</button>
        </div>
        ${this.syncStrip()}
        <div class="grid">${this._spools.map((s) => this.spoolCard(s)).join("")}</div>
      </section>`;
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
    const bar = sealed
      ? `<span class="chip">${t("inv.sealed")}</span>`
      : `<div class="barline">
           <div class="track"><i style="width:${spool.percentage}%;background:${esc(spool.colour)}"></i></div>
           <span class="pct">${spool.percentage}%</span>
         </div>`;
    return `
      <article class="card spool ${spool.has_anomaly ? "anomaly" : ""}" data-action="open" data-id="${esc(spool.id)}">
        <div class="swatch" style="background:${esc(spool.colour)}"></div>
        <div class="spool-body">
          <button class="card-x" data-action="spool-intent" data-id="${esc(spool.id)}"
            title="${t("inv.removeSpool")}">×</button>
          <div class="name">${esc(spool.name)}</div>
          <div class="sub">${esc(spool.material)}${spool.vendor ? ` · ${esc(spool.vendor)}` : ""}</div>
          <div class="big">${spool.balance_g}<small> g</small></div>
          ${bar}
          <div class="foot">
            ${this.confidenceChip(spool.confidence)}
            <span class="muted">· ${this.locationLabel(spool.location)}</span>
          </div>
          ${spool.needs_weighing ? `<div class="cta">${t("inv.weighThis")}</div>` : ""}
        </div>
      </article>`;
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

  // -- AMS ---------------------------------------------------------------------------

  amsView() {
    const t = this._t;
    const slots = [1, 2, 3, 4].map((slot) => {
      const spool = this._spools.find(
        (s) => s.location.kind === "AMS_SLOT" && s.location.slot === slot,
      );
      if (!spool) {
        return `<div class="card tray empty-tray">
          <div class="n">${t("ams.slot", { slot })}</div>
          <div class="muted">${t("ams.empty")}</div>
          <button data-action="mount-slot" data-slot="${slot}">${t("act.mount")}</button>
        </div>`;
      }
      return `<div class="card tray">
        <div class="n">${t("ams.slot", { slot })}</div>
        <div class="reel" style="background:${esc(spool.colour)}"></div>
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

    return `
      <section class="stack">
        <div class="note">${t("ams.note")}</div>
        <div class="trays">${slots.join("")}</div>
      </section>`;
  }

  // -- history -----------------------------------------------------------------------

  historyView() {
    const t = this._t;
    if (!this._movements.length) {
      return `
      <div class="empty teach">
        <h2>${t("history.emptyTitle")}</h2>
        <p>${t("history.emptyBody")}</p>
        <p class="muted">${t("history.emptyFoot")}</p>
      </div>`;
    }

    const rows = this._movements.map((m) => this.historyRow(m)).join("");
    return `
      <section class="stack">
        <div class="card ledger-wrap">
          <h3>${t("history.heading")}</h3>
          <div class="scroll">
            <table class="ledger">
              <thead><tr>
                <th>${t("history.colWhen")}</th><th>${t("history.colSpool")}</th>
                <th>${t("history.colEntry")}</th><th class="r">${t("history.colAmount")}</th>
                <th>${t("history.colSource")}</th><th class="r">${t("history.colCorrect")}</th>
              </tr></thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
          <p class="muted small">${t("history.foot", { count: this._movements.length })}</p>
        </div>
      </section>`;
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
    if (!stats) {
      // Nothing yet, for one of two reasons. While the first read is in flight, say so;
      // if it failed, the error bar above has already said what happened, and the period
      // buttons stay live so trying again is one tap rather than a tab round-trip.
      return `<section class="stack">
        ${this.statsPeriods()}
        ${this._statsLoading ? `<div class="empty">${t("app.loading")}</div>` : ""}
      </section>`;
    }

    if (stats.empty) {
      return `
        <section class="stack">
          ${this.statsPeriods()}
          <div class="empty teach">
            <h2>${t("stats.emptyTitle")}</h2>
            <p>${t("stats.emptyBody")}</p>
            <p class="muted small">${t("stats.emptyFoot")}</p>
          </div>
        </section>`;
    }

    return `
      <section class="stack">
        ${this.statsPeriods()}
        ${this.statsTotals(stats)}
        ${this.statsPrintTime(stats.print_time)}
        ${this.statsChart(t("stats.byColour"), this.statsColourRows(stats.by_colour))}
        ${this.statsChart(t("stats.byMaterial"), this.statsMaterialRows(stats.by_material))}
        ${this.statsOutcomes(stats)}
        ${this.statsTopPrints(stats.top_prints)}
        <p class="muted small">${t("stats.foot")}</p>
      </section>`;
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
      return `
      <div class="empty teach">
        <h2>${t("review.emptyTitle")}</h2>
        <p>${t("review.emptyBody")}</p>
        <p class="muted">${t("review.emptyFoot")}</p>
      </div>`;
    }

    // Newest first (docs/06 §6.3): the backend serves oldest first, the card stack leads
    // with the doubt the user most recently created. ISO timestamps sort lexically.
    const cards = this._reviews
      .slice()
      .sort((a, b) => String(b.opened_at).localeCompare(String(a.opened_at)))
      .map((review) => this.reviewCard(review))
      .join("");
    return `
      <section class="stack">
        <div class="muted">${t("review.pending", { count: this._reviews.length })}</div>
        ${cards}
      </section>`;
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

    const rows = review.lines.map((line) => this.reviewRow(line)).join("");
    const total =
      review.lines.length > 1
        ? `<div class="rv-total">${t("review.total", {
            grams: review.estimated_total_g.toFixed(1),
          })}</div>`
        : "";

    // Approve starts disabled whenever a non-zero row has no spool — the button and the
    // domain rule (02 §2.3) must never disagree about what is legal.
    const blockedSlots = review.lines
      .filter((line) => !line.spool_id && line.estimated_g !== 0)
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

  reviewRow(line) {
    const t = this._t;
    const spool = line.spool_id ? this._spools.find((s) => s.id === line.spool_id) : null;
    const amount = `
      <span class="rv-slot">${t("ams.slot", { slot: line.slot })}</span>
      <input class="rv-amt num" type="number" min="0" step="0.1" value="${esc(line.estimated_g.toFixed(1))}"> g`;

    if (line.spool_id) {
      return `
        <div class="rv-row" data-slot="${esc(line.slot)}" data-orig="${esc(line.estimated_g)}">
          <span class="rv-dot" style="background:${esc(spool?.colour ?? "transparent")}"></span>
          <span class="rv-spool">${spool ? esc(spool.name) : t("review.unknownSpool")}</span>
          ${amount}
        </div>`;
    }

    // A slot the review froze without a spool is shown, never hidden (docs/06 §6.3): the
    // amount is known, the spool is not, and the user is the one who knows which it was.
    // Retired spools stay out of the picker, by either route — charging one is refused by
    // the domain (docs/14 §14.4.5). The overview already omits them; the filter is stated
    // so the rule is visible where the picker is read.
    const options = this._spools
      .filter((s) => s.state !== "DISCARDED" && s.state !== "DELETED")
      .map((s) => `<option value="${esc(s.id)}">${esc(s.name)} — ${s.balance_g} g</option>`)
      .join("");
    return `
      <div class="rv-row unresolved" data-slot="${esc(line.slot)}" data-orig="${esc(line.estimated_g)}">
        <span class="rv-warn">⚠</span>
        <span class="rv-spool muted">${t("review.noSpoolRecorded")}</span>
        ${amount}
        <div class="rv-pickline">${t("review.whichSpool")}
          <select class="rv-pick"><option value="">${t("review.chooseSpool")}</option>${options}</select>
        </div>
      </div>`;
  }

  /**
   * Why Approve is disabled, naming the slots (docs/06 §6.3).
   *
   * Built as markup here and as `textContent` in `_syncReviewCard`; the sentence is one
   * key either way, so the two can never say different things about the same card.
   */
  _approveHint(slots) {
    if (!slots.length) return "";
    const list = slots
      .map((slot) => this._t("review.slotWord", { slot }))
      .join(this._t("act.and"));
    return fill(this._t("review.blockedHint"), "slots", list);
  }

  /** Re-derive the card's total, hint and Approve state from its inputs, in place. */
  _syncReviewCard(card) {
    let total = 0;
    let invalid = false;
    const unattributed = [];
    for (const row of card.querySelectorAll(".rv-row")) {
      const raw = row.querySelector(".rv-amt").value;
      const value = raw === "" ? 0 : Number(raw);
      if (!Number.isFinite(value) || value < 0) {
        invalid = true;
        continue;
      }
      total += value;
      const pick = row.querySelector(".rv-pick");
      if (pick && !pick.value && value !== 0) unattributed.push(row.dataset.slot);
    }

    const totalEl = card.querySelector(".rv-total b");
    if (totalEl) totalEl.textContent = total.toFixed(1);

    const blocked = invalid || unattributed.length > 0;
    card.querySelector(".rv-approve").disabled = blocked;
    const hint = card.querySelector(".rv-hint");
    hint.hidden = !blocked;
    hint.textContent = unattributed.length
      ? this._approveHint(unattributed)
      : this._t("review.invalidAmounts");
  }

  /**
   * Split the weighed total across the rows in the same proportion as the frozen
   * estimates (docs/06 §6.3) — a click, not arithmetic. With one row it replaces the
   * value outright, which is the same rule with one term. When every estimate is zero —
   * the no-data card — the spec names no proportion, so the split is even: the honest
   * default when nothing distinguishes the slots.
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

    const rows = [...card.querySelectorAll(".rv-row")];
    const basis = rows.map((row) => Number(row.dataset.orig) || 0);
    const basisTotal = basis.reduce((a, b) => a + b, 0);
    const shares =
      basisTotal > 0 ? basis.map((b) => b / basisTotal) : basis.map(() => 1 / rows.length);

    const round1 = (value) => Math.round(value * 10) / 10;
    let cumShare = 0;
    let cumRounded = 0;
    rows.forEach((row, i) => {
      cumShare += shares[i];
      // The last row closes on exactly 1, so float drift in the running share can never
      // leave the sum a tenth short of — or past — what the scale read.
      const next = round1(total * (i === rows.length - 1 ? 1 : cumShare));
      row.querySelector(".rv-amt").value = round1(next - cumRounded).toFixed(1);
      cumRounded = next;
    });
    this._syncReviewCard(card);
  }

  /**
   * Approve with only the overrides the user actually changed: `amounts` carries a slot
   * only when its value differs from what the card DISPLAYED — the estimate seeded into
   * the input at one decimal — and `assign` only the pickers with a choice. The
   * comparison must round `data-orig` the same way the seed did (`toFixed(1)`):
   * `data-orig` keeps the full-precision estimate for Distribute's basis, and comparing
   * the one-decimal input against it would flag every untouched row as edited whenever
   * the estimate carries sub-0.1 g precision, silently replacing the frozen estimate
   * with its rounded display value server-side. Untouched rows are omitted, so the
   * backend charges the full-precision frozen estimate. An input cleared to empty reads
   * as 0, sent iff 0 differs from the displayed seed — clearing a non-zero row is a
   * deliberate "this slot consumed nothing". JSON object keys are strings; the schema's
   * Coerce(int) reads them as slots.
   */
  _approveReview(card, reviewId) {
    const payload = { review_id: reviewId };
    const amounts = {};
    const assign = {};
    for (const row of card.querySelectorAll(".rv-row")) {
      const raw = row.querySelector(".rv-amt").value;
      const value = raw === "" ? 0 : Number(raw);
      const seeded = Number(Number(row.dataset.orig).toFixed(1));
      if (Number.isFinite(value) && value >= 0 && value !== seeded) {
        amounts[row.dataset.slot] = value;
      }
      const pick = row.querySelector(".rv-pick");
      if (pick && pick.value) assign[row.dataset.slot] = pick.value;
    }
    if (Object.keys(amounts).length) payload.amounts = amounts;
    if (Object.keys(assign).length) payload.assign = assign;
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

    return `
      <section class="stack">
        <button class="link" data-action="back">${t("detail.back")}</button>
        <div class="card detail">
          <div class="reel-big" style="background:${esc(spool.colour)}"></div>
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
            <div class="foot">${this.confidenceChip(spool.confidence, true)}</div>
          </div>
        </div>

        ${
          deleted
            ? `<div class="note">
                 ${t("detail.deletedNote")}
                 <div class="bar" style="margin-top:10px">
                   <button class="primary" data-action="restore-spool" data-id="${esc(spool.id)}">${t("detail.restoreSpool")}</button>
                 </div>
               </div>`
            : `<div class="bar">
                 <button class="primary" data-action="dialog" data-id="weigh">${t("detail.weigh")}</button>
                 <button data-action="dialog" data-id="adjust">${t("detail.adjust")}</button>
                 <button data-action="dialog" data-id="discard">${t("act.discard")}</button>
                 <button data-action="dialog" data-id="edit-spool">${t("detail.edit")}</button>
                 <button data-action="spool-intent" data-id="${esc(spool.id)}">${t("detail.remove")}</button>
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
      </section>`;
  }

  // -- trash -------------------------------------------------------------------------

  trashView() {
    const t = this._t;
    const trash = this._trash;
    const spools = trash?.spools ?? [];
    const movements = trash?.movements ?? [];
    if (!spools.length && !movements.length) {
      return `
      <div class="empty teach">
        <h2>${t("trash.emptyTitle")}</h2>
        <p>${t("trash.emptyBody")}</p>
      </div>`;
    }

    return `
      <section class="stack">
        ${spools.length ? this.trashSpools(spools) : ""}
        ${movements.length ? this.trashMovements(movements) : ""}
      </section>`;
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
      return `<div class="empty">${t("app.loading")}</div>`;
    }
    const state = this._printer;
    if (!state || state.dormant) {
      // The honest no-printer answer, in the voice the sync strip already speaks.
      return `
        <div class="empty teach">
          <h2>${t("printer.dormantTitle")}</h2>
          <p>${t("printer.dormantBody")}</p>
          <p class="muted small">${t("printer.dormantFoot")}</p>
        </div>`;
    }

    return `
      <section class="stack">
        <div class="bar">
          <button data-action="refresh-printer" ${this._printerLoading ? "disabled" : ""}>${t("printer.refresh")}</button>
        </div>
        ${this.printerFacts(state)}
        ${this.printerError(state.error)}
        ${this.printerTrays(state.trays ?? [])}
        <p class="muted small">${t("printer.readOnly")}</p>
        <p class="muted small">${t("printer.pendingSensors")}</p>
      </section>`;
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
    return `
      <div class="card pr-facts">
        ${this.printerFact(t("printer.status"), state.status == null ? DASH : esc(state.status))}
        ${this.printerFact(t("printer.job"), state.job_name == null ? DASH : esc(state.job_name))}
        ${this.printerFact(t("printer.progress"), progress)}
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
   * The four-tray strip: what the printer reports beside what the ledger has mounted.
   *
   * The per-slot shapes are the sync command's, computed read-only — this tab never runs
   * `DetectSpool`, so looking at it changes nothing (docs/14 §14.5).
   */
  printerTrays(trays) {
    const t = this._t;
    if (!trays.length) return `<div class="note">${t("printer.noTrays")}</div>`;
    const cards = trays
      .map((tray) => {
        const mounted = this._spools.find(
          (s) => s.location.kind === "AMS_SLOT" && s.location.slot === tray.slot,
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
      return `<div class="empty">${t("app.loading")}</div>`;
    }
    const admin = Boolean(this._hass?.user?.is_admin);
    return `
      <section class="stack">
        ${this._settings ? this.settingsForm(this._settings, admin) : ""}
        ${this.languageCard()}
      </section>`;
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
      discard: () => this.discardForm(),
      mount: () => this.mountForm(),
      "dismiss-review": () => this.dismissReviewForm(),
      "edit-spool": () => this.editSpoolForm(),
      reassign: () => this.reassignForm(),
      "void-movement": () => this.voidMovementForm(),
      "restore-movement": () => this.restoreMovementForm(),
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
    // colour, name and tag are what the tray reported; the opening weight stays the
    // user's to confirm — the one number the RFID cannot know.
    const hint = this._dialog?.prefill ?? null;
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
          <input name="opening_weight_g" type="number" step="0.1" min="1" value="${defaults.opening_weight_g}" required>
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
   * figures it prints are the ones the ledger will hold: both legs are |amount| of the
   * entry named in `movement_id`, to one decimal, which is the precision a single
   * movement is known to.
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
      <form data-form="reassign">
        <h3>${t("dlg.reassignTitle")}</h3>
        <p class="cx-says">${t("dlg.reassignSays", {
          grams: moved,
          spool: subject.spool_name,
        })}</p>
        <label>${t("dlg.reassignTo")}<select name="to_spool_id">${options}</select></label>
        <label>${t("act.note")}<input name="note" placeholder="${t("act.optional")}"></label>
        <p class="muted small">${t("dlg.reassignFoot")}</p>
        ${this.formActions(t("dlg.reassign"))}
      </form>`;
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
   * The X on a spool asks what actually happened (docs/14 §14.4.3). Two answers, two
   * different facts about the world, and one line each so neither is picked by accident.
   */
  spoolIntentBody() {
    const t = this._t;
    const spool =
      this._spools.find((s) => s.id === this._dialog.spool_id) ??
      (this._detail?.id === this._dialog.spool_id ? this._detail : null);
    if (!spool) return this.staleSubject();
    const id = esc(spool.id);
    return `
      <h3>${t("dlg.intentTitle", { name: spool.name })}</h3>
      <p class="muted">${t("dlg.intentAsk")}</p>
      <div class="intent">
        <button data-action="intent-discard" data-id="${id}">${t("dlg.intentThrewAway")}</button>
        <small>${t("dlg.intentThrewAwayHelp", { grams: spool.balance_g })}</small>
      </div>
      <div class="intent">
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
  display: block; height: 100%;

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
#root { min-height: 100%; color: var(--fl-ink); font-family: var(--fl-font-sans);
  background:
    radial-gradient(1100px 520px at 82% -8%, rgba(0, 224, 198, .07), transparent 60%),
    radial-gradient(900px 460px at -6% 4%, rgba(131, 35, 255, .06), transparent 58%),
    var(--fl-bg);
  background-attachment: fixed; }

/* A hairline and a wash, not a coloured slab. The header used to be HA's app bar wearing
   the theme's primary colour; it is now part of the same surface as everything under it. */
header { background: linear-gradient(180deg, rgba(11, 16, 22, .92), rgba(5, 7, 10, .72));
  backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
  color: var(--fl-ink); padding: 16px 22px 0; position: sticky; top: 0; z-index: 5;
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

/* The safe-area insets ride on the base rule rather than a later override, so a container
   query can restate the padding without a trailing rule quietly winning back three sides.
   The phone's notch is the panel's problem: its venue is somebody standing at a printer. */
main { padding: 22px max(22px, env(safe-area-inset-right))
    max(22px, env(safe-area-inset-bottom)) max(22px, env(safe-area-inset-left));
  max-width: 1320px; margin: 0 auto;
  animation: fl-view var(--fl-dur-slow) var(--fl-ease) both; }
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
.spool-body { padding: 16px 18px; display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
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

.note { background: var(--fl-surface); border: 1px solid var(--fl-line);
  border-left: 3px solid var(--fl-accent); padding: 12px 16px;
  border-radius: 0 var(--fl-radius-m) var(--fl-radius-m) 0; font-size: 13.5px; color: var(--fl-ink-dim); }

.trays { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; }
.tray { padding: 16px; display: flex; flex-direction: column; gap: 5px; }
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
.reel-big { width: 60px; height: 60px; border-radius: 10px; flex: none; }
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
.rv-row { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.rv-dot { width: 14px; height: 14px; border-radius: 4px; flex: none;
  border: 1px solid var(--fl-line); }
.rv-warn { flex: none; width: 14px; text-align: center; }
.rv-spool { flex: 1 1 140px; min-width: 0; }
.rv-slot { font-size: 12px; color: var(--fl-ink-dim); white-space: nowrap; }
input.num { font: inherit; font-size: 14px; width: 88px; padding: 6px 9px; border-radius: 8px;
  border: 1px solid var(--fl-line); background: var(--fl-surface-sunken);
  color: var(--fl-ink); text-align: right; font-variant-numeric: tabular-nums; }
.rv-pickline { flex-basis: 100%; padding-left: 23px; font-size: 12.5px;
  color: var(--fl-ink-dim); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
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

/* Corrections — docs/14 §14.3, §14.4. */
.spool-body { position: relative; }
.card-x { position: absolute; top: -4px; right: -6px; border: 0; background: none;
  color: var(--fl-ink-dim); font-size: 18px; line-height: 1; padding: 4px 7px;
  border-radius: 8px; opacity: .55; }
.card-x:hover { opacity: 1; color: var(--fl-bad);
  background: var(--fl-surface-sunken); }

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
.intent { display: flex; flex-direction: column; gap: 5px; padding: 11px 0;
  border-top: 1px solid var(--fl-line); }
.intent button { align-self: flex-start; }

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
.pr-trays { display: flex; flex-direction: column; }
.tray .empty-reel { border: 1px dashed var(--fl-line); background: none; }

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
@keyframes fl-view { from { opacity: 0; transform: translateY(12px); } }
@keyframes fl-pop { from { opacity: 0; transform: translateY(20px) scale(.97); } }
@keyframes fl-bar { from { transform: scaleX(0); } }
@keyframes fl-row { from { opacity: 0; transform: translateX(-14px); } }

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
  main { padding: 14px max(14px, env(safe-area-inset-right))
    max(14px, env(safe-area-inset-bottom)) max(14px, env(safe-area-inset-left)); }
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
  .tray-actions button, .trash-acts button, .rowact { min-height: 44px; }
  .modal { padding: 18px; }
}

@container panel (min-width: 1000px) {
  main { padding: 28px max(32px, env(safe-area-inset-right))
    max(80px, env(safe-area-inset-bottom)) max(32px, env(safe-area-inset-left)); }
}
`;

// Before the element is defined, not from `connectedCallback`: the faces belong to the document
// and the browser can start fetching them while Home Assistant is still deciding to mount a
// panel. It is the same kind of module-level side effect as the line below it.
installFonts();

customElements.define("filament-ledger-panel", FilamentLedgerPanel);
