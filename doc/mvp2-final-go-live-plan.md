# MVP2 Go-Live Plan — Read-Heavy, Admin-Curated, Groq-Powered

> **Status**: Planning finalized; storage setup in progress.
> **Tagline**: "I curate the best videos on the internet. You watch, learn, and chat."
> **Launch target**: 2026-09-09 (~14 days from 2026-08-22 plan-finalization)
> **Last updated**: 2026-08-22
> **Related docs**:
> - `mvp2-storage-architecture.md` — storage design + RAID rationale
> - `mvp2-raid-setup-log.md` — exact setup commands run on 2026-08-22
> - `mvp2-llm-architecture.md` — LLM providers, caching, flywheel
> - `mvp2-roles-and-access.md` — UserRole enum, VideoVisibility, promotion
> - `handover-mvp2-launch.md` — ops guide for new maintainers / Mac Studio migration

---

## 🎯 The pivot (the big idea)

### Before (MVP2.x)
- Users uploaded their own videos
- Whisper transcribed on-demand
- Ollama generated materials per request
- Bottleneck: 100 users × concurrent transcribes = 100 min queue wait
- Hardware-heavy: needs more RAM, more Ollama, more storage

### After (MVP2 go-live)
- **Admin curates** the catalog (you, nightly)
- Users **watch + chat + use study materials**, never upload
- **YouTube embed iframe** streams video (no file hosting, no ToS issue)
- **YouTube auto-captions** for transcripts (clean, free, no `yt-dlp`)
- Ollama only used for: chat + re-generate materials on demand
- Bottleneck: gone — admin processes nightly in batches

### Why this is the right call
1. ✅ **Eliminates the 100-min queue problem** (admin does it offline)
2. ✅ **Eliminates 90% of storage** (videos stream from YouTube's CDN)
3. ✅ **Eliminates CDN egress** (YouTube pays for it)
4. ✅ **Eliminates copyright issues** (we embed, not host)
5. ✅ **Eliminates upload moderation** (curated, not UGC)
6. ✅ **Better content** (you pick the top 5 videos on Vector DBs, not random user uploads)
7. ✅ **Better funnel** (content is the moat, not the tech)

---

## 🛠️ Scope (what's IN, what's OUT)

### ✅ IN for MVP2 v1.0 (ship this in 14 days)

| Component | What it does | Effort |
|---|---|---|
| **YouTube embed player** | Replace `<video>` with YouTube nocookie iframe | 1 day |
| **YouTube auto-captions** | Fetch captions from YouTube API (clean, free) | 0.5 day |
| **Admin role + DB** | `users.role` column + `require_admin` dependency | 0.5 day |
| **Admin route: `/api/admin/videos/youtube`** | Admin pastes YouTube URL → registers video + queue transcribe | 1 day |
| **Background: transcribe job** | Whisper `base` (MLX when available) using existing `transcribe_with_backend` | 0.5 day (mostly reusing existing code) |
| **LLM middleware proxy** (LiteLLM) | Rate limit + budget per user + PII scrub + cost tracking | 1 day |
| **Events table + lightweight dashboard** | SQLite `events` table, simple web dashboard reading it | 1 day |
| **gunicorn 4 workers** | Multi-process uvicorn | 0.5 day |
| **Cloudflare Tunnel** | Public HTTPS, DDoS, free | 0.5 day |
| **Tier 1 security** (firewall + FileVault check + secret perms) | macOS hardening | 0.5 day |
| **Basic analytics**: clicks per video, chat messages per user, errors | Already have data, just need to log + view | 0.5 day |

**Total**: ~7-8 days of work, leaves 6-7 days buffer.

### ❌ OUT for v1.0 (move to v1.1+)

| Item | Why deferred | ETA |
|---|---|---|
| **Mac OS app** | Huge scope, web app is enough for read-only MVP | v1.1 (post-launch, maybe never) |
| **Discord bot for YouTube picks** | Manual selection works fine for now | v1.2 (when you have 50 users) |
| **YouTube downloader (audio)** | Industry standard but gray-area ToS — start with auto-captions | **v1.1** (confirmed priority after v1.0) |
| **YouTube subscription API** | Manual paste works | v1.2 |
| **Custom Authenticator** (replacing AuthKit) | **AuthKit is NOT actually deprecated** — keep as-is | n/a |
| **Payments / Stripe** | Free beta first; paywall in v1.1 | v1.1 |
| **Account management UI** | Users self-register via Firebase; admin is manual DB row | v1.1 |
| **Attack detection (full prompt-injection filter)** | Start with LiteLLM's built-in; add regex layer in v1.1 | v1.1 |
| **Datadog-grade observability** | SQLite + manual queries fine for 100 users | v2.0 |

---

## 🌐 YouTube embed — the legal clean way

### What we DO
- ✅ Embed YouTube via official iframe (`youtube-nocookie.com`)
- ✅ Reference YouTube thumbnails (public CDN)
- ✅ Store transcript TEXT (transformation, not substitution)
- ✅ Generate mindmap/flashcards/quiz (our own work)
- ✅ Cite YouTube as source on every video page footer

### What we DON'T do (v1.0)
- ❌ Download video file to disk
- ❌ Stream YouTube video from our server
- ❌ Use `yt-dlp` to extract audio
- ❌ Store YouTube audio files

### What's legal in Singapore
- ✅ Embed iframe (licensed by YouTube for this)
- ✅ Transcript text (transformation/citation)
- ❌ Hosting downloaded video (Copyright Act 2021 §27 reproduction)

### v1.1 (after launch)
- Add `yt-dlp` audio fallback when YouTube auto-captions are missing or poor quality
- Industry-standard practice (Pocket Casts, Snipd, Otter.ai all do this)
- Audio file deleted immediately after transcript generated
- "Educational commentary" framing

---

## 🛡️ Roles & capabilities — implementation plan

> **See full design**: [doc/mvp2-roles-and-access.md](mvp2-roles-and-access.md)
> The summary below is for quick reference during the build.

### Two-enum design (extensible)

- **UserRole** (int, who you are): `0=ADMIN, 1=PAID, 2=FREE`
  - Future: `3=EDUCATION, 4=TRIAL, 5=BETA, 6=ENTERPRISE` (5-line addition)
- **VideoVisibility** (int, what you can see): `0=PUBLIC, 1=PAID_ONLY, 2=ADMIN_ONLY`
- **Capability** (str enum, what you can do): `VIEW_VIDEO, CHAT_FREE, CHAT_PAID,
  UPLOAD_VIDEO, REGEN_MATERIALS, CURATE_CATALOG, MANAGE_USERS, VIEW_ADMIN_DASHBOARD`
- **ROLE_CAPABILITIES map**: ties roles to capabilities, easy to extend

### DB schema (one new table + one new column)

```sql
-- Add users table (Day 2)
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT,
    role INTEGER NOT NULL DEFAULT 2,  -- UserRole enum, 2 = FREE
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Add visibility to videos (Day 2, additive)
ALTER TABLE videos ADD COLUMN visibility INTEGER NOT NULL DEFAULT 0;  -- PUBLIC

-- Add paid_waitlist for v1.1 prep
CREATE TABLE IF NOT EXISTS paid_waitlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    message TEXT,
    source TEXT DEFAULT 'web',
    notified_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Backend dependency

```python
# app/auth/roles.py (NEW)
from enum import IntEnum

class UserRole(IntEnum):
    ADMIN = 0
    PAID = 1
    FREE = 2
    # Future: EDUCATION = 3, TRIAL = 4, BETA = 5, ENTERPRISE = 6

class VideoVisibility(IntEnum):
    PUBLIC = 0
    PAID_ONLY = 1
    ADMIN_ONLY = 2

# app/auth/admin.py (NEW)
from functools import lru_cache
from fastapi import Depends, HTTPException
from sqlalchemy import text
from app.auth.dependencies import get_current_user
from app.auth.roles import UserRole, Capability, ROLE_CAPABILITIES
from app.database import get_db

@lru_cache(maxsize=10000)
def get_user_role(uid: str, role_db: int) -> UserRole:
    """Cached role lookup. role_db is part of cache key so changes invalidate."""
    return UserRole(role_db)

def require_capability(cap: Capability):
    """Decorator: 403 if user's role lacks this capability."""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            user = kwargs.get('user') or {}
            uid = user.get('uid', '')
            row = db.execute(text("SELECT role FROM users WHERE user_id=:uid"),
                             {"uid": uid}).fetchone()
            role = get_user_role(uid, row[0] if row else 2)
            if cap not in ROLE_CAPABILITIES.get(role, set()):
                raise HTTPException(403, f"Missing capability: {cap.value}")
            return await func(*args, **kwargs)
        return wrapper
    return decorator
```

### Two routers

```python
# app/routers/admin.py (NEW)
from fastapi import APIRouter, Depends
from app.auth.admin import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])

@router.post("/videos/youtube")
async def admin_add_youtube_video(
    youtube_url: str,
    title: str,
    user: dict = Depends(require_admin),
):
    """Admin-only: register a YouTube video.
    Streams via YouTube embed; we store materials + metadata only."""
    video_id = extract_youtube_id(youtube_url)
    # Validate, save to DB, queue transcribe job
    return {"video_id": ..., "youtube_id": video_id}
```

### Frontend gating

```html
{# base.html or video.html #}
{% if session.get('is_admin') %}
  <a href="/admin/upload">📤 Add YouTube video</a>
{% endif %}
```

---

## 🧮 Ollama cost model (the real constraint)

### Old model (UGC)
- User pays for own LLM cost (or you, ad-hoc)
- Unpredictable peaks

### New model (admin-curated + chat)
- **You** pay for ALL chat/generation
- $20 USD/month budget
- Need to model how many chats that buys

### Chat cost (rough)

| Model | Input $/M tokens | Output $/M tokens | Avg chat (1k in + 500 out) |
|---|---|---|---|
| `glm-5.2:cloud` (Ollama cloud) | $0.10 | $0.40 | ~$0.0003 per chat |
| Local 7B (qwen2.5:7b) | $0 (electricity) | $0 | ~$0 |

**At $20/month:**
- 66,000 chats on `glm-5.2:cloud`, OR
- Unlimited chats on local 7B (costs ~$2 electricity)

### Per-user rate limit (LiteLLM middleware)

```yaml
# Free tier
rate_limit: 30 chats/day per user
burst_limit: 5 chats/min
daily_budget: 50 cents per user per day (LLM cost)

# Admin
rate_limit: unlimited
```

**Free tier math**:
- 100 users × 30 chats/day = 3,000 chats/day max
- At ~$0.0003/chat = **$0.90/day LLM cost** (~$27/month)
- **You blow past $20 if everyone maxes out** → LiteLLM's budget enforcement kicks in

**Why LiteLLM**:
- Drop-in OpenAI-compatible proxy (you point `app.services.llm` at `http://localhost:4000` instead of `http://localhost:11434`)
- Built-in rate limits, budgets, spend alerts
- Free, self-hosted, well-documented

---

## 🏗️ Architecture (3 components, not 8 microservices)

```
Internet (100 users in Singapore)
   ↓
Cloudflare Tunnel + WAF + Rate Limiter
   ↓ HTTPS (auto-cert)
FastAPI app (gunicorn 4 workers)
   ├─ /api/* routers (read-only: chat, list, search)
   ├─ /admin/* routers (admin: add YouTube video)
   ├─ /api/auth/session (Firebase)
   └─ LiteLLM proxy middleware (rate limit + scrub + log)
      ↓
   Ollama (glm-5.2:cloud or local 7B)
   
Events table (SQLite) → Grafana dashboard (Docker, optional)
```

---

## 📅 14-day schedule

### Week 1

| Day | Task | Deliverable |
|---|---|---|
| **Day 1** | (a) Verify `.env` has YOUTUBE_API_KEY + GROQ_API_KEY + OLLAMA_API_KEY, restart server, confirm login works | Working auth |
| | (b) Add `users` table + `videos.visibility` column + `paid_waitlist` table (additive migrations) | DB ready for roles |
| | (c) Create `app/auth/roles.py` with UserRole, VideoVisibility, Capability, ROLE_CAPABILITIES | Role system in code |
| | (d) Create `app/auth/admin.py` with `require_capability` decorator + cached role lookup | Reusable admin gate |
| **Day 2** | (a) Add `/api/admin/videos/youtube` endpoint (uses YouTube Data API v3 to fetch captions) | Admin can register URL |
| | (b) Add `videos.youtube_id` + `videos.visibility` columns + auto-fetch title/thumbnail/duration | Video metadata populated |
| | (c) Frontend: admin-only "Add YouTube video" form with visibility dropdown (PUBLIC/PAID_ONLY/ADMIN_ONLY) | Admin can paste URLs |
| | (d) Auto-create `users` row on first authenticated login (idempotent INSERT) | New users tracked |
| | (e) Test: admin pastes URL → video appears → FREE user can browse | End-to-end works |
| **Day 3** | (a) Add `yt-dlp` metadata-only fetcher + YouTube auto-captions fetch | Transcript comes from YouTube |
| | (b) Background transcribe job using existing `transcribe_with_backend` | Whisper fallback ready |
| | **(c) Add 2 admin endpoints for caption management** (Day 3 actual shipped — see commit `26a6bd6`) | `POST /api/admin/videos/{id}/captions/retry`, `GET /api/admin/videos/{id}/captions/status` |
| | **(d) Smart retry: fall back without language preference if YouTube 429s** (Day 3 actual — `.*` pattern trips rate limit) | Ollama quota tracker ready to extend |
| **Day 4** | (a) LiteLLM proxy setup + rate limit per user + quota tracker | LLM middleware live |
| | (b) Point `app.services.llm` at LiteLLM (config change) | All LLM calls rate-limited |
| | **(c) Tier-based provider chains** (new — captured 2026-08-24): FREE→[groq], PAID/ADMIN→[ollama,openai] | Free never uses Ollama, Paid never uses Groq |
| **Day 5** | (a) Add SQLite `events` table + logging helper | Events being recorded |
| | (b) Simple web dashboard (read SQLite, render table) | First version of dashboard |
| **Day 6** | (a) gunicorn 4 workers + update `start.sh` | Production-ready server |
| | (b) Cloudflare Tunnel setup + config | Public URL working |
| **Day 7** | Buffer day (bugs, polish, testing) | Stable build |

### Week 2

| Day | Task | Deliverable |
|---|---|---|
| **Day 8** | Replace `<video>` with YouTube embed iframe + jump-to-time via IFrame API | Video player swap |
| **Day 9** | Test with 3 real YouTube videos end-to-end | Admin → user flow works |
| **Day 10** | Tier 1 security (firewall, secret perms, FileVault check) | Hardened Mac |
| **Day 11** | Invite 10-20 friends for soft launch | Real users testing |
| **Day 12** | Bug bash + load test (use `locust` or `k6`, simulate 100 concurrent) | Performance validated |
| **Day 13** | Polish + docs (README update, security disclosure, support channel) | Ready for public |
| **Day 14** | **🚀 LAUNCH** (post on r/SingaporeLearning, your network, ProductHunt?) | Public beta |

---

## 🚦 Go-live (checklist

### Pre-launch (Day 13)
- [ ] `.env` is filled with real Firebase values
- [ ] LaunchDaemon installed + crash recovery tested
- [ ] Cloudflare Tunnel live, public URL accessible
- [ ] 5+ YouTube videos in the catalog (admin pre-curated)
- [ ] LiteLLM rate limits active
- [ ] SQLite events table logging
- [ ] Dashboard live
- [ ] 10-20 friend-testers signed up and active
- [ ] Backup plan documented (what to do if the Mac dies)
- [ ] README updated with go-live instructions

### Day 14 (launch)
- [ ] Post on r/SingaporeLearning (r/SGExams, r/SGWork, etc.)
- [ ] Personal network (WhatsApp/Telegram broadcast)
- [ ] ProductHunt "Upcoming" (if scope allows)
- [ ] Twitter / LinkedIn post
- [ ] Monitor dashboard + server every 1-2 hours for first day

### Post-launch (Week 3+)
- [ ] Daily metrics review (chats per user, errors, latency)
- [ ] Weekly content update (add 2-3 new YouTube videos)
- [ ] Month 1: collect feedback, plan v1.1
- [ ] Month 2: payments + paywall

---

## 💰 Cost projection (monthly)

| Item | Cost |
|---|---|
| Electricity (Mac Studio 24/7) | ~SGD 50 |
| Internet (Singapore biz fiber) | ~SGD 200 |
| Cloudflare (free tier) | $0 |
| LiteLLM + Redis (self-hosted, free) | $0 |
| Firebase Auth (free up to 50K MAU) | $0 |
| Groq free tier (free users) | $0 |
| Ollama Pro (admin testing) | ~SGD 20/month |
| **Total** | **~SGD 270/month** |

### Break-even math
- 1,000 paid users × SGD 19.99 = **SGD 20K/month revenue**
- Less SGD 300/month cost = **SGD 19,700/month gross profit** (98.5% margin)
- Even 100 paid users = SGD 2K - 300 = SGD 1,700 gross profit

---

## 🤖 LLM provider design (free vs paid tier)

Full design lives in `doc/mvp2-llm-architecture.md`. Quick summary:

| Tier | Provider | Model | Chats/day | Cost |
|---|---|---|---|---|
| **Free** | Groq | `openai/gpt-oss-120b` (primary) → `openai/gpt-oss-20b` (fallback) | 30/day | **$0** (Groq free tier) |
| **Beta invite** | Groq | same, higher RPD limit via per-user key | 100/day | **$0** |
| **Admin (testing)** | Ollama Pro | `glm-5.2:cloud` | unlimited | $20/mo Pro |
| **Admin (production)** | Local on MBP | `qwen3:14b` | unlimited | **$0** (electricity) |

**Material generation (admin only)**: `groq/qwen/qwen3.6-27b` (better JSON reliability, free).

### Why Groq for free tier?
- **Free tier** = 30 RPM, 1K RPD, 8K TPM, 200K TPD per organization
- **Fast inference** (Groq LPU) = <1 sec first token = great UX
- **Cached tokens don't count** → our semantic cache saves quota for free

### Disclaimer (UI text for free users)

Show above chat input:

> **Free tier** — Chats are processed by Groq (a third-party LLM provider).
> Don't share personal or sensitive information. Up to 30 chats per day.
> For unlimited chats with better quality, [upgrade to Pro](#) (coming soon).

Below the disclaimer:

> By using free tier, you agree to Groq's [Terms of Service](https://groq.com/terms)
> and [Privacy Policy](https://groq.com/privacy).

---

## 💾 Caching strategy

**Semantic cache** (LiteLLM + Redis + bge-m3 local embeddings):
- Match by embedding similarity (cosine > 0.92), not exact text
- Cache key = `hash(video_id + model + messages + temperature)`
- Cache TTL = 7 days
- **Expected hit rate: 30-50%** of free-tier chat requests
- Cached responses cost **$0** and don't count toward Groq TPM

**What gets cached**:
- First user question on a fresh chat session for a video (often "summarize" / "key idea")
- Admin material re-generation on same video (identical prompt)
- Common concept questions ("what is X?")

**What doesn't get cached**:
- Mid-conversation replies (low similarity to past)
- Specific user questions with details
- High-temperature creative responses

---

## 👥 User capacity (free tier)

### Constraints stacked (tightest wins)
| Constraint | Limit | Applies to |
|---|---|---|
| Groq free (org-wide) | 30 RPM, 8K TPM, 1K RPD | All free users combined |
| Our middleware (per user) | 5 RPM, 30 RPD | Per user |
| FastAPI (gunicorn 4) | ~50 concurrent | All users combined |
| Mac Studio hardware | ~500 concurrent HTTPS | All users combined |

### Capacity table
| Scenario | Users | Aggregate RPM | Status |
|---|---|---|---|
| **Soft launch (target)** | 100 active | ~20 RPM (realistic) | ✅ Comfortable |
| Mid-growth | 200 active | ~40 RPM | ⚠️ Throttled at peak |
| Scale (no infra change) | 300 active | ~60 RPM | ❌ Hits Groq cap |
| Scale (with 3 Groq accounts) | 500 active | ~100 RPM aggregate (load-balanced) | ✅ Comfortable |
| Scale (5 accounts + cache) | 1,000 active | ~200 RPM, but cache halves = 100 RPM | ✅ Workable |

### Scaling levers (implement when needed, not now)
1. **Multiple Groq accounts** — LiteLLM load-balances across N keys (`GROQ_KEY_1`, `_2`, ...). N accounts = N× capacity.
2. **Redis request queue** — smooths spikes; users see "Thinking..." 1-5 sec.
3. **Semantic cache** — 30-50% hit rate halves effective RPM to Groq.
4. **Per-user Groq keys** — at 500+ users, each user gets their own 30 RPM. Avoids org-level cap.

### v1.0 plan: 1 Groq account, 100 users target
- Don't over-engineer
- Add 2nd Groq account when 100 users register
- Add Redis queue when concurrent >20

---

## 💎 Paid tier incentive (post-launch, v1.1)

Goal: make free tier **good enough** to be useful, **annoying enough** to upgrade.

### Tier comparison
| Feature | Free (Groq) | Paid (Ollama Pro + local) |
|---|---|---|
| Chats/day | 30 | Unlimited |
| Model quality | `gpt-oss-120b` (good) | `qwen3:14b` local (better) or Ollama Pro top models |
| Privacy | Groq servers | Our servers |
| Materials generation | ❌ | ✅ |
| Offline | ❌ | ✅ |
| Custom fine-tuned model | ❌ | ✅ (when trained) |

### Pricing (suggestion)
- **Free**: SGD 0/mo, 30 chats/day, Groq
- **Pro**: SGD 9.99/mo, unlimited, local Ollama + materials regen
- **Family**: SGD 19.99/mo, 4 accounts, all Pro

### The flywheel (the killer pitch)
> "Your paid subscription funds the training of our own fine-tuned model. The
> more subscribers, the better YOUR model gets. You're paying for a service
> that improves itself."

We log all free-tier chats (with user consent via disclaimer) → admin curates
the good ones → fine-tune Llama 3.1 8B → host locally → free tier becomes
unlimited + private. **The data flywheel is the product.**

---

## ❓ Decision points (resolved 2026-08-22)

1. ✅ **Branch name**: stay on `mvp2-production-patches` (existing branch)
2. ✅ **LiteLLM** confirmed as middleware
3. ✅ **YouTube embed** via `youtube-nocookie.com`
4. ✅ **YouTube auto-captions** via Data API v3 (free tier, ~25/day quota)
5. ✅ **Rate limits**: 5 RPM + 30 RPD per free user (below Groq 30 RPM cap)
6. ✅ **Free chat model**: Groq (`gpt-oss-120b` primary, `gpt-oss-20b` fallback)
7. ✅ **Admin model**: Ollama Pro `glm-5.2:cloud` (test) + local `qwen3:14b` (prod)
8. ✅ **Storage**: 4 drives, RAID 1 HDDs (see `mvp2-storage-architecture.md`)
9. ✅ **Backup**: 00:00 SGT daily, 30 daily + 12 monthly retention
10. ✅ **Caching**: semantic cache via LiteLLM + Redis + bge-m3
11. ✅ **Chat logging**: full conversations stored (PDPA: admin can delete on request)
12. ✅ **PII scrubbing**: deferred to v1.1 (UI disclaimer only in v1.0)
13. ✅ **UPS for MBP**: deferred to v1.1 (not blocking v1.0 launch)

---

## 📝 Update log

- **2026-08-23** — **Day 2A complete** (4 topics, 8 commits, +100 tests):
  - Topic 1: YouTube URL extraction + `videos.youtube_id` column
  - Topic 2: `POST /api/admin/videos/youtube` endpoint (no API key needed —
    ID extraction is pure regex; Day 2B will add transcript fetch)
  - Topic 3: Admin upload form UI on `/admin/upload` (Tailwind + JS preview)
  - Topic 4: Catalog service (`app/services/catalog.py`) with
    visibility-filtered queries + dashboard grid
  - Bug fix: `ensure_user_row` now runs on EVERY authenticated request
    (was only on admin routes → fresh Google OAuth users had no DB row,
    couldn't be promoted). See commit `c32beb8`.
  - Helper script: `bash scripts/promote-admin.sh you@email.com`
  - Test count: 894 passing, 91% coverage. New modules at 100% coverage.
- **2026-08-22** — Plan finalized: storage design (4 drives + RAID 1),
  LLM provider routing (Groq free + Ollama admin), caching strategy
  (semantic via Redis), user capacity math (100 target, 500 with scaling
  levers), paid tier incentive + flywheel, disclaimer text, UPS deferred
  to v1.1. Created `mvp2-storage-architecture.md` and `mvp2-llm-architecture.md`.
  Acasis H006 enclosure with 4 drives detected: Samsung 990 Pro 1TB (NVMe),
  Lexar NM610 PRO 2TB (NVMe), 2× 3TB HDDs (USB). RAID 1 planned for HDDs.
- **2026-08-20** — Created doc capturing the read-heavy pivot, YouTube embed
  approach (legal analysis), admin role design, LiteLLM middleware plan,
  14-day schedule, cost projections.