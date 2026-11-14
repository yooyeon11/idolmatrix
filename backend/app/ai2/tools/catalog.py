"""目录工具：对艺人/组合/专辑/曲目的只读检索（写库工具在 P2 按模板需要再注册）。"""

from __future__ import annotations

from typing import Any, Dict, List

from app.ai2.registry import tool
from app.models import Album, Artist, Group, Song

_KINDS = ("artist", "group", "album", "song")


@tool(
    "catalog.search_entities",
    title="目录检索",
    description="按名称模糊检索库内艺人/组合/专辑/曲目（只读，不做任何写操作）。",
    args_schema={
        "kind": {"type": "string", "desc": "实体类型：artist | group | album | song", "required": True},
        "q": {"type": "string", "desc": "检索关键词（名称片段）", "required": True},
        "limit": {"type": "int", "desc": "返回条数上限，默认 10，最大 50"},
    },
)
def search_entities(ctx, kind: str, q: str, limit: int = 10) -> List[Dict[str, Any]]:
    if kind not in _KINDS:
        raise ValueError(f"kind 必须是 {'/'.join(_KINDS)}")
    limit = max(1, min(int(limit or 10), 50))
    like = f"%{q.strip()}%"
    db = ctx.db
    if kind == "artist":
        rows = db.query(Artist).filter(Artist.name.ilike(like)).limit(limit).all()
        return [{"id": r.id, "name": r.name, "chinese_name": r.chinese_name} for r in rows]
    if kind == "group":
        rows = db.query(Group).filter(Group.name.ilike(like)).limit(limit).all()
        return [{"id": r.id, "name": r.name, "chinese_name": r.chinese_name} for r in rows]
    if kind == "album":
        rows = db.query(Album).filter(Album.name.ilike(like)).limit(limit).all()
        return [{"id": r.id, "name": r.name, "chinese_name": r.chinese_name, "year": r.release_year} for r in rows]
    rows = db.query(Song).filter(Song.name.ilike(like)).limit(limit).all()
    return [{"id": r.id, "name": r.name, "chinese_name": r.chinese_name} for r in rows]
