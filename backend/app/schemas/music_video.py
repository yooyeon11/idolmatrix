"""MusicVideo schema —— 项目最核心实体的 IO 契约。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Optional

from pydantic import field_validator

from app.schemas.album import AlbumBrief
from app.schemas.artist import ArtistBrief
from app.schemas.common import EntityReadMixin, ORMModel
from app.schemas.group import GroupBrief
from app.schemas.song import SongBrief


class _SourceKeyNorm(ORMModel):
    """source_platform / source_id 的空白串统一规范化为 None。

    空串不是 NULL：会占用 (source_platform, source_id) 部分唯一索引的
    ('', '') 键——一旦某条无元数据视频以空串入库，后续所有裸文件
    （无 info.json）入库都会撞唯一索引报 409「并发入库（重复）」。
    而入库预检对空串做 truthy 判断会直接跳过，拦不住该冲突，
    必须在 schema 入口统一清洗为 NULL（NULL 不进部分索引）。
    """

    @field_validator(
        "source_platform", "source_id", mode="before", check_fields=False
    )
    @classmethod
    def _blank_source_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v


class MusicVideoTrackInput(ORMModel):
    """入库/编辑时的曲目行：一首歌 + 该歌所属的一张或多张专辑。"""

    song_id: int
    album_ids: List[int] = []


class MusicVideoTrackRead(ORMModel):
    song_id: int
    album_ids: List[int] = []


class MusicVideoBase(_SourceKeyNorm):
    name: str
    original_title: Optional[str] = None
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    aliases: Optional[List[str]] = None

    song_id: Optional[int] = None  # 派生：曲目第一首，写入时由 tracks 覆盖
    video_type: str = "Other"  # 派生：video_types[0]
    video_types: Optional[List[str]] = None  # 类型唯一来源
    song_ids: Optional[List[int]] = None  # 派生：曲目歌曲列表
    album_ids: Optional[List[int]] = None  # 派生：曲目专辑去重
    tracks: Optional[List[MusicVideoTrackInput]] = None  # 歌曲/专辑唯一来源

    artist_ids: Optional[List[int]] = None
    group_ids: Optional[List[int]] = None
    subject_artist_id: Optional[int] = None

    event_name: Optional[str] = None
    performance_date: Optional[date] = None
    # 是否短视频：未显式标记时按 video_types 是否含 ShortVideo 自动派生（与时长无关，v3.2.32）
    is_short: Optional[bool] = None
    # 是否为 solo 表演（表演者为独立艺人而非组合表演），AI 辅助判断，供自动整理入库使用
    is_solo: Optional[bool] = None
    release_date: Optional[date] = None
    published_date: Optional[date] = None

    source_platform: Optional[str] = None
    source_id: Optional[str] = None
    source_url: Optional[str] = None
    original_uploader: Optional[str] = None

    # 技术字段：通常由程序写入，但允许更新
    duration: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    frame_rate: Optional[float] = None
    bitrate: Optional[int] = None
    file_size: Optional[int] = None

    external_links: Optional[List[Any]] = None
    description: Optional[str] = None
    # 中文简介：由 AI 根据视频内容生成
    chinese_description: Optional[str] = None


class MusicVideoCreate(MusicVideoBase):
    # 创建时通常只需元数据；文件信息在入库扫描时回填
    file_name: Optional[str] = None
    file_path: Optional[str] = None


class MusicVideoUpdate(_SourceKeyNorm):
    name: Optional[str] = None
    original_title: Optional[str] = None
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    song_id: Optional[int] = None
    video_type: Optional[str] = None
    video_types: Optional[List[str]] = None
    song_ids: Optional[List[int]] = None
    album_ids: Optional[List[int]] = None
    tracks: Optional[List[MusicVideoTrackInput]] = None
    artist_ids: Optional[List[int]] = None
    group_ids: Optional[List[int]] = None
    subject_artist_id: Optional[int] = None
    event_name: Optional[str] = None
    performance_date: Optional[date] = None
    is_short: Optional[bool] = None
    is_solo: Optional[bool] = None
    release_date: Optional[date] = None
    published_date: Optional[date] = None
    source_platform: Optional[str] = None
    source_id: Optional[str] = None
    source_url: Optional[str] = None
    original_uploader: Optional[str] = None
    external_links: Optional[List[Any]] = None
    description: Optional[str] = None
    chinese_description: Optional[str] = None
    ingestion_status: Optional[str] = None


class MusicVideoRead(MusicVideoBase, EntityReadMixin):
    id: int
    file_name: Optional[str] = None
    file_hash: Optional[str] = None
    file_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    # 用户手动选帧封面标记：True 时自动刷新不再覆盖
    cover_manual: bool = False
    # 封面焦点（0~1 归一化的人脸质心；供前端裁切时保持人脸居中）
    focus_x: Optional[float] = None
    focus_y: Optional[float] = None
    ingestion_status: str = "incoming"
    # 关联名称（由路由层用 joinedload 预加载后填充，避免前端二次查询）
    song_name: Optional[str] = None
    song_chinese_name: Optional[str] = None
    subject_artist_name: Optional[str] = None
    subject_artist_chinese_name: Optional[str] = None
    # 多对多关联（selectinload 后直接序列化）
    songs: List[SongBrief] = []
    albums: List[AlbumBrief] = []
    artists: List[ArtistBrief] = []
    groups: List[GroupBrief] = []
    tracks: List[MusicVideoTrackRead] = []
    # 翻唱原唱展示名：由曲目歌曲主唱/发行主体推导，不写入、不建关联
    cover_from: List[str] = []


class MusicVideoBrief(EntityReadMixin):
    """列表用精简版。"""

    id: int
    name: str
    video_type: str
    duration: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    thumbnail_path: Optional[str] = None
    # 封面焦点（0~1 归一化的人脸质心；供前端裁切时保持人脸居中）
    focus_x: Optional[float] = None
    focus_y: Optional[float] = None
    ingestion_status: str = "incoming"
    song_id: Optional[int] = None
    subject_artist_id: Optional[int] = None
    # 关联名称（卡片视图用）
    song_name: Optional[str] = None
    song_chinese_name: Optional[str] = None
    subject_artist_name: Optional[str] = None
    subject_artist_chinese_name: Optional[str] = None
    performance_date: Optional[date] = None
    release_date: Optional[date] = None


class MatchHintEntity(ORMModel):
    """本地匹配命中的实体（前端 preset 回显用）。"""

    id: int
    name: str
    chinese_name: Optional[str] = None


class MatchHintTrack(ORMModel):
    """本地匹配命中的曲目行：专辑来自库内 AlbumTrack 关系，非 AI 猜测。"""

    song_id: int
    song_name: str
    chinese_name: Optional[str] = None
    album_ids: List[int] = []
    # high = 标题/文件名命中。简介命中不再自动写入曲目。
    confidence: str = "high"
    # 命中来源字段：title / fulltitle / file_name
    source: str = "title"


class MatchHintsResult(ORMModel):
    """库驱动本地匹配结果（先建库后入库场景的确定性预填充）。"""

    artists: List[MatchHintEntity] = []
    groups: List[MatchHintEntity] = []
    albums: List[MatchHintEntity] = []
    tracks: List[MatchHintTrack] = []
    notices: List[str] = []
    # 自动关联策略固定严格，见 schemas/app_settings.py::INGEST_MATCH_MODES
    match_mode: str = "strict"


class ScannedFileItem(ORMModel):
    """扫描结果项：尚未入库的待整理文件。"""

    path: str
    file_name: str
    file_size: Optional[int] = None
    duration: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    file_hash: Optional[str] = None
    is_duplicate: bool = False


class IngestRequest(ORMModel):
    """入库请求：把扫描到的文件正式入库（写库 + 移动文件）。"""

    file_path: str
    music_video: MusicVideoCreate
    move_file: bool = True
    # 用户指定的入库目标相对路径（相对 library_dir，POSIX 格式）。
    # 为空时后端按默认模板自动生成建议路径。
    destination_rel: Optional[str] = None


class PathPreviewRequest(ORMModel):
    """计算入库建议路径的请求。"""

    file_name: str
    group_name: Optional[str] = None
    title: Optional[str] = None
    video_type: Optional[str] = None
    # 自动整理规则需要的关联 id / 类型 / 短视频标记
    is_short: Optional[bool] = None
    is_solo: Optional[bool] = None
    duration: Optional[int] = None  # 秒；仅作技术元数据，不参与 is_short 判定
    performance_date: Optional[str] = None  # ISO 格式（YYYY-MM-DD），串烧目录按 yymmdd 使用
    song_ids: Optional[List[int]] = None
    # 「待新建」草稿歌曲名（库里还没有，入库时才创建）：
    # 与入库同口径计入串烧判定（入库会先建歌再算路径，预览需一致）
    draft_song_names: Optional[List[str]] = None
    # 「待新建」草稿艺人/组合/直拍对象名：库里还没有，入库时会先创建再算路径；
    # 预览需与入库同口径（否则 AI 识别全新艺人时预览会错误平铺根目录）
    draft_artist_names: Optional[List[str]] = None
    draft_group_names: Optional[List[str]] = None
    draft_subject_artist_name: Optional[str] = None
    artist_ids: Optional[List[int]] = None
    group_ids: Optional[List[int]] = None
    video_types: Optional[List[str]] = None


class PathPreviewResult(ORMModel):
    """入库建议路径结果（相对与绝对路径均为 POSIX 格式）。"""

    relative_path: str
    absolute_path: str
    # 自动整理未命中时的提示（如未关联艺人/组合，无法自动归档）
    notice: Optional[str] = None


class ReorganizePlanItem(ORMModel):
    """存量重整 dry-run 条目：当前路径与规则建议不一致（或规则不适用）。"""

    id: int
    name: str
    current_path: str
    suggested_path: Optional[str] = None
    # True = 规则不适用（缺主体关联），需补关联后重试
    rule_missed: bool = False


class ReorganizeExecuteRequest(ORMModel):
    """按规则建议路径批量移动已入库视频的请求。"""

    ids: List[int]


class ReorganizeMoveResult(ORMModel):
    """单条移动结果。"""

    id: int
    ok: bool
    previous_path: Optional[str] = None
    file_path: Optional[str] = None
    error: Optional[str] = None


class ReorganizeResult(ORMModel):
    """批量移动汇总结果。"""

    moved: int
    failed: int
    results: List[ReorganizeMoveResult]


class BuildTitleRequest(ORMModel):
    """按当前表单关联字段重建标题（name）。"""

    performance_date: Optional[str] = None  # ISO 格式（YYYY-MM-DD）
    published_date: Optional[str] = None  # ISO 格式（YYYY-MM-DD）
    song_ids: Optional[List[int]] = None
    artist_ids: Optional[List[int]] = None
    group_ids: Optional[List[int]] = None
    subject_artist_id: Optional[int] = None
    event_name: Optional[str] = None
    video_types: Optional[List[str]] = None


class BuildTitleResult(ORMModel):
    """重建后的标题；艺人缺失时 name 为空串（前端保留原值）。"""

    name: str


class IngestResult(ORMModel):
    music_video_id: int
    moved: bool
    destination_path: Optional[str] = None


class VideoPathSuggestion(ORMModel):
    """已入库视频的存储路径建议（详情页编辑用）。"""

    current_path: str
    # 自动整理规则不适用时为 None
    suggested_path: Optional[str] = None
    absolute_path: Optional[str] = None
    # 规则被阻断 / 不适用时的具体原因（如重名艺人缺中文名），前端直接展示
    notice: Optional[str] = None


class VideoRelocateRequest(ORMModel):
    """更改存储路径请求：destination_rel 为空时由系统按自动整理规则推导。"""

    destination_rel: Optional[str] = None


class VideoRelocateResult(ORMModel):
    """更改存储路径结果：返回移动前后相对路径及一并迁移的伴随文件名。"""

    previous_path: str
    file_path: str
    moved_sidecars: List[str] = []


class CoverFramesRequest(ORMModel):
    """随机候选帧请求：exclude 为已抽过的时间点（秒），「换一批」时排除重复。"""

    count: int = 8
    exclude: List[float] = []


class CoverFrameItem(ORMModel):
    """单张候选帧：at 为时间点（秒），name 用于图片访问 URL。"""

    at: float
    name: str


class CoverFramesResponse(ORMModel):
    items: List[CoverFrameItem] = []


class CoverFrameApplyRequest(ORMModel):
    """应用候选帧为正式封面。"""

    at_seconds: float


class FocusResponse(ORMModel):
    """封面焦点（0~1 归一化人脸质心）；检测不到人脸时两个坐标为 None。"""

    focus_x: Optional[float] = None
    focus_y: Optional[float] = None


class MissingVideoItem(ORMModel):
    """失效视频预览条目。"""

    id: int
    name: str
    file_path: Optional[str] = None
    ingestion_status: str


class LibraryCleanupResult(ORMModel):
    """失效视频扫描 / 清理结果。"""

    scanned: int
    cleaned: int
    missing: int
    skipped_unmounted: bool = False
    unmounted_roots: List[str] = []
    items: List[MissingVideoItem] = []
    files_purged: int = 0


class FolderChild(ORMModel):
    """浏览页某一层子文件夹。"""

    name: str
    count: int
    cover_id: Optional[int] = None


class VideoCollectionCreate(ORMModel):
    name: str
    description: Optional[str] = None


class VideoCollectionUpdate(ORMModel):
    name: Optional[str] = None
    description: Optional[str] = None


class VideoCollectionRead(EntityReadMixin):
    id: int
    name: str
    description: Optional[str] = None
    video_count: int = 0
    cover_video_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class VideoCollectRequest(ORMModel):
    video_id: Optional[int] = None
    video_uid: Optional[str] = None


class VideoMembershipsResponse(ORMModel):
    memberships: dict[int, list[int]]


class FolderBrowseResult(ORMModel):
    """按库内 file_path 聚合的文件夹浏览结果（文件夹完整，视频分页）。"""

    prefix: str
    folders: List[FolderChild] = []
    videos: List[MusicVideoRead] = []
    video_total: int = 0
    page: int = 1
    page_size: int = 24


class ResolutionFacet(ORMModel):
    """某一分辨率档位在当前筛选条件下的条数。"""

    key: str
    count: int


class ResolutionFacetResponse(ORMModel):
    """浏览页「分辨率」筛选菜单的数据源：只返回有条数的档位，空档位不展示。"""

    items: List[ResolutionFacet] = []


class IncomingInfoRequest(ORMModel):
    """读取待整理文件 info.json 的请求。"""

    file_path: str


class IncomingInfoItem(ORMModel):
    """info.json 精简视图：只含标题/来源/简介/视频数据，不含评论字幕等。"""

    title: Optional[str] = None
    fulltitle: Optional[str] = None
    webpage_url: Optional[str] = None
    id: Optional[str] = None
    extractor: Optional[str] = None
    uploader: Optional[str] = None
    uploader_id: Optional[str] = None
    channel: Optional[str] = None
    upload_date: Optional[str] = None
    timestamp: Optional[int] = None
    description: Optional[str] = None
    duration: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    resolution: Optional[str] = None
    fps: Optional[float] = None
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    filesize_approx: Optional[int] = None
    format_id: Optional[str] = None
    format: Optional[str] = None
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    thumbnail: Optional[str] = None
    has_local_cover: bool = False
