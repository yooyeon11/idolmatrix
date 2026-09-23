"""Bilibili 元数据解析：NFO（bili-sync / Bili23-Downloader）与 Bili23 json。

将两种产物归一化为与 yt-dlp info.json 相同形状的 dict（INFO_KEYS 子集），
供 read_incoming_info / match-hints / AI 上下文等下游直接消费：

- title / description(plot) / uploader(actor role) / uploader_id(actor name=UID)
- id(BV 号) / webpage_url / upload_date(premiered→YYYYMMDD)
- extractor="bilibili"，附带 bili_up_mid / bili_tags / bili_premiered 扩展键
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 艺人候选括号：【宋昕冉】 / [Irene] / （李知恩）
_BRACKET_RE = re.compile(r"[【\[(]([^【\[\]()】\]]{1,40})[】\])]")
# 标题尾部日期：20260822 / 2026.08.22 / 2026-08-22
_TAIL_DATE_RE = re.compile(r"(20\d{2})[.\-/年]?(\d{1,2})[.\-/月]?(\d{1,2})日?\s*$")


def is_bilibili_info(info: dict[str, Any]) -> bool:
    """判断 info dict 是否来自 Bilibili 解析器。"""
    return info.get("extractor") == "bilibili"


def parse_bilibili_nfo(nfo_path: Path) -> Optional[dict[str, Any]]:
    """解析 Kodi movie 风格 NFO（bili-sync / Bili23 通用结构）。"""
    try:
        root = ET.parse(nfo_path).getroot()
    except (ET.ParseError, OSError) as e:
        logger.warning("NFO 解析失败 %s: %s", nfo_path, e)
        return None

    info: dict[str, Any] = {"extractor": "bilibili"}

    def _text(tag: str) -> str:
        node = root.find(tag)
        return (node.text or "").strip() if node is not None else ""

    title = _text("title")
    if not title:
        return None
    info["title"] = title

    plot = _text("plot")
    if plot:
        info["description"] = plot
    outline = _text("outline")
    if outline and "description" not in info:
        info["description"] = outline

    # 演员：name=B站UID，role=up主昵称（bili-sync）；Bili23 相同结构
    actor = root.find("actor")
    if actor is not None:
        uid = (actor.findtext("name") or "").strip()
        role = (actor.findtext("role") or "").strip()
        if role:
            info["uploader"] = role
        if uid and uid.isdigit():
            info["uploader_id"] = uid
            info["bili_up_mid"] = uid

    premiered = _text("premiered")
    if premiered:
        info["premiered"] = premiered
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", premiered)
        if m:
            info["upload_date"] = "".join(m.groups())

    # 唯一标识：<uniqueid type="bilibili">BV…</uniqueid>
    for uid_node in root.findall("uniqueid"):
        utype = (uid_node.get("type") or "").strip().lower()
        val = (uid_node.text or "").strip()
        if not val:
            continue
        if utype == "bilibili" or val.startswith("BV"):
            info["id"] = val
            info["webpage_url"] = f"https://www.bilibili.com/video/{val}/"
            break
    else:
        for node in root.findall("uniqueid"):
            val = (node.text or "").strip()
            if val:
                info["id"] = val
                info["webpage_url"] = f"https://www.bilibili.com/video/{val}/"
                break

    # 标签（Bili23 会写入 tag 节点）
    tags = [
        (t.text or "").strip()
        for t in root.findall("tag")
        if (t.text or "").strip()
    ]
    if tags:
        info["bili_tags"] = tags

    return info


def parse_bilibili_json(json_path: Path) -> Optional[dict[str, Any]]:
    """解析 Bili23-Downloader 的 json 元数据（原始解析信息）。

    字段名宽松匹配：title / desc / uploader.name / bvid / pubdate / tags 等。
    """
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Bili23 json 解析失败 %s: %s", json_path, e)
        return None
    if not isinstance(data, dict):
        return None

    info: dict[str, Any] = {"extractor": "bilibili"}

    title = data.get("title") or data.get("fulltitle")
    if not isinstance(title, str) or not title.strip():
        return None
    info["title"] = title.strip()

    desc = data.get("desc") or data.get("description")
    if isinstance(desc, str) and desc.strip():
        info["description"] = desc.strip()

    upper = data.get("upper") or data.get("uploader")
    if isinstance(upper, dict):
        name = upper.get("name") or upper.get("uname")
        mid = upper.get("mid")
        if isinstance(name, str) and name.strip():
            info["uploader"] = name.strip()
        if isinstance(mid, int):
            info["uploader_id"] = str(mid)
            info["bili_up_mid"] = str(mid)
    elif isinstance(upper, str) and upper.strip():
        info["uploader"] = upper.strip()

    bvid = data.get("bvid") or data.get("id")
    if isinstance(bvid, str) and bvid.strip():
        info["id"] = bvid.strip()
        info["webpage_url"] = f"https://www.bilibili.com/video/{bvid.strip()}/"

    # 发布时间：pubdate（unix 秒）或 pubdate_str / premiered
    pub_ts = data.get("pubdate") or data.get("pubtime") or data.get("ctime")
    if isinstance(pub_ts, (int, float)) and pub_ts > 0:
        from datetime import datetime

        dt = datetime.fromtimestamp(int(pub_ts))
        info["premiered"] = dt.strftime("%Y-%m-%d")
        info["upload_date"] = dt.strftime("%Y%m%d")
    elif isinstance(pub_ts, str) and pub_ts.strip():
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", pub_ts.strip())
        if m:
            info["premiered"] = "{:04d}-{:02d}-{:02d}".format(*map(int, m.groups()))
            info["upload_date"] = info["premiered"].replace("-", "")

    tags = data.get("tags") or data.get("tag")
    if isinstance(tags, list):
        clean = [str(t).strip() for t in tags if str(t).strip()]
        if clean:
            info["bili_tags"] = clean

    return info


def read_bilibili_meta(video_path: Path) -> Optional[dict[str, Any]]:
    """按视频文件寻找 Bilibili 元数据：同名 .nfo 优先，Bili23 json 兜底。

    bili-sync 产物：{page_name}.nfo 与视频同名；
    Bili23 json：与视频同名（用户可自定义，仅处理同名）。
    """
    candidates = [
        video_path.with_suffix(".nfo"),
        Path(str(video_path) + ".nfo"),
        video_path.with_suffix(".json"),
        Path(str(video_path) + ".json"),
    ]
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand).casefold()
        if key in seen:
            continue
        seen.add(key)
        if not cand.is_file():
            continue
        if cand.suffix.lower() == ".nfo":
            info = parse_bilibili_nfo(cand)
        else:
            info = parse_bilibili_json(cand)
        if info:
            return info
    return None


# ===== 标题结构化提取（供 match-hints / 入库默认值使用）=====

def extract_bracket_artist(title: str) -> Optional[str]:
    """提取标题括号中的艺人候选：【宋昕冉】 → 宋昕冉。"""
    for m in _BRACKET_RE.finditer(title):
        val = m.group(1).strip()
        if val and not re.fullmatch(r"[\d\s.]+", val):  # 纯数字跳过（非艺人）
            return val
    return None


def extract_tail_date(title: str) -> Optional[str]:
    """提取标题尾部日期，返回 yymmdd（重命名用）。"""
    m = _TAIL_DATE_RE.search(title.strip())
    if not m:
        return None
    y, mo, d = m.groups()
    if not (1 <= int(mo) <= 12 and 1 <= int(d) <= 31):
        return None
    return f"{y[2:]}{int(mo):02d}{int(d):02d}"
