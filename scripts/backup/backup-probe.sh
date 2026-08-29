#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# backup-probe.sh — Run the backup health probe every 5 minutes
# Called by:  com.videoapp.backup-probe.plist (LaunchDaemon in system
# domain — runs as root, no TCC blocks)
#
# What it does:
#   1. Runs app.services.backup_monitor.collect_status() in the venv
#   2. Writes /tmp/video-app-backup-status.json
#   3. Logs a one-line summary to /var/log/video-app-backup.log
#
# Why every 5 min?
#   - cheap (one launchctl print per job + one diskutil + one stat)
#   - within 5 min of any backup failure, the dashboard / /api/ready
#     / admin events page will reflect the new state
#   - launchd will throttle runaway jobs anyway
#
# Why PYTHONPATH and not cd:
#   - launchd context (root) cannot readdir() ~/Desktop/ — TCC blocks
#     the readlink/stat needed to validate cwd. So `cd` silently
#     succeeds but Python can't import from it.
#   - PYTHONPATH bypasses this entirely: we put the project root in
#     sys.path without needing cwd-based module discovery.
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

# This script lives in TWO places:
#   - Repo (source of truth): scripts/backup/backup-probe.sh
#   - Runtime (TCC-clean):   ~/Library/Application Support/VideoApp/scripts/backup/
#
# When running from the runtime copy, SCRIPT_DIR/../.. resolves to the wrong
# place (Library/Application). The installer must export VIDEOAPP_PROJECT_ROOT
# pointing at the actual repo. If unset, we fall back to the layout that
# worked before the TCC fix (repo-only, e.g. on a dev machine).
if [[ -z "${VIDEOAPP_PROJECT_ROOT:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
else
    PROJECT_ROOT="${VIDEOAPP_PROJECT_ROOT}"
fi
LOG="$HOME/Library/Logs/video-app-backup.log"

# The TCC-clean venv path. macOS TCC blocks root-launched bash from reading
# pyvenv.cfg under ~/Desktop/, so we use a symlinked python from a TCC-clean
# location. The TCC-clean path is ~/Library/Application Support/VideoApp/venv/
# which is set up by install-backup-launchdaemon.sh. Falls back to the
# in-repo venv for dev machines where TCC isn't an issue.
TCC_CLEAN_VENV="$HOME/Library/Application Support/VideoApp/venv"
if [[ -x "$TCC_CLEAN_VENV/bin/python" ]]; then
    PYTHON_BIN="$TCC_CLEAN_VENV/bin/python"
else
    PYTHON_BIN="$PROJECT_ROOT/venv/bin/python"
fi

log() { echo "[$(date '+%F %T')] [probe] $*" | tee -a "$LOG"; }

if [[ ! -x "$PYTHON_BIN" ]]; then
    log "ERROR: python not found at $PYTHON_BIN"
    exit 1
fi

# We invoke the venv's python directly (not via `source activate`) because
# launchd in user context can't source bash scripts from TCC-restricted
# paths even when they're nested inside a runtime-clean wrapper script.
# Direct exec of the venv binary works because launchd only blocks `source`
# of bash scripts; compiled binaries are fine.
#
# Important: we run a standalone copy of backup_monitor.py that lives at the
# TCC-clean runtime path. We CANNOT import from PROJECT_ROOT because root
# context can't readdir() ~/Desktop/ (TCC) — so cd, sys.path lookups, and
# open() all silently fail or return EPERM.
#
# install-backup-launchdaemon.sh keeps this copy in sync with the repo.
PROBE_SCRIPT="${PROBE_RUNTIME_DIR:-/Users/jackyli/Library/Application Support/VideoApp/scripts/backup/probe}/backup_monitor.py"
log "DEBUG: PROJECT_ROOT=$PROJECT_ROOT, python_bin=$PYTHON_BIN, PROBE_SCRIPT=$PROBE_SCRIPT"
if [[ ! -f "$PROBE_SCRIPT" ]]; then
    log "ERROR: probe script missing at $PROBE_SCRIPT"
    log "       Re-run scripts/install-backup-launchdaemon.sh to sync it"
    exit 1
fi

"$PYTHON_BIN" "$PROBE_SCRIPT" 2>&1 | /usr/bin/tee -a "$LOG"
py_exit=${PIPESTATUS[0]}
if [[ $py_exit -ne 0 ]]; then
    log "ERROR: probe exited non-zero (python exit=$py_exit)"
    exit 1
fi

log "probe OK"
