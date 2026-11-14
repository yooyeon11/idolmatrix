"""ORM 模型聚合导入。

所有模型集中导出，便于 Alembic 与数据库初始化时统一注册到 metadata。
"""

from app.models.album import Album, AlbumTrack
from app.models.artist import Artist
from app.models.company import (
    ArtistCompanyRelation,
    Company,
    GroupCompanyRelation,
)
from app.models.group import Group
from app.models.membership import GroupMembership
from app.models.music_video import MusicVideo, MusicVideoTrack
from app.models.photo import Photo, PhotoCollection, PhotoCollectionItem, PhotoSource
from app.models.video_collection import VideoCollection, VideoCollectionItem
from app.models.playback import PlaybackSession, TranscodeSession
from app.models.watch import WatchPlay
from app.models.song import Credits, Song, SongArtistRelation
from app.models.task import FileMoveLog
from app.models.app_setting import AppSetting
from app.models.user import User
from app.models.auth_session import AuthSession
from app.models.incoming_file import IncomingFile
from app.models.ingest import IngestManifest, IngestRequest
from app.models.agent_session import AgentMessage, AgentThread
from app.models.entity_image import EntityImage
from app.models.entity_field_lock import EntityFieldLock
from app.models.external_channel_video import ExternalChannelVideo
from app.models.video_score import VideoScore
from app.models.video_publish import VideoPublish
from app.models.download import (
    DownloadJob,
    DownloadSubscription,
    DownloadSubscriptionItem,
)
from app.models.api_token import ApiToken
from app.models.audit_log import AuditLog

__all__ = [
    # Artist / Group / Membership
    "Artist",
    "Group",
    "GroupMembership",
    # Company
    "Company",
    "ArtistCompanyRelation",
    "GroupCompanyRelation",
    # Album / Song
    "Album",
    "AlbumTrack",
    "Song",
    "SongArtistRelation",
    "Credits",
    # MusicVideo
    "MusicVideo",
    "MusicVideoTrack",
    "VideoCollection",
    "VideoCollectionItem",
    # Photos
    "PhotoSource",
    "Photo",
    "PhotoCollection",
    "PhotoCollectionItem",
    # Playback（Emby 式播放架构）
    "PlaybackSession",
    "TranscodeSession",
    "WatchPlay",
    # Tasks
    "FileMoveLog",
    "AppSetting",
    "User",
    "AuthSession",
    # Incoming（待整理扫描落库）
    "IncomingFile",
    "IngestManifest",
    "AgentThread",
    "AgentMessage",
    "IngestRequest",
    # EntityImages（头像/横幅候选历史）
    "EntityImage",
    # EntityFieldLocks（AI/站点获取字段锁）
    "EntityFieldLock",
    # ExternalChannelVideos（博主频道投稿快照，YouTube 缺失检测）
    "ExternalChannelVideo",
    # VideoScore（AI 视频评分，实验功能）
    "VideoScore",
    # VideoPublish（外部平台分发：B 站 BV 号）
    "VideoPublish",
    # Downloads（YouTube 下载 + 收藏夹订阅）
    "DownloadJob",
    "DownloadSubscription",
    "DownloadSubscriptionItem",
    # ApiToken / AuditLog（外部集成门禁与操作审计）
    "ApiToken",
    "AuditLog",
]
