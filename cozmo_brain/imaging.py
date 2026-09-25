"""Small shared image helpers used by both the engine (vision-in-the-loop)
and the tools that work with photos directly (who_is_this)."""

from __future__ import annotations

import base64


def encode_image_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")
