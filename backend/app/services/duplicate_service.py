"""下载前的重复校验：先查库、再查待整理与下载队列，命中就劝停。

口径与「待整理」保持完全一致（`app.services.library_service._LibraryDupIndex`）：
优先级 平台视频ID > 文件名+大小 > 大小+时长(±1秒)。

对 YouTube 下载而言，**平台视频 ID 是唯一可靠的键**：同一个视频换画质/换封装
重新下载时，文件名、字节数、时长全都变了，只有 11 位 video id 不变。
因此这里先按 `source_platform=youtube + source_id=<id>` 精确命中，
再补充「已在下载队列 / 已在待整理」两种软重复。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.download import DownloadJob
from app.models.incoming_file import IncomingFile
from app.services import archive_service
from app.services.library_service import library_duplicate_index

PLATFORM = "youtube"


def _fmt_resolution(w: Optional[int], h: Optional[int]) -> str:
    if not w or not h:
        return ""
    return f"{w}×{h}"


def _fmt_size(n: Optional[int]) -> str:
    if not n:
        return ""
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{v:.1f}{unit}" if unit != "B" else f"{int(v)}B"
        v /= 1024
    return f"{v:.1f}TB"


def check_video_duplicate(
    db: Session,
    *,
    video_id: Optional[str],
    title: Optional[str] = None,
    duration: Optional[int] = None,
) -> dict:
    """返回 {duplicate, message, matches:[...]}。

    matches 按「库内 → 待整理 → 下载队列」排序，取第一条生成提示文案。
    """
    matches: list[dict] = []
    if not video_id:
        return {"duplicate": False, "message": "", "matches": []}

    # 1) 正式库
    try:
        row = library_duplicate_index(db).find_duplicate(
            file_name="",
            file_size=None,
            duration=duration,
            source_platform=PLATFORM,
            source_id=video_id,
        )
    except Exception:  # noqa: BLE001 — 索引构建异常不该挡住下载
        row = None
    if row is not None:
        matches.append(
            {
                "where": "library",
                "label": "正式库",
                "id": row.id,
                "uid": getattr(row, "uid", None),
                "name": row.name,
                "file_name": row.file_name,
                "resolution": _fmt_resolution(row.width, row.height),
                "file_size": _fmt_size(row.file_size),
                "duration": row.duration,
            }
        )

    # 2) 待整理（已下载但还没入库）
    incoming = db.scalars(
        select(IncomingFile)
        .where(IncomingFile.source_id == video_id)
        .limit(3)
    ).all()
    for r in incoming:
        matches.append(
            {
                "where": "incoming",
                "label": "待整理",
                "id": r.id,
                "name": r.file_name,
                "file_name": r.file_name,
                "resolution": _fmt_resolution(r.width, r.height),
                "file_size": _fmt_size(r.file_size),
                "duration": r.duration,
            }
        )

    # 3) 下载队列（排队/进行中/已完成）
    jobs = db.scalars(
        select(DownloadJob)
        .where(DownloadJob.video_id == video_id)
        .where(DownloadJob.status.in_(("queued", "downloading", "processing", "done")))
        .order_by(DownloadJob.id.desc())
        .limit(3)
    ).all()
    for j in jobs:
        matches.append(
            {
                "where": "downloads",
                "label": {"queued": "排队中", "downloading": "下载中", "processing": "合并中"}.get(
                    j.status, "已下载过"
                ),
                "id": j.id,
                "name": j.title or j.video_id or j.url,
                "file_name": j.output_path or "",
                "resolution": "",
                "file_size": _fmt_size(j.total_bytes),
                "duration": j.duration,
            }
        )

    # 4) 本地下载存档（下过、但文件已移走/删除/尚未入库）
    if archive_service.is_archived(video_id):
        matches.append(
            {
                "where": "archive",
                "label": "下载存档",
                "id": None,
                "uid": None,
                "name": title or video_id,
                "file_name": "",
                "resolution": "",
                "file_size": "",
                "duration": duration,
            }
        )

    if not matches:
        return {"duplicate": False, "message": "", "matches": []}

    first = matches[0]
    detail_bits = [b for b in (first.get("resolution"), first.get("file_size")) if b]
    message = f"已存在于{first['label']}：{first['name']}"
    if detail_bits:
        message += f"（{' / '.join(detail_bits)}）"
    message += "。为避免重复占用空间，已跳过下载。"
    return {"duplicate": True, "message": message, "matches": matches}


def existing_video_ids(db: Session, video_ids: list[str]) -> set[str]:
    """订阅用：批量判断哪些 video id 已经在库里（正式库 / 待整理 / 已下载）。

    订阅每轮会展开整个列表，逐个查库代价太高 —— 这里一次性载入三张表的
    id 集合再求交集。库内 ID 全部 casefold 是**不必要**的：YouTube ID
    本身区分大小写，精确比对才对。
    """
    wanted = {v for v in video_ids if v}
    if not wanted:
        return set()

    hit: set[str] = set()

    # 正式库：直接扫 source_id（比构建整份索引轻）
    from app.models.music_video import MusicVideo

    rows = db.execute(
        select(MusicVideo.source_id).where(
            MusicVideo.deleted_at.is_(None),
            MusicVideo.source_id.in_(wanted),
        )
    ).all()
    for (sid,) in rows:
        if sid:
            hit.add(sid)

    rows = db.execute(
        select(IncomingFile.source_id).where(IncomingFile.source_id.in_(wanted))
    ).all()
    for (sid,) in rows:
        if sid:
            hit.add(sid)

    rows = db.execute(
        select(DownloadJob.video_id).where(
            DownloadJob.video_id.in_(wanted),
            DownloadJob.status.in_(("queued", "downloading", "processing", "done")),
        )
    ).all()
    for (sid,) in rows:
        if sid:
            hit.add(sid)

    # 本地下载存档：下过但已不在库里/待整理的也算"有"
    for vid in wanted:
        if vid not in hit and archive_service.is_archived(vid):
            hit.add(vid)

    return hit
