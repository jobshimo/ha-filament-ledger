# ADR-0006 — The panel is a plain custom element, not a built bundle

**Status:** Accepted
**Date:** 2026-08-02
**Amends:** [11 — Development](../11-development.md) §11.6

## Context

[11 §11.6](../11-development.md) planned the panel as a TypeScript build — Lit and Vite —
producing a bundle committed into the integration, with CI verifying the bundle reproduces
from its source.

That plan was written before any panel existed. Building the first one made the cost visible:

- A Node toolchain becomes a hard dependency of *changing a button label*.
- HACS installs a repository, not a build pipeline, so the bundle has to be committed —
  which means every panel change carries a generated diff nobody reads.
- CI has to verify the bundle matches its source, or the two silently diverge.

Against that, the panel is a few hundred lines of rendering over seven websocket commands.

## Decision

**v1 ships `www/filament-ledger-panel.js` as a hand-written ES module.** No framework, no
bundler, no build step, no `node_modules`, no generated artefact in the repository. The
browser loads exactly the file a reader opens.

Styling uses Home Assistant's own CSS custom properties throughout, so light, dark and
custom themes work with no per-theme code.

## Rationale

**The panel is not where the difficulty lives.** The hard part of this project is the
accounting, and that is in Python, behind 234 tests. The panel renders a list, a table and
five forms. Lit's ergonomics are real, but they are ergonomics — and they are being bought
with a permanent toolchain.

**What you read is what runs.** With no build step there is no question of whether the
committed bundle matches the committed source, because they are the same file. That removes
a CI job, a class of bug, and the temptation to hand-edit a bundle "just this once".

**It cannot rot.** A dependency-free ES module works in every browser Home Assistant
supports, and will keep working with no upgrade path to maintain. A Vite config from 2026
will need attention long before this panel needs a feature.

**The decision is reversible in one direction and cheap in the other.** If the panel grows
past what plain DOM handles comfortably — the review queue with per-slot editing is the
likely trigger — adding a build then is a contained piece of work, and the websocket API it
talks to does not change. Starting with the toolchain and discovering it was unnecessary is
the more expensive mistake.

## Consequences

**Accepted costs**

- Rendering is `innerHTML` with an escaping helper rather than a templating library. Every
  interpolation of user data goes through `esc()`; that discipline is manual, and a review
  has to watch for it.
- Full re-render on every state change rather than a keyed diff. At this data volume — tens
  of spools — it is imperceptible, and it removes an entire category of stale-view bug.
- No type checking on the panel. `mypy --strict` covers the Python; the JavaScript has none.
  This is the real cost, and it is what would justify revisiting the decision.

**Gained**

- No Node in the repository, in CI, or in a contributor's setup.
- No committed build artefact, and no job to verify one.
- The file a user reads in `custom_components/` is the file their browser executes.

## Note on amending 11 §11.6

The earlier plan is not deleted, because a plan that changed is worth more to a future
reader than one that appears to have been obvious. It was a reasonable default written
before the actual size of the panel was known. Writing the panel is what showed the default
was heavier than the problem.
