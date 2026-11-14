"""任务模板①：艺人/组合添加（artist_create）。

步骤序列（硬编码；LLM 只在 draft/verify 步内做判断）：
1. 读记忆 + artist-onboarding 工作流
2. 查重（catalog 精确/模糊 + 大小写不敏感）——命中即停，返回已有实体
3. QQ 音乐独立信源取数（可选；未启用跳过）
4. LLM 组装草稿（draft 步）：严格 schema，缺字段一律 null，禁止编造
5. 确定性校验：schema 强校验 + 命名硬约束（AI 不能写库）
6. 独立核验步（verify）：只给原始信源数据，逐字段核对草稿
7. 写入（默认 dry_run=true 只出报告；go=true 才经 ORM 落库）
8. 报告 HTML + 记忆分级回写

红线：写入永远走 ORM（复用 service 层的守卫语义：重名 409 / 近似名拦截）；
LLM 输出只作为草稿，永不直接触碰 Session。
"""

from __future__ import annotations

import html as html_mod
import json
from datetime import date
from typing import Any, Dict, List, Optional

from app.ai2 import knowledge, llm
from app.ai2.registry import ToolContext, run as run_tool
from app.ai2.tasks.registry import task

_ALLOWED_ARTIST_FIELDS = {
    "name", "sort_name", "chinese_name", "english_name", "korean_name",
    "stage_name", "aliases", "gender", "birth_date", "birth_place",
    "occupation", "debut_date", "description", "tagline",
}
_ALLOWED_GROUP_FIELDS = {
    "name", "sort_name", "chinese_name", "english_name", "korean_name",
    "aliases", "group_type", "gender_type", "origin_country",
    "debut_date", "description", "tagline",
}
_GROUP_TYPES = {"Girl Group", "Boy Group", "Co-ed", "Project", "Sub-unit"}

_DRAFT_PROMPT = """你是 K-pop 资料整理助手。根据给定的检索关键词与独立信源数据，组装一份艺人的实体草稿。

硬性规则：
1. 只使用信源数据里出现的信息；信源没有的字段一律填 null，禁止编造生日/出道日/成员等。
2. name 用官方英文/罗马名；chinese_name 有把握才填。
3. 日期一律 YYYY-MM-DD；只有年份时填 "{year}-01-01" 并在 issues 里注明「仅年份」。
4. 只输出一个 JSON 对象，不要任何解释或代码块标记：
{
  "name": "string（必填）",
  "chinese_name": "string|null", "english_name": "string|null", "korean_name": "string|null",
  "stage_name": "string|null", "aliases": ["string"]|null,
  "gender": "string|null", "birth_date": "date|null", "birth_place": "string|null",
  "occupation": "string|null", "debut_date": "date|null",
  "description": "string|null", "tagline": "string|null",
  "issues": ["无法确定的字段与原因"],
  "evidence": {"字段": "依据（引用信源原文）"}
}"""


def _parse_date(raw: Any) -> Optional[date]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def _clean_artist_draft(raw: Dict[str, Any], allowed: set) -> Dict[str, Any]:
    """确定性清洗：字段白名单 + 类型归一。schema 不合规字段直接丢弃（不猜）。"""
    out: Dict[str, Any] = {}
    for k, v in (raw or {}).items():
        if k not in allowed:
            continue
        if v in (None, "", []):
            continue
        if k in ("birth_date", "debut_date"):
            d = _parse_date(v)
            if d is None:
                continue
            out[k] = d  # 保留 date 对象：SQLite Date 列不接受字符串
        elif k == "aliases":
            if isinstance(v, list):
                out[k] = [str(x).strip() for x in v if str(x).strip()]
        else:
            out[k] = str(v).strip()
    return out


def _check_conflicts(db, model, name: str) -> List[Dict[str, Any]]:
    """复用守卫语义：大小写不敏感重名 + 模糊近似（只读检查，不抛异常）。"""
    from sqlalchemy import func, select

    hits: List[Dict[str, Any]] = []
    exact = db.scalars(
        select(model).where(func.lower(model.name) == name.strip().lower(), model.active_filter()).limit(3)
    ).all()
    for e in exact:
        hits.append({"id": e.id, "name": e.name, "match": "exact"})
    if not hits:
        like = f"%{name.strip()}%"
        near = db.scalars(
            select(model).where(model.name.ilike(like), model.active_filter()).limit(5)
        ).all()
        for e in near:
            hits.append({"id": e.id, "name": e.name, "match": "fuzzy"})
    return hits


@task("artist_create", "艺人/组合添加")
def artist_create(ctx: ToolContext) -> Dict[str, Any]:
    params = ctx.params
    kind = str(params.get("kind") or "artist").strip()  # artist | group
    if kind not in ("artist", "group"):
        raise ValueError("kind 必须是 artist 或 group")
    keyword = str(params.get("keyword") or params.get("name") or "").strip()
    if not keyword:
        raise ValueError("缺少参数 keyword（艺人/组合名）")
    dry_run = bool(params.get("dry_run", True))  # 默认只出报告不落库

    steps: List[Dict[str, Any]] = []
    evidence: Dict[str, Any] = {"keyword": keyword, "kind": kind}

    # 1) 知识层
    knowledge.ensure()
    mem = run_tool("knowledge.read_memory", ctx, {})
    steps.append({"step": "knowledge", "ok": True, "detail": f"记忆 {mem['chars']} 字（开工已读）"})

    # 2) 查重（红线：库内已有 → 绝不重复建）
    from app.models import Artist, Group

    model = Artist if kind == "artist" else Group
    conflicts = _check_conflicts(ctx.db, model, keyword)
    if conflicts:
        steps.append(
            {"step": "dedupe", "ok": False, "detail": f"库内已有 {len(conflicts)} 个疑似同名实体，任务终止（如确为新实体请人工确认）"}
        )
        evidence["conflicts"] = conflicts
        return {
            "steps": steps,
            "evidence": evidence,
            "created": False,
            "reason": "name_conflict",
            "conflicts": conflicts,
        }
    steps.append({"step": "dedupe", "ok": True, "detail": "库内无同名/近似实体"})

    # 3) QQ 音乐独立信源（可选）
    source_items: List[Dict[str, Any]] = []
    try:
        source_items = run_tool("qqmusic.search", ctx, {"keyword": keyword, "kind": "singer", "limit": 5})
        steps.append({"step": "sources", "ok": True, "detail": f"QQ 音乐 {len(source_items)} 条"})
    except Exception as e:  # noqa: BLE001 — 信源不可用降级继续
        steps.append({"step": "sources", "ok": False, "detail": f"QQ 音乐不可用（{e}），仅凭关键词生成最小草稿"})
    evidence["sources"] = source_items

    # 4) LLM 草稿（draft 步）
    if not llm.enabled(ctx.db):
        raise llm.Ai2NotConfigured("artist_create 需要 LLM（draft 步）：请在设置页配置 ai2")
    draft_raw = llm.call_json(
        "draft",
        [
            {"role": "system", "content": _DRAFT_PROMPT},
            {
                "role": "user",
                "content": (
                    f"检索关键词：{keyword}\n实体类型：{kind}\n\n"
                    f"独立信源（QQ 音乐检索结果）：\n{json.dumps(source_items, ensure_ascii=False)}"
                ),
            },
        ],
        ctx.db,
    )
    allowed = _ALLOWED_ARTIST_FIELDS if kind == "artist" else _ALLOWED_GROUP_FIELDS
    draft = _clean_artist_draft(draft_raw, allowed)
    if kind == "group" and draft.get("group_type") not in (None, *_GROUP_TYPES):
        draft.pop("group_type", None)  # 非法枚举直接丢
    if not draft.get("name"):
        raise ValueError("草稿缺 name（LLM 未给出可用名称，禁止编造——任务失败是正确行为）")
    draft["name"] = keyword if not str(draft["name"]).strip() else str(draft["name"]).strip()
    issues = [str(x) for x in (draft_raw.get("issues") or []) if str(x).strip()]
    steps.append(
        {"step": "draft", "ok": True, "detail": f"草稿 {len(draft)} 字段" + (f"；{len(issues)} 项不确定" if issues else "")}
    )
    evidence["draft"] = draft
    evidence["draft_issues"] = issues

    # 5) 独立核验（verify 步：只给信源+草稿字段名清单，不给草稿值之外的解释）
    verify_out = llm.verify_call(
        "verify",
        instruction=(
            f"下面是一个{'艺人' if kind == 'artist' else '组合'}草稿。逐字段核对：每个非 null 字段是否都能从信源数据或关键词直接确认。"
            "无法确认的字段列入 issues。"
        ),
        raw_data={
            "keyword": keyword,
            "sources": source_items,
            "draft": draft,
        },
        db=ctx.db,
    )
    verify_issues = [str(x) for x in (verify_out.get("issues") or []) if str(x).strip()]
    verify_ok = bool(verify_out.get("ok")) and not verify_issues
    steps.append(
        {"step": "verify", "ok": verify_ok, "detail": f"独立核验 {'通过' if verify_ok else f'{len(verify_issues)} 项存疑'}"}
    )
    evidence["verify"] = {"ok": verify_out.get("ok"), "issues": verify_issues}

    # 6) 写入（dry_run 默认 true；go 才落库，走 ORM）
    created_id: Optional[int] = None
    if dry_run:
        steps.append({"step": "write", "ok": True, "detail": "dry_run：未写库（确认报告后用 dry_run=false 落库）"})
    else:
        row = model(**draft)
        ctx.db.add(row)
        ctx.db.commit()
        ctx.db.refresh(row)
        created_id = row.id
        steps.append({"step": "write", "ok": True, "detail": f"已创建 {kind} #{created_id}（{row.name}）"})
    evidence["created_id"] = created_id

    # 7) 报告 + 记忆
    title = f"{'艺人' if kind == 'artist' else '组合'}添加 · {keyword}"
    rows = "".join(
        f"<tr><td>{html_mod.escape(s['step'])}</td><td>{'✓' if s['ok'] else '✗'}</td>"
        f"<td>{html_mod.escape(s['detail'])}</td></tr>"
        for s in steps
    )
    draft_rows = "".join(
        f"<tr><td>{html_mod.escape(k)}</td><td>{html_mod.escape(str(v))}</td></tr>" for k, v in draft.items()
    )
    issue_html = "".join(f"<li>{html_mod.escape(i)}</li>" for i in issues + verify_issues)
    report_html = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html_mod.escape(title)}</title>
<style>body{{font-family:system-ui;max-width:860px;margin:32px auto;line-height:1.7;color:#222}}
table{{border-collapse:collapse;margin:12px 0}}td,th{{border:1px solid #ddd;padding:6px 12px;font-size:13px}}</style>
</head><body><h1>{html_mod.escape(title)}{'（dry-run）' if dry_run else ''}</h1>
<table><tr><th>步骤</th><th>结果</th><th>说明</th></tr>{rows}</table>
<h2>草稿字段</h2><table><tr><th>字段</th><th>值</th></tr>{draft_rows}</table>
<h2>存疑项</h2><ul>{issue_html or '<li>（无）</li>'}</ul>
<details><summary>原始证据</summary><pre>{html_mod.escape(json.dumps(evidence, ensure_ascii=False, default=str)[:6000])}</pre></details>
</body></html>"""
    report = knowledge.write_report(f"artist-create-{keyword}", report_html)
    knowledge.append_log(
        "artist_create",
        f"{kind} {keyword}：{'已创建' if created_id else 'dry-run 未写库'}；核验{'通过' if verify_ok else '存疑'}；报告 {report.name}",
    )
    if verify_ok and created_id:
        knowledge.write_memory_conclusion(
            f"{kind} {draft['name']}：字段已经独立核验",
            {"created_id": created_id, "report": report.name, "sources": len(source_items)},
            "artist_create",
        )

    return {
        "steps": steps,
        "evidence": evidence,
        "report": report.name,
        "created": created_id is not None,
        "created_id": created_id,
        "draft": draft,
        "verify_ok": verify_ok,
    }
