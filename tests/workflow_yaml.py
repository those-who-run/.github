"""A small reader for this repository's workflow files, for the tests beside it.

Standard library only, so it reads a workflow line by line and relies on its
two-space indentation, which actionlint keeps valid. It is not a YAML parser: it
knows the shapes these workflows use and nothing more.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

JOB = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
KEY = re.compile(r"^    ([a-z-]+):(.*)$")
PERMISSION = re.compile(r"^      ([a-z-]+):\s*(read|write|none)\b")
STEP_NAME = re.compile(r"^      - name:\s*(.+?)\s*$")
STEP_KEY = re.compile(r"^        ([a-z-]+):(.*)$")


def jobs(text: str) -> dict[str, dict]:
    """Each job's permissions, its other top-level keys and its steps' lines."""
    found: dict[str, dict] = {}
    job = None
    in_jobs = False
    section = None
    for line in text.splitlines():
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if m := JOB.match(line):
            job = m.group(1)
            found[job] = {"permissions": {}, "keys": {}, "steps": []}
            section = None
            continue
        if job is None:
            continue
        if m := KEY.match(line):
            key, value = m.group(1), m.group(2).split(" #")[0].strip()
            found[job]["keys"][key] = value
            section = key
            continue
        if section == "permissions" and (m := PERMISSION.match(line)):
            found[job]["permissions"][m.group(1)] = m.group(2)
        elif section == "steps":
            found[job]["steps"].append(line)
    return found


def step(job: dict, name: str) -> dict[str, str]:
    """A named step's keys, with a `run: |` block as its script."""
    keys: dict[str, str] = {}
    inside = False
    block = None
    for line in job["steps"]:
        if m := STEP_NAME.match(line):
            if inside:
                break
            inside = m.group(1) == name
            continue
        if not inside:
            continue
        if line.startswith("      - "):
            break
        if m := STEP_KEY.match(line):
            key, value = m.group(1), m.group(2).strip()
            block = key if value == "|" else None
            keys[key] = "" if block else value
        elif block and (line.startswith("          ") or not line.strip()):
            keys[block] += line[10:] + "\n"
    if not inside and not keys:
        raise KeyError(name)
    return keys
