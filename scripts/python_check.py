"""The checks python.yml runs on a Python project, one subcommand each (paddock#55).

    python3 python_check.py <check> [--floor PERCENT]

Run it from the project's directory (the one with pyproject.toml and uv.lock), with
uv on the PATH. The checks:

    lock    uv.lock is up to date with pyproject.toml (`uv lock --check`)
    sync    the project and its default dependency groups installed from uv.lock as
            it stands (`uv sync --frozen`), and the tools below among them
    format  `ruff format --check` lists no file
    lint    `ruff check`, the project's config
    types   mypy, the project's config (the files it checks and its strictness)
    test    pytest under coverage; fails on a failing or skipped test (an expected
            failure, xfail, counts as a skip), on no tests at all, on coverage
            measuring no statements or unable to report, or on total coverage
            below --floor, rounded to the project's `precision` as coverage
            rounds it
    all     every check above, reporting each, failing if any fails

The tools are the project's own: ruff, mypy, pytest and coverage come from its
uv.lock, in a dependency group uv installs by default (`dev`), so `make check` on a
laptop and CI run the same versions. format, lint, types and test run them with
`uv run --no-sync`, after `sync`: none of them changes the environment, so a stale
uv.lock fails `lock` and nothing else. ruff, mypy and coverage read their settings
from pyproject.toml; coverage's `[tool.coverage.run] source` says what is measured.

python.yml checks this file out of the `.github` repository at the commit the
caller pinned, never from the PR it checks. tests/python/test_python_check.py breaks
a small project one way at a time and expects the matching check to fail. Standard
library only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_FLOOR = 90.0
# The tools the checks run, from the project's environment.
TOOLS = ("ruff", "mypy", "pytest", "coverage")
# pytest's exit code when it collected no tests.
NO_TESTS = 5


def _in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def error(message: str, path: str | None = None) -> None:
    if _in_actions():
        where = f" file={_repo_path(path)}" if path else ""
        print(f"::error{where}::{message}", flush=True)
    else:
        print(f"error: {path + ': ' if path else ''}{message}", file=sys.stderr, flush=True)


def _repo_path(path: str) -> str:
    """A path relative to the checkout, which annotations need, not to the project."""
    root = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()
    return os.path.relpath(os.path.abspath(path), root)


def summary(text: str) -> None:
    if name := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(name, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def run(*cmd: str) -> int:
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd).returncode


def tool(name: str, *args: str) -> int:
    """A tool from the project's environment, which `sync` installed."""
    return run("uv", "run", "--no-sync", name, *args)


def missing_tools() -> list[str]:
    """The tools the project's environment lacks."""
    probe = "import shutil, sys; print(' '.join(t for t in sys.argv[1:] if not shutil.which(t)))"
    out = subprocess.run(
        ["uv", "run", "--no-sync", "python", "-c", probe, *TOOLS],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        print(out.stdout + out.stderr, end="")
        return list(TOOLS)
    return out.stdout.split()


def check_lock(_: argparse.Namespace) -> bool:
    if run("uv", "lock", "--check") != 0:
        error("uv.lock is not up to date with pyproject.toml: run uv lock", "uv.lock")
        return False
    return True


def check_sync(_: argparse.Namespace) -> bool:
    if run("uv", "sync", "--frozen") != 0:
        error("uv sync could not install the project from uv.lock", "uv.lock")
        return False
    if missing := missing_tools():
        error(
            "the project's environment has no " + ", ".join(missing)
            + ": add them to its dev dependency group (uv add --dev "
            + " ".join(missing) + ")",
            "pyproject.toml",
        )
        return False
    return True


def check_format(_: argparse.Namespace) -> bool:
    if tool("ruff", "format", "--check", "--diff") != 0:
        error("ruff format would change the files above: run ruff format")
        return False
    return True


def check_lint(_: argparse.Namespace) -> bool:
    return tool("ruff", "check", "--output-format", "full") == 0


def check_types(_: argparse.Namespace) -> bool:
    return tool("mypy") == 0


def skipped_tests(junit: str) -> list[str]:
    """The tests pytest's JUnit XML report shows skipped, xfail among them."""
    found = []
    for case in ET.parse(junit).getroot().iter("testcase"):
        if case.find("skipped") is not None:
            found.append(f"{case.get('classname', '')}::{case.get('name', '')}".lstrip(":"))
    return found


def read_totals(report: str) -> dict:
    """The `totals` of coverage's JSON report."""
    with open(report, encoding="utf-8") as f:
        return json.load(f)["totals"]


def below_floor(totals: dict, floor: float) -> bool:
    """Whether the total is below the floor, by coverage's own rule for fail_under:
    the total as it shows it, rounded to the project's `precision` (0 places unless
    set), so `coverage report` and this agree; and a floor of 100 is met only by
    100% itself, never by a total that rounds up to it."""
    if floor >= 100:
        return float(totals["percent_covered"]) < 100
    return float(totals["percent_covered_display"]) < floor


def check_test(args: argparse.Namespace) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        junit = os.path.join(tmp, "junit.xml")
        report = os.path.join(tmp, "coverage.json")
        data = os.path.join(tmp, ".coverage")
        os.environ["COVERAGE_FILE"] = data
        code = tool("coverage", "run", "-m", "pytest", "-rs", f"--junitxml={junit}")
        skipped = skipped_tests(junit) if os.path.isfile(junit) else []
        totals = None
        reported = True
        if code == 0:
            # --fail-under=0: the floor is --floor, checked below, and the project's
            # own fail_under must not turn coverage's exit into "nothing to say".
            reported = tool("coverage", "json", "-q", "--fail-under=0", "-o", report) == 0
            if reported:
                totals = read_totals(report)

    ok = code == 0
    if code == NO_TESTS:
        error("pytest collected no tests")
    elif code != 0:
        error("pytest failed")
    for name in skipped:
        error(f"test skipped: {name}. A skipped test fails, as in v1: fix it or remove it")
        ok = False
    if not reported:
        error("coverage could not report on the tests' run: see its message above")
        return False
    if totals is None:
        return ok
    if not totals.get("num_statements"):
        # As in Go, where a package without tests counts as uncovered: a floor that
        # measures nothing would pass anything.
        error(
            "coverage measured no statements: its [tool.coverage.run] source should "
            "name the project's code, and the tests import it"
        )
        return False
    line = f"Coverage: {totals['percent_covered_display']}% (the floor is {args.floor:g}%)."
    print(line)
    summary(line)
    if below_floor(totals, args.floor):
        error(f"coverage {totals['percent_covered_display']}% is below the floor of {args.floor:g}%")
        ok = False
    return ok


CHECKS = {
    "lock": check_lock,
    "sync": check_sync,
    "format": check_format,
    "lint": check_lint,
    "types": check_types,
    "test": check_test,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("check", choices=[*CHECKS, "all"])
    parser.add_argument("--floor", type=float, default=DEFAULT_FLOOR)
    args = parser.parse_args(argv)
    if not (Path("pyproject.toml").is_file() and Path("uv.lock").is_file()):
        error("no pyproject.toml and uv.lock here: run this from the project's directory")
        return 2
    if args.check != "all":
        return 0 if CHECKS[args.check](args) else 1
    failed = [name for name in ("lock", "sync") if not CHECKS[name](args)]
    # Without the environment the rest can only fail for that one reason.
    if "sync" not in failed:
        failed += [name for name in ("format", "lint", "types", "test") if not CHECKS[name](args)]
    if failed:
        print("Failed: " + ", ".join(failed), file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
