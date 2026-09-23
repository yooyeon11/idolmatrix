"""FFmpeg Engine：把 FFmpeg 变成可管理、可观测的一等公民进程。

职责（对齐需求文档「第十七节 转码进程管理」「第二十节 FFmpeg 可观察性」）：
- start：启动 FFmpeg（HLS/MPEG-TS 持续输出 或 remux）
- stop / kill：优雅终止 → 超时强杀
- progress：从 -progress 文件解析输出时间与百分比
- stderr：行级监听，维护环形缓冲 + 可选完整日志
- pid / exit_code / full_command / stats(speed/fps/bitrate)：全量可观测

关键设计点：
1. FFmpeg 是**持续运行**的进程，而不是「每个 segment 启动一次」。
2. 保留既有修复 `-muxdelay 0 -muxpreload 0`（解决分片开头 PTS 偏移导致的卡顿）。
3. 输出走 HLS + MPEG-TS（.ts 分片），可落盘复用。
4. seek 通过 `-ss`（输入侧 seek）从指定时间点开始，不从头编码。
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from app.core.ffmpeg import require_ffmpeg
from app.media.hw_manager import HwSelection

logger = logging.getLogger(__name__)

# stderr 环形缓冲的最大行数（限制生产环境日志内存占用）
STDERR_TAIL_LINES = 200
# 进度文件中 out_time_ms 的周期刷新阈值（毫秒）
PROGRESS_REFRESH_MS = 200
# 进程等待超时：超过该时间仍未退出则 SIGKILL
STOP_TIMEOUT_SEC = 8.0


class FfmpegEngineError(RuntimeError):
    """FFmpeg 启动/运行错误；允许附带 HTTP status_code（如 429 并发上限）。"""

    def __init__(self, message: str = "", status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class HlsOutputSpec:
    """HLS 输出布局：全部落在 output_dir 下。"""

    output_dir: Path
    playlist_name: str = "playlist.m3u8"
    init_filename: str = "init.mp4"
    segment_duration: float = 6.0
    segment_type: str = "mpegts"  # mpegts | fmp4

    @property
    def playlist_path(self) -> Path:
        return self.output_dir / self.playlist_name

    @property
    def segment_pattern(self) -> str:
        return "segment_%05d.m4s" if self.segment_type == "fmp4" else "segment_%05d.ts"


@dataclass
class FfmpegProcess:
    """单个 FFmpeg 进程的运行时句柄（一等公民）。"""

    cmd: List[str]
    proc: subprocess.Popen
    progress_file: Path
    stderr_log: Optional[Path] = None
    total_duration: Optional[float] = None

    started_at: float = field(default_factory=time.time)
    stopped_at: Optional[float] = None
    exit_code: Optional[int] = None
    exit_event: threading.Event = field(default_factory=threading.Event)

    _progress: dict = field(default_factory=dict)
    _stats: dict = field(default_factory=dict)
    _stderr_tail: deque = field(default_factory=lambda: deque(maxlen=STDERR_TAIL_LINES))
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stderr_thread: Optional[threading.Thread] = None
    _monitor_thread: Optional[threading.Thread] = None
    _on_exit: Optional[Callable[["FfmpegProcess"], None]] = None

    # ===== 查询 =====

    @property
    def pid(self) -> Optional[int]:
        return self.proc.pid

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def progress(self) -> dict:
        with self._lock:
            return dict(self._progress)

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def stderr_tail(self, lines: int = 50) -> List[str]:
        with self._lock:
            return list(self._stderr_tail)[-lines:]

    # ===== 生命周期 =====

    def start_monitors(self, on_exit: Optional[Callable[["FfmpegProcess"], None]] = None) -> None:
        """启动 stderr 监听线程 + 进程退出监控线程。"""
        self._on_exit = on_exit
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, name=f"ffmpeg-stderr-{self.pid}", daemon=True
        )
        self._stderr_thread.start()
        self._monitor_thread = threading.Thread(
            target=self._monitor_exit, name=f"ffmpeg-monitor-{self.pid}", daemon=True
        )
        self._monitor_thread.start()

    def stop(self, timeout: float = STOP_TIMEOUT_SEC) -> int:
        """优雅终止进程；超时则 SIGKILL。返回最终 exit code。"""
        if self.proc.poll() is not None:
            return self.exit_code or self.proc.returncode
        self.proc.terminate()  # SIGTERM → ffmpeg 刷出尾部 segment
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg pid=%s 未在 %.1fs 内退出，执行 SIGKILL", self.pid, timeout)
            self.proc.kill()
            self.proc.wait(timeout=5)
        return self.proc.returncode

    def kill(self) -> int:
        if self.proc.poll() is not None:
            return self.proc.returncode
        self.proc.kill()
        self.proc.wait(timeout=5)
        return self.proc.returncode

    def to_dict(self) -> dict:
        """全量可观测信息（对齐「第二十节」）。"""
        prog = self.progress()
        return {
            "cmd": " ".join(self.cmd),
            "pid": self.pid,
            "running": self.running,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "exit_code": self.exit_code if self.exit_code is not None else self.proc.poll(),
            "progress": {
                "out_time_sec": prog.get("out_time_sec"),
                "percent": prog.get("percent"),
            },
            "stats": self.stats(),
            "stderr_tail": self.stderr_tail(50),
        }

    # ===== 内部 =====

    def _drain_stderr(self) -> None:
        """持续读取 stderr：写入环形缓冲 + 可选完整日志 + 解析 stats。"""
        log_f = None
        if self.stderr_log:
            log_f = self.stderr_log.open("a", encoding="utf-8", errors="replace")
        try:
            for raw in self.proc.stderr:
                line = raw.rstrip("\n")
                with self._lock:
                    self._stderr_tail.append(line)
                    self._parse_stats_line(line)
                if log_f:
                    log_f.write(line + "\n")
                    log_f.flush()
        except (OSError, ValueError):
            pass
        finally:
            if log_f:
                log_f.close()

    _STATS_PATTERN = re.compile(
        r"frame=\s*(?P<frame>\d+).*?"
        r"fps=\s*(?P<fps>[\d.]+).*?"
        r"size=\s*(?P<size>\S+).*?"
        r"time=\s*(?P<time>\S+).*?"
        r"bitrate=\s*(?P<bitrate>\S+).*?"
        r"speed=\s*(?P<speed>[\d.]+x|N/A)"
    )

    def _parse_stats_line(self, line: str) -> None:
        """从 stderr 的 stats 行解析 fps / speed / bitrate / out_time。"""
        if "speed=" not in line:
            return
        m = self._STATS_PATTERN.search(line)
        if not m:
            return
        d = m.groupdict()
        out_time = d.get("time")
        self._stats = {
            "fps": d.get("fps"),
            "speed": d.get("speed"),
            "bitrate": d.get("bitrate"),
            "size": d.get("size"),
            "out_time": out_time,
        }
        sec = _parse_clock(out_time)
        if sec is not None:
            self._progress["out_time_sec"] = sec
            if self.total_duration:
                self._progress["percent"] = min(
                    100, max(0, int(sec * 100 / self.total_duration))
                )

    def _monitor_exit(self) -> None:
        """等待进程退出，记录 exit_code / stopped_at，并触发回调。"""
        code = self.proc.wait()
        self.exit_code = code
        self.stopped_at = time.time()
        self.exit_event.set()
        if code != 0:
            tail = " | ".join(self.stderr_tail(5))
            logger.warning(
                "ffmpeg pid=%s 异常退出 code=%s, stderr: %s", self.pid, code, tail
            )
        if self._on_exit:
            try:
                self._on_exit(self)
            except Exception:  # 回调失败不能影响主流程
                logger.exception("ffmpeg on_exit 回调失败")


def _parse_clock(value: Optional[str]) -> Optional[float]:
    """解析 'HH:MM:SS.ff' 或 'MM:SS.ff' 或 'SS.ff' 为秒。"""
    if not value:
        return None
    parts = str(value).strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, TypeError):
        return None


def _drop_option(args: List[str], option: str) -> List[str]:
    """从 ffmpeg 参数列表中剔除 option 及其取值（option 必须带值）。"""
    out: List[str] = []
    i = 0
    while i < len(args):
        if args[i] == option:
            i += 2
            continue
        out.append(args[i])
        i += 1
    return out


def _read_progress_ms(path: Path) -> Optional[int]:
    """从 -progress 文件中读取最新 out_time_ms（单位：微秒）。"""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    last_ms: Optional[int] = None
    for line in content.splitlines():
        key, _, value = line.partition("=")
        if key == "out_time_ms":
            try:
                last_ms = int(value)
            except ValueError:
                pass
    return last_ms


class FfmpegEngine:
    """构建并启动 FFmpeg 进程。"""

    def __init__(self) -> None:
        self._active: dict = {}
        self._lock = threading.Lock()

    # ===== 命令构建 =====

    def build_hls_transcode_command(
        self,
        source: str,
        spec: HlsOutputSpec,
        hw: HwSelection,
        *,
        width: Optional[int] = None,
        height: Optional[int] = None,
        framerate: Optional[float] = None,
        video_bitrate: Optional[int] = None,
        audio_codec: str = "aac",
        audio_bitrate: int = 192_000,
        audio_channels: int = 2,
        start_offset: float = 0.0,
        progress_file: Optional[Path] = None,
    ) -> List[str]:
        """构建 HLS/MPEG-TS 转码命令（持续运行，video 重新编码）。"""
        cmd: List[str] = [
            require_ffmpeg(),
            "-hide_banner",
            "-y",
        ]
        # 硬件解码参数（-i 之前）
        cmd += list(hw.decode_args)
        if start_offset > 0:
            cmd += ["-ss", f"{start_offset:.3f}"]
        cmd += ["-i", source]

        cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
        cmd += ["-c:v", hw.encoder]
        encode_args = list(hw.encode_args)
        if hw.method == "qsv" and video_bitrate and video_bitrate > 0:
            # QSV 走 VBR 时剔除 ICQ 的 -global_quality，避免双速率控制模式歧义
            encode_args = _drop_option(encode_args, "-global_quality")
        cmd += encode_args
        # MPEG-TS 输出在浏览器 MSE / iOS 原生 HLS 更稳：关闭 B 帧（h264_vaapi /
        # libx264 / h264_qsv 均支持）
        cmd += ["-bf", "0"]
        # 明确 profile（VAAPI/libx264/QSV 均支持 high），避免歧义默认
        cmd += ["-profile:v", "high"]
        if hw.video_filter:
            cmd += ["-vf", hw.video_filter]
        # 只有 CPU 软件转码才强制 -pix_fmt；硬件路径（QSV/VAAPI）的帧已由
        # 解码器 / 滤镜链落到 nv12，额外强制 -pix_fmt 会触发不必要且易失败的格式转换。
        if hw.method == "cpu":
            cmd += ["-pix_fmt", hw.pixel_format]
        if framerate:
            cmd += ["-r", f"{framerate:.3f}".rstrip("0").rstrip(".")]
        # 恒定帧率打包：强制 CFR，避免 VFR 源（如部分 MKV）在 TS/HLS 里派生出
        # 非标准时间戳进而影响起播，保证时间基稳定。
        # 不带 -r 时不改变实际帧率，仅消除时间戳抖动。
        cmd += ["-fps_mode", "cfr"]
        # 码率控制（每种路径只保留一种明确的速率控制模式）：
        # VAAPI: 有码率 → VBR (-b:v + -maxrate + -bufsize)；无码率 → CQP fallback (-qp 23)
        # CPU:   恒定 CRF 质量模式；有码率 → capped CRF（追加 -maxrate/-bufsize 封顶），
        #        不追加 -b:v（libx264 中与 -crf 共存时行为依赖编码器版本，语义不确定）
        # QSV:   有码率 → VBR（已剔除 -global_quality）；无码率 → ICQ (-global_quality)
        if hw.method == "vaapi":
            if video_bitrate and video_bitrate > 0:
                cmd += ["-b:v", str(video_bitrate)]
                cmd += ["-maxrate", str(int(video_bitrate * 4 / 3))]
                cmd += ["-bufsize", str(video_bitrate * 2)]
            else:
                cmd += ["-qp", "23"]  # CQP fallback（original 质量档位无码率限制时）
        elif hw.method == "qsv":
            if video_bitrate and video_bitrate > 0:
                cmd += ["-b:v", str(video_bitrate)]
                cmd += ["-maxrate", str(int(video_bitrate * 4 / 3))]
                cmd += ["-bufsize", str(video_bitrate * 2)]
        elif video_bitrate and video_bitrate > 0:
            # CPU capped CRF：保持 -crf 的质量导向，用 maxrate 封顶满足档位码率上限
            cmd += ["-maxrate", str(video_bitrate)]
            cmd += ["-bufsize", str(video_bitrate * 2)]
        # 关键帧对齐分片边界，提升 seek 体验（n_forced 从 0 起，首个关键帧在 t=0）
        cmd += [
            "-force_key_frames",
            f"expr:gte(t,n_forced*{spec.segment_duration:g})",
        ]

        cmd += [
            "-c:a",
            audio_codec,
            "-b:a",
            f"{audio_bitrate}",
            "-ac",
            str(audio_channels),
        ]
        cmd += self._hls_output_args(spec, progress_file)
        return cmd

    def build_hls_remux_command(
        self,
        source: str,
        spec: HlsOutputSpec,
        *,
        audio_codec: str = "copy",
        start_offset: float = 0.0,
        progress_file: Optional[Path] = None,
    ) -> List[str]:
        """构建 HLS/MPEG-TS remux 命令（Direct Stream：视频 copy 不重编码）。"""
        cmd: List[str] = [require_ffmpeg(), "-hide_banner", "-y"]
        if start_offset > 0:
            cmd += ["-ss", f"{start_offset:.3f}"]
        cmd += ["-i", source]
        cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
        cmd += ["-c:v", "copy"]
        if audio_codec and audio_codec != "copy":
            cmd += ["-c:a", audio_codec]
        else:
            cmd += ["-c:a", "copy"]
        cmd += self._hls_output_args(spec, progress_file)
        return cmd

    def _hls_output_args(
        self, spec: HlsOutputSpec, progress_file: Optional[Path]
    ) -> List[str]:
        args = [
            "-f",
            "hls",
            "-hls_time",
            f"{spec.segment_duration:g}",
            # 用 -hls_playlist_type event（事件式）而不是 vod：
            #   - 与 hls_list_size 0 一样保留全部分片、完成时写 #EXT-X-ENDLIST；
            #   - 事件式清单是「增量追加」，首个分片写出后 playlist 即可读，
            #     避免 NAS 上 4K 转码首片耗时过长时播放器拿不到 manifest 报错；
            #   - 事件式不会像默认「直播」那样把客户端推到 live-edge：播放更稳定、
            #     不会起播几秒后停滞，且能在已转出的范围内拖动。
            "-hls_list_size",
            "0",
            "-hls_playlist_type",
            "event",
            "-hls_segment_type",
            spec.segment_type if spec.segment_type in ("mpegts", "fmp4") else "mpegts",
            "-hls_flags",
            "independent_segments",
            "-hls_segment_filename",
            str(spec.output_dir / spec.segment_pattern),
            "-max_muxing_queue_size",
            "4096",
            # 关键修复：消除分片开头 PTS 偏移导致的起播卡顿
            "-muxdelay",
            "0",
            "-muxpreload",
            "0",
        ]
        if spec.segment_type == "fmp4":
            args += ["-hls_fmp4_init_filename", spec.init_filename or "init.mp4"]
        if progress_file:
            args += ["-progress", str(progress_file)]
        args += [str(spec.playlist_path)]
        return args

    # ===== 进程启动 =====

    def start(
        self,
        cmd: List[str],
        *,
        progress_file: Path,
        stderr_log: Optional[Path] = None,
        total_duration: Optional[float] = None,
        on_exit: Optional[Callable[[FfmpegProcess], None]] = None,
        cwd: Optional[Path] = None,
    ) -> FfmpegProcess:
        """启动 FFmpeg 并包装为 FfmpegProcess。失败抛 FfmpegEngineError。

        cwd 指定 FFmpeg 工作目录：HLS 分片用绝对路径写盘，cwd 仅作兜底，
        必须让 FFmpeg 写到转码输出目录，避免产物落到后端进程目录。
        """
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        if stderr_log:
            stderr_log.parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=str(cwd) if cwd else None,
            )
        except OSError as e:
            raise FfmpegEngineError(f"启动 ffmpeg 失败: {e}") from e
        fp = FfmpegProcess(
            cmd=cmd,
            proc=proc,
            progress_file=progress_file,
            stderr_log=stderr_log,
            total_duration=total_duration,
        )

        def _on_exit_drop(done: FfmpegProcess) -> None:
            try:
                if on_exit:
                    on_exit(done)
            finally:
                if done.pid:
                    self.drop_process(done.pid)

        # 先登记再开监控线程，避免秒退进程在登记前就被 drop
        with self._lock:
            self._active[proc.pid] = fp
        fp.start_monitors(on_exit=_on_exit_drop)
        logger.info("ffmpeg 启动 pid=%s cmd=%s", proc.pid, " ".join(cmd))
        return fp

    def read_progress(self, path: Path, total_duration: Optional[float] = None) -> dict:
        """从进度文件解析 {out_time_sec, percent}。"""
        ms = _read_progress_ms(path)
        if ms is None:
            return {}
        sec = ms / 1_000_000.0
        percent = None
        if total_duration:
            percent = min(100, max(0, int(sec * 100 / total_duration)))
        return {"out_time_sec": sec, "percent": percent}

    def active_processes(self) -> List[FfmpegProcess]:
        with self._lock:
            return [p for p in self._active.values() if p.running]

    def drop_process(self, pid: int) -> None:
        with self._lock:
            self._active.pop(pid, None)


# 模块级单例
ffmpeg_engine = FfmpegEngine()
