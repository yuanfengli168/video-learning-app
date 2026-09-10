# Input Contract v1 — `compare-ai-results/1`

> The boundary between this tool and every other branch. Callers export to
> this format; the engine accepts nothing else. Versioned so the tool can
> evolve without breaking any branch's adapter.

---

## 1. Why a contract (not shared code)

From the four architecture principles (see `overview.md` §2): the tool must
grow for years without merge-chasing any branch, and any branch must use it
without merging. The only stable interface that survives branch divergence
is **data**: a JSON file the caller builds from its own tables, and a
results directory the tool writes. No imports across branches, ever.

**Rule of thumb:** if a caller needs to read this tool's Python, the
integration is wrong. They need this document.

---

## 2. The schema

```jsonc
{
  // REQUIRED. Bump on breaking changes. The engine refuses unknown
  // schemas with a clear error naming the newest supported version.
  "schema": "compare-ai-results/1",

  // REQUIRED, ≥ 1. Videos to compare on. Batch = just more entries.
  "transcripts": [
    {
      // REQUIRED. Stable identifier (the caller's video id). Used in
      // filenames + report joins. Any string; uniqueness enforced.
      "video_id": "5cb59f85-5bf6-4884-b5f0-b119aeffa8c2",

      // REQUIRED. Display title for the report.
      "title": "What is Claude Code?",

      // OPTIONAL. BCP-47 or whisper code. Lets the engine slice results
      // by language ("is m3 better on Chinese transcripts?").
      "language": "en",

      // OPTIONAL. For latency-vs-length analysis + report badges.
      "duration_seconds": 172,

      // OPTIONAL. Caller's pipeline info for context columns only.
      // The engine never branches on it.
      "source": "youtube_captions",       // or "whisper", "manual", ...
      "caption_quality": "auto",          // "auto" | "manual" | unknown

      // REQUIRED. The transcript itself — the ONLY content the models see.
      "segments": [
        {"start": 0.0, "end": 5.2, "text": "Welcome to this tutorial."},
        {"start": 5.2, "end": 11.0, "text": "Today we'll cover the workflow."}
      ]
    }
  ],

  // REQUIRED, ≥ 2 (MVP1). Model names as they appear in `ollama list`.
  // Engine fails fast (before any LLM call) on unavailable models.
  "models": ["glm-5.2:cloud", "minimax-m3:cloud"],

  // OPTIONAL, default ["all"]. Dimension keys to run (see mvp1-spec §3).
  // Unknown keys → 400-style error listing valid keys.
  "dimensions": ["all"],

  // OPTIONAL, default ["materials"]. Task types to run. MVP1: ["materials"]
  // only. Later contract versions add "chat", "summarize", etc.
  "tasks": ["materials"],

  // OPTIONAL. System prompt override. When ABSENT, the engine uses its
  // bundled materials-generation prompt (kept in lockstep with the
  // production prompt by manual review — see §4 caveat).
  "system_prompt": null,

  // OPTIONAL. Where results go. Default: ./comparison-runs/<timestamp>/.
  "output_dir": null,

  // OPTIONAL. Per-model overrides the engine should honor (e.g. the
  // reference model you're A/B-ing against a candidate).
  "labels": {"glm-5.2:cloud": "reference", "minimax-m3:cloud": "candidate"}
}
```

### Validation rules (engine, fail-fast)

1. Unknown `schema` → error naming supported versions. **Never** silently
   guess.
2. `models` entries checked against `ollama list` BEFORE any transcript is
   sent — fail with the missing names + how to `ollama pull` them.
3. Every transcript: `video_id` unique + non-empty; `segments` non-empty.
4. `dimensions`/`tasks` keys validated against the supported set; typos
   (e.g. `"topic_match "` with a trailing space) → error, not ignore.

---

## 3. Adapter rules (per calling branch)

An adapter is a **small script that lives on the calling branch** (not this
one) and dumps that branch's videos to this contract. That's the ONLY
per-branch code the integration ever needs.

**Reference adapter** (this branch ships one): `scripts/export_for_comparison.py`
reading main's schema. It exists as the worked example + dogfood path.

**What an adapter must guarantee:**
- Real `segments` (start/end/text) — never placeholder text, or latency +
  grounding measurements are meaningless
- Honest `language` + `caption_quality` when known — they power the slicing
  that found the m3 mismatch mechanism
- No PII beyond the video's own content (transcripts are the video's speech)

**What an adapter must NOT do:**
- Import anything from this tool's package (stay behind the contract)
- Send anything other than the video's own transcript as content

**Typical usage from another branch:**

```bash
# on the calling branch
python scripts/export_for_comparison.py --video-ids ... -o input.json
# anywhere (this branch checked out, or the tool installed)
python -m compare_ai_results run input.json
# report
python -m compare_ai_results report comparison-runs/<timestamp>/
```

---

## 4. The production-prompt caveat (known limitation)

When `system_prompt` is absent, the engine uses its **bundled** copy of the
materials-generation prompt. The 2026-09-09 A/B taught us the comparison is
only meaningful against the **exact production prompt** — models differ
meaningfully on prompt sensitivity.

**Contract v1 rule:** the bundled prompt is pinned in this repo; when a
caller's production prompt differs from ours, the caller **passes
`system_prompt` explicitly**. The engine records WHICH prompt ran (bundled
version hash or `override`) in the results index, so months later you can
tell whether a historical comparison used the same prompt as production.

**Future (contract v2):** `prompt_profile: "materials-v1"` named profiles
so callers reference shared prompt versions by name instead of pasting
bodies.

---

## 5. Results contract (what the engine writes)

```
comparison-runs/2026-09-10T142930/
  ├── run_index.json      # inputs hash, models, prompt-id, dimensions, totals
  ├── inputs.json         # the exact input that ran (reproducibility)
  ├── per-video/
  │   ├── <video_id>/<model>/raw.txt        # verbatim model response
  │   ├── <video_id>/<model>/parsed.json    # extracted materials
  │   ├── <video_id>/<model>/metrics.json   # per-dimension scores
  │   └── <video_id>/comparison.json        # side-by-side diff summary
  ├── RESULTS.md           # human scorecard (like the A/B's)
  └── report.html          # side-by-side review UI (anonymous A/B labels)
```

`report.html` presents materials labeled **Model A / Model B** (mapped to
real names in `run_index.json`, revealed after scoring) — the blind-review
mechanism from Lesson 5 of the decision log.

---

## 6. Versioning policy

- **Patch** (v1.x): new dimensions, new report features — old inputs fine.
- **Minor** (contract additions): optional new keys — old inputs fine,
  engine warns on unknown keys? No: engine IGNORES unknown keys silently
  only within the same major; documents them in `run_index.json`.
- **Major** (v2, v3): breaking changes (new required keys, segment shape
  changes, new tasks). Old adapters keep working because they pin
  `"schema": "compare-ai-results/1"` and the engine keeps v1 support
  indefinitely (v1 is ~20 lines of normalization away from v2 at worst).