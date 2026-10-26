"""Open a PR in every repository that moves it onto a release of this repository.

    python3 scripts/fan_out.py --tag vX.Y.Z [--dry-run] [--repo owner/name]...
    python3 scripts/fan_out.py --names

release-pr.yml runs it after release-please has released those-who-run/.github, with
the release App's token in GH_TOKEN. For each repository in scripts/repos.json other
than this one, it clones the default branch and moves it onto the release:

- workflow pins: every `those-who-run/.github/...@<ref>` in `.github/workflows/`
  moves to the release. A commit SHA with a `# vX.Y.Z` comment becomes the
  release's commit and tag; a bare tag becomes the new tag. Each pin keeps the form
  it had. A SHA with no tag comment can't be compared with the release, so the file
  holding it is left as it is, and the summary says so;
- the Renovate preset: `github>those-who-run/.github#vX.Y.Z` in `renovate.json`;
- the guard copy: `.claude/hooks/guard.py` and `.claude/settings.json` become
  `agent/guard.py` and `agent/settings.json` at the release, byte for byte, where
  the repository has them. Each repository's guard-copy check compares both
  against the commit its CI pins, so they move together.

A copier template (chassis) gets the same in its template files: the `.jinja`
siblings of those files, and, when its copier.yml sets `_subdirectory`, the same
paths under that directory.

Then it commits to the branch `chore/github-vX.Y.Z` and opens the PR
`ci: those-who-run/.github vX.Y.Z`, whose body links the release notes. Renovate
leaves these updates to it (default.json). It never merges, approves or arms
auto-merge: the maintainer merges each PR.

It is safe to run again. A repository with nothing to move gets no PR, nor does one
already ahead of the release. Where a PR from the branch is already open it is left
as it is, and the run says whether it matches what this run would have written. A
branch with no open PR is replaced only when it is one commit by the App's bot on
the default branch (an earlier run that failed before opening the PR); any other is
left alone and reported. Open fan-out PRs by the bot for earlier releases are closed
with a comment naming the new PR, and their branches kept.

Only regular files git tracks (mode 100644) are read or written. A symbolic link or
a submodule where one of the files above, or a directory above them, should be fails
the repository before anything is read or written, and every path is checked to
stay inside the clone. A file that isn't UTF-8 is left as it is, with a note.

One repository failing, whatever the error, doesn't stop the others. Each
repository's result is printed,
and written to the job summary ($GITHUB_STEP_SUMMARY) when there is one; the run
exits 1 at the end if any failed, 2 on bad arguments.

--dry-run clones each repository and prints the diff it would commit, and pushes,
commits and opens nothing. --names prints the repositories' names, comma-separated,
for the App token's `repositories`. --app-slug names the App whose bot authors the
commits (its noreply address is looked up with gh) and whose branches and PRs may be
replaced or closed; without it, git's own identity is used and nothing is replaced
or closed. The files of the release are read from the git repository --source (this
checkout by default) at the tag's commit, so no network is needed for them.

Needs git and gh. Standard library only, Python 3.9 or later. The tests run it
against a stub gh and local git remotes (tests/test_fan_out.py): FAN_OUT_GIT_BASE
points the clones somewhere other than https://github.com.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIST = ROOT / "scripts" / "repos.json"
CANONICAL = "those-who-run/.github"
GIT_BASE = os.environ.get("FAN_OUT_GIT_BASE", "https://github.com")

TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
SHA = re.compile(r"^[0-9a-f]{40}$")
# A reference to this repository in a workflow: a reusable workflow or an action in
# it, at a commit SHA (with the tag in a comment, or without) or at a tag.
PIN = re.compile(
    r"(?<![\w.-])(?P<path>those-who-run/\.github/[^@\s'\"]+)@"
    r"(?P<ref>[0-9a-f]{40}|v\d+\.\d+\.\d+)(?![0-9A-Za-z.])"
    r"(?P<comment>[ \t]+#[ \t]*)?(?P<comment_tag>v\d+\.\d+\.\d+)?(?![0-9A-Za-z.])"
)
PRESET = re.compile(r"github>those-who-run/\.github#(?P<tag>v\d+\.\d+\.\d+)(?![0-9A-Za-z.])")
WORKFLOW_FILE = re.compile(r"\.ya?ml(\.jinja)?$")
RENOVATE_FILES = ("renovate.json", "renovate.json.jinja")
# The guard's copies, and the file of this repository each is a copy of.
COPIES = {
    ".claude/hooks/guard.py": "agent/guard.py",
    ".claude/settings.json": "agent/settings.json",
}
REGULAR = "100644"
SUBDIRECTORY = re.compile(r"""^_subdirectory:\s*["']?([^"'\s#]+)""", re.M)

# A git command that reaches GitHub authenticates through gh, with GH_TOKEN, so the
# token is never in a URL, an argument or the clone's config.
GIT_AUTH = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]


class Failure(Exception):
    """A repository couldn't be moved; the message says why."""


def version(tag: str) -> tuple[int, int, int]:
    m = TAG.match(tag)
    if not m:
        raise ValueError(f"not a release tag: {tag}")
    return int(m[1]), int(m[2]), int(m[3])


def rewrite_pins(text: str, sha: str, tag: str) -> str:
    """Every pin of this repository moved to the release, in the form it had."""

    def move(m: re.Match) -> str:
        ref = sha if SHA.match(m["ref"]) else tag
        out = f"{m['path']}@{ref}"
        if m["comment"] is not None and m["comment_tag"] is not None:
            out += f"{m['comment']}{tag}"
        elif m["comment"] is not None:
            out += m["comment"]
        return out

    return PIN.sub(move, text)


def rewrite_preset(text: str, tag: str) -> str:
    return PRESET.sub(f"github>{CANONICAL}#{tag}", text)


def referenced_versions(text: str) -> list[tuple[int, int, int]]:
    """The releases of this repository a file names, by tag."""
    tags = [m["comment_tag"] or m["ref"] for m in PIN.finditer(text)]
    tags += [m["tag"] for m in PRESET.finditer(text)]
    return [version(t) for t in tags if TAG.match(t)]


def tracked(clone: Path) -> dict[str, str]:
    """Every path git tracks in the clone, with its mode (100644 a regular file,
    120000 a symbolic link, 160000 a submodule)."""
    entries = {}
    for record in git(clone, "ls-files", "-s", "-z").split("\0"):
        if record:
            meta, path = record.split("\t", 1)
            entries[path] = meta.split()[0]
    return entries


def subdirectory(value: str) -> str | None:
    """copier's `_subdirectory` as a plain relative path, or None for one that is
    templated, absolute, or climbs out of the repository."""
    if re.search(r"[\[\]{}%\\]", value) or value.startswith("/"):
        return None
    parts = [p for p in value.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def roots(clone: Path, files: dict[str, str]) -> list[str]:
    """The prefixes to look under: the repository's root, and a copier template's
    subdirectory when it sets one."""
    found = [""]
    for name in ("copier.yml", "copier.yaml"):
        if files.get(name) != REGULAR:
            continue
        text = read(safe(clone, name))
        m = SUBDIRECTORY.search(text) if text is not None else None
        sub = subdirectory(m[1]) if m else None
        if sub and sub + "/" not in found:
            found.append(sub + "/")
    return found


def candidates(clone: Path, files: dict[str, str]) -> tuple[list[str], list[str]]:
    """The files that may hold a pin or the preset, and the guard copies, each as a
    path in the clone. Only regular files git tracks: a symbolic link, or a
    submodule, where one of these files or the directories above it should be,
    fails the repository rather than letting a write follow it out of the clone."""
    texts, copies = [], []
    for prefix in roots(clone, files):
        watched = {prefix.rstrip("/"), f"{prefix}.github", f"{prefix}.github/workflows",
                   f"{prefix}.claude", f"{prefix}.claude/hooks"} - {""}
        for path, mode in files.items():
            if path in watched and mode != REGULAR:
                raise Failure(f"{path} is a symbolic link or a submodule (mode {mode}); refusing to follow it")
        for path, mode in sorted(files.items()):
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix):]
            is_text = (re.fullmatch(r"\.github/workflows/[^/]+", rest) and WORKFLOW_FILE.search(rest)) \
                or rest in RENOVATE_FILES
            is_copy = rest in COPIES
            if not (is_text or is_copy):
                continue
            if mode != REGULAR:
                raise Failure(f"{path} is not a regular file (mode {mode}); refusing to follow it")
            (texts if is_text else copies).append(path)
    return texts, copies


def safe(clone: Path, rel: str) -> Path:
    """The file at rel in the clone, after checking that no part of the path is a
    symbolic link and that it resolves inside the clone."""
    path = clone
    for part in rel.split("/"):
        if part in ("", ".", ".."):
            raise Failure(f"{rel}: not a plain path in the repository")
        path = path / part
        if path.is_symlink():
            raise Failure(f"{rel}: {path.relative_to(clone)} is a symbolic link; refusing to follow it")
    if clone.resolve() not in path.resolve().parents or not path.is_file():
        raise Failure(f"{rel}: not a regular file inside the clone")
    return path


def read(path: Path) -> str | None:
    """A file's text, or None when it isn't UTF-8. Bytes decoded, not read_text:
    that turns CRLF into LF, and the rewrite would then change every line of a
    CRLF file."""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None


def unversioned(text: str, sha: str) -> bool:
    """Whether the text pins this repository at a commit SHA other than the release's
    with no tag beside it, which can't be compared with the release."""
    return any(SHA.match(m["ref"]) and m["ref"] != sha and not m["comment_tag"] for m in PIN.finditer(text))


@dataclass
class Plan:
    changes: dict[str, bytes] = field(default_factory=dict)
    # Why the repository is left alone: it names a later release.
    ahead: str | None = None
    # Files left as they are, and why.
    skipped: list[str] = field(default_factory=list)


def plan(clone: Path, sha: str, tag: str, canonical: dict[str, bytes]) -> Plan:
    """The files to change and their new contents. Nothing is written."""
    out = Plan()
    texts, copies = candidates(clone, tracked(clone))
    contents = {}
    for rel in texts:
        text = read(safe(clone, rel))
        if text is None:
            out.skipped.append(f"{rel} is not UTF-8, so it was left as it is")
        elif unversioned(text, sha):
            out.skipped.append(
                f"{rel} pins {CANONICAL} at a commit SHA with no tag comment, which can't be "
                f"compared with {tag}, so it was left as it is")
        else:
            contents[rel] = text
    newest = max((v for t in contents.values() for v in referenced_versions(t)), default=None)
    if newest is not None and newest > version(tag):
        out.ahead = "already on v%d.%d.%d, later than %s" % (*newest, tag)
        return out
    for rel, old in contents.items():
        new = rewrite_pins(old, sha, tag)
        if rel.rsplit("/", 1)[-1] in RENOVATE_FILES:
            new = rewrite_preset(new, tag)
        if new != old:
            out.changes[rel] = new.encode("utf-8")
    for rel in copies:
        source = COPIES[next(c for c in COPIES if rel == c or rel.endswith("/" + c))]
        if safe(clone, rel).read_bytes() != canonical[source]:
            out.changes[rel] = canonical[source]
    return out


def run(cmd: list[str], cwd: Path | None = None, stdin: str | None = None,
        env: dict[str, str] | None = None) -> str:
    """A command's output; a Failure with its message when it fails."""
    try:
        done = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True, check=False,
                              env={**os.environ, **env} if env else None)
    except OSError as e:
        raise Failure(f"{cmd[0]}: {e.strerror}") from None
    if done.returncode != 0:
        # The command and what it printed; neither ever holds the token.
        what = " ".join([c for c in cmd if c not in GIT_AUTH][:3])
        lines = (done.stderr or done.stdout).strip().splitlines()
        # git's own reason is on its `fatal:` or `error:` line, not always the last.
        said = [line for line in lines if line.startswith(("fatal:", "error:", "gh:", "HTTP"))] or lines[-1:]
        raise Failure(f"`{what} ...` failed: {said[0] if said else f'exit {done.returncode}'}")
    return done.stdout


def git(repo: Path, *args: str, auth: bool = False) -> str:
    return run(["git", *(GIT_AUTH if auth else []), *args], cwd=repo)


@dataclass
class Result:
    repo: str
    status: str
    detail: str = ""
    url: str = ""
    failed: bool = False


@dataclass
class Release:
    tag: str
    sha: str
    canonical: dict[str, bytes]
    # GIT_AUTHOR_* and GIT_COMMITTER_*, which win over any identity in git's config.
    author: dict[str, str] = field(default_factory=dict)
    # The App whose bot opens the PRs; without it, no branch or PR is taken as the
    # bot's, so none is replaced or closed.
    slug: str = ""

    @property
    def branch(self) -> str:
        return f"chore/github-{self.tag}"

    @property
    def title(self) -> str:
        return f"ci: {CANONICAL} {self.tag}"

    @property
    def notes(self) -> str:
        return f"https://github.com/{CANONICAL}/releases/tag/{self.tag}"


def body(release: Release, changed: list[str]) -> str:
    what = []
    if any(p.startswith(".github/workflows/") or "/.github/workflows/" in p for p in changed):
        what.append(
            f"- the pins of `{CANONICAL}`'s workflows in `.github/workflows/`, to the "
            f"release's commit `{release.sha}`, with the tag in the comment where there was one;"
        )
    if any(Path(p).name in RENOVATE_FILES for p in changed):
        what.append(f"- the Renovate preset, `github>{CANONICAL}#{release.tag}`;")
    if any(p.endswith(tuple(COPIES)) for p in changed):
        what.append("- the agent guard in `.claude/`, copied byte for byte from `agent/` at the release.")
    files = "\n".join(f"- `{p}`" for p in changed)
    return (
        f"Moves this repository onto [{CANONICAL} {release.tag}]({release.notes}) "
        f"(the release notes are there):\n\n" + "\n".join(what) + "\n\n"
        f"Changed files:\n\n{files}\n\n"
        f"Opened by the release fan-out in {CANONICAL} (`scripts/fan_out.py`), which "
        f"Renovate leaves these updates to. Nothing merges it automatically: the "
        f"maintainer reviews and merges it.\n"
    )


def open_pr(repo: str, release: Release) -> dict | None:
    out = run(["gh", "pr", "list", "--repo", repo, "--head", release.branch, "--state", "open",
               "--json", "number,url"])
    prs = json.loads(out or "[]")
    return prs[0] if prs else None


def superseded(repo: str, release: Release) -> list[dict]:
    """The open fan-out PRs, by the App's bot, for releases before this one."""
    if not release.slug:
        return []
    out = run(["gh", "pr", "list", "--repo", repo, "--state", "open", "--limit", "200",
               "--json", "number,url,headRefName,author"])
    logins = {f"app/{release.slug}", f"{release.slug}[bot]"}
    older = []
    for pr in json.loads(out or "[]"):
        m = re.fullmatch(r"chore/github-(v\d+\.\d+\.\d+)", pr.get("headRefName", ""))
        if m and version(m[1]) < version(release.tag) and (pr.get("author") or {}).get("login") in logins:
            older.append(pr)
    return older


def close_superseded(repo: str, release: Release, url: str) -> list[str]:
    """Close the older fan-out PRs, each with a comment naming the new one. Their
    branches stay."""
    closed = []
    for pr in superseded(repo, release):
        comment = (f"Superseded by {url}, which moves this repository onto {CANONICAL} "
                   f"{release.tag}. Closed by the release fan-out; the branch is kept.")
        run(["gh", "pr", "close", str(pr["number"]), "--repo", repo, "--comment", comment])
        closed.append(f"#{pr['number']}")
    return closed


def ours_on_top(clone: Path, release: Release) -> bool:
    """Whether the fetched branch (refs/fan-out/branch) is one commit, authored and
    committed by the App's bot, on top of the default branch: a branch an earlier
    run pushed before it failed to open the PR. Anything else is someone's work."""
    email = release.author.get("GIT_AUTHOR_EMAIL")
    if not email:
        return False
    ref = "refs/fan-out/branch"
    if git(clone, "rev-list", "--count", f"HEAD..{ref}").strip() != "1":
        return False
    who = git(clone, "log", "-1", "--format=%ae%n%ce%n%P", ref).splitlines()
    return who[:2] == [email, email] and len(who[2].split()) == 1


def move(repo: str, release: Release, work: Path, dry_run: bool) -> Result:
    existing = open_pr(repo, release)
    clone = work / repo.replace("/", "__")
    # Every commit but no file contents until they are read: the history is needed
    # to tell whether a branch left behind is the bot's one commit on the base.
    run(["git", *GIT_AUTH, "clone", "--quiet", "--filter=blob:none", "--no-tags",
         f"{GIT_BASE}/{repo}.git", str(clone)])
    base = git(clone, "symbolic-ref", "--short", "HEAD").strip()

    p = plan(clone, release.sha, release.tag, release.canonical)
    notes = "; ".join(p.skipped)
    if p.ahead:
        return Result(repo, "left alone", "; ".join([p.ahead, *p.skipped]))
    if not p.changes:
        return Result(repo, "current", "; ".join([f"{base} is already on {release.tag}", *p.skipped]))
    # Every path is checked again just before it is written.
    for rel, content in p.changes.items():
        safe(clone, rel).write_bytes(content)
    changed = sorted(p.changes)

    def detail(*parts: str) -> str:
        return "; ".join([x for x in parts if x] + ([notes] if notes else []))

    if dry_run:
        diff = git(clone, "diff")
        print(f"--- {repo}: the diff on {release.branch} ---")
        print(diff, end="" if diff.endswith("\n") else "\n")
        older = [f"#{pr['number']}" for pr in superseded(repo, release)]
        closing = f"would close {', '.join(older)} as superseded" if older else ""
        if existing:
            return Result(repo, "dry run", detail(f"PR #{existing['number']} is open; it would be left alone", closing),
                          existing["url"])
        return Result(repo, "dry run", detail(f"would open a PR changing {count_files(changed)}", closing))

    if existing:
        # Left as it is: the maintainer may have pushed to it. Say whether it holds
        # what this run would have written.
        git(clone, "fetch", "--quiet", "origin", f"+refs/heads/{release.branch}:refs/fan-out/branch", auth=True)
        same = _matches(clone, "refs/fan-out/branch", changed)
        closed = close_superseded(repo, release, existing["url"])
        return Result(repo, "PR open", detail(
            "already open, left alone" + ("" if same else "; it differs from what this run would write"),
            f"closed {', '.join(closed)} as superseded" if closed else ""), existing["url"])

    remote = git(clone, "ls-remote", "--heads", "origin", f"refs/heads/{release.branch}", auth=True).split()
    if remote:
        # A branch with no open PR: replaced only when it is the bot's own commit
        # from an earlier run. One with anyone else's work on it, or a PR the
        # maintainer closed on purpose, is left alone.
        git(clone, "fetch", "--quiet", "origin", f"+refs/heads/{release.branch}:refs/fan-out/branch", auth=True)
        if not ours_on_top(clone, release):
            return Result(repo, "skipped", detail(
                f"{release.branch} exists with no open PR and isn't one commit by the App's bot on {base}; "
                f"left alone for the maintainer"))

    git(clone, "checkout", "--quiet", "-b", release.branch)
    git(clone, "add", "--", *changed)
    message = f"{release.title}\n\nRelease notes: {release.notes}\n"
    run(["git", "commit", "--quiet", "-m", message], cwd=clone, env=release.author)
    # Against the commit just checked, so nothing pushed since is lost.
    lease = f"--force-with-lease=refs/heads/{release.branch}:{remote[0] if remote else ''}"
    git(clone, "push", "--quiet", lease, "origin", f"HEAD:refs/heads/{release.branch}", auth=True)
    out = run(["gh", "pr", "create", "--repo", repo, "--base", base, "--head", release.branch,
               "--title", release.title, "--body-file", "-"], stdin=body(release, changed)).strip()
    url = out.splitlines()[-1] if out else ""
    result = Result(repo, "opened", detail(count_files(changed)), url)
    try:
        closed = close_superseded(repo, release, url)
    except Failure as e:
        result.failed = True
        result.detail = detail(count_files(changed), f"but closing the older fan-out PRs failed: {e}")
        return result
    if closed:
        result.detail = detail(count_files(changed), f"closed {', '.join(closed)} as superseded")
    return result


def count_files(changed: list[str]) -> str:
    return f"{len(changed)} file" + ("" if len(changed) == 1 else "s")


def _matches(clone: Path, ref: str, paths: list[str]) -> bool:
    """Whether each of the paths at ref is the file now in the clone."""
    for p in paths:
        done = subprocess.run(["git", "show", f"{ref}:{p}"], cwd=clone, capture_output=True, check=False)
        if done.returncode != 0 or done.stdout != (clone / p).read_bytes():
            return False
    return True


def summary(results: list[Result], release: Release, dry_run: bool) -> str:
    lines = [
        f"## Fan-out of {CANONICAL} {release.tag}" + (" (dry run)" if dry_run else ""),
        "",
        "| Repository | Result | PR | Detail |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        detail = r.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r.repo} | {r.status} | {r.url} | {detail} |")
    return "\n".join(lines) + "\n"


def repositories(list_path: Path) -> list[str]:
    data = json.loads(list_path.read_text(encoding="utf-8"))
    return [r for r in data["repositories"] if r != CANONICAL]


def canonical_files(source: Path, sha: str) -> dict[str, bytes]:
    files = {}
    for path in COPIES.values():
        done = subprocess.run(["git", "show", f"{sha}:{path}"], cwd=source, capture_output=True, check=False)
        if done.returncode != 0:
            raise Failure(f"{path} isn't in {source} at {sha}")
        files[path] = done.stdout
    return files


def bot_author(slug: str) -> dict[str, str]:
    """The identity to commit as the App's bot, from its account's numeric ID."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        raise Failure(f"not an App's slug: {slug}")
    uid = run(["gh", "api", f"users/{slug}%5Bbot%5D", "--jq", ".id"]).strip()
    if not uid.isdigit():
        raise Failure(f"no numeric ID for {slug}[bot]: {uid}")
    name = f"{slug}[bot]"
    email = f"{uid}+{name}@users.noreply.github.com"
    return {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", help="the release, vX.Y.Z")
    parser.add_argument("--sha", help="the release's commit (default: the tag's, in --source)")
    parser.add_argument("--source", type=Path, default=ROOT, help="this repository's git checkout")
    parser.add_argument("--list", type=Path, default=LIST, help="the repository list (scripts/repos.json)")
    parser.add_argument("--repo", action="append", default=[], help="only this repository (repeatable)")
    parser.add_argument("--app-slug", help="the App whose bot authors the commits")
    parser.add_argument("--dry-run", action="store_true", help="print each repository's diff; change nothing")
    parser.add_argument("--names", action="store_true", help="print the repositories' names, comma-separated")
    args = parser.parse_args(argv)

    try:
        repos = repositories(args.list)
    except (OSError, ValueError, KeyError) as e:
        print(f"can't read the list {args.list}: {e}", file=sys.stderr)
        return 2
    if args.names:
        print(",".join(r.split("/", 1)[1] for r in repos))
        return 0
    if not args.tag or not TAG.match(args.tag):
        print(f"--tag takes a release tag, vX.Y.Z: {args.tag}", file=sys.stderr)
        return 2
    for r in args.repo:
        if r not in repos:
            print(f"{r} isn't in {args.list}, or is {CANONICAL} itself", file=sys.stderr)
            return 2
    if args.repo:
        repos = [r for r in repos if r in args.repo]

    try:
        sha = args.sha or run(["git", "rev-parse", "--verify", f"{args.tag}^{{commit}}"], cwd=args.source).strip()
        if not SHA.match(sha):
            raise Failure(f"not a full commit SHA: {sha}")
        canonical = canonical_files(args.source, sha)
        author = bot_author(args.app_slug) if args.app_slug else {}
    except Failure as e:
        print(e, file=sys.stderr)
        return 2
    release = Release(args.tag, sha, canonical, author, args.app_slug or "")

    results = []
    work = Path(tempfile.mkdtemp(prefix="fan-out-"))
    try:
        for repo in repos:
            try:
                result = move(repo, release, work, args.dry_run)
            except Failure as e:
                result = Result(repo, "failed", str(e), failed=True)
            except Exception as e:  # noqa: BLE001 - one repository's surprise mustn't stop the rest
                result = Result(repo, "failed", f"unexpected {type(e).__name__}: {e}", failed=True)
            results.append(result)
            print(f"{result.repo}: {result.status}" + (f" {result.url}" if result.url else "")
                  + (f" ({result.detail})" if result.detail else ""),
                  file=sys.stderr if result.failed else sys.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    text = summary(results, release, args.dry_run)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    failed = [r.repo for r in results if r.failed]
    if failed:
        print(f"{len(failed)} of {len(results)} repositories failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
