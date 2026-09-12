#!/usr/bin/env bash
# grant_trial.sh — Soft-launch trial user management (2026-09-12).
#
# Grants/reverts PAID (role=1) for the 20-user soft-launch cohort.
# Per the launch plan: 30 days full Pro, no card; flip back to FREE
# (role=2) at trial end. Nothing is deleted — they keep their data.
#
# Usage:
#   bash scripts/grant_trial.sh grant  you@gmail.com friend2@...     # grant PAID
#   bash scripts/grant_trial.sh grant --all-cohort cohort.txt        # grant from file
#   bash scripts/grant_trial.sh revert you@gmail.com                 # back to FREE
#   bash scripts/grant_trial.sh revert --all-cohort cohort.txt       # whole cohort
#   bash scripts/grant_trial.sh list                                 # show cohort
#   bash scripts/grant_trial.sh status you@gmail.com                 # one user's state
#
# Cohort file format (cohort.txt): one email per line, '#' comments OK.
# The file doubles as the trial roster + due-date receipt.
#
# What 'grant' records in users.notes (audit trail):
#   trial:granted=2026-09-12;due=2026-10-12;cohort=soft-launch-20
# 'revert' preserves the original grant note + appends reverted=<date>,
# so the audit trail shows the full lifecycle after the trial ends.
#
# Idempotent: re-granting an active trial updates the due date only.
# Safety: refuses to touch ADMIN rows (role=0) — admins are never
# trial users; refuses to revert users with no trial note (protects
# your real PAID subscribers, if any exist by then).

set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────
DB_URL="${DATABASE_URL:-$(grep '^DATABASE_URL=' .env 2>/dev/null | cut -d= -f2-)}"
DB_PATH="${DB_URL#sqlite:///}"
if [[ ! -f "$DB_PATH" ]]; then
    echo "❌ DB not found at '$DB_PATH' (set DATABASE_URL or run from repo root)" >&2
    exit 1
fi

COHORT_TAG="${TRIAL_COHORT_TAG:-soft-launch-20}"
TRIAL_DAYS="${TRIAL_DAYS:-30}"

info()  { printf '\033[1;34m[trial]\033[0m %s\n' "$*"; }
fail()  { printf '\033[1;31m[trial]\033[0m ERROR: %s\n' "$*" >&2; exit 1; }

today() { date +%Y-%m-%d; }
due_date() { date -v+"${TRIAL_DAYS}"d +%Y-%m-%d 2>/dev/null || date -d "+${TRIAL_DAYS} days" +%Y-%m-%d; }

get_uid() {  # get_uid <email> → prints uid or empty
    sqlite3 "$DB_PATH" "SELECT user_id FROM users WHERE email='$1' LIMIT 1;"
}
get_role() { # get_role <email> → prints role int
    sqlite3 "$DB_PATH" "SELECT role FROM users WHERE email='$1' LIMIT 1;"
}

cmd="${1:-}"
shift || true
grant_one() {
    local email="$1"
    [[ -z "$email" ]] && return 0
    local uid role
    uid="$(get_uid "$email")"
    [[ -z "$uid" ]] && { fail "no user row for '$email' (they must sign in once first — auto-create happens on first login)"; }
    role="$(get_role "$email")"
    [[ "$role" == "0" ]] && { info "SKIP $email — is ADMIN, never trialed"; return 0; }
    local note="trial:granted=$(today);due=$(due_date);cohort=${COHORT_TAG}"
    sqlite3 "$DB_PATH" "UPDATE users SET role=1, notes='$note', updated_at=CURRENT_TIMESTAMP WHERE email='$email';"
    info "GRANTED PAID → $email (due $(due_date))"
}

revert_one() {
    local email="$1"
    [[ -z "$email" ]] && return 0
    local uid role notes
    uid="$(get_uid "$email")"
    [[ -z "$uid" ]] && { fail "no user row for '$email'"; }
    role="$(get_role "$email")"
    [[ "$role" == "0" ]] && { info "SKIP $email — is ADMIN"; return 0; }
    notes="$(sqlite3 "$DB_PATH" "SELECT notes FROM users WHERE email='$email' LIMIT 1;")"
    if [[ "$notes" != trial:* ]]; then
        info "SKIP $email — no trial note (not a trial user; protects real subscribers)"
        return 0
    fi
    sqlite3 "$DB_PATH" "UPDATE users SET role=2, notes='$notes;reverted=$(today)', updated_at=CURRENT_TIMESTAMP WHERE email='$email';"
    info "REVERTED → FREE $email (data kept)"
}

cohort_emails() { # reads file, strips comments/blanks
    grep -vE '^\s*(#|$)' "$1" || true
}

list_cohort() {
    info "Cohort ($COHORT_TAG) — trial-granted users in DB:"
    sqlite3 -column -header "$DB_PATH" \
        "SELECT email, CASE role WHEN 1 THEN 'PAID' WHEN 2 THEN 'FREE' ELSE 'role='||role END AS role, notes
         FROM users WHERE notes LIKE 'trial:%' ORDER BY created_at;"
}

status_one() {
    local email="$1"
    sqlite3 -column -header "$DB_PATH" \
        "SELECT email, role, notes FROM users WHERE email='$email';"
}

case "$cmd" in
    grant)
        if [[ "${1:-}" == "--all-cohort" ]]; then
            [[ -f "${2:-}" ]] || fail "cohort file not found: ${2:-}"
            while IFS= read -r email; do grant_one "$email"; done < <(cohort_emails "$2")
        else
            [[ $# -ge 1 ]] || fail "usage: grant <email...> | grant --all-cohort <file>"
            for email in "$@"; do grant_one "$email"; done
        fi
        ;;
    revert)
        if [[ "${1:-}" == "--all-cohort" ]]; then
            [[ -f "${2:-}" ]] || fail "cohort file not found: ${2:-}"
            while IFS= read -r email; do revert_one "$email"; done < <(cohort_emails "$2")
        else
            [[ $# -ge 1 ]] || fail "usage: revert <email...> | revert --all-cohort <file>"
            for email in "$@"; do revert_one "$email"; done
        fi
        ;;
    list)  list_cohort ;;
    status) status_one "${1:?usage: status <email>}";;
    *) cat <<EOF
grant_trial.sh — soft-launch trial user management

  bash scripts/grant_trial.sh grant  <email> [<email>...]     → PAID for $TRIAL_DAYS days
  bash scripts/grant_trial.sh grant  --all-cohort <file>       → whole cohort file
  bash scripts/grant_trial.sh revert <email> [<email>...]      → back to FREE
  bash scripts/grant_trial.sh revert --all-cohort <file>       → end the whole trial
  bash scripts/grant_trial.sh list                              → show trial cohort
  bash scripts/grant_trial.sh status <email>                    → one user

Env: DATABASE_URL, TRIAL_DAYS (default 30), TRIAL_COHORT_TAG (default $COHORT_TAG)
EOF
        ;;
esac