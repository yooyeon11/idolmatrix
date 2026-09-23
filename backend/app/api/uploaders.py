"""Uploader（上传人/频道）路由：聚合列表 + 博主固定视频类型规则。

博主固定视频类型：给某几个上传人指定固定类型（如「官方舞台」「特别舞台」「粉丝直拍」），
待整理打开详情时自动选中该类型、AI 也不再判断类型；未配置的博主照旧交给 AI。
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.core.deps import DbDep, OwnerDep, PaginationDep
from app.models.music_video import MusicVideo
from app.schemas import PageResponse
from app.services import app_settings

router = APIRouter(prefix="/uploaders", tags=["uploaders"])

# 管理列表上限：博主数量是「账号级」量级，一次全量返回比分页更好用
MANAGE_LIMIT_MAX = 2000


class UploaderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    platform: Optional[str] = None
    video_count: int = 0
    latest_video_id: Optional[int] = None
    # 该博主已固定的视频类型（空 = 跟随 AI）
    video_types: List[str] = []
    # 是否在库内存在视频（false = 仅手工添加的规则，库里还没有他的作品）
    in_library: bool = True


class UploaderManageResult(BaseModel):
    items: List[UploaderRead] = []
    total: int = 0
    rule_count: int = 0


class UploaderRuleRead(BaseModel):
    name: str
    video_types: List[str] = []


class UploaderRulesPayload(BaseModel):
    rules: List[UploaderRuleRead] = []


class UploaderRulesResult(BaseModel):
    rules: List[UploaderRuleRead] = []


def _aggregate(db: DbDep) -> list[dict]:
    """按 original_uploader + 平台聚合库内博主（不软删、名字非空）。"""
    rows = db.execute(
        select(
            MusicVideo.original_uploader.label("name"),
            MusicVideo.source_platform.label("platform"),
            func.count().label("video_count"),
            func.max(MusicVideo.id).label("latest_video_id"),
        )
        .where(MusicVideo.deleted_at.is_(None))
        .where(MusicVideo.original_uploader.is_not(None))
        .where(MusicVideo.original_uploader != "")
        .group_by(MusicVideo.original_uploader, MusicVideo.source_platform)
    ).all()
    return [
        {
            "name": r.name,
            "platform": r.platform,
            "video_count": r.video_count,
            "latest_video_id": r.latest_video_id,
        }
        for r in rows
    ]


@router.get("", response_model=PageResponse[UploaderRead])
def list_uploaders(
    db: DbDep,
    pagination: PaginationDep,
    q: Optional[str] = Query(None, description="按频道名搜索"),
):
    """聚合所有非空 original_uploader，按上传人分组统计。"""
    base = (
        select(
            MusicVideo.original_uploader.label("name"),
            MusicVideo.source_platform.label("platform"),
            func.count().label("video_count"),
            func.max(MusicVideo.id).label("latest_video_id"),
        )
        .where(MusicVideo.deleted_at.is_(None))
        .where(MusicVideo.original_uploader.is_not(None))
        .where(MusicVideo.original_uploader != "")
        .group_by(MusicVideo.original_uploader, MusicVideo.source_platform)
    )
    if q:
        like = f"%{q}%"
        base = base.where(MusicVideo.original_uploader.ilike(like))
    total = len(db.execute(base).all())
    rows = db.execute(
        base.order_by(func.count().desc(), MusicVideo.original_uploader.asc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    ).all()
    rule_map = app_settings.uploader_type_map(db)
    items = [
        UploaderRead(
            name=r.name,
            platform=r.platform,
            video_count=r.video_count,
            latest_video_id=r.latest_video_id,
            video_types=rule_map.get(str(r.name).casefold(), []),
        )
        for r in rows
    ]
    return PageResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/manage", response_model=UploaderManageResult)
def manage_uploaders(db: DbDep, q: Optional[str] = Query(None, description="按博主名搜索")):
    """博主管理列表：库内博主 ∪ 已配置规则的博主（规则优先、视频数降序）。"""
    rules = app_settings.read_uploader_rules(db)
    rule_map = {r["name"].casefold(): list(r["video_types"]) for r in rules}
    items: list[UploaderRead] = []
    seen: set[str] = set()
    for row in _aggregate(db):
        key = str(row["name"]).casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(
            UploaderRead(
                **row,
                video_types=rule_map.get(key, []),
                in_library=True,
            )
        )
    # 规则里但库里还没有作品的博主也要出现，否则手工添加后「看不到」
    for rule in rules:
        key = rule["name"].casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(
            UploaderRead(
                name=rule["name"],
                platform=None,
                video_count=0,
                latest_video_id=None,
                video_types=list(rule["video_types"]),
                in_library=False,
            )
        )
    items.sort(key=lambda i: (not i.video_types, -i.video_count, i.name.casefold()))
    items = items[:MANAGE_LIMIT_MAX]
    if q:
        like = q.strip().casefold()
        items = [i for i in items if like in i.name.casefold()]
    return UploaderManageResult(
        items=items,
        total=len(items),
        rule_count=len(rules),
    )


@router.get("/rules", response_model=UploaderRulesResult)
def list_uploader_rules(db: DbDep):
    """已配置的博主固定视频类型规则（待整理 / 视频编辑弹窗也读这里）。"""
    return UploaderRulesResult(
        rules=[UploaderRuleRead(**r) for r in app_settings.read_uploader_rules(db)]
    )


@router.put("/rules", response_model=UploaderRulesResult)
def replace_uploader_rules(payload: UploaderRulesPayload, db: DbDep, _owner: OwnerDep):
    """整体覆盖规则列表；空 video_types / 重名 / 非法类型由服务端归一化剔除。"""
    saved = app_settings.write_uploader_rules(
        db, [r.model_dump() for r in payload.rules]
    )
    return UploaderRulesResult(rules=[UploaderRuleRead(**r) for r in saved])
