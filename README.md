# Filament Ledger

[![CI](https://github.com/jobshimo/ha-filament-ledger/actions/workflows/ci.yml/badge.svg)](https://github.com/jobshimo/ha-filament-ledger/actions/workflows/ci.yml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

<!-- Ready to activate. Uncomment the HACS badge once the repository is public and installable
     as a custom repository; uncomment the release badge after the first tag (see RELEASING.md).

[![HACS: custom](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Release](https://img.shields.io/github/v/release/jobshimo/ha-filament-ledger?sort=semver)](https://github.com/jobshimo/ha-filament-ledger/releases)
-->

**Filament Ledger is a Home Assistant custom integration that tracks how much filament you
actually have left.** It keeps a double-entry ledger per spool — every gram that leaves is an
immutable movement, and the balance is their sum — instead of trusting a percentage the printer
cannot measure. It deducts automatically from finished prints, sends everything it is unsure
about to a review queue, and gives you a sidebar panel to weigh, correct and audit the lot.

<!-- ![The inventory view](docs/img/inventory.png) -->

---

## The problem

Bambu Lab printers report a `remain` percentage per AMS tray. On the reference machine — an A1
with AMS Lite — **every tray reads `remain: 100`**, including visibly half-used ones, with
`remain_enabled: true`. The field exists, claims to be active, and is useless. That is not a
guess from documentation; it is a measurement, written down in
[docs/12 — Field Notes](docs/12-field-notes.md).

The RFID tag on a Bambu spool stores the spool's **identity** — material, colour, temperatures,
slicer profile. It does not store how much is left.

So there is no sensor to read. The remaining amount has to be *accounted for*.

## The approach

Every spool has an opening balance. Every gram that leaves it is recorded as an immutable
movement. The current balance is derived, never stored:

```
balance = Σ(movements)
```

The opening weight is the first movement, so there is no special case to keep in sync.

This is a ledger, not a counter. You can always answer *why* a spool holds 340 g, not just
*that* it does. Corrections are new entries, never edits to history.

Four principles fall out of that, and the code is built to them:

1. **The system never guesses silently.** A print that ran to completion is applied
   automatically. Anything interrupted, unattributable or missing needs your approval — and a
   missing number is never treated as zero.
2. **History is append-only.** Nothing is edited. Nothing is deleted.
3. **A number without its error margin is a lie with formatting.** Every balance carries a
   confidence level, and every surface that shows a balance shows it.
4. **The domain does not know Home Assistant exists.** The business rules are tested without
   booting HA at all.

---

## Requirements

Read this section before installing. Two of these will cost you an evening if you skip them.

| | |
|---|---|
| **Home Assistant** | **2026.7.4 or newer.** Older cores are not supported and not tested. |
| **[ha-bambulab](https://github.com/greghesp/ha-bambulab)** | Installed **and configured**, with your printer set up and its entities live. Filament Ledger never talks to the printer itself — it reads `ha-bambulab`'s entities and event bus. Tested against **v2.2.22**. |
| **Printer connection mode** | **Hybrid** (Bambu Cloud account + *"connect directly to the printer MQTT"* enabled) or plain local MQTT. See the warning below. |
| **Verified hardware** | Bambu Lab **A1 with AMS Lite**. That is the machine every fixture in this repository was captured from. |
| **Printers per instance** | **One.** Documented limitation, see below. |

> ### ⚠️ Do not enable LAN-only mode on the printer to "help" this integration
>
> On a Bambu printer, LAN-only mode is not a transport setting — it is a **cloud kill switch**.
> Turning it on breaks sending jobs from Bambu Handy and from Bambu Studio, which is how most
> people actually use the machine.
>
> **Hybrid mode is what you want**, and it is what this integration was developed against:
> authenticate with Bambu Cloud *and* tick *"connect directly to the printer MQTT"* in
> `ha-bambulab`. Job state and tray data arrive over your LAN, so a Bambu outage does not blind
> the ledger; the cloud session stays alive, so the print history that carries the per-tray
> weights remains available. Nothing on the printer has to change.
>
> This is written down because recommending LAN mode was a real mistake made during
> development — it weighed architectural purity above the owner's daily workflow.
> ([docs/12 — Field Notes](docs/12-field-notes.md))

**Other printer models.** Filament Ledger consumes `ha-bambulab`'s entities through the device
registry, not by entity id and not by model, so any printer that upstream exposes with the same
sensors should work. **None of them have been tested.** If you run a P1, X1 or A1 Mini, treat it
as unverified and check the History view after your first print before trusting it. Reports are
welcome — that is what the issue templates are for.

**One printer per instance — a multi-printer release is on the roadmap.** Today the config flow
allows a single entry, and the gateway takes the first printer it finds in the registry and logs
a warning naming the ones it ignored. **Support for more than one printer is planned for a
future release**, specified in [docs/15 §15.7](docs/15-public-release.md) and scheduled behind
its own design pass — it is the largest item on the list and has not been built yet. The
foundation is already right: spools belong to no printer by design, because the tag travels with
the reel, so what the change adds is a printer dimension to slots and print jobs. Until it
ships, a ledger is one inventory on one machine.

**No printer at all is a supported mode.** Without `ha-bambulab` the integration installs and
runs as a manual inventory: register spools, weigh them, discard them, read the history. You
lose automatic deduction and the AMS and Printer tabs, and nothing else.

---

## Installation

### Via HACS (recommended)

Filament Ledger is not in the HACS default store yet, so it installs as a **custom repository**:

1. In Home Assistant, open **HACS**. (On older HACS versions, go into the **Integrations**
   section first — newer ones have a single list.)
2. Open the **⋮** menu, top right, and choose **Custom repositories**.
3. Paste `https://github.com/jobshimo/ha-filament-ledger` into the repository field.
4. Choose type **Integration**, then **Add**.
5. Search for **Filament Ledger** in HACS and click **Download**.
6. **Restart Home Assistant.**

### Manually

1. Download the latest release, or clone this repository.
2. Copy `custom_components/filament_ledger/` into your Home Assistant `config/custom_components/`
   directory, so you end up with `config/custom_components/filament_ledger/manifest.json`.
3. **Restart Home Assistant.**

The sidebar panel — **Filament**, with a 3D-printer-nozzle icon — appears by itself once the
integration is configured. There is no Lovelace card to add and no resource to register. The
panel is deliberately **not** admin-only: weighing a spool is not an administrative act, and the
review queue only works if the person standing at the printer can reach it.

---

## Configuration

**Settings → Devices & Services → Add Integration → Filament Ledger.**

Four options. Every one of them is editable afterwards — from the integration's **Configure**
button, or from the panel's own **Settings** tab — because a setting you can only choose during
installation is a setting you will get wrong once and live with.

| Option | Default | What it means |
|---|---|---|
| **Default opening weight** | `1000` g | The starting weight offered when you register a new spool. A full Bambu reel is 1 kg of filament, so that is the default; it is only a pre-fill, and you can type any figure from 1 to 10000 g. |
| **Default core weight** | `250` g | What the empty plastic spool itself weighs. Used by the *Weigh* dialog: put the whole spool on the scale, tick *"includes the core"*, and the ledger subtracts this so it compares filament against filament. |
| **Anomaly threshold** | `15` % | How far a reconciliation may disagree with the ledger before the spool is flagged with an anomaly. Set it low and you will be told about every drift; set it high and only the surprises surface. |
| **Auto-mount on RFID** | on | When the printer reports a tag that matches exactly one registered spool, mount that spool into the slot automatically. Off means every mount is a decision you make in the panel. Ambiguous tags — two spools sharing one batch tag — are **never** auto-resolved either way. |

---

## First run

The golden path, in order. It takes about ten minutes and a kitchen scale.

1. **Open the panel.** Sidebar → **Filament**. You land on Inventory, and it is empty, and it
   says so.

2. **Press ⟳ Sync with printer.** The panel asks `ha-bambulab` what is in the AMS right now and
   reports a per-slot strip: spools it recognised, tags it does not know yet, tags it could not
   read. If there is no printer it tells you that instead of inventing four empty trays.

3. **Register each detected spool.** Every unknown tag in the strip carries a **Register…**
   action, and the form opens **pre-filled from the tray** — tag, colour, material, vendor. The
   only field left is the opening weight. The system asks for the one number it cannot know and
   fills in every number it can.

   Spools that live on a shelf get registered the same way with **+ New spool**.

4. **Weigh each spool and reconcile it.** This is the step people skip and then wonder why the
   figures drift. Open a spool, press **⚖ Weigh**, put the whole reel on a kitchen scale, type
   the figure and tick *includes the core*. The dialog shows you the difference live, and
   records it as a movement — so the drift stays visible instead of being quietly absorbed.

   Until you do this, a spool's balance is whatever you typed at registration. That is a
   placeholder, not a measurement, and the ledger knows the difference.

5. **Print something.** Anything. Let it finish.

6. **Watch the deduction land.** When the print ends, the printer's own per-tray figure is
   deducted from the mounted spools automatically, and the entry appears in **History** labelled
   *auto*, naming the job. Open the spool and the new balance is one subtraction below the old
   one, with the arithmetic on screen.

   If the print was cancelled or failed — or if it finished but the printer never reported a
   figure — nothing is deducted. It goes to **Review** instead, and waits for you.

---

## The panel, view by view

Eight tabs. Every one earns its place; a new one arrives only when an existing view would have
to lie to hold it.

### Inventory

<!-- ![Inventory](docs/img/inventory.png) -->

The landing view: what you have, without a click. A card per spool — colour block first, because
you think in colours — with the balance in grams as the largest thing on it, a progress bar, the
location (`AMS · Slot n`, `Storage`, `External spool`) and a **confidence dot**: green, amber,
red. A red dot says *Weigh this spool* right on the card, because a warning with no adjacent
remedy is just noise.

Filters by location, material and free text; grid or list. **+ New spool** registers by hand;
**⟳ Sync with printer** re-reads the AMS. Sealed spools show a chip instead of a bar (a reel you
have never opened has nothing to show a bar for), depleted ones grey out and move to the end,
discarded ones hide unless you ask for them.

Tap any card for the spool's own page: the full detail header, **⚖ Weigh**, **✎ Adjust**,
**🗑 Discard**, **✎ Edit details**, and its complete history with a running balance on every row.
The edit form has no balance field at all — that is not an omission, that is the point.

### History

<!-- ![History](docs/img/history.png) -->

The whole ledger, newest first, every spool together — the last print, the correction you made
yesterday, the discard nobody remembers. Each row: when, which spool, what kind of entry, the
signed amount, and an **auto** / **confirmed** badge that tells you whether a machine or a person
put it there. An approved estimate reads *Estimate (confirmed)* on every row, three views from
the review that approved it, because an estimate must never be mistaken for a measurement.

Two row actions live here, and they are the corrections that keep the ledger honest:

- **Reassign** moves a charge to the spool that actually fed the print. It writes two linked
  entries — grams back to one spool, grams off the other — and leaves the original untouched.
- **Delete (×)** returns the grams. Under the hood it *voids* the entry and writes the reversal;
  the entry does not vanish from the spool's own detail view, it just stops counting, struck
  through and labelled. Deleted entries wait in Trash and can come back.

There is no running-balance column here on purpose: a balance only derives within one spool, and
a column of numbers that do not reconcile against their neighbours would teach you that the
arithmetic is approximate.

### Stats

<!-- ![Stats](docs/img/stats.png) -->

What it all adds up to, over 30 days, 90 days or all time. Printed and wasted totals, prints
finished, reviews resolved, measured print time and average print length, filament by colour and
by material as bars, how prints ended, and the biggest prints of the period.

Everything is computed server-side over the ledger, so a voided entry drops out of its month, a
discard counts as **waste** and never as printing, and a spool you retracted as *registered by
mistake* contributes to nothing at all. No cost figures — no spool stores a price yet, and an
invented cost is worse than an absent one. Charts are hand-drawn SVG; there is no chart library
in this repository.

### Review

<!-- ![Review queue](docs/img/review.png) -->

**The most important view in the product.** Everything the system is unsure about lands here and
nothing leaves without a decision from you.

A card per interrupted print: the job name, how it ended (`CANCELLED`, `FAILED`), where it
stopped, the raw HMS error code when there is one — searchable, not paraphrased — and a per-slot
list of proposed grams. **Every gram field is editable**; the estimate is a starting value, never
a fixed one. The estimator names itself on every card so you know how much to trust it before
deciding.

Weighing is first-class: type the measured waste into the ⚖ field and, with several spools
involved, **Distribute** splits it in the same proportion as the estimate. Approve is never the
default focus — a reflex approval is worse than no queue at all — and Approve stays disabled
while any non-zero row has no spool attributed to it, because a disabled button and a rejected
command should never disagree about what is legal.

Two shapes get distinct treatment, because they are different questions. *"Which spool fed slot
3?"* renders a spool picker on that row. *"The print finished but the printer never reported a
figure"* renders every field at zero with a banner saying exactly that — a zero you were told
about is a different object from a zero the system invented.

**Dismiss** records no consumption, with an optional reason. It is a decision written to history,
not a delete.

### AMS

<!-- ![AMS](docs/img/ams.png) -->

A physical mirror of the machine: four slot cards with what is loaded, its balance, its
confidence, and the live progress of the current print with an estimated per-slot figure. That
running figure is **informational only** — it writes nothing. If the print completes, the
printer's measured number is what gets recorded.

An unregistered tag shows a **Register** button. A tag shared by two registered spools shows both
candidates and asks you to pick, because picking wrong would drain a spool sitting on a shelf
while the one in the machine runs out with no warning. When the printer is unreachable the grid
dims, says when the data is from, and marks every card stale. Stale data is labelled, never
disguised as live.

### Printer

<!-- ![Printer](docs/img/printer.png) -->

A read-only glance at the machine beside the inventory it feeds: state, job name, progress and
layer counts, the HMS error code if there is one, whether it is online, which MQTT mode it is in,
which tray is active, and the four trays as the printer reports them next to what the ledger
believes.

It is not a printer UI — `ha-bambulab` already has cards for that, and duplicating them adds risk
with no benefit. It never writes: looking at this tab cannot change your ledger. It refreshes
when you open it and when you press **Refresh**, and there is no timer. Every figure the printer
did not report renders as a dash, never as a zero.

### Trash

<!-- ![Trash](docs/img/trash.png) -->

Where deleted things wait. **Spools** you retracted as *registered by mistake* — with their
balance, their movement count and a **Restore** that brings the spool back *and its history with
it*. **Movements** you deleted — each with the grams that came back, and a **Restore** that asks
the symmetric question: *deduct this again?*

A handful of deletions return nothing (the spool they belonged to was already gone), and those
say so in place of the button rather than offering an action that cannot work.

> The trash is empty.
>
> Deleted spools and deleted history entries wait here, and everything can be restored. Nothing
> in the ledger is ever truly gone — a deletion is one more entry, not one less.

### Settings

<!-- ![Settings](docs/img/settings.png) -->

The four options from the config flow, editable where you actually use them instead of three
navigation levels away. Saving reloads the integration, which takes a second or two, and the tab
says so before you press it. **Admin only for writing** — these values change how every user's
ledger behaves, which *is* an administrative act. Non-admins see the values read-only with a line
explaining why, because a hidden tab invites "it's broken".

The tab also carries the **language override**: Auto, English or Español. It is stored per
device, because the language of this panel on this phone is a device preference and not ledger
state. Left on Auto, the panel follows your Home Assistant profile language.

---

## How the accounting works

Four rules. They are the whole design, and every feature above inherits them rather than
renegotiating them.

- **Double-entry, append-only.** A spool's opening weight is its first movement. Every gram that
  leaves is another. Nothing is ever updated in place — the database has triggers that refuse it.
- **The balance is derived.** `balance = Σ(movements)`, always, everywhere. There is no stored
  total that can disagree with its own history.
- **Corrections are more history.** Deleting an entry writes a reversal. Reassigning a charge
  writes two linked entries. Restoring writes a reinstatement. You can always read what happened
  *and* what you decided about what happened —
  [ADR-0007 — Corrections are more history](docs/adr/0007-corrections-are-more-history.md).
- **Confidence travels with the number.** A balance anchored by a fresh weighing is `HIGH`. One
  that has taken an approved estimate since is `LOW`, and says so on every surface that shows it.

Internally everything is integer milligrams. Rounding is a display concern and never touches a
stored value.

---

## FAQ and troubleshooting

**Why does my printer say every spool is 100% full?**
Because it does not know. `remain` is odometry, and on the reference A1 it reads `100` on every
tray whether the reel is new or nearly empty. That measurement is the reason this integration
exists — see [docs/12](docs/12-field-notes.md).

**A tray shows tag `0000000000000000`. Is that a bug?**
No — that is *no tag*. A third-party or refilled spool has nothing for the printer to read.
Sixteen zeros is not an identity, and the integration treats it as absent rather than as a value
to match on. Register the spool by hand; everything else works normally.

**My Home Assistant is not in English. Do I need to change anything?**
No. Filament Ledger resolves the printer's entities through the device registry, never by their
entity ids, so localised entity names are irrelevant to it. The panel itself speaks English and
Spanish and follows your profile language; anything else falls back to English, and the Settings
tab lets you override it per device.

**The Printer tab says "No printer connected".**
`ha-bambulab` is either not installed, not configured, or was not loaded when Filament Ledger
started. Install and configure it first, confirm its entities exist, then reload Filament Ledger
(**Settings → Devices & Services → Filament Ledger → ⋮ → Reload**). Everything that does not need
a printer keeps working in the meantime.

**A print finished but nothing was deducted.**
Three things must be true, and they fail in this order of likelihood:

1. **The integration must be running when the print *ends*.** The per-tray figures are per-job
   state that upstream fills while it is watching, and they are read at the moment the job
   finishes. Restart Home Assistant mid-print and that print's figures are gone.
2. **The spool must be registered and mounted** into the slot that fed the print. An
   unattributed slot does not silently pick a spool — it opens a review and asks you.
3. **The printer must have reported a figure at all.** If it did not, the print appears in
   **Review** with everything at zero and a banner saying the data never arrived. That is the
   designed behaviour: a missing figure is not a figure of zero.

Check **Review** before assuming anything is broken. Most "nothing was deducted" reports are a
card waiting for a decision.

**Where is my data?**
`filament_ledger.db` in your Home Assistant `config/` directory — plain SQLite, no server, no
cloud. It is included in Home Assistant's own backups. **Back it up**, and if you want to poke
around, any SQLite browser will open it read-only without disturbing the integration.

**Can I use it without a Bambu printer?**
Yes, as a manual inventory. You register, weigh, adjust and discard spools by hand and read the
full history. Automatic deduction and the AMS and Printer tabs are the parts that need a printer.

---

## Development

Python 3.14, [uv](https://docs.astral.sh/uv/), no runtime dependencies at all.

```bash
git clone https://github.com/jobshimo/ha-filament-ledger.git
cd ha-filament-ledger
uv sync
uv run pytest tests/domain    # should pass in under a second
uv run pytest -q              # the whole suite: 898 tests
```

The design is written down before it is built. [docs/](docs/) holds the contract — sixteen
numbered documents and eight ADRs — and it is the document, not the code, that is amended first
when something changes:

| Document | Contents |
|---|---|
| [01 — Vision & Scope](docs/01-vision.md) | Problem, goals, explicit non-goals |
| [02 — Domain Model](docs/02-domain-model.md) | Entities, value objects, invariants |
| [03 — Architecture](docs/03-architecture.md) | Hexagonal layers, ports, adapters |
| [04 — Use Cases](docs/04-use-cases.md) | Every operation, pre/post conditions |
| [05 — HA Integration](docs/05-ha-integration.md) | Entities, services, events, WebSocket API |
| [06 — UI Specification](docs/06-ui-spec.md) | Every view, wireframed and specified |
| [07 — Consumption Estimation](docs/07-consumption-estimation.md) | Estimator strategies and accuracy |
| [08 — Data Model](docs/08-data-model.md) | SQLite schema and migrations |
| [09 — Testing Strategy](docs/09-testing-strategy.md) | What is tested, and where |
| [10 — Roadmap](docs/10-roadmap.md) | Delivery phases |
| [11 — Development](docs/11-development.md) | Toolchain, CI, conventions |
| [12 — Field Notes](docs/12-field-notes.md) | What the real printer actually reports |
| [13 — Phase 2 Brief](docs/13-phase-2-brief.md) | The automatic-deduction phase, as briefed |
| [14 — Corrections & Trash](docs/14-corrections-and-trash.md) | The v1.0 correction surface |
| [15 — Public Release](docs/15-public-release.md) | v1.2: cost, alerts, packaging, multi-printer |
| [16 — The Visual System](docs/16-visual-system.md) | v1.1: the panel's own identity, tokens and styleguide |
| [ADRs](docs/adr/) | Architecture decision records |

Contributions are welcome — read [CONTRIBUTING.md](CONTRIBUTING.md) first; it is short, and it
explains the branch model (**open pull requests against `develop`**), the four gates, and the
one checklist that CI cannot run for you. Releases are cut from `main` per
[RELEASING.md](RELEASING.md).

## Licence

[MIT](LICENSE).
