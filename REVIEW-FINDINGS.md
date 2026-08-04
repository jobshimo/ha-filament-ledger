# Review findings — pre-HACS pass

Working document. Every entry states what is wrong, how it was established, and what the fix
is. Entries are only listed once the claim survived an attempt to break it — a hypothesis I
chased and disproved is recorded under *Disproved* rather than deleted, so nobody spends an
afternoon re-raising it.

Scope of the pass: functional code only (`custom_components/filament_ledger/`, 15 211 lines).
The test suite is deliberately out of scope for this review.

Status legend: **OPEN** · **IN PROGRESS** · **FIXED** · **WON'T FIX**

---

## F1 · Approving a review after weighing the spool deducts twice — **FIXED, awaiting merge**

Fixed on `fix/review-approval-after-reconciliation` (`7bf174a`), in a worktree, not yet pushed.
The guard sits in the same pre-load loop as the discard guard and refuses before anything is
appended, using the existing `MovementRepository.list_since` — the port gained nothing:

> spool {display_name} was weighed after this print, so the estimate is already inside that
> measurement; dismiss the review instead

Verified independently of the fix's own tests: the reproduction below, replayed against the
branch, now ends at 916.0 g with the approval refused. Gates re-run from the worktree —
ruff, ruff format, mypy, and 909 passing.

**One thing the verification turned up.** The guard compares with `occurred_at > opened_at`,
strictly. A reconciliation stamped at *exactly* the moment the review opened slips through. It
surfaced because the first replay used the frozen `FakeClock`, which gave both events the same
timestamp — an artefact of the harness, not of the system: two separate user actions cannot tie
on a microsecond clock, and the review opens when a print ends, which is not when somebody is
holding a spool over a scale. Left as it is, recorded so the boundary is a known one.

**Severity: high.** A ledger reporting a balance that is wrong by the size of a print.

`ApproveReview` charges a reviewed estimate with no regard for whether the spool has been
reconciled since the review opened. A reconciliation sets the balance to the measured value,
and the scale already reflects the filament the reviewed print consumed — so the approval
charges it a second time.

The sequence is the one [docs/07 §7.4](docs/07-consumption-estimation.md) recommends: a print
fails, the user weighs the spool because the scale always wins, and clears the queue later.

**Reproduced** against the real use cases, wired by `tests/application/conftest.py::build_ledger`:

| Step | Balance |
|---|---|
| Spool registered | 1000.0 g |
| Print cancelled, review opened (84 g estimated, nothing deducted) | 1000.0 g |
| Spool weighed — the scale says 916 g, reconciliation delta −84.0 g | 916.0 g |
| Review approved | **832.0 g** |

The scale said 916. The ledger says 832.

**Why this is an oversight rather than a decision.** The same method already guards the
structurally identical case twenty lines earlier:

```python
if spool.is_discarded:
    # Also the honest accounting: discarding wrote off the whole balance,
    # so charging the estimate afterwards would count the loss twice.
    raise SpoolDiscardedError(msg)
```

The pattern was understood for discards and not generalised to reconciliations.
`reconcile_spool.py` never touches the review queue, and `review_queue.py` never mentions
`RECONCILIATION`.

**Direction of the error:** pessimistic — it under-reports what is left.
[docs/07 §7.1](docs/07-consumption-estimation.md) names the optimistic direction as the one it
most wants to avoid, so this is the less dangerous side. It is still silent, and it lands
exactly when the user trusts the number most, because they have just weighed the spool.

**Fix:** refuse the approval when a charged spool carries a `RECONCILIATION` whose
`occurred_at` is later than the review's `opened_at`, in the style of the discard guard, with a
message naming the remedy — dismiss the review (UC-07), the terminal state that already exists
for *there is no consumption to record*. Auto-dismissing on reconciliation was considered and
rejected: it resolves a decision silently, which the project's own philosophy forbids.

---

## F2 · One query orders without a tiebreak — **FIXED, awaiting merge**

Fixed on `fix/spool-listing-tiebreak` (`8218c86`), one line: `ORDER BY registered_at, rowid`,
ascending to match the ascending key, the same shape as the neighbouring reads. Gates re-run
independently: 907 passing, unchanged, because no test came with it.

**No test, deliberately, and the reasoning is worth keeping.** A regression test here cannot
fail against the unfixed code. `spool` has no index on `registered_at`, so the unfixed query
always goes through SQLite's sorter; probed with tied rows arranged so lexical id order is the
exact reverse of `rowid` order, across 2 to 10 000 rows and with `cache_size` at default, 0 and
1, every combination returned `rowid` order anyway on both SQLite 3.49.1 and 3.50.4. An index
scan would not change it either — index entries are `(key, rowid)`, so equal keys still come
back in `rowid` order. A test that goes green without the fix asserts the platform's incidental
behaviour rather than the code's contract, which is worse than no test at all.

---

## F6 · `find_by_tag` returns an unordered list — **OPEN**

**Severity: low.** A missing *ordering*, not a missing tiebreak — which is why it was flagged
rather than folded into F2: adding one changes what the query returns.

`infrastructure/persistence/spool_repository.py:136` has no `ORDER BY` at all. Three of its
four callers do not care — two filter by id and one only asks whether the list is empty. The
fourth does: `detect_spool.py:95` passes the list straight into
`AmbiguousTagDetected(candidates=…)`, and those candidates are what the AMS view offers the
user to choose between. Two spools sharing a tag could therefore be presented in a different
order on different reads.

Worth closing because the layer already holds itself to this standard elsewhere — the print-job
listing breaks ties explicitly *"so the listing is stable across calls"*.

---

### F2 original description

**Severity: low.** Cosmetic, and barely reachable.

`infrastructure/persistence/spool_repository.py:182`:

```python
f"SELECT {COLUMNS} FROM spool{where} ORDER BY registered_at"
```

Eleven of the twelve `ORDER BY` clauses in the persistence layer break ties on `rowid`. This
one does not, so two spools registered in the same instant come back in an undefined order and
the inventory could reorder between repaints.

Reaching it needs two registrations in the same microsecond: registration is one-at-a-time from
the UI, and `trays/sync` creates no spools by design. Listed because the repository's own
standard is to always tiebreak, and the fix is one clause.

---

## F3 · A duplicated print start has no guard — **NOT A DEFECT. I was wrong.**

I raised this as an asymmetry: `_ended` guards duplicate delivery twice over, `_started` not at
all. Investigated against the upstream source on the reference instance, the asymmetry turns out
to be **correct**, and the framing was mine, not the code's.

**The ending really can fire twice; the start cannot.** In `pybambu/models.py`, the three
terminal events have three separate producers — `event_print_canceled` on a `print_error` edge
(1111), `event_print_failed` (1117) and `event_print_finished` (1127) on `gcode_state` edges,
and the finish is *not* suppressed by `isCanceledPrint`. So a cancellation landing the printer
in `FINISH` fires both from one update. That is exactly what
`record_print_consumption.py` already describes: *"A cancel and a finish delivered together can
both correlate to one job."* The guard has a producer in source.

`event_print_started` has exactly one producer in all of pybambu, `models.py:1046`, inside a
stateful edge detector:

```python
if previously_idle and not currently_idle:
    self._client.callback("event_print_started")
```

A repeated payload makes `previous == current`, so it physically cannot re-fire. Verified: every
other match for that string in the integration is a translation or a comment.

**And a guard could not be written correctly anyway.** `PrintStarted` carries only a name and a
plan; the bus payload's `name` is the *printer's*, not the job's. The reference database refutes
each fallback — the same file was printed twice on 2026-08-03 with an identical name and plan
(content keying would merge two real jobs), and a cancelled job ended 14 seconds before the next
genuine print started (any time window wide enough to catch a duplicate swallows a real print).
Keying on state inverts the design, since `_running_job` returns the newest row.

`tests/application/test_print_tracking.py:102` pins it outright — *"two starts are two jobs, and
the newest is the one a later ending correlates to"*. A guard would break a test that exists on
purpose.

**What would change this:** upstream growing a second producer for `event_print_started` (worth
re-reading `models.py` around 1046 after any `ha-bambulab` upgrade), the event gaining a
job-identifying field, or a future feature treating `RUNNING` rows as authoritative rather than
as evidence — at which point the tolerance stops being free.

---

## F4 · Partial reassignment does not exist — **OPEN**

**Severity: medium.** A missing capability rather than a defect, but it makes a real situation
unrecordable.

When a spool empties mid-print and is replaced in the same AMS tray, the printer reports one
figure for that tray and `RecordPrintConsumption` charges all of it to whichever spool is
mounted when the job ends. The spool that actually fed the first half is charged nothing.

Nothing in the system can express the correction. `ReassignMovement` moves a charge whole —
`moved = abs(movement.amount)` — and `PendingReview` refuses to carry the same slot twice, so
the review path cannot split it either. `AdjustSpool` can patch the balances but writes
`MANUAL_ADJUSTMENT` with no `job_id`, which breaks per-print accounting.

**Fix:** an optional `amount` on `ReassignMovementCommand`, validated as
`0 < amount <= abs(movement.amount)`, emitting the compensating pair for that magnitude and
inheriting `job_id`, `review_id` and `reassigns_movement_id` as the whole-charge path already
does. No schema change.

---

## F5 · Restoring the void of a whole-spool discard leaves the spool in inventory — **OPEN**

**Severity: medium.** The arithmetic stays right; the spool's state does not.

`VoidMovement` treats the whole-spool `DISCARD` as a deliberate special case: voiding it
returns the entire balance *and* un-discards the spool, because leaving it `DISCARDED` would
strand those grams outside inventory. That is documented, reasoned, and correct
([docs/14 §14.4.1](docs/14-corrections-and-trash.md)).

`RestoreMovement` — the undo of that undo — has no matching branch. It appends the
`REINSTATEMENT` that takes the grams out again and leaves the spool exactly where it is.

**Reproduced** against the real use cases:

| Step | Balance | `is_discarded` | State |
|---|---|---|---|
| Spool registered | 1000.0 g | False | `SEALED` |
| Discarded whole | 0.0 g | True | `DISCARDED` |
| Discard voided (the un-discard) | 1000.0 g | False | `ACTIVE` |
| Void restored | 0.0 g | **False** | **`DEPLETED`** |

The user said *I threw it away*, undid it, then redid it — and the system's answer is *it is
empty*, not *it was thrown away*. The spool leaves the waste figures and reappears in the
inventory as an ordinary empty reel.

**The fix is not a one-liner, and the reason is interesting.** `_is_whole_spool_discard`
derives its answer from *the discard being the last entry in the history*. After the void that
is no longer true — the `VOID_REVERSAL` is last — so the same derivation cannot be reused on
the restore path. The `movement_void` row would have to remember that this particular void
performed an un-discard, which is a new column and therefore migration `0004`.

That is a design decision rather than a repair, and the same sentence in `_is_whole_spool_discard`
already admits the underlying gap: *nothing stores which kind a `DISCARD` was*.

---

## Won't fix

### W1 · Destructive websocket commands are not admin-gated

`settings/update` carries `@websocket_api.require_admin`; `spools/delete`, `spools/discard` and
the movement void do not, so any authenticated non-admin user can reach them.

**Reviewed and deliberately kept.** Every one of those operations is reversible by construction
— the trash for deleted spools, reinstatable chapters for voids — and the panel registers with
`require_admin=False` on purpose, because the person standing at the printer has to be able to
reach the queue. Weighing, mounting and correcting are not administrative acts.

---

## Disproved

Hypotheses raised during the pass, chased, and found not to hold. Recorded so they are not
raised again.

### D1 · Confidence misreading chronology through `occurred_at`

`movements_since_anchor` treats list position as time order, and `occurred_at` is not insertion
order — so a movement written after a reconciliation but stamped earlier would sort before it,
staying inside the balance while dropping out of the confidence window.

**Not reachable.** Every write path stamps `occurred_at` from the same clock at the moment of
writing: `TrackPrintJob._ended` sets `ended_at = now` (Home Assistant's clock, not the
printer's), and `ApproveReview` uses `occurred_at = now`. Confirmed against the live database —
`occurred_at` and `recorded_at` differ by milliseconds and no spool has an ordering inversion.

### D2 · A subscription leaking when the panel is removed mid-setup

`_subscribeLive` resolves asynchronously, so a panel navigated away from during setup could
hold a live subscription with nothing left to close it.

**Handled.** `if (this.isConnected) this._unsubscribe = unsubscribe; else unsubscribe();`

### D3 · Reassignment chains manufacturing filament

A reassignment's debit leg is reassignable again, so a chain of corrections could drift.

**Survived attack.** Chains of up to twelve pairs, property-tested: the ledger-wide sum of
`REASSIGNMENT` entries stays exactly zero.

---

## Coverage

Read in full: the whole `domain/` layer, `application/review_queue.py`,
`application/record_print_consumption.py`, `application/reassign_movement.py`,
`application/adjust_spool.py`, `application/track_print_job.py`,
`infrastructure/persistence/database.py`, `infrastructure/ha/panel.py`, and the subscription
half of `infrastructure/ha/websocket_api.py`.

Still to read: the remainder of `application/`, most of `infrastructure/`, the package root, and
`www/filament-ledger-panel.js` beyond its escaping, lifecycle and update paths.
