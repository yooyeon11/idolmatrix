"""任务模板②：存量资料核对（artist_audit）——对已有艺人/组合出体检报告。

只读任务：加载库内实体 → QQ 音乐独立信源 → LLM 逐字段对照 →
缺失/矛盾/存疑清单 → HTML 报告 + 记忆日志（+ 可选规则草案）。
"""

from __future__ import annotations

import html as html_mod
import json
from typing import Any, Dict, List, Optional

from app.ai2 import knowledge, llm
from app.ai2.registry import ToolContext, run as run_tool
from app.ai2.tasks.registry import task

_AUDIT_FIELDS_ARTIST = (
    "name", "chinese_name", "english_name", "korean_name", "stage_name",
    "gender", "birth_date", "birth_place", "occupation", "debut_date",
    "description", "tagline", "social_media",
)
_AUDIT_FIELDS_GROUP = (
    "name", "chinese_name", "english_name", "korean_name",
    "group_type", "gender_type", "origin_country", "debut_date",
    "description", "tagline", "social_media",
)


@task("artist_audit", "存量资料核对")
def artist_audit(ctx: ToolContext) -> Dict[str, Any]:
    params = ctx.params
    kind = str(params.get("kind") or "artist").strip()
    if kind not in ("artist", "group"):
        raise ValueError("kind 必须是 artist 或 group")
    name = str(params.get("keyword") or params.get("name") or "").strip()
    if not name:
        raise ValueError("缺少参数 keyword（艺人/组合名）")

    steps: List[Dict[str, Any]] = []
    evidence: Dict[str, Any] = {"keyword": name, "kind": kind}

    # 1) 知识层
    mem = run_tool("knowledge.read_memory", ctx, {})
    steps.append({"step": "knowledge", "ok": True, "detail": f"记忆 {mem['chars']} 字"})

    # 2) 加载实体（只读；找不到就失败，不猜）
    from sqlalchemy import func, select
    from app.models import Artist, Group

    model = Artist if kind == "artist" else Group
    row = ctx.db.scalars(
        select(model).where(func.lower(model.name) == name.lower(), model.active_filter()).limit(1)
    ).first()
    if row is None:
        raise ValueError(f"库内未找到 {'艺人' if kind == 'artist' else '组合'}「{name}」（大小写不敏感精确匹配）")
    fields = _AUDIT_FIELDS_ARTIST if kind == "artist" else _AUDIT_FIELDS_GROUP
    entity = {f: getattr(row, f, None) for f in fields}
    steps.append({"step": "load", "ok": True, "detail": f"#{row.id} {row.name}"})
    evidence["entity"] = {k: (str(v) if v is not None else None) for k, v in entity.items()}

    # 3) 独立信源
    source_items: List[Dict[str, Any]] = []
    try:
        source_items = run_tool("qqmusic.search", ctx, {"keyword": row.name, "kind": "singer", "limit": 5})
        steps.append({"step": "sources", "ok": True, "detail": f"QQ 音乐 {len(source_items)} 条"})
    except Exception as e:  # noqa: BLE001
        steps.append({"step": "sources", "ok": False, "detail": f"QQ 音乐不可用（{e}）"})
    evidence["sources"] = source_items

    # 4) LLM 对照（analyze 步）
    if not llm.enabled(ctx.db):
        raise llm.Ai2NotConfigured("artist_audit 需要 LLM（analyze 步）：请在设置页配置 ai2")
    rules_text = knowledge.read_rules()
    out = llm.call_json("analyze", [
        {
            "role": "system",
            "content": (
                "你是 K-pop 资料体检员。给定库内实体字段与独立信源检索结果，输出体检结论。"
                "只能依据给定的数据；信源没有的不要臆断。"
                '只输出 JSON：{"missing": ["缺失的重要字段"], '
                '"mismatches": [{"field": "...", "library": "...", "source": "...", "note": "..."}], '
                '"suspicious": [{"field": "...", "why": "..."}], '
                '"rule_proposals": [{"kind": "...", "draft": "...", "rationale": "..."}]（最多 2 条，无可固化惯例则空数组）, '
                '"conclusion": "一句话结论"}'
                + (f"\n\n本库已确认的补充规则：\n{rules_text}" if rules_text else "")
            ),
        },
        {
            "role": "user",
            "content": (
                f"实体类型：{kind}\n库内字段：\n{json.dumps(evidence['entity'], ensure_ascii=False)}\n\n"
                f"独立信源（QQ 音乐）：\n{json.dumps(source_items, ensure_ascii=False)}"
            ),
        },
    ], ctx.db)
    missing = [str(x) for x in (out.get("missing") or [])]
    mismatches = [m for m in (out.get("mismatches") or []) if isinstance(m, dict)]
    suspicious = [s for s in (out.get("suspicious") or []) if isinstance(s, dict)]
    conclusion = str(out.get("conclusion") or "")
    steps.append(
        {"step": "analyze", "ok": True, "detail": f"缺失 {len(missing)} / 矛盾 {len(mismatches)} / 存疑 {len(suspicious)}"}
    )
    evidence["audit"] = {"missing": missing, "mismatches": mismatches, "suspicious": suspicious, "conclusion": conclusion}

    # 5) 报告 + 记忆
    title = f"资料核对 · {row.name}"
    rows = "".join(
        f"<tr><td>{html_mod.escape(s['step'])}</td><td>{'✓' if s['ok'] else '✗'}</td>"
        f"<td>{html_mod.escape(s['detail'])}</td></tr>"
        for s in steps
    )
    miss_html = "".join(f"<li>{html_mod.escape(m)}</li>" for m in missing)
    mm_html = "".join(
        f"<tr><td>{html_mod.escape(str(m.get('field')))}</td><td>{html_mod.escape(str(m.get('library')))}</td>"
        f"<td>{html_mod.escape(str(m.get('source')))}</td><td>{html_mod.escape(str(m.get('note')))}</td></tr>"
        for m in mismatches
    )
    sus_html = "".join(f"<li><b>{html_mod.escape(str(s.get('field')))}</b>：{html_mod.escape(str(s.get('why')))}</li>" for s in suspicious)
    report_html = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html_mod.escape(title)}</title>
<style>body{{font-family:system-ui;max-width:860px;margin:32px auto;line-height:1.7;color:#222}}
table{{border-collapse:collapse;margin:12px 0}}td,th{{border:1px solid #ddd;padding:6px 12px;font-size:13px}}</style>
</head><body><h1>{html_mod.escape(title)}（#{row.id}）</h1>
<table><tr><th>步骤</th><th>结果</th><th>说明</th></tr>{rows}</table>
<h2>缺失字段</h2><ul>{miss_html or '<li>（无）</li>'}</ul>
<h2>与信源矛盾</h2><table><tr><th>字段</th><th>库内</th><th>信源</th><th>说明</th></tr>{mm_html or '<tr><td colspan=4>（无）</td></tr>'}</table>
<h2>存疑</h2><ul>{sus_html or '<li>（无）</li>'}</ul>
<p><b>结论：</b>{html_mod.escape(conclusion)}</p>
</body></html>"""
    report = knowledge.write_report(f"artist-audit-{row.name}", report_html)
    knowledge.append_log("artist_audit", f"{kind} {row.name}(#{row.id})：缺失 {len(missing)} / 矛盾 {len(mismatches)} / 存疑 {len(suspicious)}；报告 {report.name}")

    proposed_ids = []
    for rp in (out.get("rule_proposals") or [])[:2]:
        try:
            d = knowledge.propose_rule(str(rp.get("kind") or "naming"), str(rp.get("draft") or ""), str(rp.get("rationale") or ""), f"artist_audit:{row.name}")
            proposed_ids.append(d["id"])
        except ValueError:
            continue

    return {
        "steps": steps,
        "evidence": evidence,
        "report": report.name,
        "entity_id": row.id,
        "missing": missing,
        "mismatch_count": len(mismatches),
        "suspicious_count": len(suspicious),
        "conclusion": conclusion,
        "rule_proposals": proposed_ids,
    }
