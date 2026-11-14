"""ai2 知识层：skills / memory / reports / rules_pending —— 文件即真相。

目录结构（运行时数据，Docker 部署时挂 volume；代码升级不覆盖积累）：
    <storage>/ai_knowledge/
    ├── skills/            工作流定义（markdown，随代码分发默认值，可被记忆修正）
    ├── memory/MEMORY.md   长期结论（有字数上限，超限触发蒸馏）
    ├── memory/logs/YYYY-MM-DD.md  按日任务日志
    ├── reports/           每次任务的核对清单 / 变更报告 HTML
    └── rules_pending/     agent 起草的规则草案（人工确认后才合入）

记忆分级回写（防「核验错→记忆错」）：
- fact（客观事实：信源取值、任务统计、报告路径）→ 自动写日志
- conclusion（核验结论）→ 必须带证据指针；新证据矛盾时取代旧条目（superseded 标记）
- method（方法论/规则）→ 不进记忆，写 rules_pending 等人工审批
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings

MEMORY_CHAR_LIMIT = 3000


def root() -> Path:
    return settings.ai_knowledge_dir


def ensure() -> Path:
    r = root()
    for sub in ("skills", "memory", "memory/logs", "reports", "rules_pending"):
        (r / sub).mkdir(parents=True, exist_ok=True)
    mem = r / "memory" / "MEMORY.md"
    if not mem.exists():
        mem.write_text(
            "# ai2 长期结论\n\n> 只放跨任务有效的结论与踩坑。每条必须带日期与证据指针。\n",
            encoding="utf-8",
        )
    # 种子工作流：代码内置默认值，仅在目标不存在时落盘（不覆盖用户/记忆修正版）
    seeds = Path(__file__).parent / "seeds" / "skills"
    if seeds.exists():
        for seed in seeds.glob("*.md"):
            target = r / "skills" / seed.name
            if not target.exists():
                target.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
    return r


# ---------------------------------------------------------------- skills


def list_skills() -> List[Dict[str, Any]]:
    out = []
    for p in sorted((ensure() / "skills").glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        title = text.splitlines()[0].lstrip("# ").strip() if text else p.stem
        out.append({"name": p.stem, "title": title, "chars": len(text)})
    return out


def read_skill(name: str) -> Optional[str]:
    p = ensure() / "skills" / f"{Path(name).stem}.md"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def read_rules() -> str:
    """读所有已合入的规则文件（skills/rules-*.md）——任务开工时注入 prompt。"""
    parts = []
    for p in sorted((ensure() / "skills").glob("rules-*.md")):
        parts.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- memory


def read_memory() -> Dict[str, Any]:
    p = ensure() / "memory" / "MEMORY.md"
    text = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    return {
        "text": text,
        "chars": len(text),
        "needs_distill": len(text) > MEMORY_CHAR_LIMIT,
        "limit": MEMORY_CHAR_LIMIT,
    }


def _today_log_path() -> Path:
    return ensure() / "memory" / "logs" / f"{date.today().isoformat()}.md"


def append_log(task: str, entry: str) -> Path:
    """客观事实级回写：追加到当日日志（自动通道，低风险）。"""
    p = _today_log_path()
    stamp = datetime.now().strftime("%H:%M:%S")
    block = f"\n## {stamp} {task}\n\n{entry.strip()}\n"
    if not p.exists():
        p.write_text(f"# {date.today().isoformat()} ai2 任务日志\n{block}", encoding="utf-8")
    else:
        with p.open("a", encoding="utf-8") as f:
            f.write(block)
    return p


def write_memory_conclusion(
    conclusion: str,
    evidence: Dict[str, Any],
    source_task: str,
) -> Dict[str, Any]:
    """核验结论级回写：必须带证据指针；若已有同主题旧结论，标记 superseded（取代而非追加）。"""
    if not str(evidence).strip():
        raise ValueError("核验结论必须携带证据指针（evidence）")
    p = ensure() / "memory" / "MEMORY.md"
    text = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    topic = conclusion.split("：")[0].split(":")[0].strip()[:40]
    # 取代语义：同主题旧条目（## 开头行包含主题词）标记 superseded
    if topic and topic in text:
        lines = text.splitlines(keepends=True)
        out, in_entry = [], False
        for ln in lines:
            if ln.startswith("## ") and topic in ln and "superseded" not in ln:
                in_entry = True
                out.append(ln.rstrip("\n") + " `superseded`\n")
                continue
            if in_entry and ln.startswith("## "):
                in_entry = False
            if not in_entry:
                out.append(ln)
        text = "".join(out)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = (
        f"\n## {topic or '结论'}\n\n- 结论：{conclusion.strip()}\n"
        f"- 日期：{stamp}（任务：{source_task}）\n"
        f"- 证据：{json.dumps(evidence, ensure_ascii=False)}\n"
    )
    with p.open("a", encoding="utf-8") as f:
        f.write(entry)
    return {"topic": topic, "superseded_replaced": bool(topic and topic in text)}


def distill_if_needed() -> Optional[Dict[str, Any]]:
    """MEMORY.md 超限时触发蒸馏提示（P1 只报告，实际蒸馏任务在 P5 落地）。"""
    m = read_memory()
    if not m["needs_distill"]:
        return None
    return {"needs_distill": True, "chars": m["chars"], "limit": m["limit"]}


# ---------------------------------------------------------------- reports


def write_report(name: str, html: str) -> Path:
    safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name).strip("_") or "report"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    p = ensure() / "reports" / f"{stamp}-{safe}.html"
    p.write_text(html, encoding="utf-8")
    return p


def list_reports(limit: int = 50) -> List[Dict[str, Any]]:
    out = []
    for p in sorted((ensure() / "reports").glob("*.html"), reverse=True)[:limit]:
        out.append({"file": p.name, "size": p.stat().st_size})
    return out


def read_report(file_name: str) -> Optional[str]:
    p = ensure() / "reports" / Path(file_name).name
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- rules pending


@dataclass
class PendingRule:
    id: str
    kind: str          # naming / workflow / match ...
    draft: str         # 规则内容（人类可读）
    rationale: str     # agent 的理由（必须含证据）
    source_task: str
    status: str = "pending"   # pending / approved / rejected
    created_at: str = ""


def propose_rule(kind: str, draft: str, rationale: str, source_task: str) -> Dict[str, Any]:
    """方法论级产出 → 只写草案，等人工审批（自我升级的人工闸）。"""
    if not draft.strip() or not rationale.strip():
        raise ValueError("规则草案与理由都不能为空")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rid = f"{stamp}-{kind}"
    data = {
        "id": rid,
        "kind": kind,
        "draft": draft.strip(),
        "rationale": rationale.strip(),
        "source_task": source_task,
        "status": "pending",
        "created_at": stamp,
    }
    p = ensure() / "rules_pending" / f"{rid}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def list_pending() -> List[Dict[str, Any]]:
    out = []
    for p in sorted((ensure() / "rules_pending").glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def list_rules(status: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = list_pending()
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return rows


def merge_rule(rule_id: str) -> Dict[str, Any]:
    """把**已批准**的规则合入知识层（skills/rules-<kind>.md）——自我升级闭环的落库动作。

    合入的是知识层文件（agent 下次任务开工即读），不是 imflow 的 rules_ext.py：
    后者影响入库正确性，仍由人手工编辑。
    """
    p = _find_pending(rule_id)
    if not p:
        raise FileNotFoundError(f"规则草案不存在: {rule_id}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("status") != "approved":
        raise ValueError(f"规则 {rule_id} 未批准（当前 {data.get('status')}），不能合入")
    kind = data.get("kind") or "general"
    target = ensure() / "skills" / f"rules-{Path(kind).name}.md"
    stamp = datetime.now().strftime("%Y-%m-%d")
    block = (
        f"\n## {stamp} · {data.get('source_task') or '-'}\n"
        f"- 规则：{data.get('draft')}\n"
        f"- 依据：{data.get('rationale')}\n"
    )
    with target.open("a", encoding="utf-8") as f:
        f.write(block)
    data["status"] = "merged"
    data["merged_file"] = target.name
    data["merged_at"] = stamp
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _find_pending(rule_id: str) -> Optional[Path]:
    for p in (ensure() / "rules_pending").glob("*.json"):
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("id") == rule_id:
                return p
        except json.JSONDecodeError:
            continue
    return None


def decide_rule(rule_id: str, approve: bool, note: str = "") -> Dict[str, Any]:
    """人工审批：approved 的草案由人手工合入 rules_ext.py / skill md（P5 再做一键合入）。"""
    p = _find_pending(rule_id)
    if not p:
        raise FileNotFoundError(f"规则草案不存在: {rule_id}")
    data = json.loads(p.read_text(encoding="utf-8"))
    data["status"] = "approved" if approve else "rejected"
    data["decision_note"] = note
    data["decided_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def status() -> Dict[str, Any]:
    return {
        "skills": list_skills(),
        "memory": read_memory(),
        "pending_rules": [r for r in list_pending() if r.get("status") == "pending"],
        "reports_count": len(list_reports(limit=10000)),
        "distill": distill_if_needed(),
    }
