"""视频封面：按源文件指纹刷新，避免换片后仍显示旧 id 封面。

封面落在 derived/thumbnails/{id}.*。NAS 重部署若沿用旧 derived + 旧库 id，
而正式库里的视频已经换成新文件，GET 必须对比源文件再决定是否重做。

用户手动选帧（cover_manual=True）优先级最高：sidecar 出现/指纹变化
均不再覆盖，只有用户清除手动封面或封面文件丢失时才恢复自动行为。
"""

from __future__ import annotations

import logging
import random
import re
import shutil
from pathlib import Path
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.media.ffmpeg import FFmpegError, extract_thumbnail
from app.media.ffprobe import ProbeError, probe
from app.models.music_video import MusicVideo
from app.services.file_service import COVER_EXTENSIONS, confined_derived_file, find_sidecar_cover
from app.services.library_service import resolve_video_abs_path

logger = logging.getLogger(__name__)

# 候选帧小图宽度（选图够用，控制磁盘占用）
COVER_FRAME_WIDTH = 480
# 「换一批」时与已抽时间点的最小间隔（秒），保证每批不重复
COVER_FRAME_MIN_GAP = 5.0
_FRAME_NAME_RE = re.compile(r"^[\w.-]+$")


def _stamp_path(mv_id: int) -> Path:
    return settings.thumbnail_dir / f"{int(mv_id)}.src"


def source_fingerprint(path: Path, kind: str) -> str:
    st = path.stat()
    return f"{kind}:{st.st_size}:{st.st_mtime_ns}:{path.name}"


def read_stamp(mv_id: int) -> Optional[str]:
    p = _stamp_path(mv_id)
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None
    return None


def write_stamp(mv_id: int, stamp: str) -> None:
    p = _stamp_path(mv_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(stamp, encoding="utf-8")


def remove_stamps_and_thumbs(mv_id: int) -> None:
    folder = settings.thumbnail_dir
    try:
        for p in folder.glob(f"{int(mv_id)}.*"):
            p.unlink(missing_ok=True)
    except OSError:
        pass
    clear_cover_frames(mv_id)


def _thumb_rel_for(mv_id: int, ext: str) -> Path:
    suffix = ext.lower() if ext.startswith(".") else f".{ext}"
    if suffix not in COVER_EXTENSIONS and suffix not in (".jpg", ".jpeg"):
        suffix = ".jpg"
    return Path("thumbnails") / f"{int(mv_id)}{suffix}"


def current_source(mv: MusicVideo) -> Optional[tuple[Path, str]]:
    """封面优先用同名 sidecar，否则用视频文件本身。"""
    video = resolve_video_abs_path(mv)
    if video is None:
        return None
    try:
        sidecar = find_sidecar_cover(video)
    except OSError:
        sidecar = None
    if sidecar is not None:
        return sidecar, "sidecar"
    try:
        if video.is_file():
            return video, "frame"
    except OSError:
        return None
    return None


def ensure_video_thumbnail(db: Session, mv: MusicVideo, *, commit: bool = True) -> Optional[Path]:
    """若源文件/sidecar 相对已登记封面已变，则重做封面。失败时尽量退回旧文件。

    cover_manual=True（用户手动选帧）时直接返回现有封面，不受指纹/sidecar 影响；
    仅当封面文件本身丢失时清除标记并走自动流程重做。
    """
    if getattr(mv, "cover_manual", False):
        manual = confined_derived_file(mv.thumbnail_path)
        if manual is not None:
            try:
                if manual.is_file() and manual.stat().st_size > 0:
                    return manual
            except OSError:
                pass
        # 手动封面丢失：降级回自动流程
        mv.cover_manual = False
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    src = current_source(mv)
    if src is None:
        return confined_derived_file(mv.thumbnail_path)

    source_path, kind = src
    try:
        stamp = source_fingerprint(source_path, kind)
    except OSError:
        return confined_derived_file(mv.thumbnail_path)

    existing = confined_derived_file(mv.thumbnail_path)
    if existing is not None and read_stamp(mv.id) == stamp:
        return existing

    try:
        ext = source_path.suffix.lower() if kind == "sidecar" else ".jpg"
        rel = _thumb_rel_for(mv.id, ext)
        dest = settings.derived_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if kind == "sidecar":
            shutil.copyfile(str(source_path), str(dest))
        else:
            extract_thumbnail(source_path, dest)
        if not dest.is_file() or dest.stat().st_size <= 0:
            raise OSError("封面文件未生成")
        # 清掉同 id 的其它后缀，避免 GET 仍指向旧 webp
        for leftover in settings.thumbnail_dir.glob(f"{mv.id}.*"):
            if leftover.name == dest.name or leftover.suffix == ".src":
                continue
            leftover.unlink(missing_ok=True)
        write_stamp(mv.id, stamp)
        new_rel = rel.as_posix()
        # 封面已更换，旧的人脸焦点失效，待下次按需重检
        focus_reset = mv.focus_x is not None or mv.focus_y is not None
        mv.focus_x = None
        mv.focus_y = None
        if mv.thumbnail_path != new_rel or focus_reset:
            mv.thumbnail_path = new_rel
            if commit:
                db.commit()
        return dest
    except (OSError, FFmpegError, Exception) as e:  # noqa: BLE001
        logger.warning("刷新封面失败 mv=%s: %s", mv.id, e)
        return existing


def thumbnail_etag(path: Path) -> str:
    st = path.stat()
    return f'"{st.st_size:x}-{st.st_mtime_ns:x}"'


# ===== 用户手动选帧（bilibili 式候选帧封面）=====


def _frames_dir(mv_id: int) -> Path:
    return settings.derived_dir / "cover-frames" / str(int(mv_id))


def clear_cover_frames(mv_id: int) -> None:
    """清理某视频的全部候选帧临时文件。"""
    folder = _frames_dir(mv_id)
    try:
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
        # 容器目录空了顺手删掉
        parent = folder.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def _pick_random_times(
    duration: float, count: int, exclude: List[float]
) -> List[float]:
    """在 10%~90% 时长区间随机取点，避开 exclude ± GAP 的已有时间点。"""
    lo = max(0.5, duration * 0.1)
    hi = max(lo + 1.0, duration * 0.9)
    picked: List[float] = []
    attempts = 0
    while len(picked) < count and attempts < count * 60:
        attempts += 1
        t = round(random.uniform(lo, hi), 2)
        if any(abs(t - e) < COVER_FRAME_MIN_GAP for e in picked + list(exclude)):
            continue
        picked.append(t)
    return picked


def generate_cover_frames(
    mv: MusicVideo, *, count: int = 8, exclude: Optional[List[float]] = None
) -> List[dict]:
    """随机抽取候选帧（小图），返回 [{at, name}]。同时清理上一批临时文件。"""
    video = resolve_video_abs_path(mv)
    if video is None or not video.is_file():
        raise FFmpegError("源文件不存在，无法抽取候选帧")
    try:
        duration = probe(str(video)).duration
    except ProbeError as e:
        raise FFmpegError(f"读取视频信息失败: {e}") from e
    if not duration or duration <= 2:
        raise FFmpegError("视频时长过短，无法抽取候选帧")

    clear_cover_frames(mv.id)
    folder = _frames_dir(mv.id)
    folder.mkdir(parents=True, exist_ok=True)

    times = _pick_random_times(duration, count, exclude or [])
    items: List[dict] = []
    for idx, t in enumerate(times):
        name = f"{idx:02d}-{t:.2f}.jpg"
        out = folder / name
        try:
            extract_thumbnail(video, out, t, width=COVER_FRAME_WIDTH)
        except FFmpegError:
            continue
        if out.is_file() and out.stat().st_size > 0:
            items.append({"at": t, "name": name})
    if not items:
        clear_cover_frames(mv.id)
        raise FFmpegError("候选帧抽取失败，请重试")
    return items


def cover_frame_path(mv_id: int, name: str) -> Optional[Path]:
    """候选帧安全路径：校验文件名防穿越，仅允许 jpg。"""
    if not _FRAME_NAME_RE.match(name) or not name.endswith(".jpg"):
        return None
    p = _frames_dir(mv_id) / name
    try:
        if p.is_file():
            return p
    except OSError:
        return None
    return None


def apply_manual_cover(db: Session, mv: MusicVideo, at_seconds: float, *, commit: bool = True) -> Path:
    """按原始分辨率重抽用户选中的时间点，落为正式封面并打 manual 标记。"""
    video = resolve_video_abs_path(mv)
    if video is None or not video.is_file():
        raise FFmpegError("源文件不存在，无法生成封面")

    rel = _thumb_rel_for(mv.id, ".jpg")
    dest = settings.derived_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".part.jpg")
    try:
        extract_thumbnail(video, tmp, at_seconds)
        if not tmp.is_file() or tmp.stat().st_size <= 0:
            raise FFmpegError("封面未生成")
        tmp.replace(dest)
        # 清掉同 id 其它后缀（如 sidecar 复制来的 webp）；必须在 replace 之后执行，
        # 否则 glob "{id}.*" 会把刚生成的 .part.jpg 临时文件也当残留删掉
        for leftover in settings.thumbnail_dir.glob(f"{mv.id}.*"):
            if leftover.name == dest.name or leftover.suffix == ".src":
                continue
            leftover.unlink(missing_ok=True)
    except FFmpegError:
        tmp.unlink(missing_ok=True)
        raise

    # stamp 锁定为 manual 标记，保证 ensure_video_thumbnail 不再重做
    write_stamp(mv.id, "manual")
    mv.cover_manual = True
    mv.thumbnail_path = rel.as_posix()
    # 手动选帧换图后，旧的人脸焦点失效，待下次按需重检
    mv.focus_x = None
    mv.focus_y = None
    if commit:
        db.commit()
    clear_cover_frames(mv.id)
    return dest


def clear_manual_cover(db: Session, mv: MusicVideo, *, commit: bool = True) -> None:
    """清除手动封面标记，恢复自动行为（sidecar 优先，否则自动抽帧）。"""
    mv.cover_manual = False
    try:
        _stamp_path(mv.id).unlink(missing_ok=True)
    except OSError:
        pass
    if commit:
        db.commit()
    clear_cover_frames(mv.id)
    # 立即按自动规则重做一次
    ensure_video_thumbnail(db, mv, commit=commit)
