"""入库清单与生成任务。

清单是「给用户看的建议」，不是业务数据：生成清单**绝不**创建艺人/组合/歌曲，
只有用户在页面上点「确认入库」才走既有 `library_service.ingest_video`。

- IngestManifest：一条待整理文件对应一份清单，`file_path + fingerprint` 保证幂等
- IngestRequest：生成请求队列（定时 / 手动 / 单条），外置 Agent 按队列取任务
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class IngestManifest(TimestampMixin, Base):
    __tablename__ = "ingest_manifests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 待整理文件的绝对路径（唯一）：同一文件只保留最新一份清单
    file_path: Mapped[str] = mapped_column(String(1000), unique=True, index=True)
    # size + mtime_ns + 前 1MB 的 sha1 摘要：文件被替换才重新生成
    fingerprint: Mapped[str] = mapped_column(String(80), index=True)
    # draft（规则）→ ready（Agent/AI 补全）→ accepted / rejected / failed
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # 产出方：draft / agent / ai
    source: Mapped[str] = mapped_column(String(20), default="draft")
    payload: Mapped[Optional[Any]] = mapped_column(JSON)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    rules_version: Mapped[Optional[str]] = mapped_column(String(40))
    model: Mapped[Optional[str]] = mapped_column(String(120))
    error: Mapped[Optional[str]] = mapped_column(Text)
    generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class IngestRequest(TimestampMixin, Base):
    __tablename__ = "ingest_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # all（全量）/ file（单条文件）/ manifest（对某份清单重生成）
    scope: Mapped[str] = mapped_column(String(20), default="all")
    target: Mapped[Optional[str]] = mapped_column(String(1000))
    # draft（项目内规则）/ agent（外置 Agent）/ ai（页面按钮）
    stage: Mapped[str] = mapped_column(String(20), default="agent")
    # queued / running / done / failed
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error: Mapped[Optional[str]] = mapped_column(Text)
