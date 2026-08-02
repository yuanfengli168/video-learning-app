# Pocket MVP — Remote Access Setup Guide

> How to get the **Pocket** iOS app talking to the **video-learning-app** backend
> when the Mac and iPhone are on **different networks** (e.g. Mac at home in
> Singapore, iPhone with you in China). Covers Tailscale installation on both
> devices, mkcert certificate setup, iOS app configuration, and verification.
>
> **Audience**: A new developer who has just cloned this repo. Takes ~30 min on
> a Mac and ~10 min on an iPhone, both need internet.

---

## TL;DR

1. Mac runs the backend on `https://localhost:8443` with a self-signed
   certificate (mkcert).
2. Tailscale installs on Mac and iPhone, signs both into the same account.
   This makes Mac reachable from iPhone as
   `https://jackys-macbook-pro.tail9eb9d7.ts.net:8443` from anywhere.
3. iPhone installs the mkcert root CA so it trusts the self-signed cert.
4. iPhone opens the Pocket app → gear icon → enters the Tailscale URL → chat
   and sync work from anywhere.

If you just want to test locally (Mac + iPhone on same Wi-Fi), skip the
Tailscale part and use `https://192.168.<LAN_IP>:8443` instead.

---

## Prerequisites

You need:

| | |
|---|---|
| **Mac running the backend** | macOS with `brew`, `xcodegen`, `mkcert` installed (see `doc/pocket-v0.1.1-remote-access.md` §2 for full backend setup) |
| **iPhone** | iOS 16+ (for Developer Mode + profile install) |
| **Apple ID** | Same one on both devices (or sign into Tailscale with same email) |
| **Internet** | Both devices connected |
| **~30 min** | 15 on Mac, 10 on iPhone, 5 for verification |

---

## Part 1: Mac setup (one-time)

### 1.1 Install Tailscale

```bash
brew install --cask tailscale
```

If brew complains about sudo, open the GUI installer instead:

```bash
open /opt/homebrew/Caskroom/tailscale-app/*/Tailscale-*-macos.pkg
```

Then click through the macOS installer GUI.

### 1.2 Sign into Tailscale on Mac

1. Open `Tailscale.app` from `/Applications`
2. Click **"Log In"** in the menu bar item
3. Sign in with your Google / Microsoft / GitHub account
4. Note your **Tailscale hostname** — it appears in the menu bar as
   `something.tail<hash>.ts.net`. Example:
   ```
   jackys-macbook-pro.tail9eb9d7.ts.net
   ```
   You'll use this on the iPhone.

Verify:
```bash
tailscale status
# Should show your Mac with its 100.x.x.x IP
```

### 1.3 Regenerate the macOS cert to include the Tailscale hostname

The default cert only covers `localhost`, `127.0.0.1`, `::1`. The iPhone (on
Tailscale) will see the cert with the **Tailscale hostname** in the URL bar,
not localhost, so the cert must include it in its **Subject Alternative Name
(SAN)** list, otherwise iOS will reject the connection.

Find your Tailscale hostname:

```bash
tailscale status --json | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('hostname:', d.get('MagicDNSSuffix', 'unknown'))
print('node:', d.get('SelfNode', {}).get('Name', 'unknown'))
"
# Or simpler:
tailscale status
# Look for the hostname in the first column
```

Then reissue the cert (replace `<TS_HOSTNAME>` and `<LAN_IP>` with your actual
values):

```bash
cd /Users/jackyli/Desktop/Githubs/video-learning-app

# Find your Mac's LAN IP (only needed for same-Wi-Fi use)
LAN_IP=$(ipconfig getifaddr en0)
TS_HOSTNAME=$(tailscale status | head -1 | awk '{print $2}')

mkcert -key-file certs/localhost-key.pem \
       -cert-file certs/localhost.pem \
       localhost 127.0.0.1 ::1 $LAN_IP $TS_HOSTNAME
```

Verify the cert includes the new entries:

```bash
openssl x509 -in certs/localhost.pem -noout -text | grep -A1 "Subject Alternative Name"
# Should now include localhost, 127.0.0.1, ::1, your LAN IP, your Tailscale hostname
```

### 1.4 Start the backend on the iOS HTTPS port (8443)

```bash
cd /Users/jackyli/Desktop/Githubs/video-learning-app

# Make sure no uvicorn from a prior session is hanging around
bash scripts/stop.sh

# Activate venv (or use whatever your project uses)
source venv/bin/activate

# Start backend on HTTPS port 8443
nohup uvicorn app.main:app --reload --host 0.0.0.0 --port 8443 \
  --ssl-keyfile /Users/jackyli/Desktop/Githubs/video-learning-app/certs/localhost-key.pem \
  --ssl-certfile /Users/jackyli/Desktop/Githubs/video-learning-app/certs/localhost.pem \
  --h11-max-incomplete-event-size 67108864 \
  > logs/server-ios.log 2>&1 &
```

Verify:

```bash
# Should return JSON with status:ok
curl -ks https://localhost:8443/api/health
curl -ks https://$LAN_IP:8443/api/health
curl -ks https://$TS_HOSTNAME:8443/api/health
```

All three should return `{"status":"ok",...}`.

---

## Part 2: iPhone setup (one-time per device)

### 2.1 Install Tailscale on iPhone

1. Open **App Store** on iPhone
2. Search **"Tailscale"** → tap **Get** → Install
3. Open the Tailscale app
4. Tap **"Turn on Tailscale"** → grant VPN permission
5. Sign in with the **same account** as on Mac
6. Tailscale will show your iPhone's name, e.g. `iphone182`, with status
   **Connected**

Verify from Mac:
```bash
tailscale status
# Should show both Mac and iPhone in the list
```

### 2.2 Install the mkcert root CA on iPhone

The Mac uses a self-signed cert from mkcert. iOS doesn't trust this by
default. We need to install the mkcert root CA so iOS trusts it.

**Find the root CA on Mac:**

```bash
echo "$(mkcert -CAROOT)/rootCA.pem"
# Typically: /Users/<you>/Library/Application Support/mkcert/rootCA.pem
```

**Get it onto the iPhone.** There are 3 ways, pick one:

#### Option A: HTTP server (works cross-network via Tailscale)

On Mac, serve the cert over a one-shot HTTP server:

```bash
# Make sure the cert is accessible in a directory
mkdir -p /tmp/certshare
cp "$(mkcert -CAROOT)/rootCA.pem" /tmp/certshare/mkcert-rootCA.pem

# Serve on port 8888 (Tailscale will route this)
cd /tmp/certshare
nohup python3 -m http.server 8888 --bind 0.0.0.0 \
  > /tmp/certshare.log 2>&1 &
echo "HTTP server started. Tail its IP:"
echo "  http://$(tailscale ip | head -1):8888/mkcert-rootCA.pem"
```

On iPhone Safari, visit `http://<Tailscale_IP>:8888/mkcert-rootCA.pem`. iOS
will ask if you want to download the configuration profile → tap **Allow**.

When done:
```bash
pkill -f "http.server 8888"
```

#### Option B: AirDrop (works only when both on same Wi-Fi or Bluetooth)

1. On Mac, in Finder, navigate to `$(mkcert -CAROOT)/`
2. Right-click `rootCA.pem` → **Share** → **AirDrop** → tap your iPhone
3. iPhone prompts to open with Profiles → tap **Allow**

#### Option C: Email / iCloud / cloud drive

1. Upload `rootCA.pem` to iCloud Drive, Dropbox, Gmail, etc.
2. On iPhone, open the file from the relevant app
3. iOS will offer to open with Settings

### 2.3 Install the profile on iPhone

After the file is downloaded:

1. Open **Settings** app
2. You should see **"Profile Downloaded"** at the top → tap it
   (or: **General → VPN & Device Management** → tap the profile)
3. Top right says **Install** → tap it
4. Enter your iPhone passcode
5. Tap **Install** again (red warning is normal — non-Apple-signed profiles)
6. Tap **Done**

### 2.4 Trust the root CA (CRITICAL — easy to skip!)

1. **Settings → General → About**
2. Scroll all the way down → tap **Certificate Trust Settings**
3. Under **"Enable Full Trust for Root Certificates"** you'll see an entry
   starting with `mkcert...`
4. **Toggle it ON** (green)
5. Tap **Continue** on the warning

If the toggle is grayed out, you need to set an iPhone passcode first.

### 2.5 Verify from iPhone Safari

Force-restart Safari (swipe up in app switcher, swipe Safari away, reopen),
then visit:

```
https://<your-mac-tailscale-hostname>:8443/api/health
```

Should return JSON: `{"status":"ok","app":"Video Learning App",...}`.

If you see "Not Private" warning, the trust step (§2.4) didn't take — go back
and ensure the toggle is ON.

---

## Part 3: Pocket app on iPhone (once cert trust is verified)

### 3.1 Get the Pocket app onto the iPhone

The Pocket iOS app is not on the App Store yet. You'll need to build it from
source via Xcode.

1. On Mac, plug iPhone in via USB
2. Open `ios/PocketMVP/PocketMVP.xcodeproj` in Xcode
3. Xcode → Settings → Accounts → sign in with your Apple ID
4. In the project file editor, set **DEVELOPMENT_TEAM** to your Team ID
   (or use "Personal Team" for free 7-day signing)
5. Select your iPhone in the top bar as the build destination
6. Click ▶︎ Run — Xcode will build, sign, and install

> ⚠️ **Free Personal Team caveat**: Apps signed with a free Personal Team
> expire every 7 days. You'll need to re-sign by re-running from Xcode
> (with iPhone plugged in via USB, or wirelessly if paired on same Wi-Fi).
> Paid Apple Developer account ($99/yr) gives 365-day signing.

### 3.2 Enable Developer Mode on iPhone (one-time)

If you've never run an unsigned app on this iPhone:

1. **iPhone → Settings → Privacy & Security → Developer Mode**
2. Toggle **ON**
3. iPhone will restart
4. After restart, confirm "Turn on Developer Mode" → Restart

### 3.3 Configure the base URL in Pocket

1. Launch **Pocket** on iPhone
2. Tap the **⚙️ gear icon** (top-right of course list)
3. **Backend URL** field — enter one of:
   - **Tailscale** (works from anywhere): `https://<mac-ts-hostname>:8443`
   - **Same Wi-Fi only**: `https://<mac-LAN-IP>:8443`
   - **Simulator only**: `https://localhost:8443` (default, only works on
     iOS Simulator)
4. Tap **Test connection** — should show green ✅ "Connected"
5. Tap **Done**

### 3.4 Sign in

For Firebase sign-in, follow the in-app flow.

For local development (no Firebase configured), use the **"Skip sign-in
(dev)"** button at the bottom of the login screen. This uses the
`X-Dev-User-Id` header fallback. Requires the backend to be started with
`POCKET_DEV_AUTH=1` env var.

### 3.5 Verify it works

1. Open a course
2. Open a video → tap Pocket Tutor → ask a question
3. You should get a response from Ollama

If anything fails, see [Troubleshooting](#troubleshooting) below.

---

## Troubleshooting

### Safari shows "This Connection Is Not Private"

- The mkcert root CA isn't installed or trusted. Repeat Part 2.3 + 2.4.
- Force-restart Safari (swipe away in app switcher) to clear cached cert
  validation state.

### Safari shows "Safari cannot open the page because the server unexpectedly closed the connection"

- TLS error. iPhone doesn't trust the cert. Same as above.

### Safari shows "Safari cannot connect to server"

- Tailscale is OFF on iPhone (check Tailscale app → should be Connected).
- Mac firewall is blocking port 8443. On Mac: System Settings → Network →
  Firewall → Allow python3 to accept incoming connections.
- Mac is asleep. Disable sleep on Mac: System Settings → Energy → set "Wake
  for network access" to Always.

### Test connection in Pocket shows red error

- Verify the URL is correct. Format: `https://<host>:8443` — **no trailing
  slash, no path**.
- Tap "Examples" in Settings to see correct format for each scenario.
- If the error mentions "Cert error — install mkcert root CA on iPhone",
  the trust step wasn't done. Repeat Part 2.4.

### Tailscale shows "Disconnected" or "Needs login" on iPhone

- Open Tailscale app on iPhone
- Tap "Turn on Tailscale" / re-login if needed
- iPhone must allow VPN profile installation

### Cert SAN doesn't include my hostname

If you changed your Tailscale hostname, Mac's LAN IP, or added a new device:

```bash
cd /Users/jackyli/Desktop/Githubs/video-learning-app

LAN_IP=$(ipconfig getifaddr en0)
TS_HOSTNAME=$(tailscale status | head -1 | awk '{print $2}')

mkcert -key-file certs/localhost-key.pem \
       -cert-file certs/localhost.pem \
       localhost 127.0.0.1 ::1 $LAN_IP $TS_HOSTNAME

# Restart backend to pick up new cert
bash scripts/stop.sh
# ... then restart as in §1.4
```

### App on iPhone shows "Cannot connect to developer" after 7 days

Your app was signed with a free Personal Team and the signature expired.
Solutions:

- **Option A** (recommended): Get a paid Apple Developer account
  ($99/yr). 365-day signing.
- **Option B**: Plug iPhone into Mac via USB → Xcode → re-build & re-run.
  Each re-build gives a fresh 7 days.
- **Option C**: Use Xcode's wireless debug (iPhone + Mac on same Wi-Fi) to
  re-sign without USB. Note: this does NOT work over Tailscale — both
  devices must be on the same physical LAN.

---

## Quick reference: URLs

| Scenario | URL |
|---|---|
| iOS Simulator on same Mac | `https://localhost:8443` |
| iPhone + Mac on same Wi-Fi | `https://<mac-LAN-IP>:8443` (find via `ipconfig getifaddr en0`) |
| iPhone anywhere via Tailscale | `https://<mac-tailnet-hostname>:8443` (find via `tailscale status`) |
| Direct browser test from Mac | `curl -ks https://localhost:8443/api/health` |

## Quick reference: ports

| Port | Purpose |
|---|---|
| 8000 | Backend HTTP (Mac web app, no HTTPS) |
| 8443 | Backend HTTPS (iOS Pocket app, mkcert-signed) |
| 8888 | One-shot HTTP server for cert sharing (temporary) |

## Quick reference: paths

| Path | What |
|---|---|
| `certs/localhost.pem` | Server cert (regenerated with SAN list) |
| `certs/localhost-key.pem` | Server key |
| `$(mkcert -CAROOT)/rootCA.pem` | Root CA (installed on iPhone) |
| `ios/PocketMVP/SETUP.md` | This file |
| `doc/pocket-v0.1.1-remote-access.md` | Detailed design doc + history |

---

## Why this is set up this way

**Q: Why Tailscale instead of a public domain?**
A: Tailscale gives a stable hostname, end-to-end encryption, no DNS / cert /
firewall juggling, no public exposure, and works from China. Public domain
needs DNS, port-forwarding, cert renewal, and is at the mercy of the GFW.

**Q: Why mkcert instead of Let's Encrypt?**
A: Let's Encrypt certs are for public domains — they can't be issued for
`.ts.net` hostnames or LAN IPs. mkcert creates a local CA that's trusted
only by devices where you've installed the root CA. Perfect for dev/PoC.

**Q: Why HTTPS at all? Why not plain HTTP?**
A: iOS App Transport Security blocks plain HTTP for arbitrary hosts by
default. ATS exceptions work but require per-host configuration. HTTPS
with a trusted cert is simpler and matches how the app would work in
production.

---

## See also

- `doc/pocket-v0.1.1-remote-access.md` — Detailed design doc, history,
  alternatives considered, future plans (stale-data banner, QR scanner,
  mDNS auto-discovery)
- `doc/pocket-v0.1-plan.md` — Original Pocket v0.1 MVP plan
- `doc/MVP0.2-materials.md` — Course materials feature that runs on the
  same backend