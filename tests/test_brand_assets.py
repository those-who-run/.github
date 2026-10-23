"""The brand assets in assets/ come from scripts/brand.py and the wordmark's outlines
(those-who-run/paddock#238), carried over from v1's tests/test_brand.py and
tests/test_brand_assets.py.

The SVGs are built again and compared byte for byte with the committed files, with
no fontTools: the tagline's outline is taken from the committed dark banner, as
`scripts/brand_assets.py --check` does. Standard library only; CI's `test` job runs
it:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SVGS = ("banner-dark.svg", "banner-light.svg", "wordmark-dark.svg", "wordmark-light.svg")
RAW = "https://raw.githubusercontent.com/those-who-run/.github/main/assets"

_spec = importlib.util.spec_from_file_location(
    "brand_assets", ROOT / "scripts" / "brand_assets.py"
)
assert _spec and _spec.loader
brand_assets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(brand_assets)
brand = sys.modules["brand"]  # imported by brand_assets from scripts/

RED, BLACK, GREY, WHITE = (
    brand.hex_colour(c) for c in (brand.ADVAN_RED, brand.DEEP_BLACK, brand.CARBON_GREY, brand.WHITE)
)


def fills(name: str) -> list[str]:
    return re.findall(r'fill="([^"]+)"', (ASSETS / name).read_text(encoding="utf-8"))


def check(out: Path) -> tuple[int, str]:
    """``brand_assets.py --check`` on ``out``: its exit code and what it printed."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = brand_assets.main(["--check"], out=out)
    return code, err.getvalue()


class Palette(unittest.TestCase):
    def test_the_palette_is_v1s(self):
        """The colours of v1's ADR 0004 and brand.py, unchanged."""
        self.assertEqual(brand.hex_colour(brand.ADVAN_RED), "#D21F1B")
        self.assertEqual(brand.hex_colour(brand.DEEP_BLACK), "#111111")
        self.assertEqual(brand.hex_colour(brand.CARBON_GREY), "#333333")
        self.assertEqual(brand.hex_colour(brand.WHITE), "#FFFFFF")
        self.assertEqual(brand.ACCENT, brand.ADVAN_RED)

    def test_name_and_kanji(self):
        self.assertEqual(brand.NAME, "Those Who Run")
        self.assertEqual(brand.KANJI, "走り屋")


class CommittedAssets(unittest.TestCase):
    def test_regenerating_the_svgs_gives_the_committed_bytes(self):
        """What `make brand-assets` writes is what is committed: a changed palette,
        wordmark or generator without a regeneration fails here."""
        tag = brand_assets.committed_tagline(ASSETS)
        self.assertIsNotNone(tag, "the banners' tagline is not brand.TAGLINE")
        for name, text in brand_assets.files(tag).items():
            with self.subTest(name=name):
                self.assertEqual(
                    (ASSETS / name).read_bytes(),
                    text.encode("utf-8"),
                    f"assets/{name} is stale: run `make brand-assets` and commit it",
                )
        self.assertEqual(sorted(brand_assets.files(tag)), sorted(SVGS))

    def test_the_check_passes_on_the_committed_files(self):
        self.assertEqual(check(ASSETS), (0, ""))

    def test_every_fill_is_a_brand_colour(self):
        """The SVGs' fills are brand.py's colours, in the order drawn; on light, the
        Latin name in deep black, 走り屋 in Advan red."""
        expected = {
            "banner-dark.svg": [BLACK, RED, WHITE, RED, WHITE],
            "banner-light.svg": [RED, BLACK, RED, GREY],
            "wordmark-dark.svg": [RED, WHITE],
            "wordmark-light.svg": [RED, BLACK],
        }
        for name, colours in expected.items():
            with self.subTest(name=name):
                self.assertEqual(fills(name), colours)

    def test_the_svgs_say_what_wrote_them_and_carry_no_text(self):
        """A first-line comment naming the script and the fonts' licence, and outlines
        only (no <text>, so GitHub, which loads no fonts for an SVG image, shows them
        as drawn)."""
        for name in SVGS:
            with self.subTest(name=name):
                text = (ASSETS / name).read_text(encoding="utf-8")
                first = text.splitlines()[0]
                self.assertTrue(first.startswith("<!--"))
                self.assertIn("scripts/brand_assets.py", first)
                self.assertIn("SIL Open Font License 1.1", first)
                self.assertNotIn("<text", text)
                self.assertNotIn("font-family", text)
                self.assertIn(f'aria-label="{brand.KANJI} {brand.NAME}', text)

    def test_the_wordmark_shapes_are_the_source_outlines(self):
        """The paths are assets/source/wordmark.svg's, recoloured."""
        _, kanji, name = brand_assets.wordmark_parts(
            brand_assets.WORDMARK.read_text(encoding="utf-8")
        )
        for asset in ("banner-dark.svg", "wordmark-light.svg"):
            with self.subTest(asset=asset):
                text = (ASSETS / asset).read_text(encoding="utf-8")
                self.assertIn(f'd="{kanji}"', text)
                self.assertIn(f'd="{name}"', text)

    def test_the_scripts_hold_no_colour_literal(self):
        """Colours come from brand.py, never a hex literal in a generator."""
        for script in ("brand_assets.py", "wordmark.py"):
            with self.subTest(script=script):
                source = (ROOT / "scripts" / script).read_text(encoding="utf-8")
                self.assertEqual(re.findall(r"#[0-9A-Fa-f]{6}", source), [])

    def test_the_banners_say_which_tagline_they_outline(self):
        for name in ("banner-dark.svg", "banner-light.svg"):
            with self.subTest(name=name):
                text = (ASSETS / name).read_text(encoding="utf-8")
                self.assertIn(f'<path id="tagline" data-tagline="{brand.TAGLINE}"', text)

    def test_the_social_preview_is_a_link_card(self):
        """1280x640, under 1 MB and with no timestamp chunk. Its bytes are not
        compared: rasterising differs between ImageMagick builds."""
        png = (ASSETS / "social-preview.png").read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", png[16:24]), (1280, 640))
        self.assertLess(len(png), 1_000_000)
        self.assertNotIn(b"tIME", png)

    def test_the_font_licence_is_kept_beside_the_outlines(self):
        text = (ASSETS / "OFL-1.1.txt").read_text(encoding="utf-8")
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", text)
        self.assertIn("Copyright 2017 The Barlow Project Authors", text)
        self.assertIn("Copyright 2014-2021 Adobe", text)


class StaleAssets(unittest.TestCase):
    """The check fails, and says to run `make brand-assets`, when the committed files
    no longer match their sources."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name)
        for svg in ASSETS.glob("*.svg"):
            shutil.copy(svg, self.out / svg.name)

    def test_a_palette_change_makes_the_check_fail(self):
        with mock.patch.object(brand_assets, "RED", brand.hex_colour(0xD21F1C)):
            code, err = check(self.out)
        self.assertEqual(code, 1)
        self.assertIn("make brand-assets", err)

    def test_a_tagline_change_makes_the_check_fail(self):
        """The outline can't show new text, so data-tagline is compared with
        brand.TAGLINE."""
        with mock.patch.object(brand, "TAGLINE", "a different tagline"):
            code, err = check(self.out)
        self.assertEqual(code, 1)
        self.assertIn("brand.TAGLINE", err)

    def test_a_missing_svg_makes_the_check_fail(self):
        (self.out / "wordmark-light.svg").unlink()
        self.assertEqual(check(self.out)[0], 1)

    def test_an_edited_svg_makes_the_check_fail(self):
        path = self.out / "wordmark-dark.svg"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertEqual(check(self.out)[0], 1)


class OrgProfile(unittest.TestCase):
    def test_the_profile_banner_follows_the_theme(self):
        """A <picture> at the top with the dark banner for a dark theme, the light one
        otherwise. The URLs are absolute: the org profile page doesn't resolve paths
        relative to this repository."""
        readme = (ROOT / "profile" / "README.md").read_text(encoding="utf-8")
        self.assertTrue(readme.startswith("<picture>"))
        hero = re.search(r"<picture>.*?</picture>", readme, re.S)
        assert hero is not None
        self.assertRegex(
            hero.group(0),
            r'<source media="\(prefers-color-scheme: dark\)" srcset="'
            + re.escape(f"{RAW}/banner-dark.svg")
            + '"',
        )
        self.assertIn(f'src="{RAW}/banner-light.svg"', hero.group(0))
        self.assertIn('alt="走り屋 Those Who Run"', hero.group(0))


if __name__ == "__main__":
    unittest.main()
