# Roles & Tiers — Operational Cheatsheet

> **Status**: Living reference — keep in sync with `app/auth/roles.py` + `app/config.py`.
> **Related**: `mvp2-roles-and-access.md` (original design), `mlx-fork-crash-postmortem.md` (incident that exposed the role bug).
> **Last updated**: 2026-09-05 (after the role-enrichment fix `e079356`)

This is the one-page answer to "who gets what", verified against the
code — plus the failure modes we already hit so they don't recur.

---

## 1. The three roles (users.role column, IntEnum — NEVER renumber)

| Value | Role | Meaning |
|---|---|---|
| **0** | ADMIN | Full access: curate global catalog, regenerate, manage users, run plugins, admin dashboard |
| **1** | PAID | Subscriber: own courses/sections/uploads, regenerate materials, paid LLM chain |
| **2** | FREE | Default: browse PUBLIC catalog + rate-limited Groq chat only |

Lower number = more privilege. Future roles (EDUCATION/TRIAL/BETA/ENTERPRISE)
append at the end — existing rows never renumber.

## 2. Capability matrix (`ROLE_CAPABILITIES` in `app/auth/roles.py`)

| Capability | ADMIN | PAID | FREE |
|---|:-:|:-:|:-:|
| VIEW_VIDEO (browse catalog) | ✅ | ✅ | ✅ |
| CHAT_FREE (Groq, rate-limited) | ✅ | ✅ | ✅ |
| CHAT_PAID (Ollama Pro) | ✅ | ✅ | ❌ |
| UPLOAD_VIDEO | ✅ | ✅ | ❌ |
| REGEN_MATERIALS (re-run LLM) | ✅ | ✅ *own videos only* (`e2f4aa8`) | ❌ |
| MANAGE_OWN_COURSE | ✅ | ✅ | ❌ |
| CURATE_CATALOG (global catalog) | ✅ | ❌ | ❌ |
| RUN_PLUGIN (ffmpeg transcodes) | ✅ | ❌ | ❌ |
| MANAGE_USERS (roles, audit log) | ✅ | ❌ | ❌ |
| VIEW_ADMIN_DASHBOARD (`/admin/*`) | ✅ | ❌ | ❌ |

Enforcement: `@require_capability(Capability.X)` dependency (DB lookup,
lru-cached, per-request).

## 3. Video visibility matrix (`videos.visibility`, "lower = more public")

| Visibility | Who sees it |
|---|---|
| PUBLIC (0) | Everyone incl. FREE |
| PAID_ONLY (1) | PAID + ADMIN |
| ADMIN_ONLY (2) | ADMIN only |

Query pattern: `WHERE visibility <= max_visibility_for_role(role)`.
Unknown/None role → PUBLIC-only (fail-safe most restrictive).

## 4. LLM provider chains (per tier) — `config.get_provider_chain(role)`

| Tier | Chain | Models |
|---|---|---|
| ADMIN / PAID | `ollama` → `openai` | `glm-5.2:cloud` → `gpt-4o-mini` fallback |
| FREE | `groq` (single provider, no retry) | `groq/compound-mini` |

**Groq is never used for paid tiers** (quality), **Ollama is never used
for free** (cost). This is why a mis-tiered user is a *functional*
failure, not just a policy violation.

## 5. Rate limits per tier

| Tier | Per minute | Per day |
|---|---|---|
| FREE | 5 | 15 |
| PAID | 15 | 200 |
| ADMIN | 60 | 1000 |

Plus global quota trackers: Ollama Pro 800 req/5h + 3000/week
(alert at 90%); Groq free tier 250 req/day **total across all users**.

---

## 6. How role flows through the system (the part that broke)

```
Firebase ID token ──verify──> claims dict (uid, email — NO role!)
                                    │
                     get_current_user / _optional   [auth/dependencies.py]
                                    │  ← 2026-09-05 fix: joins DB role here
                                    ▼
                     claims["role"] = int(get_user_role_from_db(uid, db))
                                    │
        ┌───────────────────────────┼─────────────────────────────┐
        ▼                           ▼                             ▼
require_capability(...)    user.get("role") consumers:    user_can_access_video()
(DB lookup, own path)     - tier LLM chain (_run_generate_job)
                          - rate limit selection
                          - visibility gates (chat/videos/generation)
```

**Two role resolution paths exist:**
1. `require_capability` — does its own DB lookup (always worked)
2. `user.get("role")` consumers — read the claims dict (was broken until
   2026-09-05; the enrichment in `get_current_user` fixed all 9 sites)

## 7. Failure modes we already hit — don't repeat

### 7.1 "All 1 provider(s) failed" for a PAID user (2026-09-05, fixed `e079356`)
**Symptom**: PAID user's generate ran the groq-only FREE chain; ollama
was healthy. Error text is the tell: **"All 1 provider(s)"** = FREE
chain (paid chain has 2 providers).
**Root cause**: Firebase claims never contain `role`; every
`user.get("role", 2)` silently defaulted to FREE.
**Rule**: any NEW consumer of role MUST either use
`require_capability` or read `claims["role"]` (now guaranteed set for
authenticated users by the enrichment). Never assume Firebase carries
the role.

### 7.2 Silent default masking (the meta-lesson)
`user.get("role", 2)` defaulting silently turned a **missing key** into
"FREE user" behavior — no error, wrong tier. When adding tier-dependent
behavior, prefer explicit resolution + log a warning when the key is
absent rather than a silent `.get()` default.

### 7.3 Role caches
`get_user_role_from_db` is lru-cached per `(uid, role_db_value)` — a
role change takes effect immediately (cache key includes the value),
but **bulk changes should call `clear_role_cache()`**. The session
cookie carries the Firebase token only — a role change needs **no**
re-login (role is joined from DB per request).

### 7.4 PAID regen restriction (`e2f4aa8`)
PAID can regenerate **their OWN uploaded videos only** — never catalog
videos. If a PAID user gets 403 on a catalog video's regen button,
that's the intended behavior.

---

## 8. Quick reference: debugging a tier question

```bash
# What role is a user?
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db \
  "SELECT email, role FROM users WHERE email='<email>';"

# What chain / rate limit does that role get? (in venv)
python -c "from app.config import settings; \
  print(settings.get_provider_chain(1)); \
  print(settings.get_rate_limit_per_min(1))"

# What did the last LLM call actually use? (events table)
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db \
  "SELECT ts, message FROM events WHERE source='services.llm_providers' \
   ORDER BY ts DESC LIMIT 5;"
```

Rule of thumb: `"All N provider(s) failed"` — check N. N=1 → FREE
chain was used (should be 2 for PAID/ADMIN) → suspect role resolution,
not the providers.