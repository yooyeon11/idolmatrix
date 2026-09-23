"""专辑封面刮削服务：网易云音乐 / iTunes 专辑搜索 + 下载封面到本地。

借鉴 music-tag-web 的刮削思路：搜索候选 → 返回封面 URL 列表（用户选择）→
下载到本地并绑定专辑。全部使用标准库 urllib，不引入额外依赖。
出错时抛出 ProviderError，message 面向用户展示（中文），供路由转 HTTPException。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.core.config import settings
from app.services import proxy_config

logger = logging.getLogger(__name__)

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

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_NETEASE_SEARCH_URL = "https://music.163.com/api/search/get/web"
_ITUNES_SEARCH_URL = "https://itunes.apple.com/search"


class ProviderError(Exception):
    """封面刮削的业务错误；message 面向用户（中文）。"""


def _post_form(url: str, data: Dict[str, str]) -> bytes:
    """发送表单 POST，返回响应字节。"""
    body = urllib.parse.urlencode(data).encode("utf-8")
    headers = {
        "User-Agent": _UA,
        "Referer": "https://music.163.com/",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with proxy_config.urlopen(req, timeout=settings.audiodb_timeout) as resp:
            if resp.status != 200:
                raise ProviderError(f"网易云请求失败（HTTP {resp.status}）")
            return resp.read()
    except ProviderError:
        raise
    except TimeoutError:
        raise ProviderError("网易云请求超时，请稍后重试")
    except urllib.error.HTTPError as e:
        raise ProviderError(f"网易云请求失败（HTTP {e.code}）") from e
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        raise ProviderError(f"无法连接网易云：{reason}") from e
    except Exception as e:  # noqa: BLE001
        raise ProviderError(f"网易云返回数据解析失败：{e}") from e


def _get_json(url: str) -> dict:
    """GET 请求并解析 JSON。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with proxy_config.urlopen(req, timeout=settings.audiodb_timeout) as resp:
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


def _netease_big_cover(url: Optional[str]) -> Optional[str]:
    """网易云封面 URL 改为 600x600 大图。"""
    if not url:
        return None
    base = url.split("?", 1)[0]
    return f"{base}?param=600y600"


def _year_from_ms(ms) -> Optional[str]:
    try:
        ts = int(ms) / 1000
        return str(datetime.fromtimestamp(ts, tz=timezone.utc).year)
    except (TypeError, ValueError, OSError):
        return None


def search_netease(query: str) -> List[dict]:
    """按关键词搜索网易云专辑，返回候选列表（含封面 URL）。"""
    query = (query or "").strip()
    if not query:
        raise ProviderError("搜索关键词不能为空")
    data = {"s": query, "type": "10", "offset": "0", "limit": "25"}
    raw = _post_form(_NETEASE_SEARCH_URL, data)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ProviderError(f"网易云返回数据解析失败：{e}") from e
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
        raise ProviderError("网易云搜索失败，请稍后重试")
    albums = payload["result"].get("albums") or []
    if not isinstance(albums, list):
        return []

    candidates: List[dict] = []
    for a in albums:
        if not isinstance(a, dict):
            continue
        cover = _netease_big_cover(a.get("picUrl"))
        if not cover:
            continue
        artist = a.get("artist") or {}
        candidates.append(
            {
                "id": _text(a.get("id")) or "",
                "name": _text(a.get("name")) or "未知",
                "artist": _text(artist.get("name")),
                "cover_url": cover,
                "year": _year_from_ms(a.get("publishTime")),
                "source": "netease",
            }
        )
    return candidates


def _itunes_big_cover(url: Optional[str]) -> Optional[str]:
    """iTunes 封面 URL 由 100x100 放大到 600x600。"""
    if not url:
        return None
    return url.replace("100x100bb", "600x600bb")


def _year_from_iso(value) -> Optional[str]:
    s = _text(value)
    if not s:
        return None
    try:
        return s[:4]
    except (TypeError, ValueError):
        return None


def search_itunes(query: str) -> List[dict]:
    """按关键词搜索 iTunes 专辑，返回候选列表（含封面 URL）。"""
    query = (query or "").strip()
    if not query:
        raise ProviderError("搜索关键词不能为空")
    params = {
        "term": query,
        "entity": "album",
        "media": "music",
        "country": "cn",
        "limit": "25",
    }
    url = f"{_ITUNES_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    payload = _get_json(url)
    results = payload.get("results") or []
    if not isinstance(results, list):
        return []

    candidates: List[dict] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        cover = _itunes_big_cover(r.get("artworkUrl100"))
        if not cover:
            continue
        candidates.append(
            {
                "id": _text(r.get("collectionId")) or "",
                "name": _text(r.get("collectionName")) or "未知",
                "artist": _text(r.get("artistName")),
                "cover_url": cover,
                "year": _year_from_iso(r.get("releaseDate")),
                "source": "itunes",
            }
        )
    return candidates


def download_cover(url: str, album_id: int) -> str:
    """下载专辑封面到本地，返回相对 derived 目录的路径（avatars/albums/{id}.ext）。

    校验：仅允许 http/https、Content-Type 必须为图片、体积不超过上限。
    """
    if not url:
        raise ProviderError("外部数据源没有可用封面")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ProviderError("封面 URL 非法，仅支持 http/https")
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
    except ProviderError:
        raise
    except TimeoutError:
        raise ProviderError("封面下载超时，请稍后重试")
    except urllib.error.HTTPError as e:
        raise ProviderError(f"封面下载失败（HTTP {e.code}）") from e
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        raise ProviderError(f"封面下载失败，无法连接图片地址：{reason}") from e
    except Exception as e:  # noqa: BLE001
        raise ProviderError(f"封面下载失败：{e}") from e

    rel = Path("avatars") / "albums" / f"{album_id}{ext}"
    dest = settings.derived_dir / rel
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"".join(chunks))
    except OSError as e:  # noqa: BLE001
        raise ProviderError(f"封面保存到本地失败：{e}") from e
    logger.info("专辑封面已下载: %s", rel)
    return rel.as_posix()


def resolve_cover_path(cover_path: Optional[str]) -> Optional[Path]:
    """把专辑记录的 cover_path 解析为本地文件路径；不存在返回 None。"""
    from app.services.file_service import confined_derived_file

    return confined_derived_file(cover_path)


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
