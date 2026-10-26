# The brand assets in assets/ (scripts/brand_assets.py, scripts/wordmark.py), carried
# over from v1, and the maintainer's own jobs with his gh login (the scripts in
# scripts/, below). The checks to run are in AGENTS.md, not here.
#
# fontTools and Brotli come from .github/requirements-brand.txt, hash-checked, in a
# virtual environment under build/. The fonts are the @fontsource packages v1's web
# app locked, downloaded from npm and checked against the sha512 in v1's
# web/package-lock.json; both are SIL OFL 1.1 (assets/OFL-1.1.txt). The social
# preview PNG needs ImageMagick (magick).

.PHONY: help brand-assets wordmark clean board-check board protect-check protect envs-check envs new-repo-check

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

# The version is in the path, so a bump downloads the new font instead of reusing
# the old one from an earlier build/.
BARLOW_500 := $(FONTS)/barlow-$(BARLOW_VERSION)/barlow-latin-500-normal.woff2
BARLOW_SC  := $(FONTS)/barlow-semi-condensed-$(BARLOW_SC_VERSION)/barlow-semi-condensed-latin-700-normal.woff2
SOCIAL_SVG := $(BUILD)/social-preview.svg
NOTO       ?=

help:
	@echo "brand-assets - write the banners, wordmarks and social preview into assets/ from scripts/brand.py"
	@echo "wordmark     - redraw the wordmark's outlines, assets/source/wordmark.svg: make wordmark NOTO=<NotoSansCJK-VF.ttc>"
	@echo "clean        - remove build/ (the virtual environment and the downloaded fonts)"
	@echo ""
	@echo "Maintainer only (your own gh login). Agents never run these:"
	@echo "board-check    - the moves make board would make on Race Control, changing nothing"
	@echo "board          - set each issue card's Status on Race Control from its issue"
	@echo "protect-check  - report where each repository in scripts/repos.json differs from it"
	@echo "protect        - check, apply scripts/repos.json to every repository, check again"
	@echo "envs-check     - report each releasing repository's release-please environment (names, never values)"
	@echo "envs           - set up the release-please environments: make envs CLIENT_ID=<Client ID> PEM=<key file>"
	@echo "new-repo-check - check a new repository has what every repository needs: make new-repo-check REPO=<name>"

$(BRAND_ENV): .github/requirements-brand.txt
	rm -rf $(BUILD)/brand-venv
	$(PYTHON) -m venv $(BUILD)/brand-venv
	$(BRAND_PY) -m pip install --quiet --disable-pip-version-check --require-hashes -r .github/requirements-brand.txt
	touch $@

# One woff2 from an npm tarball, after checking the tarball's sha512.
# $(1): package, $(2): version, $(3): sha512 (hex), $(4): the file in package/files/.
define font
	mkdir -p $(dir $@)
	curl -fsSL -o $(FONTS)/$(1)-$(2).tgz $(NPM)/$(1)/-/$(1)-$(2).tgz
	$(PYTHON) -c 'import hashlib, sys; d = hashlib.sha512(open(sys.argv[1], "rb").read()).hexdigest(); sys.exit(0 if d == sys.argv[2] else f"{sys.argv[1]}: sha512 {d}, expected {sys.argv[2]}")' $(FONTS)/$(1)-$(2).tgz $(3)
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
# ImageMagick builds, and by the sha256 of the SVG it came from, written last.
brand-assets: $(BRAND_ENV) $(BARLOW_500)
	@command -v magick >/dev/null 2>&1 || { echo "ImageMagick (magick) is missing: it renders the social preview" >&2; exit 1; }
	$(BRAND_PY) scripts/brand_assets.py --tagline-font $(BARLOW_500) --social $(SOCIAL_SVG)
	magick -background none $(SOCIAL_SVG) -strip -define png:exclude-chunks=date,time assets/social-preview.png
	$(PYTHON) -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' $(SOCIAL_SVG) > assets/source/social-preview.svg.sha256
	rm -f $(SOCIAL_SVG)

# The wordmark's outlines: 走り屋 from Noto Sans CJK JP, which is a system font here as
# in v1, and the name from Barlow Semi Condensed. Run make brand-assets after it.
wordmark: $(BRAND_ENV) $(BARLOW_SC)
	@test -n "$(NOTO)" && test -f "$(NOTO)" || { echo "NOTO must name Noto Sans CJK's NotoSansCJK-VF.ttc (from your system's Noto CJK fonts, or https://github.com/notofonts/noto-cjk), e.g. make wordmark NOTO=<path>/NotoSansCJK-VF.ttc" >&2; exit 1; }
	$(BRAND_PY) scripts/wordmark.py "$(NOTO)" $(BARLOW_SC)

clean:
	rm -rf $(BUILD)

# The maintainer's own jobs. Each runs a script in scripts/ with the gh login of
# whoever runs make, so only the maintainer runs them: they change repository settings
# and the organisation's project board, and envs stores the release App's private key.
# Agents never run them (AGENTS.md; the agent guard blocks them). The scripts' headers
# say what each does.

board-check:
	@bash scripts/board-sync.sh --dry-run

board:
	@bash scripts/board-sync.sh

protect-check:
	@bash scripts/protect-repo.sh --check --all

# The first check shows the drift the apply will fix, so its exit status is ignored;
# the second must be clean.
protect:
	@bash scripts/protect-repo.sh --check --all || echo "(the drift above is what the apply changes)"
	@bash scripts/protect-repo.sh --all
	@bash scripts/protect-repo.sh --check --all

envs-check:
	@bash scripts/release-envs.sh --check

# `make envs CLIENT_ID=... PEM=...` puts the Client ID and the key file's path on make's
# own command line (and in MAKEFLAGS), where ps and the shell's history see them;
# neither is a secret. make exports them to the script's environment, and the recipes
# don't echo them. Inside the script the values reach gh only on stdin, and no
# message names either value. The key file's contents are never on any command line.
envs:
	@test -n "$$CLIENT_ID" || { echo "make envs needs CLIENT_ID=<the release App's Client ID>" >&2; exit 2; }
	@test -n "$$PEM" || { echo "make envs needs PEM=<the release App's private key file>" >&2; exit 2; }
	@test -f "$$PEM" || { echo "make envs: PEM does not name a file" >&2; exit 2; }
	@bash scripts/release-envs.sh

new-repo-check:
	@test -n "$$REPO" || { echo "make new-repo-check needs REPO=<the repository's name>" >&2; exit 2; }
	@bash scripts/new-repo-check.sh "$$REPO"
