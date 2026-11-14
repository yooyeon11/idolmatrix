"""VideoPublish 的读写 schema。

派生字段（embed_url / watch_url / playable）由后端算好，前端不拼 URL——
B 站播放器参数以后改了只改这一处。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from pydantic import field_validator

from app.schemas.common import ORMModel

# BV 号：BV + 10 位 base58（新号形如 BV1xx411c7mD，共 12 字符）。
# 不锚定大小写与具体位数之外的规则，B 站历史上调整过编码表。
BVID_RE = re.compile(r"^BV[0-9A-Za-z]{10}$")

BILIBILI_PLAYER_URL = "https://player.bilibili.com/player.html"
BILIBILI_WATCH_URL = "https://www.bilibili.com/video"


def build_embed_url(bvid: str, page: int = 1) -> str:
    """B 站官方 iframe 播放地址（分享面板「嵌入代码」同款）。

    - high_quality=1：优先高清线路
    - danmaku=0：默认关弹幕（媒体库场景不需要在别人站点的弹幕层上叠字）
    - autoplay=1：iframe 只在用户点了播放后才渲染，所以自动播是符合意图的
    - as_wide=1：宽屏布局，避免上下留黑边
    """
    p = max(1, int(page or 1))
    return (
        f"{BILIBILI_PLAYER_URL}?bvid={bvid}&page={p}"
        "&high_quality=1&danmaku=0&autoplay=1&as_wide=1"
    )


def build_watch_url(bvid: str, page: int = 1) -> str:
    p = max(1, int(page or 1))
    return f"{BILIBILI_WATCH_URL}/{bvid}?p={p}"


class VideoPublishRead(ORMModel):
    id: int
    music_video_id: int
    platform: str = "bilibili"
    bvid: Optional[str] = None
    page: int = 1
    origin: str = "manual"
    owned_by_me: bool = False
    status: str = "pending"
    match_score: Optional[float] = None
    match_note: Optional[str] = None
    source_url: Optional[str] = None
    submitted_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    error: Optional[str] = None
    # ===== 派生（不落库）=====
    embed_url: Optional[str] = None
    watch_url: Optional[str] = None
    # 只有 ok + 有 bvid 才允许前端渲染 iframe
    playable: bool = False


class VideoPublishUpsert(ORMModel):
    """绑定/更新一条分发记录。

    bvid 允许传完整 B 站地址（含 ?p=N），归一化在 service 层做，
    这里只做「归一化之后」的格式兜底。
    """

    bvid: str
    page: Optional[int] = None
    origin: str = "manual"
    owned_by_me: bool = False
    status: str = "ok"
    source_url: Optional[str] = None
    match_score: Optional[float] = None
    match_note: Optional[str] = None

    @field_validator("bvid")
    @classmethod
    def _check_bvid(cls, v: str) -> str:
        v = (v or "").strip()
        if not BVID_RE.match(v):
            raise ValueError("BV 号格式不正确（应为 BV + 10 位字母数字）")
        return v

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        from app.models.video_publish import PUBLISH_STATUSES

        if v not in PUBLISH_STATUSES:
            raise ValueError(f"status 必须是 {sorted(PUBLISH_STATUSES)} 之一")
        return v

    @field_validator("origin")
    @classmethod
    def _check_origin(cls, v: str) -> str:
        from app.models.video_publish import PUBLISH_ORIGINS

        if v not in PUBLISH_ORIGINS:
            raise ValueError(f"origin 必须是 {sorted(PUBLISH_ORIGINS)} 之一")
        return v


class VideoPublishStatusUpdate(ORMModel):
    """只改状态（状态巡检回写、人工标记锁定）。"""

    status: str
    error: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        from app.models.video_publish import PUBLISH_STATUSES

        if v not in PUBLISH_STATUSES:
            raise ValueError(f"status 必须是 {sorted(PUBLISH_STATUSES)} 之一")
        return v
