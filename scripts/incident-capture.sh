#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# incident-capture.sh — capture live-incident evidence, THEN auto-recover.
#
# WHY (born from the 2026-09-20 QueuePool-exhaustion outage): the site was
# down, the workers run as root, and stack sampling needs sudo — so by the
# time anyone could look, a restart had already destroyed the evidence.
# This script grabs everything a debugging session needs and THEN restarts
# the app, so capture and recovery are one action:
#
#   1. py-spy Python stacks of every gunicorn worker (falls back to
#      macOS `sample` C-level stacks if py-spy can't be installed)
#   2. lsof fd tables — how many DB connections each worker REALLY holds
#      (needs root; non-root lsof shows 0 for root processes — that
#      misled us once already)
#   3. TCP connection counts on :8000 — retry-storm intensity
#   4. Tail of the app log + a read-only DB status snapshot
#   5. Restart via launchctl kickstart, wait, verify local + public health
#
# Usage:    sudo bash scripts/incident-capture.sh "short reason"
# Evidence: /tmp/incident-<timestamp>/ (path printed at the end — paste it
#           back into the pairing session for analysis)
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail   # deliberately NOT -e: a failed capture step must not skip recovery

REASON="${1:-unspecified}"
TS=$(date +%Y%m%d-%H%M%S)
DIR="/tmp/incident-$TS"
APP_LABEL="system/com.video-learning-app"
LOG="$HOME/Library/Logs/video-learning-app.out.log"
DB="/Volumes/Storage-Fast-NVMe/video_learning.db"

mkdir -p "$DIR"
{
  echo "incident: $REASON"
  echo "captured: $(date)"
  echo "host:     $(hostname)"
  echo "git:      $(git -C "$(dirname "$0")/.." rev-parse --short HEAD 2>/dev/null || echo '?')"
} > "$DIR/README.txt"

echo "── [1/6] worker PIDs ─────────────────────────────"
PIDS=$(pgrep -f "gunicorn -c gunicorn.conf.py app.main:app" | sort -n)
echo "$PIDS" | tee "$DIR/pids.txt"
WORKERS=$(echo "$PIDS" | tail -n +2)   # first line is the master
if [ -z "$WORKERS" ]; then
  echo "NO GUNICORN PROCESSES FOUND — app not running?" | tee "$DIR/NO_PROCESSES.txt"
fi

echo "── [2/6] Python stacks (py-spy, fallback: sample) ──"
PSPY=""
if [ -x "venv/bin/py-spy" ]; then
  PSPY="venv/bin/py-spy"
elif command -v py-spy >/dev/null 2>&1; then
  PSPY="py-spy"
else
  # Try to install quietly; any failure degrades to `sample` only.
  venv/bin/pip install -q py-spy >/dev/null 2>&1 && PSPY="venv/bin/py-spy"
fi
for pid in $WORKERS; do
  if [ -n "$PSPY" ]; then
    "$PSPY" dump --pid "$pid" --nonblocking > "$DIR/pyspy_$pid.txt" 2>&1 \
      || echo "py-spy dump failed for $pid (see sample instead)" >> "$DIR/pyspy_$pid.txt"
  fi
  # `sample` always runs as a belt-and-braces (C-level frames still show
  # sqlite/ssl/whisper hot spots when py-spy can't attach)
  sample "$pid" 2 -file "$DIR/sample_$pid.txt" >/dev/null 2>&1
done
if [ -n "$PSPY" ]; then echo "py-spy: $PSPY"; else echo "py-spy unavailable — sample-only"; fi

echo "── [3/6] open DB connections per worker (lsof) ───"
{
  for pid in $WORKERS; do
    n=$(lsof -p "$pid" 2>/dev/null | grep -c "video_learning.db")
    echo "worker $pid: $n DB fds"
  done
} | tee "$DIR/db_fd_counts.txt"
lsof -p $(echo "$WORKERS" | tr '\n' ',' | sed 's/,$//') > "$DIR/lsof_full.txt" 2>/dev/null

echo "── [4/6] TCP :8000 connection summary ───────────"
lsof -nP -iTCP:8000 2>/dev/null | awk '{print $1, $2, $8, $9}' \
  | sort | uniq -c | sort -rn | head -20 > "$DIR/tcp8000.txt"
head -5 "$DIR/tcp8000.txt"

echo "── [5/6] log tail + DB status snapshot ──────────"
tail -n 3000 "$LOG" > "$DIR/out.log.tail"
sqlite3 -readonly "$DB" ".mode column" ".headers on" \
  "SELECT status, COUNT(*) AS n FROM videos GROUP BY status" > "$DIR/video_status.txt" 2>&1
cat "$DIR/video_status.txt"

echo "── [6/6] RESTART + verify ───────────────────────"
launchctl kickstart -k "$APP_LABEL"
sleep 12
echo "local:  $(curl -s -m 8 http://localhost:8000/api/health)"
echo "public: $(curl -s -m 10 https://www.capysmart.com/api/health)"

echo ""
echo "══════════════════════════════════════════════════════"
echo "EVIDENCE SAVED: $DIR"
echo "Paste this path back to the pairing session for analysis."
echo "══════════════════════════════════════════════════════"
ls -la "$DIR" | tail -n +2