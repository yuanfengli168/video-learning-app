"""compare_ai_results — standing model-comparison tool (MVP1-lite).

Built per doc/compare-ai-results-tool/mvp1-spec.md, scoped to the
2026-09-22 50-video minimax-vs-glm question (the full MVP1 package
grows from this seed — see TODO.md build order, steps 1-6 of 10).

Architecture principles (overview.md §2), honored:
  P1: based on main, no app/ imports anywhere (self-contained).
  P2: this package can be vendored into ANY branch by copying it.
  P3: the ONLY boundary is data — the input contract v1 JSON.
  P4: every dimension is a pure function over (parsed, transcript).

CLI:
  python -m compare_ai_results run input.json
  python -m compare_ai_results report comparison-runs/<ts>/
"""

__version__ = "0.1.0-mvp1lite"