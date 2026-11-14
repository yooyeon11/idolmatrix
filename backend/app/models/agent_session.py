"""入库助手的会话与消息。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class AgentThread(TimestampMixin, Base):
    __tablename__ = "agent_threads"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[Optional[str]] = mapped_column(String(200))
    # 绑定到某条待整理文件（可空：以后也允许全局会话）
    file_path: Mapped[Optional[str]] = mapped_column(String(1000), index=True)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class AgentMessage(TimestampMixin, Base):
    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(Integer, index=True)
    # user / assistant
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    # 工具调用轨迹（action / args / ok），便于回放
    trace: Mapped[Optional[Any]] = mapped_column(JSON)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
