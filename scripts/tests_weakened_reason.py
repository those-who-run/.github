"""Fails a PR that weakens its tests without saying why (#14, #15).

    python3 tests_weakened_reason.py --weakened true|false [--pr NUMBER] [--body-file FILE]

A language's test guard decides whether a change weakens the tests; this checks the
other half, the same for every language: when it does, the PR's description needs a
line

    Tests weakened: <which test and why>

with a reason of at least ten characters once trimmed, as in v1. Line endings may be
LF or CRLF.

With `--weakened false` it passes at once. Otherwise it reads the description of the
PR named by --pr, or by the event in $GITHUB_EVENT_PATH, from the GitHub API with
$GH_TOKEN (the job needs `pull-requests: read`). It reads it when it runs, not from
the event, so a re-run after an edit sees the new description. --body-file reads a
local file instead, for tests.

Each language workflow runs it in the same job as its guard, checked out of the
`.github` repository at the commit the caller pinned, never from the PR it checks.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

MIN_REASON = 10
_LINE = re.compile(r"^[ \t]*Tests weakened:(.*)$")


def reason(body: str) -> str | None:
    """The first `Tests weakened:` reason long enough once trimmed, or None."""
    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if (m := _LINE.match(line)) and len(text := m.group(1).strip()) >= MIN_REASON:
            return text
    return None


def pr_number() -> int:
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        return int(json.load(f)["pull_request"]["number"])


def fetch_body(pr: int) -> str:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    repo = os.environ["GITHUB_REPOSITORY"]
    request = urllib.request.Request(
        f"{api}/repos/{repo}/pulls/{pr}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response).get("body") or ""


def summary(text: str) -> None:
    if name := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(name, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weakened", required=True, choices=["true", "false"])
    parser.add_argument("--pr", type=int)
    parser.add_argument("--body-file")
    args = parser.parse_args(argv)

    if args.weakened == "false":
        print("The test guard found no weakened test: no reason needed.")
        return 0
    if args.body_file:
        with open(args.body_file, encoding="utf-8", newline="") as f:
            body = f.read()
    else:
        body = fetch_body(args.pr or pr_number())
    why = reason(body)
    if why:
        print(f"Tests weakened: {why}")
        summary(f"**Tests weakened:** {why}")
        return 0
    message = (
        "This PR weakens tests: its description needs a line "
        "'Tests weakened: <which test and why>', with at least ten characters of "
        "reason. Add it and re-run the job."
    )
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::error::{message}")
    else:
        print(f"error: {message}", file=sys.stderr)
    summary(message)
    return 1


if __name__ == "__main__":
    sys.exit(main())
