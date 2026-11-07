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
- the copies in `.claude/`: what the release's manifest, `agent/copies.json`, lists,
  each a file of `agent/` written byte for byte to its path under `.claude/` (the
  guard and its settings, the skills, the agents' briefs), and created, with its
  directories, where the repository doesn't have it yet. Each repository's check
  of its copies compares them against the commit its CI pins, so they move
  together;
- stale copies: a tracked file at `.claude/skills/twr-*/**` or
  `.claude/agents/twr-*.md` that the manifest doesn't list as a destination is
  removed. Those names are the copies' own; nothing else under `.claude/` is ever
  removed, so a skill of the repository's own, under another name, stays;
- `.gitignore`: where its rules ignore a listed copy (most ignore `.claude/*`),
  an exception for the copy's directory (`!.claude/skills/`, `!.claude/agents/`)
  goes in after the last `.claude` rule, with one comment line. Nothing else in
  the file changes, and a `.gitignore` that already lets the copies be tracked is
  not touched. The rules are asked through `git check-ignore`, for every listed
  copy, tracked already or not. If a copy is still ignored, by a rule the
  exception can't undo or one in another file, the repository fails, with the
  rule named.

A release from before the manifest has none. Its copies are the guard's two files,
`agent/guard.py` to `.claude/hooks/guard.py` and `agent/settings.json` to
`.claude/settings.json`, updated only where the repository has them: nothing is
added or removed, and `.gitignore` is left alone, as before the manifest existed.

The manifest is read at the release's commit and checked before any repository is
touched. One that isn't a JSON object of path to path, that lists a source twice,
that names a source outside `agent/` or missing from the release (or not a regular
file there), or a destination outside `.claude/`, absolute, with `..` or a
backslash in it, given twice, inside another destination, ending in `.jinja`, or
`.claude/guard.json` (each repository's own values, never a copy), or that
doesn't list the guard's two files at the destinations above, stops the run with
exit 2. Each repository's check of its copies refuses the same manifests.

A copier template (chassis) gets the same in its template files: the `.jinja`
siblings of the workflows and `renovate.json`, and, when its copier.yml sets
`_subdirectory`, the same paths under that directory, copies, stale copies and
`.gitignore` included. A copy is always written at its plain path, never beside
it with the template suffix: copier renders only the files that carry the suffix
and copies the rest as bytes, so a repository made from the template gets the
canonical file even when a skill's text holds `{{ }}`, `[[ ]]` or `[% %]` (a
skill may quote a workflow expression or a template), which rendering would
mangle or fail on. For the same reason a manifest whose destination ends in
`.jinja` is refused. A `.jinja` sibling beside a copy would be rendered over it
in a new repository, and a `.gitignore.jinja` is the template of a new
repository's `.gitignore`; the fan-out edits neither (it can't know what they
render to) and says so in a note. A `.jinja` file under `.claude/skills/twr-*/`
is a stale copy like any other file there, and is removed.

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

Only regular files git tracks (mode 100644) are read, written or removed, and a new
copy is written only where git tracks nothing. A symbolic link or a submodule
where one of the files above, or a directory above them, should be fails the
repository before anything is read or written, as does anything but a regular
file under the copies' own names (`.claude/skills/twr-*`, `.claude/agents/twr-*.md`),
and every path is checked to stay inside the clone. A text file that isn't UTF-8
is left as it is, with a note.

One repository failing, whatever the error, doesn't stop the others. Each
repository's result is printed,
and written to the job summary ($GITHUB_STEP_SUMMARY) when there is one; the run
exits 1 at the end if any failed, 2 on bad arguments.

--dry-run clones each repository and prints the diff it would commit, and pushes,
commits and opens nothing. --names prints the repositories' names, comma-separated,
for the App token's `repositories`. --app-slug names the App whose bot authors the
commits (its noreply address is looked up with gh) and whose branches and PRs may be
replaced or closed; without it, git's own identity is used and nothing is replaced
or closed. The manifest and the files of the release are read from the git
repository --source (this checkout by default) at the tag's commit, so no network
is needed for them.

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
# The guard's copies, and the file of this repository each is a copy of: all that a
# release from before the manifest copies.
COPIES = {
    ".claude/hooks/guard.py": "agent/guard.py",
    ".claude/settings.json": "agent/settings.json",
}
# The release's own list of its copies, source to destination. A release without
# one copies COPIES.
MANIFEST = "agent/copies.json"
# A path in the manifest: plain names, so it needs no quoting in git, a PR body or
# the summary.
PLAIN = re.compile(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)+")
# The repository's own values for the guard: never a copy.
OWN = ".claude/guard.json"
# The suffix of a file copier renders (chassis's `_templates_suffix`).
TEMPLATE_SUFFIX = ".jinja"
# The names under `.claude/` that belong to the copies: anything tracked there is a
# copy or a stale one. OURS also takes the entry `twr-<name>` itself, which is a
# link or a submodule when git tracks it.
OURS = re.compile(r"\.claude/(?:skills/twr-[^/]*(?:/.*)?|agents/twr-[^/]*\.md)")
STALE = re.compile(r"\.claude/(?:skills/twr-[^/]*/.+|agents/twr-[^/]*\.md)")
# A line of a .gitignore about `.claude`: the exceptions for the copies go after the
# last one, where no earlier rule can undo them.
CLAUDE_RULE = re.compile(r"\s*!?/?\.claude(?:/|\s*$)")
GITIGNORE_NOTE = f"# Tracked: the copies of {CANONICAL}'s agent/ files that its agent/copies.json lists."
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


def candidates(clone: Path, files: dict[str, str], destinations: tuple[str, ...] = tuple(COPIES),
               listed: bool = False) -> tuple[list[str], dict[str, str], list[str]]:
    """The files that may hold a pin or the preset; the release's copies, each path
    in the clone with the destination it is a copy to; and the stale copies to
    remove. Only regular files git tracks: a symbolic link, or a submodule, where
    one of these files or the directories above it should be, fails the repository
    rather than letting a write follow it out of the clone.

    With a manifest (listed), every destination is a copy, tracked yet or not, and
    a tracked file under the copies' own names that is no destination is stale.
    Without one, a copy is a destination the repository already has, and nothing is
    stale."""
    texts, copies, stale = [], {}, []
    for prefix in roots(clone, files):
        watched = {prefix.rstrip("/"), f"{prefix}.github", f"{prefix}.github/workflows",
                   f"{prefix}.claude", f"{prefix}.claude/hooks"} - {""}
        # The directories a new copy is created under.
        above = {prefix + "/".join(d.split("/")[:n]) for d in destinations
                 for n in range(1, d.count("/") + 1)} if listed else set()
        for path, mode in files.items():
            if (path in watched or path in above) and mode != REGULAR:
                raise Failure(f"{path} is a symbolic link or a submodule (mode {mode}); refusing to follow it")
            if path in above:
                raise Failure(f"{path} is a file where the directory of a copy should be")
        for path, mode in sorted(files.items()):
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix):]
            is_text = (re.fullmatch(r"\.github/workflows/[^/]+", rest) and WORKFLOW_FILE.search(rest)) \
                or rest in RENOVATE_FILES
            is_copy = rest in destinations
            is_ours = listed and OURS.fullmatch(rest)
            if not (is_text or is_copy or is_ours):
                continue
            if mode != REGULAR:
                raise Failure(f"{path} is not a regular file (mode {mode}); refusing to follow it")
            if is_text:
                texts.append(path)
            elif is_copy:
                copies[path] = rest
            elif STALE.fullmatch(rest):
                stale.append(path)
        if listed:
            for dest in destinations:
                if any(path.startswith(f"{prefix}{dest}/") for path in files):
                    raise Failure(f"{prefix}{dest} is a directory where a copy should be")
                copies.setdefault(prefix + dest, dest)
    return texts, copies, stale


def safe(clone: Path, rel: str, new: bool = False) -> Path:
    """The file at rel in the clone, after checking that no part of the path is a
    symbolic link and that it resolves inside the clone. With new, the file may be
    missing, and directories above it too (a copy the repository doesn't have
    yet); whatever is there is checked the same."""
    path = clone
    for part in rel.split("/"):
        if part in ("", ".", ".."):
            raise Failure(f"{rel}: not a plain path in the repository")
        path = path / part
        if path.is_symlink():
            raise Failure(f"{rel}: {path.relative_to(clone)} is a symbolic link; refusing to follow it")
    there = path.is_file() or (new and not path.exists())
    if clone.resolve() not in path.resolve().parents or not there:
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


def ignored(clone: Path, paths: list[str]) -> dict[str, str]:
    """Those of the paths the clone's ignore rules exclude, each with the rule that
    does, as `.gitignore:3: .claude/*`. The rules alone, whether or not git tracks
    the path (--no-index), and without the ignore file of whoever runs this
    (core.excludesFile), so a laptop's dry run answers as the job does."""
    if not paths:
        return {}
    cmd = ["git", "-c", f"core.excludesFile={os.devnull}", "check-ignore", "--no-index", "--verbose", "-z", "--stdin"]
    try:
        done = subprocess.run(cmd, cwd=clone, input="".join(p + "\0" for p in paths).encode("utf-8"),
                              capture_output=True, check=False)
    except OSError as e:
        raise Failure(f"git: {e.strerror}") from None
    # 1 is git's answer when no path is ignored.
    if done.returncode not in (0, 1):
        said = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise Failure(f"`git check-ignore ...` failed: {said[-1] if said else f'exit {done.returncode}'}")
    fields = done.stdout.decode("utf-8", "replace").split("\0")
    found = {}
    for i in range(0, len(fields) - 3, 4):
        source, line, pattern, path = fields[i:i + 4]
        # --verbose also reports the exception (`!...`) that lets a path through.
        if not pattern.startswith("!"):
            found[path] = f"{source}:{line}: {pattern}"
    return found


def exception(destination: str) -> str:
    """The .gitignore line that lets a copy be tracked: its directory under
    `.claude/`, or the file itself when it sits in `.claude/`. git can't re-include
    a file whose directory is ignored, so the directory is what is re-included."""
    parts = destination.split("/")
    return "!" + "/".join(parts[:2]) + ("/" if len(parts) > 2 else "")


def with_exceptions(text: str, lines: list[str]) -> str:
    """A .gitignore's text with the exception lines added after its last rule about
    `.claude` (at the end when it has none), under one comment. Nothing else
    changes, and the file keeps its line endings."""
    eol = "\r\n" if "\r\n" in text else "\n"
    rows = text.splitlines(keepends=True)
    at = max((i for i, row in enumerate(rows) if CLAUDE_RULE.match(row)), default=len(rows) - 1)
    if rows and not rows[at].endswith(("\n", "\r")):
        rows[at] += eol
    new = ([] if GITIGNORE_NOTE in text else [GITIGNORE_NOTE]) + lines
    rows[at + 1:at + 1] = [line + eol for line in new]
    return "".join(rows)


@dataclass
class Copies:
    """What a release copies into each repository's `.claude/`."""
    # Each destination, and the bytes of the file of this repository it is a copy of.
    files: dict[str, bytes]
    # Whether the release lists them in agent/copies.json. A release from before the
    # manifest doesn't: its copies are the guard's two files, updated where a
    # repository has them, and nothing is added or removed.
    listed: bool = False


@dataclass
class Plan:
    changes: dict[str, bytes] = field(default_factory=dict)
    # Why the repository is left alone: it names a later release.
    ahead: str | None = None
    # Notes for the result: files left as they are, and why, and what a new copy
    # leaves for the maintainer to look at.
    skipped: list[str] = field(default_factory=list)
    # Those of the changes that are copies, and those of them git doesn't track
    # yet: a file to create.
    copied: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    # Every copy a manifest lists, changed or not: the ignore rules must let each
    # one through.
    listed: list[str] = field(default_factory=list)
    # The .gitignore files given an exception for the copies.
    unignored: list[str] = field(default_factory=list)
    # Stale copies, to remove.
    removed: list[str] = field(default_factory=list)


def unignore(clone: Path, files: dict[str, str], copies: dict[str, str], out: Plan) -> None:
    """Add to the plan the .gitignore exceptions the copies need: for each one the
    rules ignore, a line in the .gitignore of its root (the repository's, or the
    template subdirectory's). A copy git already tracks counts too: git refuses to
    stage a change to a file under an ignored directory, tracked or not. A root
    without a .gitignore gets no line: move() then fails the repository, naming
    the rule that ignores the copy."""
    hidden = ignored(clone, out.listed)
    wanted: dict[str, list[str]] = {}
    templated: dict[str, list[str]] = {}
    for rel in out.listed:
        root, line = rel[:len(rel) - len(copies[rel])], exception(copies[rel])
        template = f"{root}.gitignore{TEMPLATE_SUFFIX}"
        if rel in out.added and template in files and line not in templated.setdefault(template, []):
            templated[template].append(line)
        if rel in hidden and line not in wanted.setdefault(f"{root}.gitignore", []):
            wanted[f"{root}.gitignore"].append(line)
    for path, lines in wanted.items():
        if path not in files:
            continue
        if files[path] != REGULAR:
            raise Failure(f"{path} is not a regular file (mode {files[path]}); refusing to follow it")
        text = read(safe(clone, path))
        if text is None:
            out.skipped.append(f"{path} is not UTF-8, so it was left as it is")
            continue
        out.changes[path] = with_exceptions(text, lines).encode("utf-8")
        out.unignored.append(path)
    for path, lines in templated.items():
        # What it renders to can't be known here, so it can't be checked or edited.
        out.skipped.append(
            f"{path} is the template of a new repository's .gitignore and was left as it is: check "
            f"that it lets {', '.join(line[1:] for line in lines)} be tracked")


def plan(clone: Path, sha: str, tag: str, release: Copies) -> Plan:
    """The files to change and their new contents, and the files to remove. Nothing
    is written."""
    out = Plan()
    files = tracked(clone)
    texts, copies, stale = candidates(clone, files, tuple(release.files), release.listed)
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
    for rel, destination in copies.items():
        if rel + TEMPLATE_SUFFIX in files and rel + TEMPLATE_SUFFIX not in stale:
            out.skipped.append(
                f"{rel}{TEMPLATE_SUFFIX} is beside the copy {rel}: copier renders it over the copy in a "
                f"new repository, so that repository won't get the canonical file")
        if rel not in files:
            out.added.append(rel)
        elif safe(clone, rel).read_bytes() == release.files[destination]:
            continue
        out.changes[rel] = release.files[destination]
        out.copied.append(rel)
    for rel in out.added:
        # A guard with no config blocks every command an agent sends.
        root = rel[:len(rel) - len(copies[rel])]
        if rel == f"{root}.claude/hooks/guard.py" and not {root + OWN, root + OWN + TEMPLATE_SUFFIX} & set(files):
            out.skipped.append(
                f"{rel} is new and {root}{OWN} is missing: the guard blocks every command until the "
                f"repository has one (agent/guard.example.json)")
    out.removed = stale
    if release.listed:
        out.listed = list(copies)
        unignore(clone, files, copies, out)
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
    copies: Copies
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


def body(release: Release, changed: list[str], planned: Plan) -> str:
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
    if any(not p.endswith(tuple(COPIES)) for p in planned.copied):
        what.append(f"- the skills and the agents' briefs in `.claude/`, each copied byte for byte from "
                    f"`agent/` at the release, as its `{MANIFEST}` lists them;")
    if planned.removed:
        what.append(f"- stale copies, removed: files under `.claude/skills/twr-*/` or at "
                    f"`.claude/agents/twr-*.md` that the release's `{MANIFEST}` doesn't list;")
    if planned.unignored:
        what.append("- `.gitignore`: an exception for each directory of copies its rules ignored, and "
                    "nothing else;")
    if release.copies.listed and what:
        # The lines above end as they did before the manifest, when the guard's was
        # always the last; with more after it, the last one takes the full stop.
        what = [line.rstrip(";.") + ";" for line in what[:-1]] + [what[-1].rstrip(";.") + "."]
    files = "\n".join(f"- `{p}`" + (" (removed)" if p in planned.removed else "") for p in changed)
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

    p = plan(clone, release.sha, release.tag, release.copies)
    notes = "; ".join(p.skipped)
    if p.ahead:
        return Result(repo, "left alone", "; ".join([p.ahead, *p.skipped]))
    if not p.changes and not p.removed:
        return Result(repo, "current", "; ".join([f"{base} is already on {release.tag}", *p.skipped]))
    # Every path is checked again just before it is written or removed.
    for rel in p.removed:
        safe(clone, rel).unlink()
    for rel, content in p.changes.items():
        path = safe(clone, rel, new=rel in p.added)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    # With the files and the .gitignore as they will be committed: a copy git still
    # ignores would be left out of the commit, or fail it with git's own words.
    hidden = ignored(clone, p.listed)
    if hidden:
        rel, rule = next(iter(hidden.items()))
        raise Failure(f"{rel} is a copy the repository's ignore rules leave out ({rule}), and an exception "
                      f"in its root's .gitignore couldn't change that; the rules need fixing by hand")
    changed = sorted([*p.changes, *p.removed])

    def detail(*parts: str) -> str:
        return "; ".join([x for x in parts if x] + ([notes] if notes else []))

    if dry_run:
        if p.added:
            # So the diff shows the files git doesn't track yet.
            git(clone, "add", "--intent-to-add", "--", *p.added)
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
    if p.changes:
        git(clone, "add", "--", *sorted(p.changes))
    if p.removed:
        # Not `git add`, which refuses a path under a directory the rules ignore,
        # as a stale copy's may be once nothing else is left there.
        git(clone, "rm", "--quiet", "--cached", "--", *p.removed)
    message = f"{release.title}\n\nRelease notes: {release.notes}\n"
    run(["git", "commit", "--quiet", "-m", message], cwd=clone, env=release.author)
    # Against the commit just checked, so nothing pushed since is lost.
    lease = f"--force-with-lease=refs/heads/{release.branch}:{remote[0] if remote else ''}"
    git(clone, "push", "--quiet", lease, "origin", f"HEAD:refs/heads/{release.branch}", auth=True)
    out = run(["gh", "pr", "create", "--repo", repo, "--base", base, "--head", release.branch,
               "--title", release.title, "--body-file", "-"], stdin=body(release, changed, p)).strip()
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
    """Whether each of the paths at ref is the file now in the clone, and a path
    removed from the clone is missing at ref too."""
    for p in paths:
        done = subprocess.run(["git", "show", f"{ref}:{p}"], cwd=clone, capture_output=True, check=False)
        if not (clone / p).is_file():
            if done.returncode == 0:
                return False
        elif done.returncode != 0 or done.stdout != (clone / p).read_bytes():
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


class _Pairs(list):
    """A JSON object as its pairs, so a key given twice shows; a dict would keep
    the last and hide the other."""


def manifest(raw: bytes, modes: dict[str, str]) -> dict[str, str]:
    """The copies a release's manifest lists, destination to source, after checking
    all of it. modes has the mode of each file of the release under `agent/`. A
    Failure says what is wrong; nothing is copied from a manifest with a fault."""
    def fault(why: str) -> Failure:
        return Failure(f"{MANIFEST} at the release {why}; no repository was touched")

    try:
        pairs = json.loads(raw.decode("utf-8"), object_pairs_hook=_Pairs)
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise fault(f"is not JSON ({e})") from None
    if not isinstance(pairs, _Pairs):
        raise fault("is not a JSON object of source path to destination path")
    copies: dict[str, str] = {}
    sources = set()
    for source, destination in pairs:
        if source in sources:
            raise fault(f"lists {source} twice")
        sources.add(source)
        if not isinstance(destination, str):
            raise fault(f"gives {source} a destination that is not a string")
        for path, under in ((source, "agent/"), (destination, ".claude/")):
            if not (PLAIN.fullmatch(path) and path.startswith(under)) or {".", ".."} & set(path.split("/")):
                raise fault(f"names {path!r}, which is not a plain path under {under}")
        if modes.get(source) != REGULAR:
            raise fault(f"names {source}, which is not a regular file in the release"
                        + (f" (mode {modes[source]})" if source in modes else " (it is missing)"))
        if destination == OWN:
            raise fault(f"copies {source} to {OWN}, which is each repository's own")
        if destination.endswith(TEMPLATE_SUFFIX):
            raise fault(f"copies {source} to {destination}, which copier would render in a template "
                        f"(it ends in {TEMPLATE_SUFFIX})")
        if destination in copies:
            raise fault(f"copies two files to {destination}")
        copies[destination] = source
    for destination in copies:
        inside = [d for d in copies if d.startswith(destination + "/")]
        if inside:
            raise fault(f"copies to {destination} and to {inside[0]}, inside it")
    # The guard is in every manifest, where every repository has it: one without it
    # would leave each repository's guard behind its release without a word. Each
    # repository's check of its copies asks the same.
    for destination, source in COPIES.items():
        if copies.get(destination) != source:
            raise fault(f"doesn't copy {source} to {destination}, where every repository has it")
    return copies


def release_copies(source: Path, sha: str) -> Copies:
    """What the release copies: what its manifest lists, or, for a release from
    before the manifest, the guard's two files. Read from the git repository
    source at the release's commit."""
    modes = {}
    for record in run(["git", "ls-tree", "-r", "-z", sha, "--", "agent"], cwd=source).split("\0"):
        if record:
            meta, path = record.split("\t", 1)
            modes[path] = meta.split()[0]
    listed = MANIFEST in modes
    if listed and modes[MANIFEST] != REGULAR:
        raise Failure(f"{MANIFEST} at the release is not a regular file (mode {modes[MANIFEST]}); "
                      f"no repository was touched")
    copies = manifest(_blob(source, sha, MANIFEST), modes) if listed else COPIES
    return Copies({destination: _blob(source, sha, path) for destination, path in copies.items()}, listed)


def _blob(source: Path, sha: str, path: str) -> bytes:
    done = subprocess.run(["git", "show", f"{sha}:{path}"], cwd=source, capture_output=True, check=False)
    if done.returncode != 0:
        raise Failure(f"{path} isn't in {source} at {sha}")
    return done.stdout


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
        copies = release_copies(args.source, sha)
        author = bot_author(args.app_slug) if args.app_slug else {}
    except Failure as e:
        print(e, file=sys.stderr)
        return 2
    release = Release(args.tag, sha, copies, author, args.app_slug or "")

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
