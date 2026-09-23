from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, cast

from sqlalchemy import String
from sqlalchemy.orm import Session

from app.models.photo import Photo
from app.services.photo_service import is_mtphotos_photo


def _short(value: Any, limit: int = 24) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    if not text:
        return None
    return text[:limit]


def _tag_list(value: Any, limit: int = 10) -> list[str]:
    """自由发挥的标签：去空白、限长、去重，不做词表过滤。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _short(item, 16)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def analysis_filter_clauses(
    *,
    scene: Optional[str] = None,
    shot: Optional[str] = None,
    shoes_type: Optional[str] = None,
    hosiery_present: Optional[str] = None,
    analyzed: Optional[bool] = None,
    tags: Optional[list[str]] = None,
) -> list:
    clauses = []
    if scene:
        clauses.append(Photo.analysis_scene == scene)
    if shot:
        clauses.append(Photo.analysis_shot == shot)
    if shoes_type:
        clauses.append(Photo.analysis_shoes_type == shoes_type)
    if hosiery_present:
        clauses.append(Photo.analysis_hosiery_present == hosiery_present)
    if analyzed is True:
        clauses.append(Photo.analyzed_at.is_not(None))
    elif analyzed is False:
        clauses.append(Photo.analyzed_at.is_(None))
    for tag in tags or []:
        needle = f'%"{tag}"%'
        clauses.append(cast(Photo.analysis, String).like(needle))
    return clauses


def aggregate_tags(photos: list[Photo]) -> list[dict[str, Any]]:
    """统计当前集合内出现过的自由标签，按次数降序。"""
    counter: dict[str, int] = {}
    for photo in photos:
        data = photo.analysis if isinstance(photo.analysis, dict) else None
        if not data:
            continue
        for item in data.get("tags") or []:
            text = str(item).strip()
            if not text:
                continue
            counter[text] = counter.get(text, 0) + 1
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"tag": tag, "count": count} for tag, count in ranked]


def update_analysis_text(
    photo: Photo, *, caption_zh: str, tags: list[str]
) -> dict[str, Any]:
    """手动编辑：只改描述与标签，其余 AI 结构化字段原样保留。"""
    if is_mtphotos_photo(photo):
        raise ValueError("照片墙标签来自 MT Photos，请在那边修改")
    analysis = dict(photo.analysis) if isinstance(photo.analysis, dict) else {}
    analysis["caption_zh"] = _short(caption_zh, 200)
    analysis["tags"] = _tag_list(tags)
    analysis["manually_edited"] = True
    photo.analysis = analysis
    if photo.analyzed_at is None:
        photo.analyzed_at = datetime.utcnow()
    db_session = Session.object_session(photo)
    if db_session is not None:
        db_session.commit()
        db_session.refresh(photo)
    return analysis