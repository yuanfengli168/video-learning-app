"""Section model — a module or week within a Course."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Section(Base):
    """A module or week (e.g., 'Week 1: Neural Networks')."""

    __tablename__ = "sections"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    course_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    course: Mapped["Course"] = relationship("Course", back_populates="sections")
    videos: Mapped[list["Video"]] = relationship(
        "Video",
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="Video.order_index",
    )