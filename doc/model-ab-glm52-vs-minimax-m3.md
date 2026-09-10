# Model A/B Evaluation: glm-5.2:cloud vs minimax-m3:cloud

**Date:** 2026-09-09 (run completed 2026-09-10) · **Status:** evaluation only —
glm-5.2:cloud stays the production model through the 9/15 launch (decision
recorded 2026-09-09)

## ⚡ EXECUTIVE RESULT (read this first)

**m3 passed all hard gates. It is a viable switch candidate — but glm-5.2 is
structurally more disciplined, and the one measurable gap is in topic
timestamps (clickable mindmap navigation). Verdict: don't switch for launch;
re-evaluate in week 2–3 with the fixes below.**

| Metric (23 videos each) | glm-5.2:cloud | minimax-m3:cloud |
|---|---|---|
| JSON parse (production parser) | ✅ **23/23** | ✅ **23/23** |
| Fully clean structure | ✅ **23/23** | ⚠️ **18/23** |
| Thinking-block contamination | ✅ 0 | ✅ 0 |
| Latency (median) | 22.6s (12.8–66.8) | **24.4s** (7.7–35.0) |
| Flashcards / video (median) | 8 | 9 |
| Quiz questions (median) | 5 | 5 |
| Topic timestamps (median) | 8 | **14** |
| Mindmap density (sample) | 19 nodes | **52 nodes** |

**The 5 m3 structure issues — all one class, all topic↔mindmap:**
m3 sometimes phrases a `topic_timestamps` entry differently from the exact
mindmap node name (e.g. `"Explore, Plan, Code, Commit Workflow"` vs the node
`"Explore, Plan, Code, Commit"`, or title-case vs lower-case). Affected videos:
*Lesson 10 (Diligence)*, *Explore→Plan→Code→Commit*, *Lesson 1*, *Lesson 4*,
*Lesson 6*. **User impact:** the affected topic's mindmap click falls back to
the ancestor-walk instead of an exact timestamp jump — degraded navigation on
~1 of ~14 topics per video. No crashes, no parse failures, nothing breaks.

Content-quality spot-check (same video, both models): flashcard terms and
definitions are **nearly identical**; quiz questions test the same concept
with the same correct answer. The visible differences are **mindmap density**
(m3: 52 nodes vs glm: 19 — richer but visually busier) and **more topic
timestamps** (14 vs 8 — finer navigation IF the names match).

## Why this document exists

minimax-m3:cloud is cheaper than glm-5.2:cloud on the Ollama Pro plan. If it
produces equal-quality learning materials, we switch and save money. But the
generation pipeline asks for strictly-structured JSON and its parser had
never been tested against a **thinking model** — so quality can't be judged
until the *hard gates* pass.

**This was an offline shadow test.** It read transcripts from the DB, sent
the exact production prompt (`GENERATION_SYSTEM_PROMPT` from
`app/services/llm.py`), parsed with the exact production `_extract_json`, and
wrote everything to `/tmp/model-ab/`. **Nothing was written to the assets
table, no video status changed, nothing appears on any page.**

## Hard gates — all passed by both models

| # | Gate | Result |
|---|---|---|
| 1 | JSON parses via production `_extract_json` (4 strategies) | ✅ both 23/23 — zero parse failures |
| 2 | No thinking-block contamination in `content` | ✅ both 0 — verified *properly* (see correction below) |
| 3 | Structural validity | glm 23/23 clean · m3 18/23 (topic-name mismatches only) |
| 4 | Latency | Comparable medians; m3 actually has a **lower max** (35s vs glm's 67s) |

**Correction worth recording:** my first-pass contamination check
(`"thinking" in content`) flagged 8 videos per model — **all false
positives**. The word "thinking" legitimately appears in the AI-Fluency
course transcripts' summaries. Re-verified: every response from both models
starts with clean JSON; Ollama routes thinking to the separate `thinking`
field for both. **Surprise finding: glm-5.2:cloud ALSO emits a thinking
field** (up to ~7.6K chars) — so both are thinking models under Ollama, and
m3's thinking is actually *shorter* on most videos. The original .env caveat
("glm validated, m3 risky because thinking") turns out to be half-true: the
parser risk is nil for both; the differences are elsewhere.

## Follow-up analysis (2026-09-10): is m3's density hallucination? No — measured.

User question: *"m3 builds denser mindmaps but hit 18/23 topic discipline — are
the dense nodes hallucinated, or something not in the transcript?"*

Measured against the actual transcripts (node-name word-overlap ≥50% = grounded):

| Check | Result |
|---|---|
| **Grounding, all 23 videos** (median) | glm **0.87** · m3 **0.86** — statistically identical |
| **Grounding, the 5 mismatched videos only** (m3) | **0.93** — the mismatched videos' nodes are MORE grounded than average |
| **Node count: mismatched vs clean m3 videos** | median 36 vs 38 — **density does NOT correlate with mismatch at all** |

**Conclusion:** m3's dense nodes are **NOT hallucinations** — they're real
content from the transcripts (grounding equal to glm). And the topic mismatches
are **not a density problem** (mismatched videos aren't denser). The mismatch
is a **self-consistency quirk**: m3 writes the mindmap, then writes
`topic_timestamps`, and occasionally *paraphrases* the node name slightly
("…Workflow" suffix, capitalization, or references a section concept that
isn't a literal node). It's the same failure class a human editor makes when
cross-referencing their own outline.

**Severity in practice:** the app already has the **ancestor-walk fallback**
(MVP1 feature) — a mismatched topic's click walks up the mindmap tree to the
nearest ancestor WITH a timestamp, so navigation degrades gracefully (lands
nearby) rather than breaking. That's why 18/23 is "degraded" not "broken."

This also means the cheap fix (normalize topic↔mindmap matching in the
renderer, or snap-to-nearest-node post-gen) addresses the ONE real gap, and
benefits both models (glm's 23/23 today is one prompt-drift away from the
same quirk).

## Per-video results

Full machine scorecard: `/tmp/model-ab/RESULTS.md` · Raw + parsed outputs for
side-by-side reading: `/tmp/model-ab/glm-5.2:cloud/<video-id>.json` and
`/tmp/model-ab/minimax-m3:cloud/<video-id>.json`

| Video | glm | m3 | m3 issue (if any) |
|---|---|---|---|
| Top 7 ChatGPT Developer Hacks | ✅ | ✅ | — |
| Call with Hugh Purcell | ✅ | ✅ | — |
| What is Claude Code? | ✅ | ✅ | — |
| Installing Claude Code | ✅ | ✅ | — |
| How Claude Code Works | ✅ | ✅ | — |
| Your first Claude Code prompt | ✅ | ✅ | — |
| The CLAUDE.md file | ✅ | ✅ | — |
| Explore→Plan→Code→Commit | ✅ | ✅ | topic name mismatch (substring variant) |
| Context Management in Claude Code | ✅ | ✅ | — |
| MCP in Claude Code | ✅ | ✅ | — |
| Hooks in Claude Code | ✅ | ✅ | — |
| AI Fluency Trailer | ✅ | ✅ | — |
| Lesson 1 (Introduction) | ✅ | ✅ | topic not a mindmap node |
| Lesson 2A | ✅ | ✅ | — |
| Lesson 2B (4D Framework) | ✅ | ✅ | — |
| Lesson 3A | ✅ | ✅ | — |
| Lesson 3B | ✅ | ✅ | — |
| Lesson 4 (Delegation) | ✅ | ✅ | topic not a mindmap node |
| Lesson 6 (Description) | ✅ | ✅ | case-variant mismatch |
| Lesson 7 (Prompting) | ✅ | ✅ | — |
| Lesson 8 (Discernment) | ✅ | ✅ | — |
| Lesson 10 (Diligence) | ✅ | ✅ | substring-variant mismatch |
| Lesson 11 (Conclusion) | ✅ | ✅ | — |

## How to review the diffs (your step)

Open the same video's two JSON files side by side — recommended start set:

```bash
open "/tmp/model-ab/glm-5.2:cloud/17ad9e5b-8d98-430d-8b75-63a4f361ec1f.json"   # What is Claude Code?
open "/tmp/model-ab/minimax-m3:cloud/17ad9e5b-8d98-430d-8b75-63a4f361ec1f.json"
```

Rate each dimension 1–5: **summary** accuracy, **mindmap** shape (note the
19-vs-52 node density difference — do you find 52 nodes richer or cluttered?),
**flashcards** usefulness, **quiz** correctness, and read a few
**topic_timestamps** against the transcript.

**The bar for switching:** m3 already never fails a hard gate; it needs the
topic-mismatch rate at zero AND your judgment that its mindmaps/quizzes are
as good or better on average.

## Path to a switch (if you want it, post-soft-launch)

1. **Fix the topic-mismatch class cheaply:** normalize topic↔mindmap matching
   (case-insensitive + substring match in the page renderer, or a
   post-generation validation pass that snaps topic names to the nearest
   mindmap node). ~1 focused hour; benefits BOTH models (glm 23/23 today,
   but the fix removes the whole fragility class).
2. **Decide on mindmap density:** if 52 nodes feels cluttered, add a prompt
   rule ("max 20 mindmap nodes") — a one-line prompt change, testable with
   `--models minimax-m3:cloud --limit 3`.
3. **Re-run this A/B** after the fixes (`rm -rf /tmp/model-ab && venv/bin/
   python scripts/ab_test_materials.py`).
4. **If clean → switch:** one-line `.env` change + `bash scripts/restart.sh`
   + spot-check one regenerated video end-to-end. Never mid-launch.
5. **Chat is separate:** the Discuss tab shares `OLLAMA_MODEL` — latency
   there was comparable in this test, but judge chat feel separately before
   switching (the chains support per-path model choice later if needed).

## Cost context (fill from the Ollama Pro plan page)

| Model | Price / bucket | Separate rate-limit bucket? |
|---|---|---|
| glm-5.2:cloud | _(fill)_ | _(fill)_ |
| minimax-m3:cloud | _(fill)_ | _(fill)_ |

A separate bucket = effectively extra launch capacity, which may be worth
more than the per-call price delta.

## Reproducing the run

```bash
venv/bin/python scripts/ab_test_materials.py            # cached: instant
venv/bin/python scripts/ab_test_materials.py --limit 3   # quick run
venv/bin/python scripts/ab_test_materials.py --models minimax-m3:cloud
```

Idempotent: completed videos are cached in `/tmp/model-ab/`; delete the
directory to force a full re-run (~45–60 min, sequential LLM calls).