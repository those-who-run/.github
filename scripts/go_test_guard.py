"""Reports the ways a change weakens a Go repository's tests (#15).

    python3 go_test_guard.py <base> <head>

It reads git history only: the diff from the merge base of <base> and <head> to
<head>. Reported:

- a `_test.go` file deleted, or renamed to something that is not a test file;
- a `Test`, `Fuzz` or `Example` function removed that does not come back elsewhere
  in the change;
- a skip added to a test file (`t.Skip`, `t.Skipf`, `t.SkipNow`);
- a build constraint added to a test file (`//go:build integration` keeps it out
  of a plain `go test`);
- a coverage floor lowered or removed: a `coverage-floor:` input in a workflow, or
  a `COVERAGE_FLOOR` variable in a Makefile, anywhere in the repository.

A line moved unchanged (removed in one place, added in another) does not count.

It always exits 0 when it could read the change, whatever it finds: it writes
`weakened=true` or `weakened=false` to $GITHUB_OUTPUT, and go.yml passes that on as
its `tests-weakened` output. checks.yml (#14) then requires a `Tests weakened:` line
in the PR's description. What it finds goes to the job summary either way.

go.yml fetches this file from the `.github` repository at the commit the caller
pinned, never from the PR it checks, and runs it before any of the PR's code.
Standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass

_TEST_FUNC = re.compile(r"^func\s+(?:\([^)]*\)\s*)?((?:Test|Fuzz|Example)\w*)\s*\(")
_SKIP = re.compile(r"\.Skip(?:f|Now)?\(")
_BUILD_TAG = re.compile(r"^\s*//(?:go:build\s|\s*\+build\s)")
_FLOOR = re.compile(
    r"\b(?:coverage[-_]floor|COVERAGE_FLOOR)\b\s*(?::|\?=|:=|=)\s*[\"']?(\d+(?:\.\d+)?)"
)


def is_test_file(path: str) -> bool:
    return path.endswith("_test.go")


@dataclass(frozen=True)
class Finding:
    path: str
    what: str


@dataclass
class Diff:
    """The lines a change adds and removes, per file, and what it deletes or renames."""

    added: dict[str, list[str]]
    removed: dict[str, list[str]]
    deleted: list[str]
    renamed: list[tuple[str, str]]


def _git(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def read_diff(base: str, head: str, cwd: str | None = None) -> Diff:
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    deleted: list[str] = []
    renamed: list[tuple[str, str]] = []
    for row in _git("diff", "--name-status", "-M", f"{base}...{head}", cwd=cwd).splitlines():
        status, *paths = row.split("\t")
        if status == "D":
            deleted.append(paths[0])
        elif status.startswith("R"):
            renamed.append((paths[0], paths[1]))
    old = new = ""
    in_header = False
    patch = _git("diff", "--unified=0", "-M", "--no-color", f"{base}...{head}", cwd=cwd)
    for line in patch.splitlines():
        # `--- ` and `+++ ` are file names only between `diff --git` and the first
        # hunk; inside a hunk they are a removed `-- ...` or an added `++ ...` line.
        if line.startswith("diff --git "):
            in_header, old, new = True, "", ""
        elif line.startswith("@@"):
            in_header = False
        elif in_header and line.startswith("--- "):
            old = line[6:] if line.startswith("--- a/") else ""
        elif in_header and line.startswith("+++ "):
            new = line[6:] if line.startswith("+++ b/") else ""
        elif in_header:
            continue
        elif line.startswith("+") and new:
            added.setdefault(new, []).append(line[1:])
        elif line.startswith("-") and old:
            removed.setdefault(old, []).append(line[1:])
    return Diff(added, removed, deleted, renamed)


def _added_in_tests(diff: Diff, pattern: re.Pattern[str], what: str) -> list[Finding]:
    """Added test-file lines matching pattern, less the same lines removed (a move)."""
    moved = Counter(
        line.strip()
        for path, lines in diff.removed.items()
        if is_test_file(path)
        for line in lines
        if pattern.search(line)
    )
    found = []
    for path, lines in sorted(diff.added.items()):
        if not is_test_file(path):
            continue
        for line in lines:
            if not pattern.search(line):
                continue
            if moved[line.strip()]:
                moved[line.strip()] -= 1
            else:
                found.append(Finding(path, f"{what}: {line.strip()}"))
    return found


def _floors(lines: list[str]) -> list[float]:
    return [float(m.group(1)) for line in lines if (m := _FLOOR.search(line))]


def weakenings(base: str, head: str, cwd: str | None = None) -> list[Finding]:
    """What <base>...<head> does to weaken the tests."""
    diff = read_diff(base, head, cwd)
    found: list[Finding] = []

    for path in diff.deleted:
        if is_test_file(path):
            found.append(Finding(path, "test file deleted"))
    for old, new in diff.renamed:
        if is_test_file(old) and not is_test_file(new):
            found.append(Finding(old, f"test file renamed to {new}, which is not a test file"))

    # A test function removed in one place and not added in another is a removal.
    added_names = Counter(
        m.group(1)
        for path, lines in diff.added.items()
        if is_test_file(path)
        for line in lines
        if (m := _TEST_FUNC.match(line))
    )
    for path, lines in sorted(diff.removed.items()):
        if not is_test_file(path) or path in diff.deleted:
            continue
        for line in lines:
            if not (m := _TEST_FUNC.match(line)):
                continue
            if added_names[m.group(1)]:
                added_names[m.group(1)] -= 1
            else:
                found.append(Finding(path, f"test removed: {m.group(1)}"))

    found += _added_in_tests(diff, _SKIP, "skip added")
    found += _added_in_tests(diff, _BUILD_TAG, "build constraint added to a test file")

    # Per file: a floor that goes down, or goes away (falling back to a default).
    for path in sorted(set(diff.removed) | set(diff.added)):
        before = _floors(diff.removed.get(path, []))
        after = _floors(diff.added.get(path, []))
        if not before:
            continue
        if not after:
            found.append(Finding(path, f"coverage floor removed: {max(before):g}%"))
        elif min(after) < max(before):
            found.append(
                Finding(path, f"coverage floor lowered: {max(before):g}% -> {min(after):g}%")
            )
    return found


def report(found: list[Finding]) -> str:
    if not found:
        return "### Test guard\n\nNothing in this change weakens the tests.\n"
    lines = ["### Test guard", "", "This change weakens the tests:", ""]
    lines += [f"- `{f.path}`: {f.what}" for f in found]
    lines += [
        "",
        "The PR's description needs a line `Tests weakened: <reason>` saying which "
        "test and why (checks.yml checks it). If the change is not right, fix the "
        "code, not the test.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base")
    parser.add_argument("head")
    args = parser.parse_args(argv)

    found = weakenings(args.base, args.head)
    text = report(found)
    print(text, end="")
    for f in found:
        print(f"::warning file={f.path}::{f.what}")
    if name := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(name, "a", encoding="utf-8") as out:
            out.write(text)
    if name := os.environ.get("GITHUB_OUTPUT"):
        with open(name, "a", encoding="utf-8") as out:
            out.write(f"weakened={'true' if found else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
