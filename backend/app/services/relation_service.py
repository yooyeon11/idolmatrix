"""歌曲-专辑关联（album_tracks）自动同步。"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.album import Album, AlbumTrack
from app.models.base import resolve_active_ids
from app.models.music_video import MusicVideo, MusicVideoTrack, music_video_track_albums
from app.models.song import Song


def _collect_video_pairs(mv: Any) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for row in list(getattr(mv, "video_tracks", None) or []):
        sid = getattr(row, "song_id", None)
        if not sid:
            continue
        for alb in getattr(row, "albums", None) or []:
            aid = getattr(alb, "id", None)
            if aid:
                pairs.add((int(sid), int(aid)))
    return pairs


def prune_unreferenced_song_albums(
    db: Session, pairs: Iterable[tuple[int, int]], except_video_id: int
) -> int:
    """视频上拿掉的 (歌, 专辑)，若没有其它活跃视频还在用，就从目录里删掉。"""
    removed = 0
    for sid, aid in pairs:
        still = db.scalar(
            select(MusicVideoTrack.id)
            .join(
                music_video_track_albums,
                music_video_track_albums.c.track_id == MusicVideoTrack.id,
            )
            .join(MusicVideo, MusicVideo.id == MusicVideoTrack.music_video_id)
            .where(
                MusicVideoTrack.song_id == sid,
                music_video_track_albums.c.album_id == aid,
                MusicVideoTrack.music_video_id != except_video_id,
                MusicVideo.deleted_at.is_(None),
            )
            .limit(1)
        )
        if still:
            continue
        row = db.scalar(
            select(AlbumTrack).where(
                AlbumTrack.song_id == sid, AlbumTrack.album_id == aid
            )
        )
        if row is None:
            continue
        db.delete(row)
        removed += 1
    if removed:
        db.flush()
    return removed


def ensure_song_album_links(
    db: Session, song_ids: Iterable[int], album_ids: Iterable[int]
) -> int:
    """确保每对 (song, album) 都有 album_tracks 记录，返回新建数量。"""
    sids = list(dict.fromkeys(int(s) for s in song_ids))
    aids = list(dict.fromkeys(int(a) for a in album_ids))
    if not sids or not aids:
        return 0
    existing = set(
        db.execute(
            select(AlbumTrack.song_id, AlbumTrack.album_id).where(
                AlbumTrack.song_id.in_(sids), AlbumTrack.album_id.in_(aids)
            )
        ).all()
    )
    created = 0
    for sid in sids:
        for aid in aids:
            if (sid, aid) in existing:
                continue
            max_tn = (
                db.scalar(
                    select(func.max(AlbumTrack.track_number)).where(
                        AlbumTrack.album_id == aid
                    )
                )
                or 0
            )
            db.add(
                AlbumTrack(
                    song_id=sid,
                    album_id=aid,
                    disc_number=1,
                    track_number=max_tn + 1,
                )
            )
            created += 1
    if created:
        db.flush()
    return created


def _track_song_id(item: Any) -> Optional[int]:
    if isinstance(item, dict):
        sid = item.get("song_id")
    else:
        sid = getattr(item, "song_id", None)
    if sid is None:
        return None
    return int(sid)


def _track_album_ids(item: Any) -> list[int]:
    if isinstance(item, dict):
        raw = item.get("album_ids") or []
    else:
        raw = getattr(item, "album_ids", None) or []
    return list(dict.fromkeys(int(a) for a in raw if a))


def apply_video_song_tracks(
    db: Session,
    mv: Any,
    *,
    tracks: Optional[Iterable[Any]] = None,
    song_ids: Optional[Iterable[int]] = None,
    album_ids: Optional[Iterable[int]] = None,
) -> None:
    """按曲目行写入视频的歌曲，并把专辑挂到对应歌曲上（不打笛卡尔积）。

    tracks 优先，每项 {song_id, album_ids}：只把该行的专辑连到该行的歌。
    无 tracks 时：一首歌 + 多专辑、或多首歌 + 一张专辑，才写 catalog；
    多歌多专辑的平行列表不再交叉相乘。
    视频上的 albums 由本视频曲目行派生。
    song_id / songs 只作曲目表的派生缓存，不作为独立来源。
    """
    old_pairs = _collect_video_pairs(mv)
    mv_id = getattr(mv, "id", None)
    pairs: list[tuple[int, list[int]]] = []
    ordered_song_ids: list[int] = []

    if tracks is not None:
        for item in tracks:
            sid = _track_song_id(item)
            if not sid:
                continue
            if sid not in ordered_song_ids:
                ordered_song_ids.append(sid)
            pairs.append((sid, _track_album_ids(item)))
    else:
        ordered_song_ids = list(
            dict.fromkeys(int(s) for s in (song_ids or []) if s)
        )
        if not ordered_song_ids and getattr(mv, "song_id", None):
            ordered_song_ids = [int(mv.song_id)]
        aids = list(dict.fromkeys(int(a) for a in (album_ids or []) if a))
        if len(ordered_song_ids) == 1 and aids:
            pairs = [(ordered_song_ids[0], aids)]
        elif len(aids) == 1 and ordered_song_ids:
            pairs = [(sid, list(aids)) for sid in ordered_song_ids]

    mv.songs = resolve_active_ids(db, Song, ordered_song_ids)
    if ordered_song_ids:
        mv.song_id = ordered_song_ids[0]
    else:
        mv.song_id = None

    mv.video_tracks.clear()
    db.flush()
    derived: list[int] = []
    seen: set[int] = set()
    for pos, (sid, aids) in enumerate(pairs):
        row = MusicVideoTrack(song_id=sid, position=pos)
        row.albums = resolve_active_ids(db, Album, aids)
        mv.video_tracks.append(row)
        if aids:
            ensure_song_album_links(db, [sid], aids)
        for aid in aids:
            if aid not in seen:
                seen.add(aid)
                derived.append(aid)
    mv.albums = resolve_active_ids(db, Album, derived)
    if mv_id:
        new_pairs = {(sid, aid) for sid, aids in pairs for aid in aids}
        prune_unreferenced_song_albums(db, old_pairs - new_pairs, int(mv_id))
