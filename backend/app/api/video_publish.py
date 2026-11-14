"""VideoPublish API：给视频绑定 / 解绑 / 查询 B 站分发记录。

挂在 /music-videos 前缀下（不是独立的 /video-publishes），因为这条记录
天然是「某个视频的属性」，没有独立生命周期，列表也不需要单独一页。

路由注意：`/{mv_id}/publish` 是两段路径，不会与 music_videos.py 的
`GET /{mv_id}` 冲突（后者只匹配单段）。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.deps import DbDep
from app.models.music_video import MusicVideo
from app.schemas.common import MessageResponse
from app.schemas.video_publish import (
    VideoPublishRead,
    VideoPublishStatusUpdate,
    VideoPublishUpsert,
)
from app.services import video_publish_service

router = APIRouter(prefix="/music-videos", tags=["video-publish"])


def _require_mv(db, mv_id: int) -> MusicVideo:
    mv = db.scalar(
        select(MusicVideo).where(
            MusicVideo.id == mv_id, MusicVideo.deleted_at.is_(None)
        )
    )
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    return mv


@router.get("/{mv_id}/publish", response_model=Optional[VideoPublishRead])
def get_video_publish(mv_id: int, db: DbDep):
    """当前视频的 B 站绑定；未绑定时返回 null（不是 404，前端少一次分支）。"""
    _require_mv(db, mv_id)
    row = video_publish_service.get_publish(db, mv_id)
    return video_publish_service.to_read(row) if row else None


@router.put("/{mv_id}/publish", response_model=VideoPublishRead)
def put_video_publish(mv_id: int, payload: VideoPublishUpsert, db: DbDep):
    """绑定/改绑 BV 号。

    `bvid` 允许传完整 B 站地址（后端抠出 BV 号与 p 参数）；
    但 schema 已先做格式校验，所以这里收到的一定是裸 BV 号——
    完整地址请走 `POST /{mv_id}/publish/parse` 预览。
    """
    mv = _require_mv(db, mv_id)
    row = video_publish_service.upsert_publish(db, mv, payload)
    return video_publish_service.to_read(row)


@router.post("/{mv_id}/publish/parse", response_model=dict)
def parse_bvid(mv_id: int, payload: dict, db: DbDep):
    """把用户粘贴的内容归一化成 BV 号 + 分P（供前端预览后再确认）。

    前端「粘贴地址 → 自动填 BV」用；不落库。
    """
    _require_mv(db, mv_id)
    raw = str(payload.get("raw") or "")
    bvid, page, error = video_publish_service.normalize_bvid(raw)
    return {
        "bvid": bvid,
        "page": page,
        "error": error,
        "embed_url": (
            video_publish_service.build_embed_url(bvid, page) if bvid else None
        ),
    }


@router.patch("/{mv_id}/publish/status", response_model=VideoPublishRead)
def patch_publish_status(mv_id: int, payload: VideoPublishStatusUpdate, db: DbDep):
    """改状态：人工标记「已锁定」或状态巡检回写。"""
    _require_mv(db, mv_id)
    row = video_publish_service.get_publish(db, mv_id)
    if not row:
        raise HTTPException(404, "该视频尚未绑定 B 站视频")
    row = video_publish_service.set_status(db, row, payload.status, error=payload.error)
    return video_publish_service.to_read(row)


@router.delete("/{mv_id}/publish", response_model=MessageResponse)
def delete_publish(mv_id: int, db: DbDep):
    """解绑（软删除，保留历史可回溯）。"""
    _require_mv(db, mv_id)
    row = video_publish_service.get_publish(db, mv_id)
    if not row:
        return MessageResponse(message="未绑定，无需解绑")
    video_publish_service.unlink(db, row)
    return MessageResponse(message="已解绑")
