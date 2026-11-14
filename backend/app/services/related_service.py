"""右栏推荐：相关作品 / 相关艺人。

业主 2026-11-12 口径（不同于 Emby/Plex 的外部元数据重建）：
- 同曲（不同场次/机位）
- 同组合：母队 + 小分队 + 在籍成员的 solo 作品
- 同公司：本人/本组所属公司（含上一层母公司）旗下的组合与艺人作品
- 同一天同一活动：performance_date + 归一化 event_name

输出顺序即优先级，调用方按顺序截断；所有查询都限定 library + 未删除。
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import func, or_, select

from app.models.artist import Artist
from app.models.company import ArtistCompanyRelation, Company, GroupCompanyRelation
from app.models.group import Group
from app.models.membership import GroupMembership
from app.models.music_video import MusicVideo
from app.services.group_tree import family_group_ids

PER_REASON = 4
MAX_TOTAL = 12


def _video_stmt():
    return select(MusicVideo).where(
        MusicVideo.deleted_at.is_(None),
        MusicVideo.ingestion_status == "library",
    )


def _newest(stmt, cap: int):
    return stmt.order_by(
        MusicVideo.performance_date.is_(None),
        MusicVideo.performance_date.desc(),
        MusicVideo.id.desc(),
    ).limit(cap)


def _company_ids(db, mv: MusicVideo) -> set[int]:
    """当前视频所属艺人/组合的公司（含上一层母公司）。"""
    ids: set[int] = set()
    for artist in mv.artists or []:
        ids.update(r.company_id for r in artist.company_relations)
    for group in mv.groups or []:
        ids.update(r.company_id for r in group.company_relations)
    if not ids:
        return ids
    parents = db.scalars(
        select(Company.parent_company_id).where(
            Company.id.in_(ids), Company.parent_company_id.is_not(None)
        )
    ).all()
    ids.update(p for p in parents if p)
    return ids


def collect_related(db, mv: MusicVideo) -> tuple[list[tuple[MusicVideo, str]], list[dict]]:
    """返回 ([(视频, 推荐来源)], [艺人/组合 dict])。"""
    works: list[tuple[MusicVideo, str]] = []
    seen: set[int] = {mv.id}

    def add(rows: Iterable[MusicVideo], reason: str, cap: int = PER_REASON) -> None:
        if len(works) >= MAX_TOTAL:
            return
        picked = 0
        for row in rows:
            if row.id in seen:
                continue
            seen.add(row.id)
            works.append((row, reason))
            picked += 1
            if picked >= cap or len(works) >= MAX_TOTAL:
                break

    # 1) 同曲：同一首歌的其他场次/机位
    if mv.song_id:
        add(
            db.scalars(
                _newest(_video_stmt().where(MusicVideo.song_id == mv.song_id), PER_REASON * 3)
            ).all(),
            "同曲",
        )

    # 2) 同组合（含小分队）与在籍成员 solo
    family: set[int] = set()
    group_ids = [g.id for g in (mv.groups or [])]
    for ids in family_group_ids(db, group_ids).values():
        family |= ids
    member_ids: list[int] = []
    if family:
        member_ids = list(
            db.scalars(
                select(GroupMembership.artist_id).where(
                    GroupMembership.group_id.in_(family),
                    GroupMembership.status == "Active",
                )
            ).all()
        )
        conds = [MusicVideo.groups.any(Group.id.in_(family))]
        if member_ids:
            conds.append(MusicVideo.subject_artist_id.in_(member_ids))
            conds.append(MusicVideo.artists.any(Artist.id.in_(member_ids)))
        add(
            db.scalars(
                _newest(_video_stmt().where(or_(*conds)), PER_REASON * 3)
            ).all(),
            "同组合",
        )

    # 3) 同公司（组合或艺人）
    companies = _company_ids(db, mv)
    company_artist_ids: set[int] = set()
    company_group_ids: set[int] = set()
    if companies:
        company_artist_ids = set(
            db.scalars(
                select(ArtistCompanyRelation.artist_id).where(
                    ArtistCompanyRelation.company_id.in_(companies)
                )
            ).all()
        )
        company_group_ids = set(
            db.scalars(
                select(GroupCompanyRelation.group_id).where(
                    GroupCompanyRelation.company_id.in_(companies)
                )
            ).all()
        )
        conds = []
        if company_artist_ids:
            conds.append(MusicVideo.subject_artist_id.in_(company_artist_ids))
            conds.append(MusicVideo.artists.any(Artist.id.in_(company_artist_ids)))
        if company_group_ids:
            conds.append(MusicVideo.groups.any(Group.id.in_(company_group_ids)))
        if conds:
            add(
                db.scalars(
                    _newest(_video_stmt().where(or_(*conds)), PER_REASON * 3)
                ).all(),
                "同公司",
            )

    # 4) 同一天同一活动
    if mv.performance_date:
        stmt = _video_stmt().where(MusicVideo.performance_date == mv.performance_date)
        if mv.event_name:
            stmt = stmt.where(
                func.lower(func.trim(MusicVideo.event_name))
                == mv.event_name.strip().lower()
            )
        add(db.scalars(_newest(stmt, PER_REASON * 3)).all(), "同场活动")

    # ===== 相关艺人 / 组合（可点进艺人页）=====
    artists: list[dict] = []
    seen_people: set[tuple[str, int]] = set()

    def push(kind: str, obj, reason: str) -> None:
        key = (kind, obj.id)
        if key in seen_people or getattr(obj, "deleted_at", None) is not None:
            return
        seen_people.add(key)
        artists.append(
            {
                "kind": kind,
                "id": obj.id,
                "uid": obj.uid,
                "name": obj.name,
                "chinese_name": obj.chinese_name,
                "avatar_path": obj.avatar_path,
                "reason": reason,
            }
        )

    for group in mv.groups or []:
        push("group", group, "同组合")
    for artist_id in member_ids[:6]:
        artist = db.get(Artist, artist_id)
        if artist is not None:
            push("artist", artist, "同组合成员")
    for group_id in list(company_group_ids)[:4]:
        group = db.get(Group, group_id)
        if group is not None:
            push("group", group, "同公司")
    for artist_id in list(company_artist_ids)[:4]:
        artist = db.get(Artist, artist_id)
        if artist is not None:
            push("artist", artist, "同公司")

    return works[:MAX_TOTAL], artists[:10]
