"""新版资料库视图服务（只读）：关系化列表。

第一版覆盖组合（Group）：一行看清 成员 / 公司 / 作品 / 完整度 / 问题数，
行展开所需的成员轨迹与公司关系一并返回，前端无需二次请求。

问题归因：复用 data_health.run_health_report 的结果，把带
deep_link=/groups/{uid} 的问题按 uid 归到组合行上。后续实体
（成员/专辑/歌曲）按同样模式扩展。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.album import Album, AlbumTrack
from app.models.artist import Artist
from app.models.company import Company
from app.models.group import Group
from app.models.membership import GroupMembership
from app.models.music_video import (
    MusicVideo,
    MusicVideoTrack,
    music_video_artists,
    music_video_groups,
    music_video_songs,
    music_video_track_albums,
)
from app.models.song import Credits, Song, SongArtistRelation
from app.services.data_health import run_health_report
from app.services.group_tree import family_group_ids

_GROUP_LINK_RE = re.compile(r"/groups/([0-9a-fA-F-]{36})")

# 完整度检查清单：label 用于前端展示缺什么
_CHECKLIST_BASE = ("出道日期", "组合类型", "描述/简介", "头像")
_CHECKLIST_RELATION = ("成员", "所属公司", "作品关联")


def _date_str(d) -> Optional[str]:
    return d.isoformat() if d is not None else None


def _group_work_counts(db: Session, group_ids: List[int]) -> Dict[int, Dict[str, int]]:
    """按组合统计 专辑 / 歌曲 / 影像 数量（多态发行主体 + mv_groups 关联）。

    影像数走「母队口径含小分队」（v3.2.43）：与视频列表
    `/music-videos?group_id=` 同一套家族展开，否则列表页写的数字会小于
    点进去看到的条数。专辑 / 歌曲仍是数据层的发行主体署名，不参与归并。
    """
    counts: Dict[int, Dict[str, int]] = {
        gid: {"albums": 0, "songs": 0, "videos": 0} for gid in group_ids
    }
    if not group_ids:
        return counts
    album_rows = db.execute(
        select(Album.release_artist_id, func.count())
        .where(
            Album.deleted_at.is_(None),
            Album.release_artist_type == "group",
            Album.release_artist_id.in_(group_ids),
        )
        .group_by(Album.release_artist_id)
    ).all()
    for gid, cnt in album_rows:
        if gid in counts:
            counts[gid]["albums"] = cnt
    song_rows = db.execute(
        select(Song.release_artist_id, func.count())
        .where(
            Song.deleted_at.is_(None),
            Song.release_artist_type == "group",
            Song.release_artist_id.in_(group_ids),
        )
        .group_by(Song.release_artist_id)
    ).all()
    for gid, cnt in song_rows:
        if gid in counts:
            counts[gid]["songs"] = cnt
    # 影像：先取 (组合 → 视频 id 集合)，再按家族取并集去重
    # （同一条视频若同时挂母队与小分队，只能算一条）
    family = family_group_ids(db, group_ids)
    family_ids = {gid for members in family.values() for gid in members}
    videos_by_group: Dict[int, set] = {}
    if family_ids:
        mv_rows = db.execute(
            select(music_video_groups.c.group_id, music_video_groups.c.music_video_id)
            .join(MusicVideo, MusicVideo.id == music_video_groups.c.music_video_id)
            .where(
                MusicVideo.deleted_at.is_(None),
                music_video_groups.c.group_id.in_(family_ids),
            )
        ).all()
        for gid, video_id in mv_rows:
            videos_by_group.setdefault(gid, set()).add(video_id)
    for gid in group_ids:
        seen: set = set()
        for member in family.get(gid) or {gid}:
            seen |= videos_by_group.get(member, set())
        counts[gid]["videos"] = len(seen)
    return counts


def _completeness(g: Group, member_count: int, company_count: int, works: Dict[str, int]) -> tuple[int, List[str]]:
    """返回 (0-100 完整度, 缺失项 label 列表)。8 项检查清单。"""
    missing: List[str] = []
    if g.debut_date is None:
        missing.append("出道日期")
    if not g.group_type:
        missing.append("组合类型")
    if not (g.description or g.tagline):
        missing.append("描述/简介")
    if not g.avatar_path:
        missing.append("头像")
    if member_count == 0:
        missing.append("成员")
    if company_count == 0:
        missing.append("所属公司")
    if sum(works.values()) == 0:
        missing.append("作品关联")
    filled = 8 - len(missing)
    return round(filled / 8 * 100), missing


def _issue_issues_for_uid(report: dict, uid: str) -> List[dict]:
    """取体检报告中归属于某组合 uid 的问题明细（entity_uid 命中或 deep_link 指向）。"""
    out = []
    for issue in report["issues"]:
        link = issue.get("deep_link") or ""
        m = _GROUP_LINK_RE.search(link)
        if issue.get("entity_uid") == uid or (m and m.group(1) == uid):
            out.append(issue)
    return out


def build_group_rows(db: Session, report: Optional[dict] = None) -> dict:
    """组合关系化列表：每行 = 概要 + 成员轨迹 + 公司关系 + 作品计数 + 完整度 + 问题数。"""
    groups = db.scalars(select(Group).where(Group.deleted_at.is_(None))).all()
    if report is None:
        report = run_health_report(db, max_issues=2000, use_cache=True)
    issue_index: Dict[str, Dict[str, int]] = {}
    for issue in report["issues"]:
        link = issue.get("deep_link") or ""
        m = _GROUP_LINK_RE.search(link)
        if not m:
            continue
        bucket = issue_index.setdefault(m.group(1), {"error": 0, "warning": 0, "hint": 0})
        sev = issue["severity"]
        if sev in bucket:
            bucket[sev] += 1

    # 成员（含软删除艺人的悬空关系也带出来，标 is_dangling，与体检口径一致）
    member_rows = db.execute(
        select(GroupMembership, Artist)
        .join(Artist, GroupMembership.artist_id == Artist.id)
        .where(GroupMembership.group_id.in_([g.id for g in groups] or [-1]))
        .order_by(GroupMembership.join_date.asc().nullslast(), GroupMembership.id.asc())
    ).all() if groups else []
    members_by_group: Dict[int, List[dict]] = {}
    for m, a in member_rows:
        members_by_group.setdefault(m.group_id, []).append(
            {
                "membership_id": m.id,
                "artist_id": a.id,
                "artist_uid": a.uid,
                "name": a.name,
                "stage_name": a.stage_name,
                "avatar_path": a.avatar_path,
                "join_date": _date_str(m.join_date),
                "leave_date": _date_str(m.leave_date),
                "status": m.status,
                "positions": m.positions or [],
                "is_dangling": a.deleted_at is not None,
                # ===== 艺人档案全字段（工作台行展开用）=====
                "chinese_name": a.chinese_name,
                "english_name": a.english_name,
                "korean_name": a.korean_name,
                "birth_date": _date_str(a.birth_date),
                "birth_place": a.birth_place,
                "gender": a.gender,
                "debut_date": _date_str(a.debut_date),
                "occupation": a.occupation,
                "description": a.description,
                "tagline": a.tagline,
                "social_media": a.social_media,
                "hide_from_home": bool(getattr(a, "hide_from_home", False)),
                "external_links": a.external_links or [],
            }
        )

    # 公司关系
    from app.models.company import GroupCompanyRelation

    company_rows = db.execute(
        select(GroupCompanyRelation, Company)
        .join(Company, GroupCompanyRelation.company_id == Company.id)
        .where(GroupCompanyRelation.group_id.in_([g.id for g in groups] or [-1]))
        .order_by(GroupCompanyRelation.start_date.asc().nullslast())
    ).all() if groups else []
    companies_by_group: Dict[int, List[dict]] = {}
    for rel, c in company_rows:
        companies_by_group.setdefault(rel.group_id, []).append(
            {
                "relation_id": rel.id,
                "company_id": c.id,
                "name": c.name,
                "role": rel.role,
                "status": rel.status,
                "start_date": _date_str(rel.start_date),
                "end_date": _date_str(rel.end_date),
                "is_dangling": c.deleted_at is not None,
            }
        )

    work_counts = _group_work_counts(db, [g.id for g in groups])

    # 上级组合名（小分队展示用）
    parent_ids = {g.parent_group_id for g in groups if g.parent_group_id}
    parents = (
        {
            p.id: p.name
            for p in db.scalars(select(Group).where(Group.id.in_(parent_ids)))
        }
        if parent_ids
        else {}
    )

    items: List[dict] = []
    for g in groups:
        members = members_by_group.get(g.id, [])
        companies = companies_by_group.get(g.id, [])
        works = work_counts.get(g.id, {"albums": 0, "songs": 0, "videos": 0})
        active_n = sum(
            1 for m in members if m["status"] != "Former" and not m["is_dangling"]
        )
        former_n = sum(
            1 for m in members if m["status"] == "Former" and not m["is_dangling"]
        )
        score, missing = _completeness(g, active_n + former_n, len(companies), works)
        sample = [
            {"id": m["artist_id"], "name": m["name"], "avatar_path": m["avatar_path"]}
            for m in members[:6]
        ]
        items.append(
            {
                "id": g.id,
                "uid": g.uid,
                "name": g.name,
                "korean_name": g.korean_name,
                "chinese_name": g.chinese_name,
                "debut_date": _date_str(g.debut_date),
                "group_type": g.group_type,
                "avatar_path": g.avatar_path,
                "is_subunit": g.parent_group_id is not None,
                "parent_name": parents.get(g.parent_group_id) if g.parent_group_id else None,
                "members_active": active_n,
                "members_former": former_n,
                "member_sample": sample,
                "members": members,
                "companies": companies,
                "works": works,
                "completeness": score,
                "completeness_missing": missing,
                "issues": issue_index.get(g.uid, {"error": 0, "warning": 0, "hint": 0}),
            }
        )

    # 问题多/完整度低的排前面，问题清单的天然顺序
    items.sort(
        key=lambda r: (
            -(r["issues"]["error"] * 5 + r["issues"]["warning"] * 2 + r["issues"]["hint"]),
            r["completeness"],
            r["name"],
        )
    )
    return {
        "items": items,
        "total": len(items),
        "checklist": list(_CHECKLIST_BASE) + list(_CHECKLIST_RELATION),
    }


def build_artist_workspace(db: Session, uid: str) -> Optional[dict]:
    """solo 艺人工作台：档案全字段 + 所属组合关系 + 作品 + 问题归因。

    组合成员关系只是艺人的可选关系——IU 这类独立艺人没有任何成员关系，
    完整度清单也不把「所属组合」算缺失。
    """
    from app.models.membership import GroupMembership

    a = db.scalar(select(Artist).where(Artist.uid == uid, Artist.deleted_at.is_(None)))
    if a is None:
        return None
    report = run_health_report(db, max_issues=2000, use_cache=True)
    issues = [
        i
        for i in report["issues"]
        if i.get("entity_uid") == uid
        or (i.get("deep_link") or "").find(f"/artists/{uid}") >= 0
    ]
    issue_counts = {
        "error": sum(1 for i in issues if i["severity"] == "error"),
        "warning": sum(1 for i in issues if i["severity"] == "warning"),
        "hint": sum(1 for i in issues if i["severity"] == "hint"),
    }

    mem_rows = db.execute(
        select(GroupMembership, Group)
        .join(Group, GroupMembership.group_id == Group.id)
        .where(
            GroupMembership.artist_id == a.id,
            Group.deleted_at.is_(None),
        )
        .order_by(GroupMembership.join_date.asc().nullslast())
    ).all()
    memberships = [
        {
            "membership_id": m.id,
            "group_id": g2.id,
            "group_uid": g2.uid,
            "group_name": g2.name,
            "group_type": g2.group_type,
            "join_date": _date_str(m.join_date),
            "leave_date": _date_str(m.leave_date),
            "status": m.status,
            "positions": m.positions or [],
        }
        for m, g2 in mem_rows
    ]

    # 所属公司（含历史，工作台可编辑）
    from app.models.company import ArtistCompanyRelation

    comp_rows = db.execute(
        select(ArtistCompanyRelation, Company)
        .join(Company, ArtistCompanyRelation.company_id == Company.id)
        .where(
            ArtistCompanyRelation.artist_id == a.id,
            Company.deleted_at.is_(None),
        )
        .order_by(ArtistCompanyRelation.start_date.asc().nullslast())
    ).all()
    companies = [
        {
            "id": cr.id,
            "company_id": c.id,
            "company_name": c.name,
            "role": cr.role,
            "status": cr.status,
            "start_date": _date_str(cr.start_date),
            "end_date": _date_str(cr.end_date),
        }
        for cr, c in comp_rows
    ]

    album_rows = db.execute(
        select(Album, func.count(func.distinct(AlbumTrack.id)))
        .outerjoin(AlbumTrack, AlbumTrack.album_id == Album.id)
        .where(
            Album.deleted_at.is_(None),
            Album.release_artist_type == "artist",
            Album.release_artist_id == a.id,
        )
        .group_by(Album.id)
        .order_by(Album.release_date.desc().nullslast())
    ).all()
    # 每张专辑的关联影像数（经视频曲目行挂专辑）
    from app.models.music_video import music_video_track_albums

    _mv_tracks_t = MusicVideoTrack.__table__
    album_video_counts = (
        dict(
            db.execute(
                select(
                    music_video_track_albums.c.album_id,
                    func.count(func.distinct(_mv_tracks_t.c.music_video_id)),
                )
                .join(_mv_tracks_t, _mv_tracks_t.c.id == music_video_track_albums.c.track_id)
                .where(
                    music_video_track_albums.c.album_id.in_(
                        [al.id for al, _ in album_rows] or [-1]
                    )
                )
                .group_by(music_video_track_albums.c.album_id)
            ).all()
        )
        if album_rows
        else {}
    )
    albums = [
        {
            "id": al.id,
            "uid": al.uid,
            "name": al.name,
            "release_date": _date_str(al.release_date),
            "album_type": al.album_type,
            "track_count": cnt or 0,
            "video_count": album_video_counts.get(al.id, 0),
        }
        for al, cnt in album_rows
    ]
    songs_count = db.scalar(
        select(func.count())
        .select_from(Song)
        .where(
            Song.deleted_at.is_(None),
            Song.release_artist_type == "artist",
            Song.release_artist_id == a.id,
        )
    ) or 0

    # solo 歌曲清单（发行主体=本艺人；组合成员 solo 出歌也会落在这里）
    # 注意用 scalars()：execute() 返回的是 Row 包装（(<Song>,)），直接取属性会 500
    song_rows = db.scalars(
        select(Song)
        .where(
            Song.deleted_at.is_(None),
            Song.release_artist_type == "artist",
            Song.release_artist_id == a.id,
        )
        .order_by(Song.release_date.desc().nullslast(), Song.name.asc())
    ).all()
    songs = [
        {
            "id": sg.id,
            "uid": sg.uid,
            "name": sg.name,
            "chinese_name": sg.chinese_name,
            "release_date": _date_str(sg.release_date),
            "duration": sg.duration,
        }
        for sg in song_rows
    ]

    # 关联影像（music_video_artists 多对多）
    video_rows = db.execute(
        select(MusicVideo.id, MusicVideo.uid, MusicVideo.name, MusicVideo.video_type)
        .join(music_video_artists, music_video_artists.c.music_video_id == MusicVideo.id)
        .where(
            music_video_artists.c.artist_id == a.id,
            MusicVideo.deleted_at.is_(None),
        )
        .order_by(MusicVideo.id.desc())
        .limit(60)
    ).all()
    videos = [
        {"id": v_id, "uid": v_uid, "name": v_name, "video_type": v_type}
        for v_id, v_uid, v_name, v_type in video_rows
    ]

    missing: List[str] = []
    if a.birth_date is None:
        missing.append("出生日期")
    if not a.occupation:
        missing.append("职业")
    if not (a.description or a.tagline):
        missing.append("描述/简介")
    if not a.avatar_path:
        missing.append("头像")
    if len(albums) == 0 and songs_count == 0:
        missing.append("作品关联")
    filled = 5 - len(missing)
    completeness = round(filled / 5 * 100)

    return {
        "artist": {
            "id": a.id,
            "uid": a.uid,
            "name": a.name,
            "korean_name": a.korean_name,
            "chinese_name": a.chinese_name,
            "english_name": a.english_name,
            "stage_name": a.stage_name,
            "birth_date": _date_str(a.birth_date),
            "birth_place": a.birth_place,
            "gender": a.gender,
            "debut_date": _date_str(a.debut_date),
            "occupation": a.occupation,
            "description": a.description,
            "tagline": a.tagline,
            "avatar_path": a.avatar_path,
            "banner_path": a.banner_path,
            "social_media": a.social_media,
            "hide_from_home": bool(getattr(a, "hide_from_home", False)),
            "external_links": a.external_links or [],
        },
        "memberships": memberships,
        "companies": companies,
        "albums": albums,
        "songs": songs,
        "songs_count": songs_count,
        "videos": videos,
        "completeness": completeness,
        "completeness_missing": missing,
        "issues": issues,
        "issue_counts": issue_counts,
        "completeness_checklist": ["出生日期", "职业", "描述/简介", "头像", "作品关联"],
    }


def build_group_workspace(db: Session, uid: str) -> Optional[dict]:
    """单组合工作台：基本信息（可编辑字段）+ 关系行 + 该实体的问题与来源。"""
    g = db.scalar(select(Group).where(Group.uid == uid, Group.deleted_at.is_(None)))
    if g is None:
        return None
    report = run_health_report(db, max_issues=2000, use_cache=True)
    rows = build_group_rows(db, report=report)["items"]
    row = next((r for r in rows if r["uid"] == uid), None)
    issues = _issue_issues_for_uid(report, uid)
    # 小分队列表（点击可进入其工作台，递归生长）+ 每支的成员 id 集（成员 chip 挂小分队标签）
    children = db.scalars(
        select(Group).where(
            Group.deleted_at.is_(None), Group.parent_group_id == g.id
        )
    ).all()
    child_ids = [c.id for c in children]
    sub_member_rows = (
        db.scalars(
            select(GroupMembership).where(GroupMembership.group_id.in_(child_ids))
        ).all()
        if child_ids
        else []
    )
    member_ids_by_sub: Dict[int, List[int]] = {}
    for sm in sub_member_rows:
        member_ids_by_sub.setdefault(sm.group_id, []).append(sm.artist_id)
    sub_units = [
        {
            "uid": c.uid,
            "id": c.id,
            "name": c.name,
            "group_type": c.group_type,
            "completeness": next(
                (r["completeness"] for r in rows if r["uid"] == c.uid), 0
            ),
            "members_active": next(
                (r["members_active"] for r in rows if r["uid"] == c.uid), 0
            ),
            "member_ids": member_ids_by_sub.get(c.id, []),
        }
        for c in children
    ]
    # 专辑列表：曲目数 + 关联影像数（作品区高密度展示）
    album_rows = db.execute(
        select(
            Album,
            func.count(func.distinct(AlbumTrack.id)),
        )
        .outerjoin(AlbumTrack, AlbumTrack.album_id == Album.id)
        .where(
            Album.deleted_at.is_(None),
            Album.release_artist_type == "group",
            Album.release_artist_id == g.id,
        )
        .group_by(Album.id)
        .order_by(Album.release_date.desc().nullslast())
    ).all()
    _mv_tracks_t = MusicVideoTrack.__table__

    mv_counts = dict(
        db.execute(
            select(music_video_track_albums.c.album_id, func.count(func.distinct(_mv_tracks_t.c.music_video_id)))
            .join(_mv_tracks_t, _mv_tracks_t.c.id == music_video_track_albums.c.track_id)
            .where(music_video_track_albums.c.album_id.in_([a.id for a, _ in album_rows] or [-1]))
            .group_by(music_video_track_albums.c.album_id)
        ).all()
    ) if album_rows else {}
    album_list = [
        {
            "id": a.id,
            "uid": a.uid,
            "name": a.name,
            "release_date": _date_str(a.release_date),
            "album_type": a.album_type,
            "track_count": cnt or 0,
            "video_count": mv_counts.get(a.id, 0),
        }
        for a, cnt in album_rows
    ]

    return {
        "albums": album_list,
        "group": {
            "id": g.id,
            "uid": g.uid,
            "name": g.name,
            "chinese_name": g.chinese_name,
            "english_name": g.english_name,
            "korean_name": g.korean_name,
            "debut_date": _date_str(g.debut_date),
            "group_type": g.group_type,
            "gender_type": g.gender_type,
            "origin_country": g.origin_country,
            "description": g.description,
            "tagline": g.tagline,
            "avatar_path": g.avatar_path,
            "banner_path": g.banner_path,
            "social_media": g.social_media,
            "hide_from_home": bool(getattr(g, "hide_from_home", False)),
            "external_links": g.external_links or [],
        },
        "row": row,
        "sub_units": sub_units,
        "issues": issues,
        "issue_counts": {
            "error": sum(1 for i in issues if i["severity"] == "error"),
            "warning": sum(1 for i in issues if i["severity"] == "warning"),
            "hint": sum(1 for i in issues if i["severity"] == "hint"),
        },
        "completeness_checklist": list(_CHECKLIST_BASE) + list(_CHECKLIST_RELATION),
    }


# ===== 资料库列表：艺人 / 专辑 / 歌曲 =====


def _issues_by_uid(report: dict, entity_type: str) -> Dict[str, Dict[str, int]]:
    """把体检报告按实体类型 + uid 归类为 error/warning/hint 计数。"""
    index: Dict[str, Dict[str, int]] = {}
    for issue in report["issues"]:
        if issue.get("entity_type") != entity_type:
            continue
        uid = issue.get("entity_uid")
        if not uid:
            continue
        bucket = index.setdefault(uid, {"error": 0, "warning": 0, "hint": 0})
        if issue["severity"] in bucket:
            bucket[issue["severity"]] += 1
    return index


def _sort_rows(items: List[dict]) -> None:
    """问题多 / 完整度低的排前面，与组合列表一致。"""
    items.sort(
        key=lambda r: (
            -(r["issues"]["error"] * 5 + r["issues"]["warning"] * 2 + r["issues"]["hint"]),
            r["completeness"],
            r["name"],
        )
    )


def build_artist_rows(db: Session, report: Optional[dict] = None) -> dict:
    """艺人关系化列表：档案概要 + 所属组合 + 作品计数 + 完整度 + 问题数。

    完整度口径与艺人工作台一致（5 项）。所属组合只是可选关系，
    不计入缺失。
    """
    artists = db.scalars(select(Artist).where(Artist.deleted_at.is_(None))).all()
    if report is None:
        report = run_health_report(db, max_issues=2000, use_cache=True)
    issue_index = _issues_by_uid(report, "artist")

    member_rows = db.execute(
        select(GroupMembership, Group)
        .join(Group, GroupMembership.group_id == Group.id)
        .where(
            GroupMembership.artist_id.in_([a.id for a in artists] or [-1]),
            Group.deleted_at.is_(None),
        )
        .order_by(GroupMembership.join_date.asc().nullslast())
    ).all() if artists else []
    groups_by_artist: Dict[int, List[dict]] = {}
    for m, g in member_rows:
        groups_by_artist.setdefault(m.artist_id, []).append(
            {
                "group_id": g.id,
                "group_uid": g.uid,
                "name": g.name,
                "status": m.status,
            }
        )

    album_counts = dict(
        db.execute(
            select(Album.release_artist_id, func.count())
            .where(
                Album.deleted_at.is_(None),
                Album.release_artist_type == "artist",
                Album.release_artist_id.in_([a.id for a in artists] or [-1]),
            )
            .group_by(Album.release_artist_id)
        ).all()
    ) if artists else {}
    song_counts = dict(
        db.execute(
            select(Song.release_artist_id, func.count())
            .where(
                Song.deleted_at.is_(None),
                Song.release_artist_type == "artist",
                Song.release_artist_id.in_([a.id for a in artists] or [-1]),
            )
            .group_by(Song.release_artist_id)
        ).all()
    ) if artists else {}

    # 关联影像数（经 music_video_artists 直连；组合成员身份不算关联，与前台口径一致）
    from app.models.music_video import music_video_artists

    video_counts = dict(
        db.execute(
            select(music_video_artists.c.artist_id, func.count(func.distinct(MusicVideo.id)))
            .join(MusicVideo, MusicVideo.id == music_video_artists.c.music_video_id)
            .where(
                music_video_artists.c.artist_id.in_([a.id for a in artists] or [-1]),
                MusicVideo.deleted_at.is_(None),
            )
            .group_by(music_video_artists.c.artist_id)
        ).all()
    ) if artists else {}

    items: List[dict] = []
    for a in artists:
        albums = album_counts.get(a.id, 0)
        songs = song_counts.get(a.id, 0)
        videos = video_counts.get(a.id, 0)
        missing: List[str] = []
        if a.birth_date is None:
            missing.append("出生日期")
        if not a.occupation:
            missing.append("职业")
        if not (a.description or a.tagline):
            missing.append("描述/简介")
        if not a.avatar_path:
            missing.append("头像")
        if albums == 0 and songs == 0:
            missing.append("作品关联")
        items.append(
            {
                "id": a.id,
                "uid": a.uid,
                "name": a.name,
                "chinese_name": a.chinese_name,
                "english_name": a.english_name,
                "korean_name": a.korean_name,
                "stage_name": a.stage_name,
                "birth_date": _date_str(a.birth_date),
                "debut_date": _date_str(a.debut_date),
                "gender": a.gender,
                "avatar_path": a.avatar_path,
                "groups": groups_by_artist.get(a.id, []),
                "works": {"albums": albums, "songs": songs, "videos": videos},
                "completeness": round((5 - len(missing)) / 5 * 100),
                "completeness_missing": missing,
                "issues": issue_index.get(a.uid, {"error": 0, "warning": 0, "hint": 0}),
            }
        )

    _sort_rows(items)
    return {
        "items": items,
        "total": len(items),
        "checklist": ["出生日期", "职业", "描述/简介", "头像", "作品关联"],
    }


def build_album_rows(db: Session, report: Optional[dict] = None) -> dict:
    """专辑关系化列表：发行主体 + 曲目/影像计数 + 完整度 + 问题数。"""
    albums = db.scalars(select(Album).where(Album.deleted_at.is_(None))).all()
    if report is None:
        report = run_health_report(db, max_issues=2000, use_cache=True)
    issue_index = _issues_by_uid(report, "album")

    # 多态发行主体：分别联 Group / Artist 取名称与 uid
    group_ids = {a.release_artist_id for a in albums if a.release_artist_type == "group" and a.release_artist_id}
    artist_ids = {a.release_artist_id for a in albums if a.release_artist_type == "artist" and a.release_artist_id}
    group_names = (
        {
            g.id: (g.name, g.uid)
            for g in db.scalars(select(Group).where(Group.id.in_(group_ids)))
        }
        if group_ids
        else {}
    )
    artist_names = (
        {
            a.id: (a.name, a.uid)
            for a in db.scalars(select(Artist).where(Artist.id.in_(artist_ids)))
        }
        if artist_ids
        else {}
    )

    track_counts = dict(
        db.execute(
            select(AlbumTrack.album_id, func.count(func.distinct(AlbumTrack.id)))
            .where(AlbumTrack.album_id.in_([a.id for a in albums] or [-1]))
            .group_by(AlbumTrack.album_id)
        ).all()
    ) if albums else {}

    _mv_tracks_t = MusicVideoTrack.__table__
    video_counts = dict(
        db.execute(
            select(
                music_video_track_albums.c.album_id,
                func.count(func.distinct(_mv_tracks_t.c.music_video_id)),
            )
            .join(_mv_tracks_t, _mv_tracks_t.c.id == music_video_track_albums.c.track_id)
            .where(music_video_track_albums.c.album_id.in_([a.id for a in albums] or [-1]))
            .group_by(music_video_track_albums.c.album_id)
        ).all()
    ) if albums else {}

    items: List[dict] = []
    for al in albums:
        tracks = track_counts.get(al.id, 0)
        videos = video_counts.get(al.id, 0)
        missing: List[str] = []
        if al.release_date is None:
            missing.append("发行日期")
        if not al.album_type:
            missing.append("专辑类型")
        if not al.cover_path:
            missing.append("封面")
        if tracks == 0:
            missing.append("曲目")
        subject_name, subject_uid = (None, None)
        if al.release_artist_type == "group" and al.release_artist_id in group_names:
            subject_name, subject_uid = group_names[al.release_artist_id]
        elif al.release_artist_type == "artist" and al.release_artist_id in artist_names:
            subject_name, subject_uid = artist_names[al.release_artist_id]
        items.append(
            {
                "id": al.id,
                "uid": al.uid,
                "name": al.name,
                "chinese_name": al.chinese_name,
                "korean_name": al.korean_name,
                "album_type": al.album_type,
                "release_date": _date_str(al.release_date),
                "cover_path": al.cover_path,
                "release_artist_type": al.release_artist_type,
                "release_artist_name": subject_name,
                "release_artist_uid": subject_uid,
                "works": {"tracks": tracks, "videos": videos},
                "completeness": round((4 - len(missing)) / 4 * 100),
                "completeness_missing": missing,
                "issues": issue_index.get(al.uid, {"error": 0, "warning": 0, "hint": 0}),
            }
        )

    _sort_rows(items)
    return {
        "items": items,
        "total": len(items),
        "checklist": ["发行日期", "专辑类型", "封面", "曲目"],
    }


def _song_video_counts(db: Session, song_ids: List[int]) -> Dict[int, int]:
    """歌曲关联影像数：直接关联 / M2M / 曲目行三种口径合并去重（排除短视频与软删除）。"""
    counts: Dict[int, set] = {sid: set() for sid in song_ids}
    if not song_ids:
        return {sid: 0 for sid in song_ids}
    active = MusicVideo.deleted_at.is_(None), MusicVideo.is_short.is_not(True)
    direct = db.execute(
        select(MusicVideo.song_id, MusicVideo.id)
        .where(MusicVideo.song_id.in_(song_ids), *active)
    ).all()
    for sid, vid in direct:
        counts[sid].add(vid)
    m2m = db.execute(
        select(music_video_songs.c.song_id, music_video_songs.c.music_video_id)
        .join(MusicVideo, MusicVideo.id == music_video_songs.c.music_video_id)
        .where(music_video_songs.c.song_id.in_(song_ids), *active)
    ).all()
    for sid, vid in m2m:
        counts[sid].add(vid)
    via_tracks = db.execute(
        select(MusicVideoTrack.song_id, MusicVideoTrack.music_video_id)
        .join(MusicVideo, MusicVideo.id == MusicVideoTrack.music_video_id)
        .where(MusicVideoTrack.song_id.in_(song_ids), *active)
    ).all()
    for sid, vid in via_tracks:
        counts[sid].add(vid)
    return {sid: len(v) for sid, v in counts.items()}


def build_song_rows(db: Session, report: Optional[dict] = None) -> dict:
    """歌曲关系化列表：演唱者 + 专辑/影像计数 + 完整度 + 问题数。"""
    songs = db.scalars(select(Song).where(Song.deleted_at.is_(None))).all()
    if report is None:
        report = run_health_report(db, max_issues=2000, use_cache=True)
    issue_index = _issues_by_uid(report, "song")

    rel_rows = db.execute(
        select(SongArtistRelation, Artist, Group)
        .join(Artist, SongArtistRelation.artist_id == Artist.id, isouter=True)
        .join(Group, SongArtistRelation.group_id == Group.id, isouter=True)
        .where(SongArtistRelation.song_id.in_([s.id for s in songs] or [-1]))
        .order_by(SongArtistRelation.order.asc(), SongArtistRelation.id.asc())
    ).all() if songs else []
    performers_by_song: Dict[int, List[dict]] = {}
    for rel, artist, group in rel_rows:
        if artist is not None:
            name = artist.chinese_name or artist.stage_name or artist.name
            performers_by_song.setdefault(rel.song_id, []).append(
                {
                    "subject_type": "artist",
                    "subject_id": artist.id,
                    "subject_uid": artist.uid,
                    "name": name,
                    "role": rel.role,
                }
            )
        elif group is not None:
            name = group.chinese_name or group.name
            performers_by_song.setdefault(rel.song_id, []).append(
                {
                    "subject_type": "group",
                    "subject_id": group.id,
                    "subject_uid": group.uid,
                    "name": name,
                    "role": rel.role,
                }
            )

    album_counts = dict(
        db.execute(
            select(AlbumTrack.song_id, func.count(func.distinct(AlbumTrack.album_id)))
            .where(AlbumTrack.song_id.in_([s.id for s in songs] or [-1]))
            .group_by(AlbumTrack.song_id)
        ).all()
    ) if songs else {}
    video_counts = _song_video_counts(db, [s.id for s in songs])

    items: List[dict] = []
    for s in songs:
        albums = album_counts.get(s.id, 0)
        videos = video_counts.get(s.id, 0)
        missing: List[str] = []
        if not performers_by_song.get(s.id):
            missing.append("演唱者")
        if s.release_date is None:
            missing.append("发行日期")
        if albums == 0:
            missing.append("专辑关联")
        if videos == 0:
            missing.append("影像关联")
        items.append(
            {
                "id": s.id,
                "uid": s.uid,
                "name": s.name,
                "chinese_name": s.chinese_name,
                "korean_name": s.korean_name,
                "song_type": s.song_type,
                "release_date": _date_str(s.release_date),
                "duration": s.duration,
                "performers": performers_by_song.get(s.id, []),
                "works": {"albums": albums, "videos": videos},
                "completeness": round((4 - len(missing)) / 4 * 100),
                "completeness_missing": missing,
                "issues": issue_index.get(s.uid, {"error": 0, "warning": 0, "hint": 0}),
            }
        )

    _sort_rows(items)
    return {
        "items": items,
        "total": len(items),
        "checklist": ["演唱者", "发行日期", "专辑关联", "影像关联"],
    }


def build_album_workspace(db: Session, uid: str) -> Optional[dict]:
    """专辑工作台：可编辑基本信息 + 发行主体 + 曲目清单 + 关联影像 + 问题。只读，写入走现有 PATCH。"""
    al = db.scalar(select(Album).where(Album.uid == uid, Album.deleted_at.is_(None)))
    if al is None:
        return None
    report = run_health_report(db, max_issues=2000, use_cache=True)
    issues = [
        i for i in report["issues"]
        if i.get("entity_type") == "album" and i.get("entity_uid") == uid
    ]

    # 曲目清单（含歌曲信息；悬空歌曲标 is_dangling，与体检口径一致）
    track_rows = db.execute(
        select(AlbumTrack, Song)
        .join(Song, AlbumTrack.song_id == Song.id, isouter=True)
        .where(AlbumTrack.album_id == al.id)
        .order_by(
            AlbumTrack.disc_number.asc(),
            AlbumTrack.track_number.asc(),
            AlbumTrack.id.asc(),
        )
    ).all()
    tracks = [
        {
            "track_id": t.id,
            "song_id": s.id if s is not None else None,
            "song_uid": s.uid if s is not None else None,
            "song_name": s.name if s is not None else None,
            "song_chinese_name": s.chinese_name if s is not None else None,
            "song_duration": s.duration if s is not None else None,
            "disc_number": t.disc_number,
            "track_number": t.track_number,
            "is_dangling": s is None or s.deleted_at is not None,
        }
        for t, s in track_rows
    ]

    # 关联影像（经由视频曲目行挂到本专辑的 MV，去重）
    video_rows = db.execute(
        select(MusicVideo.id, MusicVideo.uid, MusicVideo.name, MusicVideo.video_type)
        .join(MusicVideoTrack, MusicVideoTrack.music_video_id == MusicVideo.id)
        .join(music_video_track_albums, music_video_track_albums.c.track_id == MusicVideoTrack.id)
        .where(
            music_video_track_albums.c.album_id == al.id,
            MusicVideo.deleted_at.is_(None),
        )
        .distinct()
        .order_by(MusicVideo.id.asc())
    ).all()
    videos = [
        {"id": v_id, "uid": v_uid, "name": v_name, "video_type": v_type}
        for v_id, v_uid, v_name, v_type in video_rows
    ]

    # 发行主体（多态）：组合或艺人
    subject: Optional[dict] = None
    if al.release_artist_type == "group" and al.release_artist_id:
        g = db.scalar(select(Group).where(Group.id == al.release_artist_id))
        if g is not None:
            subject = {"type": "group", "id": g.id, "uid": g.uid, "name": g.name}
    elif al.release_artist_type == "artist" and al.release_artist_id:
        a = db.scalar(select(Artist).where(Artist.id == al.release_artist_id))
        if a is not None:
            subject = {"type": "artist", "id": a.id, "uid": a.uid, "name": a.name}

    label = None
    if al.label_id:
        c = db.scalar(select(Company).where(Company.id == al.label_id))
        if c is not None:
            label = {"id": c.id, "name": c.name}

    missing: List[str] = []
    if al.release_date is None:
        missing.append("发行日期")
    if not al.album_type:
        missing.append("专辑类型")
    if not al.cover_path:
        missing.append("封面")
    if not tracks:
        missing.append("曲目")

    return {
        "album": {
            "id": al.id,
            "uid": al.uid,
            "name": al.name,
            "chinese_name": al.chinese_name,
            "english_name": al.english_name,
            "korean_name": al.korean_name,
            "release_date": _date_str(al.release_date),
            "album_type": al.album_type,
            "description": al.description,
            "cover_path": al.cover_path,
        },
        "subject": subject,
        "label": label,
        "tracks": tracks,
        "videos": videos,
        "completeness": round((4 - len(missing)) / 4 * 100),
        "completeness_missing": missing,
        "issues": issues,
        "issue_counts": {
            "error": sum(1 for i in issues if i["severity"] == "error"),
            "warning": sum(1 for i in issues if i["severity"] == "warning"),
            "hint": sum(1 for i in issues if i["severity"] == "hint"),
        },
        "completeness_checklist": ["发行日期", "专辑类型", "封面", "曲目"],
    }


def _song_video_briefs(db: Session, song_id: int) -> List[dict]:
    """歌曲关联影像（三种口径合并去重）。"""
    from app.models.music_video import MusicVideoTrack

    active = MusicVideo.deleted_at.is_(None), MusicVideo.is_short.is_not(True)
    rows = db.execute(
        select(MusicVideo.id, MusicVideo.uid, MusicVideo.name, MusicVideo.video_type)
        .where(MusicVideo.song_id == song_id, *active)
    ).all()
    seen = {r[0] for r in rows}
    m2m = db.execute(
        select(MusicVideo.id, MusicVideo.uid, MusicVideo.name, MusicVideo.video_type)
        .join(music_video_songs, music_video_songs.c.music_video_id == MusicVideo.id)
        .where(music_video_songs.c.song_id == song_id, *active)
    ).all()
    for row in m2m:
        if row[0] not in seen:
            seen.add(row[0])
            rows = rows + [row]
    via_tracks = db.execute(
        select(MusicVideo.id, MusicVideo.uid, MusicVideo.name, MusicVideo.video_type)
        .join(MusicVideoTrack, MusicVideoTrack.music_video_id == MusicVideo.id)
        .where(MusicVideoTrack.song_id == song_id, *active)
    ).all()
    for row in via_tracks:
        if row[0] not in seen:
            seen.add(row[0])
            rows = rows + [row]
    return [
        {"id": v_id, "uid": v_uid, "name": v_name, "video_type": v_type}
        for v_id, v_uid, v_name, v_type in rows
    ]


def build_song_workspace(db: Session, uid: str) -> Optional[dict]:
    """歌曲工作台：可编辑基本信息 + 演出者关系 + 专辑/影像关联 + Credits + 问题。只读，写入走现有接口。"""
    s = db.scalar(select(Song).where(Song.uid == uid, Song.deleted_at.is_(None)))
    if s is None:
        return None
    report = run_health_report(db, max_issues=2000, use_cache=True)
    issues = [
        i for i in report["issues"]
        if i.get("entity_type") == "song" and i.get("entity_uid") == uid
    ]

    rel_rows = db.execute(
        select(SongArtistRelation, Artist, Group)
        .join(Artist, SongArtistRelation.artist_id == Artist.id, isouter=True)
        .join(Group, SongArtistRelation.group_id == Group.id, isouter=True)
        .where(SongArtistRelation.song_id == s.id)
        .order_by(SongArtistRelation.order.asc(), SongArtistRelation.id.asc())
    ).all()
    performers = []
    for rel, artist, group in rel_rows:
        if artist is not None:
            performers.append(
                {
                    "relation_id": rel.id,
                    "subject_type": "artist",
                    "subject_id": artist.id,
                    "subject_uid": artist.uid,
                    "name": artist.chinese_name or artist.stage_name or artist.name,
                    "role": rel.role,
                    "order": rel.order,
                    "is_dangling": False,
                }
            )
        elif group is not None:
            performers.append(
                {
                    "relation_id": rel.id,
                    "subject_type": "group",
                    "subject_id": group.id,
                    "subject_uid": group.uid,
                    "name": group.chinese_name or group.name,
                    "role": rel.role,
                    "order": rel.order,
                    "is_dangling": False,
                }
            )
        else:
            performers.append(
                {
                    "relation_id": rel.id,
                    "subject_type": "dangling",
                    "subject_id": rel.artist_id or rel.group_id,
                    "subject_uid": None,
                    "name": "悬空关系",
                    "role": rel.role,
                    "order": rel.order,
                    "is_dangling": True,
                }
            )

    album_rows = db.execute(
        select(AlbumTrack, Album)
        .join(Album, AlbumTrack.album_id == Album.id)
        .where(
            AlbumTrack.song_id == s.id,
            Album.deleted_at.is_(None),
        )
        .order_by(Album.release_date.desc().nullslast())
    ).all()
    albums = [
        {
            "track_id": t.id,
            "id": al.id,
            "uid": al.uid,
            "name": al.name,
            "release_date": _date_str(al.release_date),
            "album_type": al.album_type,
            "disc_number": t.disc_number,
            "track_number": t.track_number,
        }
        for t, al in album_rows
    ]

    videos = _song_video_briefs(db, s.id)

    credit_rows = db.execute(
        select(Credits, Artist)
        .join(Artist, Credits.artist_id == Artist.id, isouter=True)
        .where(Credits.song_id == s.id)
        .order_by(Credits.id.asc())
    ).all()
    credits = [
        {
            "id": cr.id,
            "name": cr.name,
            "role": cr.role,
            "artist_id": cr.artist_id,
            "artist_name": a.name if a is not None else None,
        }
        for cr, a in credit_rows
    ]

    missing: List[str] = []
    if not performers:
        missing.append("演唱者")
    if s.release_date is None:
        missing.append("发行日期")
    if not albums:
        missing.append("专辑关联")
    if not videos:
        missing.append("影像关联")

    return {
        "song": {
            "id": s.id,
            "uid": s.uid,
            "name": s.name,
            "chinese_name": s.chinese_name,
            "english_name": s.english_name,
            "korean_name": s.korean_name,
            "song_type": s.song_type,
            "release_date": _date_str(s.release_date),
            "duration": s.duration,
            "description": s.description,
        },
        "performers": performers,
        "albums": albums,
        "videos": videos,
        "credits": credits,
        "completeness": round((4 - len(missing)) / 4 * 100),
        "completeness_missing": missing,
        "issues": issues,
        "issue_counts": {
            "error": sum(1 for i in issues if i["severity"] == "error"),
            "warning": sum(1 for i in issues if i["severity"] == "warning"),
            "hint": sum(1 for i in issues if i["severity"] == "hint"),
        },
        "completeness_checklist": ["演唱者", "发行日期", "专辑关联", "影像关联"],
    }


def _suggest_description(db: Session, name: str) -> Optional[dict]:
    """单字段搜索：按实体名在 Fandom 检索并提取简介，不写库。

    依次尝试前 3 个候选条目，取首个非空简介（含 wikitext 导语兜底）。
    """
    from app.services import fandom_service

    for hit in fandom_service.search_pages(name, limit=3):
        title = hit.get("title") or ""
        if not title:
            continue
        try:
            detail = fandom_service.fetch_detail(title)
        except Exception:  # noqa: BLE001 — 单个候选失败继续下一个
            continue
        bio = (detail.get("biography") or "").strip()
        if not bio:
            continue
        bio = " ".join(bio.split())[:600]
        from app.services.ai_service import translate_to_simplified

        translated = translate_to_simplified(bio)
        if translated:
            bio = translated
            label = f"K-pop Fandom · {title}（已译）"
        else:
            label = f"K-pop Fandom · {title}"
        return {
            "field": "description",
            "value": bio,
            "source_label": label,
            "source_url": f"https://kpop.fandom.com/wiki/{title}",
        }
    return {
        "field": "description",
        "value": None,
        "source_label": None,
        "source_url": None,
    }


def suggest_group_field(db: Session, uid: str, field: str) -> Optional[dict]:
    g = db.scalar(select(Group).where(Group.uid == uid, Group.deleted_at.is_(None)))
    if g is None:
        return None
    if field != "description":
        raise ValueError(f"暂不支持字段：{field}")
    return _suggest_description(db, g.name)


def suggest_artist_field(db: Session, uid: str, field: str) -> Optional[dict]:
    a = db.scalar(select(Artist).where(Artist.uid == uid, Artist.deleted_at.is_(None)))
    if a is None:
        return None
    if field != "description":
        raise ValueError(f"暂不支持字段：{field}")
    return _suggest_description(db, a.name)
