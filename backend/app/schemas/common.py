"""通用响应与分页结构。"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict


T = TypeVar("T")


class ORMModel(BaseModel):
    """所有从 ORM 模型序列化的 schema 基类。"""

    model_config = ConfigDict(from_attributes=True)


class EntityReadMixin(ORMModel):
    """核心实体 Read / Brief 的公共身份字段。

    uid：永久业务身份（后端生成，客户端不可修改）
    deleted_at：软删除标记（NULL=正常，非 NULL=已删除）
    """

    uid: str
    deleted_at: Optional[datetime] = None


class PageResponse(BaseModel, Generic[T]):
    """统一分页响应。"""

    items: List[T]
    total: int
    page: int
    page_size: int

    @property
    def total_pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size if self.page_size else 0


class MessageResponse(BaseModel):
    message: str
    detail: Optional[dict] = None
