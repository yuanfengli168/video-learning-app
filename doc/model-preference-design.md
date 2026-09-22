# Model Preference System — Design Discussion (2026-09-22)

> **Status**: design RATIFIED (owner decisions below); implementation NOT
> started. This doc is the pre-implementation record — the commit messages
> will reference it. Related: `model-ab-glm52-vs-minimax-m3.md` (the A/B
> this decision is grounded in), `limits-registry.md` (the two-layer
> override pattern this reuses), `Todo.md` #14 (beta program context).

---

## The owner request (2026-09-22)

1. **PAID users get `minimax-m3:cloud`** (cheaper) — they cannot choose.
2. **ADMIN chooses** between models via a settings page (this page becomes
   a PAID feature in MVP2/3 — the owner sets it for them for now).
3. Initially admin's options: `minimax-m3:cloud`, `glm-5.2:cloud`,
   `glm-5.3:cloud`.
4. Adding a new model next month (e.g. glm-5.4) must be **zero code change**
   (pull + env edit + restart only).

## The ratified design

### Config-driven catalog (the "no code change" mechanism)

Three new env vars (defaults in `config.py`, overridable in `.env`):

```
LLM_MODEL_CATALOG=glm-5.2:cloud,minimax-m3:cloud,glm-5.3:cloud
LLM_MODEL_PAID_DEFAULT=minimax-m3:cloud
LLM_MODEL_ADMIN_DEFAULT=glm-5.2:cloud
```

- **The catalog is a comma-separated string** — the settings page renders
  its radio buttons FROM it, and it's also the validation allowlist. Adding
  glm-5.4 next month = `ollama pull glm-5.4:cloud` → append to the catalog
  → restart → the page shows 4 options. Removing a model = delete from the
  catalog; any user still holding that value falls back to their tier
  default (graceful, no migration).
- **Ollama needs no registration** — any pulled model is callable; the
  catalog is purely OUR selection/validation layer.

### Two-layer resolution (the proven 13d-lite pattern)

```
effective_model(user) = user override (users.llm_model_pref, if set AND in catalog)
                        → tier default (env var: PAID or ADMIN)
                        → legacy fallback (today's llm_model_ollama = glm-5.2:cloud)
```

- New nullable column `users.llm_model_pref` (String; migration via the
  additive `_MIGRATIONS` list — same as max_file_bytes etc.).
- **Only ADMIN's row gets written today** (the settings page is
  admin-gated). When MVP3 exposes "choose your model" to PAID, it's the
  same column + same resolver + one more capability-gated page — zero
  re-architecture.
- Safety rails: an override NOT in the catalog falls back to the tier
  default (typo / removed model can never 500 the pipeline); broken config
  falls back to today's `llm_model_ollama`.
- **FREE untouched** — groq chain, no ollama at all.

### Where the resolver lands

Single call site: `call_llm_with_fallback` Step 4
(`app/services/llm_providers.py`, `model = settings.get_model_for_provider(provider)`).
`user_role` and `user_id` are already in scope; the ollama branch consults
the override. Materials generation AND chat for PAID both flip automatically.
Audit rows keep showing the actual model (`LLM call succeeded via
ollama/minimax-m3:cloud`), so the switch is observable live.

### The admin settings page

- `/admin/settings` (or a section on an existing admin page) —
  `require_capability(MANAGE_USERS)`-gated.
- Radio group rendered from `LLM_MODEL_CATALOG` + Save.
- Writes the ADMIN's OWN `users.llm_model_pref` row; effective on the
  next LLM call (no restart).

## Owner decisions logged (2026-09-22)

| # | Decision |
|---|---|
| 1 | PAID default = `minimax-m3:cloud` (cannot choose; owner-set) |
| 2 | ADMIN default = `glm-5.2:cloud`; admin switches via the settings page |
| 3 | Initial admin catalog: glm-5.2 / minimax-m3 / glm-5.3 |
| 4 | Catalog-driven: adding/removing models is `.env`-only, zero code |
| 5 | **minimax topic-name normalization: YES, fix it — but LATER, not bundled with this** (see §Topic-name issue below) |
| 6 | Ollama is the paid-tier backbone for the next ~6+ months; the design deliberately scopes to single-provider selection |

## The minimax topic-name issue (parked, not bundled)

What it is (from `model-ab-glm52-vs-minimax-m3.md`, the 23-video A/B):
minimax-m3 sometimes phrases a `topic_timestamps` label slightly differently
from the exact mindmap node name (casing / appended words — e.g.
"Explore, Plan, Code, Commit Workflow" vs the node "Explore, Plan, Code,
Commit"). Result: that topic's mindmap click falls back to fuzzy
ancestor-walk instead of the exact timestamp jump — degraded navigation on
~1 of ~14 topics in 5 of 23 videos. **No crashes, no parse failures** —
glm was 23/23 fully-clean vs minimax 18/23 on this one metric.

The parked fix: parser-side canonicalization (case-insensitive + substring
matching between topic and node names) at the click site — likely brings
minimax to 23/23. **Owner decision 2026-09-22: fix it, but as a separate
later batch, NOT bundled with the model-preference work.** Until then,
paying users may see slightly degraded mindmap topic-jump precision on
some videos (known trade, accepted for the cost win).

## Implementation plan (when "go" comes)

- **Commit A**: config vars + `users.llm_model_pref` migration + resolver
  in `llm_providers.py` + `/admin/settings` page + tests for the resolver
  (override/catalog-validation/tier-default/fallback/fail-safe) — house
  two-commit pattern.
- **Commit B**: tests for the settings page (admin-gate, catalog rendering,
  round-trip write) + full-suite green.
- **Rollout**: PAID default flips via env (one line in `.env`, restart);
  the beta cohort's rows don't change (tier default applies at call time —
  no backfill needed).
- **Post-ship observation**: the events audit shows which model each PAID
  generation actually used; compare against the A/B expectations.