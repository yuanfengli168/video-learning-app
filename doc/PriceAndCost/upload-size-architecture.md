# Upload Size Architecture — The 100MB Tunnel Cap & 4GB Paid-User Uploads

> **Status**: Discussion captured 2026-09-20; **decision: not yet made** — Option A (chunked uploads) recommended, awaiting sign-off before any code.
> **Why this doc exists**: a PAID user's real need (200MB–4GB videos) collides with the Cloudflare tunnel's request-body cap. The research showed the obvious "just pay more" answer **does not exist** at any plan tier, so this is a design decision, not a billing one. The discussion happened in an AI-pairing session and would otherwise be lost.
>
> Companion doc: `chat-context-economics.md` (same folder, same pattern — a business constraint that looks like an engineering bug).

---

## The problem

Paid users want to upload their own videos through **www.capysmart.com**. Real file sizes:

| Content type | Typical size |
|---|---|
| Short lecture clip | ~200 MB |
| Full course recording | 3–4 GB |
| A batch of several videos | ≤ 4 GB total (per request session) |

Today's behavior (commit `12242e0`): the app enforces a **path-aware cap** — 100MB through the Cloudflare tunnel (proxied requests), 10GB direct to `localhost:8000`/LAN. That cap is correct and honest: Cloudflare's edge **rejects >100MB request bodies before they ever reach the origin**, surfacing as an opaque "server error" in the UI. The direct 10GB path is an admin-only workaround (you must be on the server machine), not something a paying remote user can use.

**So the product question: how does a remote PAID user upload a 4GB file, without paying more?**

---

## The critical research finding: no Cloudflare plan fixes this

The request-body limit is per-plan and enforced at the **edge**:

| Cloudflare plan | Max request body |
|---|---|
| Free | **100 MB** |
| Pro ($20/mo) | **100 MB** |
| Business ($200/mo) | 200 MB |
| Enterprise (custom, ~$5k+/mo) | ~500 MB+, negotiable |

There is **no plan tier that lets a single 4GB POST through**. Upgrading money buys WAF/speed/limits elsewhere, not upload size. This kills the naive options list:

- ~~Raise the limit in the app~~ — the edge rejects before our code runs
- ~~Pay Cloudflare more~~ — no tier with a big enough body limit
- ~~Compress on the client~~ — 4GB of video is already compressed; transcoding in the browser is slow and lossy

**Any solution must avoid sending a large file as a single request through the edge.** That reframes the problem: not "raise the ceiling" but "never hit it."

---

## Option A — Chunked / resumable uploads *(recommended)*

The YouTube/Google Drive pattern, inside our existing app and tunnel.

**Flow:**
1. Browser-side JS slices the file with `File.slice()` into **32–64MB chunks**
2. `POST /api/videos/upload/init` → server creates an upload session (row/ID), returns session token
3. Each chunk is its own `POST /api/videos/upload/{session_id}/chunk/{n}` — every request is under 100MB, so the edge is happy; server appends to a temp file on the NVMe
4. `POST /api/videos/upload/{session_id}/complete` → assemble, create the Video row (status `queued`), hand off to the **existing transcribe queue** — everything downstream is unchanged
5. A sweeper (or a check on next upload) deletes abandoned partial uploads older than N hours

**Cost: $0.** Existing tunnel, existing auth (session cookie capability checks), existing disk.

**Chunk-size choice matters:** each chunked request should complete in <~30s so it stays comfortably under Cloudflare's ~100s proxy timeout even on weak uplinks. 32–64MB chunks satisfy this on typical connections; we can adaptively shrink to 16MB if the client detects slow throughput.

**UX wins beyond size (this is not just "bigger"):**
- Per-chunk progress bar (real % instead of today's indeterminate spinner)
- **Retry only the failed chunk** — today a 700MB upload failing at 99% restarts from zero
- **Resume across a browser refresh** — session token + chunk bitmap lets the client ask "what have you got?" and skip finished chunks
- Batch ≤4GB: chunk each file sequentially; the queue already serializes processing

**Effort:** ~1–2 days. New endpoints (init/chunk/complete + resume-status), client JS, abandoned-upload sweeper, tests. Fits existing patterns (capability checks, BackgroundTasks, the queue).

**Risks / open questions:**
- Disk headroom: 10GB/file × concurrent paid users is a different profile than admin-only uploads. Check NVMe free space before shipping; consider a per-user concurrent-session cap.
- The app's own `TUNNEL_MAX_FILE_SIZE` check (`_effective_max_file_size`) must apply to the **sum of chunks**, not per-request — otherwise the cap logic defeats itself.

## Option B — Direct-to-R2 presigned uploads *(back pocket)*

Browser PUTs the file **directly to a Cloudflare R2 bucket** (bypasses the tunnel entirely), then our server pulls it from R2 to local NVMe and deletes it from the bucket.

- R2 free tier: 10GB storage, zero egress fees. Spool-then-delete keeps 1–2 concurrent 4GB uploads inside the free tier; overage is pennies per GB-month.
- **Cost:** ~$0 in practice, but real **complexity**: bucket + API tokens + lifecycle rules + a download step + secret management.
- **When it becomes the right answer:** if home-uplink saturation ever makes in-app chunking painful (many concurrent remote users), or if we want uploads to work while the Mac Studio is offline. Not now — Option A solves the stated problem in-app.

## Option C — Grey-cloud an upload-only hostname *(rejected)*

Point `upload.capysmart.com` DNS-only (no proxy) at the home IP with a Let's Encrypt cert. Bypasses Cloudflare → no limit.

**Why rejected:**
- Exposes the home IP publicly (DDoS/privacy — the tunnel architecture exists precisely to avoid this)
- Requires a real public IPv4 (many ISPs run CGNAT — likely won't work at all)
- An exposed origin with an upload endpoint is a security surface we'd otherwise never open

---

## Physical constraints to state out loud (independent of the fix)

1. **The home uplink is the real ceiling.** A remote user's 4GB upload is bounded by the server connection's *upload bandwidth* (what users *download* into our server rides on our line's *download*, which is usually the fat side — but symmetric residential lines are rare). At 500 Mbps down, 4GB ≈ 1–2 min; at 100 Mbps, ~6 min. Chunking changes reliability, not physics.
2. **Transcription is the next bottleneck after upload.** A 3–4GB video is likely a ~2–4 hour recording: it will hold one of the **2 global queue slots** for a long Whisper run, and its transcript will blow past even the ADMIN 8000-segment chat cap. Long-video handling (chunked transcription, or per-part chat) is a separate, later conversation — flagged here so nobody is surprised.
3. **Disk**: verify `/Volumes/Storage-Medium-NVMe` headroom policy for multi-user 10GB files before launch-week.

---

## Recommendation & decision status

**Option A now** ($0, self-contained, upgrades UX rather than just lifting a number). **Option B later** if/when scale demands. **C never.**

Sequencing proposal: write the endpoint design doc for sign-off (init/chunk/complete schemas, resume-status response, sweeper policy, per-tier upload caps — e.g. should FREE users get chunked uploads too, or is >100MB a PAID feature?), then implement in the A/B/C commit pattern this repo already uses.

**Not started — awaiting go-ahead.** Related reading: `mvp2-storage-architecture.md` (where uploads live), `roles-tiers-cheatsheet.md` (tier gating questions), `chat-context-economics.md` (the same "business limit that looks like a bug" family).

---

## Addendum — pricing & semantics brainstorm (2026-09-20, later same day)

> A second round after the initial doc. Product framing shifted from "three
> add-on categories" to "**one included tier + one add-on**", plus a storage
> sustainability model for ~100 paid users. Decisions marked **[DECIDED]** or
> **[OPEN]**.

### A1. Corrected ladder: PAID includes ≤500MB, one add-on above it

**[DECIDED]** Every PAID user can upload files up to **500MB** with no
purchase — chunked uploads ship as part of the base PAID plan. Add-ons exist
ONLY above 500MB. (Earlier draft wrongly had a "≤500MB add-on" purchase —
corrected by product owner: "any paid user has ability to upload ≤500MB".)

### A2. Per-file vs batch-total semantics

Two models were laid out in detail:

- **(a) batch-total**: the whole selection's sum must fit the tier → awkward
  partial-acceptance UX, and trivially circumvented by re-submitting files
  one at a time (a limit that users route around breeds resentment).
- **(b) per-file** *(recommended)*: each file evaluated independently;
  oversized ones are skipped pre-upload with a per-file reason; the rest
  proceed. Matches the existing bulk architecture (audit decision #9 already
  built per-file skip-with-reason + projected caps), and chunked uploads are
  inherently per-file anyway (one session per file, tier checked at init).

**[OPEN — leaning (b)]** The worked example that sold (b): user selects
100MB + 200MB + 150MB + 800MB → first three upload, video 4 shows
"800MB — above your 500MB plan. Unlock 1GB uploads →" with ZERO wasted
bytes (the browser knows file.size before upload).

### A3. Hard cap + single add-on

Product instinct: cap the platform at a modest size and teach users to
shrink beyond it, rather than selling ever-bigger tiers:

- **Proposed: 1GB hard cap**; single add-on "Large Uploads — up to 1GB",
  **$4.99 one-time** (pending the OPEN question below).
- Failure messages must teach: "2.3GB is over the 1GB limit. Quick fix:
  HandBrake → H.265, or your phone's 'high-efficiency' recording setting
  gets most lectures under 1GB."
- Content-reality table that motivates the cap: >1GB files are exactly the
  ones that stress the queue (hours of Whisper holding a 2-slot global
  queue), the chat context cap (3–4h video blows past even ADMIN 8000
  segs), and disk.
- **[OPEN]** Is the 3–4GB need customer-real or admin-own-workflow?
  If customers genuinely need 3–4GB, a 1GB cap fails them on day one
  (cap should then be 2GB and add-on 500MB→2GB). The localhost 10GB
  direct path covers the admin's own big files either way.
- Future "no friction" option (NOT now): server-side transcode via the
  M2 Max media engine — upload anything, we shrink to ≤1GB automatically.

### A4. Purchase flow — Stripe Checkout

**[DECIDED]** Stripe Checkout hosted page (payment UX, receipts, cards,
errors all handled by Stripe); we implement: entitlement check at upload
init, webhook handler, and the upsell cards. Surfaced at (1) the failure
moment — inline "unlock" card, and (2) a dashboard/uploads settings card
for buy-ahead. No in-app billing UI.

### A5. Sustainability math for ~100 paid users — storage is the real lever

The packs don't sustain anything; **disk does**:

- Included-tier habit ≈ 5 videos/mo × 500MB = 2.5GB/user/mo
- 100 users → **250GB/month → the 1.8TB NVMe fills in ~7 months**
  regardless of add-ons
- **[OPEN — leaning yes] Proposed: per-user storage quota ~15GB**
  (1.8TB ÷ 100 = 18GB each; 15GB leaves margin; a 2TB drive is a rounding
  error against $1,499/mo revenue if we'd rather scale than limit).
  Needs: storage meter on dashboard, a "storage full — delete to continue"
  state, and VERIFICATION that deleting a video actually frees the source
  file from disk (unverified as of this doc).
- The asymmetry that saves us: transcripts + materials are ~100–500KB per
  video — the learning value survives at negligible cost even with tight
  source-file management.
- GPU is fine: 100 users ≈ 250 content-hours/mo ≈ 25–50 MLX-whisper hours
  ≈ 1–2h/day. Uplink fine: chunking self-serializes.
- **Long-term pricing insight**: storage quota (+50GB for $X/mo, the
  iCloud/Dropbox model) is the more honest add-on — upload-size add-on now
  (matches user intent: "this file is too big"), storage add-on later
  (matches our cost reality). They compose.

### A6. Batch limits

**[OPEN — leaning drop]** Drop any batch item-count cap (the existing
15/day + 6 in-flight already bound volume); optionally enforce one
concurrent upload session per user (uplink serialization makes more
concurrency pointless anyway).

### Sequencing (unchanged + expanded)

1. Chunked upload core (entitlements stubbed: PAID=500MB internally, unlimited while testing)
2. Per-file tier checks + skip-with-reason batch UX (Stripe not needed yet)
3. Stripe Checkout + webhook + entitlement
4. Storage quota + meter (the sustainability lever — may move earlier if user growth is fast)
5. Then: launch tests, Whisper dedupe (#12), long-video handling (its own doc someday)

---

## Addendum A8 — pricing verdict + the beta program (2026-09-20, third round)

> Decision update: **PAID = 19.99 SGD/mo, 4GB max per video, 25GB storage** (the
> A3 size-ladder sketch is superseded). Beta program ratified with five fixes.

### The competitive honesty (kept from the critique, still true)

NotebookLM is free and does ~70% of this product's core flow. The defensible
wedge: complete study-kit (mindmap+flashcards+quiz+timestamped chat citations
in one flow), Chinese/English mixed-content handling, YouTube playlist bulk
import. The price is defensible ONLY alongside that wedge and a trial that
lets users feel it.

### The beta program (owner's plan, 2026-09-20)

- 19.99 SGD/mo PAID; 4GB/video; 25GB storage
- Invite **50 FREE users + 50 founding PAID users**
- Founding members: **free for 3 months**, in exchange for feedback
- Goal: 50 paying users after "beta success"

### The five fixes (ratified into the plan)

1. **Card on file at day 0** ($0 hold, charge at day 90). The strongest
   conversion predictor; without it, month 3 asks 50 people to pay from a
   standing start. Stripe supports this natively.
2. **Founding-member price lock**: beta users keep 14.99 SGD forever; 19.99
   becomes the true list price for post-beta users. Rewards risk-takers,
   makes the anchor real, testable.
3. **FREE cohort = funnel experiment**: free tier gets exactly ONE trial
   upload (the "magic moment" — their own lecture → full study kit) plus
   browse/limited chat. Founding cohort = retention + conversion experiment.
   Two experiments, one beta. The FREE tier today cannot upload at all —
   without a trial upload, free users never feel why to pay.
4. **Feedback structure**: route to the existing Discord
   (COMMUNITY_INVITE_URL already wired), weekly 3-question pulse, 10× 15-min
   interviews, and mine the events table (what people DO > what they say).
5. **LLM quota is the shared bottleneck, not storage**: Ollama 3000 req/wk
   is shared across ALL paid chats; 50 users ≈ 1000/wk typical but exam-season
   spikes will breach → OpenAI gpt-4o-mini fallback costs real money
   (est. S$20–50/mo peak). Review per-user rate limits before invitations.

### Beta success criteria — defined BEFORE invitations (else post-hoc rationalization)

- ≥35/50 founding users still weekly-active in month 3
- ≥40% of founding cohort converts to paying at day 90
  (industry free→paid is 5–15%; founding psychology buys up to ~40%)
- ≥10 structured interviews completed
- Zero data-loss incidents
- If conversion <40% → iterate price/product BEFORE growth spend

### Economics of the free beta

3 months, 100 users, S$0 revenue. Costs: electricity ~S$30–60/mo + OpenAI
fallback (~S$20–50 peak months) ≈ **S$200–400 total**. Cheap validation —
but budgeted, not discovered.

### Capacity recheck at this exact shape (50 paid × 25GB)

1,250GB worst case = 68% of the 1.8TB volume — fits with the earlier headroom
math. GPU at 50 users ≈ 12–25 whisper-hours/mo — fine. FREE trial uploads:
50 × ≤200MB = 10GB — trivial. The 2GB-vs-4GB concern from A-critique is
accepted-with-eyes-open: keep 4GB as the promise, watch streaming bandwidth
+ Cloudflare free-plan video-serving ToS exposure during beta, and revisit
if the tunnel complains (R2 playback is the escape hatch).