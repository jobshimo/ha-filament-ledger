# ADR-0003 — A custom integration, not an add-on

**Status:** Accepted
**Date:** 2026-08-02

## Context

Two ways to deliver this inside Home Assistant.

**Custom integration** — Python running inside the HA process. Creates entities, registers
services, emits events on the HA bus, ships a Lovelace panel. Installed through HACS.

**Add-on** — a separate Docker container with its own process, storage and web UI, talking to
HA over its REST or WebSocket API. Installed from the add-on store.

## Decision

A **custom integration**.

## Rationale

**Entities are the payoff, and only an integration creates them.** Entities are the currency
of Home Assistant. Once a spool balance is an entity, the user gets — with no code written by
this project:

- Automations triggering on any balance or state
- Mobile notifications with actionable buttons
- Long-term history and statistics graphs
- Voice assistant queries
- Dashboard cards, from any card the community has ever written

An add-on gets none of that for free. It would have to publish entities *back* into HA through
the API, reimplementing what an integration is handed.

**It is the constraint, restated.** Constraint C1 is a single surface — no additional
application. An add-on with its own web UI is precisely another application. Choosing it would
repeat the mistake [ADR-0002](0002-reject-spoolman-as-foundation.md) rejects, and in the same
sentence.

**The data it needs is already in-process.** The integration consumes `ha-bambulab` state.
Inside HA that is a state listener. From an add-on it is a WebSocket subscription with
authentication, reconnection, and a token to manage — accidental complexity that buys nothing.

**The isolation an add-on offers is not needed.** Add-ons win when a workload is heavy, needs
its own runtime, or would destabilise HA. This is a small SQLite ledger reacting to occasional
events — the load is negligible.

## Consequences

**Accepted costs**

- Runs in HA's process: a crash affects HA. Mitigated by the failure posture in
  [03 §3.8](../03-architecture.md) and by the domain layer having no I/O to fail in.
- Bound to HA's Python version and async model.
- Blocking work must go to an executor. Explicit in [03 §3.7](../03-architecture.md).
- The panel must be built as an HA custom panel rather than a free-standing web app.

**Gained**

- Entities, services, events, history and notifications, at no cost.
- HACS distribution — one-click install, automatic updates.
- Included in Home Assistant backups automatically ([08 §8.6](../08-data-model.md)).
- No authentication layer, no API client, no reconnection logic.

## Why the costs are affordable here

The architecture in [03](../03-architecture.md) confines HA to the infrastructure layer. Domain
and application know nothing about it, verified by test.

So the coupling this ADR accepts is real but shallow. If Home Assistant ever became the wrong
host, the business logic would move unchanged and the adapters would be rewritten — the exact
outcome hexagonal architecture is meant to buy, and the reason accepting this coupling is
prudent rather than careless.
