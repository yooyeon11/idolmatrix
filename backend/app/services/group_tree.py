"""组合树：完整体聚合旗下小分队的公共口径。"""

from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import select

from app.models.group import Group


def root_group(db, group: Optional[Group]) -> Optional[Group]:
    """沿 parent_group_id 上溯到顶级母队；无上级（或拿不到母队）返回自身。

    「磁盘归母队 + 数据层留分队」（v3.2.42 用户定稿）的两层口径共用本函数：
    - 磁盘层：入库目录的组合主体段一律取根母队，小分队作品也落 `tripleS/`，
      避免同一母队被切成多个顶层目录；
    - 数据层：mv.groups / release_artist 照旧挂小分队，分队维度靠
      `expand_group_ids` 聚合筛选，信息一点不丢。

    母队已软删时停在当前层；带防环保护（写入校验虽已防环，这里不依赖它）。
    db 为 None（游离对象 / 单测直构）时不上溯。
    """
    if db is None or group is None:
        return group
    cur = group
    seen: set[int] = set()
    while cur.parent_group_id is not None and cur.id not in seen:
        seen.add(cur.id)
        parent = db.get(Group, cur.parent_group_id)
        if parent is None or parent.deleted_at is not None:
            break
        cur = parent
    return cur


def family_group_ids(db, group_ids: Iterable[int]) -> dict[int, set[int]]:
    """批量版家族展开：每个组合 → 自身 + 全部活跃后代（多层，带防环）。

    一次读完全部活跃组合再在内存里逐层展开，避免按组逐个打库
    （列表页 / 排行页会一次问几十个组合）。

    「母队口径含小分队」的所有展示共用本函数：视频列表筛选、组合列表影像数、
    统计页组合排行 —— 否则会出现「列表里 6 条、列表页写 5 条」的口径分裂。
    """
    wanted = [gid for gid in dict.fromkeys(group_ids) if gid]
    if not wanted:
        return {}
    children: dict[int, list[int]] = {}
    for gid, parent_id in db.execute(
        select(Group.id, Group.parent_group_id).where(Group.deleted_at.is_(None))
    ).all():
        if parent_id is not None:
            children.setdefault(parent_id, []).append(gid)
    out: dict[int, set[int]] = {}
    for root in wanted:
        seen: set[int] = {root}
        frontier = [root]
        while frontier:
            nxt: list[int] = []
            for gid in frontier:
                for child in children.get(gid, ()):
                    if child not in seen:
                        seen.add(child)
                        nxt.append(child)
            frontier = nxt
        out[root] = seen
    return out


def expand_group_ids(db, group_id: int) -> list[int]:
    """返回 group_id 及其全部活跃后代（逐层展开，防环由写入校验保证）。

    用于按组合筛选视频/歌曲时把完整体与旗下小分队聚合到同一结果集。
    """
    return sorted(family_group_ids(db, [group_id]).get(group_id) or {group_id})
