"""媒体处理统一入口。

设计说明：
- 不使用任何 ffmpeg 绑定库（ffmpeg-python 等），统一 subprocess 调用 ffmpeg/ffprobe。
- 优点：参数完全可控、可记录完整 stderr 日志、跨平台一致、便于解析 -progress 输出。
- ffprobe 用于只读元数据（扫描入库时写库）。
- ffmpeg 用于派生产物：封面截取（extract_thumbnail）。

注：实时播放转码已从一次性文件转码重构为 Emby 式 TranscodeSession，
由 ffmpeg_engine（app.media.ffmpeg_engine）负责常驻 FFmpeg 进程管理。
"""

from app.media.ffmpeg import FFmpegError, extract_thumbnail
from app.media.ffprobe import ProbeError, ProbeResult, probe

__all__ = [
    "probe",
    "ProbeResult",
    "ProbeError",
    "FFmpegError",
    "extract_thumbnail",
]
