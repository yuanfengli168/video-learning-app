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