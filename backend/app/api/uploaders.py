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
from sqlalchemy import func, or_, select

from app.core.config import settings
from app.core.deps import DbDep, OwnerDep, PaginationDep
from app.models.external_channel_video import ExternalChannelVideo
from app.models.music_video import MusicVideo
from app.schemas import PageResponse
from app.services import app_settings, uploader_channel_service, youtube_service
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
    # 最近入库的若干条视频 id（列表页 4×4 海报墙用，最多 16 条；无视频时为空）
    recent_video_ids: List[int] = []
    # 该博主已固定的视频类型（空 = 跟随 AI）
    video_types: List[str] = []
    # 是否在库内存在视频（false = 仅手工添加的规则，库里还没有他的作品）
    in_library: bool = True
    # ===== YouTube 缺失检测配置（来自博主规则）=====
    youtube_channel_url: str = ""
    youtube_channel_id: str = ""
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
    youtube_channel_id: str = ""
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
    channel_id: str = ""
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


class BackfillChannelRequest(BaseModel):
    dry_run: bool = False
    include_rules: bool = False


def _aggregate(db: DbDep) -> list[dict]:
    """按 original_uploader + 平台聚合库内博主（不软删、名字非空）。"""
    rows = db.execute(
        select(
            MusicVideo.original_uploader.label("name"),
            MusicVideo.source_platform.label("platform"),
            MusicVideo.source_channel_id.label("source_channel_id"),
            func.count().label("video_count"),
            func.max(MusicVideo.id).label("latest_video_id"),
        )
        .where(MusicVideo.deleted_at.is_(None))
        .where(MusicVideo.original_uploader.is_not(None))
        .where(MusicVideo.original_uploader != "")
        .group_by(
            MusicVideo.original_uploader,
            MusicVideo.source_platform,
            MusicVideo.source_channel_id,
        )
    ).all()
    return [
        {
            "name": r.name,
            "platform": r.platform,
            "source_channel_id": r.source_channel_id,
            "video_count": r.video_count,
            "latest_video_id": r.latest_video_id,
        }
        for r in rows
    ]


def _snapshot_stats(db: DbDep, rule_map: dict[str, dict]) -> dict[str, dict]:
    """{博主键 或 channel_id: {total, missing}}：频道身份优先，名字键兜底。

    同一 YouTube 频道改名后会有多个 uploader_key，但 channel_id 相同：
    - 快照行按 channel_id 归组（sync 时已写入 channel_id）；
    - 库内视频按 source_channel_id 归组（backfill 后覆盖新旧名字）；
    - 没有 channel_id 的老数据退回 uploader_key 名字键。
    """
    key_to_cid = {
        key: str(rule.get("youtube_channel_id") or "").strip()
        for key, rule in rule_map.items()
        if rule.get("youtube_channel_id")
    }
    configured_keys = {
        key for key, rule in rule_map.items()
        if rule.get("youtube_channel_url") or rule.get("youtube_channel_id")
    }
    configured_cids = {key_to_cid[k] for k in configured_keys if k in key_to_cid}

    snap_rows = db.execute(
        select(
            ExternalChannelVideo.uploader_key,
            ExternalChannelVideo.channel_id,
            ExternalChannelVideo.video_id,
        )
    ).all()
    lib_rows = db.execute(
        select(
            MusicVideo.original_uploader,
            MusicVideo.source_channel_id,
            MusicVideo.source_id,
        )
        .where(MusicVideo.deleted_at.is_(None))
        .where(func.lower(MusicVideo.source_platform) == "youtube")
        .where(MusicVideo.source_id.is_not(None))
    ).all()
    lib_ids_by_key: dict[str, set[str]] = {}
    lib_ids_by_cid: dict[str, set[str]] = {}
    for name, cid, sid in lib_rows:
        lib_ids_by_key.setdefault(app_settings.uploader_key(name), set()).add(sid)
        if cid:
            lib_ids_by_cid.setdefault(str(cid), set()).add(sid)

    stats: dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()
    for raw_key, raw_cid, vid in snap_rows:
        norm = app_settings.uploader_key(raw_key)
        cid = str(raw_cid or "").strip() or key_to_cid.get(norm, "")
        if cid:
            if cid not in configured_cids and norm not in configured_keys:
                continue
            identity = f"cid:{cid}"
        else:
            if norm not in configured_keys:
                continue
            identity = norm
        if (identity, vid) in seen:
            continue
        seen.add((identity, vid))
        s = stats.setdefault(identity, {"total": 0, "missing": 0})
        s["total"] += 1
        lib_ids = set(lib_ids_by_key.get(norm, set()))
        if cid:
            lib_ids |= lib_ids_by_cid.get(cid, set())
        if vid not in lib_ids:
            s["missing"] += 1
    # 名字键的查询入口：同一 channel_id 下多个名字共享同一份统计
    for key, cid in key_to_cid.items():
        if cid and f"cid:{cid}" in stats:
            stats[key] = stats[f"cid:{cid}"]
    return stats

def _rule_map(db: DbDep) -> dict[str, dict]:
    """{博主名键(uploader_key): 规则（类型 + 频道配置）}。"""
    return {
        app_settings.uploader_key(r["name"]): {
            "video_types": list(r["video_types"]),
            "youtube_channel_url": r.get("youtube_channel_url", ""),
            "youtube_channel_id": r.get("youtube_channel_id", ""),
            "missing_check_enabled": bool(r.get("missing_check_enabled")),
        }
        for r in app_settings.read_uploader_rules(db)
    }


def _stats_fields(stats: dict, key: str) -> dict:
    s = stats.get(key)
    if not s:
        return {"channel_video_total": None, "missing_count": None}
    return {"channel_video_total": s["total"], "missing_count": s["missing"]}


def _group_aggregate(
    db: DbDep, rules: list[dict], rule_map: dict[str, dict]
) -> list[dict]:
    """把 _aggregate 的行按 channel_id（无则名字）合并成「一个频道一行」。

    返回 dict：name / platform / source_channel_id / video_count / latest_video_id /
    names / rule / stat_key。同一频道改名前后共用一行，规则优先取 channel_id 命中的那条。
    """
    aggregate = _aggregate(db)
    name_to_cid: dict[str, str] = {}
    for row in aggregate:
        cid = str(row.get("source_channel_id") or "").strip()
        if cid:
            name_to_cid[app_settings.uploader_key(str(row["name"]))] = cid

    groups: dict[str, dict] = {}
    for row in aggregate:
        name = str(row["name"])
        cid = str(row.get("source_channel_id") or "").strip() or name_to_cid.get(
            app_settings.uploader_key(name), ""
        )
        gkey = f"cid:{cid}" if cid else f"name:{name.casefold()}"
        grp = groups.get(gkey)
        if grp is None:
            groups[gkey] = {
                "name": name,
                "platform": row["platform"],
                "source_channel_id": cid,
                "video_count": int(row["video_count"] or 0),
                "latest_video_id": row["latest_video_id"],
                "names": [name],
                "key": gkey,
            }
            continue
        grp["video_count"] += int(row["video_count"] or 0)
        if row["latest_video_id"] and (
            not grp["latest_video_id"] or row["latest_video_id"] > grp["latest_video_id"]
        ):
            grp["latest_video_id"] = row["latest_video_id"]
            grp["name"] = name
        if name not in grp["names"]:
            grp["names"].append(name)

    def _rule_for(grp: dict) -> Optional[dict]:
        cid = grp["source_channel_id"]
        if cid:
            for rule in rules:
                if (rule.get("youtube_channel_id") or "") == cid:
                    return rule
        for nm in grp["names"]:
            rule = rule_map.get(app_settings.uploader_key(nm))
            if rule:
                return rule
        return None

    out: list[dict] = []
    covered: set[str] = set()
    covered_names: set[str] = set()
    for grp in groups.values():
        rule = _rule_for(grp) or {}
        name = rule.get("name") or grp["name"]
        cid = grp["source_channel_id"] or (rule.get("youtube_channel_id") or "")
        out.append(
            {
                **grp,
                "name": name,
                "source_channel_id": cid,
                "rule": rule,
                "stat_key": f"cid:{cid}" if cid else app_settings.uploader_key(name),
            }
        )
        covered.add(grp["key"])
        covered_names.update(app_settings.uploader_key(n) for n in grp["names"])

    # 规则里但库里还没有作品的博主也要出现，否则手工添加后「看不到」
    for rule in rules:
        cid = (rule.get("youtube_channel_id") or "").strip()
        key = f"cid:{cid}" if cid else f"name:{rule['name'].casefold()}"
        # 规则名已经并入某个频道组（改名后 cid 组）时不要再生一条 0 视频行
        if key in covered or app_settings.uploader_key(rule["name"]) in covered_names:
            continue
        covered.add(key)
        out.append(
            {
                "name": rule["name"],
                "platform": None,
                "source_channel_id": cid,
                "video_count": 0,
                "latest_video_id": None,
                "names": [rule["name"]],
                "key": key,
                "rule": rule,
                "stat_key": key if cid else app_settings.uploader_key(rule["name"]),
            }
        )
    return out


@router.get("", response_model=PageResponse[UploaderRead])
def list_uploaders(
    db: DbDep,
    pagination: PaginationDep,
    q: Optional[str] = Query(None, description="按频道名搜索"),
):
    """聚合所有非空 original_uploader；同一 channel_id 的改名前后名字合并为一行。"""
    rules = app_settings.read_uploader_rules(db)
    rule_map = {
        app_settings.uploader_key(r["name"]): {
            "video_types": list(r["video_types"]),
            "youtube_channel_url": r.get("youtube_channel_url", ""),
            "youtube_channel_id": r.get("youtube_channel_id", ""),
            "missing_check_enabled": bool(r.get("missing_check_enabled")),
        }
        for r in rules
    }
    stats = _snapshot_stats(db, rule_map)
    groups = _group_aggregate(db, rules, rule_map)
    if q:
        like = q.strip().casefold()
        groups = [
            g for g in groups if any(like in str(n).casefold() for n in g["names"])
        ]
    groups.sort(
        key=lambda g: (
            not (
                (g.get("rule") or {}).get("video_types")
                or (g.get("rule") or {}).get("youtube_channel_url")
            ),
            -int(g["video_count"]),
            str(g["name"]).casefold(),
        )
    )
    total = len(groups)
    page_groups = groups[pagination.offset : pagination.offset + pagination.limit]

    # 4×4 海报墙：当前页每个频道的最近 16 条视频 id
    names = sorted({n for g in page_groups for n in g["names"]})
    recent_by_name: dict[str, list[int]] = {}
    if names:
        for name, vid in db.execute(
            select(MusicVideo.original_uploader, MusicVideo.id)
            .where(MusicVideo.deleted_at.is_(None))
            .where(MusicVideo.original_uploader.in_(names))
            .order_by(MusicVideo.id.desc())
        ).all():
            recent_by_name.setdefault(str(name), []).append(int(vid))
    items: list[UploaderRead] = []
    for grp in page_groups:
        rule = grp.get("rule") or {}
        merged_recent: list[int] = []
        for nm in grp["names"]:
            for vid in recent_by_name.get(str(nm), []):
                if vid not in merged_recent:
                    merged_recent.append(vid)
        merged_recent.sort(reverse=True)
        items.append(
            UploaderRead(
                name=grp["name"],
                platform=grp["platform"],
                video_count=grp["video_count"],
                latest_video_id=grp["latest_video_id"],
                recent_video_ids=merged_recent[:16],
                video_types=list(rule.get("video_types") or []),
                youtube_channel_url=rule.get("youtube_channel_url", ""),
                youtube_channel_id=grp["source_channel_id"],
                missing_check_enabled=bool(rule.get("missing_check_enabled")),
                in_library=grp["video_count"] > 0,
                **_stats_fields(stats, grp["stat_key"]),
            )
        )
    return PageResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )

@router.get("/manage", response_model=UploaderManageResult)
def manage_uploaders(db: DbDep, q: Optional[str] = Query(None, description="按博主名搜索")):
    """博主管理列表：库内博主 ∪ 已配置规则的博主（规则优先、视频数降序）。

    频道身份优先：同一 YouTube channel_id 下的改名前后名字合并为一行；
    没有 channel_id 的老数据仍按名字（大小写不敏感）去重。
    """
    rules = app_settings.read_uploader_rules(db)
    rule_map = {
        app_settings.uploader_key(r["name"]): {
            "video_types": list(r["video_types"]),
            "youtube_channel_url": r.get("youtube_channel_url", ""),
            "youtube_channel_id": r.get("youtube_channel_id", ""),
            "missing_check_enabled": bool(r.get("missing_check_enabled")),
        }
        for r in rules
    }
    stats = _snapshot_stats(db, rule_map)
    groups = _group_aggregate(db, rules, rule_map)
    if q:
        like = q.strip().casefold()
        groups = [
            g for g in groups if any(like in str(n).casefold() for n in g["names"])
        ]
    items: list[UploaderRead] = []
    for grp in groups:
        rule = grp.get("rule") or {}
        items.append(
            UploaderRead(
                name=grp["name"],
                platform=grp["platform"],
                video_count=grp["video_count"],
                latest_video_id=grp["latest_video_id"],
                video_types=list(rule.get("video_types") or []),
                youtube_channel_url=rule.get("youtube_channel_url", ""),
                youtube_channel_id=grp["source_channel_id"],
                missing_check_enabled=bool(rule.get("missing_check_enabled")),
                in_library=grp["video_count"] > 0,
                **_stats_fields(stats, grp["stat_key"]),
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

def _channel_id_of_uploader(db: DbDep, name: str) -> str:
    """库内该名字对应的 YouTube channel_id（用于改名后按频道归组）。

    用 uploader_key 比对而不是 SQL 等值：SQLite 的 lower() 只认 ASCII，
    全角/空白/大小写变体在 SQL 里会被当成两个名字。
    """
    key = app_settings.uploader_key(name)
    rows = db.execute(
        select(MusicVideo.original_uploader, MusicVideo.source_channel_id)
        .where(MusicVideo.deleted_at.is_(None))
        .where(func.lower(MusicVideo.source_platform) == "youtube")
        .where(MusicVideo.original_uploader.is_not(None))
        .where(MusicVideo.source_channel_id.is_not(None))
        .distinct()
    ).all()
    for nm, cid in rows:
        if app_settings.uploader_key(nm) == key and cid:
            return str(cid).strip()
    return ""


def _has_youtube_videos(db: DbDep, name: str) -> bool:
    """该博主在库内是否有 YouTube 视频（名字或 channel_id 命中都算）。"""
    key = name.strip().casefold()
    cid = _channel_id_of_uploader(db, name)
    stmt = (
        select(func.count())
        .select_from(MusicVideo)
        .where(MusicVideo.deleted_at.is_(None))
        .where(func.lower(MusicVideo.source_platform) == "youtube")
    )
    if cid:
        stmt = stmt.where(
            or_(
                func.lower(func.coalesce(MusicVideo.original_uploader, "")) == key,
                MusicVideo.source_channel_id == cid,
            )
        )
    else:
        stmt = stmt.where(func.lower(func.coalesce(MusicVideo.original_uploader, "")) == key)
    return int(db.execute(stmt).scalar_one()) > 0


def _report(db: DbDep, uploader: str) -> MissingReport:
    name = (uploader or "").strip()
    if not name:
        raise HTTPException(400, "博主名不能为空")
    # 非 YouTube 博主：整块不渲染，直接返回空报告
    if not _has_youtube_videos(db, name):
        return MissingReport(uploader=name, has_youtube_videos=False)
    cid = _channel_id_of_uploader(db, name)
    channel = app_settings.find_uploader_channel(db, name, channel_id=cid)
    configured = bool(channel and channel["youtube_channel_url"])
    if not configured:
        # 未配置频道：不返回任何快照/diff 明细（历史快照数据保留在库中，重新配置后可恢复展示）
        return MissingReport(
            uploader=name,
            has_youtube_videos=True,
            channel_configured=False,
            channel_id=cid or (channel or {}).get("youtube_channel_id", ""),
            missing_check_enabled=bool(channel and channel["missing_check_enabled"]),
        )
    data = youtube_service.missing_report(
        db, name, channel_id=channel.get("youtube_channel_id") or cid
    )
    return MissingReport(
        uploader=name,
        has_youtube_videos=True,
        channel_configured=True,
        channel_id=channel.get("youtube_channel_id") or cid,
        missing_check_enabled=bool(channel and channel["missing_check_enabled"]),
        # 非逐字命中（同名不同写法 / 改名后的 channel_id 命中）时回填规则名
        matched_rule_name=(
            channel["name"] if channel.get("matched_by") in {"loose", "channel_id"} else ""
        ),
        **{k: v for k, v in data.items() if k not in {"uploader", "channel_id"}},
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
    """校验 YouTube 频道地址是否可用（出网 1-2 quota，端点本身不落库）。

    供「设置 · 资料库 · 博主管理」的「测试」按钮即时确认地址能否解析、
    以及解析到的频道名 / channel_id 是否正确（地址写错时返回 400 与原因）。
    前端测试成功后会把这个 channel_id 随博主规则一起保存。
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


@router.post("/backfill-channel-ids")
def backfill_channel_ids(
    db: DbDep,
    _owner: OwnerDep,
    payload: Optional[BackfillChannelRequest] = None,
):
    """从 .info.json 侧车回填库内视频的 YouTube channel_id。

    只补 `source_channel_id` 为空的行；`include_rules=true` 时顺带探测已配置频道 URL
    的博主规则。**不会修改 missing_check_enabled**（缺失检测仍由用户手动勾选）。
    """
    body = payload or BackfillChannelRequest()
    video_stats = uploader_channel_service.backfill_video_channel_ids(
        db, dry_run=body.dry_run
    )
    rule_stats = {"rules": 0, "updated": 0, "skipped": 0, "errors": 0}
    if body.include_rules and not body.dry_run:
        rule_stats = uploader_channel_service.backfill_rule_channel_ids(
            db, youtube_service.probe_channel
        )
    return {"videos": video_stats, "rules": rule_stats}


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
