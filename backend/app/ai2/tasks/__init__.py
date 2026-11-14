"""ai2 任务模板：硬编码步骤序列。

每个任务是一串确定性的步骤（Python 函数），LLM 只在步骤内做判断；
任务结束统一走「写报告 + 回写记忆」收尾，产出可审计。

注册表在 registry.py（与实现分离，避免循环导入）。
当前已注册：
- selftest      全链路自检（知识层 → 工具 → LLM → 报告）
- ingest_audit  入库清单核对（imflow verify + 一致性核验 + LLM 归因）
P2/P4 再注册 artist_create / artist_audit。
"""

from __future__ import annotations

from app.ai2 import knowledge, llm
from app.ai2.registry import ToolContext, run as run_tool
from app.ai2 import tools as _tools  # noqa: F401 — 导入即注册
from app.ai2.tasks import artist_audit as _artist_audit  # noqa: F401 — 注册任务模板②
from app.ai2.tasks import artist_create as _artist_create  # noqa: F401 — 注册任务模板①
from app.ai2.tasks import ingest_audit as _ingest_audit  # noqa: F401 — 注册任务模板③
from app.ai2.tasks import ingest_suggest as _ingest_suggest  # noqa: F401 — 单文件建议（面板 B）
from app.ai2.tasks import memory_distill as _memory_distill  # noqa: F401 — 记忆蒸馏（P5）
from app.ai2.tasks.registry import (
    TaskRun,
    get_run,
    list_tasks,
    register_all,
    run_task,
    start_task,
    task,
)

__all__ = ["TaskRun", "get_run", "list_tasks", "register_all", "run_task", "start_task", "task"]


# ---------------------------------------------------------------- selftest


@task("selftest", "全链路自检")
def selftest(ctx: ToolContext) -> dict:
    """验证整条链路：知识层可读写 → 工具注册表可用 → LLM 可调 → 报告落盘。
    LLM 未配置时该步骤计为未验（不算任务失败）。
    """
    steps: list[dict] = []
    evidence: dict = {}

    # 1) 知识层
    knowledge.ensure()
    mem = run_tool("knowledge.read_memory", ctx, {})
    skills = run_tool("knowledge.list_skills", ctx, {})
    steps.append(
        {"step": "knowledge", "ok": True, "detail": f"memory {mem['chars']} 字 / {len(skills)} 个工作流"}
    )
    evidence["knowledge"] = {"memory_chars": mem["chars"], "skills": len(skills)}

    # 2) 工具注册表
    hits = run_tool("catalog.search_entities", ctx, {"kind": "group", "q": "a", "limit": 3})
    steps.append(
        {"step": "registry", "ok": True, "detail": f"catalog.search_entities 返回 {len(hits)} 条"}
    )
    evidence["registry"] = {"sample": hits[:3]}

    # 3) LLM 调用（verify 步骤口径）
    llm_ok = False
    llm_detail = "未配置，跳过"
    if llm.enabled(ctx.db):
        out = llm.call(
            "verify",
            [{"role": "user", "content": "请只输出 JSON：{\"ok\": true, \"ping\": \"pong\"}"}],
            ctx.db,
        )
        llm_ok = "pong" in out
        llm_detail = f"模型回包 {out[:80]!r}"
        evidence["llm"] = {"reply_head": out[:120]}
    steps.append({"step": "llm", "ok": llm_ok, "detail": llm_detail})

    # 4) 报告 + 日志（收尾：确定性代码，非 LLM）
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>ai2 selftest</title></head>"
        "<body style='font-family:system-ui;max-width:720px;margin:32px auto;line-height:1.7'>"
        "<h1>ai2 全链路自检</h1><ol>"
        + "".join(f"<li><b>{s['step']}</b>：{s['detail']}</li>" for s in steps)
        + f"</ol><pre>{evidence!r}</pre></body></html>"
    )
    report = knowledge.write_report("selftest", html)
    knowledge.append_log("selftest", f"自检完成，报告 {report.name}；LLM={'OK' if llm_ok else '跳过/未验'}")
    evidence["report"] = report.name

    return {"steps": steps, "evidence": evidence, "report": report.name, "llm_verified": llm_ok}
