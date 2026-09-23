"""GroupMembership（组合成员关系）。

记录 Artist 在 Group 中的成员身份，支持成员变动历史（多次进出）。
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from sqlalchemy import JSON, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampMixin
from app.core.database import Base


class GroupMembership(TimestampMixin, Base):
    __tablename__ = "group_memberships"
    __table_args__ = (
        # 同一时刻同一组合同一艺人只能有一条 Active；Former 可有多段
        Index(
            "uq_membership_group_artist_active",
            "group_id",
            "artist_id",
            unique=True,
            sqlite_where=text("status = 'Active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    artist_id: Mapped[int] = mapped_column(
        ForeignKey("artists.id", ondelete="CASCADE"), index=True
    )

    join_date: Mapped[Optional[date]] = mapped_column()
    leave_date: Mapped[Optional[date]] = mapped_column()
    # Active / Inactive / Former
    status: Mapped[str] = mapped_column(String(20), default="Active", index=True)
    # JSON 数组，如 ["Vocalist", "Center", "Leader"]
    positions: Mapped[Optional[List[str]]] = mapped_column(JSON)

    # ===== 关系 =====
    group: Mapped["Group"] = relationship("Group", back_populates="memberships")
    artist: Mapped["Artist"] = relationship("Artist", back_populates="memberships")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Membership artist={self.artist_id} group={self.group_id} {self.status}>"
