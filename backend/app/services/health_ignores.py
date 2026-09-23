"""数据体检「标注无问题」豁免清单。

产品口径：体检报告是纯计算产物（只读），用户对某条问题判断「这条其实没问题、不要再报我」
时，把该问题登记为豁免。为了不建表、不做迁移，清单存在 app_settings 表的独立 key
`health_ignores` 下（与 uploader_rules 同样的手法，但不进 SECTIONS 白名单 —— 它是体检
功能数据，不该出现在 /api/settings 的读写里）。

**签名（signature）是核心**：豁免按 `key + signature` 生效，而不是只按 key。
key  = check|entity_type|entity_id（稳定定位到某实体的某类问题）
sig  = 该问题的 title+detail 摘要（描述当前事实）
数据一变（路径被归位、日期被改），sig 与登记时不同 → 豁免自动失效、问题重新出现
并标记「已重新出现」。否则用户改完数据却看不到提示，是最坑的静默失效。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting

STORE_KEY = "health_ignores"
# 豁免条数上限：正常库远用不到，防误写入超大 JSON
MAX_ITEMS = 2000


def issue_key(check: str, entity_type: str, entity_id: int) -> str:
    """问题稳定标识：同一实体的同一类问题始终得到同一 key。"""
    return f"{check}|{entity_type}|{entity_id}"


def issue_signature(title: str, detail: str) -> str:
    """问题事实摘要：描述当前内容，数据变了摘要就变（豁免随之失效）。"""
    raw = f"{(title or '').strip()}\n{(detail or '').strip()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_ignores(raw: Any) -> list[dict[str, Any]]:
    """归一化豁免项：丢掉缺 key 的脏数据、按 key 去重（后者覆盖前者）、截断上限。"""
    items = raw if isinstance(raw, list) else []
    out: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        row = {
            "key": key,
            "signature": str(item.get("signature") or "").strip(),
            "check": str(item.get("check") or "").strip(),
            "entity_type": str(item.get("entity_type") or "").strip(),
            "entity_id": int(item.get("entity_id") or 0),
            "title": str(item.get("title") or "").strip(),
            "note": str(item.get("note") or "").strip(),
            "created_at": str(item.get("created_at") or "").strip(),
        }
        if key in seen:
            out[seen[key]] = row
        else:
            seen[key] = len(out)
            out.append(row)
    return out[:MAX_ITEMS]


def read_ignores(db: Session) -> list[dict[str, Any]]:
    row = db.get(AppSetting, STORE_KEY)
    raw = row.value.get("items") if row is not None and isinstance(row.value, dict) else None
    return normalize_ignores(raw)


def _write(db: Session, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current = normalize_ignores(items)
    row = db.get(AppSetting, STORE_KEY)
    if row is None:
        db.add(AppSetting(key=STORE_KEY, value={"items": current}))
    else:
        row.value = {"items": current}
    db.commit()
    return current


def add_ignore(
    db: Session,
    *,
    key: str,
    signature: str,
    check: str = "",
    entity_type: str = "",
    entity_id: int = 0,
    title: str = "",
    note: str = "",
) -> list[dict[str, Any]]:
    """登记一条豁免（同 key 覆盖），返回归一化后的全量列表。"""
    items = [it for it in read_ignores(db) if it.get("key") != key]
    items.append(
        {
            "key": key,
            "signature": signature,
            "check": check,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "title": title,
            "note": note,
            "created_at": _now(),
        }
    )
    return _write(db, items)


def remove_ignore(db: Session, key: str) -> list[dict[str, Any]]:
    """恢复某条问题的提示（删除豁免）。"""
    return _write(db, [it for it in read_ignores(db) if it.get("key") != key])


def clear_ignores(db: Session) -> list[dict[str, Any]]:
    """清空全部豁免（恢复所有提示）。"""
    return _write(db, [])


def ignore_map(db: Session) -> dict[str, dict[str, Any]]:
    """{key: 豁免项}，供体检引擎一次性查表。"""
    return {it["key"]: it for it in read_ignores(db)}


def prune_ignores(db: Session, alive: dict[str, Optional[str]]) -> int:
    """按当前体检结果清掉「问题已不存在」的僵尸豁免（可选维护动作）。

    alive: {key: 当前 signature}。key 不在其中 = 问题已经不存在（已修好/实体已删）
    → 该豁免再无意义，删掉，避免豁免清单无限膨胀。
    返回清理条数。
    """
    items = read_ignores(db)
    kept = [it for it in items if it["key"] in alive]
    removed = len(items) - len(kept)
    if removed:
        _write(db, kept)
    return removed
