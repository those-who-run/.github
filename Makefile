# The brand assets in assets/ (scripts/brand_assets.py, scripts/wordmark.py), carried
# over from v1. Nothing else here needs make: AGENTS.md lists the checks to run.
#
# fontTools and Brotli come from .github/requirements-brand.txt, hash-checked, in a
# virtual environment under build/. The fonts are the @fontsource packages v1's web
# app locked, downloaded from npm and checked against the sha512 in v1's
# web/package-lock.json; both are SIL OFL 1.1 (assets/OFL-1.1.txt). The social
# preview PNG needs ImageMagick (magick).

.PHONY: help brand-assets wordmark clean

PYTHON    ?= python3
BUILD     := build
BRAND_PY  := $(BUILD)/brand-venv/bin/python
# The venv's python is a symlink to the system's, so its own timestamp can't say
# when the requirements were installed: this file does.
BRAND_ENV := $(BUILD)/brand-venv/installed
FONTS     := $(BUILD)/fonts
NPM       := https://registry.npmjs.org/@fontsource

BARLOW_VERSION    := 5.3.0
BARLOW_SHA512     := b46474cba40e6d1b4abfbd4c3ad878e36db6fa674d14cfd958558357554bccd6c6e282bd3cd465c89ce691c938bc7022f626c705782de12958e756a614fe7908
BARLOW_SC_VERSION := 5.3.0
BARLOW_SC_SHA512  := 11da50d4694b3287c6a83b10844c3e83a0e89cd2a6ceddb5a7ccbfa0d6d3978045cc492a6241721367e3db9606c666412ff2d96460317bbe0596f116720737d7

BARLOW_500 := $(FONTS)/barlow-latin-500-normal.woff2
BARLOW_SC  := $(FONTS)/barlow-semi-condensed-latin-700-normal.woff2
SOCIAL_SVG := $(BUILD)/social-preview.svg
NOTO       ?=

help:
	@echo "brand-assets - write the banners, wordmarks and social preview into assets/ from scripts/brand.py"
	@echo "wordmark     - redraw the wordmark's outlines, assets/source/wordmark.svg: make wordmark NOTO=<NotoSansCJK-VF.ttc>"
	@echo "clean        - remove build/ (the virtual environment and the downloaded fonts)"

$(BRAND_ENV): .github/requirements-brand.txt
	rm -rf $(BUILD)/brand-venv
	$(PYTHON) -m venv $(BUILD)/brand-venv
	$(BRAND_PY) -m pip install --quiet --disable-pip-version-check --require-hashes -r .github/requirements-brand.txt
	touch $@

# One woff2 from an npm tarball, after checking the tarball's sha512.
# $(1): package, $(2): version, $(3): sha512 (hex), $(4): the file in package/files/.
define font
	mkdir -p $(FONTS)
	curl -fsSL -o $(FONTS)/$(1)-$(2).tgz $(NPM)/$(1)/-/$(1)-$(2).tgz
	echo "$(3)  $(FONTS)/$(1)-$(2).tgz" | sha512sum --check --quiet
	tar -xzf $(FONTS)/$(1)-$(2).tgz -O package/files/$(4) > $@.tmp
	mv $@.tmp $@
endef

$(BARLOW_500):
	$(call font,barlow,$(BARLOW_VERSION),$(BARLOW_SHA512),barlow-latin-500-normal.woff2)

$(BARLOW_SC):
	$(call font,barlow-semi-condensed,$(BARLOW_SC_VERSION),$(BARLOW_SC_SHA512),barlow-semi-condensed-latin-700-normal.woff2)

# The SVGs from brand.py, the wordmark's outlines and Barlow for the tagline; then the
# social preview from an SVG that is not kept, without the PNG's date chunks, so a
# re-run writes the same bytes. tests/test_brand_assets.py checks the SVGs against
# the committed files; the PNG only by its size, as rasterising differs between
# ImageMagick builds.
brand-assets: $(BRAND_ENV) $(BARLOW_500)
	@command -v magick >/dev/null 2>&1 || { echo "ImageMagick (magick) is missing: it renders the social preview" >&2; exit 1; }
	$(BRAND_PY) scripts/brand_assets.py --tagline-font $(BARLOW_500) --social $(SOCIAL_SVG)
	magick -background none $(SOCIAL_SVG) -strip -define png:exclude-chunks=date,time assets/social-preview.png
	rm -f $(SOCIAL_SVG)

# The wordmark's outlines: 走り屋 from Noto Sans CJK JP, which is a system font here as
# in v1, and the name from Barlow Semi Condensed. Run make brand-assets after it.
wordmark: $(BRAND_ENV) $(BARLOW_SC)
	@test -n "$(NOTO)" && test -f "$(NOTO)" || { echo "NOTO must name Noto Sans CJK's NotoSansCJK-VF.ttc (Fedora: google-noto-sans-cjk-vf-fonts; or https://github.com/notofonts/noto-cjk), e.g. make wordmark NOTO=/usr/share/fonts/google-noto-sans-cjk-vf-fonts/NotoSansCJK-VF.ttc" >&2; exit 1; }
	$(BRAND_PY) scripts/wordmark.py "$(NOTO)" $(BARLOW_SC)

clean:
	rm -rf $(BUILD)
