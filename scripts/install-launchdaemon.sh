#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install-launchdaemon.sh — Install the video-learning-app LaunchDaemon
# Usage:  sudo bash scripts/install-launchdaemon.sh
#
# What it does:
#   1. Creates the per-user logs directory
#   2. Writes /Library/LaunchDaemons/com.video-learning-app.plist
#   3. Sets correct permissions (root:wheel, 644)
#   4. Loads it into launchd with launchctl load -w
#   5. Verifies it's running
#   6. Smoke-tests /api/health
#
# To uninstall later:  sudo bash scripts/uninstall-launchdaemon.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()    { echo -e "${GREEN}✅ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail()  { echo -e "${RED}❌ $1${NC}"; }
info()  { echo -e "${CYAN}ℹ️  $1${NC}"; }

# ── Sanity: must be run as sudo ─────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    fail "Must be run as root. Use:  sudo bash $0"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PLIST_PATH="/Library/LaunchDaemons/com.video-learning-app.plist"
SCRIPT_PATH="$PROJECT_ROOT/scripts/start.sh"
LOG_DIR="/Users/yuanfengli/Library/Logs"

# ── 1. Logs dir ─────────────────────────────────────────────────────
mkdir -p "$LOG_DIR"
chown yuanfengli:staff "$LOG_DIR" 2>/dev/null || true
ok "Logs dir: $LOG_DIR"

# ── 2. Confirm start.sh exists ──────────────────────────────────────
if [[ ! -x "$SCRIPT_PATH" && ! -f "$SCRIPT_PATH" ]]; then
    fail "start.sh not found at $SCRIPT_PATH"
    exit 1
fi
# Make sure it's executable (no-op if already)
chmod +x "$SCRIPT_PATH" 2>/dev/null || true
ok "Start script: $SCRIPT_PATH"

# ── 3. Check if already loaded ──────────────────────────────────────
if launchctl list | grep -q "com.video-learning-app"; then
    warn "LaunchDaemon 'com.video-learning-app' is already loaded."
    read -rp "Unload and re-install? (y/N) " reinstall
    if [[ "$reinstall" =~ ^[Yy]$ ]]; then
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        rm -f "$PLIST_PATH"
        ok "Unloaded + removed old plist"
    else
        info "Aborting — leaving existing plist in place."
        exit 0
    fi
fi

# ── 4. Write the plist ──────────────────────────────────────────────
# (Using sudo tee so we can write to /Library/LaunchDaemons as root)
info "Writing plist to $PLIST_PATH"

sudo tee "$PLIST_PATH" > /dev/null <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <!-- Identity -->
    <key>Label</key>
    <string>com.video-learning-app</string>

    <!-- What to run: bash + start.sh -->
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${SCRIPT_PATH}</string>
    </array>

    <!-- Belt-and-braces CWD -->
    <key>WorkingDirectory</key>
    <string>${PROJECT_ROOT}</string>

    <!-- Launch behavior -->
    <key>RunAtLoad</key>           <true/>
    <key>KeepAlive</key>           <true/>

    <!-- Restart throttle (avoid pegging CPU if it crashes in a loop) -->
    <key>ThrottleInterval</key>    <integer>10</integer>

    <!-- Run in the GUI session so the user can hit localhost:8000 -->
    <key>LimitLoadToSessionType</key> <string>Aqua</string>
    <key>ProcessType</key>            <string>Background</string>

    <!-- Env: explicit PATH so /opt/homebrew/bin is found -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>/Users/yuanfengli</string>
    </dict>

    <!-- Logs (User-scoped, NOT wiped on reboot) -->
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/video-learning-app.out.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/video-learning-app.err.log</string>
</dict>
</plist>
EOF

# ── 5. Permissions ──────────────────────────────────────────────────
chown root:wheel "$PLIST_PATH"
chmod 644 "$PLIST_PATH"
ok "Permissions set (root:wheel, 644)"

# ── 6. Validate XML ─────────────────────────────────────────────────
if ! plutil -lint "$PLIST_PATH" >/dev/null; then
    fail "plist is not valid XML"
    exit 1
fi
ok "plist XML is valid"

# ── 7. Load it ──────────────────────────────────────────────────────
launchctl load -w "$PLIST_PATH"
ok "Loaded LaunchDaemon"

# ── 8. Verify it's running ──────────────────────────────────────────
sleep 3
if launchctl list | grep -q "com.video-learning-app"; then
    PID=$(launchctl list | grep "com.video-learning-app" | awk '{print $1}')
    ok "LaunchDaemon is running (PID=$PID)"
else
    warn "LaunchDaemon not seen in launchctl list. Check: tail -50 $LOG_DIR/video-learning-app.err.log"
fi

# ── 9. Smoke test the app ───────────────────────────────────────────
info "Waiting 5s for the app to start..."
sleep 5
HEALTH=$(curl -s --max-time 5 http://localhost:8000/api/health 2>/dev/null || echo "")
if [[ -n "$HEALTH" ]]; then
    ok "App responded: $HEALTH"
else
    warn "App did not respond at http://localhost:8000/api/health yet."
    warn "It may still be starting up (Ollama check + uvicorn boot takes ~10s)."
    warn "Try:  sleep 10 && curl -s http://localhost:8000/api/health"
    warn "Logs: tail -f $LOG_DIR/video-learning-app.out.log"
fi

echo ""
echo "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo "${BOLD}║  Install complete                                           ║${NC}"
echo "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Plist:       $PLIST_PATH"
echo "  Start script:$SCRIPT_PATH"
echo "  Stdout log:  $LOG_DIR/video-learning-app.out.log"
echo "  Stderr log:  $LOG_DIR/video-learning-app.err.log"
echo ""
echo "  Useful commands:"
echo "    Status:  sudo launchctl list | grep video-learning-app"
echo "    Logs:    tail -f $LOG_DIR/video-learning-app.out.log"
echo "    Unload:  sudo launchctl unload $PLIST_PATH"
echo ""
echo "  Next: test crash recovery (should auto-restart in ~10s):"
echo "    PID=\$(pgrep -f 'uvicorn app.main' | head -1)"
echo "    kill -9 \$PID && sleep 12 && pgrep -f 'uvicorn app.main'"
echo ""
echo "  Then test reboot recovery:"
echo "    sudo shutdown -r now   # wait 2-3 min, reconnect via AnyDesk"
echo "    curl http://localhost:8000/api/health"
