#!/usr/bin/env python3
"""ab_test_materials.py — offline model A/B for material generation (2026-09-09).

Compares glm-5.2:cloud vs minimax-m3:cloud on the REAL production
prompt + the REAL production parser, across every video that has a
transcript — WITHOUT touching the live site:

  - Reads transcripts from the DB (read-only)
  - Sends the exact GENERATION_SYSTEM_PROMPT from app/services/llm.py
  - Parses responses with the exact _extract_json strategies
  - Writes raw + parsed outputs to /tmp/model-ab/<model>/<video>.json
  - Writes a Markdown scorecard at /tmp/model-ab/RESULTS.md
  - NEVER writes to the assets table, never touches videos.status

Usage:
    venv/bin/python scripts/ab_test_materials.py               # all videos
    venv/bin/python scripts/ab_test_materials.py --limit 3     # quick run
    venv/bin/python scripts/ab_test_materials.py --models glm-5.2:cloud
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OLLAMA_CHAT = "http://localhost:11434/api/chat"
OUT_DIR = Path("/tmp/model-ab")

MODELS = ["glm-5.2:cloud", "minimax-m3:cloud"]


def get_system_prompt() -> str:
    """The exact production system prompt (single source of truth)."""
    src = (PROJECT_ROOT / "app/services/llm.py").read_text()
    m = re.search(
        r'GENERATION_SYSTEM_PROMPT = """(.*?)"""', src, re.DOTALL
    )
    if not m:
        raise RuntimeError("GENERATION_SYSTEM_PROMPT not found in llm.py")
    return m.group(1)


def get_transcript_text(content_json: str) -> str:
    """Replicate generate_materials' transcript formatting exactly."""
    from app.services.transcription import json_to_transcript

    transcript = json_to_transcript(content_json)
    segments = transcript.get("segments", [])
    if not segments:
        return ""
    return "\n".join(
        f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}"
        for seg in segments
    )


def parse_with_production_parser(content: str) -> dict:
    """The exact _extract_json from app/services/llm.py."""
    from app.services.llm import _extract_json

    return _extract_json(content)


def call_model(
    model: str, system_prompt: str, transcript_text: str,
    timeout: float = 300.0,
) -> tuple[str, float, str]:
    """Call Ollama chat. Returns (content, latency_s, thinking_field).

    thinking_field: the Ollama message's 'thinking' content if the
    model emitted one — recorded for the scorecard (it must NEVER
    appear inside content for our parser to be safe, but Ollama puts
    it in a separate field for thinking models)."""
    import httpx

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Transcript:\n\n{transcript_text}"},
        ],
        "stream": False,
    }
    t0 = time.time()
    r = httpx.post(OLLAMA_CHAT, json=body, timeout=timeout)
    dt = time.time() - t0
    r.raise_for_status()
    data = r.json()
    msg = data.get("message", {})
    content = msg.get("content", "")
    thinking = msg.get("thinking", "") or ""
    return content, dt, thinking


def validate_parsed(parsed: dict) -> list[str]:
    """Structural checks mirroring what the page renderers expect."""
    problems = []
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
    # Topic names must match mindmap nodes EXACTLY (clickability)
    if "topic_timestamps" in parsed and "mindmap" in parsed:
        mindmap_text = str(parsed["mindmap"])
        for i, t in enumerate(parsed["topic_timestamps"]):
            if str(t.get("topic", "")) not in mindmap_text:
                problems.append(
                    f"topic_timestamps[{i}] topic not in mindmap: "
                    f"{t.get('topic')!r}"
                )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--models", nargs="*", default=MODELS)
    args = parser.parse_args()

    from app.database import SessionLocal
    from app.models import Asset, Video

    system_prompt = get_system_prompt()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for m in args.models:
        (OUT_DIR / Path(m).name).mkdir(exist_ok=True)

    db = SessionLocal()
    try:
        rows = (
            db.query(Video, Asset)
            .join(Asset, Asset.video_id == Video.id)
            .filter(Asset.asset_type == "transcript")
            .order_by(Video.created_at.asc())
            .all()
        )
        if args.limit:
            rows = rows[: args.limit]
        print(f"[ab-test] {len(rows)} video(s) × {len(args.models)} model(s)")
    finally:
        pass

    results = []
    for video, asset in rows:
        tt = get_transcript_text(asset.content)
        if not tt.strip():
            print(f"  SKIP {video.title[:40]!r} (empty transcript)")
            continue
        for model in args.models:
            out_path = (
                OUT_DIR / Path(model).name / f"{video.id}.json"
            )
            # Cache: skip if already produced (idempotent re-runs)
            if out_path.exists():
                print(f"  CACHED {model:18s} {video.title[:40]!r}")
                results.append(json.loads(out_path.read_text()))
                continue
            print(
                f"  RUN {model:18s} {video.title[:40]!r} "
                f"(transcript {len(tt)} chars)"
            )
            try:
                content, latency, thinking = call_model(
                    model, system_prompt, tt
                )
                parse_ok = True
                parse_error = None
                problems: list[str] = []
                parsed = {}
                try:
                    parsed = parse_with_production_parser(content)
                    problems = validate_parsed(parsed)
                except Exception as e:
                    parse_ok = False
                    parse_error = str(e)
                record = {
                    "video_id": video.id,
                    "title": video.title,
                    "model": model,
                    "latency_s": round(latency, 1),
                    "content_len": len(content),
                    "thinking_len": len(thinking),
                    "thinking_in_content": ("thinking" in content.lower()),
                    "parse_ok": parse_ok,
                    "parse_error": parse_error,
                    "problems": problems,
                    "n_flashcards": len(parsed.get("flashcards", [])),
                    "n_quiz": len(parsed.get("quiz", [])),
                    "n_topics": len(parsed.get("topic_timestamps", [])),
                    "raw": content,
                    "parsed": parsed,
                }
            except Exception as e:
                record = {
                    "video_id": video.id,
                    "title": video.title,
                    "model": model,
                    "error": str(e),
                    "parse_ok": False,
                }
            out_path.write_text(json.dumps(record, indent=2))
            results.append(record)
            status = (
                "OK" if record.get("parse_ok") and not record.get("problems")
                else f"ISSUES {record.get('problems', [])[:2]}"
                if record.get("parse_ok")
                else "PARSE FAIL"
            )
            print(
                f"    → {record['latency_s'] if 'latency_s' in record else '?'}s "
                f"{status}"
            )

    # ── Scorecard ──
    md = ["# Model A/B Results — Material Generation", ""]
    md.append(
        f"Generated: {time.strftime('%Y-%m-%d %H:%M %Z')} · "
        f"{len(args.models)} models · "
        f"{len(results) // max(1, len(args.models))} videos"
    )
    md.append("")
    for model in args.models:
        recs = [r for r in results if r.get("model") == model]
        n_ok = sum(1 for r in recs if r.get("parse_ok"))
        n_clean = sum(
            1 for r in recs
            if r.get("parse_ok") and not r.get("problems")
        )
        lats = [r["latency_s"] for r in recs if "latency_s" in r]
        think = [r.get("thinking_len", 0) for r in recs]
        fc = [r.get("n_flashcards", 0) for r in recs]
        md.append(f"## {model}")
        md.append("")
        md.append(
            f"- Parse OK: **{n_ok}/{len(recs)}** "
            f"(clean structure: {n_clean}/{len(recs)})"
        )
        if lats:
            md.append(
                f"- Latency: median {sorted(lats)[len(lats)//2]:.0f}s · "
                f"min {min(lats):.0f}s · max {max(lats):.0f}s"
            )
        if think:
            md.append(f"- Thinking field lengths: max {max(think)} chars")
        if fc:
            md.append(
                f"- Flashcards per video: "
                f"median {sorted(fc)[len(fc)//2]} · min {min(fc)}"
            )
        md.append("")
    md.append("## Per-video detail")
    md.append("")
    md.append(
        "| video | model | latency | parse | problems |"
    )
    md.append("|---|---|---|---|---|")
    for r in results:
        prob = "; ".join(r.get("problems", [])[:3]) or (
            r.get("parse_error") or "—"
        )
        md.append(
            f"| {r['title'][:38]} | {r['model']} | "
            f"{r.get('latency_s', '?')}s | "
            f"{'✅' if r.get('parse_ok') else '❌'} | {prob} |"
        )
    (OUT_DIR / "RESULTS.md").write_text("\n".join(md))
    print(f"\n[ab-test] scorecard: {OUT_DIR / 'RESULTS.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())