"""清单确定性校验器。

只做**代码能判定**的规则（不花 token、100% 稳定），失败的项回喂给模型重试：
    - 必填字段
    - 类型枚举
    - 标题前缀 / 4K 标记与实际分辨率
    - 日期格式、与上传日期的先后、是否未来
    - 实体 id 是否存在且未删除
    - 入库路径是否与 preview-path 一致
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

NAME_PREFIX = re.compile(r"^\d{6}\s")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VIDEO_TYPES = {
    "PerformanceVideo",
    "Fancam",
    "DancePractice",
    "Behind",
    "Interview",
    "Teaser",
    "MV",
    "Live",
    "Variety",
    "Other",
}


def _issue(field: str, rule: str, expected: Any, actual: Any) -> dict[str, Any]:
    return {"field": field, "rule": rule, "expected": expected, "actual": actual}


def verify(
    db: Session,
    payload: dict[str, Any],
    info: dict[str, Any],
    expected_rel: str | None = None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    name = str(payload.get("name") or "").strip()
    if not name:
        issues.append(_issue("name", "必填", "非空标题", ""))
    elif not NAME_PREFIX.match(name):
        issues.append(
            _issue("name", "标题应以 yymmdd 开头", "如 240518 IVE - HEYA [M COUNTDOWN]", name[:40])
        )

    vt = payload.get("video_type") or ""
    if vt not in VIDEO_TYPES:
        issues.append(_issue("video_type", "类型枚举", sorted(VIDEO_TYPES), vt))

    perf = str(payload.get("performance_date") or "")
    if not perf:
        issues.append(_issue("performance_date", "必填", "YYYY-MM-DD", ""))
    elif not ISO_DATE.match(perf):
        issues.append(_issue("performance_date", "日期格式", "YYYY-MM-DD", perf))
    else:
        try:
            pdate = datetime.strptime(perf, "%Y-%m-%d").date()
            if pdate > date.today():
                issues.append(_issue("performance_date", "不应是未来", "≤ 今天", perf))
            upload = str(info.get("upload_date") or "")
            if len(upload) == 8 and upload.isdigit():
                udate = datetime.strptime(upload, "%Y%m%d").date()
                if pdate > udate:
                    issues.append(
                        _issue("performance_date", "不应晚于上传日期", upload, perf)
                    )
        except ValueError:
            issues.append(_issue("performance_date", "日期无效", "YYYY-MM-DD", perf))

    # 4K 标记与实际分辨率
    width = info.get("width")
    try:
        width = int(width) if width else None
    except (TypeError, ValueError):
        width = None
    if width and "4K" in name and width < 3840:
        issues.append(_issue("name", "标题标了 4K 但分辨率不足", "≥3840", width))

    # 实体存在性
    from app.models.artist import Artist
    from app.models.group import Group
    from app.models.song import Song

    for key, model in (("groups", Group), ("artists", Artist), ("songs", Song)):
        for item in payload.get(key) or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            row = db.get(model, int(item["id"]))
            if row is None or getattr(row, "deleted_at", None) is not None:
                issues.append(
                    _issue(key, "实体不存在或已删除", item.get("name"), item.get("id"))
                )

    if not (payload.get("songs") or payload.get("new_entities")):
        issues.append(_issue("songs", "至少一首歌", "songs 或 new_entities", []))

    rel = str(payload.get("destination_rel") or "")
    if expected_rel and rel and rel != expected_rel:
        issues.append(_issue("destination_rel", "与 preview-path 不一致", expected_rel, rel))

    return issues
