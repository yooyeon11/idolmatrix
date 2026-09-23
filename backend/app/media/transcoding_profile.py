"""Transcoding Profile：转码规格的一等公民。

从「单个任务参数」（固定 CRF + 固定 1280:-2）升级为可复用的转码配置对象。

设计约定：
- profile 描述「输出规格」，不包含运行时硬件决策。
  硬件（QSV / VAAPI / CPU）由 HardwareAccelerationManager 在启动 FFmpeg 时
  探测并落定，写入 TranscodeSession.hardware_acceleration。
- profile_key 只依赖语义字段（编码器 / 分辨率 / 码率 / 帧率 / 容器 / 质量），
  保证相同规格跨请求稳定，用于 TranscodeSession 去重与缓存目录命名。
- 质量阶梯（original / 2160p / 1080p / 720p）的可用性由 Playback Resolver
  结合源视频分辨率、源码率与客户端能力动态计算，防止出现
  「源只有 720P 却显示 1080P」的错误档位。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from app.core.config import settings

# ===== 质量阶梯 =====
# 每个档位定义目标最大分辨率与建议最大码率（bps）。
# 0 表示不限制。实际可用档位由 Resolver 按源能力过滤。
#
# ⚠ 口径 = **短边**（min(width, height)）；档位表本身按横屏书写：
#   `max_height` 是「**短边**上限」，`max_width` 是「长边上限」。
#   即竖屏直拍 2160×3840 与横屏 3840×2160 同属 `2160p`，
#   与 `video_meta.RESOLUTION_TIERS`（统计/筛选侧）保持同一套朝向口径。
#   历史实现按 `height` 判定 → 竖屏片选「它自己的档位」被判成需要降分辨率而强制转码
#   （2160×3840 选 2160p 会被缩成 1216×2160，只剩约 1/3 像素），且菜单里还会冒出
#   比源还高的档位（1080×1920 会出现比 1080p 更高的档）。


@dataclass(frozen=True)
class QualityRung:
    name: str
    max_width: int
    max_height: int
    max_bitrate: int  # bps，0 = 不限制


# v3.4.4（用户定死）：转码档位收敛为 2160p / 1080p / 720p 三档
# （1440p / 480p / 360p 已删除，不再作为转码目标档位；统计/筛选侧
# `video_meta.RESOLUTION_TIERS` 是另一套 9 档口径，不受影响）。
QUALITY_LADDER: List[QualityRung] = [
    QualityRung("original", 0, 0, 0),
    QualityRung("2160p", 3840, 2160, 45_000_000),
    QualityRung("1080p", 1920, 1080, 12_000_000),
    QualityRung("720p", 1280, 720, 6_000_000),
]

_QUALITY_BY_NAME = {r.name: r for r in QUALITY_LADDER}


def short_side(width: Optional[int], height: Optional[int]) -> int:
    """媒体短边 `min(width, height)`：画质档位的唯一判定口径。

    竖屏直拍 1080×1920 与横屏 1920×1080 同属 1080p 档 —— 若按 height 判，
    竖屏片会被算成 1920「高」而落到错误的档位（同一个坑统计侧
    `video_meta.RESOLUTION_TIERS` 已经踩过并改为短边）。

    单边缺失时退回另一条边（历史数据常见单边为空），避免被判成 0 而丢掉全部档位。
    """
    w = int(width or 0)
    h = int(height or 0)
    if w <= 0:
        return h if h > 0 else 0
    if h <= 0:
        return w
    return min(w, h)


def long_side(width: Optional[int], height: Optional[int]) -> int:
    """媒体长边 `max(width, height)`：用于比对档位的长边上限。"""
    w = int(width or 0)
    h = int(height or 0)
    if w <= 0:
        return h if h > 0 else 0
    if h <= 0:
        return w
    return max(w, h)


def quality_rung(name: str) -> QualityRung:
    """按档位名取阶梯；未知档位回退 original。"""
    return _QUALITY_BY_NAME.get(name, _QUALITY_BY_NAME["original"])


def resolve_target_dimensions(
    rung: QualityRung, source_width: int, source_height: int
) -> Tuple[int, int]:
    """根据档位与源分辨率计算实际输出宽高（保持宽高比与**朝向**，双数对齐）。

    ⚠ 口径 = 短边：把**短边**压到 `rung.max_height`、长边同比缩放，再用
    `rung.max_width` 兜一次长边上限。竖屏源因此不会被按横屏包络压扁
    （旧实现把 height 压到档位高度，竖屏 2160×3840 选 1440p 会得到 810×1440，
    比源还小很多；短边口径下得到正确的 1440×2560）。

    original 档位、或源本身已在档位范围内时保持源尺寸（不缩放、不放大）。
    """
    if source_width <= 0 or source_height <= 0:
        return source_width, source_height
    if rung.name == "original" or rung.max_height <= 0:
        return source_width, source_height
    short = min(source_width, source_height)
    long = max(source_width, source_height)
    if short <= rung.max_height and (rung.max_width <= 0 or long <= rung.max_width):
        return source_width, source_height
    scale = rung.max_height / short if short > rung.max_height else 1.0
    if rung.max_width > 0 and long * scale > rung.max_width:
        scale = min(scale, rung.max_width / long)
    scale = min(scale, 1.0)  # 档位只降不升
    width = max(2, int(round(source_width * scale / 2) * 2))
    height = max(2, int(round(source_height * scale / 2) * 2))
    return width, height


@dataclass(frozen=True)
class TranscodingProfile:
    """一个完整的转码规格。

    字段对齐需求文档「第十节 Transcoding Profile」。
    """

    name: str
    container: str = "hls_mpegts"  # hls_mpegts / mp4
    video_codec: str = "h264"  # h264 / hevc
    audio_codec: str = "aac"  # aac / copy
    max_width: int = 0  # 0 = 不限
    max_height: int = 0  # 0 = 不限
    video_bitrate: int = 0  # bps，0 = 不限制
    max_video_bitrate: int = 0  # bps，0 = 不限制
    audio_bitrate: int = 192_000  # bps
    framerate: float = 0.0  # 0 = 保持源帧率
    preset: str = "veryfast"
    quality: int = 23  # CRF（CPU/x264）或 QP（QSV/VAAPI），语义由硬件决定
    hardware_acceleration: str = "auto"  # auto / qsv / vaapi / cpu（期望值）
    enable_hardware_decode: bool = True
    enable_hardware_encode: bool = True

    # 参与去重的语义字段（硬件是运行时决策，不参与去重）
    _KEY_FIELDS = (
        "container",
        "video_codec",
        "audio_codec",
        "max_width",
        "max_height",
        "video_bitrate",
        "max_video_bitrate",
        "audio_bitrate",
        "framerate",
        "preset",
        "quality",
    )

    @property
    def profile_key(self) -> str:
        """语义指纹：相同规格 → 相同 key，跨请求稳定。"""
        payload = json.dumps(
            {f: getattr(self, f) for f in self._KEY_FIELDS},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def with_dimensions(self, width: int, height: int) -> "TranscodingProfile":
        """把输出规格落到具体分辨率（冻结 dataclass，返回新实例）。"""
        return TranscodingProfile(
            **{**asdict(self), "max_width": width, "max_height": height}
        )

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def profile_for_quality(quality: str, src: object = None) -> TranscodingProfile:
    """从质量档位 + 服务器配置构建一个转码 profile。

    src 预留：后续可携带源视频信息（码率/帧率）做精细化约束。
    """
    rung = quality_rung(quality)
    return TranscodingProfile(
        name=f"kpop-{quality}",
        container="hls_mpegts",
        video_codec=_codec_short(settings.transcode_video_codec),
        audio_codec=_codec_short(settings.transcode_audio_codec),
        max_width=rung.max_width,
        max_height=rung.max_height,
        video_bitrate=rung.max_bitrate,
        max_video_bitrate=rung.max_bitrate,
        audio_bitrate=_parse_bitrate(settings.transcode_audio_bitrate),
        framerate=0.0,
        preset=settings.transcode_video_preset,
        quality=settings.transcode_video_crf,
        hardware_acceleration=settings.transcode_hwaccel,
        enable_hardware_decode=True,
        enable_hardware_encode=True,
    )


def available_qualities(
    source_width: int,
    source_height: int,
    client_max_height: int = 0,
    client_max_width: int = 0,
) -> List[str]:
    """按源分辨率与客户端能力计算可用档位列表。

    规则（**短边口径**，与 `video_meta.RESOLUTION_TIERS` / 前端 `preQualities` 一致）：
    - original 恒可用（Direct Play / Direct Stream 候选）。
    - 转码档位仅保留「源**短边**足以支撑」的档位 —— 防横屏 720P 源出现 1080P，
      也防竖屏 1080×1920 出现比它自己还高的档位。
    - 客户端 `max_height` 按短边、`max_width` 按长边进一步裁剪。
    - 宽高任一缺失时无法判断朝向 → 只给 original（不猜档位）。
    """
    result = ["original"]
    if source_width <= 0 or source_height <= 0:
        return result
    short = min(source_width, source_height)
    for rung in QUALITY_LADDER[1:]:
        if rung.max_height <= 0:
            continue
        if short < rung.max_height:
            continue  # 源短边不足，档位不可用
        if client_max_height > 0 and rung.max_height > client_max_height:
            continue
        if client_max_width > 0 and rung.max_width > client_max_width:
            continue
        result.append(rung.name)
    return result


def _codec_short(codec: str) -> str:
    """把 ffmpeg 编码器名映射为 profile 语义名（libx264→h264）。"""
    mapping = {
        "libx264": "h264",
        "h264_qsv": "h264",
        "h264_vaapi": "h264",
        "libx265": "hevc",
        "hevc_qsv": "hevc",
        "hevc_vaapi": "hevc",
        "aac": "aac",
        "copy": "copy",
    }
    return mapping.get(codec, codec)


def _parse_bitrate(value: str) -> int:
    """'192k' / '1.5M' → bps。"""
    try:
        v = value.strip().lower()
        if v.endswith("k"):
            return int(float(v[:-1]) * 1000)
        if v.endswith("m"):
            return int(float(v[:-1]) * 1_000_000)
        return int(v)
    except (TypeError, ValueError):
        return 192_000
