"""report.py — RESULTS.md scorecard generation.

Aggregates metrics.json per model (medians, gate pass rates, the
topic-match exact-vs-normalized delta — the 2026-09-22 question's
head and tail — plus the full mismatch log for the normalizer
decision).
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median


def _pct(n: int, d: int) -> str:
    return f"{(100 * n / d):.0f}%" if d else "—"


def build_results_md(run_dir: Path) -> str:
    metrics: dict = json.loads((run_dir / "metrics.json").read_text())
    index: dict = json.loads((run_dir / "run_index.json").read_text())

    models = list(metrics.keys())
    lines: list[str] = []
    lines.append("# Compare-AI-Results — Run Scorecard")
    lines.append("")
    lines.append(f"**Run:** {index.get('ts_utc')} · **Videos:** {index.get('videos')} "
                 f"· **Prompt:** {index.get('prompt_id')}"
                 f"{' (override)' if index.get('prompt_is_override') else ' (bundled)'}")
    lines.append("")

    # Per-model headline table
    lines.append("## Headline (per model)")
    lines.append("")
    header = "| Metric | " + " | ".join(models) + " |"
    sep = "|---" * (len(models) + 1) + "|"
    lines.append(header)
    lines.append(sep)

    def row(label, fn):
        cells = []
        for m in models:
            try:
                cells.append(str(fn(metrics[m])))
            except Exception:
                cells.append("—")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    row("Videos scored", lambda ms: len(ms))
    row("Parse OK (hard gate)", lambda ms: _pct(
        sum(1 for x in ms if x["parse_success"]["ok"]), len(ms)))
    row("Thinking contamination", lambda ms: _pct(
        sum(1 for x in ms if x["thinking_contamination"]["contaminated"]), len(ms)))
    row("Structure clean (hard gate)", lambda ms: _pct(
        sum(1 for x in ms if x.get("structure", {}).get("ok")), len(ms)))
    row("Latency median (s)", lambda ms: median(
        [x["latency"]["seconds"] for x in ms if "latency" in x]))
    lines.append("")

    # topic_match — the 2026-09-22 question
    lines.append("## Topic↔Mindmap match (the minimax question)")
    lines.append("")
    lines.append(header)
    lines.append(sep)
    def _topic_rows(ms):
        tot = sum(x["topic_match"]["total"] for x in ms if "topic_match" in x)
        ex = sum(x["topic_match"]["exact"] for x in ms if "topic_match" in x)
        nm = sum(x["topic_match"]["normalized"] for x in ms if "topic_match" in x)
        return tot, ex, nm
    for label_i, fn in (
        ("Topic entries (total)", lambda ms: _topic_rows(ms)[0]),
        ("EXACT match (production rule)", lambda ms: _topic_rows(ms)[1]),
        ("Exact rate", lambda ms: _pct(_topic_rows(ms)[1], _topic_rows(ms)[0])),
        ("NORMALIZED match (candidate fix)", lambda ms: _topic_rows(ms)[2]),
        ("Normalized rate = fix margin", lambda ms: _pct(_topic_rows(ms)[2], _topic_rows(ms)[0])),
    ):
        row(label_i, fn)
    lines.append("")
    lines.append("> **Read:** Exact rate = today's clickability. Normalized rate")
    lines.append("> = what a click-time normalizer (case-fold + substring)")
    lines.append("> would recover. The gap between them is the fix margin.")
    lines.append("")

    # Grounding
    lines.append("## Grounding (transcript word overlap)")
    lines.append("")
    lines.append(header)
    lines.append(sep)
    row("Median node overlap", lambda ms: median(
        [x["grounding"]["median_overlap"] for x in ms
         if "grounding" in x and x["grounding"]["median_overlap"] is not None]))
    row("Ungrounded nodes (total)", lambda ms: sum(
        len(x["grounding"]["ungrounded"]) for x in ms if "grounding" in x))
    lines.append("")

    # Content metrics
    lines.append("## Content metrics (style — no pass/fail)")
    lines.append("")
    lines.append(header)
    lines.append(sep)
    for label, key in (("Flashcards median", "flashcards"),
                       ("Quiz median", "quiz"),
                       ("Topics median", "topics"),
                       ("Mindmap nodes median", "mindmap_nodes")):
        row(label, lambda ms, k=key: median(
            [x["content_metrics"][k] for x in ms if "content_metrics" in x]))
    row("Summary chars median", lambda ms: median(
        [x["content_metrics"]["summary_chars"] for x in ms if "content_metrics" in x]))
    lines.append("")

    # Mismatch log — the normalizer's test set
    lines.append("## Mismatch log (exact-match failures, all models)")
    lines.append("")
    lines.append("| Model | Video | Topic (as generated) | Normalized fixes? | Closest mindmap node |")
    lines.append("|---|---|---|---|---|")
    for m in models:
        for x in metrics[m]:
            tm = x.get("topic_match")
            if not tm:
                continue
            for mm in tm["mismatches"]:
                lines.append(
                    f"| {m} | {x['title'][:40]} | {mm['topic'][:60]!r} | "
                    f"{'✅' if mm['normalized_fixes'] else '❌'} | "
                    f"{(mm['closest_node'] or '—')[:50]!r} |"
                )
    lines.append("")
    lines.append("### Mismatch resolution summary")
    lines.append("")
    for m in models:
        mismatches = [
            mm for x in metrics[m] for mm in
            (x.get("topic_match") or {}).get("mismatches", [])
        ]
        if not mismatches:
            lines.append(f"- **{m}**: zero exact-match failures 🎉")
            continue
        fixed = sum(1 for mm in mismatches if mm["normalized_fixes"])
        lines.append(
            f"- **{m}**: {len(mismatches)} mismatch(es) — normalizer "
            f"resolves {fixed} ({_pct(fixed, len(mismatches))}); "
            f"{len(mismatches) - fixed} remain unresolved"
        )
    lines.append("")
    return "\n".join(lines)


def write_report(run_dir: Path) -> Path:
    md = build_results_md(run_dir)
    out = run_dir / "RESULTS.md"
    out.write_text(md)
    print(f"[report] written → {out}")
    return out