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
- test, coverage and budget configuration: conftest.py, pytest.ini, tox.ini,
  .coveragerc, codecov.yml, the jest, vitest, playwright, karma and mocha configs,
  bundle-budget.json;
- build and release files: Makefile, Dockerfile, release-please's config, the
  commit hooks, the toolchain version files;
- coverage floors and budgets whose value changed, old and new: Go's
  `coverage-floor:` input in a workflow, `fail_under` and `--cov-fail-under`, jest
  and vitest coverage thresholds, the numbers in bundle-budget.json;
- weakened tests: test files deleted or renamed out of the test files, tests
  removed, skips and expected failures added;

each with the PRs that made it, from the commit subjects' `(#N)`. It runs nothing
from the history it reads; standard library only, Python 3.9 or later.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import PurePosixPath

# The digest's comment on the release PR starts with this, so the workflow finds its
# own comment and edits it rather than adding another.
MARKER = "<!-- release-digest -->"

# GitHub refuses an issue comment over 65,536 characters; the digest stays under it.
MAX_LENGTH = 65_000

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

# Where tests are collected, skipped or excluded from coverage, and the budgets:
# listed whenever they change, whatever the change.
BUDGET_FILE = "bundle-budget.json"
TEST_CONFIG_FILES = {
    "conftest.py", "pytest.ini", "tox.ini", ".coveragerc", "codecov.yml", ".codecov.yml",
    BUDGET_FILE,
}  # fmt: skip
TEST_CONFIG_PATTERN = re.compile(
    r"(jest|vitest|playwright)\.config\.[\w.]+|vitest\.workspace\.[\w.]+"
    r"|karma\.conf\.[\w.]+|\.mocharc(\.\w+)?"
)

BUILD_FILES = {
    "Makefile", "GNUmakefile", "Dockerfile", "Containerfile", ".dockerignore",
    "release-please-config.json", ".release-please-manifest.json",
    ".pre-commit-config.yaml", ".goreleaser.yml", ".goreleaser.yaml",
    ".go-version", ".python-version", ".node-version", ".nvmrc", ".tool-versions",
    "rust-toolchain", "rust-toolchain.toml",
}  # fmt: skip
BUILD_PATTERN = re.compile(r"(Dockerfile|Containerfile)\.[\w.-]+|[\w.-]+\.(Dockerfile|mk)")

# Test files by name, as the usual runners find them; Rust's integration tests by
# their place, any .rs file under a tests/ directory.
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
    re.compile(r"""^\s*(?:it|specify)\s+(['"])(.+?)\1"""),  # Ruby (RSpec)
)
# A test skipped or expected to fail.
SKIP = re.compile(
    r"""(
        \b[tbf]\.Skip(f|Now)?\(                               # Go
      | @(unittest\.)?skip(If|Unless)?\b | @(unittest\.)?expectedFailure\b
      | @pytest\.mark\.(skip|skipif|xfail)\b | \bpytest\.(skip|xfail)\(
      | \.skipTest\(                                          # Python
      | \b(it|test|describe)\.(skip|fixme|todo)\( | \bx(it|test|describe)\(   # JavaScript
      | \#\[ignore\b                                          # Rust
      | \bx(it|specify|describe|context)\s+['"]               # Ruby (RSpec)
    )""",
    re.X,
)
# Playwright's conditional skip, indented inside a test or describe body: a first
# argument that is not a string (a title) and a string reason after it, as in
# `test.skip(testInfo.project.name !== "phone", "touch: the phone project")`. It
# skips by design (one project of several), so it is not listed, unless the
# condition is always true. v1's rule (those-who-run/those-who-run#373).
CONDITIONAL_SKIP = re.compile(r"""^\s+test\.(?:skip|fixme)\(\s*(?P<condition>[^"'`\s)][^,]*),\s*["'`]""")
ALWAYS = {"true", "1", "!0", "!false"}

# Coverage floors and budgets, as (what, pattern) per kind of file; the pattern's
# last group is the value. A value that occurs more than once is numbered.
_NUMBER = r"(-?\d+(?:\.\d+)?)"
COVERAGE_FLOOR = (("coverage-floor", re.compile(r"^\s*coverage-floor:\s*['\"]?" + _NUMBER, re.M)),)
FAIL_UNDER = (
    ("fail_under", re.compile(r"^\s*fail_under\s*[=:]\s*['\"]?" + _NUMBER, re.M)),
    ("--cov-fail-under", re.compile(r"--cov-fail-under[= ]" + _NUMBER)),
)
JS_THRESHOLDS = (
    (
        "coverage threshold",
        re.compile(r"""\b(lines|branches|functions|statements)['"]?\s*:\s*""" + _NUMBER),
    ),
)
PYTHON_CONFIG = {"pyproject.toml", "setup.cfg", ".coveragerc", "tox.ini", "pytest.ini"}
JS_CONFIG = re.compile(r"(jest|vitest|vite)\.config\.[\w.]+")

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
    if name in TEST_CONFIG_FILES or TEST_CONFIG_PATTERN.fullmatch(name):
        return "Test, coverage and budget configuration"
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
    "Test, coverage and budget configuration",
    "Build and release files",
    "Other watched paths",
    "Coverage floors and budgets",
    "Tests weakened",
)


def is_test_file(path: str) -> bool:
    p = PurePosixPath(path)
    if p.suffix == ".rs" and "tests" in p.parts[:-1]:
        return True
    return bool(TEST_FILE.fullmatch(p.name))


def test_name(line: str) -> str | None:
    for pattern in TEST_NAME:
        m = pattern.search(line)
        if m:
            return m.group(m.lastindex or 1)
    return None


def is_skip(line: str) -> bool:
    """Whether an added line skips a test or expects it to fail. A comment doesn't,
    nor does Playwright's conditional skip inside a block."""
    text = line.strip()
    if text.startswith("//") or (text.startswith("#") and not text.startswith("#[")):
        return False
    m = CONDITIONAL_SKIP.match(line)
    if m and m.group("condition").strip() not in ALWAYS:
        return False
    return bool(SKIP.search(text))


def sets_limits(path: str) -> bool:
    """Whether a file is one `limits` reads."""
    p = PurePosixPath(path)
    return (
        p.name in (BUDGET_FILE, "package.json")
        or p.name in PYTHON_CONFIG
        or bool(JS_CONFIG.fullmatch(p.name))
        or (p.parts[:2] == (".github", "workflows") and p.suffix in (".yml", ".yaml"))
    )


def limits(path: str, text: str | None) -> dict[str, str]:
    """The coverage floors and budgets a file sets, by name, as written."""
    if not text or not sets_limits(path):
        return {}
    p = PurePosixPath(path)
    if p.name == BUDGET_FILE:
        try:
            return _flatten(json.loads(text))
        except ValueError:
            return {}
    if p.name == "package.json":
        try:
            data = json.loads(text)
        except ValueError:
            return {}
        jest = data.get("jest") if isinstance(data, dict) else None
        threshold = jest.get("coverageThreshold") if isinstance(jest, dict) else None
        return _flatten(threshold, "coverageThreshold") if threshold else {}
    if p.name in PYTHON_CONFIG:
        patterns = FAIL_UNDER
    elif JS_CONFIG.fullmatch(p.name):
        patterns = JS_THRESHOLDS
    else:
        patterns = COVERAGE_FLOOR
    out: dict[str, str] = {}
    for what, pattern in patterns:
        for m in pattern.finditer(text):
            key = what if what != "coverage threshold" else m.group(1)
            n, numbered = 1, key
            while numbered in out:
                n += 1
                numbered = f"{key} ({n})"
            out[numbered] = m.group(m.lastindex or 1)
    return out


def _flatten(value: object, prefix: str = "") -> dict[str, str]:
    """The numbers in a JSON value, by their path (`routes/src/a.tsx`)."""
    out: dict[str, str] = {}
    if isinstance(value, dict):
        items = list(value.items())
    elif isinstance(value, list):
        items = list(enumerate(value))
    else:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[prefix] = json.dumps(value)
        return out
    for key, item in items:
        out.update(_flatten(item, f"{prefix}/{key}" if prefix else str(key)))
    return out


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


def _range(since: str, until: str) -> str:
    return until if since == EMPTY_TREE else f"{since}..{until}"


def _subjects(since: str, until: str, path: str, cwd: str | None, *extra: str) -> list[str]:
    log = _git("log", "--reverse", "--format=%s", *extra, _range(since, until), "--", path, cwd=cwd)
    return log.splitlines()


def _file_log(since: str, until: str, cwd: str | None) -> dict[str, list[tuple[str, str]]]:
    """Every path the range's commits touch, with (status, subject) per commit, oldest
    first: one `git log` for the whole range, not one per file."""
    out: dict[str, list[tuple[str, str]]] = {}
    log = _git(
        "log", "--reverse", "--no-renames", "--name-status", "--format=%x00%s",
        _range(since, until), cwd=cwd,
    )  # fmt: skip
    for record in log.split("\0")[1:]:
        subject, _, rest = record.partition("\n")
        for row in rest.splitlines():
            status, tab, path = row.partition("\t")
            if tab:
                out.setdefault(path, []).append((status, subject))
    return out


def _show(rev: str, path: str, cwd: str | None) -> str | None:
    """A file's content at a commit, or None where it doesn't exist. cat-file applies
    no filters or textconv drivers."""
    if rev == EMPTY_TREE:
        return None
    try:
        return _git("cat-file", "blob", f"{rev}:{path}", cwd=cwd)
    except (subprocess.CalledProcessError, UnicodeDecodeError):
        return None


@dataclass
class _Lines:
    added: list[tuple[str, str]] = field(default_factory=list)  # (path, line)
    # (the path it was removed from, the file's path now, line)
    removed: list[tuple[str, str, str]] = field(default_factory=list)


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
            out.removed.append((old, new or old, line[1:]))
    return out


def _weakened(d: Digest, deleted: set[str], cwd: str | None) -> None:
    lines = _changed_lines(d.since, d.until, cwd)
    # A test or skip that stayed in its file (a changed line, or a renamed file) is no
    # change; so is a deleted file's tests, which the file's own line covers. Matched
    # by file and name: a test of the same name added in another file doesn't hide
    # one removed here.
    added_names = {(path, n) for path, line in lines.added if (n := test_name(line))}
    removed_lines = {(now, line.strip()) for _, now, line in lines.removed}
    seen: set[tuple[str, str]] = set()
    for path, now, line in lines.removed:
        name = test_name(line)
        if not name or (now, name) in added_names or path in deleted or (path, name) in seen:
            continue
        seen.add((path, name))
        subjects = _subjects(d.since, d.until, path, cwd, "-S", name)
        d.add("Tests weakened", f"{code(path)}: test removed: {code(name)}", _prs(subjects))
    for path, line in lines.added:
        text = line.strip()
        if is_skip(line) and (path, text) not in removed_lines and (path, text) not in seen:
            seen.add((path, text))
            subjects = _subjects(d.since, d.until, path, cwd, "-S", text)
            what = f"{code(path)}: skip or expected failure added: {code(text[:80])}"
            d.add("Tests weakened", what, _prs(subjects))


def _limits(d: Digest, old: str, new: str, subjects: list[str], cwd: str | None) -> None:
    if not (sets_limits(old) or sets_limits(new)):
        return
    before = limits(old, _show(d.since, old, cwd))
    after = limits(new, _show(d.until, new, cwd))
    for key in list(before) + [k for k in after if k not in before]:
        if before.get(key) != after.get(key):
            change = f"{before.get(key, 'unset')} -> {after.get(key, 'unset')}"
            d.add("Coverage floors and budgets", f"{code(new)}: {code(key)} {change}", _prs(subjects))


def digest(since: str, until: str = "HEAD", cwd: str | None = None, watch: tuple[str, ...] = ()) -> Digest:
    d = Digest(since, until)
    deleted: set[str] = set()
    log = _file_log(since, until, cwd)
    rows = _git("diff", "--no-ext-diff", "--name-status", "-M", since, until, cwd=cwd)
    for row in rows.splitlines():
        status, *paths = row.split("\t")
        path = paths[-1]
        subjects = [subject for _, subject in log.get(path, [])]
        section = category(path, watch) or (category(paths[0], watch) if len(paths) > 1 else None)
        if section:
            shown = " -> ".join(code(p) for p in paths)
            d.add(section, f"{status[0]} {shown}", _prs(subjects))
        _limits(d, paths[0], path, subjects, cwd)
        if status == "D" and is_test_file(path):
            deleted.add(path)
            pr = _prs([subject for s, subject in log.get(path, []) if s == "D"])
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


def markdown(d: Digest, limit: int = MAX_LENGTH) -> str:
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
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    return _truncated(lines, limit)


def _truncated(lines: list[str], limit: int) -> str:
    """The digest cut to `limit` characters at a line, with a note of what is left."""
    entries = sum(1 for line in lines if line.startswith("- "))
    note = (
        "**Truncated:** {} more entries are not shown, to stay under GitHub's comment "
        "size limit. Run `scripts/release_digest.py` from `those-who-run/.github` "
        "locally for the full list."
    )
    room = limit - len(note.format(entries)) - 2
    kept: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > room:
            break
        kept.append(line)
        size += len(line) + 1
    left = entries - sum(1 for line in kept if line.startswith("- "))
    return "\n".join(kept + ["", note.format(left), ""])


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
