"""imflow 入库流水线子进程封装（规则单一来源：G:\\coding\\idolmatrix-ingest）。

ai2 不重新实现任何入库/核对逻辑 —— 只负责：
1. 以子进程调用 imflow（python imflow.py <cmd> <batch>）
2. 解析其输出（汇总 JSON 行 + out/<batch>.verify.json）
3. 把结构化结果交给任务模板 / LLM 归因

路径配置缺失时直接报错，不降级乱做（方案红线）。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.ai2.registry import tool
from app.core.config import settings

_SUMMARY_RE = re.compile(r"汇总：(\{.+\})\s*$")

DEFAULT_IMFLOW_DIR = r"G:\coding\idolmatrix-ingest"


def imflow_dir() -> Path:
    # 优先配置（环境变量 INGEST_FLOW_DIR / 部署注入），其次代码默认
    raw = str(getattr(settings, "imflow_dir", "") or "").strip()
    if not raw:
        raw = DEFAULT_IMFLOW_DIR
    return Path(raw)


def _imflow_env() -> Dict[str, str]:
    import os

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def run_imflow(command: str, batch: str, timeout: int = 600) -> Dict[str, Any]:
    """跑一条 imflow 命令，返回 {ok, returncode, stdout_tail, summary, verify}。"""
    root = imflow_dir()
    entry = root / "imflow.py"
    if not entry.exists():
        raise FileNotFoundError(
            f"imflow 目录不存在或缺少 imflow.py：{root}（部署时请挂载或设置 INGEST_FLOW_DIR）"
        )
    proc = subprocess.run(
        [sys.executable, "imflow.py", command, batch],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=_imflow_env(),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    summary: Optional[Dict[str, Any]] = None
    m = None
    for line in (proc.stdout or "").splitlines():
        mm = _SUMMARY_RE.search(line)
        if mm:
            m = mm
    if m:
        try:
            summary = json.loads(m.group(1))
        except json.JSONDecodeError:
            summary = None
    return {
        "command": f"imflow {command} {batch}",
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "output_tail": out[-3000:],
        "summary": summary,
    }


def load_verify_json(batch: str) -> Optional[Dict[str, Any]]:
    p = imflow_dir() / "out" / f"{batch}.verify.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def load_plan_events(batch: str) -> List[str]:
    """从 plan json 提取活动名（供一致性核验聚焦）。"""
    p = imflow_dir() / "out" / f"{batch}.plan.json"
    if not p.exists():
        return []
    try:
        plan = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    events = []
    for x in plan if isinstance(plan, list) else []:
        ev = (x or {}).get("ev2") or (x or {}).get("ev")
        if ev and ev not in events:
            events.append(ev)
    return events


def list_batches() -> List[Dict[str, Any]]:
    """只读扫描 imflow 批次：定义（batches/*.json）+ 产物（out/*.plan/verify）状态汇总。"""
    root = imflow_dir()
    batches_dir = root / "batches"
    if not root.exists():
        raise FileNotFoundError(f"imflow 目录不存在：{root}（配置 IMFLOW_DIR）")
    out: List[Dict[str, Any]] = []
    if batches_dir.exists():
        for p in sorted(batches_dir.glob("*.json")):
            name = p.stem
            row: Dict[str, Any] = {"batch": name, "defined": True}
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                row["kind"] = data.get("kind") or ""
                row["archived"] = bool(data.get("archived"))
                items = data.get("items") or []
                row["declared_items"] = len(items)
            except json.JSONDecodeError:
                row["parse_error"] = True
            plan = root / "out" / f"{name}.plan.json"
            row["has_plan"] = plan.exists()
            row["planned_items"] = 0
            if plan.exists():
                try:
                    row["planned_items"] = len(json.loads(plan.read_text(encoding="utf-8", errors="replace")) or [])
                except json.JSONDecodeError:
                    pass
            verify = load_verify_json(name)
            row["has_verify"] = verify is not None
            row["verify_summary"] = (verify or {}).get("summary")
            row["mtime"] = int(p.stat().st_mtime)
            out.append(row)
    return out


@tool(
    "imflow.verify",
    title="imflow 四维核验",
    description="子进程调用 idolmatrix-ingest 的 imflow verify（逐条回读比对；只读，不写库）。",
    args_schema={
        "batch": {"type": "string", "desc": "批次名（batches/<name>.json）", "required": True},
    },
)
def verify(ctx, batch: str) -> Dict[str, Any]:
    result = run_imflow("verify", batch)
    result["verify_json"] = load_verify_json(batch)
    return result
