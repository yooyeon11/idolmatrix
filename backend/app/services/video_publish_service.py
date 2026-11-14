"""VideoPublish 业务逻辑：BV 号归一化、绑定/更新、播放载荷。

为什么不把这些放进 api 层：B 站地址形态太多（完整 URL / 带 p / 分享短链 /
裸 BV 号），归一化规则要被「详情接口、播放接口、P1 的自动匹配器」三处复用，
放 service 才不会三份实现漂移。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.music_video import MusicVideo
from app.models.video_publish import (
    ORIGIN_MANUAL,
    PUBLISH_OK,
    PUBLISH_STATUSES,
    VideoPublish,
)
from app.schemas.video_publish import (
    BVID_RE,
    VideoPublishUpsert,
    build_embed_url,
    build_watch_url,
)

logger = logging.getLogger(__name__)

# 从任意文本里抠 BV 号（完整地址、分享文案「【标题】 https://b23.tv/xxx」都覆盖）
_BVID_IN_TEXT = re.compile(r"(BV[0-9A-Za-z]{10})")


def normalize_bvid(raw: str) -> Tuple[Optional[str], int, Optional[str]]:
    """把用户输入归一化成 (bvid, page, error)。

    支持：
    - `BV1xx411c7mD`
    - `https://www.bilibili.com/video/BV1xx411c7mD?p=3`
    - `https://m.bilibili.com/video/BV1xx411c7mD`
    - 分享文案里夹带的 BV 号

    不支持 `b23.tv` 短链（需要发一次 HTTP 跳转才能拿到 BV 号，P0 不让
    后端为一次粘贴去外网；提示用户用「复制链接」得到的完整地址）。
    """
    text = (raw or "").strip()
    if not text:
        return None, 1, "请填写 BV 号或 B 站视频地址"

    page = 1
    # 短链：明确拒绝，避免用户以为绑定成功了
    if "b23.tv" in text or "bili2233.cn" in text:
        return None, 1, "不支持 b23.tv 短链，请用 B 站「复制链接」得到的完整地址"

    # 解析 p 参数（分 P）。
    # 注意不能只对 urlparse(text).query 取值：用户粘的常常是整段分享文案
    # （「看这个 https://... ?p=5 好看」），此时 urlparse 认不出 scheme，
    # query 会是空串。直接在原文里找 ?p= / &p= 更稳。
    m_p = re.search(r"[?&]p=(\d+)", text)
    if m_p:
        try:
            page = max(1, int(m_p.group(1)))
        except (TypeError, ValueError):
            page = 1

    m = _BVID_IN_TEXT.search(text)
    if not m:
        return None, 1, "没找到 BV 号（形如 BV1xx411c7mD）"

    bvid = m.group(1)
    if not BVID_RE.match(bvid):
        return None, 1, "BV 号不完整，请检查"
    return bvid, page, None


def get_publish(
    db: Session, music_video_id: int, platform: str = "bilibili"
) -> Optional[VideoPublish]:
    return db.scalars(
        select(VideoPublish).where(
            VideoPublish.music_video_id == music_video_id,
            VideoPublish.platform == platform,
            VideoPublish.deleted_at.is_(None),
        )
    ).first()


def get_publish_map(
    db: Session, music_video_ids: list[int], platform: str = "bilibili"
) -> dict[int, VideoPublish]:
    """批量取（列表页回填用，避免逐行查询）。"""
    ids = [i for i in music_video_ids if i]
    if not ids:
        return {}
    rows = db.scalars(
        select(VideoPublish).where(
            VideoPublish.music_video_id.in_(ids),
            VideoPublish.platform == platform,
            VideoPublish.deleted_at.is_(None),
        )
    ).all()
    return {r.music_video_id: r for r in rows}


def upsert_publish(
    db: Session,
    mv: MusicVideo,
    payload: VideoPublishUpsert,
    *,
    platform: str = "bilibili",
) -> VideoPublish:
    """绑定或更新一条分发记录。

    同一个视频重复绑定 = 覆盖（P0 不做历史版本；BV 号挂错时直接改）。
    """
    row = get_publish(db, mv.id, platform)
    now = datetime.utcnow()

    if row is None:
        row = VideoPublish(
            music_video_id=mv.id,
            platform=platform,
            bvid=payload.bvid,
            page=payload.page or 1,
            origin=payload.origin or ORIGIN_MANUAL,
            owned_by_me=bool(payload.owned_by_me),
            status=payload.status or PUBLISH_OK,
            source_url=payload.source_url,
            match_score=payload.match_score,
            match_note=payload.match_note,
            submitted_at=now if payload.owned_by_me else None,
            last_checked_at=now,
        )
        db.add(row)
    else:
        row.bvid = payload.bvid
        row.page = payload.page or 1
        row.origin = payload.origin or ORIGIN_MANUAL
        row.owned_by_me = bool(payload.owned_by_me)
        row.status = payload.status or PUBLISH_OK
        row.source_url = payload.source_url
        row.match_score = payload.match_score
        row.match_note = payload.match_note
        row.last_checked_at = now
        row.error = None
        # 从 pending 变 ok 时补投稿时间，便于「我自己投的」列表排序
        if row.owned_by_me and row.submitted_at is None:
            row.submitted_at = now

    db.commit()
    db.refresh(row)
    logger.info(
        "绑定 B 站 #%s mv=%s bvid=%s p%s origin=%s owned=%s",
        row.id, mv.id, row.bvid, row.page, row.origin, row.owned_by_me,
    )
    return row


def set_status(
    db: Session,
    row: VideoPublish,
    status: str,
    *,
    error: Optional[str] = None,
) -> VideoPublish:
    if status not in PUBLISH_STATUSES:
        raise ValueError(f"非法状态：{status}")
    row.status = status
    row.error = error
    row.last_checked_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def unlink(db: Session, row: VideoPublish) -> None:
    """解除绑定（软删除，保留历史便于回溯「这个视频曾经挂过哪个 BV」）。"""
    row.soft_delete()
    db.commit()


def to_read(row: VideoPublish) -> dict[str, Any]:
    """ORM → 响应 dict（含派生字段）。"""
    playable = bool(row.bvid) and row.status == PUBLISH_OK
    return {
        "id": row.id,
        "music_video_id": row.music_video_id,
        "platform": row.platform,
        "bvid": row.bvid,
        "page": row.page or 1,
        "origin": row.origin,
        "owned_by_me": bool(row.owned_by_me),
        "status": row.status,
        "match_score": row.match_score,
        "match_note": row.match_note,
        "source_url": row.source_url,
        "submitted_at": row.submitted_at,
        "last_checked_at": row.last_checked_at,
        "error": row.error,
        "embed_url": (
            build_embed_url(row.bvid, row.page or 1) if playable else None
        ),
        "watch_url": (
            build_watch_url(row.bvid, row.page or 1) if row.bvid else None
        ),
        "playable": playable,
    }


def _row_to_embed(row: VideoPublish) -> Optional[dict[str, Any]]:
    if row is None or not row.bvid or row.status != PUBLISH_OK:
        return None
    return {
        "embed_url": build_embed_url(row.bvid, row.page or 1),
        "watch_url": build_watch_url(row.bvid, row.page or 1),
        "bvid": row.bvid,
        "page": row.page or 1,
        "owned_by_me": bool(row.owned_by_me),
        "origin": row.origin,
    }


def embed_payload(
    db: Session, mv: MusicVideo, platform: str = "bilibili"
) -> Optional[dict[str, Any]]:
    """播放兜底：返回可直接渲染 iframe 的外部播放参数；不可播返回 None。"""
    return _row_to_embed(get_publish(db, mv.id, platform))


def embed_by_video_id(
    db: Session, music_video_id: int, platform: str = "bilibili"
) -> Optional[dict[str, Any]]:
    """同 `embed_payload`，但只需要 id（播放会话只带 music_video_id）。"""
    if not music_video_id:
        return None
    return _row_to_embed(get_publish(db, music_video_id, platform))
