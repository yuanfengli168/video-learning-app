# Compare-AI-Results-Tool

**Branch:** `compare-ai-results-tool` (based on `main` @ `aaf498f`) · **Status:** planning/spec phase

Compare any two (later: N) AI models on the same video across every
dimension that matters — so we always serve the best, most confident model
per scenario.

Born from the 2026-09-09 glm-5.2:cloud vs minimax-m3:cloud shadow A/B on
23 real videos, where structured measurement settled questions vibes
couldn't: thinking-model fear (unfounded), hallucination suspicion
(disproven — the dense nodes were the MOST transcript-grounded), and a real
quality gap (topic-name self-consistency, 18/23 vs 23/23) invisible to
parse + content checks alone.

## Read in this order

1. **[overview.md](overview.md)** — vision, the four architecture
   principles (base-on-main, self-contained, mergeable, structured input
   contract), branch strategy
2. **[decision-log-and-lessons.md](decision-log-and-lessons.md)** — the full
   A/B story + the methodological lessons that defined every dimension
3. **[input-contract-v1.md](input-contract-v1.md)** — the versioned schema
   (`compare-ai-results/1`) + per-branch adapter rules: the branch firewall
4. **[mvp1-spec.md](mvp1-spec.md)** — component-level spec: engine,
   dimensions, CLI, outputs, tests. Acceptance test: *the tool reproduces
   the A/B's findings on the same inputs*
5. **[roadmap.md](roadmap.md)** — MVP2 batch UI + radar charts → MVP3
   multi-model + chat task → MVP4 fine-tune advising + confidence routing

## The four principles

| # | Principle | One-liner |
|---|---|---|
| P1 | Based on `main` | Outlives the go-live branch; MVP3+ is the trunk |
| P2 | Self-contained | Zero imports from app branches; grows alone for years |
| P3 | Mergeable | Copy the package into any branch, or just call the CLI |
| P4 | Structured input contract | Transcripts-in (versioned JSON) → results-out; no schema coupling |

## MVP1 at a glance

`python -m compare_ai_results run input.json` → per-video raw + parsed +
metrics, RESULTS.md scorecard with slice tables, and a blind A/B
report.html for human judgment. Dimensions: parse, structure, topic-match,
grounding, latency, content metrics, thinking-contamination (structural
checks only — substring checks burned us once).

## What this is never

- Not auto-switching (humans decide)
- Not live-traffic replay (exported inputs only)
- Not a model opinion (environment noted, interpretation left to readers)