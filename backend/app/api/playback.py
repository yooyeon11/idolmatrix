"""Playback API：统一播放会话（Direct Play / Direct Stream / Transcode）。

播放器只面对 PlaybackSession，不感知转码细节：

    POST /playback/sessions                          创建会话 → 返回 stream_url / manifest_url
    GET  /playback/sessions/{session_id}             会话状态
    GET  /playback/sessions/{session_id}/manifest.m3u8  HLS 播放清单（首次请求惰性启动 FFmpeg）
    GET  /playback/sessions/{session_id}/segments/{filename}  HLS 分片（segment_*.ts）
    GET  /playback/sessions/{session_id}/stream      Direct Play 原文件流（支持 Range）
    POST /playback/sessions/{session_id}/heartbeat   心跳续期
    POST /playback/sessions/{session_id}/stop        停止会话（引用计数，最后一人停止 FFmpeg）
    GET  /playback/sessions/{session_id}/transcode   转码可观测信息（调试）

路由挂载在 /api 下（api_router），与 playback_manager.SESSION_URL_PREFIX 保持一致。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from app.core.deps import DbDep
from app.media.client_capabilities import ClientCapabilities
from app.models.music_video import MusicVideo
from app.models.playback import PLAY_MODE_DIRECT_PLAY, PlaybackSession
from app.schemas import (
    PlaybackSessionCreate,
    PlaybackSessionRead,
    PlaybackSessionWindow,
)
from app.services.playback_manager import playback_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/playback/sessions", tags=["playback"])
# 独立前缀的诊断路由：/api/playback/diagnose/{id}（只读，不创建会话）
diag_router = APIRouter(prefix="/playback", tags=["playback"])

_DIRECT_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
}

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _parse_byte_range(
    range_header: Optional[str], size: int
) -> Optional[tuple[int, int]]:
    """解析单区间 Range 头 → (start, end)（闭区间）；无效返回 None。"""
    if not range_header or size <= 0:
        return None
    m = _RANGE_RE.match(range_header.strip())
    if not m:
        return None
    start_s, end_s = m.groups()
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        n = int(end_s)
        if n <= 0:
            return None
        start = max(size - n, 0)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    start = max(start, 0)
    end = min(end, size - 1)
    if start > end or start >= size:
        return None
    return start, end


def range_file_response(
    path: Path, media_type: str, range_header: Optional[str]
) -> Response:
    """支持 Range 请求的文件响应（Direct Play 渐进式播放需要 seek）。"""
    size = path.stat().st_size
    if range_header:
        parsed = _parse_byte_range(range_header, size)
        if parsed:
            start, end = parsed
            fileobj = open(path, "rb")
            fileobj.seek(start)
            remaining = end - start + 1

            def _read_range_chunk() -> bytes:
                # 严格按 (start, end) 闭区间截断：响应体字节数必须与
                # Content-Range 声明一致，多读会让播放器 seek 定位错乱
                nonlocal remaining
                if remaining <= 0:
                    return b""
                chunk = fileobj.read(min(64 * 1024, remaining))
                remaining -= len(chunk)
                return chunk

            headers = {
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes {start}-{end}/{size}",
            }
            return StreamingResponse(
                iter(_read_range_chunk, b""),
                status_code=206,
                headers=headers,
                media_type=media_type,
                background=BackgroundTask(fileobj.close),
            )
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{size}"},
            media_type=media_type,
        )
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Accept-Ranges": "bytes"},
    )


def _get_mv(db, mv_id: int) -> MusicVideo:
    mv = db.get(MusicVideo, mv_id)
    if not mv or mv.deleted_at is not None:
        raise HTTPException(status_code=404, detail="MusicVideo 不存在")
    return mv


def _session_payload(db, session: PlaybackSession) -> dict:
    """会话详情；probe 失败时仍返回会话本身（前端仍可拿到 URL）。"""
    info = None
    try:
        info = playback_manager.get_media_info(Path(session.source_path))
    except Exception:  # noqa: BLE001
        pass
    return playback_manager.to_dict(session, media_info=info)


@router.post("", response_model=PlaybackSessionRead, status_code=201)
def create_session(payload: PlaybackSessionCreate, db: DbDep):
    """统一入口：服务端做播放决策并创建会话，不在此启动 FFmpeg。"""
    mv = _get_mv(db, payload.music_video_id)
    session = playback_manager.create_session(
        db,
        mv,
        requested_quality=payload.requested_quality,
        client=ClientCapabilities.parse(payload.client),
        start_seconds=payload.start_seconds,
        audio_stream_id=payload.audio_stream_id,
        subtitle_stream_id=payload.subtitle_stream_id,
    )
    return _session_payload(db, session)


@router.get("/{session_id}", response_model=PlaybackSessionRead)
def get_session(session_id: int, db: DbDep):
    session = playback_manager.get_active_session(db, session_id)
    return _session_payload(db, session)


@router.get("/{session_id}/manifest.m3u8")
def get_manifest(session_id: int, db: DbDep):
    """HLS 播放清单；首次请求时惰性启动关联的 FFmpeg 转码会话。

    fMP4 直出会话（AV1/VP9 remux）返回带 CODECS 的 master playlist，
    媒体清单在 /index.m3u8 —— xgplayer-hls 建 SourceBuffer 只认 master
    STREAM-INF 的 CODECS，不声明就会兜底 avc1 导致 AV1/VP9 拒收。
    """
    session = playback_manager.get_active_session(db, session_id)
    content, media_type = playback_manager.manifest_content(db, session)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/{session_id}/index.m3u8")
def get_media_playlist(session_id: int, db: DbDep):
    """fMP4 直出会话的媒体清单（由 master playlist 的 STREAM-INF 引用）。"""
    session = playback_manager.get_active_session(db, session_id)
    content, media_type = playback_manager.media_manifest_content(db, session)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/{session_id}/segments/{filename}")
def get_segment(session_id: int, filename: str, db: DbDep):
    """HLS 分片（segment_*.ts）；文件名严格校验防目录穿越。"""
    session = playback_manager.get_active_session(db, session_id)
    path = playback_manager.segment_path(db, session, filename)
    if filename == "init.mp4":
        media_type = "video/mp4"
    elif filename.endswith(".m4s"):
        media_type = "video/iso.segment"
    else:
        media_type = "video/mp2t"
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{session_id}/stream")
def stream_direct(session_id: int, db: DbDep, request: Request):
    """Direct Play：直接流式输出原文件（支持 Range）。"""
    session = playback_manager.get_active_session(db, session_id)
    if session.play_mode != PLAY_MODE_DIRECT_PLAY:
        raise HTTPException(status_code=400, detail="该会话不是 Direct Play，请使用 manifest")
    path = playback_manager.direct_play_stream_path(session)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    return range_file_response(
        path,
        media_type=_DIRECT_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        range_header=request.headers.get("range"),
    )


@router.post("/{session_id}/heartbeat", response_model=PlaybackSessionRead)
def heartbeat(session_id: int, db: DbDep):
    session = playback_manager.heartbeat(db, session_id)
    return _session_payload(db, session)


@router.post("/{session_id}/stop")
def stop_session(session_id: int, db: DbDep, force: bool = True):
    """停止播放会话；引用计数归零时顺带停止 FFmpeg 转码。

    force 默认 True：播放器换窗口 / 离开页面时直接杀掉 FFmpeg，避免
    优雅退出拖到 8 秒、seek 时叠满并发上限。
    """
    playback_manager.stop_session(db, session_id, force=force)
    return {"ok": True, "session_id": session_id}


@router.get("/{session_id}/transcode", response_model=dict)
def transcode_info(session_id: int, db: DbDep):
    """转码会话可观测信息（PID / command / progress / stderr / exit_code）。"""
    session = playback_manager.get_active_session(db, session_id)
    info = playback_manager.ts_info(db, session)
    if info is None:
        raise HTTPException(status_code=404, detail="该会话没有转码信息")
    return info


@router.get("/{session_id}/window", response_model=PlaybackSessionWindow)
def session_window(session_id: int, db: DbDep):
    """本会话当前「已可播范围」（前端 seek 决策依据）。

    available_until（片源绝对秒）= 已转出、可原地 seek 到的位置；超出它说明
    目标分片还没转出来，播放器应新建会话（-ss 到目标）而不是原地 seek。
    转码进行中该值随 FFmpeg 产出增长，前端按 2s 级低频轮询即可；
    finished=True 后不再变化（等于片源总时长），可停止轮询。

    为什么不让前端自己判断：转码中清单无 ENDLIST → MSE 的 duration 恒为
    Infinity，前端任何「目标是否在窗口内」的 duration 比较都恒为真（实测）。
    """
    session = playback_manager.get_active_session(db, session_id)
    return playback_manager.window_state(db, session)


@diag_router.get("/diagnose/{music_video_id}")
def diagnose(music_video_id: int, db: DbDep):
    """只读诊断：文件探测 + 各档位播放决策预演（排查画质/转码问题）。

    对应真实播放决策逻辑（resolve_playback_strategy），但不创建会话、
    不启动 FFmpeg。前端画质菜单或「选低档位未触发转码」问题可先看这里：
    requests[].requested_quality 与该档位的 play_mode / reason。
    """
    mv = _get_mv(db, music_video_id)
    return playback_manager.diagnose(db, mv)
