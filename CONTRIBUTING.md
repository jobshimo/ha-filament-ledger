# Contributing

Thank you for looking. This is a small project with strong opinions, and most of them are
written down rather than enforced by taste. Read this page and you will know what CI will say
before you push.

## Setup

```bash
git clone https://github.com/jobshimo/ha-filament-ledger.git
cd ha-filament-ledger
uv sync
```

Python 3.14.2 or newer, [uv](https://docs.astral.sh/uv/) for everything. The integration itself
declares **no runtime dependencies** — SQLite is in the standard library, `ha-bambulab` is
consumed through the Home Assistant bus rather than imported, and the domain depends on nothing
at all. Please keep it that way.

## The branch model

Three branches, and pull requests flow one way:

```
develop  →  staging  →  main
```

- **`develop`** is where work lands. Open your pull request against this one unless you have a
  reason not to; it is the default branch for contributions.
- **`staging`** is what is about to be released — `develop` merges here when it is ready to be
  tried as a whole.
- **`main`** is what users install. A pull request into `main` is refused by CI unless it comes
  from `staging`, and releases are tagged from `main` only.

The rule exists because a released integration writes to a database that holds somebody's
inventory. Two gates between a good idea and a stranger's ledger is not bureaucracy; it is the
minimum a data-owning integration owes the people running it. Maintainers approve what merges;
the repository owner can bypass the rules when a release or a hotfix needs it.

## The four gates

CI runs these on every push and pull request. Run them locally first; they take under a minute.

```bash
uv run ruff check .            # lint
uv run ruff format --check .   # formatting — ruff decides, it is not a topic
uv run mypy                    # --strict, and it is not optional
uv run pytest -q               # 898 tests at the time of writing
```

`mypy --strict` earns its keep here specifically: the domain claims that `Grams` cannot be added
to a bare number, and Python has no compile step, so without strict typing that claim is worth
exactly one `TypeError` — caught only if the line happens to execute.

A fifth gate, `hassfest`, validates `manifest.json` against Home Assistant's packaging rules. It
runs in CI only. One trap it enforces that is easy to trip: **no literal braces in any string in
`translations/*.json`** — hassfest reads `{}` as placeholder syntax and fails the build. Write
prose, not JSON examples.

## The no-HA test subset

```bash
uv run pytest tests/domain tests/application   # milliseconds, no event loop, no Home Assistant
```

CI runs a dedicated job that installs everything *except* Home Assistant and then runs
`tests/domain` and `tests/architecture`. If the domain ever grows a Home Assistant import, that
job fails on `ImportError` before a single assertion runs, and it names the culprit.

If `uv run pytest tests/domain` starts taking longer than a second, something has been imported
that should not have been.

## Architecture tests are law

`tests/architecture/` walks the AST and fails when the domain imports a framework or grows a
coroutine. They are the executable form of [docs/03 §3.2](docs/03-architecture.md), and they
cannot be silenced by an ignore comment. A rule that lives only in a document is a rule that gets
broken during the first difficult afternoon — please do not send a PR that relaxes them.

The layering, in one line: `domain/` knows nothing, `application/` knows the domain,
`infrastructure/` is the only place allowed to know Home Assistant exists.

## docs/ is the contract

The design is written down **before** it is built. [docs/](docs/) — sixteen numbered documents
and eight ADRs — is where behaviour is decided; the code is the consequence.

- Changing behaviour means amending the document that specifies it, **in the same pull request**
  as the code. A change that contradicts its own spec is a defect even when the tests pass.
- New architectural decisions get an ADR. Small ones get a section in the relevant numbered doc.
- New user-facing strings land in `www/i18n.js` (**EN and ES**) and, when backend-facing, in both
  `translations/*.json` files, in the same change. A feature that ships English-only re-opens a
  defect that was already closed once.

## Panel changes need a hand-verification checklist

[ADR-0006](docs/adr/0006-vanilla-panel.md) chose a vanilla-JavaScript panel with no framework, no
bundler and no build step — which also means **no JS test harness**. That is an accepted cost,
and it comes with an obligation.

Panel logic that contains *rules* belongs server-side, where it is testable: visibility filtering
in the read models, voidability in the domain, amounts computed by use cases. What is left in the
panel is rendering and dispatch.

**Any PR touching `www/` must include a hand-verification checklist in its description**, with
each line initialled. [docs/14 §14.9](docs/14-corrections-and-trash.md) is the model; adapt it to
what you changed, and cover at minimum:

- every dialog you touched: Cancel closes, body-click does not, scrim-click does;
- every figure that can be absent renders as a dash, never as a zero;
- the surface in **both languages**, English and Spanish;
- a **phone-width pass** — the panel's primary venue is somebody standing at the printer with a
  failed part in their hand;
- every interpolation of user data goes through `esc()` (reasons, notes, labels, job names).

This list is the panel's test suite. Skipping it is skipping the tests.

## Commits and pull requests

- **Conventional commits**: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- **English** for all code, comments, identifiers and documentation. Spanish reaches users
  through `translations/` and `i18n.js`, never through Spanish identifiers.
- One logical change per pull request. Keep the docs amendment with the code that needs it.
- Do not bump `manifest.json`'s `version`. That happens at release time — see
  [RELEASING.md](RELEASING.md).

## Reporting rather than coding

Bug reports and feature requests are contributions, and the
[issue templates](.github/ISSUE_TEMPLATE/) ask for exactly what is needed to act on them. The
most valuable single thing you can include in a bug report is **what the History tab shows** —
this is a ledger, and almost every question about it is answered by the entries.

Reports from **printers other than the A1 with AMS Lite** are especially welcome. Every fixture
in this repository was captured from that one machine; the rest of the range is designed for and
untested.
