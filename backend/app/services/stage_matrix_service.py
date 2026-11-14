"""打歌期三维矩阵：同曲（或同活动）视频按 场次 × 成员 × 机位 分组。

供视频详情页「打歌场次 & 机位切换」舱使用：
- 第 1 维 场次 = (performance_date, event_name)：全曲历史分场（无日期/无活动名各自成组）
- 第 2 维 member：subject_artist > music_video_artists(唯一) > participants > 全团
- 第 3 维 camera：video_type + 横竖屏归类（facecam/mv/横竖直拍/舞台/OneTake/其他）

候选池口径与列表页一致：library + 未软删除 + 排除翻唱（CoverStage）。
⚠ 不再做 ±N「打歌期」窗口裁剪（2026-10-27 业主口径：窗口没有用处）——
    滚动窗口会让**同一首歌的不同视频看到不同的池子**（Baby Flower 65 条被切成 52 / 13 两段互不相见）。
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import cast, func, or_, select, String
from sqlalchemy.orm import Session, selectinload

from app.models.artist import Artist
from app.models.music_video import MusicVideo, MusicVideoTrack
from app.models.song import Song

# 「场次与机位」只收录这 5 种主类型；ShortVideo / SpecialVideo / Teaser / MixEdit / Other
# 一律不入池（2026-10-26 业主口径：按视频类型区分，不再看机位/名称）
_ALLOWED_PRIMARY_TYPES = (
    "PerformanceVideo",
    "Fancam",
    "OfficialMV",
    "SpecialStage",
    "CollabStage",
)

# 机位分类（响应 camera 字段；原始 video_types 一并返回供前端二次归类）
CAMERA_FACECAM = "facecam"
CAMERA_MV = "mv"
CAMERA_LANDSCAPE_FANCAM = "landscape_fancam"
CAMERA_PORTRAIT_FANCAM = "portrait_fancam"
CAMERA_STAGE = "stage"
CAMERA_ONETAKE = "onetake"
CAMERA_OTHER = "other"


def classify_camera(mv: MusicVideo) -> str:
    """机位：**优先用入库时录入的 `camera_angle`**（权威词表），为空才回落到
    video_type + 横竖屏的旧启发式。

    回落分支保留的原因：2026-10 之前的存量条目没有 camera_angle，
    直接改成「空就是 other」会让老视频的机位胶囊全部消失。
    词表外写法（`Tower Cam` / `K-Choreo` …）由前端 CAMERA_LABEL 兜底渲染中文名。
    """
    real = (getattr(mv, "camera_angle", None) or "").strip()
    if real:
        return real

    w = int(mv.width or 0)
    h = int(mv.height or 0)
    portrait = h > w > 0
    primary = mv.video_type or ""
    types = set(mv.video_types or [])
    if primary == "OfficialMV" or "OfficialMV" in types:
        return CAMERA_MV
    if primary == "PerformanceVideo" or "PerformanceVideo" in types:
        return CAMERA_STAGE
    if primary == "SpecialStage" or "SpecialStage" in types:
        return CAMERA_STAGE
    if "Fancam" in types or primary == "Fancam":
        return CAMERA_PORTRAIT_FANCAM if portrait else CAMERA_LANDSCAPE_FANCAM
    return CAMERA_OTHER


def _orientation(mv: MusicVideo) -> str:
    w = int(mv.width or 0)
    h = int(mv.height or 0)
    if w <= 0 or h <= 0:
        return "unknown"
    return "portrait" if h > w else "landscape"


def _primary_song_id(mv: MusicVideo) -> Optional[int]:
    """主曲 id：video_tracks 第一首优先，回退派生列 song_id。"""
    vtracks = list(getattr(mv, "video_tracks", None) or [])
    if vtracks:
        sid = getattr(vtracks[0], "song_id", None)
        if sid:
            return int(sid)
    return getattr(mv, "song_id", None)


def _member_row(mv: MusicVideo, artist_map: dict) -> dict:
    """成员维度（可信度降序）：subject_artist > 唯一关联艺人 > participants > 全团。"""
    subject = getattr(mv, "subject_artist", None)
    if subject is not None and getattr(subject, "deleted_at", None) is None:
        return {
            "kind": "artist",
            "id": subject.id,
            "uid": subject.uid,
            "name": subject.chinese_name or subject.name,
            "avatar_path": subject.avatar_path,
            "source": "subject_artist",
            "confidence": "high",
        }

    artists = [a for a in (getattr(mv, "artists", None) or []) if a.deleted_at is None]
    if len(artists) == 1:
        a = artists[0]
        return {
            "kind": "artist",
            "id": a.id,
            "uid": a.uid,
            "name": a.chinese_name or a.name,
            "avatar_path": a.avatar_path,
            "source": "video_artists",
            "confidence": "medium",
        }

    parts = getattr(mv, "participants", None) or []
    first = next((p for p in parts if isinstance(p, dict) and p.get("id")), None)
    if first is not None:
        a = artist_map.get(int(first["id"]))
        if a is not None:
            return {
                "kind": "artist",
                "id": a.id,
                "uid": a.uid,
                "name": a.chinese_name or a.name,
                "avatar_path": a.avatar_path,
                "source": "participants",
                "confidence": "low",
            }
        return {
            "kind": "artist",
            "id": None,
            "uid": None,
            "name": str(first.get("name") or "未知成员"),
            "avatar_path": None,
            "source": "participants",
            "confidence": "low",
        }

    groups = [g for g in (getattr(mv, "groups", None) or []) if g.deleted_at is None]
    if groups:
        g = groups[0]
        return {
            "kind": "group",
            "id": g.id,
            "uid": g.uid,
            "name": g.chinese_name or g.name,
            "avatar_path": g.avatar_path,
            "source": "group",
            "confidence": "high",
        }
    return {
        "kind": "group",
        "id": None,
        "uid": None,
        "name": "全团",
        "avatar_path": None,
        "source": "group",
        "confidence": "low",
    }


def _cover_clause():
    """翻唱判定，与 app.api.music_videos._is_cover_clause 同口径（延迟导入避免环）。

    video_types 为 NULL 的行 cast 后 ilike 得 NULL，~NULL 会误杀整行，
    必须 coalesce 成空串再匹配。
    """
    return or_(
        MusicVideo.video_type == "CoverStage",
        func.coalesce(cast(MusicVideo.video_types, String), "").ilike('%"CoverStage"%'),
    )


def _event_key(name: str) -> str:
    """活动名归一化：**仅用于分组比较，不改展示值**。

    折叠大小写与多余空白，避免「SBS Inkigayo」和「SBS  inkigayo」被拆成两场。
    """
    return " ".join((name or "").split()).casefold()


def _slot_key(day: Optional[date], norm: str) -> str:
    """场次的稳定键（前端下拉的 value）：日期 + 归一化活动名。

    不能只给日期 —— 同一天可能有多场，只凭日期选不中第二场。
    """
    return f"{day.isoformat() if day else 'none'}|{norm}"


def build_stage_matrix(db: Session, mv: MusicVideo) -> dict:
    """构建整首歌的三维矩阵；数据不足返回 available=False。

    候选池**只按同一首歌**收录（业主 2026-10-26 口径）：
    不再用「同活动名 + 同团体」兜底，否则同场别的歌曲也会混进来。
    **不按日期窗口裁剪**（2026-10-27）：同一首歌无论从哪条视频打开，池子完全相同。
    """
    anchor_song_id = _primary_song_id(mv)
    if not anchor_song_id:
        return {"available": False, "song": None, "group": None, "days": []}

    stmt = (
        select(MusicVideo)
        .options(
            selectinload(MusicVideo.artists),
            selectinload(MusicVideo.groups),
            selectinload(MusicVideo.subject_artist),
            selectinload(MusicVideo.video_tracks),
        )
        .where(
            MusicVideo.deleted_at.is_(None),
            MusicVideo.ingestion_status == "library",
            ~_cover_clause(),
            # 只按主类型收录（ShortVideo 等自然被排除）
            MusicVideo.video_type.in_(_ALLOWED_PRIMARY_TYPES),
            or_(
                MusicVideo.song_id == anchor_song_id,
                MusicVideo.video_tracks.any(MusicVideoTrack.song_id == anchor_song_id),
                MusicVideo.songs.any(Song.id == anchor_song_id),
            ),
        )
    )

    pool = db.scalars(
        stmt.order_by(MusicVideo.performance_date.is_(None), MusicVideo.performance_date.asc(), MusicVideo.name)
    ).all()

    # 参与者 id → 艺人（解析 participants 快照用）
    part_ids: set[int] = set()
    for v in pool:
        for p in getattr(v, "participants", None) or []:
            if isinstance(p, dict) and p.get("id"):
                part_ids.add(int(p["id"]))
    artist_map = (
        {
            a.id: a
            for a in db.scalars(select(Artist).where(Artist.id.in_(part_ids))).all()
            if a.deleted_at is None
        }
        if part_ids
        else {}
    )

    # 场次 key = (演出日, 归一化活动名)。同一天真会有多场 —— 例如 2026-06-07 既有
    # SBS Inkigayo 又有 Everline Fansign；旧口径只按日期分组，会把它们并成一场，
    # 组名再取「按标题排序后第一个非空 event_name」，于是 fansign 被标成了人气歌谣。
    slots: dict[tuple[Optional[date], str], list] = {}
    slot_names: dict[tuple[Optional[date], str], str] = {}
    for v in pool:
        raw = (v.event_name or "").strip()
        slot_key = (v.performance_date, _event_key(raw))
        # 同一场次内活动名的多种写法，展示值取首次出现的那一个
        slot_names.setdefault(slot_key, raw)
        slots.setdefault(slot_key, []).append(
            {
                "id": v.id,
                "uid": v.uid,
                "name": v.name,
                "duration": v.duration,
                "width": v.width,
                "height": v.height,
                "orientation": _orientation(v),
                "camera": classify_camera(v),
                "video_type": v.video_type or "",
                "video_types": list(v.video_types or ([v.video_type] if v.video_type else [])),
                "member": _member_row(v, artist_map),
                "current": v.id == mv.id,
            }
        )

    # 同一天多场的先后：有活动名的在前、未标注沉底；同日按机位数降序（主活动在前）
    ordered = sorted(
        slots.items(),
        key=lambda kv: (
            kv[0][0] is None,
            kv[0][0] or date.min,
            0 if slot_names[kv[0]] else 1,
            -len(kv[1]),
            slot_names[kv[0]].casefold(),
        ),
    )
    day_rows = [
        {
            "key": _slot_key(slot_key[0], slot_key[1]),
            "date": slot_key[0],
            "event_name": slot_names[slot_key] or None,
            "videos": videos,
        }
        for slot_key, videos in ordered
    ]

    total = sum(len(d["videos"]) for d in day_rows)
    song_brief = None
    if anchor_song_id:
        song = db.get(Song, anchor_song_id)
        if song is not None and song.deleted_at is None:
            song_brief = {"id": song.id, "name": song.name, "chinese_name": song.chinese_name}
    group = None
    groups = [g for g in (getattr(mv, "groups", None) or []) if g.deleted_at is None]
    if groups:
        group = {
            "id": groups[0].id,
            "uid": groups[0].uid,
            "name": groups[0].chinese_name or groups[0].name,
            "avatar_path": groups[0].avatar_path,
        }

    return {
        "available": total > 1,
        "song": song_brief,
        "group": group,
        "days": day_rows,
    }
