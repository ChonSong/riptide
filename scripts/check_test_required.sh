#!/usr/bin/env bash
#
# scripts/check_test_required.sh — the `test-required` CI gate.
#
# Rule: a `feat:` / `fix:` commit must pair with test changes. It passes when the
# commit either
#
#   1. touches at least one file matching test[_/], `.test.` or `_test.py`, or
#   2. carries a `No-Tests: <non-empty reason>` trailer in the trailing block of
#      its commit message body (not the subject), for the case where there is
#      genuinely no test to add — a merge that dropped code being restored, or a
#      CI/config-only change.
#
# Anything else fails. A `No-Tests:` line with an empty reason does not exempt the
# commit, and neither does one that is not in the trailing block.
#
# Usage
# -----
#     scripts/check_test_required.sh <data-file>
#     scripts/check_test_required.sh -          # read the data file from stdin
#
# The commit data is produced by the workflow from the GitHub API (see
# .github/workflows/test-required.yml), so this script needs no checkout, no
# network and no git history of its own — which is what makes it testable. The
# format is one record per commit, fields in any order:
#
#     commit  <sha>                                  # starts a new record
#     message <base64 of the full commit message>     # `%B`: subject + body
#     file    <path changed by the commit>            # zero or more, per record
#
# Blank lines and lines starting with `#` are ignored.
#
# Exit codes
# ----------
# 0   every feat:/fix: commit touches a test file, or carries a valid trailer
# 1   at least one feat:/fix: commit does neither (the rule failed)
# 2   the check could not be evaluated (missing/unreadable data file, malformed
#     record, or undecodable message)

set -euo pipefail

# Kept in step with the pre-extraction inline grep in test-required.yml:
# `grep -Eo 'test[_/]|\.test\.|_test\.py'`.
TEST_FILE_RE='test[_/]|\.test\.|_test\.py'

usage() {
    cat >&2 <<'EOF'
usage: scripts/check_test_required.sh <data-file>|-
EOF
}

die() {
    echo "::error::check_test_required: $1" >&2
    exit 2
}

# base64-encode helper so tests and humans can build a data file:
#   printf '%s' "fix: x" | base64
decode_b64() {
    local encoded="$1"
    [ -z "$encoded" ] && {
        printf ''
        return 0
    }
    if printf '%s' "$encoded" | base64 -d >/dev/null 2>&1; then
        printf '%s' "$encoded" | base64 -d
    elif printf '%s' "$encoded" | base64 -D >/dev/null 2>&1; then
        printf '%s' "$encoded" | base64 -D
    else
        return 1
    fi
}

# The trailing block of a message body: the last paragraph, ignoring trailing
# blank lines. A `No-Tests:` line anywhere else (in the subject, or above a
# later paragraph) is not a trailer and must not exempt the commit.
trailing_block() {
    awk '
        { line[NR] = $0 }
        END {
            i = NR
            while (i >= 1 && line[i] ~ /^[[:space:]]*$/) i--
            last = i
            while (i >= 1 && line[i] !~ /^[[:space:]]*$/) i--
            for (j = i + 1; j <= last; j++) print line[j]
        }
    '
}

# Print the trailer's reason and return 0 when $1 (a full commit message) has a
# valid `No-Tests: <non-empty reason>` trailer; return 1 otherwise.
# Argument 1: the commit message body only (the subject line already dropped).
exemption_reason() {
    local body="$1" block line
    block="$(printf '%s\n' "$body" | trailing_block)"
    [ -n "$block" ] || return 1
    # The token is matched case-insensitively, and the reason must contain a
    # non-space character on the same line.
    line="$(printf '%s\n' "$block" \
        | grep -iE '^[[:space:]]*No-Tests:[[:space:]]*[^[:space:]]' \
        | tail -n 1 || true)"
    [ -n "$line" ] || return 1
    printf '%s\n' "$line" | sed -E 's/^[[:space:]]*[Nn][Oo]-[Tt][Ee][Ss][Tt][Ss]:[[:space:]]*//; s/[[:space:]]+$//'
}

# True (0) when any changed file looks like a test file.
touches_test_file() {
    printf '%s\n' "$1" | grep -Eq "$TEST_FILE_RE"
}

# True (0) when the subject is one this gate applies to: `feat:` / `fix:`, with
# or without a scope. Same patterns the gate used inline before the extraction.
is_gated_subject() {
    case "$1" in
        feat:* | fix:* | 'feat('*')'*:* | 'fix('*')'*:*) return 0 ;;
        *) return 1 ;;
    esac
}

input="${1:-}"
[ -n "$input" ] || {
    usage
    exit 2
}

if [ "$input" = "-" ]; then
    data="$(cat)"
else
    [ -f "$input" ] || die "data file not found: $input"
    [ -r "$input" ] || die "data file not readable: $input"
    data="$(cat "$input")"
fi

records=0
checked=0
exempted=0
with_tests=0
failed=0

cur_sha=""
cur_b64=""
cur_has_message=0
cur_files=""

# Evaluate the record accumulated so far. Prints one line per exempted or
# failing commit; increments the counters.
flush_record() {
    [ -n "$cur_sha" ] || return 0

    local msg subject body reason
    msg="$(decode_b64 "$cur_b64")" || die "message for commit $cur_sha is not valid base64"
    subject="$(printf '%s\n' "$msg" | head -n 1)"
    # The trailer must live in the body, so only look below the subject line.
    body="$(printf '%s\n' "$msg" | sed -e '1d')"

    records=$((records + 1))

    if is_gated_subject "$subject"; then
        checked=$((checked + 1))
        if touches_test_file "$cur_files"; then
            with_tests=$((with_tests + 1))
        elif reason="$(exemption_reason "$body")"; then
            exempted=$((exempted + 1))
            echo "Exempt: commit $cur_sha ($subject) — No-Tests: $reason"
        else
            failed=$((failed + 1))
            # Only when the trailer is genuinely in the trailing block: a
            # `No-Tests:` line elsewhere in the body is not a (failed) trailer,
            # so it must not be reported as one with an empty reason.
            if printf '%s\n' "$body" | trailing_block | grep -qiE '^[[:space:]]*No-Tests:'; then
                echo "::error::Commit $cur_sha ($subject) has a 'No-Tests:' trailer with an empty reason. The trailer must spell out what was checked in place of a test."
            fi
            echo "::error::Commit $cur_sha ($subject) adds/changes no test files. Feat/fix commits must include paired test changes, or a 'No-Tests: <reason>' trailer when there is genuinely no test to add."
        fi
    fi

    cur_sha=""
    cur_b64=""
    cur_has_message=0
    cur_files=""
}

while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        '' | '#'*) ;;
        'commit '*)
            flush_record
            cur_sha="${line#commit }"
            [ -n "$cur_sha" ] || die "record with an empty sha"
            ;;
        'message '*)
            cur_b64="${line#message }"
            cur_has_message=1
            ;;
        'file '*)
            [ -n "$cur_sha" ] || die "'file' before the first 'commit' record"
            cur_files+="${line#file }"$'\n'
            ;;
        *)
            die "unrecognised line: $line"
            ;;
    esac
done <<<"$data"

# A record that never got a `message` line would silently pass every commit, so
# refuse it instead.
if [ -n "$cur_sha" ] && [ "$cur_has_message" -eq 0 ]; then
    die "record $cur_sha has no 'message' field"
fi
flush_record

if [ "$records" -eq 0 ]; then
    echo "No feat/fix commits to check"
    exit 0
fi

if [ "$failed" -gt 0 ]; then
    echo "::error::$failed feat/fix commit(s) add/change no test files and carry no 'No-Tests: <reason>' trailer."
    exit 1
fi

if [ "$checked" -eq 0 ]; then
    echo "No feat/fix commits to check"
    exit 0
fi

echo "Checked $checked feat/fix commit(s): $with_tests with paired test changes, $exempted exempted by a 'No-Tests:' trailer."
echo "All feat/fix commits have paired test changes."
exit 0
