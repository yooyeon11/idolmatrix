"""实体「主体展示名」的统一取名口径（歌曲 + 专辑）。

歌曲精简搜索（`/songs/brief` 的 `with_owner`）、专辑曲目命中提示
（`/albums/brief` 的 `matched_song_owner`）以及专辑自身的发行主体
（`/albums/brief` 的 `owner`，供下拉无歌曲关联时兜底）都要回答「这是谁的」——
库内存在同名歌曲（I AM / Supernova / Too Hot）与同名专辑，只有靠主体才能区分。

取名口径（与前端 `resolveSongOwner`、`songs._song_relation_names` 一致）：
发行主体优先（`release_artist_type` + `release_artist_id`），缺失退到
`SongArtistRelation` 的第一条关系；组合取 中文名→主名，艺人取 中文名→艺名→主名。

⚠ 只有歌曲有「关系表」兜底；专辑主体**只认 `release_artist_*`**，未填就查无主体
（专辑没有等价的 album-artist 关系表）。
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from sqlalchemy import select

from app.models.album import Album
from app.models.artist import Artist
from app.models.group import Group
from app.models.song import Song

# 发行主体的多态引用：(type, id)，type ∈ {"group", "artist"}
ReleaseRef = Tuple[Optional[str], Optional[int]]


def load_release_owner_names(db, refs: Sequence[ReleaseRef]) -> dict[ReleaseRef, str]:
    """批量把 `(type, id)` 发行主体解析成展示名。

    组合取 中文名→主名，艺人取 中文名→艺名→主名；查不到 / id 为空的不进结果。
    一次把用到的 Group / Artist 各查一遍，避免逐条打库。
    """
    pairs = [(t, i) for t, i in refs if i]
    if not pairs:
        return {}
    group_ids = {i for t, i in pairs if t == "group"}
    artist_ids = {i for t, i in pairs if t == "artist"}
    groups = (
        {
            g.id: (g.chinese_name or g.name)
            for g in db.scalars(select(Group).where(Group.id.in_(group_ids))).all()
        }
        if group_ids
        else {}
    )
    artists = (
        {
            a.id: (a.chinese_name or a.stage_name or a.name)
            for a in db.scalars(select(Artist).where(Artist.id.in_(artist_ids))).all()
        }
        if artist_ids
        else {}
    )
    out: dict[ReleaseRef, str] = {}
    for t, i in pairs:
        name = groups.get(i) if t == "group" else (artists.get(i) if t == "artist" else None)
        if name:
            out[(t, i)] = name
    return out


def album_owner_names(db, album_ids: List[int]) -> dict[int, str]:
    """批量取专辑的「发行主体展示名」（仅 `release_artist_*`）。

    未填发行主体的专辑不在结果里 —— 调用方按「无主体」降级处理。
    """
    ids = [i for i in dict.fromkeys(album_ids) if i]
    if not ids:
        return {}
    albums = db.scalars(select(Album).where(Album.id.in_(ids))).all()
    owners = load_release_owner_names(
        db, [(a.release_artist_type, a.release_artist_id) for a in albums]
    )
    return {
        a.id: owners[(a.release_artist_type, a.release_artist_id)]
        for a in albums
        if (a.release_artist_type, a.release_artist_id) in owners
    }


def song_owner_names(db, song_ids: List[int]) -> dict[int, str]:
    """批量取歌曲的「主体展示名」：发行主体优先，缺失退到歌曲-艺人/组合关系。"""
    ids = [i for i in dict.fromkeys(song_ids) if i]
    if not ids:
        return {}
    songs = db.scalars(select(Song).where(Song.id.in_(ids))).all()
    owners = load_release_owner_names(
        db, [(s.release_artist_type, s.release_artist_id) for s in songs]
    )
    out: dict[int, str] = {
        s.id: owners[(s.release_artist_type, s.release_artist_id)]
        for s in songs
        if (s.release_artist_type, s.release_artist_id) in owners
    }
    rest = [i for i in ids if i not in out]
    if rest:
        # 复用歌曲路由里的关系名口径（含软删除过滤 + relation.order 排序）。
        # 延迟导入：`api.songs` 会 import 本模块，顶层导入成环。
        from app.api.songs import _song_relation_names

        for sid, names in _song_relation_names(db, rest).items():
            if names:
                out[sid] = names[0]
    return out
