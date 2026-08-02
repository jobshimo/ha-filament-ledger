# 11 — Development Environment

The toolchain, and why each piece is there. Nothing in this document is a preference; every
entry either enforces a rule stated elsewhere in these specs or removes a class of mistake.

---

## 11.1 Runtime

| | |
|---|---|
| Python | **3.14**, minimum 3.14.2 |
| Home Assistant | **2026.7.4** as the development target |
| Target install | HAOS, `generic-x86-64` |

The Python version is not a choice. A custom integration runs inside the Home Assistant
process, so it runs on whatever Python that release requires — `requires-python = ">=3.14.2"`
for 2026.7.4. Developing against anything else means testing a runtime the code will never
meet.

The reference *printer* is a Bambu Lab A1 with AMS Lite, per [01](01-vision.md). The reference
*host* being an x86-64 machine rather than a Raspberry Pi does not relax constraint C2 — the
Pi remains the performance floor the design targets, because other people's installations are
not this one.

## 11.2 Dependencies

Managed with **uv**. Lockfile committed.

```toml
[project]
requires-python = ">=3.14.2"

[dependency-groups]
dev = [
    "homeassistant==2026.7.4",
    "pytest",
    "pytest-asyncio",                        # ADR-0005: use cases are coroutines
    "pytest-homeassistant-custom-component", # integration tests only
    "hypothesis",                            # property tests for the ledger invariant
    "mypy",
    "ruff",
]
```

The integration itself declares **no runtime dependencies**. `manifest.json` carries an empty
`requirements` array, and that is a deliberate outcome rather than a coincidence: SQLite is in
the standard library, `ha-bambulab` is consumed through the Home Assistant bus rather than
imported ([05 §5.8](05-ha-integration.md)), and the domain depends on nothing at all
([03 §3.2](03-architecture.md)).

An integration with no dependencies cannot break because something upstream released a major
version on a Sunday.

## 11.3 Static analysis

**`mypy --strict`, and it is not optional.**

[02 §2.2](02-domain-model.md) claims that `Grams` cannot be added to a bare number. Python has
no compile step, so at runtime that claim is worth exactly one `TypeError` — caught only if
the line executes. `mypy --strict` is what makes it true *before* execution, which is the
entire reason the type exists.

The same argument applies to `SpoolId` versus `ReviewId` versus `PrintJobId`. They are all
strings underneath. Without strict typing, passing one where another belongs is a silent bug
that surfaces as a missing row.

```toml
[tool.mypy]
strict = true
warn_unreachable = true
disallow_any_explicit = true
```

**Ruff** for linting and formatting, with `ANN` (annotations), `ASYNC`, and `RUF` enabled.
`ASYNC` earns its place under [ADR-0005](adr/0005-async-io-ports.md): it catches blocking calls
inside coroutines, which is precisely the mistake that would stall the event loop.

## 11.4 Tests

```
uv run pytest tests/domain tests/application   # milliseconds, no event loop, no HA
uv run pytest                                   # everything
```

Structure and intent are specified in [09](09-testing-strategy.md). Two settings matter here:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
filterwarnings = ["error::RuntimeWarning"]
```

The second turns *"coroutine was never awaited"* into a test failure. Under
[ADR-0005](adr/0005-async-io-ports.md) a forgotten `await` produces a use case that silently
does nothing — the ledger's worst failure mode, since it leaves no entry and no error. This
setting is the cheapest possible guard against it.

## 11.5 Continuous integration

GitHub Actions, on every push and pull request:

| Job | Fails the build when |
|---|---|
| `ruff check` / `ruff format --check` | style or lint drift |
| `mypy --strict` | the type discipline is violated |
| `pytest` | any test fails |
| **architecture tests** | the domain imports a framework, or grows a coroutine |
| `hassfest` | `manifest.json` is invalid for Home Assistant |
| HACS validation | the repository would not install through HACS |

`hassfest` and HACS validation run from the first commit, not at Phase 5. Both check packaging
rules that are cheap to satisfy continuously and unpleasant to retrofit against a finished
repository.

The architecture tests are listed as their own job on purpose. They are the executable form of
[03 §3.2](03-architecture.md), and a rule that only lives in a document is a rule that will be
broken during the first difficult afternoon.

## 11.6 The panel

`ui/` is a separate TypeScript build — Lit and Vite — producing a single bundle registered as
a custom panel ([05 §5.7](05-ha-integration.md)).

**The built bundle is committed.** HACS installs a repository, not a build pipeline; a user
cannot run `npm install`. Source lives in `ui/src`, output in
`custom_components/filament_ledger/ui/`, and CI verifies that a fresh build of the committed
source reproduces the committed output — otherwise the bundle drifts from its source and
nobody notices until a bug cannot be reproduced.

Not needed until Phase 3. Written down now so Phase 3 does not start with an unplanned
toolchain decision.

## 11.7 Conventions

- **Conventional commits.** `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- **English** for all code, comments, identifiers, UI strings and documentation. Spanish is a
  Phase 5 translation, delivered through `translations/`, not by writing Spanish identifiers.
- **One use case per file**, named for the operation in the language of the problem
  ([03 §3.5](03-architecture.md)).
- **`ruff format` decides formatting.** It is not a topic.

## 11.8 Getting started

```bash
git clone git@github.com:jobshimo/ha-filament-ledger.git
cd ha-filament-ledger
uv sync
uv run pytest tests/domain          # should pass in under a second
```

If that last command takes longer than a second, something has been imported that should not
have been — and finding out on day one is the point.
