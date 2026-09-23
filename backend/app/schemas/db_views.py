"""新版资料库视图 Schema（只读）。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, field_validator


class GroupMemberRow(BaseModel):
    membership_id: int
    artist_id: int
    artist_uid: Optional[str] = None
    name: str
    stage_name: Optional[str] = None
    avatar_path: Optional[str] = None
    join_date: Optional[str] = None
    leave_date: Optional[str] = None
    status: str
    positions: List[str] = []
    is_dangling: bool = False
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    birth_date: Optional[str] = None
    birth_place: Optional[str] = None
    gender: Optional[str] = None
    debut_date: Optional[str] = None
    occupation: Optional[str] = None
    description: Optional[str] = None
    tagline: Optional[str] = None
    social_media: Optional[dict] = None
    external_links: List[dict] = []


class GroupCompanyRow(BaseModel):
    relation_id: int
    company_id: int
    name: str
    role: Optional[str] = None
    status: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_dangling: bool = False


class GroupIssueCount(BaseModel):
    error: int
    warning: int
    hint: int


class GroupListRow(BaseModel):
    id: int
    uid: str
    name: str
    korean_name: Optional[str] = None
    chinese_name: Optional[str] = None
    debut_date: Optional[str] = None
    group_type: Optional[str] = None
    avatar_path: Optional[str] = None
    is_subunit: bool = False
    parent_name: Optional[str] = None
    members_active: int
    members_former: int
    member_sample: List[dict] = []
    members: List[GroupMemberRow] = []
    companies: List[GroupCompanyRow] = []
    works: dict
    completeness: int
    completeness_missing: List[str] = []
    issues: GroupIssueCount


class GroupListResponse(BaseModel):
    items: List[GroupListRow]
    total: int
    checklist: List[str]


class GroupWorkspaceGroup(BaseModel):
    id: int
    uid: str
    name: str
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    debut_date: Optional[str] = None
    group_type: Optional[str] = None
    gender_type: Optional[str] = None
    origin_country: Optional[str] = None
    description: Optional[str] = None
    tagline: Optional[str] = None
    avatar_path: Optional[str] = None
    banner_path: Optional[str] = None
    social_media: Optional[dict] = None
    hide_from_home: bool = False
    external_links: List[dict] = []


class WorkspaceSubUnit(BaseModel):
    uid: str
    id: int
    name: str
    group_type: Optional[str] = None
    completeness: int = 0
    members_active: int = 0
    member_ids: List[int] = []


class WorkspaceAlbum(BaseModel):
    id: int
    uid: str
    name: str
    release_date: Optional[str] = None
    album_type: Optional[str] = None
    track_count: int = 0
    video_count: int = 0


class GroupWorkspaceResponse(BaseModel):
    group: GroupWorkspaceGroup
    row: Optional[GroupListRow] = None
    sub_units: List[WorkspaceSubUnit] = []
    albums: List[WorkspaceAlbum] = []
    issues: List[dict] = []
    issue_counts: GroupIssueCount
    completeness_checklist: List[str] = []


class SourcePreviewRequest(BaseModel):
    urls: List[str] = []
    url: Optional[str] = None  # 兼容单链接写法
    step: Optional[str] = None  # 1-6 或 identity/subunits/members/member_details/albums/tracks；省略=全量
    targets: Optional[List[str]] = None  # 搜索补全：多选目标，优先于 step

    @field_validator("step", mode="before")
    @classmethod
    def _coerce_step(cls, v):
        if v is None or v == "":
            return None
        return str(v)


class SourceDiffItem(BaseModel):
    field: str
    label: str
    current: Optional[str] = None
    proposed: str
    sources: List[str] = []
    source_urls: List[str] = []
    conflict: bool = False
    is_new: bool = False
    same: bool = False


class SourceMemberProposal(BaseModel):
    action: str  # create / link / exists / enrich
    name: str
    korean_name: Optional[str] = None
    join_date: Optional[str] = None
    leave_date: Optional[str] = None
    status: str
    sources: List[str] = []
    artist_id: Optional[int] = None
    artist_uid: Optional[str] = None
    positions: Optional[List[str]] = None
    artist_fields: dict = {}
    # 字段来源：ai | fandom | wikidata | wikipedia | baidu | crawl
    field_origins: dict = {}
    ai_basis: Optional[str] = None
    possible_duplicate_of: List[str] = []


class SourceCompanyProposal(BaseModel):
    action: str  # create / link / exists
    name: str
    company_id: Optional[int] = None
    role: Optional[str] = None
    sources: List[str] = []


class SourceSubunitProposal(BaseModel):
    action: str  # create / exists
    name: str
    member_names: List[str] = []
    sources: List[str] = []


class SourceAlbumProposal(BaseModel):
    action: str  # create / exists / fill_tracks / update
    name: str
    year: Optional[str] = None

    @field_validator("year", mode="before")
    @classmethod
    def _coerce_year(cls, v):
        if v is None or v == "":
            return None
        return str(v)
    release_date: Optional[str] = None
    track_count: Optional[int] = None
    record_type: Optional[str] = None
    extractor: Optional[str] = None
    external_id: Optional[str] = None
    cover_url: Optional[str] = None
    sources: List[str] = []
    album_id: Optional[int] = None
    tracks_preview: List[dict] = []
    db_release_date: Optional[str] = None
    patch: dict = {}
    candidates: List[dict] = []
    needs_tracks: bool = False


class SourcePreviewResponse(BaseModel):
    sources: List[dict] = []
    items: List[SourceDiffItem]
    member_proposals: List[SourceMemberProposal] = []
    subunit_proposals: List[SourceSubunitProposal] = []
    company_proposals: List[SourceCompanyProposal] = []
    album_proposals: List[SourceAlbumProposal] = []
    single_proposals: List[SourceAlbumProposal] = []
    album_note: Optional[str] = None
    extra: dict = {}
    errors: List[str] = []


class TracklistPreviewRequest(BaseModel):
    album_id: int
    external_id: str


class TracklistPreviewResponse(BaseModel):
    album_id: int
    album_name: str
    external_id: str
    release_date: Optional[str] = None
    tracks_preview: List[dict] = []


class SourceApplyRequest(BaseModel):
    fields: dict = {}
    members: List[dict] = []
    albums: List[dict] = []
    subunits: List[dict] = []
    companies: List[dict] = []
    source_urls: List[str] = []
    step: Optional[str] = None  # 与 preview 同；省略=全量（向后兼容）

    @field_validator("step", mode="before")
    @classmethod
    def _coerce_step(cls, v):
        if v is None or v == "":
            return None
        return str(v)


class SourceApplyResponse(BaseModel):
    applied: dict


class SourceCandidate(BaseModel):
    source_type: str
    title: str
    snippet: str = ""
    url: str


class SourceSearchResponse(BaseModel):
    items: List[SourceCandidate]


class ArtistMembershipRow(BaseModel):
    membership_id: int
    group_id: int
    group_uid: str
    group_name: str
    group_type: Optional[str] = None
    join_date: Optional[str] = None
    leave_date: Optional[str] = None
    status: str
    positions: List[str] = []


class ArtistWorkspaceArtist(BaseModel):
    id: int
    uid: str
    name: str
    korean_name: Optional[str] = None
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    stage_name: Optional[str] = None
    birth_date: Optional[str] = None
    birth_place: Optional[str] = None
    gender: Optional[str] = None
    debut_date: Optional[str] = None
    occupation: Optional[str] = None
    description: Optional[str] = None
    tagline: Optional[str] = None
    avatar_path: Optional[str] = None
    banner_path: Optional[str] = None
    social_media: Optional[dict] = None
    hide_from_home: bool = False
    external_links: List[dict] = []


class ArtistWorkspaceResponse(BaseModel):
    artist: ArtistWorkspaceArtist
    memberships: List[ArtistMembershipRow] = []
    companies: List[dict] = []
    albums: List[WorkspaceAlbum] = []
    songs: List[WorkspaceArtistSong] = []
    songs_count: int = 0
    videos: List[WorkspaceVideoBrief] = []
    completeness: int = 0
    completeness_missing: List[str] = []
    issues: List[dict] = []
    issue_counts: dict = {}
    completeness_checklist: List[str] = []


# ===== 资料库列表：艺人 / 专辑 / 歌曲（只读，与组合列表同口径）=====


class ArtistGroupBrief(BaseModel):
    group_id: int
    group_uid: str
    name: str
    status: str


class ArtistListRow(BaseModel):
    id: int
    uid: str
    name: str
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    stage_name: Optional[str] = None
    birth_date: Optional[str] = None
    debut_date: Optional[str] = None
    gender: Optional[str] = None
    avatar_path: Optional[str] = None
    groups: List[ArtistGroupBrief] = []
    works: dict
    completeness: int
    completeness_missing: List[str] = []
    issues: GroupIssueCount


class ArtistListResponse(BaseModel):
    items: List[ArtistListRow]
    total: int
    checklist: List[str]


class AlbumListRow(BaseModel):
    id: int
    uid: str
    name: str
    chinese_name: Optional[str] = None
    korean_name: Optional[str] = None
    album_type: Optional[str] = None
    release_date: Optional[str] = None
    cover_path: Optional[str] = None
    release_artist_type: Optional[str] = None
    release_artist_name: Optional[str] = None
    release_artist_uid: Optional[str] = None
    works: dict
    completeness: int
    completeness_missing: List[str] = []
    issues: GroupIssueCount


class AlbumListResponse(BaseModel):
    items: List[AlbumListRow]
    total: int
    checklist: List[str]


class SongPerformerBrief(BaseModel):
    subject_type: str
    subject_id: int
    subject_uid: Optional[str] = None
    name: str
    role: str


class SongListRow(BaseModel):
    id: int
    uid: str
    name: str
    chinese_name: Optional[str] = None
    korean_name: Optional[str] = None
    song_type: Optional[str] = None
    release_date: Optional[str] = None
    duration: Optional[int] = None
    performers: List[SongPerformerBrief] = []
    works: dict
    completeness: int
    completeness_missing: List[str] = []
    issues: GroupIssueCount


class SongListResponse(BaseModel):
    items: List[SongListRow]
    total: int
    checklist: List[str]


class AlbumWorkspaceSubject(BaseModel):
    type: str
    id: int
    uid: str
    name: str


class AlbumWorkspaceLabel(BaseModel):
    id: int
    name: str


class AlbumWorkspaceTrack(BaseModel):
    track_id: int
    song_id: Optional[int] = None
    song_uid: Optional[str] = None
    song_name: Optional[str] = None
    song_chinese_name: Optional[str] = None
    song_duration: Optional[int] = None
    disc_number: int = 1
    track_number: int = 0
    is_dangling: bool = False


class AlbumWorkspaceVideo(BaseModel):
    id: int
    uid: str
    name: str
    video_type: Optional[str] = None


class AlbumWorkspaceAlbum(BaseModel):
    id: int
    uid: str
    name: str
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    release_date: Optional[str] = None
    album_type: Optional[str] = None
    description: Optional[str] = None
    cover_path: Optional[str] = None


class AlbumWorkspaceResponse(BaseModel):
    album: AlbumWorkspaceAlbum
    subject: Optional[AlbumWorkspaceSubject] = None
    label: Optional[AlbumWorkspaceLabel] = None
    tracks: List[AlbumWorkspaceTrack] = []
    videos: List[AlbumWorkspaceVideo] = []
    completeness: int = 0
    completeness_missing: List[str] = []
    issues: List[dict] = []
    issue_counts: dict = {}
    completeness_checklist: List[str] = []


class SongWorkspacePerformer(BaseModel):
    relation_id: int
    subject_type: str
    subject_id: Optional[int] = None
    subject_uid: Optional[str] = None
    name: str
    role: str
    order: int = 0
    is_dangling: bool = False


class SongWorkspaceAlbum(BaseModel):
    track_id: int
    id: int
    uid: str
    name: str
    release_date: Optional[str] = None
    album_type: Optional[str] = None
    disc_number: int = 1
    track_number: int = 0


class SongWorkspaceVideo(BaseModel):
    id: int
    uid: str
    name: str
    video_type: Optional[str] = None


class SongWorkspaceCredit(BaseModel):
    id: int
    name: str
    role: str
    artist_id: Optional[int] = None
    artist_name: Optional[str] = None


class SongWorkspaceSong(BaseModel):
    id: int
    uid: str
    name: str
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    song_type: Optional[str] = None
    release_date: Optional[str] = None
    duration: Optional[int] = None
    description: Optional[str] = None


class SongWorkspaceResponse(BaseModel):
    song: SongWorkspaceSong
    performers: List[SongWorkspacePerformer] = []
    albums: List[SongWorkspaceAlbum] = []
    videos: List[SongWorkspaceVideo] = []
    credits: List[SongWorkspaceCredit] = []
    completeness: int = 0
    completeness_missing: List[str] = []
    issues: List[dict] = []
    issue_counts: dict = {}
    completeness_checklist: List[str] = []


class WorkspaceArtistSong(BaseModel):
    id: int
    uid: str
    name: str
    chinese_name: Optional[str] = None
    release_date: Optional[str] = None
    duration: Optional[int] = None


class WorkspaceVideoBrief(BaseModel):
    id: int
    uid: str
    name: str
    video_type: Optional[str] = None


class FieldSuggestRequest(BaseModel):
    field: str


class FieldSuggestResponse(BaseModel):
    field: str
    value: Optional[str] = None
    source_label: Optional[str] = None
    source_url: Optional[str] = None


class ArtistApplyResult(BaseModel):
    fields: List[str] = []
    fields_skipped_locked: List[str] = []


class ArtistApplyResponse(BaseModel):
    applied: ArtistApplyResult
