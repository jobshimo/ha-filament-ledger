# ADR-0005 — I/O ports are async; the domain stays synchronous

**Status:** Accepted
**Date:** 2026-08-02

## Context

Home Assistant runs on asyncio. Integration setup, service handlers and WebSocket commands are
all coroutines, and [03 §3.7](../03-architecture.md) requires SQLite work to run in an executor
so that a slow query cannot stall the event loop for every other integration in the house.

So somewhere between *"the use case asks for a spool"* and *"SQLite reads from disk"* there is
a sync/async boundary. **That boundary has to be declared, because it determines the signature
of every port in [02 §2.7](../02-domain-model.md) and of every use case in
[04](../04-use-cases.md).**

The first draft of the domain model declared ports synchronously — `get(SpoolId) -> Spool?` —
while the architecture document required an executor. Both cannot be true, and the
contradiction is not the kind that surfaces gracefully. It surfaces after forty files exist.

## Decision

**Ports that perform I/O are `async`. Use cases are `async`. The domain is synchronous.**

```
async def get(self, spool_id: SpoolId) -> Spool | None: ...     # port
async def execute(self, command: ApproveReview) -> None: ...    # use case

def balance(self, movements: list[Movement]) -> Grams: ...      # domain service
def now(self) -> datetime: ...                                  # Clock port
```

`balance` takes movements and nothing else — the opening balance is one of them
([02 §2.1](../02-domain-model.md)). Written out here because a signature in an ADR gets copied.

`Clock` stays synchronous: reading a clock is not I/O, and making it a coroutine would force
every pure calculation that needs a timestamp to become one.

Enforced by an architecture test — `test_domain_entities_and_services_are_synchronous`
([09 §9.5](../09-testing-strategy.md)) — which fails if any coroutine appears under
`domain/model`, `domain/value` or `domain/service`.

## Rationale

**The alternative is worse where it counts.** The competing option is synchronous ports with
the whole use case dispatched to an executor: `hass.async_add_executor_job(use_case.execute, …)`.
It keeps the application layer free of any concurrency vocabulary, which is genuinely
attractive.

It falls apart at the edges, and the edges are where the interesting code lives:

- **Event emission.** Use cases raise domain events that infrastructure forwards to the HA bus
  ([02 §2.9](../02-domain-model.md)). From a worker thread, every one of those needs
  `hass.loop.call_soon_threadsafe`. The buffering-and-marshalling machinery to do that safely
  is larger than the `async` keywords it was meant to avoid.
- **Locking.** The per-spool serialisation in [03 §3.7](../03-architecture.md) becomes a
  `threading.Lock` held across a thread hop, while the code that *reads* balances is on the
  event loop. Two lock disciplines in one component, for one invariant.
- **The printer gateway is genuinely async.** It is fed by HA's own event bus. Wrapping an
  event-loop-native source so a synchronous caller can consume it means a queue and a thread
  hand-off — building a bridge back to where we started.

**Purity was never at stake.** The concern about `async` in the application layer is that a
concurrency model leaks into business logic. It does not, because the business logic is not
there — it is in the domain, and the domain stays synchronous and untouched. `Grams`,
`BalanceCalculator` and `ConfidenceEvaluator` have no I/O to await, so their tests still run in
milliseconds without an event loop, which is the actual payoff hexagonal architecture was
bought for.

**A port that does I/O is async in this host.** `async` on `SpoolRepository.get` is not
implementation detail leaking upward; it is the honest signature of an operation that touches
a disk. Hiding it behind a synchronous facade does not remove the wait — it removes the
*caller's ability to see it*.

**It is also the shape of every other HA integration**, which matters for a project intended
to be readable by Home Assistant contributors and eventually distributed through HACS.

## Consequences

**Accepted costs**

- Application tests need `pytest-asyncio`. Fakes become async classes — still twenty lines,
  still storing in a dict, now with `async def`.
- Every use case call site must be awaited. A forgotten `await` produces a coroutine that is
  never run, which is a real class of bug — caught by `mypy --strict` and by
  `RuntimeWarning: coroutine was never awaited` surfacing as a test failure
  ([11](../11-development.md)).
- Async code is harder to read than synchronous code. Confined to the layers that do I/O,
  which is where the difficulty actually belongs.

**Gained**

- One concurrency model for the whole integration, matching the host.
- The domain stays testable without an event loop, and provably so.
- Per-spool serialisation is an `asyncio.Lock` in the same execution context as everything
  that reads what it protects.
- The sync/async boundary is *at the port interface*, which is a line a reader can point at,
  rather than somewhere inside an adapter where it has to be excavated.

## Alternatives rejected

**Synchronous ports, executor-wrapped use cases.** Analysed above. The purity it buys is real
but small; the marshalling it costs is real and recurring.

**Synchronous ports calling SQLite directly on the event loop.** Blocks Home Assistant. Ruled
out by [01 §1.4](../01-vision.md) C2 and by being a bad neighbour, which is not a thing this
project is willing to be.

**A dual-signature port with both `get` and `async_get`.** Two ways to do everything, two
paths to test, and the invariant that they agree is unenforceable. Interface Segregation
argues against it and so does simple arithmetic.
