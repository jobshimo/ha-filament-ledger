// The panel's stylesheet, adopted into its shadow root (docs/16).
//
// Its own module since ADR-0010 split the panel. A plain string, so it carries no logic and
// imports nothing.

/**
 * Imported by the panel and by `styleguide.html`, which adopts this exact sheet into its own
 * shadow roots so the catalogue and the panel cannot drift apart (16 §16.4).
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
/* ---- The mount dialog's spool choices (06 §6.8: colour leads) ----------------------
   Buttons, not options: each choice is the whole card, the ring carries the colour at
   the size a tray draws it, and one tap mounts. */
.mount-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 10px; margin: 6px 0 4px; }
.mount-choice { display: flex; align-items: center; gap: 12px; text-align: left;
  padding: 10px 12px; }
.mount-choice .mc-art { position: relative; flex: none; width: 56px; height: 56px; }
.mount-choice .mc-art svg.ring { width: 100%; height: 100%; display: block; }
.mount-choice .mc-art .ring-mid { position: absolute; inset: 0; display: flex;
  align-items: center; justify-content: center; }
.mount-choice .mc-body { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.mount-choice .mc-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.mount-choice .mc-sub { font-size: 12px; color: var(--fl-ink-dim); overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.mount-choice .mc-grams { font-family: var(--fl-font-mono); font-size: 13px; }
.mount-choice.spent { opacity: .55; }
.pick-sec { margin: 14px 0 6px; font-size: 10.5px; letter-spacing: .12em;
  text-transform: uppercase; color: var(--fl-ink-dim); font-weight: 700; }
.picker-layer { z-index: 30; }
.picker-modal { width: min(560px, 100%); }
.spool-field { width: 100%; }
.spool-field .sf-card { display: flex; align-items: center; gap: 12px; min-width: 0; flex: 1; }
.spool-field .sf-change { flex: none; font-size: 12px; color: var(--fl-accent-bright); }

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

/* ---- One print's rows, gathered (06 §6.6) -------------------------------------------
   The caption is not a row: it keeps no bottom border of its own, so it sits tight on
   the entries it captions and the line under the last of them is what closes the group. */
table.ledger tr.hist-job td { padding: 12px 0 3px; border-bottom: 0; white-space: nowrap;
  font-size: 12.5px; }
table.ledger tr.hist-job .hj-name { font-weight: 600; }
table.ledger tr.hist-job .hj-sum { font-family: var(--fl-font-mono);
  font-variant-numeric: tabular-nums; margin-left: 10px; }
table.ledger tr.hist-job .hj-sum.minus { color: var(--fl-bad); }
table.ledger tr.hist-job .hj-sum.plus { color: var(--fl-ok); }
table.ledger tr.hist-job .hj-count { margin-left: 10px; color: var(--fl-ink-dim);
  font-size: 12px; }

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
/* The row's spool field is the same card the reassign form shows, drawn one size down so
   it sits in a charge row rather than owning the modal: a smaller ring, tighter padding,
   and it stretches to the line it is on. Unresolved reads as a dashed outline — a place
   for a spool, not a spool — and the face inside carries the invitation. */
.rv-charge .spool-field { flex: 1 1 220px; min-width: 0; padding: 8px 10px; }
.rv-charge .spool-field .mc-art { width: 44px; height: 44px; }
.rv-charge.unresolved .spool-field { border-style: dashed; border-color: var(--fl-line-strong); }
.spool-field .sf-empty { color: var(--fl-ink-dim); font-size: 13px; }
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

/* One machine's positions on the AMS tab. Same structure, same reason. */
.ams-space { display: flex; flex-direction: column; gap: 10px; }
.ams-head { display: flex; flex-direction: column; gap: 2px; }
.ams-head .pr-h { margin: 0; text-transform: none; letter-spacing: .06em; font-size: 12.5px;
  color: var(--fl-ink); font-family: var(--fl-font-mono); }
.ams-head p { margin: 0; }
/* One AMS unit's block. The heading only exists on a machine with more than one, and it is
   deliberately quieter than the machine's own: a unit is a subdivision of a machine, and a
   heading as loud as the section's would read as a second printer on a phone. */
.ams-unit { display: flex; flex-direction: column; gap: 8px; }
.ams-unit + .ams-unit { margin-top: 4px; }
.ams-unit-h { margin: 0; font-size: 10.5px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--fl-ink-dim); font-family: var(--fl-font-mono); }

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
