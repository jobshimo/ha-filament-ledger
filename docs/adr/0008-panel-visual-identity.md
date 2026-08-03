# ADR-0008 — The panel takes a visual identity of its own

**Status:** Accepted
**Date:** 2026-08-03
**Amends:** [06 — UI Specification](../06-ui-spec.md) preamble and §6.8;
[ADR-0006](0006-vanilla-panel.md) §Decision, second paragraph

## Context

[06 §6.8](../06-ui-spec.md) and [ADR-0006](0006-vanilla-panel.md) both committed the panel to
being theme-native: Home Assistant's cards, typography and CSS custom properties throughout, so
light, dark and custom themes work with no per-theme code. That is why `STYLES` today carries
135 uses of 15 distinct HA theme variables.

The reasoning was sound and cheap: a panel that inherits the host's theme cannot look wrong in
somebody's dashboard, and it costs nothing to maintain.

What changed is that the panel was designed. A visual design now exists for four of the eight
tabs — a fixed dark surface, its own palette, its own typefaces, its own motion — and the owner
has chosen it over theme-nativeness explicitly, knowing it contradicts the written spec.

The two are not reconcilable. A design with a fixed dark background and a teal accent does not
survive being rendered through somebody's light theme; it becomes a third thing that is neither
the design nor Home Assistant.

## Decision

**The panel renders its own fixed dark identity and no longer follows the Home Assistant
theme.** The HA theme variables are replaced by tokens the panel owns, declared once on `:host`.
Light theme, dark theme and custom theme all produce the same panel.

The rendering decision in ADR-0006 is untouched — still a hand-written ES module, no framework,
no bundler, no build step. Only its styling paragraph is superseded.

[16 — The Visual System](../16-visual-system.md) specifies the tokens, the container-query
model, the styleguide page, and the delivery sequence.

## Rationale

**The panel is not a dashboard card.** A card sits among other cards and must not clash with
them. This is a full-page sidebar panel that occupies the whole surface, and it is the only
thing on screen while it is open. The argument for blending in is weakest exactly here.

**It is a tool, not a view.** The panel's venue is somebody standing at a printer with a failed
part in their hand, deciding whether an estimate was right. A dense instrument that is
instantly recognisable is worth more in that moment than one that colour-matches the rest of the
house.

**Encapsulation already exists and is free.** The panel is inside an open shadow root. The
identity can be total without leaking a single rule into the frontend, and without needing one
`!important`. The cost of this decision is confined to one file.

**One appearance is fewer appearances to be wrong in.** Theme-nativeness meant every surface had
to be checked against light, dark, and whatever a user configured. One fixed appearance is one
thing to verify, and the hand-verification checklist gets shorter rather than longer.

## Consequences

**Accepted costs**

- **The panel will not match a user's light theme, and this will be reported as a bug.** It is
  not one. It is this decision, and the release notes have to say so plainly rather than leaving
  a user to discover it.
- **No dark/light toggle, and no route to one that is cheap.** Adding it later means every token
  gaining a second value and the checklist doubling. That is the real weight of this decision and
  it should not be pretended away.
- **A user with a high-contrast or accessibility theme loses it inside this panel.** Contrast is
  now our obligation to get right in the palette, because the user can no longer fix it from
  their side.
- **The visual result cannot be tested.** ADR-0006 accepted no JS harness; this enlarges the
  untested surface from behaviour to appearance. The styleguide page and the hand-verification
  checklist are the mitigation, and they are people, not tests.

**Gained**

- A product with an identity, on a design that exists rather than one assembled by defaults.
- One appearance to verify instead of a theme matrix.
- Typography and motion that are chosen rather than inherited, self-hosted, with nothing fetched
  from a third party.

## Note on amending 06 §6.8 and ADR-0006

Neither prior text is deleted. Both were correct for a panel that had no design, and a reader
arriving later is better served by seeing that the position changed — and what changed it — than
by finding a document that reads as though this had always been obvious. The panel being
designed is what moved it.
