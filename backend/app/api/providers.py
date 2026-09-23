"""外部元数据 Provider 路由：聚合检索与详情（TheAudioDB / Deezer / iTunes / Wikidata / TMDB）。"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, Query

from app.core.deps import DbDep
from app.schemas import AlbumExternalCandidate, AlbumTracklist
from app.schemas.provider import ProviderDetail, ProviderSearchItem, TmdbTestRequest
from app.services import album_external_service, app_settings, external_providers, tmdb_service
from app.services.audiodb_service import ProviderError

router = APIRouter(prefix="/providers", tags=["providers"])


@router.post("/tmdb/test")
def test_tmdb(payload: TmdbTestRequest, db: DbDep):
    """测试 TMDB API Key 连通性（调 /configuration，不落库）。

    请求未带 Key 时用已保存配置，避免 GET 脱敏后「测试连接」误报失败。
    """
    key = (payload.api_key or "").strip()
    if not key:
        key = (app_settings.read_all(db).tmdb.api_key or "").strip()
    try:
        return tmdb_service.test_connection(key)
    except ProviderError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"TMDB 连接失败：{e}") from e


@router.get("/albums/search", response_model=List[AlbumExternalCandidate])
def search_album_candidates(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    source: str = Query("itunes", pattern="^(itunes|deezer)$", description="数据源"),
):
    """按关键词搜索外部专辑候选（iTunes / Deezer，免 Key）。"""
    try:
        return album_external_service.search_albums(q, source)
    except ProviderError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"专辑搜索失败：{e}") from e


@router.get("/albums/{external_id}/tracklist", response_model=AlbumTracklist)
def get_album_tracklist(external_id: str):
    """按带来源前缀的外部专辑 ID 拉取曲目列表（itunes:123 / deezer:456）。"""
    try:
        return album_external_service.get_tracklist(external_id)
    except ProviderError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"曲目获取失败：{e}") from e


@router.get("/audiodb/search", response_model=List[ProviderSearchItem])
def search_audiodb(
    q: str = Query(..., min_length=1, description="搜索名称"),
    type: str = Query("artist", pattern="^(artist|group)$", description="目标类型"),
):
    """按名称聚合检索外部候选（外部 id 带来源前缀、名称、缩略图、简介摘要）。"""
    try:
        return external_providers.search_candidates(q, type)
    except ProviderError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"站点获取失败：{e}") from e


@router.get("/audiodb/{external_id}", response_model=ProviderDetail)
def get_audiodb_detail(external_id: str):
    """按外部 id 获取详情（按前缀分发来源）：全简介（优先中文）、图片 URL。"""
    try:
        return external_providers.get_detail(external_id)
    except ProviderError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"站点获取失败：{e}") from e
