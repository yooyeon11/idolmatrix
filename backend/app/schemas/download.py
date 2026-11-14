"""YouTube 下载 / 订阅的请求与响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class DownloadJobRead(BaseModel):
    id: int
    url: str
    video_id: Optional[str] = None
    mode: str = "video"
    title: Optional[str] = None
    uploader: Optional[str] = None
    duration: Optional[int] = None
    status: str
    progress: float = 0.0
    downloaded_bytes: Optional[int] = None
    total_bytes: Optional[int] = None
    speed: Optional[str] = None
    eta: Optional[str] = None
    error: Optional[str] = None
    output_path: Optional[str] = None
    # 落盘明细 JSON 字符串：[{kind,name,size,width,height}]
    outputs: Optional[str] = None
    download_subtitles: bool = False
    subscription_id: Optional[int] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class DownloadJobCreate(BaseModel):
    url: str = Field(min_length=5, max_length=1000)
    # video = 单视频；playlist = 整个列表
    mode: str = "video"
    # 重复校验命中后，用户确认「仍然下载」时置 true
    force: bool = False
    # 本次是否同时下载字幕（中文优先）
    download_subtitles: bool = False


class DownloadCheckRequest(BaseModel):
    url: str = Field(min_length=5, max_length=1000)
    mode: str = "video"


class DuplicateMatch(BaseModel):
    where: str
    label: str
    id: Optional[int] = None
    uid: Optional[str] = None
    name: Optional[str] = None
    file_name: Optional[str] = None
    resolution: str = ""
    file_size: str = ""
    duration: Optional[int] = None


class DownloadCheckResult(BaseModel):
    url: str
    video_id: Optional[str] = None
    title: Optional[str] = None
    uploader: Optional[str] = None
    duration: Optional[int] = None
    duplicate: bool = False
    message: str = ""
    matches: List[DuplicateMatch] = []


class DownloadSubscriptionRead(BaseModel):
    id: int
    url: str
    name: Optional[str] = None
    enabled: bool = True
    download_existing: bool = False
    initialized: bool = False
    last_checked_at: Optional[datetime] = None
    last_error: Optional[str] = None
    total_enqueued: int = 0
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class DownloadSubscriptionCreate(BaseModel):
    url: str = Field(min_length=5, max_length=1000)
    name: Optional[str] = Field(default=None, max_length=300)
    download_existing: bool = False


class DownloadSubscriptionUpdate(BaseModel):
    enabled: Optional[bool] = None
    name: Optional[str] = Field(default=None, max_length=300)
    download_existing: Optional[bool] = None


class YtdlpStatus(BaseModel):
    available: bool
    version: str = ""
    path: str = ""
    install_dir: str = ""
    updatable: bool = False
    # 恒为 nightly（稳定版已下线）
    channel: str = "nightly"
    # 生效的下载代理（已脱敏）：来自设置 → 站点获取代理（全站共用）
    proxy: str = ""
    proxy_source: str = "none"
    # 本地下载存档（incoming/download-archive.txt）：已记录的 video id 数量与路径
    archive_path: str = ""
    archive_count: int = 0
    error: Optional[str] = None


class YtdlpUpdateRequest(BaseModel):
    # 只为兼容旧客户端保留；服务端恒按 nightly 处理（稳定版已下线）
    channel: Optional[str] = None


class NetworkProbeItem(BaseModel):
    source: str
    ok: bool
    ms: int = 0
    bytes: int = 0
    final_host: str = ""
    error: str = ""


class NetworkProbeResult(BaseModel):
    channel: str
    proxy: str = ""
    proxy_source: str = "none"
    results: List[NetworkProbeItem] = []


class SubscriptionCheckResult(BaseModel):
    subscription_id: int
    fetched: int = 0
    new: int = 0
    enqueued: int = 0
    skipped: int = 0
    error: str = ""


class DownloadListResponse(BaseModel):
    jobs: List[DownloadJobRead] = []
    subscriptions: List[DownloadSubscriptionRead] = []
