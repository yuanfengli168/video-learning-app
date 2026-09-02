#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# setup-probe.sh — Install + load the backup-health probe LaunchAgent.
#
# Companion to setup-backups.sh. Generates the probe plist with the TCC-clean
# runtime path (~/Library/Application Support/VideoApp/scripts/backup/) so
# macOS doesn't return EPERM when launchd tries to exec the script.
#
# Idempotent: safe to run multiple times.
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

RUNTIME_DIR="$HOME/Library/Application Support/VideoApp/scripts/backup"
PLIST_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"
LABEL="com.videoapp.backup-probe"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; }

# ── Pre-flight ────────────────────────────────────────────────────────────
if [ ! -d "$PROJECT_DIR/scripts/backup" ]; then
    fail "Backup scripts not found at $PROJECT_DIR/scripts/backup/"
    exit 1
fi

mkdir -p "$PLIST_DIR" "$LOG_DIR" "$RUNTIME_DIR"

# ── Sync the probe script to runtime location ─────────────────────────────
if [ ! -f "$PROJECT_DIR/scripts/backup/backup-probe.sh" ]; then
    fail "backup-probe.sh missing from repo"
    exit 1
fi
cp -f "$PROJECT_DIR/scripts/backup/backup-probe.sh" "$RUNTIME_DIR/"
chmod +x "$RUNTIME_DIR/backup-probe.sh"
ok "Synced backup-probe.sh → $RUNTIME_DIR"

# ── Generate plist at runtime path ────────────────────────────────────────
plist="$PLIST_DIR/${LABEL}.plist"
cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <!-- TCC-clean path (see Prod-Must-do.md). The repo copy under
         scripts/backup/ is the source of truth; this plist runs the
         runtime copy under ~/Library/Application Support/. -->
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${RUNTIME_DIR}/backup-probe.sh</string>
    </array>

    <key>StartInterval</key>
    <integer>300</integer>

    <!--
      Tell backup-probe.sh where the actual repo is. The script lives in
      the TCC-clean runtime path but it needs to source the venv from the
      repo. VIDEOAPP_PROJECT_ROOT must be set by the installer.
    -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>VIDEOAPP_PROJECT_ROOT</key>
        <string>${PROJECT_DIR}</string>
    </dict>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/video-app-backup.log</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/video-app-backup.log</string>

    <key>RunAtLoad</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
EOF
ok "Wrote $plist"

# ── Reload ────────────────────────────────────────────────────────────────
launchctl unload "$plist" 2>/dev/null || true
launchctl load "$plist"
ok "Loaded $LABEL"

echo ""
ok "Probe active. After ~5 minutes:"
echo "    cat /tmp/video-app-backup-status.json"
echo "    curl -s http://localhost:8000/api/ready | python3 -m json.tool"
echo "Uninstall with: launchctl unload $plist && rm $plist"
