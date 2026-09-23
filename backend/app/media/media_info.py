"""媒体文件结构信息（流级别）。

与 app/media/ffprobe.py 的 ProbeResult 的区别：
- ProbeResult：单流摘要，服务于 MusicVideo 入库时写入技术字段。
- MediaInfo：完整流列表（video / audio / subtitle），
  服务于播放决策（Direct Play / Direct Stream / Transcode），
  为后续音轨 / 字幕选择预留结构。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from app.core.ffmpeg import require_ffprobe

# 视频编码 -> 浏览器 / 容器兼容归一
_CONTAINER_PRIMARY = {
    "mov": "mp4",  # QuickTime 系容器统一按 mp4 家族处理
    "mp4": "mp4",
    "m4v": "mp4",
    "matroska": "mkv",
    "webm": "webm",
    "mpegts": "mpegts",
    "avi": "avi",
    "flv": "flv",
    "wmv": "wmv",
    "asf": "wmv",
    "mpeg": "mpeg",
    "mxf": "mxf",
    "ogg": "ogg",
    "ogv": "ogg",
}


@dataclass
class MediaStream:
    index: int
    codec_type: str  # video / audio / subtitle
    codec_name: str
    profile: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    pixel_format: Optional[str] = None
    channels: Optional[int] = None
    channel_layout: Optional[str] = None
    language: Optional[str] = None
    bit_rate: Optional[int] = None  # bps
    frame_rate: Optional[float] = None
    is_default: bool = False


@dataclass
class MediaInfo:
    path: str
    format_name: str = ""
    container: str = ""
    duration: float = 0.0  # 秒
    bit_rate: int = 0  # bps
    size: int = 0  # 字节
    video_streams: List[MediaStream] = field(default_factory=list)
    audio_streams: List[MediaStream] = field(default_factory=list)
    subtitle_streams: List[MediaStream] = field(default_factory=list)

    # ===== 便捷属性 =====
    @property
    def video_stream(self) -> Optional[MediaStream]:
        return self.video_streams[0] if self.video_streams else None

    @property
    def default_audio_stream(self) -> Optional[MediaStream]:
        for s in self.audio_streams:
            if s.is_default:
                return s
        return self.audio_streams[0] if self.audio_streams else None

    @property
    def width(self) -> int:
        vs = self.video_stream
        return vs.width or 0 if vs else 0

    @property
    def height(self) -> int:
        vs = self.video_stream
        return vs.height or 0 if vs else 0

    @property
    def video_codec(self) -> Optional[str]:
        vs = self.video_stream
        return vs.codec_name if vs else None

    @property
    def audio_codec(self) -> Optional[str]:
        aas = self.default_audio_stream
        return aas.codec_name if aas else None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "container": self.container,
            "format_name": self.format_name,
            "duration": self.duration,
            "bit_rate": self.bit_rate,
            "size": self.size,
            "width": self.width,
            "height": self.height,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "video_streams": [vars(s) for s in self.video_streams],
            "audio_streams": [vars(s) for s in self.audio_streams],
            "subtitle_streams": [vars(s) for s in self.subtitle_streams],
        }


class MediaProbeError(RuntimeError):
    pass


def probe_media_info(path: str | Path) -> MediaInfo:
    """对媒体文件执行 ffprobe，返回完整的流级信息。"""
    try:
        binary = require_ffprobe()
    except RuntimeError as e:
        raise MediaProbeError(str(e)) from e
    path = str(path)
    if not Path(path).exists():
        raise MediaProbeError(f"文件不存在: {path}")

    cmd = [
        binary,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=False
        )
    except subprocess.TimeoutExpired as e:
        raise MediaProbeError(f"ffprobe 超时: {path}") from e
    if res.returncode != 0:
        raise MediaProbeError(
            f"ffprobe 失败 (code={res.returncode}): {res.stderr.strip() or '未知错误'}"
        )
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError as e:
        raise MediaProbeError(f"ffprobe 输出解析失败: {e}") from e
    return _parse(path, data)


def _parse(path: str, data: dict) -> MediaInfo:
    fmt: dict = data.get("format", {}) or {}
    streams: list = data.get("streams", []) or []
    format_name = (fmt.get("format_name") or "").strip()
    info = MediaInfo(
        path=path,
        format_name=format_name,
        container=normalize_container(format_name),
        duration=_to_float(fmt.get("duration")) or 0.0,
        bit_rate=_to_int(fmt.get("bit_rate")) or 0,
        size=_to_int(fmt.get("size")) or 0,
    )
    for raw in streams:
        codec_type = raw.get("codec_type")
        if codec_type not in ("video", "audio", "subtitle"):
            continue
        stream = MediaStream(
            index=_to_int(raw.get("index")) or 0,
            codec_type=codec_type,
            codec_name=(raw.get("codec_name") or "").lower(),
            profile=raw.get("profile") or "",
            width=_to_int(raw.get("width")),
            height=_to_int(raw.get("height")),
            pixel_format=raw.get("pix_fmt"),
            channels=_to_int(raw.get("channels")),
            channel_layout=raw.get("channel_layout"),
            language=raw.get("tags", {}).get("language") if raw.get("tags") else None,
            bit_rate=_to_int(raw.get("bit_rate")),
            frame_rate=_parse_frame_rate(
                raw.get("avg_frame_rate") or raw.get("r_frame_rate")
            ),
            is_default=bool(
                (raw.get("disposition") or {}).get("default")
                if isinstance(raw.get("disposition"), dict)
                else False
            ),
        )
        if codec_type == "video":
            info.video_streams.append(stream)
        elif codec_type == "audio":
            info.audio_streams.append(stream)
        else:
            info.subtitle_streams.append(stream)
    return info


def normalize_container(format_name: str) -> str:
    """把 ffprobe 的 format_name（如 'mov,mp4,m4a,3gp'）归一为单一容器名。"""
    if not format_name:
        return ""
    primary = format_name.split(",")[0].strip().lower()
    return _CONTAINER_PRIMARY.get(primary, primary)


def _to_float(v: object) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _to_int(v: object) -> Optional[int]:
    try:
        return int(float(v)) if v is not None else None
    except (TypeError, ValueError):
        return None


def _parse_frame_rate(rate: Optional[str]) -> Optional[float]:
    """形如 '30000/1001' 的分式帧率。"""
    if not rate or rate == "0/0":
        return None
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else None
        return float(rate)
    except (TypeError, ValueError):
        return None
