# Filament Ledger

A Home Assistant custom integration that tracks how much filament you actually have left,
using double-entry bookkeeping instead of guesswork.

> **Status: specification.** No code yet. This repository currently holds the complete
> design. Implementation starts once the specs are reviewed and frozen.

---

## The problem

Bambu Lab printers report a `remain` percentage per AMS tray. On the **A1 and A1 Mini that
field does not exist at all**, and on other models it is an odometry estimate that is known
to break (stuck at 100%, or reporting `-1`).

The RFID tag on a Bambu spool stores the spool's **identity** — material, colour,
temperatures, slicer profile. It does not store how much is left.

So there is no sensor to read. The remaining amount has to be *accounted for*.

## The approach

Every spool has an opening balance. Every gram that leaves it is recorded as an immutable
movement. The current balance is derived, never stored:

```
balance = Σ(movements)
```

The opening weight is the first movement, so there is no special case to keep in sync.

This is a ledger, not a counter. You can always answer *why* a spool holds 340 g, not just
*that* it does. Corrections are new entries, never edits to history.

## What it does

- **One inventory** for spools mounted in the AMS and spools sitting on a shelf.
- **Automatic deduction** for prints that run to completion, using the slicer's per-tray
  figure — because a job that finished consumed what it was planned to consume.
- **A review queue** for cancelled and failed prints. Estimated consumption is *proposed*,
  never applied silently. You confirm the number — and you can weigh the waste and type in
  the real one.
- **Reconciliation** against a kitchen scale, recorded as a movement so the drift stays
  visible.
- **Discards** — a whole spool or part of one, with a reason.
- **A confidence level** per spool, so an estimate is never presented as a measurement.

## Design principles

1. **The system never guesses silently.** A plan that ran to completion is applied
   automatically. Anything interrupted, unattributable or missing requires human approval —
   and a missing number is never treated as zero.
2. **History is append-only.** Nothing is edited. Nothing is deleted.
3. **A number without its error margin is a lie with formatting.** Every balance carries a
   confidence level.
4. **The domain does not know Home Assistant exists.** Business rules are testable without
   booting HA.

## Documentation

| Document | Contents |
|---|---|
| [01 — Vision & Scope](docs/01-vision.md) | Problem, goals, explicit non-goals |
| [02 — Domain Model](docs/02-domain-model.md) | Entities, value objects, invariants |
| [03 — Architecture](docs/03-architecture.md) | Hexagonal layers, ports, adapters |
| [04 — Use Cases](docs/04-use-cases.md) | Every operation, pre/post conditions |
| [05 — HA Integration](docs/05-ha-integration.md) | Entities, services, events, WebSocket API |
| [06 — UI Specification](docs/06-ui-spec.md) | Every view, wireframed and specified |
| [07 — Consumption Estimation](docs/07-consumption-estimation.md) | Estimator strategies and accuracy |
| [08 — Data Model](docs/08-data-model.md) | SQLite schema and migrations |
| [09 — Testing Strategy](docs/09-testing-strategy.md) | What is tested, and where |
| [10 — Roadmap](docs/10-roadmap.md) | Delivery phases |
| [11 — Development](docs/11-development.md) | Toolchain, CI, conventions |
| [ADRs](docs/adr/) | Architecture decision records |

## Hardware this was designed against

Bambu Lab A1 with AMS Lite, using Bambu RFID filament, integrated through
[`ha-bambulab`](https://github.com/greghesp/ha-bambulab).

The design is not A1-specific — the A1 is simply the hardest case, because it reports no
remaining-filament data whatsoever.

## Licence

[MIT](LICENSE).
