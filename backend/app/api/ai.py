"""AI 能力路由：入库元数据建议等。"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import (
    Album,
    Artist,
    Group,
    MusicVideo,
    Song,
)
from app.schemas.ai import (
    AiAnalyzeRequest,
    AiAnalyzeResult,
    AiEntityTaglineRequest,
    AiEntityTaglineResult,
    AiSearchAlbumRequest,
    AiSearchAlbumResult,
    AiSuggestRequest,
    AiSuggestResult,
    AiSuggestSocialRequest,
    AiSuggestSocialResult,
)
from app.services import app_settings
from app.services.ai_context import build_album_candidates, build_library_candidates
from app.services.ai_service import (
    analyze_entity,
    build_system_prompt,
    fill_saved_ai_cfg,
    generate_entity_tagline,
    search_albums,
    suggest_ingest,
    suggest_social_links,
    _ingest_ai_cfg,
)

router = APIRouter(prefix="/ai", tags=["ai"])

logger = logging.getLogger(__name__)


def _resolve_relation_names(db: Session, current: dict) -> dict:
    """把表单中已选关联的 id 解析为官方名称，供 AI 在标题建议中引用。"""
    resolved: dict = {}

    def names(model, ids, key):
        if not isinstance(ids, list) or not ids:
            return
        rows = db.scalars(
            select(model).where(model.id.in_(ids), model.deleted_at.is_(None))
        ).all()
        resolved[key] = [r.name for r in rows if r and r.name]

    names(Song, current.get("song_ids"), "song_names")
    names(Album, current.get("album_ids"), "album_names")
    names(Artist, current.get("artist_ids"), "artist_names")
    names(Group, current.get("group_ids"), "group_names")
    return resolved


def _apply_uploader_rule(
    db: Session, result: dict, current: dict, context: dict
) -> dict:
    """博主固定视频类型规则优先于 AI：命中上传人时直接覆盖类型。

    待整理页在打开详情时已按规则自动选中，这里再兜一次，保证「规则命中的博主
    不需要 AI 判断类型」在任何调用方（含视频编辑弹窗）都成立。

    ⚠ `suggest_ingest()` 返回的是 **普通 dict**（`ai_service._normalize()` 的产物），
    不是 pydantic 模型 —— 必须用下标赋值。历史写法 `result.video_types = ...`
    会 `AttributeError` 冒泡成 HTTP 500（v3.2.6~3.2.12 的线上缺陷）。
    """
    uploader = (
        current.get("original_uploader")
        or context.get("uploader")
        or context.get("channel")
    )
    types = list(app_settings.find_uploader_types(db, uploader))
    if not types:
        return result
    if isinstance(result, dict):
        result["video_types"] = types
        result["video_type"] = types[0]
    else:  # 兜底：万一将来真改成返回模型实例
        result.video_types = types  # type: ignore[attr-defined]
        result.video_type = types[0]  # type: ignore[attr-defined]
    return result


@router.post("/suggest", response_model=AiSuggestResult, response_model_exclude_none=True)
def ai_suggest_endpoint(
    payload: AiSuggestRequest,
    db: Session = Depends(get_db),
):
    """调用用户配置的入库 AI，生成入库元数据建议。"""
    current = payload.current or {}
    context = dict(payload.context or {})
    resolved = _resolve_relation_names(db, current)
    if resolved:
        context["resolved_relations"] = resolved
    # 本库候选：组合/成员名单 + 按标题模糊匹配的艺人与歌曲，辅助 AI 做别名匹配
    query_text = " ".join(
        str(x) for x in (
            context.get("file_name"),
            context.get("title"),
            context.get("fulltitle"),
        ) if x
    )
    from app.services.app_settings import read_ingest_match_mode

    seed_groups = [i for i in (current.get("group_ids") or []) if isinstance(i, int)]
    library_context = build_library_candidates(
        db,
        query_text,
        match_mode=read_ingest_match_mode(db),
        group_ids=seed_groups,
    )
    # 中文简介规则：设置页自定义 > 后端内置默认（原版自由口径）
    desc_rule = app_settings.read_all(db).ingest_ai.desc_prompt
    try:
        result = suggest_ingest(
            fill_saved_ai_cfg(payload.model_dump()),
            context,
            current,
            library_context=library_context,
            aux_notes=payload.aux_notes,
            system_prompt=build_system_prompt(desc_rule),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"AI 建议失败: {e}") from e
    try:
        return _apply_uploader_rule(db, result, current, context)
    except Exception as e:  # noqa: BLE001
        # 规则只是叠加在 AI 结果之上的「覆盖层」，它出问题不应该把整条
        # AI 填写链路打成 500 —— 记日志并原样返回 AI 建议。
        logger.warning("应用博主固定类型规则失败，已忽略：%s", e)
        return result


@router.post("/search-albums", response_model=AiSearchAlbumResult, response_model_exclude_none=True)
def ai_search_albums_endpoint(
    payload: AiSearchAlbumRequest,
    db: Session = Depends(get_db),
):
    """调用用户配置的 AI，专门识别视频对应的专辑名（供查重确认后关联）。"""
    current = payload.current or {}
    context = dict(payload.context or {})
    resolved = _resolve_relation_names(db, current)
    if resolved:
        context["resolved_relations"] = resolved
    # 库内专辑候选：这些艺人/组合的影像已关联过的专辑，辅助 AI 优先从中选择
    album_candidates = build_album_candidates(
        db,
        [i for i in (current.get("artist_ids") or []) if isinstance(i, int)],
        [i for i in (current.get("group_ids") or []) if isinstance(i, int)],
    )
    try:
        return search_albums(
            fill_saved_ai_cfg(payload.model_dump()),
            context,
            current,
            db,
            payload.song_names or [],
            payload.artist_names or [],
            payload.group_names or [],
            album_candidates=album_candidates,
            aux_notes=payload.aux_notes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"AI 专辑搜索失败: {e}") from e


@router.post("/analyze-entity", response_model=AiAnalyzeResult)
def ai_analyze_entity_endpoint(
    payload: AiAnalyzeRequest,
    db: Session = Depends(get_db),
):
    """调用用户配置的 AI，分析并自动填充数据库编辑表单。"""
    try:
        return analyze_entity(
            fill_saved_ai_cfg(payload.model_dump()),
            payload.entity_type,
            payload.current or {},
            payload.memberships or [],
            payload.group_names or [],
            db,
            only_fields=payload.only_fields,
            group_id=payload.group_id,
            aux_notes=payload.aux_notes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"AI 分析失败: {e}") from e


def _count_entity_videos(db: Session, link_table_name: str, link_col_name: str, entity_id: int) -> int:
    """统计该艺人/组合关联的库内视频数（软删除与未入库不计）。"""
    link_table = Artist.__table__.metadata.tables.get(link_table_name)
    if link_table is None:
        return 0
    link_col = link_table.c[link_col_name]
    return int(
        db.scalar(
            select(func.count(MusicVideo.id))
            .select_from(MusicVideo)
            .join(link_table, link_table.c.music_video_id == MusicVideo.id)
            .where(
                link_col == entity_id,
                MusicVideo.deleted_at.is_(None),
                MusicVideo.ingestion_status == "library",
            )
        )
        or 0
    )


def _build_entity_tagline_context(db: Session, entity_type: str, entity_id: int) -> tuple:
    """读取艺人/组合真实资料组装 AI 上下文，返回 (obj, context)。"""
    if entity_type == "artist":
        model = Artist
        link = ("music_video_artists", "artist_id")
    elif entity_type == "group":
        model = Group
        link = ("music_video_groups", "group_id")
    else:
        raise HTTPException(400, "entity_type 仅支持 artist / group")
    obj = db.scalar(
        select(model).where(model.id == entity_id, model.deleted_at.is_(None))
    )
    if obj is None:
        raise HTTPException(404, "艺人/组合不存在或已删除")

    description = (obj.description or "").strip()
    context = {
        "entity_type": "艺人" if entity_type == "artist" else "组合",
        "name": obj.name,
        "chinese_name": obj.chinese_name,
        "stage_name": getattr(obj, "stage_name", None),
        "occupation": getattr(obj, "occupation", None),
        "group_type": getattr(obj, "group_type", None),
        "debut_date": obj.debut_date.isoformat() if obj.debut_date else None,
        "video_count": _count_entity_videos(db, link[0], link[1], entity_id),
        "description": description[:600] if description else None,
    }
    return obj, context


@router.post("/entity-tagline", response_model=AiEntityTaglineResult)
def ai_entity_tagline_endpoint(
    payload: AiEntityTaglineRequest,
    db: Session = Depends(get_db),
):
    """调用用户配置的 AI，为艺人/组合生成一句话简介并写回。"""
    obj, context = _build_entity_tagline_context(db, payload.entity_type, payload.entity_id)
    # 提示词优先级：请求携带 > 设置页自定义 > 后端内置默认
    prompt = (payload.prompt or "").strip()
    if not prompt:
        saved = app_settings.read_all(db).ingest_ai.tagline_prompt
        prompt = (saved or "").strip()
    try:
        result = generate_entity_tagline(
            fill_saved_ai_cfg(payload.model_dump()), context, prompt or None
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"AI 一句话简介生成失败: {e}") from e
    obj.tagline = result["tagline"]
    db.commit()
    return AiEntityTaglineResult(tagline=result["tagline"])


@router.post("/suggest-social", response_model=AiSuggestSocialResult, response_model_exclude_none=True)
def ai_suggest_social_endpoint(
    payload: AiSuggestSocialRequest,
    db: Session = Depends(get_db),
):
    """提议艺人/组合社交媒体链接（只读，不写库；前端确认后再 PATCH）。"""
    obj, context = _build_entity_tagline_context(db, payload.entity_type, payload.entity_id)
    # 附带已有链接，避免 AI 重复提议；仍只返回 links
    context["existing_social_media"] = getattr(obj, "social_media", None)
    cfg = fill_saved_ai_cfg(payload.model_dump())
    if payload.use_saved_ingest_ai or not (cfg.get("base_url") and cfg.get("model")):
        try:
            saved = _ingest_ai_cfg()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"读取入库 AI 设置失败: {e}") from e
        if saved.get("enabled") is False and not (payload.base_url and payload.model):
            raise HTTPException(400, "请先在设置中启用并配置「数据入库 AI」")
    try:
        result = suggest_social_links(cfg, context)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"AI 社交媒体搜索失败: {e}") from e
    return AiSuggestSocialResult(
        links=result.get("links") or {},
        basis=result.get("basis") or "",
        sources=result.get("sources") or [],
    )
