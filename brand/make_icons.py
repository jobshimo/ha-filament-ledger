"""The brand mark, generated rather than drawn by hand.

Run it and `icon.png` and `icon@2x.png` are rebuilt exactly as shipped:

    uv run --with pillow python brand/make_icons.py

**The mark is the panel's spool ring.** Not a new idea: `spoolRing` in
`custom_components/filament_ledger/www/filament-ledger-panel.js` already draws every spool
as a track circle with an accent arc over it, and the icon is that same figure at 72 %. The
palette is read off the panel's own custom properties, so a change there is a change here.

Three alternatives were drawn and dropped, recorded so nobody spends an afternoon
rediscovering them:

- **A side-on reel** — flanges with wound strands between them. At 32 px it reads as a
  radiator, not a spool.
- **A rule laid across the ring** (the double-entry line) — at 32 px it reads as a letter.
- **A strand leaving the ring tangentially** — reads as a **Q** at every size, and the
  strand degrades into a stray dash.

All three failed at the size that decides: 32 px, which is what Home Assistant's sidebar
renders. One silhouette survives scaling; a composition of two does not.

Sizes are what HACS and `home-assistant/brands` both ask for: 256 px, and 512 px for `@2x`.
Drawn at 4x and downsampled with LANCZOS, because an arc rasterised straight to 256 px has
visible stair-stepping and this mark is mostly arc.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent

# The panel's palette, verbatim (www/filament-ledger-panel.js, `:host` custom properties).
BG = (5, 7, 10, 255)  # --fl-bg
SURFACE = (14, 21, 29, 255)  # --fl-surface-raised
TRACK = (43, 57, 71, 255)  # --fl-line-strong
ACCENT = (0, 224, 198, 255)  # --fl-accent

SUPERSAMPLE = 4
SIZE = 256 * SUPERSAMPLE
REMAINING = 0.72


def _arc(draw: ImageDraw.ImageDraw, box: list[float], start: float, end: float, width: int) -> None:
    """An arc with round caps. Pillow draws butt caps only, so the caps are circles at the
    ends — the same thing `stroke-linecap: round` does in the panel's SVG."""
    draw.arc(box, start, end, fill=ACCENT, width=width)
    centre = (box[0] + box[2]) / 2
    radius = (box[2] - box[0]) / 2 - width / 2
    for angle in (start, end):
        radians = math.radians(angle)
        x = centre + radius * math.cos(radians)
        y = centre + radius * math.sin(radians)
        draw.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=ACCENT)


def build() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # A rounded-square plate rather than a bare glyph: the teal has to hold up on Home
    # Assistant's light theme too, and a dark plate guarantees that without asking the mark
    # to carry two colour schemes.
    draw.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=int(SIZE * 0.22), fill=BG)

    padding, stroke = SIZE * 0.26, int(SIZE * 0.105)
    box = [padding, padding, SIZE - padding, SIZE - padding]
    draw.arc(box, 0, 360, fill=TRACK, width=stroke)
    # Starts at twelve o'clock and runs clockwise, like the panel's `stroke-dashoffset` arc.
    _arc(draw, box, -90, -90 + 360 * REMAINING, stroke)

    # The hub. A spool has a core, and without it the mark reads as a plain progress donut.
    hub = SIZE * 0.40
    draw.ellipse([hub, hub, SIZE - hub, SIZE - hub], fill=SURFACE)
    return image


if __name__ == "__main__":
    master = build()
    for pixels, name in ((256, "icon.png"), (512, "icon@2x.png")):
        master.resize((pixels, pixels), Image.LANCZOS).save(OUT / name)
        print(f"{name}: {pixels}x{pixels}")
