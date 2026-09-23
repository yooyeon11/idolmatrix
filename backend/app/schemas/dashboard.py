"""Dashboard 统计 schema。"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.schemas.common import ORMModel


class DashboardStats(ORMModel):
    total_videos: int = 0
    incoming_count: int = 0
    library_count: int = 0
    total_artists: int = 0
    total_groups: int = 0
    total_songs: int = 0
    total_albums: int = 0
    video_type_breakdown: Dict[str, int] = {}


class StorageUsage(ORMModel):
    library_size_bytes: int = 0
    derived_size_bytes: int = 0


class StatsOverview(ORMModel):
    library_count: int = 0
    incoming_count: int = 0
    shorts_count: int = 0
    last_30d_count: int = 0
    total_duration_seconds: int = 0
    total_size_bytes: int = 0
    photo_count: int = 0
    type_breakdown: Dict[str, int] = {}
    # 分辨率构成，键为档位：8k / 4k / 2k / 1080p / 720p / 480p / 360p / 240p / sd / unknown
    resolution_breakdown: Dict[str, int] = {}


class StatsRankItem(ORMModel):
    id: int
    uid: str
    name: str
    chinese_name: Optional[str] = None
    extra: Optional[str] = None
    avatar_path: Optional[str] = None
    video_count: int = 0
    duration_seconds: int = 0


class StatsWatchBlock(ORMModel):
    available: bool = False
    play_count: int = 0
    seconds_watched: int = 0
    completed_count: int = 0
    artists: List[StatsRankItem] = []
    groups: List[StatsRankItem] = []
    subject_artists: List[StatsRankItem] = []
    songs: List[StatsRankItem] = []
    videos: List[StatsRankItem] = []
    items: List[StatsRankItem] = []


class WatchPingRequest(ORMModel):
    music_video_id: int
    position: float = 0.0
    duration: Optional[float] = None
    playing: bool = False
    ended: bool = False


class WatchPingResult(ORMModel):
    play_id: int
    music_video_id: Optional[int] = None
    seconds_watched: float = 0.0
    completed: bool = False


class LibraryStats(ORMModel):
    overview: StatsOverview
    range: str = "all"
    include_shorts: bool = False
    artists: List[StatsRankItem] = []
    groups: List[StatsRankItem] = []
    subject_artists: List[StatsRankItem] = []
    songs: List[StatsRankItem] = []
    watch: StatsWatchBlock = StatsWatchBlock()


class RecapDay(ORMModel):
    """热力日历单日：date 为本地时区 ISO 日期。"""

    date: str
    count: int = 0


class RecapHour(ORMModel):
    """观看时钟单小时。"""

    hour: int
    count: int = 0


class RecapMonth(ORMModel):
    """月度趋势单月：month 为 YYYY-MM。"""

    month: str
    count: int = 0


class RecapRecord(ORMModel):
    """馆藏之最的单条视频。"""

    id: int
    uid: str
    name: str
    chinese_name: Optional[str] = None
    video_type: Optional[str] = None
    created_at: Optional[str] = None
    duration: int = 0
    file_size: int = 0


class StatsRecap(ORMModel):
    heatmap: List[RecapDay] = []
    clock: List[RecapHour] = []
    clock_peak_hour: Optional[int] = None
    records: Dict[str, Optional[RecapRecord]] = {}
    monthly: List[RecapMonth] = []
