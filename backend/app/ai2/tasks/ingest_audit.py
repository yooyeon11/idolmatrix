"""任务模板③：入库清单核对（ingest_audit）。

步骤序列（全部确定性编排，LLM 只在 analyze 步内做归因）：
1. 读记忆 + ingest-audit 工作流
2. imflow verify（子进程，四维回读核验——只读，不写库）
3. 一致性核验（consistency.event_naming：同活动命名「齐不齐」）
4. LLM 差异归因（可选；未配置模型则跳过）
5. 报告 HTML 落盘 + 记忆回写；LLM 提出的规则 → rules_pending 草案（人工闸）

红线：本任务不做任何入库写操作；imflow 只被以 verify 调用。
"""

from __future__ import annotations

import html as html_mod
import json
from typing import Any, Dict, List

from app.ai2 import knowledge, llm
from app.ai2.registry import ToolContext, run as run_tool
from app.ai2.tasks.registry import task


def _analyze_prompt(verify_summary: Any, consistency: Dict[str, Any], batch: str) -> List[dict]:
    return [
        {
            "role": "system",
            "content": (
                "你是 K-pop 媒体库的入库核对分析员。给你两组**结构化核对结果**："
                "① imflow 四维回读核验（对不对：库内条目 vs 批次计划逐字段比对）；"
                "② 库内一致性核验（齐不齐：同一活动的命名惯例）。"
                "只依据给出的数据做归因与建议，禁止编造。"
                '只输出 JSON：{"attribution": [{"issue": "...", "cause": "...", "suggest": "..."}], '
                '"rule_proposals": [{"kind": "naming", "draft": "规则一句话", "rationale": "依据（必须引用给出的统计）"}], '
                '"conclusion": "一句话总体结论"}。'
                "rule_proposals 仅在你认为存在可固化的库内惯例分歧时给出，最多 3 条；没有就给空数组。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"批次：{batch}\n\n"
                f"imflow verify 汇总：\n{json.dumps(verify_summary, ensure_ascii=False, default=str)[:4000]}\n\n"
                f"一致性核验结果：\n{json.dumps(consistency, ensure_ascii=False, default=str)[:4000]}"
            ),
        },
    ]


@task("ingest_audit", "入库清单核对")
def ingest_audit(ctx: ToolContext) -> Dict[str, Any]:
    batch = str(ctx.params.get("batch") or "").strip()
    if not batch:
        raise ValueError("缺少参数 batch（批次名，如 20261015-kara-honey）")
    event_filter = str(ctx.params.get("event") or "").strip()
    steps: List[Dict[str, Any]] = []
    evidence: Dict[str, Any] = {"batch": batch}

    # 1) 知识层
    knowledge.ensure()
    mem = run_tool("knowledge.read_memory", ctx, {})
    steps.append({"step": "knowledge", "ok": True, "detail": f"记忆 {mem['chars']} 字（开工已读）"})

    # 2) imflow verify（子进程，只读）
    verify_result = run_tool("imflow.verify", ctx, {"batch": batch})
    ok = bool(verify_result.get("ok"))
    summary = verify_result.get("summary") or verify_result.get("verify_json")
    detail = "回读核验通过" if ok else f"imflow 退出码 {verify_result.get('returncode')}"
    steps.append({"step": "imflow_verify", "ok": ok, "detail": detail})
    evidence["imflow"] = {
        "command": verify_result.get("command"),
        "summary": summary,
        "output_tail": (verify_result.get("output_tail") or "")[-800:],
    }

    # 3) 一致性核验（齐不齐）
    from app.ai2.tools import imflow as imflow_tools

    events = [event_filter] if event_filter else imflow_tools.load_plan_events(batch)
    consistency_out: Dict[str, Any] = {"events_checked": 0, "findings_total": 0, "per_event": []}
    for ev in events[:5]:  # 每批最多聚焦 5 个活动，防止核对范围失控
        rep = run_tool("consistency.event_naming", ctx, {"event": ev})
        consistency_out["events_checked"] += 1
        consistency_out["findings_total"] += len(rep.get("findings") or [])
        consistency_out["per_event"].append(
            {
                "event": rep.get("event"),
                "count": rep.get("count"),
                "conventions": rep.get("conventions"),
                "findings": rep.get("findings"),
            }
        )
    steps.append(
        {
            "step": "consistency",
            "ok": True,
            "detail": (
                f"{consistency_out['events_checked']} 个活动 / "
                f"{consistency_out['findings_total']} 项偏离"
                if events
                else "批次计划未提供活动名，跳过（可用 event 参数指定）"
            ),
        }
    )
    evidence["consistency"] = consistency_out

    # 4) LLM 差异归因（可选）
    attribution: List[Dict[str, Any]] = []
    rule_proposals: List[Dict[str, Any]] = []
    conclusion = ""
    if llm.enabled(ctx.db) and (summary is not None or consistency_out["findings_total"]):
        try:
            out = llm.call_json("analyze", _analyze_prompt(summary, consistency_out, batch), ctx.db)
            attribution = out.get("attribution") or []
            rule_proposals = out.get("rule_proposals") or []
            conclusion = str(out.get("conclusion") or "")
            steps.append(
                {"step": "analyze", "ok": True, "detail": f"归因 {len(attribution)} 项 / 规则提案 {len(rule_proposals)} 条"}
            )
            evidence["analyze"] = {"attribution": attribution, "conclusion": conclusion}
        except Exception as e:  # noqa: BLE001 — LLM 失败不让核对任务失败
            steps.append({"step": "analyze", "ok": False, "detail": f"LLM 归因失败：{e}"})
    else:
        steps.append({"step": "analyze", "ok": False, "detail": "LLM 未配置或无差异素材，跳过归因"})

    # 5) 报告 + 记忆（收尾：确定性代码）
    findings_total = consistency_out["findings_total"]
    title = f"入库核对 · {batch}"
    rows = "".join(
        f"<tr><td>{html_mod.escape(s['step'])}</td><td>{'✓' if s['ok'] else '✗'}</td>"
        f"<td>{html_mod.escape(s['detail'])}</td></tr>"
        for s in steps
    )
    attr_html = "".join(
        f"<li><b>{html_mod.escape(str(a.get('issue')))}</b>：{html_mod.escape(str(a.get('cause')))}"
        f" → {html_mod.escape(str(a.get('suggest')))}</li>"
        for a in attribution
    )
    cons_html = "".join(
        f"<li>{html_mod.escape(pe.get('event') or '')}（{pe.get('count')} 条，主流尾标："
        f"{html_mod.escape(str((pe.get('conventions') or {}).get('dominant_bracket_shape')))}）："
        + "".join(
            f"<div class='f'>{html_mod.escape(str(f.get('detail')))}</div>" for f in (pe.get("findings") or [])
        )
        + "</li>"
        for pe in consistency_out["per_event"]
    )
    report_html = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html_mod.escape(title)}</title>
<style>body{{font-family:system-ui;max-width:860px;margin:32px auto;line-height:1.7;color:#222}}
table{{border-collapse:collapse;margin:12px 0}}td,th{{border:1px solid #ddd;padding:6px 12px;font-size:13px}}
.f{{color:#b54708;font-size:13px;margin:2px 0}}li{{margin:6px 0}}pre{{background:#f6f6f8;padding:12px;border-radius:8px;overflow:auto}}</style>
</head><body><h1>{html_mod.escape(title)}</h1>
<table><tr><th>步骤</th><th>结果</th><th>说明</th></tr>{rows}</table>
<h2>一致性核验（齐不齐）</h2><ol>{cons_html or '<li>（无）</li>'}</ol>
<h2>差异归因（LLM）</h2><ul>{attr_html or '<li>（未运行或无差异）</li>'}</ul>
<p><b>结论：</b>{html_mod.escape(conclusion or '（见步骤明细）')}</p>
<details><summary>原始证据</summary><pre>{html_mod.escape(json.dumps(evidence, ensure_ascii=False, default=str)[:8000])}</pre></details>
</body></html>"""
    report = knowledge.write_report(f"ingest-audit-{batch}", report_html)

    log_lines = [
        f"批次 {batch}：imflow {'通过' if ok else '未通过'}；一致性偏离 {findings_total} 项；报告 {report.name}"
    ]
    knowledge.append_log("ingest_audit", "\n".join(log_lines))

    proposed_ids = []
    for rp in rule_proposals[:3]:
        try:
            d = knowledge.propose_rule(
                str(rp.get("kind") or "naming"),
                str(rp.get("draft") or ""),
                str(rp.get("rationale") or ""),
                f"ingest_audit:{batch}",
            )
            proposed_ids.append(d["id"])
        except ValueError:
            continue
    if proposed_ids:
        steps.append(
            {"step": "rules_pending", "ok": True, "detail": f"{len(proposed_ids)} 条规则草案待人工审批"}
        )

    return {
        "steps": steps,
        "evidence": evidence,
        "report": report.name,
        "imflow_ok": ok,
        "findings_total": findings_total,
        "rule_proposals": proposed_ids,
    }
