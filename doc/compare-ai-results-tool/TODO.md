# TODO — Compare-AI-Results-Tool

> **Branch**: `compare-ai-results-tool` (based on `main` @ `aaf498f`)
> **Status**: SPEC COMPLETE, NO CODE YET (as of 2026-09-11)
> **Paused**: 2026-09-11 — user switching back to `mvp2-production-patches`
> for the 9/15 go-live. **Resume this when the product is truly live.**
>
> This file is the cold-restart guide: read it after weeks away and be
> productive in 10 minutes without re-reading everything.

---

## When you come back — start here

1. Read this file top to bottom (5 min)
2. Skim [`Readme.md`](Readme.md) → [`decision-log-and-lessons.md`](decision-log-and-lessons.md) (the "why")
3. Open [`mvp1-spec.md`](mvp1-spec.md) and build in order (§ Roadmap below)
4. **Acceptance test** is defined and ready: the 23-video A/B inputs +
   known findings (§ Validation assets) — the tool must reproduce them

## Why this branch is paused (context, don't re-litigate)

- 9/15 go-live takes all attention until launch + early campaign
- glm-5.2 vs m3 verdict already recorded: **no switch for launch**; m3 is a
  viable week-2/3 candidate after the topic-matching fix (full analysis on
  `mvp2-production-patches`: `doc/model-ab-glm52-vs-minimax-m3.md`)
- This tool's MVP1 has no launch dependency — nothing on the go-live
  branch imports it or needs it

---

## ✅ DONE (committed @ `4abdc01` on this branch)

| What | Where |
|---|---|
| Vision + 4 architecture principles (base-on-main, self-contained, mergeable, input contract) | [`overview.md`](overview.md) |
| Full A/B story + 5 methodological lessons + 8 carried-forward decisions | [`decision-log-and-lessons.md`](decision-log-and-lessons.md) |
| Input contract v1 (`compare-ai-results/1`) + per-branch adapter rules + versioning policy | [`input-contract-v1.md`](input-contract-v1.md) |
| MVP1 component-level spec (engine, dimensions, CLI, outputs, tests) | [`mvp1-spec.md`](mvp1-spec.md) |
| Multi-year roadmap (MVP2 batch+charts → MVP3 N-model+chat → MVP4 advising+routing) + non-goals | [`roadmap.md`](roadmap.md) |

## ⬜ NEXT — build order (MVP1)

Pick up at step 1. Roughly a focused 2–3 days total for steps 1–6.

| # | Task | Notes |
|---|---|---|
| 1 | **Scaffold package** — `compare_ai_results/` top-level package per mvp1-spec §1 (NOT under `app/`) + `tests/` layout | P2 self-containment: zero `app/` imports |
| 2 | **`input_schema.py`** — validate `compare-ai-results/1`, fail-fast rules from contract §2 | Every rule in the contract is a test case in `test_input_schema.py` |
| 3 | **`ollama_client.py` + `prompt.py` + `extract.py`** — thin HTTP wrapper; pinned prompt copy; 4-strategy JSON extraction (port from the A/B script's proven parser) | Fail-fast model availability check (contract validation rule 2) |
| 4 | **Dimension scorers** — the 7 from mvp1-spec §3: parse, structure, topic_match (exact + normalized), grounding, latency, content_metrics, thinking_contamination (**structural checks ONLY** — substring checks are the documented false-positive trap, Lesson 1) | Pure functions; golden fixtures = the 5 real m3 mismatch response classes (suffix/case/non-node) |
| 5 | **Engine + CLI** (`run`, `validate`, `report`, `models`) — orchestration per mvp1-spec §2; sequential MVP1; per-(transcript, model, prompt) caching | Exit codes: 0/2/3/4/1 per spec §5 |
| 6 | **`RESULTS.md` + `report.html`** — scorecard with slice tables + correlation notes; blind A/B view (Model A/B labels, reveal-after-scoring) | Rendered mindmap+summary preview if open question #1 resolved "lean: yes" |
| 7 | **Reference adapter** — `scripts/export_for_comparison.py` reading **main's** schema (this branch's base) | The worked example other branches copy |
| 8 | **Acceptance validation** — run the 23-video A/B inputs through the tool; must reproduce: parse 23/23 both · m3 topic exact-match 18/23 · grounding parity 0.87 vs 0.86 · latency medians ~22.6 vs ~24.4s | **The MVP1 done-bar** ("the tool rediscovers what we learned by hand") |
| 9 | **Cross-branch proof** — export from `mvp2-production-patches` via its own adapter (run there, write it there — 10 lines) and run THIS tool's CLI on the output. No merge. | Proves P4 end-to-end; closes "mergeable without merging" |
| 10 | **MVP1 wrap** — README update with real usage examples, commit, merge to `main` whenever ready (independent of go-live branch) | Then roadmap MVP2 is next |

## ⬜ OPEN QUESTIONS (settle before/while coding — from overview §5)

1. **Report scope**: rendered mindmap+summary preview in MVP1? (lean: yes —
   those are the two humans judge visually)
2. **Composite machine score**: emit? (lean: yes, clearly labeled
   machine-only)
3. **Results storage**: timestamped dirs + index (lean) vs in-repo history
4. **Model availability**: fail-fast (already decided YES — listed as a
   validation rule; nothing to settle)

## 🔗 Validation assets (what makes resuming easy)

- **Known-truth dataset**: the 23-video A/B inputs are reconstructable from
  mvp2-production-patches' DB (`videos`+`assets` WHERE `asset_type=
  'transcript'`), and the expected findings are recorded in
  [`decision-log-and-lessons.md`](decision-log-and-lessons.md) §1 — this is
  the acceptance test's ground truth
- **The original one-off runner still exists** on mvp2-production-patches
  (`scripts/ab_test_materials.py`) — useful for cross-checking the new
  engine's first runs
- **Ephemeral artifacts** (`/tmp/model-ab/`) may be gone after reboot — fine;
  everything durable is in the two branches' docs

## 🔀 Branch handoff notes (for going back to go-live)

- Your terminal + workspace currently sit on `compare-ai-results-tool`;
  `git checkout mvp2-production-patches` when ready
- This branch is 1 commit ahead of main (`4abdc01`, docs only) — zero risk
  of drift while parked; rebase only if main moves meaningfully before resume
- **Untracked files warning**: `aaf498f`'s gitignore change means new files
  in the worktree may show as untracked after switching — if `git status`
  looks odd on the patches branch, it's the parked docs are NOT there (they
  live only on this branch) — that's expected, not lost work
- The go-live branch's own remaining checklist lives in
  `doc/mvp2-production-patches-status.md` (Day 10 security hardening +
  the ops items: drives → Mac Studio, named Cloudflare tunnel,
  LaunchDaemon install, cookie `Secure`, trial-script)

## 💡 Parked ideas (don't start before MVP1 is done)

- Prompt A/B in one run (contract v2 feature — see roadmap MVP4)
- Parallel generation runs (MVP2 — but measure Ollama queueing effects on
  latency first; the roadmap warns about this)
- Fine-tune advising (needs MVP1's repeatable runs as its training data)