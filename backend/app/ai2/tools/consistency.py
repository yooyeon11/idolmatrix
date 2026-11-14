"""库内一致性核验（「齐不齐」维度，与 QQ 音乐交叉核验「对不对」正交）。

全部确定性代码：同类条目检索 → 命名惯例统计 → 偏离项列举。
命名不符的判定不经过 LLM；LLM 只消费这里的结构化结论做归因与建议。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.ai2.registry import tool
from app.models.music_video import MusicVideo

# 标题规范（imflow rules 的库内口径）：YYMMDD Artist - Song [Event Name Type]
_TITLE_RE = re.compile(r"^(\d{6})\s+(.+?)\s+-\s+(.+?)(?:\s+\[([^\[\]]+)\])?$")
_BRACKET_RE = re.compile(r"\[([^\[\]]+)\]")
_DATE_PREFIX_RE = re.compile(r"^\d{6}\s")

# 标题方括号段内的画质/帧率/编码标签（与 imflow rules 的 QUALITY 口径对齐的关键子集）
_QUALITY_TOKEN_RE = re.compile(
    r"(?i)\b(?:[48]k(?:60p)?|\d{3,4}p\d{0,3}|\d{2,3}fps|60p|hevc|av1|vp9)\b"
)


def _strip_quality_from_name(name: str) -> str:
    """剥掉标题方括号内的画质/帧率/编码标签；括号变空则整段去掉（供建议清洗复用）。"""
    def _clean_bracket(m: re.Match) -> str:
        inner = _QUALITY_TOKEN_RE.sub("", m.group(1)).strip(" -")
        return f"[{inner}]" if inner else ""

    cleaned = _BRACKET_RE.sub(_clean_bracket, name)
    return cleaned.strip()


def _bracket_shape(bracket: Optional[str]) -> str:
    """把尾标括号压缩成「形状」：去日期/画质标签后的词干集合，用于同类对比。"""
    if not bracket:
        return ""
    tokens = [t.strip() for t in bracket.split() if t.strip()]
    # 丢弃画质/帧率/编码类标签（与 imflow _QUALITY_TOKEN_RE 同口径的关键子集）
    drop = re.compile(r"(?i)^(?:[48]k(?:60p)?|\d{3,4}p\d{0,3}|\d{2,3}fps|60p|hevc|av1|vp9|shorts?)$")
    keep = [t for t in tokens if not drop.match(t)]
    return " ".join(keep)


@tool(
    "consistency.event_naming",
    title="活动命名一致性",
    description=(
        "按活动名统计库内条目的标题规范：日期前缀、尾标括号形状、机位字段。"
        "同一活动下出现多种括号形状 / 缺日期前缀 / 括号机位与 camera_angle 字段不一致 → 列为偏离项（只读）。"
    ),
    args_schema={
        "event": {"type": "string", "desc": "活动名（event_name 精确或前缀匹配）", "required": True},
        "limit": {"type": "int", "desc": "最多分析的条目数，默认 200"},
    },
)
def event_naming(ctx, event: str, limit: int = 200) -> Dict[str, Any]:
    db: Session = ctx.db
    ev = event.strip()
    rows = (
        db.query(MusicVideo)
        .filter(MusicVideo.event_name.ilike(f"%{ev}%"))
        .order_by(MusicVideo.performance_date.asc())
        .limit(max(1, min(int(limit or 200), 500)))
        .all()
    )
    if not rows:
        return {"event": ev, "count": 0, "findings": [], "conventions": {}}

    shapes: Dict[str, List[int]] = defaultdict(list)
    no_date_prefix: List[int] = []
    bracket_cam_mismatch: List[Dict[str, Any]] = []
    bad_title: List[Dict[str, Any]] = []

    for r in rows:
        m = _TITLE_RE.match(r.name or "")
        if not m:
            bad_title.append({"id": r.id, "name": r.name})
            if not _DATE_PREFIX_RE.match(r.name or ""):
                no_date_prefix.append(r.id)
            continue
        bracket = m.group(4)
        shape = _bracket_shape(bracket)
        shapes[shape].append(r.id)
        if not _DATE_PREFIX_RE.match(r.name or ""):
            no_date_prefix.append(r.id)
        # 括号里的机位词 vs camera_angle 字段
        if bracket and r.camera_angle:
            cam = r.camera_angle.lower()
            if cam and cam not in bracket.lower():
                bracket_cam_mismatch.append(
                    {"id": r.id, "name": r.name, "camera_angle": r.camera_angle, "bracket": bracket}
                )

    dominant = max(shapes.items(), key=lambda kv: len(kv[1])) if shapes else ("", [])
    findings: List[Dict[str, Any]] = []
    for shape, ids in sorted(shapes.items(), key=lambda kv: -len(kv[1])):
        if shape == dominant[0]:
            continue
        findings.append(
            {
                "kind": "bracket_shape_divergence",
                "detail": f"尾标形状「{shape or '(无括号)'}」{len(ids)} 条 vs 主流「{dominant[0] or '(无括号)'}」{len(dominant[1])} 条",
                "ids": ids[:20],
            }
        )
    if no_date_prefix:
        findings.append(
            {"kind": "missing_date_prefix", "detail": f"{len(no_date_prefix)} 条标题缺 YYMMDD 日期前缀", "ids": no_date_prefix[:20]}
        )
    if bad_title:
        findings.append(
            {
                "kind": "title_format_off",
                "detail": f"{len(bad_title)} 条不符合「YYMMDD Artist - Song [Event Type]」基本格式",
                "samples": bad_title[:10],
            }
        )
    if bracket_cam_mismatch:
        findings.append(
            {
                "kind": "bracket_vs_camera_angle",
                "detail": f"{len(bracket_cam_mismatch)} 条括号机位与 camera_angle 字段不一致",
                "samples": bracket_cam_mismatch[:10],
            }
        )
    return {
        "event": ev,
        "count": len(rows),
        "conventions": {"dominant_bracket_shape": dominant[0], "dominant_count": len(dominant[1])},
        "findings": findings,
    }


@tool(
    "consistency.top_events",
    title="高频活动清单",
    description="按条目数列出库内最高频的活动名（找核对切入点用）。",
    args_schema={
        "limit": {"type": "int", "desc": "返回条数，默认 20"},
    },
)
def top_events(ctx, limit: int = 20) -> List[Dict[str, Any]]:
    db: Session = ctx.db
    rows = (
        db.query(MusicVideo.event_name, sa_func.count(MusicVideo.id))
        .filter(MusicVideo.event_name.isnot(None), MusicVideo.event_name != "")
        .group_by(MusicVideo.event_name)
        .order_by(sa_func.count(MusicVideo.id).desc())
        .limit(max(1, min(int(limit or 20), 100)))
        .all()
    )
    return [{"event": ev, "count": n} for ev, n in rows]
