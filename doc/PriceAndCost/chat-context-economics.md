# Chat Context Economics — The 600-Segment Cap & Tier-Aware Limits

> **Date**: 2026-09-19 (soft-launch day +1)
> **Status**: Discussion captured; **decision: ship as-is for launch**, tier-aware caps queued as a v1.1 candidate
> **Why this doc exists**: this is a **business/monetization question**, not just an engineering one — the chat transcript cap is a tier differentiator that costs real money per question. The discussion happened in an AI-pairing session and would otherwise be lost.

---

## 1. How chat context works today (the mechanics)

When a user asks a question about a video, the chat endpoint builds one prompt:

```
[system] video title + summary + mindmap + quiz
[system] transcript (timestamped segments)
[user]  the question
```

- **Whisper transcribes fully** — the entire transcript is stored in the DB asset, no limit at that stage.
- `app/services/chat.py::transcript_to_chat_text()` then caps what the LLM sees: **MAX_SEGMENTS = 600** (~10K tokens).
- If the video has more, it keeps the **first 300 + last 300 segments** and inserts `[N segments omitted for length]` in the middle.

### Real example — the 2026-09-18 Apple Event upload (measured)

| Metric | Value |
|---|---|
| Video length | 77 minutes |
| Total segments | **1,340** |
| Total words | 10,666 (~8 words/segment) |
| What the LLM saw | head 0→16.6 min + tail 59.9→77 min |
| **Omitted middle** | **740 segments = minutes 16.7 → 59.9 (~43 min gone)** |
| Symptom | User asked about AirPods 5 (~min 30) → model honestly said "that falls within the omitted portion" |

**Segment definition**: one timestamped chunk Whisper emits (~a phrase / one breath, ~8 words avg). 3,000 words ≈ 1,400 segments.

```
🎬 video → 🎤 whisper (full, no cap) → 🗄️ DB transcript asset (full, kept forever)
                                          ↓ chat request
                              📦 prompt builder: 600-seg cap
                                 → ✂️ head 300 + tail 300
                                 → 🤖 LLM sees ~10K transcript tokens
```

---

## 2. The key fact that changes the economics

**`glm-5.2:cloud` has a 1M-token context window** (verified live via Ollama API: `glm5.2.context_length = 1048576`).

So the 600 cap was **never about the model** — it was tuned for the FREE tier's small-window Groq model and applied globally. The real constraints for raising it:

| Constraint | Reality |
|---|---|
| **Cost per chat** | Ollama Pro bills by token. The transcript re-ships with **every message** in a conversation. A 1,340-seg video ≈ 24K tokens per question — ×10 questions = 240K tokens on one video |
| **Latency** | ~24-54K input tokens = slower time-to-first-token, especially on cellular |
| **FREE tier model** | Groq compound-mini — genuinely small window; **cannot** be raised |
| **Ollama Pro quota** | 800 req/5h, 3000/wk shared across ALL paid+admin chats — big contexts burn it faster |

---

## 3. The monetization angle (why this is a business question)

The cap is a **natural tier differentiator** that maps to real unit economics:

| Tier | Model | Context | Proposed cap | Value to user | Cost to us (per question) |
|---|---|---|---|---|---|
| **FREE** | groq compound-mini | small (model-limited) | **600** (unchanged) | intro/conclusion chat on long videos | $0 — hard floor |
| **PAID** ($14.99/mo) | glm-5.2:cloud | 1M | **3,000** (~54K tok) | **full-transcript chat for any video ≤ ~4.5h** — the Apple Event fits entirely | ~2× today's token burn |
| **ADMIN** | glm-5.2:cloud | 1M | **8,000** (~144K tok) | everything, always | admin absorbs consciously |

**The pitch writes itself**: *"Free: chat about the highlights. Paid: the AI has watched the entire lecture."* That's a demonstrable, honest upgrade — not a fake limit.

**Break-even thinking** (rough): if a PAID user averages 15 questions/day on long videos at 3,000 segs (~54K tok/question), that's ~810K tokens/day. Ollama Pro's 800 req/5h and 3000/wk caps — plus quota auto-fallback at 90% — are the guardrails. **Watch `/admin/budget` in launch week** to see the real burn before promising limits in marketing copy.

**Sequencing decision (2026-09-19): ship launch as-is (all tiers 600)**. Reasons: (a) the honest-omission behavior is acceptable UX, (b) no pricing promise exists yet that the higher caps must honor, (c) tier-aware caps are ~30 min of work to add once the numbers from real usage are in.

---

## 4. What we'd build (v1.1 candidate, ~30 min)

1. `transcript_to_chat_text(segments, max_segments)` — pass the cap in; the head/tail slicing stays as the graceful fallback above the cap.
2. The chat endpoint already loads the user's role → map FREE=600 / PAID=3,000 / ADMIN=8,000.
3. Tests: per-tier caps, the fallback slice, and the omission marker.

**Related, cheaper alternative if token burn becomes a problem**: timestamp-directed retrieval (user asks about min 30 → fetch segments 25-35 only into context) — much cheaper per question, but worse for open-ended questions ("what's the main argument?"). RAG over transcripts (we already run `nomic-embed-text` locally) is the full fix for later.

---

## 5. Cross-links

- `app/services/chat.py` — `MAX_SEGMENTS = 600`, `transcript_to_chat_text()`
- `app/routers/chat.py` — where the prompt is assembled; role already available
- `doc/mvp2-llm-architecture.md` — tier model chains (FREE→groq, PAID/ADMIN→ollama)
- `doc/roles-tiers-cheatsheet.md` — the tier matrix
- `/admin/budget` — the live quota/usage surface to watch post-launch
- `Todo.md` §1 — MCP/API wishlist (RAG would serve both surfaces)