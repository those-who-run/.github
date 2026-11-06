# The agent guard

The one copy of the Claude Code guard and project settings that every repository in
the organisation takes. Each repository copies these files unchanged; what differs
between repositories lives in its own `.claude/guard.json`.

| File | Goes to | What it is |
| --- | --- | --- |
| `guard.py` | `.claude/hooks/guard.py` | The `PreToolUse` hook that checks every Bash command an agent sends. |
| `settings.json` | `.claude/settings.json` | The project settings: secrets denied to the Read and Edit tools, an ask before editing the settings, the guard or its config, bypass mode off, and the hook that runs the guard. |
| `guard.example.json` | `.claude/guard.json` | The repository's values. Copy it and change what differs. |
| `skills/<name>/SKILL.md` | `.claude/skills/<name>/SKILL.md` | The skills: how an agent builds, proves, cleans, opens and answers a review on a change. See [The skills and the reviewer](#the-skills-and-the-reviewer). |
| `agents/twr-reviewer.md` | `.claude/agents/twr-reviewer.md` | The reviewer's brief. |
| `copies.json` | stays here | The manifest: every file above that a repository copies, and where its copy lives. See [The manifest](#the-manifest). |
| `lanes.json`, `lanes.py` | stay here | How closely a pull request is read, from the paths it changes, and the reference classifier. |
| `tests/` | stays here | The tests of the guard, the lanes, the skills' front matter and the manifest. |

The guard started in v1 as those-who-run/those-who-run#352. Its docstring says what it
blocks, what it asks about and what it can't see; the tables in `guard.py` are the
policy.

## What the guard does

- **Blocks** (the agent is told why and to ask the maintainer): skipping the commit
  hooks (including `pre-commit uninstall`), pushing to the protected branch,
  force-pushing, deleting remote branches or tags (`--delete`, `--prune`, a `:ref`
  refspec), pushing any tag by name or with `--tags`, creating a release tag,
  `gh pr merge --admin`, creating or changing releases, running or switching
  workflows, changing the repository's settings, secrets, variables and rulesets,
  printing the `gh` token or changing its login (`token`, `login`, `logout` and
  `refresh` under `gh auth`), writes to a cluster or a registry, reading a secret
  from Bash, and the maintainer's own jobs in `those-who-run/.github`: the make
  targets `board`, `board-check`, `protect`, `protect-check`, `envs`, `envs-check`
  and `new-repo-check`, and the scripts behind them (`protect-repo.sh`,
  `board-sync.sh`, `release-envs.sh`, `new-repo-check.sh`), whether run by path,
  through a shell or with `source`. Reading or linting those scripts passes. Two dry
  runs pass, and only in exactly these forms: `bash -n <script>` (or `sh -n`, `dash
  -n`, `zsh -n`) and `make -n <target>`. A shell given one of the scripts anywhere in
  its arguments is otherwise blocked, whatever its options, since an option that takes
  a value (`-eo pipefail`, `+xo noexec`) moves where the script really is; so is any
  other make command naming a target. Longer dry runs such as `bash -nx <script>`,
  `make -nk <target>` or `make --dry-run <target>` are blocked too. Every repository takes
  the same guard, so a target of one of those names is blocked in any repository;
  name a repository's own targets differently. Known gaps, as for any command the
  guard can't see into: a script fed to a shell on stdin (`bash < script`,
  `cat script | bash`), a target name that reaches make at run time (`echo board |
  xargs make`, `t=board; make $t`), make running a command of its own
  (`make -E '$(shell ...)'`, `make --eval ...`), and a program that runs make or a
  script itself. `make -n` still runs a recipe line that starts with `+` or uses
  `$(MAKE)`, and every `$(shell ...)` the Makefile expands; the maintainer targets
  use none of these today, so their dry run runs nothing. `gh project item-edit`,
  the write `make board` makes, isn't blocked. That is safe only because agents run
  as the bot account (the identity hook enforces it) and the bot's token can't write
  to the organisation's projects; an agent with the maintainer's login could. The
  tables in `guard.py` have the full list.
- **Asks** the maintainer first: posting in public (`gh issue` and `gh pr` writes),
  any `gh pr merge` (`--auto` and `--disable-auto` included), re-running a workflow
  run, acting on a repository outside the organisation, and any command it can't
  follow that names `git`, `gh`, `kubectl`, `oc`, `helm`, `docker`, `podman` or
  `pre-commit`.
- **Asks before auto-merge and the merge queue** (rule `gh-auto-merge`): a
  `gh api graphql` call whose query uses `enablePullRequestAutoMerge`,
  `disablePullRequestAutoMerge`, `enqueuePullRequest` or `dequeuePullRequest`, as
  `gh pr merge` does. Merging is the maintainer's decision, and once auto-merge is on
  or a PR is in the queue it merges without a further ask. The guard reads a query
  given inline (`-f query=...`, `-F query=...`); a GraphQL request it reads from a
  file or stdin (`--input`, `-F query=@file`, `-F query=@-`) asks whatever it holds.
- **Reads `gh api` endpoints the way gh does**, before any rule on them: a full URL to
  `api.github.com` (in any case), or to a GitHub Enterprise Server's `/api/v3/` or
  `/api/graphql`, meets the same rules as its path, as does a path with a leading `/`,
  a `?query` or a `#fragment`. A write to any other full URL asks; a GraphQL mutation
  the guard blocks still blocks there.
- **Asks about values it can't read before they run**: a `gh api` endpoint, or any
  field of a `gh api graphql` call, holding a shell expansion that the guard doesn't
  resolve. It resolves only a plain `$NAME` or `${NAME}` whose variable the command
  line set with a plain `NAME=value` (or `export NAME=value`) it can read
  (`q='...'; gh api graphql -f query="$q"`); a `${...}` with an operator, `$(...)`,
  backquotes, `$'...'`, a glob or a brace expansion is never resolved, nor is a
  variable named in a prefix assignment of the same command, or one set by `read`,
  `for`, `declare`, `printf -v` and the like (also behind `command` or `builtin`), nor
  any after an assignment to an array, an element or with `+=`, after `declare -n` or
  `trap`, or on a command line that defines a function. `$(cat <<'EOF' ... EOF)` is
  read as the here-document it prints, literal when its own delimiter is quoted, and
  only when nothing in the whole command, here-document included and quotes aside
  (`PA""TH`), defines `cat` as a function or alias, names PATH, or runs `hash`,
  `enable`, `eval`, `source` or `.`. What the guard can still see
  decides first: a GraphQL
  mutation, and the REST rules, which it checks on the endpoint as written, a part
  it can't read standing for any one path segment, so
  `gh api -X PUT "repos/$O/$R/pulls/$N/merge"` blocks as the literal path does. Text
  in single quotes (a GraphQL `$owner`) and gh's own placeholders (`{owner}`,
  `{repo}`) are literal. Brace expansion counts with quoted parts too
  (`{"graphql",}`). A `gh api` write with no endpoint the guard can see asks. `xargs`
  or `parallel` running `gh api` asks, since its input may be flags, unless a
  replacement string (`xargs -I{}`, `-i`, `--replace`, or parallel's `{}`) fixes where
  the input goes and that is nowhere a flag could be; there the input is a value the
  guard can't read. parallel with `{= ... =}`, `--rpl` or `--plus` running gh asks. `env` takes any `NAME=value` argument as an assignment, as env
  does.
- **Fails closed**: if the guard errors, or `.claude/guard.json` is missing or can't be
  read, the command is blocked. Without a readable config every Bash command is
  blocked, with the reason, until the file is fixed; the Read and Edit tools still
  reach it, so nothing needs Bash to fix it.

Blocks and asks are logged, with tokens and passwords redacted, to
`~/.claude/twr-guard.log`. `TWR_GUARD_LOG` moves the log; set it empty to turn it off.

## Reading a verdict by hand

`--check` prints the verdict the hook would give a command, and writes nothing to the
log, so the log holds only what agents really sent:

```sh
python3 .claude/hooks/guard.py --check 'git push origin main'
```

It prints `allow`, or `ask` or `block` with the rule and the reason, such as
`block (git-push): main takes changes only through a PR`, and exits 0 for allow, 1 for
ask and 2 for block. Given no command, it reads the hook input on stdin, as the hook
does (with a terminal on stdin it prints the usage instead of waiting):

```sh
echo '{"tool_name": "Bash", "tool_input": {"command": "git commit -n"}}' \
    | python3 .claude/hooks/guard.py --check
```

It only reports: it runs nothing, and its verdict comes from the same code as the
hook's, failing closed the same way (an error or an unreadable `guard.json` prints
`block`). Claude Code runs the hook with no argument (`settings.json`), and the guard
refuses any argument but `--check` with exit 2, so a hook set up with a wrong argument
blocks every command rather than letting one through.

## Taking the guard into a repository

1. Copy the files:

   ```sh
   mkdir -p .claude/hooks
   base=https://raw.githubusercontent.com/those-who-run/.github/main/agent
   curl -fsSL "$base/guard.py" -o .claude/hooks/guard.py
   curl -fsSL "$base/settings.json" -o .claude/settings.json
   curl -fsSL "$base/guard.example.json" -o .claude/guard.json
   ```

2. Edit `.claude/guard.json`. Every key is optional and defaults to the value shown in
   `guard.example.json`; an unknown key, or a key given twice, makes the config
   unreadable, so a misspelt or repeated key blocks rather than being ignored.

   | Key | Default | Meaning |
   | --- | --- | --- |
   | `branch` | `"main"` | The branch that takes changes only through a pull request, by its short name (`main`, not `refs/heads/main`), made of `A-Z a-z 0-9 . _ / -`. Pushes are matched without regard to case. |
   | `release_tags` | `["v*"]` | The tags a release starts from. Each pattern is a prefix followed by one `*`, which stands for a version and so must start with a digit: `v*` covers `v1.2.0` but not `very-old`. List every pattern the repository releases under, such as `["v*", "gen/go/v*"]`. |
   | `release_workflow` | `"release.yml"` | The workflow that publishes a release, named in the reasons the guard gives. |
   | `owner` | `"those-who-run"` | The organisation. `gh` commands on a repository outside it ask first. |

3. Keep the files tracked and the rest of `.claude/` out of git:

   ```gitignore
   .claude/*
   !.claude/settings.json
   !.claude/guard.json
   !.claude/hooks/
   ```

4. Check that it runs (needs `python3` 3.9 or later, nothing else):

   ```sh
   python3 .claude/hooks/guard.py --check 'git push origin main'; echo "exit $?"
   ```

   It prints the block, its rule and reason, and `exit 2`, and leaves the log alone.
   The same with the hook input on stdin, as Claude Code sends it:

   ```sh
   echo '{"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}' \
       | python3 .claude/hooks/guard.py --check; echo "exit $?"
   ```

A repository that needs a rule the guard doesn't have raises it here, so that the
canonical copy carries it, rather than changing its own copy.

## Checking a copy against the canonical one

`guard.py` and `settings.json` should be byte for byte the same as the files here.
From the repository's root:

```sh
base=https://raw.githubusercontent.com/those-who-run/.github/main/agent
curl -fsSL "$base/guard.py" | diff - .claude/hooks/guard.py
curl -fsSL "$base/settings.json" | diff - .claude/settings.json
```

No output means the copy is current. With a checkout of this repository beside it,
`sha256sum ../.github/agent/guard.py .claude/hooks/guard.py` gives two equal sums.

## The manifest

`copies.json` lists every file here that a repository holds a copy of, and where
the copy lives in that repository:

```json
{
  "agent/guard.py": ".claude/hooks/guard.py",
  "agent/skills/twr-dev/SKILL.md": ".claude/skills/twr-dev/SKILL.md"
}
```

It is one JSON object with its keys sorted: a path in this repository, then the
path of its copy, always under `.claude/`. It lists the guard's two files
(`guard.py` and `settings.json`) and every file under `skills/` and `agents/`, each
of which keeps its path (`agent/skills/<name>/SKILL.md` goes to
`.claude/skills/<name>/SKILL.md`). `guard.example.json`, `lanes.json`, `lanes.py`,
this README and `tests/` are not copied, so they are not in it.

The manifest is there so that whatever copies these files, or checks a
repository's copies, reads one list and no tool keeps its own:

- It is read at the release of this repository that the other repository pins,
  never from `main`, so the list and the files it names are of the same commit.
- A release made before the manifest has none. For those the copies are the
  guard's two files, as they always were, and a reader handles both.
- A file in a repository at `.claude/skills/twr-*/` or `.claude/agents/twr-*.md`
  that is no destination in the manifest is a stale copy, left behind by a skill
  that was renamed or removed. The `twr-` prefix is what tells a copy from a
  skill of the repository's own, which is never touched.

**Adding a skill, or a file to one, means adding its line.** So does adding an
agent's brief; renaming or removing one changes or removes its line.
`tests/test_copies.py` fails while a file under `skills/` or `agents/` is missing
from the manifest, while the manifest names a file that isn't there or anything
else in this directory, and when a destination leaves `.claude/`.

## The skills and the reviewer

The skills are the procedures an agent follows on a change, written once here so a
session on a laptop and an unattended job read the same text. Each is a folder
under `skills/` holding a `SKILL.md`, in the format Claude Code loads from
`.claude/skills/`.

| Skill | When | What it does |
| --- | --- | --- |
| `twr-dev` | At the start of any change | The loop for one issue and one pull request: read the spec, plan the proof, build, test, check, clean, open. It chains the four below. |
| `twr-proof` | Before the code, and for the evidence table | Picks the kind of evidence each acceptance criterion needs (`unit`, `recording`, `parity`, `e2e`, `live`, `car`) and how to report it. An agent never claims `live` or `car`. |
| `twr-test-audit` | Whenever a test is added, edited or removed | Four questions before a test is added, and a regression test shown failing on the old code. |
| `twr-deslop` | After the checks pass, before the pull request | A clean-up of the diff that changes no behaviour. |
| `twr-pr-ready` | Just before opening the pull request | The readiness checklist and the body, with one row of evidence per criterion. |
| `twr-fix-review` | When a review arrives | Verify each item, then fix it or contest it with a reason. Two rounds at most. |

`agents/twr-reviewer.md` is the brief of `twr-reviewer`, the independent reviewer:
a second read for the maintainer, never an approval. It checks the acceptance criteria first,
then correctness, then the tests, and writes three files (`comment.md`,
`private.md`, `verdict.json`) for a step with no model in it to post. It never
posts, pushes or approves itself. A review must be run with the brief taken from a
release of this repository, never from the pull request under review, so a change
can't rewrite the reviewer that judges it.

These files are rules, like `AGENTS.md`: nothing in them enforces anything. They
never repeat or weaken a rule from paddock's `AGENTS.md`, which wins where the two
differ. They reach the other repositories as the copies
[the manifest](#the-manifest) lists; a repository that doesn't hold them yet reads
them from here.

### Lanes

`lanes.json` sorts a pull request into a lane by the paths it changes, and
`lanes.py` is the reference for what its patterns mean:

```sh
git diff --name-status origin/main...HEAD | python3 agent/lanes.py --repo those-who-run/ecu
```

It prints one word:

- `sensitive`: any path matches a sensitive pattern: the workflows, `.claude/`,
  `AGENTS.md`, an ADR, the contracts' `proto/`, the deploy manifests, a Dockerfile,
  a dependency manifest, a migration. One such file makes the whole change
  sensitive. A repository may add patterns of its own under `repositories`, and
  everything in this repository is sensitive.
- `light`: every path is documentation, or a test file the change adds. An edited,
  renamed or deleted test is not light, since that is where a weakened test hides.
- `standard`: everything else.

The lane changes how deep the review goes and how closely the maintainer reads. It
never changes who merges. No model decides it, so a pull request can't describe
itself into a lighter lane. A repository can only add sensitive patterns: it can't
declare its own code light.

## Running the tests

From the root of this repository, with no packages to install:

```sh
python3 -m unittest discover agent/tests
```
