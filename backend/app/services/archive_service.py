"""下载存档（yt-dlp archive 兼容）：记录「我们下载过的平台视频 id」。

目的：即使下载好的文件后来被移走 / 删除 / 尚未入库，也不会把同一支视频再下一次。

文件位置：`<incoming>/download-archive.txt`
- 跟媒体盘走：本机是 `storage/incoming/`，NAS 上是 `/data/incoming/`，同一卷不会丢
- 待整理扫描只认视频后缀（`VIDEO_EXTENSIONS`），这个 .txt 不会被当成待整理条目

格式与 `yt-dlp --download-archive` 完全一致，每行 `<extractor> <id>`：
    youtube pqRKy6R8xKg

⚠ 与 Stacher 的 `~/.stacher/default_archive.txt` **不共用**（两边各自追加，避免并发写同一文件）。
   需要合并时把行拼进来即可 —— 解析规则相同，注释/空行会被忽略。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from app.core.config import settings

ARCHIVE_FILENAME = "download-archive.txt"
DEFAULT_EXTRACTOR = "youtube"

# (mtime_ns, size) -> ids；文件一变就重读，几千行的解析成本 < 10ms
_cache: Optional[tuple[tuple[int, int], set[str]]] = None
_lock = threading.Lock()


def archive_path() -> Path:
    return settings.incoming_dir / ARCHIVE_FILENAME


def _stamp(p: Path) -> tuple[int, int]:
    st = p.stat()
    return (st.st_mtime_ns, st.st_size)


def _parse_ids(p: Path) -> set[str]:
    ids: set[str] = set()
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) != 2:
                    continue
                extractor, vid = parts
                if extractor.lower() == DEFAULT_EXTRACTOR:
                    ids.add(vid)
    except OSError:
        return set()
    return ids


def _load() -> set[str]:
    """读取并缓存；文件不存在/读失败一律返回空集（不阻断下载流程）。"""
    global _cache
    p = archive_path()
    try:
        stamp = _stamp(p)
    except OSError:
        _cache = None
        return set()
    if _cache is not None and _cache[0] == stamp:
        return _cache[1]
    ids = _parse_ids(p)
    _cache = (stamp, ids)
    return ids


def is_archived(video_id: Optional[str]) -> bool:
    if not video_id:
        return False
    with _lock:
        return video_id in _load()


def count() -> int:
    with _lock:
        return len(_load())


def append(video_id: Optional[str], extractor: str = DEFAULT_EXTRACTOR) -> bool:
    """追加一条记录；已存在则不写（幂等）。返回是否真的写入了。"""
    if not video_id:
        return False
    global _cache
    with _lock:
        ids = _load()
        if video_id in ids:
            return False
        p = archive_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8", newline="\n") as f:
                f.write(f"{extractor} {video_id}\n")
        except OSError:
            return False
        try:
            _cache = (_stamp(p), ids | {video_id})
        except OSError:
            _cache = None
        return True
