"""集中配置：媒体路径、FFmpeg 路径、数据库地址等。

通过 pydantic-settings 从环境变量 / .env 加载，单一事实来源。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# 项目根目录：backend/app/core/config.py -> backend/ -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_path(p: str | Path) -> Path:
    """把相对路径解析为基于 PROJECT_ROOT 的绝对路径。"""
    path = Path(p)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===== 应用 =====
    app_name: str = "K-pop Media Library"
    debug: bool = False
    # 后端监听端口（固定避开 8000，避免与本机其他服务冲突）
    server_port: int = 8010

    # ===== 数据库 =====
    database_url: str = "sqlite:///./data/kpop_media.db"

    # ===== 存储路径 =====
    storage_incoming_dir: str = "./storage/incoming"
    storage_incoming_bili_dir: str = "./storage/incoming-bilibili"
    storage_library_dir: str = "./storage/library"
    storage_derived_dir: str = "./storage/derived"

    # ===== 图片文件夹浏览白名单根路径 =====
    # 绑定「本地文件夹」时浏览器可到达的根目录（逗号分隔多个）。
    # NAS 部署时 /data 下已挂载 incoming/library/mt-photos 等卷，默认只开放 /data；
    # 需要浏览其他挂载点时通过环境变量 PHOTO_BROWSE_ROOTS 追加，如 "/data,/media"。
    photo_browse_roots: str = "/data"

    # ===== FFmpeg =====
    ffmpeg_path: str = ""
    ffprobe_path: str = ""

    # ===== 转码默认参数 =====
    transcode_video_codec: str = "libx264"
    transcode_video_preset: str = "veryfast"
    transcode_video_crf: int = 23
    transcode_audio_codec: str = "aac"
    transcode_audio_bitrate: str = "192k"
    transcode_scale: str = "1280:-2"
    # 硬件加速模式：auto=自动探测（VAAPI 优先，不可用回退 CPU），none/cpu=强制软件转码，
    # vaapi=强制 VAAPI，qsv=强制 QSV（Intel 专有；显式指定时也会先做真实试编探测，
    # 失败即回退软件转码）。驱动不写死：由镜像自带的多厂商驱动集 + libva 自动枚举决定。
    transcode_hwaccel: str = "auto"
    # 硬件**解码**模式（v3.4.2 起）：CPU 占用的大头在解码，4K AV1/VP9 尤其明显。
    # auto  = 探测到该 GPU 可硬解当前源编码时，走「GPU 解码 → GPU 缩放 → GPU 编码」
    #         全链路；探测不通过自动退回「软解 + 硬编」，不会失败。
    # force = 跳过能力探测直接尝试硬解（排查用；ffmpeg 侧仍要求有 {codec}_vaapi 解码器）
    # off   = 始终软解 + 硬编（保守档）
    transcode_hw_decode: str = "auto"
    # vainfo 可执行文件路径（留空则自动查找：ffmpeg 同目录 / PATH）。
    # 它是「这块 GPU 到底能解哪些编码」的唯一可靠来源（Intel / AMD 通用）。
    vainfo_path: str = ""

    # ===== 播放 / 转码会话（Emby 式播放架构）=====
    # HLS 分片时长（秒）：调小可缩短「首个分片生成」的等待时间，
    # 避免 NAS 上转码速度慢导致播放器长时间拿不到 manifest 而报错
    hls_segment_duration: int = 3
    # manifest 首次请求时阻塞等待 playlist 生成的最长时间（秒）。
    # 4K 软解首片可能远超分片时长；实际等待取 max(本值, 分片时长*2)。
    # 超时后 ffmpeg 仍在运行则返回 503，由前端 VHS 重试兜底
    hls_playlist_wait_timeout: int = 15
    # 播放会话闲置回收阈值（秒）：心跳后超过该时长无活动则关闭
    playback_session_timeout: int = 3600
    # 转码会话闲置回收阈值（秒）：无播放会话引用且闲置超过该时长则停止并清理
    transcode_session_timeout: int = 900
    # 同时进行的最大转码路数（含 CPU 软转；达到上限时新起转码返回 429）。remux 不计入。
    transcode_max_concurrent: int = 2
    # 会话引用归零后延迟删除对应 window 目录的时长（秒）
    transcode_cleanup_delay_seconds: int = 1800
    # 周期清理：derived/transcodes 下无活跃引用且 mtime 超过该时长（秒）的目录将被删除
    transcode_cleanup_max_age_seconds: int = 86400
    # 周期清理任务的运行间隔（秒）
    transcode_cleanup_interval_seconds: int = 300
    # incoming 待整理目录扫描落库的运行间隔（秒）
    incoming_scan_interval_seconds: int = 300

    # ===== 站点获取（TheAudioDB） =====
    # 免费测试 Key 为 "2"（检索仅限测试数据，如 coldplay）；
    # 正式使用请到 theaudiodb.com 注册后替换为自己的 Key
    audiodb_api_key: str = "2"
    audiodb_base_url: str = "https://www.theaudiodb.com/api/v1/json"
    audiodb_timeout: int = 15
    audiodb_image_timeout: int = 30

    # ===== 跨域 =====
    cors_origins: List[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # 是否放行跨站写请求（全局开关）。
    # ⚠ 设置页的开关已移除（v3.2.18），这里是**唯一**入口：只能通过环境变量
    # ALLOW_CROSS_ORIGIN_WRITES=true 开启，docker-compose.yml 已透传该变量。
    # 默认 False 保持 CSRF 严格校验；通过内网穿透(NAS/隧道)等入口访问、
    # 且 Origin 与 Host 不一致导致「跨站请求被拒绝」(403) 时才在部署层放开。
    # 注意：登录态为 Cookie，放开后谨慎用于可信网络。
    allow_cross_origin_writes: bool = False

    # ===== 登录门禁 =====
    # auto：有/无用户都强制（零用户仅放行 bootstrap/status/health）
    # true：同 auto；false：整栈关闭，回到 2.1.x 匿名（救火阀）
    auth_required: str = "auto"
    auth_cookie_secure: str = "auto"
    auth_trusted_proxies: str = ""
    auth_openapi: bool = False
    auth_idle_seconds: int = 7 * 24 * 3600
    auth_absolute_seconds: int = 30 * 24 * 3600
    auth_login_window_seconds: int = 15 * 60
    auth_login_max_failures: int = 5
    bootstrap_token: str = ""
    bootstrap_owner_username: str = "owner"
    bootstrap_owner_password: str = ""
    auth_reset_owner_password: str = ""

    # ===== 请求体上限（Content-Length；无该头则不在中间件层拦截）=====
    # JSON 足够覆盖设置/入库表单；multipart 对齐照片墙单次上限 30×25MB
    max_json_body_bytes: int = 2 * 1024 * 1024
    max_upload_body_bytes: int = 800 * 1024 * 1024

    # ===== 派生属性 =====
    @property
    def incoming_dir(self) -> Path:
        return _resolve_path(self.storage_incoming_dir)

    @property
    def incoming_bili_dir(self) -> Path:
        return _resolve_path(self.storage_incoming_bili_dir)

    @property
    def incoming_scan_dirs(self) -> list[Path]:
        """扫描根目录清单：YouTube incoming + Bilibili incoming-bilibili。"""
        return [self.incoming_dir, self.incoming_bili_dir]

    def resolve_incoming_root(self, path: "Path") -> "Path | None":
        """判断 path 是否位于任一 incoming 根目录内，返回所在根；否则 None。"""
        p = path.resolve()
        for root in self.incoming_scan_dirs:
            try:
                p.relative_to(root.resolve())
                return root
            except ValueError:
                continue
        return None

    @property
    def library_dir(self) -> Path:
        return _resolve_path(self.storage_library_dir)

    @property
    def derived_dir(self) -> Path:
        return _resolve_path(self.storage_derived_dir)

    @property
    def transcode_dir(self) -> Path:
        return self.derived_dir / "transcode"

    @property
    def playback_transcode_dir(self) -> Path:
        """播放转码缓存根目录（derived/transcodes）。

        与旧 transcode_dir 分离：实时播放转码是可再生的缓存，
        不再是媒体元数据中的永久转码文件（MusicVideo.transcode_path）。
        """
        return self.derived_dir / "transcodes"

    @property
    def thumbnail_dir(self) -> Path:
        return self.derived_dir / "thumbnails"

    @property
    def photo_thumb_dir(self) -> Path:
        return self.derived_dir / "photo-thumbs"

    @property
    def photo_upload_dir(self) -> Path:
        return self.derived_dir / "photo-uploads"

    @property
    def avatar_dir(self) -> Path:
        return self.derived_dir / "avatars"

    @property
    def db_path(self) -> Path:
        """SQLite 数据库文件路径。"""
        # 形如 sqlite:///./data/kpop_media.db
        url = self.database_url
        if url.startswith("sqlite:///"):
            return _resolve_path(url.replace("sqlite:///", "", 1))
        return _resolve_path("./data/kpop_media.db")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors(cls, v):
        if isinstance(v, str):
            # 兼容 JSON 字符串或逗号分隔
            v = v.strip()
            if v.startswith("["):
                import json

                return json.loads(v)
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    def ensure_directories(self) -> None:
        """启动时创建必要的目录结构。"""
        for d in [
            self.incoming_dir,
            self.incoming_bili_dir,
            self.library_dir,
            self.derived_dir,
            self.transcode_dir,
            self.playback_transcode_dir,
            self.thumbnail_dir,
            self.photo_thumb_dir,
            self.photo_upload_dir,
            self.avatar_dir,
            self.db_path.parent,
        ]:
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
