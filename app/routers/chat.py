"""Chat router — create chat sessions and send/receive messages."""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import Asset, ChatMessage, ChatSession, Course, Section, Video
from app.models.chat import SCOPE_VIDEO, VALID_SCOPES, VIDEO_SCOPE_CONCEPT_PLACEHOLDER
from app.services.chat import (
    build_system_prompt,
    build_video_system_prompt,
    chat_with_ollama,
    parse_citations,
    render_quiz_for_chat,
    transcript_to_chat_text,
)
from app.services.transcription import json_to_transcript

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatCreate(BaseModel):
    """Request body for creating a chat session."""
    video_id: str
    concept: str


class VideoChatCreate(BaseModel):
    """Request body for creating a video-scope (whole-video) chat session.

    No `concept` field — the chat covers the whole video, not a single
    flashcard. The server pulls transcript + summary + mindmap + quiz
    from the video's existing Assets and assembles the LLM context.
    """
    video_id: str


class MessageSend(BaseModel):
    """Request body for sending a message."""
    content: str


@router.post("/sessions")
async def create_chat_session(
    body: ChatCreate,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Create a new chat session for a flashcard concept.

    The system prompt is built from the concept name and stored in the session.
    """
    video = db.get(Video, body.video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Verify ownership
    section = db.get(Section, video.section_id)
    course = db.get(Course, section.course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your video")

    system_prompt = build_system_prompt(body.concept)

    session = ChatSession(
        user_id=user.get("uid", ""),
        video_id=body.video_id,
        concept=body.concept,
        system_prompt=system_prompt,
    )
    db.add(session)
    db.commit()

    return {
        "session_id": session.id,
        "concept": session.concept,
        "system_prompt": session.system_prompt,
    }


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: MessageSend,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Send a message in a chat session and get the AI's response.

    1. Save the user's message
    2. Build the conversation history
    3. Call Ollama with the system prompt + history
    4. Save the assistant's response
    5. Return both messages
    """
    session = db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    if session.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your chat session")

    # Save user message
    user_msg = ChatMessage(
        session_id=session_id,
        role="user",
        content=body.content,
    )
    db.add(user_msg)
    db.commit()

    # Build conversation history for Ollama
    all_messages = db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    ).scalars().all()

    ollama_messages = [
        {"role": msg.role, "content": msg.content}
        for msg in all_messages
    ]

    try:
        ai_response = chat_with_ollama(
            messages=ollama_messages,
            system_prompt=session.system_prompt,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Chat failed: {exc}"
        ) from exc

    # Save assistant message
    ai_msg = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=ai_response,
    )
    db.add(ai_msg)
    db.commit()

    # MVP3.0 Part B (manualTodo [jul14] #6): parse any `[M:SS]` /
    # `[H:MM:SS]` citation markers the AI emitted so the frontend
    # can convert each one into a clickable link. The parser is
    # pure and unit-testable in app/services/chat.py.
    #
    # Only attach citations for video-scope sessions — flashcard-scope
    # chats (default) are about a single concept and the AI doesn't
    # have a transcript to cite, so any [M:SS] would be a hallucination.
    citations: list[dict[str, Any]] = []
    if session.scope == SCOPE_VIDEO:
        citations = parse_citations(ai_response)

    return {
        "user_message": {
            "role": "user",
            "content": body.content,
        },
        "ai_message": {
            "role": "assistant",
            "content": ai_response,
        },
        # Structured list of seek targets. The frontend can either
        # trust this list OR re-parse on the client (defense in depth).
        "citations": citations,
    }


@router.get("/sessions/{session_id}")
async def get_chat_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Get a chat session with all messages."""
    session = db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    if session.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your chat session")

    return {
        "id": session.id,
        "concept": session.concept,
        "video_id": session.video_id,
        "scope": session.scope,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "messages": [
            {
                "role": msg.role,
                "content": msg.content,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
            for msg in session.messages
        ],
    }


@router.get("/sessions")
async def list_chat_sessions(
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List all chat sessions for the current user."""
    sessions = db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user.get("uid", ""))
        .order_by(ChatSession.created_at.desc())
    ).scalars().all()

    return [
        {
            "id": s.id,
            "concept": s.concept,
            "video_id": s.video_id,
            "scope": s.scope,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "message_count": len(s.messages),
        }
        for s in sessions
    ]


def _maybe_log_transcript_parse_error(
    db: Session,
    video_id: str,
    exc: Exception,
    raw_content: str,
) -> None:
    """Log a structured warning when the transcript Asset can't be parsed.

    MVP3.0 Part B (manualTodo [jul14] #6): previously the chat endpoint
    silently swallowed the JSON parse error in _build_video_chat_context
    and told the LLM "(Transcript present but could not be parsed.)"
    — which made the AI hallucinate explanations for why the transcript
    didn't exist. Now we:
      1. Log to the server console with enough context to debug
      2. Include a hint in the LLM-facing message so the AI can tell
         the user "your transcript exists but my parser couldn't read
         it — try regenerating"
      3. Return the (already-built) empty transcript text so the rest
         of the chat flow keeps working.

    The hint text is in English because the LLM can render it in
    whatever language the user's question is in. Kept short so it
    doesn't bloat the prompt.
    """
    import logging
    logger = logging.getLogger(__name__)
    snippet = (raw_content or "")[:200].replace("\n", " ")
    logger.warning(
        "transcript parse failed for video_id=%s: %s | content preview: %r",
        video_id,
        exc,
        snippet,
    )


def _build_video_chat_context(db: Session, video: Video) -> str:
    """Pull the video's transcript + summary + mindmap + quiz and
    format them into the LLM system prompt for a video-scope chat.

    Returns the formatted system prompt string. Falls back to a
    placeholder when an asset is missing so the chat is still
    usable on a half-processed video.
    """
    # Pull all four assets in one round-trip
    assets = db.execute(
        select(Asset).where(Asset.video_id == video.id)
    ).scalars().all()
    by_type: dict[str, Asset] = {a.asset_type: a for a in assets}

    # Transcript: parse stored JSON, format as `[mm:ss] text` lines
    transcript_text = ""
    transcript_asset = by_type.get("transcript")
    if transcript_asset and transcript_asset.content:
        try:
            segments = json_to_transcript(transcript_asset.content)
            transcript_text = transcript_to_chat_text(segments)
        except Exception as exc:
            # Bad JSON in the DB — log it for the developer AND give
            # the LLM a clearer message so it can tell the user what
            # to do (manualTodo [jul14] #6). Previously we just
            # substituted "(Transcript present but could not be
            # parsed.)" and the LLM made up a reason for the failure.
            _maybe_log_transcript_parse_error(
                db, video.id, exc, transcript_asset.content,
            )
            transcript_text = (
                "(The transcript exists in the database but could not be "
                "parsed — likely a stale or malformed JSON. Ask the user to "
                "re-transcribe the video. The summary, mindmap, and quiz are "
                "still available below.)"
            )

    # Summary / mindmap: stored as raw markdown text
    summary = (by_type.get("summary").content if by_type.get("summary") else "") or ""
    mindmap = (by_type.get("mindmap").content if by_type.get("mindmap") else "") or ""

    # Quiz: stored as JSON string — format as Q/A list
    quiz_asset = by_type.get("quiz")
    quiz = render_quiz_for_chat(quiz_asset.content) if quiz_asset else ""

    return build_video_system_prompt(
        video_title=video.title,
        summary=summary,
        mindmap=mindmap,
        quiz=quiz,
        transcript=transcript_text,
    )


@router.post("/video-sessions")
async def create_video_chat_session(
    body: VideoChatCreate,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Create a chat session for the whole video (the "💬 Discuss" tab).

    Unlike flashcard-scope chats, this one is given:
    - The full transcript (with timestamps)
    - The generated summary
    - The mindmap
    - The quiz questions + correct answers

    so the user can ask questions about any part of the video.

    Multiple sessions per video are allowed (one per discussion topic).
    Sessions are listed in the regular chat history page with a
    "Video" scope badge.
    """
    video = db.get(Video, body.video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Verify ownership
    section = db.get(Section, video.section_id)
    course = db.get(Course, section.course_id)
    if course.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your video")

    # Build the LLM context from the video's existing materials
    system_prompt = _build_video_chat_context(db, video)

    session = ChatSession(
        user_id=user.get("uid", ""),
        video_id=body.video_id,
        concept=VIDEO_SCOPE_CONCEPT_PLACEHOLDER,  # NOT NULL workaround
        scope=SCOPE_VIDEO,
        system_prompt=system_prompt,
    )
    db.add(session)
    db.commit()

    return {
        "session_id": session.id,
        "video_id": session.video_id,
        "scope": session.scope,
        "system_prompt": session.system_prompt,
    }


@router.delete("/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    """Delete a chat session."""
    session = db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    if session.user_id != user.get("uid", ""):
        raise HTTPException(status_code=403, detail="Not your chat session")

    db.delete(session)
    db.commit()

    return {"status": "deleted"}