"""外部元数据统一入口：TheAudioDB + Deezer + iTunes + Wikidata + TMDB。

- search_candidates：聚合各来源的检索候选，external_id 带来源前缀
  （audiodb: / deezer: / itunes: / wiki: / tmdb:），无前缀视为旧版 TheAudioDB id。
- get_detail：按前缀分发到对应来源，返回统一 ProviderDetail 结构
  （tmdb: 额外带 gallery 头像画廊与 meta 人工核对信息）。

Deezer 与 iTunes Search API 免 Key 免注册；Wikidata/Wikipedia 走
wiki_service；TMDB 仅艺人且需在设置页启用。任一来源失败不影响
其他来源（仅记录日志），全部失败抛 ProviderError。
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from app.core.config import settings
from app.services import audiodb_service, fandom_service, proxy_config, tmdb_service, wiki_service
from app.services.audiodb_service import ProviderError

logger = logging.getLogger(__name__)

_SOURCE_AUDIODB = "TheAudioDB"
_SOURCE_DEEZER = "Deezer"
_SOURCE_ITUNES = "iTunes"
_SOURCE_WIKI = "Wikidata"
_SOURCE_FANDOM = "K-pop Fandom"

_DEEZER_API = "https://api.deezer.com"
_ITUNES_API = "https://itunes.apple.com"


def _http_json(url: str, params: Optional[dict] = None, timeout: Optional[int] = None, error_hint: str = "外部站点") -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    timeout = timeout if timeout is not None else settings.audiodb_timeout
    try:
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


# ---------------------------------------------------------------------------
# Deezer
# ---------------------------------------------------------------------------

def _deezer_search(q: str, type_: str) -> List[dict]:
    data = _http_json(
        f"{_DEEZER_API}/search/artist",
        {"q": q, "limit": "5"},
        error_hint="Deezer",
    )
    out = []
    for r in data.get("data") or []:
        aid = r.get("id")
        name = (r.get("name") or "").strip()
        if not aid or not name:
            continue
        out.append(
            {
                "external_id": f"deezer:{aid}",
                "name": name,
                "kind": type_,
                "thumbnail": r.get("picture_medium") or r.get("picture_big"),
                "bio_excerpt": None,
                "source": _SOURCE_DEEZER,
            }
        )
    return out


def _deezer_detail(external_id: str) -> dict:
    data = _http_json(f"{_DEEZER_API}/artist/{external_id}", error_hint="Deezer")
    if not data.get("id"):
        raise ProviderError("未找到该 Deezer 条目")
    return {
        "external_id": f"deezer:{external_id}",
        "name": (data.get("name") or "未知").strip(),
        "kind": "artist",
        "biography": None,
        "biography_lang": None,
        "thumb": data.get("picture_xl") or data.get("picture_big") or data.get("picture_medium"),
        "logo": None,
        "fanart": None,
        "banner": None,
        "wide": None,
    }


# ---------------------------------------------------------------------------
# iTunes Search API（用专辑封面当艺人头像：musicArtist 实体本身无图）
# ---------------------------------------------------------------------------

def _itunes_artwork_600(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    return url.replace("100x100", "600x600").replace("200x200", "600x600")


def _itunes_search(q: str, type_: str) -> List[dict]:
    data = _http_json(
        f"{_ITUNES_API}/search",
        {"term": q, "entity": "album", "attribute": "artistTerm", "limit": "25"},
        error_hint="iTunes",
    )
    out = []
    seen = set()
    for r in data.get("results") or []:
        aid = r.get("artistId")
        name = (r.get("artistName") or "").strip()
        if not aid or not name or aid in seen:
            continue
        thumb = _itunes_artwork_600(r.get("artworkUrl100"))
        if not thumb:
            continue
        seen.add(aid)
        out.append(
            {
                "external_id": f"itunes:{aid}",
                "name": name,
                "kind": type_,
                "thumbnail": thumb,
                "bio_excerpt": None,
                "source": _SOURCE_ITUNES,
            }
        )
        if len(out) >= 5:
            break
    return out


def _itunes_detail(external_id: str) -> dict:
    data = _http_json(
        f"{_ITUNES_API}/lookup",
        {"id": external_id, "entity": "album", "limit": "20"},
        error_hint="iTunes",
    )
    results = data.get("results") or []
    # lookup?entity=album 只返回专辑记录，musicArtist 条目不一定出现
    artist = next((r for r in results if r.get("wrapperType") == "musicArtist"), None)
    name = (artist or {}).get("artistName")
    if not name:
        name = next((r.get("artistName") for r in results if r.get("artistName")), None)
    if not name:
        raise ProviderError("未找到该 iTunes 条目")
    artwork = next(
        (_itunes_artwork_600(r.get("artworkUrl100")) for r in results if r.get("artworkUrl100")),
        None,
    )
    return {
        "external_id": f"itunes:{external_id}",
        "name": name.strip(),
        "kind": "artist",
        "biography": None,
        "biography_lang": None,
        "thumb": artwork,
        "logo": None,
        "fanart": None,
        "banner": None,
        "wide": None,
    }


# ---------------------------------------------------------------------------
# Wikidata / Wikipedia
# ---------------------------------------------------------------------------

def _wiki_kind(description: Optional[str], fallback: str) -> str:
    d = (description or "").lower()
    if any(h in d for h in ("group", "band", "trio", "duo", "组合", "乐团")):
        return "group"
    if any(h in d for h in ("singer", "rapper", "dancer", "songwriter", "musician", "idol", "actress", "actor", "歌手", "演员")):
        return "artist"
    return fallback


def _wiki_search(q: str, type_: str) -> List[dict]:
    results = wiki_service.search_entities(q, limit=6)
    if not results:
        return []
    qids = [r["qid"] for r in results[:4]]
    entities = wiki_service._entities(qids, with_claims=True)
    out = []
    for r in results[:4]:
        ent = entities.get(r["qid"]) or {}
        desc = wiki_service._description(ent) or r.get("description")
        image = wiki_service._image_url(ent)
        out.append(
            {
                "external_id": f"wiki:{r['qid']}",
                "name": r["label"],
                "kind": _wiki_kind(desc, type_),
                "thumbnail": image,
                "bio_excerpt": desc,
                "source": _SOURCE_WIKI,
            }
        )
    return out


def _wiki_detail(external_id: str, fallback_kind: str = "artist") -> dict:
    qid = external_id
    # 详情阶段不知道用户检索时的类型，从条目描述推断，推断不出默认 group 更稳
    entities = wiki_service._entities([qid], with_claims=False)
    ent = entities.get(qid) or {}
    kind = _wiki_kind(wiki_service._description(ent), fallback_kind)
    info = wiki_service.get_entity_info(qid, kind)
    if not info:
        raise ProviderError("未找到该 Wikidata 条目")
    bio = info.get("wikipedia_extract")
    lang = "zh" if info.get("wikipedia_lang") == "zh" else "en"
    if not bio:
        bio = info.get("description")
        lang = "en"
    return {
        "external_id": f"wiki:{external_id}",
        "name": info.get("label") or "未知",
        "kind": kind,
        "biography": bio,
        "biography_lang": lang if bio else None,
        "thumb": info.get("image"),
        "logo": None,
        "fanart": None,
        "banner": None,
        "wide": None,
    }


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

def _fandom_search(q: str, type_: str) -> List[dict]:
    """Fandom K-pop Wiki 候选：搜索 + 批量摘要/主图（2 次请求）。"""
    pages = fandom_service.search_pages(q, limit=6)
    if not pages:
        return []
    titles = [p["title"] for p in pages[:4]]
    meta = fandom_service.query_pages_meta(titles)
    out: List[dict] = []
    for t in titles:
        m = meta.get(t) or {}
        snippet = next((p["snippet"] for p in pages if p["title"] == t), "")
        out.append(
            {
                "external_id": f"fandom:{t}",
                "name": t,
                "kind": type_,
                "thumbnail": m.get("image"),
                "bio_excerpt": m.get("extract") or snippet or None,
                "source": _SOURCE_FANDOM,
            }
        )
    return out


def _fandom_detail(external_id: str, fallback_kind: str = "artist") -> dict:
    detail = fandom_service.fetch_detail(external_id)
    detail["kind"] = detail.get("kind") or fallback_kind
    return detail


def _search_audiodb(q: str, type_: str) -> List[dict]:
    out: List[dict] = []
    for c in audiodb_service.search_candidates(q, type_):
        c = dict(c)
        c["external_id"] = f"audiodb:{c['external_id']}"
        c["source"] = _SOURCE_AUDIODB
        out.append(c)
    return out


def search_candidates(q: str, type_: str) -> List[dict]:
    """聚合搜索四个来源（并行请求，总耗时≈最慢单源）；type_ 为 artist 或 group。

    NAS 直连海外站点慢且易超时，串行请求时各源耗时叠加（可达 30s+ 触发前端超时），
    并行后任一源失败/超时只损失该源结果，不影响其余来源。
    """
    q = (q or "").strip()
    if not q:
        raise ProviderError("搜索关键词不能为空")

    fetchers = (_search_audiodb, _deezer_search, _itunes_search, _wiki_search, _fandom_search, tmdb_service.search_candidates)
    merged: List[dict] = []
    with ThreadPoolExecutor(max_workers=len(fetchers)) as pool:
        futures = [pool.submit(fetcher, q, type_) for fetcher in fetchers]
        for fetcher, fut in zip(fetchers, futures):
            try:
                merged.extend(fut.result())
            except ProviderError as e:
                logger.info("[providers] %s 跳过: %s", fetcher.__name__, e)
            except Exception as e:  # noqa: BLE001
                logger.warning("[providers] %s 异常: %s", fetcher.__name__, e)

    if not merged:
        raise ProviderError("所有外部数据源均无结果，请稍后重试或更换关键词")
    return merged


def get_detail(external_id: str, fallback_kind: str = "artist") -> dict:
    """按 external_id 前缀分发到对应来源。无前缀视为旧版 TheAudioDB id。"""
    external_id = (external_id or "").strip()
    if not external_id:
        raise ProviderError("缺少外部站点 ID")
    if external_id.startswith("deezer:"):
        return _deezer_detail(external_id.split(":", 1)[1])
    if external_id.startswith("itunes:"):
        return _itunes_detail(external_id.split(":", 1)[1])
    if external_id.startswith("fandom:"):
        return _fandom_detail(external_id.split(":", 1)[1])
    if external_id.startswith("wiki:"):
        return _wiki_detail(external_id.split(":", 1)[1], fallback_kind)
    if external_id.startswith("tmdb:"):
        return tmdb_service.get_detail(external_id.split(":", 1)[1])
    if external_id.startswith("audiodb:"):
        return audiodb_service.get_detail(external_id.split(":", 1)[1])
    return audiodb_service.get_detail(external_id)
