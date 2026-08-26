#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# status.sh — Show whether the Video Learning App is running
# Usage:  bash scripts/status.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; }

# Check Mac sleep state FIRST (Day 7 lesson).
# If the Mac Studio is asleep, the app is "up" from gunicorn's perspective
# but unreachable from Cloudflare Tunnel. This was the silent cause of
# "the URL is dead but `status.sh` says everything is fine" incidents.
#
# pmset output keys we care about:
#   "sleep 0"    = auto-sleep disabled (good for a server)
#   "sleep 1"    = auto-sleep enabled (bad — Mac sleeps after idle)
#   "Sleep On Power Button 1" = power button can sleep (acceptable;
#                                only fires on physical press)
echo "--- Mac sleep state ---"
if command -v pmset &>/dev/null; then
    AUTO_SLEEP="$(pmset -g | grep -E '^[[:space:]]*sleep[[:space:]]' | awk '{print $2}' || true)"
    if [[ "$AUTO_SLEEP" == "0" ]]; then
        ok "Mac auto-sleep disabled (good for server use)"
    elif [[ -n "$AUTO_SLEEP" ]]; then
        fail "Mac auto-sleep ENABLED (sleep=$AUTO_SLEEP) — Mac Studio will sleep when idle!"
        fail "→ fix: sudo pmset -a disablesleep 1"
        echo ""
    else
        warn "Could not parse auto-sleep setting from pmset"
    fi
fi

# Detect gunicorn master (production, Day 6+) OR uvicorn (legacy/dev).
# After Day 6 the default is gunicorn, so `pgrep -f 'uvicorn app.main'`
# was returning empty even when the app was perfectly healthy — the
# runbook's first status check was lying. Now matches either pattern.
PIDS="$(pgrep -f 'video-learning-app|gunicorn|uvicorn app.main' || true)"

if [[ -n "$PIDS" ]]; then
    # Identify which server (helps the operator know which rest.sh flow applies)
    PID_COUNT=$(echo "$PIDS" | wc -l | tr -d ' ')
    if pgrep -f 'video-learning-app|gunicorn' &>/dev/null; then
        # Multi-line PID list, formatted nicely
        PID_DISPLAY=$(echo "$PIDS" | tr '\n' ',' | sed 's/,$//' | sed 's/,/, /g')
        ok "gunicorn running ($PID_COUNT processes; master PID=$(echo "$PIDS" | head -1))"
    else
        ok "uvicorn running (PID=$PIDS) [dev mode]"
    fi
else
    fail "no gunicorn or uvicorn process found"
fi

echo ""
echo "--- Port 8000 ---"
if lsof -i:8000 &>/dev/null; then
    lsof -i:8000 | head -5
else
    warn "Port 8000 is free"
fi

echo ""
echo "--- HTTP smoke test ---"
# /api/health is the liveness probe (Day 6): always 200 when gunicorn is up.
# /api/ready is the readiness probe (Day 6): 200 when DB+events+Ollama are up,
#   503 when DB unreachable. We accept both — 503 here means "up but unhealthy".
for path in /api/health /api/ready / /login /docs; do
    code="$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000${path}" || echo "000")"
    case "$code" in
        200) ok "GET ${path} → ${code}" ;;
        503) warn "GET ${path} → ${code} (up but unhealthy — see /admin/events)" ;;
        404) warn "GET ${path} → ${code}" ;;
        000) fail "GET ${path} → connection refused" ;;
        *)   fail "GET ${path} → ${code}" ;;
    esac
done

echo ""
echo "--- Authenticated API (expect 401) ---"
code="$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "http://localhost:8000/api/courses/foo/sections/bar/retry-failed" || echo "000")"
if [[ "$code" == "401" ]]; then
    ok "POST .../retry-failed → ${code} (auth check working)"
else
    fail "POST .../retry-failed → ${code} (expected 401)"
fi

echo ""
echo "--- Last 5 log lines ---"
if [[ -f logs/server.log ]]; then
    tail -5 logs/server.log
else
    warn "No logs/server.log"
fi
