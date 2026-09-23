"""各实体的完成度（completion_pct）SQL 表达式。

每个函数返回一个标量 SQL 表达式（0–100 整数），用于 list API 的
SELECT 附加列和 ORDER BY 排序。计算规则：等权统计关键字段是否填写。
"""

from __future__ import annotations

from sqlalchemy import case, exists, func

from app.models.album import AlbumTrack
from app.models.artist import Artist
from app.models.company import ArtistCompanyRelation, GroupCompanyRelation
from app.models.group import Group
from app.models.membership import GroupMembership
from app.models.song import Song, SongArtistRelation
from app.models.album import Album


def song_completion_pct():
    """歌曲完成度（7 个关键字段，等权）。
    chinese_name / release_date / song_type / 发行主体 / duration / 关联艺人 / 所属专辑
    """
    has_artist = exists(1).where(SongArtistRelation.song_id == Song.id)
    has_album = exists(1).where(AlbumTrack.song_id == Song.id)
    return (
        case((Song.chinese_name.is_not(None), 1), else_=0)
        + case((Song.release_date.is_not(None), 1), else_=0)
        + case((Song.song_type.is_not(None), 1), else_=0)
        + case((Song.release_artist_type.is_not(None), 1), else_=0)
        + case((Song.duration.is_not(None), 1), else_=0)
        + case((has_artist, 1), else_=0)
        + case((has_album, 1), else_=0)
    ) * 100 / 7


def artist_completion_pct():
    """艺术家完成度（7 个关键字段，等权）。
    chinese_name / stage_name / gender / birth_date / debut_date / avatar_path / 组合经历
    """
    has_membership = exists(1).where(GroupMembership.artist_id == Artist.id)
    return (
        case((Artist.chinese_name.is_not(None), 1), else_=0)
        + case((Artist.stage_name.is_not(None), 1), else_=0)
        + case((Artist.gender.is_not(None), 1), else_=0)
        + case((Artist.birth_date.is_not(None), 1), else_=0)
        + case((Artist.debut_date.is_not(None), 1), else_=0)
        + case((Artist.avatar_path.is_not(None), 1), else_=0)
        + case((has_membership, 1), else_=0)
    ) * 100 / 7


def group_completion_pct():
    """组合完成度（7 个关键字段，等权）。
    chinese_name / group_type / gender_type / debut_date / avatar_path / 有成员 / sort_name
    """
    has_members = exists(1).where(
        GroupMembership.group_id == Group.id,
        GroupMembership.status == "Active",
    )
    return (
        case((Group.chinese_name.is_not(None), 1), else_=0)
        + case((Group.group_type.is_not(None), 1), else_=0)
        + case((Group.gender_type.is_not(None), 1), else_=0)
        + case((Group.debut_date.is_not(None), 1), else_=0)
        + case((Group.avatar_path.is_not(None), 1), else_=0)
        + case((has_members, 1), else_=0)
        + case((Group.sort_name.is_not(None), 1), else_=0)
    ) * 100 / 7


def album_completion_pct():
    """专辑完成度（6 个关键字段，等权）。
    chinese_name / release_date / album_type / 发行主体 / cover_path / 有曲目
    """
    has_tracks = exists(1).where(AlbumTrack.album_id == Album.id)
    return (
        case((Album.chinese_name.is_not(None), 1), else_=0)
        + case((Album.release_date.is_not(None), 1), else_=0)
        + case((Album.album_type.is_not(None), 1), else_=0)
        + case((Album.release_artist_type.is_not(None), 1), else_=0)
        + case((Album.cover_path.is_not(None), 1), else_=0)
        + case((has_tracks, 1), else_=0)
    ) * 100 / 6


def company_completion_pct():
    """公司完成度（5 个关键字段，等权）。
    chinese_name / company_type / description / 关联艺人 / 关联组合
    """
    from app.models.company import Company

    has_artist = exists(1).where(ArtistCompanyRelation.company_id == Company.id)
    has_group = exists(1).where(GroupCompanyRelation.company_id == Company.id)
    return (
        case((Company.chinese_name.is_not(None), 1), else_=0)
        + case((Company.company_type.is_not(None), 1), else_=0)
        + case((Company.description.is_not(None), 1), else_=0)
        + case((has_artist, 1), else_=0)
        + case((has_group, 1), else_=0)
    ) * 100 / 5
