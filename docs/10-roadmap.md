# 10 — Roadmap

Phases are ordered so that each one produces something usable on its own. No phase exists
purely as scaffolding for the next.

---

## Phase 0 — Answer the open questions

**Before any production code.**

The four questions in [01 §1.6](01-vision.md) are answered with the physical procedures in
[09 §9.7](09-testing-strategy.md), on the actual A1.

Deliverables: recorded MQTT payloads for cancellations and failures; a purge measurement from a
two-colour print; a G-code retrieval success rate; a confirmed sensor population path. Findings
written back into [01](01-vision.md).

**Why first:** Q4 determines whether automatic deduction works at all, and Q3 determines
whether the accurate estimator is the default or a bonus. Building on assumptions and
discovering later that the sensors were never populated would invalidate a phase of work.

**A day of measurement here removes weeks of building on a guess.**

---

## Phase 1 — Ledger core

The domain and application layers, complete, with persistence. No UI, no printer.

- All value objects, entities, domain services
- SQLite repositories with triggers and migrations
- UC-01, UC-08, UC-09, UC-10, UC-11, UC-12 — everything not printer-dependent
- Full domain and application test suites; architecture tests in place from the start

**Usable outcome:** a working manual inventory driven through HA service calls. Register a
spool, weigh it, discard part of it, read its history. No printer required — which is exactly
the "manual inventory only" mode offered in the config flow.

**Exit criteria:** every rule in [02](02-domain-model.md) has a passing test. Architecture
tests pass. The seven statements in [01 §1.5](01-vision.md) that do not involve the printer are
satisfiable.

---

## Phase 2 — Printer integration

Connect to reality.

- `BambuLabGateway` implementing `PrinterGateway`
- Job lifecycle tracking; RFID detection
- UC-02, UC-03, UC-04, UC-05
- `LinearProgressEstimator` only
- HA entities, services, event bridge
- Config flow

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

## Phase 5 — Release

- Documentation for users, not just for developers
- HACS packaging
- Translations (English, Spanish)
- JSON export, and the optional Spoolman exporter
- Public repository

---

## Explicitly deferred

Not "never" — "not before the core is proven". Each would be easier to justify after real use,
and each would be speculative now.

- Cost tracking (money per spool, per print)
- Multi-printer support
- Filament drying cycle logging
- Barcode/QR labels for physical spools
- Consumption analytics and trends over time
- Slicer plugins

**None of these is designed for in advance.** Designing for a feature that may never be built
is how a clean architecture becomes an elaborate one — and the domain model in
[02](02-domain-model.md) is deliberately free of hooks, flags, and extension points that exist
only to serve a hypothesis.

---

## Sequencing principle

Every phase ends with something that works.

Phase 1 alone is a usable manual inventory. Phase 2 makes it automatic. Phase 3 makes it
pleasant. Phase 4 makes it precise.

If the project stopped after any phase, what exists would still be worth having. That is the
test a phase boundary has to pass.
