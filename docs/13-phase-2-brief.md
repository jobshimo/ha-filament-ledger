# 13 — Phase 2 Brief

Where the next session starts. Written while the context was fresh, so nobody has to
reconstruct it from commit messages.

---

## What is already true

The ledger runs inside Home Assistant. Four real spools are registered and mounted, the
printer is connected in hybrid mode, and every entity it exposes has been catalogued in
[12 — Field Notes](12-field-notes.md).

Everything below the adapter layer is done and tested: domain, application, SQLite. The
`print_job` and `pending_review` tables already exist in migration 0001, so Phase 2 needs no
schema change.

## The one thing to do first

**Answer Q4.** Run a print start to finish with the integration connected, then read the
per-tray attributes off the weight sensor.

```js
// in the Home Assistant frontend console
hass.states["sensor.a1_<serial>_peso_de_la_impresion"]
```

- **Attributes carry `AMS 1 Tray n` keys** → automatic deduction is viable. Build UC-04 as
  designed.
- **Attributes stay empty** → the product still works, but every print goes through the
  review queue. That is the designed degradation, not a failure — and it makes the estimator
  work in [07](07-consumption-estimation.md) load-bearing rather than a refinement.

Nothing else in Phase 2 should be built before this is known. Building UC-04 against a field
that never populates is the single most expensive mistake available here.

## Then, in order

1. **`BambuLabGateway`** against the boundary in [05 §5.8](05-ha-integration.md). State
   listeners plus the `bambu_lab_event` bus. No imports from `custom_components.bambu_lab`,
   no reaching into `coordinator.get_model()`.
2. **Fixtures.** Capture real payloads from the A1 before writing assertions.
   [09 §9.4](09-testing-strategy.md): hand-written fixtures encode what the developer
   *believes* the printer sends.
3. **UC-02 / UC-03** — RFID mount and unmount. Tag resolution returns a list: none →
   `UnknownSpoolDetected`, one → mount, several → `AmbiguousTagDetected`. Never auto-create.
4. **UC-04** — the only automatic deduction, with the missing-figure branch from
   [04](04-use-cases.md) step 2 built at the same time, not after.
5. **UC-05 / UC-06 / UC-07** — the review queue, slot-keyed with a frozen resolution.
6. **`LinearProgressEstimator`** only. The G-code estimator is Phase 4.
7. **The Review view in the panel** — currently an honest empty state.

## Traps already paid for

**Do not enable LAN mode on the printer.** It is a cloud kill switch, not a transport
setting. Hybrid is what is configured and it gives both halves.

**`tag_uid: "0000000000000000"` means no tag.** Not a value to match on.

**Entity ids are localised.** This instance is Spanish. Resolve through the device registry.

**The tag belongs to the spool, not the tray.** A tray is a position; spools move.

**Nothing at module level in `__init__.py` may import Home Assistant** — it makes the domain
unimportable on its own, and the CI job that installs everything except Home Assistant is
what catches it.

## Still open

- **Q2** — does the slicer's `used_g` already include flush? Answerable from one sliced
  `.3mf`, no printer needed.
- **Q3** — G-code retrieval rate. Only affects estimator accuracy, not feasibility.
- **Q1** — closed. Upstream distinguishes cancelled from failed on the event bus.

## Not built, and deliberately noted

`binary_sensor` for anomaly and connectivity, per-slot AMS entities, and the Spoolman
exporter. None of them block Phase 2; all of them are additive.
