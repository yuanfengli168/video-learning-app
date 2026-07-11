"""Chat models — chat sessions and messages for real-world usage learning."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Allowed values for ChatSession.scope.
# - "flashcard" (default): triggered by a flashcard's "Teach me real-world
#   usage" button. The `concept` column holds the flashcard text.
# - "video": triggered by the "💬 Discuss" tab on the video page. The
#   chat has access to the full transcript + summary + mindmap + quiz
#   via the system prompt. `concept` is set to a placeholder string
#   (the column is NOT NULL).
SCOPE_FLASHCARD = "flashcard"
SCOPE_VIDEO = "video"
VALID_SCOPES = {SCOPE_FLASHCARD, SCOPE_VIDEO}

# Placeholder stored in the (NOT NULL) `concept` column for
# video-scope sessions. The chat history UI hides it when
# `scope == SCOPE_VIDEO`. Importing it from here keeps the
# router and tests in sync.
VIDEO_SCOPE_CONCEPT_PLACEHOLDER = "[whole video]"


class ChatSession(Base):
    """A chat session.

    Two scopes:
    - scope='flashcard' (default): one chat per flashcard concept. The
      `concept` column holds the trigger text.
    - scope='video': one (or more) chats per video. The `concept`
      column is NULL; context is built from the video's transcript
      and generated materials.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    video_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    # The flashcard concept that triggered this chat. For video-scope
    # sessions, this is set to a placeholder ("[whole video]") so the
    # column can stay NOT NULL — the placeholder is filtered out in
    # the UI when scope='video'. Cheaper than a schema migration.
    concept: Mapped[str] = mapped_column(String(255), nullable=False)
    # Which kind of chat this is — see SCOPE_* constants above. Defaults
    # to "flashcard" so existing rows (pre-scope) still work.
    scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SCOPE_FLASHCARD, server_default=SCOPE_FLASHCARD
    )
    # System prompt used to initialise the chat
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # back_populates mirrors Video.chat_sessions — needed so that
    # `db.delete(video)` cascades to the sessions automatically.
    video: Mapped["Video"] = relationship(
        "Video", back_populates="chat_sessions"
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    """Individual message in a chat session."""

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    session: Mapped["ChatSession"] = relationship(
        "ChatSession", back_populates="messages"
    )