"""实体字段锁请求/响应模型。"""

from __future__ import annotations

from typing import Dict

from pydantic import BaseModel, Field


class EntityLocksUpdate(BaseModel):
    """PUT /entity-locks/{type}/{id} 请求体：稀疏键值，true=锁定。"""

    locks: Dict[str, bool] = Field(default_factory=dict)


class EntityLocksRead(BaseModel):
    entity_type: str
    entity_id: int
    locks: Dict[str, bool] = Field(default_factory=dict)
