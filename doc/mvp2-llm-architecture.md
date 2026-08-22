# MVP2 LLM Architecture — Providers, Routing, Caching, Scaling

> **Status**: Design finalized. Implementation during 14-day build (Day 6-8).
> **Last updated**: 2026-08-22
> **Related**: `mvp2-final-go-live-plan.md`, `mvp2-storage-architecture.md`

This doc captures the LLM layer design for MVP2: which providers we use, how
requests are routed, how caching works, how we scale beyond 100 users, and the
"data flywheel" that powers our future fine-tuned model.

---

## 1. Goals

1. **Free users get a great experience** — fast, smart chat, no quota anxiety
2. **Admin gets unlimited usage** — for testing + material generation
3. **Our budget stays predictable** — fixed cost + free tier, no surprise bills
4. **We collect training data** — for the flywheel (Section 7)
5. **We can scale to 500+ users** without major rework

---

## 2. Provider routing table

| Task | Tier | Provider | Model | Why |
|---|---|---|---|---|
| **Free chat (primary)** | Free | Groq | `groq/openai/gpt-oss-120b` | Best free quality |
| **Free chat (fallback)** | Free | Groq | `groq/openai/gpt-oss-20b` | Lighter, faster, more quota headroom |
| **Free chat (last resort)** | Free | Groq | `groq/qwen/qwen3.6-27b` | Different family, may avoid specific rate-limit buckets |
| **Admin chat (test)** | Admin | Ollama Pro | `glm-5.2:cloud` | Best quality we can buy |
| **Admin chat (prod)** | Admin | Local on MBP | `qwen3:14b` (via Ollama) | Unlimited, free, private |
| **Material generation** | Admin only | Groq | `groq/qwen/qwen3.6-27b` | Best JSON reliability for structured output |
| **Embeddings (cache)** | Internal | Local | `bge-m3` (Ollama) | Free, fast, already installed |
| **Speech-to-text (Whisper)** | Admin | Local MLX | `faster-whisper base` | Already wired up; admin processes nightly |

---

## 3. LiteLLM proxy architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser                                                            │
│   ↓ Firebase ID token (Bearer)                                       │
│  FastAPI middleware                                                  │
│   1. Verify Firebase token → extract uid                             │
│   2. Look up / create LiteLLM virtual key for uid                    │
│   3. Log chat to chat_logs (uid, model, messages, tokens, latency)   │
│   4. Rewrite: Authorization: Bearer sk-litellm-<uid-key>            │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│  LiteLLM proxy (localhost:4000)                                     │
│   • Redis rate-limit: 5 RPM + 30 RPD per virtual key                │
│   • Redis semantic cache: hit if embedding sim > 0.92               │
│   • Spend log: tokens, cost, model, latency (LiteLLM DB)            │
│   • Router: primary → fallback → fallback2                           │
└─────────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ↓               ↓               ↓
        Groq free      Ollama Pro      Local Ollama
        (free users)   (admin test)    (admin prod, embeddings)
```

### LiteLLM config.yaml (planned)

```yaml
model_list:
  # Free tier — primary
  - model_name: chat-free
    litellm_params:
      model: groq/openai/gpt-oss-120b
      api_key: os.environ/GROQ_API_KEY_1   # primary account
    model_info:
      tier: free

  # Free tier — fallback (kicked in on 429 or model error)
  - model_name: chat-fallback
    litellm_params:
      model: groq/openai/gpt-oss-20b
      api_key: os.environ/GROQ_API_KEY_1   # same account, lighter model
    model_info:
      tier: free

  # Admin — Ollama Pro
  - model_name: chat-admin-cloud
    litellm_params:
      model: ollama_chat/glm-5.2:cloud
      api_base: https://ollama.com/v1
      api_key: os.environ/OLLAMA_API_KEY
    model_info:
      tier: admin

  # Admin — local Ollama on MBP
  - model_name: chat-admin-local
    litellm_params:
      model: ollama_chat/qwen3:14b
      api_base: http://localhost:11434
    model_info:
      tier: admin

  # Admin — material generation (best JSON reliability)
  - model_name: generate-admin
    litellm_params:
      model: groq/qwen/qwen3.6-27b
      api_key: os.environ/GROQ_API_KEY_1
    model_info:
      tier: admin

router:
  default: chat-free
  fallbacks:
    - chat-fallback
    # chat-admin-local is NOT a free-tier fallback (admin only)

litellm_settings:
  drop_params: true
  set_verbose: false
  cache: true
  cache_params:
    type: redis
    host: localhost
    port: 6379
    ttl: 604800                 # 7 days
    mode: default               # semantic-similarity is set per-key
  # Per-key semantic caching (in virtual key creation):
  #   "mode": "semantic",
  #   "similarity_threshold": 0.92

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: sqlite:////Volumes/Storage-Fast-NVMe/litellm/litellm.db

# Per-tier limits applied when creating virtual keys
# Free key:     RPD=30,  RPM=5,  max_budget=0    (Groq free, no $$ cap)
# Beta key:     RPD=100, RPM=10, max_budget=0
# Admin key:    unlimited, unlimited, max_budget=20  ($20/mo Pro safety net)
```

---

## 4. Per-tier rate limits

### Free tier
| Limit | Value | Why |
|---|---|---|
| RPM | 5/min | Below Groq 30 RPM cap (leaves room for 5 concurrent free users) |
| RPD | 30/day | Conservative; fits Groq 1K RPD for ~30 concurrent max |
| TPM | (Groq limit, 8K) | Our middleware doesn't cap TPM; Groq does |
| Budget | $0 | Free tier = $0 spend target |

### Beta invite tier
| Limit | Value | Why |
|---|---|---|
| RPM | 10/min | Twice free, for friend-testers + content creators |
| RPD | 100/day | 3× free |
| Budget | $0 | Same |

### Admin tier
| Limit | Value | Why |
|---|---|---|
| RPM | unlimited | Admin needs to test fast |
| RPD | unlimited | Same |
| Budget | $20/month | Ollama Pro hard stop |

---

## 5. Caching strategy

### Semantic cache (primary)

**How it works**:
1. User sends chat request
2. FastAPI middleware embeds the request (using local `bge-m3`) → vector
3. Middleware sends to LiteLLM with `cache: { mode: semantic, similarity_threshold: 0.92 }`
4. LiteLLM compares to past cached requests for the same `video_id`
5. If match: return cached response, **$0 cost, 0 RPM used**
6. If no match: forward to Groq, get response, cache it

**Hit rate target**: 30-50% of free-tier requests

**Example cache hits** (all on the same video):
```
"What is the main concept in this video?"      ← cache miss, calls Groq
"What's the key idea here?"                     ← cache hit (sim=0.94)
"Summarize the most important point"           ← cache hit (sim=0.93)
"Explain the central thesis"                    ← cache hit (sim=0.92)
```

### Exact cache (admin operations)

- Material re-generation on same video (admin clicks "regenerate")
- Whisper re-transcribe on same video file
- Cache key includes file hash → same file = cache hit, no recompute

### Cache invalidation

- Manual: admin clicks "regenerate" → cache purged for that video
- TTL-based: 7 days for chat, 30 days for materials
- On schema change: cache cleared automatically (LiteLLM version bump)

---

## 6. Chat logging schema (for fine-tuning)

Every free-tier chat is logged for the flywheel. The `chat_logs` table:

```sql
CREATE TABLE chat_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL,                   -- Firebase UID
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    video_id TEXT,                       -- which video the chat was about
    model TEXT NOT NULL,                 -- e.g. "groq/openai/gpt-oss-120b"
    messages_json TEXT NOT NULL,         -- [{role, content}, ...]
    tokens_in INTEGER,
    tokens_out INTEGER,
    latency_ms INTEGER,
    cache_hit BOOLEAN DEFAULT 0,         -- 1 if served from cache
    feedback_rating INTEGER,             -- 1-5 stars (user feedback, optional)
    feedback_text TEXT,                  -- user comment (optional)
    admin_deleted BOOLEAN DEFAULT 0,     -- PDPA: deleted on user request
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_chat_logs_uid ON chat_logs(uid);
CREATE INDEX idx_chat_logs_video ON chat_logs(video_id);
CREATE INDEX idx_chat_logs_timestamp ON chat_logs(timestamp);
```

### Privacy (PDPA Singapore compliance)

- Users see a disclaimer: "Free tier chats are processed by Groq and may be
  logged to improve our service. Don't share personal information."
- Users can request deletion of all their chat logs (admin can run:
  `DELETE FROM chat_logs WHERE uid = ?`)
- Logs are stored on Singapore-resident MBP (not transferred internationally
  except to Groq for inference)

### What we DON'T log

- Free-tier admin chats (admin = internal, no flywheel data needed)
- Paid-tier chats (v1.1: opt-in to logging for free month discount)
- Anything that's been deleted by the user

---

## 7. The flywheel (our moat)

```
Free users chat (Groq)
   ↓
Every conversation logged to chat_logs
   ↓
Admin (you) reviews weekly:
  - Mark high-quality exchanges (good Q&A)
  - Mark poor exchanges (forbidden content, wrong answers)
   ↓
Curated dataset (JSONL):
  {"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}], "rating": 5}
   ↓
Fine-tune Llama 3.1 8B (or Qwen2.5 7B) on the curated data
   ↓
Host the fine-tuned model locally on Mac Studio (qwen3:14b → our-finetuned:v1)
   ↓
Free tier routes to local model → unlimited, free, private
   ↓
Free tier becomes better → more users → more data → better model
   ↓
GOTO step 1
```

### Why this works

1. **Free tier data improves free tier** (closed loop)
2. **Fine-tuned 8B model on a good dataset can match GPT-4 quality for narrow domain**
3. **Our domain is narrow** (educational Q&A about curated videos)
4. **Once we're self-hosted, we have unlimited free tier at $0 marginal cost**
5. **Users love "your data is private" + "free" combo**

### When to start the flywheel

- **v1.0** (launch): collect data only, no fine-tuning yet
- **v1.1** (~3 months in): 1,000+ curated conversations → first fine-tune
- **v1.2** (~6 months in): self-hosted model replaces Groq for free tier

---

## 8. Scaling beyond 100 users

### Capacity math

With 1 Groq account, semantic cache, and our 5 RPM/user middleware cap:
- **100 active users**: ✅ comfortable (peak ~20 RPM, cache halves to 10)
- **200 active users**: ⚠️ throttled at peak (~40 RPM)
- **300 active users**: ❌ hits Groq cap

### Scaling levers (implement when needed)

| Lever | Capacity gain | Effort | When to add |
|---|---|---|---|
| **2nd Groq account** (LiteLLM load-balances) | 2× | 10 min (new API key + config line) | At 100 registered users |
| **Redis request queue** | smooths spikes, no capacity gain | 1 hour | At 50 concurrent peak |
| **Semantic cache optimization** (better threshold) | +20% hit rate | 2 hours (tune) | At 200 active users |
| **3rd-5th Groq account** | 3-5× | 30 min each | At 300 active users |
| **Per-user Groq keys** (each user gets own 30 RPM) | 100× (theoretical) | 4 hours (signup flow) | At 500 active users |
| **Ollama Pro for overflow** (paid) | unlimited | 2 hours (config) | At 1,000 active users |

### Decision rule

> **Add capacity when daily p95 latency to first token > 2 seconds**
> OR when 429 errors exceed 1% of requests

Don't preemptively scale. Scale when user-visible pain appears.

---

## 9. Failure modes

| What fails | Impact | Recovery |
|---|---|---|
| Groq down | Free tier chat fails | Fallback chain tries `gpt-oss-20b`, then admin model. If all fail: friendly error "Service busy, try again". Auto-retry with backoff. |
| Groq rate-limited (429) | Burst of failures | Semantic cache absorbs 30-50%. Rest fall back to lighter model. Surplus shows "service busy" message. |
| Redis down | No caching, no rate limiting | LiteLLM falls back to in-memory (slower, no persistence). Rate limit enforced at FastAPI middleware level instead. Degraded but functional. |
| Ollama Pro down | Admin can't test in cloud | Falls back to local `qwen3:14b`. Admin may not notice. |
| Local Ollama down (MBP rebooted) | Admin prod model unavailable | Admin falls back to Groq admin model. Restart Ollama (`brew services start ollama`). |
| LiteLLM proxy crashed | All LLM calls fail | LaunchDaemon auto-restarts. App returns 503 to users. |
| `bge-m3` embedding down | Cache misses (degrades perf) | Falls back to exact cache. Hit rate drops to 5-10% but still works. |

---

## 10. Open items

- [ ] Verify Groq account quota with a test burst (Day 6 of build)
- [ ] Tune semantic cache threshold (start at 0.92, adjust based on data)
- [ ] Decide on Groq account rotation strategy (manual today, automated later)
- [ ] Decide on admin feedback UI for marking high-quality chats (flywheel curation)
- [ ] Decide whether to expose cache hit/miss in admin dashboard

---

## Update log

- **2026-08-22** — Created doc capturing LLM provider routing, LiteLLM config
  (yaml skeleton), per-tier rate limits, semantic cache design, chat_logs
  schema, PDPA compliance notes, flywheel strategy, scaling levers, failure
  modes. All based on 2026-08-22 conversation about Groq free tier.
