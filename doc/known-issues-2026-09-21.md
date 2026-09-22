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