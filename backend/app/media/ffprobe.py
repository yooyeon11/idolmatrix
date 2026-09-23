"""ffprobe 封装：读取媒体文件元数据。

设计：通过 subprocess 调用 ffprobe 并解析 JSON 输出，
返回结构化的技术字段，供 MusicVideo 写入数据库。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.core.ffmpeg import require_ffprobe


@dataclass
class ProbeResult:
    """ffprobe 解析结果，字段对齐 MusicVideo 模型技术字段。"""

    duration: Optional[float]  # 秒
    width: Optional[int]
    height: Optional[int]
    video_codec: Optional[str]
    audio_codec: Optional[str]
    frame_rate: Optional[float]
    bitrate: Optional[int]  # bps
    file_size: Optional[int]  # 字节
    format_name: Optional[str] = None
    raw: Optional[dict] = None  # 原始 ffprobe 输出，便于调试

    @property
    def duration_seconds_int(self) -> Optional[int]:
        return int(self.duration) if self.duration is not None else None


class ProbeError(RuntimeError):
    pass


def probe(path: str | Path) -> ProbeResult:
    """对单个媒体文件执行 ffprobe。"""
    try:
        binary = require_ffprobe()
    except RuntimeError as e:
        raise ProbeError(str(e)) from e
    path = str(path)
    if not Path(path).exists():
        raise ProbeError(f"文件不存在: {path}")

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
        raise ProbeError(f"ffprobe 超时: {path}") from e

    if res.returncode != 0:
        raise ProbeError(
            f"ffprobe 失败 (code={res.returncode}): {res.stderr.strip() or '未知错误'}"
        )

    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError as e:
        raise ProbeError(f"ffprobe 输出解析失败: {e}") from e

    return _parse(data)


def _parse(data: dict) -> ProbeResult:
    fmt: dict = data.get("format", {}) or {}
    streams: list = data.get("streams", []) or []

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = _to_float(fmt.get("duration") or (video_stream or {}).get("duration"))
    bitrate = _to_int(fmt.get("bit_rate"))
    file_size = _to_int(fmt.get("size"))

    width = height = None
    video_codec = frame_rate = None
    if video_stream:
        width = _to_int(video_stream.get("width"))
        height = _to_int(video_stream.get("height"))
        video_codec = video_stream.get("codec_name")
        frame_rate = _parse_frame_rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))

    audio_codec = audio_stream.get("codec_name") if audio_stream else None

    return ProbeResult(
        duration=duration,
        width=width,
        height=height,
        video_codec=video_codec,
        audio_codec=audio_codec,
        frame_rate=frame_rate,
        bitrate=bitrate,
        file_size=file_size,
        format_name=fmt.get("format_name"),
        raw=data,
    )


def _to_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> Optional[int]:
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
