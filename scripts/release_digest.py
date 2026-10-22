"""What a release changes in the places that need a human look.

    python3 release_digest.py                  from the last release tag (vX.Y.Z,
                                               not a pre-release) to HEAD
    python3 release_digest.py v0.1.0..main     a range of your choosing
    ... --watch scripts/ --watch agent/        more paths to list, for this
                                               repository only

The release PR is where the maintainer signs off on everything merged since the
last release, so release-please.yml posts this digest on it. It lists, from git
history only and whatever the repository's language:

- dependencies: changed manifests and lockfiles (go.mod, package.json, uv.lock, ...);
- workflows and GitHub configuration: anything under .github/;
- agent policy: .claude/, AGENTS.md, CLAUDE.md;
- code owners: CODEOWNERS, wherever it is;
- build and release files: Makefile, Dockerfile, release-please's config, the
  commit hooks, the toolchain version files;
- weakened tests: test files deleted or renamed out of the test files, tests
  removed, skips and expected failures added;

each with the PRs that made it, from the commit subjects' `(#N)`. It runs nothing
from the history it reads; standard library only, Python 3.9 or later.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import PurePosixPath

# The digest's comment on the release PR starts with this, so the workflow finds its
# own comment and edits it rather than adding another.
MARKER = "<!-- release-digest -->"

# git's empty tree: the "since" of a repository with no release yet.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Dependency manifests and lockfiles, by file name, at any depth. A new transitive
# package shows only in a lockfile, so the locks are listed with the manifests.
DEPENDENCY_FILES = {
    "go.mod", "go.sum", "go.work", "go.work.sum",
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
    "pnpm-lock.yaml", "bun.lock", "bun.lockb", ".npmrc",
    "pyproject.toml", "uv.lock", "poetry.lock", "Pipfile", "Pipfile.lock",
    "setup.py", "setup.cfg",
    "Cargo.toml", "Cargo.lock", "Gemfile", "Gemfile.lock",
    "pom.xml", "build.gradle", "build.gradle.kts", "gradle.lockfile",
    "composer.json", "composer.lock", "mix.exs", "mix.lock",
}  # fmt: skip
REQUIREMENTS = re.compile(r"requirements([-_.][\w.-]*)?\.(txt|in)")

AGENT_FILES = {"AGENTS.md", "CLAUDE.md", ".mcp.json"}

BUILD_FILES = {
    "Makefile", "GNUmakefile", "Dockerfile", "Containerfile", ".dockerignore",
    "release-please-config.json", ".release-please-manifest.json",
    ".pre-commit-config.yaml", ".goreleaser.yml", ".goreleaser.yaml",
    ".go-version", ".python-version", ".node-version", ".nvmrc", ".tool-versions",
    "rust-toolchain", "rust-toolchain.toml",
}  # fmt: skip
BUILD_PATTERN = re.compile(r"(Dockerfile|Containerfile)\.[\w.-]+|[\w.-]+\.(Dockerfile|mk)")

# Test files by name, as the usual runners find them.
TEST_FILE = re.compile(
    r"""(
        [\w.-]+_test\.go
      | test_[\w.-]*\.py | [\w.-]+_test\.py | conftest\.py
      | [\w.-]+\.(test|spec)\.(js|jsx|ts|tsx|mjs|cjs|mts|cts)
      | [\w.-]+_spec\.rb
    )""",
    re.X,
)
# A test's name, where its definition starts a line.
TEST_NAME = (
    re.compile(r"^func\s+((?:Test|Fuzz|Benchmark|Example)\w*)\s*\("),  # Go
    re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\("),  # Python
    re.compile(r"""^\s*(?:it|test)\s*\(\s*(['"`])(.+?)\1"""),  # JavaScript and TypeScript
)
# A test skipped or expected to fail.
SKIP = re.compile(
    r"""(
        \b[tbf]\.Skip(f|Now)?\(                               # Go
      | @(unittest\.)?skip(If|Unless)?\b | @(unittest\.)?expectedFailure\b
      | @pytest\.mark\.(skip|skipif|xfail)\b | \bpytest\.(skip|xfail)\(
      | \.skipTest\(                                          # Python
      | \b(it|test|describe)\.(skip|todo)\( | \bx(it|test|describe)\(   # JavaScript
      | \#\[ignore\]                                          # Rust
    )""",
    re.X,
)

_PR = re.compile(r"\(#(\d+)\)\s*$")


def _git(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def code(text: str) -> str:
    """`text` as a Markdown code span it cannot close: a backtick becomes `'`. Inside
    the span, `@name` pings nobody and `[x](url)` is no link."""
    return "`" + text.replace("`", "'") + "`"


def category(path: str, watch: tuple[str, ...] = ()) -> str | None:
    """The digest section a changed path belongs in, or None if it needs no look."""
    p = PurePosixPath(path)
    name, parts = p.name, p.parts
    if name in DEPENDENCY_FILES or REQUIREMENTS.fullmatch(name):
        return "Dependencies"
    if name == "CODEOWNERS":
        return "Code owners"
    if parts[0] == ".github":
        return "Workflows and GitHub configuration"
    if ".claude" in parts[:-1] or name in AGENT_FILES:
        return "Agent policy"
    if name in BUILD_FILES or BUILD_PATTERN.fullmatch(name):
        return "Build and release files"
    if any(path.startswith(w) if w.endswith("/") else path == w for w in watch):
        return "Other watched paths"
    return None


SECTIONS = (
    "Dependencies",
    "Workflows and GitHub configuration",
    "Agent policy",
    "Code owners",
    "Build and release files",
    "Other watched paths",
    "Tests weakened",
)


def is_test_file(path: str) -> bool:
    return bool(TEST_FILE.fullmatch(PurePosixPath(path).name))


def test_name(line: str) -> str | None:
    for pattern in TEST_NAME:
        m = pattern.search(line)
        if m:
            return m.group(m.lastindex or 1)
    return None


@dataclass
class Digest:
    since: str
    until: str
    sections: dict[str, list[tuple[str, list[str]]]] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not any(self.sections.values())

    def add(self, section: str, what: str, prs: list[str]) -> None:
        self.sections.setdefault(section, []).append((what, prs))


def _prs(subjects: list[str]) -> list[str]:
    """`#N` for each subject that names its PR, once each, in order; the subject
    itself, short and as code, for one that doesn't (a direct push)."""
    out: list[str] = []
    for subject in subjects:
        m = _PR.search(subject)
        ref = f"#{m.group(1)}" if m else code(subject[:60])
        if ref not in out:
            out.append(ref)
    return out


def _subjects(since: str, until: str, path: str, cwd: str | None, *extra: str) -> list[str]:
    rev_range = until if since == EMPTY_TREE else f"{since}..{until}"
    log = _git("log", "--reverse", "--format=%s", *extra, rev_range, "--", path, cwd=cwd)
    return log.splitlines()


@dataclass
class _Lines:
    added: list[tuple[str, str]] = field(default_factory=list)  # (path, line)
    removed: list[tuple[str, str]] = field(default_factory=list)


def _changed_lines(since: str, until: str, cwd: str | None) -> _Lines:
    """Every added and removed line in the test files between the two commits."""
    out = _Lines()
    # No external diff or textconv drivers: they would run programs on the history.
    diff = _git(
        "diff", "--no-ext-diff", "--no-textconv", "--no-color", "-M", "-U0",
        since, until, cwd=cwd,
    )  # fmt: skip
    old = new = ""
    for line in diff.splitlines():
        if line.startswith("--- "):
            old = line[6:] if line.startswith("--- a/") else ""
        elif line.startswith("+++ "):
            new = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("diff --git "):
            old = new = ""
        elif line.startswith("+") and new and is_test_file(new):
            out.added.append((new, line[1:]))
        elif line.startswith("-") and old and is_test_file(old):
            out.removed.append((old, line[1:]))
    return out


def _weakened(d: Digest, deleted: set[str], cwd: str | None) -> None:
    lines = _changed_lines(d.since, d.until, cwd)
    # A test or skip that only moved (removed in one place, added in another) is no
    # change; so is a deleted file's tests, which the file's own line covers.
    added_names = {n for _, line in lines.added if (n := test_name(line))}
    removed_lines = {line.strip() for _, line in lines.removed}
    seen: set[tuple[str, str]] = set()
    for path, line in lines.removed:
        name = test_name(line)
        if name and name not in added_names and path not in deleted and (path, name) not in seen:
            seen.add((path, name))
            subjects = _subjects(d.since, d.until, path, cwd, "-S", name)
            d.add("Tests weakened", f"{code(path)}: test removed: {code(name)}", _prs(subjects))
    for path, line in lines.added:
        text = line.strip()
        if SKIP.search(text) and text not in removed_lines and (path, text) not in seen:
            seen.add((path, text))
            subjects = _subjects(d.since, d.until, path, cwd, "-S", text)
            what = f"{code(path)}: skip or expected failure added: {code(text[:80])}"
            d.add("Tests weakened", what, _prs(subjects))


def digest(since: str, until: str = "HEAD", cwd: str | None = None, watch: tuple[str, ...] = ()) -> Digest:
    d = Digest(since, until)
    deleted: set[str] = set()
    rows = _git("diff", "--no-ext-diff", "--name-status", "-M", since, until, cwd=cwd)
    for row in rows.splitlines():
        status, *paths = row.split("\t")
        path = paths[-1]
        subjects = _subjects(since, until, path, cwd)
        section = category(path, watch) or (category(paths[0], watch) if len(paths) > 1 else None)
        if section:
            shown = " -> ".join(code(p) for p in paths)
            d.add(section, f"{status[0]} {shown}", _prs(subjects))
        if status == "D" and is_test_file(path):
            deleted.add(path)
            pr = _prs(_subjects(since, until, path, cwd, "--diff-filter=D"))
            d.add("Tests weakened", f"{code(path)}: test file deleted", pr)
        elif status.startswith("R") and is_test_file(paths[0]) and not is_test_file(path):
            deleted.add(paths[0])
            d.add(
                "Tests weakened",
                f"{code(paths[0])} -> {code(path)}: renamed out of the test files",
                _prs(subjects),
            )
    _weakened(d, deleted, cwd)
    return d


def markdown(d: Digest) -> str:
    since = "the first commit" if d.since == EMPTY_TREE else code(d.since)
    lines = [
        MARKER,
        "### Before you merge: what needs a look",
        "",
        f"Changes from {since} to {code(d.until)} in the places a release should not "
        "ship unread. This comment is updated on every push to the default branch.",
        "",
    ]
    if d.empty:
        lines += [
            "Nothing in the watched places changed. Merging by hand is what releases it.",
            "",
        ]
        return "\n".join(lines)
    lines += ["**Read the list below before you merge.** Merging by hand is what releases it.", ""]
    for title in SECTIONS:
        items = d.sections.get(title)
        if not items:
            continue
        lines += [f"**{title}**", ""]
        lines += [f"- {what} ({', '.join(prs) or 'no PR found'})" for what, prs in items]
        lines += [""]
    return "\n".join(lines)


def last_release(cwd: str | None = None) -> str:
    """The last vX.Y.Z tag reachable from HEAD, or the empty tree if there is none. A
    pre-release (v1.0.0-rc.1) is not a release: the digest covers everything since the
    last real one."""
    try:
        return _git(
            "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "--exclude", "v*-*",
            "HEAD", cwd=cwd,
        ).strip()  # fmt: skip
    except subprocess.CalledProcessError:
        return EMPTY_TREE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("range", nargs="?", help="<from>..<to>; default: the last release tag..HEAD")
    parser.add_argument(
        "--watch",
        action="append",
        default=[],
        help="another path to list; a directory ends in /. Repeat for more.",
    )
    args = parser.parse_args(argv)
    if args.range:
        if ".." not in args.range or "..." in args.range:
            parser.error("the range is <from>..<to>, e.g. v0.1.0..main")
        since, until = args.range.split("..", 1)
        until = until or "HEAD"
    else:
        since, until = last_release(), "HEAD"
    watch = tuple(w for w in args.watch if w)
    print(markdown(digest(since, until, watch=watch)), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
