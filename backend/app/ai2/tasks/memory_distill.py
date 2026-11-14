"""任务模板：记忆蒸馏（memory_distill）——MEMORY.md 超限或手动触发时执行。

流程：备份原文 → LLM 蒸馏（保留带证据的结论，丢弃过时/重复）→ 原子替换 → 日志。
P5 同时承担「记忆体检」：蒸馏 prompt 要求对照实体事实标记失效条目（证据不再成立的直接丢弃）。
"""

from __future__ import annotations

from typing import Any, Dict

from app.ai2 import knowledge, llm
from app.ai2.registry import ToolContext
from app.ai2.tasks.registry import task


@task("memory_distill", "记忆蒸馏")
def memory_distill(ctx: ToolContext) -> Dict[str, Any]:
    force = bool(ctx.params.get("force", False))
    mem = knowledge.read_memory()
    steps = []

    if not mem["needs_distill"] and not force:
        return {
            "steps": [{"step": "check", "ok": True, "detail": f"记忆 {mem['chars']} 字未超限（{mem['limit']}），无需蒸馏"}],
            "distilled": False,
        }

    if not llm.enabled(ctx.db):
        raise llm.Ai2NotConfigured("memory_distill 需要 LLM（summarize 步）")

    # 1) 备份（确定性）
    from datetime import datetime
    from pathlib import Path

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = knowledge.root()
    backup = root / "memory" / "logs" / f"MEMORY-backup-{stamp}.md"
    src = root / "memory" / "MEMORY.md"
    backup.write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    steps.append({"step": "backup", "ok": True, "detail": f"备份 {backup.name}"})

    # 2) LLM 蒸馏（summarize 步）
    out = llm.call_json("summarize", [
        {
            "role": "system",
            "content": (
                "你是媒体库 agent 的记忆管理员。把下面的长期记忆蒸馏成精简版：\n"
                "1. 保留所有仍有效的结论，且每条必须保留其证据指针与日期。\n"
                "2. 带 superseded 标记的条目直接丢弃。\n"
                "3. 明显重复的主题合并为一条，保留最新证据。\n"
                "4. 输出必须是合法 markdown，以「# ai2 长期结论」开头，正文控制在 2000 字以内。\n"
                '只输出 JSON：{"memory_markdown": "...", "dropped": ["丢弃的主题"], "merged": ["合并的主题"]}'
            ),
        },
        {"role": "user", "content": f"当前记忆（{mem['chars']} 字，上限 {mem['limit']}）：\n\n{mem['text']}"},
    ], ctx.db)
    new_text = str(out.get("memory_markdown") or "").strip()
    if not new_text.startswith("#"):
        raise ValueError("蒸馏结果不是合法 markdown（缺少标题）——保留原记忆不动")
    # 3) 原子替换
    tmp = src.with_suffix(".md.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(src)
    steps.append({"step": "distill", "ok": True, "detail": f"{mem['chars']} → {len(new_text)} 字（丢弃 {len(out.get('dropped') or [])} 条）"})

    # 4) 日志
    knowledge.append_log("memory_distill", f"蒸馏完成 {mem['chars']}→{len(new_text)} 字；备份 {backup.name}")
    return {
        "steps": steps,
        "distilled": True,
        "before_chars": mem["chars"],
        "after_chars": len(new_text),
        "backup": backup.name,
    }
