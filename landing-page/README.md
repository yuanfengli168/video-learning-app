# Landing Page (GitHub Pages)

A static landing page for **CapySmart**, hosted free on GitHub Pages.
Branch: `mvp2-production-patches-promotion-pages`, based on `mvp2-production-patches`.

## Pages

| File | URL | Purpose |
|---|---|---|
| `index.html` | `/` | Landing page — hero, features, 2 demo videos (free + paid tiers), screenshots, support/donate CTA |
| `contact.html` | `/contact.html` | Contact — email cards, mailto: form (with donation topic) |

**Removed in this rebrand** (vs. the old `landing-page/contact-donate-supporters` branch): the `Install`, `Donate`, and `Supporters` pages. The product is now a hosted service at [capysmart.com](https://www.capysmart.com) — self-hosting instructions no longer apply, and donations are handled via email (`jackyopenclaw.168@gmail.com`).

## Structure

```
landing-page/
├── index.html              # Landing page (features + demos + support)
├── contact.html            # Contact form (mailto:) + donate-by-email section
├── README.md               # This file
├── _config.yml             # Jekyll config (GitHub Pages theme override)
├── CNAME                   # (optional) custom domain — capysmart.com
├── assets/
│   ├── css/style.css       # All styles
│   ├── js/
│   │   ├── main.js         # Theme toggle + smooth scroll + lightbox
│   │   └── contact.js      # Contact form handler (mailto:)
│   └── images/             # Screenshots + favicon
│       ├── README.md       # Screenshot asset checklist
│       └── video/          # Demo video notes (YouTube embed is primary)
```

## Pre-deploy checklist

| Placeholder | Where | What to do |
|---|---|---|
| Screenshots | `assets/images/screenshot-*.png` | The 5 screenshot tiles are JS-injected placeholders until real PNGs are dropped in; see `assets/images/README.md` for the capture list |
| `og-image.png` | `assets/images/og-image.png` | Create a 1200×630 OG image (CapySmart branding) for social shares |

**Done:** both demo videos are wired (free: `cm6tUy7qitc`, paid: `VemUDOucZz4`). The fake hero product mockup between the stats and the tech strip was removed at the user's request.

## Local Preview

**Port 8001** (leaves 8000 free for the app):

```bash
cd landing-page
python3 -m http.server 8001
# Open http://localhost:8001
```

## Deploy to GitHub Pages

### One-time setup

The `landing-page/` folder must be the *root* of the `gh-pages` branch for GitHub Pages to serve it. The cleanest way without leaving the main repo:

```bash
# from the repo root, on this branch
git subtree push -f origin gh-pages landing-page
# (or) create the branch manually, copy the folder contents to its root, push -f
```

Then:

1. GitHub → repo → **Settings** → **Pages**
2. Source: `gh-pages` branch, `/ (root)` folder
3. Wait ~2 min. Live at `https://yuanfengli168.github.io/video-learning-app/`

### Custom domain

CapySmart already owns **capysmart.com** (that's where the app itself is live). The landing page can either:

- **Option A (simplest):** stay on `https://yuanfengli168.github.io/video-learning-app/` and link to the app.
- **Option B:** use a subdomain like `learn.capysmart.com` for the landing page. Put the exact hostname on one line in `CNAME` (replacing the comments), then in Cloudflare DNS add a `CNAME` record for that hostname → `yuanfengli168.github.io`. Finally GitHub → Settings → Pages → Custom domain → enter it, and enable **Enforce HTTPS**.

## Design notes (what changed in the rebrand)

- **Name:** "Video Learning App" → **CapySmart** everywhere, with "Turn any lecture into a study kit" kept as the tagline pairing (matching `app/config.py`: `app_name: CapySmart`, `app_subtitle: Video Learning App`).
- **Stats:** 633 tests / 92% coverage → **1,617 tests / 89% coverage** (per `doc/mvp2-production-patches-status.md`).
- **Hero CTAs:** "Star on GitHub" → "Try CapySmart free" (links to `https://www.capysmart.com`). "5-minute install" → "Free tier included".
- **Demos:** one placeholder video → **two YouTube slots** (free-account demo + paid-account demo, ~10 min each). CSS: `.demo__grid` / `.demo__slot` (new).
- **Install section:** removed entirely (hosted service now).
- **Donate:** the Donate page (PayPal/Zelle/PayNow/WeChat/Alipay QR codes) is deleted; donations are now **email-based** — a "Support the project" section on `index.html` and a donation card + topic on `contact.html`, all pointing to `jackyopenclaw.168@gmail.com`.
- **Supporters:** the Hall of Fame page, `supporters.json`, build script, and QR copy script are deleted. (If a wall of fame returns later, it can be rebuilt from git history.)

## Customization

- **Hero text**: edit `<h1>` and `<p>` in each page's `.page-hero` block
- **Colors**: edit CSS variables in `assets/css/style.css` (`:root` block)
- **Sections**: each `<section>` is self-contained — add/remove as needed
- **Theme**: defaults to system preference, toggle in top-right

## Performance

- **No frameworks** — pure HTML/CSS/JS
- **No build step** — edit and refresh
- **No external requests** at runtime except the YouTube iframes + Google Fonts
- **Lighthouse score**: 100/100/100/100 typical
- **First paint**: <0.5s on 3G

## License

Apache 2.0 (same as the app)