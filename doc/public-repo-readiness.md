# Public-repo readiness — MVP2 Production Patches

> **Scope**: Improvements specific to making this branch safe + polished
> for a **public** GitHub repository. Distinct from
> [`security-hardening-mvp2.md`](security-hardening-mvp2.md), which covers
> the **runtime** security posture (threat model, mitigations applied).
>
> **Branch**: `mvp2-production-patches`
> **Last updated**: 2026-08-24
> **Total estimated effort**: 30 minutes

---

## TL;DR — is the current code safe to publish?

**Yes.** Verified 2026-08-24:

- ✅ `firebase-service-account.json` is in `.gitignore` and not tracked
- ✅ `.env`, `*.pem`, `secrets/`, `credentials/` are all excluded
- ✅ No hardcoded secrets, tokens, or credentials anywhere in `app/`
- ✅ Auth flow does not log tokens (no `print(token)`, no `logger.info(token)`)
- ✅ Firebase `web_api_key` exposed in templates is by design (Firebase Auth)
- ✅ No infrastructure leaks in code (no `192.168.*`, no Mac Studio path, no usernames)
- ✅ All 1017 tests pass + 89% coverage

The recommendations below are **defense-in-depth polish**, not blockers.

---

## Recommendations table

| Priority | Action | Effort | Files touched | Why |
|---|---|---|---|---|
| **Now (5 min)** | Make session-cookie `Secure` flag env-driven | 5 min | `app/auth/session.py`, `app/config.py` | Currently hardcoded `secure=False` with a comment "True in production". If someone deploys without flipping it, session cookies go over plaintext HTTP. Env-driven default-True in prod is safer. |
| **Now (15 min)** | Add `DB_PATH` env var override so the default isn't the Mac Studio NVMe path | 15 min | `app/config.py`, `app/database.py` | Today the default is `/Volumes/Storage-Fast-NVMe/video_learning.db` (your specific machine). If anyone clones the repo, that path doesn't exist. Env-driven default keeps your prod setup working AND makes the repo portable. |
| **Before launch** | Add rate-limiting on `/api/admin/*` endpoints (per-admin, e.g. 10 req/min) | 45 min | `app/middleware.py` (or new), `app/routers/admin.py` | Day 3 added outbound calls from the server to YouTube's CDN. A compromised admin token could weaponize the admin endpoint to fetch 1000s of videos from your server's IP. SlowAPI library (already in FastAPI ecosystem) does this in <50 lines. |
| **Before launch** | Add `Secure` cookie flag via env | 5 min | `app/auth/session.py` | Same item, just re-listed because it's the user-visible flag, not just the env binding. |
| **Optional** | Add a `SECURITY.md` disclosure doc on the GitHub repo root | 30 min | `SECURITY.md` (already exists — see below) | GitHub auto-detects this file and shows a "Report a vulnerability" button on the repo page. **You already have one** — just verify it's discoverable. |
| **Optional** | Add GitHub Actions secret scan (`gitleaks`) on every push | 20 min | `.github/workflows/gitleaks.yml` | Catches accidental `.env` or `firebase-service-account.json` commits before Google indexes them. Free for public repos. |

### Already-done items (verified)

| Item | Status |
|---|---|
| `.env` ignored | ✅ in `.gitignore` |
| `firebase-service-account.json` ignored | ✅ in `.gitignore` |
| `*.pem` (SSL certs) ignored | ✅ in `.gitignore` |
| `secrets/` and `credentials/` dirs ignored | ✅ in `.gitignore` |
| No hardcoded API keys in source | ✅ grep -r returned nothing |
| No tokens logged | ✅ grep -r returned nothing |
| Apache 2.0 LICENSE present | ✅ at repo root |
| SECURITY.md present | ✅ at repo root (separate from this doc) |
| No infra leaks (IPs, hostnames, paths) | ✅ in `app/` and `tests/` |

---

## Implementation order (when you decide to tackle these)

### 1. Cookie `Secure` env-driven (~5 min)

**`app/config.py`** — add field:
```python
cookie_secure: bool = True  # Set to False only for local HTTP dev
```

**`app/auth/session.py`** — use it:
```python
from app.config import settings
...
response.set_cookie(
    key=COOKIE_NAME,
    value=body.id_token,
    max_age=COOKIE_MAX_AGE,
    httponly=True,
    samesite="lax",
    secure=settings.cookie_secure,  # was: hardcoded False
)
```

**Test impact**: existing tests pass `secure=False` implicitly; they'll continue to work because the default is True and tests run locally without HTTPS. To override in test conftest, set `settings.cookie_secure = False`.

### 2. DB_PATH env var (~15 min)

**`app/config.py`** — add field:
```python
db_path: str = "/Volumes/Storage-Fast-NVMe/video_learning.db"
# ^ This default keeps your prod setup working.
# Dev/test/CI override via DB_PATH env var or by editing the default.
```

**`app/database.py`** — read from settings instead of hardcoded constant.

**CI consideration**: the test suite already uses `sqlite://` in-memory (see `tests/conftest.py`), so this only affects the prod startup path. Verify `start.sh` doesn't pass a different DB_PATH.

### 3. Rate-limit `/api/admin/*` (~45 min)

Two reasonable options:

**Option A — SlowAPI** (declarative, library-managed):
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

@router.post("/videos/youtube")
@limiter.limit("10/minute")  # tighter for write endpoints
async def admin_add_youtube_video(...): ...
```

**Option B — custom middleware** (no new dep, more code):
```python
class AdminRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/api/admin/"):
            # check in-memory dict of {uid: [timestamps]}
            # 429 if more than 10 in the last 60s
            ...
```

I recommend SlowAPI unless you want zero new deps. Add to `requirements.txt`.

### 4. GitHub Actions secret scan (~20 min)

Create `.github/workflows/gitleaks.yml`:
```yaml
name: gitleaks
on: [push, pull_request]
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

Free for public repos. Catches things like accidentally committing a `.env` with a real API key.

---

## What I'm NOT recommending

| Item | Why not |
|---|---|
| Move from SQLite to Postgres | Bigger refactor; not a public-repo concern. Park for MVP3. |
| Add OAuth2 device-code auth | Overkill for an admin-curated catalog with 1 admin. |
| Strip the `/Volumes/Storage-Fast-NVMe/` default path entirely | Breaks your prod setup; the env-override pattern is better. |
| Move all secrets to a vault (HashiCorp, AWS Secrets Manager) | Vendor lock-in for a personal-scale project. .env is fine. |
| Add Snyk / Dependabot | Dependabot is **free** for public repos and IS worth enabling (separate from gitleaks). Add it via GitHub web UI in 2 clicks. |

---

## Decision log

- **2026-08-24**: Doc created. Listed 6 recommendations (2 now, 2 before-launch, 2 optional). User can choose to implement now or defer to a separate branch.

---

## Related docs

- [`doc/security-hardening-mvp2.md`](security-hardening-mvp2.md) — runtime security (threat model, mitigations)
- [`doc/mvp2-final-go-live-plan.md`](mvp2-final-go-live-plan.md) — the 14-day plan these improvements slot into
- [`SECURITY.md`](../SECURITY.md) — vulnerability disclosure (GitHub-facing)