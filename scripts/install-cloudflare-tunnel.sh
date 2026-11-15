#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install-cloudflare-tunnel.sh — Expose the local app to the public internet
# via Cloudflare Tunnel (free, no port forwarding required).
#
# Why this script exists:
#   Mac Studio's gunicorn (Day 6) binds to 0.0.0.0:8000, but home
#   routers / CGNAT hide it from the public internet. Cloudflare
#   Tunnel creates an *outbound-only* encrypted tunnel from your
#   Mac to Cloudflare's edge. Result: testers get a real
#   https://<name>.trycloudflare.com URL.
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

# Check if the app is running locally. We test BOTH endpoints:
#   /api/health  — liveness; if this is down, the process is dead
#   /api/ready   — readiness; if this is 503, the DB or Ollama is
#                  unhealthy, so the tunnel would just proxy 503s
# Cloudflare works either way, but we warn the user so they know
# the upstream is broken before they debug the tunnel.
if ! curl -sf "http://localhost:${APP_PORT}/api/health" >/dev/null 2>&1; then
  warn "App not responding on http://localhost:${APP_PORT}/api/health"
  warn "Start the app first:  bash scripts/start.sh"
  warn "Continuing anyway — Cloudflare will work but won't have anything to proxy"
  echo ""
elif ! curl -sf "http://localhost:${APP_PORT}/api/ready" >/dev/null 2>&1; then
  warn "App /api/ready returned non-200 (DB or Ollama may be unhealthy)"
  warn "Tunnel will still work, but requests will return 503 from upstream"
  warn "See: curl http://localhost:${APP_PORT}/api/ready | python3 -m json.tool"
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

  # A named tunnel needs a real hostname on a domain in the Cloudflare
  # account (e.g. learn.yourdomain.com). Priority: --hostname arg >
  # TUNNEL_HOSTNAME env var > interactive prompt.
  # (--permanent was originally written with hostname:"" and no DNS
  # route — the tunnel connected but served nothing, and the smoke
  # test grepped trycloudflare.com which a named tunnel never emits.
  # Fixed 2026-09-18 during the Studio go-live.)
  if [ -z "${TUNNEL_HOSTNAME:-}" ]; then
    echo ""
    info "Setting up PERMANENT tunnel (requires free Cloudflare account)..."
    info "A named tunnel needs a hostname on a domain in your Cloudflare account."
    info "  e.g. learn.yourdomain.com (the domain must be added in"
    info "  Cloudflare dashboard → Add a site first)"
    read -rp "  Hostname for the app [learn.$(whoami).com]: " TUNNEL_HOSTNAME
  fi
  if [ -z "$TUNNEL_HOSTNAME" ]; then
    fail "No hostname provided. Re-run with TUNNEL_HOSTNAME=learn.yourdomain.com bash $0 --permanent"
    exit 1
  fi
  echo ""
  info "Using hostname: ${TUNNEL_HOSTNAME}"
  echo ""

  # Login (opens browser)
  if [ ! -f "$HOME/.cloudflared/cert.pem" ]; then
    info "Step 1/5: Logging into Cloudflare (browser will open)..."
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
    info "Step 2/5: Creating tunnel '$TUNNEL_NAME'..."
    cloudflared tunnel create "$TUNNEL_NAME"
    TUNNEL_ID=$(cloudflared tunnel list 2>/dev/null | grep "$TUNNEL_NAME" | awk '{print $1}')
    ok "Tunnel created: $TUNNEL_ID"
  fi

  # Route DNS: create the CNAME <hostname> → <tunnel-id>.cfargotunnel.com.
  # Idempotent-ish: if the record already exists cloudflared exits
  # non-zero with 'already exists' — that's success for our purposes.
  info "Step 3/5: Routing DNS ${TUNNEL_HOSTNAME} → tunnel..."
  if cloudflared tunnel route dns "$TUNNEL_NAME" "$TUNNEL_HOSTNAME" 2>&1 | grep -qi "already exists"; then
    ok "DNS record already exists for ${TUNNEL_HOSTNAME}"
  else
    ok "DNS routed: ${TUNNEL_HOSTNAME} → ${TUNNEL_ID}.cfargotunnel.com"
  fi

  # Write config file
  CREDS_FILE="$HOME/.cloudflared/${TUNNEL_ID}.json"
  if [ ! -f "$CREDS_FILE" ]; then
    fail "Credentials file not found: $CREDS_FILE"
    fail "Try deleting the tunnel: cloudflared tunnel delete $TUNNEL_NAME"
    exit 1
  fi

  info "Step 4/5: Writing config file ~/.cloudflared/config.yml..."
  cat > "$HOME/.cloudflared/config.yml" << EOF
# Cloudflare Tunnel config for video-learning-app
# Generated by scripts/install-cloudflare-tunnel.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")

tunnel: ${TUNNEL_ID}
credentials-file: ${CREDS_FILE}

ingress:
  - hostname: ${TUNNEL_HOSTNAME}
    service: http://localhost:${APP_PORT}
  # Catch-all (required) — sends 404 for anything else
  - service: http_status:404

loglevel: info
logfile: /var/log/cloudflared.log
EOF

  ok "Config written to ~/.cloudflared/config.yml"

  # Install as a system service (auto-start on boot)
  info "Step 5/5: Installing as a launchd service..."
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

  # Smoke test — poll the public URL (the named-tunnel hostname, NOT a
  # trycloudflare URL) until it answers or we time out.
  echo ""
  info "Smoke-testing https://${TUNNEL_HOSTNAME} (may take up to 30s for DNS to propagate)..."
  TUNNEL_OK=0
  for i in {1..30}; do
    code="$(curl -s -o /dev/null -w "%{http_code}" -m 5 "https://${TUNNEL_HOSTNAME}/api/health" || echo "000")"
    if [ "$code" = "200" ]; then
      TUNNEL_OK=1
      break
    fi
    sleep 1
  done

  echo ""
  echo -e "${BOLD}════════════════════════════════════════════════════════════════${NC}"
  echo -e "${BOLD}🎉 Tunnel is set up!${NC}"
  echo -e "${BOLD}════════════════════════════════════════════════════════════════${NC}"
  echo ""

  echo -e "  Your public URL: ${GREEN}${BOLD}https://${TUNNEL_HOSTNAME}${NC}"
  if [ "$TUNNEL_OK" = "1" ]; then
    ok "Smoke test: HTTP 200 via the public URL"
  else
    warn "Smoke test: no 200 yet (last code: ${code:-none}). DNS can take a minute — retry:"
    echo "    curl -s -o /dev/null -w '%{http_code}\n' https://${TUNNEL_HOSTNAME}/api/health"
  fi

  echo ""
  echo "  ⚠️  REQUIRED before public login works (Day 9 lesson):"
  echo "    Firebase Console → Authentication → Settings → Authorized domains"
  echo "    → add: ${TUNNEL_HOSTNAME}"
  echo "    Then flip COOKIE_SECURE=true in .env and restart the app."
  echo ""
  echo "  Test it:"
  echo "    1. Turn OFF wifi on your phone (use cellular)"
  echo "    2. Open https://${TUNNEL_HOSTNAME} in the phone browser"
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