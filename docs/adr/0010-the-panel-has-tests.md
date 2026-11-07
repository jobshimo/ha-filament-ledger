# ADR-0010 — The panel has tests, and still no build

**Status:** Accepted
**Date:** 2026-09-29
**Amends:** [ADR-0006](0006-vanilla-panel.md) — "No Node in the repository, in CI, or in a
contributor's setup"

## Context

ADR-0006 chose a hand-written ES module over a built bundle, and accepted having no
JavaScript test harness. It was written for "a few hundred lines of rendering over seven
websocket commands", and it named its own trigger for revisiting: the panel growing past
what plain DOM handles comfortably.

It grew. `filament-ledger-panel.js` reached 5910 lines — one ~4500-line custom element and
~950 lines of CSS — and it is the most-changed file in the repository. Its only safety net is
a hand-verification checklist ([CONTRIBUTING](../../CONTRIBUTING.md)) and two Python tests that
read its source with regular expressions. Splitting a file that size into modules, which its
size now demands, cannot be done safely against a checklist.

## Decision

**The panel gets tests run by Node's built-in test runner, and nothing else changes.**

- `node --test` only. No `package.json`, no `node_modules`, no lockfile, no dependency of any
  kind — the runner, the assertions and the module loader all ship with Node.
- No DOM library. The panel renders by returning HTML strings from plain methods, so the
  harness (`tests/panel/harness.mjs`) defines only the three globals the module touches while
  it is being imported, and calls view methods on an instance built without its constructor.
- CI runs the suite in its own job. A contributor who does not touch `www/` needs no Node.
- **Everything else in ADR-0006 stands**: no framework, no bundler, no build step, no
  generated artefact. The browser still loads exactly the files a reader opens — now several
  plain ES modules under `www/` instead of one, imported relatively from the same
  versioned static root.

## Consequences

- Panel behaviour — what a view shows, what it escapes, which actions it offers — is
  asserted by machine, and a refactor of the panel has a green bar to hold.
- Node joins CI, and the setup of a contributor who changes the panel. That is the cost
  ADR-0006 wanted to avoid, paid in its smallest form.
- The hand-verification checklist stays: layout, phone width, dialogs and both languages on a
  real screen are still not something these tests see.
- Still no type checking on the panel. That remains the open cost ADR-0006 named.
