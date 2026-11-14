"""QQ 音乐交叉信源工具（「对不对」维度，与库内一致性「齐不齐」正交）。

复用 app.services.qqmusic_service（配置/鉴权/搜索均走现有链路）；
未启用时抛 QQMusicError → 任务模板把该步骤记为跳过，不让任务失败。
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.ai2.registry import tool


@tool(
    "qqmusic.search",
    title="QQ 音乐检索",
    description="QQ 音乐独立信源检索（歌曲/专辑/歌手）。核验草稿字段的「对不对」。未启用时报错由任务层降级为跳过。",
    args_schema={
        "keyword": {"type": "string", "desc": "检索关键词", "required": True},
        "kind": {"type": "string", "desc": "song | album | singer（默认 song）"},
        "limit": {"type": "int", "desc": "返回条数上限，默认 5，最大 20"},
    },
)
def search(ctx, keyword: str, kind: str = "song", limit: int = 5) -> List[Dict[str, Any]]:
    from app.services import qqmusic_service

    return qqmusic_service.brief(keyword, kind, limit=max(1, min(int(limit or 5), 20)))
