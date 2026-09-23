"""临时验证脚本：PlaybackManager + TranscodeManager 的会话复用/启动/引用计数/恢复/清理。

覆盖（对齐用户需求中的 Case）：
- Case 5：同规格并发请求复用同一 TranscodeSession（1 个 FFmpeg，多 PlaybackSession）
- Case 7：最后播放者停止 → TranscodeSession stopped + FFmpeg 终止
- Case 8：FFmpeg 启动失败 → TS_FAILED + error_message
- Case 9：重启恢复 —— recover_stale 不遗留僵尸 processing
- Case 1/2/3：create_session 决策链路（Direct Play / Direct Stream / Transcode）
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# 必须在 import 任何 app 模块前设置环境（settings 单例一旦实例化即固定）
_tmp = Path(tempfile.mkdtemp(prefix="p5_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

import app.media.ffmpeg_engine as fe_mod
import app.services.transcode_manager as tm_mod

fe_mod.require_ffmpeg = lambda: "/usr/bin/ffmpeg"  # mock：测试环境无 ffmpeg

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.media.ffmpeg_engine import FfmpegEngineError
from app.media.hw_manager import HwSelection, hw_manager
from app.media.media_info import MediaInfo, MediaStream
from app.media.transcoding_profile import TranscodingProfile
from app.media.client_capabilities import ClientCapabilities
from app.models.music_video import MusicVideo
from app.models.playback import (
    PS_FAILED,
    PS_PLAYING,
    PS_STARTING,
    PS_STOPPED,
    TS_CREATED,
    TS_FAILED,
    TS_RUNNING,
    TS_STARTING,
    TS_STOPPED,
    PlaybackSession,
    TranscodeSession,
)
from app.services.playback_manager import (
    PlaybackManager,
    PlaybackError,
    _segment_ready,
    rewrite_hls_uris,
)
from app.services.transcode_manager import (
    TranscodeManager,
    build_cache_key,
    transcode_manager,
    window_start_for,
)
from sqlalchemy import select

Base.metadata.create_all(bind=engine)

PASS = 0


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}")
    else:
        print(f"  FAIL  {name}  {detail}")


# ===== 测试替身 =====

class FakeProc:
    def __init__(self, pid=4242):
        self.pid = pid
        self._running = True
        self.exit_code = None
        self._stderr = ["line-1", "line-2", "line-3", "line-4", "line-5"]

    @property
    def running(self):
        return self._running

    def progress(self):
        return {"percent": 42.0, "speed": "1.2x"}

    def stderr_tail(self, lines=50):
        return self._stderr[:lines]

    def stop(self):
        self._running = False
        self.exit_code = 0
        return 0

    def kill(self):
        self._running = False
        self.exit_code = 137
        return 0


_start_calls = []


def fake_start(cmd, *, progress_file, stderr_log=None, total_duration=None, on_exit=None, cwd=None):
    _start_calls.append(
        {"cmd": cmd, "total_duration": total_duration, "on_exit": on_exit}
    )
    return FakeProc()


def cpu_hw():
    return HwSelection(
        method="cpu", video_codec="h264", encoder="libx264",
        encode_args=["-preset", "veryfast", "-crf", "23"],
        pixel_format="yuv420p", video_filter="scale=1920:1080",
    )


def mk_media_info(
    *,
    container="mp4", duration=180.0, bit_rate=4_500_000,
    vcodec="h264", width=1920, height=1080, pixfmt="yuv420p",
    acodec="aac", achan=2,
):
    vs = MediaStream(
        index=0, codec_type="video", codec_name=vcodec,
        width=width, height=height, pixel_format=pixfmt,
        frame_rate=30.0, bit_rate=bit_rate, is_default=True,
    )
    aas = MediaStream(
        index=1, codec_type="audio", codec_name=acodec,
        channels=achan, language="kor", is_default=True, bit_rate=192_000,
    )
    return MediaInfo(
        path="/fake/source.mp4", container=container, duration=duration,
        bit_rate=bit_rate, video_streams=[vs], audio_streams=[aas],
    )


def main():
    global PASS

    # 外键约束（PRAGMA foreign_keys=ON）要求 music_video_id 指向真实行：
    # Part B~F 直接以固定 id（1、100-107）创建会话，先落占位 MusicVideo，
    # 测试结束时清理（外键为 SET NULL，遗留引用会被置空而非阻塞删除）
    _FIXTURE_IDS = [1, *range(100, 108)]
    with SessionLocal() as db:
        db.add_all(MusicVideo(id=i, name=f"fk-fixture-{i}") for i in _FIXTURE_IDS)
        db.commit()

    # ========== Part A：纯函数 ==========
    print("== A. window_start_for / build_cache_key ==")
    check("w0=0", window_start_for(0, 6) == 0)
    check("w5.9=0", window_start_for(5.9, 6) == 0)
    check("w6.0=6", window_start_for(6.0, 6) == 6)
    check("w120=120", window_start_for(120, 6) == 120)
    check("w1799=1794", window_start_for(1799, 6) == 1794)

    k1 = build_cache_key("/a.mp4", "pkey", "hls", 0, None, None)
    k2 = build_cache_key("/a.mp4", "pkey", "hls", 0, None, None)
    k3 = build_cache_key("/b.mp4", "pkey", "hls", 0, None, None)
    k4 = build_cache_key("/a.mp4", "pkey", "hls", 6, None, None)
    check("key 确定", k1 == k2, k1)
    check("源不同 → key 不同", k1 != k3)
    check("窗口不同 → key 不同", k1 != k4)
    check("key 40 位", len(k1) == 40)

    # ========== Part B：get_or_create 复用 ==========
    print("== B. 会话复用（Case 5）==")
    profile = TranscodingProfile(
        name="p", container="hls_fmp4", video_codec="h264",
        audio_codec="aac", max_width=1920, max_height=1080,
        video_bitrate=4_500_000, max_video_bitrate=4_500_000,
        audio_bitrate=192_000, framerate=30.0, preset="veryfast",
        quality=23, hardware_acceleration="auto",
    )
    with SessionLocal() as db:
        ts1, created1 = transcode_manager.get_or_create(
            db, music_video_id=100, source_path="/a.mp4",
            profile=profile, quality="1080p", protocol="hls", window_start=0,
        )
        check("首次创建", created1 is True)
        ts2, created2 = transcode_manager.get_or_create(
            db, music_video_id=100, source_path="/a.mp4",
            profile=profile, quality="1080p", protocol="hls", window_start=0,
        )
        check("同 key 复用同一会话", ts2.id == ts1.id, f"ts1={ts1.id} ts2={ts2.id}")
        check("复用不重建", created2 is False)

        ts3, _ = transcode_manager.get_or_create(
            db, music_video_id=100, source_path="/a.mp4",
            profile=profile, quality="1080p", protocol="hls", window_start=6,
        )
        check("不同窗口 → 不同会话", ts3.id != ts1.id)

        db.delete(ts1)
        db.delete(ts2)
        db.delete(ts3)
        db.commit()

    # ========== Part C：ensure_started ==========
    print("== C. 启动（remux vs transcode / 幂等 / 失败）==")
    old_select = hw_manager.select
    old_start = fe_mod.ffmpeg_engine.start
    hw_manager.select = lambda **kw: cpu_hw()
    fe_mod.ffmpeg_engine.start = fake_start

    try:
        with SessionLocal() as db:
            # C1: Direct Stream remux（video_codec=copy）
            remux_profile = TranscodingProfile(
                name="remux", container="hls_fmp4", video_codec="copy",
                audio_codec="aac", max_width=0, max_height=0,
                video_bitrate=0, max_video_bitrate=0,
                audio_bitrate=192_000, framerate=0.0, preset="veryfast",
                quality=23, hardware_acceleration="cpu",
            )
            ts, _ = transcode_manager.get_or_create(
                db, music_video_id=101, source_path="/b.mp4",
                profile=remux_profile, quality="original",
                protocol="hls", window_start=0,
            )
            _start_calls.clear()
            transcode_manager.ensure_started(
                db, ts, source_width=1920, source_height=1080, total_duration=180.0
            )
            db.refresh(ts)
            cmd = _start_calls[-1]["cmd"]
            check("remux: -c:v copy", "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy", " ".join(cmd[:40]))
            check("remux: 无 -crf", "-crf" not in cmd)
            check("remux: hw=none", ts.hardware_acceleration == "none", ts.hardware_acceleration)
            check("remux: status running", ts.status == TS_RUNNING, ts.status)
            check("remux: pid 记录", ts.ffmpeg_pid == 4242, ts.ffmpeg_pid)
            check("remux: full_command", bool(ts.full_command) and "playlist.m3u8" in ts.full_command)

            # C2: Transcode（CPU mock）
            ts2, _ = transcode_manager.get_or_create(
                db, music_video_id=102, source_path="/c.mp4",
                profile=profile, quality="1080p", protocol="hls", window_start=0,
            )
            _start_calls.clear()
            p1 = transcode_manager.ensure_started(
                db, ts2, source_width=3840, source_height=2160, total_duration=300.0
            )
            db.refresh(ts2)
            cmd2 = _start_calls[-1]["cmd"]
            check("transcode: -c:v libx264", "-c:v" in cmd2 and cmd2[cmd2.index("-c:v") + 1] == "libx264")
            check("transcode: -crf 23", "-crf" in cmd2 and cmd2[cmd2.index("-crf") + 1] == "23")
            check("transcode: hw=cpu", ts2.hardware_acceleration == "cpu", ts2.hardware_acceleration)
            check("transcode: -ss 不存在(w=0)", "-ss" not in cmd2)

            # C3: 幂等 —— 不重复启动
            _start_calls.clear()
            p2 = transcode_manager.ensure_started(
                db, ts2, source_width=3840, source_height=2160, total_duration=300.0
            )
            check("幂等复用进程", p2 is p1)
            check("幂等不重复启动", len(_start_calls) == 0, f"calls={len(_start_calls)}")

            # C4: seek 窗口 >0 → 命令带 -ss
            ts3, _ = transcode_manager.get_or_create(
                db, music_video_id=103, source_path="/d.mp4",
                profile=profile, quality="1080p", protocol="hls", window_start=1800,
            )
            _start_calls.clear()
            transcode_manager.ensure_started(
                db, ts3, source_width=1920, source_height=1080, total_duration=3600.0
            )
            cmd3 = _start_calls[-1]["cmd"]
            check("seek 窗口: -ss 1800", "-ss" in cmd3 and cmd3[cmd3.index("-ss") + 1] == "1800.000", " ".join(cmd3[:20]))
            check("seek 窗口: total_duration 传入", _start_calls[-1]["total_duration"] == 3600.0)

            # C5: 启动失败 → TS_FAILED
            def failing_start(*a, **kw):
                raise FfmpegEngineError("ffmpeg not found")

            fe_mod.ffmpeg_engine.start = failing_start
            ts4, _ = transcode_manager.get_or_create(
                db, music_video_id=104, source_path="/e.mp4",
                profile=profile, quality="1080p", protocol="hls", window_start=0,
            )
            try:
                transcode_manager.ensure_started(
                    db, ts4, source_width=1920, source_height=1080, total_duration=100.0
                )
                check("启动失败抛出", False)
            except FfmpegEngineError:
                check("启动失败抛出", True)
            db.refresh(ts4)
            check("失败状态 TS_FAILED", ts4.status == TS_FAILED, ts4.status)
            check("失败 error_message", bool(ts4.error_message), ts4.error_message)

            for t in (ts, ts2, ts3, ts4):
                db.delete(t)
            db.commit()
    finally:
        hw_manager.select = old_select
        fe_mod.ffmpeg_engine.start = old_start

    # ========== Part D：引用计数 + 停止（Case 7）==========
    print("== D. 引用计数 / 停止 ==")
    fe_mod.ffmpeg_engine.start = fake_start
    with SessionLocal() as db:
        ts, _ = transcode_manager.get_or_create(
            db, music_video_id=105, source_path="/f.mp4",
            profile=profile, quality="1080p", protocol="hls", window_start=0,
        )
        _start_calls.clear()
        transcode_manager.ensure_started(
            db, ts, source_width=1920, source_height=1080, total_duration=200.0
        )
        db.refresh(ts)

        ps1 = PlaybackSession(
            music_video_id=105, source_path="/f.mp4", play_mode="transcode",
            transcode_required=True, quality="1080p", status=PS_PLAYING,
            transcode_session_id=ts.id,
            last_active_at=datetime.utcnow(),
        )
        ps2 = PlaybackSession(
            music_video_id=105, source_path="/f.mp4", play_mode="transcode",
            transcode_required=True, quality="1080p", status=PS_PLAYING,
            transcode_session_id=ts.id,
            last_active_at=datetime.utcnow(),
        )
        db.add_all([ps1, ps2])
        db.commit()
        db.refresh(ts)

        check("引用数=2", transcode_manager.reference_count(db, ts.id) == 2)

        # 第一个播放者停止 → FFmpeg 仍在
        pb = PlaybackManager()
        pb.stop_session(db, ps1.id)
        db.refresh(ts)
        proc = transcode_manager.get_process(ts.id)
        check("停 1 个后引用=1", transcode_manager.reference_count(db, ts.id) == 1)
        check("FFmpeg 仍在", proc is not None and proc.running)

        # 最后一个播放者停止 → FFmpeg 终止
        pb.stop_session(db, ps2.id)
        db.refresh(ts)
        check("引用=0", transcode_manager.reference_count(db, ts.id) == 0)
        check("ts 状态 stopped", ts.status == TS_STOPPED, ts.status)
        check("ts stopped_at", ts.stopped_at is not None)
        check("ffmpeg 已停止", ts.ffmpeg_pid is not None and not (transcode_manager.get_process(ts.id) and transcode_manager.get_process(ts.id).running))

        # on_exit 回调（模拟 monitor 线程退出）不抛异常、保持 stopped
        callback = _start_calls[-1]["on_exit"]
        if callback:
            done = FakeProc()
            done.stop()  # exit_code=0
            callback(done)
        db.refresh(ts)
        check("on_exit 后仍 stopped", ts.status == TS_STOPPED)

        db.delete(ps1)
        db.delete(ps2)
        db.delete(ts)
        db.commit()

    # ========== Part E：recover_stale（Case 9）==========
    print("== E. 僵尸恢复 ==")
    with SessionLocal() as db:
        dead = TranscodeSession(
            cache_key="dead-key", profile_key="pk", source_path="/dead.mp4",
            output_format="hls_fmp4", video_codec="h264", audio_codec="aac",
            hardware_acceleration="cpu", status=TS_RUNNING,
            ffmpeg_pid=99999999,  # 不存在的 pid
            last_active_at=datetime.utcnow(),
        )
        orphan = TranscodeSession(
            cache_key="orphan-key", profile_key="pk", source_path="/orphan.mp4",
            output_format="hls_fmp4", video_codec="h264", audio_codec="aac",
            hardware_acceleration="cpu", status=TS_RUNNING,
            ffmpeg_pid=424242,  # 存活（模拟）但进程句柄已随重启丢失
            last_active_at=datetime.utcnow(),
        )
        stale_ps = PlaybackSession(
            music_video_id=1, source_path="/x.mp4", play_mode="transcode",
            transcode_required=True, status=PS_STARTING,
            last_active_at=datetime.utcnow(),
        )
        db.add_all([dead, orphan, stale_ps])
        db.commit()

        orig_alive = tm_mod._is_pid_alive
        tm_mod._is_pid_alive = lambda pid: pid == 424242
        try:
            stats = transcode_manager.recover_stale(db)
        finally:
            tm_mod._is_pid_alive = orig_alive
        db.refresh(dead)
        db.refresh(orphan)
        db.refresh(stale_ps)
        check("僵尸 ts → failed", dead.status == TS_FAILED, dead.status)
        check("僵尸 ts 有 error", "遗留" in (dead.error_message or ""), dead.error_message)
        check("孤儿 ts（活 pid）→ failed", orphan.status == TS_FAILED, orphan.status)
        check("孤儿 ts 有 error", "终止" in (orphan.error_message or ""), orphan.error_message)
        check("遗留 playback → stopped", stale_ps.status == PS_STOPPED)
        check("统计 transcode_failed=2", stats["transcode_failed"] == 2)

        db.delete(dead)
        db.delete(orphan)
        db.delete(stale_ps)
        db.commit()

    # ========== Part F：cleanup_expired ==========
    print("== F. 闲置回收 / 缓存清理 ==")
    fe_mod.ffmpeg_engine.start = fake_start
    with SessionLocal() as db:
        old_ts_timeout = settings.transcode_session_timeout
        old_ps_timeout = settings.playback_session_timeout
        settings.transcode_session_timeout = 10
        settings.playback_session_timeout = 10
        try:
            idle_ts, _ = transcode_manager.get_or_create(
                db, music_video_id=106, source_path="/g.mp4",
                profile=profile, quality="1080p", protocol="hls", window_start=0,
            )
            _start_calls.clear()
            transcode_manager.ensure_started(
                db, idle_ts, source_width=1920, source_height=1080, total_duration=120.0
            )
            db.refresh(idle_ts)
            idle_ts.last_active_at = datetime.utcnow() - timedelta(seconds=3600)
            idle_ts.ffmpeg_pid = 99999998  # 进程已死（模拟）

            old_ps = PlaybackSession(
                music_video_id=107, source_path="/h.mp4", play_mode="transcode",
                transcode_required=True, status=PS_PLAYING,
                last_active_at=datetime.utcnow() - timedelta(seconds=7200),
            )
            db.add(old_ps)
            db.commit()

            # 孤立缓存目录（不被任何会话引用 + 陈旧）
            orphan = settings.playback_transcode_dir / "999" / "pk" / "w00000000"
            orphan.mkdir(parents=True, exist_ok=True)
            (orphan / "dummy.txt").write_text("x")
            old = time.time() - 7200
            os.utime(orphan, (old, old))
            os.utime(orphan.parent, (old, old))

            stats = transcode_manager.cleanup_expired(db)
            db.refresh(idle_ts)
            db.refresh(old_ps)
            check("闲置 ts 停止", idle_ts.status == TS_STOPPED, idle_ts.status)
            check("心跳过期 ps 关闭", old_ps.status == PS_STOPPED, old_ps.status)
            check("孤立缓存目录删除", not orphan.exists())
            check("目录清理计数=1", stats["dirs_removed"] == 1, stats["dirs_removed"])

            pinned = settings.playback_transcode_dir / "888" / "pk" / "w00000000"
            pinned.mkdir(parents=True, exist_ok=True)
            (pinned / "dummy.txt").write_text("y")
            os.utime(pinned, (old, old))
            dead_ts = TranscodeSession(
                cache_key="stopped-pin",
                profile_key="pk",
                source_path="/z.mp4",
                output_format="hls_mpegts",
                video_codec="h264",
                audio_codec="aac",
                status=TS_STOPPED,
                output_directory=str(pinned.resolve()),
            )
            db.add(dead_ts)
            db.commit()
            stats_pin = transcode_manager.cleanup_expired(db)
            check("已停止会话不钉住缓存", not pinned.exists(), stats_pin)
            db.delete(dead_ts)
            db.delete(idle_ts)
            db.delete(old_ps)
            db.commit()
        finally:
            settings.transcode_session_timeout = old_ts_timeout
            settings.playback_session_timeout = old_ps_timeout

    # ========== Part G：PlaybackManager.create_session 决策链路 ==========
    print("== G. 统一播放会话（Case 1/2/3）==")
    pb = PlaybackManager()
    old_get_info = pb.get_media_info
    try:
        with SessionLocal() as db:
            src = Path(settings.library_dir)
            src.mkdir(parents=True, exist_ok=True)
            (src / "g1.mp4").touch()
            (src / "g2.mkv").touch()
            (src / "g3.mkv").touch()
            (src / "g4.mp4").touch()
            (src / "g5.mp4").touch()
            mv1 = MusicVideo(name="g1", file_path="g1.mp4")
            mv2 = MusicVideo(name="g2", file_path="g2.mkv")
            mv3 = MusicVideo(name="g3", file_path="g3.mkv")
            db.add_all([mv1, mv2, mv3])
            db.commit()

            # G1: mp4+h264+aac → Direct Play（0 FFmpeg）
            pb.get_media_info = lambda p: mk_media_info(container="mp4", vcodec="h264", acodec="aac", width=1280, height=720)
            s1 = pb.create_session(db, mv1, requested_quality="original")
            db.refresh(s1)
            check("G1 direct_play", s1.play_mode == "direct_play", s1.play_mode)
            check("G1 无 transcode 会话", s1.transcode_session_id is None)
            check("G1 transcode_required=False", s1.transcode_required is False)
            d1 = pb.to_dict(s1)
            check("G1 stream_url", d1["stream_url"] and d1["stream_url"].endswith(f"/{s1.id}/stream"), d1["stream_url"])
            check("G1 manifest_url=None", d1["manifest_url"] is None)

            # G1.5: 1080P 源选 720P → 原文件无法满足档位，必须转码降分辨率
            pb.get_media_info = lambda p: mk_media_info(container="mp4", vcodec="h264", acodec="aac", width=1920, height=1080)
            mv4 = MusicVideo(name="g4", file_path="g4.mp4")
            db.add(mv4)
            db.commit()
            s1b = pb.create_session(db, mv4, requested_quality="720p")
            db.refresh(s1b)
            check("G1.5 选 720P 触发转码", s1b.play_mode == "transcode", s1b.play_mode)
            check("G1.5 quality=720p", s1b.quality == "720p", s1b.quality)
            check("G1.5 记录请求档位", s1b.requested_quality == "720p", s1b.requested_quality)
            check("G1.5 记录决策原因", bool(s1b.decision_reason), s1b.decision_reason)
            check("G1.5 输出 1280x720", s1b.width == 1280 and s1b.height == 720, f"{s1b.width}x{s1b.height}")
            check("G1.5 transcode_required=True", s1b.transcode_required is True)
            check("G1.5 关联转码会话", s1b.transcode_session_id is not None)
            d1b = pb.to_dict(s1b)
            check("G1.5 使用 HLS 清单", d1b["manifest_url"] and "manifest.m3u8" in d1b["manifest_url"], d1b["manifest_url"])

            # G1.6: 720P 源选 720P → 原文件已满足档位，仍 Direct Play（不浪费转码）
            pb.get_media_info = lambda p: mk_media_info(container="mp4", vcodec="h264", acodec="aac", width=1280, height=720)
            mv5 = MusicVideo(name="g5", file_path="g5.mp4")
            db.add(mv5)
            db.commit()
            s1c = pb.create_session(db, mv5, requested_quality="720p")
            db.refresh(s1c)
            check("G1.6 源已是 720P 直接播", s1c.play_mode == "direct_play", s1c.play_mode)
            check("G1.6 无转码会话", s1c.transcode_session_id is None)
            check("G1.6 记录请求档位", s1c.requested_quality == "720p", s1c.requested_quality)
            check("G1.6 记录决策原因", bool(s1c.decision_reason), s1c.decision_reason)

            # G2: mkv+h264+aac → Direct Stream（视频不重编码）
            pb.get_media_info = lambda p: mk_media_info(container="mkv", vcodec="h264", acodec="aac", width=1280, height=720)
            s2 = pb.create_session(db, mv2, requested_quality="original")
            db.refresh(s2)
            check("G2 direct_stream", s2.play_mode == "direct_stream", s2.play_mode)
            check("G2 关联 transcode 会话", s2.transcode_session_id is not None)
            ts2 = db.get(TranscodeSession, s2.transcode_session_id)
            check("G2 ts video copy", ts2.video_codec == "copy", ts2.video_codec)
            check("G2 transcode_required=False", s2.transcode_required is False)
            d2 = pb.to_dict(s2)
            check("G2 manifest_url", d2["manifest_url"] and "manifest.m3u8" in d2["manifest_url"])

            # G2b: mkv+av1，客户端能解 AV1 → Direct Stream remux（fMP4），不要强制转码
            client_av1 = ClientCapabilities(
                supported_video_codecs=["h264", "av1"],
                supported_audio_codecs=["aac"],
                supported_containers=["mp4", "webm"],
                supports_hls=True,
                supports_fmp4=True,
            )
            pb.get_media_info = lambda p: mk_media_info(
                container="mkv", vcodec="av1", acodec="aac", width=1920, height=1080
            )
            s2b = pb.create_session(db, mv2, requested_quality="original", client=client_av1)
            db.refresh(s2b)
            check("G2b av1 走 Direct Stream", s2b.play_mode == "direct_stream", s2b.play_mode)
            check("G2b 不强制转码", s2b.transcode_required is False)
            check("G2b 输出 fMP4 HLS", s2b.container == "hls_fmp4", s2b.container)
            ts2b = db.get(TranscodeSession, s2b.transcode_session_id)
            check("G2b 视频 copy", ts2b.video_codec == "copy", ts2b.video_codec)

            # G2c: 同一份 AV1，客户端未报 av1 → 才转码
            s2c = pb.create_session(db, mv2, requested_quality="original")
            db.refresh(s2c)
            check("G2c 不解 av1 才转码", s2c.play_mode == "transcode", s2c.play_mode)

            # G2d: 桌面 Chrome UA 即便没报 av1，也按能解处理（canPlayType 漏报）
            chrome_quiet = ClientCapabilities.parse({
                "supported_video_codecs": ["h264"],
                "supported_audio_codecs": ["aac"],
                "supported_containers": ["mp4", "webm"],
                "supports_hls": True,
                "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            })
            s2d = pb.create_session(db, mv2, requested_quality="original", client=chrome_quiet)
            db.refresh(s2d)
            check("G2d Chrome UA 漏报 av1 仍 Direct Stream", s2d.play_mode == "direct_stream", s2d.play_mode)
            check("G2d Chrome UA 输出 fMP4", s2d.container == "hls_fmp4", s2d.container)

            # G3: mkv+hevc+aac → Transcode（必须重编码）
            pb.get_media_info = lambda p: mk_media_info(container="mkv", vcodec="hevc", acodec="aac", width=1920, height=1080)
            s3 = pb.create_session(db, mv3, requested_quality="1080p")
            db.refresh(s3)
            check("G3 transcode", s3.play_mode == "transcode", s3.play_mode)
            check("G3 transcode_required=True", s3.transcode_required is True)
            ts3 = db.get(TranscodeSession, s3.transcode_session_id)
            check("G3 ts 输出规格", ts3.video_codec == "h264" and ts3.height == 1080, f"{ts3.video_codec}x{ts3.height}")
            check("G3 初始状态 starting", s3.status == PS_STARTING, s3.status)
            d3 = pb.to_dict(s3)
            check("G3 manifest_url", d3["manifest_url"] and "manifest.m3u8" in d3["manifest_url"])

            # G4: 心跳状态机 created→starting→playing
            pb.heartbeat(db, s3.id)
            db.refresh(s3)
            check("心跳1 → playing", s3.status == PS_PLAYING, s3.status)

            # G5: 1080p 源请求 4K 档 → 收敛到 1080p
            pb.get_media_info = lambda p: mk_media_info(container="mp4", vcodec="hevc", acodec="aac", width=1920, height=1080)
            s4 = pb.create_session(db, mv3, requested_quality="2160p")
            db.refresh(s4)
            check("G5 档位收敛 1080p", s4.quality == "1080p", s4.quality)

            # G6: 未达转码档位上限 → 会话复用（同 key 直接复用）
            pb.get_media_info = lambda p: mk_media_info(container="mp4", vcodec="hevc", acodec="aac", width=1920, height=1080)
            s5 = pb.create_session(db, mv3, requested_quality="1080p")
            db.refresh(s5)
            check("G6 同规格复用 ts", s5.transcode_session_id == s3.transcode_session_id, f"{s5.transcode_session_id} vs {s3.transcode_session_id}")

            # G6.5: 播放器请求 manifest → 惰性启动 FFmpeg（真实播放流程）
            fe_mod.ffmpeg_engine.start = fake_start
            _start_calls.clear()
            pb.ensure_transcode_started(db, s3)
            db.refresh(ts3)
            check("G6.5 manifest 触发启动", ts3.status == TS_RUNNING and ts3.ffmpeg_pid == 4242, f"{ts3.status}/{ts3.ffmpeg_pid}")

            # G7: stop_session 引用归零联动停止（s3/s4/s5 全部引用同一个 ts）
            pb.stop_session(db, s3.id)
            db.refresh(ts3)
            check("G7 停 1 个后 ts 仍 running", ts3.status == TS_RUNNING, ts3.status)
            pb.stop_session(db, s5.id)
            db.refresh(ts3)
            check("G7 停 2 个后 ts 仍 running", ts3.status == TS_RUNNING, ts3.status)
            pb.stop_session(db, s4.id)
            db.refresh(ts3)
            check("G7 最后引用停止 → ts stopped", ts3.status in (TS_STOPPED, TS_FAILED), ts3.status)

            ts4 = db.get(TranscodeSession, s1b.transcode_session_id) if s1b.transcode_session_id else None

            # G8: diagnose() 只读预演（不创建会话、不启动 FFmpeg）
            pb.get_media_info = lambda p: mk_media_info(container="mp4", vcodec="h264", acodec="aac", width=1920, height=1080)
            mv4.width = 1920
            mv4.height = 1080
            db.commit()
            sessions_before_diag = db.query(PlaybackSession.id).count()
            diag = pb.diagnose(db, mv4)
            check("G8 命中数据库元数据", diag["db_meta"]["height"] is not None, diag["db_meta"].get("height"))
            check("G8 输出文件探测", diag["probe"] and diag["probe"]["width"] == 1920 and diag["probe"]["height"] == 1080, diag["probe"])
            check("G8 可用画质含 720p", "720p" in diag["available_qualities"], diag["available_qualities"])
            req720 = next((r for r in diag["requests"] if r["requested_quality"] == "720p"), None)
            check("G8 720P 预演=转码", req720 and req720["play_mode"] == "transcode", req720)
            check("G8 预演记录原因", req720 and bool(req720["reason"]), req720)
            check("G8 预演不创建会话", db.query(PlaybackSession.id).count() == sessions_before_diag, "会话数异常")
            req_orig = next((r for r in diag["requests"] if r["requested_quality"] == "original"), None)
            check("G8 original 预演=直连", req_orig and req_orig["play_mode"] == "direct_play", req_orig)

            # G9: VP9 4K 选 720p 仍须转码降分辨率；客户端能解 VP9 时 original 走 remux
            print("== G9. VP9 源按档位转码 / 客户端能解则 original remux ==")
            (src / "g9.mkv").touch()
            pb.get_media_info = lambda p: mk_media_info(container="mkv", vcodec="vp9", acodec="opus", width=3840, height=2160)
            mv9 = MusicVideo(name="g9-vp9", file_path="g9.mkv")
            db.add(mv9)
            db.commit()
            client_vp9 = ClientCapabilities(
                supported_video_codecs=["h264", "hevc", "vp9"],
                supported_audio_codecs=["aac", "opus"],
                # 与 Chrome 一致：能解 VP9，但不直连 MKV
                supported_containers=["mp4", "m4v", "mov", "webm"],
                supports_hls=True, supports_fmp4=True, supports_aac=True,
            )
            s9 = pb.create_session(db, mv9, requested_quality="720p", client=client_vp9)
            db.refresh(s9)
            check("G9 VP9 720p 转码降分辨率", s9.play_mode == "transcode", s9.play_mode)
            check("G9 VP9 目标为 h264", s9.video_codec == "h264", s9.video_codec)
            check("G9 VP9 720p 输出 1280x720", s9.width == 1280 and s9.height == 720,
                  (s9.width, s9.height))
            check(
                "G9 原因含降低分辨率",
                "降低分辨率" in (s9.decision_reason or "") or "720p" in (s9.decision_reason or ""),
                s9.decision_reason,
            )
            s9b = pb.create_session(db, mv9, requested_quality="original", client=client_vp9)
            db.refresh(s9b)
            check("G9 VP9 original 客户端能解 → Direct Stream", s9b.play_mode == "direct_stream", s9b.play_mode)
            check("G9 VP9 original 输出 fMP4", s9b.container == "hls_fmp4", s9b.container)
            ts9b = db.get(TranscodeSession, s9b.transcode_session_id)
            check("G9 VP9 original 视频 copy", ts9b and ts9b.video_codec == "copy", ts9b.video_codec if ts9b else None)
            check("G9 VP9 original 音频转 AAC", s9b.audio_codec == "aac", s9b.audio_codec)
            s9c = pb.create_session(db, mv9, requested_quality="original")
            db.refresh(s9c)
            check("G9 客户端不解 VP9 才转码", s9c.play_mode == "transcode", s9c.play_mode)
            db.delete(s9c)
            db.delete(s9b)
            db.delete(s9)
            if ts9b:
                db.delete(ts9b)
            db.delete(mv9)
            db.commit()

            for obj in (mv1, mv2, mv3, mv4, mv5, s1, s2, s3, s4, s5, s1b, s1c, ts2, ts3, ts4):
                db.delete(obj)
            db.commit()
    finally:
        pb.get_media_info = old_get_info

    # ========== Part H：manifest 重写 / segment 校验 ==========
    print("== H. manifest 重写 / segment 穿越防护 ==")
    fe_mod.ffmpeg_engine.start = fake_start
    pb = PlaybackManager()
    old_get_info = pb.get_media_info
    try:
        with SessionLocal() as db:
            mv = MusicVideo(name="g1-h", file_path="g1.mp4")
            db.add(mv)
            db.commit()
            pb.get_media_info = lambda p: mk_media_info(container="mkv", vcodec="hevc", acodec="aac", width=1920, height=1080)
            s = pb.create_session(db, mv, requested_quality="1080p")
            db.refresh(s)
            ts = db.get(TranscodeSession, s.transcode_session_id)
            out = Path(ts.output_directory)
            out.mkdir(parents=True, exist_ok=True)
            # P0 §5.2：ensure_started 会先清空本路旧产物再启动 FFmpeg。测试先启动会话，
            # 让 fake_start 把进程注册为 running，随后写入的"ffmpeg 已产出"文件才不会
            # 在随后 manifest_content 的 ensure_started 幂等短路里被再清一次。
            pb.ensure_transcode_started(db, s)
            (out / "segment_00001.ts").write_bytes(b"seg1")
            (out / "segment_00002.ts").write_bytes(b"seg2")
            (out / "playlist.m3u8").write_text(
                "#EXTM3U\n#EXT-X-VERSION:7\n"
                "#EXT-X-TARGETDURATION:6\n"
                "#EXTINF:6.000000,\n"
                "segment_00001.ts\n"
                "#EXTINF:6.000000,\n"
                "segment_00002.ts\n"
                "#EXT-X-ENDLIST\n"
            )

            content, mtype = pb.manifest_content(db, s)
            check("H media type", mtype == "application/vnd.apple.mpegurl", mtype)
            check(
                "H 无 init.mp4 重写（MPEG-TS 无 init 段）",
                "init.mp4" not in content,
                content,
            )
            check("H segment 重写", f"/api/playback/sessions/{s.id}/segments/segment_00001.ts" in content)
            check("H 注释行保留", "#EXTM3U" in content and "#EXT-X-ENDLIST" in content)

            p = pb.segment_path(db, s, "segment_00001.ts")
            check("H segment 定位", p.name == "segment_00001.ts" and p.is_file())

            from fastapi import HTTPException

            try:
                pb.segment_path(db, s, "../secret.mp4")
                check("H 穿越被拒", False)
            except HTTPException as e:
                check("H 穿越被拒", e.status_code == 400, e.status_code)
            try:
                pb.segment_path(db, s, "not-a-segment.txt")
                check("H 非法文件名被拒", False)
            except HTTPException as e:
                check("H 非法文件名被拒", e.status_code == 400)

            # 清理测试生成的会话缓存目录
            import shutil

            shutil.rmtree(Path(ts.output_directory).parent.parent.parent, ignore_errors=True)

            db.delete(s)
            db.delete(ts)
            db.delete(mv)
            db.commit()
    finally:
        pb.get_media_info = old_get_info

    # ========== Part H2：MPEG-TS 清单重写（用户验收场景，session_id=9）==========
    print("== H2. MPEG-TS 清单重写（TS 分片，EVENT→VOD 覆盖）==")
    # 生产形态：FFmpeg 写的是 #EXT-X-PLAYLIST-TYPE:EVENT（VOD 注入永不发生），
    # 必须覆盖成 VOD——但仅在转码已结束（finished=True）时才覆盖。
    h2_input = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:7\n"
        "#EXT-X-PLAYLIST-TYPE:EVENT\n"
        "#EXT-X-TARGETDURATION:3\n"
        "#EXTINF:3.0,\n"
        "segment_00000.ts\n"
        "#EXTINF:3.0,\n"
        "segment_00001.ts\n"
    )
    h2_out = rewrite_hls_uris(h2_input, 9)
    check(
        "H2 segment_00000 重写",
        "/api/playback/sessions/9/segments/segment_00000.ts" in h2_out,
        h2_out,
    )
    check(
        "H2 segment_00001 重写",
        "/api/playback/sessions/9/segments/segment_00001.ts" in h2_out,
    )
    check(
        "H2 不产生错误 URL /sessions/9/init.mp4",
        "init.mp4" not in h2_out,
        h2_out,
    )
    check(
        "H2 finished=True 覆盖为 VOD",
        "#EXT-X-PLAYLIST-TYPE:VOD" in h2_out,
        h2_out,
    )
    check(
        "H2 不含 EVENT 类型",
        "#EXT-X-PLAYLIST-TYPE:EVENT" not in h2_out
        and "PLAYLIST-TYPE:EVENT" not in h2_out,
        h2_out,
    )
    check(
        "H2 VOD 插在 #EXTM3U 之后",
        "#EXTM3U\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:3"
        in h2_out,
        h2_out,
    )
    check(
        "H2 升级为 VOD 并补齐 ENDLIST",
        "#EXT-X-PLAYLIST-TYPE:VOD" in h2_out
        and h2_out.rstrip().endswith("#EXT-X-ENDLIST"),
        h2_out,
    )
    check(
        "H2 #EXTINF 行不被改写",
        "#EXTINF:3.0," in h2_out,
    )

    # ---- H2.0 转码进行中（finished=False）：EVENT、不补片、无 ENDLIST（修复 iPhone 首播转圈）----
    print("== H2.0 转码进行中清单（finished=False → EVENT，无 ENDLIST，不补未来分片）==")
    growing_input = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:7\n"
        "#EXT-X-PLAYLIST-TYPE:EVENT\n"
        "#EXT-X-TARGETDURATION:3\n"
        "#EXTINF:3.0,\n"
        "segment_00000.ts\n"
        "#EXTINF:3.0,\n"
        "segment_00001.ts\n"
    )
    growing_out = rewrite_hls_uris(
        growing_input, 9, total_duration=60, segment_duration=3, finished=False
    )
    check(
        "H2.0 finished=False 保持 EVENT",
        "#EXT-X-PLAYLIST-TYPE:EVENT" in growing_out,
        growing_out,
    )
    check(
        "H2.0 finished=False 不含 VOD",
        "#EXT-X-PLAYLIST-TYPE:VOD" not in growing_out,
    )
    check(
        "H2.0 finished=False 无 ENDLIST",
        "#EXT-X-ENDLIST" not in growing_out,
        growing_out,
    )
    check(
        "H2.0 只暴露已生成分片（00000/00001 重写）",
        "/api/playback/sessions/9/segments/segment_00000.ts" in growing_out
        and "/api/playback/sessions/9/segments/segment_00001.ts" in growing_out,
        growing_out,
    )
    check(
        "H2.0 即便给了 total_duration 也不补未来分片（00002 不存在）",
        "/api/playback/sessions/9/segments/segment_00002.ts" not in growing_out,
        growing_out,
    )

    # ---- H2.1 补分片按本路输出时长（finished=True 才允许补齐） ----
    print("== H2.1 按 total_duration 补分片（finished=True → VOD 补齐）==")
    # total_duration=12, segment_duration=3, 已有 1 个 3s 分片（segment_00000）
    # → 补 9s = 3 个分片（00001–00003），不多补、不覆写已有分片
    pad_input = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:7\n"
        "#EXT-X-PLAYLIST-TYPE:EVENT\n"
        "#EXT-X-TARGETDURATION:3\n"
        "#EXTINF:3.0,\n"
        "segment_00000.ts\n"
    )
    pad_out = rewrite_hls_uris(pad_input, 9, total_duration=12, segment_duration=3)
    check(
        "H2.1 已有分片不覆写（00000 保留）",
        "/api/playback/sessions/9/segments/segment_00000.ts" in pad_out,
        pad_out,
    )
    check(
        "H2.1 补齐 00001",
        "/api/playback/sessions/9/segments/segment_00001.ts" in pad_out,
    )
    check(
        "H2.1 补齐 00002",
        "/api/playback/sessions/9/segments/segment_00002.ts" in pad_out,
    )
    check(
        "H2.1 补齐 00003",
        "/api/playback/sessions/9/segments/segment_00003.ts" in pad_out,
    )
    check(
        "H2.1 不多补 00004（12s / 3s = 4 段上限）",
        "/api/playback/sessions/9/segments/segment_00004.ts" not in pad_out,
        pad_out,
    )

    # ---- H2.2 从中间窗口起：total_duration 必须是本路输出时长 ----
    # window_start=90, source_duration=300 → output_duration = 300 - 90 = 210
    print("== H2.2 中途窗口补片（window_start=90 → total_duration=210）==")
    mid_input = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:7\n"
        "#EXT-X-PLAYLIST-TYPE:EVENT\n"
        "#EXT-X-TARGETDURATION:3\n"
        "#EXTINF:3.0,\n"
        "segment_00000.ts\n"
    )
    mid_out = rewrite_hls_uris(mid_input, 10, total_duration=210, segment_duration=3)
    check(
        "H2.2 按 210s 补齐到 segment_00069（210/3-1）",
        "/api/playback/sessions/10/segments/segment_00069.ts" in mid_out,
        mid_out,
    )
    check(
        "H2.2 不补出超过 210s 的序号（00070 不存在）",
        "/api/playback/sessions/10/segments/segment_00070.ts" not in mid_out,
    )

    # ---- H2.3 分片「已写完」判定（补丁 §2：禁止对未完成分片返回 200）----
    print("== H2.3 _segment_ready：仅写完才可服务 ==")
    from types import SimpleNamespace

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        ts = SimpleNamespace(output_directory=str(out))
        # 只有文件、但磁盘 playlist 未列出且 N+1 不存在 → 未写完
        (out / "segment_00000.ts").write_bytes(b"x")
        check(
            "H2.3 仅文件存在未列入 playlist → 未写完",
            _segment_ready(ts, "segment_00000.ts") is False,
        )
        # N+1 存在 → N 已写完
        (out / "segment_00001.ts").write_bytes(b"y")
        check(
            "H2.3 N+1 已存在 → N 已完成",
            _segment_ready(ts, "segment_00000.ts") is True,
        )
        # 磁盘 playlist 已列出 00002 → 00002 已写完
        (out / "segment_00002.ts").write_bytes(b"z")
        (out / "playlist.m3u8").write_text(
            "#EXTM3U\n#EXT-X-PLAYLIST-TYPE:EVENT\n"
            "#EXTINF:6.0,\nsegment_00000.ts\n"
            "#EXTINF:6.0,\nsegment_00002.ts\n"
        )
        check(
            "H2.3 磁盘 playlist 已列出 → 已写完",
            _segment_ready(ts, "segment_00002.ts") is True,
        )
        check(
            "H2.3 未列出且 N+1 不存在 → 仍未写完",
            _segment_ready(ts, "segment_00003.ts") is False,
        )
        # fMP4 初始化段：文件已写出即可就绪
        (out / "init.mp4").write_bytes(b"i")
        check("H2.3 init.mp4 已写出即可就绪", _segment_ready(ts, "init.mp4") is True)
        check("H2.3 非法文件名仍未就绪", _segment_ready(ts, "secret.bin") is False)

    print("== H2.4 fMP4 清单重写（EXT-X-MAP / .m4s）==")
    h24_input = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:7\n"
        "#EXT-X-PLAYLIST-TYPE:EVENT\n"
        '#EXT-X-MAP:URI="init.mp4"\n'
        "#EXT-X-TARGETDURATION:3\n"
        "#EXTINF:3.0,\n"
        "segment_00000.m4s\n"
        "#EXTINF:3.0,\n"
        "segment_00001.m4s\n"
    )
    h24_out = rewrite_hls_uris(h24_input, 11)
    check(
        "H2.4 MAP URI 重写",
        '/api/playback/sessions/11/segments/init.mp4' in h24_out,
        h24_out,
    )
    check(
        "H2.4 m4s 分片重写",
        "/api/playback/sessions/11/segments/segment_00000.m4s" in h24_out
        and "/api/playback/sessions/11/segments/segment_00001.m4s" in h24_out,
        h24_out,
    )

    print(f"\n===== p5 验证结果：{PASS} 项通过 =====")

    with SessionLocal() as db:
        for i in _FIXTURE_IDS:
            fixture = db.get(MusicVideo, i)
            if fixture:
                db.delete(fixture)
        db.commit()


if __name__ == "__main__":
    main()
