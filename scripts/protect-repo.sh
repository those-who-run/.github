#!/usr/bin/env bash
# Apply the org's standard protection to its repositories, or report where they
# differ from it. The org is on GitHub Free, where rulesets can only be set per
# repository, so this script does by hand what an organisation ruleset would do on
# GitHub Team.
#
# Run by the maintainer, with gh logged in as an admin of the repositories. Never by
# an agent, and never from CI with write rights: these are the settings that hold
# agents and workflows to the rules, so whoever runs it can loosen them.
#
#   scripts/protect-repo.sh [--check] --all
#   scripts/protect-repo.sh [--check] <owner/repo>
#   scripts/protect-repo.sh [--check] <owner/repo> [--checks "test image"] [--check-name "<name>"]...
#                           [--approvals N] [--code-owners [--actions-prs]] [--no-actions]
#
# The list. scripts/repos.json holds every v2 repository and what it needs (v1,
# those-who-run/those-who-run, is in maintenance and keeps its settings, so it isn't
# listed). `--all` applies every repository in it; `<owner/repo>` applies one. A listed
# repository takes its settings only from the list, so the options below are refused
# for it: change the list in a PR instead. --list <file> reads another list.
#
# Each repository in the list has every one of these fields:
#   checks       status checks `main` must pass, from GitHub Actions only, each name a
#                string as GitHub reports it: `test`, or `checks / checks` for a
#                reusable workflow's job (`<caller job> / <reusable job>`). [] for none.
#                The checks are strict in every repository: a pull request's branch
#                must be up to date with `main` before it merges.
#   actions      whether Actions is on.
#   approvals    approving reviews a pull request needs (see --approvals).
#   code_owners  require a code owner's approval (see --code-owners).
#   actions_prs  Actions may create and approve PRs (see --actions-prs). Not needed
#                for release-please, which opens its PR as the release App; false
#                unless a workflow opens PRs with the GITHUB_TOKEN.
#   issues       whether Issues is on: only in paddock, where every issue lives.
#   publishes    whether it publishes, and so has a `release` environment.
#   tags         its release tag patterns, such as "v*" or "gen/go/v*".
#   codeql       languages for CodeQL's default setup, such as ["go", "actions"];
#                [] turns it off. Only the languages default setup supports.
# and may have this one, false when left out:
#   prevent_self_review  whether the `release` environment stops a reviewer approving
#                a deployment in a run they started. Only with publishes. Off while
#                the maintainer is the only reviewer: his merge starts each release
#                run, so with it on nobody could approve the deployment and every
#                release would wait until it timed out. The environment still needs
#                his approval, and the bot account isn't a reviewer. Turn it on once
#                there is a second reviewer.
# The top-level `maintainer` is the required reviewer of each `release` environment,
# and the account each .github/CODEOWNERS must name in its catch-all `*` rule.
# The top-level `release_app_id` is the numeric App ID of the organisation's release
# App (twr-release), the one actor that may create a release tag: release-please
# creates them as that App. (The workflow authenticates with the App's Client ID, from
# the `release-please` environment; a ruleset needs the number.) Where no App exists
# yet, such as a new organisation or a fork, it is a string starting with PLACEHOLDER,
# and every run refuses the `release tag creation` ruleset (applying it and checking
# it) in each repository, carries on with the rest, and exits 1.
#
# The options, for a repository not in the list (a one-off, such as outings):
# --checks      status checks `main` must pass, separated by spaces. Leave it out for
#               a repo with no CI; `main` still needs a pull request.
# --check-name  one status check whose name may hold spaces, such as
#               "checks / checks". Give it once per check; it adds to --checks.
# --approvals   approving reviews a pull request needs before it merges (default
#               0). Use 1 where an agent's bot account opens PRs, so they wait
#               for a person; admins can still merge their own PRs through the
#               bypass. A push after the approval needs a new one. Leave it at 0
#               where Dependabot or release-please PRs merge without a person.
# --code-owners require a code owner's approval (.github/CODEOWNERS, read from the
#               base branch) on top of --approvals (default off). Use it where
#               "Allow GitHub Actions to create and approve pull requests" is to be
#               turned on, for release-please: an approval by github-actions[bot] or
#               an agent's bot account then counts for nothing, since neither is a
#               code owner.
# --actions-prs turn "Allow GitHub Actions to create and approve pull requests" on
#               (default off: each run turns it off). Not needed for release-please,
#               which opens its PR as the release App. Only with --code-owners: with
#               the setting on, a workflow's token can approve a PR, and code-owner
#               review is what stops that approval counting.
# --no-actions  turn Actions off (repos with no workflows, like outings).
# A one-off run protects release tags matching v* (creation by the release App only,
# as for a listed repository, with the App ID from the list) and leaves Issues and
# CodeQL as they are.
#
# --check reads each repository's settings and prints what differs from the list,
# changing nothing; it exits 1 on any difference. It also reports every ruleset of the
# repository's own, listed here or not, whose enforcement isn't `active`. The
# organisation's rulesets aren't read: an apply here can't change them. Where code_owners is on, it also
# reads .github/CODEOWNERS, which must have a catch-all `*` rule naming the
# maintainer. What can't be read without admin rights is printed as "not checked",
# so --check is only complete with an admin login, and it warns when it isn't.
# Without --check, each setting is written only where it differs, so a second run in
# a row reports no change.
#
# Failures. A bad list or bad options, or a repository that can't be applied (no admin
# rights, or actions_prs without a CODEOWNERS file), stop the run with exit 2 before
# anything is read or changed. Once writing starts, a write GitHub refuses doesn't stop
# the run, and nor does a refused read of a ruleset: the script prints GitHub's
# message, carries on with the next setting and the next repository, and exits 1 at
# the end. So does a refused `release tag creation` ruleset (release_app_id still a
# placeholder). --check exits 1 on drift or a refusal.
#
# Needs: gh and jq.
set -euo pipefail

usage() {
  echo "usage: protect-repo.sh [--check] (--all | <owner/repo> [--checks \"a b\"] [--check-name \"a / b\"]... [--approvals N] [--code-owners [--actions-prs]] [--no-actions]) [--list FILE]" >&2
  exit 2
}
fail() { echo "$*" >&2; exit 2; }

list="$(cd "$(dirname "$0")" && pwd)/repos.json"
mode=apply
all=false
target=""
one_off=false
f_checks=()
f_approvals=0
f_code_owners=false
f_actions_prs=false
f_actions=true
while [ $# -gt 0 ]; do
  case "$1" in
    --check) mode=check; shift ;;
    --all) all=true; shift ;;
    --list) [ $# -ge 2 ] || usage; list=$2; shift 2 ;;
    --checks)
      [ $# -ge 2 ] || usage
      read -r -a split <<<"$2"
      f_checks+=("${split[@]}"); one_off=true; shift 2 ;;
    --check-name) [ $# -ge 2 ] || usage; f_checks+=("$2"); one_off=true; shift 2 ;;
    --approvals) [ $# -ge 2 ] || usage; f_approvals=$2; one_off=true; shift 2 ;;
    --code-owners) f_code_owners=true; one_off=true; shift ;;
    --actions-prs) f_actions_prs=true; one_off=true; shift ;;
    --no-actions) f_actions=false; one_off=true; shift ;;
    -h|--help) usage ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) [ -z "$target" ] || fail "one repository at a time, or --all"; target=$1; shift ;;
  esac
done
if [ "$all" = true ]; then
  [ -z "$target" ] || fail "--all or one repository, not both"
  [ "$one_off" = false ] || fail "--all takes its settings from the list, not from options"
else
  [ -n "$target" ] || usage
fi
case "$f_approvals" in
  ''|*[!0-9]*) fail "--approvals takes a number: $f_approvals" ;;
esac

# Everything a repository's settings must satisfy, for the list and for a one-off
# alike. Checked before anything is read or changed, so a refused run leaves every
# repository as it was. $strict is false for a one-off, which may leave issues and
# codeql null (left as they are).
# shellcheck disable=SC2016 # a jq program: jq expands its $names, not bash
# langs is what the default-setup PATCH accepts in GitHub's published REST
# description, so a language it would refuse fails here, before any repository changes.
VALIDATE='
def app_id: (type == "number" and . > 0 and . == floor) or (type == "string" and startswith("PLACEHOLDER"));
def langs: ["actions","c-cpp","csharp","go","java-kotlin","javascript-typescript","python","ruby","swift"];
def fields: ["checks","actions","approvals","code_owners","actions_prs","issues","publishes","tags","codeql"];
def optional: ["prevent_self_review"];
def bool($r; $f): if (.[$f] | type) != "boolean" then "\($r): \($f) must be true or false" else empty end;
def strings: type == "array" and all(.[]; type == "string" and length > 0);
def repo($r):
  if type != "object" then "\($r): must be an object" else
    ((keys - fields - optional)[] | "\($r): unknown field \(.)"),
    ((fields - keys)[] | "\($r): missing field \(.)"),
    (if (.checks | strings | not) then "\($r): checks must be a list of names"
     elif (.checks | length) != (.checks | unique | length) then "\($r): checks has a name twice"
     else empty end),
    (if (.approvals | type) != "number" or .approvals < 0 or .approvals != (.approvals | floor)
     then "\($r): approvals must be a whole number, 0 or more" else empty end),
    bool($r; "actions"), bool($r; "code_owners"), bool($r; "actions_prs"), bool($r; "publishes"),
    (if $strict or .issues != null then bool($r; "issues") else empty end),
    (if (.tags | strings | not) or (.tags | length) == 0 then "\($r): tags must list at least one pattern"
     elif any(.tags[]; startswith("refs/") or test("\\s")) then "\($r): tags are patterns such as v*, without refs/tags/ or spaces"
     else empty end),
    (if (.codeql == null and ($strict | not)) then empty
     elif (.codeql | strings | not) then "\($r): codeql must be a list of languages"
     elif any(.codeql[]; . as $l | langs | index($l) | not) then "\($r): codeql languages are \(langs | join(", "))"
     else empty end),
    (if .actions_prs == true and .code_owners != true then
       "\($r): actions_prs needs code_owners: with Actions allowed to approve PRs, only code-owner review stops a workflow token approving one"
     else empty end),
    (if .actions_prs == true and .actions != true then "\($r): actions_prs and actions off contradict each other" else empty end),
    (if has("prevent_self_review") then bool($r; "prevent_self_review") else empty end),
    (if .prevent_self_review == true and .publishes != true then
       "\($r): prevent_self_review needs publishes: it is a setting of the release environment" else empty end),
    (if .publishes == true and .actions != true then "\($r): publishes needs actions: releases run on Actions" else empty end),
    (if .actions != true and ((.checks // []) | length) > 0 then "\($r): checks need actions: they report from GitHub Actions" else empty end),
    (if .actions != true and ((.codeql // []) | length) > 0 then "\($r): codeql needs actions: default setup runs on Actions" else empty end)
  end;
if type != "object" then "the list must be a JSON object" else
  ((keys - ["maintainer","release_app_id","repositories"])[] | "unknown top-level field \(.)"),
  (if (.maintainer | type) != "string" or (.maintainer | test("^[A-Za-z0-9-]+$") | not)
   then "maintainer must be a GitHub login" else empty end),
  (if (.release_app_id | app_id | not)
   then "release_app_id must be the release App'"'"'s App ID, a whole number, or a string starting with PLACEHOLDER until the App exists"
   else empty end),
  (if (.repositories | type) != "object" or (.repositories | length) == 0
   then "repositories must name at least one repository"
   else .repositories | to_entries[] |
     (if (.key | test("^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$") | not) then "\(.key): not an owner/repo name" else empty end),
     (.value | repo(.key))
   end),
  (if (.repositories | type) == "object" and ([.repositories | keys[] | ascii_downcase] | length) != ([.repositories | keys[] | ascii_downcase] | unique | length)
   then "a repository is listed twice" else empty end)
end'

[ -f "$list" ] || fail "no list at $list"
listed=$(cat "$list")
jq -e . >/dev/null 2>&1 <<<"$listed" || fail "$list is not JSON"
problems=$(jq -r --argjson strict true "$VALIDATE" <<<"$listed")
[ -z "$problems" ] || fail "$list: $problems"
maintainer=$(jq -r .maintainer <<<"$listed")
release_app_id=$(jq -c .release_app_id <<<"$listed")
app_refused=""
if [ "$(jq -r '.release_app_id | type' <<<"$listed")" != number ]; then
  app_refused="release_app_id in $list is still a placeholder ($release_app_id): set it to the release App's numeric App ID, from the App's settings page, by pull request"
  echo "warning: $app_refused. The 'release tag creation' ruleset is refused in every repository, and the run exits 1." >&2
fi

# The repositories to protect, one compact JSON object each: {"repo": ..., settings}.
if [ "$all" = true ]; then
  targets=$(jq -c '.repositories | to_entries[] | {repo: .key, prevent_self_review: false} + .value' <<<"$listed")
else
  # GitHub's names ignore case, so "Paddock" is paddock and takes paddock's settings.
  entry=$(jq -c --arg r "$target" '.repositories | to_entries[] | select((.key | ascii_downcase) == ($r | ascii_downcase)) | {repo: .key, prevent_self_review: false} + .value' <<<"$listed")
  if [ -n "$entry" ]; then
    [ "$one_off" = false ] || fail "$target is in $list, which sets its options: change it there"
    targets=$entry
  else
    targets=$(jq -cn --arg r "$target" --argjson approvals "$f_approvals" \
      --argjson code_owners "$f_code_owners" --argjson actions_prs "$f_actions_prs" \
      --argjson actions "$f_actions" '$ARGS.positional as $checks |
      {repo: $r, checks: $checks, actions: $actions, approvals: $approvals,
       code_owners: $code_owners, actions_prs: $actions_prs, issues: null,
       publishes: false, prevent_self_review: false, tags: ["v*"], codeql: null}' --args ${f_checks[@]+"${f_checks[@]}"})
    problems=$(jq -r --argjson strict false --arg m "$maintainer" --argjson app "$release_app_id" \
      '{maintainer: $m, release_app_id: $app, repositories: {(.repo): del(.repo)}} | '"$VALIDATE" <<<"$targets")
    [ -z "$problems" ] || fail "${problems//actions_prs needs code_owners/--actions-prs needs --code-owners}"
  fi
fi

# The actions every workflow may use besides GitHub's own (actions/*, github/*, CodeQL
# among them) and the organisation's own reusable workflows, which GitHub always
# allows here. Each needs a reason; anything else fails before it runs.
allowed_actions=(
  # release-please opens each repository's release PR and tags its releases.
  "googleapis/release-please-action@*"
  # image builds: buildx, the ghcr.io login, build and push (image.yml).
  "docker/*"
  # Python with uv, for radio.
  "astral-sh/setup-uv@*"
  # what kind of update a Dependabot PR is.
  "dependabot/fetch-metadata@*"
  # OpenSSF Scorecard; v1's inventory found it missing from the old list.
  "ossf/scorecard-action@*"
)

# Prints "<path>: want X, have Y" for every value that differs between $w and $h,
# with the nested keys joined by dots.
# shellcheck disable=SC2016 # a jq program
DIFF='
def flat: if type == "object" and length > 0
  then to_entries[] as $e | ($e.value | flat) as [$k, $v] | [[$e.key] + $k, $v]
  else [[], .] end;
def table: [flat | {key: (.[0] | join(".")), value: .[1]}] | from_entries;
($w | table) as $tw | ($h | table) as $th
| ($tw + $th | keys[]) as $k | select($tw[$k] != $th[$k])
| "\($p)\($k): want \($tw[$k] | tojson), have \($th[$k] | tojson)"'

# A ruleset reduced to what this script sets, so GitHub's extra fields and the order
# it returns things in don't count as a difference. $want is the body we send.
# shellcheck disable=SC2016 # a jq program
CANON_RULESET='
def checks: [.[]? | "\(.context)@\(.integration_id // "any")"] | sort;
def params($keep): with_entries(select(.key as $k | ($keep // {}) | has($k)))
  | (if has("allowed_merge_methods") then .allowed_merge_methods |= sort else . end)
  | (if has("required_status_checks") then .required_status_checks |= checks else . end);
([$want.rules[] | {key: .type, value: (.parameters // {})}] | from_entries) as $keep
| if . == {} then {} else
  {target, enforcement,
   include: (.conditions.ref_name.include // [] | sort),
   exclude: (.conditions.ref_name.exclude // [] | sort),
   bypass: ([.bypass_actors[]? | "\(.actor_type):\(.actor_id):\(.bypass_mode)"] | sort),
   rules: ([.rules[]? | .type as $t | {key: $t, value: ((.parameters // {}) | params($keep[$t]))}] | from_entries)}
  end'

say() { printf '%s: %s\n' "$repo" "$*"; }

# reconcile NAME WANT HAVE WRITER [ARGS...]: in --check, report how HAVE differs from
# WANT; otherwise run the writer only when they differ.
reconcile() {
  local name=$1 want=$2 have=$3
  shift 3
  if jq -en --argjson w "$want" --argjson h "$have" '$w == $h' >/dev/null; then
    [ "$mode" = check ] || say "$name: unchanged"
    return 0
  fi
  if [ "$mode" = check ] && [ "$have" = '{}' ]; then
    say "drift: $name: not set"
    drift=true
  elif [ "$mode" = check ]; then
    jq -nr --argjson w "$want" --argjson h "$have" --arg p "$repo: drift: $name: " "$DIFF"
    drift=true
  elif "$@"; then
    say "$name: changed"
    changed=true
  else
    say "$name: not changed: GitHub refused a write (its message is above)" >&2
    failed=true
    repo_failed=true
  fi
}
not_checked() { say "$1: not checked (reading it needs admin rights)"; }
# api ARGS...: gh api, for every write (and the reads a writer needs), which may fail
# without stopping the run. The writers run as a condition (reconcile's `elif "$@"`),
# where bash ignores set -e, so each call is checked here and by its caller. On a
# failure it prints the call and GitHub's message, with any field errors it lists,
# and returns non-zero.
api() {
  local body status=0 what="" msg a
  body=$(gh api "$@" 2>"$gh_err") || status=$?
  if [ "$status" -eq 0 ]; then
    [ -z "$body" ] || printf '%s\n' "$body"
    return 0
  fi
  for a in "$@"; do
    case "$a" in
      -X|GET|POST|PUT|PATCH|DELETE|repos/*|users/*) what+="${what:+ }${a%%\?*}" ;;
    esac
  done
  msg=$(jq -rs '[.[] | objects | (.message // empty), (.errors[]?
    | if type == "string" then . else (.message // tostring) end)] | join("; ")' <<<"$body" 2>/dev/null) || msg=""
  [ -n "$msg" ] || msg=$(tr '\n' ' ' <"$gh_err")
  say "error: gh api $what: ${msg:-no message (exit $status)}" >&2
  return "$status"
}
# A read that may fail (404 for something absent, 403 without admin rights) prints
# its fallback instead of gh's error body.
read_or() {
  local body
  if body=$(gh api "$1" 2>/dev/null); then printf '%s\n' "$body"; else printf '%s\n' "$2"; fi
}
# GitHub returns lists 30 to a page, and gh --paginate prints each page's JSON in
# turn, so these join the pages before anything looks for a name in them.
# Only the repository's own rulesets: GitHub includes the organisation's by default
# (includes_parents), which this script neither sets nor can fix from here.
rulesets() { api --paginate "repos/$repo/rulesets?includes_parents=false" | jq -cs 'add // []'; }
branch_policies() {
  api --paginate "repos/$repo/environments/release/deployment-branch-policies" |
    jq -cs '{branch_policies: (map(.branch_policies // []) | add // [])}'
}

write_ruleset() {
  local id=$1 body=$2
  if [ -n "$id" ]; then
    api -X PUT "repos/$repo/rulesets/$id" --input - <<<"$body" >/dev/null
  else
    api -X POST "repos/$repo/rulesets" --input - <<<"$body" >/dev/null
  fi
}
ruleset() {
  local name=$1 body=$2 id have want_c have_c list
  # A read GitHub refuses leaves this ruleset unchecked and unwritten, and the run
  # carries on: writing it blind could make a second ruleset of the same name.
  have='{}'
  if ! list=$(rulesets) ||
     { id=$(jq -r --arg n "$name" '[.[] | select(.name == $n)][0].id // empty' <<<"$list") &&
       [ -n "$id" ] && ! have=$(api "repos/$repo/rulesets/$id"); }; then
    if [ "$mode" = check ]; then
      say "ruleset '$name': not checked: GitHub refused a read (its message is above)" >&2
    else
      say "ruleset '$name': not changed: GitHub refused a read (its message is above)" >&2
    fi
    failed=true
    repo_failed=true
    return 0
  fi
  want_c=$(jq -c --argjson want "$body" "$CANON_RULESET" <<<"$body")
  have_c=$(jq -c --argjson want "$body" "$CANON_RULESET" <<<"$have")
  # GitHub leaves bypass_actors out of a ruleset for anyone who can't edit it, so
  # without admin rights its absence says nothing about the bypass list. An apply
  # (always an admin) still compares it, so a missing list is written, not skipped.
  if [ "$mode" = check ] && [ "$have" != '{}' ] && ! jq -e 'has("bypass_actors")' >/dev/null <<<"$have"; then
    want_c=$(jq -c 'del(.bypass)' <<<"$want_c")
    have_c=$(jq -c 'del(.bypass)' <<<"$have_c")
    not_checked "ruleset '$name' bypass"
  fi
  reconcile "ruleset '$name'" "$want_c" "$have_c" write_ruleset "$id" "$body"
}

# Reports each ruleset on the repository whose enforcement isn't `active`: drift in
# --check, a warning in an apply (one the list doesn't manage stays as it is).
enforcement_report() {
  local list off name enforcement
  if ! list=$(rulesets); then
    say "ruleset enforcement: not checked: GitHub refused a read (its message is above)" >&2
    failed=true
    repo_failed=true
    return 0
  fi
  off=$(jq -r '.[] | select((.enforcement // "active") != "active") | "\(.name)\t\(.enforcement)"' <<<"$list")
  [ -n "$off" ] || return 0
  while IFS=$'\t' read -r name enforcement; do
    if [ "$mode" = check ]; then
      say "drift: ruleset '$name' is not enforced: enforcement is $enforcement, want active"
      drift=true
    else
      say "warning: ruleset '$name' is not enforced: enforcement is $enforcement"
    fi
  done <<<"$off"
}

# Each writer returns non-zero if any of its writes failed. One that makes several
# writes carries on past a failed one where the next doesn't depend on it.
write_merge() {
  api -X PATCH "repos/$repo" -F allow_squash_merge=true -F allow_merge_commit=false \
    -F allow_rebase_merge=false -f squash_merge_commit_title=PR_TITLE \
    -F delete_branch_on_merge=true -F allow_auto_merge=true >/dev/null
}
write_issues() { api -X PATCH "repos/$repo" -F "has_issues=$1" >/dev/null; }
write_security() {
  local rc=0
  api -X PATCH "repos/$repo" \
    -f 'security_and_analysis[secret_scanning][status]=enabled' \
    -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled' >/dev/null || rc=1
  api -X PUT "repos/$repo/vulnerability-alerts" >/dev/null || rc=1
  api -X PUT "repos/$repo/private-vulnerability-reporting" >/dev/null || rc=1
  return "$rc"
}
write_actions() {
  local cfg=$1 public=$2 rc=0
  if [ "$(jq -r .actions <<<"$cfg")" = false ]; then
    api -X PUT "repos/$repo/actions/permissions" -F enabled=false >/dev/null
    return
  fi
  # The allowlist and the token's permissions are written even if turning on the
  # SHA-pinned selection failed: each narrows what workflows may do on its own.
  api -X PUT "repos/$repo/actions/permissions" -F enabled=true -f allowed_actions=selected -F sha_pinning_required=true >/dev/null || rc=1
  jq -n '{github_owned_allowed: true, verified_allowed: false, patterns_allowed: $ARGS.positional}' --args "${allowed_actions[@]}" |
    api -X PUT "repos/$repo/actions/permissions/selected-actions" --input - >/dev/null || rc=1
  api -X PUT "repos/$repo/actions/permissions/workflow" -f default_workflow_permissions=read \
    -F "can_approve_pull_request_reviews=$(jq -r .actions_prs <<<"$cfg")" >/dev/null || rc=1
  if [ "$public" = true ]; then
    api -X PUT "repos/$repo/actions/permissions/fork-pr-contributor-approval" -f approval_policy=all_external_contributors >/dev/null || rc=1
  fi
  return "$rc"
}
write_environment() {
  local cfg=$1 id policies rc=0
  id=$(api "users/$maintainer" | jq -e .id) || return 1
  # The branch policies belong to the environment, so they wait for it.
  jq --argjson id "$id" '{wait_timer: 0, prevent_self_review: .prevent_self_review,
    reviewers: [{type: "User", id: $id}], can_admins_bypass: false,
    deployment_branch_policy: {protected_branches: false, custom_branch_policies: true}}' <<<"$cfg" |
    api -X PUT "repos/$repo/environments/release" --input - >/dev/null || return 1
  policies=$(branch_policies) || return 1
  # Add the missing policies, then remove any the list doesn't name.
  jq -c --argjson have "$policies" '[{name: "main", type: "branch"}] + [.tags[] | {name: ., type: "tag"}]
    | .[] | select(. as $p | $have.branch_policies | any(.name == $p.name and .type == $p.type) | not)' <<<"$cfg" |
    (
      ok=0
      while IFS= read -r p; do
        api -X POST "repos/$repo/environments/release/deployment-branch-policies" --input - <<<"$p" >/dev/null || ok=1
      done
      exit "$ok"
    ) || rc=1
  jq -r --argjson cfg "$cfg" '([{name: "main", type: "branch"}] + [$cfg.tags[] | {name: ., type: "tag"}]) as $want
    | .branch_policies[] | select(. as $p | $want | any(.name == $p.name and .type == $p.type) | not) | .id' <<<"$policies" |
    (
      ok=0
      while IFS= read -r pid; do
        api -X DELETE "repos/$repo/environments/release/deployment-branch-policies/$pid" >/dev/null || ok=1
      done
      exit "$ok"
    ) || rc=1
  return "$rc"
}
write_codeql() {
  jq -c 'if (.codeql | length) > 0 then {state: "configured", query_suite: "default", languages: .codeql}
         else {state: "not-configured"} end' <<<"$1" |
    api -X PATCH "repos/$repo/code-scanning/default-setup" --input - >/dev/null
}

# Prints what is wrong with the repository's .github/CODEOWNERS, or nothing. Code-owner
# review asks only for the owners of the files a PR changes, so a file no rule covers
# would merge without a code owner's approval: a catch-all `*` rule naming the
# maintainer closes that. The last `*` line counts, as the last matching rule wins.
codeowners_problem() {
  local file
  if ! file=$(gh api "repos/$repo/contents/.github/CODEOWNERS" 2>/dev/null); then
    echo "no .github/CODEOWNERS on the default branch"
    return 0
  fi
  jq -r --arg m "$maintainer" '
    [.content // "" | gsub("\\s"; "") | @base64d | split("\n")[]
     | sub("(^|\\s)#.*$"; "") | [splits("\\s+") | select(length > 0)]
     | select(length > 0 and .[0] == "*")] as $all
    | if ($all | length) == 0 then "no catch-all * rule in .github/CODEOWNERS"
      elif ($all[-1][1:] | map(ascii_downcase) | index("@" + ($m | ascii_downcase)) | not)
      then "the catch-all * rule in .github/CODEOWNERS does not name @\($m)"
      else empty end' <<<"$file"
}

# Reads only. Refuses, before anything changes anywhere, an apply without admin
# rights or Actions allowed to approve PRs in a repository with no CODEOWNERS (where
# code-owner review would ask for no one).
preflight() {
  local cfg=$1 info
  repo=$(jq -r .repo <<<"$cfg")
  info=$(gh api "repos/$repo" 2>/dev/null) || fail "$repo: can't read the repository"
  [ "$mode" = check ] && return 0
  [ "$(jq -r '.permissions.admin // false' <<<"$info")" = true ] ||
    fail "$repo: applying needs an admin of the repository; --check reads without one"
  if [ "$(jq -r .actions_prs <<<"$cfg")" = true ] &&
     ! gh api "repos/$repo/contents/.github/CODEOWNERS" >/dev/null 2>&1; then
    fail "$repo: actions_prs needs a .github/CODEOWNERS on the default branch, or code-owner review asks for no one"
  fi
}

protect() {
  local cfg=$1 info admin public checks approvals fresh tag_rules want have problem pol
  repo=$(jq -r .repo <<<"$cfg")
  info=$(gh api "repos/$repo")
  repo_failed=false
  admin=$(jq -r '.permissions.admin // false' <<<"$info")
  public=$(jq -r '.private == false' <<<"$info")
  changed=false
  if [ "$mode" = check ] && [ "$admin" != true ]; then
    say "warning: not an admin of this repository, so this check is incomplete: --check is only complete with an admin login"
    incomplete=true
  fi

  # main: changes arrive by squashed pull request; no force-push, no deletion. Admins
  # may bypass only through a pull request, which lets them merge their own PRs when
  # approvals are required. With reviews
  # required, an approval is dropped by a later push and must come after it.
  # 15368 is the GitHub Actions app: only its runs can satisfy a required check.
  # The checks are strict: a pull request's branch must be up to date with `main`
  # before it merges, so its checks have run against the `main` it merges into. Two
  # PRs each green on their own head can still break `main` once both are merged
  # (one renames what the other's tests call); strict makes the second update and
  # run its checks again first. Every repository takes it, the list has no field
  # for it, and a one-off with --checks takes it too.
  approvals=$(jq .approvals <<<"$cfg")
  fresh=$([ "$approvals" -gt 0 ] && echo true || echo false)
  checks=$(jq -c '[.checks[] | {context: ., integration_id: 15368}]' <<<"$cfg")
  ruleset main "$(jq -cn --argjson cfg "$cfg" --argjson checks "$checks" --argjson fresh "$fresh" '
    {name: "main", target: "branch", enforcement: "active",
     conditions: {ref_name: {include: ["~DEFAULT_BRANCH"], exclude: []}},
     bypass_actors: [{actor_id: 5, actor_type: "RepositoryRole", bypass_mode: "pull_request"}],
     rules: ([{type: "deletion"}, {type: "non_fast_forward"}, {type: "required_linear_history"},
       {type: "pull_request", parameters: {required_approving_review_count: $cfg.approvals,
         dismiss_stale_reviews_on_push: $fresh, require_code_owner_review: $cfg.code_owners,
         require_last_push_approval: $fresh, required_review_thread_resolution: false,
         allowed_merge_methods: ["squash"]}}]
       + if ($checks | length) > 0 then [{type: "required_status_checks", parameters: {
           strict_required_status_checks_policy: true, do_not_enforce_on_create: false,
           required_status_checks: $checks}}] else [] end)}')"

  # Release tags: nobody moves or deletes one, with no bypass at all. Creating one is
  # restricted too, and only the release App, which release-please runs as, may: a
  # bypass covers its own ruleset only, so creation has a ruleset of its own and the
  # App's bypass reaches nothing else. GitHub takes an App installed on the
  # organisation as a bypass actor, by its App ID, but not the built-in Actions app.
  # Refused while release_app_id is a placeholder: a ruleset with no bypass would stop
  # every release, and one naming the wrong App would let it tag.
  tag_rules=$(jq -c '[.tags[] | "refs/tags/\(.)"] | {ref_name: {include: ., exclude: []}}' <<<"$cfg")
  ruleset "release tags" "$(jq -cn --argjson c "$tag_rules" '
    {name: "release tags", target: "tag", enforcement: "active", conditions: $c, bypass_actors: [],
     rules: [{type: "deletion"}, {type: "update"}, {type: "non_fast_forward"}]}')"
  if [ -n "$app_refused" ]; then
    say "refused: ruleset 'release tag creation': $app_refused" >&2
    failed=true
    repo_failed=true
  else
    ruleset "release tag creation" "$(jq -cn --argjson c "$tag_rules" --argjson app "$release_app_id" '
      {name: "release tag creation", target: "tag", enforcement: "active", conditions: $c,
       bypass_actors: [{actor_id: $app, actor_type: "Integration", bypass_mode: "always"}],
       rules: [{type: "creation"}]}')"
  fi

  # Every ruleset on the repository, not only the three above, must be enforced. A
  # ruleset someone set to disabled (or evaluate) by hand protects nothing, and a
  # disabled `main` once went unnoticed. The list is read after the writes above, so
  # an apply reports only what it didn't set itself.
  enforcement_report

  # Merging: squash only, the PR title as the commit, branches deleted, auto-merge allowed.
  want='{"allow_squash_merge":true,"allow_merge_commit":false,"allow_rebase_merge":false,"squash_merge_commit_title":"PR_TITLE","delete_branch_on_merge":true,"allow_auto_merge":true}'
  if [ "$admin" = true ]; then
    reconcile "merge settings" "$want" "$(jq -c --argjson w "$want" 'with_entries(select(.key as $k | $w | has($k)))' <<<"$info")" write_merge
  else
    not_checked "merge settings"
  fi

  # Issues: on only where the list says (paddock, where every issue lives).
  if [ "$(jq -r .issues <<<"$cfg")" != null ]; then
    reconcile "issues" "$(jq -c '{has_issues: .issues}' <<<"$cfg")" "$(jq -c '{has_issues}' <<<"$info")" \
      write_issues "$(jq -r .issues <<<"$cfg")"
  fi

  # Security: secret scanning with push protection, Dependabot alerts, private reports.
  if [ "$admin" = true ]; then
    have=$(jq -c --arg alerts "$(gh api "repos/$repo/vulnerability-alerts" >/dev/null 2>&1 && echo true || echo false)" \
      --argjson pvr "$(read_or "repos/$repo/private-vulnerability-reporting" '{}')" '
      {secret_scanning: .security_and_analysis.secret_scanning.status,
       push_protection: .security_and_analysis.secret_scanning_push_protection.status,
       dependabot_alerts: ($alerts == "true"),
       private_vulnerability_reporting: ($pvr.enabled // false)}' <<<"$info")
    reconcile "security" \
      '{"secret_scanning":"enabled","push_protection":"enabled","dependabot_alerts":true,"private_vulnerability_reporting":true}' \
      "$have" write_security
  else
    not_checked "security"
  fi

  # Actions: GitHub's actions plus the ones above, pinned by SHA; a read-only token
  # that can approve PRs only with actions_prs; outside contributors' PRs wait for
  # approval.
  if [ "$admin" = true ]; then
    want=$(jq -cn --argjson cfg "$cfg" --argjson public "$public" '
      if $cfg.actions then
        {enabled: true, allowed_actions: "selected", sha_pinning_required: true,
         github_owned_allowed: true, verified_allowed: false,
         patterns_allowed: ($ARGS.positional | sort), default_workflow_permissions: "read",
         can_approve_pull_request_reviews: $cfg.actions_prs}
        + if $public then {fork_pr_approval: "all_external_contributors"} else {} end
      else {enabled: false} end' --args "${allowed_actions[@]}")
    have=$(jq -cn --argjson cfg "$cfg" --argjson public "$public" \
      --argjson perm "$(read_or "repos/$repo/actions/permissions" '{}')" \
      --argjson sel "$(read_or "repos/$repo/actions/permissions/selected-actions" '{}')" \
      --argjson wf "$(read_or "repos/$repo/actions/permissions/workflow" '{}')" \
      --argjson fork "$(read_or "repos/$repo/actions/permissions/fork-pr-contributor-approval" '{}')" '
      if $cfg.actions then
        {enabled: $perm.enabled, allowed_actions: $perm.allowed_actions,
         sha_pinning_required: $perm.sha_pinning_required,
         github_owned_allowed: $sel.github_owned_allowed, verified_allowed: $sel.verified_allowed,
         patterns_allowed: ($sel.patterns_allowed // [] | sort),
         default_workflow_permissions: $wf.default_workflow_permissions,
         can_approve_pull_request_reviews: $wf.can_approve_pull_request_reviews}
        + if $public then {fork_pr_approval: $fork.approval_policy} else {} end
      else {enabled: $perm.enabled} end')
    reconcile "Actions" "$want" "$have" write_actions "$cfg" "$public"
  else
    not_checked "Actions"
  fi

  # CODEOWNERS is a file, changed by pull request, so this only reports it.
  if [ "$(jq -r .code_owners <<<"$cfg")" = true ]; then
    problem=$(codeowners_problem)
    if [ -z "$problem" ]; then
      :
    elif [ "$mode" = check ]; then
      say "drift: code owners: $problem"
      drift=true
    else
      say "warning: $problem: fix it by pull request"
    fi
  fi

  # release: deployments only from main and the release tags, each waiting for the
  # maintainer; admins can't skip it. Prevent self-review follows the list (see the
  # header for why it is off by default). It holds no registry secrets: images go to
  # ghcr.io with the job's own token.
  if [ "$(jq -r .publishes <<<"$cfg")" = true ]; then
    if [ "$admin" = true ]; then
      want=$(jq -c --arg m "$maintainer" '{reviewers: [$m | ascii_downcase], prevent_self_review: .prevent_self_review,
        can_admins_bypass: false, protected_branches: false, custom_branch_policies: true,
        policies: (["branch:main"] + [.tags[] | "tag:\(.)"] | sort)}' <<<"$cfg")
      if ! pol=$(branch_policies 2>/dev/null); then pol='{}'; fi
      have=$(jq -c --argjson pol "$pol" '
        if . == {} then {} else
        ([.protection_rules[]? | select(.type == "required_reviewers")][0] // {}) as $r
        | {reviewers: ([$r.reviewers[]? | "\(.reviewer.login)" | ascii_downcase] | sort),
           prevent_self_review: ($r.prevent_self_review // false),
           protected_branches: .deployment_branch_policy.protected_branches,
           custom_branch_policies: .deployment_branch_policy.custom_branch_policies,
           policies: ([$pol.branch_policies[]? | "\(.type):\(.name)"] | sort)}
          + if has("can_admins_bypass") then {can_admins_bypass} else {} end end' \
        <<<"$(read_or "repos/$repo/environments/release" '{}')")
      # can_admins_bypass isn't in GitHub's published REST description, though live
      # environment responses carry it and it is the setting that stops an admin
      # skipping the maintainer's approval. It is still written; if a response ever
      # leaves it out, it is reported as not checked rather than as drift for good.
      if [ "$have" != '{}' ] && ! jq -e 'has("can_admins_bypass")' >/dev/null <<<"$have"; then
        want=$(jq -c 'del(.can_admins_bypass)' <<<"$want")
        say "release environment: can_admins_bypass: not checked (GitHub didn't return it)"
      fi
      reconcile "release environment" "$want" "$have" write_environment "$cfg"
    else
      not_checked "release environment"
    fi
  fi

  # CodeQL's default setup for the repository's languages.
  if [ "$(jq -r .codeql <<<"$cfg")" != null ]; then
    if [ "$admin" = true ]; then
      want=$(jq -c 'if (.codeql | length) > 0 then {state: "configured", languages: (.codeql | sort)}
                    else {state: "not-configured"} end' <<<"$cfg")
      have=$(jq -c --argjson cfg "$cfg" 'if ($cfg.codeql | length) > 0
        then {state, languages: (.languages // [] | sort)} else {state} end' \
        <<<"$(read_or "repos/$repo/code-scanning/default-setup" '{}')")
      reconcile "CodeQL" "$want" "$have" write_codeql "$cfg"
    else
      not_checked "CodeQL"
    fi
  fi

  if [ "$mode" = apply ] && [ "$changed" = false ] && [ "$repo_failed" = false ]; then
    say "no change"
  fi
}

repo=""
drift=false
changed=false
incomplete=false
failed=false
repo_failed=false
# gh's stderr for the last call, read by api() when GitHub refuses one.
gh_err=$(mktemp)
trap 'rm -f "$gh_err"' EXIT
# The list is read on fd 3, so nothing in the loop can swallow it from stdin.
while IFS= read -r cfg <&3; do preflight "$cfg"; done 3<<<"$targets"
while IFS= read -r cfg <&3; do protect "$cfg"; done 3<<<"$targets"
if [ "$failed" = true ]; then
  echo "failed: GitHub refused a write, or a setting was refused, in at least one repository; the messages are above" >&2
fi
if [ "$mode" = check ]; then
  if [ "$drift" = true ] || [ "$failed" = true ]; then exit 1; fi
  if [ "$incomplete" = true ]; then
    echo "no drift in what could be read: --check is only complete with an admin login"
  else
    echo "no drift"
  fi
fi
[ "$failed" = false ] || exit 1
