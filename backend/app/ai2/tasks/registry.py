"""任务注册表（与任务实现分离，避免循环导入）。"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.ai2.registry import ToolContext

_TASKS: Dict[str, Callable[[ToolContext], Dict[str, Any]]] = {}
_RUNS: Dict[str, "TaskRun"] = {}
_LOCK = threading.Lock()


@dataclass
class TaskRun:
    id: str
    name: str
    status: str = "running"  # running / done / failed / skipped
    steps: List[Dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "steps": self.steps,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": int(((self.finished_at or time.time()) - self.started_at) * 1000),
            "error": self.error,
            "result": self.result,
        }


def task(name: str, title: str):
    def deco(fn: Callable[[ToolContext], Dict[str, Any]]) -> Callable[[ToolContext], Dict[str, Any]]:
        if name in _TASKS:
            raise ValueError(f"任务重名: {name}")
        fn.__ai2_title__ = title  # type: ignore[attr-defined]
        _TASKS[name] = fn
        return fn
    return deco


def list_tasks() -> List[Dict[str, Any]]:
    return [{"name": n, "title": getattr(fn, "__ai2_title__", n)} for n, fn in _TASKS.items()]


def get_run(run_id: str) -> Optional[TaskRun]:
    return _RUNS.get(run_id)


def register_all() -> None:
    """幂等：模块导入时已注册；保留入口供 API 启动时显式调用。"""
    return None


def _execute(run: "TaskRun", name: str, db: Session, params: Optional[Dict[str, Any]]) -> None:
    """在给定 session 上执行任务并写回 run 记录（同步逻辑，供线程/同步两种入口复用）。"""
    ctx = ToolContext(db=db, task=name, params=dict(params or {}))
    try:
        result = _TASKS[name](ctx)
        run.steps = list(result.get("steps") or [])
        run.status = "done"
        run.result = result
    except Exception as e:  # noqa: BLE001 — 任务级异常统一记录（含 Ai2NotConfigured→skipped 判定）
        from app.ai2.llm import Ai2NotConfigured

        if isinstance(e, Ai2NotConfigured):
            run.status = "skipped"
        else:
            run.status = "failed"
        run.error = f"{type(e).__name__}: {e}"
    finally:
        run.finished_at = time.time()


def run_task(name: str, db: Session, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """同步执行任务（脚本 / 自检用）。"""
    if name not in _TASKS:
        raise KeyError(f"未注册的任务: {name}")
    run = TaskRun(id=uuid.uuid4().hex[:12], name=name)
    with _LOCK:
        _RUNS[run.id] = run
    _execute(run, name, db, params)
    return run.snapshot()


def start_task(name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """后台执行任务：立即返回 running 快照，前端轮询 GET /tasks/runs/{id}。

    用独立 Session（请求的 session 在响应返回时已关闭）；线程 daemon 化，
    进程退出即放弃未完成任务（任务本身幂等/只读为主）。
    """
    if name not in _TASKS:
        raise KeyError(f"未注册的任务: {name}")
    run = TaskRun(id=uuid.uuid4().hex[:12], name=name)
    with _LOCK:
        _RUNS[run.id] = run

    def _worker() -> None:
        from app.core.database import SessionLocal

        db = SessionLocal()
        try:
            _execute(run, name, db, params)
        except Exception as e:  # noqa: BLE001 — 兜底，避免线程静默死掉
            run.status = "failed"
            run.error = f"{type(e).__name__}: {e}"
            run.finished_at = time.time()
        finally:
            db.close()

    threading.Thread(target=_worker, name=f"ai2-{name}-{run.id}", daemon=True).start()
    return run.snapshot()
