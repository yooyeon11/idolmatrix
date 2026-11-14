"""ai2 工具注册表：白名单机制 —— LLM 只能调用这里注册过的工具。

红线：任何写库工具必须在注册时声明 readonly=False，并由任务模板显式调用；
LLM 自由文本里出现的「写库」动作一律无效。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("app.ai2.registry")


@dataclass
class ToolSpec:
    name: str
    title: str
    description: str
    args_schema: Dict[str, Dict[str, str]]  # {arg: {"type": "string|int|bool|...", "desc": ...}}
    readonly: bool = True
    fn: Optional[Callable[..., Any]] = field(default=None, repr=False)


@dataclass
class ToolContext:
    """工具运行上下文：任务模板创建，工具只拿到这些。"""
    db: Session
    task: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


_REGISTRY: Dict[str, ToolSpec] = {}


def tool(
    name: str,
    title: str,
    description: str,
    args_schema: Optional[Dict[str, Dict[str, str]]] = None,
    readonly: bool = True,
):
    """注册装饰器。"""
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in _REGISTRY:
            raise ValueError(f"工具重名: {name}")
        _REGISTRY[name] = ToolSpec(
            name=name,
            title=title,
            description=description,
            args_schema=args_schema or {},
            readonly=readonly,
            fn=fn,
        )
        return fn
    return deco


def get(name: str) -> ToolSpec:
    if name not in _REGISTRY:
        raise KeyError(f"未注册的工具: {name}")
    return _REGISTRY[name]


def describe() -> List[Dict[str, Any]]:
    """给 LLM / 前端看的工具清单。"""
    return [
        {
            "name": s.name,
            "title": s.title,
            "description": s.description,
            "args": s.args_schema,
            "readonly": s.readonly,
        }
        for s in _REGISTRY.values()
    ]


_ALLOWED_TYPES = {"string", "int", "float", "bool", "list", "dict"}


def _validate(spec: ToolSpec, args: Dict[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    for key, meta in spec.args_schema.items():
        typ = meta.get("type", "string")
        if typ not in _ALLOWED_TYPES:
            raise ValueError(f"工具 {spec.name} 参数 {key} 类型非法: {typ}")
        if key not in args or args[key] in (None, ""):
            if meta.get("required", False):
                raise ValueError(f"工具 {spec.name} 缺少必填参数 {key}")
            continue
        val = args[key]
        checks = {
            "string": lambda v: isinstance(v, str),
            "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "bool": lambda v: isinstance(v, bool),
            "list": lambda v: isinstance(v, list),
            "dict": lambda v: isinstance(v, dict),
        }
        if not checks[typ](val):
            raise ValueError(f"工具 {spec.name} 参数 {key} 需要 {typ}，得到 {type(val).__name__}")
        clean[key] = val
    # 未知参数直接丢弃（白名单语义）
    return clean


def run(name: str, ctx: ToolContext, args: Optional[Dict[str, Any]] = None) -> Any:
    """执行白名单工具：参数按 schema 校验，异常向上抛给任务模板记录。"""
    spec = get(name)
    if spec.fn is None:
        raise RuntimeError(f"工具 {name} 未绑定实现")
    clean = _validate(spec, dict(args or {}))
    logger.info("tool %s(%s) task=%s readonly=%s", name, clean, ctx.task, spec.readonly)
    return spec.fn(ctx, **clean)
