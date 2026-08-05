# 16 — The Visual System

The panel stops borrowing Home Assistant's clothes and puts on its own.

Until now the panel was theme-native by decision: HA's cards, HA's typography, HA's custom
properties, so it read as part of Home Assistant rather than as a foreign application embedded
in it. That was the right call for v1.0 and it is being reversed on purpose. This document
specifies what replaces it, how it survives inside Home Assistant, and how eight tabs written
across several sittings end up looking like one product.

The decision itself is recorded in [ADR-0008](adr/0008-panel-visual-identity.md). This document
is the contract that implements it.

---

## 16.1 What is changing, and what is not

**Changing.** The panel gets a fixed dark identity of its own: its own palette, its own
typefaces, its own spacing and motion. It no longer follows the user's HA theme, in either
direction — a light theme, a dark theme and a custom theme all render the same panel.

**Not changing.** Everything behind the pixels. [ADR-0006](adr/0006-vanilla-panel.md) still
holds: a hand-written ES module, no framework, no bundler, no build step. No websocket command
changes, no read model changes, no schema migration. **This work cannot touch the database, and
a pull request in this series that adds a Python migration has gone wrong.**

The size of the change, measured rather than estimated: `STYLES` is 352 lines and 218
selectors, and it contains **135 uses of 15 distinct Home Assistant theme variables**. Those
135 uses are the divorce.

---

## 16.2 How this works inside Home Assistant

Five facts about the host, and what each one obliges.

### The panel already has encapsulation

The panel attaches an open shadow root and paints by replacing `innerHTML` on a single `#root`
element. Nothing of HA's styling reaches inside and nothing of ours escapes. **The identity can
be total without a single `!important` and without any risk to the surrounding frontend.**

### Home Assistant already tells us how much room there is

A `panel_custom` element receives four properties from the frontend — `hass`, `narrow`, `route`
and `panel` — plus `showMenu`, which says whether the sidebar is currently displayed. The panel
implements `set hass` and ignores the rest.

`narrow` and `showMenu` are to be implemented, but they are **not** the responsive mechanism.
They are the signal for decisions CSS cannot make — principally whether to draw a control that
opens the sidebar when HA has collapsed it.

### The host has a definite height, and that is what the layout shell stands on

[06 §6.1](06-ui-spec.md#61-navigation) asks for chrome that stays put while the content
scrolls under it. Inside a shadow root that needs one thing the panel cannot assume: a
containing block with a **definite** height, so the content region's `flex: 1` resolves to a
real box instead of to its own content.

Home Assistant supplies one, and the mechanism is worth writing down because it is not
obvious. Measured on a running instance:

```
body                      854px   definite
  home-assistant                  display: inline   (no styles of its own)
    home-assistant-main           display: inline
      ha-drawer           854px   definite
        partial-panel-resolver    display: inline
          ha-panel-custom         display: inline
            filament-ledger-panel         height: 100%  →  854px
```

The three elements between the panel and `ha-drawer` carry no CSS at all, so they are
`display: inline` and are **not block containers** — a percentage height resolves against
the nearest block container ancestor, which skips all three and lands on `ha-drawer`. So
`:host { height: 100% }` is the viewport's height, and the shell is a flex column that
fills it.

**Do not swap that for `100vh`.** It is the same number today and the wrong one the moment
Home Assistant puts anything above the panel, and on a phone `vh` is the wrong number
whenever the URL bar is showing.

**It degrades rather than breaks.** If a future host stops supplying a definite height, the
column becomes content-sized, the content region stops being a scroller, and the panel scrolls
as one document — which is what it did before the shell existed. The header keeps a
`position: sticky` rule for exactly that case; it is inert in the shell and is the reason the
fallback is merely worse rather than unusable.

### Responsiveness is a container query, never a viewport query

The panel does not occupy the viewport. It occupies whatever is left of the viewport after HA's
sidebar, and that remainder changes when the sidebar is pinned, collapsed or hidden — without
the viewport changing at all. **A `@media` rule and a `window.innerWidth` read are both wrong
here, and wrong in the same way**: a 900 px window with the sidebar open leaves the panel about
640 px and both of them will report 900.

So the host declares itself a container and every responsive rule asks the container:

```css
:host { container-type: inline-size; container-name: panel; }

@container panel (max-width: 600px) { /* phone */ }
@container panel (min-width: 1000px) { /* roomy */ }
```

Three tiers: phone below 600, base between, roomy above 1000. The numbers are a starting point
to be tuned against a real device, not a law.

Two consequences worth knowing before the first line is written. `container-type: inline-size`
applies `contain: inline-size`, so the host's width stops depending on its contents — with
`display: block` and a width that comes from the parent this should change nothing, and it is
the first thing to check if the layout moves. And container queries need Chrome 105, Safari 16
or Firefox 110; every browser Home Assistant supports is past that.

### `@font-face` cannot live in the shadow root

A `@font-face` rule declared inside a shadow root is ignored. Font faces resolve against the
document, and by design a shadow tree cannot define one — otherwise one component could
redefine another's fonts and encapsulation would leak through the font stack.

**So the fonts do not go in `STYLES`.** The panel injects a `<style>` element into
`document.head` once, guarded by id so repeated construction cannot stack duplicates, pointing
at the woff2 files under the integration's own static path. Everything else stays in the shadow
root where it belongs.

The typefaces ship with the integration — Space Grotesk (variable, one file covering 400–700)
and IBM Plex Mono — subset to latin and latin-ext, roughly 125 KB in eight files. **Nothing is
fetched from Google Fonts or any third party.** An integration that phones home for a typeface
would leak the fact that a panel was opened, to a party that has no business knowing, on an
appliance the user believes is local.

That weight is also why `StaticPathConfig` is registered with `cache_headers=True`. It was
`False`, directly under a comment explaining why caching was desirable — the flag did the
opposite of what its own comment claimed, and 125 KB of fonts revalidated on every load is what
made that stop being harmless.

**Caching and busting are one decision, and neither is safe alone.** `cache_headers=True` makes
Home Assistant send a month of `max-age`; a cached panel that survives an upgrade is a user
running last month's code. So **the version rides in the path**:

```
/filament_ledger_static/<manifest version>/filament-ledger-panel.js
```

A query string would not do. `panel.js?v=1.1.0` imports `./i18n.js`, which resolves **without**
the query — that file, and the fonts, would stay stale for the whole month while the module
itself refreshed. A versioned directory busts the module, its imports and its assets together,
because every relative resolution inherits the prefix.

The segment comes from `manifest.json`, which is the version of record
([RELEASING](../RELEASING.md)) and the file the release workflow checks the tag against — so
the URL a browser caches and the code it is caching cannot drift apart. It is read directly
rather than through `homeassistant.loader`, which answers the same question but only once its
own registry exists, coupling panel registration to a part of Home Assistant this adapter
otherwise never touches.

---

## 16.3 The token vocabulary

Every value lives once, on `:host`, and nothing downstream hard-codes a colour, a radius or a
duration. This is what makes the later tabs match the earlier ones without anyone remembering a
hex code.

| Group | Tokens |
| --- | --- |
| Surface | `--fl-bg`, `--fl-surface`, `--fl-surface-raised`, `--fl-line` |
| Ink | `--fl-ink`, `--fl-ink-dim`, `--fl-ink-faint` |
| Accent | `--fl-accent`, `--fl-accent-soft`, `--fl-accent-glow` |
| Semantic | `--fl-ok`, `--fl-warn`, `--fl-bad` |
| Type | `--fl-font-sans`, `--fl-font-mono`, `--fl-step--1` … `--fl-step-5` |
| Space | `--fl-space-1` … `--fl-space-8` |
| Radius | `--fl-radius-s`, `--fl-radius-m`, `--fl-radius-l` |
| Elevation | `--fl-shadow-1`, `--fl-shadow-2` |
| Motion | `--fl-ease`, `--fl-dur-fast`, `--fl-dur-base`, `--fl-dur-slow` |

Semantic names, not literal ones: `--fl-bad`, never `--fl-red`. The day a warning stops being
amber, one line changes and nothing reads as a lie.

---

## 16.4 The styleguide page

`www/styleguide.html` renders every primitive in every state, on one page, served by the static
path the panel already registers:

```
/filament_ledger_static/<manifest version>/styleguide.html
```

The version segment is the cache-busting prefix from §16.2; the panel's own module URL carries
it too, so the address to open is the one beside the version currently installed.

It imports `STYLES` from the panel module and adopts that exact stylesheet into each specimen's
shadow root. Better still, it does not hand-write the markup either: it asks a real, unmounted
`<filament-ledger-panel>` to render each view from fixtures whose shapes come from
`infrastructure/ha/serialisers.py`. There is no second copy of the CSS and no second copy of the
markup, so the catalogue cannot drift away from the panel — it can only break loudly, and a
specimen that throws says so in place rather than rendering empty.

Two sheets sit beside `STYLES`, and both are temporary in different ways.

- ~~An approximation of Home Assistant's default theme.~~ **Removed by the token pass, as
  planned.** It existed because outside HA not one of the 15 theme variables was defined and
  the specimens rendered as unstyled boxes. The panel now owns its palette, so there is
  nothing left to approximate.
- **A containment rule for dialogs**, and only for dialogs. `.scrim` is `position: fixed`,
  which is right in the panel and wrong on a catalogue — it escapes its frame and stacks
  overlays on the page. Scoping it to `absolute` inside a positioned stage reproduces the same
  layout. This is the only place the page overrides the panel, it concerns positioning rather
  than appearance, and if the list ever grows past that the catalogue has started lying.

**What it must never do: talk to Home Assistant.** No websocket, no `hass`, no real spool, no
real balance. Hard-coded sample markup only. That constraint is what makes the page safe to
ship regardless of whether the static path authenticates — there is nothing on it to leak.

It exists for three reasons, and the third is the one that matters:

1. It is the before-and-after instrument for the restyle.
2. It is where a new component is designed, in isolation, before it has to work inside a tab.
3. **It is the panel's missing test suite.** [ADR-0006](adr/0006-vanilla-panel.md) accepted
   having no JS harness. This does not fix that, but it turns "does the Trash tab match the
   Inventory tab" from a question of memory into a question anyone can answer by looking at one
   page.

It is a developer surface: English only, and outside the i18n obligation in
[CONTRIBUTING](../CONTRIBUTING.md) that covers user-facing strings.

---

## 16.5 One vocabulary, eight tabs

The panel has eight tabs. The design covers four of them — Inventory, AMS, Review and Stats —
plus the spool detail and the weighing dialog. The other four, and roughly eleven dialog forms,
have no design and are not going to get one.

**They do not need one.** The designed surfaces already contain nearly every primitive the rest
require:

| Undesigned surface | Built from | Genuinely new |
| --- | --- | --- |
| History | the detail view's history rail, unbounded, plus filters | nothing |
| Printer | KPI grid, fact rows, and AMS's slot grid | nothing |
| Trash | cards plus an action row | a struck-through / retired state |
| Settings | the weighing dialog's fields, inside cards | a toggle |
| ~11 dialogs | the modal shell and its fields | select, number, textarea, radio group, danger button |

Six new components in total. They are designed in the styleguide **first**, then used — not
invented inside whichever tab needed one at the time. That ordering is the whole mechanism by
which eight tabs stay consistent.

---

## 16.6 What the design does not decide

The mock is a mock, and the gaps are as load-bearing as the content.

**Spool visualisation modes — Ring, Profile, 3D — are out of scope.** The panel has no such
feature today. That is a new capability wearing a restyle's clothing, and it belongs to its own
change with its own specification.

**Motion has no off switch in the mock.** Eighteen keyframes, including floating particles and
a scanline, and no `prefers-reduced-motion`. Ours honours it: under that query the decorative
animation stops entirely and state transitions collapse to near-zero duration. Motion is
decoration here, and decoration that ignores a stated accessibility preference is a defect.

**Touch targets are short.** The mock's phone tab is about 38 px tall; the current panel's is no
better. The floor is 44 px for anything tappable. Since every surface is being rewritten
anyway, this is the cheapest it will ever be to fix.

**There is no safe-area handling.** On a phone with a notch or a home indicator the panel must
pad with `env(safe-area-inset-*)`, or the last row of the longest tab sits under the system UI —
on the one device this panel was built for.

**The mock is a fixed 1360×940 canvas with a single JavaScript breakpoint at 760.** Its mobile
values are a good starting point and nothing more; the real values come from §16.2's container
tiers, tuned on the device.

---

## 16.7 Delivery

Small pull requests against `develop`, each one independently verifiable, in this order. Every
one of them keeps the panel working: there is no point in the sequence where the tree is
half-restyled and unusable.

1. **This document and ADR-0008** (#1). The contract, before the code that honours it.
2. **The typefaces** (#2), self-hosted, with the document-level `@font-face` injection. Small on
   purpose: it isolated the one behaviour in this whole change that could simply refuse to work.
3. **The styleguide page** (#3), rendering today's components with today's styles. The "before"
   photograph.
4. **Tokens and container queries** (#4). `STYLES` rewritten, touch targets, reduced motion,
   safe areas. **Class names preserved**, so no markup moved and all eight tabs changed
   together. The pull request that carried the risk.
5. **The coil** (#5) — a spool drawn as an arc of its own colour, at three sizes.
6. **The ambience** (#6) — motes, a strand, a sweep, a winding.
7. **Caching, with busting** (#7). `cache_headers=True` and the versioned path §16.2 describes.

Then `develop` → `staging`, verified whole on a real instance, then `staging` → `main` and a
tag, per [RELEASING](../RELEASING.md).

**Two things moved from the plan as written, and both moved for a reason worth keeping.**

`cache_headers=True` was step 2 and became step 7. Turning caching on without busting it would
have left every user of this series running a stale panel for a month; and while the series was
being verified daily on a real instance, `False` meant a redeploy showed up on an ordinary
reload rather than needing a hard refresh.

Steps 5 and 6 replaced a single "the four designed surfaces" step. The coil and the ambience are
what the design actually *is* to a person looking at it, and splitting them meant each could be
deployed and judged on its own rather than arriving as one large change nobody could isolate a
complaint within.

**This ships as v1.1.** It is a minor: no schema change, no websocket command change, nothing a
user has to do on upgrade beyond looking at it. The release notes owe one thing the generated
notes cannot supply — that the panel no longer follows the Home Assistant theme, stated as a
decision rather than left to be discovered ([ADR-0008](adr/0008-panel-visual-identity.md)). The
feature set formerly called v1.1 is now v1.2 ([15](15-public-release.md),
[10 — Roadmap](10-roadmap.md) Phase 7).

Preserving class names in step 4 deserves its reason stated. The alternative — new class names,
with markup migrating tab by tab — means every intermediate commit leaves some tabs styled and
others not. Restyling in place costs some fidelity to the mock, which steps 5 and 6 then pay
back deliberately, and buys a panel that is never broken and a diff that is CSS only.

---

## 16.8 Hand-verification checklist

The model is [14 §14.9](14-corrections-and-trash.md#149-hand-verification-checklist), and the
obligation comes from [CONTRIBUTING](../CONTRIBUTING.md): every pull request touching `www/`
carries one, each line initialled, and it is executed on a real instance before the pull request
leaves draft.

Adapt per pull request; these lines are the minimum for this series.

1. Both languages, Spanish and English, on every surface touched.
2. Phone width, on a phone — not a narrowed desktop window. The panel's venue is somebody
   standing at the printer holding a failed part.
3. **Sidebar pinned, then collapsed, without reloading.** The layout must respond to the panel's
   own width. This is the specific thing container queries were chosen for, and the specific
   thing a viewport rule would get wrong.
4. Every dialog touched: Cancel closes, a click on the body does not, a click on the scrim does.
5. Every figure that can be absent renders as a dash, never as a zero.
6. Every interpolation of user data still goes through `esc()`.
7. Keyboard: focus is visible against the new background at every step, and nothing is reachable
   only by pointer.
8. The HA theme switched between light and dark: the panel must be identical in both. Anything
   that moves is a variable that survived the divorce.

---

## 16.9 Traps already paid for

**`@font-face` in a shadow root silently does nothing.** No error, no warning — the text simply
renders in the fallback font, which looks like a font that failed to load rather than a rule
that was never honoured. §16.2 has the fix.

**`@media` is not `@container`.** The failure is invisible on a developer's wide monitor and
appears only with the sidebar pinned. Any rule written against the viewport in this panel is a
bug that will be found by a user, not by us.

**`cache_headers=False` is the setting that disables caching.** The flag reads as though it
means "do not send headers I have to think about". It means the browser revalidates every time.

**Turning it on without busting the URL is worse than leaving it off.** A month of `max-age` on
a path that never changes is a user running last month's code after an upgrade, discovered as
"the new version did not do anything". And **a query string is not enough**: it busts the file
it is attached to and nothing that file imports. §16.2 has the shape that works.

**A backtick inside `STYLES` ends the stylesheet.** `STYLES` is a template literal, so one
backtick in a CSS comment — around a property name, say — closes it early, the rest is parsed
as JavaScript, the module throws, and `customElements.define` never runs. The symptom is
specific and worth memorising: the element exists in the DOM, its `shadowRoot` is `null`, and
`customElements.get` returns nothing. `node --check` catches it instantly, which is a reason to
run it on the way to a deploy rather than after the last edit before one.

**The panel repaints by replacing markup wholesale.** Every node is new after every paint, so
anything measured, scrolled or focused must be re-applied after the paint and never once at
startup. The tab strip already learned this ([06 §6.1](06-ui-spec.md#61-navigation)), the
content region's scroll position learned it second — in both axes, not just the obvious one —
and the History filter row learned it third, where "focused" stopped being hypothetical: a
control rebuilt under a reader's fingers eats the word they were typing unless the focus and
the caret are put back. Any new component that measures itself inherits the same rule.

**`overflow-x: auto` makes a box scroll in both axes, and a sticky heading sticks to it.** A
horizontal-scroll wrapper is not horizontal-only: CSS computes an `overflow` of `visible` to
`auto` the moment the other axis is not `visible`, so the wrapper becomes the nearest
scrollport and anything `position: sticky` inside it pins to a box whose vertical extent never
moves. Nothing errors and nothing warns; the heading simply never sticks, and the declaration
that would have worked is three elements away. The fix is structural rather than a property —
remove the scroll container from between the sticky element and the scroller that actually
moves ([06 §6.6](06-ui-spec.md)). Its second half: a **collapsed** border belongs to the table
rather than to the cell, so it stays behind with the rows while a sticky heading travels, and
the line under the headings detaches and scrolls away on its own.

**`min-height: 100%` is not a definite height, and a flex scroller needs one.** A column laid
out with `min-height` grows to fit its content, so the region meant to scroll simply gets
taller and the whole panel overflows its host instead — the failure looks like the scroll
container being ignored, and the missing declaration is three elements away. The same trap has
a second half: a flex item's automatic minimum size is its content, so an item with
`overflow-y: auto` and no `min-height: 0` refuses to shrink and never scrolls either. Both are
needed, and neither produces an error.

**Do not bump `manifest.json`.** Not in any of these pull requests. That is a release-time edit
and [RELEASING](../RELEASING.md) owns it.
