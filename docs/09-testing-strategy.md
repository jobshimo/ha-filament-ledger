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
must be rejected. `Grams` in particular: milligram precision, signed arithmetic, and that
adding a bare number raises `TypeError`.

> An earlier draft claimed `Grams(1) + 5` "does not compile". Python has no compile step that
> would catch it, so as written the guarantee was decoration. It is made real by two things
> instead: a runtime `TypeError` covered by a test, and **`mypy --strict` running in CI**
> ([11 §11.3](11-development.md)) so the mistake is caught before the code is ever executed.
>
> The point of `Grams` is that it makes a class of bug impossible. A type discipline nothing
> enforces makes it merely discouraged.

**Derived state** — `SpoolState` and `Confidence` are functions, and both are specified as
total ([02 §2.2](02-domain-model.md), [02 §2.6](02-domain-model.md)). Tested as such: every
branch, both boundaries of every threshold, and a property test asserting that an arbitrary
movement history always yields exactly one state and exactly one confidence level. A rule
with a gap is a rule two implementers will resolve differently.

**State machines** — every legal `SpoolState` transition, and every illegal one rejected.
Illegal transitions matter more; a state machine that only permits is not a state machine.

**`BalanceCalculator`** — empty history, single movement, mixed signs, and the property that
matters most:

```
∀ movements: balance == Σ(amounts)
```

No separate `opening` term — the opening balance is the first movement
([02 §2.1](02-domain-model.md)). Any formulation that adds or subtracts an opening weight
*alongside* the sum counts it twice, and the sign error that hides in the subtracting variant
makes every print increase the balance.

Property-based, with generated movement sequences including mixed signs and a
reconciliation that raises the balance. This is the central rule of the system and deserves
more than example tests — it is the one assertion that, if it passes, means the product's
headline number is arithmetic rather than opinion.

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
| An RFID matching two spools mounts neither | Guessing sends every later print to the wrong ledger |
| A finished print with no per-tray figure deducts nothing and opens a review | Missing is not zero, and zero would be invisible |
| A finished print with an unmounted consuming slot opens a review for that slot | The consumption is real even when the attribution is not |
| Approving a review with an unresolved non-zero slot is rejected | The alternative is inventing a spool or dropping grams silently |
| A review's slot→spool resolution is frozen at open, not read at approval | A spool swapped in the meantime must not absorb the deduction |
| Registering without a core weight fails | A silent zero corrupts every later reconciliation |
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
def test_domain_entities_and_services_are_synchronous(): ...
```

`test_movement_repository_exposes_no_mutation` inspects the port interface and asserts no
method name suggests update or delete. It looks pedantic until the afternoon someone adds one
to fix a bug quickly.

`test_domain_entities_and_services_are_synchronous` asserts that nothing under `domain/model`,
`domain/value` or `domain/service` is a coroutine function. [ADR-0005](adr/0005-async-io-ports.md)
puts the async boundary at the I/O ports; the first `async def` that appears on an entity is
the first sign that I/O has leaked inward, and it will be added for a reason that seems good
at the time.

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

**Q1 — closed.** `ha-bambulab` already distinguishes the cases on the event bus, so no
procedure is needed. What remains is a *confirmation*, folded into Q4's runs: check that a
hand cancellation produces `event_print_canceled` and a genuine failure produces
`event_print_failed`, and keep the raw fields so a disagreement is recoverable.

**Q2 — purge accounting.** Two steps, cheapest first:

1. **No printer required.** Slice a two-colour plate. Compare the `used_g` values in the
   `.3mf`'s `slice_info` metadata against the filament and flush totals Bambu Studio reports
   for the same plate. That answers whether flush is inside `used_g`.
2. **Confirmation.** Weigh two spools, run the print, weigh again, and compare the real total
   against the integration's per-tray figures.

**Q3 — G-code retrieval.** Attempt retrieval for twenty consecutive jobs; record success rate
and latency. Now scoped to the per-layer curve only, since the per-tray totals arrive without
it.

**Q4 — population path. The one that matters.** Test with Bambu Cloud, then LAN with FTP model
data. For twenty consecutive completed prints, record how often `print_weight` and its
per-tray attributes are actually populated by the time the finish event fires. A rate below
100% is not a curiosity — it is the size of the hole that [UC-04](04-use-cases.md) step 2
exists to cover, and it decides whether the LAN-only setup can be recommended at all.

Results are written back into [01 §1.6](01-vision.md), and the provisional rules that depend on
them are then either confirmed or replaced. **No estimator accuracy figure is published until
Q2 and Q3 are answered with real measurements.**
