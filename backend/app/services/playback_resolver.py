"""Playback Resolver：统一的播放决策层。

所有播放器进入路径（新 Playback API、旧兼容路由）都必须经过
resolve_playback_strategy()，禁止在业务层各自判断。

决策流程（对标 Emby）：

    Direct Play / Direct Stream 仅在「请求档位可由原文件满足」（original，
    或源分辨率 ≤ 档位上限）时可行
    Can Direct Play?   → YES → DIRECT_PLAY（原始文件直接播放，0 FFmpeg）
    Can Direct Stream? → YES → DIRECT_STREAM（容器 remux，视频不重编码）
    else                     → TRANSCODE（启动 HLS/MPEG-TS 转码）

    用户显式选择较低档位（如 1080P 源选 720P）时，原文件无法满足，
    必须进入 TRANSCODE 降分辨率；源分辨率本来就 ≤ 所选档位则仍走直连。

决策输入：
- MediaInfo：源文件流级信息（容器 / 视频 / 音频 / 分辨率 / 码率）
- ClientCapabilities：客户端能力
- requested_quality：请求档位（original / 2160p / 1080p / 720p）

决策输出 PlaybackDecision：
- play_mode / quality / 输出规格 / stream_protocol / transcode_required
- transcode 路径附带 TranscodingProfile（供 Transcode Manager 使用）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.core.config import settings
from app.media.client_capabilities import ClientCapabilities
from app.media.media_info import MediaInfo
from app.media.transcoding_profile import (
    TranscodingProfile,
    available_qualities,
    long_side,
    profile_for_quality,
    quality_rung,
    resolve_target_dimensions,
    short_side,
)

# ===== 播放模式（与 models.playback 对齐）=====
PLAY_MODE_DIRECT_PLAY = "direct_play"
PLAY_MODE_DIRECT_STREAM = "direct_stream"
PLAY_MODE_TRANSCODE = "transcode"

# ===== 流协议 =====
STREAM_PROTOCOL_PROGRESSIVE = "progressive"
STREAM_PROTOCOL_HLS = "hls"

# 可以直接 remux（-c:v copy）进 HLS/MPEG-TS 的视频编码
REMUXXABLE_VIDEO_CODECS = {"h264", "hevc", "mpeg4", "av1", "vp9"}

# 浏览器可直接软解的像素格式（10bit / 422 / 444 会强制转码到 8bit）
BROWSER_SAFE_PIXEL_FORMATS = {"yuv420p", "yuvj420p", "nv12"}

# 支持 10bit 解码的编码（Chrome/Edge 对 VP9/AV1 支持 10bit 420/422 软解与硬解）
_10BIT_CAPABLE_CODECS = {"vp9", "av1"}
_10BIT_SAFE_PIXEL_FORMATS = {
    "yuv420p10le",
    "yuv420p12le",
    "yuv422p10le",
    "p010le",
    "yuvj420p10le",
}

# AV1/VP9 不能安全塞进 MPEG-TS，Direct Stream 时改用 fMP4 HLS。
# ⚠ v3.4.5 实测（CDP + SourceBuffer 探针）：xgplayer-hls 3.0.26 建 MSE SourceBuffer
# 只认 init 段 stsd 里的 avcC/hvcC，**不解析 av1C/vpcC** —— 解不出视频 codec 就兜底
# 声明 avc1.42e01e，AV1/VP9 的 fMP4 分片随即被 Chrome 拒收：
# CHUNK_DEMUXER_ERROR_APPEND_FAILED "Video stream codec av1 doesn't match SourceBuffer
# codecs"。
# ✅ v3.4.9 出路：xgplayer-hls 会解析 **master playlist STREAM-INF 的 CODECS 属性**
# （媒体清单里写没用），据此声明 SourceBuffer。因此两件事缺一不可：
#   ① 后端给 hls_fmp4 会话包一层带 CODECS 四码的 master playlist（playback_manager）；
#   ② 前端补丁放开其分类正则 /^av1$/ → /^av01/（frontend/scripts/patch-xgplayer-hls.mjs，
#      build 时自动应用；vp09 本来就被 /^vp0?[89]/ 认，无需补丁也能直出）。
_FMP4_REMUX_CODECS = {"av1", "vp9"}

# 播放器 MSE 链路能「声明」进 SourceBuffer 的编码（决定 Direct Stream 资格）；
# 比 ffmpeg 能不能 remux（REMUXXABLE_VIDEO_CODECS）更严。
# ✅ v3.5.0 起：HLS 内核已切换为 hls.js（xgplayer-hls.js 插件）——
#   原生解析 master playlist STREAM-INF 的 CODECS 四码（av01/vp09 无需任何补丁），
#   从 master 切到 variant 不会重建 SourceBuffer，av1/vp9 加回本集合恢复
#   原画直出（Direct Stream fMP4 remux），不再整库转码。
#   历史包袱（v3.4.9 时期 xgplayer-hls 3.0.26 的两坑，供考古）：
#   ① 建 SourceBuffer 只认 init 段 stsd 的 avcC/hvcC，不解析 av1C/vpcC，
#      兜底声明 avc1.42e01e → CHUNK_DEMUXER_ERROR_APPEND_FAILED；
#   ② master→variant 切换时重建全部 SourceBuffer，append 静默失败，播放卡死。
_MSE_DECLARABLE_VIDEO_CODECS = {"h264", "hevc", "mpeg4", "av1", "vp9"}

# fMP4 HLS（MSE）里浏览器稳妥可播的音频；Opus/FLAC 等即使 video 元素能解 webm，
# 塞进 fMP4 清单也经常失败，因此只 copy AAC，其余转 AAC。
_FMP4_COPY_AUDIO = {"aac", "mp4a", "aac_lc"}


@dataclass
class PlaybackDecision:
    """一次播放请求的完整决策结果。"""

    play_mode: str
    quality: str
    width: int
    height: int
    video_codec: str  # 输出视频编码（copy=不重编码）
    audio_codec: str  # 输出音频编码（copy=不重编码）
    video_bitrate: int  # 输出视频码率 bps（copy=源码率）
    audio_bitrate: int  # 输出音频码率 bps（copy=源码率）
    container: str  # 输出容器（mp4 / hls_mpegts）
    stream_protocol: str  # progressive / hls
    transcode_required: bool
    reason: str  # 决策依据（可读，供排查）
    profile: Optional[TranscodingProfile] = None
    available_qualities: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "play_mode": self.play_mode,
            "quality": self.quality,
            "width": self.width,
            "height": self.height,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "video_bitrate": self.video_bitrate,
            "audio_bitrate": self.audio_bitrate,
            "container": self.container,
            "stream_protocol": self.stream_protocol,
            "transcode_required": self.transcode_required,
            "reason": self.reason,
            "available_qualities": self.available_qualities,
            "profile": self.profile.to_dict() if self.profile else None,
        }


def resolve_playback_strategy(
    source: MediaInfo,
    client: Optional[ClientCapabilities] = None,
    requested_quality: str = "original",
) -> PlaybackDecision:
    """统一的播放决策入口。

    参数:
        source: 源文件流级信息（probe_media_info 的结果）
        client: 客户端能力（缺省按现代浏览器默认）
        requested_quality: 请求档位；非法档位自动收敛到可用档位
    """
    client = client or ClientCapabilities.defaults()

    available = available_qualities(
        source.width,
        source.height,
        client_max_height=client.max_height,
        client_max_width=client.max_width,
    )
    quality = _clamp_quality(requested_quality, available)

    # ---- 1. Direct Play：原文件直接播放 ----
    # Direct Play 只能交付原始分辨率：用户显式选了较低档位（如 1080P 源选 720P）
    # 时必须转码降分辨率，不能直接播原文件
    if (
        _original_meets_request(quality, source)
        and can_direct_play(source, client) is None
    ):
        vs = source.video_stream
        aas = source.default_audio_stream
        return PlaybackDecision(
            play_mode=PLAY_MODE_DIRECT_PLAY,
            quality="original",
            width=source.width,
            height=source.height,
            video_codec=vs.codec_name if vs else "unknown",
            audio_codec=aas.codec_name if aas else "none",
            video_bitrate=vs.bit_rate or 0,
            audio_bitrate=aas.bit_rate if aas else 0,
            container=source.container,
            stream_protocol=STREAM_PROTOCOL_PROGRESSIVE,
            transcode_required=False,
            reason="Direct Play：容器/编码/分辨率全部兼容，直接播放原始文件",
            available_qualities=available,
        )

    # ---- 2. Direct Stream：容器 / 音频轻量处理，视频不重编码 ----
    if can_direct_stream(source, client, quality) is None:
        aas = source.default_audio_stream
        vs = source.video_stream
        hls_container = _remux_container(_canon_vcodec(vs.codec_name if vs else ""))
        out_audio = _remux_audio_codec(source, client, hls_container)
        profile = _direct_stream_profile(source, out_audio, container=hls_container)
        return PlaybackDecision(
            play_mode=PLAY_MODE_DIRECT_STREAM,
            quality="original",
            width=source.width,
            height=source.height,
            video_codec="copy",
            audio_codec=out_audio,
            video_bitrate=vs.bit_rate or 0,
            audio_bitrate=profile.audio_bitrate if out_audio != "copy" else (aas.bit_rate or 0),
            container=hls_container,
            stream_protocol=STREAM_PROTOCOL_HLS,
            transcode_required=False,
            reason="Direct Stream：视频编码兼容，仅 remux 容器/音频（不重编码视频）",
            profile=profile,
            available_qualities=available,
        )

    # ---- 3. Transcode：真正需要重编码 ----
    vs = source.video_stream
    profile = _transcode_profile(source, quality, client)
    width, height = resolve_target_dimensions(
        quality_rung(quality), source.width, source.height
    )
    reason = _transcode_reason(source, client, quality)
    return PlaybackDecision(
        play_mode=PLAY_MODE_TRANSCODE,
        quality=quality,
        width=width,
        height=height,
        video_codec=profile.video_codec,
        audio_codec=profile.audio_codec,
        video_bitrate=profile.video_bitrate,
        audio_bitrate=profile.audio_bitrate,
        container="hls_mpegts",
        stream_protocol=STREAM_PROTOCOL_HLS,
        transcode_required=True,
        reason=reason,
        profile=profile,
        available_qualities=available,
    )


def can_direct_play(source: MediaInfo, client: ClientCapabilities) -> Optional[str]:
    """Direct Play 可行性；返回 None=可行，否则返回原因。

    编码/音频判定用**渐进口径**（progressive_*，与 file:// 播放同源）——
    Direct Play 是 <video> src 直连原文件，不经 MSE。个别环境（GPU 查询异常/
    远程桌面）渐进可硬解 VP9/AV1 4K 但 MSE 不认 fMP4，恰是 Direct Play 的用武之地；
    旧前端未上报渐进口径时内部回退 MSE 列表（兼容）。
    """
    if not client.supports_container(source.container):
        return f"容器不支持: {source.container or '未知'}"
    vs = source.video_stream
    if not vs:
        return "无视频流"
    if not client.supports_progressive_video_codec(vs.codec_name):
        return f"视频编码不支持: {vs.codec_name}"
    aas = source.default_audio_stream
    if aas and not client.supports_progressive_audio_codec(aas.codec_name):
        return f"音频编码不支持: {aas.codec_name}"
    # 客户端上限同样按短边/长边比对：`max_height` 是短边上限、`max_width` 是长边上限，
    # 否则竖屏 1080×1920 会被「height 1920 > 上限 1080」误判超限（短边口径下它是 1080p）。
    short = short_side(source.width, source.height)
    long = long_side(source.width, source.height)
    if client.max_height > 0 and short > client.max_height:
        return f"分辨率超出客户端上限: 短边 {short} > {client.max_height}"
    if client.max_width > 0 and long > client.max_width:
        return f"分辨率超出客户端上限: 长边 {long} > {client.max_width}"
    if client.max_video_bitrate > 0:
        rate = _effective_video_bitrate(source)
        if rate > client.max_video_bitrate:
            return f"码率超出客户端上限: {rate} > {client.max_video_bitrate}"
    if (
        vs.pixel_format
        and not _pixel_format_ok(_canon_vcodec(vs.codec_name), vs.pixel_format)
    ):
        return f"像素格式浏览器兼容性差: {vs.pixel_format}"
    return None


def can_direct_stream(
    source: MediaInfo, client: ClientCapabilities, quality: str
) -> Optional[str]:
    """Direct Stream 可行性（视频不重编码的 remux）；返回 None=可行。"""
    if not _original_meets_request(quality, source):
        return "需要降低分辨率，无法仅 remux"
    vs = source.video_stream
    if not vs:
        return "无视频流"
    codec = _canon_vcodec(vs.codec_name)
    if not client.supports_video_codec(codec):
        return f"视频编码不支持且无法 remux: {vs.codec_name}"
    if codec not in REMUXXABLE_VIDEO_CODECS:
        return f"视频编码无法安全 remux: {vs.codec_name}"
    # 播放器 MSE 链路声明不了的编码走转码（见 _MSE_DECLARABLE_VIDEO_CODECS 注释；
    # v3.5.0 起 hls.js 内核 av1/vp9 均可声明）
    if codec not in _MSE_DECLARABLE_VIDEO_CODECS:
        return f"播放器 MSE 不支持声明该编码，需要转码: {vs.codec_name}"
    # 同 can_direct_play：客户端上限按短边/长边比对（短边口径，竖屏不再误判超限）
    short = short_side(source.width, source.height)
    long = long_side(source.width, source.height)
    if client.max_height > 0 and short > client.max_height:
        return f"分辨率超出客户端短边上限，需要转码缩放: {short} > {client.max_height}"
    if client.max_width > 0 and long > client.max_width:
        return f"分辨率超出客户端长边上限，需要转码缩放: {long} > {client.max_width}"
    if client.max_video_bitrate > 0:
        rate = _effective_video_bitrate(source)
        if rate > client.max_video_bitrate:
            return "码率超出客户端上限，需要转码降码率"
    if (
        vs.pixel_format
        and not _pixel_format_ok(codec, vs.pixel_format)
    ):
        return f"像素格式需要转码: {vs.pixel_format}"
    return None


def _pixel_format_ok(codec_name: str, pixel_format: Optional[str]) -> bool:
    """像素格式是否浏览器可解：8bit 420 全安全；VP9/AV1 额外允许 10bit 420。"""
    if not pixel_format:
        return True
    pix = pixel_format.lower()
    codec = _canon_vcodec(codec_name)
    if pix in BROWSER_SAFE_PIXEL_FORMATS:
        return True
    if codec in _10BIT_CAPABLE_CODECS:
        if pix in _10BIT_SAFE_PIXEL_FORMATS:
            return True
        # AV1/VP9 的 420 族（含 10/12bit）Chrome 都能软解；444 才强制转码
        if "420" in pix and "444" not in pix:
            return True
    return False


def _canon_vcodec(name: str) -> str:
    n = (name or "").lower().strip()
    if n in {"av1", "av01", "av1c", "libaom-av1"}:
        return "av1"
    if n in {"vp9", "vp09", "vp9.0"}:
        return "vp9"
    if n in {"hevc", "h265", "hev1", "hvc1"}:
        return "hevc"
    if n in {"h264", "avc", "avc1", "avc3"}:
        return "h264"
    if n in {"mpeg4", "mp4v"}:
        return "mpeg4"
    return n


def _effective_video_bitrate(source: MediaInfo) -> int:
    """估算视频码率，与客户端 max_video_bitrate（视频码率上限）同口径比较。

    优先取视频流自身码率；缺失时（MKV 等常见）用容器总码率扣减默认音频流
    码率；再退回容器总码率。直接拿总码率比对视频上限会因含音频而误判超限，
    触发不必要的转码。
    """
    vs = source.video_stream
    if vs and vs.bit_rate:
        return vs.bit_rate
    total = source.bit_rate or 0
    aas = source.default_audio_stream
    audio_rate = aas.bit_rate if aas else 0
    if total and audio_rate and total > audio_rate:
        return total - audio_rate
    return total


# ===== 内部辅助 =====

def _original_meets_request(quality: str, source: MediaInfo) -> bool:
    """原文件（原始分辨率）是否已满足请求档位。

    ⚠ **短边口径**：档位的 `max_height` 是「短边上限」、`max_width` 是「长边上限」，
    与 `available_qualities` / `video_meta.RESOLUTION_TIERS` 同一套规则。

    - quality == original：恒满足，可作为 Direct Play / Direct Stream 候选。
    - 其他档位：源短边不超过档位短边上限时，原文件本身就是该档位，无需转码；
      超出时必须转码降分辨率（横屏 3840×2160 选 720p、竖屏 2160×3840 选 1080p 都算超出）。

    历史实现按 height 判 → 竖屏 2160×3840 选它自己的 2160p 会被判成「需要降分辨率」
    而强制转码（并缩成 1216×2160），而横屏 3840×2160 选 2160p 是直出 —— 同一档位横竖不对称。
    """
    if quality == "original":
        return True
    rung = quality_rung(quality)
    if rung.max_height <= 0 and rung.max_width <= 0:
        return True
    short = short_side(source.width, source.height)
    if short <= 0:
        return True  # 分辨率未知，不据此阻断
    long = long_side(source.width, source.height)
    if rung.max_height > 0 and short > rung.max_height:
        return False
    if rung.max_width > 0 and long > rung.max_width:
        return False
    return True


def _clamp_quality(requested: str, available: List[str]) -> str:
    """把请求档位收敛到可用档位。

    - 已在可用列表 → 原样返回
    - original / 空 → original（客户端要求原画，交给决策层判断是否可行）
    - 超出源能力（如 720P 源请求 1080P）→ 取最接近且不超过请求的可用档位
    """
    requested = requested or "original"
    if requested in available:
        return requested
    if requested == "original":
        return "original"
    rung = quality_rung(requested)
    for name in available:
        if name == "original":
            continue
        if quality_rung(name).max_height <= rung.max_height:
            return name
    return "original"


def _remux_container(codec: str) -> str:
    """AV1/VP9 不能稳妥放进 MPEG-TS，改用 fMP4 HLS；H.264/HEVC 仍走 TS。"""
    return "hls_fmp4" if codec in _FMP4_REMUX_CODECS else "hls_mpegts"


def _remux_audio_codec(
    source: MediaInfo, client: ClientCapabilities, container: str
) -> str:
    """Direct Stream 音频：能 copy 则 copy，否则转 AAC。"""
    aas = source.default_audio_stream
    if not aas:
        return "copy"
    codec = (aas.codec_name or "").lower()
    if container == "hls_fmp4":
        if codec in _FMP4_COPY_AUDIO and client.supports_audio_codec(codec):
            return "copy"
        return "aac"
    if client.supports_audio_codec(codec):
        return "copy"
    return "aac"


def _direct_stream_profile(
    source: MediaInfo, out_audio: str, container: str = "hls_mpegts"
) -> TranscodingProfile:
    """Direct Stream 的转码规格：视频 copy，音频按需重编码。"""
    return TranscodingProfile(
        name="direct-stream-remux",
        container=container,
        video_codec="copy",
        audio_codec=out_audio,
        max_width=source.width,
        max_height=source.height,
        video_bitrate=0,
        max_video_bitrate=0,
        audio_bitrate=192_000,
        framerate=0.0,
        preset="veryfast",
        quality=23,
        hardware_acceleration="cpu",
        enable_hardware_decode=False,
        enable_hardware_encode=False,
    )


def _transcode_profile(
    source: MediaInfo,
    quality: str,
    client: ClientCapabilities,
) -> TranscodingProfile:
    """Transcode 的转码规格：按档位 + 源信息构建。"""
    profile = profile_for_quality(quality)
    width, height = resolve_target_dimensions(
        quality_rung(quality), source.width, source.height
    )
    return profile.with_dimensions(width, height)


def _transcode_reason(
    source: MediaInfo, client: ClientCapabilities, quality: str
) -> str:
    """生成转码决策的可读原因。"""
    reasons = []
    dp = can_direct_play(source, client)
    if dp:
        reasons.append(f"Direct Play 不可行({dp})")
    ds = can_direct_stream(source, client, quality)
    if ds:
        reasons.append(f"Direct Stream 不可行({ds})")
    if quality != "original":
        reasons.append(f"请求档位 {quality}")
    return "Transcode：" + "；".join(reasons) if reasons else "Transcode：需要重编码"
