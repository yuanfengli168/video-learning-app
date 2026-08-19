#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install-cloudflare-tunnel.sh — Expose the local app to the public internet
# via Cloudflare Tunnel (free, no port forwarding required).
#
# Why this script exists:
#   Mac Studio's uvicorn binds to 0.0.0.0:8000, but home routers / CGNAT
#   hide it from the public internet. Cloudflare Tunnel creates an
#   *outbound-only* encrypted tunnel from your Mac to Cloudflare's edge.
#   Result: testers get a real https://<name>.trycloudflare.com URL.
#
# Usage:
#   # Step 1 — quick test (no Cloudflare account needed):
#   bash scripts/install-cloudflare-tunnel.sh --quick
#
#   # Step 2 — permanent tunnel (requires free Cloudflare account):
#   bash scripts/install-cloudflare-tunnel.sh --permanent
#
#   # Step 3 — uninstall:
#   bash scripts/install-cloudflare-tunnel.sh --uninstall
#
# What --quick does:
#   - Installs cloudflared (if missing)
#   - Launches a *temporary* tunnel for immediate testing
#   - Prints the https://*.trycloudflare.com URL
#   - URL changes every time you restart (good for testing only)
#
# What --permanent does:
#   - Installs cloudflared (if missing)
#   - Logs you into Cloudflare (free account, opens browser)
#   - Creates a named tunnel (video-learning-app)
#   - Writes ~/.cloudflared/config.yml
#   - Installs as a launchd system service (auto-start on reboot)
#   - Prints the permanent URL
#   - Smoke-tests the tunnel
#
# What --uninstall does:
#   - Stops and removes the launchd service
#   - Deletes ~/.cloudflared/ (the tunnel config + credentials)
#   - Does NOT uninstall the cloudflared binary
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

# ── Sanity checks ──────────────────────────────────────────────────────────
APP_PORT="${APP_PORT:-8000}"
APP_USER="$(whoami)"

if [ "$(uname)" != "Darwin" ]; then
  fail "This script is macOS-only (uses launchd)."
  exit 1
fi

# Check if the app is running locally
if ! curl -sf "http://localhost:${APP_PORT}/api/health" >/dev/null 2>&1; then
  warn "App not responding on http://localhost:${APP_PORT}/api/health"
  warn "Start the app first:  bash scripts/start.sh"
  warn "Continuing anyway — Cloudflare will work but won't have anything to proxy"
  echo ""
fi

# ── Helper functions ────────────────────────────────────────────────────────
install_cloudflared() {
  if command -v cloudflared >/dev/null 2>&1; then
    ok "cloudflared already installed: $(cloudflared --version 2>&1 | head -1)"
    return
  fi

  if ! command -v brew >/dev/null 2>&1; then
    fail "Homebrew not found. Install it first: https://brew.sh"
    exit 1
  fi

  info "Installing cloudflared via Homebrew..."
  brew install cloudflared
  ok "Installed: $(cloudflared --version 2>&1 | head -1)"
}

run_quick_tunnel() {
  install_cloudflared

  echo ""
  info "Starting QUICK tunnel (no account needed, URL is temporary)..."
  info "When you see a https://*.trycloudflare.com URL, copy it."
  info "Test from your phone (turn OFF wifi, use cellular)."
  info "Press Ctrl-C to stop."
  echo ""

  # cloudflared will run in the foreground
  cloudflared tunnel --url "http://localhost:${APP_PORT}"
}

run_permanent_tunnel() {
  install_cloudflared

  echo ""
  info "Setting up PERMANENT tunnel (requires free Cloudflare account)..."
  echo ""

  # Login (opens browser)
  if [ ! -f "$HOME/.cloudflared/cert.pem" ]; then
    info "Step 1/4: Logging into Cloudflare (browser will open)..."
    info "  → Sign up at https://dash.cloudflare.com/sign-up if you don't have an account"
    info "  → Select your account when prompted"
    info "  → Click 'Allow' to authorize the tunnel"
    cloudflared tunnel login
    ok "Cloudflare login successful"
  else
    ok "Already logged in to Cloudflare"
  fi

  # Create the named tunnel (idempotent: skip if already exists)
  TUNNEL_NAME="video-learning-app"
  if cloudflared tunnel list 2>/dev/null | grep -q "$TUNNEL_NAME"; then
    ok "Tunnel '$TUNNEL_NAME' already exists"
    TUNNEL_ID=$(cloudflared tunnel list 2>/dev/null | grep "$TUNNEL_NAME" | awk '{print $1}')
  else
    info "Step 2/4: Creating tunnel '$TUNNEL_NAME'..."
    cloudflared tunnel create "$TUNNEL_NAME"
    TUNNEL_ID=$(cloudflared tunnel list 2>/dev/null | grep "$TUNNEL_NAME" | awk '{print $1}')
    ok "Tunnel created: $TUNNEL_ID"
  fi

  # Write config file
  CREDS_FILE="$HOME/.cloudflared/${TUNNEL_ID}.json"
  if [ ! -f "$CREDS_FILE" ]; then
    fail "Credentials file not found: $CREDS_FILE"
    fail "Try deleting the tunnel: cloudflared tunnel delete $TUNNEL_NAME"
    exit 1
  fi

  info "Step 3/4: Writing config file ~/.cloudflared/config.yml..."
  cat > "$HOME/.cloudflared/config.yml" << EOF
# Cloudflare Tunnel config for video-learning-app
# Generated by scripts/install-cloudflare-tunnel.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")

tunnel: ${TUNNEL_ID}
credentials-file: ${CREDS_FILE}

# Default: proxy everything to the local app
ingress:
  - hostname: ""
    service: http://localhost:${APP_PORT}
  # Catch-all (required) — sends 404 for anything else
  - service: http_status:404

# Optional: enable logging
loglevel: info
logfile: /var/log/cloudflared.log
EOF

  ok "Config written to ~/.cloudflared/config.yml"

  # Install as a system service (auto-start on boot)
  info "Step 4/4: Installing as a launchd service..."
  sudo cloudflared service install 2>&1 | grep -v "^$" || true

  # Kickstart the service
  sudo launchctl kickstart -kp "system/com.cloudflare.cloudflared" 2>/dev/null || true

  # Wait a moment for the service to start
  sleep 3

  # Verify
  if sudo launchctl list 2>/dev/null | grep -q "com.cloudflare.cloudflared"; then
    ok "Service is running"
  else
    warn "Service may not have started. Check: sudo launchctl list | grep cloudflared"
    warn "Logs: tail -f /var/log/cloudflared.err.log"
  fi

  # Smoke test — wait up to 10 seconds for the tunnel to come up
  echo ""
  info "Smoke-testing the tunnel (this can take up to 10 seconds)..."
  TUNNEL_URL=""
  for i in {1..20}; do
    sleep 0.5
    # Try to extract the URL from logs or just tell user where to find it
    if sudo test -f /var/log/cloudflared.log; then
      TUNNEL_URL=$(sudo grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" /var/log/cloudflared.log 2>/dev/null | head -1 || echo "")
      if [ -n "$TUNNEL_URL" ]; then
        break
      fi
    fi
  done

  echo ""
  echo -e "${BOLD}════════════════════════════════════════════════════════════════${NC}"
  echo -e "${BOLD}🎉 Tunnel is set up!${NC}"
  echo -e "${BOLD}════════════════════════════════════════════════════════════════${NC}"
  echo ""

  if [ -n "$TUNNEL_URL" ]; then
    echo -e "  Your public URL: ${GREEN}${BOLD}${TUNNEL_URL}${NC}"
  else
    echo "  Your public URL: check the output of:"
    echo "    sudo tail -f /var/log/cloudflared.log | grep trycloudflare"
    echo "  Or run: cloudflared tunnel info $TUNNEL_NAME"
  fi

  echo ""
  echo "  Test it:"
  echo "    1. Turn OFF wifi on your phone (use cellular)"
  echo "    2. Open the URL above in your phone browser"
  echo "    3. Try logging in + uploading a video"
  echo ""
  echo "  Useful commands:"
  echo "    # Check service status"
  echo "    sudo launchctl list | grep cloudflared"
  echo ""
  echo "    # View logs (Ctrl-C to exit)"
  echo "    sudo tail -f /var/log/cloudflared.log"
  echo ""
  echo "    # Tunnel info"
  echo "    cloudflared tunnel info $TUNNEL_NAME"
  echo ""
  echo "  Next step: post the URL on LinkedIn/Twitter to recruit testers!"
  echo ""
}

run_uninstall() {
  info "Uninstalling Cloudflare Tunnel..."

  # Stop and remove the launchd service
  if sudo launchctl list 2>/dev/null | grep -q "com.cloudflare.cloudflared"; then
    info "Stopping service..."
    sudo launchctl bootout system/com.cloudflare.cloudflared 2>/dev/null || true
    ok "Service stopped"
  fi

  # Delete the tunnel itself (asks Cloudflare to remove it)
  if command -v cloudflared >/dev/null 2>&1; then
    if cloudflared tunnel list 2>/dev/null | grep -q "video-learning-app"; then
      info "Deleting tunnel from Cloudflare..."
      cloudflared tunnel delete video-learning-app 2>/dev/null || true
      ok "Tunnel deleted"
    fi
  fi

  # Remove local config + credentials
  if [ -d "$HOME/.cloudflared" ]; then
    info "Removing ~/.cloudflared/ ..."
    rm -rf "$HOME/.cloudflared"
    ok "Config removed"
  fi

  ok "Uninstall complete. The cloudflared binary is still installed (brew uninstall cloudflared to remove it)."
}

# ── Main ────────────────────────────────────────────────────────────────────
case "${1:-}" in
  --quick|-q)
    run_quick_tunnel
    ;;
  --permanent|-p)
    run_permanent_tunnel
    ;;
  --uninstall|-u)
    run_uninstall
    ;;
  --help|-h|"")
    cat <<EOF
${BOLD}install-cloudflare-tunnel.sh${NC} — expose the local app to the internet via Cloudflare Tunnel

${BOLD}Usage:${NC}
  bash scripts/install-cloudflare-tunnel.sh --quick       # temporary URL, no account needed
  bash scripts/install-cloudflare-tunnel.sh --permanent    # permanent URL, free Cloudflare account
  bash scripts/install-cloudflare-tunnel.sh --uninstall    # remove everything
  bash scripts/install-cloudflare-tunnel.sh --help         # this message
EOF
    ;;
  *)
    fail "Unknown argument: $1"
    echo "Run with --help to see options."
    exit 1
    ;;
esac