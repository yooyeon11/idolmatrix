"""客户端播放能力描述。

播放请求必须携带客户端能力，服务端 Playback Resolver 据此决定
Direct Play / Direct Stream / Transcode。

字段对齐需求文档「第十一节 客户端能力」：
- supported_video_codecs / supported_audio_codecs / supported_containers
- max_width / max_height / max_video_bitrate / max_audio_bitrate
- supports_h264 / supports_hevc / supports_av1 / supports_vp9
- supports_hls / supports_fmp4 / supports_mpegts
- supports_ac3 / supports_eac3 / supports_aac

无能力上报时回退到现代浏览器默认能力（defaults()）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

# 视频编码别名表：不同 muxer / 容器中的编码名归一
_VIDEO_ALIASES = {
    "h264": {"h264", "avc", "avc1", "avc3"},
    "hevc": {"hevc", "h265", "hev1", "hvc1"},
    "av1": {"av1", "av01", "av1c", "libaom-av1"},
    "vp9": {"vp9", "vp09", "vp9.0"},
}

_AUDIO_ALIASES = {
    "aac": {"aac", "mp4a", "aac_lc"},
    "ac3": {"ac3"},
    "eac3": {"eac3", "ec-3", "e-3"},
    "mp3": {"mp3", "mp3a"},
    "opus": {"opus"},
    "flac": {"flac"},
}

# 现代浏览器（Chrome / Edge / Safari 15+）默认能力
_DEFAULT_VIDEO_CODECS = ["h264", "avc", "avc1", "avc3"]
_DEFAULT_AUDIO_CODECS = ["aac", "mp3", "mp4a"]
_DEFAULT_CONTAINERS = ["mp4", "m4v", "mov"]


@dataclass
class ClientCapabilities:
    """一次播放请求的客户端能力描述。"""

    supported_video_codecs: List[str] = field(default_factory=list)
    supported_audio_codecs: List[str] = field(default_factory=list)
    # 渐进直连（Direct Play，<video> src 原文件）口径 —— v3.5.3 双轨：
    # supported_*_codecs 代表 MSE（fMP4 HLS）可解，Direct Stream / Transcode 决策用；
    # progressive_*_codecs 代表渐进管线可解（与 file:// 播放同源）。
    # 个别环境（GPU 查询异常/远程桌面）渐进可硬解 VP9/AV1 4K 但 MSE 不认 fMP4，
    # 此时 Direct Play 直连原文件才是正解而非降档转码。
    progressive_video_codecs: List[str] = field(default_factory=list)
    progressive_audio_codecs: List[str] = field(default_factory=list)
    supported_containers: List[str] = field(default_factory=list)
    max_width: int = 0  # 0 = 不限
    max_height: int = 0
    max_video_bitrate: int = 0  # bps
    max_audio_bitrate: int = 0  # bps
    supports_hls: bool = True
    supports_fmp4: bool = True
    supports_mpegts: bool = False
    supports_ac3: bool = False
    supports_eac3: bool = False
    supports_aac: bool = True
    user_agent: str = ""

    # ===== 构造 =====
    @classmethod
    def defaults(cls) -> "ClientCapabilities":
        """无能力上报时的兜底：现代浏览器 + HLS/fMP4。"""
        return cls(
            supported_video_codecs=list(_DEFAULT_VIDEO_CODECS),
            supported_audio_codecs=list(_DEFAULT_AUDIO_CODECS),
            supported_containers=list(_DEFAULT_CONTAINERS),
            max_width=1920,
            max_height=1080,
            supports_hls=True,
            supports_fmp4=True,
            supports_mpegts=False,
            supports_ac3=False,
            supports_eac3=False,
            supports_aac=True,
        )

    @classmethod
    def modern_browser(cls) -> "ClientCapabilities":
        """「现代桌面浏览器」档：与前端 `VideoPlayView.vue::clientCapabilities()`
        在桌面 Chrome 下**实际上报**的内容对齐（h264/av1/vp9 + aac/opus/vorbis/flac、
        mp4/m4v/mov/webm、不设分辨率上限）。

        **用途仅限只读诊断**（`PlaybackManager.diagnose`）这类拿不到真实上报的场景 ——
        让诊断结论与真实播放一致。历史实现让它用 `defaults()`，于是 4K AV1 会被诊断成
        「全档位转码 / 视频编码不支持且无法 remux: av1」，而真实 session 是 direct_stream，
        排障时被彻底误导。

        ⚠ **不要拿它当播放兜底**：真实播放缺少能力上报时仍用 `defaults()`
        （只 h264/mp4、短边上限 1080）—— 宁可多转码，也不要把不能播的片判成直出。
        ⚠ 有意**不含 hevc**：桌面 Chrome 的 HEVC 取决于系统 HEVC 扩展，默认不可靠；
        诊断里会把 HEVC 片报成需要转码，并在 reason 里点明编码，属可接受的诚实结论。
        """
        return cls(
            supported_video_codecs=["h264", "av1", "vp9"],
            supported_audio_codecs=["aac", "opus", "vorbis", "mp3", "flac"],
            supported_containers=["mp4", "m4v", "mov", "webm"],
            max_width=0,
            max_height=0,
            supports_hls=True,
            supports_fmp4=True,
            supports_mpegts=False,
            supports_ac3=False,
            supports_eac3=False,
            supports_aac=True,
        )

    @classmethod
    def parse(cls, data: Optional[dict]) -> "ClientCapabilities":
        """从请求体解析能力；缺省字段回退默认。"""
        if not data:
            return cls.defaults()

        video_list = _normalize_list(data.get("supported_video_codecs"))
        audio_list = _normalize_list(data.get("supported_audio_codecs"))
        container_list = _normalize_list(data.get("supported_containers"))

        # 显式布尔字段（supports_h264 等）与编码列表互为补充
        for key, aliases in _VIDEO_ALIASES.items():
            flag = data.get(f"supports_{key}")
            if flag and key not in video_list:
                video_list.append(key)
            elif flag is False and key in video_list:
                video_list.remove(key)
        for key, aliases in _AUDIO_ALIASES.items():
            flag = data.get(f"supports_{key}")
            if flag and key not in audio_list:
                audio_list.append(key)
            elif flag is False and key in audio_list:
                audio_list.remove(key)

        ua = str(data.get("user_agent") or "")
        # v3.5.2：仅当客户端未上报任何编码（旧版前端/异常环境）时才按 UA 补桌面
        # Chromium 默认集。新版前端以 MediaSource.isTypeSupported 实测为准上报：
        # MSE 不认的编码（如个别环境 VP9 fMP4）会被刻意剔除，必须尊重，
        # 否则后端误判 direct_stream，hls.js 会在 MSE 处 fatal（起播转圈）。
        if not video_list and not audio_list:
            video_list, audio_list = _augment_desktop_chromium(ua, video_list, audio_list)

        return cls(
            supported_video_codecs=video_list or list(_DEFAULT_VIDEO_CODECS),
            supported_audio_codecs=audio_list or list(_DEFAULT_AUDIO_CODECS),
            # 渐进口径（v3.5.3）：旧版前端未上报时为空 —— 判定方法内部回退到 MSE 列表
            progressive_video_codecs=_normalize_list(data.get("progressive_video_codecs")),
            progressive_audio_codecs=_normalize_list(data.get("progressive_audio_codecs")),
            supported_containers=container_list or list(_DEFAULT_CONTAINERS),
            max_width=int(data.get("max_width") or 0),
            max_height=int(data.get("max_height") or 0),
            max_video_bitrate=int(data.get("max_video_bitrate") or 0),
            max_audio_bitrate=int(data.get("max_audio_bitrate") or 0),
            supports_hls=bool(data.get("supports_hls", True)),
            supports_fmp4=bool(data.get("supports_fmp4", True)),
            supports_mpegts=bool(data.get("supports_mpegts", False)),
            supports_ac3=bool(data.get("supports_ac3", False)),
            supports_eac3=bool(data.get("supports_eac3", False)),
            supports_aac=bool(data.get("supports_aac", True)),
            user_agent=ua,
        )

    # ===== 判定 =====
    def supports_video_codec(self, codec: str) -> bool:
        if not codec:
            return False
        normalized = _normalize_codec(codec)
        if normalized in self.supported_video_codecs:
            return True
        # 别名归一后再匹配
        for aliases in _VIDEO_ALIASES.values():
            if normalized in aliases:
                return bool(
                    set(aliases) & {c.lower() for c in self.supported_video_codecs}
                )
        return False

    def supports_progressive_video_codec(self, codec: str) -> bool:
        """渐进直连（Direct Play）编码判定：优先渐进口径，未上报（旧前端）回退 MSE 列表。"""
        if not codec:
            return False
        if not self.progressive_video_codecs:
            return self.supports_video_codec(codec)
        normalized = _normalize_codec(codec)
        if normalized in self.progressive_video_codecs:
            return True
        for aliases in _VIDEO_ALIASES.values():
            if normalized in aliases:
                return bool(
                    set(aliases) & {c.lower() for c in self.progressive_video_codecs}
                )
        return False

    def supports_progressive_audio_codec(self, codec: str) -> bool:
        """渐进直连（Direct Play）音频判定：优先渐进口径，未上报（旧前端）回退 MSE 列表。"""
        if not codec:
            return False
        if not self.progressive_audio_codecs:
            return self.supports_audio_codec(codec)
        normalized = _normalize_codec(codec)
        if normalized in self.progressive_audio_codecs:
            return True
        for aliases in _AUDIO_ALIASES.values():
            if normalized in aliases:
                return bool(
                    set(aliases) & {c.lower() for c in self.progressive_audio_codecs}
                )
        return False

    def supports_audio_codec(self, codec: str) -> bool:
        if not codec:
            return False
        normalized = _normalize_codec(codec)
        if normalized in self.supported_audio_codecs:
            return True
        for aliases in _AUDIO_ALIASES.values():
            if normalized in aliases:
                return bool(
                    set(aliases) & {c.lower() for c in self.supported_audio_codecs}
                )
        return False

    def supports_container(self, container: str) -> bool:
        if not container:
            return False
        normalized = container.lower().lstrip(".")
        return normalized in {c.lower() for c in self.supported_containers}

    # ===== 便捷属性 =====
    @property
    def supports_h264(self) -> bool:
        return self.supports_video_codec("h264")

    @property
    def supports_hevc(self) -> bool:
        return self.supports_video_codec("hevc")

    @property
    def supports_av1(self) -> bool:
        return self.supports_video_codec("av1")

    @property
    def supports_vp9(self) -> bool:
        return self.supports_video_codec("vp9")

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _augment_desktop_chromium(
    ua: str, video_list: List[str], audio_list: List[str]
) -> tuple[List[str], List[str]]:
    """桌面 Chromium / Firefox：补齐 AV1/VP9/Opus，避免 canPlayType 漏报。"""
    ua_l = (ua or "").lower()
    if not ua_l:
        return video_list, audio_list
    if any(t in ua_l for t in ("iphone", "ipad", "ipod", "android")):
        return video_list, audio_list
    is_chromium = ("chrome" in ua_l or "edg/" in ua_l or "chromium" in ua_l) and "iphone" not in ua_l
    is_firefox = "firefox" in ua_l
    if not (is_chromium or is_firefox):
        return video_list, audio_list
    for codec in ("h264", "av1", "vp9"):
        if codec not in video_list:
            video_list.append(codec)
    for codec in ("aac", "opus", "mp3"):
        if codec not in audio_list:
            audio_list.append(codec)
    return video_list, audio_list


def _normalize_codec(codec: str) -> str:
    return (codec or "").lower().strip()


def _normalize_list(values: object) -> List[str]:
    if not values:
        return []
    if isinstance(values, str):
        return [v.strip().lower() for v in values.split(",") if v.strip()]
    if isinstance(values, (list, tuple)):
        return [str(v).strip().lower() for v in values if str(v).strip()]
    return []
