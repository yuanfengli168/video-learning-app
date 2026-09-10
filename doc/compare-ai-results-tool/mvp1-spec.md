# MVP1 Spec — Compare-AI-Results-Tool

> Scope: **one video → N models → full dimension report**, repeatable via
> CLI on batches (a batch is just an input with many transcripts). This is
> the spec for the first shippable version on `compare-ai-results-tool`.

---

## 1. Package layout (self-contained, per P1/P2)

```
compare_ai_results/            # top-level Python package (NOT under app/)
  __init__.py
  __main__.py                  # CLI entry: python -m compare_ai_results
  cli.py                        # run / report / validate subcommands
  input_schema.py              # compare-ai-results/1 validation (fail-fast)
  engine.py                    # orchestrates: validate → probe models → run → score
  ollama_client.py             # thin HTTP wrapper (no branch deps)
  prompt.py                    # bundled materials prompt (pinned copy)
  extract.py                   # JSON extraction (production-parser port)
  dimensions/
    __init__.py                # registry: key → scorer
    parse_success.py           # did it parse via extract.py?
    structure.py               # keys, flashcard/quiz shape, answer_index bounds
    topic_match.py             # topic names vs mindmap nodes (exact + normalized)
    grounding.py               # node words vs transcript (≥50% overlap)
    latency.py                 # wall-clock per call
    content_metrics.py         # counts, densities, summary length
  report.py                    # RESULTS.md + report.html generation
  anonymize.py                 # A/B blind-labeling for report.html
tests/
  test_input_schema.py
  test_engine_mocked.py        # no network: mocked ollama responses
  test_dimensions.py           # golden cases per dimension (incl. the 5 m3
                               # mismatch examples as fixtures)
  test_extract.py
  test_report.py
scripts/
  export_for_comparison.py    # reference adapter (main's schema → contract)
```

**Why top-level, not `app/services/comparison/`:** P2 (self-contained) —
`app/` on main will evolve with MVP3; a top-level package with zero app
imports can be vendored into ANY branch later (P3 merge shape) by copying
the directory + adding one router shim. The `app/` integration is a thin
adapter living wherever it's merged.

---

## 2. Engine flow

```
validate input (schema v1, fail-fast)
  → probe models (ollama list; missing → hard error BEFORE any LLM spend)
  → for each transcript × model × task:
        build prompt (bundled or override)
        call ollama (timeout 300s, no retry in MVP1 — count as failure)
        extract (production 4-strategy parser port)
        score dimensions (each dimension scorer pure-function)
  → aggregate per-model / per-slice (language, caption_quality, length bucket)
  → write outputs (per input-contract §5)
  → emit RESULTS.md + report.html
```

**Concurrency (MVP1):** sequential, like the A/B. The 23×2 run took ~50 min;
fine for offline tooling. Parallelism is an MVP2 optimization with its own
measurement (Ollama queueing affects latency readings — must be measured,
not assumed).

**Caching:** per (transcript hash, model, prompt hash) — re-runs are free,
like the A/B script's. Cache lives under the run's `output_dir`.

---

## 3. Dimensions (the validated set — from the decision log)

| Key | Type | Catches | MVP1 rule |
|---|---|---|---|
| `parse_success` | hard gate | Dead outputs | `extract()` returns dict or not |
| `structure` | hard gate | Malformed shapes | required keys; flashcards ≥3; quiz ≥2 with 4 options + `answer_index` ∈ 0–3 |
| `topic_match` | quality | Self-consistency (Lesson 2) | exact match rate + normalized match rate (case-insensitive + substring) — BOTH reported; exact is the headline, normalized shows the fix margin |
| `grounding` | quality | Hallucination (Lesson 3) | per-node ≥50% word overlap vs transcript; report median + list of ungrounded nodes |
| `latency` | efficiency | UX budget (Lesson 4) | wall-clock; report median/min/max per model; **never** auto-fail (budgets differ by task) |
| `content_metrics` | style | Density/counts (human review flags) | flashcard/quiz/topic counts, mindmap node count, summary length — no pass/fail |
| `thinking_contamination` | hard gate | Lesson 1 | **structural check only**: response starts with thinking markers (`<think`, `thinking:`, ```` ```thinking ````) before JSON — substring checks forbidden |

**Hard gates** = a model failing one is disqualified regardless of other
scores. **Quality** = reported, sliced, ranked — human decides. **Style**
= context for human review.

**Explicitly manual (always):** summary accuracy, phrasing quality,
mindmap taste. The report's blind A/B view exists for exactly this.

---

## 4. Outputs

### RESULTS.md (machine scorecard)

Modeled on the A/B scorecard, plus:

- **Slice tables** (Lesson 3's mechanism came from slicing): per-language,
  per-caption-quality, per-length-bucket breakdowns
- **Correlation notes**: density vs topic_match failures, grounding vs
  mismatch — precomputed so reviewers see the same insights the A/B needed
  hand-analysis for
- **Composite machine score** (clearly labeled machine-only):
  parse rate, structure rate, topic exact-match rate, grounding median —
  presented as a table, never a single number

### report.html (the human judgment surface)

- Video picker → per-video side-by-side: **Model A / Model B** (blind —
  real names in run_index.json, revealed via a "reveal" toggle AFTER
  scoring)
- Rendered summary + rendered mindmap (Markmap CDN, same as the app) —
  the two materials humans judge visually (per overview open question #1)
- Raw JSON diff for flashcards/quiz/topics
- 1–5 rubric inputs per dimension per model, saved to
  `human_scores.json` (schema in run_index)

---

## 5. CLI

```bash
# Validate an input file without running (adapter development aid)
python -m compare_ai_results validate input.json

# Run a comparison
python -m compare_ai_results run input.json

# Regenerate reports from an existing run (e.g. after adding human scores)
python -m compare_ai_results report comparison-runs/2026-09-10T142930/

# Convenience: which models are available right now
python -m compare_ai_results models
```

Exit codes: 0 ok · 2 input invalid (with per-field errors) · 3 model
unavailable (names listed) · 4 all generations failed (per-model detail) ·
1 unexpected.

---

## 6. Tests (no network in CI)

- **Golden fixtures from the real A/B:** the 5 m3 mismatch responses
  (captured, committed) become `test_topic_match` fixtures — the exact
  near-miss classes (suffix, case, non-node) are regression-tested forever
- Mocked `ollama_client` for engine tests; dimension scorers tested as
  pure functions with hand-built materials
- `test_input_schema`: every validation rule incl. unknown-schema error
  naming supported versions

---

## 7. What "done" means for MVP1

1. `validate` + `run` + `report` work end-to-end on the reference input
   (main's videos exported by the reference adapter)
2. Re-running the 2026-09-09 A/B through the tool reproduces its findings
   (23/23 parse both, 18/23 m3 topic exact-match, grounding parity) —
   **the acceptance test is "the tool rediscovers what we learned by hand"**
3. All tests green with zero network access
4. A second branch (mvp2-production-patches) can run it via adapter + CLI
   with no merge — proven by doing exactly that once