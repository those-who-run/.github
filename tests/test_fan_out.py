"""scripts/fan_out.py, which opens a PR in every repository moving it onto a release of
this repository, run against a stub `gh` and local git remotes, so nothing reaches
GitHub.

Each repository is a bare git repository under a temporary directory, which the
script clones and pushes to through FAN_OUT_GIT_BASE. The stub `gh` keeps the open
PRs in a JSON file, answers `gh pr list`, `gh pr create` and the `gh api` lookup of a
bot's ID, and logs every call. The release's files come from a local git repository
standing in for this one, tagged at the release. It has no manifest
(`agent/copies.json`), as every release before the manifest; the tests of the
manifest give it one of their own, so they don't depend on this repository's.

Standard library only; CI's `test` job runs it:

    python3 -m unittest discover tests
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workflow_yaml import WORKFLOWS, jobs

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "fan_out.py"
ORG = "those-who-run"

_spec = importlib.util.spec_from_file_location("fan_out", SCRIPT)
assert _spec and _spec.loader
fan_out = importlib.util.module_from_spec(_spec)
sys.modules["fan_out"] = fan_out  # dataclasses look their module up
_spec.loader.exec_module(fan_out)

OLD_SHA = "6328457b1a763ee250f4326023ee0a5db0ab78cb"
OLD = "v0.2.8"
NEW = "v0.3.0"
BRANCH = f"chore/github-{NEW}"
TITLE = f"ci: those-who-run/.github {NEW}"
GUARD = b"# the guard at the release\nprint('guard')\n"
SETTINGS = b'{"hooks": {"release": true}}\n'
# A skill with what copier would render, or choke on, in a file with its suffix.
SKILL = (b"---\nname: twr-dev\n---\n\n# The loop\n\nA workflow's ${{ github.sha }}, and a template's "
         b"{{ name }}, [[ name ]] and [% if image %], stay as they are.\n")
REVIEWER = b"---\nname: twr-reviewer\n---\n\nThe reviewer's brief.\n"
MANIFEST = {
    "agent/agents/twr-reviewer.md": ".claude/agents/twr-reviewer.md",
    "agent/guard.py": ".claude/hooks/guard.py",
    "agent/settings.json": ".claude/settings.json",
    "agent/skills/twr-dev/SKILL.md": ".claude/skills/twr-dev/SKILL.md",
}
# What each destination holds at the release.
COPIED = {
    ".claude/agents/twr-reviewer.md": REVIEWER,
    ".claude/hooks/guard.py": GUARD,
    ".claude/settings.json": SETTINGS,
    ".claude/skills/twr-dev/SKILL.md": SKILL,
}
# The rules every repository has today: the guard's files tracked, the rest of
# .claude/ ignored, the skills and the agents' briefs with it.
GITIGNORE = (
    "__pycache__/\n"
    "# Claude Code: only the shared settings, the guard and its config are tracked;\n"
    "# the rest, among it the worktrees in .claude/worktrees/, stays out.\n"
    ".claude/*\n"
    "!.claude/settings.json\n"
    "!.claude/guard.json\n"
    "!.claude/hooks/\n"
    ".claude/hooks/*\n"
    "!.claude/hooks/guard.py\n"
)
AFTER = ".cache/\nbuild/\n"
EXCEPTIONS = (
    "# Tracked: the copies of those-who-run/.github's agent/ files that its agent/copies.json lists.\n"
    "!.claude/agents/\n"
    "!.claude/skills/\n"
)
# A repository as each is today: the guard at an older release, and those rules.
HELD = {
    ".claude/hooks/guard.py": b"# an old guard\n",
    ".claude/settings.json": b"{}\n",
    ".claude/guard.json": "{}\n",
    ".gitignore": GITIGNORE + AFTER,
}

STUB = r'''
import json, os, sys

state_path, log_path = os.environ["GH_STATE"], os.environ["GH_CALLS"]
state = json.load(open(state_path))
args = sys.argv[1:]
call = {"argv": args}

def opt(name):
    return args[args.index(name) + 1] if name in args else None

def done(out="", status=0):
    with open(log_path, "a") as log:
        log.write(json.dumps(call) + "\n")
    json.dump(state, open(state_path, "w"))
    if out:
        print(out)
    sys.exit(status)

if args[:2] == ["pr", "list"]:
    repo, head = opt("--repo"), opt("--head")
    if repo in state.get("refuse", []):
        print("gh: HTTP 403: Resource not accessible by integration", file=sys.stderr)
        done(status=1)
    assert opt("--state") == "open", args
    if repo in state.get("garbage", []):
        done("not json")
    prs = [{"number": p["number"], "url": p["url"], "headRefName": p["head"],
            "author": {"login": p.get("author", "someone")}} for p in state["prs"]
           if p["repo"] == repo and p["state"] == "open" and head in (None, p["head"])]
    fields = opt("--json").split(",")
    done(json.dumps([{k: v for k, v in pr.items() if k in fields} for pr in prs]))
if args[:2] == ["pr", "close"]:
    repo, number = opt("--repo"), int(args[2])
    call["comment"] = opt("--comment")
    [pr] = [p for p in state["prs"] if p["repo"] == repo and p["number"] == number]
    pr["state"], pr["comment"] = "closed", opt("--comment")
    done()
if args[:2] == ["pr", "create"]:
    repo = opt("--repo")
    call["body"] = sys.stdin.read()
    number = len(state["prs"]) + 1
    url = f"https://github.com/{repo}/pull/{number}"
    state["prs"].append({"repo": repo, "number": number, "url": url, "head": opt("--head"),
                         "base": opt("--base"), "title": opt("--title"), "state": "open",
                         "body": call["body"], "author": "app/twr-release"})
    done(url)
if args[:1] == ["api"] and args[1].startswith("users/"):
    assert args[2:] == ["--jq", ".id"], args
    done(str(state["users"][args[1][len("users/"):]]))
call["unhandled"] = True
done(status=1)
'''


# The tests' git: no global or system config (a signing setting there would break
# the commits), and a fixed identity. setUp fills it in.
GIT_ENV: dict[str, str] = {}

# The environment without git's own variables: a commit hook runs these tests with
# GIT_INDEX_FILE and the like set to the committing checkout's, and git in the tests'
# repositories would write that index instead of their own.
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


class Link:
    """A symbolic link in a repository, to target."""

    def __init__(self, target: str | Path):
        self.target = str(target)


BOT = "4242+twr-release[bot]@users.noreply.github.com"
BOT_ENV = {"GIT_AUTHOR_NAME": "twr-release[bot]", "GIT_AUTHOR_EMAIL": BOT,
           "GIT_COMMITTER_NAME": "twr-release[bot]", "GIT_COMMITTER_EMAIL": BOT}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**ENV, **GIT_ENV}).stdout


def pinned(name: str, sha: str = OLD_SHA, tag: str = OLD) -> str:
    return f"    uses: {ORG}/.github/.github/workflows/{name}@{sha} # {tag}\n"


CI = (
    "name: CI\n"
    "jobs:\n"
    "  checks:\n"
    + pinned("checks.yml")
    + "  go:\n"
    + pinned("go.yml")
    + "  other:\n"
    "    uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
)
RENOVATE = (
    '{\n  "$schema": "https://docs.renovatebot.com/renovate-schema.json",\n'
    f'  "extends": ["github>{ORG}/.github#{OLD}"]\n}}\n'
)


class FanOut(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        self.env = {
            "PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.tmp / "home"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "tester", "GIT_AUTHOR_EMAIL": "tester@example.com",
            "GIT_COMMITTER_NAME": "tester", "GIT_COMMITTER_EMAIL": "tester@example.com",
            "GH_STATE": str(self.tmp / "gh.json"),
            "GH_CALLS": str(self.tmp / "calls.jsonl"),
            "FAN_OUT_GIT_BASE": (self.tmp / "remotes").as_uri(),
            "GITHUB_STEP_SUMMARY": str(self.tmp / "summary.md"),
        }
        (self.tmp / "home").mkdir()
        GIT_ENV.clear()
        GIT_ENV.update(self.env)
        (self.tmp / "bin").mkdir()
        gh = self.tmp / "bin" / "gh"
        gh.write_text(f"#!{sys.executable}\n{STUB}")
        gh.chmod(0o755)
        self.state = {"prs": [], "users": {"twr-release%5Bbot%5D": 4242}}
        self.save()
        self.repos: list[str] = []
        # This repository, at the release.
        self.source = self.tmp / "source"
        (self.source / "agent").mkdir(parents=True)
        (self.source / "agent" / "guard.py").write_bytes(GUARD)
        (self.source / "agent" / "settings.json").write_bytes(SETTINGS)
        git(self.tmp, "init", "--quiet", "-b", "main", str(self.source))
        self.commit(self.source)
        git(self.source, "tag", NEW)
        self.sha = git(self.source, "rev-parse", "HEAD").strip()

    def commit(self, repo: Path):
        # --force: a fixture may hold files its own .gitignore ignores, as a
        # repository does once it tracks them.
        git(repo, "add", "-A", "--force")
        git(repo, "commit", "--quiet", "-m", "init")

    def save(self):
        Path(self.env["GH_STATE"]).write_text(json.dumps(self.state))

    def remote(self, name: str, files: dict[str, str | bytes]) -> str:
        """A repository on the pretend GitHub, with these files on main."""
        repo = f"{ORG}/{name}"
        work = self.tmp / "work" / name
        work.mkdir(parents=True)
        git(work, "init", "--quiet", "-b", "main")
        for path, content in files.items():
            (work / path).parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, Link):
                (work / path).symlink_to(content.target)
            elif isinstance(content, bytes):
                (work / path).write_bytes(content)
            else:
                (work / path).write_text(content)
        self.commit(work)
        bare = self.tmp / "remotes" / ORG / f"{name}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        git(self.tmp, "clone", "--quiet", "--bare", str(work), str(bare))
        self.repos.append(repo)
        return repo

    def push_branch(self, name: str, commits: list[dict[str, str]], branch: str = BRANCH):
        """Push branch to the remote, with one commit on main per entry, each by the
        identity it names (GIT_AUTHOR_* and GIT_COMMITTER_*)."""
        work = self.tmp / "branch-work" / name
        git(self.tmp, "clone", "--quiet", str(self.tmp / "remotes" / ORG / f"{name}.git"), str(work))
        git(work, "checkout", "--quiet", "-b", branch)
        for i, who in enumerate(commits):
            (work / f"note-{i}.txt").write_text(f"{i}\n")
            git(work, "add", "-A")
            subprocess.run(["git", "commit", "--quiet", "-m", f"commit {i}"], cwd=work, check=True,
                           env={**ENV, **GIT_ENV, **who})
        git(work, "push", "--quiet", "origin", branch)
        return git(work, "rev-parse", "HEAD").strip()

    def run_fan_out(self, *args: str) -> subprocess.CompletedProcess:
        listing = self.tmp / "repos.json"
        listing.write_text(json.dumps({"repositories": {r: {} for r in [f"{ORG}/.github", *self.repos]}}))
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--tag", NEW, "--source", str(self.source), "--list", str(listing), *args],
            env={**ENV, **self.env}, capture_output=True, text=True,
        )

    def calls(self) -> list[dict]:
        path = Path(self.env["GH_CALLS"])
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def prs(self) -> list[dict]:
        return json.loads(Path(self.env["GH_STATE"]).read_text())["prs"]

    def on_branch(self, name: str, path: str, branch: str = BRANCH) -> str:
        bare = self.tmp / "remotes" / ORG / f"{name}.git"
        return git(bare, "show", f"{branch}:{path}")

    def branches(self, name: str) -> list[str]:
        bare = self.tmp / "remotes" / ORG / f"{name}.git"
        return git(bare, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()

    # The rewrites.

    def test_pins_move_in_the_form_they_had(self):
        text = (
            pinned("checks.yml")
            + f"    uses: {ORG}/.github/.github/workflows/go.yml@{OLD}\n"
            + f"    uses: {ORG}/.github/.github/workflows/image.yml@{OLD_SHA}\n"
            + f"    uses: {ORG}/.github/.github/workflows/image-publish.yml@{OLD_SHA}  #  {OLD}\n"
            + f"    uses: {ORG}/paddock/.github/workflows/x.yml@{OLD_SHA} # {OLD}\n"
        )
        self.assertEqual(
            fan_out.rewrite_pins(text, self.sha, NEW),
            pinned("checks.yml", self.sha, NEW)
            + f"    uses: {ORG}/.github/.github/workflows/go.yml@{NEW}\n"
            + f"    uses: {ORG}/.github/.github/workflows/image.yml@{self.sha}\n"
            + f"    uses: {ORG}/.github/.github/workflows/image-publish.yml@{self.sha}  #  {NEW}\n"
            + f"    uses: {ORG}/paddock/.github/workflows/x.yml@{OLD_SHA} # {OLD}\n",
        )

    def test_a_pin_on_a_later_tag_is_not_a_near_miss(self):
        # v0.2.80 is not v0.2.8 with something after it.
        self.assertEqual(fan_out.referenced_versions(pinned("x.yml", tag="v0.2.80")), [(0, 2, 80)])

    def test_both_pin_forms_end_to_end(self):
        bare_tag = f"    uses: {ORG}/.github/.github/workflows/checks.yml@{OLD}\n"
        self.remote("ecu", {".github/workflows/ci.yml": CI, ".github/workflows/bare.yml": "jobs:\n  x:\n" + bare_tag})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        ci = self.on_branch("ecu", ".github/workflows/ci.yml")
        self.assertIn(pinned("checks.yml", self.sha, NEW), ci)
        self.assertIn(pinned("go.yml", self.sha, NEW), ci)
        self.assertIn("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1", ci)
        self.assertNotIn(OLD_SHA, ci)
        self.assertIn(f"checks.yml@{NEW}\n", self.on_branch("ecu", ".github/workflows/bare.yml"))

    def test_line_endings_are_kept(self):
        self.remote("ecu", {"renovate.json": RENOVATE.replace("\n", "\r\n").encode()})
        self.assertEqual(self.run_fan_out().returncode, 0)
        bare = self.tmp / "remotes" / ORG / "ecu.git"
        raw = subprocess.run(["git", "show", f"{BRANCH}:renovate.json"], cwd=bare,
                             check=True, capture_output=True, env={**ENV, **GIT_ENV}).stdout
        self.assertEqual(raw, RENOVATE.replace(OLD, NEW).replace("\n", "\r\n").encode())

    def test_renovate_preset(self):
        self.remote("paddock", {"renovate.json": RENOVATE})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.on_branch("paddock", "renovate.json"), RENOVATE.replace(OLD, NEW))

    def test_guard_copy(self):
        self.remote("salsa", {".claude/hooks/guard.py": b"# an old guard\n", ".claude/settings.json": b"{}\n",
                              ".claude/guard.json": "{}\n"})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        bare = self.tmp / "remotes" / ORG / "salsa.git"
        raw = subprocess.run(["git", "show", f"{BRANCH}:.claude/hooks/guard.py"], cwd=bare,
                             check=True, capture_output=True, env={**ENV, **GIT_ENV}).stdout
        self.assertEqual(raw, GUARD)
        self.assertEqual(self.on_branch("salsa", ".claude/settings.json").encode(), SETTINGS)
        # The repository's own guard config isn't a copy.
        self.assertEqual(self.on_branch("salsa", ".claude/guard.json"), "{}\n")

    def test_a_guard_the_repository_doesnt_have_isnt_added(self):
        self.remote("radio", {"renovate.json": RENOVATE})
        self.assertEqual(self.run_fan_out().returncode, 0)
        bare = self.tmp / "remotes" / ORG / "radio.git"
        files = git(bare, "ls-tree", "-r", "--name-only", BRANCH).split()
        self.assertEqual(files, ["renovate.json"])

    def test_chassis_template_files(self):
        self.remote("chassis", {
            "copier.yml": "_subdirectory: template\n_templates_suffix: .jinja\n",
            ".github/workflows/ci.yml": CI,
            ".github/workflows/release-pr.yml.jinja": "[# a template #]\n" + pinned("release-please.yml"),
            "renovate.json": RENOVATE,
            "renovate.json.jinja": RENOVATE,
            ".claude/hooks/guard.py": b"# old\n",
            "template/.github/workflows/ci.yml.jinja": CI,
            "template/renovate.json.jinja": RENOVATE,
            "template/.claude/hooks/guard.py": b"# old\n",
            "template/.claude/settings.json": b"{}\n",
        })
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        for path in (".github/workflows/ci.yml", "template/.github/workflows/ci.yml.jinja"):
            with self.subTest(path):
                self.assertEqual(self.on_branch("chassis", path), CI.replace(OLD_SHA, self.sha).replace(OLD, NEW))
        self.assertIn(pinned("release-please.yml", self.sha, NEW),
                      self.on_branch("chassis", ".github/workflows/release-pr.yml.jinja"))
        for path in ("renovate.json", "renovate.json.jinja", "template/renovate.json.jinja"):
            with self.subTest(path):
                self.assertEqual(self.on_branch("chassis", path), RENOVATE.replace(OLD, NEW))
        for path in (".claude/hooks/guard.py", "template/.claude/hooks/guard.py"):
            with self.subTest(path):
                self.assertEqual(self.on_branch("chassis", path).encode(), GUARD)
        self.assertEqual(self.on_branch("chassis", "template/.claude/settings.json").encode(), SETTINGS)
        self.assertEqual(self.on_branch("chassis", "copier.yml"), "_subdirectory: template\n_templates_suffix: .jinja\n")

    # The PR.

    def test_the_pr(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI, "renovate.json": RENOVATE,
                            ".claude/hooks/guard.py": b"# old\n"})
        done = self.run_fan_out("--app-slug", "twr-release")
        self.assertEqual(done.returncode, 0, done.stderr)
        [pr] = self.prs()
        self.assertEqual((pr["repo"], pr["head"], pr["base"], pr["title"]), (f"{ORG}/ecu", BRANCH, "main", TITLE))
        self.assertIn(f"https://github.com/{ORG}/.github/releases/tag/{NEW}", pr["body"])
        for path in (".github/workflows/ci.yml", "renovate.json", ".claude/hooks/guard.py"):
            self.assertIn(f"`{path}`", pr["body"])
        bare = self.tmp / "remotes" / ORG / "ecu.git"
        log = git(bare, "log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%s", BRANCH).splitlines()
        bot = "twr-release[bot] <4242+twr-release[bot]@users.noreply.github.com>"
        self.assertEqual(log, [bot, bot, TITLE])
        # One commit on top of main.
        self.assertEqual(git(bare, "rev-list", "--count", f"main..{BRANCH}").strip(), "1")
        self.assertIn(f"{ORG}/ecu: opened https://github.com/{ORG}/ecu/pull/1", done.stdout)
        summary = Path(self.env["GITHUB_STEP_SUMMARY"]).read_text()
        self.assertIn(f"| {ORG}/ecu | opened | https://github.com/{ORG}/ecu/pull/1 |", summary)

    def test_this_repository_is_skipped(self):
        self.remote("ecu", {"renovate.json": RENOVATE})
        self.run_fan_out()
        repos = {c["argv"][c["argv"].index("--repo") + 1] for c in self.calls() if "--repo" in c["argv"]}
        self.assertEqual(repos, {f"{ORG}/ecu"})

    def test_nothing_merges_or_approves(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        self.run_fan_out()
        for call in self.calls():
            self.assertNotIn(call["argv"][:2], (["pr", "merge"], ["pr", "review"]))
            self.assertNotIn("--auto", call["argv"])
            self.assertFalse(call.get("unhandled"), call)
        text = SCRIPT.read_text()
        for word in ("merge", "--auto", "review", "--approve"):
            self.assertNotIn(f'"{word}"', text)

    # Idempotence.

    def test_a_repository_already_current_gets_no_pr(self):
        self.remote("ecu", {
            ".github/workflows/ci.yml": CI.replace(OLD_SHA, self.sha).replace(OLD, NEW),
            "renovate.json": RENOVATE.replace(OLD, NEW),
            ".claude/hooks/guard.py": GUARD,
        })
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.prs(), [])
        self.assertEqual(self.branches("ecu"), ["main"])
        self.assertIn(f"{ORG}/ecu: current", done.stdout)

    def test_a_repository_on_a_later_release_is_left_alone(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI.replace(OLD, "v0.4.0"), "renovate.json": RENOVATE})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.prs(), [])
        self.assertIn("already on v0.4.0, later than v0.3.0", done.stdout)

    def test_a_second_run_opens_no_second_pr(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        self.assertEqual(self.run_fan_out().returncode, 0)
        head = git(self.tmp / "remotes" / ORG / "ecu.git", "rev-parse", BRANCH)
        again = self.run_fan_out()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(self.prs()), 1)
        self.assertEqual(git(self.tmp / "remotes" / ORG / "ecu.git", "rev-parse", BRANCH), head)
        self.assertIn(f"{ORG}/ecu: PR open https://github.com/{ORG}/ecu/pull/1 (already open, left alone)", again.stdout)
        self.assertEqual([c for c in self.calls() if c["argv"][:2] == ["pr", "create"]].__len__(), 1)

    def test_an_open_pr_that_differs_is_left_alone(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        bare = self.tmp / "remotes" / ORG / "ecu.git"
        git(bare, "branch", BRANCH, "main")
        self.state["prs"].append({"repo": f"{ORG}/ecu", "number": 7, "url": f"https://github.com/{ORG}/ecu/pull/7",
                                  "head": BRANCH, "state": "open"})
        self.save()
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(self.prs()), 1)
        self.assertEqual(git(bare, "rev-parse", BRANCH), git(bare, "rev-parse", "main"))
        self.assertIn("left alone; it differs from what this run would write", done.stdout)

    def test_the_bots_branch_left_without_a_pr_is_replaced(self):
        # An earlier run pushed the branch, then failed before opening the PR.
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        self.push_branch("ecu", [BOT_ENV])
        done = self.run_fan_out("--app-slug", "twr-release")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(self.prs()), 1)
        self.assertIn(self.sha, self.on_branch("ecu", ".github/workflows/ci.yml"))

    def test_a_branch_with_someone_elses_work_is_left_alone(self):
        # The maintainer closed the PR on purpose and kept the branch with a commit
        # of his own on it, or the bot's commit has another on top: a plain push
        # --force would replace either, and this test would fail.
        person = {"GIT_AUTHOR_NAME": "maintainer", "GIT_AUTHOR_EMAIL": "maintainer@example.com",
                  "GIT_COMMITTER_NAME": "maintainer", "GIT_COMMITTER_EMAIL": "maintainer@example.com"}
        cases = {"his own commit": [person], "the bot's commit and his": [BOT_ENV, person],
                 "two of the bot's": [BOT_ENV, BOT_ENV]}
        for n, (case, commits) in enumerate(cases.items()):
            with self.subTest(case):
                name = f"repo{n}"
                self.remote(name, {".github/workflows/ci.yml": CI})
                head = self.push_branch(name, commits)
                done = self.run_fan_out("--app-slug", "twr-release", "--repo", f"{ORG}/{name}")
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(git(self.tmp / "remotes" / ORG / f"{name}.git", "rev-parse", BRANCH).strip(), head)
                self.assertEqual([p for p in self.prs() if p["repo"] == f"{ORG}/{name}"], [])
                self.assertIn(f"{ORG}/{name}: skipped", done.stdout)
                self.assertIn("isn't one commit by the App's bot", done.stdout)

    def test_a_push_to_the_branch_during_the_run_is_not_overwritten(self):
        # The bot's leftover branch passes the check, then someone pushes to it before
        # the run's own push. The push is leased against the commit checked, so it is
        # refused; a plain --force would overwrite theirs, and this test would fail.
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        self.push_branch("ecu", [BOT_ENV])
        bare = self.tmp / "remotes" / ORG / "ecu.git"
        theirs = git(bare, "rev-parse", "main").strip()
        real_git = shutil.which("git")
        wrapper = self.tmp / "bin" / "git"
        wrapper.write_text(
            f"#!{sys.executable}\nimport os, subprocess, sys\n"
            f"if 'push' in sys.argv and os.path.exists({str(self.tmp / 'race')!r}):\n"
            f"    os.remove({str(self.tmp / 'race')!r})\n"
            f"    subprocess.run([{real_git!r}, '-C', {str(bare)!r}, 'update-ref', 'refs/heads/{BRANCH}', {theirs!r}], check=True)\n"
            f"os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])\n")
        wrapper.chmod(0o755)
        (self.tmp / "race").write_text("")
        done = self.run_fan_out("--app-slug", "twr-release")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn(f"{ORG}/ecu: failed", done.stderr)
        self.assertEqual(git(bare, "rev-parse", BRANCH).strip(), theirs)
        self.assertEqual(self.prs(), [])

    def test_without_the_apps_identity_no_branch_is_replaced(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        head = self.push_branch("ecu", [BOT_ENV])
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(git(self.tmp / "remotes" / ORG / "ecu.git", "rev-parse", BRANCH).strip(), head)
        self.assertEqual(self.prs(), [])

    # Older fan-out PRs.

    def older_pr(self, repo: str, number: int, tag: str, author: str = "app/twr-release") -> dict:
        pr = {"repo": repo, "number": number, "url": f"https://github.com/{repo}/pull/{number}",
              "head": f"chore/github-{tag}", "state": "open", "author": author}
        self.state["prs"].append(pr)
        return pr

    def test_an_older_fan_out_pr_is_closed_and_its_branch_kept(self):
        repo = self.remote("ecu", {".github/workflows/ci.yml": CI})
        self.push_branch("ecu", [BOT_ENV], branch="chore/github-v0.2.8")
        self.older_pr(repo, 1, "v0.2.8")
        self.older_pr(repo, 2, "v0.2.7", author="maintainer")  # not the bot's
        self.older_pr(repo, 3, "v0.4.0")  # a later release
        self.state["prs"].append({"repo": repo, "number": 4, "url": "u", "head": "feature", "state": "open",
                                  "author": "app/twr-release"})
        self.save()
        done = self.run_fan_out("--app-slug", "twr-release")
        self.assertEqual(done.returncode, 0, done.stderr)
        prs = {p["number"]: p for p in self.prs()}
        self.assertEqual({n: p["state"] for n, p in prs.items()},
                         {1: "closed", 2: "open", 3: "open", 4: "open", 5: "open"})
        self.assertEqual(prs[5]["head"], BRANCH)
        self.assertIn(prs[5]["url"], prs[1]["comment"])
        self.assertIn("the branch is kept", prs[1]["comment"])
        [close] = [c for c in self.calls() if c["argv"][:2] == ["pr", "close"]]
        self.assertNotIn("--delete-branch", close["argv"])
        self.assertIn("chore/github-v0.2.8", self.branches("ecu"))
        self.assertIn("closed #1 as superseded", done.stdout)

    def test_a_dry_run_names_the_pr_it_would_close(self):
        repo = self.remote("ecu", {".github/workflows/ci.yml": CI})
        self.older_pr(repo, 1, "v0.2.8")
        self.save()
        done = self.run_fan_out("--app-slug", "twr-release", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("would close #1 as superseded", done.stdout)
        self.assertEqual(self.prs()[0]["state"], "open")

    def test_a_repository_with_no_workflow_references(self):
        self.remote("dash", {".github/workflows/ci.yml": "jobs:\n  x:\n    runs-on: ubuntu-latest\n",
                             "renovate.json": RENOVATE})
        self.remote("podium", {"README.md": "nothing to move\n"})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        [pr] = self.prs()
        self.assertEqual(pr["repo"], f"{ORG}/dash")
        self.assertNotIn(".github/workflows", pr["body"])
        self.assertEqual(self.on_branch("dash", ".github/workflows/ci.yml"), "jobs:\n  x:\n    runs-on: ubuntu-latest\n")
        self.assertIn(f"{ORG}/podium: current", done.stdout)

    # Symbolic links: nothing outside the clone is read or written.

    def outside(self) -> Path:
        out = self.tmp / "outside"
        out.mkdir(exist_ok=True)
        (out / "victim.py").write_bytes(b"precious\n")
        (out / "ci.yml").write_text(CI)
        return out

    def assert_untouched(self, out: Path):
        self.assertEqual((out / "victim.py").read_bytes(), b"precious\n")
        self.assertEqual((out / "ci.yml").read_text(), CI)

    def test_symbolic_links_are_refused(self):
        out = self.outside()
        cases = {
            "a guard copy": {".claude/hooks/guard.py": Link(out / "victim.py"), "renovate.json": RENOVATE},
            "a workflow file": {".github/workflows/ci.yml": Link(out / "ci.yml"), "renovate.json": RENOVATE},
            "the workflows directory": {".github/workflows": Link(out), "renovate.json": RENOVATE},
            "the .claude directory": {".claude": Link(out), "renovate.json": RENOVATE},
        }
        for n, (case, files) in enumerate(cases.items()):
            for dry in (False, True):
                with self.subTest(case, dry_run=dry):
                    name = f"link{n}{'d' if dry else ''}"
                    self.remote(name, files)
                    args = ["--repo", f"{ORG}/{name}"] + (["--dry-run"] if dry else [])
                    done = self.run_fan_out(*args)
                    self.assertEqual(done.returncode, 1, done.stdout)
                    self.assertIn(f"{ORG}/{name}: failed", done.stderr)
                    self.assertIn("refusing to follow it", done.stderr)
                    self.assert_untouched(out)
                    self.assertEqual(self.branches(name), ["main"])
                    # Nothing from the outside file reaches the diff either.
                    self.assertNotIn("precious", done.stdout)
        self.assertEqual(self.prs(), [])

    def test_each_guard_against_links_on_its_own(self):
        # Two checks, each enough alone: git's modes before anything is read, and the
        # path on disk before each read and write.
        for files in ({".github/workflows/ci.yml": "120000"}, {".github/workflows": "120000"},
                      {".claude/hooks/guard.py": "160000"}, {"template": "120000", "copier.yml": "100644"}):
            with self.subTest(files):
                clone = self.tmp / "clone-modes"
                clone.mkdir(exist_ok=True)
                (clone / "copier.yml").write_text("_subdirectory: template\n")
                with self.assertRaisesRegex(fan_out.Failure, "refusing to follow it"):
                    fan_out.candidates(clone, files)
        clone = self.tmp / "clone-disk"
        (clone / ".github").mkdir(parents=True)
        out = self.outside()
        (clone / ".github" / "workflows").symlink_to(out)
        (clone / "guard.py").symlink_to(out / "victim.py")
        for rel in (".github/workflows/ci.yml", "guard.py", "../outside/victim.py"):
            with self.subTest(rel):
                with self.assertRaises(fan_out.Failure):
                    fan_out.safe(clone, rel)

    def test_a_link_elsewhere_in_the_repository_is_no_matter(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI, "docs/link": Link("../README.md"), "README.md": "x\n"})
        self.assertEqual(self.run_fan_out().returncode, 0)
        self.assertEqual(len(self.prs()), 1)

    # Unexpected input.

    def test_a_file_that_isnt_utf8_is_skipped(self):
        self.remote("aaa", {".github/workflows/ci.yml": CI, ".github/workflows/latin.yml": b"# caf\xe9\n" + pinned("x.yml").encode()})
        self.remote("bbb", {".github/workflows/ci.yml": CI})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(sorted(p["repo"] for p in self.prs()), [f"{ORG}/aaa", f"{ORG}/bbb"])
        self.assertIn(".github/workflows/latin.yml is not UTF-8, so it was left as it is", done.stdout)
        bare = self.tmp / "remotes" / ORG / "aaa.git"
        self.assertIn(OLD_SHA.encode(), subprocess.run(
            ["git", "show", f"{BRANCH}:.github/workflows/latin.yml"], cwd=bare, check=True, capture_output=True,
            env={**ENV, **GIT_ENV}).stdout)

    def test_an_unexpected_error_fails_that_repository_only(self):
        self.remote("aaa", {".github/workflows/ci.yml": CI})
        self.remote("bbb", {".github/workflows/ci.yml": CI})
        self.state["garbage"] = [f"{ORG}/aaa"]  # gh prints something that isn't JSON
        self.save()
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 1)
        self.assertIn(f"{ORG}/aaa: failed (unexpected JSONDecodeError", done.stderr)
        self.assertEqual([p["repo"] for p in self.prs()], [f"{ORG}/bbb"])
        summary = Path(self.env["GITHUB_STEP_SUMMARY"]).read_text()
        self.assertIn(f"| {ORG}/aaa | failed |", summary)
        self.assertIn(f"| {ORG}/bbb | opened |", summary)

    def test_a_template_subdirectory_behind_a_linked_temporary_directory(self):
        real = self.tmp / "real-tmp"
        real.mkdir()
        (self.tmp / "linked-tmp").symlink_to(real)
        self.env["TMPDIR"] = str(self.tmp / "linked-tmp")
        self.remote("chassis", {"copier.yml": "_subdirectory: template\n",
                                "template/renovate.json.jinja": RENOVATE})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.on_branch("chassis", "template/renovate.json.jinja"), RENOVATE.replace(OLD, NEW))

    def test_a_subdirectory_that_climbs_out_is_ignored(self):
        for value in ("../x", "/etc", "[[ name ]]"):
            with self.subTest(value):
                self.assertIsNone(fan_out.subdirectory(value))
        self.assertEqual(fan_out.subdirectory("./template/"), "template")

    def test_a_longer_owner_name_is_not_this_repository(self):
        text = f"    uses: x{ORG}/.github/.github/workflows/a.yml@{OLD_SHA} # {OLD}\n"
        self.assertEqual(fan_out.rewrite_pins(text, self.sha, NEW), text)
        self.assertEqual(fan_out.referenced_versions(text), [])
        for before in ("my-", "a.", "_"):
            with self.subTest(before):
                other = text.replace(f"x{ORG}", f"{before}{ORG}")
                self.assertEqual(fan_out.rewrite_pins(other, self.sha, NEW), other)
        # Still found after a space, a quote or `uses:`.
        for line in (f"uses: {ORG}/.github/x.yml@{OLD}", f"uses: '{ORG}/.github/x.yml@{OLD}'"):
            with self.subTest(line):
                self.assertIn(NEW, fan_out.rewrite_pins(line, self.sha, NEW))

    def test_a_bare_sha_without_a_tag_is_left_alone(self):
        bare_sha = f"    uses: {ORG}/.github/.github/workflows/image.yml@{OLD_SHA}\n"
        self.remote("ecu", {".github/workflows/ci.yml": CI, ".github/workflows/image.yml": "jobs:\n  x:\n" + bare_sha})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.on_branch("ecu", ".github/workflows/image.yml"), "jobs:\n  x:\n" + bare_sha)
        self.assertIn(self.sha, self.on_branch("ecu", ".github/workflows/ci.yml"))
        summary = Path(self.env["GITHUB_STEP_SUMMARY"]).read_text()
        self.assertIn(".github/workflows/image.yml pins those-who-run/.github at a commit SHA with no tag comment", summary)
        # The release's own SHA without a comment is current, not unknown.
        self.assertFalse(fan_out.unversioned(bare_sha.replace(OLD_SHA, self.sha), self.sha))

    # Dry runs and failures.

    def test_dry_run_prints_the_diff_and_touches_nothing(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI, "renovate.json": RENOVATE})
        done = self.run_fan_out("--dry-run")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"-  \"extends\": [\"github>{ORG}/.github#{OLD}\"]", done.stdout)
        self.assertIn(f"+  \"extends\": [\"github>{ORG}/.github#{NEW}\"]", done.stdout)
        self.assertIn(f"+{pinned('checks.yml', self.sha, NEW)}", done.stdout)
        self.assertIn(f"{ORG}/ecu: dry run (would open a PR changing 2 files)", done.stdout)
        self.assertEqual(self.prs(), [])
        self.assertEqual(self.branches("ecu"), ["main"])
        self.assertEqual({tuple(c["argv"][:2]) for c in self.calls()}, {("pr", "list")})
        self.assertIn("(dry run)", Path(self.env["GITHUB_STEP_SUMMARY"]).read_text())

    def test_one_failure_doesnt_stop_the_others(self):
        self.remote("contracts", {".github/workflows/ci.yml": CI})
        self.repos.append(f"{ORG}/gone")  # no remote: the clone fails
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        self.remote("salsa", {".github/workflows/ci.yml": CI})
        self.state["refuse"] = [f"{ORG}/salsa"]
        self.save()
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(sorted(p["repo"] for p in self.prs()), [f"{ORG}/contracts", f"{ORG}/ecu"])
        self.assertIn(f"{ORG}/gone: failed", done.stderr)
        self.assertIn(f"{ORG}/salsa: failed", done.stderr)
        self.assertIn("2 of 4 repositories failed", done.stderr)
        summary = Path(self.env["GITHUB_STEP_SUMMARY"]).read_text()
        self.assertIn(f"| {ORG}/gone | failed |", summary)
        self.assertIn(f"| {ORG}/ecu | opened |", summary)

    def test_bad_arguments(self):
        self.remote("ecu", {".github/workflows/ci.yml": CI})
        listing = self.tmp / "repos.json"
        for args in (["--tag", "0.3.0"], ["--tag", "v0.3"], ["--tag", NEW, "--repo", f"{ORG}/.github"],
                     ["--tag", "v9.9.9"]):
            with self.subTest(args=args):
                done = subprocess.run(
                    [sys.executable, str(SCRIPT), "--source", str(self.source), "--list", str(listing), *args],
                    env={**ENV, **self.env}, capture_output=True, text=True)
                self.assertEqual(done.returncode, 2, done.stderr)
        self.assertEqual(self.calls(), [])

    def test_names_for_the_token(self):
        listing = self.tmp / "repos.json"
        listing.write_text(json.dumps({"repositories": {f"{ORG}/.github": {}, f"{ORG}/ecu": {}, f"{ORG}/dash": {}}}))
        done = subprocess.run([sys.executable, str(SCRIPT), "--names", "--list", str(listing)],
                              capture_output=True, text=True, check=True)
        self.assertEqual(done.stdout, "ecu,dash\n")

    def test_the_real_list_names_every_other_repository(self):
        names = fan_out.repositories(fan_out.LIST)
        self.assertNotIn("those-who-run/.github", names)
        self.assertIn("those-who-run/chassis", names)

    # The manifest: what a release lists in agent/copies.json is what is copied.

    def release(self, manifest: dict | None = None, files: dict | None = None, raw: bytes | None = None):
        """Give the release a manifest, and the skill and the brief it lists. files
        adds to the release's files or replaces them; raw is the manifest's bytes as
        they are."""
        every = {"agent/agents/twr-reviewer.md": REVIEWER, "agent/skills/twr-dev/SKILL.md": SKILL, **(files or {})}
        listing = json.dumps(MANIFEST if manifest is None else manifest, indent=2).encode() + b"\n"
        every.setdefault("agent/copies.json", listing if raw is None else raw)
        for path, content in every.items():
            target = self.source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.exists():
                target.unlink()
            if isinstance(content, Link):
                target.symlink_to(content.target)
            else:
                target.write_bytes(content)
        self.commit(self.source)
        git(self.source, "tag", "--force", NEW)
        self.sha = git(self.source, "rev-parse", "HEAD").strip()

    def raw(self, name: str, path: str, branch: str = BRANCH) -> bytes:
        bare = self.tmp / "remotes" / ORG / f"{name}.git"
        return subprocess.run(["git", "show", f"{branch}:{path}"], cwd=bare, check=True, capture_output=True,
                              env={**ENV, **GIT_ENV}).stdout

    def tree(self, name: str, branch: str = BRANCH) -> list[str]:
        bare = self.tmp / "remotes" / ORG / f"{name}.git"
        return git(bare, "ls-tree", "-r", "--name-only", branch).splitlines()

    def checked_out(self, name: str, branch: str = BRANCH) -> Path:
        """A checkout of the branch, to ask git about it."""
        work = self.tmp / "checked-out" / name
        git(self.tmp, "clone", "--quiet", "--branch", branch, str(self.tmp / "remotes" / ORG / f"{name}.git"), str(work))
        return work

    def ignores(self, work: Path, path: str) -> bool:
        done = subprocess.run(["git", "check-ignore", "--no-index", "--quiet", path], cwd=work, env={**ENV, **GIT_ENV})
        self.assertIn(done.returncode, (0, 1))
        return done.returncode == 0

    def test_a_release_without_a_manifest_copies_the_guard_alone(self):
        # As before the manifest: the release has skills, but lists nothing, so only
        # the guard moves, where the repository has it. Nothing is added, a file
        # under the copies' names stays, and .gitignore is left alone.
        (self.source / "agent" / "skills" / "twr-dev").mkdir(parents=True)
        (self.source / "agent" / "skills" / "twr-dev" / "SKILL.md").write_bytes(SKILL)
        self.commit(self.source)
        git(self.source, "tag", "--force", NEW)
        self.sha = git(self.source, "rev-parse", "HEAD").strip()
        self.remote("salsa", {**HELD, ".claude/skills/twr-old/SKILL.md": b"old\n"})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.tree("salsa"), [".claude/guard.json", ".claude/hooks/guard.py", ".claude/settings.json",
                                              ".claude/skills/twr-old/SKILL.md", ".gitignore"])
        self.assertEqual(self.raw("salsa", ".claude/hooks/guard.py"), GUARD)
        self.assertEqual(self.raw("salsa", ".claude/settings.json"), SETTINGS)
        self.assertEqual(self.raw("salsa", ".gitignore").decode(), GITIGNORE + AFTER)
        [pr] = self.prs()
        self.assertIn("Changed files:\n\n- `.claude/hooks/guard.py`\n- `.claude/settings.json`\n\n", pr["body"])
        self.assertIn(f"{ORG}/salsa: opened https://github.com/{ORG}/salsa/pull/1 (2 files)", done.stdout)

    def test_a_manifest_copies_the_skills_and_the_agents(self):
        self.release()
        self.remote("salsa", HELD)
        done = self.run_fan_out("--app-slug", "twr-release")
        self.assertEqual(done.returncode, 0, done.stderr)
        for path, content in COPIED.items():
            with self.subTest(path):
                self.assertEqual(self.raw("salsa", path), content)
        # The repository's own guard config isn't a copy, and nothing else arrives:
        # not the manifest, nor a file the manifest doesn't list.
        self.assertEqual(self.raw("salsa", ".claude/guard.json"), b"{}\n")
        self.assertEqual(self.tree("salsa"), sorted([*COPIED, ".claude/guard.json", ".gitignore"]))
        bare = self.tmp / "remotes" / ORG / "salsa.git"
        self.assertEqual(git(bare, "rev-list", "--count", f"main..{BRANCH}").strip(), "1")
        # Each a regular file, as git tracks the guard's.
        modes = {line.split()[0] for line in git(bare, "ls-tree", "-r", BRANCH).splitlines()}
        self.assertEqual(modes, {"100644"})
        [pr] = self.prs()
        for path in COPIED:
            self.assertIn(f"- `{path}`\n", pr["body"])
        self.assertIn("- the agent guard in `.claude/`, copied byte for byte from `agent/` at the release;\n", pr["body"])
        self.assertIn("- the skills and the agents' briefs in `.claude/`", pr["body"])
        self.assertIn("- `.gitignore`: an exception for each directory of copies its rules ignored, and "
                      "nothing else.\n", pr["body"])
        self.assertIn(f"{ORG}/salsa: opened https://github.com/{ORG}/salsa/pull/1 (5 files)", done.stdout)

    def test_a_copy_is_written_where_the_repository_has_none(self):
        # With a manifest every listed copy is written, the guard's included. A
        # guard with no config blocks every command, so the result says so.
        self.release()
        self.remote("radio", {"renovate.json": RENOVATE})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.tree("radio"), sorted([*COPIED, "renovate.json"]))
        self.assertIn(".claude/hooks/guard.py is new and .claude/guard.json is missing: the guard blocks every "
                      "command until the repository has one", done.stdout)
        # Where the config is there, nothing is said.
        self.remote("dash", {".claude/guard.json": "{}\n"})
        done = self.run_fan_out("--repo", f"{ORG}/dash")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("is missing", done.stdout)

    def test_a_stale_copy_is_removed_and_the_repositorys_own_kept(self):
        self.release()
        own = {
            ".claude/skills/pit-stop/SKILL.md": b"the repository's own skill\n",
            ".claude/skills/pit-stop/twr-notes.md": b"its own file\n",
            ".claude/agents/strategist.md": b"the repository's own agent\n",
            ".claude/agents/twr-notes.txt": b"not a brief\n",
            ".claude/commands/twr-old.md": b"elsewhere under .claude/\n",
            "docs/.claude/skills/twr-old/SKILL.md": b"not this repository's .claude/\n",
        }
        stale = {
            ".claude/skills/twr-old/SKILL.md": b"a skill since renamed\n",
            ".claude/skills/twr-old/notes/more.md": b"and a file of it\n",
            ".claude/skills/twr-dev/EXTRA.md": b"a file the skill no longer has\n",
            ".claude/agents/twr-old.md": b"a brief since removed\n",
        }
        self.remote("salsa", {**HELD, **{p: COPIED[p] for p in COPIED}, **own, **stale,
                              ".gitignore": GITIGNORE + EXCEPTIONS + AFTER})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.tree("salsa"), sorted([*COPIED, *own, ".claude/guard.json", ".gitignore"]))
        for path, content in own.items():
            with self.subTest(path):
                self.assertEqual(self.raw("salsa", path), content)
        [pr] = self.prs()
        for path in stale:
            self.assertIn(f"- `{path}` (removed)\n", pr["body"])
        self.assertIn("- stale copies, removed:", pr["body"])
        self.assertNotIn("`.gitignore`", pr["body"])
        self.assertIn(f"{ORG}/salsa: opened https://github.com/{ORG}/salsa/pull/1 (4 files)", done.stdout)
        # A second run finds the PR holding the same removals.
        again = self.run_fan_out()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn(f"{ORG}/salsa: PR open https://github.com/{ORG}/salsa/pull/1 (already open, left alone)", again.stdout)

    def test_the_gitignore_lets_the_copies_be_tracked(self):
        self.release()
        self.remote("salsa", HELD)
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        # Two exceptions and their comment, after the last .claude rule; every other
        # line as it was.
        self.assertEqual(self.raw("salsa", ".gitignore").decode(), GITIGNORE + EXCEPTIONS + AFTER)
        work = self.checked_out("salsa")
        for path in COPIED:
            with self.subTest(path):
                self.assertFalse(self.ignores(work, path))
        # A file dropped beside the copies is tracked by a plain `git add`, which an
        # ignored one is not.
        (work / ".claude/skills/twr-dev/MORE.md").write_text("x\n")
        git(work, "add", "-A")
        self.assertIn("A  .claude/skills/twr-dev/MORE.md", git(work, "status", "--short"))
        # The rest of .claude/ stays out.
        for path in (".claude/worktrees/agent/README.md", ".claude/settings.local.json", ".claude/hooks/other.py",
                     ".claude/todo.md"):
            with self.subTest(path):
                self.assertTrue(self.ignores(work, path))

    def test_a_gitignore_that_lets_the_copies_through_is_left_alone(self):
        self.release()
        cases = {
            "the exceptions are there": GITIGNORE + "!.claude/skills/\n!.claude/agents/\n" + AFTER,
            "nothing ignores .claude": "build/\n",
            "there is none": None,
        }
        for n, (case, rules) in enumerate(cases.items()):
            with self.subTest(case):
                name = f"repo{n}"
                files = {k: v for k, v in HELD.items() if k != ".gitignore"}
                if rules is not None:
                    files[".gitignore"] = rules
                self.remote(name, files)
                done = self.run_fan_out("--repo", f"{ORG}/{name}")
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(self.raw(name, ".claude/skills/twr-dev/SKILL.md"), SKILL)
                self.assertEqual(".gitignore" in self.tree(name), rules is not None)
                if rules is not None:
                    self.assertEqual(self.raw(name, ".gitignore").decode(), rules)
                [pr] = [p for p in self.prs() if p["repo"] == f"{ORG}/{name}"]
                self.assertNotIn("`.gitignore`", pr["body"])

    def test_copies_tracked_under_rules_that_ignore_them_get_their_exceptions(self):
        # Someone added the copies with `git add --force` and left the rules alone.
        # git then refuses to stage a change to a file under the ignored directory,
        # so the rules are put right for every listed copy, changed or not.
        self.release()
        self.remote("salsa", {**HELD, ".claude/skills/twr-dev/SKILL.md": b"an older skill\n",
                              ".claude/agents/twr-reviewer.md": REVIEWER,
                              ".claude/skills/twr-old/SKILL.md": b"stale\n"})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.raw("salsa", ".claude/skills/twr-dev/SKILL.md"), SKILL)
        self.assertEqual(self.raw("salsa", ".gitignore").decode(), GITIGNORE + EXCEPTIONS + AFTER)
        self.assertEqual(self.tree("salsa"), sorted([*COPIED, ".claude/guard.json", ".gitignore"]))
        work = self.checked_out("salsa")
        for path in COPIED:
            with self.subTest(path):
                self.assertFalse(self.ignores(work, path))

    def test_a_stale_copy_is_removed_from_a_directory_the_rules_ignore(self):
        # The release lists no brief, so nothing asks for an exception for
        # .claude/agents/, and the stale brief there is tracked but ignored.
        self.release(manifest={k: v for k, v in MANIFEST.items() if "agents" not in k})
        rules = GITIGNORE + "!.claude/skills/\n" + AFTER
        self.remote("salsa", {**HELD, ".claude/skills/twr-dev/SKILL.md": SKILL, ".claude/hooks/guard.py": GUARD,
                              ".claude/settings.json": SETTINGS, ".claude/agents/twr-old.md": b"stale\n",
                              ".gitignore": rules})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.tree("salsa"), [".claude/guard.json", ".claude/hooks/guard.py", ".claude/settings.json",
                                              ".claude/skills/twr-dev/SKILL.md", ".gitignore"])
        self.assertEqual(self.raw("salsa", ".gitignore").decode(), rules)
        self.assertIn(f"{ORG}/salsa: opened https://github.com/{ORG}/salsa/pull/1 (1 file)", done.stdout)

    def test_only_the_exception_that_is_missing_is_added(self):
        self.release()
        self.remote("salsa", {**HELD, ".gitignore": GITIGNORE + "!.claude/skills/\n" + AFTER})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.raw("salsa", ".gitignore").decode(),
                         GITIGNORE + "!.claude/skills/\n" + EXCEPTIONS.replace("!.claude/skills/\n", "") + AFTER)

    def test_the_exceptions_keep_the_files_shape(self):
        note, lines = EXCEPTIONS.splitlines()[0], ["!.claude/agents/", "!.claude/skills/"]
        self.assertEqual(fan_out.with_exceptions(GITIGNORE + AFTER, lines), GITIGNORE + EXCEPTIONS + AFTER)
        # CRLF stays CRLF.
        crlf = (GITIGNORE + AFTER).replace("\n", "\r\n")
        self.assertEqual(fan_out.with_exceptions(crlf, lines), (GITIGNORE + EXCEPTIONS + AFTER).replace("\n", "\r\n"))
        # A last line with no newline gets one, and the new lines theirs.
        self.assertEqual(fan_out.with_exceptions(".claude/*", lines), ".claude/*\n" + EXCEPTIONS)
        # No rule about .claude: at the end. An empty file: the lines alone.
        self.assertEqual(fan_out.with_exceptions("*.log\n", lines), "*.log\n" + EXCEPTIONS)
        self.assertEqual(fan_out.with_exceptions("", lines), EXCEPTIONS)
        # Every way of writing a rule about .claude counts as one, and a name that
        # only starts the same does not.
        for rule in (".claude/", "/.claude/*", "!/.claude/hooks/", ".claude", "  !.claude/x"):
            with self.subTest(rule):
                self.assertEqual(fan_out.with_exceptions(f"{rule}\nbuild/\n", lines), f"{rule}\n{EXCEPTIONS}build/\n")
        self.assertEqual(fan_out.with_exceptions(".claudette/\nbuild/\n", lines), ".claudette/\nbuild/\n" + EXCEPTIONS)
        # The comment is written once, whenever a later release adds a line.
        self.assertEqual(fan_out.with_exceptions(GITIGNORE + EXCEPTIONS, ["!.claude/commands/"]),
                         GITIGNORE + EXCEPTIONS + "!.claude/commands/\n")
        self.assertEqual(note.count("\n"), 0)
        # The exception is for the directory under .claude/, or the file itself there.
        self.assertEqual(fan_out.exception(".claude/skills/twr-dev/SKILL.md"), "!.claude/skills/")
        self.assertEqual(fan_out.exception(".claude/agents/twr-reviewer.md"), "!.claude/agents/")
        self.assertEqual(fan_out.exception(".claude/settings.json"), "!.claude/settings.json")

    def test_a_copy_no_exception_can_free_fails_the_repository(self):
        # An exception for the copy's directory is all the fan-out adds. Where that
        # doesn't let the copy through, it says which rule is in the way and leaves
        # the repository to the maintainer, rather than push a PR without the copy.
        self.release()
        cases = {
            "a rule on the file's name": ({".gitignore": "*.md\n"}, ".gitignore:1: *.md"),
            "a rule in another file": ({".gitignore": GITIGNORE, ".claude/.gitignore": "agents/\nskills/\n"},
                                       ".claude/.gitignore:1: agents/"),
            "no .gitignore in the root": ({".claude/.gitignore": "agents/\nskills/\n"},
                                          ".claude/.gitignore:1: agents/"),
        }
        for n, (case, (files, rule)) in enumerate(cases.items()):
            for dry in (False, True):
                with self.subTest(case, dry_run=dry):
                    name, fine = f"stuck{n}{'d' if dry else ''}", f"fine{n}{'d' if dry else ''}"
                    self.remote(name, {**{k: v for k, v in HELD.items() if k != ".gitignore"}, **files})
                    self.remote(fine, HELD)
                    done = self.run_fan_out("--repo", f"{ORG}/{name}", "--repo", f"{ORG}/{fine}",
                                            *(["--dry-run"] if dry else []))
                    self.assertEqual(done.returncode, 1, done.stdout)
                    self.assertIn(f"{ORG}/{name}: failed (.claude/agents/twr-reviewer.md is a copy the repository's "
                                  f"ignore rules leave out ({rule})", done.stderr)
                    self.assertEqual(self.branches(name), ["main"])
                    # The other repository is moved all the same.
                    self.assertIn(f"{ORG}/{fine}: {'dry run' if dry else 'opened'}", done.stdout)
        self.assertEqual(sorted(p["repo"] for p in self.prs()), [f"{ORG}/fine{n}" for n in range(3)])

    def test_a_template_gets_the_copies_as_plain_files(self):
        # chassis: the repository's own .claude/, and the template's under
        # `_subdirectory`. A copy is a plain file in both, never one with the
        # template suffix, so copier copies its bytes and renders nothing in them.
        self.release()
        self.remote("chassis", {
            **HELD,
            "copier.yml": "_subdirectory: template\n_templates_suffix: .jinja\n",
            "template/.claude/hooks/guard.py": b"# old\n",
            "template/.claude/settings.json": b"{}\n",
            "template/.claude/guard.json.jinja": '{"owner": "[[ owner ]]"}\n',
            "template/.gitignore": GITIGNORE + AFTER,
            "template/.gitignore.jinja": "[% include '.gitignore' %]\n",
            "template/.claude/skills/twr-old/SKILL.md": b"a skill since renamed\n",
            "template/.claude/skills/twr-dev/SKILL.md.jinja": b"[[ name ]]'s loop\n",
            "template/.claude/agents/twr-reviewer.md.jinja": b"[[ name ]]'s reviewer\n",
            "template/.claude/skills/[[name]]/SKILL.md.jinja": b"the new repository's own skill\n",
            "template/renovate.json.jinja": RENOVATE,
        })
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        for root in ("", "template/"):
            for path, content in COPIED.items():
                with self.subTest(root + path):
                    self.assertEqual(self.raw("chassis", root + path), content)
            self.assertEqual(self.raw("chassis", f"{root}.gitignore").decode(), GITIGNORE + EXCEPTIONS + AFTER)
        tree = self.tree("chassis")
        # No copy with the template suffix is written, and the one that was under the
        # copies' names is stale; the template's own files stay.
        self.assertEqual([p for p in tree if p.endswith(".jinja")], [
            "template/.claude/agents/twr-reviewer.md.jinja", "template/.claude/guard.json.jinja",
            "template/.claude/skills/[[name]]/SKILL.md.jinja", "template/.gitignore.jinja",
            "template/renovate.json.jinja"])
        self.assertNotIn("template/.claude/skills/twr-old/SKILL.md", tree)
        self.assertEqual(self.raw("chassis", "template/.gitignore.jinja"), b"[% include '.gitignore' %]\n")
        self.assertEqual(self.on_branch("chassis", "template/renovate.json.jinja"), RENOVATE.replace(OLD, NEW))
        # What the fan-out can't settle is left for the maintainer, by name.
        self.assertIn("template/.claude/agents/twr-reviewer.md.jinja is beside the copy "
                      "template/.claude/agents/twr-reviewer.md: copier renders it over the copy", done.stdout)
        self.assertIn("template/.gitignore.jinja is the template of a new repository's .gitignore and was left as "
                      "it is: check that it lets .claude/agents/, .claude/skills/ be tracked", done.stdout)
        self.assertNotIn("SKILL.md.jinja is beside", done.stdout)
        # The template has its config as a template, so its new copies need no note.
        self.assertNotIn("is missing", done.stdout)

    def test_a_repository_with_the_copies_gets_no_pr(self):
        self.release()
        self.remote("ecu", {**HELD, **COPIED, ".claude/skills/pit-stop/SKILL.md": b"its own\n",
                            ".gitignore": GITIGNORE + EXCEPTIONS + AFTER})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.prs(), [])
        self.assertEqual(self.branches("ecu"), ["main"])
        self.assertIn(f"{ORG}/ecu: current", done.stdout)

    def test_a_second_run_after_the_merge_changes_nothing(self):
        self.release()
        self.remote("ecu", {**HELD, ".claude/skills/twr-old/SKILL.md": b"stale\n", "renovate.json": RENOVATE,
                            ".github/workflows/ci.yml": CI})
        self.assertEqual(self.run_fan_out().returncode, 0)
        # The maintainer merges the PR.
        bare = self.tmp / "remotes" / ORG / "ecu.git"
        git(bare, "update-ref", "refs/heads/main", f"refs/heads/{BRANCH}")
        git(bare, "update-ref", "-d", f"refs/heads/{BRANCH}")
        self.state = json.loads(Path(self.env["GH_STATE"]).read_text())
        self.state["prs"][0]["state"] = "closed"
        self.save()
        again = self.run_fan_out()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn(f"{ORG}/ecu: current (main is already on {NEW})", again.stdout)
        self.assertEqual(self.branches("ecu"), ["main"])
        self.assertEqual(len(self.prs()), 1)
        dry = self.run_fan_out("--dry-run")
        self.assertNotIn("diff --git", dry.stdout)

    def test_a_dry_run_shows_the_new_and_the_stale_copies_and_writes_nothing(self):
        self.release()
        self.remote("ecu", {**HELD, ".claude/skills/twr-old/SKILL.md": b"stale\n"})
        done = self.run_fan_out("--dry-run")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("new file mode 100644\n", done.stdout)
        self.assertIn("+++ b/.claude/skills/twr-dev/SKILL.md\n", done.stdout)
        self.assertIn("+A workflow's ${{ github.sha }}", done.stdout)
        self.assertIn("deleted file mode 100644\n", done.stdout)
        self.assertIn("--- a/.claude/skills/twr-old/SKILL.md\n", done.stdout)
        self.assertIn("+!.claude/skills/\n", done.stdout)
        self.assertIn(f"{ORG}/ecu: dry run (would open a PR changing 6 files)", done.stdout)
        self.assertEqual(self.prs(), [])
        self.assertEqual(self.branches("ecu"), ["main"])
        self.assertEqual({tuple(c["argv"][:2]) for c in self.calls()}, {("pr", "list")})

    def test_a_manifest_with_a_fault_stops_the_run_before_any_repository(self):
        self.remote("ecu", HELD)
        outside = self.outside()
        listed = json.dumps(MANIFEST)
        cases = {
            "not JSON": ({"raw": b"{not json"}, "is not JSON"),
            "not UTF-8": ({"raw": b'{"agent/guard.py": "\xff"}'}, "is not JSON"),
            "a list": ({"raw": b'[["agent/guard.py", ".claude/hooks/guard.py"]]'}, "is not a JSON object"),
            "a string": ({"raw": b'"agent/guard.py"'}, "is not a JSON object"),
            "empty": ({"raw": b"{}"}, "doesn't copy agent/guard.py to .claude/hooks/guard.py"),
            "without the guard": ({"manifest": {k: v for k, v in MANIFEST.items() if k != "agent/guard.py"}},
                                  "doesn't copy agent/guard.py to .claude/hooks/guard.py, where every repository has it"),
            "without the settings": ({"manifest": {k: v for k, v in MANIFEST.items() if k != "agent/settings.json"}},
                                     "doesn't copy agent/settings.json to .claude/settings.json"),
            "the guard somewhere else": ({"manifest": {**MANIFEST, "agent/guard.py": ".claude/hooks/other.py"}},
                                         "doesn't copy agent/guard.py to .claude/hooks/guard.py"),
            "a destination that is a list": ({"raw": b'{"agent/guard.py": [".claude/hooks/guard.py"]}'},
                                             "a destination that is not a string"),
            "a destination that is an object": ({"raw": b'{"agent/guard.py": {"to": ".claude/hooks/guard.py"}}'},
                                                "a destination that is not a string"),
            "a source twice": ({"raw": listed.replace("}", ', "agent/guard.py": ".claude/hooks/other.py"}').encode()},
                               "lists agent/guard.py twice"),
            "a source the release doesn't have": ({"manifest": {**MANIFEST, "agent/skills/twr-gone/SKILL.md":
                                                                ".claude/skills/twr-gone/SKILL.md"}},
                                                  "agent/skills/twr-gone/SKILL.md, which is not a regular file in "
                                                  "the release (it is missing)"),
            "a source that is a directory": ({"manifest": {"agent/skills": ".claude/skills"}},
                                             "agent/skills, which is not a regular file in the release"),
            "a source that is a link": ({"manifest": {**MANIFEST, "agent/link.md": ".claude/link.md"},
                                         "files": {"agent/link.md": Link(outside / "victim.py")}},
                                        "agent/link.md, which is not a regular file in the release (mode 120000)"),
            "a source outside agent/": ({"manifest": {"scripts/fan_out.py": ".claude/fan_out.py"}},
                                        "'scripts/fan_out.py', which is not a plain path under agent/"),
            "a source that climbs out": ({"manifest": {"agent/../LICENSE": ".claude/LICENSE"}},
                                         "'agent/../LICENSE', which is not a plain path under agent/"),
            "a destination outside .claude/": ({"manifest": {"agent/guard.py": ".github/workflows/guard.py"}},
                                               "'.github/workflows/guard.py', which is not a plain path under .claude/"),
            "a destination that climbs out": ({"manifest": {"agent/guard.py": ".claude/../.github/workflows/ci.yml"}},
                                              "which is not a plain path under .claude/"),
            "a destination from the root": ({"manifest": {"agent/guard.py": "/.claude/hooks/guard.py"}},
                                            "which is not a plain path under .claude/"),
            "a destination that is .claude itself": ({"manifest": {"agent/guard.py": ".claude"}},
                                                     "which is not a plain path under .claude/"),
            "a destination under a name like .claude": ({"manifest": {"agent/guard.py": ".claudette/guard.py"}},
                                                        "which is not a plain path under .claude/"),
            "a destination with a backslash": ({"manifest": {"agent/guard.py": ".claude\\hooks\\guard.py"}},
                                               "which is not a plain path under .claude/"),
            "a destination with a space": ({"manifest": {"agent/guard.py": ".claude/hooks/the guard.py"}},
                                           "which is not a plain path under .claude/"),
            "a destination with an empty part": ({"manifest": {"agent/guard.py": ".claude//guard.py"}},
                                                 "which is not a plain path under .claude/"),
            "the repository's own config": ({"manifest": {"agent/guard.py": ".claude/guard.json"}},
                                            "to .claude/guard.json, which is each repository's own"),
            "a destination copier would render": ({"manifest": {"agent/guard.py": ".claude/hooks/guard.py.jinja"}},
                                                  "which copier would render in a template"),
            "two files to one destination": ({"manifest": {"agent/guard.py": ".claude/x", "agent/settings.json": ".claude/x"}},
                                             "copies two files to .claude/x"),
            "a destination inside another": ({"manifest": {"agent/guard.py": ".claude/x", "agent/settings.json": ".claude/x/y"}},
                                             "copies to .claude/x and to .claude/x/y, inside it"),
            "a manifest that is a link": ({"files": {"agent/copies.json": Link("guard.py")}},
                                          "agent/copies.json at the release is not a regular file (mode 120000)"),
        }
        for case, (release, said) in cases.items():
            self.release(**release)
            for dry in (False, True):
                with self.subTest(case, dry_run=dry):
                    done = self.run_fan_out(*(["--dry-run"] if dry else []))
                    self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                    self.assertIn(said, done.stderr)
                    self.assertIn("no repository was touched", done.stderr)
                    self.assertEqual(done.stdout, "")
        # Not one call to gh, not one clone, and nothing from outside is printed.
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.branches("ecu"), ["main"])
        self.assert_untouched(outside)

    def test_links_where_a_copy_goes_are_refused(self):
        self.release()
        out = self.outside()
        (out / "skills").mkdir()
        cases = {
            "the skills directory": {".claude/skills": Link(out / "skills")},
            "a skill's directory": {".claude/skills/twr-dev": Link(out)},
            "the agents directory": {".claude/agents": Link(out)},
            "a new copy's place": {".claude/agents/twr-reviewer.md": Link(out / "victim.py")},
            "a stale copy": {".claude/skills/twr-old/SKILL.md": Link(out / "victim.py")},
            "a stale skill's directory": {".claude/skills/twr-old": Link(out)},
            "a stale brief": {".claude/agents/twr-old.md": Link(out / "victim.py")},
        }
        for n, (case, files) in enumerate(cases.items()):
            for dry in (False, True):
                with self.subTest(case, dry_run=dry):
                    name = f"link{n}{'d' if dry else ''}"
                    self.remote(name, {**{k: v for k, v in HELD.items() if k != ".gitignore"}, **files})
                    done = self.run_fan_out("--repo", f"{ORG}/{name}", *(["--dry-run"] if dry else []))
                    self.assertEqual(done.returncode, 1, done.stdout)
                    self.assertIn(f"{ORG}/{name}: failed", done.stderr)
                    self.assertIn("refusing to follow it", done.stderr)
                    self.assert_untouched(out)
                    self.assertEqual(sorted(p.name for p in out.iterdir()), ["ci.yml", "skills", "victim.py"])
                    self.assertEqual(list((out / "skills").iterdir()), [])
                    self.assertEqual(self.branches(name), ["main"])
                    self.assertNotIn("precious", done.stdout)
        self.assertEqual(self.prs(), [])

    def test_a_gitignore_that_is_a_link_is_not_written_through(self):
        # Reached only when a copy is ignored by a rule elsewhere, since git reads
        # no rules through a link.
        self.release()
        out = self.outside()
        self.remote("ecu", {".claude/guard.json": "{}\n", ".gitignore": Link(out / "ci.yml"),
                            ".claude/.gitignore": "skills/\n"})
        done = self.run_fan_out()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn(".gitignore is not a regular file (mode 120000); refusing to follow it", done.stderr)
        self.assert_untouched(out)
        self.assertEqual(self.branches("ecu"), ["main"])

    def test_a_file_or_a_directory_in_a_copys_way_fails_the_repository(self):
        self.release()
        cases = {
            "a file where a directory goes": ({".claude/skills": "not a directory\n"},
                                              ".claude/skills is a file where the directory of a copy should be"),
            "a directory where a copy goes": ({".claude/agents/twr-reviewer.md/notes.md": "x\n"},
                                              ".claude/agents/twr-reviewer.md is a directory where a copy should be"),
            "a copy git tracks as a program": ({}, ".claude/hooks/guard.py is not a regular file (mode 100755)"),
        }
        for n, (case, (files, said)) in enumerate(cases.items()):
            with self.subTest(case):
                name = f"way{n}"
                self.remote(name, {".claude/guard.json": "{}\n", ".claude/hooks/guard.py": b"# old\n", **files})
                if not files:
                    work = self.tmp / "work" / name
                    git(work, "update-index", "--chmod=+x", ".claude/hooks/guard.py")
                    git(work, "commit", "--quiet", "-m", "a program")
                    git(work, "push", "--quiet", str(self.tmp / "remotes" / ORG / f"{name}.git"), "main")
                self.remote(f"fine{n}", HELD)
                done = self.run_fan_out("--repo", f"{ORG}/{name}", "--repo", f"{ORG}/fine{n}")
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn(f"{ORG}/{name}: failed ({said}", done.stderr)
                self.assertEqual(self.branches(name), ["main"])
                self.assertIn(f"{ORG}/fine{n}: opened", done.stdout)

    def test_a_new_copy_is_checked_on_disk_too(self):
        # As for the files that are there: git's modes first, then the path on disk
        # before each write, each enough alone.
        clone = self.tmp / "clone-new"
        (clone / ".claude").mkdir(parents=True)
        out = self.outside()
        (clone / ".claude" / "skills").symlink_to(out)
        (clone / ".claude" / "agents").write_text("a file\n")
        self.assertEqual(fan_out.safe(clone, ".claude/hooks/guard.py", new=True), clone / ".claude/hooks/guard.py")
        for rel in (".claude/skills/twr-dev/SKILL.md", "../outside/new.md", ".claude/../../outside/new.md"):
            with self.subTest(rel):
                with self.assertRaises(fan_out.Failure):
                    fan_out.safe(clone, rel, new=True)
        # A file that isn't there is refused unless it is a new copy.
        with self.assertRaises(fan_out.Failure):
            fan_out.safe(clone, ".claude/hooks/guard.py")
        for files in ({".claude/skills": "120000"}, {".claude/skills/twr-dev": "160000"}, {".claude": "120000"},
                      {".claude/skills/twr-old/SKILL.md": "120000"}, {".claude/agents/twr-old.md": "160000"},
                      {".claude/skills/twr-old": "160000"}):
            with self.subTest(files):
                with self.assertRaisesRegex(fan_out.Failure, "refusing to follow it"):
                    fan_out.candidates(clone, files, tuple(COPIED), True)
        # Without a manifest those names are nobody's, as before it.
        self.assertEqual(fan_out.candidates(clone, {".claude/skills/twr-old/SKILL.md": "120000"}), ([], {}, []))

    def test_this_repositorys_manifest_is_one_the_fan_out_takes(self):
        # Whichever merges first, the manifest or this reader of it, the two are
        # checked against each other from then on.
        path = ROOT / "agent" / "copies.json"
        if not path.is_file():
            self.skipTest("agent/copies.json is not in this checkout yet")
        modes = {p.relative_to(ROOT).as_posix(): "100644" for p in (ROOT / "agent").rglob("*")
                 if p.is_file() and not p.is_symlink()}
        copies = fan_out.manifest(path.read_bytes(), modes)
        self.assertEqual({d: s for d, s in copies.items() if d in fan_out.COPIES}, fan_out.COPIES)


class TheFanOutJob(unittest.TestCase):
    """release-pr.yml's fan-out job: after a release only, on main, in the
    release-please environment, as the release App with the three permissions the
    fan-out writes with, and nothing else."""

    @classmethod
    def setUpClass(cls):
        cls.text = (WORKFLOWS / "release-pr.yml").read_text()
        cls.job = jobs(cls.text)["fan-out"]
        cls.block = cls.text[cls.text.index("\n  fan-out:\n"):]

    def test_it_runs_after_a_release_on_main(self):
        keys = self.job["keys"]
        self.assertEqual(keys["needs"], "release")
        self.assertEqual(keys["if"], "needs.release.outputs.release_created == 'true' && github.ref == 'refs/heads/main'")
        self.assertEqual(keys["environment"], "release-please")
        self.assertEqual(self.job["permissions"], {"contents": "read"})

    def test_the_apps_token(self):
        self.assertRegex(self.block, r"uses: actions/create-github-app-token@[0-9a-f]{40} # v\d+\.\d+\.\d+\n")
        self.assertIn("client-id: ${{ vars.RELEASE_APP_CLIENT_ID }}", self.block)
        self.assertIn("private-key: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}", self.block)
        # Only the repositories it opens PRs in.
        self.assertIn("repositories: ${{ steps.repos.outputs.names }}", self.block)
        self.assertIn("run: echo \"names=$(python3 scripts/fan_out.py --names)\" >> \"$GITHUB_OUTPUT\"", self.block)
        asked = set(re.findall(r"(?m)^          permission-([a-z-]+): (\w+)", self.block))
        self.assertEqual(asked, {("contents", "write"), ("pull-requests", "write"), ("workflows", "write")})
        self.assertNotIn("skip-token-revoke", self.block)
        # Handed to the script in its step's environment only, once.
        self.assertEqual(self.block.count("steps.app.outputs.token"), 1)
        self.assertIn("          GH_TOKEN: ${{ steps.app.outputs.token }}\n", self.block)

    def test_a_token_that_cant_be_minted_says_where_to_look(self):
        self.assertIn("      - name: Why there is no token\n        if: failure() && steps.app.outcome == 'failure'\n",
                      self.block)
        hint = self.block[self.block.index("Why there is no token"):self.block.index("A PR in every repository")]
        for words in ("installation", "Workflows", "repository access", "GITHUB_STEP_SUMMARY"):
            self.assertIn(words, hint)
        # It prints no secret, variable or step output.
        self.assertNotIn("${{", hint)

    def test_it_checks_out_the_release_and_runs_the_script(self):
        self.assertIn("ref: ${{ needs.release.outputs.tag_name }}", self.block)
        self.assertIn("persist-credentials: false", self.block)
        self.assertIn('run: python3 scripts/fan_out.py --tag "$TAG" --app-slug "$APP_SLUG"', self.block)
        self.assertNotIn("--dry-run", self.block)
        # The tag reaches the script through the environment, never inline in run:.
        self.assertNotRegex(self.block, r"run:.*\$\{\{")


if __name__ == "__main__":
    unittest.main()
