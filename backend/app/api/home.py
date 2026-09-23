"""首页主舞台：艺人/组合聚焦轮播数据（随机池）+ 官方直拍。"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Artist, Group, MusicVideo
from app.models.music_video import music_video_artists, music_video_groups
from app.schemas.common import ORMModel
from app.services.video_focus import ensure_entity_focus
from app.services.video_meta import normalize_video_types

router = APIRouter(prefix="/home", tags=["home"])

# 单次返回的帧数：首屏轮播够用又不至于加载过重
HERO_STAGE_LIMIT = 8

# 直拍类型优先级：官方 > 个人/组合 > 统一 Fancam（新口径）
_FANCAM_OFFICIAL = ("OfficialFancam", "OfficialFacecam")
_FANCAM_FALLBACK = ("PersonalFancam", "GroupFancam", "Fancam")
_FANCAM_ALL = _FANCAM_OFFICIAL + _FANCAM_FALLBACK


class HeroStageItem(ORMModel):
    """一帧 = 一位艺人或一个组合。"""

    type: str  # artist | group
    id: int
    uid: str = ""
    name: str = ""
    chinese_name: str | None = None
    english_name: str | None = None
    korean_name: str | None = None
    stage_name: str | None = None
    group_type: str | None = None
    tagline: str | None = None
    description: str | None = None
    social_media: dict[str, Any] | None = None
    focus_x: float | None = None
    focus_y: float | None = None
    has_banner: bool = False
    has_avatar: bool = False
    video_count: int = 0
    # 实体最近更新时间：前端拼图片 URL 缓存键，换海报后立刻生效
    updated_at: datetime | None = None


class HeroStageResult(ORMModel):
    items: list[HeroStageItem] = []


class HeroFancamItem(ORMModel):
    """主舞台右侧 2×2 直拍卡片。"""

    id: int
    uid: str
    name: str = ""
    video_type: str | None = None
    video_types: list[str] | None = None
    thumbnail_path: str | None = None
    file_hash: str | None = None
    duration: int | None = None
    performance_date: str | None = None


class HeroFancamResult(ORMModel):
    items: list[HeroFancamItem] = []


def _video_counts(db: Session, table_name: str, col_name: str) -> dict[int, int]:
    """按艺人/组合批量统计库内视频数，返回 {实体 id: 数量}。"""
    link_table = Artist.__table__.metadata.tables.get(table_name)
    if link_table is None:
        return {}
    link_col = link_table.c[col_name]
    rows = db.execute(
        select(link_col, func.count(MusicVideo.id))
        .select_from(MusicVideo)
        .join(link_table, link_table.c.music_video_id == MusicVideo.id)
        .where(
            MusicVideo.deleted_at.is_(None),
            MusicVideo.ingestion_status == "library",
        )
        .group_by(link_col)
    ).all()
    return {int(entity_id): int(cnt) for entity_id, cnt in rows}


def _has_library_video(id_col, link_table, fk_col: str):
    """EXISTS：该实体至少关联一条库内未删除视频。

    口径与文件详情页（artist_id / group_id + ingestion_status=library）
    以及本文件的 _video_counts 完全一致：只看 M2M 关联表，
    不把 subject_artist_id 单独关联的视频算作「有关联」。
    """
    return (
        select(link_table.c.music_video_id)
        .join(MusicVideo, MusicVideo.id == link_table.c.music_video_id)
        .where(
            link_table.c[fk_col] == id_col,
            MusicVideo.deleted_at.is_(None),
            MusicVideo.ingestion_status == "library",
        )
        .exists()
    )


def _pick_entities(db: Session, model, link_table, fk_col: str, limit: int) -> list:
    """随机挑选主舞台实体：必须有头像 + 有关联视频，排除 hide_from_home 与软删除。

    「有关联视频」在做随机抽取前就下推到 SQL（EXISTS），
    否则先抽后过滤会让轮播帧数不足，且空壳实体占用名额。
    """
    return list(
        db.scalars(
            select(model)
            .where(
                model.deleted_at.is_(None),
                model.hide_from_home.is_(False),
                model.avatar_path.is_not(None),
                _has_library_video(model.id, link_table, fk_col),
            )
            .order_by(func.random())
            .limit(limit)
        ).all()
    )


def _normalize_social(raw: Any) -> dict[str, str] | None:
    """把实体 social_media JSON 规整为 {platform: url}，丢掉空值。"""
    if not isinstance(raw, dict):
        return None
    out: dict[str, str] = {}
    for k, v in raw.items():
        key = str(k or "").strip()
        if not key:
            continue
        if isinstance(v, dict):
            url = str(v.get("url") or v.get("href") or "").strip()
        else:
            url = str(v or "").strip()
        if url:
            out[key] = url
    return out or None


def _desc_snippet(raw: Optional[str], limit: int = 160) -> Optional[str]:
    text = " ".join((raw or "").split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _fancam_rank(mv: MusicVideo) -> tuple[int, float]:
    """排序键：官方直拍优先，其次个人/组合/统一 Fancam；同档按演出日新→旧。"""
    _, types = normalize_video_types(mv.video_types, mv.video_type)
    type_set = set(types)
    if type_set & set(_FANCAM_OFFICIAL):
        tier = 0
    elif type_set & {"PersonalFancam", "GroupFancam"}:
        tier = 1
    elif "Fancam" in type_set:
        tier = 2
    else:
        tier = 9
    # 日期越新越好：用负时间戳方便稳定排序
    stamp = 0.0
    for d in (mv.performance_date, mv.release_date, mv.published_date):
        if d is not None:
            try:
                stamp = -float(d.toordinal())
            except Exception:  # noqa: BLE001
                stamp = 0.0
            break
    return (tier, stamp)


def _query_entity_fancams(
    db: Session, entity_type: str, entity_id: int, limit: int
) -> list[MusicVideo]:
    """取某艺人/组合关联的库内直拍，官方优先，最多 limit 条。

    关联视频可能不少，但直拍筛选与排序放在 Python 侧更稳妥
    （SQLite JSON 多值 contains 方言差异大）。
    """
    base = [
        MusicVideo.deleted_at.is_(None),
        MusicVideo.ingestion_status == "library",
    ]
    if entity_type == "artist":
        linked_ids = select(music_video_artists.c.music_video_id).where(
            music_video_artists.c.artist_id == entity_id
        )
        stmt = select(MusicVideo).where(
            *base,
            or_(
                MusicVideo.id.in_(linked_ids),
                MusicVideo.subject_artist_id == entity_id,
            ),
        )
    elif entity_type == "group":
        linked_ids = select(music_video_groups.c.music_video_id).where(
            music_video_groups.c.group_id == entity_id
        )
        stmt = select(MusicVideo).where(*base, MusicVideo.id.in_(linked_ids))
    else:
        raise HTTPException(400, "type 仅支持 artist / group")

    rows = list(db.scalars(stmt.limit(400)).all())
    seen: set[int] = set()
    filtered: list[MusicVideo] = []
    for mv in rows:
        if mv.id in seen:
            continue
        _, types = normalize_video_types(mv.video_types, mv.video_type)
        if not (set(types) & set(_FANCAM_ALL)):
            continue
        seen.add(mv.id)
        filtered.append(mv)
    filtered.sort(key=_fancam_rank)
    return filtered[:limit]


@router.get("/hero-stage", response_model=HeroStageResult)
def hero_stage(
    db: Session = Depends(get_db),
    limit: int = Query(HERO_STAGE_LIMIT, ge=1, le=12),
):
    """随机抽取「有头像 + 有关联视频」的艺人与组合，组装主舞台轮播帧。

    只从有关联视频的实体里随机，避免轮播到空壳资料页；
    排除 hide_from_home 与软删除。全库都没有合格实体时返回空列表，
    前端会自动回退成视频轮播。
    """
    # 两次抽取各自放宽到 limit，再交替取用：
    # 两侧都够时仍是艺人对半 / 组合对半；一侧不足（比如库里只有组合有视频）
    # 时自动由另一侧补齐，不会出现「只有 4 帧」的半空轮播。
    artists = _pick_entities(db, Artist, music_video_artists, "artist_id", limit)
    groups = _pick_entities(db, Group, music_video_groups, "group_id", limit)
    pool: list[tuple[str, Any]] = []
    ai = gi = 0
    take_artist = random.random() < 0.5
    while len(pool) < limit and (ai < len(artists) or gi < len(groups)):
        if take_artist and ai < len(artists):
            pool.append(("artist", artists[ai]))
            ai += 1
        elif not take_artist and gi < len(groups):
            pool.append(("group", groups[gi]))
            gi += 1
        elif ai < len(artists):
            pool.append(("artist", artists[ai]))
            ai += 1
        else:
            pool.append(("group", groups[gi]))
            gi += 1
        take_artist = not take_artist
    random.shuffle(pool)

    artist_counts = _video_counts(db, "music_video_artists", "artist_id")
    group_counts = _video_counts(db, "music_video_groups", "group_id")

    items: list[HeroStageItem] = []
    for etype, obj in pool:
        try:
            ensure_entity_focus(db, obj)
        except Exception:  # noqa: BLE001
            pass
        counts = artist_counts if etype == "artist" else group_counts
        items.append(
            HeroStageItem(
                type=etype,
                id=obj.id,
                uid=obj.uid,
                name=obj.name or "",
                chinese_name=obj.chinese_name,
                english_name=getattr(obj, "english_name", None),
                korean_name=getattr(obj, "korean_name", None),
                stage_name=getattr(obj, "stage_name", None),
                group_type=getattr(obj, "group_type", None),
                tagline=obj.tagline,
                description=_desc_snippet(obj.description),
                social_media=_normalize_social(obj.social_media),
                focus_x=obj.focus_x,
                focus_y=obj.focus_y,
                has_banner=bool(obj.banner_path),
                has_avatar=bool(obj.avatar_path),
                video_count=counts.get(obj.id, 0),
                updated_at=obj.updated_at,
            )
        )
    return HeroStageResult(items=items)


@router.get(
    "/hero-stage/{entity_type}/{entity_id}/fancams",
    response_model=HeroFancamResult,
)
def hero_stage_fancams(
    entity_type: str,
    entity_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(4, ge=1, le=8),
):
    """当前主舞台实体的直拍卡片：官方直拍优先，最多 4 条。无则空列表。"""
    et = (entity_type or "").strip().lower()
    if et not in ("artist", "group"):
        raise HTTPException(400, "type 仅支持 artist / group")
    model = Artist if et == "artist" else Group
    obj = db.scalar(select(model).where(model.id == entity_id, model.deleted_at.is_(None)))
    if obj is None:
        raise HTTPException(404, "艺人/组合不存在或已删除")

    rows = _query_entity_fancams(db, et, entity_id, limit)
    items: list[HeroFancamItem] = []
    for mv in rows:
        _, types = normalize_video_types(mv.video_types, mv.video_type)
        items.append(
            HeroFancamItem(
                id=mv.id,
                uid=mv.uid,
                name=mv.name or "",
                video_type=mv.video_type,
                video_types=types,
                thumbnail_path=mv.thumbnail_path,
                file_hash=mv.file_hash,
                duration=mv.duration,
                performance_date=mv.performance_date.isoformat()
                if mv.performance_date
                else None,
            )
        )
    return HeroFancamResult(items=items)
