"""艺人生长：多来源字段级提案（K-pop Fandom / 维基百科 / 百度百科 / TMDB）。

与组合生长同构但相互独立：组合生长面向成员/小分队/专辑/曲目，
艺人生长只处理档案字段（简介 / 生日 / 出生地 / 职业 / 各语种名 / 出道日期）。

preview 只读产出字段级 diff（简介自动翻译为简体中文，AI 未配置时保留原文）；
apply 按勾选写入并遵守字段锁。
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import date as date_cls
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.artist import Artist
from app.models.entity_field_lock import EntityFieldLock
from app.services import fandom_service, tmdb_service
from app.services.ai_service import translate_to_simplified
from app.services.audiodb_service import ProviderError
from app.services.source_sync import (
    _BAIDU_URL_RE,
    _FANDOM_URL_RE,
    _WIKIPEDIA_URL_RE,
    SourceError,
    _fandom_template_date,
    _norm,
)

APPLICABLE_FIELDS = {
    "description": "描述",
    "tagline": "一句话简介",
    "birth_date": "出生日期",
    "birth_place": "出生地",
    "occupation": "职业",
    "korean_name": "韩文名",
    "english_name": "英文名",
    "chinese_name": "中文名",
    "debut_date": "出道日期",
}
DATE_FIELDS = {"birth_date", "debut_date"}


def _diff(artist: Artist, field: str, proposed: str, source: str, source_url: str = "") -> dict:
    current = _norm(getattr(artist, field, None))
    return {
        "field": field,
        "label": APPLICABLE_FIELDS[field],
        "current": current,
        "proposed": proposed,
        "sources": [source],
        "source_urls": [source_url] if source_url else [],
        "same": (current or "") == (proposed or ""),
        "conflict": bool(current) and (current or "") != (proposed or ""),
        "is_new": current is None,
    }


def _looks_korean(s: str) -> bool:
    return any("\uac00" <= ch <= "\ud7a3" for ch in s or "")


def _clean_infobox_value(value: str) -> str:
    """infobox 原始值常混有 <ref> 引用与标记，清洗成纯文本。"""
    v = re.sub(r"<ref[^>]*/>", " ", value or "")
    v = re.sub(r"<ref[^>]*>.*?</ref>", " ", v, flags=re.S)
    v = re.sub(r"<[^>]+>", " ", v)
    v = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", v)
    return _norm(v)


# ===== Fandom =====


def _fandom_artist_preview(name: str, artist: Artist) -> dict:
    """按艺人名搜 Fandom 条目，产出档案字段提案。"""
    errors: List[str] = []
    for hit in fandom_service.search_pages(name, limit=3):
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        try:
            detail = fandom_service.fetch_detail(title)
        except ProviderError as e:
            errors.append(f"Fandom「{title}」：{e}")
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(f"Fandom「{title}」请求失败：{e}")
            continue
        source = f"K-pop Fandom · {title}"
        source_url = f"https://kpop.fandom.com/wiki/{title}"
        items: List[dict] = []
        bio = _norm(detail.get("biography"))
        if bio:
            translated = translate_to_simplified(bio)
            if translated:
                bio = translated
                source += "（已译）"
            items.append(_diff(artist, "description", bio, source, source_url))
        ff = detail.get("fandom_fields") or {}
        if _norm(ff.get("debut_date")):
            items.append(_diff(artist, "debut_date", _norm(ff["debut_date"]), source, source_url))
        # 别名里挖韩文名 / 英文名（仅库内为空时提议，避免张冠李戴）
        for alias in (ff.get("aliases") or [])[:3]:
            v = _norm(alias)
            if not v or v == artist.name:
                continue
            if _looks_korean(v) and not _norm(artist.korean_name):
                items.append(_diff(artist, "korean_name", v, source, source_url))
            elif not _looks_korean(v) and not _norm(artist.english_name) and len(v) <= 40:
                items.append(_diff(artist, "english_name", v, source, source_url))
        # infobox 原始键：生日 / 出生地 / 职业
        wikitext = fandom_service.page_wikitext_lead(title)
        raw = fandom_service.parse_infobox_raw(wikitext) if wikitext else {}
        raw_birth = _clean_infobox_value(raw.get("birth_date") or "")
        if raw_birth:
            parsed = _fandom_template_date(raw_birth)
            if parsed:
                items.append(_diff(artist, "birth_date", parsed, source, source_url))
        birth_place = _clean_infobox_value(raw.get("birth_place") or "")
        if birth_place:
            items.append(_diff(artist, "birth_place", birth_place, source, source_url))
        occupation = _clean_infobox_value(raw.get("occupation") or "")
        if occupation:
            items.append(_diff(artist, "occupation", occupation, source, source_url))
        if items:
            return {
                "source": {"source_type": "fandom", "label": source, "url": source_url},
                "items": items,
                "errors": errors,
            }
        # 首个候选命中但字段全空时继续尝试下一个候选
    return {
        "source": {"source_type": "fandom", "label": f"K-pop Fandom · {name}", "url": ""},
        "items": [],
        "errors": errors or [f"Fandom 上没有找到「{name}」的可用条目"],
    }


# ===== 维基百科 =====


def _wiki_artist_preview(lang_title: str, artist: Artist) -> dict:
    """维基百科摘要：简介（自动翻译）为主。"""
    import urllib.parse

    lang, _, title = lang_title.partition(":")
    if not title:
        lang, title = "en", lang_title
    title = urllib.parse.unquote(title.replace("_", " "))
    host = f"{lang}.wikipedia.org"
    url = f"https://{host}/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
    try:
        req = urllib.request.Request(
            f"https://{host}/api/rest_v1/page/summary/{urllib.parse.quote(title.replace(' ', '_'))}",
            headers={"User-Agent": "IdolMatrix/1.0 (artist-growth)"},
        )
        from app.services import proxy_config as _pcfg

        with _pcfg.urlopen(req, timeout=12) as resp:
            summary = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"维基百科摘要获取失败：{e}") from e
    extract = _norm(summary.get("extract"))
    items: List[dict] = []
    if extract:
        translated = translate_to_simplified(extract)
        if translated:
            extract = translated
        items.append(_diff(artist, "description", extract, f"Wikipedia({lang})", url))
    return {
        "source": {"source_type": "wikipedia", "label": f"Wikipedia · {title}", "url": url},
        "items": items,
        "errors": [],
    }


# ===== 百度百科 =====


def _baidu_artist_preview(url: str, artist: Artist) -> dict:
    """百度百科：尽力抽取标题/简介（与组合生长同口径），只取简介与中文名。"""
    import re as _re
    import urllib.request

    title = None
    extract = None
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; IdolMatrix/1.0)",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        m = _re.search(r"<title>([^<]+)</title>", html, _re.I)
        if m:
            title = _norm(m.group(1).replace("_百度百科", "").replace("-百度百科", "").strip())
        m = _re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            _re.I,
        )
        if not m:
            m = _re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
                html,
                _re.I,
            )
        if m:
            extract = _norm(m.group(1))
    except Exception as e:  # noqa: BLE001
        return {
            "source": {"source_type": "baidu", "label": "百度百科（抽取受限）", "url": url},
            "items": [],
            "errors": [f"百度百科页面抓取失败：{e}"],
        }
    items: List[dict] = []
    if title and not _norm(artist.chinese_name):
        items.append(_diff(artist, "chinese_name", title, "百度百科", url))
    if extract:
        items.append(_diff(artist, "description", extract, "百度百科", url))
    return {
        "source": {"source_type": "baidu", "label": f"百度百科 · {title or '条目'}", "url": url},
        "items": items,
        "errors": [],
    }


# ===== TMDB =====


def _tmdb_artist_preview(name: str, artist: Artist) -> dict:
    """TMDB 人物检索：简介（优先中文）+ 生日 / 出生地。仅配置了 TMDB 时有效。"""
    try:
        cands = tmdb_service.search_candidates(name, "artist")
    except Exception as e:  # noqa: BLE001
        return {
            "source": {"source_type": "tmdb", "label": "TMDB", "url": ""},
            "items": [],
            "errors": [f"TMDB 检索失败：{e}"],
        }
    if not cands:
        return {
            "source": {"source_type": "tmdb", "label": "TMDB", "url": ""},
            "items": [],
            "errors": [],
        }
    try:
        detail = tmdb_service.get_detail(cands[0]["external_id"])
    except ProviderError as e:
        return {
            "source": {"source_type": "tmdb", "label": "TMDB", "url": ""},
            "items": [],
            "errors": [f"TMDB 详情失败：{e}"],
        }
    source = f"TMDB · {detail.get('name') or name}"
    items: List[dict] = []
    bio = _norm(detail.get("biography"))
    if bio:
        translated = translate_to_simplified(bio)
        if translated:
            bio = translated
            source += "（已译）"
        items.append(_diff(artist, "description", bio, source, ""))
    meta = _norm(detail.get("meta"))
    if meta:
        # meta 形如 "Acting · 1991-03-29 · 大邱广域市"，尽力拆出生日与出生地
        parts = [p.strip() for p in meta.split("·")]
        for p in parts:
            if len(p) >= 8 and p[0:1].isdigit() and not _norm(artist.birth_date):
                parsed = _fandom_template_date(p)
                if parsed:
                    items.append(_diff(artist, "birth_date", parsed, source, ""))
            elif not _norm(artist.birth_place) and not any(c.isdigit() for c in p):
                items.append(_diff(artist, "birth_place", p, source, ""))
    return {
        "source": {"source_type": "tmdb", "label": source, "url": ""},
        "items": items,
        "errors": [],
    }


# ===== 对外入口 =====


def _merge_items(baskets: List[dict]) -> List[dict]:
    """同字段多来源时保留第一个非空提案（按传入顺序，Fandom 优先）。"""
    merged: Dict[str, dict] = {}
    for basket in baskets:
        for item in basket.get("items") or []:
            field = item["field"]
            if field not in merged and item.get("proposed"):
                merged[field] = item
    return list(merged.values())


def preview_artist_sources(db: Session, uid: str, urls: List[str]) -> dict:
    """艺人生长预览：只读，产出字段级 diff。urls 为空时按名称自动检索。"""
    artist = db.scalar(select(Artist).where(Artist.uid == uid, Artist.deleted_at.is_(None)))
    if artist is None:
        raise SourceError("艺人不存在")

    baskets: List[dict] = []
    errors: List[str] = []
    sources: List[dict] = []

    clean_urls = [u for u in (urls or []) if u and u.strip()]
    if clean_urls:
        for u in clean_urls:
            try:
                if _FANDOM_URL_RE.search(u):
                    m = _FANDOM_URL_RE.search(u)
                    basket = _fandom_artist_preview(urllib.parse.unquote(m.group(1)), artist)
                elif _WIKIPEDIA_URL_RE.search(u):
                    m = _WIKIPEDIA_URL_RE.search(u)
                    basket = _wiki_artist_preview(m.group(0).split("/wiki/")[-1], artist)
                elif _BAIDU_URL_RE.search(u):
                    basket = _baidu_artist_preview(u, artist)
                else:
                    errors.append(f"暂不支持的来源链接：{u}")
                    continue
            except SourceError as e:
                errors.append(str(e))
                continue
            baskets.append(basket)
            sources.append(basket["source"])
    else:
        for fetch in (
            lambda: _fandom_artist_preview(artist.name, artist),
            lambda: _wiki_artist_preview(artist.name, artist),
            lambda: _tmdb_artist_preview(artist.name, artist),
        ):
            basket = fetch()
            baskets.append(basket)
            sources.append(basket["source"])
            errors.extend(basket.get("errors") or [])

    items = _merge_items(baskets)
    # 提案排序：缺失字段在前，与组合生长观感一致
    items.sort(key=lambda i: (0 if i["is_new"] else 1, i["field"]))
    return {
        "sources": sources,
        "items": items,
        "extra": {},
        "errors": errors,
    }


def apply_artist_fields(db: Session, uid: str, fields: Dict[str, Any], source_urls: List[str]) -> dict:
    """把勾选的字段写入艺人档案；🔒 锁定字段跳过。"""
    artist = db.scalar(select(Artist).where(Artist.uid == uid, Artist.deleted_at.is_(None)))
    if artist is None:
        raise SourceError("艺人不存在")

    lock_row = db.scalar(
        select(EntityFieldLock).where(
            EntityFieldLock.entity_type == "artists",
            EntityFieldLock.entity_id == artist.id,
        )
    )
    locked = (lock_row.locks or {}) if lock_row else {}

    applied = {"fields": [], "fields_skipped_locked": []}
    for field, value in (fields or {}).items():
        if field not in APPLICABLE_FIELDS:
            continue
        if locked.get(field):
            applied["fields_skipped_locked"].append(field)
            continue
        clean_val = _norm(value)
        if field in DATE_FIELDS:
            # Date 列必须写 date 对象；年份/年月精度不得写成 None 清空已有值
            if not clean_val or len(clean_val) != 10:
                continue
            try:
                parsed = date_cls.fromisoformat(clean_val)
            except ValueError:
                continue
            setattr(artist, field, parsed)
            applied["fields"].append(field)
            continue
        setattr(artist, field, clean_val or None)
        applied["fields"].append(field)

    if source_urls:
        links = [l for l in (artist.external_links or []) if isinstance(l, dict)]
        have = {l.get("url") for l in links}
        for u in source_urls:
            if u and u not in have:
                links.append({"url": u, "source": "artist-growth"})
        artist.external_links = links
    db.commit()
    return applied


# ===== solo 专辑生长（iTunes / Deezer / MusicBrainz，与组合生长同源）=====

_ALBUM_TYPE_MAP = {
    "single": "Single",
    "ep": "MiniAlbum",
    "album": "FullAlbum",
    "compilation": "Compilation",
}


def preview_artist_albums(db: Session, uid: str) -> dict:
    """按艺人名聚合检索外部专辑，产出建碟/补全/补曲目提案（只读）。"""
    from app.models.album import AlbumTrack
    from app.services.source_sync import _album_proposals_for

    artist = db.scalar(select(Artist).where(Artist.uid == uid, Artist.deleted_at.is_(None)))
    if artist is None:
        raise SourceError("艺人不存在")
    albums, _singles, note = _album_proposals_for(
        db,
        name=artist.name,
        entity_type="artist",
        entity_id=artist.id,
        debut_year=artist.debut_date.year if artist.debut_date else None,
    )
    # 已在库且没有任何曲目的专辑：有外部 id 时标记为「补曲目」，可勾选直接写入
    track_counts = dict(
        db.execute(
            select(AlbumTrack.album_id, func.count())
            .where(
                AlbumTrack.album_id.in_(
                    [r["album_id"] for r in albums if r.get("album_id")] or [-1]
                )
            )
            .group_by(AlbumTrack.album_id)
        ).all()
    )
    for r in albums:
        aid = r.get("album_id")
        if (
            aid
            and r.get("action") in ("update", "exists")
            and r.get("external_id")
            and track_counts.get(aid, 0) == 0
        ):
            r["action"] = "fill_tracks"
    return {"albums": albums, "note": note}


def apply_artist_albums(db: Session, uid: str, albums: List[dict], include_tracks: bool = True) -> dict:
    """把勾选的专辑提案写入：建碟（挂发行主体=本艺人）/ 补发行日 / 补封面 / 写曲目。

    create 默认随碟写入曲目表；fill_tracks 为已建专辑补曲目。
    未在库的歌曲创建为该艺人的 solo 歌曲。尊重专辑字段锁（release_date / cover_path）。
    """
    from app.models.album import Album, AlbumTrack
    from app.services import cover_service
    from app.services.source_sync import _clean_date, _norm, _norm_name

    artist = db.scalar(select(Artist).where(Artist.uid == uid, Artist.deleted_at.is_(None)))
    if artist is None:
        raise SourceError("艺人不存在")

    applied = {
        "albums_created": 0,
        "albums_updated": 0,
        "albums_skipped": 0,
        "songs_created": 0,
        "tracks_created": 0,
        "tracklist_failed": 0,
    }

    for a in albums or []:
        action = a.get("action") or "create"
        name = _norm(a.get("name"))
        if not name:
            continue

        existing = next(
            (
                x
                for x in db.scalars(
                    select(Album).where(
                        Album.deleted_at.is_(None),
                        Album.release_artist_type == "artist",
                        Album.release_artist_id == artist.id,
                    )
                )
                if _norm_name(x.name) == _norm_name(name)
            ),
            None,
        )
        alb_lock_row = (
            db.scalar(
                select(EntityFieldLock).where(
                    EntityFieldLock.entity_type == "albums",
                    EntityFieldLock.entity_id == existing.id,
                )
            )
            if existing
            else None
        )
        alb_locked = (alb_lock_row.locks or {}) if alb_lock_row else {}
        external_id = _norm(a.get("external_id"))

        # ---- 建碟 ----
        if action == "create":
            if existing:
                applied["albums_skipped"] += 1
                continue
            release_raw = _clean_date(a.get("release_date"))
            release_v = None
            if release_raw and len(release_raw) == 10:
                try:
                    release_v = date_cls.fromisoformat(release_raw)
                except ValueError:
                    release_v = None
            album = Album(
                name=name,
                release_date=release_v,
                album_type=_ALBUM_TYPE_MAP.get((a.get("record_type") or "").lower()),
                release_artist_type="artist",
                release_artist_id=artist.id,
            )
            db.add(album)
            db.flush()
            applied["albums_created"] += 1
            cover_url = _norm(a.get("cover_url"))
            if cover_url:
                try:
                    rel = cover_service.download_cover(cover_url, album.id)
                    if rel:
                        album.cover_path = rel
                except Exception:  # noqa: BLE001 — 封面下载失败不阻断建碟
                    pass
            if include_tracks and external_id:
                try:
                    bundle = _fetch_tracklist_bundle(external_id)
                    counts = _create_artist_tracks(db, artist, album, bundle["tracks"])
                    applied["songs_created"] += counts["songs_created"]
                    applied["tracks_created"] += counts["tracks_created"]
                except Exception:  # noqa: BLE001 — 单张碟的曲目抓取失败不阻断
                    applied["tracklist_failed"] += 1
            continue

        # ---- 补全 / 补曲目（针对已在库的专辑）----
        album = existing
        if album is None:
            applied["albums_skipped"] += 1
            continue
        force = action == "exists"
        changed = False

        if not alb_locked.get("release_date"):
            rd = _clean_date(a.get("release_date"))
            if rd and len(rd) == 10:
                try:
                    new_d = date_cls.fromisoformat(rd)
                except ValueError:
                    new_d = None
                if new_d is not None and (force or album.release_date is None):
                    if album.release_date != new_d:
                        album.release_date = new_d
                        changed = True

        cover_url = _norm(a.get("cover_url"))
        if cover_url and not alb_locked.get("cover_path"):
            if force or not album.cover_path:
                try:
                    rel = cover_service.download_cover(cover_url, album.id)
                    if rel and rel != album.cover_path:
                        album.cover_path = rel
                        changed = True
                except Exception:  # noqa: BLE001
                    pass

        if changed:
            applied["albums_updated"] += 1
        else:
            applied["albums_skipped"] += 1

        # ---- 曲目（fill_tracks，或 update/exists 勾选时顺带补空曲目）----
        if include_tracks and external_id and action in ("fill_tracks", "update", "exists"):
            have_tracks = (
                db.scalar(
                    select(func.count())
                    .select_from(AlbumTrack)
                    .where(AlbumTrack.album_id == album.id)
                )
                or 0
            )
            if have_tracks == 0:
                try:
                    bundle = _fetch_tracklist_bundle(external_id)
                    counts = _create_artist_tracks(db, artist, album, bundle["tracks"])
                    applied["songs_created"] += counts["songs_created"]
                    applied["tracks_created"] += counts["tracks_created"]
                except Exception:  # noqa: BLE001
                    applied["tracklist_failed"] += 1

    db.commit()
    return applied


def _create_artist_tracks(db: Session, artist: Artist, album, tracks: List[dict]) -> Dict[str, int]:
    """把曲目表写入专辑：未在库歌曲创建为该艺人的 solo 歌曲（与组合生长同口径）。"""
    from app.models.album import AlbumTrack
    from app.models.song import Song
    from app.services.source_sync import _norm_name, _to_int

    counts = {"songs_created": 0, "tracks_created": 0}
    artist_songs = {
        _norm_name(x.name): x
        for x in db.scalars(
            select(Song).where(
                Song.deleted_at.is_(None),
                Song.release_artist_type == "artist",
                Song.release_artist_id == artist.id,
            )
        )
    }
    used_positions: set = set()
    for idx, t in enumerate(tracks, start=1):
        tname = _norm(t.get("name"))
        if not tname:
            continue
        disc = _to_int(t.get("disc_number")) or 1
        tn = _to_int(t.get("track_number")) or idx
        while (disc, tn) in used_positions:
            tn += 1
        used_positions.add((disc, tn))
        song = artist_songs.get(_norm_name(tname))
        if song is None:
            song = Song(
                name=tname,
                release_artist_type="artist",
                release_artist_id=artist.id,
            )
            db.add(song)
            db.flush()
            artist_songs[_norm_name(tname)] = song
            counts["songs_created"] += 1
        exists_track = db.scalar(
            select(AlbumTrack).where(
                AlbumTrack.album_id == album.id,
                AlbumTrack.song_id == song.id,
            )
        )
        if exists_track:
            continue
        db.add(
            AlbumTrack(
                album_id=album.id,
                song_id=song.id,
                disc_number=disc,
                track_number=tn,
            )
        )
        counts["tracks_created"] += 1
    return counts


def _fetch_tracklist_bundle(external_id: str) -> dict:
    """按 external_id 抓取曲目表：{tracks, release_date, year}。"""
    from app.services import album_external_service as aes

    if external_id.startswith("itunes:"):
        data = aes.get_itunes_tracklist(external_id)
    else:
        data = aes.get_deezer_tracklist(external_id.split(":", 1)[1])
    return {
        "tracks": data.get("tracks") or [],
        "release_date": data.get("release_date"),
        "year": data.get("year"),
    }
