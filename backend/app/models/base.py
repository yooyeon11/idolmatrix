"""模型基类与公共混入。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, String, func, select
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """所有业务表统一的创建/更新时间戳。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BaseEntityMixin(TimestampMixin):
    """核心业务实体的公共 Mixin。

    提供双 ID + 软删除生命周期：

    - id：数据库内部技术主键（由各实体声明为 INTEGER PRIMARY KEY）
    - uid：永久业务身份标识（UUID，创建时自动生成，不可修改，删除后不复用）
    - deleted_at：软删除标记（NULL=正常，非 NULL=已删除）

    外部持久化引用原则：任何需要长期保存并在系统外引用的实体身份
    （如未来的 NFO / JSON 导出 / 导入 / 外部 API 数据），必须保存 uid 而非 id。
    内部派生产物（缩略图、转码路径、任务关联）可继续使用 id。

    同时通过 sqlite_autoincrement=True 为 SQLite 启用严格 AUTOINCREMENT，
    保证核心实体即使被物理删除，内部 id 也不会被重新分配。
    """

    __table_args__ = {"sqlite_autoincrement": True}

    uid: Mapped[str] = mapped_column(
        String(36),
        unique=True,
        index=True,
        nullable=False,
        default=lambda: str(uuid.uuid4()),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, index=True, default=None
    )

    # ===== 生命周期 =====
    def soft_delete(self) -> None:
        """软删除：仅写入 deleted_at，保留 id/uid/元数据/关系/文件信息。"""
        self.deleted_at = datetime.utcnow()

    def restore(self) -> None:
        """恢复：清除 deleted_at，实体回到正常状态（uid 保持不变）。"""
        self.deleted_at = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    # ===== 统一活跃查询入口 =====
    @classmethod
    def active_filter(cls):
        """返回「未软删除」的 WHERE 条件：deleted_at IS NULL。"""
        return cls.deleted_at.is_(None)

    @classmethod
    def get_active(cls, db, pk: int):
        """按内部 id 查询未软删除实体；不存在或已删除返回 None。"""
        return db.scalar(select(cls).where(cls.id == pk, cls.active_filter()))

    @classmethod
    def get_by_uid(cls, db, uid: str):
        """按永久业务 uid 查询未软删除实体；不存在或已删除返回 None。"""
        return db.scalar(select(cls).where(cls.uid == uid, cls.active_filter()))


def resolve_active_ids(db, model, ids: Optional[List[int]]):
    """把内部 id 列表解析为未软删除实体列表。

    保持输入顺序，无效 / 已软删除的 id 自动跳过。
    """
    if not ids:
        return []
    id_list = list(ids)
    objs = db.scalars(
        select(model).where(model.id.in_(id_list), model.active_filter())
    ).all()
    by_id = {o.id: o for o in objs}
    return [by_id[i] for i in id_list if i in by_id]
