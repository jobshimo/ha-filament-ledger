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

The version bump reaches `main` the way everything else does — `develop` → `staging` → `main`
(see [CONTRIBUTING.md](CONTRIBUTING.md#the-branch-model)); a maintainer without ruleset-bypass
cannot push to `main` directly, and should not want to. Once the bump is merged, tag that
commit. The tag is `v` plus the manifest version, exactly:

```bash
git checkout main && git pull
git tag v1.0.0
git push origin v1.0.0
```

If the tag and the manifest disagree, HACS will install one version and report another.
`.github/workflows/release.yml` is the safety net: it runs on the pushed tag, refuses to
publish when the tag and `manifest.json` disagree, and creates the GitHub release with
generated notes when they do agree — step 4 happens by itself.

### 4. The release publishes itself — then edit the notes

`release.yml` creates the release with generated notes the moment the tag lands. Generated
notes are a commit list, not a changelog — edit the release afterwards (**Releases → the new
release → Edit**) so the notes contain:

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

## What the automation does and does not do

Worth knowing before the first release, so nothing is assumed that is not true:

- **`.github/workflows/release.yml` publishes the release.** On every `vX.Y.Z` tag it refuses
  a tag that is not an ancestor of `main`, refuses a tag that disagrees with `manifest.json`,
  and then creates the release with generated notes — skipping cleanly if that release already
  exists, so re-running the job is safe. The quality gates run in `ci.yml` on every push and
  pull request; the ancestry check is what makes "a tag on `main` is a gated SHA" a fact rather
  than a hope.
- **It does not write your release notes.** Generated notes are a commit list. Edit the
  release afterwards — the checklist above says what the notes owe a user.
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
