# Known Issues & Findings — 2026-09-21 (first real 1GB+ uploads)

> **Trigger**: the first real >1GB uploads through the new chunked path
> (1.4GB + 1.5GB, 4K Zoom AVI recordings). Upload machinery itself:
> **flawless** (byte-exact assembly, ~12MB/s sustained, zero chunk
> failures). The issues below are all *downstream or source-content* —
> plus one real bug found in the queue's model dispatch.
> **Owner decisions recorded**: no AVI transcode-on-upload for now;
> a reminding notice on unplayable videos instead (Todo #15).

---

## 1. "19-minute transcript on a 30-minute video" — NOT a bug

ffprobe on both source files (4K 30fps mpeg4-in-AVI, Zoom screen
recordings):

| Video | Video track | Audio track | Transcribed |
|---|---|---|---|
| 545e88e9 (HughMeFBR) | 22.7 min | **21.3 min** | 19.2 min ✓ |
| 0c5b8bfa (Zoom) | 46.9 min | **31.9 min** | 31.5 min ✓ |

**The source files' audio tracks are shorter than their video tracks**
(recording stopped/resumed; mic dropped early — classic Zoom artifacts).
Whisper transcribed all the audio that exists. Upload integrity verified
independently: declared size == file size, session completed, all chunks
present. The pipeline's duration-from-audio choice is correct for a
learning app (matches the transcript timeline).

## 2. AVI videos don't play in the browser — container support, not us

**Browsers (Chrome/Safari/Firefox) have never supported mpeg4-in-AVI**
(the MPEG-4 Part 2 variant Zoom records). The file, the `/file` endpoint,
and the MIME type are all correct — VLC/QuickTime play the downloads
fine. Previous `.mp4`/`.webm` uploads play fine through the same
machinery.

**Owner decision (2026-09-21)**: NO transcode-on-upload for now — it
adds wait time per upload. Instead: **a reminder notice** on any video
whose container can't render in-browser ("This format can't play here —
transcript + materials still work. Convert to MP4 with [HandBrake /
the existing tool] for playback.") → **Todo #15**. Transcode-on-upload
stays on the relaunch-polish list (option A, M2 Max media engine,
~30-60s per 30min) if beta data shows users hitting this often.

## 3. Materials failed on 545e88e9 — Ollama quota exhausted + the fallback doesn't exist yet

Confirmed from the events table: `"you (jackyopenclaw168) have reached
your session usage limit, upgrade for higher limits"`. The owner's guess
was right.

**The deeper finding**: the PAID chain is configured `ollama,openai`
but **`OPENAI_API_KEY` is not set in `.env`** — so the "fallback chain"
is a single point of failure. When Ollama quota dies, every materials
generation behind it fails with "All 1 provider(s) in your tier's chain
failed."

**Actions**: (a) owner adds `OPENAI_API_KEY` to `.env` (fallback becomes
real); (b) Retry on the video's page regenerates materials — the
transcript survived. (c) This is live evidence for **14d — the LLM
quota review**, now clearly beta-blocking: quota exhaustion mid-batch
silently kills materials for everyone behind it. Review = per-user
limits + the missing-fallback alerting + the retry UX.

## 4. REAL BUG — the queue dispatch hardcodes model "base" ✅ FIXED `396e9bd` (2026-09-22)

**Fixed 2026-09-22 (`396e9bd`) + production-verified same day**: the
upload of video `5d8b6d2d` (10:38) dispatched with
`whisper_backend=mlx-whisper` — the stamped choice, not "base".
`_staggered_transcribe_job` now reads `video.whisper_model` (stamped
at upload), falling back to the default only when NULL. The
original write-up is kept below for the incident trail.

`_staggered_transcribe_job` (app/routers/courses.py, the mini-queue's
dispatch target) calls:

```python
_run_transcribe_job(video_id, "base")      # ← ignores the stamped choice
```

…instead of the `whisper_model` column the upload stamps on the row
(`local-large-turbo` → MLX large-v3-turbo). **Every upload through the
queue since 9/16 transcribed with faster-whisper "base"** — not the MLX
turbo model: lower quality + slower than intended. The 9/18–19 MLX runs
came from the recovery scripts, which pass the model correctly — which
is why the switch seemed to start 9/20 (uploads resumed through the
queue).

**Impact**: also explains the 7-minute transcribe on the 47-min video —
it ran CPU faster-whisper "base", not the ~5-10× realtime MLX turbo.

**Fix** (one line, next code batch): `_run_transcribe_job(video_id,
video.whisper_model or get_default_model_choice())` — read the stamped
choice at dispatch, fall back to the default only when NULL.

## 5. Q&A from the session (recorded for the doc trail)

**Q: Is Cloudflare's 100MB a shared/per-IP limit?**
**A: Neither — it's per-REQUEST.** One HTTP request body >100MB is
rejected at the edge; nothing about users or IPs. 10 simultaneous
uploaders = 10 independent request streams, each under 100MB — all
fine. The genuinely shared resources are the home uplink bandwidth (the
real ceiling, ~100+ Mbps effective per the 2-minute/1.5GB test) and —
after upload — the 2-slot transcribe queue + Ollama quota.

**Q: 1.5GB / 50 chunks / ~2 minutes — good?**
**A: Excellent.** ~12MB/s sustained through the tunnel = near the
practical fiber ceiling. Validates 32MB×N chunking end to end.

**Q: 10 users × 1GB simultaneously from 10 IPs?**
**A: Works; wall-time ~13-20 minutes for all** (they share the one
uplink; HTTP's natural sharing is the fairness). Then 10 transcribes
through the 2-slot queue ≈ 1-2.5h of steady work with honest queue
positions. The real choke point at that scale is **Ollama quota**
(issue 3), not uploads.

**Q: Why did transcription take 7 min instead of 1?**
**A: Two stacked causes** — (a) the bug in §4 (CPU faster-whisper "base"
instead of MLX turbo), and (b) these are 4K 30fps AVIs (3840×2160 /
3152×1982 — Zoom screen recordings): the audio-extraction step decodes
expensive video streams. 7 min for a 47-min 4K file on CPU ≈ 6.7×
realtime — respectable, but MLX turbo + the stamped-choice fix should
cut it substantially.

---

## Resulting work items

| # | Item | Priority | Status |
|---|---|---|---|
| §4 | Queue dispatch reads the stamped `whisper_model` (one-line fix + test) | **next code batch** | ✅ **DONE** `396e9bd` (2026-09-22, prod-verified: `5d8b6d2d` ran mlx-whisper) |
| §3 | `OPENAI_API_KEY` in `.env` (owner, manual) | immediate | ⏳ **owner decision: deliberately NOT set** (2026-09-21, "no need for now yet") — Ollama-exhaustion failures surface as "All 1 provider(s) failed" until 14d lands a real fallback |
| §3 | 14d — LLM quota review (limits + fallback alerting + retry UX) | **beta-blocking** | ⏳ not started |
| §2 | Todo #15 — unplayable-container reminder notice on the video page | **beta-blocking** (Zoom users will hit it) | ⏳ not started |
| §2 | AVI/mp4 transcode-on-upload (option A) | relaunch polish (revisit if beta data demands) | ⏳ deferred |

## Follow-ups discovered 2026-09-22 (logged while closing §4)

1. **The bulk-upload "some error" popups (18-file and 8-file picks)
were Cloudflare EDGE rejections, not app errors.** The old
multi-file client bundled all files into ONE multipart POST; the
free tunnel plan caps **each request body at 100MB**, so both
batches died before reaching the server — which is also why
NOTHING was logged. Fixed by per-file sequential uploads
(`ab31553`/`dd611ee`): >100MB files take their own chunked
session, ≤100MB the single endpoint. Any future client-side
failure now leaves an `ui.upload` events row (the failure
beacon) even when the request never reached us.
2. **The interrupted-upload banner dismissal never stuck** —
dismiss key derived from the banner's display text vs the load
key derived from filename+size. Fixed in `ab31553` (the stash-
key pattern); the banner now disappears until a NEW sweep occurs.

---

## §6 — `ClientDisconnect` on chunked upload leaves session stuck for full 1h TTL (DISCOVERED 2026-09-26, NOT YET FIXED)

### What the user saw

Owner navigated away from the course page mid-chunked-upload of a
~456 MB / 6-chunk video (session `b7737a0b`). After returning:

- Progress bar was gone (expected — the page was unloaded, the JS
  context destroyed).
- Every new `POST /api/upload-sessions/init` returned **400
  "active session in progress"** for ~1 hour, even though no
  client was still uploading.
- Net result: the user was soft-blocked from any new upload until
  the sweeper caught up.

### Root cause

1. Client opened session `b7737a0b` (`active`), 5 chunks PUT 200.
2. Owner navigated away → Starlette raised
   `starlette.requests.ClientDisconnect` on the 6th chunk PUT.
   This exception is **unhandled** in the chunk handler — it
   surfaces as ERROR 500 in the log. Nothing flips the session
   to `cancelled`.
3. The "one-active-session" rule (created during the 13a sweeper
   design, 2026-09-21, intentionally blocks quota gaming) kept
   every new `init` returning `UploadLimitError` ("active session
   in progress").
4. The session sat `active` for the full 1h TTL. The sweeper
   flipped it to `cancelled` at 19:27 — after which new inits
   succeeded.

The exact "unhandled ClientDisconnect on chunk PUT" exception
was **previously identified on 2026-09-21** as a known wart on
the chunked-upload path; it was parked then because the
manifestation was rare (the user usually stayed on the page).
The 9/26 incident shows it manifests reliably **any time** the
user navigates away mid-upload, which is the natural workflow
on a slow connection.

### Confirmed reproduction

Direct faithful replay against the live DB with the owner's
real `User` row, the real `upload_sessions.create_session()`
service, and the actual `UploadLimitError` catch the router
raises — `init` succeeds immediately after the sweeper runs,
confirming the sweeper is the only release mechanism today.

### Fix options (compared 2026-09-26, no decision yet)

| | **A: Instant cleanup on `ClientDisconnect`** | **B: Client auto-retry on init 400** | **C: Better 400 + self-service cleanup** | **D: Full resume-across-navigation feature** |
|---|---|---|---|---|
| **What changes** | Catch `ClientDisconnect` in chunk PUT, cancel session immediately | On init 400, auto-`DELETE` the user's last active session, retry once | Improve init 400 text + add one-click "Clear and retry" button | New `GET /api/upload-sessions/me` + resume UI states (4-6 chunks visible, resume from chunk N) |
| **Diff size** | ~10-20 LOC + 2 tests | ~30-50 LOC JS | ~30 LOC + 1 button | ~150-300 LOC + 4-6 tests + docs |
| **Solves "blocking" (can't init new upload)** | ✅ Immediately | ✅ Automatically | ✅ Manually (1 click) | ✅ (also resumes) |
| **Solves "progress gone"** | ❌ Still gone | ❌ Still gone | ❌ Still gone | ✅ Resumes from chunk N |
| **Risk of false cancel** | ⚠️ **High** — every Wifi blip, sleep/wake, browser pause cancels | ✅ Low — only fires on actual init 400 | ✅ None — user-driven | ✅ None — session only ends on explicit cancel or TTL |
| **Multi-client safety** | ✅ All clients benefit | ❌ Only the per-file UI; mobile/CLI unaffected | ✅ All clients see the better text | ✅ Server endpoint is universal |
| **New support load?** | ⚠️ Yes — "why did my upload restart? I just refreshed!" | Low — silent recovery | None — user in control | Low — known UX pattern |
| **Backwards compatible** | ✅ No server contract change | ✅ No server contract change | ✅ Additive only | ⚠️ Touches both init flow and chunk flow |

### Owner direction (2026-09-26)

User explicitly chose **C today, D next**: ship C (better 400
text + one-click "Clear and retry") as the conservative, zero-
behavior-change unblock; defer D (resume-across-navigation)
to a separate batch as a real new feature, not a bug fix. A and
B were discussed and rejected.

### Status

- **Diagnosis**: complete (2026-09-26, this section).
- **Fix shipped (2026-09-26/27)**: commits `c011833` (server:
  A-safe + structured 400 + GET /me) + `f4cc494` (client: Clear-and-
  retry modal + at-attempt preflight). Production live after the
  next restart. 5 new + 1 updated tests in `test_upload_sessions.py`;
  full suite at 1651 passing. `CHANGELOG.md` §[2.1.0.11] captures
  the release entry.
- **Fix D (resume-across-navigation UI)**: parked in `Todo.md`
  §18; not started — separate batch.

### Topic 1 (recap for the trail): can progress bar come back if user returns?

**Short answer: no — not today, not without feature D.**

The progress bar lives in the now-unloaded page's JS context.
The DOM node and its update code are destroyed the moment the
user navigates away. A new page load starts from a clean JS
state with no knowledge of the half-uploaded session.

The **data is not lost** — chunks 0-4 (≈364 MB of the 456 MB)
are still on disk in the session's staging dir, valid for 1h
until the sweeper cancels. But the browser **doesn't ask the
server** "do I have an unfinished upload?" — the page-load code
only fetches courses/videos, not upload sessions.