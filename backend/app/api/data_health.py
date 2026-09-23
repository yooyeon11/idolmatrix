"""数据体检路由：只读报告 + 悬空残留清理 + 「标注无问题」豁免。"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.deps import DbDep
from app.schemas.data_health import (
    DataHealthReport,
    HealthIgnoreCreate,
    HealthIgnoreList,
    HealthIgnoreRemove,
)
from app.services.data_health import run_health_report
from app.services.health_ignores import (
    add_ignore,
    clear_ignores,
    read_ignores,
    remove_ignore,
)
from app.services.soft_delete_service import purge_dangling_edges

router = APIRouter(prefix="/data-health", tags=["data-health"])


class PurgeDanglingResult(BaseModel):
    """悬空关系清理结果（各字段为删除/置空行数）。"""

    memberships_deleted: int = 0
    mv_subjects_cleared: int = 0
    subunit_parents_cleared: int = 0
    artist_company_relations_deleted: int = 0
    group_company_relations_deleted: int = 0


@router.get("", response_model=DataHealthReport)
def get_health_report(
    db: DbDep,
    max_issues: int = Query(500, ge=1, le=2000),
):
    """运行全部体检检查并返回报告。纯只读，可随时调用。"""
    return run_health_report(db, max_issues=max_issues)


# ===== 「标注无问题」豁免（唯一写体检数据的地方）=====


@router.get("/ignores", response_model=HealthIgnoreList)
def list_ignores(db: DbDep):
    """列出所有已标注无问题的条目。"""
    return HealthIgnoreList(items=read_ignores(db))


@router.post("/ignores", response_model=HealthIgnoreList)
def create_ignore(payload: HealthIgnoreCreate, db: DbDep):
    """把某条体检问题标注为「无问题」：此后不再提示、不计分。

    豁免记的是 key + signature；若该问题的数据后来变了，豁免自动失效并重新提示。
    """
    key = (payload.key or "").strip()
    if not key:
        return HealthIgnoreList(items=read_ignores(db))
    items = add_ignore(
        db,
        key=key,
        signature=(payload.signature or "").strip(),
        check=payload.check,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        title=payload.title,
        note=payload.note,
    )
    return HealthIgnoreList(items=items)


@router.post("/ignores/remove", response_model=HealthIgnoreList)
def delete_ignore(payload: HealthIgnoreRemove, db: DbDep):
    """恢复某条问题的提示（撤销「标注无问题」）。"""
    return HealthIgnoreList(items=remove_ignore(db, (payload.key or "").strip()))


@router.post("/ignores/clear", response_model=HealthIgnoreList)
def clear_all_ignores(db: DbDep):
    """恢复全部提示（清空豁免清单）。"""
    return HealthIgnoreList(items=clear_ignores(db))


@router.post("/purge-dangling", response_model=PurgeDanglingResult)
def purge_dangling(db: DbDep):
    """清理已软删实体留下的悬空关系边（幂等）。

    删除成员关系 / 公司关系中指向已删实体的行；将活跃 MV 的
    subject_artist_id、活跃小分队的 parent_group_id 在父实体已删时置空。
    不硬删艺人/组合/公司本身，也不改动回收站墓碑。
    """
    counts = purge_dangling_edges(db)
    db.commit()
    return PurgeDanglingResult(**counts)
