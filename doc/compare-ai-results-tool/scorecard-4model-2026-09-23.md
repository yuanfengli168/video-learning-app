# Model Scorecard — 4-Model Comparison on 50 Videos (2026-09-22/23)

> **Run:** `comparison-runs/2026-09-23T015607` · 50 YouTube-catalog videos ·
> prompt `65d4753dd576efa2` (bundled, pinned from mvp2-production-patches @
> bf97c71) · scored with the same engine/dimensions/parser as the 23-video
> A/B (which stays as the historical baseline: `mvp2-production-patches:doc/model-ab-glm52-vs-minimax-m3.md`).
>
> **Why this run exists:** PAID users were switched to minimax-m3 on
> 2026-09-22 for its 2x quota advantage; the 50-video expansion of the A/B
> showed its topic-click defect was worse than measured (86% exact vs glm's
> 99.7%). This run answers: **is there a model with glm-class quality at
> flash-class cost?**

## The verdict in one table

| Metric | glm-5.2:cloud (reference) | minimax-m3:cloud | **glm-5.3-flash:cloud** ⭐ | deepseek-v4.1-flash:cloud |
|---|---|---|---|---|
| Parse OK (hard gate) | 100% | 98% | **100%** | **100%** |
| Structure clean (hard gate) | 100% | 96% | **100%** | **100%** |
| **Topic↔Mindmap EXACT rate** | 99.7% (2 fail) | 86% (120 fail) | **100% (0 fail)** 🎉 | 99.9% (1 fail) |
| Normalized rate (fix ceiling) | 100% | 92% | 100% | 100% |
| Latency median | 36s | 15s | 63s | 31s |
| Topic entries (total) | 577 | 871 | 643 | 665 |
| Mindmap nodes (median) | 20 | 44 | 29 | 28.5 |
| Ungrounded nodes (total) | 88 | 182 | **66** (best) | 82 |
| Summary chars (median) | 1945 | 2569 | **2780** (best) | 2294 |
| Ollama price per generation* | ~$0.032 | ~$0.015 | **~$0.0035** | ~$0.0038 |
| $60 Pro credits ≈ | ~1,900 gens | ~4,000 | **~17,000** | ~15,800 |

*\*~15k input + 2.5k output tokens (materials generation). Chat skews even
cheaper for flash models (input-dominated). Prices from ollama.com/pricing,
2026-09-22.*

## Findings

1. **glm-5.3-flash:cloud is the winner — better than glm-5.2 on quality AND
   9x cheaper.** Zero topic mismatches (glm-5.2 itself had 2), best
   grounding, richest summaries, +12% topics vs glm-5.2. Same GLM family =
   the JSON discipline the production parser relies on.
2. **minimax-m3 is confirmed out as PAID default** — the 23-video A/B's
   "5 videos affected, one phrasing class, fixable by a normalizer" did not
   scale: 20/50 videos, 120 mismatches, and the failures are mostly topics
   with NO corresponding mindmap node (a normalizer cannot draw missing
   nodes; it resolves only 43%).
3. **deepseek-v4.1-flash is a strong runner-up** — 1 mismatch, 8x cheaper,
   2x faster than glm-5.3-flash. Keep in the catalog.
4. **The one trade: latency.** glm-5.3-flash is the slowest (63s median;
   thinking model + long transcripts). Materials generation is background
   work so this is tolerable; for interactive chat it may feel slow —
   a split-config (flash for materials, glm-5.2 or deepseek for chat) is a
   future option the model-preference resolver could support.
5. **Cost at beta scale:** ~3,000 PAID generations/month ≈ $96 on
   glm-5.2 vs ≈ **$10.50** on glm-5.3-flash — an 89% reduction, turning the
   $60 Pro credits into ~6x the monthly capacity.

## Recommended actions (owner decision pending)

- **PAID default → glm-5.3-flash:cloud** (one env line: `LLM_MODEL_PAID_DEFAULT=glm-5.3-flash:cloud` — the model-preference infra shipped 2026-09-22 makes this a config change, zero code).
- **Chat model:** keep glm-5.2 for interactive latency, or accept flash everywhere for max savings.
- **Catalog:** add glm-5.3-flash + deepseek-v4.1-flash to `LLM_MODEL_CATALOG` (admin-selectable); demote minimax-m3 to an option.
- **Normalizer click-fix (parked):** with 0 mismatches on the winner, the topic-name normalization work is no longer needed for PAID quality; revisit only if a catalog model needs it.

## Run notes (reproducibility)

- 200/200 generations complete (4 models × 50 videos); 2 transient cloud
  500s and 1 read-timeout hit during the run — the client now retries both
  (2x with backoff); the global raw-cache (also fixed during this run,
  per mvp1-spec's transcript-hash×model×prompt design) made the final
  resume cost only the 6 missing generations.
- Raw data: `comparison-runs/_cache/<video_id>/<model>/` (global, re-usable
  by future runs); this run's aggregated `metrics.json` +
  per-video files under `comparison-runs/2026-09-23T015607/`.
- Language distribution of the 50 videos: en ~47%, `zu` 25%, `en-j3PyPqV-e1s`
  11% (the two bad-tag groups are the known parser-tagging issue, content is
  English) — minimax's worst video (41/42 mismatches) was in the `zu` batch.