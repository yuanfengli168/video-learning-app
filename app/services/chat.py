"""Chat service — Ollama chat for real-world usage learning.

When a user clicks "Teach me real-world usage" on a flashcard, a chat session
is created with a system prompt that instructs the AI to teach real-world
applications of the concept.
"""

import httpx

from app.config import settings

# System prompt for real-world usage teaching
CHAT_SYSTEM_PROMPT = """You are an expert tutor specializing in real-world applications.

The user is learning about: "{concept}"

Your job is to teach the user how this concept is used in the real world.
- Give concrete, practical examples
- Relate it to well-known companies, products, or scenarios
- Be encouraging and conversational
- Keep responses concise (2-3 paragraphs max)
- If the user asks follow-up questions, continue the conversation naturally

Do not just repeat the definition — the user already knows that.
Focus on HOW and WHERE this concept is applied in practice.
"""


def build_system_prompt(concept: str) -> str:
    """Build the system prompt for a chat session about a concept."""
    return CHAT_SYSTEM_PROMPT.format(concept=concept)


def chat_with_ollama(
    messages: list[dict[str, str]],
    system_prompt: str = "",
    model: str | None = None,
) -> str:
    """Send a chat request to Ollama and return the assistant's response.

    Args:
        messages: List of {role, content} dicts (user/assistant history).
        system_prompt: System prompt for this conversation.
        model: Ollama model name (defaults to settings.ollama_model).

    Returns:
        The assistant's response text.
    """
    model = model or settings.ollama_model

    full_messages = []
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)

    response = httpx.post(
        f"{settings.ollama_base_url}/api/chat",
        json={
            "model": model,
            "stream": False,
            "messages": full_messages,
        },
        timeout=120.0,
    )
    response.raise_for_status()

    result = response.json()
    return result.get("message", {}).get("content", "")