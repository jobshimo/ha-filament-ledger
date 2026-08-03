# 10 — Roadmap

Phases are ordered so that each one produces something usable on its own. No phase exists
purely as scaffolding for the next.

---

## Phase 0 — Answer the open questions

**Before any printer-dependent code. Runs in parallel with Phase 1.**

The remaining questions in [01 §1.6](01-vision.md) are answered with the procedures in
[09 §9.7](09-testing-strategy.md), on the actual A1.

Prerequisite, and it is not optional: **`ha-bambulab` must be installed and connected to the
printer.** Nothing in this phase can start before that.

Deliverables: a `print_weight` population rate across twenty completed prints in each of LAN
and Cloud configurations (Q4); a flush comparison from a sliced `.3mf`, confirmed against a
scale (Q2); a G-code retrieval success rate (Q3); confirmation that the cancel and fail events
behave as upstream documents them (Q1). Findings written back into [01](01-vision.md).

**Why it gates Phase 2 and not Phase 1.** Q4 determines whether automatic deduction works at
all and Q3 determines how good the interrupted-print estimate can get — both are questions
about the *printer*. Phase 1 touches no printer. An earlier draft of this roadmap held all
production code behind this phase, which meant an afternoon of physical measurement blocked
several weeks of work that does not depend on its outcome.

That gate was self-imposed and wrong. The whole point of putting the printer behind
`PrinterGateway` ([03](03-architecture.md)) is that the ledger does not wait for it.

**A day of measurement here still removes weeks of building on a guess — it just does not have
to be the first day.**

---

## Phase 1 — Ledger core

The domain and application layers, complete, with persistence. No UI, no printer.

**Can start immediately.** It depends on no open question and on no hardware.

- Project skeleton and toolchain from [11 — Development](11-development.md), including
  `mypy --strict` and the architecture tests, both wired into CI on the first commit
- All value objects, entities, domain services
- SQLite repositories with triggers and migrations
- UC-01, UC-08, UC-09, UC-10, UC-11, UC-12, and the manual paths of UC-02/UC-03 — mount
  and unmount as services and websocket commands. Everything not printer-dependent
- Full domain and application test suites; architecture tests in place from the start

The toolchain goes first, not last. `mypy --strict` is what makes the `Grams` type discipline
real rather than decorative ([09 §9.2](09-testing-strategy.md)), and retrofitting strict typing
onto a finished layer is a different and worse job than starting with it.

**Usable outcome:** a working manual inventory driven through HA service calls. Register a
spool, weigh it, discard part of it, read its history. No printer required — which is exactly
the "manual inventory only" mode offered in the config flow.

**Exit criteria:** every rule in [02](02-domain-model.md) has a passing test. Architecture
tests pass. The seven statements in [01 §1.5](01-vision.md) that do not involve the printer are
satisfiable.

---

## Phase 2 — Printer integration

Connect to reality.

- `BambuLabGateway` implementing `PrinterGateway`, against the boundary fixed in
  [05 §5.8](05-ha-integration.md) and fixture-tested with payloads captured from the real A1
- Job lifecycle tracking; RFID detection
- UC-04, UC-05, **UC-06, UC-07**, and the automatic RFID paths of UC-02/UC-03 — the manual
  mount/unmount paths shipped with Phase 1
- `LinearProgressEstimator` only
- HA entities, services, event bridge
- Config flow

UC-06 and UC-07 belong here, not later. A review that cannot be approved or dismissed is a
queue that only fills up, and the exit criteria below require resolving one.

**Usable outcome:** successful prints deduct automatically; cancellations open reviews
resolvable by service call.

**Exit criteria:** a real print deducts the correct amount without intervention. A real
cancellation opens a review and changes no balance until approved.

**`GcodeLayerEstimator` is deliberately absent.** Linear estimation is imprecise but honest,
and it is behind an approval queue where the user can correct it. Shipping the accurate
estimator later costs nothing, because the strategy pattern was there from the start.

---

## Phase 3 — The panel

The UI from [06](06-ui-spec.md).

- WebSocket API
- Panel registration and shell
- Inventory view, review queue, AMS view, spool detail and history
- Every dialog: register, weigh, discard, adjust
- Responsive and theme-native

**Usable outcome:** the product as specified. Everything reachable without a service call.

**Exit criteria:** all seven statements in [01 §1.5](01-vision.md) achievable through the UI
alone. Review queue verified on a phone — that is where it will actually be used, standing at
the printer.

---

## Phase 4 — Accuracy

Now that the system works, make its numbers better.

- `GcodeLayerEstimator` and the G-code parser
- `CompositeEstimator` wiring, with estimator provenance shown in the UI
- Purge accounting, informed by the Q2 answer
- Confirmed cancellation classification, informed by Q1 — applied retroactively to stored jobs,
  which the preserved raw fields make possible
- Confidence threshold tuning against real usage
- **Accuracy figures for [07 §7.5](07-consumption-estimation.md), from measurement**

**Exit criteria:** the estimation table in [07](07-consumption-estimation.md) contains measured
numbers instead of qualitative labels. The provisional thresholds in [02 §2.6](02-domain-model.md)
are either confirmed or replaced with tuned values.

---

## Phase 5 — Corrections & trash (v1.0)

**Runs before Phase 4, despite the number.** The ordering rule is need, not sequence — the
same rule that let Phase 1 start before Phase 0 finished. Phases 2 and 3 shipped, the owner
runs the ledger daily, and daily use produced this phase's contents: two visible defects and
the correction features a live ledger turns out to need. Accuracy work waits; a Cancel
button that does nothing does not.

Specified in full in [14 — Corrections & Trash](14-corrections-and-trash.md), on the
accounting decision of [ADR-0007](adr/0007-corrections-are-more-history.md): corrections are
more history, never less — movements stay immutable, every correction is new linked records,
default views may hide, the database never forgets.

- The dead-Cancel defect in every dialog, root cause diagnosed, fixed and hand-verified
- Edit spool — the panel UI over the already-shipped `spools/update`, with tag provenance
  (`tag_source`, migration 0003) and weight corrections that go through movements
- Reassign a charge to the spool that actually fed it, as a linked compensating pair
- Delete and restore — voided movements, deleted spools, a Trash tab, and the exact
  visibility and statistics rules for both
- The Printer tab — read-only, fed by what the gateway already discovers, honest when dormant
- Language (panel string table EN/ES + `translations/es.json`), account display, and a
  Settings tab over the config entry options

**Usable outcome:** the ledger the owner already uses, now correctable without ceremony and
honest about every correction.

**Exit criteria:** every acceptance criterion in [14](14-corrections-and-trash.md), and its
§14.9 hand-verification checklist executed on the owner's instance — the panel has no test
harness, so the checklist *is* the panel's test suite.

---

## Phase 6 — Public release (v1.1)

What was "Phase 5 — Release" before v1.0 existed, renumbered and widened: the owner's
corrections ship before the world's features, because a published defect is a support
burden and an unpublished one is a chore. Earlier documents that say "Phase 5" about
release-era features ([08 §8.6](08-data-model.md), [ADR-0002](adr/0002-reject-spoolman-as-foundation.md))
mean this phase; they are left unedited because a renumbering is not a change of intent.

Specified to contract level in [15 — Public Release](15-public-release.md), every item
flagged for final scoping before implementation:

- Cost per print — price on the spool, cost derived per movement, never stored
- Low-stock alerts with hysteresis — one alert per crossing, re-armed by refill
- Actor attribution on movements, and admin gating as a config option
  (default on for new installs, off for upgrades)
- HACS packaging, brands submission, semantic releases, user documentation
- JSON/CSV export, and the optional Spoolman exporter ([ADR-0002](adr/0002-reject-spoolman-as-foundation.md))
- Statistics view — hand-rolled SVG, per [ADR-0006](adr/0006-vanilla-panel.md)'s no-library rule
- Multi-printer — the largest item, gated behind its own design pass
  ([15 §15.7](15-public-release.md))

**Usable outcome:** an integration a stranger can install from HACS and trust.

**Exit criteria:** a clean HACS install on a fresh instance reaches a working panel with no
manual steps; the [15](15-public-release.md) acceptance shapes hold for every item that
survived scoping.

---

## Explicitly deferred

Not "never" — "not before the core is proven". Each would be easier to justify after real use,
and each would be speculative now.

Three graduated when real use proved them: cost tracking, consumption analytics and
multi-printer support are now scoped in [15 — Public Release](15-public-release.md)
(§15.1, §15.6, §15.7). That is the deferral mechanism working as intended — they were
deferred until the evidence existed, and then specified against evidence.

Still deferred:

- Filament drying cycle logging
- Barcode/QR labels for physical spools
- Slicer plugins

**None of these is designed for in advance.** Designing for a feature that may never be built
is how a clean architecture becomes an elaborate one — and the domain model in
[02](02-domain-model.md) is deliberately free of hooks, flags, and extension points that exist
only to serve a hypothesis.

---

## Sequencing principle

Every phase ends with something that works.

Phase 1 alone is a usable manual inventory. Phase 2 makes it automatic. Phase 3 makes it
pleasant. Phase 4 makes it precise. Phase 5 makes it correctable. Phase 6 makes it public.

If the project stopped after any phase, what exists would still be worth having. That is the
test a phase boundary has to pass.
