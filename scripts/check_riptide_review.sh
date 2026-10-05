#!/usr/bin/env bash
#
# scripts/check_riptide_review.sh — the `riptide-review-required` CI gate.
#
# Rule: a PR may not merge while a Riptide review's findings stand unaddressed. The
# gate decides that from the PR's comments and commits alone, offline.
#
# What counts as an answer to a review
# ------------------------------------
#   1. the newest review that raises no findings (its sign-off, no 🔴/🟡 rows) — a
#      review that re-read the code and had nothing to say; or
#   2. at least one commit AFTER that review whose changed files include a file the
#      findings name. A commit that changes something else is not an answer: the
#      previous rule accepted any commit at all, so a findings review could be
#      greened by an unrelated push while every finding stood.
#
# A finding row that names no file (`| 🟡 | title | — |`) cannot be matched against a
# commit's files, so its presence relaxes rule 2 back to "some commit landed" and the
# run says so — an unverifiable row must not be silently treated as answered.
#
# The Companion's deterministic pass
# ----------------------------------
# `## Riptide Pass:` is not a review — it is the deterministic pre-pass, and it says
# nothing about an LLM review's findings. It still satisfies this gate (a PR must not
# be blocked forever by a check nothing can clear), but the run reports that state
# explicitly instead of letting a green check imply a review happened.
#
# Exclusions are anchored to the comment's FIRST LINE. A review is free to *quote*
# `## Riptide Pass:` or `## ✨ Review Required` while discussing them — matching on
# the whole body dropped such a review and let the gate fall back to an older
# comment, greening a check while that review's findings stood.
#
# Usage
# -----
#     scripts/check_riptide_review.sh <data-file>
#
# The comment and commit data is produced by the workflow from the GitHub API (see
# .github/workflows/riptide-review-required.yml), so this script needs no checkout,
# no network and no token of its own — which is what makes it testable. Format, one
# record per line, `review` and `commit` starting a new record:
#
#     review <comment_id> <created_at-iso8601> <base64 of the comment body>
#     commit <sha>        <committer-date-iso8601>
#     file   <path changed by that commit>          # zero or more, per commit
#
# ISO-8601 timestamps compare correctly as strings. Blank lines and lines starting
# with `#` are ignored.
#
# Exit codes
# ----------
# 0   the PR has an answer to its newest findings (or has none, or only a pass)
# 1   a findings review stands with no commit touching the files it names
# 2   the check could not be evaluated (missing/unreadable data file, or a
#     malformed record: a `review` line without a body, or an undecodable body)

set -euo pipefail

DATA="${1:-}"
if [ -z "$DATA" ]; then
  echo "usage: check_riptide_review.sh <data-file>" >&2
  exit 2
fi
if [ ! -r "$DATA" ]; then
  echo "cannot read data file: $DATA" >&2
  exit 2
fi

# Report to the log, and to the check's step summary when GitHub provides one, so a
# green run states WHICH rule satisfied it.
note() {
  printf '%s\n' "$*"
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '%s\n' "$*" >>"$GITHUB_STEP_SUMMARY"
  fi
}

# One machine-readable line naming the comment this run judged:
#   chosen: review <id>   the newest review
#   chosen: pass <ts>     no review; satisfied by the Companion's deterministic pass
#   chosen: none          nothing to judge
# The tests that lock the selector read this rather than matching prose.
chosen() {
  printf 'chosen: %s\n' "$*"
}

fail() {
  printf '::error::%s\n' "$*"
  note "❌ $*"
  exit 1
}

pass() {
  note "✅ $*"
  exit 0
}

die2() {
  printf '%s\n' "$*" >&2
  exit 2
}

# ── Records ───────────────────────────────────────────────────────────────────

REVIEW_IDS=()
REVIEW_TS=()
REVIEW_BODIES=()
COMMIT_SHAS=()
COMMIT_TS=()
declare -A COMMIT_FILES=()
current_commit=""

while IFS= read -r line; do
  case "$line" in
    ""|\#*) continue ;;
  esac
  field="${line%% *}"
  rest="${line#* }"
  case "$field" in
    review)
      id="${rest%% *}"
      rest="${rest#* }"
      ts="${rest%% *}"
      body64="${rest#* }"
      if [ "$body64" = "$rest" ] || [ -z "$ts" ] || [ -z "$id" ]; then
        die2 "malformed review record (need: review <id> <created_at> <base64 body>): $line"
      fi
      body="$(printf '%s' "$body64" | base64 -d 2>/dev/null)" \
        || die2 "review $id has an undecodable body"
      REVIEW_IDS+=("$id")
      REVIEW_TS+=("$ts")
      REVIEW_BODIES+=("$body")
      current_commit=""
      ;;
    commit)
      sha="${rest%% *}"
      ts="${rest#* }"
      if [ -z "$sha" ] || [ "$ts" = "$rest" ] || [ -z "$ts" ]; then
        die2 "malformed commit record (need: commit <sha> <committer-date>): $line"
      fi
      COMMIT_SHAS+=("$sha")
      COMMIT_TS+=("$ts")
      COMMIT_FILES["$sha"]=""
      current_commit="$sha"
      ;;
    file)
      # Files belong to the preceding commit; a `file` before any `commit` is a
      # malformed record rather than something to guess at.
      if [ -z "$current_commit" ]; then
        die2 "file record before any commit record: $line"
      fi
      COMMIT_FILES["$current_commit"]+="$rest"$'\n'
      ;;
    *)
      die2 "unknown record field '$field': $line"
      ;;
  esac
done <"$DATA"

# ── Select the review this gate judges ────────────────────────────────────────

first_line() { printf '%s' "${1%%$'\n'*}"; }

# Newest wins, by timestamp rather than by position in the file. Timestamps are
# whole seconds, so two comments can share one; the tie goes to the higher comment
# id, because the API hands comments back oldest-first and a strict `>` kept the
# FIRST of the two — a clean review posted in the same second as a later findings
# review greened the check while those findings stood.
newer_than() {
  local ts="$1" id="$2" best_ts="$3" best_id="$4"
  if [[ "$ts" != "$best_ts" ]]; then
    [[ "$ts" > "$best_ts" ]]
  elif [[ "$id" =~ ^[0-9]+$ && "$best_id" =~ ^[0-9]+$ ]]; then
    (( id > best_id ))
  else
    [[ "$id" > "$best_id" ]]
  fi
}

review_index=-1
pass_index=-1

for i in "${!REVIEW_IDS[@]}"; do
  body="${REVIEW_BODIES[$i]}"
  head="$(first_line "$body")"

  # Anchored to the first line: a review may quote these headings and stay a review.
  case "$head" in
    *"Review Required"*) continue ;;
  esac
  if [[ "$head" == *"Riptide Pass:"* ]]; then
    if [ "$pass_index" -eq -1 ] || newer_than "${REVIEW_TS[$i]}" "${REVIEW_IDS[$i]}" \
      "${REVIEW_TS[$pass_index]}" "${REVIEW_IDS[$pass_index]}"; then
      pass_index="$i"
    fi
    continue
  fi

  # Inclusion is anchored to the first line too, for the same reason as the
  # exclusions: a later non-review comment whose body merely NAMES a marker —
  # a fix summary quoting the sign-off, a session log — would be selected as
  # the newest review and, carrying no severity rows, report clean with no
  # commit required, clearing the gate without a review ever existing. Every
  # real review starts `## Review:` (assemble_review.py's first emitted
  # part); the `Riptide Review ·` sign-off is still required in the body,
  # which no fix summary or log carries.
  if [[ "$head" == "## Review:"* && "$body" == *"Riptide Review ·"* ]]; then
    if [ "$review_index" -eq -1 ] || newer_than "${REVIEW_TS[$i]}" "${REVIEW_IDS[$i]}" \
      "${REVIEW_TS[$review_index]}" "${REVIEW_IDS[$review_index]}"; then
      review_index="$i"
    fi
  fi
done

if [ "$review_index" -eq -1 ]; then
  if [ "$pass_index" -eq -1 ]; then
    chosen none
    fail "No Riptide review found on this PR. Comment '@riptide-bot review' to trigger one."
  fi
  chosen "pass ${REVIEW_TS[$pass_index]}"
  note "ℹ️ No deep review on this PR: satisfied by the Companion's deterministic pass at ${REVIEW_TS[$pass_index]}."
  note "   That pass runs the deterministic scan only — it is not a review of this head's code."
  pass "Deterministic pass present, and no review has findings."
fi

chosen "review ${REVIEW_IDS[$review_index]}"

REVIEW_BODY="${REVIEW_BODIES[$review_index]}"
REVIEW_TIME="${REVIEW_TS[$review_index]}"

# ── Findings? ────────────────────────────────────────────────────────────────
# One predicate for both the "has findings" test and the row parser: a severity
# row starts a line. While the two disagreed — the test matching `| 🟡` anywhere,
# the parser only at column 0 — a review that quoted a row counted as
# findings-bearing with nothing parsed, and the run settled for any commit.

ROW_RE='^[[:space:]]*\|[[:space:]]*(🔴|🟡)'

# A severity row quoted inside a fenced code block is documentation (a format
# example), not a finding — but the row predicate cannot tell a fence apart,
# so a quoted row's File cell would be demanded as a path no push can satisfy.
# Drop fenced regions before collecting rows.
strip_fenced() {
  python3 - "$1" << 'PYEOF'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
open(sys.argv[1], "w", encoding="utf-8").write(stripped)
PYEOF
}

rows_file="$(mktemp)"
printf '%s' "$REVIEW_BODY" > "$rows_file"
strip_fenced "$rows_file"
rows="$(grep -E "$ROW_RE" "$rows_file" || true)"
rm -f "$rows_file"
row_count="$(printf '%s\n' "$rows" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"

if [ "$row_count" -eq 0 ]; then
  pass "Review ${REVIEW_IDS[$review_index]} is clean — no follow-up commit required."
fi

# ── A commit after the review must touch a file the findings name ─────────────

# The File cell is a row's LAST cell. Read that cell, not the row's last code
# span: when a row names no file, its title's inline `code` is not a path, and
# demanding a commit that touches a symbol cannot be satisfied by any push.
# `named_count` counts ROWS that name a file (before dedup), so two rows in one
# file are two named rows — counting unique paths instead relaxed the rule back
# to "any commit" for exactly the reviews that repeat a file.
named_refs="$(
  printf '%s\n' "$rows" | while IFS= read -r row; do
    [ -n "$row" ] || continue
    cell="$(printf '%s' "$row" | awk -F'|' '{print $(NF-1)}')"
    # The cell's first code span IS the path: `_build_severity_table` renders
    # `path` or `path:line`, and a bare `—` when the finding names no file. A
    # character-class filter here rejected real filenames — spaces, `+`, `@` — and
    # read those rows as fileless, which relaxed the rule back to "any commit" for
    # a finding a commit could have matched exactly.
    ref="$(printf '%s' "$cell" | grep -oE '`[^`]+`' | head -n 1 | tr -d '`')"
    case "$ref" in
      '' | '—') continue ;;  # empty cell, or the no-file placeholder
    esac
    printf '%s\n' "$ref"
  done
)"
named_count="$(printf '%s\n' "$named_refs" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"
finding_paths="$(printf '%s\n' "$named_refs" | sed -E 's/:[0-9]+$//' | sed '/^[[:space:]]*$/d' | sort -u)"

new_commits=()
for i in "${!COMMIT_SHAS[@]}"; do
  if [[ "${COMMIT_TS[$i]}" > "$REVIEW_TIME" ]]; then
    new_commits+=("${COMMIT_SHAS[$i]}")
  fi
done

if [ "${#new_commits[@]}" -eq 0 ]; then
  fail "Review ${REVIEW_IDS[$review_index]} has findings. At least one commit addressing them is required before merge."
fi

if [ -z "$finding_paths" ] || [ "$named_count" -lt "$row_count" ]; then
  note "ℹ️ ${row_count} finding row(s), ${named_count} naming a file: at least one finding names no file and cannot"
  note "   be matched against a commit's files, so this run accepts any commit after the review."
  pass "Follow-up commit(s) found (${#new_commits[@]}); not all findings name a file to check against."
fi

touched=""
for sha in "${new_commits[@]}"; do
  while IFS= read -r changed; do
    [ -n "$changed" ] || continue
    if printf '%s\n' "$finding_paths" | grep -Fxq -- "$changed"; then
      touched+="$changed"$'\n'
    fi
  done <<<"${COMMIT_FILES[$sha]:-}"
done

if [ -z "$touched" ]; then
  fail "Review ${REVIEW_IDS[$review_index]} has findings. ${#new_commits[@]} commit(s) landed after it, but none touches the files the findings name ($(printf '%s' "$finding_paths" | tr '\n' ' ')). A commit that changes something else does not answer a review."
fi

note "Touched by a follow-up commit: $(printf '%s' "$touched" | sed '/^$/d' | sort -u | tr '\n' ' ')"
pass "Review ${REVIEW_IDS[$review_index]}'s findings are addressed by a follow-up commit."
