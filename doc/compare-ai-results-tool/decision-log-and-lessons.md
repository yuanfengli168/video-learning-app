# Decision Log & Lessons — from the 2026-09-09 A/B to this tool

> This document preserves the full reasoning trail: why each comparison
> dimension exists, what surprised us, and the methodological lessons that
> are now baked into the tool's design.

---

## 1. The experiment that started this (2026-09-09 → 09-10)

**Question:** minimax-m3:cloud is cheaper than glm-5.2:cloud on Ollama Pro.
Can it produce equal-quality materials? If yes, switch and save money.

**Method:** offline shadow A/B on 23 real videos (every video with a
transcript at the time). Read transcripts from the DB (read-only), send the
**exact production prompt** (`GENERATION_SYSTEM_PROMPT` from
`app/services/llm.py` on mvp2-production-patches), parse with the **exact
production `_extract_json`** (4 strategies: direct, code-fence, preamble
strip, brace match), write outputs to `/tmp/model-ab/`. Zero writes to the
assets table; nothing appeared on any page.

**Results (23 videos × 2 models, ~50 min sequential run):**

| Metric | glm-5.2:cloud | minimax-m3:cloud |
|---|---|---|
| JSON parse (production parser) | 23/23 | 23/23 |
| Fully clean structure | 23/23 | 18/23 |
| Thinking-block contamination | 0 | 0 |
| Latency median (range) | 22.6s (12.8–66.8) | 24.4s (7.7–35.0) |
| Flashcards/video (median) | 8 | 9 |
| Quiz questions (median) | 5 | 5 |
| Topic timestamps (median) | 8 | 14 |
| Mindmap nodes (median) | ~17 | ~37 |

**Content spot-check (same video):** flashcard terms/definitions nearly
identical; quiz tested the same concept with the same correct answer;
summary lengths comparable (1782 vs 1979 chars).

**Verdict recorded:** glm-5.2 stays the launch model (2026-09-09 decision:
never swap close to launch). m3 is a viable week-2/3 switch candidate after
the topic-matching normalization fix.

---

## 2. Lesson 1 — the "thinking model" fear was wrong in an interesting way

**Original assumption** (recorded in `.env` caveat, 2026-09-09): *"minimax-m3
is a THINKING model — it emits a 'Thinking…' block before its answer, so it
risks JSON parse failures."*

**What the data showed:**
1. Zero parse failures for EITHER model across all 46 generations.
2. Ollama routes thinking to a separate `thinking` field for both.
3. **Surprise: glm-5.2 also emits thinking** — up to ~7.6K chars, often
   LONGER than m3's. The "glm validated / m3 risky" framing was half-true
   at best.

**Methodological lesson:** my first-pass contamination check was
`"thinking" in content.lower()` — it flagged 8 videos per model. **All 16
flags were false positives**: the word "thinking" appears legitimately in
the AI-Fluency course *content*. Re-verification (does the response START
with clean JSON?) showed zero real contamination.

**Tool design consequence:** the engine's contamination check must be
structural (starts-with markers), never substring matching. Substring
checks on generated content about content produce false positives.

---

## 3. Lesson 2 — the real gap was self-consistency, and only measurement found it

The only measurable quality difference: **topic↔mindmap name discipline**.
glm: 23/23 exact matches. m3: 18/23 — and all 5 failures were the same
class:
- `"Explore, Plan, Code, Commit Workflow"` vs node `"Explore, Plan, Code, Commit"` (suffix)
- `"AI is Not a Database or Vending Machine"` vs node `"AI is not a database or vending machine"` (case)
- `"Core Competencies Approach"` (references a concept that isn't a literal node)

**User impact when it happens:** the topic's mindmap click falls back to the
ancestor-walk (MVP1 feature) — lands *near* the right moment, never a dead
click. Degraded, not broken.

**Why this matters for the tool:** this failure class is invisible to
"does it parse?" and invisible to "does the content look good?" — it only
appears when you **cross-validate one part of the output against another
part of the same output**. That's now a first-class dimension
(`topic_match`), and it generalizes: self-consistency checks between output
components are a quality signal no single-component view catches.

---

## 4. Lesson 3 — the density question: hallucination was the wrong hypothesis

**User hypothesis (2026-09-10):** *"m3 makes denser mindmaps but mismatches
more — so are the dense nodes hallucinated / not in the transcript?"*

**Measured answer — NO:**

| Check | Result |
|---|---|
| Node grounding vs transcript (word-overlap ≥ 50%) | glm 0.87 · m3 0.86 — identical |
| Grounding of the 5 mismatched videos (m3) | **0.93** — the *most* grounded |
| Node count: mismatched (36) vs clean (38) | density does NOT correlate with mismatch |

**Mechanism:** m3 writes the mindmap, then writes `topic_timestamps`, and
occasionally **paraphrases its own node name** between the two steps — the
same error a human editor makes cross-referencing their own outline. The
videos it works hardest on (most grounded) are where it paraphrases.

**Tool design consequence:** `grounding` is a first-class dimension (each
node checked against its transcript source), and the tool must report
correlation breakdowns (density vs failure) rather than single-number
scores — because the first interesting finding came from slicing exactly
that way.

---

## 5. Lesson 4 — latency intuition from toy inputs is useless

**Toy-input probe (2026-09-09, 4-line transcript):** glm ~9–11s, m3 ~23–31s
→ "m3 is 2–3× slower."

**Full-run reality (real 4K–50K-char transcripts):** medians 22.6 vs 24.4s,
and **m3 had the LOWER max** (35s vs glm's 67s). The thinking overhead that
dominates short prompts washes out on real work.

**Tool design consequence:** latency is always measured on real-size
transcripts in the comparison corpus; toy probes are disqualifier-only
tools, never quoted as performance data.

---

## 6. Lesson 5 — what "quality" comparison actually needs

What the A/B taught us about dimensions:

| Dimension | Catches | Why it exists |
|---|---|---|
| `parse_success` | Dead outputs | The one user-visible hard failure (video → status='error') |
| `structure` | Malformed shapes | Quiz needs 4 options + valid answer_index; flashcards need counts |
| `topic_match` | Self-inconsistency | Lesson 2 — invisible to parse + content checks |
| `grounding` | Hallucination | Lesson 3 — settles "invented vs extracted" |
| `latency` | UX budget | Lesson 4 — chat vs background have different budgets |
| `content_metrics` | Style differences | Density, counts, lengths — flags for human review, not auto-fails |
| **Human judgment** | The rest | Summary accuracy, phrasing quality, mindmap *taste* — stays manual, always |

**The honest caveat recorded:** the A/B's "quality is close" judgment was
made by the same person who ran the test (me), not blind. The tool's
side-by-side report exists precisely so the human judgment step is
*someone else's* eyes, with materials labeled neutrally (A/B, not by model
name) until scores are recorded.

---

## 7. Decisions carried forward into the tool

| # | Decision | Reason |
|---|---|---|
| D1 | Base on `main`, not mvp2-patches | Tool must outlive go-live branch; simpler base = cleaner independence |
| D2 | Versioned input contract (`compare-ai-results/1`) | Branch firewall — any branch exports + calls, no merges needed |
| D3 | Production prompt + production parser when in-app | The comparison must measure what production actually does |
| D4 | Structural contamination checks, never substring | Lesson 1's false-positive trap |
| D5 | Grounding + correlation slices, not single scores | Lesson 3's insight came from slicing |
| D6 | Real-size transcripts for latency | Lesson 4 |
| D7 | Human judgment stays a first-class step | Lessons 5+6 — machine scores rank, humans decide |
| D8 | glm-5.2 remains launch model regardless | Original constraint, unchanged |

---

## 8. Artifacts from the original experiment

- `scripts/ab_test_materials.py` (on `mvp2-production-patches`) — the
  one-off runner this tool replaces/generalizes
- `doc/model-ab-glm52-vs-minimax-m3.md` + `doc/model-ab-raw-scorecard.md`
  (on mvp2-production-patches) — the full write-up + scorecard
- `/tmp/model-ab/` (ephemeral) — raw outputs; the tool's output format is
  modeled on these but timestamped + indexed