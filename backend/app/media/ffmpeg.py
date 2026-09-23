"""ffmpeg 封装：封面截取（缩略图）。

设计说明：
- 实时播放转码已重构为 Emby 式 TranscodeSession：常驻 FFmpeg 进程（HLS/MPEG-TS 持续输出）
  由 app.media.ffmpeg_engine 管理，硬件方案由 app.media.hw_manager 决策，
  本模块不再承载任何播放转码逻辑。
- 本模块仅保留只读/轻量派生产物能力：封面单帧截取（extract_thumbnail）。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Union

from app.core.ffmpeg import require_ffmpeg
from app.media.ffprobe import ProbeError, probe

PathLike = Union[str, Path]


class FFmpegError(RuntimeError):
    pass


def extract_thumbnail(
    source: PathLike,
    output: PathLike,
    at_seconds: float = 5.0,
    *,
    width: int | None = None,
) -> None:
    """截取单帧作为封面（默认 5 秒处）。width 指定时缩放到指定宽度（选帧候选用小图）。"""
    source = str(source)
    output = str(output)
    Path(output).parent.mkdir(parents=True, exist_ok=True)

    # 若文件短于 at_seconds，ffprobe 后取中点
    try:
        d = probe(source).duration
        if d is not None and d < at_seconds:
            at_seconds = max(0.0, d / 2.0)
    except ProbeError:
        pass

    cmd = [require_ffmpeg(), "-y", "-ss", f"{at_seconds:.2f}", "-i", source, "-frames:v", "1"]
    if width is not None:
        cmd += ["-vf", f"scale={int(width)}:-2", "-q:v", "4"]
    else:
        cmd += ["-q:v", "3"]
    cmd.append(output)
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    if res.returncode != 0 or not Path(output).exists():
        raise FFmpegError(
            f"封面截取失败 (code={res.returncode}): {(res.stderr or '')[-300:].strip()}"
        )


def extract_photo_thumbnail(
    source: PathLike,
    output: PathLike,
    *,
    is_video: bool = False,
    width: int = 480,
) -> None:
    """生成图库宫格用的 JPEG 缩略图。图片缩放；视频截一帧再缩放。"""
    source = str(source)
    dest = Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".part.jpg")
    ffmpeg = require_ffmpeg()

    def _run(ss: float | None) -> subprocess.CompletedProcess:
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        if is_video and ss is not None:
            cmd += ["-ss", f"{ss:.2f}"]
        cmd += [
            "-i",
            source,
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:-2",
            "-q:v",
            "4",
            str(tmp),
        ]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=45, check=False)

    if tmp.exists():
        tmp.unlink(missing_ok=True)
    if is_video:
        res = _run(1.0)
        if res.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 0:
            tmp.unlink(missing_ok=True)
            res = _run(0.0)
    else:
        res = _run(None)
    if res.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 0:
        tmp.unlink(missing_ok=True)
        raise FFmpegError(
            f"照片缩略图失败 (code={res.returncode}): {(res.stderr or '')[-300:].strip()}"
        )
    os.replace(tmp, dest)


def crop_still(
    source: PathLike,
    output: PathLike,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    out_width: int,
    out_height: int,
) -> None:
    """从静帧裁一块并缩放到目标尺寸，输出 JPEG。"""
    source = str(source)
    dest = Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".part.jpg")
    if tmp.exists():
        tmp.unlink(missing_ok=True)
    vf = (
        f"crop={int(width)}:{int(height)}:{int(x)}:{int(y)},"
        f"scale={int(out_width)}:{int(out_height)}:flags=lanczos"
    )
    cmd = [
        require_ffmpeg(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        source,
        "-frames:v",
        "1",
        "-vf",
        vf,
        "-q:v",
        "2",
        str(tmp),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
    if res.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 0:
        tmp.unlink(missing_ok=True)
        raise FFmpegError(
            f"裁切失败 (code={res.returncode}): {(res.stderr or '')[-300:].strip()}"
        )
    os.replace(tmp, dest)
