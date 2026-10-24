"""Brand constants for Those Who Run (走り屋), carried over from v1's
``src/those_who_run/brand.py`` (those-who-run/those-who-run) with the same values.

The brand assets in ``assets/`` take every colour and word from here
(``scripts/brand_assets.py``). The palette is a 90s Japanese track-day shop: Advan
red on black, carbon grey for secondary panels, white type (v1's ADR 0004).
"""

from __future__ import annotations

NAME = "Those Who Run"
KANJI = "走り屋"
TAGLINE = "a self-hosted voice race engineer for Gran Turismo 7"

# Colours as 24-bit ints, the form discord.Embed(colour=...) and most UI kits take.
ADVAN_RED = 0xD21F1B
DEEP_BLACK = 0x111111
CARBON_GREY = 0x333333
WHITE = 0xFFFFFF

ACCENT = ADVAN_RED
BACKGROUND = DEEP_BLACK
PANEL = CARBON_GREY
TEXT = WHITE


def hex_colour(value: int) -> str:
    """0xD21F1B -> '#D21F1B', for CSS and docs."""
    return f"#{value:06X}"
