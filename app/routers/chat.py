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
from app.services.chat import build_system_prompt, chat_with_ollama

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatCreate(BaseModel):
    """Request body for creating a chat session."""
    video_id: str
    concept: str


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

    return {
        "user_message": {
            "role": "user",
            "content": body.content,
        },
        "ai_message": {
            "role": "assistant",
            "content": ai_response,
        },
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
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "message_count": len(s.messages),
        }
        for s in sessions
    ]


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