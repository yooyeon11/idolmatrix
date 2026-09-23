"""人名归一化与艺人查重。

把空格 / 连字符 / 大小写差异当成同一个人（JEONG SAEBI ≡ JEONG SAE BI），
并在 name / stage_name / 各语言名 / aliases 上匹配。
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.artist import Artist

# 保留字母数字、汉字、假名、韩文；其余（空格、连字符、中间点等）丢掉
_KEEP = re.compile(r"[^0-9a-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+")
FUZZY_THRESHOLD = 0.78
EXACT_SCORE = 0.999


def normalize_person_name(value: Optional[str]) -> str:
    if not value or not str(value).strip():
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    return _KEEP.sub("", text)


def artist_name_variants(artist: Artist) -> List[str]:
    vals: List[Optional[str]] = [
        artist.name,
        artist.stage_name,
        artist.chinese_name,
        artist.english_name,
        artist.korean_name,
    ]
    if isinstance(artist.aliases, list):
        vals.extend(a for a in artist.aliases if isinstance(a, str))
    return [v.strip() for v in vals if v and str(v).strip()]


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
        return 0.95
    return difflib.SequenceMatcher(None, a, b).ratio()


def score_artist(query: str, artist: Artist) -> float:
    needle = normalize_person_name(query)
    if not needle:
        return 0.0
    best = 0.0
    for raw in artist_name_variants(artist):
        best = max(best, _similarity(needle, normalize_person_name(raw)))
        if best >= 1.0:
            return 1.0
    return best


def match_artist_exact(
    db: Session, *names: Optional[str], extra: Optional[Iterable[str]] = None
) -> Optional[Artist]:
    """归一化后完全相同则命中；多个候选名任一命中即可。"""
    hits = match_artist_candidates(db, *names, extra=extra)
    return hits[0] if hits else None


def match_artist_candidates(
    db: Session, *names: Optional[str], extra: Optional[Iterable[str]] = None
) -> List[Artist]:
    """与 match_artist_exact 同规则，但返回全部命中候选。

    组合成员导入场景：同名成员在多个组合各自成条是常态，
    调用方需要拿到所有同名艺人而不是任意第一个。
    """
    needles = {normalize_person_name(n) for n in names if n}
    if extra:
        needles.update(normalize_person_name(n) for n in extra)
    needles.discard("")
    if not needles:
        return []
    hits: List[Artist] = []
    for artist in db.scalars(select(Artist).where(Artist.active_filter())):
        variants = {normalize_person_name(v) for v in artist_name_variants(artist)}
        if needles & variants:
            hits.append(artist)
    return hits


def fuzzy_artists(
    db: Session, query: str, *, limit: int = 8, threshold: float = FUZZY_THRESHOLD
) -> List[Tuple[Artist, float]]:
    needle = normalize_person_name(query)
    if not needle:
        return []
    hits: List[Tuple[Artist, float]] = []
    for artist in db.scalars(select(Artist).where(Artist.active_filter())):
        score = score_artist(query, artist)
        if score >= threshold:
            hits.append((artist, score))
    hits.sort(key=lambda x: x[1], reverse=True)
    return hits[:limit]


def _object_name_variants(obj: object, attrs: Sequence[str]) -> List[str]:
    vals: List[str] = []
    for attr in attrs:
        value = getattr(obj, attr, None)
        if isinstance(value, list):
            vals.extend(a.strip() for a in value if isinstance(a, str) and a.strip())
        elif value and str(value).strip():
            vals.append(str(value).strip())
    return vals


def score_named(query: str, obj: object, attrs: Sequence[str]) -> float:
    needle = normalize_person_name(query)
    if not needle:
        return 0.0
    best = 0.0
    for raw in _object_name_variants(obj, attrs):
        best = max(best, _similarity(needle, normalize_person_name(raw)))
        if best >= 1.0:
            return 1.0
    return best


def fuzzy_named(
    db: Session,
    model,
    query: str,
    attrs: Sequence[str],
    *,
    limit: int = 8,
    threshold: float = FUZZY_THRESHOLD,
) -> List[Tuple[object, float]]:
    needle = normalize_person_name(query)
    if not needle:
        return []
    hits: List[Tuple[object, float]] = []
    for row in db.scalars(select(model).where(model.active_filter())):
        score = score_named(query, row, attrs)
        if score >= threshold:
            hits.append((row, score))
    hits.sort(key=lambda x: x[1], reverse=True)
    return hits[:limit]


SONG_NAME_ATTRS = ("name", "chinese_name", "english_name", "korean_name", "aliases")
GROUP_NAME_ATTRS = ("name", "chinese_name", "english_name", "korean_name", "aliases")


def normalize_song_name(value: Optional[str]) -> str:
    """歌名归一化：与人名同一规则（NFKC + casefold + 丢掉非字母数字/汉字/假名/韩文）。

    注意不剥离括号后缀：UP (Inst.) 归一化为 upinst，与 UP 保持不同，
    避免 Remix/Inst./Acca 版本被误并成同一首歌。
    """
    return normalize_person_name(value)


def match_song_exact(
    db: Session,
    name: Optional[str],
    *,
    extra_names: Optional[Iterable[str]] = None,
    duration: Optional[int] = None,
    release_artist_type: Optional[str] = None,
    release_artist_id: Optional[int] = None,
    duration_tolerance: int = 3,
) -> Optional["object"]:
    """按归一化歌名精确匹配库内歌曲，用于曲目导入查重。

    三信号消歧（保守策略，宁返回 None 新建，不静默合并错歌）：
      1. 歌名归一化后必须与 name/各语言名/alias 之一完全一致；
      2. 提供发行主体时优先主体一致者；库内同名歌都有明确的
         其他发行主体则视为不同歌（翻唱/同名），直接不匹配；
      3. 候选仍多条时用时长（±duration_tolerance 秒）收窄，仍不唯一则放弃。
    """
    from app.models.song import Song

    needles = {normalize_song_name(name)}
    needles.update(normalize_song_name(n) for n in (extra_names or ()))
    needles.discard("")
    if not needles:
        return None
    pool = []
    for song in db.scalars(select(Song).where(Song.active_filter())):
        variants = {normalize_song_name(v) for v in _object_name_variants(song, SONG_NAME_ATTRS)}
        if needles & variants:
            pool.append(song)
    if not pool:
        return None
    if release_artist_type and release_artist_id:
        same_artist = [
            s
            for s in pool
            if s.release_artist_type == release_artist_type
            and s.release_artist_id == release_artist_id
        ]
        if same_artist:
            pool = same_artist
        elif any(s.release_artist_type and s.release_artist_id for s in pool):
            return None
    if len(pool) == 1:
        return pool[0]
    if duration:
        near = [
            s for s in pool
            if s.duration and abs(s.duration - duration) <= duration_tolerance
        ]
        if len(near) == 1:
            return near[0]
    return None


def fuzzy_songs(db: Session, query: str, *, limit: int = 8):
    from app.models.song import Song

    return fuzzy_named(db, Song, query, SONG_NAME_ATTRS, limit=limit)


def fuzzy_groups(db: Session, query: str, *, limit: int = 8):
    from app.models.group import Group

    return fuzzy_named(db, Group, query, GROUP_NAME_ATTRS, limit=limit)


def normalize_ingest_match_mode(value: Optional[str]) -> str:
    """入库匹配档归一化。

    ⚠ 这里**有意**保留 normal：产品层已经把自动关联策略固定成 strict
    （见 schemas/app_settings.py::INGEST_MATCH_MODES，存量库的 normal 会被改写），
    但宽匹配算法本身仍在（build_match_hints 的 normal 分支 + 单测依赖），
    保留是为了将来能一键回退，不要把这里的 normal 当漏改删掉。
    """
    return "strict" if value == "strict" else "normal"


def related_group_family_ids(db: Session, group_ids: Iterable[int]) -> set[int]:
    """已识别组合的关系闸范围：自身 + 旗下小分队 + 直接母队。

    不含兄弟小分队（识别到 EVOlution 时不自动纳入 NXT）。
    """
    from app.models.group import Group
    from app.services.group_tree import expand_group_ids

    out: set[int] = set()
    for gid in group_ids:
        if not gid:
            continue
        out.update(expand_group_ids(db, gid))
        group = db.get(Group, gid)
        if (
            group is not None
            and getattr(group, "deleted_at", None) is None
            and group.parent_group_id
        ):
            parent = db.get(Group, group.parent_group_id)
            if parent is not None and getattr(parent, "deleted_at", None) is None:
                out.add(parent.id)
    return out


def related_artist_ids_for_groups(db: Session, group_ids: Iterable[int]) -> set[int]:
    """关系闸内的艺人 id（含历任；已软删艺人排除）。"""
    from app.models.membership import GroupMembership

    gids = related_group_family_ids(db, group_ids)
    if not gids:
        return set()
    return set(
        db.scalars(
            select(GroupMembership.artist_id)
            .join(Artist, Artist.id == GroupMembership.artist_id)
            .where(
                GroupMembership.group_id.in_(gids),
                Artist.deleted_at.is_(None),
            )
        )
    )


def merge_artist_aliases(artist: Artist, extras: Sequence[str]) -> bool:
    """把尚未出现过的写法写入 aliases。主名等已有字段不重复记。"""
    have = {normalize_person_name(v) for v in artist_name_variants(artist)}
    existing = [a for a in (artist.aliases or []) if isinstance(a, str) and a.strip()]
    changed = False
    for raw in extras:
        text = (raw or "").strip()
        key = normalize_person_name(text)
        if not text or not key or key in have:
            continue
        existing.append(text)
        have.add(key)
        changed = True
    if changed:
        artist.aliases = existing
    return changed


# ===== 专辑曲目导入判定（三态：link / new / review） =====

# 主体关联命中但时长差超过该值（秒）→ 降级存疑（防同艺人同名重录版/英文版）
LINK_DURATION_GUARD = 10


def song_related_video_ids(db: Session, song_ids: Sequence[int]) -> dict[int, set[int]]:
    """歌曲关联视频 id：直连 FK / M2M / 曲目行三口径合并去重，仅活跃视频。"""
    from app.models.music_video import (
        MusicVideo,
        MusicVideoTrack,
        music_video_songs,
    )

    out: dict[int, set[int]] = {sid: set() for sid in song_ids}
    if not song_ids:
        return out
    for sid, vid in db.execute(
        select(MusicVideo.song_id, MusicVideo.id).where(
            MusicVideo.deleted_at.is_(None), MusicVideo.song_id.in_(song_ids)
        )
    ):
        if sid is not None:
            out[sid].add(vid)
    for sid, vid in db.execute(
        select(music_video_songs.c.song_id, music_video_songs.c.music_video_id)
        .join(MusicVideo, MusicVideo.id == music_video_songs.c.music_video_id)
        .where(
            MusicVideo.deleted_at.is_(None), music_video_songs.c.song_id.in_(song_ids)
        )
    ):
        out[sid].add(vid)
    for sid, vid in db.execute(
        select(MusicVideoTrack.song_id, MusicVideoTrack.music_video_id)
        .join(MusicVideo, MusicVideo.id == MusicVideoTrack.music_video_id)
        .where(
            MusicVideo.deleted_at.is_(None), MusicVideoTrack.song_id.in_(song_ids)
        )
    ):
        out[sid].add(vid)
    return out


def _subject_link_hit(
    db: Session,
    rel_rows: Sequence[object],
    video_ids: set[int],
    release_artist_type: Optional[str],
    release_artist_id: Optional[int],
) -> bool:
    """歌曲是否通过 SongArtistRelation 或关联视频佐证归属专辑主体。"""
    from app.models.music_video import music_video_artists, music_video_groups

    if not release_artist_type or not release_artist_id:
        return False
    if release_artist_type == "artist":
        if any(r.artist_id == release_artist_id for r in rel_rows):  # type: ignore[attr-defined]
            return True
    elif release_artist_type == "group":
        if any(r.group_id == release_artist_id for r in rel_rows):  # type: ignore[attr-defined]
            return True
    if not video_ids:
        return False
    if release_artist_type == "artist":
        from app.models.music_video import MusicVideo

        if db.scalar(
            select(MusicVideo.id)
            .where(
                MusicVideo.id.in_(video_ids),
                MusicVideo.subject_artist_id == release_artist_id,
            )
            .limit(1)
        ):
            return True
        return (
            db.scalar(
                select(music_video_artists.c.music_video_id)
                .where(
                    music_video_artists.c.music_video_id.in_(video_ids),
                    music_video_artists.c.artist_id == release_artist_id,
                )
                .limit(1)
            )
            is not None
        )
    return (
        db.scalar(
            select(music_video_groups.c.music_video_id)
            .where(
                music_video_groups.c.music_video_id.in_(video_ids),
                music_video_groups.c.group_id == release_artist_id,
            )
            .limit(1)
        )
        is not None
    )


def _has_other_subject_evidence(
    db: Session, rel_rows: Sequence[object], video_ids: set[int]
) -> bool:
    """歌曲是否带有「归属于其他主体」的证据（挂载关系或视频主体指向）。"""
    from app.models.music_video import MusicVideo, music_video_artists, music_video_groups

    if rel_rows:
        return True
    if not video_ids:
        return False
    if db.scalar(
        select(MusicVideo.id)
        .where(MusicVideo.id.in_(video_ids), MusicVideo.subject_artist_id.is_not(None))
        .limit(1)
    ):
        return True
    if db.scalar(
        select(music_video_artists.c.music_video_id)
        .where(music_video_artists.c.music_video_id.in_(video_ids))
        .limit(1)
    ):
        return True
    return (
        db.scalar(
            select(music_video_groups.c.music_video_id)
            .where(music_video_groups.c.music_video_id.in_(video_ids))
            .limit(1)
        )
        is not None
    )


def match_song_for_album_import(
    db: Session,
    name: Optional[str],
    *,
    duration: Optional[int] = None,
    release_artist_type: Optional[str] = None,
    release_artist_id: Optional[int] = None,
) -> Tuple[str, Optional[object], List[object]]:
    """专辑曲目导入查重三态判定。

    判定链（按信号强度递降）：
      1. link  —— 库内同名歌通过 SongArtistRelation 或关联视频佐证归属专辑主体，
                  且时长差不超 LINK_DURATION_GUARD → 自动关联；
      2. new   —— 候选明确挂着其他主体（关系或视频证据）→ 判定同名不同歌，新建；
      3. review —— 主体信号缺失或冲突 → 返回候选列表交人工确认。

    返回 (verdict, song, candidates)。
    """
    from app.models.song import Song, SongArtistRelation

    pool = []
    needle = normalize_song_name(name)
    if not needle:
        # 纯符号歌名（如 @%）归一化后为空：回退原始名去空格大小写精确比对
        raw = (name or "").strip().casefold()
        if not raw:
            return "new", None, []
        for song in db.scalars(select(Song).where(Song.active_filter())):
            if any(
                v.strip().casefold() == raw
                for v in _object_name_variants(song, SONG_NAME_ATTRS)
            ):
                pool.append(song)
        if not pool:
            return "new", None, []
    else:
        for song in db.scalars(select(Song).where(Song.active_filter())):
            variants = {normalize_song_name(v) for v in _object_name_variants(song, SONG_NAME_ATTRS)}
            if needle in variants:
                pool.append(song)
        if not pool:
            return "new", None, []

    vid_map = song_related_video_ids(db, [s.id for s in pool])
    rel_map: dict[int, list] = {}
    for rel in db.scalars(
        select(SongArtistRelation).where(
            SongArtistRelation.song_id.in_([s.id for s in pool])
        )
    ):
        rel_map.setdefault(rel.song_id, []).append(rel)

    def _close(s) -> bool:
        return bool(duration and s.duration and abs(s.duration - duration) <= 3)

    def _far(s) -> bool:
        return bool(
            duration and s.duration and abs(s.duration - duration) > LINK_DURATION_GUARD
        )

    if release_artist_type and release_artist_id:
        linked = [
            s
            for s in pool
            if _subject_link_hit(
                db, rel_map.get(s.id) or [], vid_map.get(s.id) or set(),
                release_artist_type, release_artist_id,
            )
        ]
        confident = [s for s in linked if not _far(s)]
        if confident:
            best = min(
                confident,
                key=lambda s: abs(s.duration - duration) if duration and s.duration else 0,
            )
            return "link", best, []
        if linked:
            # 主体命中但时长对不上：可能是同名重录版/英文版，交人工
            return "review", None, linked
        rest = [
            s
            for s in pool
            if not _has_other_subject_evidence(db, rel_map.get(s.id) or [], vid_map.get(s.id) or set())
        ]
        if rest:
            return "review", None, rest
        return "new", None, []

    # 专辑无发行主体：唯一候选且时长吻合（±3 秒）才自动关联，其余存疑
    close = [s for s in pool if _close(s)]
    if len(close) == 1:
        return "link", close[0], []
    return "review", None, pool
