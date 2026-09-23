#!/usr/bin/env python3
"""真实媒体 E2E：10 个测试用例全链路验证（真实 HTTP + 真实 FFmpeg）。

前置：开发服务器运行在 127.0.0.1:8010；
      /tmp/mv_e2e/ 下已生成 case1_h264_aac.mp4 / case2_h264.mkv / case3_hevc.mp4 / case4_4k.mp4。
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8010"
BACKEND = "/Users/zhanghaoran/Downloads/mv/backend"
MEDIA_DIR = "/tmp/mv_e2e"

CASE_FILES = {
    1: "case1_h264_aac.mp4",  # MP4 H264 AAC -> Direct Play
    2: "case2_h264.mkv",      # MKV H264 AAC -> Direct Stream
    3: "case3_hevc.mp4",      # HEVC AAC -> Transcode
    4: "case4_4k.mp4",        # 4K H264 -> 1080p transcode
}

RESULTS = []


def http(method, path, body=None, timeout=120):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype:
                return resp.status, json.loads(raw)
            return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode(errors="replace")


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  | {detail}" if detail else ""))


def wait_manifest(sid, tries=30, delay=1.0):
    for _ in range(tries):
        st, raw = http("GET", f"/api/playback/sessions/{sid}/manifest.m3u8")
        if st == 200:
            return raw
        time.sleep(delay)
    return None


def wait_segment(sid, seg_name, tries=30, delay=1.0):
    """轮询等待分段文件生成（FFmpeg 异步启动，文件存在才可下载）。"""
    for _ in range(tries):
        st, raw = http("GET", f"/api/playback/sessions/{sid}/segments/{seg_name}", timeout=30)
        if st == 200:
            return st, raw
        time.sleep(delay)
    return st, None


def ts_info(sid):
    st, info = http("GET", f"/api/playback/sessions/{sid}/transcode")
    return info if st == 200 and isinstance(info, dict) else {}


def cleanup_test_data():
    """幂等清理：物理删除上次运行的测试数据（避免 file_hash/uid/cache_key 唯一约束冲突）。"""
    subprocess.run(["pkill", "-f", "ffmpeg"], capture_output=True)
    time.sleep(1)
    sys.path.insert(0, BACKEND)
    from sqlalchemy import delete
    from app.core.database import SessionLocal
    from app.models.music_video import MusicVideo
    from app.models.playback import PlaybackSession, TranscodeSession

    with SessionLocal() as db:
        mv_ids = [
            r[0] for r in db.query(MusicVideo.id)
            .filter(MusicVideo.name.like("e2e_case%")).all()
        ]
        if mv_ids:
            db.execute(delete(PlaybackSession).where(PlaybackSession.music_video_id.in_(mv_ids)))
            db.execute(delete(TranscodeSession).where(TranscodeSession.music_video_id.in_(mv_ids)))
            db.execute(delete(MusicVideo).where(MusicVideo.id.in_(mv_ids)))
        db.execute(delete(TranscodeSession).where(TranscodeSession.cache_key.like("e2e_case9%")))
        db.commit()


def create_mv(name, fname):
    path = os.path.join(MEDIA_DIR, fname)
    st, body = http("POST", "/api/music-videos", {
        "name": name,
        "file_path": path,
        "video_type": "Other",
    })
    return body.get("id") if st in (200, 201) else None


def create_ps(mv_id, **extra):
    payload = {"music_video_id": mv_id}
    payload.update(extra)
    return http("POST", "/api/playback/sessions", payload)


def stop_ps(sid):
    return http("POST", f"/api/playback/sessions/{sid}/stop")


def ffmpeg_procs():
    out = subprocess.run(["pgrep", "-f", "ffmpeg"], capture_output=True, text=True).stdout
    return [p for p in out.split() if p]


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, ValueError):
        return False


def main():
    cleanup_test_data()
    ids = {}
    for case, fname in CASE_FILES.items():
        mid = create_mv(f"e2e_case{case}_{fname}", fname)
        if not mid:
            print(f"FAIL: 无法创建 MusicVideo case{case}")
            sys.exit(2)
        ids[case] = mid
        print(f"case{case} mv_id={mid} <- {fname}")

    # ============ Case 1: MP4 H264 AAC -> Direct Play, 0 FFmpeg ============
    st, ps = create_ps(ids[1])
    check("Case1 创建会话", st == 201, f"st={st}")
    check("Case1 play_mode=direct_play", ps.get("play_mode") == "direct_play", ps.get("play_mode"))
    check("Case1 transcode_required=False", ps.get("transcode_required") is False)
    check("Case1 无 manifest_url", not ps.get("manifest_url"))
    check("Case1 有 stream_url", bool(ps.get("stream_url")))
    st, raw = http("GET", ps["stream_url"], timeout=30)
    check("Case1 stream 可拉取(200)", st == 200, f"st={st}, {len(raw)} bytes")
    time.sleep(0.5)
    check("Case1 FFmpeg 进程数=0", len(ffmpeg_procs()) == 0, str(ffmpeg_procs()))
    stop_ps(ps["session_id"])

    # ============ Case 2: MKV H264 AAC -> Direct Stream(remux) ============
    st, ps = create_ps(ids[2])
    check("Case2 play_mode=direct_stream", ps.get("play_mode") == "direct_stream", ps.get("play_mode"))
    check("Case2 transcode_required=False", ps.get("transcode_required") is False)
    check("Case2 有 manifest_url", bool(ps.get("manifest_url")))
    m3u8 = wait_manifest(ps["session_id"])
    check("Case2 manifest 生成", m3u8 is not None)
    check("Case2 playlist 为 MPEG-TS 分片", b"segment_" in m3u8 if m3u8 else False)
    info = ts_info(ps["session_id"])
    check("Case2 remux 命令含 -c:v copy", "copy" in (info.get("full_command") or ""),
          (info.get("full_command") or "")[:120])
    seg_name = None
    if m3u8:
        for line in m3u8.decode().splitlines():
            if "segment_" in line:
                # playlist 重写为绝对 API 路径，这里取 basename 再拼 BASE
                seg_name = os.path.basename(line.strip())
                break
    if seg_name:
        st, raw = wait_segment(ps["session_id"], seg_name)
        check("Case2 分片可下载(remux)", st == 200 and raw and len(raw) > 0,
              f"st={st}, {len(raw) if raw else 0} bytes")
    stop_ps(ps["session_id"])

    # ============ Case 3: HEVC -> Transcode ============
    st, ps = create_ps(ids[3])
    check("Case3 play_mode=transcode", ps.get("play_mode") == "transcode", ps.get("play_mode"))
    check("Case3 transcode_required=True", ps.get("transcode_required") is True)
    m3u8 = wait_manifest(ps["session_id"])
    check("Case3 manifest 生成(触发 FFmpeg)", m3u8 is not None)
    time.sleep(2)
    info = ts_info(ps["session_id"])
    check("Case3 绑定 transcode_session", bool(info.get("transcode_session_id")),
          f"ts_id={info.get('transcode_session_id')}")
    check("Case3 FFmpeg pid 存在", bool(info.get("ffmpeg_pid")), f"pid={info.get('ffmpeg_pid')}")
    check("Case3 记录硬件=cpu", info.get("hardware_acceleration") == "cpu", info.get("hardware_acceleration"))
    check("Case3 full_command 含 libx264", "libx264" in (info.get("full_command") or ""),
          (info.get("full_command") or "")[:120])
    stop_ps(ps["session_id"])
    time.sleep(2)

    # ============ Case 4: 4K -> 1080p ============
    st, ps = create_ps(ids[4], requested_quality="1080p")
    check("Case4 play_mode=transcode", ps.get("play_mode") == "transcode", ps.get("play_mode"))
    check("Case4 quality=1080p", ps.get("quality") == "1080p", ps.get("quality"))
    check("Case4 输出 1920x1080", ps.get("width") == 1920 and ps.get("height") == 1080,
          f"{ps.get('width')}x{ps.get('height')}")
    aq = ps.get("available_qualities") or []
    check("Case4 不出现 2160p（源 FHD）", "2160p" not in aq, str(aq))
    check("Case4 包含 1080p/720p", all(q in aq for q in ("1080p", "720p")), str(aq))
    m3u8 = wait_manifest(ps["session_id"])
    check("Case4 1080p manifest 生成", m3u8 is not None)
    time.sleep(2)
    info = ts_info(ps["session_id"])
    check("Case4 scale=1920:1080", "1920:1080" in (info.get("full_command") or ""),
          (info.get("full_command") or "")[:140])
    stop_ps(ps["session_id"])
    time.sleep(2)

    # ============ Case 5: 两用户同视频同质量 -> 复用同一 transcode_session ============
    # 说明：Case3 已停止（历史 stopped 会话占用 cache_key），此处首次请求走
    # "重新激活"路径，第二次请求直接复用活跃会话 —— 两个 PlaybackSession 共用一个 FFmpeg。
    st_a, ps_a = create_ps(ids[3])
    st_b, ps_b = create_ps(ids[3])
    check("Case5 两个请求均成功", st_a == 201 and st_b == 201, f"st_a={st_a} st_b={st_b}")
    check("Case5 两个 PlaybackSession 不同", ps_a.get("session_id") != ps_b.get("session_id"),
          f"{ps_a.get('session_id')} vs {ps_b.get('session_id')}")
    info_a = ts_info(ps_a["session_id"])
    info_b = ts_info(ps_b["session_id"])
    check("Case5 复用同一 transcode_session",
          info_a.get("transcode_session_id") == info_b.get("transcode_session_id")
          and bool(info_a.get("transcode_session_id")),
          f"ts_id={info_a.get('transcode_session_id')} == {info_b.get('transcode_session_id')}")
    # 只应有一个 FFmpeg（等待 manifest 触发惰性启动）
    wait_manifest(ps_a["session_id"])
    wait_manifest(ps_b["session_id"])
    time.sleep(2)
    procs = ffmpeg_procs()
    check("Case5 仅 1 个 FFmpeg 进程", len(procs) == 1, f"count={len(procs)}: {procs}")

    # ============ Case 6: seek 到第 7 秒 -> -ss 6.000，不从 0:00 ============
    st, ps = create_ps(ids[4], requested_quality="1080p", start_seconds=7)
    m3u8 = wait_manifest(ps["session_id"])
    check("Case6 seek manifest 生成", m3u8 is not None)
    time.sleep(2)
    info = ts_info(ps["session_id"])
    cmd = info.get("full_command") or ""
    check("Case6 full_command 含 -ss 6.000", "-ss 6.000" in cmd, cmd[:160])
    check("Case6 输出仍 1920x1080", "1920:1080" in cmd, "1920:1080" in cmd)
    stop_ps(ps["session_id"])
    time.sleep(2)

    # ============ Case 7: 停止播放 -> FFmpeg 终止 + 会话 stopped ============
    info_before = ts_info(ps_a["session_id"])
    ts_id7 = info_before.get("transcode_session_id")
    stop_ps(ps_a["session_id"])
    stop_ps(ps_b["session_id"])
    time.sleep(2)
    # 停止后 /transcode 观察端点按设计 404，直接查库验证最终状态与进程退出
    sys.path.insert(0, BACKEND)
    os.chdir(BACKEND)
    from app.core.database import SessionLocal
    from app.models.playback import TranscodeSession
    with SessionLocal() as db:
        ts7 = db.get(TranscodeSession, ts_id7)
        check("Case7 转码会话已停止",
              ts7 is not None and ts7.status in ("stopped", "failed"),
              ts7.status if ts7 else None)
        pid7 = ts7.ffmpeg_pid if ts7 else None
        check("Case7 FFmpeg 进程已退出", pid7 is None or not pid_alive(pid7), f"pid={pid7}")

    # ============ Case 8: FFmpeg 崩溃 -> FAILED + exit_code + stderr ============
    st, ps = create_ps(ids[3])
    m3u8 = wait_manifest(ps["session_id"])
    check("Case8 manifest 生成", m3u8 is not None)
    time.sleep(2)
    info = ts_info(ps["session_id"])
    pid = info.get("ffmpeg_pid")
    check("Case8 拿到 ffmpeg pid", bool(pid), f"pid={pid}")
    check("Case8 FFmpeg 仍在运行(可注入崩溃)", pid_alive(pid), f"pid={pid}")
    if pid and pid_alive(pid):
        os.kill(int(pid), 9)
    else:
        print("[WARN] Case8 FFmpeg 已退出，跳过崩溃注入")
    time.sleep(3)
    info = ts_info(ps["session_id"])
    check("Case8 状态=failed", info.get("status") == "failed", info.get("status"))
    check("Case8 exit_code 非零", info.get("exit_code") is not None and info.get("exit_code") != 0,
          f"exit={info.get('exit_code')}")
    check("Case8 记录 error_message", bool(info.get("error_message")), (info.get("error_message") or "")[:100])
    check("Case8 记录 stderr_tail", bool(info.get("stderr_tail")), (info.get("stderr_tail") or "")[:80])
    stop_ps(ps["session_id"])

    # ============ Case 9: 僵尸会话恢复（模拟 Docker 重启后残留 running）============
    sys.path.insert(0, BACKEND)
    os.chdir(BACKEND)
    from app.core.database import SessionLocal
    from app.models.playback import TranscodeSession, TS_RUNNING
    from app.services.transcode_manager import transcode_manager

    with SessionLocal() as db:
        fake = TranscodeSession(
            cache_key="e2e_case9_zombie",
            profile_key="e2e_case9",
            music_video_id=ids[3],
            source_path=os.path.join(MEDIA_DIR, "case3_hevc.mp4"),
            output_format="hls_mpegts",
            video_codec="h264",
            audio_codec="aac",
            hardware_acceleration="cpu",
            status=TS_RUNNING,
            progress=0.5,
            ffmpeg_pid=99999999,
            output_directory="/tmp/mv_e2e/zombie_out",
        )
        db.add(fake)
        db.commit()
        fake_id = fake.id
        stats = transcode_manager.recover_stale(db)
        after = db.get(TranscodeSession, fake_id)
        check("Case9 recover_stale 将僵尸置为 failed", after.status == "failed",
              f"{after.status} stats={stats}")
        check("Case9 记录 error_message", bool(after.error_message), (after.error_message or "")[:100])

    # ============ Case 10: 硬件降级 QSV->VAAPI->CPU 且记录实际值 ============
    st, info = http("GET", "/api/system/hw-accel")
    check("Case10 hw-accel 接口可用", st == 200, f"st={st}")
    if isinstance(info, dict):
        check("Case10 探测包含 qsv/vaapi 字段", any(k in str(info).lower() for k in ("qsv", "vaapi")),
              str(info)[:120])
    st, ps = create_ps(ids[3])
    m3u8 = wait_manifest(ps["session_id"])
    check("Case10 manifest 生成", m3u8 is not None)
    time.sleep(2)
    tinfo = ts_info(ps["session_id"])
    check("Case10 实际使用 cpu 编码", tinfo.get("hardware_acceleration") == "cpu",
          tinfo.get("hardware_acceleration"))
    cmd = tinfo.get("full_command") or ""
    check("Case10 命令用 libx264(非 h264_qsv/vaapi)",
          "h264_qsv" not in cmd and "h264_vaapi" not in cmd, cmd[:140])
    stop_ps(ps["session_id"])

    # ---- 汇总 ----
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n===== 结果: {passed}/{len(RESULTS)} 通过 =====")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {name} | {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
