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

import json
import logging
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from app.core.config import settings
from app.core.deps import DbDep
from app.media.client_capabilities import ClientCapabilities
from app.media.hw_manager import hw_manager
from app.models.music_video import MusicVideo
from app.models.playback import (
    PLAY_MODE_BILIBILI,
    PLAY_MODE_DIRECT_PLAY,
    PLAY_MODE_TRANSCODE,
    PS_PLAYING,
    PlaybackSession,
)
from app.schemas import (
    PlaybackSessionCreate,
    PlaybackSessionRead,
    PlaybackSessionWindow,
)
from app.services.playback_manager import playback_manager
from app.services.transcode_manager import transcode_manager
from app.services import video_publish_service

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


def _file_validators(path: Path) -> tuple[str, str]:
    """ETag / Last-Modified：200 与 206 必须一致，否则 If-Range 失效。"""
    from email.utils import formatdate

    st = path.stat()
    etag = f'"{st.st_size:x}-{int(st.st_mtime):x}"'
    last_modified = formatdate(st.st_mtime, usegmt=True)
    return etag, last_modified


def range_file_response(
    path: Path,
    media_type: str,
    range_header: Optional[str],
    head_only: bool = False,
) -> Response:
    """支持 Range 请求的文件响应（Direct Play 渐进式播放需要 seek）。

    head_only=True 时（HEAD 请求）返回同样的状态码与头部，但不带响应体：
    VLC / libmpv / AVPlayer 都会先 HEAD 探大小与可跳转性。
    """
    size = path.stat().st_size
    etag, last_modified = _file_validators(path)
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
                # 必须显式给 Content-Length：chunked 的 206 在 iOS AVPlayer /
                # 部分 ExoPlayer 版本上会被判为「长度未知」，拖动直接失败。
                "Content-Length": str(end - start + 1),
                "ETag": etag,
                "Last-Modified": last_modified,
            }
            if head_only:
                fileobj.close()
                return Response(
                    status_code=206, headers=headers, media_type=media_type
                )
            return StreamingResponse(
                iter(_read_range_chunk, b""),
                status_code=206,
                headers=headers,
                media_type=media_type,
                background=BackgroundTask(fileobj.close),
            )
        return Response(
            status_code=416,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes */{size}",
                "ETag": etag,
                "Last-Modified": last_modified,
            },
            media_type=media_type,
        )
    if head_only:
        return Response(
            status_code=200,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(size),
                "Content-Type": media_type,
                "ETag": etag,
                "Last-Modified": last_modified,
            },
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


def _should_use_external(mv: MusicVideo) -> bool:
    """是否应该走 B 站外链播放。

    只有两种情况返回 True：
    1. 只读镜像（本地根本没有媒体文件）
    2. 该视频从未有过本地文件（file_path 为空，例如纯元数据条目）

    ⚠ 刻意**不**检查「file_path 有值但文件当前不存在」：那种情况继续交给
    playback_manager 抛 PlaybackError，错误里带具体文件名；而且 NAS 未挂载
    时全库文件都「不存在」，若在这里静默切到 B 站外链，会把挂载故障掩盖成
    「最近高清源都失效了」，排查方向全错。
    """
    if settings.mirror_mode:
        return True
    if not hasattr(mv, "file_path"):
        # 非 ORM 对象（单测用 SimpleNamespace 伪造）：不猜意图，交回原逻辑
        return False
    return not mv.file_path


def _session_payload(db, session: PlaybackSession) -> dict:
    """会话详情；probe 失败时仍返回会话本身（前端仍可拿到 URL）。"""
    if session.play_mode == PLAY_MODE_BILIBILI:
        # 外部承载：source_path 为空，绝不能去 ffprobe（会白等一次超时）。
        payload = playback_manager.to_dict(session)
        ext = video_publish_service.embed_by_video_id(db, session.music_video_id or 0)
        if ext:
            payload.update(
                embed_url=ext["embed_url"],
                watch_url=ext["watch_url"],
                embed_bvid=ext["bvid"],
                embed_page=ext["page"],
            )
        return payload

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

    # ===== 外部承载分支（B 站）=====
    # 触发条件：本地没有片源（例如 mirror 只读实例，或文件被移走），
    # 且该视频已绑定一条 status=ok 的 BV 号。此时不读文件、不探流、
    # 不转码，只落一条 play_mode=bilibili 的会话（心跳/统计照常）。
    if _should_use_external(mv):
        ext = video_publish_service.embed_payload(db, mv)
        if ext:
            session = PlaybackSession(
                music_video_id=mv.id,
                source_path="",
                play_mode=PLAY_MODE_BILIBILI,
                transcode_required=False,
                requested_quality=payload.requested_quality,
                decision_reason=(
                    f"外部承载：B 站 {ext['bvid']} p{ext['page']}"
                    + ("（只读镜像）" if settings.mirror_mode else "（本地片源缺失）")
                ),
                quality="external",
                width=mv.width,
                height=mv.height,
                container="bilibili",
                stream_protocol=None,
                client_capabilities=None,
                status=PS_PLAYING,
                started_at=datetime.utcnow(),
                last_active_at=datetime.utcnow(),
            )
            db.add(session)
            db.commit()
            db.refresh(session)
            logger.info(
                "播放会话 #%s 创建: mv=%s → bilibili %s p%s（本地无片源）",
                session.id, mv.id, ext["bvid"], ext["page"],
            )
            return _session_payload(db, session)

        if settings.mirror_mode:
            raise HTTPException(
                409,
                "该视频尚未绑定可在线播放的 B 站视频（只读镜像无本地片源）",
            )
        raise HTTPException(
            409,
            "该视频没有可用的播放源：本地文件缺失，且未绑定可播放的 B 站视频",
        )

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


@router.api_route("/{session_id}/stream", methods=["GET", "HEAD"])
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
        head_only=request.method.upper() == "HEAD",
    )


@router.post("/client-log")
def client_log(payload: dict):
    """浏览器侧诊断回传：把「播放失败」的现场写进服务端日志。

    为什么需要：手机自带浏览器的失败原因在设备上，服务端只能看到「没有请求」，
    而用户很难把控制台信息贴出来。前端在出错时把能力/媒体元素状态发过来，
    日志里就能直接看到 mse/hls/错误码，不必再猜。
    """
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        text = str(payload)
    logger.warning("客户端诊断 %s", text[:1500])
    return {"ok": True}


# ===== 「普通 MP4」输出（compat / progressive 共用）=====
#
# 这两条兜底路径原先各自手写 libx264 软编，在低功耗 NAS 上等于不可用：
# 实测 4K60 竖屏直拍（187s）软编 150 秒吐不出第一个字节，而 HLS 走 VAAPI
# 硬编是 4.2× 实时。设备端能解码时，正确做法是「只换容器、不重编码」。
#
# ⚠ 必须覆盖 VP9 / AV1：业主库内实测 AV1 33% + VP9 63% + HEVC 4%，几乎没有 H.264。
#   只放 h264/hevc 等于 96% 的片子仍然全量转码。
_MP4_REMUX_CODECS = frozenset({"h264", "hevc", "vp9", "av1"})

# MP4 容器里的编码 tag：不打标签时部分播放器认不出（尤其 hevc / av1）
_MP4_CODEC_TAGS = {"hevc": "hvc1", "av1": "av01", "vp9": "vp09"}


def _client_video_codecs(session: PlaybackSession) -> set[str]:
    """会话快照里客户端声明的视频解码能力（合并 MSE 与渐进两条管线）。"""
    caps = session.client_capabilities
    if not isinstance(caps, dict):
        return set()
    names: set[str] = set()
    for key in ("supported_video_codecs", "progressive_video_codecs"):
        for raw in caps.get(key) or []:
            name = str(raw or "").strip().lower()
            if name in ("avc", "avc1", "avc3"):
                name = "h264"
            elif name in ("h265", "hev1", "hvc1"):
                name = "hevc"
            if name:
                names.add(name)
    return names


def _can_remux(
    session: PlaybackSession,
    source_codec: str,
    source_width: int = 0,
    source_height: int = 0,
) -> tuple[bool, str]:
    """能否只换容器、不重编码（设备端解码优先）。

    ⚠ 只在「客户端确实声明能解这个编码」时才 remux —— 否则 remux 出来的是
    能下载但放不了的 MP4，比转码更糟，而且用户拿不到任何提示。
    H.264 例外：它是所有浏览器的底线能力，客户端没上报也安全。

    ⚠ 请求降档（目标尺寸 < 源尺寸）时同样不能 remux：`-c:v copy` 输出的仍是
    原始分辨率与码率，降档形同虚设。实测（2026-11-12 外网 vivo 平板）点 720p
    生成的仍是 2026x3840 AV1、与原画同为 564.5MB，中转/弱网根本拉不动。
    源尺寸未知时不加这条限制（此时 codec 探测通常也已失败，走原有判定）。
    """
    tw, th = int(session.width or 0), int(session.height or 0)
    sw, sh = int(source_width or 0), int(source_height or 0)
    if tw and th and sw and sh and (
        min(tw, th) < min(sw, sh) or max(tw, th) < max(sw, sh)
    ):
        return False, (
            f"请求降档 {tw}x{th}（源 {sw}x{sh}），remux 无法降低分辨率/码率"
        )
    codec = (source_codec or "").strip().lower()
    if codec not in _MP4_REMUX_CODECS:
        return False, f"源编码 {codec or '未知'} 不适合 MP4 remux"
    if codec == "h264":
        return True, "h264 全平台可解"
    if codec in _client_video_codecs(session):
        return True, f"客户端声明可解 {codec}"
    return False, f"客户端未声明 {codec} 解码能力"


def _build_mp4_command(
    session: PlaybackSession,
    src: Path,
    *,
    start: float = 0.0,
    remux: bool = False,
    fragment: bool = False,
    source_codec: str = "",
) -> list[str]:
    """普通 MP4 输出命令。

    - `remux=True`：视频 `-c:v copy` 零重编码（设备端解码），只重编音轨
    - `fragment=True`：fMP4 走 pipe（渐进流）；否则落盘普通 MP4（faststart）
    """
    from app.media.ffmpeg_engine import require_ffmpeg

    width = int(session.width or 0)
    height = int(session.height or 0)
    bitrate = int(session.video_bitrate or 4_000_000)
    cmd = [require_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]

    if remux:
        if start > 0:
            cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy"]
        tag = _MP4_CODEC_TAGS.get(source_codec)
        if tag:
            # MP4 里的 hevc / av1 / vp9 必须打标签，否则 Safari / 部分安卓
            # 认成未知编码（表现为「能下载、点开黑屏」）
            cmd += ["-tag:v", tag]
        # 音轨照样重编：MKV 里常见 opus/flac，MP4 容器兼容性差；
        # 相对视频重编码，音频开销可以忽略
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"]
        cmd += (
            ["-movflags", "frag_keyframe+empty_moov+default_base_moof"]
            if fragment
            else ["-movflags", "+faststart"]
        )
        return cmd + ["-f", "mp4"] + (["pipe:1"] if fragment else [])

    # 与 HLS 转码同口径走硬件：decode_args 放 -i 之前，编码参数 / 滤镜链照搬
    hw = hw_manager.select(
        preference="auto",
        video_codec="h264",
        preset="veryfast",
        quality=23,
        scale=f"{width}:{height}" if width and height else None,
        source_codec=source_codec,
    )
    cmd += list(hw.decode_args)
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?"]
    cmd += ["-c:v", hw.encoder] + list(hw.encode_args)
    cmd += ["-bf", "0", "-profile:v", "high"]
    if hw.video_filter:
        cmd += ["-vf", hw.video_filter]
    if hw.method == "cpu":
        cmd += ["-pix_fmt", hw.pixel_format]
    cmd += ["-fps_mode", "cfr"]
    if hw.method in ("vaapi", "qsv"):
        cmd += [
            "-b:v", str(bitrate),
            "-maxrate", str(int(bitrate * 4 / 3)),
            "-bufsize", str(bitrate * 2),
        ]
    else:
        # CPU capped CRF：质量导向 + maxrate 封顶（与 HLS 的 CPU 分支同口径）
        cmd += ["-maxrate", str(bitrate), "-bufsize", str(bitrate * 2)]
    cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"]
    cmd += (
        ["-movflags", "frag_keyframe+empty_moov+default_base_moof"]
        if fragment
        else ["-movflags", "+faststart"]
    )
    return cmd + ["-f", "mp4"] + (["pipe:1"] if fragment else [])


@router.get("/{session_id}/progressive.mp4")
def progressive_stream(session_id: int, db: DbDep):
    """没有 MediaSource 的浏览器兜底：ffmpeg 直出 fMP4，<video src> 渐进播放。

    为什么需要这条：HLS 无论 mpegts 还是 fMP4，都要先塞进 MSE。
    部分手机自带浏览器（vivo / 华为 ArkWeb 等定制内核）虽然 UA 里写着 Chrome，
    但 `MediaSource` 不可用或 `isTypeSupported('video/mp4;codecs=avc1…')` 为假，
    此时 hls.js 直接判不支持，manifest 一次都不会被请求 —— 表现就是「转圈后播放失败」。

    这条输出不经过 MSE：H.264/AAC 的 fragmented MP4 直接进 <video>，
    代价是不能像 HLS 那样任意 seek（前端按 window 重建会话，-ss 由
    transcode_manager 的 window_start 提供）。
    """
    session = playback_manager.get_active_session(db, session_id)
    if session.play_mode != PLAY_MODE_TRANSCODE:
        raise HTTPException(status_code=400, detail="只有转码会话支持渐进输出")
    src = Path(session.source_path or "")
    if not src.is_file():
        raise HTTPException(status_code=404, detail="媒体文件不存在")

    start = 0.0
    if session.transcode_session_id:
        try:
            start = float(transcode_manager.get_window_start(session.transcode_session_id) or 0)
        except Exception:  # noqa: BLE001 — 拿不到窗口起点就从头转
            start = 0.0

    # 设备端解码优先：客户端声明能解源编码时只换容器（-c:v copy），
    # 187 秒 4K 片子通常几秒出片；只有解不了才重编码，且走硬件。
    info = None
    try:
        info = playback_manager.get_media_info(src)
    except Exception:  # noqa: BLE001 — 探测失败就按不支持 remux 处理
        info = None
    source_codec = (getattr(info, "video_codec", "") or "").lower()
    remux, why = _can_remux(
        session,
        source_codec,
        int(getattr(info, "width", 0) or 0),
        int(getattr(info, "height", 0) or 0),
    )
    logger.info(
        "渐进兜底 %s: remux=%s（%s）", Path(src).name, remux, why
    )
    cmd = _build_mp4_command(
        session,
        src,
        start=start,
        remux=remux,
        fragment=True,
        source_codec=source_codec,
    )

    proc = subprocess.Popen(  # noqa: S603 — 参数全部本地构造，无用户输入拼接
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )

    def body():
        try:
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    return StreamingResponse(
        body(),
        media_type="video/mp4",
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(lambda: proc.poll() is None and proc.kill()),
    )


_compat_locks: dict[str, "threading.Lock"] = {}
_compat_locks_guard = threading.Lock()


def _compat_cache_path(
    session: PlaybackSession, variant: str = "", start: float = 0.0
) -> Path:
    """兼容版缓存路径：按 (视频, 档位, 变体, 窗口起点, 源大小, 源 mtime) 命名。

    `variant` 区分 remux / 转码产物 —— 两者可播性不同（remux 保留源编码，
    只能给声明可解该编码的客户端），共用一个文件名会把 A 客户端的缓存
    喂给 B 客户端，表现为「刚才还能播，换台设备黑屏」。
    `start` 必须进缓存键：compat 现在跟随转码窗口输出，同一会话 seek 后
    起点不同，复用旧文件会让画面从错误位置开始。
    """
    src = Path(session.source_path or "")
    st = src.stat()
    root = settings.derived_dir / "compat"
    root.mkdir(parents=True, exist_ok=True)
    tag = f"-{variant}" if variant else ""
    window = f"-s{int(round(start * 1000))}" if start > 0 else ""
    return root / (
        f"{session.music_video_id}-{session.quality or 'original'}"
        f"{tag}{window}-{st.st_size}-{st.st_mtime_ns}.mp4"
    )


@router.get("/{session_id}/compat.mp4")
def compat_stream(session_id: int, db: DbDep):
    """兼容版播放：转成**普通 MP4**（moov 在前、有真实时长、支持 Range）再按文件服务。

    为什么不能继续用边转边发的 fMP4：fragmented MP4 头部里没有总时长
    （实测 ffprobe 把 184 秒的片子读成 4.2 秒），Chrome 能忍，但被国产浏览器
    劫持后的系统原生播放器常常直接判「播放失败」。

    ⚠ 输出必须与 HLS 转码会话使用**同一个窗口起点**。播放器的时间轴是
    `start_offset + currentTime`，若这里偷偷输出整片（从 0 开始），
    非零窗口会话会出现「画面从头播、进度条却显示目标时间」，seek 也会偏移。

    ★ v3.9.10037：设备端解码优先。
    客户端声明能解源编码（h264 / hevc）时只做 **remux**（`-c:v copy`），
    187 秒 4K 片子通常几秒出片；只有解不了才重编码，且与 HLS 同口径走硬件。
    旧实现无条件软编 libx264，实测 150 秒吐不出第一个字节 —— 这才是
    「安卓平板一直转圈/播放失败」的真正原因。
    """
    session = playback_manager.get_active_session(db, session_id)
    if session.play_mode != PLAY_MODE_TRANSCODE:
        raise HTTPException(status_code=400, detail="只有转码会话需要兼容版")
    src = Path(session.source_path or "")
    if not src.is_file():
        raise HTTPException(status_code=404, detail="媒体文件不存在")

    info = None
    try:
        info = playback_manager.get_media_info(src)
    except Exception:  # noqa: BLE001 — 探测失败就按「不可 remux」处理
        info = None
    source_codec = (getattr(info, "video_codec", "") or "").lower()
    remux, why = _can_remux(
        session,
        source_codec,
        int(getattr(info, "width", 0) or 0),
        int(getattr(info, "height", 0) or 0),
    )

    # 与 progressive / HLS 同口径取窗口起点：compat 只输出当前会话可播窗口，
    # 前端才能继续用「start_offset + currentTime」这一套绝对时间轴。
    start = 0.0
    if session.transcode_session_id:
        try:
            start = float(
                transcode_manager.get_window_start(session.transcode_session_id) or 0
            )
        except Exception:  # noqa: BLE001 — 拿不到窗口起点就从头输出
            start = 0.0

    out = _compat_cache_path(session, "rmx" if remux else "tr", start)
    if not out.exists():
        with _compat_locks_guard:
            lock = _compat_locks.setdefault(str(out), threading.Lock())
        with lock:
            if not out.exists():
                out = _render_compat(
                    session,
                    src,
                    out,
                    remux=remux,
                    why=why,
                    source_codec=source_codec,
                    start=start,
                )

    return FileResponse(
        out,
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"},
    )


def _render_compat(
    session: PlaybackSession,
    src: Path,
    out: Path,
    *,
    remux: bool,
    why: str,
    source_codec: str,
    start: float = 0.0,
) -> Path:
    """生成兼容版 MP4；remux 失败自动回退硬编，返回最终落盘路径。"""
    tmp = out.with_suffix(".part")
    cmd = _build_mp4_command(
        session,
        src,
        start=start,
        remux=remux,
        fragment=False,
        source_codec=source_codec,
    )
    cmd.append(str(tmp))
    logger.info("兼容版%s开始: %s（%s）", "remux" if remux else "硬编", out.name, why)
    proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603

    if proc.returncode != 0 and remux:
        # remux 失败（源封装异常 / 音轨不兼容等）→ 回退重编码，
        # 别把 500 直接甩给用户
        logger.warning("remux 失败，回退硬编: %s", (proc.stderr or "")[-300:])
        tmp.unlink(missing_ok=True)
        out = _compat_cache_path(session, "tr", start)
        tmp = out.with_suffix(".part")
        cmd = _build_mp4_command(
            session,
            src,
            start=start,
            remux=False,
            fragment=False,
            source_codec=source_codec,
        )
        cmd.append(str(tmp))
        proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603

    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        logger.error(
            "兼容版输出失败(%s): %s", proc.returncode, (proc.stderr or "")[-500:]
        )
        raise HTTPException(status_code=500, detail="兼容版转码失败")

    tmp.replace(out)
    logger.info("兼容版就绪: %s (%.1f MB)", out.name, out.stat().st_size / 1024 / 1024)
    return out


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
