"""dimensions.py — the validated dimension set (mvp1-spec §3).

Every dimension is a PURE FUNCTION over (parsed, transcript_segments)
— no I/O, no globals, no LLM calls (P4). Hard gates (parse_success,
structure, thinking_contamination) disqualify; quality dims are
reported; style dims add context. topic_match reports BOTH exact and
normalized rates — exact is the headline, normalized shows the
click-time normalizer's fix margin (the 2026-09-22 question).
"""

from __future__ import annotations

import re
from typing import Any


# ── helpers ─────────────────────────────────────────────────────────────

def _mindmap_node_names(mindmap_md: str) -> list[str]:
    """Extract node labels from Markmap markdown.

    Nodes are the text after the # markers, stripped of markdown
    formatting. e.g. "# Root\\n## - Child" → ["Root", "Child"].
    """
    names: list[str] = []
    for line in mindmap_md.splitlines():
        m = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
        if m:
            # Strip bold/italic/link markers
            label = re.sub(r"[*_`\[\]]", "", m.group(1)).strip()
            if label:
                names.append(label)
    return names


def _normalize(name: str) -> str:
    """Case-fold + punctuation-strip + whitespace-collapse + light stem
    for fuzzy matching. 'Explore, Plan, Code, Commit Workflow' →
    'explore plan code commit workflow'; trailing-s stripped so
    plural drift ('Cornerstones' vs 'Cornerstone') resolves."""
    s = name.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Light stem: singular/plural unification (word-final s only —
    # cheap and sufficient for node-label matching; NOT a real
    # stemmer, deliberately)
    s = " ".join(
        w[:-1] if len(w) > 3 and w.endswith("s") else w for w in s.split()
    )
    return s


def _is_match(topic: str, nodes: list[str]) -> tuple[bool, bool]:
    """Return (exact, normalized_match).

    exact: the raw topic string appears in the mindmap text (the
    production clickability rule — the old A/B's check).

    normalized: the normalized topic equals a normalized node, OR one
    is a substring of the other (the candidate fix's rule — catches
    appended-word phrasings like '... Commit Workflow' vs the node
    '... Commit'). The substring direction is checked both ways so a
    node that TRUNCATES its own topic still counts.
    """
    if not topic or not nodes:
        return False, False
    exact = topic in "\n".join(nodes)
    n_topic = _normalize(topic)
    if not n_topic:
        return exact, False
    for node in nodes:
        n_node = _normalize(node)
        if not n_node:
            continue
        if n_topic == n_node or n_topic in n_node or n_node in n_topic:
            return exact, True
    return exact, False


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"\W+", text.lower()) if len(w) > 2}


# ── dimensions (each: parsed → dict of metrics) ──────────────────────────

def parse_success(raw: str) -> dict[str, Any]:
    """Hard gate: does it extract as a dict?"""
    from compare_ai_results.extract import extract_json

    try:
        extract_json(raw)
        return {"ok": True}
    except ValueError as e:
        return {"ok": False, "error": str(e)[:200]}


def thinking_contamination(raw: str) -> dict[str, Any]:
    """Hard gate (structural only — Lesson 1): response STARTS with
    thinking markers before any JSON. Substring checks are
    forbidden (thinking markers legitimately appear inside
    transcript-derived content)."""
    head = raw.lstrip()[:40].lower()
    contaminated = (
        head.startswith("<think")
        or head.startswith("thinking:")
        or head.startswith("```thinking")
    )
    return {"contaminated": contaminated}


def structure(parsed: dict) -> dict[str, Any]:
    """Hard gate: required keys + shape bounds (the old A/B's
    validate_parsed, minus topic-match which is its own dimension)."""
    problems: list[str] = []
    for key in ("summary", "mindmap", "flashcards", "quiz",
                "topic_timestamps"):
        if key not in parsed:
            problems.append(f"missing key: {key}")
    if "summary" in parsed and not str(parsed["summary"]).strip():
        problems.append("empty summary")
    if "mindmap" in parsed and not str(parsed["mindmap"]).strip():
        problems.append("empty mindmap")
    if "flashcards" in parsed:
        n = len(parsed["flashcards"])
        if n < 3:
            problems.append(f"only {n} flashcards")
    if "quiz" in parsed:
        n = len(parsed["quiz"])
        if n < 2:
            problems.append(f"only {n} quiz questions")
        for i, q in enumerate(parsed["quiz"]):
            if q.get("answer_index") not in (0, 1, 2, 3):
                problems.append(f"quiz[{i}] bad answer_index")
            if len(q.get("options", [])) != 4:
                problems.append(f"quiz[{i}] has {len(q.get('options', []))} options")
    if "topic_timestamps" in parsed:
        for i, t in enumerate(parsed["topic_timestamps"]):
            if not isinstance(t.get("start"), (int, float)):
                problems.append(f"topic_timestamps[{i}] non-numeric start")
    return {"ok": not problems, "problems": problems}


def topic_match(parsed: dict) -> dict[str, Any]:
    """Quality: topic↔mindmap self-consistency (Lesson 2).

    Reports exact rate AND normalized rate, plus the mismatch log
    (every non-exact topic with the closest node) — the raw material
    for the click-time normalizer decision.
    """
    topics = parsed.get("topic_timestamps") or []
    mindmap = str(parsed.get("mindmap") or "")
    if not topics or not mindmap:
        return {"total": len(topics), "exact": 0, "normalized": 0,
                "mismatches": []}
    nodes = _mindmap_node_names(mindmap)
    exact_n = norm_n = 0
    mismatches: list[dict] = []
    for t in topics:
        topic = str(t.get("topic", ""))
        ex, nm = _is_match(topic, nodes)
        if ex:
            exact_n += 1
        if nm:
            norm_n += 1
        if not ex:
            # Closest node by normalized-substring; for the log
            best = None
            n_topic = _normalize(topic)
            for node in nodes:
                if _normalize(node) and (
                    n_topic in _normalize(node) or _normalize(node) in n_topic
                ):
                    best = node
                    break
            mismatches.append({
                "topic": topic,
                "normalized_fixes": nm,
                "closest_node": best,
            })
    return {
        "total": len(topics),
        "exact": exact_n,
        "normalized": norm_n,
        "mismatches": mismatches,
    }


def grounding(parsed: dict, segments: list[dict]) -> dict[str, Any]:
    """Quality (Lesson 3): per-node word overlap vs transcript; ≥50%
    of nodes must have ≥50% of their words in the transcript."""
    nodes = _mindmap_node_names(str(parsed.get("mindmap") or ""))
    if not nodes:
        return {"median_overlap": None, "ungrounded": []}
    transcript_words: set[str] = set()
    for seg in segments:
        transcript_words |= _words(str(seg.get("text", "")))
    overlaps = []
    ungrounded = []
    for node in nodes:
        nw = _words(node)
        if not nw:
            continue
        hit = len(nw & transcript_words) / len(nw)
        overlaps.append(hit)
        if hit < 0.5:
            ungrounded.append({"node": node, "overlap": round(hit, 2)})
    overlaps.sort()
    median = overlaps[len(overlaps) // 2] if overlaps else None
    return {"median_overlap": round(median, 3) if median is not None else None,
            "ungrounded": ungrounded}


def content_metrics(parsed: dict) -> dict[str, Any]:
    """Style: counts + densities for human review (no pass/fail)."""
    mindmap_md = str(parsed.get("mindmap") or "")
    return {
        "flashcards": len(parsed.get("flashcards") or []),
        "quiz": len(parsed.get("quiz") or []),
        "topics": len(parsed.get("topic_timestamps") or []),
        "mindmap_nodes": len(_mindmap_node_names(mindmap_md)),
        "summary_chars": len(str(parsed.get("summary") or "")),
    }


def latency(elapsed_s: float) -> dict[str, Any]:
    """Efficiency (Lesson 4): wall-clock; never auto-fails."""
    return {"seconds": round(elapsed_s, 1)}