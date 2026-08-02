# 03 — Architecture

## 3.1 Shape

Hexagonal (ports and adapters). Three layers, dependencies pointing strictly inward.

```
┌───────────────────────────────────────────────────────────────┐
│                       INFRASTRUCTURE                          │
│                                                               │
│   ha/            entities · services · websocket · config     │
│   persistence/   SQLite repositories                          │
│   printer/       ha-bambulab gateway                          │
│   estimation/    G-code parser, estimator implementations     │
│   export/        Spoolman adapter (optional)                  │
│                                                               │
│   ┌───────────────────────────────────────────────────────┐   │
│   │                    APPLICATION                        │   │
│   │                                                       │   │
│   │   Use cases. Orchestrate the domain, transactional    │   │
│   │   boundary, emit events. No HA imports.               │   │
│   │                                                       │   │
│   │   ┌───────────────────────────────────────────────┐   │   │
│   │   │                   DOMAIN                      │   │   │
│   │   │                                               │   │   │
│   │   │   Entities · Value Objects · Domain Services  │   │   │
│   │   │   Ports (interfaces)                          │   │   │
│   │   │                                               │   │   │
│   │   │   Depends on NOTHING.                         │   │   │
│   │   └───────────────────────────────────────────────┘   │   │
│   └───────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘

              ui/   Lovelace panel (TypeScript/Lit)
                    talks to infrastructure over the WebSocket API
```

## 3.2 The rule that is enforced, not requested

**`domain/` and `application/` must not import `homeassistant`.**

This is verified by an automated test, not by discipline:

```python
def test_domain_has_no_framework_dependencies():
    for module in walk_modules("custom_components/filament_ledger/domain"):
        for imported in imports_of(module):
            assert not imported.startswith("homeassistant")
            assert not imported.startswith("sqlite3")
            assert not imported.startswith("aiohttp")
```

An architectural rule that lives only in a document is a rule that will be broken during the
first difficult afternoon. A rule with a failing test is a rule that holds.

The payoff is concrete: every business rule in [02 — Domain Model](02-domain-model.md) is
tested without a Home Assistant instance, in milliseconds, and stays passing when HA changes
an API.

## 3.3 Layer responsibilities

### Domain

Entities, value objects, domain services, and the port interfaces. Knows nothing about
storage, transport, or presentation.

**Contains:** the rule that a balance is derived; what makes a spool state transition legal;
how confidence is computed; what counts as an anomaly.

### Application

One class per use case. Each has a single public method. Each is a transaction boundary.

**Contains:** orchestration, repository coordination, event emission, authorisation of
sequence ("a review cannot be approved twice").

**Does not contain:** business rules. If a rule is being written here, it belongs in the
domain.

### Infrastructure

Implements every port. This is the only layer that knows Home Assistant, SQLite, MQTT, or
the file system exist.

**Contains:** HA entity classes, service registration, WebSocket command handlers, the config
flow, SQLite repositories, the `ha-bambulab` gateway, G-code parsing.

### UI

A Lovelace panel. Communicates only through the WebSocket API — never touches the database or
imports from the other layers. It could be replaced entirely without changing a line of
business logic.

## 3.4 SOLID, concretely

Not as a slogan. Where each principle is actually load-bearing in this design:

**Single Responsibility.** One use case per class. `ApproveReview` approves reviews. It does
not also estimate, notify, or persist printer state. When cancellation handling changes, one
file changes.

**Open/Closed.** `ConsumptionEstimator` is the clearest case. Adding a third estimation
strategy means adding a class and registering it — no existing file is edited. See
[07](07-consumption-estimation.md).

**Liskov Substitution.** Every `ConsumptionEstimator` returns per-slot grams or raises
`EstimationUnavailable`. No implementation returns `None` to mean failure, and none throws an
exception type the caller cannot anticipate. Substituting one for another cannot break a
caller.

**Interface Segregation.** `MovementRepository` exposes `append` and queries — no `update`, no
`delete`. The interface makes the immutability invariant unexpressible rather than merely
discouraged. Likewise, a read-only consumer of balances depends on a narrow query port, not
the full repository.

**Dependency Inversion.** The domain declares `PrinterGateway`; infrastructure implements it
against `ha-bambulab`. Swapping to direct MQTT, or supporting a Prusa, means one new adapter
and zero changes inward. This is also what keeps Spoolman a plug-in export rather than a
foundation — see [ADR-0002](adr/0002-reject-spoolman-as-foundation.md).

## 3.5 Directory layout

```
custom_components/filament_ledger/
├── __init__.py                    HA entry point, dependency wiring
├── manifest.json
├── config_flow.py
├── const.py
│
├── domain/
│   ├── model/
│   │   ├── spool.py
│   │   ├── movement.py
│   │   ├── print_job.py
│   │   └── pending_review.py
│   ├── value/
│   │   ├── grams.py
│   │   ├── material.py
│   │   ├── colour.py
│   │   ├── location.py
│   │   ├── spool_state.py
│   │   ├── percentage.py
│   │   └── confidence.py
│   ├── service/
│   │   ├── balance_calculator.py
│   │   ├── confidence_evaluator.py
│   │   └── anomaly_detector.py
│   ├── port/
│   │   ├── repositories.py
│   │   ├── printer_gateway.py
│   │   ├── consumption_estimator.py
│   │   └── clock.py
│   └── event.py
│
├── application/
│   ├── register_spool.py
│   ├── mount_spool.py
│   ├── unmount_spool.py
│   ├── record_print_consumption.py
│   ├── open_pending_review.py
│   ├── approve_review.py
│   ├── dismiss_review.py
│   ├── reconcile_spool.py
│   ├── discard_filament.py
│   ├── adjust_spool.py
│   └── query/
│       ├── spool_overview.py
│       └── movement_history.py
│
├── infrastructure/
│   ├── ha/
│   │   ├── entities/
│   │   │   ├── spool_sensor.py
│   │   │   ├── slot_sensor.py
│   │   │   ├── pending_reviews_sensor.py
│   │   │   └── stock_sensor.py
│   │   ├── services.py
│   │   ├── websocket_api.py
│   │   ├── event_bridge.py
│   │   └── coordinator.py
│   ├── persistence/
│   │   ├── database.py
│   │   ├── migrations/
│   │   ├── spool_repository.py
│   │   ├── movement_repository.py
│   │   ├── print_job_repository.py
│   │   └── review_repository.py
│   ├── printer/
│   │   ├── bambulab_gateway.py
│   │   └── tray_reading.py
│   ├── estimation/
│   │   ├── gcode_layer_estimator.py
│   │   ├── linear_progress_estimator.py
│   │   ├── composite_estimator.py
│   │   ├── gcode_source.py         infra-level FTP retrieval; not a domain port
│   │   └── gcode_parser.py
│   └── export/
│       └── spoolman_exporter.py
│
├── ui/                            built panel assets
└── translations/

tests/
├── domain/                        pure, fast, no HA
├── application/                   in-memory fakes
├── infrastructure/                adapters, real SQLite
└── integration/                   full HA test harness
```

The structure is intended to be readable as a description of the system. Someone opening
`application/` sees every operation the system performs, named in the language of the problem
— not `handlers/`, `managers/`, or `utils/`.

## 3.6 Composition root

Wiring happens in exactly one place: `__init__.py`, during `async_setup_entry`. Nothing else
constructs a dependency.

```python
async def async_setup_entry(hass, entry):
    database = await Database.open(hass.config.path("filament_ledger.db"))
    await database.migrate()

    spools    = SqliteSpoolRepository(database)
    movements = SqliteMovementRepository(database)
    jobs      = SqlitePrintJobRepository(database)
    reviews   = SqliteReviewRepository(database)
    clock     = SystemClock()
    events    = HomeAssistantEventBus(hass)

    gateway = BambuLabGateway(hass, entry.data[CONF_PRINTER_DEVICE_ID])

    estimator = CompositeEstimator([
        GcodeLayerEstimator(gateway),
        LinearProgressEstimator(),
    ])

    use_cases = UseCases(
        register_spool = RegisterSpool(spools, movements, clock, events),
        approve_review = ApproveReview(reviews, movements, spools, clock, events),
        ...
    )
```

Every dependency is injected. No use case constructs its own collaborators, which is precisely
what makes each one testable with in-memory fakes and no patching.

## 3.7 Concurrency and consistency

Home Assistant is asyncio. Three rules:

1. **SQLite access runs in an executor.** Blocking the event loop stalls the whole of Home
   Assistant — an unacceptable neighbour effect for an integration.
2. **Every port that performs I/O is `async`, and so is every use case.** The sync/async
   boundary sits at the port interface, not somewhere inside the adapters. Recorded as
   [ADR-0005](adr/0005-async-io-ports.md), because it determines the signature of every
   interface in [02 §2.7](02-domain-model.md) and is expensive to reverse.
3. **Writes to a single spool are serialised** by a per-spool `asyncio.Lock`. Two movements
   appended concurrently would each read a stale balance. The ledger tolerates this for reads,
   but anomaly detection and confidence evaluation must see a consistent sequence.

The domain itself stays synchronous. Entities, value objects, `BalanceCalculator`,
`ConfidenceEvaluator` and `AnomalyDetector` perform no I/O, so there is nothing for them to
await — and their tests need no event loop.

Balance is computed on demand and cached in memory, invalidated on append. With a
recomputation cost linear in movement count and a Raspberry Pi as the target floor, a spool
exceeding a few thousand movements would trigger snapshotting — noted as a future concern, not
built now. Building it before it is needed would be speculative complexity.

## 3.8 Failure posture

Constraint C4 requires that an unavailable printer never corrupts data.

| Failure | Behaviour |
|---|---|
| Printer offline | Inventory management fully functional. Slot state marked stale, not cleared. |
| `ha-bambulab` missing or broken | Integration loads; printer-dependent features disabled with a clear message. Manual operations unaffected. |
| G-code unavailable | Estimator falls back to linear. The review records which estimator ran, so the user knows the number's provenance. |
| **Job reaches `FINISHED` with no usable per-tray figure** | **A review is opened with a zero estimate and an explicit flag. Nothing is deducted, and nothing is assumed to be zero.** |
| Slot consumed filament with no spool mounted | A review is opened for that slot with the amount and no resolution, and the user assigns the spool. |
| RFID resolves to several spools | `AmbiguousTagDetected`; the slot stays unmounted until the user picks one. |
| Print ends while HA is down | Job reconstructed from printer state on reconnect. If consumption cannot be determined, a review is opened rather than a value invented. |
| Database corruption | Migrations are transactional. The ledger is append-only, so a partial write loses the last entry rather than the history. |

The fourth row is the one that earns this table. The per-tray figure only exists once the
sliced `.3mf` has been retrieved, and upstream reports that retrieval failing often enough to
matter in LAN mode ([Q4](01-vision.md)). A completed print that deducts nothing is invisible
and optimistic — the system would report filament that has already been extruded. Treating a
missing figure as zero is the one failure mode this design cannot tolerate, so it is written
into the failure posture rather than left to an implementer's judgement.

The consistent principle: **degrade to asking the user, never to inventing a number.**
