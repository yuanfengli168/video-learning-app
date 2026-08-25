#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# restart.sh — Stop then start the Video Learning App (graceful)
# Usage:  bash scripts/restart.sh
#
# Day 6: sends SIGTERM (graceful shutdown, 5s timeout) then SIGKILL,
# then starts the app via the standard start.sh (gunicorn by default).
# Use this after config changes, after pulling new code, or as the
# first step in incident response.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "🛑 Stopping..."
bash "$SCRIPT_DIR/stop.sh"
sleep 1

echo ""
echo "🚀 Starting..."
bash "$SCRIPT_DIR/start.sh"
