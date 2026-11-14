"""ai2 REST API：任务提交/进度/知识层查询/规则审批。

P1 范围：selftest 任务 + 知识层与规则草案的完整读写闭环。
任务模板（artist_create 等）在 P2/P3 注册后自动出现在 GET /ai2/tasks。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.ai2 import knowledge, llm, tasks
from app.ai2.registry import describe as describe_tools
from app.core.deps import DbDep, OwnerDep

router = APIRouter(prefix="/ai2", tags=["ai2"])


@router.get("/status")
def get_status(db: DbDep):
    """总览：配置就绪度 + 知识层状态 + 可用任务与工具。"""
    tasks.register_all()
    return {
        "enabled": llm.enabled(db),
        "knowledge": knowledge.status(),
        "tasks": tasks.list_tasks(),
        "tools": describe_tools(),
    }


@router.get("/tasks")
def list_tasks():
    tasks.register_all()
    return tasks.list_tasks()


class TaskRunRequest(BaseModel):
    params: Dict[str, Any] = Field(default_factory=dict)


@router.post("/tasks/{name}/run")
def run_task(name: str, body: TaskRunRequest, db: DbDep, _owner: OwnerDep):
    tasks.register_all()
    try:
        # 后台执行：立即返回 running 快照，前端轮询 GET /tasks/runs/{id}
        return tasks.start_task(name, body.params)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/tasks/runs/{run_id}")
def get_run(run_id: str):
    run = tasks.get_run(run_id)
    if not run:
        raise HTTPException(404, "运行记录不存在（进程内存，重启后失效）")
    return run.snapshot()


# ---------------------------------------------------------------- 知识层


@router.get("/knowledge/skills")
def get_skills():
    return knowledge.list_skills()


@router.get("/knowledge/skills/{name}")
def get_skill(name: str):
    text = knowledge.read_skill(name)
    if text is None:
        raise HTTPException(404, "工作流不存在")
    return {"name": name, "text": text}


@router.get("/knowledge/memory")
def get_memory():
    return knowledge.read_memory()


@router.get("/knowledge/reports")
def get_reports(limit: int = 50):
    return knowledge.list_reports(limit=limit)


@router.get("/knowledge/reports/{file_name}")
def get_report(file_name: str):
    html = knowledge.read_report(file_name)
    if html is None:
        raise HTTPException(404, "报告不存在")
    return {"file": file_name, "html": html}


# ---------------------------------------------------------------- imflow 批次（面板 A）


@router.get("/imflow/batches")
def get_imflow_batches():
    from app.ai2.tools import imflow as imflow_tools

    try:
        return {"batches": imflow_tools.list_batches()}
    except FileNotFoundError as e:
        raise HTTPException(503, str(e)) from e


# ---------------------------------------------------------------- 规则审批与合入


@router.get("/rules/pending")
def get_pending_rules():
    return knowledge.list_pending()


@router.get("/rules")
def get_rules(status: Optional[str] = None):
    return knowledge.list_rules(status=status)


class RuleDecision(BaseModel):
    approve: bool
    note: str = ""


@router.post("/rules/{rule_id}/decide")
def decide_rule(rule_id: str, body: RuleDecision, _owner: OwnerDep):
    try:
        return knowledge.decide_rule(rule_id, body.approve, body.note)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


class RuleMerge(BaseModel):
    pass


@router.post("/rules/{rule_id}/merge")
def merge_rule(rule_id: str, _owner: OwnerDep, _body: RuleMerge):
    """把已批准的规则合入知识层（skills/rules-<kind>.md）——下次任务开工即读。"""
    try:
        return knowledge.merge_rule(rule_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
