"""专辑外部元数据服务：iTunes / Deezer 专辑搜索与曲目列表。

与 external_providers 相同思路：免 Key 的公开 API + 标准库 urllib，
出错抛 ProviderError（中文 message），由路由转 HTTPException。

- search_albums(q, source)：专辑搜索候选（名称/艺人/封面/年份/曲目数）
- get_tracklist(external_id)：按 "itunes:123" / "deezer:456" 前缀分发，
  返回统一结构的曲目列表（碟号/轨号/歌名/秒数/外部 trackId）
"""

from __future__ import annotations

import json
import re
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional

from app.core.config import settings
from app.services import proxy_config
from app.services.audiodb_service import ProviderError, _UA

logger = logging.getLogger(__name__)

_ITUNES_API = "https://itunes.apple.com"
_DEEZER_API = "https://api.deezer.com"


def _http_json(url: str, params: Optional[dict] = None, error_hint: str = "外部站点") -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with proxy_config.urlopen(req, timeout=settings.audiodb_timeout) as resp:
            if resp.status != 200:
                raise ProviderError(f"{error_hint} 请求失败（HTTP {resp.status}）")
            return json.loads(resp.read().decode("utf-8"))
    except ProviderError:
        raise
    except TimeoutError as e:
        raise ProviderError(f"{error_hint} 请求超时，请稍后重试") from e
    except urllib.error.HTTPError as e:
        raise ProviderError(f"{error_hint} 请求失败（HTTP {e.code}）") from e
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        raise ProviderError(f"无法连接 {error_hint}：{reason}") from e
    except Exception as e:  # noqa: BLE001
        raise ProviderError(f"{error_hint} 返回数据解析失败：{e}") from e


def _text(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None



def _iso_date_prefix(value) -> Optional[str]:
    """从 iTunes/Deezer 返回值提取完整 YYYY-MM-DD；仅年份则返回 None。"""
    s = _text(value)
    if not s:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else None

def _to_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _itunes_big_cover(url) -> Optional[str]:
    s = _text(url)
    if not s:
        return None
    return s.replace("100x100bb", "600x600bb")


# ---------------------------------------------------------------------------
# iTunes
# storefront 顺序：US 优先（K-pop 上架最全、lookup 能取到曲目），CN 兜底。
# 实测 CN storefront 的 search 能返回专辑（含 trackCount），但 lookup entity=song
# 常为 0 首（大陆区歌曲未上架）；且各 storefront 的 collectionId 互不通用，
# 因此 external_id 编码 storefront（itunes:us:123），lookup 时同区查询；
# 同区取到 0 首时，再跨区按「专辑名+艺人」重搜一次兜底。
# ---------------------------------------------------------------------------

_ITUNES_STOREFRONTS = ("us", "cn")


def _norm_for_match(s: Optional[str]) -> str:
    import re
    import unicodedata

    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", unicodedata.normalize("NFKC", s or "").lower())


def _itunes_search_in(term: str, storefront: str) -> List[dict]:
    data = _http_json(
        f"{_ITUNES_API}/search",
        {
            "term": term,
            "entity": "album",
            "media": "music",
            "country": storefront,
            "limit": "25",
        },
        error_hint="iTunes",
    )
    candidates: List[dict] = []
    for r in data.get("results") or []:
        if not isinstance(r, dict):
            continue
        cid = _to_int(r.get("collectionId"))
        name = _text(r.get("collectionName"))
        if cid is None or not name:
            continue
        release = _text(r.get("releaseDate"))
        candidates.append(
            {
                "external_id": f"itunes:{storefront}:{cid}",
                "name": name,
                "artist": _text(r.get("artistName")),
                "cover_url": _itunes_big_cover(r.get("artworkUrl100")),
                "year": release[:4] if release else None,
                "release_date": _iso_date_prefix(release),
                "source": "itunes",
                "track_count": _to_int(r.get("trackCount")),
                "record_type": (_text(r.get("collectionType")) or "").lower() or None,
            }
        )
    return candidates


_MB_API = "https://musicbrainz.org/ws/2"


def search_musicbrainz_release_groups(artist_name: str) -> List[dict]:
    """MusicBrainz：艺人搜索 → 该艺人的发行物组（类型权威：album/ep/single）。

    遵守 1 req/s 限速（两次请求间 sleep 1.1s）。失败返回空。
    """
    import re as _re
    import time as _time

    name = (artist_name or "").strip()
    if not name:
        return []
    try:
        data = _http_json(
            f"{_MB_API}/artist",
            {"query": name, "fmt": "json", "limit": "5"},
            error_hint="MusicBrainz",
        )
    except Exception:  # noqa: BLE001
        return []

    def _nk(x):
        return _re.sub(r"\s+", " ", (x or "")).strip().casefold()

    target = next((a for a in data.get("artists") or [] if _nk(a.get("name")) == _nk(name)), None)
    if target is None or not target.get("id"):
        return []
    _time.sleep(1.1)  # MusicBrainz 限速 1 req/s
    try:
        data = _http_json(
            f"{_MB_API}/release-group",
            {"artist": target["id"], "fmt": "json", "limit": "100"},
            error_hint="MusicBrainz",
        )
    except Exception:  # noqa: BLE001
        return []
    out: List[dict] = []
    for rg in data.get("release-groups") or []:
        title = _text(rg.get("title"))
        rgid = rg.get("id")
        if not title or not rgid:
            continue
        frd = _text(rg.get("first-release-date"))
        out.append(
            {
                "external_id": f"musicbrainz:{rgid}",
                "name": title,
                "artist": _text(target.get("name")),
                "cover_url": None,
                "year": frd[:4] if frd else None,
                "source": "musicbrainz",
                "track_count": None,
                "record_type": (_text(rg.get("primary-type")) or "").lower() or None,
            }
        )
    return out



def search_itunes_albums(query: str) -> List[dict]:
    for storefront in _ITUNES_STOREFRONTS:
        candidates = _itunes_search_in(query, storefront)
        if candidates:
            return candidates
    return []


def _itunes_lookup_in(collection_id: str, storefront: str) -> tuple:
    """返回 (collection dict | None, tracks list)。"""
    data = _http_json(
        f"{_ITUNES_API}/lookup",
        {"id": collection_id, "entity": "song", "limit": "200", "country": storefront},
        error_hint="iTunes",
    )
    results = data.get("results") or []
    collection = next((r for r in results if isinstance(r, dict) and r.get("wrapperType") == "collection"), None)
    tracks: List[dict] = []
    for r in results:
        if not isinstance(r, dict) or r.get("wrapperType") != "track":
            continue
        name = _text(r.get("trackName"))
        if not name:
            continue
        ms = _to_int(r.get("trackTimeMillis"))
        tracks.append(
            {
                "disc_number": _to_int(r.get("discNumber")) or 1,
                "track_number": _to_int(r.get("trackNumber")) or 0,
                "name": name,
                "duration": round(ms / 1000) if ms else None,
                "external_track_id": _text(r.get("trackId")),
            }
        )
    tracks.sort(key=lambda t: (t["disc_number"], t["track_number"]))
    return collection, tracks


def _itunes_cross_storefront_retry(collection: dict, storefront: str) -> tuple:
    """同区 0 首时，跨区按「专辑名 艺人」重搜并取同名专辑的曲目。

    返回 (collection, tracks, new_storefront, new_cid)；失败时后两项为 None。
    """
    other = next((sf for sf in _ITUNES_STOREFRONTS if sf != storefront), None)
    if other is None:
        return None, [], None, None
    name = _text(collection.get("collectionName")) or ""
    artist = _text(collection.get("artistName")) or ""
    term = " ".join(x for x in (name, artist) if x).strip() or name
    if not term:
        return None, [], None, None
    want = _norm_for_match(name)
    if not want:
        return None, [], None, None
    try:
        candidates = _itunes_search_in(term, other)
    except ProviderError:
        return None, [], None, None
    for cand in candidates:
        if _norm_for_match(cand.get("name")) != want:
            continue
        cand_artist = cand.get("artist")
        if artist and cand_artist and _norm_for_match(cand_artist) != _norm_for_match(artist):
            continue
        new_cid = cand["external_id"].split(":", 2)[2]
        coll, trks = _itunes_lookup_in(new_cid, other)
        if coll and trks:
            return coll, trks, other, new_cid
    return None, [], None, None


def get_itunes_tracklist(external_id: str) -> dict:
    """external_id 支持 "itunes:{storefront}:{cid}"（新）与 "itunes:{cid}"（旧，默认 us）。"""
    raw = (external_id or "").strip()
    if raw.startswith("itunes:"):
        raw = raw.split(":", 1)[1]
    parts = raw.split(":")
    if len(parts) == 2 and parts[0] in _ITUNES_STOREFRONTS:
        storefront, collection_id = parts[0], parts[1]
    else:
        storefront, collection_id = "us", raw
    collection, tracks = _itunes_lookup_in(collection_id, storefront)
    if not collection:
        raise ProviderError("未找到该 iTunes 专辑，请换一个候选或数据源")
    if not tracks:
        collection, tracks, new_sf, new_cid = _itunes_cross_storefront_retry(collection, storefront)
        if not collection:
            raise ProviderError("该专辑在外部站点暂无曲目数据，请换一个候选或数据源")
        storefront, collection_id = new_sf, new_cid
    release = _text(collection.get("releaseDate"))
    return {
        "external_id": f"itunes:{storefront}:{collection_id}",
        "name": _text(collection.get("collectionName")) or "未知",
        "artist": _text(collection.get("artistName")),
        "cover_url": _itunes_big_cover(collection.get("artworkUrl100")),
        "year": release[:4] if release else None,
        "release_date": _iso_date_prefix(release),
        "source": "itunes",
        "tracks": tracks,
    }


# ---------------------------------------------------------------------------
# Deezer（/search/album + /album/{id}，曲目 duration 单位为秒）
# ---------------------------------------------------------------------------


def search_deezer_albums(query: str) -> List[dict]:
    data = _http_json(
        f"{_DEEZER_API}/search/album",
        {"q": query, "limit": "25"},
        error_hint="Deezer",
    )
    candidates: List[dict] = []
    for r in data.get("data") or []:
        if not isinstance(r, dict):
            continue
        aid = _to_int(r.get("id"))
        name = _text(r.get("title"))
        if aid is None or not name:
            continue
        cover = _text(r.get("cover_big")) or _text(r.get("cover_medium")) or _text(r.get("cover_xl"))
        release = _text(r.get("release_date"))
        candidates.append(
            {
                "external_id": f"deezer:{aid}",
                "name": name,
                "artist": _text((r.get("artist") or {}).get("name")),
                "cover_url": cover,
                "year": release[:4] if release else None,
                "release_date": _iso_date_prefix(release),
                "source": "deezer",
                "track_count": _to_int(r.get("nb_tracks")),
                "record_type": _text(r.get("record_type")) or None,
            }
        )
    return candidates


def get_deezer_tracklist(album_id: str) -> dict:
    data = _http_json(f"{_DEEZER_API}/album/{album_id}", error_hint="Deezer")
    err = data.get("error")
    if err:
        raise ProviderError(f"Deezer 返回错误：{err.get('message') or '未知错误'}")
    if not data.get("id"):
        raise ProviderError("未找到该 Deezer 专辑，请换一个候选或数据源")
    tracks: List[dict] = []
    for t in (data.get("tracks") or {}).get("data") or []:
        if not isinstance(t, dict):
            continue
        name = _text(t.get("title"))
        if not name:
            continue
        dur = _to_int(t.get("duration"))
        tracks.append(
            {
                "disc_number": _to_int(t.get("disk_number")) or 1,
                "track_number": _to_int(t.get("track_position")) or 0,
                "name": name,
                "duration": dur,
                "external_track_id": _text(t.get("id")),
            }
        )
    tracks.sort(key=lambda t: (t["disc_number"], t["track_number"]))
    release = _text(data.get("release_date"))
    artist = data.get("artist") or {}
    cover = _text(data.get("cover_xl")) or _text(data.get("cover_big")) or _text(data.get("cover_medium"))
    return {
        "external_id": f"deezer:{album_id}",
        "name": _text(data.get("title")) or "未知",
        "artist": _text(artist.get("name")),
        "cover_url": cover,
        "year": release[:4] if release else None,
        "release_date": _iso_date_prefix(release),
        "source": "deezer",
        "tracks": tracks,
    }


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------


def search_albums(query: str, source: str) -> List[dict]:
    query = (query or "").strip()
    if not query:
        raise ProviderError("搜索关键词不能为空")
    if source == "itunes":
        return search_itunes_albums(query)
    if source == "deezer":
        return search_deezer_albums(query)
    raise ProviderError("不支持的数据源，仅支持 itunes / deezer")


def get_tracklist(external_id: str) -> dict:
    """按带来源前缀的外部专辑 ID 拉取曲目列表。"""
    external_id = (external_id or "").strip()
    if external_id.startswith("itunes:"):
        return get_itunes_tracklist(external_id)
    if external_id.startswith("deezer:"):
        return get_deezer_tracklist(external_id.split(":", 1)[1])
    raise ProviderError("外部专辑 ID 非法，需带来源前缀（itunes:/deezer:）")
