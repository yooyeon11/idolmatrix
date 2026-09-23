"""视频类型归一 + 分辨率分档：视频元数据的共享口径。"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

COVER_VIDEO_TYPE = "CoverStage"
MIX_VIDEO_TYPE = "MixEdit"
SHORT_VIDEO_TYPE = "ShortVideo"
COLLAB_VIDEO_TYPE = "CollabStage"

# ===== 分辨率分档 =====
# 按「短边」判定：竖屏直拍是 1080×1920，若按 height 判会误算成 4K，故取 min(width, height)。
# 顺序即展示顺序（高清 → 低清），空档不返回给前端。
# 低清不合并成笼统的「标清」：360P / 240P 各自成档，只有比 240P 更低的（144P 等）
# 才落到 sd 兜底档（前端标签「<240P」）—— 识别结果要能跟原始分辨率对上。
RESOLUTION_TIERS: tuple[tuple[str, int], ...] = (
    ("8k", 4000),
    ("4k", 1800),
    ("2k", 1400),
    ("1080p", 1000),
    ("720p", 700),
    ("480p", 400),
    ("360p", 360),
    ("240p", 240),
    ("sd", 1),
)
RESOLUTION_UNKNOWN = "unknown"


def resolution_tier(width: Any, height: Any) -> str:
    """把宽高归入分辨率档位；缺字段/非法值返回 unknown。

    必须宽高齐全才归档：只有单边时无法判断短边，归 unknown 比猜一个档位更诚实。
    """
    try:
        w = int(width or 0)
        h = int(height or 0)
    except (TypeError, ValueError):
        return RESOLUTION_UNKNOWN
    if w <= 0 or h <= 0:
        return RESOLUTION_UNKNOWN
    short = min(w, h)
    for key, floor in RESOLUTION_TIERS:
        if short >= floor:
            return key
    return RESOLUTION_UNKNOWN


def resolution_ranges() -> list[tuple[str, int, Optional[int]]]:
    """(档位, 短边下限, 短边上限[不含])，上限 None = 无上限。

    供 SQL 侧过滤复用同一套档位：短边 >= lo 等价于 width/height 都 >= lo，
    短边 < hi 等价于 width/height 任一 < hi —— 不必依赖方言特有的 least()/min()。
    """
    out: list[tuple[str, int, Optional[int]]] = []
    for i, (key, floor) in enumerate(RESOLUTION_TIERS):
        upper = RESOLUTION_TIERS[i - 1][1] if i > 0 else None
        out.append((key, floor, upper))
    return out


def normalize_video_types(
    video_types: Optional[Iterable[str]] = None,
    video_type: Optional[str] = None,
) -> tuple[str, list[str]]:
    types: list[str] = []
    for raw in video_types or []:
        t = (raw or "").strip()
        if t and t not in types:
            types.append(t)
    primary = (video_type or "").strip() or None
    if primary and primary not in types:
        types.insert(0, primary)
    if not types:
        types = ["Other"]
    return types[0], types


def types_contain(stored_types: Optional[Sequence[str]], stored_primary: Optional[str], wanted: list[str]) -> bool:
    have = list(stored_types or [])
    if stored_primary and stored_primary not in have:
        have.insert(0, stored_primary)
    return any(t in have for t in wanted)


def is_short_video(
    video_types: Optional[Iterable[str]] = None,
    video_type: Optional[str] = None,
) -> bool:
    """短视频判定唯一口径：**只看视频类型是否含 ShortVideo**，与时长无关。

    历史实现用「类型含 ShortVideo 或时长 < 70 秒」兜底，会把 70 秒以内的
    MIX 混剪 / 预告 / 片段强行标成短视频，于是它们出现在短视频列表里。
    短视频列表的准入从此只认类型（v3.2.32）。
    """
    _, types = normalize_video_types(video_types, video_type)
    return SHORT_VIDEO_TYPE in types


def _person_label(entity: Any) -> Optional[str]:
    if entity is None or getattr(entity, "deleted_at", None) is not None:
        return None
    chinese = getattr(entity, "chinese_name", None)
    if chinese:
        return str(chinese)
    stage = getattr(entity, "stage_name", None)
    if stage:
        return str(stage)
    name = getattr(entity, "name", None)
    return str(name) if name else None


def cover_from_names(mv: Any) -> list[str]:
    """翻唱视频的原唱展示名：从曲目歌曲的主唱 / 发行主体推导，不建视频级关联。

    非翻唱类型返回空列表。歌曲未填艺人关系且没有发行主体时也返回空。
    """
    _, types = normalize_video_types(
        getattr(mv, "video_types", None), getattr(mv, "video_type", None)
    )
    if COVER_VIDEO_TYPE not in types:
        return []

    songs: list[Any] = []
    rows = list(getattr(mv, "video_tracks", None) or [])
    rows.sort(key=lambda r: getattr(r, "position", 0) or 0)
    for row in rows:
        song = getattr(row, "song", None)
        if song is not None and getattr(song, "deleted_at", None) is None:
            songs.append(song)
    if not songs:
        song = getattr(mv, "song", None)
        if song is not None and getattr(song, "deleted_at", None) is None:
            songs.append(song)

    names: list[str] = []
    seen: set[str] = set()

    def add(label: Optional[str]) -> None:
        if label and label not in seen:
            seen.add(label)
            names.append(label)

    pending_release: list[tuple[str, int]] = []
    for song in songs:
        rels = list(getattr(song, "artist_relations", None) or [])
        primary = [r for r in rels if (getattr(r, "role", None) or "PrimaryArtist") == "PrimaryArtist"]
        use = primary or [
            r for r in rels if getattr(r, "role", None) not in ("CoverArtist", "Remixer")
        ]
        hit = False
        for rel in sorted(use, key=lambda r: getattr(r, "order", 0) or 0):
            label = _person_label(getattr(rel, "artist", None)) or _person_label(
                getattr(rel, "group", None)
            )
            if label:
                add(label)
                hit = True
        if not hit:
            rtype = getattr(song, "release_artist_type", None)
            rid = getattr(song, "release_artist_id", None)
            if rtype and rid:
                pending_release.append((str(rtype), int(rid)))

    if pending_release:
        from sqlalchemy.orm import object_session

        from app.models.artist import Artist
        from app.models.group import Group

        session = object_session(mv)
        if session is not None:
            for rtype, rid in pending_release:
                if rtype == "artist":
                    add(_person_label(session.get(Artist, rid)))
                elif rtype == "group":
                    add(_person_label(session.get(Group, rid)))
    return names
