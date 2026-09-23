"""为 AI 入库识别注入「本库候选」上下文。

裸名字（如只有韩文成员名「채영」或罗马音）超出纯文本模型的知识边界时，
AI 只能靠世界知识裸猜。把库内的组合/成员名单与按标题模糊匹配出的
艺人/歌曲候选注入提示词，识别任务从"凭记忆猜"变成"在候选集中做别名
匹配"，命中率更高，也避免建议新建重复条目。
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Album, Artist, Group
from app.models.membership import GroupMembership
from app.services.name_match import (
    fuzzy_artists,
    fuzzy_named,
    fuzzy_songs,
    normalize_ingest_match_mode,
)

MAX_GROUPS = 60
MAX_MATCHED = 10
# 每首命中歌曲最多带出的专辑数（一首歌通常只属 1~3 张专辑，防止膨胀）
MAX_ALBUMS_PER_SONG = 3
ALBUM_NAME_ATTRS = ("name", "chinese_name", "english_name", "korean_name", "aliases")


def _artist_line(a: Artist) -> str:
    parts = [a.name]
    for extra in (a.stage_name, a.chinese_name, getattr(a, "korean_name", None)):
        if extra and extra not in parts:
            parts.append(extra)
    return " / ".join(parts)


def _members_by_group(db: Session, group_ids: list[int]) -> dict[int, list[Artist]]:
    if not group_ids:
        return {}
    rows = db.execute(
        select(GroupMembership.group_id, Artist)
        .join(Artist, Artist.id == GroupMembership.artist_id)
        .where(GroupMembership.group_id.in_(group_ids), Artist.deleted_at.is_(None))
        .order_by(GroupMembership.group_id, GroupMembership.join_date, Artist.name)
    ).all()
    out: dict[int, list[Artist]] = {}
    for group_id, artist in rows:
        out.setdefault(group_id, []).append(artist)
    return out


def build_library_candidates(
    db: Session,
    query_text: Optional[str],
    *,
    limit: int = MAX_MATCHED,
    match_mode: Optional[str] = None,
    group_ids: Optional[list[int]] = None,
) -> dict[str, Any]:
    """构建库内候选上下文：全部组合（含成员）+ 按标题/文件名模糊匹配的艺人与歌曲。

    strict：不把标题切成碎片去 fuzzy；艺人候选来自标题独立命中，
    且若已识别组合（标题或表单）则只保留该团关系闸内的人。
    """
    out: dict[str, Any] = {}
    query = (query_text or "").strip()
    mode = normalize_ingest_match_mode(match_mode)
    is_strict = mode == "strict"

    groups = db.scalars(
        select(Group).where(Group.deleted_at.is_(None)).order_by(Group.name)
    ).all()
    members_by_group = _members_by_group(db, [g.id for g in groups[:MAX_GROUPS]])
    group_list: list[dict[str, Any]] = []
    for g in groups[:MAX_GROUPS]:
        members = members_by_group.get(g.id) or []
        group_list.append(
            {
                "name": g.name,
                "chinese_name": g.chinese_name or None,
                "members": [_artist_line(m) for m in members] or None,
            }
        )
    if group_list:
        out["groups"] = group_list

    if query:
        seen: set[int] = set()
        matched_artists: list[str] = []
        if is_strict:
            from app.services.incoming_match_service import build_match_hints
            from app.services.name_match import related_artist_ids_for_groups

            hints = build_match_hints(
                db, query, {"title": query}, match_mode="strict"
            )
            seed_groups = {i for i in (group_ids or []) if isinstance(i, int) and i}
            hint_group_ids = {g["id"] for g in hints.get("groups") or [] if g.get("id")}
            locked_groups = seed_groups | hint_group_ids
            allowed = (
                related_artist_ids_for_groups(db, locked_groups) if locked_groups else None
            )
            for row in hints.get("artists") or []:
                aid = row.get("id")
                if not isinstance(aid, int) or aid in seen:
                    continue
                if allowed is not None and aid not in allowed:
                    continue
                artist = db.get(Artist, aid)
                if artist is None or getattr(artist, "deleted_at", None) is not None:
                    continue
                seen.add(aid)
                matched_artists.append(_artist_line(artist))
                if len(matched_artists) >= limit:
                    break
        else:
            sources = [query, *[s for s in query.split() if len(s) >= 2]]
            for source in sources:
                for artist, _score in fuzzy_artists(db, source, limit=limit):
                    if artist.id in seen:
                        continue
                    seen.add(artist.id)
                    matched_artists.append(_artist_line(artist))
                    if len(matched_artists) >= limit:
                        break
                if len(matched_artists) >= limit:
                    break
        if matched_artists:
            out["matched_artists"] = matched_artists

        seen_songs: set[int] = set()
        matched_songs: list[str] = []
        matched_tracks: list[dict[str, Any]] = []
        song_sources = [query] if is_strict else [query, *[s for s in query.split() if len(s) >= 2]]
        for source in song_sources:
            for song, _score in fuzzy_songs(db, source, limit=limit):
                if song.id in seen_songs:
                    continue
                seen_songs.add(song.id)
                parts = [song.name]
                for extra in (song.chinese_name, getattr(song, "korean_name", None)):
                    if extra and extra not in parts:
                        parts.append(extra)
                matched_songs.append(" / ".join(parts))
                # 带出库内「歌曲 → 所属专辑」官方配对，让 AI 直接使用官方专辑名
                track_albums = _song_album_names(db, song.id)
                if track_albums:
                    matched_tracks.append(
                        {"song": " / ".join(parts), "albums": track_albums}
                    )
                if len(matched_songs) >= limit:
                    break
            if len(matched_songs) >= limit:
                break
        if matched_songs:
            out["matched_songs"] = matched_songs
        if matched_tracks:
            out["matched_tracks"] = matched_tracks

        seen_albums: set[int] = set()
        matched_albums: list[str] = []
        for source in song_sources:
            for album, _score in fuzzy_named(db, Album, source, ALBUM_NAME_ATTRS, limit=limit):
                if album.id in seen_albums:
                    continue
                seen_albums.add(album.id)
                matched_albums.append(album.name)
                if len(matched_albums) >= limit:
                    break
            if len(matched_albums) >= limit:
                break
        if matched_albums:
            out["matched_albums"] = matched_albums

    return out


def _song_album_names(db: Session, song_id: int, *, limit: int = MAX_ALBUMS_PER_SONG) -> list[str]:
    """库内该歌曲的所属专辑官方名（按 AlbumTrack 反查，最多 limit 张）。"""
    from app.models.album import AlbumTrack

    rows = db.execute(
        select(Album.name)
        .join(AlbumTrack, AlbumTrack.album_id == Album.id)
        .where(AlbumTrack.song_id == song_id, Album.deleted_at.is_(None))
        .order_by(Album.release_date)
        .limit(limit)
    ).all()
    return [n for (n,) in rows if n]


def build_album_candidates(
    db: Session, artist_ids: list[int], group_ids: list[int], *, limit: int = 15
) -> list[str]:
    """专辑识别候选（去重）：
    1) 这些艺人/组合的影像已关联过的专辑；
    2) 资料库中该艺人/组合名下的专辑（release_artist 直接归属）；
    3) 收录其发行歌曲的专辑（AlbumTrack → Song.release_artist）。
    先建库后入库场景下 (1) 常为空，(2)(3) 才是主要来源。
    """
    ids_artists = [i for i in (artist_ids or []) if i]
    ids_groups = [i for i in (group_ids or []) if i]
    if not ids_artists and not ids_groups:
        return []

    from app.models import Album, MusicVideo, Song
    from app.models.album import AlbumTrack
    from app.models.music_video import (
        music_video_albums,
        music_video_artists,
        music_video_groups,
    )

    conds = []
    if ids_artists:
        conds.append(
            select(music_video_artists.c.music_video_id)
            .where(
                music_video_artists.c.artist_id.in_(ids_artists),
                music_video_artists.c.music_video_id == MusicVideo.id,
            )
            .exists()
        )
    if ids_groups:
        conds.append(
            select(music_video_groups.c.music_video_id)
            .where(
                music_video_groups.c.group_id.in_(ids_groups),
                music_video_groups.c.music_video_id == MusicVideo.id,
            )
            .exists()
        )
    stmt = (
        select(Album.name)
        .join(music_video_albums, music_video_albums.c.album_id == Album.id)
        .join(MusicVideo, MusicVideo.id == music_video_albums.c.music_video_id)
        .where(
            Album.deleted_at.is_(None),
            MusicVideo.deleted_at.is_(None),
            or_(*conds),
        )
        .distinct()
        .order_by(Album.name)
        .limit(limit)
    )
    names = [n for n in db.scalars(stmt).all() if n]
    seen = set(names)

    # 资料库直接归属的专辑（先建库场景的主要来源）
    owner_conds = []
    if ids_artists:
        owner_conds.append(
            (Album.release_artist_type == "artist") & Album.release_artist_id.in_(ids_artists)
        )
    if ids_groups:
        owner_conds.append(
            (Album.release_artist_type == "group") & Album.release_artist_id.in_(ids_groups)
        )
    for name in db.scalars(
        select(Album.name)
        .where(Album.deleted_at.is_(None), or_(*owner_conds))
        .order_by(Album.release_date.desc())
        .limit(limit * 2)
    ).all():
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    # 收录其发行歌曲的专辑（AlbumTrack → Song.release_artist）
    song_conds = []
    if ids_artists:
        song_conds.append(
            (Song.release_artist_type == "artist") & Song.release_artist_id.in_(ids_artists)
        )
    if ids_groups:
        song_conds.append(
            (Song.release_artist_type == "group") & Song.release_artist_id.in_(ids_groups)
        )
    for name in db.scalars(
        select(Album.name)
        .join(AlbumTrack, AlbumTrack.album_id == Album.id)
        .join(Song, Song.id == AlbumTrack.song_id)
        .where(
            Album.deleted_at.is_(None),
            Song.deleted_at.is_(None),
            or_(*song_conds),
        )
        .distinct()
        .order_by(Album.release_date.desc())
        .limit(limit * 2)
    ).all():
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names[:limit]
