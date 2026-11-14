"""ai2 LLM 客户端：provider 无关 + 步骤级模型覆盖。

配置来源：app_settings 的 `ai2` 分区（Ai2Settings）。
- 全局：provider / base_url / api_key / model
- 步骤级：step_models = {"verify": "deepseek-reasoner", ...}
  未配置的步骤用全局 model。

底层管道复用 app.services.llm_core（OpenAI 兼容 + Anthropic、JSON 修复），
不重复实现任何 HTTP 细节。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.llm_core import chat_completion, parse_json

logger = logging.getLogger("app.ai2.llm")

# 允许配置步骤级模型的步骤名（防止任意键污染配置）
KNOWN_STEPS = (
    "draft",     # 草稿组装（默认便宜模型）
    "verify",    # 独立核验（建议配更强模型）
    "analyze",   # 差异归因
    "summarize", # 报告摘要
    "translate", # 翻译辅助
)


class Ai2NotConfigured(RuntimeError):
    """ai2 未启用或缺凭据：调用方应把任务标记为 skipped 而不是 failed。"""


def _read_cfg(db: Session) -> Dict[str, Any]:
    from app.services import app_settings as app_settings_service

    read = app_settings_service.read_all(db)
    blob = getattr(read, "ai2", None)
    if blob is None:
        return {}
    return blob.model_dump() if hasattr(blob, "model_dump") else dict(blob)


def cfg_for_step(db: Session, step: str) -> Dict[str, Any]:
    """取某步骤的最终调用配置（全局配置 + 该步骤的模型覆盖）。"""
    cfg = _read_cfg(db)
    if not cfg.get("enabled"):
        raise Ai2NotConfigured("ai2 未启用（设置页 → AI 任务）")
    if not cfg.get("base_url") or not cfg.get("model"):
        raise Ai2NotConfigured("ai2 缺少 Base URL / 模型配置")
    out = dict(cfg)
    overrides = cfg.get("step_models") or {}
    if step not in KNOWN_STEPS:
        raise ValueError(f"未知步骤名: {step}（允许: {KNOWN_STEPS}）")
    model = overrides.get(step)
    if isinstance(model, str) and model.strip():
        out["model"] = model.strip()
    return out




def _flatten_system(messages: List[dict]) -> List[dict]:
    """把 system 内容并入首条 user 消息。

    原因：部分 OpenAI 兼容网关（本机 7863 实测）会丢弃 system role，
    导致模型看不到 schema 指令、直接输出散文。并入 user 是唯一稳妥做法。
    """
    system_parts = [str(m.get("content") or "") for m in messages if m.get("role") == "system"]
    rest = [dict(m) for m in messages if m.get("role") != "system"]
    if not system_parts:
        return rest
    head = chr(10).join(x for x in system_parts if x.strip())
    if rest and rest[0].get("role") == "user":
        rest[0]["content"] = head + chr(10) * 2 + str(rest[0].get("content") or "")
    else:
        rest.insert(0, {"role": "user", "content": head})
    return rest


def call(step: str, messages: List[dict], db: Session, timeout: int = 120) -> str:
    """按步骤配置调用聊天接口，返回文本。"""
    cfg = cfg_for_step(db, step)
    return chat_completion(cfg, _flatten_system(messages), timeout=timeout)


def call_json(step: str, messages: List[dict], db: Session, timeout: int = 180) -> Dict[str, Any]:
    """按步骤配置调用并解析为 JSON（自动修复常见 LLM 格式错误）。"""
    cfg = cfg_for_step(db, step)
    text = chat_completion(cfg, _flatten_system(messages), timeout=timeout)
    return parse_json(text)


def verify_call(
    step: str,
    instruction: str,
    raw_data: Any,
    db: Session,
    timeout: int = 180,
) -> Dict[str, Any]:
    """独立核验调用：与执行步上下文隔离 —— 只给核验指令与原始数据，
    不给任何草稿/执行上下文，避免 LLM 自查自夸。

    返回 JSON：{"ok": bool, "issues": [str], "evidence": {...}}（结构由指令约定）。
    """
    messages = [
        {
            "role": "system",
            "content": (
                "你是独立核验员。你只能依据下面给出的原始数据做判断，"
                "不允许假设任何未给出的信息。只输出一个 JSON 对象："
                '{"ok": boolean, "issues": ["问题描述", ...], "evidence": {"字段": "依据"}}。'
                "发现任何无法从原始数据确认的字段，都必须写进 issues。"
            ),
        },
        {
            "role": "user",
            "content": f"核验指令：{instruction}\n\n原始数据：\n{raw_data}",
        },
    ]
    return call_json(step, messages, db, timeout=timeout)


def enabled(db: Optional[Session]) -> bool:
    if db is None:
        return False
    try:
        cfg = _read_cfg(db)
    except Exception:  # noqa: BLE001 — 配置读失败视同未启用
        return False
    return bool(cfg.get("enabled") and cfg.get("base_url") and cfg.get("model"))
