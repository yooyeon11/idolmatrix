"""TheAudioDB 站点获取服务：检索候选、获取简介/头像、下载图片到本地。

全部使用标准库 urllib，不引入额外依赖。出错时抛出 ProviderError，
message 面向用户展示（中文），供路由转成 HTTPException。
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from app.core.config import settings
from app.services import proxy_config

logger = logging.getLogger(__name__)

_UA = "Kpop-Media-Library/0.1"

# 允许下载的图片类型：Content-Type -> 扩展名
_IMAGE_EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


class ProviderError(Exception):
    """站点获取的业务错误；message 面向用户（中文）。"""


def _build_base() -> str:
    key = (settings.audiodb_api_key or "").strip()
    if not key:
        raise ProviderError("未配置 TheAudioDB API Key，请在 .env 中设置 AUDIODB_API_KEY")
    base = (settings.audiodb_base_url or "").strip().rstrip("/")
    if not base:
        raise ProviderError("未配置 TheAudioDB Base URL，请在 .env 中设置 AUDIODB_BASE_URL")
    return f"{base}/{key}"


def _get_json(url: str, timeout: Optional[int] = None) -> dict:
    timeout = timeout if timeout is not None else settings.audiodb_timeout
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with proxy_config.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise ProviderError(f"站点请求失败（HTTP {resp.status}）")
            return json.loads(resp.read().decode("utf-8"))
    except ProviderError:
        raise
    except TimeoutError:
        raise ProviderError("站点请求超时，请稍后重试")
    except urllib.error.HTTPError as e:
        raise ProviderError(f"站点请求失败（HTTP {e.code}）") from e
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        raise ProviderError(f"无法连接外部站点：{reason}") from e
    except Exception as e:  # noqa: BLE001
        raise ProviderError(f"站点返回数据解析失败：{e}") from e


def _text(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _kind_of(record: dict) -> str:
    """粗略判断艺人/组合：intMembers >= 2 视为组合，否则艺人。"""
    try:
        members = int(record.get("intMembers") or 0)
    except (TypeError, ValueError):
        members = 0
    return "group" if members >= 2 else "artist"


def _excerpt(text_value: Optional[str], limit: int = 160) -> Optional[str]:
    s = _text(text_value)
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit] + ("…" if len(s) > limit else "")


def search_candidates(q: str, type_: str) -> List[dict]:
    """按名称搜索 TheAudioDB，返回候选列表（外部 id、名称、缩略图、简介摘要）。"""
    q = (q or "").strip()
    if not q:
        raise ProviderError("搜索关键词不能为空")
    url = f"{_build_base()}/search.php?s={urllib.parse.quote(q)}"
    data = _get_json(url)
    rows = data.get("artists") or []
    if not isinstance(rows, list) or not rows:
        return []

    def to_candidate(r: dict) -> dict:
        kind = _kind_of(r)
        return {
            "external_id": _text(r.get("idArtist")) or "",
            "name": _text(r.get("strArtist")) or "未知",
            "kind": kind,
            "thumbnail": _text(r.get("strArtistThumb")) or _text(r.get("strArtistLogo")),
            "bio_excerpt": _excerpt(r.get("strBiographyCN") or r.get("strBiographyEN")),
        }

    candidates = [to_candidate(r) for r in rows if to_candidate(r).get("external_id")]
    if type_ in ("artist", "group"):
        filtered = [c for c in candidates if c["kind"] == type_]
        # 类型过滤后为空时回退返回全部，避免用户因误判而无法选择
        return filtered if filtered else candidates
    return candidates


def get_detail(external_id: str) -> dict:
    """按外部 id 获取详情：全简介（优先中文）、图片 URL 列表。"""
    external_id = (external_id or "").strip()
    if not external_id:
        raise ProviderError("缺少外部站点 ID")
    url = f"{_build_base()}/artist.php?i={urllib.parse.quote(external_id)}"
    data = _get_json(url)
    rows = data.get("artists") or []
    if not isinstance(rows, list) or not rows:
        raise ProviderError("未找到该外部站点条目")
    r = rows[0]
    bio = _text(r.get("strBiographyCN"))
    lang = "zh"
    if not bio:
        bio = _text(r.get("strBiographyEN"))
        lang = "en"
    return {
        "external_id": external_id,
        "name": _text(r.get("strArtist")) or "未知",
        "kind": _kind_of(r),
        "biography": bio,
        "biography_lang": lang if bio else None,
        "thumb": _text(r.get("strArtistThumb")),
        "logo": _text(r.get("strArtistLogo")),
        "fanart": _text(r.get("strArtistFanart")),
        "banner": _text(r.get("strArtistBanner")),
        "wide": _text(r.get("strArtistWideThumb")),
    }


def download_image(url: str) -> tuple[bytes, str]:
    """下载图片并返回 (字节, 扩展名)；落盘与入库由调用方负责。

    校验：仅允许 http/https、Content-Type 必须为图片、体积不超过上限。
    Commons 等缩略图服务首次渲染可能瞬时 404/5xx，自动重试（最多 3 次共约 2 秒）。
    调用方以唯一文件名落盘（entity_image_service.append_bytes），从设计上避免
    覆盖手动上传的历史文件。
    """
    if not url:
        raise ProviderError("外部数据源没有可用头像")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ProviderError("图片 URL 非法，仅支持 http/https")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with proxy_config.urlopen(req, timeout=settings.audiodb_image_timeout) as resp:
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                ext = _IMAGE_EXT_BY_TYPE.get(ctype)
                if not ext:
                    raise ProviderError(f"下载内容不是图片（{ctype or '未知类型'}）")
                total = 0
                chunks: List[bytes] = []
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_IMAGE_BYTES:
                        raise ProviderError("图片体积过大，已中止下载")
                    chunks.append(chunk)
            logger.info("图片已下载: %s（%s 字节）", url, total)
            return b"".join(chunks), ext
        except (urllib.error.HTTPError, TimeoutError, urllib.error.URLError) as e:
            code = getattr(e, "code", None)
            transient = isinstance(e, TimeoutError) or (isinstance(code, int) and code >= 404)
            if transient and attempt < 2:
                time.sleep(1)
                continue
            if isinstance(e, TimeoutError):
                raise ProviderError("图片下载超时，请稍后重试") from e
            if isinstance(e, urllib.error.HTTPError):
                raise ProviderError(f"图片下载失败（HTTP {e.code}）") from e
            reason = getattr(e, "reason", e)
            raise ProviderError(f"图片下载失败，无法连接图片地址：{reason}") from e
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"图片下载失败：{e}") from e


def resolve_avatar_path(avatar_path: Optional[str]) -> Optional[Path]:
    """把实体记录的 avatar_path 解析为本地文件路径；不存在返回 None。"""
    from app.services.file_service import confined_derived_file

    return confined_derived_file(avatar_path)


_IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
}


def media_type_of(path: Path) -> str:
    return _IMAGE_MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")
