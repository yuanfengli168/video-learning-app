# Roadmap — Compare-AI-Results-Tool

> MVP1 ships the engine + CLI + reports (see `mvp1-spec.md`). This file is
> the multi-year growth plan the architecture must survive — every item
> below is why the input contract is versioned and the package is
> branch-independent.

---

## MVP2 — Batch workflows + visual analytics

*Target: the first months of the soft launch, when model data starts
compounding.*

| Feature | What it adds | Why it's this phase |
|---|---|---|
| **Batch run UI** | Web page (in-branch or standalone) over runs of 10–100 videos: progress, per-video drill-down, queue management | Batch is already possible via CLI input; the value add is ergonomics once comparisons are weekly, not monthly |
| **Strength/weakness radar chart** | Per-model radar across dimensions (parse, structure, topic match, grounding, latency, density) — the at-a-glance "which model for which scenario" visual | Needs the MVP1 dimension set exercised on enough videos to be meaningful |
| **Scenario recommendation table** | Machine-ranked "best model per scenario": short vs long transcripts, manual vs auto captions, per language | Direct productization of slicing; only meaningful with slice-level sample sizes |
| **Historical run index** | Cross-run comparison page: same models re-run monthly → regression detection (providers silently update models) | The compounding value — every stored run makes the next comparison cheaper |

## MVP3 — Multi-model (2 → 3 → 4+) and new tasks

*Target: as the product's model surface grows.*

| Feature | What it adds |
|---|---|
| **N-model comparison** | Contract `models` already takes N entries; report + charts generalize pairwise → matrix. Radar overlay of 4 models is the signature view |
| **Chat task type** | Contract v2 `"tasks": ["chat"]` — scripted Q&A scenarios (from transcripts) scored on latency + grounding + human rubric. Solves the "m3 for materials, but chat?" open question from the A/B |
| **Summarize/other tasks** | Same pattern per product surface; the dimension framework is task-agnostic by construction |
| **Provider breadth** | Ollama-only in MVP1; extend `ollama_client` → `provider_client` registry (OpenAI, Groq, LiteLLM passthrough) so the tool compares ACROSS providers, not just within Ollama |

## MVP4 — Fine-tune advising + product integration

*Target: post-launch, when quality data + user feedback both exist.*

| Feature | What it adds |
|---|---|
| **Fine-tune advising** | Pattern analysis across runs: which failure classes repeat (e.g. m3's topic paraphrase) → concrete prompt-fix recommendations (the "add a max-20-nodes rule" and "normalize topic names" advice, generated instead of hand-derived) |
| **Prompt A/B** | Contract v2: multiple `system_prompt` variants in ONE run — comparing prompts with the same rigor as models. The generation prompt itself becomes a tunable |
| **In-app comparison page** | `/admin/model-compare` on product branches (P3 merge shape): pick 2 models, see live radar + last-run deltas. The marketing story: "every model we ship is benchmarked on every dimension" |
| **Confidence routing** | The endgame: per-scenario model selection wired into the product's provider chain (e.g. m3 for long-transcript materials, glm for chat) — this tool's outputs become runtime routing data, versioned like config |

---

## Standing principles for all phases

1. **The contract is the constitution** — every growth feature must work
   through versioned inputs; v1 adapters must never break
2. **Human judgment is never automated away** — machine scores rank,
   humans decide (D7 in the decision log)
3. **Every new dimension needs a real finding to justify it** — the MVP1
   set exists because each one caught something real; resist
   dimension-metrics theater
4. **No branch lock-in** — if a feature requires importing product code,
   it goes behind the contract instead

---

## Dependencies between phases

```
MVP1 (engine+CLI+reports)
   └── MVP2 batch UI needs MVP1's run format stable
        └── MVP3 N-model needs MVP2's radar/slicing
             └── MVP4 routing needs MVP3's multi-provider + scenario tables
```

Each phase is independently shippable; nothing requires the next.

---

## Explicit non-goals (ever)

- **No automatic model switching** — the tool informs, humans decide (D7)
- **No production traffic replay** — comparisons run on exported inputs;
  live-traffic shadowing is a product feature, not a comparison tool
- **No model-hosting opinions** — the tool doesn't care if a model is
  local or cloud; latency measurements note the environment and leave
  interpretation to the reader