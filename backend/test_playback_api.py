"""Phase 3：统一 Playback API 契约层验证。

使用 FastAPI TestClient 走真实路由 + Pydantic schema，
PlaybackManager 用替身桩（stub）替代，聚焦 API 层契约：
    - 路由注册 / 状态码 / 响应结构
    - 请求体解析（client 能力、start_seconds、音轨/字幕选择）
    - manifest / segments / stream / heartbeat / stop / transcode 信息

PlaybackManager 的行为已由 test_playback_manager.py（77 项）覆盖。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

_TMP = tempfile.mkdtemp(prefix="pb_api_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["STORAGE_DERIVED_DIR"] = f"{_TMP}/derived"
os.environ["STORAGE_MEDIA_ROOT_DIR"] = f"{_TMP}/media"
os.environ["AUTH_REQUIRED"] = "false"

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models.playback import PLAY_MODE_DIRECT_PLAY  # noqa: E402
import app.api.playback as pb_api  # noqa: E402

passed = 0
failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def fake_session(play_mode: str = "transcode", sid: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=sid,
        music_video_id=1,
        source_path=f"{_TMP}/media/sample.mkv",
        play_mode=play_mode,
        transcode_required=play_mode != PLAY_MODE_DIRECT_PLAY,
        quality="1080p",
        width=1920,
        height=1080,
        video_bitrate=5_000_000,
        audio_bitrate=192_000,
        video_codec="h264",
        audio_codec="aac",
        container="mp4",
        stream_protocol="hls_mpegts",
        status="starting",
        transcode_session_id=7,
    )


PLAYBACK_PAYLOAD = {
    "session_id": 1,
    "music_video_id": 1,
    "play_mode": "transcode",
    "transcode_required": True,
    "quality": "1080p",
    "width": 1920,
    "height": 1080,
    "video_bitrate": 5_000_000,
    "audio_bitrate": 192_000,
    "video_codec": "h264",
    "audio_codec": "aac",
    "container": "mp4",
    "stream_protocol": "hls_mpegts",
    "status": "starting",
    "stream_url": None,
    "manifest_url": "/api/playback/sessions/1/manifest.m3u8",
    "subtitle_urls": [],
    "audio_streams": [],
}


def main() -> None:
    # ---- 路由契约桩 ----
    pb_api.playback_manager.create_session = lambda db, mv, **kw: fake_session()

    def _get_session(db, sid: int):
        if sid != 1:
            raise HTTPException(status_code=404, detail="播放会话不存在或已结束")
        return fake_session()

    pb_api.playback_manager.get_active_session = _get_session
    pb_api.playback_manager.to_dict = lambda s, media_info=None: PLAYBACK_PAYLOAD
    pb_api.playback_manager.heartbeat = lambda db, sid: fake_session()
    pb_api.playback_manager.stop_session = lambda db, sid, **kw: None

    sample = Path(f"{_TMP}/media/sample.mkv")
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_bytes(b"FAKE-MEDIA-BYTES")
    direct_file = Path(f"{_TMP}/media/direct.mp4")
    direct_file.write_bytes(b"FAKE-DIRECT")
    seg_file = Path(f"{_TMP}/media/segment_00000.ts")
    seg_file.write_bytes(b"FAKE-SEG")

    pb_api.playback_manager.manifest_content = lambda db, s: (
        "#EXTM3U\n/api/playback/sessions/1/segments/segment_00000.ts\n",
        "application/vnd.apple.mpegurl",
    )
    pb_api.playback_manager.segment_path = lambda db, s, fn: seg_file
    pb_api.playback_manager.direct_play_stream_path = lambda s: direct_file

    def _ts_info(db, s):
        if s.id == 1:
            return {
                "transcode_session_id": 7,
                "status": "running",
                "hardware_acceleration": "cpu",
                "ffmpeg_pid": 4242,
                "progress": 12.5,
                "full_command": "ffmpeg -i ...",
                "running": True,
            }
        return None

    pb_api.playback_manager.ts_info = _ts_info

    with TestClient(app) as client:
        print("== A. 创建会话 ==")
        # 真实 _get_mv 走空库：不存在 → 404（校验 404 契约）
        r = client.post("/api/playback/sessions", json={"music_video_id": 999})
        check("不存在的 mv → 404", r.status_code == 404, str(r.status_code))

        # 之后用桩替代 _get_mv，聚焦路由契约
        pb_api._get_mv = lambda db, mv_id: SimpleNamespace(id=mv_id, deleted_at=None)

        r = client.post(
            "/api/playback/sessions",
            json={
                "music_video_id": 1,
                "requested_quality": "1080p",
                "start_seconds": 30.0,
                "audio_stream_id": 2,
                "subtitle_stream_id": None,
                "client": {
                    "supported_video_codecs": ["h264"],
                    "supports_hevc": False,
                    "max_height": 1080,
                },
            },
        )
        check("创建 → 201", r.status_code == 201, str(r.status_code))
        body = r.json()
        check("返回 session_id", body.get("session_id") == 1, str(body))
        check("返回 play_mode", body.get("play_mode") == "transcode")
        check(
            "返回 manifest_url",
            body.get("manifest_url") == "/api/playback/sessions/1/manifest.m3u8",
            str(body.get("manifest_url")),
        )

        r = client.post(
            "/api/playback/sessions",
            json={"music_video_id": 1, "requested_quality": "xxx"},
        )
        check("非法质量也收敛（不报错）", r.status_code == 201, str(r.status_code))

        print("== B. 会话详情 / 心跳 / 停止 ==")
        r = client.get("/api/playback/sessions/1")
        check("详情 → 200", r.status_code == 200)
        check("详情含 stream_url 字段", "stream_url" in r.json())

        r = client.get("/api/playback/sessions/999")
        check("详情不存在 → 404", r.status_code == 404, str(r.status_code))

        r = client.post("/api/playback/sessions/1/heartbeat")
        check("心跳 → 200", r.status_code == 200)
        check("心跳返回会话", r.json().get("session_id") == 1)

        r = client.post("/api/playback/sessions/1/stop")
        check("停止 → 200 + ok", r.status_code == 200 and r.json().get("ok"))

        print("== C. manifest / segments ==")
        r = client.get("/api/playback/sessions/1/manifest.m3u8")
        check("manifest → 200", r.status_code == 200)
        check(
            "manifest content-type",
            r.headers.get("content-type", "").startswith("application/vnd.apple.mpegurl"),
            r.headers.get("content-type", ""),
        )
        check("manifest no-cache", "no-cache" in r.headers.get("cache-control", ""))
        check("manifest 内容", "#EXTM3U" in r.text, r.text)

        r = client.get("/api/playback/sessions/1/segments/segment_00000.ts")
        check("segment ts → 200", r.status_code == 200)
        check(
            "segment content-type mp2t",
            r.headers.get("content-type", "").startswith("video/mp2t"),
            r.headers.get("content-type", ""),
        )
        check("segment 有缓存头", "no-store" in r.headers.get("cache-control", ""))

        print("== D. Direct Play stream ==")
        # 非 direct play 会话请求 stream → 400
        r = client.get("/api/playback/sessions/1/stream")
        check("非 Direct Play 请求 stream → 400", r.status_code == 400, str(r.status_code))

        # direct play 会话 → 200 + Range 支持
        pb_api.playback_manager.get_active_session = lambda db, sid: fake_session(
            play_mode=PLAY_MODE_DIRECT_PLAY
        )
        r = client.get("/api/playback/sessions/1/stream")
        check("Direct Play stream → 200", r.status_code == 200, str(r.status_code))
        check(
            "Direct Play content-type",
            r.headers.get("content-type", "").startswith("video/mp4"),
            r.headers.get("content-type", ""),
        )
        r = client.get(
            "/api/playback/sessions/1/stream", headers={"Range": "bytes=0-9"}
        )
        check("Direct Play 支持 Range → 206", r.status_code == 206, str(r.status_code))
        check("Range 响应体正确", r.content == b"FAKE-DIREC", r.content)

        print("== E. 转码可观测信息 ==")
        pb_api.playback_manager.get_active_session = _get_session
        r = client.get("/api/playback/sessions/1/transcode")
        check("transcode 信息 → 200", r.status_code == 200, str(r.status_code))
        info = r.json()
        check("含 pid", info.get("ffmpeg_pid") == 4242)
        check("含 command", "ffmpeg" in (info.get("full_command") or ""))

        r = client.get("/api/playback/sessions/999/transcode")
        check("无转码信息 → 404", r.status_code == 404, str(r.status_code))

    print(f"\n===== p3 验证结果：{passed} 项通过，{failed} 项失败 =====")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
