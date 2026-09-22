"""Tests for the compare_ai_results MVP1-lite package.

Golden cases per mvp1-spec: the 5 known m3 mismatch examples from the
23-video A/B are fixtures here (test_topic_match) — the tool must
classify them exactly as the A/B did. No network: engine flows are
exercised with mocked ollama responses.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── input_schema ──────────────────────────────────────────────────────────

def test_valid_input_passes():
    from compare_ai_results.input_schema import validate_input

    raw = {
        "schema": "compare-ai-results/1",
        "transcripts": [{
            "video_id": "v1", "title": "T",
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
        }],
        "models": ["a:cloud", "b:cloud"],
    }
    assert validate_input(raw) == []


def test_unknown_schema_rejected():
    from compare_ai_results.input_schema import validate_input

    errors = validate_input({"schema": "compare-ai-results/2"})
    assert errors and "compare-ai-results/1" in errors[0]


def test_duplicate_video_ids_rejected():
    from compare_ai_results.input_schema import validate_input

    raw = {
        "schema": "compare-ai-results/1",
        "transcripts": [
            {"video_id": "v1", "segments": [{"start": 0, "end": 1, "text": "x"}]},
            {"video_id": "v1", "segments": [{"start": 0, "end": 1, "text": "x"}]},
        ],
        "models": ["a", "b"],
    }
    errors = validate_input(raw)
    assert any("duplicate" in e for e in errors)


def test_empty_segments_rejected():
    from compare_ai_results.input_schema import validate_input

    raw = {
        "schema": "compare-ai-results/1",
        "transcripts": [{"video_id": "v1", "segments": []}],
        "models": ["a", "b"],
    }
    errors = validate_input(raw)
    assert any("segments" in e for e in errors)


def test_models_must_be_two():
    from compare_ai_results.input_schema import validate_input

    raw = {
        "schema": "compare-ai-results/1",
        "transcripts": [
            {"video_id": "v1", "segments": [{"start": 0, "end": 1, "text": "x"}]}
        ],
        "models": ["only-one"],
    }
    errors = validate_input(raw)
    assert any(">= 2" in e for e in errors)


def test_unknown_dimension_rejected_not_ignored():
    from compare_ai_results.input_schema import validate_input

    raw = {
        "schema": "compare-ai-results/1",
        "transcripts": [
            {"video_id": "v1", "segments": [{"start": 0, "end": 1, "text": "x"}]}
        ],
        "models": ["a", "b"],
        "dimensions": ["topic_match "],  # trailing-space typo
    }
    errors = validate_input(raw)
    assert any("dimensions" in e for e in errors)


# ── extract (4-strategy parser port) ─────────────────────────────────────

def test_extract_strategies():
    from compare_ai_results.extract import extract_json

    assert extract_json('{"a": 1}') == {"a": 1}                    # direct
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}     # fence
    assert extract_json('Sure! Here is the JSON:\n{"a": 1}') == {"a": 1}  # preamble
    assert extract_json('prose {"a": 1} more prose') == {"a": 1}  # brace match
    with pytest.raises(ValueError):
        extract_json("no json here at all")


# ── topic_match — the 2026-09-22 question, with the A/B's real failures ──

def _tm(parsed):
    from compare_ai_results.dimensions import topic_match
    return topic_match(parsed)


def test_topic_match_exact_pass():
    r = _tm({"topic_timestamps": [{"topic": "Hooks", "start": 1}],
             "mindmap": "# Hooks\n## Basics"})
    assert r["exact"] == 1 and r["normalized"] == 1


def test_topic_match_the_five_real_m3_failures():
    """The 5 known mismatch classes from the 23-video A/B, as fixtures:
    appended words, casing, phrasing variants. Exact must FAIL,
    normalized must RESOLVE (that's the fix margin claim under test)."""
    cases = [
        # (topic, mindmap) — from model-ab-glm52-vs-minimax-m3.md
        ("Explore, Plan, Code, Commit Workflow",
         "# Explore, Plan, Code, Commit\n## Workflow"),      # appended word
        ("Core Competencies Approach",
         "# Core competencies approach"),                    # casing
        ("Cornerstone of Delegation",
         "# Cornerstones of Delegation\n## Fundamentals"),   # plural drift
        ("AI is Not a Database or Vending Machine",
         "# AI is not a database or vending machine"),       # casing
        ("Context-Specific Disclosure",
         "# Context-specific disclosure\n## Why it matters"),  # hyphen/case
    ]
    from compare_ai_results.dimensions import topic_match
    for topic, mindmap in cases:
        r = topic_match({
            "topic_timestamps": [{"topic": topic, "start": 0}],
            "mindmap": mindmap,
        })
        assert r["exact"] == 0, f"should fail exact: {topic!r}"
        assert r["normalized"] == 1, f"normalizer must resolve: {topic!r}"
        assert r["mismatches"][0]["normalized_fixes"] is True


def test_topic_match_false_positive_guard():
    """The normalizer must NOT rescue genuinely-different topics —
    substring matching must not become 'anything matches anything'."""
    from compare_ai_results.dimensions import topic_match
    r = topic_match({
        "topic_timestamps": [{"topic": "Quantum Computing Basics", "start": 0}],
        "mindmap": "# Cooking Italian Pasta\n## Sauces",
    })
    assert r["exact"] == 0
    assert r["normalized"] == 0
    assert r["mismatches"][0]["normalized_fixes"] is False


# ── thinking_contamination (Lesson 1: structural check ONLY) ──────────────

def test_thinking_contamination_structural_only():
    from compare_ai_results.dimensions import thinking_contamination
    assert thinking_contamination("<think> hmm\n{...}")["contaminated"] is True
    assert thinking_contamination("Thinking: let me...\n{...}")["contaminated"] is True
    # Thinking markers INSIDE the content (e.g. quoted in transcript
    # text) must NOT trip the gate — only a leading marker does.
    assert thinking_contamination('{"summary": "he said <think> out loud"}')[
        "contaminated"] is False
    assert thinking_contamination('{"summary": "ok"}')["contaminated"] is False


# ── structure + grounding + content_metrics ───────────────────────────────

def _good_parsed():
    return {
        "summary": "A" * 300,
        "mindmap": "# Root\n## Child",
        "flashcards": [{"term": "t", "definition": "d"}] * 3,
        "quiz": [
            {"question": "q", "options": ["a", "b", "c", "d"],
             "answer_index": 0}
        ] * 2,
        "topic_timestamps": [{"topic": "Root", "start": 0}],
    }


def test_structure_ok():
    from compare_ai_results.dimensions import structure
    assert structure(_good_parsed())["ok"] is True


def test_structure_catches_missing_keys():
    from compare_ai_results.dimensions import structure
    parsed = _good_parsed()
    del parsed["quiz"]
    assert structure(parsed)["ok"] is False


def test_grounding():
    from compare_ai_results.dimensions import grounding
    segs = [{"start": 0, "end": 5, "text": "the quick brown fox jumps"}]
    r = grounding({"mindmap": "# Quick Brown Fox"}, segs)
    assert r["median_overlap"] == 1.0
    assert r["ungrounded"] == []
    r2 = grounding({"mindmap": "# Quantum Teleportation Matrix"}, segs)
    assert r2["ungrounded"][0]["node"] == "Quantum Teleportation Matrix"


def test_content_metrics():
    from compare_ai_results.dimensions import content_metrics
    m = content_metrics(_good_parsed())
    assert m["flashcards"] == 3 and m["quiz"] == 2 and m["mindmap_nodes"] == 2


# ── engine (mocked ollama — no network) ──────────────────────────────────

def test_engine_end_to_end_mocked(tmp_path):
    """validate → (mocked) probe+chat → score → files on disk."""
    from compare_ai_results import engine

    inp = {
        "schema": "compare-ai-results/1",
        "transcripts": [{
            "video_id": "v1", "title": "Test",
            "segments": [{"start": 0.0, "end": 2.0, "text": "hello world"}],
        }],
        "models": ["model-a", "model-b"],
    }
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(inp))

    fake_response = json.dumps({
        "summary": "A" * 300,
        "mindmap": "# Root\n## Child",
        "flashcards": [{"term": "t", "definition": "d"}] * 3,
        "quiz": [{"question": "q", "options": ["a", "b", "c", "d"],
                  "answer_index": 0}] * 2,
        "topic_timestamps": [{"topic": "Root", "start": 0}],
    })

    with patch("compare_ai_results.ollama_client.check_models_available",
               return_value=[]), \
         patch("compare_ai_results.ollama_client.chat",
               return_value=(fake_response, 1.23, "")):
        run_dir = engine.run(input_path, out_root=tmp_path / "runs")

    assert (run_dir / "run_index.json").exists()
    assert (run_dir / "metrics.json").exists()
    assert (run_dir / "per-video" / "v1" / "model-a.metrics.json").exists()

    metrics = json.loads((run_dir / "metrics.json").read_text())
    for model in ("model-a", "model-b"):
        m = metrics[model][0]
        assert m["parse_success"]["ok"] is True
        assert m["structure"]["ok"] is True

    # Report generation
    from compare_ai_results.report import write_report
    out = write_report(run_dir)
    text = out.read_text()
    assert "Topic↔Mindmap match" in text
    assert "model-a" in text


def test_engine_fails_fast_on_missing_models(tmp_path):
    """Contract rule 2: unavailable models → error BEFORE any LLM call."""
    from compare_ai_results import engine

    inp = {
        "schema": "compare-ai-results/1",
        "transcripts": [{
            "video_id": "v1", "title": "T",
            "segments": [{"start": 0.0, "end": 1.0, "text": "x"}],
        }],
        "models": ["not-installed-a", "not-installed-b"],
    }
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(inp))

    with patch("compare_ai_results.ollama_client.check_models_available",
               return_value=["not-installed-a", "not-installed-b"]):
        with pytest.raises(SystemExit) as exc:
            engine.run(input_path, out_root=tmp_path)
    assert "ollama pull" in str(exc.value)


def test_engine_cache_idempotent(tmp_path):
    """A re-run after a crash costs ZERO LLM calls (raw cache hit)."""
    from compare_ai_results import engine

    inp = {
        "schema": "compare-ai-results/1",
        "transcripts": [{
            "video_id": "v1", "title": "T",
            "segments": [{"start": 0.0, "end": 1.0, "text": "x"}],
        }],
        "models": ["model-a", "model-b"],
    }
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(inp))

    fake = json.dumps(_good_parsed())
    with patch("compare_ai_results.ollama_client.check_models_available",
               return_value=[]), \
         patch("compare_ai_results.ollama_client.chat",
               return_value=(fake, 1.0, "")) as mock_chat:
        engine.run(input_path, out_root=tmp_path / "runs")
        # Second run: cached — chat must NOT be called again
        engine.run(input_path, out_root=tmp_path / "runs")
    assert mock_chat.call_count == 2  # only the first run's 2 calls