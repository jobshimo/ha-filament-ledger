# ADR-0002 — Spoolman is not the foundation; it is an optional export

**Status:** Accepted
**Date:** 2026-08-02
**Supersedes:** an earlier recommendation, during design discussion, to build on Spoolman

## Context

[Spoolman](https://github.com/Donkie/Spoolman) is a mature open-source filament inventory
manager: a database of spools with material, colour, vendor and remaining weight, its own web
UI, and a Home Assistant integration.

[SpoolmanSync](https://github.com/gibz104/SpoolmanSync) connects it to Bambu printers — mapping
loaded spools to AMS slots and deducting weight when prints complete. It explicitly supports
the A1 with AMS Lite.

Roughly the inventory half of this project already exists there. Building on it was the initial
recommendation.

## Decision

**Spoolman is not a dependency.** The project implements its own domain and persistence.

Spoolman is supported as an **optional export adapter** behind a port, so a user who already
runs it can keep it in sync. The domain has no knowledge of it.

## Rationale

**It contradicts the primary constraint.** The stated requirement is a single surface inside
Home Assistant — *"if I can centralise it in HA, better, so I don't have more applications"*.
Spoolman is another container, another database, another UI. Adopting it means the user manages
two inventories in two places and learns which one to trust.

**The gap is in the half that would have to be built anyway.** Spoolman's model has no
approval queue, no distinction between measured and estimated consumption, no confidence
level, no reconciliation-as-movement, and no partial discard with a reason. Those are not
additions to its model — they conflict with it: Spoolman stores a remaining weight, and this
project's central decision ([ADR-0001](0001-append-only-ledger.md)) is that a stored balance is
the wrong representation.

So the choice was never "reuse versus build". It was **"build the hard half against a foreign
schema, or build both halves against a schema designed for the problem"**.

**Cancellations are exactly what is missing.** SpoolmanSync deducts when prints *complete*.
Every cancelled print consumes filament that is never recorded, so the balance drifts upward —
permanently, and optimistically. An inventory that overstates what you have is the failure mode
that actually hurts.

**The reusable part is small.** What Spoolman genuinely provides is a spool table and a web UI.
The spool table is one entity. The web UI is being replaced regardless, because the requirement
is a Home Assistant panel.

## Consequences

**Accepted costs**

- Inventory CRUD is written rather than inherited — a few days of work in Phase 1.
- Users already running Spoolman must migrate or accept two systems. Mitigated by the export
  adapter and by JSON import in Phase 5.
- Spoolman's vendor and filament metadata catalogue is not inherited.

**Gained**

- One application, one UI, one source of truth — the actual requirement.
- A schema designed for approval, confidence and reconciliation instead of retrofitted to
  resist them.
- No coupling to an external project's release cycle, schema migrations, or API changes.
- Spoolman remains available to anyone who wants it, as an adapter — which is what Dependency
  Inversion is *for*, rather than a slogan.

## Note on the reversal

The earlier recommendation, made before the approval-queue and manual-correction requirements
were known, was to use Spoolman as the inventory backend. Those requirements changed the
answer: they land squarely in the part Spoolman does not model, and they conflict with how it
represents a balance.

The reversal is recorded rather than quietly dropped. A decision that changed is worth more to
a future reader than a decision that appears to have been obvious.
