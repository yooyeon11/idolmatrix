"""软删除级联：实体 tombstone + 静默拆除关系边。

策略（v1，用户确认）：
- 保留实体 soft-delete 墓碑（deleted_at），便于回收站恢复与 id/uid 安全。
- 软删除实体时 **硬删除/置空** 指向它的关系边，避免数据体检把坟墓当活错误。
- 恢复只还原实体本身；已拆除的关系 **不会** 自动重建，需用户重新挂接 /
  重新生长。v1 不做完整 membership 快照。

本模块集中实现各实体的级联与一次性残留清理（purge_dangling_edges）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models.artist import Artist
from app.models.company import (
    ArtistCompanyRelation,
    Company,
    GroupCompanyRelation,
)
from app.models.group import Group
from app.models.membership import GroupMembership
from app.models.music_video import MusicVideo


def soft_delete_artist(db: Session, artist: Artist) -> dict[str, int]:
    """软删除艺人，并静默拆除其关系边。

    - group_memberships（artist_id）硬删除
    - artist_company_relations（artist_id）硬删除
    - music_videos.subject_artist_id 置空（含已软删 MV，避免残留）

    不级联软删专辑/歌曲/影像本身。
    """
    artist.soft_delete()
    memberships = db.execute(
        delete(GroupMembership).where(GroupMembership.artist_id == artist.id)
    ).rowcount or 0
    company_rels = db.execute(
        delete(ArtistCompanyRelation).where(
            ArtistCompanyRelation.artist_id == artist.id
        )
    ).rowcount or 0
    mv_subjects = db.execute(
        update(MusicVideo)
        .where(MusicVideo.subject_artist_id == artist.id)
        .values(subject_artist_id=None)
    ).rowcount or 0
    return {
        "memberships_deleted": int(memberships),
        "artist_company_relations_deleted": int(company_rels),
        "mv_subjects_cleared": int(mv_subjects),
    }


def soft_delete_group(db: Session, group: Group) -> dict[str, int]:
    """软删除组合，并静默拆除其关系边。

    - group_memberships（group_id）硬删除
    - group_company_relations（group_id）硬删除
    - 活跃小分队 parent_group_id=本组合 时置空（不自动软删小分队）

    不级联软删该组合下的专辑/歌曲/影像。
    """
    group.soft_delete()
    memberships = db.execute(
        delete(GroupMembership).where(GroupMembership.group_id == group.id)
    ).rowcount or 0
    company_rels = db.execute(
        delete(GroupCompanyRelation).where(
            GroupCompanyRelation.group_id == group.id
        )
    ).rowcount or 0
    # 仅清理仍活跃的小分队上级指针；已软删小分队保留原 parent 供考古
    subunits = db.execute(
        update(Group)
        .where(
            Group.parent_group_id == group.id,
            Group.deleted_at.is_(None),
            Group.id != group.id,
        )
        .values(parent_group_id=None)
    ).rowcount or 0
    return {
        "memberships_deleted": int(memberships),
        "group_company_relations_deleted": int(company_rels),
        "subunit_parents_cleared": int(subunits),
    }


def soft_delete_company(db: Session, company: Company) -> dict[str, int]:
    """软删除公司，并拆除艺人/组合对公司的关系行。"""
    company.soft_delete()
    artist_rels = db.execute(
        delete(ArtistCompanyRelation).where(
            ArtistCompanyRelation.company_id == company.id
        )
    ).rowcount or 0
    group_rels = db.execute(
        delete(GroupCompanyRelation).where(
            GroupCompanyRelation.company_id == company.id
        )
    ).rowcount or 0
    return {
        "artist_company_relations_deleted": int(artist_rels),
        "group_company_relations_deleted": int(group_rels),
    }


def soft_delete_album(db: Session, album: Any) -> dict[str, int]:
    """软删除专辑。当前体检不检查专辑指向已删主体的悬空，仅写 tombstone。"""
    album.soft_delete()
    return {}


def soft_delete_song(db: Session, song: Any) -> dict[str, int]:
    """软删除歌曲。不级联拆边（体检无对应悬空检查）。"""
    song.soft_delete()
    return {}


def soft_delete_music_video(db: Session, mv: Any) -> dict[str, int]:
    """软删除影像。主体指针随实体一起进墓碑，无需额外拆边。"""
    mv.soft_delete()
    return {}


def purge_dangling_edges(db: Session) -> dict[str, int]:
    """一次性清理「已软删实体」留下的悬空关系边。幂等。

    覆盖：
    - memberships：艺人或组合已软删 → 删除关系行
    - MV subject：艺人已软删且 MV 仍活跃 → subject_artist_id=NULL
    - 小分队上级：父组合已软删且小分队仍活跃 → parent_group_id=NULL
    - 公司关系：公司 / 艺人 / 组合任一侧已软删 → 删除关系行
    """
    deleted_artist_ids = select(Artist.id).where(Artist.deleted_at.is_not(None))
    deleted_group_ids = select(Group.id).where(Group.deleted_at.is_not(None))
    deleted_company_ids = select(Company.id).where(Company.deleted_at.is_not(None))

    memberships = db.execute(
        delete(GroupMembership).where(
            (GroupMembership.artist_id.in_(deleted_artist_ids))
            | (GroupMembership.group_id.in_(deleted_group_ids))
        )
    ).rowcount or 0

    mv_subjects = db.execute(
        update(MusicVideo)
        .where(
            MusicVideo.deleted_at.is_(None),
            MusicVideo.subject_artist_id.in_(deleted_artist_ids),
        )
        .values(subject_artist_id=None)
    ).rowcount or 0

    subunit_parents = db.execute(
        update(Group)
        .where(
            Group.deleted_at.is_(None),
            Group.parent_group_id.in_(deleted_group_ids),
        )
        .values(parent_group_id=None)
    ).rowcount or 0

    artist_company = db.execute(
        delete(ArtistCompanyRelation).where(
            (ArtistCompanyRelation.company_id.in_(deleted_company_ids))
            | (ArtistCompanyRelation.artist_id.in_(deleted_artist_ids))
        )
    ).rowcount or 0

    group_company = db.execute(
        delete(GroupCompanyRelation).where(
            (GroupCompanyRelation.company_id.in_(deleted_company_ids))
            | (GroupCompanyRelation.group_id.in_(deleted_group_ids))
        )
    ).rowcount or 0

    return {
        "memberships_deleted": int(memberships),
        "mv_subjects_cleared": int(mv_subjects),
        "subunit_parents_cleared": int(subunit_parents),
        "artist_company_relations_deleted": int(artist_company),
        "group_company_relations_deleted": int(group_company),
    }
