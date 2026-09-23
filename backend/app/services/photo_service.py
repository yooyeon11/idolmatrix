"""图片文件夹扫描与图库信息流。

本地文件不搬家。扫描是显式操作：把磁盘上的图片/视频写入 photos 索引。
官方/粉丝分区会解析 Instagram sidecar 或 Twitter/X dump txt，按帖子分组展示；
照片墙忽略 txt，按单张排列。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.artist import Artist
from app.models.group import Group
from app.models.photo import (
    PHOTO_OWNER_TYPES,
    PHOTO_PROVIDERS,
    PHOTO_SECTIONS,
    Photo,
    PhotoSource,
)

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".heic",
    ".bmp",
    ".avif",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
SKIP_NAMES = {
    "crawler-index.json",
    "thumbs.db",
    "desktop.ini",
    ".ds_store",
}
MAX_SCAN_FILES = 20_000
MT_SYNC_COOLDOWN_SECONDS = 45
_FILENAME_DT = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}[-:]\d{2}[-:]\d{2})(?:_(\d+))?",
)
_IG_STEM = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[ T_]\d{2}[-.:]\d{2}[-.:]\d{2}(?:_UTC)?)(?:_(\d+))?$",
    re.IGNORECASE,
)
# Twitter/X 导出：`2026-07-11 2075849226597359894 2075849220184264704.jpg`
_X_STEM = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{10,})\s+(\d{10,})$")
_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H-%M-%S",
    "%Y-%m-%d",
)


class PhotoServiceError(ValueError):
    """业务校验失败（路径不存在、分区非法等）。"""


@dataclass
class SidecarPost:
    post_key: str
    published_at: Optional[datetime]
    caption: str
    media_names: list[str]
    directory: Path
    author: Optional[str] = None


@dataclass
class PostTextMeta:
    caption: str
    published_at: Optional[datetime] = None
    author: Optional[str] = None


@dataclass
class ScannedMedia:
    path: Path
    file_name: str
    media_kind: str
    published_at: Optional[datetime] = None
    caption: Optional[str] = None
    post_key: Optional[str] = None
    position_in_post: int = 0
    author: Optional[str] = None


@dataclass
class ScanStats:
    source_id: int
    files_seen: int = 0
    created: int = 0
    updated: int = 0
    restored: int = 0
    removed: int = 0
    posts: int = 0
    elapsed_ms: int = 0
    thumbs_queued: int = 0
    warning: str = ""


def validate_section(section: str) -> str:
    if section not in PHOTO_SECTIONS:
        raise PhotoServiceError("分区仅支持 official / fan / wall")
    return section


def validate_owner_type(owner_type: str) -> str:
    if owner_type not in PHOTO_OWNER_TYPES:
        raise PhotoServiceError("owner_type 仅支持 artist / group")
    return owner_type


def validate_provider(provider: str) -> str:
    text = (provider or "folder").strip() or "folder"
    if text not in PHOTO_PROVIDERS:
        raise PhotoServiceError("来源仅支持 folder / mtphotos")
    return text


def is_mtphotos_source(source: Optional[PhotoSource]) -> bool:
    return bool(source is not None and source.provider == "mtphotos")


def is_mtphotos_photo(photo: Photo) -> bool:
    return is_mtphotos_source(photo.source)


def resolve_owner(db: Session, owner_type: str, owner_id: int):
    owner_type = validate_owner_type(owner_type)
    model = Artist if owner_type == "artist" else Group
    obj = model.get_active(db, owner_id)
    if not obj:
        raise PhotoServiceError("艺人或组合不存在")
    return obj


def resolve_folder(folder_path: str) -> Path:
    raw = (folder_path or "").strip()
    if not raw:
        raise PhotoServiceError("文件夹路径不能为空")
    try:
        path = Path(raw).expanduser().resolve()
    except OSError as e:
        raise PhotoServiceError(f"无法解析文件夹路径：{e}") from e
    if not path.is_dir():
        raise PhotoServiceError("路径不是存在的文件夹")
    return path


def _browse_roots() -> list[Path]:
    """图片文件夹浏览白名单根目录（PHOTO_BROWSE_ROOTS，逗号分隔）。

    resolve() 把符号链接解析成物理路径（如 macOS /var → /private/var），
    保证与浏览路径 resolve() 之后可直接比较。
    """
    roots: list[Path] = []
    for part in (settings.photo_browse_roots or "").split(","):
        text = part.strip()
        if text:
            try:
                roots.append(Path(text).expanduser().resolve())
            except OSError:
                continue
    return roots


def _within_browse_roots(path: Path) -> bool:
    return any(root == path or root in path.parents for root in _browse_roots())


def _browse_entry(folder: Path) -> dict:
    """统计一个目录的直接子目录数与直接图片文件数（不递归，保证浏览速度）。"""
    subfolders = 0
    photos = 0
    try:
        for child in folder.iterdir():
            try:
                if child.is_dir():
                    subfolders += 1
                elif child.suffix.lower() in IMAGE_EXTENSIONS:
                    photos += 1
            except OSError:
                continue
    except OSError:
        pass
    return {
        "name": folder.name or str(folder),
        "path": str(folder),
        "photo_count": photos,
        "subfolder_count": subfolders,
    }


def browse_folder(folder_path: Optional[str]) -> dict:
    """列出白名单内的目录树（绑定本地文件夹时的浏览器数据源）。

    folder_path 为空 → 返回根层（白名单根目录；单根时直接进入该根的子目录）。
    folder_path 非空 → 校验在白名单内后返回其直接子目录。
    """
    roots = [r for r in _browse_roots() if r.is_dir()]
    if not roots:
        raise PhotoServiceError(
            "容器内没有可浏览的目录：请检查挂载，或通过 PHOTO_BROWSE_ROOTS 配置浏览根路径"
        )

    raw = (folder_path or "").strip()
    if not raw:
        # 单根时直接进入该根的子目录，省一次点击
        if len(roots) == 1:
            root = roots[0]
            return {
                "path": str(root),
                "parent": None,
                "entries": [_browse_entry(c) for c in sorted(root.iterdir()) if c.is_dir()],
            }
        return {
            "path": "",
            "parent": None,
            "entries": [
                {"name": str(r), "path": str(r), "photo_count": 0, "subfolder_count": 0}
                for r in roots
            ],
        }

    try:
        path = Path(raw).expanduser().resolve()
    except OSError as e:
        raise PhotoServiceError(f"无法解析文件夹路径：{e}") from e
    if not _within_browse_roots(path):
        raise PhotoServiceError("该路径不在可浏览范围内")
    if not path.is_dir():
        raise PhotoServiceError("路径不是存在的文件夹")

    parent = path.parent
    parent_path = str(parent) if _within_browse_roots(parent) else None
    entries = [_browse_entry(c) for c in sorted(path.iterdir()) if c.is_dir()]
    return {"path": str(path), "parent": parent_path, "entries": entries}


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def instagram_group_parts(file_name: str) -> tuple[Optional[str], int]:
    """Instagram 导出文件名：同一帖 `…_UTC.jpg` / `…_UTC_1.jpg`。"""
    stem = Path(file_name or "").stem
    matched = _IG_STEM.match(stem)
    if not matched:
        return None, 0
    return matched.group(1).replace(" ", "_"), int(matched.group(2) or 0)


def filename_group_parts(file_name: str) -> tuple[Optional[str], int]:
    """按文件名把同一帖归组：Instagram sidecar 序号，或 Twitter/X 推文 ID。"""
    prefix, pos = instagram_group_parts(file_name)
    if prefix:
        return prefix, pos
    stem = Path(file_name or "").stem
    matched = _X_STEM.match(stem)
    if not matched:
        return None, 0
    return f"{matched.group(1)}_{matched.group(2)}", int(matched.group(3))


def _is_twitter_prefix(prefix: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}_\d{10,}$", prefix or ""))


def _json_caption(data: object) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    for key in ("full_text", "content", "text", "caption", "description"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    legacy = data.get("legacy")
    if isinstance(legacy, dict):
        value = legacy.get("full_text")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _read_post_caption_file(path: Path) -> Optional[PostTextMeta]:
    suffix = path.suffix.lower()
    try:
        text = _read_text(path).strip()
    except OSError:
        return None
    if not text:
        return None
    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        caption = _json_caption(data)
        return PostTextMeta(caption=caption) if caption else None
    twitter = parse_twitter_dump_text(text)
    if twitter and (twitter.caption or twitter.author):
        return twitter
    parsed = parse_sidecar_txt(path)
    if parsed and (parsed.caption or parsed.author):
        return PostTextMeta(
            caption=parsed.caption,
            published_at=parsed.published_at,
            author=parsed.author,
        )
    if looks_like_twitter_dump(text):
        return None
    return PostTextMeta(caption=text)


def caption_for_filename_group(directory: Optional[Path], prefix: str) -> Optional[PostTextMeta]:
    """X/IG 文件名成帖后，从同目录 txt/json 取博文和发帖时间。"""
    if directory is None or not prefix:
        return None
    try:
        if not directory.is_dir():
            return None
    except OSError:
        return None
    candidates: list[Path] = []
    if _is_twitter_prefix(prefix):
        date, tweet_id = prefix.split("_", 1)
        candidates.extend(
            [
                directory / f"{date} {tweet_id}.txt",
                directory / f"{tweet_id}.txt",
                directory / f"{date}_{tweet_id}.txt",
                directory / f"{tweet_id}.json",
                directory / f"{date} {tweet_id}.json",
            ]
        )
        try:
            for path in directory.iterdir():
                if path.suffix.lower() in {".txt", ".json"} and tweet_id in path.stem:
                    candidates.append(path)
        except OSError:
            pass
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            continue
        meta = _read_post_caption_file(path)
        if meta and (meta.caption or meta.author):
            return meta
    return None


def _relative_post_key(root: Path, path: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
        return str(rel.with_suffix("")).replace("\\", "/")
    except ValueError:
        return path.stem


def parse_datetime(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    text = raw.strip().replace("T", " ")
    text = re.sub(r"\s*(UTC|GMT|Z)$", "", text, flags=re.I).strip()
    text = re.sub(r"\s*[+-]\d{2}:?\d{2}$", "", text).strip()
    if not text:
        return None
    candidates = [text]
    if len(text) >= 19:
        candidates.append(text[:19])
    for item in candidates:
        for fmt in _DT_FORMATS:
            try:
                return datetime.strptime(item, fmt)
            except ValueError:
                continue
    return None


def published_at_from_name(name: str) -> Optional[datetime]:
    stem = Path(name).stem
    m = _FILENAME_DT.match(stem)
    if m:
        return parse_datetime(m.group(1))
    x = _X_STEM.match(stem)
    if x:
        return parse_datetime(x.group(1))
    prefix, _pos = instagram_group_parts(name)
    if not prefix:
        return None
    raw = prefix
    if raw.upper().endswith("_UTC"):
        raw = raw[:-4]
    raw = raw.replace("T", " ")
    if "_" in raw[:13]:
        raw = raw.replace("_", " ", 1)
    return parse_datetime(raw)


def _after_label(line: str, labels: Iterable[str]) -> Optional[str]:
    """取「标签：值」行的值；不匹配返回 None。支持中英文冒号。"""
    stripped = line.strip()
    for label in labels:
        if stripped.startswith(label):
            return stripped[len(label) :].lstrip("：:").strip()
    return None


def parse_sidecar_txt(path: Path) -> Optional[SidecarPost]:
    """解析 Instagram 导出 sidecar：发布时间 / 发布者 / 博文原文 / 媒体文件列表。"""
    try:
        text = _read_text(path)
    except OSError as e:
        logger.warning("读取 sidecar 失败 %s: %s", path, e)
        return None

    published_raw = ""
    author_raw = ""
    caption_lines: list[str] = []
    media_names: list[str] = []
    mode: Optional[str] = None
    saw_meta = False

    for line in text.splitlines():
        published = _after_label(line, ("发布时间",))
        if published is not None:
            published_raw = published
            saw_meta = True
            mode = None
            continue
        author = _after_label(line, ("发布者",))
        if author is not None:
            author_raw = author
            saw_meta = True
            mode = None
            continue
        caption_head = _after_label(line, ("博文原文",))
        if caption_head is not None:
            saw_meta = True
            caption_lines = [caption_head] if caption_head else []
            mode = "caption"
            continue
        media_head = _after_label(line, ("媒体文件列表",))
        if media_head is not None:
            saw_meta = True
            mode = "media"
            if media_head:
                media_names.append(Path(media_head).name)
            continue
        if mode == "caption":
            caption_lines.append(line.rstrip())
        elif mode == "media":
            name = line.strip()
            if name:
                media_names.append(Path(name).name)

    if not saw_meta:
        return None
    caption = "\n".join(caption_lines).strip()
    return SidecarPost(
        post_key=path.stem,
        published_at=parse_datetime(published_raw) or published_at_from_name(path.name),
        caption=caption,
        media_names=media_names,
        directory=path.parent,
        author=author_raw or None,
    )


_TWITTER_DASH = re.compile(r"^-{4,}$")
_TWITTER_EQ = re.compile(r"^={4,}$")
_TWITTER_STOP = re.compile(
    r"^(stats|source|tags|media|user info|tweet)\s*:?\s*$",
    re.IGNORECASE,
)


def looks_like_twitter_dump(text: str) -> bool:
    head = text[:2500]
    if re.search(r"(?im)^\s*USER INFO\s*$", head) and re.search(r"(?im)^\s*TWEET\s*$", head):
        return True
    if re.search(r"(?im)^\s*Screen Name\s*:", head) and re.search(
        r"(?im)^\s*(Created At|Tweet ID)\s*:", head
    ):
        return True
    return False


def _format_twitter_author(name: str, screen: str) -> str:
    handle = (screen or "").strip()
    if handle and not handle.startswith("@"):
        handle = "@" + handle
    display = (name or "").strip()
    handle_bare = handle[1:] if handle.startswith("@") else handle
    if display.startswith("@"):
        display_bare = display[1:]
    else:
        display_bare = display
    if handle and display and display_bare.lower() != handle_bare.lower():
        return f"{name.strip()} ({handle})"
    return handle or display


def parse_twitter_dump_text(text: str) -> Optional[PostTextMeta]:
    """解析 X dump：USER INFO / TWEET / Content 虚线块，抽出发帖人和正文。

    发布者优先级：显式「发布者：」行 > USER INFO 的 Name / Screen Name。
    作者单独存进 author，不再拼进博文首行。
    """
    if not text or not looks_like_twitter_dump(text):
        return None
    name = ""
    screen = ""
    created_raw = ""
    explicit_author = ""
    content_lines: list[str] = []
    mode: Optional[str] = None
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = raw.strip()
        author_head = _after_label(stripped, ("发布者",))
        if author_head:
            explicit_author = author_head
            continue
        if _TWITTER_EQ.match(stripped):
            if mode == "content":
                break
            continue
        if mode == "content":
            if _TWITTER_DASH.match(stripped):
                if content_lines:
                    break
                continue
            if _TWITTER_STOP.match(stripped) or re.match(
                r"(?i)^(stats|source|tags)\s*:", stripped
            ):
                break
            content_lines.append(raw.rstrip())
            continue
        if not stripped:
            continue
        key, sep, val = stripped.partition(":")
        if not sep:
            continue
        key_n = key.strip().lower()
        val = val.strip()
        if key_n == "name" and val and not name:
            name = val
        elif key_n == "screen name" and val:
            screen = val
        elif key_n in {"created at", "created_at"} and val:
            created_raw = val
        elif key_n == "content":
            mode = "content"
            if val:
                content_lines.append(val)
    body = "\n".join(content_lines).strip()
    author = explicit_author.strip() or _format_twitter_author(name, screen)
    if not body and not author:
        return None
    return PostTextMeta(
        caption=body,
        published_at=parse_datetime(created_raw),
        author=author or None,
    )


def _media_kind(path: Path) -> Optional[str]:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def _iter_files(folder: Path, recursive: bool) -> Iterable[Path]:
    try:
        iterator = folder.rglob("*") if recursive else folder.iterdir()
        for p in iterator:
            try:
                if p.is_file():
                    yield p
            except OSError:
                continue
    except OSError as e:
        raise PhotoServiceError(f"读取文件夹失败：{e}") from e


def collect_media(folder: Path, recursive: bool, section: str) -> tuple[list[ScannedMedia], int]:
    """扫描磁盘，返回媒体列表和识别到的帖子数。"""
    files: list[Path] = []
    sidecars: list[Path] = []
    for p in _iter_files(folder, recursive):
        name = p.name
        if name.lower() in SKIP_NAMES or name.startswith("."):
            continue
        if p.suffix.lower() == ".txt":
            sidecars.append(p)
            continue
        if _media_kind(p):
            files.append(p)
        if len(files) + len(sidecars) > MAX_SCAN_FILES * 2:
            raise PhotoServiceError(f"文件过多，单次扫描上限 {MAX_SCAN_FILES} 个媒体文件")

    if len(files) > MAX_SCAN_FILES:
        raise PhotoServiceError(f"文件过多，单次扫描上限 {MAX_SCAN_FILES} 个媒体文件")

    by_dir_name: dict[tuple[str, str], Path] = {}
    by_name: dict[str, list[Path]] = {}
    for p in files:
        by_dir_name[(str(p.parent), p.name.lower())] = p
        by_name.setdefault(p.name.lower(), []).append(p)

    assigned: dict[str, ScannedMedia] = {}
    posts = 0
    use_sidecar = section != "wall"

    def _resolve_listed(post: SidecarPost, listed: str) -> Optional[Path]:
        key = listed.lower()
        same_dir = by_dir_name.get((str(post.directory), key))
        if same_dir is not None:
            return same_dir
        hits = by_name.get(key) or []
        return hits[0] if hits else None

    if use_sidecar:
        for txt in sidecars:
            post = parse_sidecar_txt(txt)
            if post is None:
                continue
            posts += 1
            listed = post.media_names
            if not listed:
                # 按 stem_01.jpg 惯例兜底
                prefix = post.post_key.lower()
                listed = [
                    p.name
                    for p in files
                    if p.parent == post.directory
                    and p.stem.lower().startswith(prefix)
                ]
                listed.sort()
            post_key = _relative_post_key(folder, txt)
            for idx, listed_name in enumerate(listed):
                media_path = _resolve_listed(post, listed_name)
                if media_path is None:
                    continue
                kind = _media_kind(media_path)
                if not kind:
                    continue
                resolved = str(media_path.resolve())
                assigned[resolved] = ScannedMedia(
                    path=media_path,
                    file_name=media_path.name,
                    media_kind=kind,
                    published_at=post.published_at or published_at_from_name(media_path.name),
                    caption=post.caption or None,
                    post_key=post_key,
                    position_in_post=idx,
                    author=post.author or None,
                )

    leftover_buckets: dict[tuple[str, str], list[tuple[int, Path]]] = {}
    for p in files:
        resolved = str(p.resolve())
        if resolved in assigned:
            continue
        prefix, pos = filename_group_parts(p.name)
        if not prefix:
            continue
        try:
            rel_dir = str(p.parent.resolve().relative_to(folder.resolve())).replace("\\", "/")
            if rel_dir == ".":
                rel_dir = ""
        except ValueError:
            rel_dir = p.parent.name
        leftover_buckets.setdefault((rel_dir, prefix), []).append((pos, p))
    for (rel_dir, prefix), group in leftover_buckets.items():
        if len(group) < 2 and not _is_twitter_prefix(prefix):
            continue
        group.sort(key=lambda x: (x[0], x[1].name))
        post_key = f"{rel_dir}/{prefix}" if rel_dir else prefix
        text_meta = caption_for_filename_group(group[0][1].parent, prefix)
        caption = text_meta.caption if text_meta else None
        published = text_meta.published_at if text_meta else None
        author = text_meta.author if text_meta else None
        posts += 1
        for idx, (_pos, media_path) in enumerate(group):
            kind = _media_kind(media_path)
            if not kind:
                continue
            assigned[str(media_path.resolve())] = ScannedMedia(
                path=media_path,
                file_name=media_path.name,
                media_kind=kind,
                published_at=published or published_at_from_name(media_path.name),
                caption=caption,
                post_key=post_key,
                position_in_post=idx,
                author=author,
            )

    items: list[ScannedMedia] = []
    for p in files:
        resolved = str(p.resolve())
        if resolved in assigned:
            items.append(assigned[resolved])
            continue
        kind = _media_kind(p)
        if not kind:
            continue
        items.append(
            ScannedMedia(
                path=p,
                file_name=p.name,
                media_kind=kind,
                published_at=published_at_from_name(p.name),
            )
        )
    items.sort(key=lambda x: (x.published_at or datetime.min, x.file_name), reverse=True)
    return items, posts


def bind_source(
    db: Session,
    *,
    owner_type: str,
    owner_id: int,
    section: str,
    folder_path: str = "",
    recursive: bool = False,
    provider: str = "folder",
    external_id: Optional[str] = None,
    mt_kind: Optional[str] = None,
) -> PhotoSource:
    resolve_owner(db, owner_type, owner_id)
    section = validate_section(section)
    provider = validate_provider(provider)
    if provider == "mtphotos":
        kind = (mt_kind or "").strip().lower()
        if not kind:
            if str(folder_path or "").startswith("mtphotos://folder/"):
                kind = "folder"
            else:
                kind = "album"
        if kind == "folder":
            return bind_mtphotos_folder(
                db,
                owner_type=owner_type,
                owner_id=owner_id,
                section=section,
                folder_id=external_id or folder_path,
                recursive=recursive,
            )
        return bind_mtphotos_album(
            db,
            owner_type=owner_type,
            owner_id=owner_id,
            section=section,
            album_id=external_id or folder_path,
        )
    folder = resolve_folder(folder_path)
    if not _within_browse_roots(folder):
        raise PhotoServiceError("该路径不在可浏览范围内")
    stored = str(folder)
    existing = db.scalar(
        select(PhotoSource).where(
            PhotoSource.owner_type == owner_type,
            PhotoSource.owner_id == owner_id,
            PhotoSource.folder_path == stored,
        )
    )
    if existing:
        raise PhotoServiceError("该文件夹已绑定")
    source = PhotoSource(
        owner_type=owner_type,
        owner_id=owner_id,
        section=section,
        folder_path=stored,
        provider="folder",
        recursive=bool(recursive),
    )
    db.add(source)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise PhotoServiceError("该文件夹已绑定") from e
    db.refresh(source)
    return source


def bind_mtphotos_album(
    db: Session,
    *,
    owner_type: str,
    owner_id: int,
    section: str,
    album_id: str | int,
) -> PhotoSource:
    from app.services import mtphotos

    try:
        aid = int(str(album_id).strip())
    except (TypeError, ValueError) as e:
        raise PhotoServiceError("请选择 MT Photos 相册") from e
    if aid <= 0:
        raise PhotoServiceError("请选择 MT Photos 相册")
    try:
        base_url, api_key = mtphotos.configured_from_db(db)
        albums = mtphotos.list_albums(base_url, api_key)
    except mtphotos.MtPhotosError as e:
        raise PhotoServiceError(str(e)) from e
    album = next((a for a in albums if a.id == aid), None)
    if album is None:
        raise PhotoServiceError("找不到这个 MT Photos 相册")
    stored = mtphotos.album_source_path(aid)
    existing = db.scalar(
        select(PhotoSource).where(
            PhotoSource.owner_type == owner_type,
            PhotoSource.owner_id == owner_id,
            PhotoSource.folder_path == stored,
        )
    )
    if existing:
        raise PhotoServiceError("该相册已绑定")
    source = PhotoSource(
        owner_type=owner_type,
        owner_id=owner_id,
        section=section,
        folder_path=stored,
        provider="mtphotos",
        external_id=str(aid),
        recursive=False,
    )
    db.add(source)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise PhotoServiceError("该相册已绑定") from e
    db.refresh(source)
    try:
        sync_mtphotos_source(db, source, force=True)
    except mtphotos.MtPhotosError as e:
        logger.warning("绑定后首次同步失败 source=%s: %s", source.id, e)
    return source


def bind_mtphotos_folder(
    db: Session,
    *,
    owner_type: str,
    owner_id: int,
    section: str,
    folder_id: str | int,
    recursive: bool = True,
) -> PhotoSource:
    from app.services import mtphotos

    if section == "wall":
        raise PhotoServiceError("照片墙请绑定 MT Photos 相册")
    try:
        fid = int(str(folder_id).strip())
    except (TypeError, ValueError) as e:
        raise PhotoServiceError("请选择 MT Photos 文件夹") from e
    if fid <= 0:
        raise PhotoServiceError("请选择 MT Photos 文件夹")
    try:
        base_url, api_key = mtphotos.configured_from_db(db)
        info = mtphotos.folder_info(base_url, api_key, fid)
    except mtphotos.MtPhotosError as e:
        raise PhotoServiceError(str(e)) from e
    stored = mtphotos.folder_source_path(fid)
    existing = db.scalar(
        select(PhotoSource).where(
            PhotoSource.owner_type == owner_type,
            PhotoSource.owner_id == owner_id,
            PhotoSource.folder_path == stored,
        )
    )
    if existing:
        raise PhotoServiceError("该文件夹已绑定")
    source = PhotoSource(
        owner_type=owner_type,
        owner_id=owner_id,
        section=section,
        folder_path=stored,
        provider="mtphotos",
        external_id=str(fid),
        recursive=bool(recursive),
    )
    db.add(source)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise PhotoServiceError("该文件夹已绑定") from e
    db.refresh(source)
    try:
        sync_mtphotos_source(db, source, force=True)
    except mtphotos.MtPhotosError as e:
        logger.warning("绑定后首次同步失败 source=%s: %s", source.id, e)
    return source


UPLOAD_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_FILES = 30


def _safe_upload_name(name: str) -> str:
    raw = Path(name or "upload").name.strip() or "upload"
    raw = _UNSAFE_NAME.sub("_", raw)
    raw = raw.replace("..", "_")
    if len(raw) > 180:
        stem, ext = Path(raw).stem[:160], Path(raw).suffix[:20]
        raw = f"{stem}{ext}"
    return raw or "upload"


def upload_folder(owner_type: str, owner_id: int) -> Path:
    return (settings.photo_upload_dir / owner_type / str(int(owner_id))).resolve()


def ensure_upload_source(db: Session, owner_type: str, owner_id: int) -> PhotoSource:
    resolve_owner(db, owner_type, owner_id)
    folder = upload_folder(owner_type, owner_id)
    folder.mkdir(parents=True, exist_ok=True)
    stored = str(folder)
    existing = db.scalar(
        select(PhotoSource).where(
            PhotoSource.owner_type == owner_type,
            PhotoSource.owner_id == owner_id,
            PhotoSource.folder_path == stored,
        )
    )
    if existing:
        if existing.section != "wall":
            existing.section = "wall"
            db.commit()
        return existing
    source = PhotoSource(
        owner_type=owner_type,
        owner_id=owner_id,
        section="wall",
        folder_path=stored,
        recursive=False,
    )
    db.add(source)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(PhotoSource).where(
                PhotoSource.owner_type == owner_type,
                PhotoSource.owner_id == owner_id,
                PhotoSource.folder_path == stored,
            )
        )
        if existing:
            return existing
        raise PhotoServiceError("无法创建上传目录索引")
    db.refresh(source)
    return source


def add_uploaded_photo(
    db: Session,
    source: PhotoSource,
    *,
    original_name: str,
    data: bytes,
) -> Photo:
    if is_mtphotos_source(source):
        raise PhotoServiceError("照片墙已接入 MT Photos，请在 MT Photos 中上传")
    owner_mt = db.scalar(
        select(PhotoSource.id).where(
            PhotoSource.owner_type == source.owner_type,
            PhotoSource.owner_id == source.owner_id,
            PhotoSource.section == "wall",
            PhotoSource.provider == "mtphotos",
        )
    )
    if owner_mt:
        raise PhotoServiceError("照片墙已接入 MT Photos，请在 MT Photos 中上传")
    if not data:
        raise PhotoServiceError("上传文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise PhotoServiceError(f"文件过大，上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB")
    name = _safe_upload_name(original_name)
    ext = Path(name).suffix.lower()
    if ext not in UPLOAD_EXTENSIONS:
        raise PhotoServiceError("仅支持常见图片和短视频（jpg/png/webp/gif/mp4 等）")
    folder = Path(source.folder_path)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    if dest.exists():
        stem = Path(name).stem
        n = 2
        while True:
            cand = folder / f"{stem}_{n}{ext}"
            if not cand.exists():
                dest = cand
                name = dest.name
                break
            n += 1
            if n > 1000:
                raise PhotoServiceError("无法生成唯一文件名")
    dest.write_bytes(data)
    stored = str(dest.resolve())
    existing = db.scalar(
        select(Photo).where(Photo.source_id == source.id, Photo.file_path == stored)
    )
    if existing:
        if existing.deleted_at is not None:
            existing.restore()
            db.commit()
            db.refresh(existing)
        return existing
    photo = Photo(
        source_id=source.id,
        section="wall",
        file_path=stored,
        file_name=name,
        media_kind="video" if ext in VIDEO_EXTENSIONS else "image",
        published_at=datetime.utcnow(),
        caption=None,
        post_key=None,
        position_in_post=0,
    )
    db.add(photo)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(Photo).where(Photo.source_id == source.id, Photo.file_path == stored)
        )
        if existing:
            return existing
        raise PhotoServiceError("写入照片索引失败")
    db.refresh(photo)
    from app.services import photo_thumb

    photo_thumb.enqueue_photo_thumbs([photo.id])
    return photo


def delete_source(db: Session, source: PhotoSource) -> None:
    from app.services import photo_thumb

    photo_ids = [p.id for p in source.photos]
    db.delete(source)
    db.commit()
    photo_thumb.remove_thumbs(photo_ids)


def _source_folder(photo: Photo) -> Path:
    source = photo.source
    if source is None:
        raise PhotoServiceError("照片来源不存在")
    try:
        return Path(source.folder_path).resolve()
    except OSError as e:
        raise PhotoServiceError("无法解析来源文件夹") from e


def _unlink_inside(folder: Path, path: Path) -> bool:
    """只删除位于绑定文件夹内的文件。文件已不在则视为成功。"""
    try:
        resolved = path.resolve()
        resolved.relative_to(folder)
    except (OSError, ValueError) as e:
        raise PhotoServiceError("照片路径不在绑定文件夹内") from e
    if not resolved.exists():
        return False
    if not resolved.is_file():
        raise PhotoServiceError("目标不是文件，已拒绝删除")
    try:
        resolved.unlink()
    except OSError as e:
        raise PhotoServiceError(f"无法删除文件：{e}") from e
    return True


def _delete_sidecar(folder: Path, post_key: str, hint_dir: Optional[Path]) -> bool:
    if not post_key:
        return False
    candidates: list[Path] = []
    if hint_dir is not None:
        candidates.append(hint_dir / f"{post_key}.txt")
    candidates.append(folder / f"{post_key}.txt")
    seen: set[str] = set()
    deleted = False
    for raw in candidates:
        try:
            resolved = raw.resolve()
            resolved.relative_to(folder)
        except (OSError, ValueError):
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_file():
            try:
                resolved.unlink()
                deleted = True
            except OSError as e:
                logger.warning("删除帖子原文失败 %s: %s", resolved, e)
                raise PhotoServiceError(f"无法删除帖子原文：{e}") from e
    return deleted


def _purge_photo_row(db: Session, photo: Photo) -> dict:
    folder = _source_folder(photo)
    file_deleted = False
    try:
        raw = Path(photo.file_path)
        if photo.file_path:
            file_deleted = _unlink_inside(folder, raw)
    except PhotoServiceError:
        raise
    except OSError as e:
        raise PhotoServiceError(f"无法删除文件：{e}") from e
    photo_id = photo.id
    db.delete(photo)
    db.flush()
    return {"id": photo_id, "file_deleted": file_deleted, "folder": folder}


def delete_photo(db: Session, photo: Photo) -> dict:
    """彻底删除一张照片：磁盘文件 + 缩略图 + 索引。下次扫描不会恢复。"""
    if is_mtphotos_photo(photo):
        raise PhotoServiceError("请在 MT Photos 中管理照片")
    from app.services import photo_thumb

    source_id = photo.source_id
    post_key = photo.post_key
    hint_dir = Path(photo.file_path).parent if photo.file_path else None
    purged = _purge_photo_row(db, photo)
    sidecar_deleted = False
    if post_key:
        remaining = db.scalar(
            select(func.count())
            .select_from(Photo)
            .where(Photo.source_id == source_id, Photo.post_key == post_key)
        ) or 0
        if remaining == 0:
            sidecar_deleted = _delete_sidecar(purged["folder"], post_key, hint_dir)
    db.commit()
    photo_thumb.remove_thumbs([purged["id"]])
    return {
        "deleted": 1,
        "files_deleted": int(purged["file_deleted"]) + int(sidecar_deleted),
    }


def delete_post(db: Session, photo: Photo) -> dict:
    """彻底删除一个帖子：帖内全部媒体、sidecar txt、缩略图和索引。"""
    if is_mtphotos_photo(photo):
        raise PhotoServiceError("请在 MT Photos 中管理照片")
    from app.services import photo_thumb

    post_key = (photo.post_key or "").strip()
    if not post_key:
        return delete_photo(db, photo)

    rows = list(
        db.scalars(
            select(Photo)
            .options(selectinload(Photo.source))
            .where(Photo.source_id == photo.source_id, Photo.post_key == post_key)
        ).all()
    )
    if not rows:
        raise PhotoServiceError("帖子不存在")
    folder = _source_folder(rows[0])
    hint_dir = Path(rows[0].file_path).parent if rows[0].file_path else None
    ids: list[int] = []
    files_deleted = 0
    for row in rows:
        purged = _purge_photo_row(db, row)
        ids.append(purged["id"])
        files_deleted += int(purged["file_deleted"])
    if _delete_sidecar(folder, post_key, hint_dir):
        files_deleted += 1
    db.commit()
    photo_thumb.remove_thumbs(ids)
    return {"deleted": len(ids), "files_deleted": files_deleted}


def _mt_file_id(photo: Photo) -> Optional[int]:
    from app.services import mtphotos

    parsed = mtphotos.parse_photo_path(photo.file_path)
    return parsed[1] if parsed else None


def _merge_mt_file(item, detail):
    from app.services import mtphotos

    md5 = (detail.md5 if detail and detail.md5 else item.md5) or item.md5
    name = (
        (detail.file_name if detail and detail.file_name else "")
        or item.file_name
        or f"{item.id}.jpg"
    )
    ftype = (detail.file_type if detail else "") or item.file_type
    published = (detail.token_at if detail else None) or item.token_at
    if published is None and item.day:
        try:
            published = datetime.strptime(item.day[:10], "%Y-%m-%d")
        except ValueError:
            published = None
    disk = (detail.disk_path if detail and detail.disk_path else "") or item.disk_path
    if detail and detail.file_name:
        item.file_name = detail.file_name
    if disk:
        item.disk_path = disk
    item.file_name = name
    item.md5 = md5
    item.file_type = ftype
    item.token_at = published
    kind = mtphotos.media_kind_of(ftype, name)
    return md5, name, kind, published, disk


def _common_existing_root(paths: list[Path]) -> Optional[Path]:
    dirs: list[Path] = []
    for raw in paths:
        try:
            d = raw.expanduser()
            d = d if d.is_dir() else d.parent
            if d.is_dir():
                dirs.append(d.resolve())
        except OSError:
            continue
    if not dirs:
        return None
    parts_list = [d.parts for d in dirs]
    common: list[str] = []
    for items in zip(*parts_list):
        if len(set(items)) != 1:
            break
        common.append(items[0])
    if len(common) < 2:
        return None
    try:
        root = Path(*common)
    except (TypeError, ValueError):
        return None
    if root.parent == root:
        return None
    return root if root.is_dir() else None


def _mt_path_prefixes(disk_prefix: str) -> list[str]:
    """用户填写的前缀 + MT 容器里常见挂载点（/photo、/upload）。"""
    items: list[str] = []
    for raw in (disk_prefix, "/photo", "/upload"):
        prefix = (raw or "").strip().replace("\\", "/").rstrip("/")
        if prefix and prefix not in items:
            items.append(prefix)
    return items


def rewrite_mt_disk_path(
    raw: str, disk_prefix: str = "", mount_path: str = "/data/mt-photos"
) -> str:
    """把 MT 报的路径改成本容器能读的挂载路径。

    MT compose 常见：`/home/idol/photo:/photo`，所以文件路径是 /photo/...，
    不是宿主机的 /home/idol/photo/...。
    """
    text = (raw or "").strip().replace("\\", "/")
    if not text:
        return ""
    mount = (mount_path or "/data/mt-photos").strip().replace("\\", "/").rstrip("/") or "/data/mt-photos"
    if text == mount or text.startswith(mount + "/"):
        return text
    for prefix in _mt_path_prefixes(disk_prefix):
        if text.lower() == prefix.lower() or text.lower().startswith(prefix.lower() + "/"):
            rest = text[len(prefix) :].lstrip("/")
            return f"{mount}/{rest}" if rest else mount
    return text


def resolve_mt_disk_path(
    raw: str, disk_prefix: str = "", mount_path: str = "/data/mt-photos"
) -> str:
    """优先返回实际存在的路径，便于 sidecar txt 能读到。"""
    mapped = rewrite_mt_disk_path(raw, disk_prefix, mount_path)
    original = (raw or "").strip().replace("\\", "/")
    mount = (mount_path or "/data/mt-photos").strip().replace("\\", "/").rstrip("/") or "/data/mt-photos"
    parts = [p for p in original.split("/") if p and p not in (".", "..") and not p.endswith(":")]
    stripped = parts[1:] if parts and parts[0].lower() in {"photo", "upload", "config"} else parts
    candidates = [mapped, original, mount]
    for seq in (stripped, parts):
        for n in range(1, min(6, len(seq) + 1)):
            candidates.append(f"{mount}/{'/'.join(seq[-n:])}")
    seen: set[str] = set()
    for item in candidates:
        if not item or item in seen:
            continue
        seen.add(item)
        try:
            path = Path(item)
            if path.exists():
                return str(path)
        except OSError:
            continue
    return mapped or original


def _disk_root_for_mt(
    files: list, folder_disk_path: str = "", recursive: bool = True
) -> Optional[Path]:
    if folder_disk_path:
        try:
            root = Path(folder_disk_path).expanduser()
            if root.is_dir():
                return root.resolve()
        except OSError:
            pass
    return _common_existing_root(
        [Path(f.disk_path) for f in files if getattr(f, "disk_path", "")]
    )


def _apply_post_meta(files: list, section: str, disk_root: Optional[Path], recursive: bool) -> dict[int, ScannedMedia]:
    if section == "wall" or not files:
        return {}
    meta: dict[int, ScannedMedia] = {}
    if disk_root is not None and disk_root.is_dir():
        scanned, _posts = collect_media(disk_root, recursive, section)
        by_resolved: dict[str, ScannedMedia] = {}
        by_dir_name: dict[tuple[str, str], ScannedMedia] = {}
        by_name: dict[str, list[ScannedMedia]] = {}
        for item in scanned:
            resolved = str(item.path.resolve())
            by_resolved[resolved] = item
            by_dir_name[(str(item.path.parent.resolve()), item.file_name.lower())] = item
            by_name.setdefault(item.file_name.lower(), []).append(item)
        for f in files:
            hit = None
            if f.disk_path:
                try:
                    disk = Path(f.disk_path).expanduser().resolve()
                    hit = by_resolved.get(str(disk))
                    if hit is None:
                        hit = by_dir_name.get((str(disk.parent), (f.file_name or disk.name).lower()))
                except OSError:
                    hit = None
            if hit is None:
                hits = by_name.get((f.file_name or "").lower()) or []
                if len(hits) == 1:
                    hit = hits[0]
            if hit is not None:
                meta[f.id] = hit
    leftover = [f for f in files if f.id not in meta]
    buckets: dict[tuple[str, str], list[tuple[int, object]]] = {}
    for f in leftover:
        prefix, pos = filename_group_parts(f.file_name)
        if not prefix:
            continue
        rel_dir = ""
        if f.disk_path:
            try:
                parent = Path(f.disk_path).expanduser().resolve().parent
                if disk_root is not None and disk_root.is_dir():
                    rel = str(parent.relative_to(disk_root.resolve())).replace("\\", "/")
                    rel_dir = "" if rel == "." else rel
                else:
                    rel_dir = parent.name
            except (OSError, ValueError):
                rel_dir = Path(f.disk_path).parent.name
        buckets.setdefault((rel_dir, prefix), []).append((pos, f))
    for (rel_dir, prefix), group in buckets.items():
        group.sort(key=lambda x: (x[0], x[1].file_name))
        post_key = f"{rel_dir}/{prefix}" if rel_dir else prefix
        caption_dir = None
        sample = group[0][1]
        if getattr(sample, "disk_path", ""):
            caption_dir = Path(sample.disk_path).parent
        elif disk_root is not None:
            caption_dir = disk_root / rel_dir if rel_dir else disk_root
        text_meta = caption_for_filename_group(caption_dir, prefix)
        caption = text_meta.caption if text_meta else None
        published = text_meta.published_at if text_meta else None
        author = text_meta.author if text_meta else None
        for idx, (_pos, f) in enumerate(group):
            meta[f.id] = ScannedMedia(
                path=Path(f.disk_path or f.file_name),
                file_name=f.file_name,
                media_kind="image",
                published_at=published
                or published_at_from_name(f.file_name)
                or f.token_at,
                caption=caption,
                post_key=post_key,
                position_in_post=idx,
                author=author,
            )
    return meta


def sync_mtphotos_source(db: Session, source: PhotoSource, *, force: bool = False) -> ScanStats:
    """把 MT Photos 相册/文件夹同步成本地索引，不下载原图。"""
    from app.services import mtphotos

    started = time.perf_counter()
    stats = ScanStats(source_id=source.id)
    if not is_mtphotos_source(source):
        raise PhotoServiceError("不是 MT Photos 来源")
    if not force and source.last_scanned_at is not None:
        age = (datetime.utcnow() - source.last_scanned_at).total_seconds()
        missing_dates = db.scalar(
            select(func.count())
            .select_from(Photo)
            .where(
                Photo.source_id == source.id,
                Photo.active_filter(),
                Photo.published_at.is_(None),
            )
        ) or 0
        if age < MT_SYNC_COOLDOWN_SECONDS and not missing_dates:
            stats.files_seen = source.last_scan_files
            stats.posts = source.last_scan_posts
            stats.elapsed_ms = 0
            return stats
    existing_rows = db.scalars(select(Photo).where(Photo.source_id == source.id)).all()
    folder_disk = ""
    try:
        from app.services.app_settings import read_all as read_app_settings

        mt_cfg = read_app_settings(db).mtphotos
        disk_prefix = mt_cfg.disk_prefix or ""
        mount_path = mt_cfg.mount_path or "/data/mt-photos"
        base_url, api_key = mtphotos.configured_from_db(db)
        kind, sid = mtphotos.parse_mt_source(source.folder_path, source.external_id)
        if kind == "folder":
            info = mtphotos.folder_info(base_url, api_key, sid)
            folder_disk = resolve_mt_disk_path(info.path or "", disk_prefix, mount_path)
            files = mtphotos.list_folder_files(
                base_url, api_key, sid, recursive=bool(source.recursive)
            )
            details = mtphotos.files_by_ids(base_url, api_key, [f.id for f in files])
        else:
            files = mtphotos.list_album_files(base_url, api_key, sid)
            details = mtphotos.files_by_ids(
                base_url, api_key, [f.id for f in files], album_id=sid
            )
    except mtphotos.MtPhotosError as e:
        raise PhotoServiceError(str(e)) from e

    if not files:
        active_n = sum(1 for row in existing_rows if row.deleted_at is None)
        if active_n:
            logger.warning(
                "MT 来源返回 0 个文件，已保留现有 %s 张，避免把索引清空",
                active_n,
            )
            stats.files_seen = active_n
            stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return stats
    by_file_id: dict[int, Photo] = {}
    for row in existing_rows:
        fid = _mt_file_id(row)
        if fid is not None:
            by_file_id[fid] = row
    seen_ids: set[int] = set()
    stats.files_seen = len(files)
    container_id = sid
    for item in files:
        detail = details.get(item.id)
        md5, name, kind, published, disk = _merge_mt_file(item, detail)
        if not md5:
            continue
        item.md5 = md5
        item.file_name = name
        item.token_at = published
        if disk:
            item.disk_path = resolve_mt_disk_path(disk, disk_prefix, mount_path)

    disk_root = _disk_root_for_mt(files, folder_disk, bool(source.recursive))
    post_meta = _apply_post_meta(
        files, source.section, disk_root, recursive=True if disk_root else bool(source.recursive)
    )
    post_keys = {m.post_key for m in post_meta.values() if m.post_key}

    for item in files:
        detail = details.get(item.id)
        md5, name, kind, published, _disk = _merge_mt_file(item, detail)
        if not md5:
            continue
        scanned = post_meta.get(item.id)
        name_date = published_at_from_name(name)
        if scanned and scanned.published_at:
            published = scanned.published_at
        elif name_date:
            published = name_date
        stored = mtphotos.photo_file_path(container_id, item.id, md5)
        seen_ids.add(item.id)
        caption = scanned.caption if scanned else None
        author = scanned.author if scanned else None
        post_key = scanned.post_key if scanned else None
        position = scanned.position_in_post if scanned else 0
        row = by_file_id.get(item.id)
        if row is None:
            db.add(
                Photo(
                    source_id=source.id,
                    section=source.section,
                    file_path=stored,
                    file_name=name,
                    media_kind=kind,
                    published_at=published,
                    caption=caption,
                    author=author,
                    post_key=post_key,
                    position_in_post=position,
                )
            )
            stats.created += 1
            continue
        row.section = source.section
        row.file_path = stored
        row.file_name = name
        row.media_kind = kind
        row.published_at = published
        row.post_key = post_key
        row.position_in_post = position
        if caption:
            row.caption = caption
        if author:
            row.author = author
        if row.deleted_at is not None:
            row.restore()
            stats.restored += 1
        else:
            stats.updated += 1

    vanished: list[int] = []
    for row in existing_rows:
        fid = _mt_file_id(row)
        if fid in seen_ids or row.deleted_at is not None:
            continue
        row.soft_delete()
        vanished.append(row.id)
        stats.removed += 1

    source.last_scanned_at = datetime.utcnow()
    source.last_scan_files = stats.files_seen
    source.last_scan_posts = len(post_keys)
    stats.posts = len(post_keys)
    if source.section != "wall" and disk_root is None:
        stats.warning = (
            "容器读不到照片目录，无法解析 txt 成帖。"
            "MT compose 若是 /home/idol/photo:/photo，前缀请填 /photo（不是宿主机路径），"
            "并把同一份目录挂到 /data/mt-photos。"
        )
        logger.warning(
            "MT sidecar 目录不可读 source=%s folder_disk=%s",
            source.id,
            folder_disk,
        )
    elif source.section != "wall" and stats.posts == 0:
        stats.warning = (
            f"已读到目录 {disk_root}，但没有识别到 sidecar txt 或 Instagram 文件名，仍按单张显示。"
        )
    db.commit()
    stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
    if vanished:
        from app.services import photo_thumb

        photo_thumb.remove_thumbs(vanished)
    return stats


def _enrich_mtphotos_tags(db: Session, photos: list[Photo]) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from app.services import mtphotos

    pending = [
        p
        for p in photos
        if is_mtphotos_photo(p)
        and not (isinstance(p.analysis, dict) and p.analysis.get("mt_meta_version") == 3)
    ]
    if not pending:
        return
    try:
        base_url, api_key = mtphotos.configured_from_db(db)
    except mtphotos.MtPhotosError:
        return

    def _one(photo: Photo) -> tuple[int, str, list[str]]:
        from app.services import mtphotos as mt

        parsed = mt.parse_photo_path(photo.file_path)
        if parsed is None:
            return photo.id, "", []
        _album, fid, md5 = parsed
        names: list[str] = []
        caption = ""
        try:
            names = mt.file_tags(base_url, api_key, fid)
        except mt.MtPhotosError as e:
            logger.info("读取 MT 用户标签失败 photo=%s: %s", photo.id, e)
        try:
            caption, llm_tags = mt.file_llm_insight(base_url, api_key, fid, md5)
            for tag in llm_tags:
                if tag not in names:
                    names.append(tag)
        except mt.MtPhotosError as e:
            logger.info("读取 MT LLM 描述失败 photo=%s: %s", photo.id, e)
        return photo.id, caption, names

    fetched: dict[int, tuple[str, list[str]]] = {}
    workers = min(6, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, p) for p in pending]
        for fut in as_completed(futs):
            pid, caption, names = fut.result()
            fetched[pid] = (caption, names)
    changed = False
    for photo in pending:
        caption, names = fetched.get(photo.id, ("", []))
        analysis = dict(photo.analysis) if isinstance(photo.analysis, dict) else {}
        analysis["tags"] = names
        if caption:
            analysis["caption_zh"] = caption[:500]
        analysis["mt_tags_checked"] = True
        analysis["mt_meta_version"] = 3
        photo.analysis = analysis
        if caption and (photo.section == "wall" or not photo.caption):
            photo.caption = caption[:2000]
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(photo, "analysis")
        changed = True
    if changed:
        db.commit()


def scan_source(db: Session, source: PhotoSource) -> ScanStats:
    if is_mtphotos_source(source):
        return sync_mtphotos_source(db, source, force=True)
    started = time.perf_counter()
    folder = resolve_folder(source.folder_path)
    media, posts = collect_media(folder, bool(source.recursive), source.section)

    existing_rows = db.scalars(select(Photo).where(Photo.source_id == source.id)).all()
    by_path = {row.file_path: row for row in existing_rows}
    seen_paths: set[str] = set()
    stats = ScanStats(source_id=source.id, files_seen=len(media), posts=posts)

    for item in media:
        stored = str(item.path.resolve())
        seen_paths.add(stored)
        row = by_path.get(stored)
        if row is None:
            db.add(
                Photo(
                    source_id=source.id,
                    section=source.section,
                    file_path=stored,
                    file_name=item.file_name,
                    media_kind=item.media_kind,
                    published_at=item.published_at,
                    caption=item.caption,
                    author=item.author,
                    post_key=item.post_key,
                    position_in_post=item.position_in_post,
                )
            )
            stats.created += 1
            continue
        row.section = source.section
        row.file_name = item.file_name
        row.media_kind = item.media_kind
        row.published_at = item.published_at
        row.caption = item.caption
        row.author = item.author
        row.post_key = item.post_key
        row.position_in_post = item.position_in_post
        if row.deleted_at is not None:
            row.restore()
            stats.restored += 1
        else:
            stats.updated += 1

    vanished: list[int] = []
    for row in existing_rows:
        if row.file_path in seen_paths or row.deleted_at is not None:
            continue
        row.soft_delete()
        vanished.append(row.id)
        stats.removed += 1

    source.last_scanned_at = datetime.utcnow()
    source.last_scan_files = stats.files_seen
    source.last_scan_posts = stats.posts
    db.commit()
    stats.elapsed_ms = int((time.perf_counter() - started) * 1000)

    from app.services import photo_thumb

    if vanished:
        photo_thumb.remove_thumbs(vanished)
    active_ids = list(
        db.scalars(
            select(Photo.id).where(Photo.source_id == source.id, Photo.active_filter())
        ).all()
    )
    stats.thumbs_queued = photo_thumb.enqueue_photo_thumbs(active_ids)
    return stats


def list_sources(db: Session, owner_type: str, owner_id: int) -> list[tuple[PhotoSource, int]]:
    validate_owner_type(owner_type)
    sources = db.scalars(
        select(PhotoSource)
        .where(
            PhotoSource.owner_type == owner_type,
            PhotoSource.owner_id == owner_id,
        )
        .order_by(PhotoSource.section.asc(), PhotoSource.id.asc())
    ).all()
    if not sources:
        return []
    ids = [s.id for s in sources]
    counts = dict(
        db.execute(
            select(Photo.source_id, func.count())
            .where(Photo.source_id.in_(ids), Photo.active_filter())
            .group_by(Photo.source_id)
        ).all()
    )
    return [(s, int(counts.get(s.id, 0))) for s in sources]


def source_to_read(source: PhotoSource, photo_count: int = 0, label: Optional[str] = None) -> dict:
    return {
        "id": source.id,
        "owner_type": source.owner_type,
        "owner_id": source.owner_id,
        "section": source.section,
        "folder_path": source.folder_path,
        "provider": source.provider or "folder",
        "external_id": source.external_id,
        "recursive": source.recursive,
        "last_scanned_at": source.last_scanned_at,
        "last_scan_files": source.last_scan_files,
        "last_scan_posts": source.last_scan_posts,
        "photo_count": photo_count,
        "label": label,
        "created_at": source.created_at,
        "updated_at": source.updated_at,
    }


def source_labels(db: Session, sources: list[PhotoSource]) -> dict[int, str]:
    from app.services import mtphotos

    needed = [s for s in sources if is_mtphotos_source(s)]
    if not needed:
        return {}
    names: dict[int, str] = {}
    try:
        base_url, api_key = mtphotos.configured_from_db(db)
        albums = {a.id: a.name for a in mtphotos.list_albums(base_url, api_key)}
    except mtphotos.MtPhotosError:
        albums = {}
        base_url = api_key = ""
    for source in needed:
        try:
            kind, sid = mtphotos.parse_mt_source(source.folder_path, source.external_id)
        except mtphotos.MtPhotosError:
            continue
        if kind == "folder":
            label = f"MT 文件夹 #{sid}"
            if base_url:
                try:
                    info = mtphotos.folder_info(base_url, api_key, sid)
                    label = info.name or info.path or label
                except mtphotos.MtPhotosError:
                    pass
            names[source.id] = label
            continue
        names[source.id] = albums.get(sid) or f"MT 相册 #{sid}"
    return names


def _feed_item_sort_key(item: dict):
    return (
        item["published_at"] or datetime.min,
        item["post_key"] or item["photos"][0].file_name,
    )


def _feed_item_name_key(item: dict):
    return min((p.file_name or "").lower() for p in item["photos"])


def photo_order_clauses(sort_by: str = "date", sort_dir: str = "desc"):
    by = (sort_by or "date").strip().lower()
    if by not in ("date", "name"):
        by = "date"
    descending = (sort_dir or "desc").strip().lower() != "asc"
    col = func.lower(Photo.file_name) if by == "name" else Photo.published_at
    if descending:
        return (col.desc().nulls_last(), Photo.id.desc())
    return (col.asc().nulls_last(), Photo.id.asc())


def _sort_feed_items(items: list[dict], sort_by: str, sort_dir: str) -> list[dict]:
    """feed item 排序：date 按发布时间（无日期的排最后），name 按首图文件名。"""
    by = (sort_by or "date").strip().lower()
    descending = (sort_dir or "desc").strip().lower() != "asc"
    if by == "name":
        items.sort(key=_feed_item_name_key, reverse=descending)
        return items
    dated = [it for it in items if it["published_at"]]
    undated = [it for it in items if not it["published_at"]]
    dated.sort(key=_feed_item_sort_key, reverse=descending)
    return dated + undated


def build_feed_items(
    photos: list[Photo],
    section: str,
    sort_by: str = "date",
    sort_dir: str = "desc",
) -> list[dict]:
    if section == "wall":
        items = []
        for p in photos:
            items.append(
                {
                    "kind": "photo",
                    "post_key": None,
                    "caption": p.caption,
                    "author": p.author,
                    "published_at": p.published_at,
                    "photos": [p],
                }
            )
        return items

    grouped: dict[str, list[Photo]] = {}
    ungrouped: list[Photo] = []
    for p in photos:
        if p.post_key:
            grouped.setdefault(p.post_key, []).append(p)
        else:
            ungrouped.append(p)

    items: list[dict] = []
    for key, plist in grouped.items():
        plist.sort(key=lambda x: (x.position_in_post, x.file_name, x.id))
        pub = next((x.published_at for x in plist if x.published_at), None)
        cap = next((x.caption for x in plist if x.caption), None)
        author = next((x.author for x in plist if x.author), None)
        items.append(
            {
                "kind": "post",
                "post_key": key,
                "caption": cap,
                "author": author,
                "published_at": pub,
                "photos": plist,
            }
        )
    for p in ungrouped:
        items.append(
            {
                "kind": "photo",
                "post_key": None,
                "caption": p.caption,
                "author": p.author,
                "published_at": p.published_at,
                "photos": [p],
            }
        )
    return _sort_feed_items(items, sort_by, sort_dir)


def _mt_tag_filter_file_ids(
    db: Session, sources: list[PhotoSource], tags: Optional[list[str]]
) -> Optional[set[int]]:
    """MT 照片墙按标签筛选。用户标签走 searchV2，LLM 标签走 llmTagFiles。None 表示改走本地 analysis。"""
    from app.services import mtphotos

    if not tags:
        return None
    mt_sources = [s for s in sources if is_mtphotos_source(s)]
    if not mt_sources or any(not is_mtphotos_source(s) for s in sources):
        return None
    try:
        base_url, api_key = mtphotos.configured_from_db(db)
        catalog = {t.name: t.id for t in mtphotos.list_tags(base_url, api_key)}
        matched: Optional[set[int]] = None
        used_remote = False
        for name in tags:
            chunk: set[int]
            tid = catalog.get(name)
            if tid:
                chunk = mtphotos.search_files_by_tag_ids(base_url, api_key, [tid])
                used_remote = True
            else:
                chunk = mtphotos.search_files_by_llm_tag(base_url, api_key, name)
                if chunk:
                    used_remote = True
            if matched is None:
                matched = set(chunk)
            else:
                matched &= chunk
        if not used_remote:
            return None
        return matched or set()
    except mtphotos.MtPhotosError as e:
        raise PhotoServiceError(str(e)) from e


def _timeline_from_photos(photos: list[Photo]) -> list[dict]:
    from collections import Counter

    from app.services import mtphotos

    counts: Counter[str] = Counter()
    for photo in photos:
        counts[mtphotos.day_key(photo.published_at)] += 1
    days = sorted((d for d in counts if d), reverse=True)
    out = [{"date": d, "count": int(counts[d])} for d in days]
    if counts.get(""):
        out.append({"date": "", "count": int(counts[""])})
    return out


def _like_pattern(term: str) -> str:
    """LIKE 模糊匹配 pattern，转义 % _ \\ 通配符。"""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _ascii_escaped(term: str) -> str:
    """把非 ASCII 字符转成 json.dumps ensure_ascii 的 \\uXXXX 形态。"""
    return "".join(c if ord(c) < 128 else "\\u%04x" % ord(c) for c in term)


def search_filter_clauses(
    q: Optional[str],
    author: Optional[str],
    *,
    expand_posts: bool,
    scope: Optional[list] = None,
) -> list:
    """搜索/发布者筛选条件。

    q：模糊匹配博文（caption）/ 发布者（author）/ 文件名 / 标签（analysis JSON 序列化文本），一个框全搜。
    author：精确匹配发布者。
    expand_posts=True（官方/粉丝帖子流）：帖子级语义——帖内任一照片命中则整帖返回，
      通过 post_key 子查询扩展（子查询限定 scope，避免跨 owner 的相同 post_key 误命中）。
    expand_posts=False（照片墙/收藏夹按单张展示）：直接行级匹配。
    """
    from sqlalchemy import String, and_, cast, or_

    conds = []
    if author:
        conds.append(Photo.author == author)
    if q:
        like = _like_pattern(q)
        # SQLite 默认 ensure_ascii 存 JSON，中文实际以 \uXXXX 转义文本落库，
        # 标签搜索需同时匹配原文与转义两种形态
        like_ascii = _like_pattern(_ascii_escaped(q))
        analysis_match = or_(
            cast(Photo.analysis, String).like(like, escape="\\"),
            cast(Photo.analysis, String).like(like_ascii, escape="\\"),
        )
        conds.append(
            or_(
                Photo.caption.like(like, escape="\\"),
                Photo.author.like(like, escape="\\"),
                Photo.file_name.like(like, escape="\\"),
                # 标签存于 analysis JSON（含 caption_zh 摘要）
                analysis_match,
            )
        )
    if not conds:
        return []
    cond = and_(*conds)
    if not expand_posts:
        return [cond]
    base = [cond, Photo.active_filter()]
    if scope:
        base.extend(scope)
    matched_keys = select(Photo.post_key).where(*base, Photo.post_key.isnot(None))
    return [
        or_(
            and_(Photo.post_key.isnot(None), Photo.post_key.in_(matched_keys)),
            and_(Photo.post_key.is_(None), cond),
        )
    ]


def owner_feed(
    db: Session,
    *,
    owner_type: str,
    owner_id: int,
    section: str,
    page: int,
    page_size: int,
    scene: Optional[str] = None,
    shot: Optional[str] = None,
    shoes_type: Optional[str] = None,
    hosiery_present: Optional[str] = None,
    analyzed: Optional[bool] = None,
    tags: Optional[list[str]] = None,
    on_date: Optional[str] = None,
    q: Optional[str] = None,
    author: Optional[str] = None,
    sort_by: str = "date",
    sort_dir: str = "desc",
) -> dict:
    validate_owner_type(owner_type)
    section = validate_section(section)
    sources = db.scalars(
        select(PhotoSource).where(
            PhotoSource.owner_type == owner_type,
            PhotoSource.owner_id == owner_id,
            PhotoSource.section == section,
        )
    ).all()
    for source in sources:
        if is_mtphotos_source(source):
            try:
                sync_mtphotos_source(db, source)
            except PhotoServiceError as e:
                logger.warning("同步 MT Photos 失败 source=%s: %s", source.id, e)
                if source.last_scanned_at is None:
                    raise
            db.refresh(source)
    source_ids = [s.id for s in sources]
    last_scanned = max((s.last_scanned_at for s in sources if s.last_scanned_at), default=None)
    from app.services import photo_thumb

    for source in sources:
        if not is_mtphotos_source(source):
            photo_thumb.maybe_backfill_source(source.id)
    empty = {
        "items": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
        "source_count": len(sources),
        "photo_count": 0,
        "last_scanned_at": last_scanned,
        "readonly": any(is_mtphotos_source(s) for s in sources),
        "timeline": [],
    }
    if not source_ids:
        empty["source_count"] = 0
        empty["readonly"] = False
        return empty

    from app.services.photo_analysis import analysis_filter_clauses

    stmt = (
        select(Photo)
        .options(selectinload(Photo.source))
        .where(Photo.source_id.in_(source_ids), Photo.active_filter())
        .order_by(*photo_order_clauses(sort_by, sort_dir))
    )
    for clause in search_filter_clauses(
        (q or "").strip() or None,
        (author or "").strip() or None,
        expand_posts=section != "wall",
        scope=[Photo.source_id.in_(source_ids)],
    ):
        stmt = stmt.where(clause)
    mt_tag_ids = _mt_tag_filter_file_ids(db, list(sources), tags)
    local_tags = tags
    if mt_tag_ids is not None:
        local_tags = None
        allowed = []
        for photo in db.scalars(
            select(Photo)
            .options(selectinload(Photo.source))
            .where(Photo.source_id.in_(source_ids), Photo.active_filter())
        ).all():
            fid = _mt_file_id(photo)
            if fid is not None and fid in mt_tag_ids:
                allowed.append(photo.id)
        if not allowed:
            return empty
        stmt = stmt.where(Photo.id.in_(allowed))
    for clause in analysis_filter_clauses(
        scene=scene,
        shot=shot,
        shoes_type=shoes_type,
        hosiery_present=hosiery_present,
        analyzed=analyzed,
        tags=local_tags,
    ):
        stmt = stmt.where(clause)
    photos = list(db.scalars(stmt).all())
    timeline = _timeline_from_photos(photos)
    if on_date is not None:
        from app.services import mtphotos

        want = on_date.strip()
        if want in ("none", "unknown"):
            want = ""
        photos = [p for p in photos if mtphotos.day_key(p.published_at) == want]
    items = build_feed_items(photos, section, sort_by, sort_dir)
    total = len(items)
    offset = max(page - 1, 0) * page_size
    page_items = items[offset : offset + page_size]
    _enrich_mtphotos_tags(db, [p for it in page_items for p in it["photos"]])
    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "source_count": len(sources),
        "photo_count": len(photos),
        "last_scanned_at": last_scanned,
        "readonly": any(is_mtphotos_source(s) for s in sources),
        "timeline": timeline,
    }


def aggregate_authors(photos: list[Photo]) -> list[dict]:
    """发布者聚合（照片数计数，发布者多的排前面）。"""
    counts: dict[str, int] = {}
    for p in photos:
        name = (p.author or "").strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return [
        {"author": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    ]


def owner_filter_options(
    db: Session,
    *,
    owner_type: str,
    owner_id: int,
    section: str,
) -> dict:
    """当前分区全部照片的筛选选项：自由标签 + 发布者（不受筛选影响）。"""
    from app.services.photo_analysis import aggregate_tags

    validate_owner_type(owner_type)
    section = validate_section(section)
    sources = db.scalars(
        select(PhotoSource).where(
            PhotoSource.owner_type == owner_type,
            PhotoSource.owner_id == owner_id,
            PhotoSource.section == section,
        )
    ).all()
    empty = {"options": [], "authors": []}
    if not sources:
        return empty
    if sources and all(is_mtphotos_source(s) for s in sources):
        from app.services import mtphotos

        try:
            base_url, api_key = mtphotos.configured_from_db(db)
            merged: list[dict] = []
            seen: set[str] = set()
            for tag in mtphotos.list_tags(base_url, api_key) + mtphotos.list_llm_tags(
                base_url, api_key
            ):
                if tag.name in seen:
                    continue
                seen.add(tag.name)
                merged.append({"tag": tag.name, "count": 0})
            if merged:
                return {
                    "options": merged,
                    "authors": aggregate_authors(_section_photos(db, sources)),
                }
        except mtphotos.MtPhotosError as e:
            logger.info("读取 MT 标签列表失败: %s", e)
            # 退回已缓存到 analysis 的标签
    photos = _section_photos(db, sources)
    return {
        "options": aggregate_tags(photos),
        "authors": aggregate_authors(photos),
    }


def _section_photos(db: Session, sources: list[PhotoSource]) -> list[Photo]:
    source_ids = [s.id for s in sources]
    return list(
        db.scalars(
            select(Photo)
            .options(selectinload(Photo.source))
            .where(Photo.source_id.in_(source_ids), Photo.active_filter())
        ).all()
    )


def resolve_photo_file(photo: Photo) -> Path:
    """只根据数据库记录的路径取文件，并校验仍位于来源文件夹内。"""
    if photo.is_deleted:
        raise PhotoServiceError("照片不存在")
    if is_mtphotos_photo(photo):
        raise PhotoServiceError("MT Photos 照片请走远程代理")
    raw = Path(photo.file_path)
    try:
        path = raw.resolve()
    except OSError as e:
        raise PhotoServiceError("照片文件不可用") from e
    if not path.is_file():
        raise PhotoServiceError("照片文件不存在")
    source = photo.source
    if source is None:
        raise PhotoServiceError("照片来源不存在")
    try:
        folder = Path(source.folder_path).resolve()
        path.relative_to(folder)
    except (OSError, ValueError) as e:
        raise PhotoServiceError("照片路径不在绑定文件夹内") from e
    return path


def media_type_of_name(name: str) -> str:
    return media_type_of(Path(name or "file.jpg"))


def fetch_mtphotos_original(db: Session, photo: Photo) -> tuple[bytes, str]:
    from app.services import mtphotos

    parsed = mtphotos.parse_photo_path(photo.file_path)
    if parsed is None:
        raise PhotoServiceError("无效的 MT Photos 照片")
    _album_id, file_id, md5 = parsed
    try:
        base_url, api_key = mtphotos.configured_from_db(db)
        data, ctype = mtphotos.fetch_original(
            base_url, api_key, file_id, md5, is_video=photo.media_kind == "video"
        )
    except mtphotos.MtPhotosError as e:
        raise PhotoServiceError(str(e)) from e
    if not ctype or ctype == "application/octet-stream":
        ctype = media_type_of_name(photo.file_name)
    return data, ctype


def fetch_mtphotos_thumb(db: Session, photo: Photo) -> bytes:
    from app.services import mtphotos

    parsed = mtphotos.parse_photo_path(photo.file_path)
    if parsed is None:
        raise PhotoServiceError("无效的 MT Photos 照片")
    _album_id, _file_id, md5 = parsed
    try:
        base_url, api_key = mtphotos.configured_from_db(db)
        data, _ctype = mtphotos.fetch_thumb(
            base_url, api_key, md5, is_video=photo.media_kind == "video"
        )
    except mtphotos.MtPhotosError as e:
        raise PhotoServiceError(str(e)) from e
    return data


def media_type_of(path: Path) -> str:
    ext = path.suffix.lower()
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".avif": "image/avif",
        ".heic": "image/heic",
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
    }
    return mapping.get(ext, "application/octet-stream")
