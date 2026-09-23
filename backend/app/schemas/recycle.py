"""回收站：软删除记录的列表与恢复。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.schemas.common import ORMModel


class RecycleItem(ORMModel):
    id: int
    uid: Optional[str] = None
    name: str
    chinese_name: Optional[str] = None
    deleted_at: datetime
    extra: Optional[str] = None
    file_missing: bool = False
