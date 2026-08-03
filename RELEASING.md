# Releasing

How a version of Filament Ledger gets cut. Short, manual, and deliberately so — there is one
version of record and one command that publishes it.

**The version of record is `custom_components/filament_ledger/manifest.json`.** No other file
carries a version that users see. The git tag must equal it. Two versions that can disagree
eventually will.

---

## The steps

### 1. Confirm the tree is green

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
```

All four, on the commit you are about to tag. CI runs the same set plus `hassfest`; check that
the run for that SHA is green before going further.

### 2. Bump the manifest version

Edit `custom_components/filament_ledger/manifest.json`:

```json
  "version": "1.0.0"
```

Semantic versioning. Breaking changes to the database schema, the WebSocket API or the service
surface are a major bump; new features are a minor; fixes are a patch. A schema migration on its
own is not breaking — migrations run forward automatically — but a migration users cannot roll
back from is worth calling out in the notes regardless.

Commit it alone:

```bash
git commit -am "chore: release v1.0.0"
```

### 3. Tag it

The tag is `v` plus the manifest version, exactly:

```bash
git tag v1.0.0
git push origin main --follow-tags
```

If the tag and the manifest disagree, HACS will install one version and report another. Check
them against each other before pushing; this is the one step with no safety net today (see
*Still manual*, below).

### 4. Publish the GitHub release

```bash
gh release create v1.0.0 --title "v1.0.0" --notes-file notes.md
```

Or the web UI — **Releases → Draft a new release → choose the tag**. What the notes must contain:

- **What changed**, in the user's language. "Reassign a charge to another spool" beats
  "`ReassignMovement` use case".
- **Any migration that runs on upgrade**, named, with what it does to existing data. Users
  should never be surprised by a schema change discovered from a log line.
- **Anything that needs action after upgrading** — a reload, a re-sync, a setting to check.
- **Breaking changes first**, if there are any, under their own heading.

Do not attach a zip. HACS installs from the repository contents (`zip_release` is not set in
`hacs.json`), so an attached archive would be a second artifact nobody consumes.

### 5. Users update

HACS custom-repository users see the new version in **HACS → Integrations → Filament Ledger →
Update**, then restart Home Assistant. Manual installers replace
`config/custom_components/filament_ledger/` with the new contents and restart.

Migrations run on the next start. There is nothing else to do.

---

## Still manual

Worth knowing before the first release, so nothing is assumed that is not true:

- **There is no release workflow.** `.github/workflows/ci.yml` runs the quality gates on pushes
  and pull requests; nothing runs on a tag. The tag-driven workflow that re-verifies the gates on
  the tagged SHA and **fails the release when the tag and `manifest.json` disagree** is specified
  in [docs/15 §15.4](docs/15-public-release.md) and not built. Until it exists, step 3's check is
  a human one.
- **Nothing bumps the version for you.** Do not let a pull request bump `manifest.json`; that is
  a release-time edit, and CONTRIBUTING.md says so.

---

## Getting into the HACS default store

Right now Filament Ledger installs as a **custom repository**, which is a perfectly normal way to
ship and needs nothing from anybody else. Inclusion in the HACS default store — where users find
it by searching, without pasting a URL — is future work, and it has a prerequisite with real lead
time.

**Brands first.** An integration in the default store must have its icon in
[`home-assistant/brands`](https://github.com/home-assistant/brands): a pull request adding
`custom_integrations/filament_ledger/icon.png` (256×256, with `icon@2x.png` at 512×512), and
optionally a logo. It is reviewed by humans and it is not instant, which is why
[docs/15 §15.4](docs/15-public-release.md) says it goes first.

**Then the default store.** With brands merged and at least one published release, submit the
repository to [`hacs/default`](https://github.com/hacs/default) following the
[HACS publishing documentation](https://hacs.xyz/docs/publish/include). The requirements HACS
checks — a `hacs.json` at the root, the integration under `custom_components/<domain>/`, a valid
`manifest.json` with `documentation` and `issue_tracker`, a description, topics on the repository
and a published release — are all satisfied by this repository already, except the release itself
and the brands entry.

Neither of these changes anything for existing custom-repository users. They keep updating
exactly as before.
