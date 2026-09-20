# Postmortem — The 2026-09-19/20 Outage Series (QueuePool exhaustion)

> **Status**: RESOLVED 2026-09-20 ~19:00. All fixes live + validated end-to-end.
> **Impact**: Two multi-hour site-dark outages (9/19 ~13:00–16:14, 9/20 17:52–18:22 local), plus scattered 500s since 9/16; every error read "Internal server error: TimeoutError" — the worst kind of failure: intermittent, self-"healing" (until the next time), and misleading (blamed YouTube API timeouts for two days).
> **Root causes (3, stacked)**: a queue claim rollback, a macOS fork-safety abort, and — honestly — my own first fix shipped with a bug and a second fix in the wrong layer. All documented below so none of this class happens again.

---

## Timeline (all times local, +0800)

| When | What |
|---|---|
| 9/16 | Transcribe mini-queue ships (2-slot DB-backed scheduler in every worker). The claim bug ships with it — silently (see "why it hid"). |
| 9/19 ~07:00 | User uploads bulk MIT videos + seoul video. Queue claims rows; the rollback bug re-claims them every 3s; duplicate threads pile up. |
| 9/19 13:00–16:00 | Site intermittently dark. User sees TimeoutError on phone. Diagnosed (wrongly) as YouTube-API slowness — plausible because the enrichment calls WERE also timing out. 425 QueuePool errors logged that day. |
| 9/19 evening | "Recovery run" partially works (YouTube throttle blocks caption re-downloads); job-registration fix ships (`5349bca`) — **unmasking** the real bug (duplicates now actually RUN instead of early-returning). |
| 9/20 09:41 | User uploads Hermes Agent 05 (62MB webm). Row sits `queued`; storm rebuilds all day. |
| 9/20 17:52 | Pool fully exhausted → site dark again. All 4 workers ~70% CPU spinning in the scheduler's exception loop, holding no usable connections. |
| 9/20 18:22 | `incident-capture.sh` (built during this incident) grabs py-spy stacks + root-lsof **then** restarts. Evidence: ~15 transcribe threads per worker (60 total) all inside `detect_audio_language` for ONE video; 62 DB fds per worker vs a 30-connection pool. |
| 9/20 18:30 | Root cause #1 identified + fixed: `db.commit()` in `_claim_one`. Mitigation: victim row flipped to `error`. |
| 9/20 18:35 | User retries upload → **502**. New evidence: freshly booted workers dying with SIGABRT seconds after boot, one mid-request (file landed on disk, no DB row committed). Root cause #2: macOS ObjC fork-safety abort. |
| 9/20 18:43 | Fork fix v1: env var in `gunicorn.conf.py` + `app/main.py`. Restart → **still crashing**. |
| 9/20 18:47 | Diagnosis: `ps eww` (as root) shows the env var missing — both placements execute AFTER the master's ObjC-active runtime is already initialized. Also found: the pool watchdog (shipped hours earlier in `c827a7b`) had been TypeError-ing on every tick — `pool.status()` returns a STRING in SQLAlchemy 2.x, not a metrics tuple. |
| 9/20 18:49 | Fork fix v2: export in `scripts/start.sh` (the earliest hook launchd runs — before any Python). Restart. **0 crashes after.** |
| 9/20 18:52 | User re-uploads Hermes. Success: claimed exactly once, 1 worker at 273% CPU (healthy Whisper work), fork-crash counter flat at 42 through the whole upload. **End-to-end validated.** |

---

## Root cause #1 — the queue claim rollback (the storm)

**The bug** (shipped 9/16 in `transcribe_queue.py`):

```python
# _claim_one() flipped the row to 'transcribing' with one UPDATE...
row = db.execute(sql).first()
return row[0] if row else None
# ...but never COMMITTED.
```

The scheduler opens its session with `with SessionLocal() as db:` — on close, SQLAlchemy **rolls back** uncommitted work. So the status flip reverted to `queued` seconds after every claim. Each of the 4 per-process schedulers then re-claimed the same row every 3 seconds, and every claim dispatched **another** `_staggered_transcribe_job` thread that never observed "already claimed".

**The math of the outage**: 15 threads/worker × 4 workers ≈ 60 concurrent transcribes of ONE video, each holding a checked-out pool connection through multi-minute Whisper work → 60 checked-out vs a 30-capacity QueuePool → every request timed out.

**Why it hid for four days**:
1. The claim flip *looked* right in any single-transaction test — conftest sessions commit on teardown, masking it. The regression test we finally wrote reproduces the exact bug shape (claim in a fresh session, verify from a third session).
2. Yesterday's job-registration fix (`5349bca`) actually **unmasked** it: before that, duplicate threads silently early-returned (`get_job` found nothing); after, they *ran* — real Whisper work per duplicate. Classic fix-reveals-bug.
3. The "restart fixes it" pattern (restarts kill the threads, the row reverts, a fresh storm starts quietly) made it look like transient restart-orphaning — we even built the orphan-recovery button partly on that misread. (The button remains useful for the *real* orphan class: in-memory BackgroundTasks dying with workers.)

**This also retroactively explains the entire "stuck queue" saga** — the 9/12 8-video starvation, the 9/18 LangChain hang, the "form-B orphans": the head-of-queue row was being claimed-and-rolled-back forever, so rows behind it never advanced.

## Root cause #2 — the macOS ObjC fork-safety abort (the 502s)

`objc[pid]: +[NSMutableString initialize] may have been in progress in another thread when fork() was called... Crashing instead.`

Gunicorn's master forks workers; when an ObjC initialization (from native deps like ctranslate2) races a thread at the fork moment, macOS **deliberately crashes** the child. We saw the aborts on 9/19 (32, written off as a transient boot race) and 9/20 (38 → 42). The user-visible hit: a worker died **mid-upload-request** — 202 accepted, file on disk, no DB row → "Upload failed: Server error (502)".

**The fix took two tries (both my errors, documented so they don't repeat):**
- **v1 (wrong layer)**: `os.environ.setdefault()` in `gunicorn.conf.py` + `app/main.py`. Plausible, tested, shipped — and ineffective: by the time the config file executes, the master's ObjC-active runtime is already initialized. Live check confirmed the var missing from running processes.
- **v2 (correct)**: `export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` in `scripts/start.sh` — the earliest hook launchd runs, before any Python exists. Verified: 0 crashes after the 18:49 boot, and the counter stayed flat *through* a real upload.

**Lesson**: `ps eww` from a non-root shell silently shows an empty environment for root processes — it called v1 "live" and would have called v2 "missing" too. Trust behavioral signals (crash counter, SIGABRT log lines), not env inspection, when the target runs as root.

## Root cause #3 — my watchdog shipped broken

The pool watchdog (`c827a7b`, meant to be the self-healing layer) crashed on every tick: it indexed `engine.pool.status()` as a tuple, but in SQLAlchemy 2.x `status()` returns a **human-readable string**. The real metrics API is `checkedout()` / `checkedin()` / `overflow()`. Found via the incident-capture evidence (TypeError tracebacks sitting right next to the fork-crash lines). Fixed in `39a5ce5`.

No test would have caught it — the watchdog only runs in live processes, and its tick exceptions were swallowed by design ("never die"). The `except Exception: log.exception` pattern is right; shipping it without one manual tick-verification was not.

---

## The fixes (all live, all tested)

| # | Fix | Commit | Test |
|---|---|---|---|
| 1 | `db.commit()` in `_claim_one` — claim is durable cross-process | `c827a7b` | `test_claim_survives_session_close` (reproduces the exact rollback shape) |
| 2 | Per-process `_dispatched_videos` guard — duplicate dispatch refused even if a claim bug ever regresses | `c827a7b` | `test_duplicate_dispatch_refused` |
| 3 | Pool watchdog, repaired (`checkedout()` API) — sustained >24/30 checked-out for 3 samples → diagnostic log + `engine.pool.dispose()` (self-heal ~90s) | `39a5ce5` | live-verified by design; tick manually verified |
| 4 | `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` in `start.sh` (pre-Python) + belt-and-braces in config/main | `16671f2` | behavioral: 0 SIGABRTs since; counter flat through real upload |
| 5 | `incident-capture.sh` — capture-then-recover: py-spy stacks + root-lsof + TCP + log tail, THEN restart | `8580406` | used live in this incident |

**End-to-end validation (9/20 18:52)**: the Hermes re-upload — the exact action that produced the 502 — succeeded with a healthy single-claim profile (1 worker at 273% doing real Whisper work; 3 idle; no storm, no crashes).

## What the user asked for, and how it's covered now

> "1) shouldn't happen next time. 2) should fix by itself as quick as possible next time."

1. **Shouldn't happen**: root cause fixed + regression-tested (claim commit); belt-and-braces guard (duplicate dispatch refused with an ERROR log even on any future regression).
2. **Self-fix fast**: the (now working) pool watchdog disposes a leaking pool in ≤90s without a restart; gunicorn respawns dead workers; launchd KeepAlive respawns the master; and if anything ever looks wrong while you're at work: `sudo bash scripts/incident-capture.sh "reason"` grabs complete evidence and recovers in one command.

## Open follow-ups (non-urgent)

- The fork-safety env var applies to the app daemon; the backup/refresh LaunchDaemons run short-lived scripts (low fork exposure) — revisit only if their logs ever show the same abort.
- Consider a doctor.sh check for the fork-crash counter (trendline, not just count — the log is append-only).
- Whisper-output duplicate lines (Todo #12) and the chunked-upload project (Todo #13) are unchanged in priority by this incident — though the pool watchdog now also guards that project's future background threads.