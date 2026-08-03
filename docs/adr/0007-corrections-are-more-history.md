# ADR-0007 — Corrections are more history, never less

**Status:** Accepted
**Date:** 2026-08-03
**Depends on:** [ADR-0001](0001-append-only-ledger.md)

## Context

v1.0 ([14 — Corrections & Trash](../14-corrections-and-trash.md)) adds three operations the
owner asked for in plain words: *delete a movement and get the grams back*, *restore it from
a trash*, and *move a charge that landed on the wrong spool onto the right one*. Every one of
them sounds like mutation.

Movements are immutable at three independent layers, and each layer was placed deliberately:

- The entity has no setters, no update method, no delete
  (`domain/model/movement.py:1-7`).
- The repository port exposes only `append` and reads — "immutability is enforced by the
  shape of the interface, not by a comment asking politely"
  (`domain/port/repositories.py:55-62`).
- The database aborts any `UPDATE` or `DELETE` on the `movement` table with triggers
  (`infrastructure/persistence/migrations/0001_initial.sql:108-118`), and
  `tests/architecture/test_layers.py` plus the repository suite keep both true.

Three options for reconciling the owner's request with that rule:

1. **Weaken the rule.** Delete rows, or add a `voided` flag that an `UPDATE` sets.
2. **Refuse the features.** The ledger already offers `MANUAL_ADJUSTMENT` for everything.
3. **Express every correction as new records** — reversals, reinstatements, compensating
   pairs — each linked to what it corrects, with the original untouched.

## Decision

Option 3. **A correction adds history; it never subtracts it.**

- **Voiding a movement** appends a `VOID_REVERSAL` movement — the exact negation of the
  voided entry — and inserts one row into a new `movement_void` status table that links the
  two. The `movement` table and its triggers are untouched.
- **Restoring a voided movement** appends a `REINSTATEMENT` movement equal to the original,
  linked to it through `reinstates_movement_id`; the void row records the reinstatement and
  the chapter is closed.
- **Reassigning a charge** appends a compensating pair of `REASSIGNMENT` movements — a
  credit on the wrongly charged spool, an equal debit on the right one — both carrying
  `reassigns_movement_id`.

At every instant, for every spool, the balance remains

```
balance = Σ(movements)
```

with **no query consulting the void table to compute it**. A voided movement and its
reversal sum to zero, so the arithmetic of [ADR-0001](0001-append-only-ledger.md) needs no
amendment, no special case, and no second source of truth.

**The UI may hide voided chapters from default views. The database never forgets them.**
Hidden is not deleted — the retention rule of [08 §8.5](../08-data-model.md) already says
exactly this about discarded spools and resolved reviews, and voided movements join that
list rather than founding a new category.

## Rationale

**The whole value of the ledger is answering "why".** [ADR-0001](0001-append-only-ledger.md)
chose derivation over storage because a stored balance cannot explain itself. A deletion is
the same failure re-introduced through the back door: a balance that changed because a row
*vanished* is a balance with a hole in its explanation. A reversal explains itself — the
row says what it undoes, when, and at whose word.

**The triggers stay untouched, so the guarantee stays whole.** Two independent enforcements
of the central invariant were judged proportionate in 0001; carving an exception for a
`voided` flag would mean teaching the trigger to allow *some* updates, and a rule with an
exception is a rule that will grow another. `movement_void` is a separate table precisely so
that the movement rows never need one.

**Reversal pairs are honest under concurrency, flags are not.** Appends do not conflict —
the argument from 0001 applies verbatim. A flag flipped by two concurrent voids is a
read-modify-write race; two appended reversals are a visible, diagnosable double-entry that
the `movement_void` primary key refuses anyway.

**Restore comes for free, and it is symmetric.** A deleted row cannot be un-deleted with any
confidence; a voided chapter is reopened by appending one more entry. The trash is a *view*
over facts that already exist, not a holding pen for rows awaiting destruction.

**Chains are legal and honest.** Void, restore, void again — each step is one more linked
record. The history of the user's own corrections is itself history, and six months later
the sequence reads as what happened rather than as a row that flickered in and out of
existence.

## Consequences

**Accepted costs**

- Default views must filter. The global History query learns to skip open void chapters and
  the movements of deleted spools; the Trash view is the place that lists them. This is
  presentation work the flag-based design would also have needed.
- Two link columns on `movement` and one new table. The columns are nullable and written
  only at `INSERT`, so the immutability triggers never fire — `ALTER TABLE ADD COLUMN` does
  not touch existing rows.
- A spool's detail history grows rows a deletion-based design would have removed. This is
  deliberate: the per-spool view is the derivation surface
  ([06 §6.5](../06-ui-spec.md)), and hiding rows there would break the visible closed sum.
  Voided rows are *styled* as voided in the detail view, never omitted from it.
- Confidence and anomaly evaluation must be told about voids at the application layer —
  a voided estimate no longer bears on the balance, so it must no longer bear on the
  confidence either. The domain services stay pure; the application filters what it feeds
  them ([14 §14.4](../14-corrections-and-trash.md)).

**Gained**

- Undo, with an audit trail instead of at its expense. Every gram that moves, moves by a
  recorded entry — including the grams that move *back*.
- The accounting for v1.1 comes free. Cost-per-print ([15 §15.1](../15-public-release.md))
  sums movement costs per job; because reversals inherit `job_id`, a voided print charge
  nets its own cost to zero with no special case.
- One mechanism serves all three features. Void, restore and reassign are the same
  operation — append linked entries — with different linkage, exactly as weigh, discard and
  adjust were one operation with different types in 0001.

## Alternatives rejected

**Hard delete.** Requires dropping the triggers, breaks the running-balance derivation of
every later row, and makes [UC-12](../04-use-cases.md) retroactively a liar — the view that
promises "nothing above can be edited" would be displaying a history that has been edited.

**A `voided` flag on `movement`.** Requires `UPDATE`, therefore requires weakening the
trigger. It also splits the truth: `Σ(movements)` stops being the balance unless every
summation remembers to exclude flagged rows, which is a filter that will be forgotten
exactly once, silently. In the chosen design a forgotten filter mis-*displays*; it can
never mis-*count*.

**A tombstone table without reversal movements** — void rows that summations subtract.
Same defect from the other side: the balance would derive from two tables, and
[ADR-0001](0001-append-only-ledger.md) exists specifically to keep it derivable from one.

## Related

- [14 — Corrections & Trash](../14-corrections-and-trash.md) — the v1.0 spec that applies
  this decision: schemas, use cases, API and panel behaviour.
- [15 — Public Release](../15-public-release.md) — v1.1 features that lean on the linkage
  this ADR introduces.
- [08 §8.5](../08-data-model.md) — "hidden is not deleted", now load-bearing for movements
  as well as spools.
