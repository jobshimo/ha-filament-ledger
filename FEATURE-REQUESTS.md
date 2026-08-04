# Requested features — analysis before implementation

Working document. Each entry records what was asked for, what the code does today, what the
change actually touches, and what has to be decided before anyone writes a line. Nothing here
is specified enough to implement yet; that is the point. Items graduate into `openspec`
proposals one at a time.

Sizing is stated as **UI only** (panel and websocket serialisers), **behaviour** (application
layer, no schema change), or **schema** (a migration, therefore forward-only and irreversible
for the user who runs it).

---

## Can all of it be built? Yes — every item, without losing anything

Stated plainly up front, because an earlier draft of this document called two of these
constraints *walls*, and that was the wrong word. Nothing here is blocked. Two items need a
model change rather than a feature bolted onto the existing one, and this section says exactly
what that change is, so nobody has to take the claim on trust.

| | Item | Verdict | Cost |
|---|---|---|---|
| 1 | Mark a spool finished | Buildable as-is | UI only, reuses reconciliation |
| 2 | A delete affordance that belongs | Buildable as-is | Design, then UI |
| 3 | Only the list scrolls | Buildable as-is | One layout shell |
| 4 | Confidence that means something | Buildable, needs measurement first | Behaviour |
| 5 | History header and filters | Buildable as-is | Behaviour, filtering in SQL |
| 6 | Split a print across spools | Buildable **after one model change** | Schema, migration 0004 |
| 7a | Remaining time, real job duration | Buildable as-is | UI only |
| 7b | Accumulated hours | Buildable, ours to compute | Behaviour |
| 7c | More than one printer | Buildable **after one model change** | Schema, its own release |

The two model changes are set out under items 6 and 7c. Both are **widenings** — every state the
system can represent today maps onto exactly one state afterwards, so no existing ledger loses a
fact and no feature is traded away to get them. That is the test a refactor has to pass here,
and both pass it.

---

## 1 · Mark a spool as finished

**Asked for:** a way to say *this spool is done* directly. Today the only route is entering
consumption until the balance reaches zero, which is neither obvious nor honest — the user does
not know what number to type.

**What exists.** Two mechanisms already sit underneath, and they are not the same thing:

- `DiscardFilament` with `DiscardMode.WHOLE_SPOOL` writes off the remaining balance as **waste**
  and retires the spool. It is what *I threw this away* means.
- A spool whose balance reaches zero derives `SpoolState.DEPLETED` on its own.

*Finished* is a third meaning and the ledger has no entry for it: the filament was **printed**,
not thrown away, and the remainder is the accumulated error of every estimate since the last
weighing. Recording it as `DISCARD` would inflate the waste figures with filament that was
actually used; recording it as `PRINT_CONSUMPTION` would attach a print's charge to no print.

**The honest reading is that finishing a spool is a reconciliation to zero.** `ReconcileSpool`
already computes `delta = measured − current` and writes a `RECONCILIATION`, which is exactly
the right entry: the user is asserting a measured truth of 0 g, and the delta is the drift that
truth reveals. That also makes the anomaly detector's `LARGE_RECONCILIATION_DELTA` fire when the
drift was large, which is information the user wants.

**Sizing: UI only.** No new movement type, no migration. A **[ Finished ]** action that calls
the existing reconcile path with a measured value of zero, with a confirmation naming the drift
it is about to record.

**To decide:**
- Does *finished* also retire the spool from the inventory list, or leave it visible at 0 g
  until deleted? `DEPLETED` already exists as a state and the AMS view has to keep showing an
  empty spool that is still physically loaded.
- Wording. *Finished*, *empty*, *used up* — this is a ledger, and the word chosen becomes the
  note on a permanent entry.

---

## 2 · The delete affordance does not belong to the design

**Asked for:** the **✕** on a spool card was never in the visual system and looks it. It needs
an affordance that belongs.

**What exists.** [docs/16 — The Visual System](docs/16-visual-system.md) defines the panel's
vocabulary, and the ✕ predates it. It also currently carries two different meanings depending on
where it sits — on a history row it voids an entry, on a spool card it opens the intent modal
(*discarded, or registered by mistake?*).

**Sizing: UI only.** But it is a design decision before it is a code change: the spool card
needs a place for actions that is not a floating glyph, and whatever is chosen has to work at
the three card sizes the visual system already defines and on a phone.

**To decide:** whether spool actions move into the detail view entirely, or the card grows a
deliberate action row. Related to item 1 — *Finished* needs somewhere to live too, and inventing
two separate homes for two spool actions is how this got untidy in the first place.

---

## 3 · Only the spool list should scroll

**Asked for:** the header, the tab strip and the **+ Nueva bobina** / **Sincronizar** buttons
must stay put; only the spools scroll under them.

**What exists.** The panel repaints by replacing `innerHTML` wholesale
([ADR-0006](docs/adr/0006-vanilla-panel.md)), and the whole view scrolls as one document.

**Sizing: UI only,** but it touches the layout every view is built on, not just the inventory —
and item 5 asks for exactly the same thing in the history. Worth doing once as a layout shell
that every tab inherits rather than twice as a patch.

**To decide:** whether the sticky region includes the tab strip. On a phone the strip already
has its own overflow handling (`_paintTabOverflow`), and pinning it costs vertical space where
there is least of it.

---

## 4 · Confidence changes in a way nobody can follow

**Asked for:** an explanation, and a fix — one or two prints and a spool drops from HIGH to
MEDIUM.

**This one deserves a straight answer, because the criticism is right and the diagnosis is not.**

The rule is not improvised. `ConfidenceEvaluator` implements three documented levels: LOW if any
estimate has landed since the anchor, MEDIUM once consumption since the anchor reaches 20 % of
the opening weight, HIGH otherwise — where the anchor is the last weighing, or the opening
balance if the spool has never been weighed. `ConfidenceThresholds` carries its own docstring
saying the figure is provisional and *"meant to be tuned against real usage in Phase 4, not
defended"*.

So it is a stated heuristic awaiting exactly this feedback. But the shape is wrong, and the
reference instance shows why:

| Spool | Opening | Spent since anchor | % | Level |
|---|---|---|---|---|
| 4355… | 1000 g | 0.0 g | 0 % | HIGH |
| f4b5… | 1000 g | 207.0 g | 20.7 % | MEDIUM |
| 6780… | 1000 g | 300.8 g | 30.1 % | MEDIUM |
| 7922… | 1000 g | 909.5 g | 91.0 % | MEDIUM |
| db2c… | 1000 g | 1000.0 g | 100.0 % | MEDIUM |

**One threshold produces two buckets across the entire life of a spool.** A reel that is 21 %
drawn and a reel that is 100 % drawn wear the same badge. The signal saturates after roughly one
ordinary print and then never moves again, so it stops carrying information at exactly the point
the balance starts drifting.

Two things are wrong, and they are separable:

1. **The scale.** A single cut-off cannot express a gradient. Either more levels, or a
   continuous *drift risk* the badge derives from — and if it stays three levels, the upper
   boundary has to be somewhere a user would recognise as *now it is worth weighing*.
2. **The silence.** Nothing anywhere tells the user what the badge means or why it changed. A
   spool that went to MEDIUM should be able to say *300 g printed since you last weighed this*.
   That sentence is already computable from `movements_since_anchor` and is arguably worth more
   than the badge itself.

**Sizing: behaviour** for the scale (`ConfidenceEvaluator` and its thresholds, plus the settings
that expose them), **UI only** for the explanation.

**To decide:** the thresholds are a product judgement, not an implementation detail. They should
be set against real data — the reference instance now has enough history to plot drift at
reconciliation against consumption since the previous anchor, which is the measurement
[docs/07 §7.5](docs/07-consumption-estimation.md) deliberately left unquantified.

---

## 5 · The history loses its header, and cannot be filtered

**Asked for:** a header that stays put while navigating, and filters — an arbitrary date or
range, colour, weight above or below a figure, free-text search over the entry name, and one
control that clears every filter at once.

**What exists.** `movement_history()` in `query.py` returns a flat recent list. The panel renders
it whole. There is no filter surface at any layer: not in the query, not in the websocket
command, not in the panel.

**Sizing: behaviour, and more than it looks.** Filtering has to happen in SQL, not in the panel
— a ledger's history grows without bound and shipping the whole table to the browser to filter
it there is the kind of decision that works for a year and then does not. That means the
`movements/history` websocket command grows a filter payload, `MovementRepository` grows a
filtered read, and the existing indexes need checking against the new predicates.

Free-text search is the one to be careful with: the searchable text lives in `note` and in the
job name, which are different columns reached differently, and `LIKE '%…%'` cannot use an index.
At this repository's data sizes that is fine and should be stated as a deliberate limit rather
than discovered later.

**To decide:**
- Filter by *colour* means the colour of the spool a movement belongs to, which is a join —
  worth confirming that is the intent rather than filtering the spool list.
- Whether filters survive a repaint and a tab change. They are state exactly like `_statsPeriod`
  already is, and the answer determines where they live.
- Whether the header staying put is the same layout shell as item 3. It should be.

---

## 6 · Reviews must split a print across several spools

**Asked for:** select more than one spool, put 10 g on one, and have a control that loads the
remaining 290 g onto the next. Fast, not fiddly.

**Why it does not work today.** `PendingReview` is keyed one line per slot, and its
`__post_init__` refuses the same slot twice, so the entity cannot express *this tray's grams came
from two spools*. It is not a missing button; the model has no place to put the second spool.

**The model change — and it is a correction, not a workaround.** The current shape conflates two
facts that merely happen to be one-to-one in v1:

- **The estimate is per tray, by nature.** The printer reports `used_g` per tray and can report
  nothing else. `estimated_usage` — JSON `{slot: mg}` — is already right and does not move.
- **The attribution is per charge.** Which spool, how many grams. Today that lives in
  `slot_resolution`, JSON `{slot: spool_id}`, which can only ever hold one spool per tray —
  and that is the whole limitation, in one column.

So the change is to replace `slot_resolution` with a list of charges, `[{slot, spool_id, mg}]`.
The estimate stays keyed by tray because that is what the printer knows; the attribution becomes
a list because that is what reality can be.

**Migration 0004 is lossless and mechanical.** Every existing `{slot: spool_id}` entry becomes
exactly one charge for that slot, carrying the slot's estimated amount — which is precisely what
the current UI already shows as the default. A `null` resolution becomes no charge, which is
already what an unresolved slot means. Nothing to interpret, nothing that can go wrong on
somebody's live ledger.

**What it buys beyond the ask.** `confirmed_charges` stops being a join of two maps and becomes a
direct read. The invariant gets stated where it belongs — for each tray, the charges sum to that
tray's confirmed amount — and **[ Load the rest ]** falls out of it for free: the remainder is
that tray's amount minus the charges entered so far, recomputed as the user types. The feature
that was asked for is what the corrected model does naturally.

**Do this together with F4** in [REVIEW-FINDINGS.md](REVIEW-FINDINGS.md). F4's partial
reassignment corrects a charge *after* it lands; this splits it *before*. Same situation — a
spool emptying mid-print — approached from both ends, and the entity change underneath is shared.

**To decide:**
- Whether the split is expressed on the review (before any movement) or only as a correction
  after (F4's route). Doing both is more surface than the problem needs; the review is the
  better home because it is where the user already has the figure in front of them.
- **[ Load the rest ]** needs a defined meaning when three spools are involved and the second
  amount is edited afterwards. The obvious reading — the remainder relative to the slot's total,
  recomputed live — is the one to write down before building it.
- `[ Distribute ]` already exists ([docs/07 §7.4](docs/07-consumption-estimation.md)) and splits
  a measured total across spools by the estimator's ratios. This request is its manual sibling
  and they should not become two unrelated mechanisms.

---

## 7 · Printer data: remaining time, accumulated hours, more than one printer

**Asked for:** hours remaining on a running print, the printer's own accumulated print hours if
they exist, and support for more than one printer.

**What upstream actually exposes**, read from `ha-bambulab`'s `definitions.py` on the reference
instance:

| Wanted | Available |
|---|---|
| Remaining time on the running print | **Yes** — `remaining_time` |
| Real job duration | **Yes** — `start_time` and `end_time` |
| Printer identity | **Yes** — `printer_name`, `serial` |
| Job identity | **Yes** — `subtask_name` |
| Lifetime print hours | **No such sensor exists** |

So remaining time is cheap: one more key in `PRINT_SENSOR_KEYS`, one more field on the printer
glance. The repository's own rule applies — the translation key is read off a real entity
registry before the constant is frozen, never guessed (`bambu_gateway.py`).

**Accumulated hours have no upstream source**, so they can only be *our* figure, summed from the
job rows this ledger already keeps. That is honest and worth doing, but it must be labelled as
what it is — hours this ledger observed, starting the day it was installed — and not presented
as the machine's odometer. `start_time`/`end_time` would make each job's duration the printer's
own rather than derived from when Home Assistant noticed, which is a real accuracy gain.

**Multi-printer is the largest item in this document by a wide margin.** The single-machine
assumption is not a UI shortcut; it is baked into the domain:

- `AmsSlot(slot)` carries a slot index and nothing else — no AMS, no printer. Two printers both
  have a tray 1.
- `reported_usage` is keyed `dict[SlotIndex, Grams]` for the same reason.
- The database enforces one spool per slot with a partial unique index over `slot` alone
  (migration 0001).
- `BambuLabGateway` documents the limit outright: *"One printer, one AMS … the first (by
  identity) wins and a warning names the ones ignored."*

Every one of those is a correct v1 decision, and every one of them changes together.

**The model change.** A tray is currently identified by a bare index. It has to become a
three-part reference — printer, AMS unit, tray — because that is what physically identifies a
tray once there is more than one machine. `serial` is already exposed upstream, so the stable
identity exists and nothing has to be invented.

That single value change is what the rest follows from: `reported_usage` keys by it, the partial
unique index covers all three parts instead of `slot` alone, and the gateway discovers per
printer rather than picking the first and warning into a log nobody reads.

**Migration is lossless here too.** Every existing row belongs to the one printer the ledger has
ever talked to, so each becomes `(that serial, AMS 1, its slot)`. The serial is known at
migration time because discovery already resolves it. No row is ambiguous, because a
single-printer history cannot be.

So it is buildable and nothing is traded away — but it reaches the domain, the schema, the
gateway and the AMS view at once. **That earns its own release**, not a line beside a sticky
header. Sequenced late here for that reason alone, not because of any doubt it can be done.

**To decide:** whether it lands in the next release or the one after. Until it does, the honest
interim is to surface the existing limit in the UI rather than only in the log — a second
printer today is silently ignored, and *silently* is the part worth fixing even before the rest.

---

## Ordering

Not a plan yet, but the dependencies are already clear:

- **3** (layout shell) comes before **5**'s sticky header — same mechanism, done once.
- **1** and **2** are one piece of design work, not two: both need a home for spool actions.
- **6** and **F4** share an entity change and should be specified together.
- **7**'s remaining time is independent and small; **7**'s multi-printer is its own release.
- **4** needs data before it needs code — the thresholds are a measurement, not a guess to
  replace with another guess.
