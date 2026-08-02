# 06 — UI Specification

The panel is a sidebar entry in Home Assistant. It follows HA's own design language — same
cards, same typography, same theme variables — so it reads as part of Home Assistant rather
than a foreign application embedded in it.

Four views. No more. Every additional screen is a place the user has to learn.

---

## 6.1 Navigation

```
┌──────────────────────────────────────────────────────────────────────┐
│  Filament Ledger                                                     │
│  ┌────────────┬──────────────┬─────────────┬──────────────────────┐  │
│  │ Inventory  │ Review  ⑵    │ AMS         │ History              │  │
│  └────────────┴──────────────┴─────────────┴──────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

**Review** carries a count badge and is the only tab that demands attention. When it is at
zero it stays visible but unhighlighted — a tab that appears and disappears is a tab the user
cannot build a habit around.

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
│                 weighed 974 g including core · confirmed by you      │
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

## 6.6 Cross-cutting rules

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

**Confidence is never hidden.** Any surface showing a balance shows its confidence alongside.
A number presented without its reliability invites false trust.

**Destructive actions confirm; corrective ones do not.** Discard confirms. Reconciliation does
not — it only ever adds a compensating entry, and nothing is lost.

**Theme-native.** HA CSS custom properties throughout, so light, dark, and custom themes work
without any per-theme code.

**Responsive.** Cards reflow to a single column on narrow screens. The review queue is
routinely used on a phone, standing at the printer with the failed part in hand — that is the
layout to get right first.

**Accessible.** Confidence is never conveyed by colour alone; the dot always carries a text
label. All interactive elements are keyboard reachable, all inputs are labelled.
