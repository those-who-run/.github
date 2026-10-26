#!/usr/bin/env bash
# Set each issue card's Status on the organisation's project board, Race Control, from
# its issue. The bot account can't write to the project, so the maintainer runs this
# after each triage session (`make board`, or `make board-check` to see the moves
# first).
#
# Run by the maintainer, with his own gh login (it needs the `project` scope). Never by
# an agent: it changes the organisation's project board.
#
#   scripts/board-sync.sh [--dry-run] [--owner ORG] [--project N] [--field NAME]
#
# Each issue card's Status follows its issue:
#   closed                            -> Done
#   open, with the label needs triage -> Needs triage
#   any other open issue              -> Ready
# except that a card in any column other than those three (In progress, In review, or
# one added later) stays where it is unless its issue is closed: only Done overrides
# them, as the work on it is under way. A card with no Status yet is set. A card
# already in the right column is left alone, and so are pull requests and draft issues.
#
# The Status field (--field, Status by default) and its options are looked up by name
# on every run, never by ID: the option IDs change when someone recreates the options.
# All five options must exist (Needs triage, Ready, In progress, In review, Done), so a
# renamed column stops the run rather than letting its cards be moved. The issues' state and
# labels are read in bulk, one `gh issue list` per repository on the board.
#
# --dry-run prints the moves it would make and changes nothing. Either way it prints
# one line per move and a summary. It exits 2 before any move when the board or the
# field can't be read or an option is missing, and 1 at the end if GitHub refused a
# move or a card's issue couldn't be found.
#
# Needs: gh and jq.
set -euo pipefail

owner=those-who-run
project=2
dry_run=false
FIELD="Status"
usage() {
  echo "usage: board-sync.sh [--dry-run] [--owner ORG] [--project N] [--field NAME]" >&2
  exit 2
}
fail() { echo "$*" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=true; shift ;;
    --owner) [ $# -ge 2 ] || usage; owner=$2; shift 2 ;;
    --project) [ $# -ge 2 ] || usage; project=$2; shift 2 ;;
    --field) [ $# -ge 2 ] && [ -n "$2" ] || usage; FIELD=$2; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done
case "$project" in
  ''|*[!0-9]*) fail "--project takes the project's number: $project" ;;
esac

DONE="Done"
TRIAGE="Needs triage"
READY="Ready"
# The columns the board has besides those three, which the script never sets. Each
# must exist, so a rename shows here and not as cards moved out of it.
IN_PROGRESS="In progress"
IN_REVIEW="In review"
TRIAGE_LABEL="needs triage"
# gh's lists stop at --limit; a list that reaches it may be cut short, so the run
# stops rather than miss cards or issues. BOARD_ITEM_LIMIT lowers the card limit, for
# the tests.
ITEM_LIMIT=${BOARD_ITEM_LIMIT:-5000}
case "$ITEM_LIMIT" in
  ''|*[!0-9]*|0) fail "BOARD_ITEM_LIMIT takes a number above 0: $ITEM_LIMIT" ;;
esac
ISSUE_LIMIT=5000

# The project's node ID and the Status field, by name.
project_id=$(gh project view "$project" --owner "$owner" --format json | jq -er .id) ||
  fail "can't read project $project of $owner (gh needs the project scope: gh auth refresh -s project)"
fields=$(gh project field-list "$project" --owner "$owner" --format json --limit 100) ||
  fail "can't read the fields of project $project"
field=$(jq -c --arg f "$FIELD" '[.fields[] | select(.name == $f)] | if length == 1 then .[0] else empty end' <<<"$fields")
[ -n "$field" ] || fail "project $project has no single field named $FIELD"
[ "$(jq -r '.options | type' <<<"$field")" = array ] || fail "$FIELD is not a single-select field"
field_id=$(jq -r .id <<<"$field")
for name in "$TRIAGE" "$READY" "$IN_PROGRESS" "$IN_REVIEW" "$DONE"; do
  jq -e --arg n "$name" '[.options[] | select(.name == $n)] | length == 1' >/dev/null <<<"$field" ||
    fail "$FIELD has no option named \"$name\" (it has: $(jq -r '[.options[].name] | join(", ")' <<<"$field"))"
done
option_id() { jq -r --arg n "$1" '.options[] | select(.name == $n) | .id' <<<"$field"; }

items=$(gh project item-list "$project" --owner "$owner" --format json --limit "$ITEM_LIMIT") ||
  fail "can't read the cards of project $project"
total=$(jq '.totalCount // (.items | length)' <<<"$items")
[ "$(jq '.items | length' <<<"$items")" -ge "$total" ] ||
  fail "project $project has $total cards, more than the $ITEM_LIMIT read: raise BOARD_ITEM_LIMIT"

# The issue cards: item ID, repository, number and current Status ("" for none). gh
# names a field's value after the field with only its first letter lower-cased (its
# camelCase), so "Status" is "status" and "Board Status" is "board Status".
key=$(jq -rn --arg f "$FIELD" '"\($f[0:1] | ascii_downcase)\($f[1:])"')
cards=$(jq -c --arg k "$key" '[.items[] | select(.content.type == "Issue")
  | {id, repo: .content.repository, number: .content.number, status: (.[$k] // "")}]' <<<"$items")
others=$(jq '[.items[] | select(.content.type != "Issue")] | length' <<<"$items")

# Every issue in each repository on the board, as {"owner/repo#N": {state, triage}}.
issues='{}'
while IFS= read -r repo; do
  [ -n "$repo" ] || continue
  list=$(gh issue list --repo "$repo" --state all --json number,state,labels --limit "$ISSUE_LIMIT") ||
    fail "can't list the issues of $repo"
  [ "$(jq length <<<"$list")" -lt "$ISSUE_LIMIT" ] ||
    fail "$repo has $ISSUE_LIMIT issues or more: raise ISSUE_LIMIT"
  issues=$(jq -c --arg r "$repo" --arg t "$TRIAGE_LABEL" --argjson have "$issues" '
    $have + (map({key: "\($r)#\(.number)",
                  value: {state, triage: ([.labels[]?.name] | index($t) != null)}}) | from_entries)' <<<"$list")
done < <(jq -r '[.[].repo] | unique[]' <<<"$cards")

# The plan: each card with the column it belongs in, and what to do about it.
# Only a card in one of the three columns the script sets, or with no Status, moves to
# another of them; any other column moves only to Done.
plan=$(jq -c --arg done_ "$DONE" --arg triage "$TRIAGE" --arg ready "$READY" '
  .[] | . as $c | $issues["\(.repo)#\(.number)"] as $i
  | if $i == null then $c + {action: "missing"}
    else ($c + {want: (if $i.state == "CLOSED" then $done_ elif $i.triage then $triage else $ready end)})
      | if .status == .want then . + {action: "right"}
        elif .want != $done_ and (.status | IN($done_, $triage, $ready, "") | not)
          then . + {action: "kept"}
        else . + {action: "move"} end
    end' --argjson issues "$issues" <<<"$cards")

moved=0 right=0 kept=0 missing=0 refused=0
verb=$([ "$dry_run" = true ] && echo "would move" || echo "moved")
while IFS= read -r card; do
  [ -n "$card" ] || continue
  ref=$(jq -r '"\(.repo)#\(.number)"' <<<"$card")
  case "$(jq -r .action <<<"$card")" in
    right) right=$((right + 1)) ;;
    kept) kept=$((kept + 1)) ;;
    missing)
      echo "$ref: not found among the repository's issues; left alone" >&2
      missing=$((missing + 1)) ;;
    move)
      from=$(jq -r '.status | if . == "" then "no status" else . end' <<<"$card")
      want=$(jq -r .want <<<"$card")
      if [ "$dry_run" = false ] &&
         ! gh project item-edit --id "$(jq -r .id <<<"$card")" --project-id "$project_id" \
           --field-id "$field_id" --single-select-option-id "$(option_id "$want")" >/dev/null; then
        echo "$ref: not moved from $from to $want: GitHub refused it (its message is above)" >&2
        refused=$((refused + 1))
        continue
      fi
      echo "$ref: $verb: $from -> $want"
      moved=$((moved + 1)) ;;
  esac
done <<<"$plan"

echo "$verb $moved, already right $right, kept in progress, review or another column $kept, not found $missing, refused $refused; $others cards that aren't issues left alone"
[ "$missing" -eq 0 ] && [ "$refused" -eq 0 ] || exit 1
