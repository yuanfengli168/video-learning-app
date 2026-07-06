"""LLM service — Ollama integration for generating learning materials.

Sends video transcripts to Ollama and receives structured JSON containing:
- Markdown summary
- Mindmap data (Markmap-compatible markdown)
- Quiz questions and answers
- Flashcard terms and definitions
"""

import json
import re
from typing import Any

import httpx

from app.config import settings

# System prompt for generating learning materials
GENERATION_SYSTEM_PROMPT = """You are an expert educational content generator.
Given a video transcript with timestamps, generate learning materials in JSON format.

You MUST respond with ONLY a valid JSON object, no markdown, no explanation.
The JSON must have exactly these keys:

{
  "summary": "A concise markdown summary of the key points (200-500 words)",
  "mindmap": "Markmap-compatible markdown for a mindmap. Use # for root, ## for branches, ### for sub-branches",
  "flashcards": [
    {"term": "Concept name", "definition": "Clear explanation of the concept"}
  ],
  "quiz": [
    {
      "question": "A clear question",
      "options": ["A", "B", "C", "D"],
      "answer": "Correct option text",
      "answer_index": 0
    }
  ],
  "topic_timestamps": [
    {"topic": "Topic name exactly as it appears in mindmap", "start": 60, "end": 120}
  ]
}

Rules:
- Generate 5-10 flashcards covering the most important concepts
- Generate 3-5 quiz questions with 4 options each
- The summary should be in markdown with headers and bullet points
- The mindmap should be hierarchical markdown that Markmap can render
- answer_index is 0-based (0=A, 1=B, 2=C, 3=D)
- TOPIC TIMESTAMPS: For each major topic in the mindmap, identify which part of the video discusses it
  - Use the timestamps from the transcript to determine start and end (in seconds)
  - Each topic must match EXACTLY (case-sensitive) a node name from the mindmap
  - Generate 5-15 topic_timestamps entries covering the most important topics
  - Topics should not overlap in time ranges
  - Skip very short topics (less than 10 seconds)
  - For parent topics in the mindmap, you can include them too if they cover a clear time range
"""


def _extract_json(text: str) -> dict[str, Any]:
    """Extract JSON from a text response, handling markdown code fences.

    Tries to find a JSON object in the response, even if wrapped in
    ```json ... ``` fences or has extra text.
    """
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON in code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find the first { ... } block
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError("Could not extract valid JSON from LLM response")


def generate_materials(transcript: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    """Generate learning materials from a transcript using Ollama.

    Args:
        transcript: Dict with 'segments' (list of {start, end, text}).
        model: Ollama model name (defaults to settings.ollama_model).

    Returns:
        Dict with keys: summary, mindmap, flashcards, quiz.
    """
    model = model or settings.ollama_model

    # Build transcript text from segments
    transcript_text = "\n".join(
        f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}"
        for seg in transcript.get("segments", [])
    )

    if not transcript_text.strip():
        raise ValueError("Transcript is empty — cannot generate materials")

    # Call Ollama API
    response = httpx.post(
        f"{settings.ollama_base_url}/api/chat",
        json={
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Transcript:\n\n{transcript_text}"},
            ],
        },
        timeout=300.0,  # 5 min timeout for long transcripts
    )
    response.raise_for_status()

    result = response.json()
    content = result.get("message", {}).get("content", "")

    return _extract_json(content)


def generate_summary(transcript: dict[str, Any], model: str | None = None) -> str:
    """Generate just the markdown summary."""
    materials = generate_materials(transcript, model)
    return materials.get("summary", "")


def generate_mindmap(transcript: dict[str, Any], model: str | None = None) -> str:
    """Generate just the mindmap markdown."""
    materials = generate_materials(transcript, model)
    return materials.get("mindmap", "")


def generate_flashcards(transcript: dict[str, Any], model: str | None = None) -> list[dict[str, str]]:
    """Generate just the flashcards."""
    materials = generate_materials(transcript, model)
    return materials.get("flashcards", [])


def generate_quiz(transcript: dict[str, Any], model: str | None = None) -> list[dict[str, Any]]:
    """Generate just the quiz."""
    materials = generate_materials(transcript, model)
    return materials.get("quiz", [])