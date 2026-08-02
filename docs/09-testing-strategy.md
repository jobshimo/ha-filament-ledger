# 09 — Testing Strategy

The architecture in [03](03-architecture.md) exists to make this document short. A domain with
no framework dependencies is a domain that can be tested exhaustively, fast, and without
ceremony.

---

## 9.1 Shape

```
        ╱ integration ╲          few · slow · full HA harness
      ╱─────────────────╲
    ╱   infrastructure    ╲      some · real SQLite, fake printer
  ╱─────────────────────────╲
╱      domain + application   ╲  many · milliseconds · no I/O
─────────────────────────────────
```

The bottom layer carries the weight. Every rule in [02 — Domain Model](02-domain-model.md) is
verified there, because that is where the rules live.

## 9.2 Domain tests

**No mocks.** Entities and value objects are constructed directly. If a domain test needs a
mock, the domain has a dependency it should not have — the test failing to be simple is the
signal.

Coverage targets, by category rather than percentage:

**Value objects** — construction validity, arithmetic, equality, and the invalid cases that
must be rejected. `Grams` in particular: milligram precision, signed arithmetic, and the fact
that adding a bare number does not compile.

**State machines** — every legal `SpoolState` transition, and every illegal one rejected.
Illegal transitions matter more; a state machine that only permits is not a state machine.

**`BalanceCalculator`** — empty history, single movement, mixed signs, and the property that
matters most:

```
∀ movements: balance == opening + Σ(amounts)
```

Property-based, with generated movement sequences. This is the central rule of the system and
deserves more than example tests.

**`ConfidenceEvaluator`** — each level's boundary conditions, and the transitions between them
in both directions.

**`AnomalyDetector`** — negative balance, depleted-while-printing, oversized reconciliation
delta. Including the case that a negative balance is *reported*, not rejected.

## 9.3 Application tests

In-memory fakes for every port. Not mocks with expectations — real, simple implementations
that store in a dict. A fake repository is twenty lines and makes every test read as a
scenario rather than a script of assertions.

Each use case is tested for: the happy path, each documented failure mode, and the
postconditions stated in [04](04-use-cases.md).

The cases that must not be missed, because they are where correctness actually lives:

| Test | Why |
|---|---|
| Approving a review twice records one deduction | A duplicate ledger entry is indistinguishable from a real one afterwards |
| Recording the same print twice deducts once | Same reason, automatic path |
| An opened review changes no balance | The core guarantee of the queue |
| Confirmed amounts override estimates | The user's number always wins |
| An unknown RFID creates no spool | Never invent an opening weight |
| Reconciliation with zero delta writes nothing | A zero movement is noise |
| Discarding more than the balance succeeds and flags an anomaly | The ledger records reality |
| Unmounting records no movement | Location is not quantity |

## 9.4 Infrastructure tests

**Repositories** run against a real in-memory SQLite, not a fake. The point is to verify the
mapping and the constraints, and a fake database verifies neither.

Explicitly tested: the update and delete triggers **abort**; the one-spool-per-slot index
rejects a double mount; migrations apply cleanly from empty and are idempotent.

**Printer gateway** runs against recorded fixtures — real payloads captured from the actual
A1, not hand-written approximations. Hand-written fixtures encode what the developer *believes*
the printer sends.

**Estimators** run against real G-code files, including a multi-colour one with purge, and a
truncated file to prove `EstimationUnavailable` is raised rather than a wrong number returned.

## 9.5 Architecture tests

Executable versions of the rules in [03 §3.2](03-architecture.md):

```python
def test_domain_imports_no_framework(): ...
def test_application_imports_no_framework(): ...
def test_domain_does_not_import_infrastructure(): ...
def test_movement_repository_exposes_no_mutation(): ...
```

The last one inspects the port interface and asserts no method name suggests update or delete.
It looks pedantic until the afternoon someone adds one to fix a bug quickly.

**A rule that lives only in a document is a rule that will be broken.** These four keep the
architecture honest without requiring anyone to remember it.

## 9.6 Integration tests

`pytest-homeassistant-custom-component`. Deliberately few — they are slow, and everything they
could assert about business rules is already asserted faster below.

They verify only what genuinely requires HA: config flow completes; entities are created with
correct device classes; services are registered and dispatch to the right use case; WebSocket
commands return the expected shapes; domain events reach the HA bus with the documented
payloads.

## 9.7 Manual verification

Some things cannot be automated, and pretending otherwise produces tests that pass while the
product is wrong. Each open question from [01 §1.6](01-vision.md) has a physical procedure:

**Q1 — cancellation classification.** Cancel a print by hand; record `gcode_state` and
`print_error`. Induce a real failure; record both again. Repeat across several instances.

**Q2 — purge accounting.** Weigh two spools. Run a two-colour print. Weigh again. Compare the
real total against the integration's reported per-tray figures. The difference is the purge —
and whether it was already counted.

**Q3 — G-code retrieval.** Attempt retrieval for twenty consecutive jobs; record success rate
and latency.

**Q4 — sensor population path.** Test with Bambu Cloud, then LAN with FTP model data. Record
which populates the weight sensors.

Results are written back into [01 §1.6](01-vision.md), and the provisional rules that depend on
them are then either confirmed or replaced. **No estimator accuracy figure is published until
Q2 and Q3 are answered with real measurements.**
