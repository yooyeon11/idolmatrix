"""Song / SongArtistRelation / Credits schema。"""

from __future__ import annotations

from datetime import date
from typing import Any, List, Optional

from app.schemas.common import EntityReadMixin, ORMModel


# ===== Song =====
class SongBase(ORMModel):
    name: str
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    release_date: Optional[date] = None
    song_type: Optional[str] = None
    release_artist_type: Optional[str] = None
    release_artist_id: Optional[int] = None
    duration: Optional[int] = None
    description: Optional[str] = None
    external_links: Optional[List[Any]] = None


class SongCreate(SongBase):
    pass


class SongUpdate(ORMModel):
    name: Optional[str] = None
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    release_date: Optional[date] = None
    song_type: Optional[str] = None
    release_artist_type: Optional[str] = None
    release_artist_id: Optional[int] = None
    duration: Optional[int] = None
    description: Optional[str] = None
    external_links: Optional[List[Any]] = None
    album_ids: Optional[List[int]] = None


class SongRead(SongBase, EntityReadMixin):
    id: int
    album_ids: List[int] = []
    completion_pct: Optional[int] = None
    # 关联视频数（列表/详情接口填充；三种关联口径合并去重，排除短视频）
    video_count: Optional[int] = None
    # 所属艺人/组合名称（列表/详情接口填充；按 SongArtistRelation.order 排序）
    relation_names: Optional[List[str]] = None


class SongBrief(EntityReadMixin):
    id: int
    name: str
    chinese_name: Optional[str] = None
    # ===== 以下字段只在「下拉 / 搜索」档（/songs/brief 的 with_owner / deep / album_hits）填 =====
    # 所属主体展示名（组合中文名 / 艺人中文名→艺名）；同名歌曲靠它区分
    owner: Optional[str] = None
    # 不是歌曲主名/中文名命中时给出：english_name / korean_name / alias
    matched_field: Optional[str] = None
    # 命中的具体值（别名原文 / 韩文名…），供前端写「别名「XXX」」
    matched_value: Optional[str] = None
    # 由「所属专辑名」而非歌曲名命中时给出
    matched_album_id: Optional[int] = None
    matched_album_name: Optional[str] = None


class SongFuzzyHit(ORMModel):
    """新建前查重用的模糊匹配命中项。"""

    id: int
    name: str
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    score: float


# ===== SongArtistRelation =====
class SongArtistRelationBase(ORMModel):
    song_id: int
    artist_id: Optional[int] = None
    group_id: Optional[int] = None
    role: str = "PrimaryArtist"
    order: int = 0


class SongArtistRelationCreate(SongArtistRelationBase):
    pass


class SongArtistRelationRead(SongArtistRelationBase):
    id: int


# ===== Credits =====
class CreditsBase(ORMModel):
    song_id: int
    artist_id: Optional[int] = None
    name: str
    role: str


class CreditsCreate(CreditsBase):
    pass


class CreditsRead(CreditsBase):
    id: int
