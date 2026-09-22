# Limits Registry — The Single Source of Truth

> **Status**: Living reference. Every numeric limit in CapySmart, one table,
> per tier, with its env var, its enforcement point, and when it changed.
> **Why this doc exists** (2026-09-21): limits lived in scattered code
> constants and doc addenda; the 600-segment cap shipped with no central
> record, and the upload-size decisions spanned three doc rounds (A1–A9).
> This is the registry that prevents drift. **Rule**: when a limit changes,
> update this doc in the same commit as the code.
>
> **Related**: `roles-tiers-cheatsheet.md` (who gets what capability — links
> here for the numbers), `PriceAndCost/upload-size-architecture.md` (the
> economics/decisions A1–A10 behind the upload/storage rows).
>
> **Last updated**: 2026-09-22 (13a/13d-lite marked SHIPPED — they went live 2026-09-21; §3 notes the 2026-09-22 per-file multi-upload client change)

---

## The model (ratified 2026-09-21)

Two resolution layers, same for every limit family:

```
effective_limit(user) = user override (if set)  →  else tier default (env var)
```

- **Tier defaults are env vars** (pydantic settings) — changeable without
  code, per the "test at 1GB first, raise later" plan.
- **Per-user overrides are DB columns** — the paid-add-on infrastructure
  ("they paid more → flip them to 50GB/100GB/larger files" = one SQL update
  via the flip-kit, no code). Ship nullable columns; NULL = tier default.
- **Role ints never renumber** (see cheatsheet §1): 0=ADMIN, 1=PAID, 2=FREE.

---

## 1. Upload — max file size (13a, **SHIPPED 2026-09-21**)

| Tier | Default | Env var | Per-user override |
|---|---|---|---|
| ADMIN | 20 GB | `UPLOAD_MAX_FILE_ADMIN_GB` | `users.max_file_bytes` |
| PAID | **1 GB** (start; raise to 2/4GB via env/override once 32MB chunks are proven) | `UPLOAD_MAX_FILE_PAID_GB` | `users.max_file_bytes` |
| FREE | ❌ no upload capability at all (`UPLOAD_VIDEO` is PAID+) | — | — |

**Enforced at**: chunked-upload `init` (fail-fast, before any bytes
transfer — browser knows file size, so the UX is instant), AND the legacy
single/bulk endpoints (same resolver — no dodging the limit via the old
path). **The chunk-sum is what's checked, never per-chunk** (the trap noted
in the upload-architecture doc).

**Client+server**: the UI warns at file-pick ("This file is 1.8GB — your
plan allows up to 1GB per video. Try compressing it, or ask us about larger
uploads."); the server re-validates at init (the client can be lied to).

**History**: 100MB tunnel cap (commit `12242e0`) → A8 ratified 4GB →
2026-09-21 owner decision: **start at 1GB, infra ready to raise**.

## 2. Storage — total quota (13d-lite, **SHIPPED 2026-09-21**)

| Tier | Default | Env var | Per-user override |
|---|---|---|---|
| ADMIN | **100 GB** (soft/documented cap — see note) | `STORAGE_QUOTA_ADMIN_GB` | `users.storage_quota_bytes` |
| PAID | **25 GB** | `STORAGE_QUOTA_PAID_GB` | `users.storage_quota_bytes` |
| FREE | ❌ n/a (no uploads) | — | — |

**What counts** (ratified 2026-09-21, revised same day): **completed videos
+ ACTIVE staging sessions at their DECLARED size** (the reservation model,
see §3a). Why declared not actual-bytes: actual-bytes is gameable at the
margin (abandon at 99% repeatedly — each new init passes because "only
990MB staged"); declared keeps check/display/actual one consistent number,
and one-active-session-per-user prevents stacking abuse. Meter wording:
"24.0 GB of 25 GB used — includes 1.0 GB reserved for an upload in
progress." The instant-release valve is the DELETE endpoint; the sweeper
(1h TTL, §3a) is the automatic backstop. Transcripts/materials (~KBs each)
are EXCLUDED from the count by design — the meter stays legible, and the
learning value survives deletions.

**ADMIN 100GB note** (owner-ratified with eyes open): admin owns the box
and the override column — this is a soft/documented cap that reflects the
~200GB "owner content" slice of the 1.8TB volume budget. If it ever blocks
admin work, flip the override (practical difference ≈ nil).

**Capacity math behind 25GB** (from the pricing doc): 50 paid users worst
case = 1,250GB = 68% of the 1.8TB volume, with ~300GB headroom reserved
and the admin's 100GB budgeted separately.

## 3. Chunked uploads — transport limits (13a, **SHIPPED 2026-09-21**)

| Limit | Value | Env var |
|---|---|---|
| Chunk size | 32 MB (under the 100MB edge cap; ≤~30s/request even on weak uplinks; a 1GB file = 32 chunks) | `UPLOAD_CHUNK_SIZE_MB` |
| Active sessions per user | 1 (uplink self-serializes; blocks quota gaming) | — (constant) |
| Abandoned-session sweeper | **1h no-activity → staging deleted** (2026-09-21 revision, see §3a) | `UPLOAD_SESSION_TTL_HOURS` (default 1) |

**2026-09-22 client addendum — per-file multi-upload:** the course +
dashboard pages no longer bundle multiple selected files into ONE
bulk POST (the 100MB edge cap rejected the whole bundle — two real
incidents that left ZERO server-side trace). Each file now takes its
own tunnel-safe path: >100MB → its own chunked session, ≤100MB → the
single endpoint. A 429 cap stops the batch with the honest reason;
every client-side failure is beamed to the server (`ui.upload`
telemetry — see roles-tiers-cheatsheet §B).

### §3a — The sweeper design (ratified 2026-09-21, after two full design rounds)

**Round 1 (24h TTL + resume-across-restart)** was superseded by **Round 2**
below. Both rounds logged here because the *reasoning* is the reusable part.

**The physical fact that shapes everything**: with chunked HTTP uploads, the
server receives **no signal at the moment of abandonment** — tab close, lid
close, browser crash, and server crash all look identical (client silence).
The only detector available is "no chunks for X time." **There is no
instant.** (Browser `pagehide` beacons were considered and rejected: they
also fire on refresh — killing every F5 — and never fire on sleep/crash.)

**Abandonment taxonomy** (the scenarios the design must cover):

| Scenario | What happens | Swept? |
|---|---|---|
| A. Genuine abandonment — user picks a big file, sees the estimate, closes the tab forever | Chunks stop; timer runs | ✅ the classic case |
| B. Sleep/laptop-close — 60% done, lid closes, wakes later | Old design: resume within 24h. **New design: re-upload from zero** (the accepted trade, see below) | ✅ (at 1h) |
| C. Deliberate but unfinished — "I'll finish tonight" | Same as B | ✅ if they don't return within TTL |
| D. **Our crash/orphan** (the 9/20 fork-crash pattern: request accepted, process dies) | Session dead but looks active — the Hermes orphan-files incident, systematized | ✅ — **and 1h TTL beats 24h here; this was the owner's key improvement** |
| E. Staging probing — someone stages chunks to test how long free storage persists | Timer runs | ✅ exposure capped at TTL×size |

**Round 2 — the ratified simple model (owner decision 2026-09-21):**

- **TTL = 1 hour of no chunk activity** (env `UPLOAD_SESSION_TTL_HOURS`,
  default 1). "Effectively immediate in human terms" while barely surviving
  a lunch-break lid-close. Sweep pass every ~10 min.
- **During-upload robustness is unchanged and stays**: automatic per-chunk
  retries absorb flaky networks *mid-upload* (that's robustness during a
  session, distinct from resume *across* sessions).
- **Mid-flight failure UX**: client error toast — "Upload failed —
  connection dropped. Retry / Cancel." Retry re-sends from zero (1GB ≈
  2–10 min on typical wifi); **Cancel = instant space release** (the
  DELETE endpoint).
- **The permanent banner (owner's "error sign somewhere")**: after a sweep,
  the user's next visit to the upload page shows, until their next
  successful upload completes (with a dismiss ✕): *"Your last upload was
  interrupted and cleaned up — nothing was lost, just upload again."*
  Transparent garbage collection, no surprises, actionable.
- **Claim mechanics (the c827a7b lesson applied by construction)**: sweeper
  claims rows with an atomic UPDATE → **commits the claim immediately** →
  deletes the staging dir (filesystem is the session's source of truth —
  the resume bitmap is a directory listing; deleting is idempotent) →
  row marked `cancelled` + an events-table row per batch
  (`source='services.upload_sweeper'`: count + bytes freed — the beta's
  abandonment-rate data).
- **Runner**: in-app sweeper thread (the proven queue pattern) — testable
  with the repo's standard db_session fixtures; hourly cadence × 4 workers
  via the claim; NOT a LaunchDaemon (least-testable path; the queue thread
  pattern is now battle-tested after the 9/20 incident fixes).

**The conscious trade-off (ratified with eyes open)**: resume-across-restart
(scenario B) is **dropped for beta** — a swept session means re-upload from
zero. Quantified: at the beta's 1GB cap this costs 2–10 minutes and most
uploads finish in one sitting; at relaunch's 2–4GB (30–60+ min uploads,
common interruptions) it would be exactly the pain chunking exists to
remove. **Resume returns at relaunch** — cheaply, because the
filesystem-as-truth design keeps the server-side capability nearly free
(the expensive part was always the client UX states we're deferring).
Revisit trigger: when file limits are raised past 2GB.

**Quota basis**: declared-size reservation (init reserves the full
declared size; the meter shows "includes X GB reserved for an upload in
progress"; one active session per user prevents stacking abuse). Rationale:
actual-bytes counting is gameable at the margin (abandon at 99% repeatedly);
declared-size keeps check/display/actual one consistent number; the DELETE
endpoint is the instant-release valve.

**Why 1h and not shorter**: any threshold in seconds kills real uploads on
cellular (30–60s stalls are routine); minutes kills lid-close; 1h survives
all legitimate pauses while capping exposure at ~1h×size×users (all 50 PAID
users abandoning 1GB simultaneously = 50GB for an hour = 2.7% of the
volume, trivial). Why not longer: staging counts toward quota — a parked
session holds the user's meter hostage, and 24h would block their next
upload for a day.

## 4. Chat — transcript segments in context (SHIPPED, commit `498eb4e`)

| Tier | Cap | Enforcement |
|---|---|---|
| ADMIN | 8,000 (~144K tok) | `segment_cap_for_role()` at session creation; over-cap keeps head+tail with an honest `[N segments omitted]` marker |
| PAID | 3,000 (~54K tok) | same |
| FREE | 600 (~11K tok) | same (Groq model's real context limit) |

**History**: global 600 for everyone (MVP2 era) → tier-aware 2026-09-20.
Numbers + economics: `PriceAndCost/chat-context-economics.md`.

## 5. LLM — request rates (SHIPPED; display on /usage; per-worker in-memory enforcement)

| Tier | Rate | Notes |
|---|---|---|
| FREE | 15/day (Groq) | display-only on /usage today; enforcement is the per-worker limiter (known 4x-loose gap, tracked) |
| PAID | 50 / rolling 7h + 100 / fixed Mon–Sun week (Ollama chain) | display-only; 14d will review these numbers vs 100 users into finals |
| ADMIN | same chain as PAID | quota shared: Ollama 800 req/5h, 3000/week shared across ALL paid+admin |

**14d review note** (not yet done): 50 users × ~20 q/wk ≈ 1000/wk fits,
but finals-season spikes breach the shared 3000/wk → spills to paid OpenAI
fallback (est. S$20–50/mo). Review per-user limits BEFORE invitations.

## 6. Upload count caps (SHIPPED, audit decision #9)

15 uploads/day + 6 in-flight per user (single+bulk combined, projected
per-file in bulk). These predate the storage quota; both stay — count caps
bound *behavior*, quota bounds *bytes*.

## 7. The 100MB tunnel cap (SHIPPED, commit `12242e0`) — PLATFORM, not product

Cloudflare's free plan rejects >100MB request bodies at the edge — this is
why chunked uploads (§3) exist. `TUNNEL_MAX_FILE_SIZE` remains as the
per-request cap for the LEGACY direct path; per-FILE limits (§1) are the
product promise enforced at init. Direct localhost/LAN admin path: 10GB
per request, unchanged.

## 8. Transcribe queue (SHIPPED): 2 global slots, 3s poll, 5s stagger

Do not raise SLOTS without a live throughput test (audit decision #12).

---

## /usage page — what displays (ratified 2026-09-21; ships with 13a/13d)

1. **Storage card** (PAID+ADMIN): "X.X GB of 25 GB used" bar; storage-full
   state shows the honest math + the delete-to-free-space action.
2. **"Your limits" summary card**: per-file upload limit, storage quota,
   chat segments, LLM rates — **sourced from the same settings the code
   enforces** (anti-drift: the page can never disagree with reality, the
   same principle as the server-computed orphan badge).
3. **FREE users see a friendly version** (owner decision 2026-09-21: "make
   them feel welcomed, and even let them want to upgrade"): their real
   numbers (600 segments, 15 chats/day), the FREE-tier value stated
   warmly, and what PAID unlocks (uploads, bigger chat context) with the
   upgrade link. No dark patterns — numbers that genuinely invite.

## Change log

| Date | Change |
|---|---|
| 2026-09-21 | Registry created. §1–3 ratified (not yet implemented): 1GB PAID file cap (env), 20GB ADMIN (env), 25GB/100GB quotas (env), per-user override columns, 32MB chunks, per-user override infra. §4–8: existing shipped limits, recorded for completeness. |
| 2026-09-21 (later) | **Sweeper revision after two design rounds** (§3a): TTL 24h → **1h** (env `UPLOAD_SESSION_TTL_HOURS`); resume-across-restart DROPPED for beta (re-upload from zero after a sweep — quantified trade, revisit at relaunch when limits pass 2GB); permanent interrupted-upload banner on the upload page (with dismiss ✕); in-app sweeper thread (not a LaunchDaemon); declared-size quota reservation; sweep claim-commits immediately (the c827a7b pattern by construction). Owner's key insight captured: shorter TTL *improves* scenario D (our own crash-orphans squat the user's quota for 1h, not 24h). |
| 2026-09-22 | **Status flip: §1/§2/§3 are SHIPPED** — 13a (chunked uploads, commits `5ee1760`→`57534ee`) + 13d-lite (storage quota + /usage cards) went live 2026-09-21 and were production-verified (1.4GB + 1.5GB browser uploads byte-exact). §3 addendum: the multi-file client is per-file sequential since `ab31553` (the bundled bulk POST died at Cloudflare's edge — 100MB per-REQUEST cap — with zero server-side trace; two real incidents). **KNOWN GAP (owner decision pending): the swap-to-MP4 flow doubles on-disk usage** (original + converted file both stay) while the /usage meter counts one — see mvp2-storage note in session docs; delete-original-on-swap proposed. |