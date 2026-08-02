# ADR-0001 — Balances are derived from an append-only ledger

**Status:** Accepted
**Date:** 2026-08-02

## Context

The system must track how much filament remains on each spool. Two representations are
possible.

**A stored balance.** A `remaining_grams` column, updated on every consumption. Simple, fast
to read, and the obvious choice.

**A derived balance.** An immutable sequence of movements; the balance is their sum.

The requirements complicate the obvious choice. The user must be able to correct values
manually, enter weighed amounts, reconcile against a scale, and record discards. Estimated
consumption from cancelled prints must be approvable and correctable.

Every one of those is a *write* to the balance from a different source, with different
reliability.

## Decision

Balances are **derived**:

```
balance = Σ(movements)
```

with the opening balance stored as the first movement, so there is no special case.

Movements are immutable. The repository interface exposes no update or delete, and the
database enforces the same with triggers. Corrections are new compensating entries.

## Rationale

**A stored balance cannot answer "why".** When it reads 340 g and the user believes it should
read 500 g, a stored balance offers nothing. A ledger shows every gram that left and what took
it. Given that several sources write to this number with differing reliability, "why" is not a
nice-to-have — it is the only way to debug a wrong balance.

**Manual correction demands an audit trail.** A system where the user can overwrite a number
by hand, with no record, is a system whose numbers mean nothing three months on. With a
ledger, a correction is an entry with a timestamp and a reason.

**Reconciliation drift is the system's error signal.** When a scale disagrees with the ledger,
that delta is the only honest measure of how wrong the estimates have been. A stored balance
silently absorbs the difference and destroys the signal. A ledger records it — and that record
is what makes the confidence model in [02 §2.6](../02-domain-model.md) possible at all.

**Immutability makes a class of bug impossible.** No partial update can corrupt a balance,
because balances are never written. The worst outcome of a failed append is a missing entry —
detectable and correctable — rather than a silently wrong number.

**Correctness under concurrency.** Appends do not conflict. Two concurrent movements both land;
neither overwrites the other. A read-modify-write on a stored balance loses one of them.

## Consequences

**Accepted costs**

- Reading a balance requires summing. Mitigated by an in-memory cache invalidated on append,
  with snapshotting available if a spool ever accumulates enough movements to matter — noted in
  [08 §8.3](../08-data-model.md) and deliberately not built yet.
- Storage grows without bound. A few thousand movements a year is kilobytes. Not a real cost.
- Every balance change requires constructing a movement, including the reason for it. This is
  friction by design.

**Gained**

- Complete auditability. [UC-12](../04-use-cases.md) is a query over data that already exists,
  not a feature requiring its own logging.
- Manual correction, weighing, discard and estimate approval are the *same operation* with
  different types. One mechanism serves all four requirements.
- Confidence becomes computable, because the history distinguishes measured entries from
  confirmed estimates.

## Alternatives rejected

**Stored balance with a separate audit log.** Two sources of truth that can disagree, and no
way to tell which is right when they do. The ledger *is* the audit log; keeping both is strictly
worse.

**Event sourcing with full rebuild.** Same benefits, far more machinery — event stores,
projections, replay infrastructure — for a system with four entities. The ledger is the useful
half of event sourcing without the ceremony.
