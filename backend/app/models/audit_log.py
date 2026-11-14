"""操作审计日志：谁、何时、对哪个实体做了什么。

设计取舍（2026-11-13）：

1. **只记写操作**（POST/PUT/PATCH/DELETE）。读操作量太大，且没有回溯价值。
2. **只存字段级 diff**，不存整对象。整对象会让日志表比业务表还大，
   而且简介这类长文本会把库撑爆。
3. 拿不到 diff 的路径（没有埋点的写接口）仍然记一行操作日志，
   `changes_json` 为空 —— 至少有「谁在什么时候调了这个接口」。
4. `request_id` 用于把同一次操作的多条日志串起来（未来做批量任务时有用）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    # web = 浏览器会话；token = X-API-Token；system = 内部任务
    actor_type: Mapped[str] = mapped_column(String(12), default="web")
    # 用户名 或 令牌名（令牌名是审计归属的关键：能区分是哪个工具干的）
    actor_label: Mapped[str] = mapped_column(String(80), default="")
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(300))
    entity_type: Mapped[Optional[str]] = mapped_column(String(40), default=None)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    # create / update / delete / other
    action: Mapped[str] = mapped_column(String(16), default="other")
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    # {"description": {"from": "...", "to": "..."}}，最多 8KB
    changes_json: Mapped[Optional[str]] = mapped_column(Text, default=None)
    ip: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )
