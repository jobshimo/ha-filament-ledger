// How a spool is drawn: the filament ring, at the three sizes the panel uses it.

import { esc } from "../i18n.js";

/**
 * The three sizes the coil is drawn at, and the one place their geometry is written down.
 *
 * `box` is the SVG's own coordinate space and the element's rendered size in CSS pixels —
 * the charts already work this way (`STATS_BAR_ROW`), and a viewBox that matches the pixel
 * box means a stroke width is a real width rather than a number to be scaled in the head.
 */

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

export { spoolRing };
