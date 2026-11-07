#!/usr/bin/env bash
# Create or update, or check, the labels every repository needs: the ones the merge
# ladder reads on pull requests (`hold`, `re-review`) and, in paddock, on issues
# (`agent-ready`). scripts/labels.json lists them; scripts/repos.json says which
# repositories there are.
#
# Run by the maintainer, with his own gh login (`make labels`, `make labels-check`).
# Never by an agent: it changes the labels of every repository, and the ladder reads
# them as the maintainer's word.
#
#   scripts/labels.sh [--check] [--list FILE] [--labels FILE]
#
# Each label in scripts/labels.json has:
#   name         the label's name, as GitHub shows it. Letters, digits, spaces and
#                `:._-` only, so its name goes in a URL path with its spaces encoded.
#   color        six hex digits, lowercase, without `#`.
#   description  what the label means, in GitHub's limit of 100 characters.
# and may have:
#   only         the repositories (owner/repo, each in scripts/repos.json) that take
#                this label; every repository in the list takes it when left out.
#
# --check reads every repository's labels and prints, for each required label, that
# it is present with the right colour and description, present but different (what
# differs), or missing; it changes nothing and exits 1 when anything differs or a
# read was refused.
#
# Without --check it creates each missing label and updates each one that differs
# (its colour and description), then reads everything back and checks it again. It
# never deletes or renames a label, and never touches a label the list doesn't name:
# a label with the right name in another case (`Hold` for `hold`) is reported and
# updated under the name GitHub has, never renamed. A write GitHub refuses doesn't stop
# the run: the script prints GitHub's message, carries on, and exits 1 at the end.
#
# Before anything is written, every repository in the list is read: a repository this
# login can't read (gh not logged in, a login that expired, a repository gone) or,
# for an apply, can't push to, stops the run with exit 2 and nothing changed anywhere.
# A bad list stops it the same way.
#
# Needs: gh and jq.
set -euo pipefail

usage() {
  echo "usage: labels.sh [--check] [--list FILE] [--labels FILE]" >&2
  exit 2
}
fail() { echo "$*" >&2; exit 2; }

here="$(cd "$(dirname "$0")" && pwd)"
list="$here/repos.json"
labels="$here/labels.json"
mode=apply
while [ $# -gt 0 ]; do
  case "$1" in
    --check) mode=check; shift ;;
    --list) [ $# -ge 2 ] || usage; list=$2; shift 2 ;;
    --labels) [ $# -ge 2 ] || usage; labels=$2; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

[ -f "$list" ] || fail "no list at $list"
repos=$(jq -c '.repositories | keys' "$list" 2>/dev/null) || fail "$list is not the repository list"
[ "$(jq 'length' <<<"$repos")" -gt 0 ] || fail "$list names no repository"

# Everything the label list must satisfy, checked before anything is read or changed.
# shellcheck disable=SC2016 # a jq program: jq expands its $names, not bash
VALIDATE='
def fields: ["name","color","description"];
def one:
  if type != "object" then "each label must be an object" else . as $l |
    ($l.name | if type == "string" then . else "a label" end) as $n |
    ((keys - fields - ["only"])[] | "\($n): unknown field \(.)"),
    ((fields - keys)[] | "\($n): missing field \(.)"),
    (if ($l.name | type) != "string" or ($l.name | test("^[A-Za-z0-9][A-Za-z0-9 :._-]*$") | not)
     then "\($l.name | tojson): name must be letters, digits, spaces and :._- only" else empty end),
    (if ($l.color | type) != "string" or ($l.color | test("^[0-9a-f]{6}$") | not)
     then "\($n): color must be six lowercase hex digits without #" else empty end),
    (if ($l.description | type) != "string" or ($l.description | length) == 0 or ($l.description | length) > 100
     then "\($n): description must be 1 to 100 characters" else empty end),
    (if has("only") then
       (if ($l.only | type) != "array" or ($l.only | length) == 0 or any($l.only[]; type != "string")
        then "\($n): only must list at least one repository"
        else ($l.only[] | select(. as $r | $repos | map(ascii_downcase) | index($r | ascii_downcase) | not)
              | "\($n): only names \(.), which is not in the repository list")
        end)
     else empty end)
  end;
if type != "object" then "the file must be a JSON object" else
  ((keys - ["labels"])[] | "unknown top-level field \(.)"),
  (if (.labels | type) != "array" or (.labels | length) == 0 then "labels must list at least one label"
   else (.labels[] | one),
        (if ([.labels[] | (.name? | strings) // "" | ascii_downcase] | length) != ([.labels[] | (.name? | strings) // "" | ascii_downcase] | unique | length)
         then "a label is listed twice" else empty end)
   end)
end'

[ -f "$labels" ] || fail "no label list at $labels"
wanted=$(cat "$labels")
jq -e . >/dev/null 2>&1 <<<"$wanted" || fail "$labels is not JSON"
problems=$(jq -r --argjson repos "$repos" "$VALIDATE" <<<"$wanted")
[ -z "$problems" ] || fail "$labels: $problems"

say() { printf '%s: %s\n' "$repo" "$*"; }
# api ARGS...: gh api for the writes and the reads that may fail without stopping the
# run. On a failure it prints the call and GitHub's message and returns non-zero.
api() {
  local body status=0 what="" msg a
  body=$(gh api "$@" 2>"$gh_err") || status=$?
  if [ "$status" -eq 0 ]; then
    [ -z "$body" ] || printf '%s\n' "$body"
    return 0
  fi
  for a in "$@"; do
    case "$a" in
      -X|GET|POST|PATCH|repos/*) what+="${what:+ }${a%%\?*}" ;;
    esac
  done
  msg=$(jq -rs '[.[] | objects | (.message // empty), (.errors[]?
    | if type == "string" then . else (.message // tostring) end)] | join("; ")' <<<"$body" 2>/dev/null) || msg=""
  [ -n "$msg" ] || msg=$(tr '\n' ' ' <"$gh_err")
  say "error: gh api $what: ${msg:-no message (exit $status)}" >&2
  return "$status"
}

# The labels the repository must have, one compact JSON object each.
wanted_for() {
  jq -c --arg r "$1" '.labels[] | select((.only // [$r]) | map(ascii_downcase) | index($r | ascii_downcase))
    | {name, color, description}' <<<"$wanted"
}
# A label's name goes in the path, so its space is encoded.
encode() { printf '%s' "${1// /%20}"; }

# Reads only. Refuses, before anything changes anywhere, a repository that can't be
# read or, for an apply, pushed to.
preflight() {
  local info
  repo=$1
  info=$(gh api "repos/$repo" 2>/dev/null) || fail "$repo: can't read the repository: deleted, or not visible to this login? Fix scripts/repos.json or the gh login (gh auth status)"
  [ "$mode" = check ] && return 0
  [ "$(jq -r '.permissions.push // false' <<<"$info")" = true ] ||
    fail "$repo: applying needs push access to the repository; --check reads without it"
}

# have_labels: every label of the repository, as one JSON array, or returns non-zero.
have_labels() {
  api --paginate "repos/$repo/labels?per_page=100" | jq -cs 'add // []'
}
# compare WANT HAVE: prints what differs between the wanted label and the one GitHub
# has (nothing when they agree). GitHub keeps colours lowercase and may return a null
# description.
compare() {
  jq -nr --argjson w "$1" --argjson h "$2" '
    (if ($h.color // "" | ascii_downcase) != $w.color then "color want \($w.color), have \($h.color // "" | tojson)" else empty end),
    (if ($h.description // "") != $w.description then "description want \($w.description | tojson), have \($h.description // "" | tojson)" else empty end)'
}

check_repo() {
  local have want name current diff line
  repo=$1
  if ! have=$(have_labels); then
    say "labels: not checked: GitHub refused a read (its message is above)" >&2
    failed=true
    return 0
  fi
  while IFS= read -r want <&4; do
    name=$(jq -r .name <<<"$want")
    current=$(jq -c --arg n "$name" '[.[] | select((.name | ascii_downcase) == ($n | ascii_downcase))][0] // empty' <<<"$have")
    if [ -z "$current" ]; then
      say "drift: label '$name': missing"
      drift=true
      continue
    fi
    diff=$(compare "$want" "$current")
    if [ "$(jq -r .name <<<"$current")" != "$name" ]; then
      say "drift: label '$name': named '$(jq -r .name <<<"$current")' on GitHub (never renamed here: rename it by hand)"
      drift=true
    fi
    if [ -z "$diff" ]; then
      say "label '$name': ok"
    else
      while IFS= read -r line; do say "drift: label '$name': $line"; done <<<"$diff"
      drift=true
    fi
  done 4< <(wanted_for "$repo")
}

apply_repo() {
  local have want name current diff existing
  repo=$1
  if ! have=$(have_labels); then
    say "labels: not changed: GitHub refused a read (its message is above)" >&2
    failed=true
    return 0
  fi
  while IFS= read -r want <&4; do
    name=$(jq -r .name <<<"$want")
    current=$(jq -c --arg n "$name" '[.[] | select((.name | ascii_downcase) == ($n | ascii_downcase))][0] // empty' <<<"$have")
    if [ -z "$current" ]; then
      if api -X POST "repos/$repo/labels" --input - <<<"$want" >/dev/null; then
        say "label '$name': created"
      else
        say "label '$name': not created: GitHub refused a write (its message is above)" >&2
        failed=true
      fi
      continue
    fi
    diff=$(compare "$want" "$current")
    existing=$(jq -r .name <<<"$current")
    [ "$existing" = "$name" ] || say "label '$name': named '$existing' on GitHub, left as it is (never renamed here)"
    if [ -z "$diff" ]; then
      say "label '$name': unchanged"
    elif jq -c '{color, description}' <<<"$want" |
         api -X PATCH "repos/$repo/labels/$(encode "$existing")" --input - >/dev/null; then
      say "label '$name': changed"
    else
      say "label '$name': not changed: GitHub refused a write (its message is above)" >&2
      failed=true
    fi
  done 4< <(wanted_for "$repo")
}

repo=""
drift=false
failed=false
# gh's stderr for the last call, read by api() when GitHub refuses one.
gh_err=$(mktemp)
trap 'rm -f "$gh_err"' EXIT
# The lists are read on fd 3, so nothing in the loops can swallow them from stdin.
while IFS= read -r r <&3; do preflight "$r"; done 3< <(jq -r '.[]' <<<"$repos")
if [ "$mode" = apply ]; then
  while IFS= read -r r <&3; do apply_repo "$r"; done 3< <(jq -r '.[]' <<<"$repos")
  echo "read back after the writes:"
fi
while IFS= read -r r <&3; do check_repo "$r"; done 3< <(jq -r '.[]' <<<"$repos")
if [ "$failed" = true ]; then
  echo "failed: GitHub refused a read or a write in at least one repository; the messages are above" >&2
  exit 1
fi
if [ "$drift" = true ]; then
  [ "$mode" = check ] || echo "the labels above still differ after the apply" >&2
  exit 1
fi
echo "no drift"
