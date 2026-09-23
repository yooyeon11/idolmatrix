"""文件服务：哈希计算、入库移动、校验、日志。

入库移动流程：
1. 计算源文件 hash（用于去重与校验）
2. 生成目标路径（按组合/标题组织）
3. 移动文件
4. 校验目标文件 hash 与源一致
5. 写 FileMoveLog
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.task import FileMoveLog

logger = logging.getLogger(__name__)

# 哈希分块大小：8MB，平衡内存与速度
_CHUNK_SIZE = 8 * 1024 * 1024
_posix_fadvise = getattr(os, "posix_fadvise", None)
_POSIX_FADV_DONTNEED = getattr(os, "POSIX_FADV_DONTNEED", None)


def drop_file_cache(path: str | Path, fd: Optional[int] = None) -> None:
    """通知内核丢掉该文件的页缓存（Linux posix_fadvise）。其它系统为空操作。"""
    if _posix_fadvise is None or _POSIX_FADV_DONTNEED is None:
        return
    close_fd = False
    try:
        if fd is None:
            fd = os.open(path, os.O_RDONLY)
            close_fd = True
        _posix_fadvise(fd, 0, 0, _POSIX_FADV_DONTNEED)
    except OSError:
        return
    finally:
        if close_fd and fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _advise_dontneed(fd: int, offset: int, length: int) -> None:
    if _posix_fadvise is None or _POSIX_FADV_DONTNEED is None:
        return
    try:
        _posix_fadvise(fd, offset, length, _POSIX_FADV_DONTNEED)
    except OSError:
        return


def compute_file_hash(path: str | Path, algorithm: str = "sha256") -> str:
    """计算文件哈希，用于去重与移动校验。读过的块立刻从页缓存剔除。"""
    h = hashlib.new(algorithm)
    offset = 0
    with open(path, "rb") as f:
        fd = f.fileno()
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
            n = len(chunk)
            _advise_dontneed(fd, offset, n)
            offset += n
            del chunk
    return h.hexdigest()


def _file_id(path: Path) -> Optional[tuple]:
    try:
        st = path.stat()
        return (st.st_dev, st.st_ino)
    except OSError:
        return None


def get_file_size(path: str | Path) -> int:
    return Path(path).stat().st_size


COVER_EXTENSIONS = (".webp", ".jpg", ".jpeg", ".png", ".avif")

DERIVED_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")


def clear_derived_image_variants(
    folder: str, kind_dir: str, entity_id: int, keep_rel: str | None = None
) -> None:
    """清理 derived 下同目录同 id 的其它扩展名图片，保留 keep_rel 指向的新文件。

    头像/横幅的落盘路径是确定性的 {folder}/{kind_dir}/{id}.{ext}，但三个写方
    （站点下载、手动上传、图库裁切）各自可能产出不同扩展名；每次写入后清扫
    全部扩展名变体，避免旧扩展名文件残留成为孤儿并在后续被误读取。
    """
    keep_name = Path(str(keep_rel).replace("\\", "/")).name if keep_rel else ""
    base = settings.derived_dir / folder / kind_dir
    for ext in DERIVED_IMAGE_EXTENSIONS:
        name = f"{entity_id}{ext}"
        if name == keep_name:
            continue
        try:
            (base / name).unlink(missing_ok=True)
        except OSError:
            pass


def confined_derived_file(rel_or_abs: Optional[str | Path]) -> Optional[Path]:
    """只允许读取 derived_dir 内的文件，拒绝绝对路径穿越。"""
    if not rel_or_abs:
        return None
    raw = Path(str(rel_or_abs).replace("\\", "/"))
    root = settings.derived_dir.resolve()
    try:
        if raw.is_absolute():
            p = raw.resolve()
        else:
            p = (root / raw).resolve()
        p.relative_to(root)
    except (OSError, ValueError):
        return None
    return p if p.is_file() else None


def find_sidecar_cover(video_path: str | Path) -> Optional[Path]:
    """查找与视频同名的封面：foo.webp 或 foo.mkv.webp。"""
    video = Path(video_path)
    seen: set[str] = set()
    candidates: list[Path] = []
    for ext in COVER_EXTENSIONS:
        candidates.append(video.with_suffix(ext))
        candidates.append(Path(str(video) + ext))
    # bili-sync 本地封面：{page_name}-poster.jpg
    candidates.append(video.with_name(video.stem + "-poster.jpg"))
    candidates.append(video.with_name(video.stem + "-poster.png"))
    for p in candidates:
        key = str(p).casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def move_to_library(
    db: Session,
    source_path: str | Path,
    destination_rel: str | Path,
    music_video_id: Optional[int] = None,
    move: bool = True,
    source_hash: Optional[str] = None,
) -> tuple[Path, FileMoveLog]:
    """移动或复制文件到正式库，并写日志、做哈希校验。

    source_hash 为调用方已算过的源文件哈希时不再重读源。
    rename 成功（目标与源同一 inode）时不再对目标做第二次全量哈希；
    跨卷 copy+delete 仍会重算。

    返回 (最终绝对路径, FileMoveLog 实例)。
    """
    source = Path(source_path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"源文件不存在: {source}")

    lib_root = settings.library_dir.resolve()
    destination = (settings.library_dir / destination_rel).resolve()
    if not destination.is_relative_to(lib_root):
        raise ValueError("入库路径不能超出正式库目录范围")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_id = _file_id(source)

    # 目标已存在同名文件：若内容与源一致则复用，避免产生重复副本；
    # 仅在内容不一致时附加后缀，防止误覆盖其它视频
    if destination.exists():
        same_file = source.resolve() == destination.resolve()
        size_match = same_file or (
            source.exists()
            and destination.exists()
            and source.stat().st_size == destination.stat().st_size
        )
        hash_before = source_hash or (compute_file_hash(source) if size_match else None)
        hash_after = (
            hash_before
            if same_file and hash_before
            else (compute_file_hash(destination) if size_match else None)
        )
        if hash_before and hash_after and hash_before == hash_after:
            if move and source != destination:
                source.unlink(missing_ok=True)
            log = FileMoveLog(
                music_video_id=music_video_id,
                source_path=str(source),
                destination_path=str(destination),
                status="reused",
                file_hash_before=hash_before,
                file_hash_after=hash_after,
                verified=True,
                message="目标文件与源内容一致，直接复用",
            )
            db.add(log)
            db.flush()
            return destination, log
        stem, suffix = destination.stem, destination.suffix
        i = 1
        while destination.exists():
            destination = destination.with_name(f"{stem}_{i}{suffix}")
            i += 1

    hash_before = source_hash or compute_file_hash(source)
    status = "failed"
    hash_after: Optional[str] = None
    verified = False
    message = ""

    try:
        if move:
            shutil.move(str(source), str(destination))
            action = "moved"
        else:
            shutil.copy2(str(source), str(destination))
            action = "copied"

        renamed = action == "moved" and source_id is not None and _file_id(destination) == source_id
        if renamed:
            hash_after = hash_before
            verified = True
        else:
            hash_after = compute_file_hash(destination)
            verified = hash_after == hash_before
        status = action if verified else "failed"
        if not verified:
            message = "移动前后 hash 不一致"
            if move and destination.exists() and not source.exists():
                try:
                    shutil.move(str(destination), str(source))
                    message += "（已回移源文件）"
                    logger.warning("哈希不一致，已将文件回移至 %s", source)
                except Exception:  # noqa: BLE001
                    logger.exception("哈希不一致且回移失败，正式库遗留: %s", destination)
                    message += "（回移失败，正式库遗留无记录文件）"
    except Exception as e:  # noqa: BLE001
        message = str(e)
        logger.exception("文件移动失败: %s -> %s", source, destination)
        if move and destination.exists() and not source.exists():
            try:
                shutil.move(str(destination), str(source))
                message += "（已回移源文件）"
            except Exception:  # noqa: BLE001
                logger.exception("移动异常且回移失败: %s", destination)

    log = FileMoveLog(
        music_video_id=music_video_id,
        source_path=str(source),
        destination_path=str(destination),
        status=status,
        file_hash_before=hash_before,
        file_hash_after=hash_after,
        verified=verified,
        message=message or None,
    )
    db.add(log)
    db.flush()
    return destination, log


def list_library_dirs() -> list[str]:
    """列出 library_dir 下已有的子目录（相对路径，POSIX 格式）。

    供入库时选择已有目录，避免浏览器端无法浏览 NAS 文件系统。
    """
    root = settings.library_dir
    if not root.exists():
        return []
    dirs: list[str] = []
    for p in sorted(root.rglob("*")):
        if p.is_dir():
            dirs.append(p.relative_to(root).as_posix())
    return dirs


def _sanitize(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    # 去除 Windows / Unix 文件系统不允许的字符
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if c in bad else c for c in name).strip().rstrip(".")
    return cleaned or None


def _find_dir_ci(parent: Path, name: str) -> Optional[str]:
    """在 parent 下查找与 name 大小写不敏感相等的子目录名；未命中返回 None。"""
    want = name.casefold()
    try:
        with os.scandir(parent) as it:
            for entry in it:
                try:
                    if entry.is_dir() and entry.name.casefold() == want:
                        return entry.name
                except OSError:
                    continue
    except OSError:
        return None
    return None


def match_existing_dir(rel: Path) -> Path:
    """把相对路径的每一级目录与正式库已存在的目录做大小写不敏感匹配：
    命中已存在目录则采用磁盘上的实际名称，避免因大小写不同新建重复目录
    （如已有 Twice/ 时，TWICE 的视频归入 Twice/）；未命中的层级保留原样。

    仅调整目录名，不改变层级与文件。正式库不可访问时原样返回。
    """
    root = settings.library_dir
    if not root.is_dir():
        return rel
    resolved = Path()
    current = root
    for part in rel.parts:
        if current.is_dir():
            hit = _find_dir_ci(current, part)
            if hit is not None:
                resolved = resolved / hit
                current = current / hit
                continue
        resolved = resolved / part
        current = current / part
    return resolved
