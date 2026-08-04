# Action plan

Execution order for the findings in [REVIEW-FINDINGS.md](REVIEW-FINDINGS.md) and the requests in
[FEATURE-REQUESTS.md](FEATURE-REQUESTS.md). Grouped into releases, because a release is the unit
a user actually receives and because the branch model already makes `main` mean *shipped*.

## Strategy

**One work unit, one branch, one merge.** Every unit follows the same loop, and the loop does not
shorten under time pressure:

1. Specify the unit precisely enough that its acceptance is not a matter of opinion.
2. Implement it in an isolated worktree, tests alongside the behaviour they cover.
3. The four gates — `ruff check`, `ruff format --check`, `mypy`, `pytest` — green from the
   worktree, plus the HACS and hassfest jobs once it reaches CI.
4. **Verified independently of whoever wrote it**: the diff read, the gates re-run, and where a
   defect had a reproduction, that reproduction replayed against the fix. A passing test written
   by the same author that wrote the fix proves less than a replay of the original failure.
5. `develop` → `staging` → `main`, then the next unit.

**Ordering rule.** Anything that changes the shape of the model comes before the features built
on it. Doing it the other way means building twice.

**Migrations are numbered on landing, not on planning.** Two units in this plan need one; whichever
merges first takes `0004`.

---

## Release 1.1.1 — consolidate

Everything already written and verified, plus the two small ones. No new capability.

| Unit | Source | State |
|---|---|---|
| Brand mark and HACS validation | — | merged to `staging`, needs `main` |
| Refuse an approval the scale already counted | F1 | branch ready, verified |
| Spool listing breaks its ties | F2 | branch ready, verified |
| `find_by_tag` returns an ordered list | F6 | to write |
| `.claude/` out of version control | — | to write |
| Working documents committed | — | to write |

Closes with the manifest bump and the `v1.1.1` tag.

---

## Release 1.2.0 — the panel's shape

The three requests that are about the panel being usable rather than about the ledger being
right. Sequenced first because none of them touches the model, so they cannot conflict with the
model changes that follow.

1. **A layout shell every tab inherits** (request 3). Header, tab strip and view actions fixed;
   only the content scrolls. Written once here so the history's sticky header is inherited rather
   than re-implemented.
2. **A home for spool actions** (requests 1 and 2). One design decision covering both: where a
   spool's actions live, so that *Finished* and the retirement affordance stop being two floating
   glyphs. **[ Finished ]** reconciles to zero — no new movement type, no migration.
3. **History filters** (request 5). Date, colour, weight above/below, free text over the entry
   name, and one control that clears them. Filtering in SQL, not in the panel.

---

## Release 1.3.0 — attribution

The release that makes the situation this whole review started from recordable: a spool empties
mid-print and is replaced in the same tray.

1. **The review carries charges, not one spool per tray** (request 6). `slot_resolution` becomes a
   list of `{slot, spool_id, mg}`; `estimated_usage` is untouched because the printer really does
   report per tray. Migration rewrites every existing row losslessly. **[ Load the rest ]** falls
   out of the invariant.
2. **Partial reassignment** (F4). The same situation corrected after the fact rather than split
   before it. Shares the entity work above, which is why it is here and not earlier.
3. **Restoring the void of a whole-spool discard re-discards the spool** (F5). Needs the
   `movement_void` row to remember that its void performed an un-discard — a column, therefore the
   second migration of this release.

---

## Release 1.4.0 — the printer, and a confidence that means something

1. **Remaining time and real job duration** (request 7a). `remaining_time`, `start_time` and
   `end_time` exist upstream. The keys are read off a real entity registry before they are frozen,
   never guessed — the rule the gateway already states.
2. **Accumulated print hours** (request 7b). No upstream sensor exists, so this is our figure,
   summed from the job rows already stored, and labelled as what it is: hours this ledger
   observed. Not the machine's odometer.
3. **Confidence** (request 4). **Measurement first.** The reference instance now holds enough
   history to plot drift at reconciliation against consumption since the previous anchor — the
   figure [docs/07 §7.5](docs/07-consumption-estimation.md) deliberately left blank. The scale is
   chosen from that, not from another guess. Ships with the sentence that explains it: *300 g
   printed since you last weighed this*.

---

## Release 2.0.0 — more than one printer

Request 7c. A tray stops being a bare index and becomes printer + AMS + tray. Touches the domain
value, the schema's unique index, the gateway's discovery and the AMS view together. Migration is
unambiguous because every existing row belongs to the only printer this ledger has spoken to.

Major, because the websocket payload's shape changes for anyone consuming it.

**Before then**, in whichever release lands first: a second printer is currently ignored in
silence, with a warning only in the log. Surfacing that in the UI is small and does not wait.

---

## What would make me stop and ask

Full autonomy has an edge, and it is worth naming rather than discovering:

- A migration that cannot be made lossless. The plan asserts twice that one is; if that turns out
  to be wrong for real data, the design is wrong, not the data.
- Any change that would need `docs/` amended in a way that contradicts what it already specifies,
  rather than extending it. The documents are the contract.
- A confidence scale chosen without the measurement, because that is replacing one guess with
  another and the current one is at least documented as provisional.
