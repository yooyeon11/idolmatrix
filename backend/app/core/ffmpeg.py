"""FFmpeg / ffprobe 路径检测与可用性自检。

设计原则：不依赖任何 ffmpeg 绑定库，统一通过 subprocess 调用，便于：
- 完全控制参数与进度日志
- 准确捕获 stderr 与退出码
- 跨平台一致行为
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.config import settings


class FFmpegNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class FFmpegBinaries:
    ffmpeg: str
    ffprobe: str
    ffmpeg_version: str | None
    ffprobe_version: str | None


def _detect(name: str, configured: str) -> str | None:
    """优先用配置路径，否则从 PATH 检测。"""
    if configured:
        p = Path(configured)
        # 直接路径
        if p.exists() and p.is_file():
            return str(p.resolve())
        # 当作命令名交给 shutil.which
    found = shutil.which(configured or name)
    return found


@lru_cache(maxsize=1)
def resolve_binaries() -> FFmpegBinaries:
    ffmpeg = _detect("ffmpeg", settings.ffmpeg_path)
    ffprobe = _detect("ffprobe", settings.ffprobe_path)

    ffmpeg_version = _version(ffmpeg) if ffmpeg else None
    ffprobe_version = _version(ffprobe) if ffprobe else None

    return FFmpegBinaries(
        ffmpeg=ffmpeg or "",
        ffprobe=ffprobe or "",
        ffmpeg_version=ffmpeg_version,
        ffprobe_version=ffprobe_version,
    )


def _version(binary_path: str) -> str | None:
    try:
        res = subprocess.run(
            [binary_path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if res.returncode != 0:
            return None
        return res.stdout.splitlines()[0] if res.stdout else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def check_available() -> tuple[bool, str]:
    """启动自检：返回 (是否可用, 人类可读信息)。"""
    bins = resolve_binaries()
    missing = []
    if not bins.ffmpeg:
        missing.append("ffmpeg")
    if not bins.ffprobe:
        missing.append("ffprobe")

    if missing:
        msg = (
            f"未检测到 {' / '.join(missing)}。"
            "请在 .env 中配置 FFMPEG_PATH / FFPROBE_PATH，"
            "或将其所在目录加入系统 PATH。"
        )
        return False, msg

    return True, (
        f"ffmpeg: {bins.ffmpeg_version} | ffprobe: {bins.ffprobe_version}"
    )


def require_ffmpeg() -> str:
    bins = resolve_binaries()
    if not bins.ffmpeg:
        raise FFmpegNotFoundError(
            "ffmpeg 不可用，请在 .env 中配置 FFMPEG_PATH"
        )
    return bins.ffmpeg


def require_ffprobe() -> str:
    bins = resolve_binaries()
    if not bins.ffprobe:
        raise FFmpegNotFoundError(
            "ffprobe 不可用，请在 .env 中配置 FFPROBE_PATH"
        )
    return bins.ffprobe
