---
name: Filament Ledger
description: A precise, teal-lit instrument panel for a spool ledger inside Home Assistant.
colors:
  bg: "#11171c"
  surface: "#192127"
  surface-raised: "#202a32"
  surface-sunken: "#141c22"
  line: "#607487"
  line-soft: "#34434f"
  line-strong: "#8296aa"
  ink: "#e6edf3"
  ink-bright: "#ffffff"
  ink-dim: "#8b9aab"
  ink-faint: "#9aaaba"
  accent: "#5de0c6"
  accent-bright: "#7ff5e7"
  on-accent: "#052820"
  ok: "#3ddc84"
  warn: "#ffb340"
  bad: "#ff8fa3"
  light-bg: "#f4f6f8"
  light-surface: "#ffffff"
  light-ink: "#233646"
  light-accent: "#006e63"
typography:
  display-figure:
    fontFamily: "IBM Plex Mono, ui-monospace, Roboto Mono, Menlo, monospace"
    fontWeight: 600
    letterSpacing: "-0.03em"
  title:
    fontFamily: "Space Grotesk, system-ui, sans-serif"
    fontSize: "19px"
    fontWeight: 600
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Space Grotesk, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 500
    lineHeight: 1.6
  small:
    fontFamily: "Space Grotesk, system-ui, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
  label:
    fontFamily: "Space Grotesk, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    letterSpacing: "0.06em"
rounded:
  s: "8px"
  m: "12px"
  l: "16px"
  xl: "18px"
  pill: "999px"
spacing:
  tap: "44px"
  gap: "16px"
components:
  button:
    backgroundColor: "transparent"
    textColor: "{colors.ink-dim}"
    rounded: "{rounded.s}"
    padding: "9px 16px"
    height: "{spacing.tap}"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.s}"
    padding: "9px 16px"
    height: "{spacing.tap}"
  button-primary-hover:
    backgroundColor: "{colors.accent-bright}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.l}"
  chip:
    textColor: "{colors.ink-dim}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
  tab:
    textColor: "{colors.ink-dim}"
    rounded: "10px"
    padding: "10px 18px"
  tab-active:
    textColor: "{colors.ink-bright}"
---

# Design System: Filament Ledger

## Overview

**Creative North Star: "The Workshop Ledger"**

The panel is a precision instrument for a maker's bench: slate surfaces, one cool teal light, and figures set like gauge readings. It is deliberately not Home Assistant's own look. ADR-0008 reversed the earlier theme-native position so the panel carries its own palette, type and motion, inside a shadow root that nothing of HA's styling can reach. Numbers are the hero: grams are tabular mono, labels are small and quiet, and colour is reserved for state.

Density is moderate and card-based, one column on a phone and wider on a desktop. Depth is tonal first, with soft ambient shadows; a sparse layer of decoration (drifting motes, a dashed filament strand, a slow card sweep) gives the surface a pulse without ever carrying information, and it switches off under reduced motion.

Dark is the default identity. A light variant and an automatic mode exist (ADR-0011) and are a full re-mapping of the same semantic tokens, not a second design.

**Key Characteristics:**
- One accent (teal) plus three state colours (ok, warn, bad); everything else is slate.
- Semantic token names (`--fl-bad`, never `--fl-red`) so a state can change colour without lying.
- Tabular monospaced numerals for every quantity.
- 44px minimum tap target on everything interactive.
- Layout answers the panel's own width, not the viewport's.

## Colors

A cool slate canvas lit by a single mint-teal; state colours appear only when a state exists.

### Primary
- **Filament Teal** (#5de0c6): the one accent. Primary button fill, active tab, links, focus ring, selection, progress and ring strokes. Bright hover variant **Lit Teal** (#7ff5e7). Text on it is **Deep Spruce** (#052820).

### Secondary
- **Signal Green** (#3ddc84): healthy, confirmed, in-stock.
- **Amber Warning** (#ffb340): low stock, needs weighing, attention.
- **Soft Coral** (#ff8fa3): errors, voided, destructive.
Each has a translucent soft fill (14-16% alpha) for backgrounds of alerts and badges.

### Neutral
- **Night Slate** (#11171c): the panel background.
- **Bench Slate** (#192127): card and surface fill.
- **Raised Slate** (#202a32) and **Sunken Slate** (#141c22): raised and recessed wells (bars, tracks).
- **Mist Ink** (#e6edf3): body text; **Pure White** (#ffffff) for emphasised figures; **Dim Steel** (#8b9aab) for secondary text; **Faint Steel** (#9aaaba) for tertiary.
- **Rule Steel** (#607487), **Soft Rule** (#34434f), **Strong Rule** (#8296aa): card borders, dividers, control outlines.

### Named Rules
**The One Light Rule.** Teal is the only brand colour and marks interactive or live things; if something is teal it can be acted on or is current.
**The Honest State Rule.** Green, amber and coral appear only when the ledger asserts that state. They are never used for decoration.
**The Mirror Rule.** The light variant remaps every token (accent becomes #006e63, bg #f4f6f8, surface #ffffff, ink #233646); no component may hard-code a colour outside the token set.

## Typography

**Display Font:** IBM Plex Mono (with ui-monospace, Roboto Mono, Menlo)
**Body Font:** Space Grotesk (with system-ui, sans-serif)
**Label/Mono Font:** IBM Plex Mono for numerals, Space Grotesk for labels

**Character:** A geometric grotesque for words and a calm mono for quantities: instrument labelling, not editorial. Both are bundled as woff2 (OFL) so the panel never depends on a CDN.

### Hierarchy
- **Display figure** (Plex Mono 600, large, -0.03em, tabular-nums): the big gram balance on a spool.
- **Title** (Space Grotesk 600, 19px, -0.01em): view and empty-state headings.
- **Body** (Space Grotesk 500, 14px, line-height 1.6): controls and prose; keep prose near 46ch.
- **Small** (12.5px / 13px): metadata, hints, table cells.
- **Label** (600-700, 10.5-11px, +0.06em, uppercase): chips and badges.

### Named Rules
**The Tabular Rule.** Any number that can be compared or summed is monospaced with tabular figures.

## Layout

A single scrolling view region under fixed chrome (header plus tab strip). The panel host is sized to the viewport (100dvh) and declared a size container named `panel`, so responsive rules use `@container panel` breakpoints (max-width 600px for phone, min-width 1000px for wide) rather than media queries; HA's sidebar changes the panel's width without changing the viewport. Content is stacked with a 16px gap; cards use 16-18px internal padding. The tab strip scrolls horizontally with edge fade masks and the active tab is scrolled back into view after each repaint. Bottom padding respects the safe-area inset.

## Elevation & Depth

Hybrid: tonal layering carries hierarchy (bg, surface, raised, sunken), with soft ambient shadows lifting cards. Shadows are removed on narrow panels.

### Shadow Vocabulary
- **Card lift** (`box-shadow: 0 8px 24px rgba(0,0,0,.35)`): default card.
- **Overlay lift** (`box-shadow: 0 14px 40px rgba(0,0,0,.4)`): dialogs and popovers.
- **Active tab glow** (`0 0 22px accent-glow`, plus a 1px top highlight): selected tab only.
In the light variant shadows become cool, low-alpha slate (`.10` and `.16`).

### Named Rules
**The Decoration Is Mute Rule.** Ambient motes, the dashed strand and the card sweep never carry data and are hidden when `prefers-reduced-motion` is set or the panel is narrow.

## Shapes

Soft but not bubbly: 8px controls, 12px inset panels, 16px cards, 18px featured cards, 10px tabs, full pills for chips and badges. Cards carry a 1px Rule Steel border; wells (bars) are 10px tall with a 5px radius. Circular ring gauges represent spool remaining.

## Components

### Buttons
- **Shape:** 8px radius, 44px minimum height.
- **Default:** transparent, 1px Strong Rule outline, dim text; hover brightens text and border.
- **Primary:** Filament Teal fill, Deep Spruce text, weight 600; hover shifts to Lit Teal.
- **Link:** no chrome, teal text.
- **Focus:** 2px teal outline, 2px-3px offset on everything. Disabled: 65% opacity, not-allowed.

### Chips / Badges
- Uppercase 10.5-11px labels, pill radius, 1px outline in dim steel; `user` badges flip to teal text with a current-colour border.

### Cards / Containers
- 16px radius, Bench Slate fill, 1px Rule Steel border, Card lift shadow, 16-18px padding.

### Inputs / Fields
- Numeric inputs: 14px, ~88px wide, 8px radius, 6px 9px padding; teal caret and accent-colour checkboxes (20px). Placeholders in Dim Steel. Fieldsets use Soft Rule borders at 12px radius.

### Navigation
- Horizontal tab strip: 10px-radius buttons, 14px 600 weight, dim until hovered; active tab gets a teal-tinted gradient fill, teal line border and glow. A count badge (18px pill) marks Review; on phones tabs tighten to 13.5px.

### Spool ring (signature)
- A circular gauge in the spool's own colour showing remaining filament, paired with the big monospaced gram figure; spool colours use the registered filament colour with a colour name label.

## Do's and Don'ts

### Do:
- **Do** read every colour, radius and duration from the `--fl-*` tokens.
- **Do** use `--fl-tap` (44px) as the floor for tappable targets.
- **Do** ask the container (`@container panel`), not the viewport.
- **Do** keep copy and any new string in English, Spanish and Turkish together.

### Don't:
- **Don't** add a framework, bundler or build step to the panel (ADR-0006).
- **Don't** use literal colour names or hard-coded hex values in components.
- **Don't** use teal for non-interactive decoration, or state colours for emphasis.
- **Don't** let ambient decoration run under reduced motion.
