"""Album / AlbumTrack schema。"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from app.schemas.common import EntityReadMixin, ORMModel


class AlbumBase(ORMModel):
    name: str
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    release_date: Optional[date] = None
    album_type: Optional[str] = None
    release_artist_type: Optional[str] = None
    release_artist_id: Optional[int] = None
    label_id: Optional[int] = None
    description: Optional[str] = None
    # 本地封面相对路径（站点获取/上传）
    cover_path: Optional[str] = None


class AlbumCreate(AlbumBase):
    pass


class AlbumUpdate(ORMModel):
    name: Optional[str] = None
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    release_date: Optional[date] = None
    album_type: Optional[str] = None
    release_artist_type: Optional[str] = None
    release_artist_id: Optional[int] = None
    label_id: Optional[int] = None
    description: Optional[str] = None
    cover_path: Optional[str] = None


class AlbumRead(AlbumBase, EntityReadMixin):
    id: int
    completion_pct: Optional[int] = None
    # 已收录曲目数（列表/详情接口填充）
    track_count: Optional[int] = None


class AlbumBrief(EntityReadMixin):
    """下拉/搜索用精简版。"""

    id: int
    name: str
    chinese_name: Optional[str] = None
    album_type: Optional[str] = None
    cover_path: Optional[str] = None
    # 专辑自身的发行主体展示名（release_artist_*，未填则 None）。下拉提示语
    # 「主体 - 专辑 - 歌曲」在**关联不到任何歌**时（未被曲目行选中的候选），
    # 用它退化成「主体 - 专辑」，至少让用户看出这是谁的专辑。
    owner: Optional[str] = None
    # 由「专辑内曲目名」而非专辑名命中时，给出造成命中的那首歌及其主体名，
    # 供前端在选项上拼「主体 - 专辑 - 歌曲」提示（`song_hits=true` 才会填）。
    matched_song_id: Optional[int] = None
    matched_song_name: Optional[str] = None
    matched_song_owner: Optional[str] = None


class CoverSearchRequest(ORMModel):
    """专辑封面搜索请求。"""

    query: str
    source: str = "netease"  # netease / itunes


class CoverSearchItem(ORMModel):
    """专辑封面候选：来自网易云 / iTunes 搜索结果。"""

    id: str
    name: str
    artist: Optional[str] = None
    cover_url: str
    year: Optional[str] = None
    source: str


class CoverApplyRequest(ORMModel):
    """应用选中的封面 URL 到专辑。"""

    cover_url: str


class AlbumFuzzyHit(ORMModel):
    """新建前查重用的模糊匹配命中项。"""

    id: int
    name: str
    chinese_name: Optional[str] = None
    album_type: Optional[str] = None
    score: float


class AlbumTrackBase(ORMModel):
    album_id: int
    song_id: int
    disc_number: int = 1
    track_number: int


class AlbumTrackCreate(AlbumTrackBase):
    pass


class AlbumTrackRead(AlbumTrackBase):
    id: int


class AlbumTrackDetailRead(AlbumTrackRead):
    """曲目行 + 关联歌曲摘要（专辑详情曲目单用）。"""

    song_name: Optional[str] = None
    song_chinese_name: Optional[str] = None
    song_duration: Optional[int] = None
    song_uid: Optional[str] = None


# ===== 专辑曲目外部导入 =====
class AlbumExternalCandidate(ORMModel):
    """外部专辑搜索候选（iTunes / Deezer）。"""

    external_id: str
    name: str
    artist: Optional[str] = None
    cover_url: Optional[str] = None
    year: Optional[str] = None
    source: str
    track_count: Optional[int] = None


class AlbumTracklistItem(ORMModel):
    disc_number: int = 1
    track_number: int
    name: str
    duration: Optional[int] = None  # 秒
    external_track_id: Optional[str] = None


class AlbumTracklist(ORMModel):
    external_id: str
    name: str
    artist: Optional[str] = None
    cover_url: Optional[str] = None
    year: Optional[str] = None
    source: str
    tracks: List[AlbumTracklistItem] = []


class AlbumImportTrackItem(ORMModel):
    """待导入的一条曲目；matched_song_id 由预览回填，用户也可手动指定。"""

    name: str
    disc_number: int = 1
    track_number: int
    duration: Optional[int] = None
    external_track_id: Optional[str] = None
    matched_song_id: Optional[int] = None
    # 存疑条目的人工结论：明确要求新建（不自动匹配库内同名歌）
    force_new: bool = False


class AlbumImportReviewVideo(ORMModel):
    """存疑候选的关联视频摘要（供人工确认）。"""

    video_id: int
    video_name: Optional[str] = None


class AlbumImportReviewCandidate(ORMModel):
    """存疑候选歌：同名但主体信号不足，需人工确认是否为同一首。"""

    song_id: int
    song_name: str
    song_chinese_name: Optional[str] = None
    song_duration: Optional[int] = None
    videos: List[AlbumImportReviewVideo] = []


class AlbumImportPreviewItem(ORMModel):
    """导入前查重结果三态 + 位置占用预检。

    match_status：
      - match：可自动关联已有歌曲（主体佐证命中）
      - new：将新建（库内无同名歌，或候选明确归属其他主体）
      - review：存疑，需人工确认（附候选列表）
      - in-album：该歌曲已在此专辑中
    position_status：free（位置空闲）/ same（位置已是该歌，幂等跳过）/
      conflict（位置被其他歌占用）
    """

    index: int
    name: str
    disc_number: int
    track_number: int
    duration: Optional[int] = None
    match_status: str  # match / new / review / in-album
    matched_song_id: Optional[int] = None
    matched_song_name: Optional[str] = None
    matched_song_duration: Optional[int] = None
    position_status: Optional[str] = None
    position_song_name: Optional[str] = None
    review_candidates: List[AlbumImportReviewCandidate] = []


class AlbumImportTracksRequest(ORMModel):
    source: Optional[str] = None  # itunes / deezer，用于 external_links
    tracks: List[AlbumImportTrackItem] = []


class AlbumImportTrackResult(ORMModel):
    name: str
    song_id: Optional[int] = None
    detail: Optional[str] = None  # 失败/跳过原因


class AlbumImportTracksResult(ORMModel):
    linked: List[AlbumImportTrackResult] = []
    created: List[AlbumImportTrackResult] = []
    failed: List[AlbumImportTrackResult] = []
    # 非失败的安全跳过：位置已是该歌（幂等）/ 歌已在专辑 / 存疑未人工确认
    skipped: List[AlbumImportTrackResult] = []


class AlbumAlignTrackResult(ORMModel):
    """单条曲目对齐明细；action：aligned（按外部落位）/ same（本就就位）/
    appended（未匹配外部曲目单，保持原位或顺延碟末）/ skipped（同名歧义，保守不动）。"""

    name: str
    song_id: Optional[int] = None
    disc_number: Optional[int] = None
    track_number: Optional[int] = None
    action: str
    detail: Optional[str] = None


class AlbumAlignTracksResult(ORMModel):
    aligned: List[AlbumAlignTrackResult] = []
    appended: List[AlbumAlignTrackResult] = []
    skipped: List[AlbumAlignTrackResult] = []


class AlbumAlignTracksRequest(ORMModel):
    """按外部曲目单调序：tracks 与导入预览同构（仅取 name/disc/track）。"""

    tracks: List[AlbumImportTrackItem] = []
