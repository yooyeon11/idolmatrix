"""应用级设置：跨设备共享的键值（JSON）。"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class AppSetting(TimestampMixin, Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[Optional[Any]] = mapped_column(JSON)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AppSetting {self.key}>"
