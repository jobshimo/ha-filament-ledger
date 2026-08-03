# 15 — Public Release (v1.1)

The path from the owner's printer to anybody's. v1.0
([14 — Corrections & Trash](14-corrections-and-trash.md)) makes the ledger correctable;
v1.1 makes it publishable: money, alerts, multi-user attribution, packaging, export,
statistics, and the lifted single-printer assumption.

**Status of this document.** Every item below is specified to contract level — schema
shapes, API surfaces, the rules and their whys — but each is **flagged for final scoping
before implementation**: v1.1 is the first release whose features were not demanded by
daily use, and the cheapest time to cut one is before it is built
([10 — Roadmap](10-roadmap.md), sequencing principle). An implementer picks an item,
confirms its scope with the owner, then builds against this contract. Nothing here may
be silently redesigned; a scope change gets written back into this document first.

The accounting substrate for all of it is
[ADR-0007](adr/0007-corrections-are-more-history.md): corrections are linked history,
balances are `Σ(movements)`, and every feature below inherits those invariants rather
than renegotiating them.

---

## 15.1 Cost per print

**Motivation.** N4 ([01 §1.3](01-vision.md)) deferred money on purpose: "the ledger
records grams; money is a later, additive feature." This is the later. The domain
already carries everything cost needs — amounts, jobs, opening weights — so cost is a
derivation, not a second ledger.

**Contract.**

- **Price lives on the spool**, nullable: what this physical reel cost. Migration (the
  next free number at implementation time, in the 0002 self-contained format):

  ```sql
  ALTER TABLE spool ADD COLUMN price_minor INTEGER CHECK (price_minor >= 0);
  ALTER TABLE spool ADD COLUMN price_currency TEXT;   -- ISO 4217, e.g. 'EUR'
  ```

  Integer minor units (cents), for the same reason movements are integer milligrams
  (`migrations/0001_initial.sql:89-91`): floating-point money drifts, and a ledger that
  drifts is a ledger nobody trusts. Both columns set or both null — enforced in the
  domain, as `tag_source` pairing is ([14 §14.2](14-corrections-and-trash.md)).
- **Cost is derived per movement, never stored**:
  `cost = |amount| × price / opening_weight`. Stored cost would be a second source of
  truth that a later price correction makes wrong; derived cost follows the price the
  way balances follow movements ([ADR-0001](adr/0001-append-only-ledger.md)).
- **Cost per print** sums movement costs by `job_id`. Because reversals and
  reassignment legs inherit `job_id`
  ([14 §14.3-14.4](14-corrections-and-trash.md)), a voided charge nets its print's cost
  to zero and a reassigned charge moves its cost to the right spool's price — with no
  special case. This is the payoff ADR-0007 predicted.
- **Rounding: currency rounds to 2 decimals, half-up, at display time only.**
  Arithmetic stays exact (`Decimal` over integer minor units); rounding is a
  presentation concern in `serialisers.py`, the one place rounding already lives
  (`serialisers.py:1-6`). Mixed currencies never sum: a monthly total is per-currency,
  and a spool without a price contributes "unpriced", not zero — a missing figure is
  not a figure of zero, in money as in grams.

**Surface.** Price fields join the register and edit dialogs (optional, clearly
labelled per-spool); `history_line`/`movement_line` gain nullable `cost` +
`currency`; review cards show the estimated cost beside the grams; monthly totals join
the statistics view (§15.6).

**Acceptance shape.** A priced spool's print shows its cost in History and detail; a
voided print charge nets to zero cost; unpriced spools render "unpriced" everywhere;
no stored cost exists anywhere in the schema.

---

## 15.2 Low-stock alerts

**Motivation.** The product exists so a spool never runs dry mid-print
([01 §1.1](01-vision.md)); today the user still has to look. The ledger should say so
itself, through the event surface that already exists for exactly this kind of
automation ([05 §5.5](05-ha-integration.md)).

**Contract.**

- **Threshold**: a global option (grams) in the config entry, editable in the Settings
  tab ([14 §14.6.4](14-corrections-and-trash.md)), plus an optional per-spool override
  column (nullable; null = use global; the per-spool value wins because the user set
  it for that spool specifically).
- **Trigger with hysteresis — fires once per crossing.** The alert fires when a
  movement takes a spool's balance from ≥ threshold to < threshold, and **re-arms only
  when the balance returns to ≥ threshold** (a refill, a reconciliation upward, a
  void's restitution). Without the re-arm rule every subsequent print below the line
  would fire again, and an alert that fires daily is an alert nobody reads — the
  approval-reflex argument of [ADR-0004](adr/0004-approval-queue-for-estimates.md),
  applied to notifications.
- The armed/fired state must survive restarts, so it is a column
  (`spool.low_stock_fired_at TEXT`, nullable), cleared by the re-arm transition — not
  an in-memory flag that a reboot resets into a duplicate alert.
- **Surface**: a `LowStockDetected(spool_id, balance, threshold)` domain event bridged
  as `filament_ledger_low_stock` (the automation hook, [05 §5.5](05-ha-integration.md)
  pattern), plus a Home Assistant repair issue so users without automations still see
  it. Discarded and deleted spools never alert; a `SEALED` spool never alerts (it has
  not been opened; its balance is its opening weight by construction).

**Acceptance shape.** Crossing fires exactly one event and one repair; ten more prints
below threshold fire nothing; a reconciliation above threshold re-arms; restart between
crossing and re-arm does not re-fire.

---

## 15.3 Actor attribution and admin gating

**Motivation.** The panel is shared by design — `require_admin=False` because "weighing
a spool is not an administrative act, and the queue only works if the person standing
at the printer can reach it" (`infrastructure/ha/panel.py:62-64`). The Phase 1
hardening audit (commit `a8657f8`) closed the accounting races and left the multi-user
question — *who* recorded a movement, and whether mutating commands should be gated —
deliberately open for the single-user v1. Publishing ends the single-user assumption.

**Contract.**

- **Movements gain an actor**: nullable `movement.actor_id TEXT` — the HA user id, or
  NULL for `AUTOMATIC` movements (a machine is not an actor; pretending otherwise
  muddies the provenance `MovementSource` keeps clean,
  `domain/value/movement_type.py:14-29`). Additive nullable column written only at
  INSERT — the immutability triggers are never confronted, the
  [14 §14.7](14-corrections-and-trash.md) precedent. **No backfill**: historic
  movements have no recorded actor and inventing one would be invented history — the
  `tag_source` backfill argument, applied identically.
- **Capture at the adapters**: websocket handlers read `connection.user`, service
  handlers read `call.context.user_id`, and pass the actor into the command objects.
  The domain does not know what an HA user is; it stores an opaque actor string —
  dependencies keep pointing inward ([03 §3.2](03-architecture.md)).
- **Panel**: History rows and spool detail show the actor's name (resolved via
  `hass` user lookup client-side; the header already shows the current user,
  [14 §14.6.3](14-corrections-and-trash.md)). AUTOMATIC rows keep reading
  "automatic" — the existing source badge already draws this line.
- **Admin gating becomes a config option**: `require_admin_for_mutations` — when on,
  every mutating WS command and service refuses non-admin actors (reads stay open;
  a read-only household member is the panel's whole point). **Default: on for new
  installs, off for upgrades.** A new install gets the safe default; an existing
  household that has been editing freely must not wake up locked out by an upgrade —
  the migration note in the config entry (`async_migrate_entry`, version bump) records
  which path applied.

**Acceptance shape.** A movement recorded via the panel stores the caller's user id; an
automatic deduction stores NULL; the gate on → non-admin mutation refused with a
readable error, reads unaffected; upgrade path leaves the gate off and says so in the
log.

---

## 15.4 HACS packaging

**Motivation.** [ADR-0003](adr/0003-custom-integration-over-addon.md) chose a custom
integration partly because HACS is its distribution channel. Publishing is mostly
discipline, not code.

**Contract**, with what the packaging pass delivered marked against each item.

- **✅ `hacs.json` at the repository root** (name, minimum HA version matching what CI
  tests against). Verified rather than extended: `name`, `homeassistant: 2026.7.4` and
  `render_readme: true` are the complete set this repository needs. `content_in_root`
  stays absent because the integration lives under `custom_components/`, which is the
  default; `zip_release` stays absent because releases ship repository contents rather
  than an attached archive, and the key is only meaningful paired with `filename`. A
  key added "for completeness" is a key that has to be kept true.
- **✅ `README.md` rewritten for users, not developers**: hero, requirements, HACS and
  manual install, the four config options in plain words, the first-run golden path, a
  section per panel view, how the accounting works, and a troubleshooting section whose
  entries are the questions the field notes predict. The explicit **hybrid-mode
  warning** is a callout in Requirements — never ask a user to enable LAN mode; it is a
  cloud kill switch, not a transport setting ([12 — Field Notes](12-field-notes.md)).
  **Screenshots are commented-out placeholders** (`docs/img/*.png`, one per view) —
  the markup is in place and the images are the owner's to capture from a real
  instance; an invented screenshot would be the one lie the rest of this document
  exists to prevent.
- **✅ Contribution and release surface**: `CONTRIBUTING.md` (the four gates, the no-HA
  subset, architecture tests as law, docs as the contract, and the §14.9
  hand-verification obligation for any `www/` change — there is no JS harness, so the
  checklist *is* the panel's test suite), `RELEASING.md` (the exact bump→tag→release
  sequence), and `.github/ISSUE_TEMPLATE/` — the bug template asks for HA version,
  `ha-bambulab` version, printer model, connection mode and **what History shows**,
  because this is a ledger and almost every question about it is answered by the
  entries.
- **⬜ A submission to `home-assistant/brands`** (domain `filament_ledger`, icon) —
  required for the integration to render properly in the UI, and it has review lead
  time, so it goes first. Not done: it needs an icon asset that does not exist in this
  repository yet. `RELEASING.md` carries the process and the 256×256/512×512
  requirement.
- **⬜ HACS default-store inclusion** (`hacs/default`) — blocked on brands and on a
  first published release. Until then the README documents the custom-repository path,
  which needs nothing from anybody else and is a normal way to ship.
- **⬜ Semantic releases via GitHub Actions**: tag-driven; the workflow verifies the gate
  suite (pytest, ruff, mypy, hassfest) on the tagged SHA, updates nothing by hand. Not
  built — `ci.yml` runs on push and pull request only, nothing runs on a tag.
  `RELEASING.md` documents the manual sequence and names this gap rather than implying
  a safety net that is not there.
- **⬜ Version discipline in `manifest.json`**: the `version` field
  (`manifest.json:13`, currently `0.2.0`) is the single version of record; the release
  tag must equal it, and the workflow fails the release when they diverge — two
  versions that can disagree will. The *rule* is now written in `RELEASING.md` and
  `CONTRIBUTING.md` (no pull request bumps the version; that is a release-time edit);
  the *enforcement* waits on the workflow above.

**Acceptance shape.** A clean HACS install on a fresh HA instance reaches a working
panel with zero manual file operations; the release workflow refuses a tag whose
manifest disagrees.

**What remains, in order.** Icon asset → brands PR → first tagged release → release
workflow → `hacs/default` submission. Only the first two have external lead time.

---

## 15.5 Export

**Motivation.** [08 §8.6](08-data-model.md) promised it: JSON export "for portability
rather than backup", and the Spoolman exporter as the optional bridge
[ADR-0002](adr/0002-reject-spoolman-as-foundation.md) reduced Spoolman to.

**Contract.**

- **Formats: JSON (complete) and CSV (flat).** JSON carries everything — spools with
  provenance and void tables, movements with linkage; it is the format a future import
  reads. CSV carries the two obvious tables (spools, movements) for spreadsheets.
  Voided movements and deleted spools are **included and marked** — an export that
  silently dropped them would contradict the retention rule it is exporting
  ([08 §8.5](08-data-model.md)).
- **Transport: a service writing to `/config`** (`filament_ledger.export`, fields:
  format, path defaulting under `/config/filament_ledger/`), not a websocket stream —
  a WS frame carrying an entire ledger is a payload-size gamble, and a file beside the
  database is what HA backups and users' own tooling already understand. The service
  fires an event with the written path when done.
- **Spoolman exporter**: per the original pointer, a mapping of spools onto Spoolman's
  filament/spool model, pushed to a configured Spoolman URL — optional, config-gated,
  and one-way. Flagged as the first candidate to cut in scoping: it serves users this
  project's own inventory already serves.

**Acceptance shape.** Export → wipe → the JSON contains every fact needed to rebuild
the ledger by hand; the CSV opens in a spreadsheet with sane headers; voided/deleted
rows are present and marked.

---

## 15.6 Statistics view

> **Shipped early, ahead of this release.** The Stats tab exists: period selector,
> totals, by-colour and by-material bars, print outcomes, biggest prints and measured
> print time, all served by one `filament_ledger/statistics` command over a read model
> in `application/query.py`. It is specified as built in [06 §6.7](06-ui-spec.md), which
> is now the document of record for it; this section keeps the contract it was built
> against and the two items still outstanding.
>
> **Still outstanding, and both waiting on §15.1's price field:**
>
> - **Cost totals.** No figure on the tab is in currency, because no spool stores a
>   price. Nothing was approximated in the meantime — an invented cost is worse than
>   an absent one.
> - **The waste-to-consumption ratio as a cost.** Waste ships as its own gram total
>   beside consumption, which is the honest half of the story; what it *cost* waits with
>   everything else.
>
> **Deliberately not built, and not waiting on anything:** consumption *by month* as a
> time series. Three fixed windows answer the question a household actually asks, and a
> monthly series over a ledger that is months old compares samples too small to mean
> anything ([06 §6.7](06-ui-spec.md) states the reasoning). It stays available to
> reconsider once a ledger with years in it exists.

**Motivation.** "Consumption analytics and trends" was explicitly deferred
([10 — Roadmap](10-roadmap.md)) until the core was proven and the data existed. The
data now exists — months of movements with types, sources, jobs and (after §15.1)
costs.

**Contract.**

- **Content**: consumption by month, by material, by colour; waste — `DISCARD`
  movements — against print consumption as a ratio; monthly cost totals per §15.1.
  The [14 §14.4.5](14-corrections-and-trash.md) stats table governs throughout:
  `DELETED` contributes nothing anywhere, voided chapters net out, discards are the
  waste series.
- **Aggregation is a read model** in `application/query.py` — a query has no business
  in the panel, and panel logic is the untestable layer
  ([14 §14.8](14-corrections-and-trash.md)). The WS command serves finished series;
  the panel only draws.
- **Charts are hand-rolled SVG.** [ADR-0006](adr/0006-vanilla-panel.md) stands: no
  framework, no bundler, no external libraries — which for charts means literally
  `<svg>` elements built by the render functions. This is stated as a constraint with
  its consequence: bars, stacked bars and simple line series are in budget;
  anything needing axes engines, zooming or tooltips-on-curves is out, and the view is
  designed to those limits rather than fighting them. Theming uses the same HA CSS
  custom properties as everything else.

**Acceptance shape.** Series match hand-computed sums over the same SQLite file;
a voided print charge is absent from its month; a discard shows in waste, a deleted
spool nowhere; the view renders in light and dark themes with no chart library in the
repository.

**Acceptance, as met.** `tests/application/test_statistics.py` builds each of those
scenarios through the real use cases on real SQLite — a deleted spool's consumption
counted nowhere, an open void chapter dropping out with its reversal, a discard in waste
and never in consumption, a discarded spool's prints still counted — and
`tests/ha/test_websocket_api.py::TestStatistics` pins the whole payload. No chart library
entered the repository; the charts are `<svg>` elements built by the render functions.

---

## 15.7 Multi-printer

**The largest item, and the only one with a hard design-first requirement.** Do not
begin implementation from this section alone; it defines the ground rules and the
blast radius, and demands a dedicated design pass first.

**Motivation.** N3 ([01 §1.3](01-vision.md)) scoped v1 to one printer and forbade
speculative fleet design. The gateway honoured it explicitly: "One printer, one AMS.
v1 targets a single ledger on a single machine; if the registry ever holds several …
the first (by identity) wins and a warning names the ones ignored"
(`infrastructure/ha/bambu_gateway.py:26-29`, enforced at lines 404-414 and 433-443).
Publishing makes multi-printer the most-requested issue on day one; better to have the
design ready than to grow it under pressure.

**Ground rules for the design pass.**

- **Slot keys become `(printer, slot)`.** Today `SlotIndex` 1..4 is globally unique
  (`domain/value/identifiers.py:41-58`) and everything slot-shaped — locations,
  review lines, tray readings, the unique mount index
  (`migrations/0001_initial.sql:42-44`) — assumes it. This is the deep cut: the domain
  value, the schema, the WS shapes and the panel views all widen together, and the
  migration must map existing rows onto the single known printer.
- **One gateway per printer**, selected by device — either config-entry subentries or
  a device selector in the options flow; the design pass decides which after checking
  what HA's subentry support looks like at implementation time. Discovery already
  groups by device id (`bambu_gateway.py:417-443`); the change is keeping every group
  instead of `min(groups)`.
- **One ledger, several printers** — not one ledger per printer. The inventory is the
  household's shelf; printers are consumers of it. A spool's location gains the
  printer dimension; the balance arithmetic does not change at all.
- **The single-printer install must not notice.** Same panel, same views, no printer
  pickers shown when one printer exists. The feature is additive in the UI exactly as
  the migration is additive in the schema.

**Acceptance shape** (for the eventual implementation, after its design doc): two
printers deduct into one inventory with correct attribution; a v1.1 database migrates
with every existing movement and review mapped to the original printer; a
single-printer instance's UI is pixel-identical to before.

---

## 15.8 Traps already paid for

The [14 §14.10](14-corrections-and-trash.md) list applies to this release in full —
uow non-reentrancy (`application/review_queue.py:109-128` precedent), the hassfest
brace rule (`5e0073b`), PEP 758 as formatter canon (`bambu_gateway.py:318-323`),
string-keyed JSON maps (`websocket_api.py:52-56`), focus-stealing re-renders
(`www/filament-ledger-panel.js:124-126`), `esc()` discipline, registry-based
discovery. Three additions specific to v1.1:

**Money is integer minor units end to end.** The first `float` price that enters the
system is the last trustworthy cost report it produces. The `Grams` discipline
([02 §2.2](02-domain-model.md)) is the template; follow it exactly.

**Translated surfaces grow with every feature here.** Each new string lands in
`www/i18n.js` (EN + ES) and, where backend-facing, in both `translations/*.json`
files in the same change — a feature that ships English-only re-opens the defect
[14 §14.6](14-corrections-and-trash.md) closed.

**The version of record is `manifest.json`.** Every release automation compares
against it and fails on divergence; no other file may carry a version.

---

## 15.9 Sequencing

Within v1.1, order by dependency and cuttability: **15.4 HACS packaging** first (brands
review has lead time, and everything else benefits from release automation) — its
documentation and contribution surface is done, and the icon, brands PR, release
workflow and default-store submission remain, marked item by item in §15.4 — then
**15.3 attribution** and **15.2 alerts** (small, self-contained), then **15.1 cost**
and **15.5 export**, then what remains of **15.6 statistics** — the view itself shipped
early ([06 §6.7](06-ui-spec.md)), and only its cost figures still want §15.1 — and **15.7
multi-printer** last, behind its own design pass. Every item is independently
shippable; the roadmap's phase test — stop anywhere and what exists is worth having —
applies within the release too ([10 — Roadmap](10-roadmap.md)).
