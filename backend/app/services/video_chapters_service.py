"""视频章节（实验性，只读推导）：从文件内嵌章节 / 简介时间轴解析。

优先级：容器内嵌章节（MKV Chapters 等，结构化可信）> 简介「MM:SS 标题」时间轴。
来源数据（文件、简介文本）不变则结果不变 —— 进程内按 (mv_id, mtime, size) 缓存，
不落库；手动编辑章节留待后续阶段（届时再加持久化）。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.media.ffmpeg import FFmpegError
from app.media.ffprobe import ProbeError, probe_chapters
from app.services.library_service import resolve_video_abs_path

logger = logging.getLogger(__name__)

# 简介时间轴行:「0:00 开场」/「01:02:03 Title」/「[03:24] 舞蹈」/「3:24 - 高音」等
_CHAPTER_LINE_RE = re.compile(
    r"^\s*(?:[-–—*•>#]+\s*)?\[?(?P<h>\d{1,2}):(?P<m>\d{1,2}):(?P<s>\d{2})\]?"
    r"(?:\s*[-–—~～:：>]+?\s*)?(?P<title>.+?)\s*$"
)
# 无小时位:「0:00 开场」
_CHAPTER_LINE_MS_RE = re.compile(
    r"^\s*(?:[-–—*•>#]+\s*)?\[?(?P<m>\d{1,3}):(?P<s>\d{2})\]?"
    r"(?:\s*[-–—~～:：>]+?\s*)?(?P<title>.+?)\s*$"
)

# 简介时间轴的最低可信条数:少于该数时视为「碰巧带时间戳的普通行」(如文件名里的 22:10)
_MIN_DESC_CHAPTERS = 3

# 进程内缓存:同文件(mtime/size 不变)只推导一次
_CACHE: Dict[tuple, List[Dict[str, Any]]] = {}
_CACHE_MAX = 200


def _parse_ts(match: re.Match) -> Optional[float]:
    """时间戳 → 秒;分/秒段超界(如 9:99)视为非时间戳返回 None。"""
    gd = match.groupdict()
    minutes = int(gd.get("m") or 0)
    seconds = int(gd.get("s") or 0)
    if minutes > 99 or seconds > 59:
        return None
    total = minutes * 60 + seconds
    if gd.get("h"):
        total += int(gd["h"]) * 3600
    return float(total)


def parse_description_chapters(description: str, duration: Optional[float]) -> List[Dict[str, Any]]:
    """从简介文本解析「时间戳 标题」时间轴。

    - 兼容库内简介的 {BR} 占位与真实换行;
    - 至少解析出 _MIN_DESC_CHAPTERS 条才认为是章节列表(挡住文件名里 22:10 这类假时间);
    - 按时间排序去重;超出时长的条目丢弃(时长未知时不裁)。
    """
    if not description:
        return []
    text = description.replace("{BR}", "\n")
    found: List[Dict[str, Any]] = []
    seen_times: set[float] = set()
    for line in text.split("\n"):
        m = _CHAPTER_LINE_RE.match(line) or _CHAPTER_LINE_MS_RE.match(line)
        if not m:
            continue
        t = _parse_ts(m)
        if t is None:
            continue
        title = (m.group("title") or "").strip()
        if not title or t in seen_times:
            continue
        seen_times.add(t)
        found.append({"time": t, "title": title})
    if len(found) < _MIN_DESC_CHAPTERS:
        return []
    found.sort(key=lambda c: c["time"])
    if duration and duration > 0:
        found = [c for c in found if c["time"] < duration]
    return found


def _clamp_and_sort(chapters: List[Dict[str, Any]], duration: Optional[float]) -> List[Dict[str, Any]]:
    chapters.sort(key=lambda c: c["time"])
    if duration and duration > 0:
        chapters = [c for c in chapters if 0 <= c["time"] < duration]
    out: List[Dict[str, Any]] = []
    seen: set[float] = set()
    for c in chapters:
        if c["time"] in seen:
            continue
        seen.add(c["time"])
        out.append(c)
    return out


def _embedded_chapters(video: Path) -> List[Dict[str, Any]]:
    try:
        raw = probe_chapters(video)
    except (ProbeError, FFmpegError) as e:
        logger.info("内嵌章节读取失败(忽略): %s", e)
        return []
    out: List[Dict[str, Any]] = []
    for i, c in enumerate(raw):
        try:
            start = float(c.get("start_time") or 0)
        except (TypeError, ValueError):
            continue
        title = ((c.get("tags") or {}).get("title") or "").strip() or f"章节 {i + 1}"
        out.append({"time": start, "title": title, "source": "embedded"})
    return out


def get_chapters(mv) -> List[Dict[str, Any]]:
    """推导某视频的章节列表(内嵌优先,简介时间轴兜底),带进程内缓存。"""
    video = resolve_video_abs_path(mv)
    # 缓存键除文件指纹外必须包含简介/时长：简介时间轴是兜底来源，而元数据
    # 编辑不改文件 mtime/size —— 只按文件键控会让「先看后补简介」的视频
    # 永远命中旧的空结果，章节看起来像没生效。
    desc = mv.description or ""
    meta_key = f"{len(desc)}:{hash(desc)}:{mv.duration or 0}"
    key: Optional[tuple] = None
    if video is not None and video.is_file():
        st = video.stat()
        key = (mv.id, int(st.st_mtime), st.st_size, meta_key)
    if key is not None and key in _CACHE:
        return _CACHE[key]

    duration = float(mv.duration) if mv.duration else None
    chapters: List[Dict[str, Any]] = []

    # ① 容器内嵌章节(结构化,可信)
    if video is not None and video.is_file():
        chapters.extend(_embedded_chapters(video))

    # ② 简介时间轴(无内嵌时)
    if not chapters:
        chapters.extend(parse_description_chapters(mv.description or "", duration))
        for i, c in enumerate(chapters):
            c["source"] = "description"
            if not c.get("title"):
                c["title"] = f"章节 {i + 1}"

    chapters = _clamp_and_sort(chapters, duration)

    if key is not None:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = chapters
    return chapters
