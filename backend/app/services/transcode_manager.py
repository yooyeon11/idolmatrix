"""Transcode Manager：转码会话的编排中枢（Phase 5）。

职责（对齐需求文档「第十五/十六/十七/十八/二十一节」）：
- get_or_create：按 cache_key 复用转码会话（相同源 + 相同 profile + 相同协议 +
  相同起始窗口 → 同一个 FFmpeg，多个 PlaybackSession 共享）
- start：构建 FFmpeg 命令并启动（延迟到首次 manifest 请求）
- monitor：进程退出回调 → 更新 DB（exit_code / stderr_tail / status）
- stop / kill：优雅终止 FFmpeg（terminate → kill），引用计数归零时触发
- recover_stale：启动时把「僵尸 processing」会话恢复为 failed
- cleanup：闲置超时回收（停止无引用的 FFmpeg、删除过期缓存目录）

进程模型（已确认）：单容器单进程内管理 FFmpeg（subprocess + 监控线程），
通过 in-memory 的 _command_spec 保留每个会话的命令构建上下文；
服务器重启后旧会话一律 recover 为 failed，因此无需持久化完整 profile。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import SessionLocal
from app.media.ffmpeg_engine import (
    FfmpegEngineError,
    FfmpegProcess,
    HlsOutputSpec,
    ffmpeg_engine,
)
from app.media.hw_manager import HwSelection, hw_manager
from app.media.transcoding_profile import TranscodingProfile
from app.models.playback import (
    PS_CREATED,
    PS_FAILED,
    PS_PAUSED,
    PS_PLAYING,
    PS_STARTING,
    PS_STOPPED,
    TS_CREATED,
    TS_FAILED,
    TS_RUNNING,
    TS_STARTING,
    TS_STOPPED,
    TS_STOPPING,
    PlaybackSession,
    TranscodeSession,
)

logger = logging.getLogger(__name__)

# 活跃（未终止）的转码会话状态
ACTIVE_TS = frozenset({TS_CREATED, TS_STARTING, TS_RUNNING})
# 活跃（未终止）的播放会话状态
ACTIVE_PS = frozenset({PS_CREATED, PS_STARTING, PS_PLAYING, PS_PAUSED})

# stderr 尾部入库长度上限（限制生产库体积）
STDERR_STORE_LIMIT = 2000


def build_cache_key(
    source_path: str,
    profile_key: str,
    protocol: str,
    window_start: int,
    audio_stream_id: Optional[int],
    subtitle_stream_id: Optional[int],
) -> str:
    """转码会话去重键：源 + profile + 协议 + 起始窗口 + 音轨/字幕。"""
    payload = "|".join(
        str(x)
        for x in (
            source_path,
            profile_key,
            protocol,
            window_start,
            audio_stream_id,
            subtitle_stream_id,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def window_start_for(seconds: float, segment_duration: int) -> int:
    """把播放起始时间对齐到 HLS 分片边界（同窗口内拖动不重建会话）。"""
    if seconds <= 0:
        return 0
    return int(seconds // segment_duration) * segment_duration


def _is_pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # Windows 上对不存在的 PID 调用 os.kill(pid, 0) 抛的
        # 是 WinError 87（ERROR_INVALID_PARAMETER）而非 ProcessLookupError
        return False
    return True


class TranscodeManager:
    """转码会话编排中枢（进程内单例）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # transcode_session_id -> FfmpegProcess
        self._processes: Dict[int, FfmpegProcess] = {}
        # transcode_session_id -> 命令构建上下文（进程内保留）
        self._command_spec: Dict[int, dict] = {}

    # ===== 会话创建 / 复用 =====

    def get_or_create(
        self,
        db,
        *,
        music_video_id: Optional[int],
        source_path: str,
        profile: TranscodingProfile,
        quality: str,
        protocol: str,
        window_start: int,
        audio_stream_id: Optional[int] = None,
        subtitle_stream_id: Optional[int] = None,
    ) -> Tuple[TranscodeSession, bool]:
        """按 cache_key 查找活跃会话；不存在则创建（不启动 FFmpeg）。

        返回 (session, created)。多个并发请求同时创建时以 DB 唯一索引兜底，
        重复创建的会话立即标记 failed 由失败方自行回查。
        """
        cache_key = build_cache_key(
            source_path,
            profile.profile_key,
            protocol,
            window_start,
            audio_stream_id,
            subtitle_stream_id,
        )
        with self._lock:
            existing = db.scalar(
                select(TranscodeSession).where(
                    TranscodeSession.cache_key == cache_key,
                    TranscodeSession.active_filter(),
                )
            )
            if existing:
                if existing.status in ACTIVE_TS:
                    existing.last_active_at = datetime.utcnow()
                    db.commit()
                    return existing, False
                if existing.status == TS_STOPPED and self._is_vod_complete(
                    existing.output_directory
                ):
                    existing.last_active_at = datetime.utcnow()
                    db.commit()
                    return existing, False
                self._reclaim_session(existing)
                if not self._is_vod_complete(existing.output_directory):
                    self._clean_output_dir(existing.output_directory)
                existing.status = TS_CREATED
                existing.progress = 0.0
                existing.ffmpeg_pid = None
                existing.exit_code = None
                existing.started_at = None
                existing.stopped_at = None
                existing.error_message = None
                existing.stderr_tail = None
                existing.full_command = None
                existing.playlist_path = None
                existing.output_directory = self._output_dir_for(
                    music_video_id, profile.profile_key, window_start
                )
                existing.last_active_at = datetime.utcnow()
                db.commit()
                self._command_spec[existing.id] = {
                    "profile": profile,
                    "quality": quality,
                    "protocol": protocol,
                    "window_start": window_start,
                    "audio_stream_id": audio_stream_id,
                    "subtitle_stream_id": subtitle_stream_id,
                }
                return existing, False

            ts = TranscodeSession(
                cache_key=cache_key,
                profile_key=profile.profile_key,
                music_video_id=music_video_id,
                source_path=source_path,
                output_format=profile.container,
                video_codec=profile.video_codec,
                audio_codec=profile.audio_codec,
                width=profile.max_width,
                height=profile.max_height,
                video_bitrate=profile.max_video_bitrate,
                audio_bitrate=profile.audio_bitrate,
                framerate=profile.framerate,
                hardware_acceleration="cpu",
                status=TS_CREATED,
                progress=0.0,
                output_directory=self._output_dir_for(
                    music_video_id, profile.profile_key, window_start
                ),
                last_active_at=datetime.utcnow(),
            )
            db.add(ts)
            try:
                db.flush()
                db.commit()
            except IntegrityError:
                # 唯一索引冲突兜底：
                # 1) 另一请求已抢建活跃会话 → 复用；
                # 2) 历史已停止/失败会话仍占用 cache_key（deleted_at IS NULL
                #    唯一约束）→ 重新激活复用，避免反复创建冲突记录。
                db.rollback()
                winner = db.scalar(
                    select(TranscodeSession)
                    .where(
                        TranscodeSession.cache_key == cache_key,
                        TranscodeSession.active_filter(),
                    )
                    .order_by(TranscodeSession.id.desc())
                )
                if winner:
                    if winner.status not in ACTIVE_TS:
                        if (
                            winner.status == TS_STOPPED
                            and self._is_vod_complete(winner.output_directory)
                        ):
                            winner.last_active_at = datetime.utcnow()
                            db.commit()
                            return winner, False
                        self._reclaim_session(winner)
                        if not self._is_vod_complete(winner.output_directory):
                            self._clean_output_dir(winner.output_directory)
                        winner.status = TS_CREATED
                        winner.progress = 0.0
                        winner.ffmpeg_pid = None
                        winner.exit_code = None
                        winner.started_at = None
                        winner.stopped_at = None
                        winner.error_message = None
                        winner.stderr_tail = None
                        winner.full_command = None
                        winner.playlist_path = None
                        winner.output_directory = self._output_dir_for(
                            music_video_id, profile.profile_key, window_start
                        )
                    winner.last_active_at = datetime.utcnow()
                    db.commit()
                    self._command_spec[winner.id] = {
                        "profile": profile,
                        "quality": quality,
                        "protocol": protocol,
                        "window_start": window_start,
                        "total_duration": None,
                    }
                    return winner, False
                raise
            self._command_spec[ts.id] = {
                "profile": profile,
                "quality": quality,
                "protocol": protocol,
                "window_start": window_start,
                "total_duration": None,
            }
            return ts, True

    def _reclaim_session(self, ts: TranscodeSession) -> None:
        """回收历史会话占用的进程句柄（若有残留 FFmpeg 则终止）。"""
        proc = self._processes.pop(ts.id, None)
        if proc:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                logger.exception("回收会话 #%s 时终止 ffmpeg 失败", ts.id)

    def _clean_output_dir(self, output_directory: Optional[str]) -> None:
        """清空输出目录里本路转码的产物，避免 `append_list` 旧文件污染新会话。

        P0 施工说明 §5.2：只清 playlist.m3u8 / segment_*.ts / ffmpeg_progress.log
        （ffmpeg_stderr.log 可留可截断），不要 rmtree 整个目录，避免和正在读该
        路径的进程打架；也不要动其它 profile / wXXXXXXX 目录（output_directory
        已按 cache_key + window_start 隔离）。
        """
        if not output_directory:
            return
        out_dir = Path(output_directory)
        if not out_dir.is_dir():
            return
        for p in out_dir.iterdir():
            if p.is_dir():
                continue
            name = p.name
            if name in ("playlist.m3u8", "ffmpeg_progress.log", "init.mp4"):
                try:
                    p.unlink()
                except OSError:
                    logger.exception("清理输出目录 %s 失败", p)
            elif name.startswith("segment_") and (name.endswith(".ts") or name.endswith(".m4s")):
                try:
                    p.unlink()
                except OSError:
                    logger.exception("清理输出目录 %s 失败", p)

    # ===== 启动 =====

    def ensure_started(
        self,
        db,
        ts: TranscodeSession,
        *,
        source_width: Optional[int],
        source_height: Optional[int],
        total_duration: Optional[float],
        source_codec: Optional[str] = None,
    ) -> FfmpegProcess:
        """确保 FFmpeg 已启动（幂等）。首次 manifest 请求时调用。"""
        with self._lock:
            proc = self._processes.get(ts.id)
            if proc and proc.running:
                return proc

            # 已失败的会话不重启，直接报错（避免反复重启秒退的 ffmpeg）
            if ts.status == TS_FAILED:
                raise FfmpegEngineError(
                    f"转码会话 #{ts.id} 已失败: {ts.error_message or 'ffmpeg 异常退出'}"
                )

            # VOD 已完成且输出完整：直接复用缓存输出，不再重启 FFmpeg
            if ts.status == TS_STOPPED and self._is_vod_complete(ts.output_directory):
                return None

            spec = self._command_spec.get(ts.id)
            if not spec:
                raise FfmpegEngineError(
                    f"转码会话 #{ts.id} 缺少命令上下文（可能已被回收）"
                )
            profile: TranscodingProfile = spec["profile"]
            spec["total_duration"] = total_duration

            out_dir = Path(ts.output_directory or "")
            out_dir.mkdir(parents=True, exist_ok=True)
            # P0 施工说明 §5.2：同一 cache_key 二次启动前清空本路旧产物，
            # 防止 append_list（旧清单）残留导致播放器看到两份分段/花屏。
            self._clean_output_dir(ts.output_directory)
            spec_obj = HlsOutputSpec(
                output_dir=out_dir,
                playlist_name="playlist.m3u8",
                segment_duration=settings.hls_segment_duration,
                segment_type="fmp4" if profile.container == "hls_fmp4" else "mpegts",
            )
            progress_file = out_dir / "ffmpeg_progress.log"
            stderr_log = out_dir / "ffmpeg_stderr.log"
            start_offset = float(spec["window_start"])

            if profile.video_codec == "copy":
                # ===== Direct Stream：remux，视频不重编码 =====
                audio_out = (
                    profile.audio_codec
                    if profile.audio_codec and profile.audio_codec != "copy"
                    else "copy"
                )
                cmd = ffmpeg_engine.build_hls_remux_command(
                    ts.source_path,
                    spec_obj,
                    audio_codec=audio_out,
                    start_offset=start_offset,
                    progress_file=progress_file,
                )
                ts.hardware_acceleration = "none"
            else:
                # ===== Transcode：硬件选择 → 构建转码命令 =====
                # 并发上限约束所有重编码（含 CPU 软转）。NAS 上 4K libx264
                # 一路就能占 1～2GB，不限路数会把内存打满。
                self._enforce_concurrency_limit(db, exclude_ts_id=ts.id)
                # 仅在源分辨率超过目标档位时才缩放；源本身已是目标分辨率
                # （如 4K→4K）时不加 scale，避免无意义的二次缩放与质量损失。
                scale = None
                target_height = profile.max_height
                if profile.max_width and source_width and source_height:
                    if (
                        source_width > profile.max_width
                        or source_height > profile.max_height
                    ):
                        scale = f"{profile.max_width}:{profile.max_height}"
                    else:
                        target_height = None
                hw: HwSelection = hw_manager.select(
                    preference=profile.hardware_acceleration,
                    video_codec=profile.video_codec,
                    preset=profile.preset,
                    quality=profile.quality,
                    scale=scale,
                    target_height=target_height,
                    source_codec=source_codec,
                )
                cmd = ffmpeg_engine.build_hls_transcode_command(
                    ts.source_path,
                    spec_obj,
                    hw,
                    height=profile.max_height,
                    framerate=profile.framerate,
                    video_bitrate=profile.max_video_bitrate or profile.video_bitrate,
                    audio_codec=profile.audio_codec,
                    audio_bitrate=profile.audio_bitrate,
                    start_offset=start_offset,
                    progress_file=progress_file,
                )
                ts.hardware_acceleration = hw.method

            ts.status = TS_STARTING
            ts.full_command = " ".join(cmd)
            ts.started_at = datetime.utcnow()
            db.commit()

            try:
                proc = ffmpeg_engine.start(
                    cmd,
                    progress_file=progress_file,
                    stderr_log=stderr_log,
                    total_duration=total_duration,
                    on_exit=self._make_on_exit(ts.id),
                    cwd=out_dir,
                )
            except FfmpegEngineError as e:
                ts.status = TS_FAILED
                ts.error_message = str(e)
                ts.stopped_at = datetime.utcnow()
                db.commit()
                raise

            self._processes[ts.id] = proc
            ts.status = TS_RUNNING
            ts.ffmpeg_pid = proc.pid
            db.commit()
            return proc

    def _enforce_concurrency_limit(self, db, exclude_ts_id: int) -> None:
        """并发上限：统计当前仍在运行的重编码会话（含 CPU），达到上限则抛错。

        remux（video_codec=copy）不计入。正在启动、尚未拿到 pid 的会话也占名额，
        避免并发请求同时越过上限。
        """
        limit = settings.transcode_max_concurrent
        if limit <= 0:
            return
        # 只计仍在编码的会话。TS_STOPPING 是正在杀掉的旧窗口，不占名额，
        # 否则 seek 替换时旧进程收尾会把新窗口顶成 429。
        active = (
            db.query(TranscodeSession)
            .filter(
                TranscodeSession.status.in_((TS_STARTING, TS_RUNNING)),
                or_(
                    TranscodeSession.video_codec.is_(None),
                    TranscodeSession.video_codec != "copy",
                ),
            )
            .all()
        )
        running = [
            a
            for a in active
            if a.id != exclude_ts_id
            and (not a.ffmpeg_pid or _is_pid_alive(a.ffmpeg_pid))
        ]
        if len(running) >= limit:
            names = ", ".join(a.id.__str__() for a in running)
            raise FfmpegEngineError(
                f"已达转码并发上限（{limit} 路），当前转码: {names}",
                status_code=503,
            )

    def _is_vod_complete(self, output_directory: Optional[str]) -> bool:
        """判断 VOD playlist 是否已完整输出（ENDLIST 且所有分片存在）。"""
        if not output_directory:
            return False
        out_dir = Path(output_directory)
        playlist = out_dir / "playlist.m3u8"
        if not playlist.exists():
            return False
        try:
            raw = playlist.read_text(encoding="utf-8")
        except OSError:
            return False
        if "#EXT-X-ENDLIST" not in raw:
            return False
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "." in stripped and not (out_dir / stripped).is_file():
                return False
        return True

    def _make_on_exit(self, ts_id: int):
        def on_exit(proc: FfmpegProcess) -> None:
            with SessionLocal() as db:
                ts = db.get(TranscodeSession, ts_id)
                if ts is None:
                    return
                ts.exit_code = proc.exit_code
                ts.stderr_tail = " | ".join(proc.stderr_tail(10))[:STDERR_STORE_LIMIT]
                ts.stopped_at = datetime.utcnow()
                if ts.status == TS_STOPPING:
                    ts.status = TS_STOPPED
                elif proc.exit_code == 0:
                    ts.status = TS_STOPPED
                else:
                    ts.status = TS_FAILED
                    ts.error_message = (
                        f"ffmpeg 异常退出 (code={proc.exit_code}): {ts.stderr_tail}"
                    )
                db.commit()
            with self._lock:
                self._processes.pop(ts_id, None)

        return on_exit

    # ===== 停止 =====

    def stop_session(
        self, db, ts_id: int, *, mark_stopped: bool = True, force: bool = False
    ) -> None:
        """停止 FFmpeg 进程并落库（引用计数归零后调用）。

        force=True：seek / 换画质替换旧窗口时直接 SIGKILL，避免 8 秒优雅退出
        期间旧 pid 仍占着并发名额。播放已经不要这条路了，不必刷尾部分片。
        """
        with self._lock:
            ts = db.get(TranscodeSession, ts_id)
            proc = self._processes.get(ts_id)
            if ts is None:
                return
            if proc and proc.running and ts.status not in (TS_STOPPING, TS_STOPPED):
                ts.status = TS_STOPPING
                db.commit()
                try:
                    if force:
                        proc.kill()
                    else:
                        proc.stop()
                except Exception:  # noqa: BLE001
                    logger.exception("停止 ffmpeg pid=%s 失败", proc.pid)
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
                ts = db.get(TranscodeSession, ts_id)
                if ts:
                    ts.exit_code = proc.exit_code
                    if ts.status == TS_STOPPING:
                        ts.status = TS_STOPPED
                    ts.stopped_at = datetime.utcnow()
                    db.commit()
            elif mark_stopped and ts.status not in (TS_STOPPED, TS_FAILED):
                ts.status = TS_STOPPED
                ts.stopped_at = datetime.utcnow()
                db.commit()

    def kill_session(self, ts_id: int) -> None:
        proc = self._processes.get(ts_id)
        if proc:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                logger.exception("kill ffmpeg pid=%s 失败", proc.pid)

    # ===== 查询 =====

    def get_process(self, ts_id: int) -> Optional[FfmpegProcess]:
        return self._processes.get(ts_id)

    def get_total_duration(self, ts_id: int) -> Optional[float]:
        """返回会话的**源片**总时长（秒）。

        该值由 ensure_started 在启动时从 source MediaInfo 写入 _command_spec；
        未启动/上下文丢失时返回 None。
        """
        spec = self._command_spec.get(ts_id)
        if not spec:
            return None
        return spec.get("total_duration")

    def get_window_start(self, ts_id: int) -> float:
        """返回本路的 window_start（秒，非负）。

        window_start 用于计算本路输出时长 output_duration =
        max(0, source_duration - window_start)。未启动/上下文丢失时按 0 处理。
        """
        spec = self._command_spec.get(ts_id)
        if not spec:
            return 0.0
        return float(spec.get("window_start") or 0)

    def refresh_progress(self, db, ts_id: int) -> None:
        """把 FFmpeg 实时进度刷入 DB（供状态接口观测）。"""
        proc = self._processes.get(ts_id)
        ts = db.get(TranscodeSession, ts_id)
        if not proc or not ts or ts.status not in ACTIVE_TS:
            return
        prog = proc.progress()
        if prog:
            ts.progress = float(prog.get("percent") or 0.0)
            db.commit()

    # ===== 引用计数（由 PlaybackManager 调用）=====

    def reference_count(self, db, ts_id: int) -> int:
        """当前活跃的 PlaybackSession 引用数。"""
        return db.scalar(
            select(func.count())
            .select_from(PlaybackSession)
            .where(
                PlaybackSession.transcode_session_id == ts_id,
                PlaybackSession.status.in_(ACTIVE_PS),
                PlaybackSession.active_filter(),
            )
        ) or 0

    # ===== 启动恢复 =====

    def recover_stale(self, db) -> dict:
        """启动时恢复僵尸会话：进程已死的 running → failed，遗留播放 → stopped。

        Case 9（Docker 重启）目标：重启后不允许残留大量假的 processing 任务。
        """
        stats = {"transcode_failed": 0, "playback_stopped": 0}

        for ts in db.scalars(
            select(TranscodeSession).where(
                TranscodeSession.status.in_(ACTIVE_TS),
                TranscodeSession.active_filter(),
            )
        ):
            if ts.ffmpeg_pid and _is_pid_alive(ts.ffmpeg_pid):
                # 进程句柄已随服务重启丢失：无法优雅 stop，直接按 PID 终止
                try:
                    os.kill(ts.ffmpeg_pid, 9)
                except OSError:
                    pass
                ts.status = TS_FAILED
                ts.exit_code = ts.exit_code or -1
                ts.error_message = (
                    f"服务重启后终止了遗留的 FFmpeg 进程：pid={ts.ffmpeg_pid}"
                )
            elif ts.status in (TS_STARTING, TS_RUNNING) and ts.ffmpeg_pid:
                ts.status = TS_FAILED
                ts.exit_code = ts.exit_code or -1
                ts.error_message = (
                    f"服务重启/崩溃后遗留的转码会话：pid={ts.ffmpeg_pid} 已不存在"
                )
            else:
                ts.status = TS_FAILED
                ts.error_message = "服务重启/崩溃后遗留的转码会话（未完成启动）"
            ts.stopped_at = datetime.utcnow()
            stats["transcode_failed"] += 1
        db.commit()

        cutoff = datetime.utcnow() - timedelta(days=1)
        for ps in db.scalars(
            select(PlaybackSession).where(
                PlaybackSession.status.in_(ACTIVE_PS),
                PlaybackSession.active_filter(),
            )
        ):
            ps.status = PS_STOPPED
            ps.stopped_at = datetime.utcnow()
            if ps.last_active_at and ps.last_active_at < cutoff:
                pass  # 超期会话同样置为 stopped（已统一处理）
            stats["playback_stopped"] += 1
        db.commit()
        return stats

    # ===== 缓存清理 =====

    def cleanup_expired(self, db) -> dict:
        """闲置回收：停止无引用的 FFmpeg + 关闭超期播放会话 + 删除过期缓存目录。"""
        stats = {"transcode_stopped": 0, "playback_closed": 0, "dirs_removed": 0}
        now = datetime.utcnow()

        # 1. 停止：无活跃引用 且 超过 idle 阈值的转码会话
        ts_cutoff = now - timedelta(seconds=settings.transcode_session_timeout)
        for ts in db.scalars(
            select(TranscodeSession).where(
                TranscodeSession.status.in_(ACTIVE_TS),
                TranscodeSession.active_filter(),
            )
        ):
            if self.reference_count(db, ts.id) > 0:
                continue
            if ts.last_active_at and ts.last_active_at < ts_cutoff:
                self.stop_session(db, ts.id)
                stats["transcode_stopped"] += 1
        db.commit()

        # 2. 关闭：心跳超期的播放会话
        ps_cutoff = now - timedelta(seconds=settings.playback_session_timeout)
        for ps in db.scalars(
            select(PlaybackSession).where(
                PlaybackSession.status.in_(ACTIVE_PS),
                PlaybackSession.active_filter(),
                PlaybackSession.last_active_at < ps_cutoff,
            )
        ):
            ps.status = PS_STOPPED
            ps.stopped_at = now
            stats["playback_closed"] += 1
        db.commit()

        # 3. 目录：仅删除「不再被任何未删除会话引用」且目录陈旧 或 会话已终止 的缓存
        stats["dirs_removed"] = self._cleanup_cache_dirs(db)
        return stats

    @staticmethod
    def _dir_key(p: str | Path) -> str:
        try:
            return str(Path(p).resolve())
        except OSError:
            return str(p)

    def _referenced_output_dirs(self, db) -> set:
        """仅「仍在转码」的会话会钉住缓存目录；已停止的不得阻止清理。"""
        keys: set = set()
        for ts in db.scalars(
            select(TranscodeSession).where(
                TranscodeSession.status.in_(ACTIVE_TS),
                TranscodeSession.active_filter(),
            )
        ):
            if ts.output_directory:
                keys.add(self._dir_key(ts.output_directory))
        return keys

    def _cleanup_cache_dirs(self, db) -> int:
        """删除过期缓存目录（不触碰任何 DB 会话的 output_directory）。

        仅操作 derived/transcodes（settings.playback_transcode_dir），与
        library/incoming 天然隔离，不会误删原始媒体。删除前确认：
        - 目录不是任何 TranscodeSession 的 output_directory（含状态为
          running/starting 的会话，即正在写入的 ffmpeg 工作目录都被引用，
          故不会删到仍在写入的目录）；
        - 目录 mtime 超过「引用归零后的延迟删除阈值」。
        """
        root = settings.playback_transcode_dir
        if not root.exists():
            return 0
        referenced = self._referenced_output_dirs(db)
        removed = 0
        stale = timedelta(seconds=settings.transcode_cleanup_delay_seconds)
        now = datetime.utcnow()
        for mv_dir in root.iterdir():
            if not mv_dir.is_dir():
                continue
            for prof_dir in mv_dir.iterdir():
                if not prof_dir.is_dir():
                    continue
                for win_dir in prof_dir.iterdir():
                    if not win_dir.is_dir():
                        continue
                    rel = self._dir_key(win_dir)
                    if rel in referenced:
                        continue
                    mtime = datetime.utcfromtimestamp(win_dir.stat().st_mtime)
                    if now - mtime > stale:
                        shutil.rmtree(win_dir, ignore_errors=True)
                        logger.info("转码缓存延迟清理：已删除 %s（引用归零超过 %ss）", rel, stale.total_seconds())
                        removed += 1
        return removed

    def sweep_max_age(self, db) -> int:
        """定时清理：删除 mtime 超过 24h 且无活跃引用的转码缓存目录。

        仅操作 derived/transcodes；被任一 TranscodeSession 引用（含正在
        running 的 ffmpeg 工作目录）的目录一律跳过。
        """
        root = settings.playback_transcode_dir
        if not root.exists():
            return 0
        referenced = self._referenced_output_dirs(db)
        max_age = timedelta(seconds=settings.transcode_cleanup_max_age_seconds)
        now = datetime.utcnow()
        removed = 0
        for mv_dir in root.iterdir():
            if not mv_dir.is_dir():
                continue
            for prof_dir in mv_dir.iterdir():
                if not prof_dir.is_dir():
                    continue
                for win_dir in prof_dir.iterdir():
                    if not win_dir.is_dir():
                        continue
                    rel = self._dir_key(win_dir)
                    if rel in referenced:
                        continue
                    mtime = datetime.utcfromtimestamp(win_dir.stat().st_mtime)
                    if now - mtime > max_age:
                        shutil.rmtree(win_dir, ignore_errors=True)
                        logger.info("转码缓存定时清理：已删除 %s（mtime>%ss 且无活跃引用）", rel, max_age.total_seconds())
                        removed += 1
        return removed

    def purge_cache_for(self, db, music_video_id: int) -> int:
        """手动清理指定视频的全部转码缓存目录（保留活跃会话）。"""
        root = settings.playback_transcode_dir / str(music_video_id)
        if not root.exists():
            return 0
        referenced = {
            self._dir_key(ts.output_directory)
            for ts in db.scalars(
                select(TranscodeSession).where(
                    TranscodeSession.music_video_id == music_video_id,
                    TranscodeSession.status.in_(ACTIVE_TS),
                    TranscodeSession.active_filter(),
                )
            )
            if ts.output_directory
        }
        removed = 0
        for prof_dir in root.iterdir():
            if not prof_dir.is_dir():
                continue
            for win_dir in prof_dir.iterdir():
                rel = self._dir_key(win_dir)
                if rel in referenced:
                    continue
                shutil.rmtree(win_dir, ignore_errors=True)
                removed += 1
        return removed

    def get_cache_stats(self) -> dict:
        """统计播放转码缓存根目录：目录数、文件数、总大小。"""
        root = settings.playback_transcode_dir
        dirs = 0
        files = 0
        total = 0
        if root.exists():
            for p in root.rglob("*"):
                if p.is_dir():
                    dirs += 1
                elif p.is_file():
                    files += 1
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
        return {
            "root": str(root),
            "dirs": dirs,
            "files": files,
            "total_bytes": total,
        }

    def purge_all_cache(self, db) -> dict:
        """一键清空全部播放转码缓存（保留活跃会话引用的目录）。

        返回：{"dirs_removed": N, "bytes_freed": B}
        """
        stats = {"dirs_removed": 0, "bytes_freed": 0}
        root = settings.playback_transcode_dir
        if not root.exists():
            return stats
        referenced = self._referenced_output_dirs(db)
        for mv_dir in root.iterdir():
            if not mv_dir.is_dir():
                continue
            for prof_dir in mv_dir.iterdir():
                if not prof_dir.is_dir():
                    continue
                for win_dir in prof_dir.iterdir():
                    if not win_dir.is_dir():
                        continue
                    rel = self._dir_key(win_dir)
                    if rel in referenced:
                        continue
                    size = 0
                    for p in win_dir.rglob("*"):
                        if p.is_file():
                            try:
                                size += p.stat().st_size
                            except OSError:
                                pass
                    shutil.rmtree(win_dir, ignore_errors=True)
                    stats["dirs_removed"] += 1
                    stats["bytes_freed"] += size
        return stats

    # ===== 目录布局 =====

    def _output_dir_for(
        self, music_video_id: Optional[int], profile_key: str, window_start: int
    ) -> str:
        base = settings.playback_transcode_dir / str(music_video_id or 0)
        return str(base / profile_key / f"w{window_start:08d}")


transcode_manager = TranscodeManager()
