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

PID="$(pgrep -f 'uvicorn app.main' || true)"

if [[ -n "$PID" ]]; then
    ok "uvicorn running (PID=$PID)"
else
    fail "uvicorn not running"
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
for path in / /login /docs; do
    code="$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000${path}" || echo "000")"
    case "$code" in
        200) ok "GET ${path} → ${code}" ;;
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
