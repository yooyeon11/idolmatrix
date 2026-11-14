"""知识层工具：只读查询为主。记忆回写 / 规则草案是任务模板的确定性收尾步骤，
不作为 LLM 可自由调用的工具（防自我升级失控）。"""

from __future__ import annotations

from typing import Any, Dict, List

from app.ai2 import knowledge
from app.ai2.registry import tool


@tool(
    "knowledge.read_memory",
    title="读长期记忆",
    description="读 ai2 长期结论（MEMORY.md）与是否需要蒸馏的标记。",
    args_schema={},
)
def read_memory(ctx) -> Dict[str, Any]:
    return knowledge.read_memory()


@tool(
    "knowledge.list_skills",
    title="列工作流",
    description="列出知识层已登记的工作流（skills/*.md）。",
    args_schema={},
)
def list_skills(ctx) -> List[Dict[str, Any]]:
    return knowledge.list_skills()


@tool(
    "knowledge.read_skill",
    title="读工作流",
    description="读某个工作流的完整内容（任务开工前必读）。",
    args_schema={
        "name": {"type": "string", "desc": "工作流名（不含 .md）", "required": True},
    },
)
def read_skill(ctx, name: str) -> Dict[str, Any]:
    text = knowledge.read_skill(name)
    if text is None:
        raise FileNotFoundError(f"工作流不存在: {name}")
    return {"name": name, "text": text}


@tool(
    "knowledge.list_reports",
    title="列报告",
    description="列出历史任务报告（最近优先）。",
    args_schema={
        "limit": {"type": "int", "desc": "返回条数上限，默认 20"},
    },
)
def list_reports(ctx, limit: int = 20) -> List[Dict[str, Any]]:
    return knowledge.list_reports(limit=max(1, min(int(limit or 20), 100)))
