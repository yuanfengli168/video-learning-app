# Security Policy

This document covers the security model of the Video Learning App, what data is stored where, and how to report a vulnerability.

---

## 🔒 Threat model

The app is **single-user, local-first** by design (see [MVP1.0-successfullyFinished.md](MVP1.0-successfullyFinished.md)). The threat model is:

- ✅ **In scope:** accidental commits of secrets, local file-system access, basic web attacks (CSRF, XSS, IDOR)
- ❌ **Out of scope (MVP1):** multi-tenant data isolation, rate limiting, audit logs, key rotation — these are MVP2 concerns

If you're deploying this app for **multiple users** (e.g. on Render.com with the free PostgreSQL backend), the [deployment guide](deployment.md) notes that MVP2 hardening (PostgreSQL row-level security, S3 with proper ACLs, rate limiting) is still required.

---

## 📂 What data lives where

| Data | Location | Git-tracked? | Notes |
|---|---|---|---|
| **Source code** | `app/`, `tests/`, `scripts/` | ✅ Yes | The whole point of git |
| **Firebase service account key** | `firebase-service-account.json` (project root) | ❌ No — gitignored | This file is **highly sensitive**. Never commit. If leaked, rotate immediately in Firebase Console |
| **User-uploaded videos** | `uploads/<video_id>.mp4` | ❌ No — gitignored | Owned by the user who uploaded. Disk content can grow large |
| **Generated assets** (transcripts, summaries, mindmap markdown, topic timestamps) | `storage/` | ❌ No — gitignored | SQLite is the source of truth; this is a cache |
| **SQLite database** | `video_learning.db` | ❌ No — gitignored (`*.db` pattern) | Contains user accounts (Firebase UIDs), course structure, chat history |
| **Virtual environment** | `venv/` | ❌ No — gitignored | |
| **`.env` file** | project root | ❌ No — gitignored | Real Firebase config; use `.env.example` as the template |
| **Browser session cookie** | `fb_token` (httpOnly, sameSite=lax) | ❌ N/A | httpOnly so JS can't read it; sameSite=lax so cross-origin POSTs can't use it |
| **Firebase auth cache** | Browser IndexedDB (Firebase `browserLocalPersistence`) | ❌ N/A | Cleared by [`scripts/setup.sh`](../scripts/setup.sh) / [login.html](../app/templates/login.html) on signOut |

---

## ✅ What the app does to protect data

### Authentication
- Firebase ID tokens verified by **Firebase Admin SDK** ([app/auth/firebase_admin.py](../app/auth/firebase_admin.py)) — no JWT secret in our code, so no risk of us leaking a signing key
- `verify_id_token(id_token, check_revoked=True)` — even a leaked token is invalid after the user signs out
- httpOnly session cookie (`fb_token`) — JS can't exfiltrate it via XSS
- `Secure=False` in dev, `True` in production (one-line flip in [app/auth/session.py](../app/auth/session.py) when behind HTTPS)

### Authorization (ownership checks)
Every endpoint that touches user data verifies the caller owns the resource. Examples:

- [app/routers/videos.py:267](../app/routers/videos.py) — `if course.user_id != user.get("uid", ""): raise 403`
- [app/routers/chat.py](../app/routers/chat.py) — same check before creating a chat session
- [app/routers/courses.py](../app/routers/courses.py) — courses are scoped to `user_id` from the start

### Input sanitization
- All user-supplied text rendered as HTML goes through `escapeHtml()` ([app/templates/base.html](../app/templates/base.html)) before insertion via `innerHTML`
- SQLAlchemy ORM is used everywhere — **no raw SQL**, so SQL injection is not possible
- File uploads are validated for content type (multipart/form-data) and saved with a **random UUID filename** ([app/routers/videos.py:69](../app/routers/videos.py)) — original filenames are never used as filesystem paths

### CORS
- FastAPI defaults to no CORS — the API is same-origin only (the Jinja2 templates and the API live on the same port). Cross-origin requests are blocked by the browser.

### CSRF
- The session cookie is `sameSite=lax`, which blocks cross-origin POSTs (the only mutating verb in our API)
- State-changing endpoints (POST/DELETE) require a valid session cookie AND a valid Firebase token, both of which the browser only sends for same-origin requests

---

## ⚠️ Known limitations (intentional, MVP2)

These are NOT vulnerabilities today (MVP1 is single-user) but become real issues if you scale to multi-tenant without MVP2 hardening:

| Limitation | MVP2 fix |
|---|---|
| No rate limiting on auth endpoints | Add Redis-backed rate limit middleware |
| No brute-force protection on `/api/auth/session` | Same as above; ideally Firebase's built-in throttling covers this |
| No audit log of who accessed what | Add a `access_log` table; log every GET/POST/DELETE with uid + resource_id |
| No key rotation reminder | Add a startup warning if `firebase-service-account.json` is older than 90 days |
| No CSP / X-Frame-Options headers | Add `Content-Security-Policy`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` middleware |
| `secure=False` on session cookie (dev only) | Document HTTPS requirement in deployment.md (already done) |

---

## 🚨 How to report a vulnerability

**Please do NOT open a public GitHub issue for security problems.**

Email `jacky@...` (your contact) with:
1. **What** you found (XSS, IDOR, secret leak, etc.)
2. **Where** — file path or endpoint
3. **Repro** — minimal steps to reproduce
4. **Impact** — what an attacker could do with it

You'll get a response within 48 hours. Critical issues get a patch within 7 days; non-critical within 30 days.

---

## 🛡️ Pre-commit secret scan (recommended)

To avoid the "I accidentally committed a Firebase key" nightmare, enable a pre-commit hook:

```bash
# Install pre-commit (one-time)
pip install pre-commit

# .pre-commit-config.yaml at project root:
cat > .pre-commit-config.yaml <<'EOF'
repos:
  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.4.0
    hooks:
      - id: detect-secrets
        args: ['--baseline', '.secrets.baseline']
EOF

# One-time baseline (scans existing code, whitelists everything that's not actually a secret)
detect-secrets scan > .secrets.baseline

# Install the hook
pre-commit install
```

This is **not currently set up** in the repo — it's a recommendation for when you onboard collaborators.

---

## 📋 Security audit history

| Date | Result | Action |
|---|---|---|
| 2026-07-06 | Initial audit before v1.0.0 release | No issues found. `.gitignore` already covers all sensitive files; no secrets in git history (verified with `git ls-files \| grep -iE "firebase\|secret"`); ownership checks in place on all data-touching endpoints. Wrote this document. |

---

**Last reviewed:** 2026-07-06 · commit `38a2f67` (v1.0.0)
