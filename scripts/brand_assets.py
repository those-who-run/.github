"""Write the brand assets in ``assets/`` from ``brand.py`` and the wordmark's outlines.
Carried over from v1's ``scripts/brand_assets.py`` (those-who-run/those-who-run) with
the same drawing and colours; only the paths changed.

- ``banner-dark.svg``, ``banner-light.svg``: the wordmark, a red rule and the tagline,
  for a README's hero (dark on deep black; light on a transparent background);
- ``wordmark-dark.svg``, ``wordmark-light.svg``: the wordmark alone, transparent, for
  dark and light pages;
- ``social-preview.png``: 1280x640, the banner laid out for a link card (rendered
  from an SVG by ``make brand-assets`` with ImageMagick; the SVG is not kept).

Every colour comes from ``brand.py``. The wordmark's shapes are the two paths of
``assets/source/wordmark.svg`` (``make wordmark``), recoloured, so no CJK font is
needed here. The tagline is outlined from Barlow by fontTools, from
``.github/requirements-brand.txt``: ``make brand-assets`` runs this in a virtual
environment under ``build/``. The outlines need no font on the viewer's side, which
matters on GitHub: an SVG in Markdown is shown as an image and loads no fonts.

``--check`` needs neither fontTools nor ImageMagick: it takes the tagline's outline
from the committed dark banner, builds every SVG again and fails when one differs
from the committed file (the palette, the wordmark or the tagline changed). The
outline can't show a changed tagline, so the tagline's path carries its text as
``data-tagline``, and ``--check`` fails when that is not ``brand.TAGLINE``. The PNG's
bytes are not checked, since rasterising differs between ImageMagick builds; the
sha256 of the SVG it was rendered from is (``SOCIAL_STAMP``).

    python scripts/brand_assets.py --tagline-font BARLOW_WOFF2 [--social SVG]
    python scripts/brand_assets.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import brand  # noqa: E402

WORDMARK = ROOT / "assets" / "source" / "wordmark.svg"
OUT = ROOT / "assets"
# The sha256 of the SVG the committed PNG was rendered from, written by
# `make brand-assets` after ImageMagick: the PNG's bytes can't be compared, but this
# can, so a palette change that regenerates only the SVGs still fails the tests.
SOCIAL_STAMP = OUT / "source" / "social-preview.svg.sha256"
RED, BLACK, GREY, WHITE = (
    brand.hex_colour(c) for c in (brand.ADVAN_RED, brand.DEEP_BLACK, brand.CARBON_GREY, brand.WHITE)
)
LABEL = f"{brand.KANJI} {brand.NAME}"
TAGLINE_SIZE = 36.0  # px, the tagline's em in every banner

# The wordmark: its viewBox (x, y, width, height), then the paths of 走り屋 and the name.
Parts = tuple[tuple[float, float, float, float], str, str]


def head(what: str) -> str:
    """The SVG's first line: what it is, what wrote it, and the fonts' licence."""
    return (
        f"<!-- {what}: outlines of Noto Sans CJK JP, Barlow Semi Condensed and Barlow,"
        " SIL Open Font License 1.1. Written by scripts/brand_assets.py"
        " (make brand-assets): never edit. -->"
    )


def num(x: float) -> str:
    """At most two decimals, no trailing zeros."""
    return f"{x:.2f}".rstrip("0").rstrip(".")


def wordmark_parts(svg: str) -> Parts:
    """The wordmark's viewBox and its two paths: 走り屋, then the Latin name."""
    box = re.search(r'viewBox="([^"]+)"', svg)
    paths = re.findall(r'<path fill="[^"]+" d="([^"]+)"/>', svg)
    if box is None or len(paths) != 2:
        raise SystemExit(f"{WORDMARK} is not the two-path wordmark scripts/wordmark.py writes")
    x, y, w, h = (float(v) for v in box.group(1).split())
    return (x, y, w, h), paths[0], paths[1]


def outline_tagline(font_file: Path) -> str:
    """The tagline as one path, ``TAGLINE_SIZE`` px, centred on x = 0, baseline y = 0."""
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont

    font = TTFont(font_file)
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
    scale = TAGLINE_SIZE / font["head"].unitsPerEm
    width = sum(glyphs[cmap[ord(c)]].width for c in brand.TAGLINE) * scale
    pen, bounds = SVGPathPen(glyphs), BoundsPen(glyphs)
    x = -width / 2
    for c in brand.TAGLINE:
        glyph = glyphs[cmap[ord(c)]]
        t = (scale, 0, 0, -scale, x, 0)
        glyph.draw(TransformPen(pen, t))
        glyph.draw(TransformPen(bounds, t))
        x += glyph.width * scale
    return re.sub(r"(\d+\.\d{2})\d+", r"\1", pen.getCommands())


def mark(parts: Parts, name_fill: str, cx: float, top: float, width: float) -> str:
    """The wordmark scaled to ``width``, centred on ``cx``, its top at ``top``."""
    (x, y, w, _), kanji, name = parts
    s = width / w
    return (
        f'  <g transform="translate({num(cx - width / 2 - x * s)} {num(top - y * s)})'
        f' scale({num(s)})">\n'
        f'    <path fill="{RED}" d="{kanji}"/>\n'
        f'    <path fill="{name_fill}" d="{name}"/>\n'
        "  </g>\n"
    )


def tagline(d: str, fill: str, cx: float, baseline: float, scale: float = 1.0) -> str:
    """The tagline centred on ``cx``, ``scale`` times ``TAGLINE_SIZE``."""
    size = f" scale({num(scale)})" if scale != 1.0 else ""
    return (
        f'  <path id="tagline" data-tagline="{html.escape(brand.TAGLINE)}" fill="{fill}"'
        f' transform="translate({num(cx)} {num(baseline)}){size}" d="{d}"/>\n'
    )


def rule(cx: float, y: float, width: float) -> str:
    return (
        f'  <rect x="{num(cx - width / 2)}" y="{num(y)}" width="{num(width)}"'
        f' height="4" fill="{RED}"/>\n'
    )


def svg(what: str, w: int, h: int, label: str, body: str) -> str:
    return (
        f"{head(what)}\n"
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}"'
        f' height="{h}" role="img" aria-label="{label}">\n{body}</svg>\n'
    )


def banner(parts: Parts, tag: str, dark: bool) -> str:
    """1280x320: the wordmark, a red rule and the tagline."""
    bg = f'  <rect width="1280" height="320" fill="{BLACK}"/>\n' if dark else ""
    body = (
        bg
        + mark(parts, WHITE if dark else BLACK, 640, 60, 840)
        + rule(640, 206, 160)
        + tagline(tag, WHITE if dark else GREY, 640, 264)
    )
    theme = "dark" if dark else "light"
    return svg(f"README banner, {theme}", 1280, 320, f"{LABEL}: {brand.TAGLINE}", body)


def wordmark(parts: Parts, dark: bool) -> str:
    """The wordmark alone at its own size, transparent: white name for dark pages."""
    (x, y, w, h), kanji, name = parts
    theme = "dark" if dark else "light"
    body = (
        f'  <path fill="{RED}" d="{kanji}"/>\n'
        f'  <path fill="{WHITE if dark else BLACK}" d="{name}"/>\n'
    )
    return (
        f"{head(f'Wordmark for {theme} pages')}\n"
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{num(x)} {num(y)} {num(w)} {num(h)}"'
        f' role="img" aria-label="{LABEL}">\n{body}</svg>\n'
    )


def social(parts: Parts, tag: str) -> str:
    """1280x640 for a link card: the banner's parts, larger, and a red stripe."""
    body = (
        f'  <rect width="1280" height="640" fill="{BLACK}"/>\n'
        + mark(parts, WHITE, 640, 190, 1000)
        + rule(640, 380, 200)
        + tagline(tag, WHITE, 640, 460, scale=1.3)
        + f'  <rect y="616" width="1280" height="24" fill="{RED}"/>\n'
    )
    return svg("Social preview", 1280, 640, f"{LABEL}: {brand.TAGLINE}", body)


def files(tag: str) -> dict[str, str]:
    parts = wordmark_parts(WORDMARK.read_text(encoding="utf-8"))
    return {
        "banner-dark.svg": banner(parts, tag, dark=True),
        "banner-light.svg": banner(parts, tag, dark=False),
        "wordmark-dark.svg": wordmark(parts, dark=True),
        "wordmark-light.svg": wordmark(parts, dark=False),
    }


def social_digest(tag: str) -> str:
    """The sha256 of the social preview's SVG for ``tag``, as ``SOCIAL_STAMP`` holds it."""
    parts = wordmark_parts(WORDMARK.read_text(encoding="utf-8"))
    return hashlib.sha256(social(parts, tag).encode("utf-8")).hexdigest()


def committed_tagline(out: Path) -> str | None:
    """The committed dark banner's tagline outline, or None when it was outlined
    from other text than ``brand.TAGLINE`` (its ``data-tagline``)."""
    path = out / "banner-dark.svg"
    committed = path.read_text(encoding="utf-8") if path.is_file() else ""
    found = re.search(r'<path id="tagline"[^>]* d="([^"]+)"/>', committed)
    if found is None:
        raise SystemExit(f"{path} has no tagline: run `make brand-assets`")
    text = re.search(r'data-tagline="([^"]*)"', found.group(0))
    if text is None or html.unescape(text.group(1)) != brand.TAGLINE:
        return None
    return found.group(1)


def main(argv: list[str] | None = None, out: Path = OUT) -> int:
    p = argparse.ArgumentParser(description="Write the brand assets from brand.py.")
    p.add_argument("--tagline-font", type=Path, help="Barlow's woff2, to outline the tagline")
    p.add_argument("--social", type=Path, help="also write the social preview's SVG here")
    p.add_argument("--check", action="store_true", help="fail if a committed SVG is stale")
    args = p.parse_args(argv)
    if args.check:
        tag = committed_tagline(out)
        if tag is None:
            print(
                f"the tagline in {out} is not brand.TAGLINE ({brand.TAGLINE!r}):"
                " run `make brand-assets` and commit them",
                file=sys.stderr,
            )
            return 1
        stale = [
            name
            for name, text in files(tag).items()
            if not (out / name).is_file() or (out / name).read_text(encoding="utf-8") != text
        ]
        if stale:
            print(
                f"{', '.join(stale)} in {out} stale against brand.py or the wordmark:"
                " run `make brand-assets` and commit them",
                file=sys.stderr,
            )
            return 1
        return 0
    if args.tagline_font is None:
        p.error("--tagline-font is needed to write the assets (or --check)")
    tag = outline_tagline(args.tagline_font)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files(tag).items():
        (out / name).write_text(text, encoding="utf-8")
        print(f"wrote {out / name}")
    if args.social:
        parts = wordmark_parts(WORDMARK.read_text(encoding="utf-8"))
        args.social.write_text(social(parts, tag), encoding="utf-8")
        print(f"wrote {args.social}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
