# MVP3 Go-Live Plan — Read-Heavy, Admin-Curated

> **Status**: Planning. No code yet.
> **Tagline**: "I curate the best videos on the internet. You watch, learn, and chat."
> **Launch target**: 14 days from 2026-08-08 (working backwards, due ~2026-08-22)
> **Last updated**: 2026-08-20

---

## 🎯 The pivot (the big idea)

### Before (MVP2.x)
- Users uploaded their own videos
- Whisper transcribed on-demand
- Ollama generated materials per request
- Bottleneck: 100 users × concurrent transcribes = 100 min queue wait
- Hardware-heavy: needs more RAM, more Ollama, more storage

### After (MVP3 go-live)
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

### ✅ IN for MVP3 v1.0 (ship this in 14 days)

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

## 🛡️ Admin role — implementation plan

### DB schema (one small table)

```sql
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,        -- matches Firebase UID
    email TEXT,
    role TEXT NOT NULL DEFAULT 'user',  -- 'user' | 'admin' | 'support_admin'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Seed with your account
INSERT INTO users (user_id, email, role)
VALUES ('YOUR_FIREBASE_UID', 'your@email.com', 'admin');
```

### Backend dependency

```python
# app/auth/admin.py
from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.auth.dependencies import get_current_user
from app.database import get_db


def require_admin(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Return current user only if they have role='admin' in DB.
    
    Use on every admin-only route. Admins added manually (no self-promotion).
    Future 'support_admin' role: read-only access for CS team.
    """
    uid = user.get("uid", "")
    row = db.execute(
        text("SELECT role FROM users WHERE user_id = :uid"),
        {"uid": uid}
    ).fetchone()
    if not row or row[0] not in ("admin",):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
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
| **Day 1** | (a) Verify `.env` is filled, restart server, confirm login works | Working auth |
| | (b) Add `users` table + `require_admin` dependency + admin router skeleton | Admin route exists |
| **Day 2** | (a) Add `/api/admin/videos/youtube` endpoint | Admin can register URL |
| | (b) Frontend: `is_admin` context + admin-only UI | Admin can paste URLs |
| **Day 3** | (a) Add `yt-dlp` metadata-only fetcher + YouTube auto-captions fetch | Transcript comes from YouTube |
| | (b) Background transcribe job using existing `transcribe_with_backend` | Whisper fallback ready |
| **Day 4** | (a) LiteLLM proxy setup + rate limit per user + budget | LLM middleware live |
| | (b) Point `app.services.llm` at LiteLLM (config change) | All LLM calls rate-limited |
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
| LiteLLM (self-hosted, free) | $0 |
| Firebase Auth (free up to 50K MAU) | $0 |
| Ollama cloud (`glm-5.2:cloud`) | ~SGD 20-50 (varies with usage) |
| **Total** | **~SGD 270-300/month** |

### Break-even math
- 1,000 paid users × SGD 19.99 = **SGD 20K/month revenue**
- Less SGD 300/month cost = **SGD 19,700/month gross profit** (98.5% margin)
- Even 100 paid users = SGD 2K - 300 = SGD 1,700 gross profit

---

## ❓ Decision points (before we code)

These are the choices I'm waiting on:

1. **Branch name**: `mvp3-go-live-read-heavy` or different?
2. **LiteLLM vs hand-rolled middleware**: confirm LiteLLM?
3. **YouTube embed** (confirmed): `youtube-nocookie.com`?
4. **Transcript source** (confirmed): YouTube auto-captions only for v1.0?
5. **Rate limits**: 30 chats/day per free user OK?
6. **Local 7B model vs `glm-5.2:cloud`**: which for chat?
7. **When to start coding**: today, after Go-Live doc finalized, or after server config confirmed?

Reply with picks and I'll set up the branch + start Day 1.

---

## 📝 Update log

- **2026-08-20** — Created doc capturing the read-heavy pivot, YouTube embed approach (legal analysis), admin role design, LiteLLM middleware plan, 14-day schedule, cost projections.