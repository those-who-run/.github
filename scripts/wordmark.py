"""Write the wordmark's outlines, ``assets/source/wordmark.svg``, which
``scripts/brand_assets.py`` recolours into every banner and wordmark, so nothing
that shows 走り屋 needs a CJK font. Carried over from v1's ``scripts/wordmark.py``
(those-who-run/those-who-run) with the same drawing; v1's app icons stay with v1's
web app.

The fonts, both SIL Open Font License 1.1 (``assets/OFL-1.1.txt``):

- 走り屋: Noto Sans CJK JP, at weight 700 (the variable ``NotoSansCJK-VF.ttc``;
  Fedora's google-noto-sans-cjk-vf-fonts, or https://github.com/notofonts/noto-cjk);
- "Those Who Run": Barlow Semi Condensed 700, the Latin woff2 of the
  ``@fontsource/barlow-semi-condensed`` package, which ``make wordmark`` downloads
  and checks against its hash.

fontTools comes from ``.github/requirements-brand.txt``, hash-checked:
``make wordmark NOTO=<.ttc>`` runs this in a virtual environment under ``build/``.

    python scripts/wordmark.py NOTO_TTC BARLOW_SC_700_WOFF2
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTCollection, TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import brand  # noqa: E402

OUT = ROOT / "assets" / "source" / "wordmark.svg"
RED, WHITE = (brand.hex_colour(c) for c in (brand.ADVAN_RED, brand.WHITE))


def licence(fonts: str) -> str:
    """The SVG's first line: whose outlines these are, and what wrote them."""
    return (
        f"<!-- Outlines of {fonts}, SIL Open Font License 1.1."
        " Written by scripts/wordmark.py (make wordmark): never edit. -->"
    )


def run(font: TTFont, text: str, scale: float, x0: float, baseline: float):
    """Path data for ``text`` set from ``x0`` on ``baseline`` (y down), its bounds
    and where the next glyph would start."""
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    pen, bounds = SVGPathPen(glyphs), BoundsPen(glyphs)
    x = x0
    for ch in text:
        glyph = glyphs[cmap[ord(ch)]]
        t = (scale, 0, 0, -scale, x, baseline)
        glyph.draw(TransformPen(pen, t))
        glyph.draw(TransformPen(bounds, t))
        x += glyph.width * scale
    return pen.getCommands(), bounds.bounds, x


def short(d: str) -> str:
    """Two decimals are plenty at these sizes."""
    return re.sub(r"(\d+\.\d{2})\d+", r"\1", d)


def wordmark(noto: TTFont, barlow: TTFont) -> str:
    """走り屋 in Advan red, then the name in white, on one line."""
    kanji, kb, end = run(noto, "走り屋", 64.0 / noto["head"].unitsPerEm, 0, 64)
    name, nb, _ = run(barlow, "Those Who Run", 40.0 / barlow["head"].unitsPerEm, end + 20, 58)
    x0, y0 = min(kb[0], nb[0]), min(kb[1], nb[1])
    x1, y1 = max(kb[2], nb[2]), max(kb[3], nb[3])
    pad = 4
    box = f"{x0 - pad:.0f} {y0 - pad:.0f} {x1 - x0 + 2 * pad:.0f} {y1 - y0 + 2 * pad:.0f}"
    return (
        f"{licence('Noto Sans CJK JP and Barlow Semi Condensed')}\n"
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{box}" role="img"'
        f' aria-label="走り屋 Those Who Run">\n'
        f'  <path fill="{RED}" d="{short(kanji)}"/>\n'
        f'  <path fill="{WHITE}" d="{short(name)}"/>\n</svg>\n'
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Write the wordmark's outlines.")
    p.add_argument("noto", type=Path, help="NotoSansCJK-VF.ttc (Noto Sans CJK JP is its first)")
    p.add_argument("barlow", type=Path, help="barlow-semi-condensed-latin-700-normal.woff2")
    args = p.parse_args(argv)
    noto = instantiateVariableFont(TTCollection(args.noto).fonts[0], {"wght": 700})
    barlow = TTFont(args.barlow)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(wordmark(noto, barlow), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
