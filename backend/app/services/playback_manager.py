"""Playback Manager：播放会话编排（统一播放链路的中枢）。

流程（对齐需求文档「第八节」「第三十一节」）：
    播放器 POST /playback/sessions
        → probe 源媒体信息（缓存）
        → Playback Resolver 决策（Direct Play / Direct Stream / Transcode）
        → 创建 PlaybackSession（关联 TranscodeSession，但**不**立即启动 FFmpeg）
        → 返回 session + stream_url / manifest_url

后续按需动作：
    - manifest 首次请求 → 惰性启动 FFmpeg（ensure_transcode_started）
    - heartbeat → 刷新 last_active_at
    - stop → 引用计数；最后一个播放会话停止时停止 FFmpeg（Case 7）
    - seek 跨分片窗口 → 新窗口 = 新 TranscodeSession（Case 6，不从 0:00 重转）
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from fastapi import HTTPException

from app.core.config import settings
from app.media.client_capabilities import ClientCapabilities
from app.media.media_info import MediaInfo, MediaProbeError, probe_media_info
from app.media.transcoding_profile import (
    QUALITY_LADDER,
    TranscodingProfile,
    available_qualities,
)
from app.models.music_video import MusicVideo
from app.models.playback import (
    PS_CREATED,
    PS_FAILED,
    PS_PLAYING,
    PS_STARTING,
    PS_STOPPED,
    PLAY_MODE_DIRECT_PLAY,
    PLAY_MODE_DIRECT_STREAM,
    PLAY_MODE_TRANSCODE,
    PlaybackSession,
    TranscodeSession,
    TS_FAILED,
    TS_STOPPED,
)
from app.services.playback_resolver import resolve_playback_strategy
from app.services.transcode_manager import (
    ACTIVE_TS,
    transcode_manager,
    window_start_for,
)

logger = logging.getLogger(__name__)

# probe 结果进程内缓存 TTL（秒）：避免每个请求都跑 ffprobe
MEDIA_CACHE_TTL = 300.0

# segment 接口对「清单已按 VOD 补齐、但分片尚未转出」的有界等待上限（秒）。
# P0 施工说明 §3：建议 20s；硬转下足够产出后续分片，软转（4K）下也能容纳更长首片。
SEGMENT_WAIT_TIMEOUT = 20.0

# 允许通过 segment 接口取用的文件名（防目录穿越）
_SEGMENT_NAME = re.compile(r"^(init\.mp4|segment_\d{5}\.(ts|m4s))$")
_SEGMENT_LINE = re.compile(r"segment_(\d{5})\.(ts|m4s)")

# 播放会话 URL 前缀（路由注册处保持一致）
SESSION_URL_PREFIX = "/api/playback/sessions"


def rewrite_hls_uris(
    raw: str,
    session_id: int,
    total_duration: Optional[float] = None,
    segment_duration: float = 6.0,
    finished: bool = True,
) -> str:
    """把 HLS/MPEG-TS playlist 中的相对 URI 重写为 Playback Session 的绝对
    API URL，并按转码状态给播放器呈现合适的清单语义。

    分片行（segment_00000.ts → {base}/segment_00000.ts）与普通指令行
    （#EXT-X-VERSION / #EXT-X-TARGETDURATION / #EXTINF 等）进行 URI 重写。

    - finished=False（转码仍在进行，磁盘 playlist 无 ENDLIST）：作为「增长中的
      事件流」呈现 —— PLAYLIST-TYPE:EVENT、只暴露磁盘已真实列出的分片（不补未来
      分片）、不写 ENDLIST。这样 iOS 原生 HLS / 桌面 VHS 会持续拉取清单增补，
      而不是把缺失分片当整段失败（修复 iPhone 首播无限转圈）。
    - finished=True（转码已结束，磁盘已有 ENDLIST）：作为 VOD 呈现 ——
      PLAYLIST-TYPE:VOD、可按本路输出时长补齐分片、写 ENDLIST。

    total_duration 必须是**本路输出时长**（source_duration - window_start），
    不是片源总时长，且仅 finished=True 时才允许据此补齐。
    磁盘上 FFmpeg 自己的 playlist.m3u8 永不被修改。
    """
    base = f"{SESSION_URL_PREFIX}/{session_id}/segments"
    lines = []
    real_duration = 0.0
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append(line)
            continue
        if stripped.startswith("#EXT-X-PLAYLIST-TYPE:"):
            # 覆盖：丢弃 FFmpeg 原写的类型，下面按 finished 状态统一注入
            continue
        if stripped == "#EXT-X-ENDLIST":
            # 内存清单的 ENDLIST 由 finished 统一决定，先不保留原始行
            continue
        if stripped.startswith("#EXT-X-MAP:"):
            rewritten = re.sub(
                r'URI="([^"]+)"',
                lambda m: f'URI="{base}/{m.group(1)}"',
                stripped,
            )
            lines.append(rewritten)
            continue
        if stripped.startswith("#EXTINF:"):
            try:
                real_duration += float(stripped.split(":", 1)[1].split(",", 1)[0])
            except ValueError:
                pass
            lines.append(line)
            continue
        if not stripped.startswith("#") and _SEGMENT_NAME.match(stripped):
            lines.append(f"{base}/{stripped}")
            continue
        lines.append(line)

    # 类型声明，插在 #EXTM3U 之后
    updated = "\n".join(lines)
    play_type = "VOD" if finished else "EVENT"
    marker = "#EXTM3U\n"
    type_line = f"#EXT-X-PLAYLIST-TYPE:{play_type}\n"
    if updated.startswith(marker):
        updated = updated.replace(marker, marker + type_line, 1)
    else:
        updated = type_line + updated

    # 仅在转码已结束时按本路输出时长补分片（转码中绝不补未来分片）
    if finished and total_duration and real_duration < total_duration:
        pad_duration = total_duration - real_duration
        idx = _first_missing_segment_index(lines)
        ext = _segment_ext(lines)
        added = []
        while pad_duration > 1e-6:
            seg_len = min(segment_duration, pad_duration)
            added.append(f"#EXTINF:{seg_len:.6f},")
            added.append(f"{base}/segment_{idx:05d}.{ext}")
            pad_duration -= seg_len
            idx += 1
        if added:
            updated = updated.rstrip() + "\n" + "\n".join(added) + "\n"

    # 转码结束时给播放器 ENDLIST（视频.js 当 VOD、进度条为本路全长）
    if finished and not updated.rstrip().endswith("#EXT-X-ENDLIST"):
        updated = updated.rstrip() + "\n#EXT-X-ENDLIST\n"
    return updated


# ===== fMP4 直出会话的 CODECS 四码 =====
# fourcc 的 profile / 位深必须与源一致（Chrome 建 SourceBuffer 时会校验）；
# level / tier 只是提示不校验 —— av01 固定 13（= level 5.1，4K60 足够），
# vp09 固定 00（= level 未知，MDN 官方示例即 vp09.00.10.08）。
_FMP4_CODEC_CACHE: Dict[str, Optional[Tuple[str, int]]] = {}


def _fmp4_codec_strings(session: PlaybackSession) -> Optional[Tuple[str, int]]:
    """返回 (CODECS 字符串, 总码率 bps)；源探测失败返回 None（master 退回旧路径）。

    结果按 source_path 进程内缓存：清单在转码期间会被播放器反复拉取，
    不值得每次都 ffprobe。
    """
    path = session.source_path
    if path in _FMP4_CODEC_CACHE:
        return _FMP4_CODEC_CACHE[path]
    result: Optional[Tuple[str, int]] = None
    try:
        info = probe_media_info(path)
        vs = info.video_stream
        if vs is not None:
            codec = (vs.codec_name or "").lower()
            pix = (vs.pixel_format or "").lower()
            if "12" in pix:
                depth = "12"
            elif "10" in pix:
                depth = "10"
            else:
                depth = "08"
            fourcc = None
            if codec == "av1":
                prof = {"main": 0, "high": 1, "professional": 2}.get(
                    (vs.profile or "main").strip().lower(), 0
                )
                # RFC 6381：av01.<Profile>.<Level><Tier>.<BitDepth> —— level 与
                # tier 同段（"13M"），拆成两段是无效四码，Chrome 建 SB 即失败
                fourcc = f"av01.{prof:02d}.13M.{depth}"
            elif codec == "vp9":
                # VP9 profile：8bit 420 = 0，10/12bit 420 = 2（vp09.PP.LL.DD 三段）
                prof = 2 if depth in ("10", "12") else 0
                fourcc = f"vp09.{prof:02d}.00.{depth}"
            if fourcc:
                audio_fourcc = "mp4a.40.2"  # fMP4 直出音频只 copy AAC / 转 AAC
                bandwidth = (session.video_bitrate or 0) + (session.audio_bitrate or 0)
                if bandwidth <= 0:
                    bandwidth = info.bit_rate or 10_000_000
                result = (f"{fourcc},{audio_fourcc}", int(bandwidth))
    except MediaProbeError:
        result = None
    except OSError:
        result = None
    _FMP4_CODEC_CACHE[path] = result
    return result


def _first_missing_segment_index(lines) -> int:
    """从已生成的分片行中推断下一个待生成的分片序号。

    兼容裸名与已被 rewrite_hls_uris 改写过的绝对 URL，以及 .ts / .m4s。
    """
    max_idx = -1
    for line in lines:
        m = _SEGMENT_LINE.search(line)
        if m:
            max_idx = max(max_idx, int(m.group(1)))
    return max_idx + 1


def _segment_ext(lines) -> str:
    for line in lines:
        m = _SEGMENT_LINE.search(line)
        if m:
            return m.group(2)
    return "ts"


def _segment_ready(ts, filename: str) -> bool:
    """分片是否已写完、可安全返回（补丁 §2：禁止对未完成分片返回 200）。

    仅当满足其一才视为「已写完」：
      - 磁盘 playlist（FFmpeg 真实产物）已列出该分片：HLS 模式下 FFmpeg 只会在
        分片完整写出后才把它加入 playlist，这是最可靠的「写完」信号；
      - segment_{N+1} 已存在：FFmpeg 顺序写分片，N+1 存在说明 N 已完成。
      - init.mp4：文件已写出即可（fMP4 初始化段）。
    """
    out = Path(ts.output_directory)
    if filename == "init.mp4":
        init = out / "init.mp4"
        return init.is_file() and init.stat().st_size > 0
    m = re.match(r"segment_(\d{5})\.(ts|m4s)$", filename)
    if m:
        n = int(m.group(1))
        ext = m.group(2)
        if (out / f"segment_{n + 1:05d}.{ext}").is_file():
            return True
        playlist = out / "playlist.m3u8"
        if playlist.is_file():
            content = playlist.read_text(errors="ignore")
            if f"segment_{n:05d}.{ext}" in content:
                return True
        return False
    return False


def produced_duration_from_playlist(playlist: Path) -> float:
    """累加 playlist 里已列出分片的 #EXTINF → 已转出时长（秒，本路流内时间）。

    转码进行中，本模块把清单呈现为「增长中的事件流」，只列出 FFmpeg 已真实
    写出的分片（见 rewrite_hls_uris 的 finished=False 分支）。因此这个和就是
    **当前真正可播到的位置**，也是前端判断「seek 目标是否还需要重建会话」的
    唯一可靠依据 —— 浏览器侧拿不到它：未完成清单下 MSE 的 duration 恒为
    Infinity（实测确认），靠 duration 判定会把任何目标都当成「已在窗口内」。

    读不到文件（尚未产出 / 权限）或格式异常时返回 0.0，调用方自然退化为
    「还没转出任何内容」。
    """
    try:
        raw = playlist.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0.0
    total = 0.0
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#EXTINF:"):
            continue
        try:
            total += float(stripped.split(":", 1)[1].split(",", 1)[0])
        except (ValueError, IndexError):
            continue
    return total


class PlaybackError(RuntimeError):
    pass


class PlaybackManager:
    """PlaybackSession 的创建 / 查询 / 心跳 / 停止 / 资源定位。"""

    def __init__(self) -> None:
        # path -> (probe_time, MediaInfo)
        self._media_cache: Dict[str, Tuple[float, MediaInfo]] = {}

    # ===== 媒体信息 =====

    def resolve_source_path(self, mv: MusicVideo) -> Path:
        fp = mv.file_path
        if not fp:
            raise PlaybackError(f"视频 #{mv.id} 缺少 file_path")
        p = Path(fp)
        if p.is_absolute():
            return p
        candidate = settings.library_dir / fp
        if candidate.exists():
            return candidate
        candidate = settings.incoming_dir / fp
        if candidate.exists():
            return candidate
        raise PlaybackError(f"媒体文件不存在: {fp}")

    def get_media_info(self, path: Path) -> MediaInfo:
        key = str(path)
        hit = self._media_cache.get(key)
        now = time.monotonic()
        if hit and now - hit[0] < MEDIA_CACHE_TTL:
            return hit[1]
        try:
            info = probe_media_info(str(path))
        except MediaProbeError as e:
            raise PlaybackError(f"无法解析媒体信息: {e}") from e
        self._media_cache[key] = (now, info)
        return info

    def invalidate_media_cache(self, path: Optional[Path] = None) -> None:
        if path is None:
            self._media_cache.clear()
        else:
            self._media_cache.pop(str(path), None)

    # ===== 会话创建 =====

    def create_session(
        self,
        db,
        mv: MusicVideo,
        *,
        requested_quality: str = "original",
        client: Optional[ClientCapabilities] = None,
        start_seconds: float = 0.0,
        audio_stream_id: Optional[int] = None,
        subtitle_stream_id: Optional[int] = None,
    ) -> PlaybackSession:
        """统一入口：解析 → 决策 → 建会话（不启动 FFmpeg）。"""
        client = client or ClientCapabilities.defaults()
        source_path = self.resolve_source_path(mv)
        info = self.get_media_info(source_path)

        decision = resolve_playback_strategy(
            source=info,
            client=client,
            requested_quality=requested_quality,
        )

        session = PlaybackSession(
            music_video_id=mv.id,
            source_path=str(source_path),
            play_mode=decision.play_mode,
            transcode_required=decision.transcode_required,
            requested_quality=requested_quality,
            decision_reason=decision.reason,
            quality=decision.quality,
            width=decision.width,
            height=decision.height,
            video_bitrate=decision.video_bitrate,
            audio_bitrate=decision.audio_bitrate,
            video_codec=decision.video_codec,
            audio_codec=decision.audio_codec,
            container=decision.container,
            stream_protocol=decision.stream_protocol,
            audio_stream_id=audio_stream_id,
            subtitle_stream_id=subtitle_stream_id,
            client_capabilities=client.to_dict(),
            status=PS_STARTING,
            started_at=datetime.utcnow(),
            last_active_at=datetime.utcnow(),
        )

        # Transcode / Direct Stream 关联转码会话（惰性启动）
        if decision.play_mode in (PLAY_MODE_TRANSCODE, PLAY_MODE_DIRECT_STREAM):
            ts, _created = transcode_manager.get_or_create(
                db,
                music_video_id=mv.id,
                source_path=str(source_path),
                profile=decision.profile,  # type: ignore[arg-type]
                quality=decision.quality,
                protocol=decision.stream_protocol,
                window_start=window_start_for(
                    start_seconds, settings.hls_segment_duration
                ),
                audio_stream_id=audio_stream_id,
                subtitle_stream_id=subtitle_stream_id,
            )
            session.transcode_session_id = ts.id

        db.add(session)
        db.commit()
        db.refresh(session)
        logger.info(
            "播放会话 #%s 创建: mv=%s requested_quality=%s start=%.3fs → mode=%s quality=%s "
            "源=%sx%s %s/%s/%s reason=%s",
            session.id,
            mv.id,
            requested_quality,
            start_seconds,
            decision.play_mode,
            decision.quality,
            info.width or 0,
            info.height or 0,
            info.container or "-",
            info.video_codec or "-",
            info.audio_codec or "-",
            decision.reason,
        )
        return session

    def diagnose(
        self, db, mv: MusicVideo, client: Optional[ClientCapabilities] = None
    ) -> dict:
        """只读诊断：文件探测 + 各档位播放决策预演。

        不创建会话、不启动 FFmpeg。输出数据库元数据、文件探测结果、
        可用画质，以及按 quality ladder 逐档预演的播放决策与原因，
        供排查「该直出却转码 / 选择了低档位却未触发转码」类问题。

        ⚠ client 缺省用 `ClientCapabilities.modern_browser()`（对齐桌面 Chrome 的**实际**
        上报：h264/av1/vp9 + opus/flac、不设分辨率上限），**不再用 `defaults()`** ——
        后者只支持 h264/mp4 且短边上限 1080，会把 4K AV1 报成「全档位转码 /
        视频编码不支持且无法 remux: av1」，而真实 session 是 direct_stream，
        且其 `available_qualities` 也被 1080 截断，与真实 API 互相矛盾。
        """
        result: Dict[str, object] = {
            "music_video_id": mv.id,
            "db_meta": {
                "name": mv.name,
                "width": mv.width,
                "height": mv.height,
                "video_codec": mv.video_codec,
                "audio_codec": mv.audio_codec,
                "duration": mv.duration,
                "bitrate": mv.bitrate,
                "file_size": mv.file_size,
            },
            "file": None,
            "probe": None,
            "available_qualities": [],
            "sample_client": None,
            "requests": [],
        }
        try:
            source_path = self.resolve_source_path(mv)
        except PlaybackError as e:
            result["file"] = {"exists": False, "error": str(e)}
            return result
        try:
            st = source_path.stat()
            result["file"] = {
                "exists": True,
                "path": str(source_path),
                "size": st.st_size,
            }
        except OSError as e:
            result["file"] = {"exists": False, "error": str(e)}
            return result
        try:
            info = self.get_media_info(source_path)
        except PlaybackError as e:
            result["probe"] = {"error": str(e)}
            return result
        vs = info.video_stream
        aas = info.default_audio_stream
        result["probe"] = {
            "width": info.width,
            "height": info.height,
            "video_codec": info.video_codec,
            "video_pixel_format": vs.pixel_format if vs else None,
            "audio_codec": info.audio_codec,
            "audio_channels": aas.channels if aas else None,
            "container": info.container,
            "duration": info.duration,
            "bit_rate": info.bit_rate,
            "size": info.size,
        }
        client = client or ClientCapabilities.modern_browser()
        result["sample_client"] = client.to_dict()
        avail = available_qualities(
            info.width,
            info.height,
            client_max_height=client.max_height or 0,
            client_max_width=client.max_width or 0,
        )
        result["available_qualities"] = avail
        candidates = ["original"] + [r.name for r in QUALITY_LADDER[1:]]
        result["requests"] = []
        for q in candidates:
            decision = resolve_playback_strategy(
                source=info, client=client, requested_quality=q
            )
            result["requests"].append(
                {
                    "requested_quality": q,
                    # 档位超出源能力时会被收敛（1080p 源请求 2160p → 1080p）。
                    # 把收敛结果单列：否则 reason 写的是收敛后的档位、与
                    # requested_quality 对不上，看起来像自相矛盾。
                    "clamped_quality": decision.quality,
                    "available": q in avail,
                    "play_mode": decision.play_mode,
                    "transcode_required": decision.transcode_required,
                    "quality": decision.quality,
                    "width": decision.width,
                    "height": decision.height,
                    "reason": decision.reason,
                }
            )
        return result

    # ===== 查询 =====

    def get_active_session(self, db, session_id: int) -> PlaybackSession:
        s = db.get(PlaybackSession, session_id)
        if (
            not s
            or s.deleted_at is not None
            or s.status in (PS_STOPPED, PS_FAILED)
        ):
            raise HTTPException(status_code=404, detail="播放会话不存在或已结束")
        return s

    def to_dict(self, session: PlaybackSession, media_info: Optional[MediaInfo] = None) -> dict:
        """会话详情（供前端只消费 URL，不感知转码细节）。"""
        d = {
            "session_id": session.id,
            "music_video_id": session.music_video_id,
            "play_mode": session.play_mode,
            "transcode_required": session.transcode_required,
            "requested_quality": session.requested_quality,
            "decision_reason": session.decision_reason,
            "quality": session.quality,
            "width": session.width,
            "height": session.height,
            "video_bitrate": session.video_bitrate,
            "audio_bitrate": session.audio_bitrate,
            "video_codec": session.video_codec,
            "audio_codec": session.audio_codec,
            "container": session.container,
            "stream_protocol": session.stream_protocol,
            "status": session.status,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "last_active_at": (
                session.last_active_at.isoformat() if session.last_active_at else None
            ),
            # P0 施工说明 §4.2：只读字段。
            # source_duration = 片源秒数；start_offset = 本路 window_start。
            # 播放器时间轴是「本路流」时间：currentTime = 本路 0，画面才是片源
            # start_offset 秒。前端若要做「总进度」可从二者推导，但不要拿它去再
            # seek 一次 start_seconds（-ss 只在这一个地方存在）。
            "source_duration": float(media_info.duration) if media_info and media_info.duration else None,
            "start_offset": (
                transcode_manager.get_window_start(session.transcode_session_id)
                if session.transcode_session_id
                else 0.0
            ),
        }
        # 播放 URL：播放器只需 stream_url / manifest_url 二选一
        if session.play_mode == PLAY_MODE_DIRECT_PLAY:
            d["stream_url"] = f"{SESSION_URL_PREFIX}/{session.id}/stream"
            d["manifest_url"] = None
        else:
            d["manifest_url"] = f"{SESSION_URL_PREFIX}/{session.id}/manifest.m3u8"
            d["stream_url"] = None
        d["subtitle_urls"] = []
        d["audio_streams"] = []
        d["available_qualities"] = []
        if media_info:
            d["audio_streams"] = [
                {
                    "index": a.index,
                    "codec": a.codec_name,
                    "language": a.language,
                    "channels": a.channels,
                    "default": a.is_default,
                }
                for a in media_info.audio_streams
            ]
            d["subtitle_urls"] = [
                {
                    "index": st.index,
                    "language": st.language,
                    "codec": st.codec_name,
                }
                for st in media_info.subtitle_streams
            ]
            try:
                client = ClientCapabilities.parse(session.client_capabilities)
                d["available_qualities"] = available_qualities(
                    media_info.width,
                    media_info.height,
                    client_max_height=client.max_height or 0,
                    client_max_width=client.max_width or 0,
                )
            except Exception:  # noqa: BLE001
                pass
        return d

    # ===== 心跳 / 状态 =====

    def heartbeat(self, db, session_id: int) -> PlaybackSession:
        s = self.get_active_session(db, session_id)
        s.last_active_at = datetime.utcnow()
        if s.status == PS_CREATED:
            s.status = PS_STARTING
        elif s.status == PS_STARTING:
            s.status = PS_PLAYING
        db.commit()
        # 顺带刷新关联转码会话活跃度（供闲置回收判断）
        if s.transcode_session_id:
            ts = db.get(TranscodeSession, s.transcode_session_id)
            if ts:
                ts.last_active_at = datetime.utcnow()
                transcode_manager.refresh_progress(db, ts.id)
                db.commit()
        return s

    # ===== 停止（引用计数）=====

    def stop_session(self, db, session_id: int, *, force: bool = False) -> None:
        """停止播放会话；若这是最后一个引用该转码会话的播放者 → 停止 FFmpeg。

        force=True：seek / 换源替换时立刻杀掉 FFmpeg，不等优雅退出。
        """
        s = db.get(PlaybackSession, session_id)
        if not s or s.status in (PS_STOPPED, PS_FAILED):
            return
        ts_id = s.transcode_session_id
        s.status = PS_STOPPED
        s.stopped_at = datetime.utcnow()
        db.commit()
        if ts_id:
            remaining = transcode_manager.reference_count(db, ts_id)
            if remaining <= 0:
                transcode_manager.stop_session(db, ts_id, force=force)

    # ===== manifest / 资源 =====

    def ensure_transcode_started(
        self, db, session: PlaybackSession
    ) -> TranscodeSession:
        """惰性启动关联的 FFmpeg（首次 manifest 请求时）。"""
        if not session.transcode_session_id:
            raise HTTPException(status_code=400, detail="该会话无需转码")
        ts = db.get(TranscodeSession, session.transcode_session_id)
        if not ts:
            raise HTTPException(status_code=404, detail="转码会话不存在")
        info = self.get_media_info(Path(session.source_path))
        transcode_manager.ensure_started(
            db,
            ts,
            source_width=info.width,
            source_height=info.height,
            total_duration=float(info.duration or 0) or None,
            source_codec=info.video_codec,
        )
        session.last_active_at = datetime.utcnow()
        db.commit()
        return ts

    def _wait_for_playlist(
        self, db, ts: TranscodeSession, playlist: Path, timeout: Optional[float] = None
    ) -> None:
        """阻塞等待 playlist.m3u8 生成（最多 timeout 秒）。

        timeout 缺省取 max(hls_playlist_wait_timeout, 分片时长*2)：
        4K 软解首片可能远超 3 秒分片时长，固定 8 秒在 NAS 上偏紧。
        等待期间若 ffmpeg 退出则提前返回，由调用方检查 TS_FAILED。
        """
        if timeout is None:
            timeout = max(
                settings.hls_playlist_wait_timeout,
                settings.hls_segment_duration * 2,
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if playlist.exists():
                return
            proc = transcode_manager.get_process(ts.id)
            if proc is None or not proc.running:
                return  # ffmpeg 已退出，让调用方检查状态
            time.sleep(0.3)

    def window_state(self, db, session: PlaybackSession) -> dict:
        """本会话当前「已可播范围」——供前端判断 seek 是原地跳还是重建会话。

        背景（v3.4.3 修复「转码中 seek 到未转出位置跳不过去」）：
        转码进行中，HLS 清单是增长中的事件流（`PLAYLIST-TYPE:EVENT`、无
        `ENDLIST`，为的是让 iOS 原生 HLS 持续增补而不是把缺片当整段失败），
        此时浏览器 MSE 的 `duration` 恒为 **Infinity**。前端原来用 duration
        判断「目标是否落在当前窗口内」，在 Infinity 下**恒判为真** → 永远走
        「原地 seek」分支，父组件那条「按目标时间重建转码会话（-ss）」的路径
        永远不触发；而目标分片尚未转出，播放器只能卡在已转出区间。

        因此把上界改为由后端明确给出：**片源绝对秒**。

            available_until = start_offset + 已列出分片时长之和

        - 转码中：等于「FFmpeg 已写出内容」的末端；
        - 转码结束（ENDLIST / 进程已退出）：抬到片源总时长（分片已全部在盘，
          此时原地 seek 由 segment 接口直接返回文件，无需重建会话）；
        - direct_play：不走 HLS，返回 None（前端走原生时间轴）。

        客户端应把它当作「可原地 seek 的上界」，超出即应重建会话。
        """
        info = {
            "session_id": session.id,
            "play_mode": session.play_mode,
            "start_offset": 0.0,
            "available_until": None,
            "source_duration": None,
            "finished": True,
        }
        if session.play_mode == PLAY_MODE_DIRECT_PLAY or not session.transcode_session_id:
            return info
        ts = db.get(TranscodeSession, session.transcode_session_id)
        if not ts or not ts.output_directory:
            return info
        out_dir = Path(ts.output_directory)
        start = float(transcode_manager.get_window_start(ts.id))
        source = transcode_manager.get_total_duration(ts.id)
        info["start_offset"] = start
        info["source_duration"] = float(source) if source else None

        playlist = out_dir / "playlist.m3u8"
        produced = produced_duration_from_playlist(playlist)
        available = start + produced
        finished = ts.status == TS_STOPPED
        if not finished and playlist.is_file():
            try:
                finished = "#EXT-X-ENDLIST" in playlist.read_text(
                    encoding="utf-8", errors="ignore"
                )
            except OSError:
                finished = False
        if finished and source:
            available = max(available, float(source))
        info["finished"] = bool(finished)
        info["available_until"] = round(available, 3)
        return info

    def manifest_content(self, db, session: PlaybackSession) -> Tuple[str, str]:
        """播放器实际请求的清单。

        hls_mpegts / 旧会话：直接返回媒体清单（media_manifest_content）。
        hls_fmp4 直出（AV1/VP9 remux）：包一层带 CODECS 的 master playlist ——
        xgplayer-hls 3.0.26 建 SourceBuffer 的 codec **只来自 master STREAM-INF
        的 CODECS 属性**（媒体清单/ init 段 stsd 的 av1C/vpcC 它不解析），
        不声明就兜底 avc1.42e01e，AV1/VP9 分片被 Chrome 拒收（v3.4.5 实测）。
        """
        if (session.container or "") == "hls_fmp4":
            master = self._fmp4_master_playlist(session)
            if master is not None:
                return master, "application/vnd.apple.mpegurl"
        return self.media_manifest_content(db, session)

    def _fmp4_master_playlist(self, session: PlaybackSession) -> Optional[str]:
        """为 fMP4 直出会话构造 master playlist；拿不到四码就返回 None（退回旧路径）。"""
        codec_info = _fmp4_codec_strings(session)
        if not codec_info:
            return None
        codecs, bandwidth = codec_info
        width = session.width or 0
        height = session.height or 0
        resolution = f",RESOLUTION={width}x{height}" if width and height else ""
        return (
            "#EXTM3U\n"
            "#EXT-X-VERSION:6\n"
            f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth}{resolution},CODECS=\"{codecs}\"\n"
            f"{SESSION_URL_PREFIX}/{session.id}/index.m3u8\n"
        )

    def media_manifest_content(self, db, session: PlaybackSession) -> Tuple[str, str]:
        """读取并重写 playlist：把相对 segment 名替换为绝对 API 路径。

        返回 (content, media_type)。
        """
        ts = self.ensure_transcode_started(db, session)
        playlist = Path(ts.output_directory or "") / "playlist.m3u8"

        # playlist 尚未生成：阻塞等待首片产出（时长由 hls_playlist_wait_timeout
        # 与分片时长决定，见 _wait_for_playlist）。
        # VHS 遇 503 不会自动重试，改为在服务端轮询等待，避免前端报错。
        if not playlist.exists():
            self._wait_for_playlist(db, ts, playlist)

        if not playlist.exists():
            # 等待超时后仍未生成：检查 ffmpeg 是否已失败
            ts_fresh = db.get(TranscodeSession, ts.id)
            if ts_fresh and ts_fresh.status == TS_FAILED:
                raise HTTPException(
                    status_code=500,
                    detail=f"转码失败: {ts_fresh.error_message or 'ffmpeg 异常退出'}",
                )
            proc = transcode_manager.get_process(ts.id)
            if proc is not None and proc.running:
                # 仍在转码但首片耗时超过等待窗口：返回 503 引导前端重试
                raise HTTPException(
                    status_code=503,
                    detail="正在准备播放，请稍后重试",
                    headers={"Retry-After": "2"},
                )
            raise HTTPException(status_code=404, detail="playlist 尚未生成，请稍后重试")
        try:
            raw = playlist.read_text(encoding="utf-8")
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"读取 playlist 失败: {e}") from e

        # 本路 VOD 清单的时长 = 本路输出时长 = max(0, 源时长 - window_start)。
        # 禁止把片源总时长直接传给清单重写：否则会给一个从 90s 起转的会话
        # 补出当前 FFmpeg 永远不会写出的序号（P0 施工说明 §2.2）。
        source_duration = transcode_manager.get_total_duration(ts.id)
        window_start = transcode_manager.get_window_start(ts.id)
        output_duration = None
        if source_duration is not None:
            output_duration = max(0.0, float(source_duration) - window_start)

        # 转码是否已结束：FFmpeg 只在输入读完时写 #EXT-X-ENDLIST；转码会话进入
        # TS_STOPPED 也说明进程已正常结束。仍在转码（且磁盘无 ENDLIST）时，
        # 以「增长中的事件流」呈现清单（见 rewrite_hls_uris 的 finished 分支）。
        finished = ts.status == TS_STOPPED or "#EXT-X-ENDLIST" in raw

        content = rewrite_hls_uris(
            raw,
            session.id,
            total_duration=output_duration if finished else None,
            segment_duration=settings.hls_segment_duration,
            finished=finished,
        )
        return content, "application/vnd.apple.mpegurl"

    def segment_path(self, db, session: PlaybackSession, filename: str) -> Path:
        """定位 segment 文件（严格校验文件名，防目录穿越）。"""
        if not _SEGMENT_NAME.match(filename):
            raise HTTPException(status_code=400, detail="非法文件名")
        if not session.transcode_session_id:
            raise HTTPException(status_code=400, detail="该会话没有转码输出")
        ts = db.get(TranscodeSession, session.transcode_session_id)
        if not ts or not ts.output_directory:
            raise HTTPException(status_code=404, detail="转码会话不存在")
        # 补丁 §2：仅当分片「已写完」才返回 200（磁盘 playlist 已列出 / N+1 已存在
        # / 进程已结束），避免对 FFmpeg 正在写的半成品分片返回 200，导致
        # 播放器拿到坏片并卡死在 segment_00000 反复重试。
        if _segment_ready(ts, filename):
            return Path(ts.output_directory) / filename
        # 尚未写完：若转码仍在进行，有界等待它写完（video.js fragLoadingMaxRetry
        # 会吃到后续的 503 重试）
        proc = transcode_manager.get_process(session.transcode_session_id)
        proc_alive = bool(proc and proc.running)
        if ts.status in ACTIVE_TS and proc_alive:
            deadline = time.monotonic() + SEGMENT_WAIT_TIMEOUT
            while time.monotonic() < deadline:
                if _segment_ready(ts, filename):
                    return Path(ts.output_directory) / filename
                # 进程在这轮里退出则提前终止等待
                proc = transcode_manager.get_process(session.transcode_session_id)
                proc_alive = bool(proc and proc.running)
                if not (ts.status in ACTIVE_TS and proc_alive):
                    break
                time.sleep(0.25)
        # 最终判定
        if _segment_ready(ts, filename):
            return Path(ts.output_directory) / filename
        proc = transcode_manager.get_process(session.transcode_session_id)
        proc_alive = bool(proc and proc.running)
        if proc_alive and ts.status in ACTIVE_TS:
            # 转码仍在进行，本路 FFmpeg 最终会按序号写出该分片：让播放器重试
            raise HTTPException(
                status_code=503,
                detail="分片正在转码，请稍后重试",
                headers={"Retry-After": "2"},
            )
        if ts.status == TS_FAILED:
            raise HTTPException(
                status_code=500,
                detail=f"转码失败: {ts.error_message or 'ffmpeg 异常退出'}",
            )
        raise HTTPException(status_code=404, detail="segment 不存在")

    def direct_play_stream_path(self, session: PlaybackSession) -> Path:
        return Path(session.source_path)

    def ts_info(self, db, session: PlaybackSession) -> Optional[dict]:
        """转码会话的可观测信息（仅供状态/调试接口）。"""
        if not session.transcode_session_id:
            return None
        ts = db.get(TranscodeSession, session.transcode_session_id)
        if not ts:
            return None
        proc = transcode_manager.get_process(ts.id)
        return {
            "transcode_session_id": ts.id,
            "status": ts.status,
            "hardware_acceleration": ts.hardware_acceleration,
            "ffmpeg_pid": ts.ffmpeg_pid,
            "progress": ts.progress,
            "exit_code": ts.exit_code,
            "started_at": ts.started_at.isoformat() if ts.started_at else None,
            "stopped_at": ts.stopped_at.isoformat() if ts.stopped_at else None,
            "full_command": ts.full_command,
            "stderr_tail": ts.stderr_tail,
            "error_message": ts.error_message,
            "output_directory": ts.output_directory,
            "running": bool(proc and proc.running),
        }


playback_manager = PlaybackManager()
