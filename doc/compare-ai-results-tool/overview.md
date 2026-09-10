# Compare-AI-Results-Tool — Overview

> **Branch**: `compare-ai-results-tool` (based on `main` @ `aaf498f`)
> **Created**: 2026-09-10 · **Status**: planning (this doc set is the spec)
> **Tagline**: *Compare any two AI models on the same video, across every
> dimension that matters — so we always serve the best, most confident model
> per scenario.*

---

## 1. Why this tool exists

The 2026-09-09 glm-5.2:cloud vs minimax-m3:cloud A/B (23 real videos,
shadow-run, full write-up in
[`decision-log-and-lessons.md`](decision-log-and-lessons.md)) proved that
model choice is a **measurable engineering question**, not a vibe:

- Both models parsed 23/23 (thinking-model fear: unfounded)
- Content quality was nearly identical (flashcards verbatim-close)
- The ONE real gap — topic↔mindmap name discipline, 18/23 vs 23/23 — only
  surfaced because we had per-video structured validation
- The gap's *root cause* (self-paraphrase, not hallucination — the mismatched
  videos were the MOST transcript-grounded at 0.93) only surfaced because we
  had grounding analysis

**The insight:** every material our product ships (summary, mindmap,
flashcards, quiz, topic timestamps, chat) is a claim about model quality.
Manual comparison doesn't scale; a **standing comparison tool** turns every
model decision — switch, upgrade, A/B, regression — into a repeatable
measurement. This is product infrastructure, not a one-off experiment.

**Strategic framing:** the app's core promise is "AI turns videos into
learning materials." A tool that *proves* which model does that best, per
scenario, is a durable competitive asset:

1. It de-risks every future model switch (new Ollama/OpenAI/Groq models
   arrive constantly)
2. It gives us a **quality story for marketing** ("we benchmark every model
   on every dimension before we ship it to you")
3. It compounds: every comparison stored becomes a historical dataset of
   model behavior over time (models get updated silently by providers —
   regression detection)

---

## 2. The four architecture principles (settled 2026-09-10)

These came out of the user's requirements and are non-negotiable:

### P1 — Based on `main`, not mvp2-production-patches

**Why:** mvp2-production-patches is the *go-live* branch (launch 2026-09-15);
`main` is the product's long-term trunk (MVP3+). A long-lived tool must not
inherit go-live-only middleware, patches, or features that may be reverted
or restructured after launch. Also: main is *simpler* (no LiteLLM provider
layer — `app/services/llm_providers.py` doesn't exist here), which forces
cleaner independence.

**Consequence:** this tool talks to **Ollama's HTTP API directly**
(`/api/chat`), re-implementing only what it needs (prompt building, JSON
extraction) rather than importing any branch's service layer.

### P2 — Self-contained: grows by itself over years

No imports from app branches' feature code. The tool's dependencies:
- **Ollama HTTP API** (models, chat)
- **A transcript source** (see P4)
- **Standard library + pytest**

When MVP3 changes materials generation, this tool doesn't notice. When the
tool adds new dimensions, no branch notices.

### P3 — Mergeable / usable by any branch

Two integration shapes, both supported:

**a) Merged** — the tool ships inside the app (`app/services/comparison/` +
`app/routers/comparison.py` + `/admin/model-compare` UI page). Branches
merge it like any other feature; the module has no outward dependencies, so
merge conflicts are structurally impossible except in the deliberate
integration points (router registration, admin nav link).

**b) Borrowed without merging** — the core engine is a **pure function
library** (`compare(engine_params) → results`) + CLI. Any branch (or even a
laptop without the app) runs it against any models, reading transcripts
from wherever. The structured input contract (P4) is what makes this work.

### P4 — Structured input contract: the branch firewall

The tool NEVER depends on any branch's schema/tables/feature output. Callers
pass a **versioned, JSON-serializable input contract**:

```jsonc
// comparison_input_v1.json
{
  "schema": "compare-ai-results/1",
  "transcripts": [
    {
      "video_id": "5cb59f85-...",
      "title": "What is Claude Code?",
      "language": "en",
      "duration_seconds": 172,
      "segments": [
        {"start": 0.0, "end": 5.2, "text": "..."},
        {"start": 5.2, "end": 11.0, "text": "..."}
      ]
    }
  ],
  "models": ["glm-5.2:cloud", "minimax-m3:cloud"],
  "dimensions": ["all"],          // or subset: ["parse", "topic_match", ...]
  "tasks": ["materials"],          // MVP1: ["materials"]; later: ["chat"], ["summarize"]
  "output_dir": "..."
}
```

**Why `schema: "compare-ai-results/1"`:** version the contract explicitly.
When this tool adds dimensions (MVP2) or tasks (chat), old inputs still
parse (v1 dims degrade gracefully) and new inputs are explicit. Callers on
any branch can be sure their export keeps working as the tool evolves.

**Key property:** transcripts in = results out. No DB reads inside the
comparison core. A branch that wants to use the tool *exports* its videos
to this format (a small adapter script per branch — the only per-branch
code), runs the tool, reads results.

---

## 3. MVP1 scope (this branch's first shippable)

| Component | What it does |
|---|---|
| **Core engine** (`compare-ai-results/`) | Pure-function comparison: takes `comparison_input_v1`, runs each model on each transcript, validates + scores every dimension |
| **Dimensions** | The full set validated by the 2026-09-09 A/B: parse success, structure (flashcards/quiz shape), topic↔mindmap match, transcript grounding, latency, content metrics (counts, density) |
| **Adapter: app export** | One script (`scripts/export_for_comparison.py` on the *calling branch*) that dumps any branch's videos to `comparison_input_v1`. Ship one on this branch reading from main's schema as the reference |
| **CLI** | `python -m compare_ai_results run comparison_input.json` + `report results/` |
| **Output** | Per-video JSON + `RESULTS.md` scorecard + `report.html` side-by-side diff view |
| **Tests** | Engine unit tests with mocked LLM responses (no network in CI) |

**Explicitly OUT of MVP1:** multi-video *batch* UI (CLI covers batch via
input list), charts, 3+ models, chat task, fine-tune advice. Those are MVP2+
(see [`roadmap.md`](roadmap.md)).

See [`mvp1-spec.md`](mvp1-spec.md) for the full component-level spec.

---

## 4. Relationship to the branches

```
main ──────────────────────────────────────►  (product trunk, MVP3+)
  │
  ├─ mvp2-production-patches ──►  (go-live 9/15; may be merged INTO main post-launch)
  │        │
  │        └── borrows this tool via adapter + CLI (no merge required)
  │
  └─ compare-ai-results-tool ──►  (THIS branch; merges into main whenever ready)
           └── self-contained: no branch imports, only the input contract
```

**Post-launch plan:** mvp2-production-patches gets merged to main after the
campaign (normal flow). This branch merges to main independently, whenever
MVP1 is done — they touch different files by design.

---

## 5. Open questions (to settle before coding)

1. **HTML report scope for MVP1** — side-by-side JSON diff is the minimum;
   rendered-materials preview (actual mindmap/markdown rendering per model)
   is high-value but adds scope. Lean: do rendered preview in MVP1 only for
   mindmap + summary (the two users judge visually).
2. **Score aggregation** — per-video 5-point human scores stay manual (good);
   should the tool also emit a composite machine score (parse rate,
   match rate, grounding median) for at-a-glance comparison? Lean: yes —
   but clearly labeled as machine metrics, never a replacement for human
   judgment.
3. **Where results are stored** — output dir per run (timestamped), or a
   persistent `comparisons/` history in-repo (valuable for regression
   detection across months)? Lean: timestamped dirs + a tiny index file.
4. **Model availability checks** — tool should fail fast with a clear error
   when a requested model isn't in `ollama list`, before burning transcript
   time.

---

## 6. Document set

| Doc | Purpose |
|---|---|
| [`overview.md`](overview.md) | This file — vision, principles, branch strategy |
| [`decision-log-and-lessons.md`](decision-log-and-lessons.md) | The full 2026-09-09 A/B story + what it taught us (why each dimension exists) |
| [`mvp1-spec.md`](mvp1-spec.md) | Component-level MVP1 spec: engine, dimensions, CLI, outputs, tests |
| [`input-contract-v1.md`](input-contract-v1.md) | The versioned input schema + per-branch adapter rules |
| [`roadmap.md`](roadmap.md) | MVP2+: batch UI, charts, multi-model, fine-tune advice, regression detection |