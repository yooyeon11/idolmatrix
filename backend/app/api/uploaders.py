"""Uploader（上传人/频道）路由：聚合列表 + 博主固定视频类型规则 + 缺失视频检测。

博主固定视频类型：给某几个上传人指定固定视频类型（如「官方舞台」「特别舞台」「粉丝直拍」），
待整理打开详情时自动选中该类型、AI 也不再判断类型；未配置的博主照旧交给 AI。

缺失视频检测（2026-09 起）：配置了 YouTube 频道地址并开启检测的博主，可在博主页
拉取频道完整投稿快照（external_channel_videos 表），与库内 source_id 比对得到缺失清单。
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.core.config import settings
from app.core.deps import DbDep, OwnerDep, PaginationDep
from app.models.external_channel_video import ExternalChannelVideo
from app.models.music_video import MusicVideo
from app.schemas import PageResponse
from app.services import app_settings, youtube_service
from app.services.audiodb_service import ProviderError

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
    # ===== YouTube 缺失检测配置（来自博主规则）=====
    youtube_channel_url: str = ""
    missing_check_enabled: bool = False
    # ===== 缺失检测统计（快照汇总；未检测过为 None）=====
    channel_video_total: Optional[int] = None
    missing_count: Optional[int] = None


class UploaderManageResult(BaseModel):
    items: List[UploaderRead] = []
    total: int = 0
    rule_count: int = 0


class UploaderRuleRead(BaseModel):
    name: str
    video_types: List[str] = []
    youtube_channel_url: str = ""
    missing_check_enabled: bool = False


class UploaderRulesPayload(BaseModel):
    rules: List[UploaderRuleRead] = []


class UploaderRulesResult(BaseModel):
    rules: List[UploaderRuleRead] = []


class MissingVideoItem(BaseModel):
    video_id: str
    title: str = ""
    duration: Optional[int] = None
    published_at: Optional[str] = None
    is_short: bool = False
    thumbnail_path: Optional[str] = None
    url: str = ""


class MissingReport(BaseModel):
    uploader: str
    # 该博主在库内是否有 YouTube 视频（前端据此决定是否渲染整个缺失区块；
    # 非 YouTube 博主不展示，避免无关噪音）
    has_youtube_videos: bool = False
    # 规则配置回显（前端据此决定是否显示检测入口）
    channel_configured: bool = False
    missing_check_enabled: bool = False
    # 命中的规则名与原博主名「肉眼同名但写法不同」时回填（如 `MBCKpop` 命中
    # `MBC Kpop` 的配置），前端据此标注，避免用户以为配置没生效
    matched_rule_name: str = ""
    channel_video_total: int = 0
    library_count: int = 0
    missing_count: int = 0
    short_missing_count: int = 0
    last_checked_at: Optional[str] = None
    items: List[MissingVideoItem] = []


class MissingCheckRequest(BaseModel):
    uploader: str


class ChannelProbeRequest(BaseModel):
    """频道地址探测（不落库，仅校验能否解析到频道）。"""

    channel: str = ""


class ChannelProbeResult(BaseModel):
    channel_id: str = ""
    title: str = ""
    uploads_playlist: str = ""


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


def _snapshot_stats(db: DbDep, rule_map: dict[str, dict]) -> dict[str, dict]:
    """{博主名键(uploader_key): {total, missing}}：缺失按「快照 − 库内 YouTube 视频」实时比对。

    一次聚合查询拿快照全集，再与库内 (uploader, source_id) 集合在 Python 侧做差，
    避免逐博主两次往返。仅统计已配置频道地址的博主（未配置不展示快照统计）。
    """
    configured = {k for k, r in rule_map.items() if r.get("youtube_channel_url")}
    snap_rows = db.execute(
        select(
            ExternalChannelVideo.uploader_key,
            ExternalChannelVideo.video_id,
        )
    ).all()
    lib_rows = db.execute(
        select(
            MusicVideo.original_uploader,
            MusicVideo.source_id,
        )
        .where(MusicVideo.deleted_at.is_(None))
        .where(func.lower(MusicVideo.source_platform) == "youtube")
        .where(MusicVideo.source_id.is_not(None))
    ).all()
    # 快照键与库内博主名统一按 uploader_key 归一化：同名不同写法（空格 / 全角 /
    # 零宽字符）的重复记录合到同一份统计，否则完成度永远算不出来
    lib_ids: dict[str, set[str]] = {}
    for name, sid in lib_rows:
        lib_ids.setdefault(app_settings.uploader_key(name), set()).add(sid)
    stats: dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()
    for key, vid in snap_rows:
        norm = app_settings.uploader_key(key)
        if norm not in configured:
            continue
        # 历史键与新键并存时同一视频会重复出现，按 (归一化博主, video_id) 去重
        if (norm, vid) in seen:
            continue
        seen.add((norm, vid))
        s = stats.setdefault(norm, {"total": 0, "missing": 0})
        s["total"] += 1
        if vid not in lib_ids.get(norm, set()):
            s["missing"] += 1
    return stats


def _rule_map(db: DbDep) -> dict[str, dict]:
    """{博主名键(uploader_key): 规则（类型 + 频道配置）}。"""
    return {
        app_settings.uploader_key(r["name"]): {
            "video_types": list(r["video_types"]),
            "youtube_channel_url": r.get("youtube_channel_url", ""),
            "missing_check_enabled": bool(r.get("missing_check_enabled")),
        }
        for r in app_settings.read_uploader_rules(db)
    }


def _stats_fields(stats: dict, key: str) -> dict:
    s = stats.get(key)
    if not s:
        return {"channel_video_total": None, "missing_count": None}
    return {"channel_video_total": s["total"], "missing_count": s["missing"]}


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
    rule_map = _rule_map(db)
    stats = _snapshot_stats(db, rule_map)
    items = [
        UploaderRead(
            name=r.name,
            platform=r.platform,
            video_count=r.video_count,
            latest_video_id=r.latest_video_id,
            video_types=rule_map.get(app_settings.uploader_key(str(r.name)), {}).get(
                "video_types", []
            ),
            youtube_channel_url=rule_map.get(app_settings.uploader_key(str(r.name)), {}).get(
                "youtube_channel_url", ""
            ),
            missing_check_enabled=rule_map.get(app_settings.uploader_key(str(r.name)), {}).get(
                "missing_check_enabled", False
            ),
            **_stats_fields(stats, app_settings.uploader_key(str(r.name))),
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
    rule_map = {
        app_settings.uploader_key(r["name"]): {
            "video_types": list(r["video_types"]),
            "youtube_channel_url": r.get("youtube_channel_url", ""),
            "missing_check_enabled": bool(r.get("missing_check_enabled")),
        }
        for r in rules
    }
    stats = _snapshot_stats(db, rule_map)
    items: list[UploaderRead] = []
    seen: set[str] = set()
    for row in _aggregate(db):
        # seen 仍按字面（含大小写）去重：同名不同写法的记录都列出来，不隐藏任何一条
        if str(row["name"]).casefold() in seen:
            continue
        seen.add(str(row["name"]).casefold())
        # 规则与统计查表用归一化键：肉眼同名的重复记录共用同一份配置
        key = app_settings.uploader_key(str(row["name"]))
        rule = rule_map.get(key, {})
        items.append(
            UploaderRead(
                **row,
                video_types=rule.get("video_types", []),
                youtube_channel_url=rule.get("youtube_channel_url", ""),
                missing_check_enabled=rule.get("missing_check_enabled", False),
                in_library=True,
                **_stats_fields(stats, key),
            )
        )
    # 规则里但库里还没有作品的博主也要出现，否则手工添加后「看不到」
    for rule in rules:
        if rule["name"].casefold() in seen:
            continue
        seen.add(rule["name"].casefold())
        key = app_settings.uploader_key(rule["name"])
        items.append(
            UploaderRead(
                name=rule["name"],
                platform=None,
                video_count=0,
                latest_video_id=None,
                video_types=list(rule["video_types"]),
                youtube_channel_url=rule.get("youtube_channel_url", ""),
                missing_check_enabled=bool(rule.get("missing_check_enabled")),
                in_library=False,
                **_stats_fields(stats, key),
            )
        )
    # 已配置（固定类型或频道检测）的排前面，其次按视频数
    items.sort(
        key=lambda i: (
            not (i.video_types or i.youtube_channel_url),
            -i.video_count,
            i.name.casefold(),
        )
    )
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
    """整体覆盖规则列表；空 video_types 且无频道配置 / 重名 / 非法类型由服务端归一化剔除。"""
    saved = app_settings.write_uploader_rules(
        db, [r.model_dump() for r in payload.rules]
    )
    return UploaderRulesResult(rules=[UploaderRuleRead(**r) for r in saved])


# ---------------------------------------------------------------------------
# 缺失视频检测
# ---------------------------------------------------------------------------

def _has_youtube_videos(db: DbDep, name: str) -> bool:
    """该博主在库内是否有 YouTube 视频（大小写不敏感比对，与缺失比对同口径）。"""
    key = name.strip().casefold()
    total = db.execute(
        select(func.count())
        .select_from(MusicVideo)
        .where(MusicVideo.deleted_at.is_(None))
        .where(func.lower(MusicVideo.source_platform) == "youtube")
        .where(func.lower(func.coalesce(MusicVideo.original_uploader, "")) == key)
    ).scalar_one()
    return int(total) > 0


def _report(db: DbDep, uploader: str) -> MissingReport:
    name = (uploader or "").strip()
    if not name:
        raise HTTPException(400, "博主名不能为空")
    # 非 YouTube 博主：整块不渲染，直接返回空报告
    if not _has_youtube_videos(db, name):
        return MissingReport(uploader=name, has_youtube_videos=False)
    channel = app_settings.find_uploader_channel(db, name)
    configured = bool(channel and channel["youtube_channel_url"])
    if not configured:
        # 未配置频道：不返回任何快照/diff 明细（历史快照数据保留在库中，重新配置后可恢复展示）
        return MissingReport(
            uploader=name,
            has_youtube_videos=True,
            channel_configured=False,
            missing_check_enabled=bool(channel and channel["missing_check_enabled"]),
        )
    data = youtube_service.missing_report(db, name)
    return MissingReport(
        uploader=name,
        has_youtube_videos=True,
        channel_configured=True,
        missing_check_enabled=bool(channel and channel["missing_check_enabled"]),
        # 宽容匹配命中（原博主名与规则名肉眼同名但写法不同）时标注出来
        matched_rule_name=channel["name"] if channel.get("matched_by") == "loose" else "",
        **{k: v for k, v in data.items() if k != "uploader"},
    )


@router.get("/missing-videos", response_model=MissingReport)
def get_missing_videos(
    db: DbDep,
    uploader: str = Query(..., min_length=1, description="博主名"),
):
    """缺失视频报告（不出网：读快照 + 库内实时比对；检测过快照才有内容）。"""
    return _report(db, uploader)


@router.post("/missing-check", response_model=MissingReport)
def check_missing_videos(payload: MissingCheckRequest, db: DbDep, _owner: OwnerDep):
    """触发一次缺失检测：拉取频道完整投稿快照 → 返回缺失报告。"""
    name = (payload.uploader or "").strip()
    if not name:
        raise HTTPException(400, "博主名不能为空")
    channel = app_settings.find_uploader_channel(db, name)
    if not channel or not channel["youtube_channel_url"]:
        raise HTTPException(400, "该博主未配置 YouTube 频道地址（设置 · 资料库 · 博主）")
    try:
        youtube_service.sync_uploader(db, name, channel["youtube_channel_url"])
    except ProviderError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"YouTube 拉取失败：{e}") from e
    return _report(db, name)


@router.post("/channel-probe")
def probe_uploader_channel(payload: ChannelProbeRequest, _owner: OwnerDep):
    """校验 YouTube 频道地址是否可用（出网 1-2 quota，不落库）。

    供「设置 · 资料库 · 博主管理」的「测试」按钮即时确认地址能否解析、
    以及解析到的频道名是否正确（地址写错时返回 400 与原因）。
    """
    ref = (payload.channel or "").strip()
    if not ref:
        raise HTTPException(400, "请先填写 YouTube 频道地址")
    try:
        return youtube_service.probe_channel(ref)
    except ProviderError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"YouTube 请求失败：{e}") from e


@router.get("/missing-videos/{video_id}/cover")
def get_missing_video_cover(video_id: str):
    """缺失视频封面（检测时已缓存到 derived/yt-covers，不回源 YouTube）。"""
    if not video_id.isalnum():
        raise HTTPException(400, "非法的视频 ID")
    for ext in (".jpg", ".webp", ".png"):
        p = settings.youtube_cover_dir / f"{video_id}{ext}"
        if p.exists() and p.stat().st_size > 0:
            return FileResponse(
                p,
                media_type="image/webp" if ext == ".webp" else "image/jpeg",
                headers={"Cache-Control": "private, max-age=86400"},
            )
    raise HTTPException(404, "封面不存在或尚未检测")
