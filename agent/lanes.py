"""The lane of a change: how closely a pull request is read, from the paths it changes.

    git diff --name-status <base>...<head> | python3 agent/lanes.py --repo owner/name

prints one word: `light`, `standard` or `sensitive`. The patterns are in lanes.json,
beside this file; this is the reference for what they mean, and the tests in
agent/tests/test_lanes.py are its examples. No model reads the diff to decide this:
a pull request can't talk its way into a lighter lane.

- `sensitive`: any path matches a sensitive pattern, the repository's own included.
  One sensitive file makes the whole change sensitive.
- `light`: every path is documentation, or a test file the change adds. A test that
  is edited, renamed or deleted is not light: that is where a weakened test hides.
- `standard`: everything else, and an empty change (nothing to read is not a reason
  to read less).

A pattern is matched against the whole path from the repository's root. `*` matches
within one directory, `**` matches across directories, and `**/` at the start also
matches a file at the root. A rename or a copy counts as both of its paths, each
with the rename's status, so neither is an added file.

Standard library only, Python 3.9 or later. Exits 2 on bad input: an unreadable
config, or a line that isn't `git diff --name-status` output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "lanes.json"
LANES = ("light", "standard", "sensitive")
KEYS = ("sensitive", "docs", "tests")
ADDED = "A"


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """A lanes.json pattern as a regular expression over a whole path."""
    if not pattern or pattern.startswith("/") or "\\" in pattern:
        raise ValueError(f"bad pattern {pattern!r}: a relative path with forward slashes")
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append(r"(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(r".*")
            i += 2
        elif pattern[i] == "*":
            out.append(r"[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def load(path: Path = CONFIG) -> dict:
    """The config, checked: a wrong key or pattern fails here, not at the first PR."""
    config = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    for key in KEYS:
        if not isinstance(config.get(key), list) or not config[key]:
            problems.append(f"{key}: a list of patterns, not empty")
    repositories = config.get("repositories", {})
    if not isinstance(repositories, dict):
        problems.append("repositories: an object, repository name to its patterns")
        repositories = {}
    for repo, extra in repositories.items():
        if not isinstance(extra, dict) or set(extra) - {"sensitive"}:
            problems.append(f"repositories.{repo}: only `sensitive` may be added per repository")
    for key, patterns in _all_patterns(config):
        for pattern in patterns:
            if not isinstance(pattern, str):
                problems.append(f"{key}: {pattern!r} is not a string")
                continue
            try:
                compile_pattern(pattern)
            except ValueError as error:
                problems.append(f"{key}: {error}")
    if problems:
        raise ValueError("; ".join(problems))
    return config


def _all_patterns(config: dict):
    for key in KEYS:
        if isinstance(config.get(key), list):
            yield key, config[key]
    repositories = config.get("repositories", {})
    if isinstance(repositories, dict):
        for repo, extra in repositories.items():
            if isinstance(extra, dict) and isinstance(extra.get("sensitive"), list):
                yield f"repositories.{repo}.sensitive", extra["sensitive"]


def _matches(path: str, patterns: list[str]) -> bool:
    return any(compile_pattern(p).fullmatch(path) for p in patterns)


def classify(repo: str, changes: list[tuple[str, str]], config: dict) -> str:
    """The lane for `changes`, a list of (status letter, path), in `repo` (owner/name)."""
    sensitive = list(config["sensitive"])
    for name, extra in config.get("repositories", {}).items():
        if name.lower() == repo.lower():
            sensitive += extra.get("sensitive", [])
    if not changes:
        return "standard"
    if any(_matches(path, sensitive) for _, path in changes):
        return "sensitive"
    for status, path in changes:
        if _matches(path, config["docs"]):
            continue
        if status == ADDED and _matches(path, config["tests"]):
            continue
        return "standard"
    return "light"


def parse_name_status(text: str) -> list[tuple[str, str]]:
    """`git diff --name-status` output as (status letter, path); a rename gives two."""
    changes = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split("\t")
        status = fields[0][:1]
        paths = fields[1:]
        wanted = 2 if status in ("R", "C") else 1
        if status not in "ACDMRT" or not status or len(paths) != wanted or not all(paths):
            raise ValueError(f"line {number}: not `git diff --name-status` output: {line!r}")
        changes += [(status, path) for path in paths]
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the lane of a change read from stdin.")
    parser.add_argument("--repo", required=True, help="the repository, as owner/name")
    parser.add_argument("--config", type=Path, default=CONFIG, help="lanes.json (default: beside this file)")
    args = parser.parse_args(argv)
    try:
        config = load(args.config)
        changes = parse_name_status(sys.stdin.read())
    except (OSError, ValueError) as error:
        print(f"lanes: {error}", file=sys.stderr)
        return 2
    print(classify(args.repo, changes, config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
