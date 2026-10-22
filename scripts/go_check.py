"""The checks go.yml runs on a Go module, one subcommand each (#15).

    python3 go_check.py <check> [--floor PERCENT] [--tools-modfile tools/go.mod]

Run it from the module's directory (the one with go.mod). The checks:

    fmt     gofmt lists no file
    tidy    `go mod tidy` would change neither go.mod nor go.sum, nor the tools
            module's tools/go.mod and tools/go.sum
    vet     go vet ./...
    lint    golangci-lint, the caller's config, the version in tools/go.mod
    test    the race detector, shuffled order; fails on a failing or skipped test,
            or total coverage below --floor
    build   go build ./...
    vuln    govulncheck, failing on a vulnerability the code reaches
    all     every check above, reporting each, failing if any fails

The tools run with `go tool -modfile=tools/go.mod <tool>`: their versions are pinned
in the module's own tools/go.mod and checksummed in tools/go.sum, so `make check` on
a laptop and CI run the same binaries. Before lint and vuln run a tool, tools/go.mod
must require golangci-lint and govulncheck from their upstream modules
(github.com/golangci/golangci-lint/v2, golang.org/x/vuln), list them by those paths
in its `tool` block, and have no `replace` directive; the tools then run by their
full package paths, with GOWORK=off.

go.yml checks this file out of the `.github` repository at the commit the caller
pinned, never from the PR it checks. tests/go/test_go_check.py breaks a small module
one way at a time and expects the matching check to fail. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_FLOOR = 90.0
DEFAULT_TOOLS = "tools/go.mod"
# The tools go.yml runs, by package path, and the upstream module each comes from.
GOLANGCI_LINT = "github.com/golangci/golangci-lint/v2/cmd/golangci-lint"
GOVULNCHECK = "golang.org/x/vuln/cmd/govulncheck"
TOOL_MODULES = {
    GOLANGCI_LINT: "github.com/golangci/golangci-lint/v2",
    GOVULNCHECK: "golang.org/x/vuln",
}
_TOTAL = re.compile(r"^total:\s+\(statements\)\s+([\d.]+)%\s*$", re.MULTILINE)


def _in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def error(message: str, path: str | None = None) -> None:
    if _in_actions():
        where = f" file={_repo_path(path)}" if path else ""
        print(f"::error{where}::{message}", flush=True)
    else:
        print(f"error: {path + ': ' if path else ''}{message}", file=sys.stderr, flush=True)


def _repo_path(path: str) -> str:
    """A path relative to the checkout, which annotations need, not to the module."""
    root = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()
    return os.path.relpath(os.path.abspath(path), root)


def summary(text: str) -> None:
    if name := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(name, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def run(*cmd: str, env: dict[str, str] | None = None) -> int:
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, env=env).returncode


def go_files() -> list[str]:
    """The module's Go files, skipping what the go command skips: testdata, vendor
    and directories starting with `.` or `_`, and nested modules."""
    found = []
    for top, dirs, files in os.walk("."):
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith((".", "_"))
            and d not in ("testdata", "vendor")
            and not os.path.exists(os.path.join(top, d, "go.mod"))
        )
        found += [os.path.join(top, f)[2:] for f in sorted(files) if f.endswith(".go")]
    return found


def check_fmt(_: argparse.Namespace) -> bool:
    files = go_files()
    if not files:
        return True
    out = subprocess.run(["gofmt", "-l", *files], capture_output=True, text=True)
    if out.returncode != 0:
        print(out.stderr, end="")
        error("gofmt could not read the files above")
        return False
    unformatted = out.stdout.split()
    for path in unformatted:
        error("not formatted: run gofmt -w on it", path)
    return not unformatted


def check_tidy(args: argparse.Namespace) -> bool:
    # -diff prints what tidy would change and exits non-zero, touching nothing.
    ok = True
    if run("go", "mod", "tidy", "-diff") != 0:
        error("go.mod or go.sum is not tidy: run go mod tidy", "go.mod")
        ok = False
    # The tools module is a module of its own, in its own directory: tidied there,
    # it keeps the tools' requirements and nothing else.
    tools = Path(args.tools_modfile)
    if tools.is_file():
        print(f"+ (in {tools.parent}) go mod tidy -diff", flush=True)
        if subprocess.run(["go", "mod", "tidy", "-diff"], cwd=tools.parent).returncode != 0:
            error(
                f"{tools} or its go.sum is not tidy: run go mod tidy in {tools.parent}",
                str(tools),
            )
            ok = False
    return ok


def check_vet(_: argparse.Namespace) -> bool:
    return run("go", "vet", "./...") == 0


def tools_problems(modfile: str) -> list[str]:
    """What keeps tools/go.mod from naming the upstream tools: a `replace`
    directive, or a tool or its module missing."""
    out = subprocess.run(
        ["go", "mod", "edit", "-json", modfile], capture_output=True, text=True
    )
    if out.returncode != 0:
        return [f"go mod edit could not read it: {out.stderr.strip()}"]
    mod = json.loads(out.stdout)
    problems = []
    for r in mod.get("Replace") or []:
        old = r["Old"]["Path"] + (f"@{r['Old']['Version']}" if r["Old"].get("Version") else "")
        problems.append(f"it has a replace directive, for {old}: remove it")
    required = {r["Path"] for r in mod.get("Require") or []}
    tools = {t["Path"] for t in mod.get("Tool") or []}
    for tool, module in TOOL_MODULES.items():
        if module not in required:
            problems.append(f"it does not require {module}")
        if tool not in tools:
            problems.append(f"its tool block does not list {tool}")
    return problems


def _tool(args: argparse.Namespace, tool: str, *tool_args: str) -> bool:
    if not Path(args.tools_modfile).is_file():
        error(
            f"{args.tools_modfile} is missing: the module pins its tools there "
            "(go get -modfile=tools/go.mod -tool <tool>@<version>)"
        )
        return False
    if problems := tools_problems(args.tools_modfile):
        for problem in problems:
            error(f"{args.tools_modfile}: {problem}", args.tools_modfile)
        return False
    # By full package path, outside any go.work, so only tools/go.mod decides.
    env = {**os.environ, "GOWORK": "off"}
    return run("go", "tool", f"-modfile={args.tools_modfile}", tool, *tool_args, env=env) == 0


def check_lint(args: argparse.Namespace) -> bool:
    return _tool(args, GOLANGCI_LINT, "run", "./...")


def check_vuln(args: argparse.Namespace) -> bool:
    return _tool(args, GOVULNCHECK, "./...")


def check_build(_: argparse.Namespace) -> bool:
    return run("go", "build", "./...") == 0


def check_test(args: argparse.Namespace) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        profile = os.path.join(tmp, "cover.out")
        # The race detector needs cgo, whatever the build uses.
        env = {**os.environ, "CGO_ENABLED": "1"}
        cmd = ["go", "test", "-race", "-shuffle=on", "-covermode=atomic"]
        cmd += [f"-coverprofile={profile}", "-json", "./..."]
        print("+ " + " ".join(cmd), flush=True)
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, text=True)
        assert proc.stdout is not None
        skipped, failed = read_events(proc.stdout)
        passed = proc.wait() == 0
        coverage = total_coverage(profile) if passed else None

    ok = passed
    if not passed:
        error("go test failed" + (": " + ", ".join(failed) if failed else ""))
    for name in skipped:
        error(f"test skipped: {name}. A skipped test fails, as in v1: fix it or remove it")
        ok = False
    if coverage is not None:
        line = f"Coverage: {coverage:.1f}% of statements (the floor is {args.floor:g}%)."
        print(line)
        summary(line)
        if coverage < args.floor:
            error(f"coverage {coverage:.1f}% is below the floor of {args.floor:g}%")
            ok = False
    return ok


def read_events(lines) -> tuple[list[str], list[str]]:
    """Echo `go test -json` readably and return the skipped and failed tests.

    A test's own output is shown only when it fails or skips, so a green run stays
    short; package lines (ok, FAIL, the shuffle seed, build errors) always are."""
    skipped: list[str] = []
    failed: list[str] = []
    held: dict[tuple[str, str], list[str]] = {}
    for raw in lines:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            print(raw, end="")
            continue
        action, test = event.get("Action"), event.get("Test")
        name = f"{event.get('Package', '')}.{test}"
        if output := event.get("Output"):
            if test:
                held.setdefault((event.get("Package", ""), test), []).append(output)
            else:
                print(output, end="")
        if not test:
            continue
        if action in ("pass", "fail", "skip"):
            lines_out = held.pop((event.get("Package", ""), test), [])
            if action != "pass":
                print("".join(lines_out), end="")
            if action == "skip":
                skipped.append(name)
            elif action == "fail":
                failed.append(name)
    sys.stdout.flush()
    return skipped, failed


def total_coverage(profile: str) -> float:
    out = subprocess.run(
        ["go", "tool", "cover", f"-func={profile}"], capture_output=True, text=True
    )
    match = _TOTAL.search(out.stdout)
    if out.returncode != 0 or not match:
        print(out.stdout + out.stderr, end="")
        return 0.0
    return float(match.group(1))


CHECKS = {
    "fmt": check_fmt,
    "tidy": check_tidy,
    "vet": check_vet,
    "lint": check_lint,
    "test": check_test,
    "build": check_build,
    "vuln": check_vuln,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("check", choices=[*CHECKS, "all"])
    parser.add_argument("--floor", type=float, default=DEFAULT_FLOOR)
    parser.add_argument("--tools-modfile", default=DEFAULT_TOOLS)
    args = parser.parse_args(argv)
    if not Path("go.mod").is_file():
        error("no go.mod here: run this from the module's directory")
        return 2
    names = list(CHECKS) if args.check == "all" else [args.check]
    failed = [name for name in names if not CHECKS[name](args)]
    if args.check == "all" and failed:
        print("Failed: " + ", ".join(failed), file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
