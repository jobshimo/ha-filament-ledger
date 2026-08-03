# 14 — Corrections & Trash (v1.0)

The first release shaped by production use rather than by design. The owner has run the
ledger against the real printer since Phase 2/3 shipped, and this document is what that use
asked for: two visible defects, and the family of correction features —
edit, reassign, delete, restore — that a live ledger turns out to need.

Everything here stands on one accounting decision, made once and cited everywhere:
[ADR-0007](adr/0007-corrections-are-more-history.md). **Corrections are more history, never
less.** Movements stay immutable at all three layers
(`domain/model/movement.py:1-7`, `domain/port/repositories.py:55-62`,
`migrations/0001_initial.sql:108-118`); every correction below is expressed as new, linked
records; balances remain `Σ(movements)` at every instant; default views may hide, the
database never forgets.

This spec is written to be implemented cold. Every contract names the file and line it
extends, every schema change is full SQL, and every behaviour has a numbered acceptance
criterion. Where a rule exists, its why is next to it.

---

## 14.1 Defect — every Cancel button in every dialog is dead

### What the owner sees

Open any dialog. Click **[ Cancel ]**. Nothing happens. Clicking the dark area *outside*
the dialog closes it; the button that exists to close it does not.

### Root cause, diagnosed

The panel dispatches every click through one listener on its root element
(`www/filament-ledger-panel.js:122`), which resolves the nearest `[data-action]` ancestor
and switches on it (`_onClick`, lines 172-240). `close-dialog` is handled there
(lines 216-219). The dialog markup, however, wraps its body in a modal `div` carrying an
inline handler:

```html
<div class="scrim" data-action="close-dialog">
  <div class="modal" onclick="event.stopPropagation()">   <!-- line 943 -->
```

The inline `stopPropagation()` was meant to keep clicks *inside* the dialog from bubbling
to the scrim and closing it. It does that — by killing the bubble entirely, so no click
originating inside the modal ever reaches the root listener at all. The Cancel button
(`formActions`, line 1059) carries `data-action="close-dialog"` and sits inside the modal;
its clicks die at line 943. The scrim's own clicks originate outside the modal, bubble
normally, and work — which is exactly the asymmetry the owner reported.

Form submission still works everywhere because `submit` is a different event type,
dispatched to the root's `submit` listener (line 123); the inline handler only stops
`click`. That is why every dialog *functions* except for its Cancel — the diagnosis that
confirms the mechanism.

### Fix contract

Keep in-modal clicks from closing the dialog **without** killing action dispatch:

1. Remove the inline `onclick` from the modal `div` (line 943). No other inline handler
   exists in the panel; after this fix none may be introduced — inline handlers bypass the
   one dispatch path the panel has, and this defect is what that costs.
2. In `_onClick`, the `close-dialog` case learns one guard: when the **resolved action
   target is the scrim itself** but the click *originated inside* `.modal` — checked with
   `event.composedPath()` (or equivalently, `event.target.closest(".modal")`) — the close
   is ignored. A click whose nearest `[data-action]` is anything *other* than the scrim
   (the Cancel button, any future in-modal action) dispatches exactly as every other
   action does.

The guard lives in the dispatcher, not in the markup, because the markup already proved it
cannot host this rule safely.

### Acceptance criteria

1. Every dialog's **[ Cancel ]** closes it: new-spool, weigh, adjust, discard, mount,
   dismiss-review — and every dialog this release adds (§14.2-§14.4).
2. Clicking anywhere inside a dialog body — labels, inputs, empty padding — does **not**
   close it.
3. Clicking the scrim outside the dialog closes it, as before.
4. In-modal `data-action` buttons other than Cancel dispatch normally (regression guard
   for the dialogs this release adds).

### Test obligations

The panel has no JavaScript harness ([ADR-0006](adr/0006-vanilla-panel.md) accepts this
cost), so **this fix must be verified by hand**, dialog by dialog, and the verification
recorded in the checklist of §14.9. Do not mark this item done from reading the diff — the
defect being fixed was itself introduced by markup that read as if it worked.

---

## 14.2 Edit spool

### Motivation

The backend for editing shipped in Phase 3 — the audit commit that hardened Phase 1 notes
"`spools/update` exists as docs/05 promised" — and [06 §6.5](06-ui-spec.md) lists
**Edit details** among the spool actions. The panel never grew the dialog. From the
owner's seat this is the release's second defect: a promised, implemented capability that
cannot be reached.

### Contract

A **[ Edit ]** button joins the action bar of the spool detail view
(`www/filament-ledger-panel.js:902-906`, beside Weigh / Adjust / Discard). It opens an
edit dialog over the **existing** `filament_ledger/spools/update` command
(`infrastructure/ha/websocket_api.py:204-236`) backed by `EditSpoolDetails`
(`application/move_spool.py:87-123`).

**Editable, exactly as the shipped backend defines:** label, vendor, colour, material
(with `material_other` when the kind is `OTHER`), core weight. Absent and null both mean
"leave unchanged" — the schema's documented semantics (`websocket_api.py:208-210`) and
`Spool.with_details`' contract (`domain/model/spool.py:115-134`). The dialog pre-fills
every field from the loaded `spool_detail`.

**The opening weight is not edited, and the dialog contains no balance field.** That is
not an omission but the point ([06 §6.5](06-ui-spec.md)): there is no endpoint that sets a
balance, and this dialog does not become one. What the dialog offers instead is a
**weight-correction section** that generates a *movement*, so history explains every
number:

- **"Set remaining filament to __ g"** — an absolute restatement. Sent as
  `filament_ledger/spools/reconcile` with `includes_core: false` (the user is stating net
  filament, not a scale reading) and an automatic note naming the edit dialog as origin.
  Absolute restatements are reconciliations because that is what UC-08 *is*: making the
  ledger equal a number the user asserts, with the delta recorded and visible
  (`application/reconcile_spool.py:48-110`).
- **"Add / remove __ g"** — a relative fix. Sent as `filament_ledger/spools/adjust` with a
  required reason (`application/adjust_spool.py:136-153` refuses a blank one, and the why
  is printed in the dialog already shipped: an unexplained adjustment is
  indistinguishable from a bug).

Both fields empty means no correction call. The panel submits `spools/update` first, then
the correction if one was entered — two commands, in that order. If the correction fails
its validation, the metadata edit stands and the dialog re-opens showing the error: the
two writes are independent facts and pretending they were atomic would mean inventing a
transaction the API does not have.

### The tag rule (owner's)

> A tag the printer attached is the printer's statement. A tag I typed is mine to change.

- A tag whose provenance is **DETECTED** — attached by the register-from-sync flow, which
  is the only path where a tray reading supplies the tag
  (`www/filament-ledger-panel.js:978-984`, `tray_sync.py:150-164` hints) — renders
  **read-only** in the edit dialog, with the provenance stated.
- A tag whose provenance is **MANUAL** — typed by the user at registration or here — is
  editable: it can be changed or cleared.
- A spool with no tag can be given one here; a tag attached here is MANUAL by definition.

This requires recording provenance, which v1 never did. Migration 0003 (§14.7) adds
`spool.tag_source` with values `MANUAL | DETECTED`, and **backfills every existing tag as
MANUAL** — provenance was never recorded, and claiming DETECTED for a tag whose origin
nobody knows would be invented history. MANUAL is the honest floor: it grants the owner
edit rights over tags the printer may in fact have supplied, which is a permission granted
too widely once, rather than a lie stored forever.

### Domain and API surface

- `Spool` gains `tag_source: TagSource | None` (a two-value `StrEnum`), paired with
  `tag_uid`: both set or both `None`. The pairing is enforced in hydration and the
  constructor, mirroring how `TagUid` itself enforces its rules
  (`domain/value/identifiers.py:68-97`); SQLite's `ADD COLUMN` cannot carry a
  cross-column `CHECK`, so the domain is the enforcement point and the column check
  covers only the value set.
- A new transition `Spool.with_tag(tag: TagUid | None, source: TagSource | None)` guarded
  by the rule above — a **separate** transition rather than a new `with_details`
  parameter, because `with_details` defines `None` as "unchanged"
  (`spool.py:127-134`) and clearing a tag needs `None` to mean *cleared*. Overloading one
  method with two meanings of `None` is how the next defect gets written.
- `EditSpoolDetails.execute` gains a tri-state `tag` parameter: an `UNSET` module
  sentinel (leave unchanged), `None` (clear), or a `TagUid` (set, provenance MANUAL).
  Attempting to set or clear a DETECTED tag raises a new domain error
  `TagNotEditableError`, which `guarded` (`websocket_api.py:95-111`) already turns into a
  websocket error the panel can show.
- Setting a tag that already belongs to another non-discarded, non-deleted spool requires
  `confirm_duplicate_tag: true` — the same rule, for the same reason, as UC-01
  ([04](04-use-cases.md)): Bambu tags identify a batch, duplicates are legal but must be
  deliberate.
- `filament_ledger/spools/update` gains two optional fields:

```
tag_uid: string | null        # absent → unchanged; "" is invalid; null → clear
confirm_duplicate_tag: bool   # required true when tag_uid collides
```

  Note the wire mapping: **absent means unchanged, null means clear** — this command's
  existing fields use null for "unchanged" (`websocket_api.py:208-210`), and the tag
  deliberately deviates because it is the only clearable field. The schema comment must
  state the deviation; a reader who assumes uniformity here writes the bug this section
  exists to prevent.
- `RegisterSpoolCommand` gains `tag_source`, defaulting `MANUAL`; the panel's
  register-from-sync path (`_onSubmit "new-spool"`, `www/filament-ledger-panel.js:254-271`)
  sends `DETECTED`, because the tag it forwards came from the tray reading, not the user.

### Panel behaviour

- The edit dialog mirrors the register form's fields and pre-fills from `_detail`. The
  colour input is the same `type="color"` control; material select plus
  `material_other` follow `newSpoolForm` (`www/filament-ledger-panel.js:961-987`).
- The DETECTED tag renders as text with a caption: *"attached by the printer — edit is
  disabled so the tag always matches the physical spool"*. The MANUAL tag renders as an
  editable input with a clear affordance.
- The weight-correction section is visually separated and captioned: *"This writes a
  movement to history — the edit itself never touches the balance."*
- On success: dialog closes, `refresh()` — the standard `guarded` flow (lines 159-168).

### Acceptance criteria

1. Editing label, vendor, colour, material, core weight round-trips: the detail view
   shows the new values after save; the balance and history are unchanged.
2. Leaving a field untouched leaves it unchanged server-side (absent-means-unchanged
   verified per field).
3. An absolute weight restatement produces exactly one `RECONCILIATION` movement whose
   delta is `stated − current`; a relative fix produces exactly one `MANUAL_ADJUSTMENT`
   with the given reason. Both visible in history with their notes.
4. Both correction fields empty → no movement is written.
5. A DETECTED tag cannot be changed via the WS command: the attempt fails with
   `TagNotEditableError` and the dialog never offers the input.
6. A MANUAL tag can be changed and cleared; a cleared tag stores `tag_uid NULL,
   tag_source NULL`.
7. Setting a tag that collides with another in-inventory spool fails without
   `confirm_duplicate_tag`, succeeds with it.
8. After migration, every pre-existing tagged spool reads `tag_source = 'MANUAL'`.
9. A spool registered from the sync strip stores `tag_source = 'DETECTED'`.

### Test obligations

- **Application suite** (real SQLite, `tests/application/conftest.py:1-9`): the
  `EditSpoolDetails` tag matrix — set / change / clear / refuse-DETECTED /
  duplicate-confirm — plus provenance persistence and the migration backfill
  (extend `tests/application/test_migrations.py`).
- **WS suite** (fake hass over the real backend, `tests/ha/conftest.py:1-11`): the
  extended `spools/update` schema — absent vs null tag semantics, error surface for
  `TagNotEditableError`.
- **Panel**: hand-verified via §14.9 — dialog prefill, read-only DETECTED rendering, the
  two-command submit order.

---

## 14.3 Reassign a charge to another spool

### Motivation

The wrong spool gets charged in exactly the ways UC-05/UC-06 anticipate — a review
resolved against the wrong slot assignment, a manual mount that lagged reality — and
today the only remedy is a pair of hand-written adjustments that lose the link to the
print. The owner's request: from the History row, *move this charge to the spool that
actually fed it*, with the job's accounting following the material.

### Contract

New use case **`ReassignMovement`** — one class, one public method, one transaction
boundary, the [04](04-use-cases.md) format:

**Input** — `movement_id`, `to_spool_id`, optional note.

**Preconditions**
- The movement exists, is **DECREASE-direction** (`MovementType.direction`,
  `domain/value/movement_type.py:41-43, 71-81`), and is **not voided** (no open
  `movement_void` row, §14.4). Increase-direction entries have no "charge" to move, and a
  voided charge has already been returned — reassigning it would move grams that are no
  longer anywhere.
- The target spool exists, is in inventory (not `DISCARDED`, not `DELETED`), and differs
  from the movement's spool.

**Flow** (one unit of work; events after commit, per the invariant every use case in
`application/` states)
1. Load and validate movement and both spools.
2. Append a `REASSIGNMENT` movement of **+X** on the wrongly charged spool, where
   `X = |movement.amount|`.
3. Append a `REASSIGNMENT` movement of **−X** on the target spool.
4. Both carry `reassigns_movement_id = movement_id`, and both **inherit the original's
   `job_id` and `review_id`** — per-print accounting follows the material, which is what
   makes cost-per-print ([15 §15.1](15-public-release.md)) come out right without a
   special case.
5. The original movement is untouched — [ADR-0007](adr/0007-corrections-are-more-history.md).
6. Re-evaluate confidence for **both** spools and run anomaly detection on both, with the
   existing services (`ConfidenceEvaluator`, `AnomalyDetector`) exactly as UC-06 steps
   7-8 do ([04](04-use-cases.md)).
7. Raise `MovementRecorded` per leg and a new `MovementReassigned(movement_id,
   from_spool_id, to_spool_id, amount)` bridged as `filament_ledger_movement_reassigned`
   ([05 §5.5](05-ha-integration.md) pattern).

**Postconditions** — source balance up by X, target down by X, original untouched, both
legs traceable to the movement they correct.

**Failures** — movement not found; movement not DECREASE; movement voided; target
missing, discarded, deleted, or identical to the source. All domain/application errors,
all surfaced through `guarded`.

`MovementType` gains `REASSIGNMENT` with direction `EITHER` (`movement_type.py:71-81`):
one type for both legs, distinguished by sign, because the pair is one correction and
splitting it into credit/debit types would make every query that asks "was this a
reassignment?" ask twice.

**A reassignment leg is itself a movement** — voidable (§14.4) and, for the debit leg,
reassignable again. Chains are legal and honest, and each link is recorded.

### API surface

```
filament_ledger/movements/reassign
  → { movement_id: str, to_spool_id: str, note?: str | null }
  ← { ok: true }
```

The note is optional, unlike UC-10's mandatory reason, and the difference is principled:
an adjustment without a reason is inexplicable, but a reassignment explains itself
structurally — the link names the movement it corrects and the pair names both spools.

No HA service is added. The correction surface is anchored in History rows the service
grammar cannot reference usably; the curated service list
(`const.py:38-50` — "one per use case") grows only when an automation story exists.

### Panel behaviour

- History rows — in the global History tab (`historyRow`,
  `www/filament-ledger-panel.js:586-598`) and the spool detail table — gain a row action
  **[ Reassign… ]**, offered only for DECREASE-direction, non-voided movements. The wire
  already carries `type` and `amount_g` (`serialisers.py:116-134`); the global row needs
  `movement_id` and `spool_id` added to `movement_line` so the action can name its
  subject.
- The modal states exactly what will happen with the grams before anything is sent:

  > *Return **84.1 g** to **PLA Basic Black**, and charge **84.1 g** to the spool you
  > choose. The original entry stays in history, marked as reassigned.*

  Spool picker excludes discarded and deleted spools, the same filter the review card's
  picker applies (`www/filament-ledger-panel.js:730-735`). Optional note field.
- After success: standard `guarded` refresh. Both legs appear in History labelled
  **Reassigned** (`HISTORY_LABELS` gains the key, lines 45-53), each row's detail naming
  the counterpart spool.

### Acceptance criteria

1. Reassigning a −84.1 g `PRINT_CONSUMPTION` writes +84.1 g on the source and −84.1 g on
   the target, both `REASSIGNMENT`, both linked, both inheriting `job_id`.
2. The original movement's row is byte-identical before and after (immutability
   triggers untouched — assert by direct SQL comparison).
3. Balances: source up by exactly X, target down by exactly X, every other spool
   unchanged.
4. An INCREASE movement, a voided movement, and a movement on a deleted target are each
   refused with a distinct error.
5. Confidence and anomaly state re-evaluated on both spools (a reassignment onto a spool
   near its anomaly threshold raises the event).
6. Reassigning a reassignment debit leg works and chains the linkage.
7. The modal's stated gram figures match what lands in the ledger, to one decimal.

### Test obligations

- **Application suite**: the full flow against real SQLite — linkage columns, inheritance
  of `job_id`/`review_id`, precondition matrix, event emission order (after commit).
- **WS suite**: command schema, error mapping.
- **Panel**: §14.9 checklist — action visibility rules (absent on INCREASE rows, absent
  on voided rows), modal wording matches the amounts sent.

---

## 14.4 Delete, restore, and the Trash

The owner's semantics, stated as requirements. Each quoted rule below is the contract;
the machinery underneath is [ADR-0007](adr/0007-corrections-are-more-history.md)'s —
nothing in this section updates or deletes a movement row.

### 14.4.1 Delete a movement

> **"The X on a History row — manual or automatic — asks: 'This returns X g to
> [spool]'. Confirming deletes the entry from the history I see, and the grams come
> back."**

**Under the hood:** confirming **voids** the movement. A new table records the void; a
`VOID_REVERSAL` movement returns the grams:

- `movement_void` row: `movement_id` (PK/FK), `voided_at`, optional `reason`,
  `reversal_movement_id` (FK). The `movement` table and its immutability triggers are
  **untouched** — the void row plus the reversal *are* the record.
- The reversal is the exact negation of the voided entry, type `VOID_REVERSAL`
  (direction `EITHER` — voiding a +6.2 g reconciliation must produce −6.2 g), source
  `USER_CONFIRMED`, inheriting the original's `job_id` and `review_id` so per-print
  accounting nets to zero.
- One unit of work: void row and reversal commit together or not at all.

**Voidable:** every movement type except two, each refused for a stated reason:

| Refused | Why |
|---|---|
| `OPENING_BALANCE` | A spool is born with a ledger entry ([UC-01](04-use-cases.md)); a spool whose opening entry is voided has a balance with no origin. The operation that removes a mistaken registration is *delete the spool* (§14.4.3), which retires the whole story coherently. |
| `VOID_REVERSAL` | A correction is corrected through its own flow — restore (§14.4.2) — never by voiding the correction, which would fork the provenance chain into two competing readings. |

A movement can be voided **at most once**: the `movement_void` primary key enforces it,
and the use case checks first so the error is readable. Chains re-void the
*reinstatement* (below), never the original — every link in the chain is a fresh
movement with its own void row, and the primary key holds.

> **"If the spool no longer exists — deleted or discarded — the modal explains, and
> offers: restore the spool first, or delete the entry without getting anything back,
> because there is nothing to return to."**

**Void with restitution is refused when the spool is `DELETED` or `DISCARDED`** — grams
only return to spools that are in inventory; a reversal landing on a retired spool would
be a balance change nobody can see. The modal branches:

- Spool `DELETED` → offer **[ Restore the spool first ]** (jumps to the Trash flow,
  §14.4.4) or **[ Void without restitution ]**.
- Spool `DISCARDED` → the restore path is *voiding its whole-spool `DISCARD` movement*
  (below), or **[ Void without restitution ]**.
- Void without restitution writes the void row with `reversal_movement_id NULL` and a
  **mandatory** reason — the record must say why nothing came back, because a NULL
  reversal with no explanation reads as a bug six months later. The movement still sums
  into its spool's balance (nothing reversed it); it merely leaves the default views.
  A without-restitution void is terminal: it cannot be reinstated (§14.4.2), because
  nothing was returned and "deduct it again" would double-charge. If the spool is later
  restored and the grams should move after all, UC-10's free-form adjustment is the
  honest tool, and the void row's reason is the pointer.

**One special case, stated rather than discovered:** voiding a **whole-spool `DISCARD`**
movement also clears `discarded_at` in the same transaction — the restitution returns
the entire balance, and leaving the spool `DISCARDED` would strand the returned grams
outside inventory. This is the un-discard: the void of the discard *is* the restore, one
recorded operation. (A whole-spool discard executed at zero balance wrote no movement at
all — `application/adjust_spool.py:75-80` — so there is nothing to void and such a spool
cannot return this way. Known, accepted, rare: the spool held nothing.) Voiding a
*partial* `DISCARD` changes no state.

### 14.4.2 Restore a movement from the Trash

> **"Restoring asks the symmetric question: 'Deduct X g from [spool] again?'"**

Restoring appends a `REINSTATEMENT` movement equal to the original (same sign, same
magnitude, direction `EITHER`), carrying `reinstates_movement_id` → the original; the
void row records `reinstated_at` and `reinstatement_movement_id`, closing the chapter.
The void row's two reinstatement columns are the only columns in this design that are
ever written after insert — `movement_void` is a status table, not a ledger, and the
movements it points at remain immutable.

**Preconditions:** an open void row exists (`reinstatement_movement_id IS NULL`);
the void had restitution (`reversal_movement_id IS NOT NULL` — see §14.4.1 for why the
other kind is terminal); the spool is in inventory (if it is `DELETED`, restore the
spool first — the symmetric rule to voiding).

**Chains are legal and honest.** Void m₁ (reversal m₂) → restore (reinstatement m₃) →
void m₃ (reversal m₄) → … Each step is one new movement plus one new void row keyed by a
*different* movement id. The primary key never bends and the full sequence reads as what
happened.

### 14.4.3 Delete a spool

> **"The X on a spool asks what actually happened: 'Did you throw it away?' — then it's
> waste and counts as waste. 'Was it registered by mistake?' — then it was never really
> here."**

The intent modal has exactly these two paths:

- **Thrown away** → the existing `DISCARD` flow, unchanged
  (`application/adjust_spool.py:46-124`, UC-09): a real-world event, counted as waste in
  every statistic, spool retained with history intact.
- **Registered by mistake** → **`DELETED`**: a bookkeeping retraction. New nullable
  column `spool.deleted_at` (migration 0003); a new `SpoolState.DELETED` derived from it
  exactly as `DISCARDED` derives from `discarded_at`. `DELETED` and `DISCARDED` are
  mutually exclusive by flow (the modal is the only entry point and a discarded spool
  never shows the X); if both are ever set by defect, `DELETED` wins for display.

Deleting:
- moves the spool out of inventory and its movements out of default History — **driven
  by the spool's state, not by per-movement void rows**: retracting a registration is
  one fact about the spool, and stamping forty movement voids would record it forty
  times;
- **frees the slot**: location is cleared to storage in the same transaction, and the
  partial unique indexes learn `AND deleted_at IS NULL` (§14.7) so the invariant "one
  spool per slot" ignores deleted spools the way it already ignores discarded ones
  (`migrations/0001_initial.sql:42-48`);
- writes no movement — deletion is a location-and-state change, and the strict
  separation of location change from quantity change ([UC-03](04-use-cases.md)) extends
  to it.

> **"Restore brings the spool back — and its history comes back with it."**

Restoring clears `deleted_at`; the spool returns to inventory in storage (its old slot
may be occupied; it was freed on delete and is not reclaimed), and its movements
reappear in default History automatically because visibility was derived from the
spool's state all along.

### 14.4.4 The Trash tab

`TABS` (`www/filament-ledger-panel.js:32-37`) gains **Trash**, after AMS — the
correction surfaces sit behind the daily ones. Two sections:

- **Spools** — every `DELETED` spool: swatch, name, material, balance at deletion,
  movement count, deleted-when, **[ Restore ]**.
- **Movements** — every *open* void chapter: the voided entry (spool, type label,
  amount, when voided, reason), and either **[ Restore ]** or — for without-restitution
  voids — the explanation in place of the button: *"nothing was returned when this was
  deleted; the ledger still counts it."* Closed chapters (reinstated) do not appear:
  the Trash lists what is currently out, not everything that ever was.

Empty state, in the teaching voice the panel already speaks
(`www/filament-ledger-panel.js:384-396, 550-561`):

```
       The trash is empty.

       Deleted spools and deleted history entries wait here,
       and everything can be restored. Nothing in the ledger
       is ever truly gone — a deletion is one more entry,
       not one less.
```

### 14.4.5 Visibility and statistics — the exact rules

**The one place nothing is ever hidden is the spool detail view.** [06 §6.5](06-ui-spec.md)
defines it as a derivation whose rows must reconcile to the header; hiding a voided row
there would break the closed sum in the very view that exists to prove it. Voided rows
render struck-through with a **voided** chip (and reversals/reinstatements with theirs);
the arithmetic stays whole.

Everywhere else, defaults hide what the owner deleted:

| Surface (code) | `DISCARDED` spool | `DELETED` spool | Open void chapter |
|---|---|---|---|
| Inventory / overview (`query.py:153-165`) | excluded by default filter | excluded by default filter | n/a |
| Stock totals (`query.py:238-253`, via `counts_as_stock`) | excluded | excluded | net zero by arithmetic — no rule needed |
| Global History (`query.py:180-213`) | movements **shown** — waste is history | movements hidden (spool skipped, same mechanism as the missing-spool skip at lines 195-199) | original and reversal hidden; listed in Trash |
| Spool detail (`query.py:167-178`) | shown in full | reachable from Trash, shown in full | shown, styled voided |
| Needs-weighing count (`query.py:251`) | excluded (not in overview) | excluded | n/a |
| Review card spool picker (`panel:730-735`) | excluded already | excluded | n/a |
| Statistics (`query.py` · `Queries.statistics`, shipped — [06 §6.7](06-ui-spec.md)) | `DISCARD` movements **count as waste**, and the spool's prints stay counted | excluded from everything | a voided `DISCARD` is not waste — the void says it never happened |

A movement is *hidden as voided* iff it has an open void row, or it is the
`VOID_REVERSAL` of one. A closed chapter (reinstated) shows all three rows in the global
History, labelled — the net is honest and the story is complete.

**Confidence and anomaly evaluation ignore open void chapters.** A voided estimate no
longer bears on the balance, so it must not keep a spool at `LOW`
([02 §2.6](02-domain-model.md)); the application layer filters the history it hands
`ConfidenceEvaluator` — the voided originals and their reversals drop out as a pair,
which is arithmetically neutral and semantically right. The domain service stays pure;
[ADR-0007](adr/0007-corrections-are-more-history.md) records this as an accepted cost.

### Domain and persistence surface

- New port `MovementVoidRepository`: `append(void)`, `get(movement_id)`, `list_open()`,
  `record_reinstatement(movement_id, reinstatement_id, at)`. The `MovementRepository`
  port is **untouched** — it still exposes no update and no delete, and
  `test_movement_repository_exposes_no_mutation`
  ([09 §9.5](09-testing-strategy.md)) keeps guarding exactly that interface. The void
  table gets its own port because it is a different thing: a status record about a
  movement, not a movement.
- New use cases, one class each ([04](04-use-cases.md) format): `VoidMovement`,
  `RestoreMovement`, `DeleteSpool`, `RestoreSpool` — plus `ReassignMovement` (§14.3).
  Each brackets its read-compute-write in one unit of work and publishes after commit,
  the invariant every existing use case states in its comments.
- New domain events, bridged with the `filament_ledger_` prefix
  ([05 §5.5](05-ha-integration.md)): `MovementVoided`, `MovementReinstated`,
  `MovementReassigned`, `SpoolDeleted`, `SpoolRestored`.
- New spool transitions: `deleted(at)` (guards: not discarded, not already deleted;
  clears location), `restored()` (clears `deleted_at`), `restored_from_discard()`
  (used only by the whole-spool-DISCARD void).

### API surface

```
filament_ledger/movements/void
  → { movement_id: str, reason?: str | null, without_restitution?: bool }
  ← { ok: true, returned_g: float | null }     # null when without restitution
filament_ledger/movements/restore
  → { movement_id: str }
  ← { ok: true, deducted_g: float }
filament_ledger/spools/delete
  → { spool_id: str }
  ← { ok: true }
filament_ledger/spools/restore
  → { spool_id: str }
  ← { ok: true }
filament_ledger/trash
  → { }
  ← { spools: [...], movements: [...] }        # shapes per §14.4.4's two sections
```

`without_restitution` must be explicitly `true` for the no-return branch — the server
refuses restitution voids on retired spools rather than silently downgrading them,
because a silent downgrade is a gram count that changed meaning without the user
noticing. The discard intent modal's "thrown away" path calls the **existing**
`spools/discard`; no new command duplicates it.

`movement_line` (`serialisers.py:116-134`) gains `movement_id`, `spool_id`, `direction`,
and `voided` so History rows can offer the right actions without a second query; the
spool-detail `history_line` (`serialisers.py:100-113`) gains `movement_id`, `voided`,
and the link fields needed for the chips.

### Panel behaviour

- History rows (both tables) gain **[ Delete ]** — the X of the owner's description —
  shown for voidable, non-voided movements. Confirmation modal, verbatim contract:
  *"This returns **X g** to **[spool]**."* — or the §14.4.1 branch when the spool is
  retired. Optional reason field (mandatory in the without-restitution branch, and the
  modal says why).
- Spool cards and the detail view gain the X → intent modal of §14.4.3, with the two
  outcomes explained in one line each: discard *"counts as waste in your statistics"*,
  delete *"treats it as never registered — restorable from the Trash"*.
- Trash tab per §14.4.4. Restore modals state the symmetric question with the real
  number: *"Deduct **X g** from **[spool]** again?"*

### Acceptance criteria

1. Voiding a −84.1 g print charge writes a +84.1 g `VOID_REVERSAL` and one void row;
   balance returns to its pre-print value; the original row is byte-identical.
2. Default global History no longer shows the voided pair; the Trash lists the chapter;
   the spool detail shows both rows, styled, and its displayed sum still closes.
3. Restoring writes a `REINSTATEMENT` equal to the original, closes the void row, and
   the balance drops by X again; the chapter leaves the Trash and all three rows appear
   in the global History.
4. Void → restore → void again works, producing a second, independent chapter keyed to
   the reinstatement movement.
5. `OPENING_BALANCE` and `VOID_REVERSAL` refuse to void, each with its own error.
6. Voiding a movement of a `DELETED` spool without `without_restitution: true` is
   refused; with it, the void row stores a NULL reversal and the mandatory reason, and
   the spool's balance is unchanged.
7. A without-restitution void refuses restoration, and its Trash row explains instead
   of offering the button.
8. Voiding a whole-spool `DISCARD` returns the balance and returns the spool to
   inventory in one transaction; voiding a partial `DISCARD` changes no spool state.
9. Deleting a mounted spool frees its slot immediately (another spool can mount there),
   sets `deleted_at`, writes no movement.
10. A deleted spool is absent from inventory, stock, needs-weighing and the global
    History; present in the Trash; its detail remains reachable and complete.
11. Restoring a deleted spool returns it to storage, restores its History visibility,
    and does not reclaim its old slot.
12. Stats table of §14.4.5 holds: discard still counts as stock-excluded waste;
    deleted counts as nothing, everywhere.
13. Confidence: a spool at `LOW` solely because of an approved estimate returns to its
    prior level when that estimate is voided.
14. The immutability triggers fire unchanged: a direct SQL `UPDATE`/`DELETE` on any
    movement row — voided or not — still aborts.

### Test obligations

- **Application suite** (real SQLite): all of the above except panel items — this is
  where the correctness lives, in the style of `tests/application/test_ledger.py`
  (scenarios over a wired ledger, `RecordingEventBus` assertions, direct SQL checks for
  trigger behaviour). Migration 0003 joins `tests/application/test_migrations.py`:
  applies cleanly from empty, from a populated v2 database, backfill correct,
  idempotence of the runner.
- **WS suite** (fake hass): every new command's schema, result shape, and error mapping;
  `trash` result shapes.
- **Panel**: §14.9 — modal wording matches amounts, action visibility rules, Trash
  sections and empty state.

---

## 14.5 The Printer tab

### Motivation

The gateway already reads the printer's state to drive the ledger
(`infrastructure/ha/bambu_gateway.py`); the owner has no surface that *shows* it beside
the inventory it feeds. This tab is a read-only glance — what is printing, how far
along, which tray is feeding — not a printer UI. Printer *control* stays a non-goal
(N1, [01 §1.3](01-vision.md)): `ha-bambulab` has its own cards, and duplicating them
adds risk with no benefit.

### Contract

New read-only command, served from what the gateway already discovers:

```
filament_ledger/printer/state
  → { }
  ← {
      "dormant": false,
      "status": "printing",              # print_status, verbatim state string
      "progress_pct": 42,                # int or null
      "current_layer": 71,               # int or null
      "total_layers": 209,               # int or null
      "job_name": "vase_final.gcode",    # UNKNOWN_JOB_NAME fallback (gateway:107)
      "error": { "active": true, "code": "216172782120927489" } | null,
      "online": true | null,
      "connection_mode": "local" | null,
      "active_tray": 4 | null,
      "trays": [ { ...per-slot shape of trays/sync, read-only... } ]
    }
```

- The job sensors come from the gateway's existing discovery —
  `PRINT_SENSOR_KEYS` (`bambu_gateway.py:74-84`): `print_status`, `current_layer`,
  `total_layers`, `print_progress`, `gcode_file_downloaded`, `print_error` — read
  through the same total, never-raising readers the job events use
  (`_sensor_state`, `_layer`, `_progress`, `_error_code`, lines 278-336).
- **Three sensors join discovery**: active tray, online, connection mode. The physical
  entities are catalogued in [12 — Field Notes](12-field-notes.md) (entity table,
  `bandeja_activa`, `en_linea`, `modo_de_conexion_mqtt`); their upstream
  `translation_key`s are **read off the reference instance's entity registry before the
  constant is frozen** — the same discipline that produced `PRINT_SENSOR_KEYS`, because
  entity ids are localised and a key nobody verified is a key that breaks in another
  language ([13 — Traps](13-phase-2-brief.md)). Unavailable or undiscovered sensors
  serialise as `null`, never as an invented value — the gateway's standing policy
  (`bambu_gateway.py:170-182`).
- The error code crosses the wire as a **decimal string** — HMS codes are 64-bit and a
  JSON number lands in JavaScript as a double (`serialisers.py:179-186` states the rule;
  the panel already owns the `hms()` formatter, `www/filament-ledger-panel.js:73-79`).
- `trays` reuses the per-slot shape of `trays/sync` (`serialisers.py:150-164`) computed
  **read-only**: the same repository reads `TraySync._outcome` performs
  (`tray_sync.py:98-121`) *without* running `DetectSpool` first. A tab that mutates the
  ledger by being looked at would violate the reader's reasonable model of "just
  looking"; the sync button on the Inventory tab remains the mutation path.
- **A dormant gateway answers `{ dormant: true }`** and the tab renders the honest
  empty state, in the voice the sync strip already uses
  (`www/filament-ledger-panel.js:426-430`): no printer connected, how to connect one,
  no spinner, no four invented trays.

**No new polling.** The ledger is push-shaped (`__init__.py:37-39`); this command reads
current entity state when called.

**Amended (v1.1): the tab is pushed to, and it keeps itself current.** The original rule was
"on open and on an explicit **[ Refresh ]** button — a glance has a moment, and the moment
is the user's". The moment was the wrong unit. A tab left open showing a finished print as
still running is not a glance, it is a lie with a timestamp, and the person it lies to is
standing at the printer.

**Nothing polls, and the panel never asks twice.** Discovery already resolves which entities
carry these figures — the tray sensors and the job sensors, `BambuLabGateway.watched_entity_ids`
— so the panel's one subscription ([06 §6.8](06-ui-spec.md)) watches **those**, and pushes a new
snapshot when one of them changes. Not on an interval, and not on every state change in the
house.

A first attempt got this wrong in a way worth recording: it treated Home Assistant handing over
a changed `hass` as the signal. That object is re-assigned whenever *anything* in the house
changes — several times a minute on a real instance — so a "minimum interval" stopped bounding
anything and started setting the pace. It was polling with better manners, and it was visible
as flicker. The entity list belongs to the layer that did the discovery, and the push belongs
to the server.

### Panel behaviour

`TABS` gains **Printer** between AMS and Trash. Layout: status line (state, job name,
error as HMS quad with the verbatim code in the title attribute — the review card's
pattern, lines 655-665), progress bar with layer counts, connection facts (online,
mode), and the four-tray strip with each tray's mounted spool from the ledger beside
what the printer reports. Every displayed figure that is `null` renders as an honest
dash, never as zero — a missing figure is not a figure of zero
([UC-04](04-use-cases.md) step 2's principle, applied to display).

### Acceptance criteria

1. With the printer connected and idle: status, connection mode and trays render; the
   progress section shows the honest no-job state.
2. Mid-print: progress, layers and job name match the `ha-bambulab` entities at the
   moment of refresh.
3. With `ha-bambulab` absent: `{ dormant: true }` and the teaching empty state — no
   error bar, no spinner.
4. An unavailable individual sensor renders a dash while the rest of the tab works.
5. Opening the tab and pressing Refresh cause exactly one command call each. **No timer
   exists and the panel issues nothing on its own**: a new snapshot arrives only when one of
   the gateway's own entities changes, pushed over the subscription, and is held rather than
   shown while a dialog is open or a field has focus.
6. Reading the tab never writes: movement count and spool locations are identical
   before and after (distinguishes this path from `trays/sync`).

### Test obligations

- **WS suite**: the command against a fake registry/states carrying the docs/12 shapes —
  populated, partially unavailable, and dormant. Fixture-first, per
  [09 §9.4](09-testing-strategy.md): capture the three new sensors' payloads from the
  real A1 before asserting on them.
- **Panel**: §14.9 — dash-not-zero rendering, dormant state, refresh behaviour.

---

## 14.6 Language, account and settings

### Motivation

The owner's household runs Home Assistant in Spanish; the panel speaks hard-coded
English. The backend already ships `translations/en.json` only. And the four options
chosen at install time (`config_flow.py:39-59`) are editable solely through the config
entry's options flow — three navigation levels away from the panel that they configure.

### 14.6.1 Panel i18n

- **A string-table module**, `www/i18n.js`, served from the same static directory the
  panel already loads from (`panel.py:41-53` registers the whole `www/` path, so a
  relative ES-module import works with no new registration). It exports the table and a
  `t(key, params)` lookup. No framework, no build step —
  [ADR-0006](adr/0006-vanilla-panel.md) governs this file exactly as it governs the
  panel.
- **Languages: EN complete, ES complete.** A key missing from the active language falls
  back to EN — a missing string must never render as a blank or a raw key, because the
  panel's strings carry the teaching voice and a hole in it reads as breakage.
- **Selection:** a manual override in `localStorage` (`filament_ledger.language`:
  `en`, `es`, or absent = auto) wins; otherwise `hass.locale.language` with a
  prefix match (`es-419` → `es`); otherwise EN. The override lives client-side because
  language of *this panel on this device* is a device preference, not ledger state.
- **Every user-facing string goes through the table.** The extraction inventory is the
  panel's current hard-coded English: **136 distinct strings** (counting each
  placeholder pattern once and each repeated literal — *Cancel*, *Dismiss*, *Note* —
  once), distributed as: the label maps `CONFIDENCE`/`ESTIMATORS`/`TABS`/
  `HISTORY_LABELS` (16), relative-time words (3), Inventory view and sync strip (24),
  AMS view (6), History view (12), Review view and card (21), spool detail (10), the
  six dialogs (41), and shared chrome — loading, error bar, actions (3). The
  implementing change must enumerate them key by key; the acceptance criterion is a
  panel source with **zero** user-facing literals outside `i18n.js`, which is a
  greppable review rule precisely because there is no JS harness to assert it.
- Braces in `i18n.js` placeholders are fine — the hassfest brace rule (§14.10) applies
  to `translations/*.json`, which hassfest parses, not to panel JavaScript, which it
  does not. Stating the boundary prevents both the over-caution and the mistake.

### 14.6.2 Backend translations

`translations/es.json`, mirroring `en.json` key-for-key — config flow, options, service
descriptions. Two rules, both already paid for:

- Key structure must match `en.json` exactly; hassfest validates the mirror.
- **No literal braces in any translation string** — hassfest reads `{}` as placeholder
  syntax and fails the build. This burned once (commit `5e0073b`: JSON-shaped examples
  in service descriptions had to be rewritten as prose); the ES file must be written
  prose-first, not translated brace-for-brace.

### 14.6.3 Account

The panel header shows who Home Assistant says is standing at it: `hass.user.name`,
with an **admin** badge when `hass.user.is_admin`. The why is forward-looking and
worth stating: the panel is deliberately not admin-only
(`infrastructure/ha/panel.py:62-64` — weighing a spool is not an administrative act),
so several household users share one surface; showing the identity readies the ground
for actor attribution in v1.1 ([15 §15.3](15-public-release.md)) and costs one line of
header today.

### 14.6.4 Settings tab

`TABS` gains **Settings**, last — configuration is the least-frequent surface. It
shows and edits the config entry options through two new commands:

```
filament_ledger/settings/get
  → { }
  ← { default_opening_weight: 1000, default_core_weight: 250,
      anomaly_threshold: 15, auto_mount_on_rfid: true }

filament_ledger/settings/update      # admin-only
  → { any subset of the four fields }
  ← { ok: true }
```

- Field names and bounds are exactly the config flow's
  (`const.py:13-16`, `config_flow.py:39-59`); the WS schema re-states the same ranges,
  for the same reason every adapter validates — a typo must be a message, not a stack
  trace (`websocket_api.py:52-56`).
- `settings/update` is registered with `@websocket_api.require_admin`: these values
  change how every user's ledger behaves, which *is* an administrative act — the
  considered inverse of the panel's own `require_admin=False`.
- The write goes through `hass.config_entries.async_update_entry(entry, options=…)`,
  which fires the registered update listener and **reloads the entry**
  (`__init__.py:229`, `_reload_on_options_change` at lines 252-253) — the existing,
  only mechanism by which option changes take effect (`DetectSpool` holds `auto_mount`
  as a plain value on precisely this promise, `application/detect_spool.py:50-54`).
  The tab says so before saving: *"Saving reloads Filament Ledger — a second or two."*
- The tab also hosts the language override of §14.6.1 (Auto / English / Español) —
  stored in `localStorage`, no backend call, and labelled as per-device.
- Non-admin users see the values read-only with a line explaining why — a hidden tab
  invites "it's broken"; a labelled read-only one teaches the model.

### Acceptance criteria

1. A Spanish HA profile renders the entire panel in Spanish with no override set;
   every view, dialog, empty state and error path included.
2. The override wins over the profile, per device, and survives reload.
3. A deliberately removed ES key falls back to its EN string, not to a blank or key.
4. `hassfest` passes with `es.json` present (mirror-structure and brace rules both).
5. The header shows the current user's name; the admin badge appears only for admins.
6. `settings/get` returns the effective options (`entry.data` overlaid with
   `entry.options`, the composition root's own merge, `__init__.py:84`).
7. A non-admin `settings/update` is refused by the framework; an admin update changes
   the option, reloads the entry, and the new value is observable in behaviour (e.g.
   register-dialog default weight).
8. The panel source contains no user-facing string literal outside `i18n.js`.

### Test obligations

- **WS suite**: both settings commands — shapes, bounds, admin gate (the fake
  connection carries an admin flag), and the reload side-effect (assert
  `async_update_entry` was invoked with the new options).
- **Backend translations**: hassfest in CI is the test; no unit test duplicates it.
- **Panel/i18n**: §14.9 — language walk-through in both languages; the
  zero-literals grep is a review-time check and is listed in the checklist.

---

## 14.7 Migration 0003 — one release, one migration

One release, one migration, the 0001 precedent: related schema lands together
(`migrations/0001_initial.sql:3-5` reserved two whole tables on the same argument).
Self-contained transaction whose **last statement records the version** — the 0002
format (`migrations/0002_scrub_absent_tag_sentinel.sql:9-11`): either everything lands,
including the version row, or nothing does.

Additive throughout, per the migration rules of [08 §8.4](08-data-model.md): columns
added, never removed or repurposed; movement rows never rewritten. The movement columns
are nullable and written only at `INSERT` — `ALTER TABLE ADD COLUMN` does not touch
existing rows and no statement here or in any use case UPDATEs a movement, so the
immutability triggers are never confronted, let alone modified.

```sql
-- 0003 — corrections, provenance, and the trash. See docs/14-corrections-and-trash.md
-- and docs/adr/0007-corrections-are-more-history.md.
--
-- Everything here is additive. The movement table gains nullable link columns written
-- only at INSERT time, so the immutability triggers (0001) never fire and are not
-- touched. Same shape as 0001 and 0002: one self-contained transaction whose last
-- statement records the version — either all of it lands, or none of it does.

BEGIN;

-- §14.2 — tag provenance. Existing tags backfill as MANUAL: provenance was never
-- recorded, and claiming DETECTED would be invented history. MANUAL is the honest
-- floor — it over-grants edit rights once rather than storing a lie forever.
ALTER TABLE spool ADD COLUMN tag_source TEXT
    CHECK (tag_source IN ('MANUAL', 'DETECTED'));

UPDATE spool SET tag_source = 'MANUAL' WHERE tag_uid IS NOT NULL;

-- §14.4.3 — a spool registered by mistake. Distinct from discarded_at on purpose:
-- DISCARDED is a real-world event that counts as waste; DELETED is a bookkeeping
-- retraction that counts as nothing, anywhere.
ALTER TABLE spool ADD COLUMN deleted_at TEXT;

-- The one-spool-per-slot and one-external-spool invariants ignore deleted spools the
-- same way they already ignore discarded ones. Recreating an index is not destructive;
-- the data is untouched.
DROP INDEX idx_spool_slot;
CREATE UNIQUE INDEX idx_spool_slot
    ON spool(location_slot)
    WHERE location_kind = 'AMS_SLOT' AND discarded_at IS NULL AND deleted_at IS NULL;

DROP INDEX idx_spool_external;
CREATE UNIQUE INDEX idx_spool_external
    ON spool(location_kind)
    WHERE location_kind = 'EXTERNAL_SPOOL' AND discarded_at IS NULL AND deleted_at IS NULL;

-- §14.3 / §14.4 — correction provenance, on the movement itself. Nullable, INSERT-only.
ALTER TABLE movement ADD COLUMN reassigns_movement_id  TEXT REFERENCES movement(id);
ALTER TABLE movement ADD COLUMN reinstates_movement_id TEXT REFERENCES movement(id);

-- §14.4.1 — the void record. One row per voided movement, ever: chains re-void the
-- reinstatement, never the original, so the primary key holds by design.
-- reversal_movement_id NULL means voided without restitution — the spool was already
-- out of inventory, there was nothing to return to, and reason says so.
-- The two reinstatement columns are the only post-insert writes in this design:
-- movement_void is a status record, not a ledger. The movements it points at stay
-- immutable.
CREATE TABLE movement_void (
    movement_id               TEXT PRIMARY KEY REFERENCES movement(id),
    voided_at                 TEXT NOT NULL,
    reason                    TEXT,
    reversal_movement_id      TEXT REFERENCES movement(id),
    reinstated_at             TEXT,
    reinstatement_movement_id TEXT REFERENCES movement(id),

    -- A chapter is closed by both facts together or neither.
    CHECK ((reinstated_at IS NULL) = (reinstatement_movement_id IS NULL)),
    -- A void without restitution returned nothing, so there is nothing to deduct
    -- again: it can never be reinstated.
    CHECK (reinstatement_movement_id IS NULL OR reversal_movement_id IS NOT NULL)
);

CREATE INDEX idx_void_open
    ON movement_void(movement_id)
    WHERE reinstatement_movement_id IS NULL;

INSERT INTO schema_version (version, applied_at) VALUES (3, datetime('now'));

COMMIT;
```

New `MovementType` values — `VOID_REVERSAL`, `REINSTATEMENT`, `REASSIGNMENT` — need no
schema change (`movement.type` is free `TEXT`); they join `_DIRECTION`
(`movement_type.py:71-81`), all three as `EITHER` with the per-type sign rules stated in
§14.3/§14.4, and they join the label maps: `_MOVEMENT_LABELS` (`query.py:268-276`) and
the panel's `HISTORY_LABELS` via the string table of §14.6.1.

---

## 14.8 Test obligations — the release as a whole

The house pattern, restated so nobody re-derives it:

- **Application tests run the real thing**: a wired ledger over a real SQLite file, no
  Home Assistant (`tests/application/conftest.py:1-9`). Every rule in this document
  that touches accounting lives here — voids, restores, reassignments, deletion
  semantics, the stats table, migration 0003.
- **Adapter tests run a hand-rolled hass over the real backend**
  (`tests/ha/conftest.py:1-11`): every new WS command's schema, shape and error
  mapping; the admin gate; the printer snapshot against captured fixtures.
- **The panel has no JS harness** ([ADR-0006](adr/0006-vanilla-panel.md)). Panel logic
  with rules in it belongs server-side where it is testable — which this spec already
  arranges: visibility filtering in the read models, voidability in the domain,
  amounts computed by use cases. What remains in the panel is rendering and dispatch,
  and that is verified by hand against §14.9. A rule that exists only in panel
  JavaScript is a rule in the one untestable layer — do not put any there.

Suggested new suites: `tests/application/test_corrections.py` (void/restore/reassign),
`tests/application/test_spool_deletion.py`, extensions to `test_migrations.py` and
`test_movement_history.py` (visibility), `tests/ha/test_websocket_corrections.py`,
`tests/ha/test_printer_state.py`, `tests/ha/test_settings.py`.

## 14.9 Hand-verification checklist

Executed on the owner's instance before the release is called done; each line initialled
in the PR description. The panel has no harness — this list is the panel's test suite,
and skipping it is skipping the tests.

1. All six pre-existing dialogs: Cancel closes, body-click does not, scrim-click does
   (§14.1, criteria 1-3).
2. Edit dialog: prefill, each field round-trips, DETECTED tag read-only, MANUAL tag
   editable, weight-correction writes the right movement type (§14.2).
3. Reassign modal wording matches the resulting ledger entries to one decimal (§14.3).
4. Delete/restore flows for a movement and for a spool, including the retired-spool
   branch and the Trash renderings (§14.4).
5. Printer tab in all three states: printing, idle, dormant; dashes never zeros
   (§14.5).
6. Full walk-through in Spanish, then with the override forced to English (§14.6).
7. Settings round-trip as admin; read-only view as non-admin (§14.6.4).
8. Phone-width pass over every new surface — the panel's primary venue is standing at
   the printer ([10 — Roadmap](10-roadmap.md), Phase 3 exit criteria).

## 14.10 Traps already paid for

Carried forward in the [13](13-phase-2-brief.md) manner: each of these has already cost
an afternoon once. Reading this list is cheaper.

**The unit of work does not nest.** `Database` is the unit of work and its lock is not
re-entrant (`infrastructure/persistence/database.py:72-76`); a use case that calls
another use case inside its own `async with self.uow` deadlocks or corrupts the
boundary. The precedent is `OpenPendingReview.open_within_unit`
(`application/review_queue.py:109-128`): the callee exposes an explicit within-unit
channel, publishes nothing itself, and the caller owns the commit and the events.
`VoidMovement`'s discard-restore special case and any correction that composes use
cases must follow it — never a nested `async with`.

**hassfest reads braces in translation strings as placeholders.** Commit `5e0073b`.
Prose, not JSON examples, in every `translations/*.json` string — ES included.

**PEP 758 is the formatter's canonical form.** An unparenthesised
`except InvalidValueError, ArithmeticError:` is Python 3.14 catching either exception
(`bambu_gateway.py:318-323`), not a Python 2 leftover. Do not "fix" it; the formatter
will put it back.

**JSON object keys arrive as strings.** Slot-keyed maps cross the wire as
`{"2": …}`; the schema's `Coerce(int)` is what reads them
(`websocket_api.py:52-56`), and the panel builds them from `dataset` strings
deliberately (`www/filament-ledger-panel.js:823-856`). Any new slot-keyed or id-keyed
map gets the same treatment on both sides.

**A full re-render steals focus from an input mid-keystroke.** The review card patches
itself in place through the `input` listener instead of re-rendering
(`www/filament-ledger-panel.js:124-126`, `_syncReviewCard` at 753-780). Any new dialog
with live-updating fields — the edit dialog's correction section is one — uses the same
patch-in-place pattern, never `render()` per keystroke.

**Every interpolation of user data goes through `esc()`.** The discipline is manual
([ADR-0006](adr/0006-vanilla-panel.md) accepted cost), the helper is
`www/filament-ledger-panel.js:55-58`, and review has to watch for it — reasons, notes,
labels and job names in every new modal and Trash row are user data.

**Entity ids are localised; discovery goes through the registry.** The three new
printer sensors (§14.5) resolve by `platform` + `translation_key`, verified on the
reference instance, exactly as `_discover_trays`/`_discover_print_sensors` do
(`bambu_gateway.py:383-443`) — never by entity id string.
