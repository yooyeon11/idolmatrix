"""EntityFieldLock：实体字段锁（跨设备持久的「AI/站点获取不写入」标记）。

通用技术表：entity_type + entity_id 指向任一实体，每实体一行。
locks 为稀疏 JSON：只存已锁定的键（DB 字段名，或保留区块键
avatar / memberships）。锁只在前端约束「AI 分析」「站点获取」
的写入动作，不拦手动编辑与保存；服务端仅负责存取。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import JSON, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class EntityFieldLock(TimestampMixin, Base):
    __tablename__ = "entity_field_locks"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            name="uq_entity_field_locks_entity",
        ),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # songs / albums / artists / groups / companies
    entity_type: Mapped[str] = mapped_column(String(16), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    # 稀疏键值：仅出现 locked=true 的键
    locks: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, default=dict)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EntityFieldLock #{self.id} {self.entity_type}/{self.entity_id} "
            f"keys={list((self.locks or {}).keys())}>"
        )
