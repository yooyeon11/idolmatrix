"""播放会话 schema：统一 Playback API 的请求 / 响应模型。

播放器只消费 PlaybackSession 返回的 stream_url / manifest_url，
由服务端决定 Direct Play / Direct Stream / Transcode。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field

from app.schemas.common import ORMModel


class PlaybackSessionCreate(ORMModel):
    """创建播放会话的请求体。

    client 为可选客户端能力描述；不传时按现代浏览器默认能力决策。
    """

    music_video_id: int
    requested_quality: str = "original"
    start_seconds: float = 0.0
    audio_stream_id: Optional[int] = None
    subtitle_stream_id: Optional[int] = None
    client: Optional[Dict[str, Any]] = None


class PlaybackSessionRead(ORMModel):
    """播放会话响应：播放器只需要 URL + 基础信息。"""

    session_id: int
    music_video_id: Optional[int] = None
    play_mode: str
    transcode_required: bool = False
    requested_quality: Optional[str] = None
    decision_reason: Optional[str] = None
    quality: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    video_bitrate: Optional[int] = None
    audio_bitrate: Optional[int] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    container: Optional[str] = None
    stream_protocol: Optional[str] = None
    status: str
    created_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None
    # P0 施工说明 §4.2：只读。source_duration=片源秒数；start_offset=本路 window_start
    source_duration: Optional[float] = None
    start_offset: float = 0.0
    stream_url: Optional[str] = None
    manifest_url: Optional[str] = None
    subtitle_urls: List[Dict[str, Any]] = Field(default_factory=list)
    audio_streams: List[Dict[str, Any]] = Field(default_factory=list)
    available_qualities: List[str] = Field(default_factory=list)


class TranscodeSessionInfo(ORMModel):
    """转码会话可观测信息（调试 / 状态接口）。"""

    transcode_session_id: int
    status: str
    hardware_acceleration: Optional[str] = None
    ffmpeg_pid: Optional[int] = None
    progress: float = 0.0
    exit_code: Optional[int] = None
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    full_command: Optional[str] = None
    stderr_tail: Optional[str] = None
    error_message: Optional[str] = None
    output_directory: Optional[str] = None
    running: bool = False


class PlaybackSessionWindow(ORMModel):
    """播放会话的「已可播范围」——前端据此决定 seek 是原地跳还是重建会话。

    转码进行中 HLS 清单是 EVENT（无 ENDLIST），浏览器 MSE 的 duration 恒为
    Infinity，无法用来判断「目标分片转出来了没有」；故由后端给出明确上界
    available_until（片源**绝对**秒 = start_offset + 已列出分片时长之和）。
    超出该上界即表示目标尚未转出，客户端应新建会话（-ss 到目标）而不是原地
    seek —— 否则播放器只能卡在已转出区间（v3.4.3 修复的正是这条）。
    """

    session_id: int
    play_mode: str
    start_offset: float = 0.0
    available_until: Optional[float] = None
    source_duration: Optional[float] = None
    finished: bool = True
