"""Draws a simple battery icon for Cozmo's 128x32 face display.

Backend-agnostic (just builds a PIL image) — real.py sends it via
cli.display_image(), simulated.py just notes that it would have.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

_WIDTH = 128
_HEIGHT = 32


def render_battery_icon(charge_ratio: float, critical: bool) -> Image.Image:
    """`charge_ratio` 0.0-1.0 (already mapped from voltage by the caller).
    `critical` draws a filled exclamation mark instead of a fill level, and
    a thicker/doubled outline, to read as more urgent than "low" at this
    resolution."""
    charge_ratio = max(0.0, min(1.0, charge_ratio))
    img = Image.new("1", (_WIDTH, _HEIGHT), color=0)
    draw = ImageDraw.Draw(img)

    # Battery body, centered, with a small positive-terminal nub on the right.
    body = (24, 6, 96, 25)
    nub = (96, 12, 102, 19)
    draw.rectangle(body, outline=1, fill=0)
    draw.rectangle(nub, outline=1, fill=1)

    if critical:
        # Doubled outline for emphasis, plus a solid exclamation mark.
        draw.rectangle((body[0] - 2, body[1] - 2, body[2] + 2, body[3] + 2), outline=1)
        cx = (body[0] + body[2]) // 2
        draw.rectangle((cx - 2, body[1] + 3, cx + 2, body[3] - 8), fill=1)
        draw.rectangle((cx - 2, body[3] - 5, cx + 2, body[3] - 3), fill=1)
    else:
        inner = (body[0] + 3, body[1] + 3, body[2] - 3, body[3] - 3)
        fill_width = int((inner[2] - inner[0]) * charge_ratio)
        if fill_width > 0:
            draw.rectangle((inner[0], inner[1], inner[0] + fill_width, inner[3]), fill=1)

    return img
