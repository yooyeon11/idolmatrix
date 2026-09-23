"""TMDB 艺人资料源：头像（profiles 多图自选）+ 简介。仅艺人，TMDB 无组合实体。

设置存在 SQLite app_setting（key=tmdb：enabled/api_key）。服务层拿不到
db session，这里做 TTL 缓存（同 proxy_config 模式）；设置写入时由
app_settings.write_sections 立即刷新。所有请求经 proxy_config.urlopen，
自动套用站点获取代理。

图片尺寸：搜索缩略图 w185；头像候选 h632（竖版头像专用尺寸，画质适中）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services import proxy_config
from app.services.audiodb_service import ProviderError

logger = logging.getLogger(__name__)

_TMDB_API = "https://api.themoviedb.org/3"
# 图片站基址多年未变，官方文档亦按此写死；避免每次详情多打一次 /configuration
_IMAGE_BASE = "https://image.tmdb.org/t/p"
_THUMB_SIZE = "w185"
_PROFILE_SIZE = "h632"
_SOURCE = "TMDB"

_TTL_SECONDS = 15.0

_lock = threading.Lock()
_state: Dict[str, Any] = {"enabled": False, "api_key": ""}
_loaded_at: float = 0.0


def set_credentials(enabled: bool, api_key: str) -> None:
    """直接更新凭据状态（设置写入后立即生效，绕过 TTL）。"""
    global _loaded_at
    with _lock:
        _state["enabled"] = bool(enabled)
        _state["api_key"] = (api_key or "").strip()
        _loaded_at = time.monotonic()


def current() -> Dict[str, Any]:
    """返回当前凭据状态副本（供诊断/测试）。"""
    with _lock:
        return dict(_state)


def _credentials() -> Dict[str, Any]:
    global _loaded_at
    now = time.monotonic()
    if now - _loaded_at >= _TTL_SECONDS:
        try:
            # 延迟导入避免循环依赖
            from app.core.database import SessionLocal
            from app.models.app_setting import AppSetting

            with SessionLocal() as db:
                row = db.get(AppSetting, "tmdb")
                blob = row.value if row is not None and isinstance(row.value, dict) else {}
            set_credentials(bool(blob.get("enabled")), str(blob.get("api_key") or ""))
        except Exception as e:  # noqa: BLE001
            # 读库失败（如启动初期建表未完成）不致命，维持当前状态并推迟下次重试
            logger.debug("[tmdb] 读取设置失败，维持当前状态: %s", e)
            with _lock:
                _loaded_at = time.monotonic()
    with _lock:
        return dict(_state)


def _http_json(url: str, params: Optional[dict] = None, timeout: Optional[int] = None, error_hint: str = "TMDB") -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    timeout = timeout if timeout is not None else settings.audiodb_timeout
    try:
        from app.services import audiodb_service

        req = urllib.request.Request(url, headers={"User-Agent": audiodb_service._UA})
        with proxy_config.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise ProviderError(f"{error_hint} 请求失败（HTTP {resp.status}）")
            return json.loads(resp.read().decode("utf-8"))
    except ProviderError:
        raise
    except TimeoutError as e:
        raise ProviderError(f"{error_hint} 请求超时，请稍后重试") from e
    except Exception as e:  # noqa: BLE001
        raise ProviderError(f"{error_hint} 请求失败：{e}") from e


def _enabled_credentials() -> Dict[str, Any]:
    creds = _credentials()
    if not (creds["enabled"] and creds["api_key"]):
        raise ProviderError("TMDB 未启用或未配置 API Key")
    return creds


# ---------------------------------------------------------------------------
# 检索 / 详情
# ---------------------------------------------------------------------------

def search_candidates(q: str, type_: str) -> List[dict]:
    """供 external_providers 聚合调用：组合不支持（返回空），未启用静默跳过。"""
    if (type_ or "artist") != "artist":
        return []
    creds = _credentials()
    if not (creds["enabled"] and creds["api_key"]):
        return []
    data = _http_json(
        f"{_TMDB_API}/search/person",
        {
            "api_key": creds["api_key"],
            "query": q,
            "language": "zh-CN",
            "include_adult": "false",
        },
    )
    out: List[dict] = []
    for r in (data.get("results") or [])[:5]:
        pid = r.get("id")
        name = (r.get("name") or "").strip()
        if not pid or not name:
            continue
        profile = r.get("profile_path")
        known_for = "、".join(
            ((k.get("name") or k.get("title") or "").strip() for k in (r.get("known_for") or [])[:3] if k.get("name") or k.get("title"))
        )
        out.append(
            {
                "external_id": f"tmdb:{pid}",
                "name": name,
                "kind": "artist",
                "thumbnail": f"{_IMAGE_BASE}/{_THUMB_SIZE}{profile}" if profile else None,
                # 代表作用作摘要，辅助人工核对是否同名同人
                "bio_excerpt": f"代表作：{known_for}" if known_for else None,
                "source": _SOURCE,
            }
        )
    return out


def _profile_urls(profiles: List[dict]) -> List[str]:
    return [f"{_IMAGE_BASE}/{_PROFILE_SIZE}{p.get('file_path')}" for p in profiles or [] if p.get("file_path")]


def _fetch_biography(api_key: str, person_id: str, language: str) -> dict:
    return _http_json(
        f"{_TMDB_API}/person/{person_id}",
        {
            "api_key": api_key,
            "language": language,
            "append_to_response": "images",
            # profiles 多数无语言标记，需放行 null 否则会被语言过滤掉
            "include_image_language": "zh,en,null",
        },
    )


def get_detail(external_id: str) -> dict:
    """按 TMDB 人物 id 取详情：简介（优先中文）+ profiles 头像画廊。"""
    creds = _enabled_credentials()
    person_id = (external_id or "").strip()
    if not person_id:
        raise ProviderError("缺少 TMDB 人物 ID")
    data = _fetch_biography(creds["api_key"], person_id, "zh-CN")
    if not data.get("id"):
        raise ProviderError("未找到该 TMDB 条目")
    if not (data.get("biography") or "").strip():
        # 无中文简介时回退英文原文
        data = _fetch_biography(creds["api_key"], person_id, "en-US")
    profiles = _profile_urls((data.get("images") or {}).get("profiles") or [])
    if not profiles and data.get("profile_path"):
        profiles = [f"{_IMAGE_BASE}/{_PROFILE_SIZE}{data['profile_path']}"]
    meta = " · ".join(
        str(b).strip()
        for b in (data.get("known_for_department"), data.get("birthday"), data.get("place_of_birth"))
        if b
    )
    return {
        "external_id": f"tmdb:{person_id}",
        "name": (data.get("name") or "未知").strip(),
        "kind": "artist",
        "biography": (data.get("biography") or "").strip() or None,
        "biography_lang": "zh" if (data.get("biography") or "").strip() else None,
        "thumb": profiles[0] if profiles else None,
        "logo": None,
        "fanart": None,
        "banner": None,
        "wide": None,
        "gallery": profiles,
        # 职业/生日/出生地，供与库内艺人生日人工核对
        "meta": meta or None,
    }


# ---------------------------------------------------------------------------
# 连接测试
# ---------------------------------------------------------------------------

def test_connection(api_key: str) -> dict:
    """校验 API Key（调 /configuration，轻量只读）。"""
    key = (api_key or "").strip()
    if not key:
        raise ProviderError("请先填写 API Key")
    cfg = _http_json(f"{_TMDB_API}/configuration", {"api_key": key})
    image_base = (cfg.get("images") or {}).get("base_url")
    if not image_base:
        raise ProviderError("TMDB 返回内容异常，请检查 API Key 是否有效")
    return {"ok": True, "image_base": image_base}
