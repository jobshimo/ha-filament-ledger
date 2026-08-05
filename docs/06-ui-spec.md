# 06 — UI Specification

The panel is a sidebar entry in Home Assistant, and it has a visual identity of its own — its
own palette, typefaces, spacing and motion, on a fixed dark surface that does not follow the
user's theme. [16 — The Visual System](16-visual-system.md) specifies it and
[ADR-0008](adr/0008-panel-visual-identity.md) records why the earlier theme-native position was
reversed. Everything below describes what the panel shows and why; the visual system describes
what it looks like.

Six views, and every one of them earns its tab. Every additional screen is a place the user
has to learn, so a new one arrives only when an existing view would have to lie to hold it.
Three further tabs — Printer, Trash and Settings — are specified in
[14 — Corrections and Trash](14-corrections-and-trash.md) §14.5, §14.4.4 and §14.6.4 rather
than here; they are surfaces onto facts the views below already own.

---

## 6.1 Navigation

```
┌──────────────────────────────────────────────────────────────────────┐
│  Filament Ledger                                                     │
│  ┌───────────┬──────────┬────────┬───────────┬─────┬─────────────┐   │
│  │ Inventory │ History  │ Stats  │ Review ⑵  │ AMS │ Printer   … │   │
│  └───────────┴──────────┴────────┴───────────┴─────┴─────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

**Review** carries a count badge and is the only tab that demands attention. When it is at
zero it stays visible but unhighlighted — a tab that appears and disappears is a tab the user
cannot build a habit around.

**Stats sits beside History** because the two answer one question at two zoom levels:
History is every entry, Stats is what those entries add up to.

### On a phone, where the strip does not fit

The panel repaints by replacing its markup wholesale ([ADR-0006](adr/0006-vanilla-panel.md)),
which builds a brand-new tab strip scrolled hard to the left on every navigation. Left alone
that is a real defect: tap a tab near the right-hand end and it highlights itself somewhere
off-screen, so the panel appears to have ignored the tap. Two rules fix it, and both apply
after *every* paint because the nodes are new every time:

- **The active tab is scrolled into view, centred, instantly.** Not smoothly — a scroll
  animation on every single navigation reads as jitter rather than as polish.
- **A fade shows at whichever end still has tabs beyond it**, and at neither end when the
  strip fits. The strip admits there is more to see rather than ending in a hard edge that
  looks like the end of the list.

Labels are never traded for icons. Density is worth less than knowing what a tab is before
tapping it; the padding tightens on narrow screens instead.

### The shell: what is fixed, and what scrolls

Every tab is built from one layout shell, and the shell is what decides that **the content
scrolls under the chrome rather than the whole panel scrolling as one document.** Four
regions, top to bottom:

```
┌──────────────────────────────────────────────────────────────────────┐
│  Filament Ledger                                        Martín  ADMIN│  1  fixed
│  │ Inventory │ History │ Stats │ Review ⑵ │ AMS │ Printer   …        │  2  fixed
├──────────────────────────────────────────────────────────────────────┤
│  [ + New spool ]  [ Sync with printer ]                              │  3  fixed
├──────────────────────────────────────────────────────────────────────┤
│  ┌────────────────┐  ┌────────────────┐                            ▲ │
│  │  PLA Basic     │  │  PLA Matte     │                            │ │  4  scrolls
│  └────────────────┘  └────────────────┘                            ▼ │
└──────────────────────────────────────────────────────────────────────┘
```

1. **The panel header** — the product and the account line.
2. **The tab strip**, which scrolls *horizontally* within itself on a narrow screen exactly
   as above, while staying vertically pinned.
3. **The view's actions** — the row of controls that acts on the tab as a whole. Inventory
   has *+ New spool* and *Sync with printer*; Stats has its period selector; Printer has
   *Refresh*; the spool detail has *Back*. Several tabs have none.
4. **The content** — the spool cards, the ledger rows, the review stack. **This is the only
   region in the panel that scrolls vertically.**

Three rules make it worth having.

**A tab with no actions renders no region 3 at all**, rather than an empty one. Fixed chrome
is paid for in the dimension a phone has least of, so a region nothing occupies must not cost
its margin on the tabs that leave it empty.

**Only whole-view controls are pinned.** A control that acts on one row belongs beside that
row and scrolls with it: a review card keeps its own Approve, a trash row keeps its own
Restore, and the spool detail keeps *Weigh* and *Adjust* under the spool they weigh and
adjust (§6.5) rather than hoisting them above it. Region 3 is for what acts on everything
below it, which is also the test for what belongs in it later.

**The content region keeps its scroll position across a repaint.** The panel repaints by
replacing its markup wholesale ([ADR-0006](adr/0006-vanilla-panel.md)), so the scroller is a
new element after every paint and starts at the top unless it is put back. A push from the
backend — a print finishing while somebody is reading row forty — must not throw them to the
top; a live panel that does that is worse than one that never updates. Arriving somewhere is
the exception and not an exception to the rule: a change of view opens at its top, which is
the same distinction that decides whether the entry animation runs (§6.8).

---

## 6.2 View 1 — Inventory

The default landing view. Answers "what do I have?" without a click.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Inventory                                    [+ New spool]          │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ Total stock  4 820 g  ·  9 spools  ·  2 need weighing          │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  [ All ▾ ]  [ Any material ▾ ]  [ Search…            ]  [ ⊞ | ☰ ]   │
│                                                                      │
│  ┌──────────────────────┐  ┌──────────────────────┐                 │
│  │ ███  PLA Basic       │  │ ███  PLA Matte       │                 │
│  │ ███  Black           │  │ ███  Ivory White     │                 │
│  │      Bambu Lab       │  │      Bambu Lab       │                 │
│  │                      │  │                      │                 │
│  │      612 g           │  │      184 g           │                 │
│  │ ▓▓▓▓▓▓▓▓▓▓▓░░░░ 61%  │  │ ▓▓▓░░░░░░░░░░░░ 18%  │                 │
│  │                      │  │                      │                 │
│  │ 🔴 AMS · Slot 1      │  │ 🔴 AMS · Slot 2      │                 │
│  │    Weigh this spool  │  │    Weigh this spool  │                 │
│  └──────────────────────┘  └──────────────────────┘                 │
│                                                                      │
│  ┌──────────────────────┐  ┌──────────────────────┐                 │
│  │ ███  PETG HF         │  │ ███  PLA Basic       │                 │
│  │ ███  Orange          │  │ ███  Black           │                 │
│  │      Bambu Lab       │  │      Bambu Lab       │                 │
│  │                      │  │                      │                 │
│  │      1 000 g         │  │      847 g           │                 │
│  │  ⬚ Sealed            │  │ ▓▓▓▓▓▓▓▓▓▓▓▓░░░ 85%  │                 │
│  │                      │  │                      │                 │
│  │ 🟢 Storage           │  │ 🟡 Storage           │                 │
│  └──────────────────────┘  └──────────────────────┘                 │
└──────────────────────────────────────────────────────────────────────┘
```

### Card anatomy

- **Colour block** — the actual filament colour, full-bleed on the left edge. This is the
  fastest possible identification; the user thinks in colours, not in labels.
- **Balance in grams**, largest element on the card. It is the number the view exists for.
- **Progress bar** tinted with the filament colour, with a neutral track.
- **Confidence dot** — 🟢 high, 🟡 medium, 🔴 low. When low, the card shows the call to
  action *"Weigh this spool"* directly. A warning with no adjacent remedy is just noise.
- **Location** — `AMS · Slot n`, `Storage`, or `External spool`.

### States

- **Sealed** — a "Sealed" chip, no progress bar. A spool never opened has nothing to show a
  bar for.
- **Anomaly** — amber left border and a ⚠ chip. Tapping goes straight to the explanation.
- **Depleted** — greyed, moved to the end of the list, kept visible. It is still a real object
  until discarded.
- **Discarded** — hidden unless the *All* filter includes it.

### Filters

Location (`All` / `In AMS` / `Storage` / `Discarded`), material, free-text over label, vendor
and colour name. Grid or list toggle; the list layout adds columns for last movement and
confidence.

### Sync with printer

A **⟳ Sync with printer** button sits beside *+ New spool*. The pair is this tab's fixed
action row (§6.1) and leads the view: the summary card below them is a figure to read, not a
control to reach, so it scrolls away with the spools while the two buttons do not.

It runs the same reconciliation pass startup runs — every tray the printer currently reports,
through the same detection rules — and renders a transient per-slot outcome strip: mounted
spools by name, unknown tags
with a **Register…** action that opens the new-spool form pre-filled from the tray's hints
(§6.4), ambiguous tags left for the user because the system does not pick, unreadable tags
named as such. With no printer connected the strip says so honestly — *"No printer connected —
nothing to sync"* — instead of spinning or inventing four empty slots. The strip is a report
of a moment: dismissing it, switching tabs, or syncing again replaces it.

### Empty state

No spools yet is the first thing a new user sees, so it must teach rather than apologise:

```
       No spools yet.

       Register the filament you own, and the ledger starts
       tracking every gram that leaves it.

       [ + Register your first spool ]
```

---

## 6.3 View 2 — Review queue

The most important view in the product. Everything the system is *unsure* about lands here,
and nothing leaves without a decision.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Review                                        2 pending             │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ ⚠  bracket_v3.gcode.3mf                    CANCELLED           │  │
│  │    Today, 14:02 · stopped at layer 71 of 209 (34%)              │  │
│  │                                                                │  │
│  │    Estimated from G-code · layer-accurate                      │  │
│  │                                                                │  │
│  │    ███ PLA Basic Black    Slot 1     [  28.4 ] g               │  │
│  │    ███ PLA Matte Ivory    Slot 2     [   6.1 ] g               │  │
│  │                                        ─────────               │  │
│  │                                total    34.5  g                │  │
│  │                                                                │  │
│  │    ⚖ I weighed the waste:  [        ] g   [ Distribute ]       │  │
│  │                                                                │  │
│  │    Note ────────────────────────────────────────────────────   │  │
│  │    [                                                        ]  │  │
│  │                                                                │  │
│  │                          [ Dismiss ]        [ ✓ Approve ]      │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ ⛔ calibration_cube.3mf                     FAILED              │  │
│  │    Yesterday, 22:41 · stopped at layer 4 of 60 (7%)             │  │
│  │    Printer error  HMS 0300-0100-0002-0001                      │  │
│  │                                                                │  │
│  │    Estimated from progress · approximate  ⓘ                    │  │
│  │                                                                │  │
│  │    ███ PLA Basic Black    Slot 1     [   1.9 ] g               │  │
│  │                                                                │  │
│  │    ⚖ I weighed the waste:  [        ] g                        │  │
│  │                                                                │  │
│  │                          [ Dismiss ]        [ ✓ Approve ]      │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

### Rules this view enforces

**Every gram field is editable.** The estimate is a starting value, never a fixed one. The
user's number always wins.

**The estimator is named on every card.** *"Estimated from G-code · layer-accurate"* versus
*"Estimated from progress · approximate"* tells the user how much to trust the figure before
deciding. Hiding provenance is how a guess gets mistaken for a measurement.

**Weighing is first-class.** The ⚖ field takes a single measured total. With one spool it
replaces the value outright. With several, **[ Distribute ]** splits the measured total across
spools *in the same proportion as the estimate* — a click, not arithmetic.

**Failures show the raw error code.** HMS codes are searchable. A user diagnosing a failure
needs the real string, not a friendly paraphrase of it.

**Approve is never the default focus.** No accidental Enter-key approval. The whole point of
the queue is deliberate confirmation; a reflex approval is worse than no queue at all.

**A slot with no spool is shown, not hidden.** Rows are keyed by *slot*, and a slot the
system could not attribute renders with a spool picker in place of the swatch:

```
│    ███ PLA Basic Black    Slot 1     [  28.4 ] g               │
│    ⚠  no spool recorded   Slot 3     [  12.1 ] g               │
│        which spool was in this slot?  [ Choose spool ▾ ]       │
│                                                                │
│                          [ Dismiss ]        [ ✓ Approve ]      │
│                            Approve is disabled until slot 3    │
│                            has a spool, or its amount is 0     │
```

This is the case [UC-04](04-use-cases.md) opens when filament was consumed from a slot the
inventory did not know was loaded: the amount is known, the spool is not. **Approve stays
disabled** while any non-zero row is unattributed — matching the domain rule in
[02 §2.3](02-domain-model.md), because a disabled button and a rejected service call should
never disagree about what is legal.

The alternative designs both lose information: hiding the row discards a real consumption, and
silently attributing it to whatever is in the slot *now* deducts from the wrong spool. The
user knows which spool it was. Ask them.

**The opposite case — nothing is known.** When a print reaches `FINISHED` but its per-tray
figure never arrived at all ([UC-04](04-use-cases.md) step 2), there is no amount to render:

```
│ ⚠  bracket_v4.3mf                          FINISHED            │
│    Today, 16:20 · completed                                    │
│                                                                │
│    ⛔ No consumption data — the printer never reported it       │
│       Nothing has been deducted for this print.                │
│                                                                │
│    ███ PLA Basic Black    Slot 1     [   0.0 ] g               │
│    ███ PLA Matte Ivory    Slot 2     [   0.0 ] g               │
│                                                                │
│    ⚖ I weighed the spools:  [        ] g   [ Distribute ]      │
```

Every field starts at zero and the banner says why, because a zero the user was told about is
a different object from a zero the system invented. The spools are known here — they were
mounted — so the rows are attributed and only the amounts are missing. Weighing, or dismissing
after a glance at the part, is one action either way.

Distinguishing these two shapes matters: one is *"how much came off which spool?"*, the other
is *"how much came off at all?"*. A single generic empty state would answer neither.

### Dismiss

Opens a small confirmation: *"Record no consumption for this print?"* with an optional reason.
Dismissal is a decision written to history, not a delete.

### Empty state

```
       ✓  Nothing to review.

       Cancelled and failed prints will appear here so you
       can confirm how much filament they used.
```

---

## 6.4 View 3 — AMS

A physical mirror of the machine. Answers "what is loaded right now?" at a glance.

```
┌──────────────────────────────────────────────────────────────────────┐
│  AMS Lite                                    ● Connected             │
│                                                                      │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐        │
│  │  SLOT 1    │ │  SLOT 2    │ │  SLOT 3    │ │  SLOT 4    │        │
│  │            │ │            │ │            │ │            │        │
│  │  ████████  │ │  ████████  │ │  ████████  │ │            │        │
│  │  ████████  │ │  ████████  │ │  ████████  │ │   empty    │        │
│  │            │ │            │ │            │ │            │        │
│  │ PLA Basic  │ │ PLA Matte  │ │ PETG HF    │ │            │        │
│  │ Black      │ │ Ivory      │ │ Orange     │ │            │        │
│  │            │ │            │ │            │ │            │        │
│  │   612 g    │ │   184 g    │ │   940 g    │ │            │        │
│  │ ▓▓▓▓▓░░ 61%│ │ ▓▓░░░░░ 18%│ │ ▓▓▓▓▓▓ 94% │ │  [ Mount ] │        │
│  │            │ │            │ │            │ │            │        │
│  │ 🔴 low     │ │ 🔴 low     │ │ 🟡 medium  │ │            │        │
│  │  printing  │ │  weigh it  │ │            │ │            │        │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘        │
│                                                                      │
│  Current print ─────────────────────────────────────────────────     │
│  bracket_v4.3mf · layer 88 of 209 · 42%                             │
│  ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░                                              │
│  Estimated use so far   Slot 1  38.2 g   ·   Slot 2  9.4 g          │
└──────────────────────────────────────────────────────────────────────┘
```

The live consumption row is **informational only**. It writes nothing. If the print completes,
the printer's measured figure is what gets recorded; if it is cancelled, the value seeds a
review. Showing a running number the user can see is what makes the eventual review figure
credible instead of surprising.

### Unknown spool

When an unregistered RFID appears — the case UC-02 refuses to auto-resolve:

```
┌────────────┐
│  SLOT 4    │
│            │
│  ████████  │
│    ？      │
│            │
│ Unknown    │
│ PLA · Blue │
│            │
│ Not in     │
│ inventory  │
│            │
│ [ Register]│
└────────────┘
```

Tapping **Register** opens the new-spool form pre-filled with everything the RFID provided —
material, colour, vendor, tag — leaving only the opening weight to confirm. The system asks
for the one number it cannot know, and fills in every number it can.

### Ambiguous tag

A Bambu tag identifies a batch, not a spool ([02 §2.3](02-domain-model.md)), so two registered
spools can legitimately answer to the same RFID. The slot stays unmounted and asks:

```
┌────────────┐
│  SLOT 2    │
│            │
│  ████████  │
│    ⚠       │
│            │
│ Two spools │
│ share this │
│ tag        │
│            │
│ ○ 612 g    │
│ ○ 1 000 g  │
│            │
│ [ Confirm ]│
└────────────┘
```

Candidates are distinguished by the only thing that actually differs — their balance, and when
each was last used. Picking wrong is not a cosmetic error: every subsequent print would drain
a spool sitting on a shelf while the one in the machine runs out with no warning. So the system
does not pick.

### Disconnected

The grid dims, a banner reads *"Printer unreachable — showing last known state from 14:02"*,
and every slot card is marked stale. Inventory operations remain fully available. Stale data
is labelled, never disguised as live.

---

## 6.5 View 4 — Spool detail & history

Reached by tapping any spool card. This view is what makes the ledger worth its immutability.

```
┌──────────────────────────────────────────────────────────────────────┐
│  ←  PLA Basic Black                                        [ ⋮ ]     │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  ████████                                                      │  │
│  │  ████████   612 g remaining of 1 000 g                         │  │
│  │  ████████   ▓▓▓▓▓▓▓▓▓▓▓░░░░░░  61%                             │  │
│  │                                                                │  │
│  │  Bambu Lab · PLA Basic · #000000                               │  │
│  │  AMS Slot 1 · active · tag A1B2C3D4                            │  │
│  │                                                                │  │
│  │  🔴 Low confidence · an estimate was approved today             │  │
│  │     Last weighed 3 days ago                                    │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  [ ⚖ Weigh ]  [ ✎ Adjust ]  [ 🗑 Discard ]  [ ✎ Edit details ]      │
│                                                                      │
│  History ───────────────────────────────────────────────────────     │
│                                                                      │
│   today 14:02   Estimated consumption      − 28.4 g     612 g       │
│                 bracket_v3 · cancelled · confirmed by you            │
│                                                                      │
│   today 09:15   Print                      − 84.1 g     640 g       │
│                 vase_final · automatic                               │
│                                                                      │
│   3 days ago    Reconciliation             +  6.2 g     724 g       │
│                 weighed 974.2 g including core · confirmed by you    │
│                                                                      │
│   4 days ago    Print                      −112.0 g     718 g       │
│                 enclosure_panel · automatic                          │
│                                                                      │
│   6 days ago    Discard                    −  8.0 g     830 g       │
│                 "tangled section" · confirmed by you                 │
│                                                                      │
│   7 days ago    Print                      −162.0 g     838 g       │
│                 lamp_shade · automatic                               │
│                                                                      │
│   8 days ago    Opening balance          + 1 000.0 g  1 000 g       │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### What the history must convey

Each row shows the **running balance after that entry**, so the arithmetic is visible rather
than asserted. Read bottom-up, this example is a closed sum:

```
1 000.0  − 162.0  −  8.0  − 112.0  +  6.2  −  84.1  −  28.4  =  611.7  →  612 g
```

That the example above adds up is not decoration. An earlier draft of this document had a
history whose rows did not reconcile — 1 000 g minus 8 g reaching 830 g — in the one view
whose entire purpose is to let a doubting user follow the balance to its origin. A worked
example that does not work teaches the reader that the arithmetic is approximate, which is the
opposite of what this product claims.

Every row states its **source** — *automatic* or *confirmed by you* — which is the difference
between a plan the printer carried out and a decision a person made.

**The header's confidence follows from the rows below it.** This spool was reconciled three
days ago and has since taken an approved `ESTIMATED_CONSUMPTION`, which
[02 §2.6](02-domain-model.md) defines as `LOW` — so the header reads `🔴 Low confidence` and
the same red dot appears on its inventory card and in the AMS view. It is not decorated as
`HIGH` because it was weighed recently; being weighed recently stopped being enough the moment
an estimate was applied on top.

Worth stating because an earlier draft of this document showed exactly that spool as `🟢 High`
while displaying the estimate that makes it `LOW`, three views apart. A specification whose
examples contradict its own rules teaches the rules are negotiable.

The list reads bottom-up as a derivation: opening balance, then every gram that left, arriving
at the number in the header. A user who doubts the balance can follow it to its origin. That
is the entire justification for an append-only ledger; without this view, immutability is cost
with no benefit.

### Actions

**⚖ Weigh** — the reconciliation dialog:

```
┌────────────────────────────────────────────┐
│  Weigh spool                               │
│                                            │
│  Put the whole spool on a kitchen scale.   │
│                                            │
│  Measured weight   [          ] g          │
│  ☑ Includes the spool core (250 g)         │
│                                            │
│  Ledger says      612 g                    │
│  Scale says       598 g                    │
│  Difference     −  14 g   (2.3%)           │
│                                            │
│  This will be recorded as a correction.    │
│  Nothing in your history changes.          │
│                                            │
│              [ Cancel ]  [ Record ]        │
└────────────────────────────────────────────┘
```

The difference is computed live as the user types, and the reassurance about history is
explicit — a user afraid of destroying data will avoid the feature that keeps the system
honest.

**🗑 Discard** — offers *whole spool* or *partial*, with a required reason.

**✎ Adjust** — signed amount, required reason. Deliberately the least prominent action, since
the specific operations should cover almost every case.

**✎ Edit details** — label, vendor, colour, material, core weight. **Never the balance.** The
form contains no balance field at all; that is not an omission but the point.

---

## 6.6 View 5 — History

The whole ledger at once, newest first. §6.5 answers *"where did this spool's balance come
from?"*; this view answers *"what has been happening?"* — the last print, the correction
made yesterday, the discard nobody remembers. One table, every spool together:

```
┌──────────────────────────────────────────────────────────────────────┐
│  All movements                                                       │
│                                                                      │
│   today 14:02   ███ PLA Basic Black                                  │
│                 Estimate (confirmed)        − 28.4 g    confirmed    │
│                 bracket_v3 · weighed the waste                       │
│                                                                      │
│   today 09:15   ███ PLA Basic Black                                  │
│                 Print                       − 84.1 g    auto         │
│                 vase_final                                           │
│                                                                      │
│   3 days ago    ███ PLA Matte Ivory                                  │
│                 Reconciliation              +  6.2 g    confirmed    │
│                                                                      │
│   8 days ago    ███ PETG HF Orange                                   │
│                 Opening balance          + 1 000.0 g    confirmed    │
└──────────────────────────────────────────────────────────────────────┘
```

Each row: **when** (relative, with the exact ISO timestamp a hover away), the **spool** —
swatch first, because the user thinks in colours — the **entry** as a human label (*Print*,
*Estimate (confirmed)*, *Adjustment*, *Reconciliation*, *Opening balance*, *Discard*,
*Purge*), the **signed amount** at one decimal with decreases in red, a **source badge**
(*auto* / *confirmed*), and the **job name** and **note** when the movement carries them.
The estimate label keeps its parenthetical on every row — an approved estimate must never
read like a measurement, even three views away from the review that approved it.

**No running balance column.** A balance only derives within one spool's history; a
cross-spool slice has no arithmetic to show, and a column of numbers that do not reconcile
against their neighbours would teach the reader that the arithmetic is approximate. The row
links to its spool, where §6.5 does the deriving.

The view serves the newest hundred entries. It is a window, not an export.

### Empty state

```
       No movements yet.

       Every gram that enters or leaves any spool lands here,
       newest first. Register a spool and its opening balance
       becomes the first row.
```

---

## 6.7 View 6 — Stats

What the ledger adds up to. §6.6 answers *"what has been happening?"* entry by entry; this
view answers *"where is my filament going?"* — which colour empties fastest, how much ends
up in the bin, how often a print gets as far as finishing.

It shipped ahead of its release ([15 §15.6](15-public-release.md)) because the data was
already there: every figure below is a sum over movements the ledger has been keeping since
day one, and none of it needed a new fact to be recorded.

```
┌──────────────────────────────────────────────────────────────────────┐
│  PERIOD   [ 30 days ]  [ 90 days ]  [ All time ]                     │
│                                                                      │
│  PRINTED      WASTED     PRINTS FINISHED    REVIEWS RESOLVED         │
│  1 284 g      96 g       11                 3                        │
│                                                                      │
│  PRINT TIME             AVERAGE PRINT                                │
│  38 h 12 min            3 h 28 min                                   │
│  Measured across 11 prints that recorded both a start and an end.    │
│                                                                      │
│  FILAMENT BY COLOUR                                                  │
│    ███ #8323FF                                            812 g      │
│    ████████████████████████████████████████                          │
│    ███ #FFFFF0                                            340 g      │
│    ████████████████                                                  │
│                                                                      │
│  HOW PRINTS ENDED                                                    │
│    ████████████████████████████████████▏███▏██                       │
│    ■ 11 finished   ■ 1 cancelled   ■ 1 failed                        │
│                                                                      │
│  BIGGEST PRINTS                                                      │
│    vase_final.gcode.3mf          3 days ago            84 g          │
│    bracket_v3.gcode.3mf          8 days ago            71 g          │
└──────────────────────────────────────────────────────────────────────┘
```

**Period: 30 days, 90 days, all time. Nothing finer.** No date picker, and no month-on-month
comparison. A household ledger is months old at best, and a custom range invites comparisons
between windows whose sample sizes make them meaningless. Thirty days is the default because
it is the window a person actually plans a filament order in.

**The period is applied server-side.** The panel sends the window and receives finished
figures; it never receives the ledger and filters it. Two reasons, both hard: the payload
would grow with the ledger, and the visibility rules below would end up re-implemented in
panel JavaScript, which is the one layer this project cannot test
([14 §14.8](14-corrections-and-trash.md)).

**Everything obeys [14 §14.4.5](14-corrections-and-trash.md), without exception.** A spool
retracted as *registered by mistake* contributes to nothing — not a total, not a bar, not a
row. An open void chapter drops out with its reversal, which is arithmetically neutral
because the two sum to zero. A discard is **waste**, never printing: filament that left the
spool without producing anything, and folding it into consumption would flatter every figure
on the page. A *discarded* spool's prints stay counted, because waste is history.

**Consumption is attributed to the entry as written.** A later reassignment moves a charge
between spools in the balances; the colour and material bars keep the attribution the
consumption entry carried. The totals are identical either way — a reassignment is a pair
that nets to zero — and the alternative draws a negative bar whenever the entry being
corrected fell outside the window.

**Print time is measured, not estimated.** `print_job` has carried `started_at` and
`ended_at` since the first migration ([08 §8.1](08-data-model.md)), so the duration is a
subtraction rather than an inference. It covers only jobs with a *positive* duration, which
excludes exactly one row: the one written when a restart swallowed a print's start and both
timestamps became the moment the ending arrived. That row's duration is zero, and zero is not
how long a print took. The card says how many prints the figures cover, and **when nothing in
the period can be measured the card is absent entirely** — a row of dashes teaches nothing.

**Charts are hand-rolled inline SVG.** [ADR-0006](adr/0006-vanilla-panel.md) stands: no
framework, no bundler, no chart library. Bars are drawn relative to the largest value rather
than to the total, because the question is *which colour goes fastest* and not *what share of
the whole*. The colour chart paints each bar in the filament's own stored hex — the one place
in the panel where a chart colour is data rather than theme — with an outline, so white
filament is visible on a white card. Everything else takes its colours from Home Assistant's
CSS custom properties, so light, dark and custom themes work without a line of per-theme code.

### Empty state

```
       Nothing to count yet.

       This page adds up what the ledger already holds: how much
       filament your prints used, how much was thrown away, which
       colours and materials go fastest, and how your prints ended.
       It fills itself in as you print — nothing here is typed in
       by hand.

       Try All time if you have printed before but not recently.
```

The last line matters. An empty statistics page is the one empty state a user is most likely
to read as breakage, so it names the thing to try next and says outright that an empty page
means an empty period rather than a figure that failed to load.

---

## 6.8 Cross-cutting rules

**Colour is the primary identifier.** Every reference to a spool anywhere shows its swatch.
Text labels are secondary because the user's mental model is visual.

**Grams everywhere.** No switching between grams, metres and percentages between views.
Percentage is always secondary to the absolute figure.

Precision follows what the number can actually support:

- **Balances — whole grams.** `612 g`, not `611.7 g`. The tenth is arithmetically real and
  physically meaningless; a kitchen scale reads to the gram, and displaying a decimal claims a
  precision the system cannot back up.
- **Movement amounts — one decimal.** `− 28.4 g`. A single movement *is* known to that
  precision, and hiding it would make the history stop reconciling on screen.
- **Percentages — whole numbers, rounded to nearest.** 4 of 60 layers is 7%, not 6%.
- **One exception: the reconciliation difference, one decimal.** `2.3%`, not `2%`. That figure
  is not a progress indicator — it is the system's error signal ([UC-08](04-use-cases.md)),
  the only honest measure of how wrong the estimates have been. Rounding away its tenths is
  rounding away the thing being measured.

Internally everything stays in integer milligrams regardless ([08 §8.1](08-data-model.md)).
Rounding is a display concern and it never touches stored values.

**The view is live, and the backend is what says so.** A figure on screen is a claim about the
ledger *now*. The panel opens **one subscription** — `filament_ledger/subscribe` — and the
integration pushes a payload whenever something changes. A print finishing, a review resolved,
a correction made in another browser: each reaches every open panel without a reload and
without anyone asking.

Two things can change what the panel shows, and the server knows both:

- **The ledger**, which changes only when this integration writes to it. Its own
  `filament_ledger_*` events ([05 §5.5](05-ha-integration.md)) are that moment, exactly.
- **The printer**, whose figures belong to the gateway's entities. Discovery already resolved
  which ones, so the subscription watches *those* and nothing else
  ([14 §14.5](14-corrections-and-trash.md)).

**The panel never polls, and never infers.** No interval, no comparing of `hass` objects
between assignments — Home Assistant hands one over whenever anything in the house changes, and
treating that as news about this integration is how a panel ends up polling while insisting it
does not. The client holds no list of event names either: it used to, and a copy is a thing
that drifts. The set lives once, in `event_bridge.LEDGER_EVENTS`, checked against that module's
own source by a test.

The push carries state rather than a nudge. Five read models used to be five round trips per
change per open panel; they are one payload now, computed once for whoever is listening.

Two rules bound it. **A burst costs one push** — one print finishing raises a movement, maybe a
depletion, maybe a confidence change. And **an update never interrupts the person at the
keyboard**: the panel repaints by replacing markup wholesale
([ADR-0006](adr/0006-vanilla-panel.md)), so one arriving while a dialog is open or a field has
focus is *held*, not dropped, and shown the moment the surface is idle. A stale number is a
smaller wrong than a number that ate what somebody was typing into it.

**Arriving at a view is animated; being updated in one is not.** The entry transition runs on a
change of view and nowhere else — replaying it on every update is what a live panel looks like
when it flickers. The same distinction governs the scroll position: arriving opens at the top,
being updated keeps the reader's place (§6.1).

**One shell, and one scroller.** Every tab is the same four regions, and only the last of them
moves (§6.1). A tab that wants a row of controls declares it and the shell pins it; a tab that
does not, does not pay for one. The rule exists so that the next surface needing fixed chrome —
the History filters this document still owes §6.6 — is a line in an existing shell rather than
a second mechanism beside it.

**Confidence is never hidden.** Any surface showing a balance shows its confidence alongside.
A number presented without its reliability invites false trust.

**Destructive actions confirm; corrective ones do not.** Discard confirms. Reconciliation does
not — it only ever adds a compensating entry, and nothing is lost.

**One appearance.** The panel renders its own fixed dark identity rather than the user's Home
Assistant theme ([ADR-0008](adr/0008-panel-visual-identity.md)). Light, dark and custom themes
all produce the same panel. Every value comes from a token declared once on `:host`
([16 §16.3](16-visual-system.md)); nothing downstream hard-codes a colour, a radius or a
duration.

**Responsive to the panel, not to the window.** Cards reflow to a single column when the panel
is narrow — which is not the same as the window being narrow, because Home Assistant's sidebar
takes its width from the same viewport. Every responsive rule is a container query against the
host ([16 §16.2](16-visual-system.md)), so pinning or collapsing the sidebar reflows the panel
without a reload. The review queue is routinely used on a phone, standing at the printer with
the failed part in hand — that is the layout to get right first.

**Accessible.** Confidence is never conveyed by colour alone; the dot always carries a text
label. All interactive elements are keyboard reachable, all inputs are labelled. Anything
tappable is at least 44 px tall — the panel is used one-handed, at a printer. Motion is
decoration: under `prefers-reduced-motion` the decorative animation stops and transitions
collapse to near zero. Because the panel no longer inherits a theme, a user cannot fix our
contrast from their side, which makes the palette's contrast our obligation rather than
theirs.
