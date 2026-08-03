# Screenshots

Taken from the owner's own instance, on real data, at 1280 CSS pixels.

| File | What it shows | Status |
| --- | --- | --- |
| `inventory.jpg` | The Inventory tab — the hero shot, used twice | ✅ |
| `history.jpg` | The History tab | ✅ |
| `stats.jpg` | The Stats tab over thirty days | ✅ |
| `ams.jpg` | The AMS tab | ✅ |
| `review.png` | One review card with a slot still unresolved | ⬜ needs a real open review |
| `printer.png` | The Printer tab mid-print, with progress and layers | ⬜ needs a print running |
| `trash.png` | The Trash, with one restorable row and one that is not | ⬜ |
| `settings.png` | The Settings tab as an admin sees it | ⬜ |

The four that are missing all need a state the ledger was not in when these were taken. Their
README references stay commented out, so nothing renders as a broken image until they land.

## How these were made

Not with a screen capture. The panel lives in a shadow root, so the shot is built from the
panel itself: clone `#root`, serialise it into an SVG `foreignObject`, draw that to a canvas.
Four things had to be true, and each was wrong once:

- **The typefaces travel inline.** An SVG rendered as an image loads no external resource, so
  the woff2 files are fetched and embedded as base64. Without this everything renders in the
  document default, which is a serif.
- **`#root` has to survive.** It is the element carrying `font-family`; serialising only its
  children loses the whole type stack.
- **Every `<svg>` needs an explicit `xmlns`.** The browser infers it in HTML and does not
  serialise it, so in the XML document the coils and the charts silently stop being SVG.
- **Animations are disabled for the frame.** A still renders a CSS animation at its *first*
  keyframe, and for the coil that is the arc fully hidden. The screenshot must show the
  settled state, which is the one a person actually sees.

The ambient layer, the card sweep and the header strand are hidden too: they are motion, and a
still frame of motion is just a smudge.

## What never reaches a file

**The account line is stripped before the frame is drawn** — it is the only place a person's
name appears in the panel.

Everything else in these images is real: real spools, real balances, real print times. If a
figure should not be public, change it in the ledger rather than editing the picture.

## How to take them

**Use real data.** The point of the hero shot is that this is a ledger somebody uses, and
invented spools read as invented. If a figure is private, change it in the ledger rather than
editing the picture.

**Take them from the panel, not the styleguide.** The styleguide is a catalogue with fixtures;
the README is showing the product.

**Desktop width, sidebar collapsed.** The panel is a container query away from a different
layout, and the wide one is what reads at README size. Collapsing the sidebar also keeps
somebody's house out of the frame.

**Dark, obviously** — there is no other option any more ([ADR-0008](../adr/0008-panel-visual-identity.md)).

**Crop to the panel.** No browser chrome, no Home Assistant sidebar, no clock.

**Two-times pixel density if the display offers it.** Text at README width is small, and the
typefaces are the point of half of this.

## Before committing one

- No spool names, job names or notes that say more than you want a stranger to know
- No account name in the header — the panel shows whoever is signed in
- No entity ids, no tokens, nothing from the browser's address bar

A screenshot is the one file in this repository that cannot be reviewed by reading a diff.
