"""数据体检报告 Schema（只读）。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class HealthIssue(BaseModel):
    check: str
    severity: str  # error / warning / hint
    entity_type: str
    entity_id: int
    entity_uid: Optional[str] = None
    entity_name: str = ""
    title: str
    detail: str = ""
    suggestion: str = ""
    deep_link: Optional[str] = None
    # 「标注无问题」用：key 定位问题条目，signature 描述当前事实（数据变则豁免失效）
    issue_key: str = ""
    signature: str = ""
    reopened: bool = False


class HealthCheckSummary(BaseModel):
    key: str
    name: str
    severity: str
    count: int


class HealthCounts(BaseModel):
    error: int
    warning: int
    hint: int


class HealthEntityStats(BaseModel):
    groups: int
    artists: int
    albums: int
    songs: int
    companies: int
    music_videos: int


class DataHealthReport(BaseModel):
    score: int
    counts: HealthCounts
    total_issues: int
    ignored_count: int = 0
    entity_stats: HealthEntityStats
    checks: List[HealthCheckSummary]
    issues: List[HealthIssue]
    # 已「标注无问题」的条目（不计分、不计问题数）
    ignored_issues: List[HealthIssue] = []


class HealthIgnoreItem(BaseModel):
    """一条「标注无问题」豁免。"""

    key: str
    signature: str = ""
    check: str = ""
    entity_type: str = ""
    entity_id: int = 0
    title: str = ""
    note: str = ""
    created_at: str = ""


class HealthIgnoreList(BaseModel):
    items: List[HealthIgnoreItem]


class HealthIgnoreCreate(BaseModel):
    key: str
    signature: str
    check: str = ""
    entity_type: str = ""
    entity_id: int = 0
    title: str = ""
    note: str = ""


class HealthIgnoreRemove(BaseModel):
    key: str
