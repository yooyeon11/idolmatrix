"""频道稳定身份回填（channel_id）。

业主 2026-10-26：YouTube 频道改名后，同一频道会被拆成两个博主。根治方案是把
yt-dlp `.info.json` 里本来就有的 `channel_id`（UC…）存进 `music_videos.source_channel_id`，
之后博主管理 / 缺失检测都按频道身份归组，不再依赖显示名。

本模块只做存量回填：新条目在入库时已经由 `library_service.ingest_music_video` 写入。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.music_video import MusicVideo
from app.services.json_top_level import extract_json_top_level
from app.services.library_service import (
    extract_channel_id,
    resolve_video_abs_path,
)

logger = logging.getLogger(__name__)


def _sidecar_path(video: Optional[Path]) -> Optional[Path]:
    if video is None:
        return None
    # xxx.mkv -> xxx.info.json
    return Path(str(video.with_suffix("")) + ".info.json")


def backfill_video_channel_ids(db: Session, *, dry_run: bool = False) -> dict:
    """从库内视频的 `.info.json` 侧车回填 source_channel_id。

    只处理 YouTube 来源且尚无 channel_id 的活跃条目；有侧车但没有 channel_id 的
    老文件（手工命名 / 非 yt-dlp 下载）计入 `no_channel_id`，不猜测。
    """
    rows = db.scalars(
        select(MusicVideo)
        .where(MusicVideo.deleted_at.is_(None))
        .where(func.lower(MusicVideo.source_platform) == "youtube")
        .where(MusicVideo.source_channel_id.is_(None))
    ).all()

    stats = {
        "scanned": len(rows),
        "updated": 0,
        "no_file": 0,
        "no_sidecar": 0,
        "no_channel_id": 0,
        "errors": 0,
        "dry_run": dry_run,
    }
    for mv in rows:
        try:
            path = resolve_video_abs_path(mv)
            side = _sidecar_path(path)
            if path is None or not path.is_file():
                stats["no_file"] += 1
                continue
            if side is None or not side.is_file():
                stats["no_sidecar"] += 1
                continue
            info = extract_json_top_level(side, ("channel_id", "channel_url"))
            cid = extract_channel_id(info)
            if not cid:
                stats["no_channel_id"] += 1
                continue
            if not dry_run:
                mv.source_channel_id = cid
            stats["updated"] += 1
        except Exception:  # noqa: BLE001
            stats["errors"] += 1
            logger.warning("回填 channel_id 失败 mv=%s", mv.id, exc_info=True)

    if not dry_run and stats["updated"]:
        db.commit()
    return stats


def backfill_rule_channel_ids(db: Session, probe) -> dict:
    """给已配置频道 URL 的博主规则补 channel_id（可注入 probe 以便测试/避免出网）。

    `probe(url)` 需返回含 `channel_id` 的 dict；失败计入 errors。
    不会修改 missing_check_enabled。
    """
    from app.services import app_settings

    rules = app_settings.read_uploader_rules(db)
    stats = {"rules": len(rules), "updated": 0, "skipped": 0, "errors": 0}
    changed = False
    for rule in rules:
        url = (rule.get("youtube_channel_url") or "").strip()
        if not url or rule.get("youtube_channel_id"):
            stats["skipped"] += 1
            continue
        try:
            result = probe(url)
            cid = str(result.get("channel_id") or "").strip()
            if not cid:
                stats["skipped"] += 1
                continue
            rule["youtube_channel_id"] = cid
            stats["updated"] += 1
            changed = True
        except Exception:  # noqa: BLE001
            stats["errors"] += 1
            logger.warning("探测频道失败 rule=%s", rule.get("name"), exc_info=True)
    if changed:
        app_settings.write_uploader_rules(db, rules)
    return stats

def channel_identity_for_uploader(db: Session, name: Optional[str]) -> tuple[str, list[str]]:
    """返回 (channel_id, 该频道在库内/规则里出现过的所有名字)。

    没有 channel_id 时退回 ("", [name])。用于让 `/uploaders/<旧名>` 页面也能列出
    改名后新名下的视频。
    """
    from app.services import app_settings

    raw = (name or "").strip()
    if not raw:
        return "", []
    key = app_settings.uploader_key(raw)
    cid = ""
    rules = app_settings.read_uploader_rules(db)
    for rule in rules:
        if app_settings.uploader_key(rule["name"]) == key and rule.get("youtube_channel_id"):
            cid = str(rule["youtube_channel_id"]).strip()
            break
    names: set[str] = {raw}
    if not cid:
        rows = db.execute(
            select(MusicVideo.original_uploader, MusicVideo.source_channel_id)
            .where(MusicVideo.deleted_at.is_(None))
            .where(func.lower(MusicVideo.source_platform) == "youtube")
            .where(MusicVideo.original_uploader.is_not(None))
            .where(MusicVideo.source_channel_id.is_not(None))
            .distinct()
        ).all()
        for nm, vid_cid in rows:
            if app_settings.uploader_key(nm) == key and vid_cid:
                cid = str(vid_cid).strip()
                break
    if not cid:
        return "", [raw]
    for rule in rules:
        if str(rule.get("youtube_channel_id") or "").strip() == cid:
            names.add(rule["name"])
    rows = db.execute(
        select(MusicVideo.original_uploader)
        .where(MusicVideo.deleted_at.is_(None))
        .where(func.lower(MusicVideo.source_platform) == "youtube")
        .where(MusicVideo.source_channel_id == cid)
        .where(MusicVideo.original_uploader.is_not(None))
        .distinct()
    ).all()
    for (nm,) in rows:
        if nm:
            names.add(str(nm))
    return cid, sorted(names)
