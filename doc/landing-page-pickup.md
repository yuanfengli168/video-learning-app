# Landing Page — Pickup Handover

> **Status:** Paused. MVP3.0 takes priority; merge MVP2.1 + `landing-page/contact-donate-supporters` into `main` first (planned in this session), then come back here for the remaining tasks below.
> **Branch on disk:** `landing-page/contact-donate-supporters` (preserved, uncommitted work was committed before merge)
> **Latest commit:** `60602f7` — `Landing page: add Contact, Donate, Supporters + YouTube demo embed`

---

## 1. What's already shipped (do NOT redo)

These are merged into `main` after this session:

- ✅ 3 new pages: `contact.html`, `donate.html`, `supporters.html` in `landing-page/`
- ✅ YouTube embed slot in `landing-page/index.html` line 274 (placeholder, see §3)
- ✅ `supporters.json` schema + `supporters.schema.json` doc + `assets/js/supporters.js`
- ✅ `scripts/build_supporters.py` — XLSX/CSV → `supporters.json` converter
- ✅ `scripts/copy_personal_assets.sh` — copy `.local` QRs over placeholders
- ✅ `.gitignore` — pattern `*\.local.*` blocks real QRs from any commit
- ✅ PayPal.me handle wired: `paypal.me/jackyLi168` (5 buttons)
- ✅ Zelle ID wired: `jackyopenclaw.168@gmail.com`
- ✅ Contact form uses `mailto:` (Formspree dropped — no third-party)
- ✅ Test passed locally: Zelle QR `1320×1752`, 150,416 bytes, served at `/assets/images/donate/zelle.png`
- ✅ All 633 tests green

---

## 2. Unfinished tasks — pick up here

### 2.1 Real QR codes — drop in 3 more (5 min total)

`zelle.png` is already done as proof of concept. Do the same for the other 3:

```bash
cd landing-page

# 1. Drop your real QR with the .local. pattern anywhere in the filename
cp ~/Downloads/<paynow-qr>.png    assets/images/donate/paynow.png.local.PNG
cp ~/Downloads/<wechat-qr>.jpg    assets/images/donate/wechat.png.local.jpg
cp ~/Downloads/<alipay-qr>.png    assets/images/donate/alipay.png.local.PNG

# 2. Copy over placeholders
./scripts/copy_personal_assets.sh paynow
./scripts/copy_personal_assets.sh wechat
./scripts/copy_personal_assets.sh alipay

# 3. Verify served bytes match source (just like zelle):
curl -s "http://localhost:8001/assets/images/donate/paynow.png" -o /tmp/p.png
shasum /tmp/p.png assets/images/donate/paynow.png.local.PNG
# SHA1 must match
```

The `.local.` substring can sit **anywhere** in the filename. The matcher is `*\.local.*` (case-insensitive glob). Already verified with these patterns:

| Pattern you might save as | Status |
|---|---|
| `zelle.png.local.PNG` | ignored ✓ |
| `zelle.png.local.png` | ignored ✓ |
| `my.zelle.local.png` | ignored ✓ |
| `.zelle.png.local` | ignored ✓ |
| `ZELLE.PNG.LOCAL.PNG` | ignored ✓ |
| `zelle.png` (the placeholder) | tracked ✓ (explicit allow rule) |

### 2.2 YouTube demo video (10 min)

`landing-page/index.html` line 274 has the placeholder:

```html
<!-- YouTube embed (primary) — change YOUR_VIDEO_ID to your real demo -->
<iframe ... src="https://www.youtube.com/embed/YOUR_VIDEO_ID?rel=0&amp;modestbranding=1" ...>
```

Steps:
1. Upload the demo MP4 to YouTube (≤ 15 min typical, "unlisted" is fine for testing)
2. Copy the video id (`dQw4w9WgXcQ` style — 11 chars after `youtu.be/` or from `/watch?v=`)
3. Replace `YOUR_VIDEO_ID` on `index.html:274`
4. Confirm `<noscript>` fallback link on line ~283 also uses the same id
5. Test by `python3 -m http.server 8001` → open `http://localhost:8001/`, scroll to "See it in action"

### 2.3 Deploy to GitHub Pages (15 min, optional but recommended)

The `landing-page/` folder is set up to serve as a static site. To make it live:

```bash
# Make sure you're on main (we'll do this in §4 today)
cd /Users/jackyli/Desktop/Githubs/video-learning-app
git status      # should say "On branch main, nothing to commit"
git push        # we did this already in this session
```

Then on GitHub:

1. Settings → Pages
2. Source: **Deploy from a branch**
3. Branch: **`main`** / **`/landing-page`** folder
4. Save → wait ~60 s → your CNAME domain (if configured in `_config.yml`) will resolve

If you haven't set a custom domain yet, the page will live at `https://<user>.github.io/video-learning-app/landing-page/` until then.

### 2.4 (Optional) Restore the Zelle QR before any push to public

After this session committed, `landing-page/assets/images/donate/zelle.png` is back to the **917-byte gray placeholder**. To re-deploy with your real Zelle QR:

```bash
cd landing-page
./scripts/copy_personal_assets.sh zelle
python3 -m http.server 8001 &
# verify in browser, then Ctrl-C the server before commit
# (the placeholder is reverted by `git checkout -- assets/images/donate/zelle.png`)
```

---

## 3. Key file references (so you don't grep for them)

| Topic | File | Line(s) |
|---|---|---|
| YouTube placeholder | `landing-page/index.html` | 269 (comment), 274 (iframe src) |
| PayPal.me buttons | `landing-page/donate.html` | several `.btn-pay` `<a>` tags |
| Zelle card | `landing-page/donate.html` | search `.pay-card--region` |
| Talk-tier cap (4/month) | `landing-page/supporters.json` | line 7 (`monthly_caps.talk: 4`) |
| Talk-tier badge | `landing-page/supporters.html` | 137 (`.tier-badge--talk`), 154 (filter button) |
| Contact email | `landing-page/contact.html` | 105, 113, 180, 219 |
| Mailto JS handler | `landing-page/assets/js/contact.js` | top comment + handler |
| Real-QR copy script | `landing-page/scripts/copy_personal_assets.sh` | whole file |
| QR gitignore | `landing-page/.gitignore` | `*\.local.*` pattern |
| Marketing assets | `doc/marketing/` | `social-captions.md`, `video-demo-script.md`, `README.md` |

---

## 4. Triage flow after MVP3.0

Recommended order when you return:

1. **§2.1** (QRs) — fastest win, real motivation to see the page go live
2. **§2.2** (YouTube demo) — needs the demo MP4 ready
3. **§2.3** (deploy) — once §2.1 + §2.2 done
4. **§2.4** — only at deploy time

If MVP3.0 takes >2 weeks, just `git checkout` the `landing-page/contact-donate-supporters` branch and pick up directly from §2.1.

---

## 5. Known gotchas (worth not relearning)

- **`start_new_session=True`** on ffmpeg subprocess — needed so `--reload` doesn't kill orphans. Already in `app/services/plugins.py` and `app/workers/plugin_pool.py` (committed in v2.1.0.4 / `635fa8e`).
- **VideoToolbox vs CQP**: don't switch back to `-q:v 65`. CBR `-b:v 2000k` is correct — CQP produces 50% larger files on hardware encoder.
- **`pool_size=10, max_overflow=20, pool_timeout=30`** in `app/database.py` for SQLite (skipped for `:memory:`). Do not lower — long transcodes need the headroom.
- **Talk tier cap**: the `4/month` value lives only in `supporters.json` as a hint; actual enforcement is **manual** (you decide who books slots and tag them in the spreadsheet). The cap is a UX hint, not a gate.

---

*Document created on the way out the door to MVP3.0. — GitHub Copilot (assistant session)*
