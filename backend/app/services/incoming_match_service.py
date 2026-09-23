"""待整理视频的库驱动本地匹配（先建库后入库场景的核心链路）。

与 AI 建议互补：AI 靠世界知识猜专辑，这里直接从资料库关系读。
流程：
  1. 从文件名 / 标题识别艺人/组合（不看发布者、频道、简介）；
  2. 锁定其名下歌曲（release_artist 多态 + SongArtistRelation），
     先从扫描文本剥掉日期/画质标签，再按整词扫歌名
     （库驱动提取：不需要先从标题解析出歌名，串烧也能一次提取多首）；
  3. 命中歌曲经 AlbumTrack 反查所属专辑；
  4. 时长显著不符时降级为提示，不自动填充（宁缺勿错）。
  5. 纯数字歌名只在已识别主体的歌单里自动挂；若标题里还有其他非数字歌名，
     开头的独立数字当年份丢掉（24 tripleS GND → 只挂 GND）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.album import Album, AlbumTrack
from app.models.artist import Artist
from app.models.group import Group
from app.models.song import Song, SongArtistRelation
from app.services.bilibili_meta import extract_bracket_artist, is_bilibili_info
from app.services.name_match import (
    normalize_ingest_match_mode,
    normalize_person_name,
    related_artist_ids_for_groups,
)

# 识别艺人/组合/歌曲只看标题与文件名：发布者/频道常含粉丝名，简介常提及其他人
TITLE_KEYS = ("title", "fulltitle")

# 识别只看正式名（name/各语言名/艺名），**别名与粉丝名不参与**：
# 粉丝名常是英语常用词（aespa 粉丝名 MY ⊂ "OH MY GIRL ARIN"），进来就是误匹配源。
ARTIST_NAME_ATTRS = ("name", "stage_name", "chinese_name", "english_name", "korean_name")
GROUP_NAME_ATTRS = ("name", "chinese_name", "english_name", "korean_name")
SONG_NAME_ATTRS = ("name", "chinese_name", "english_name", "korean_name")

_ASCII_TOKEN = re.compile(r"[A-Za-z0-9]+")
# 严格档：韩文/汉字/假名命中不能嵌在更长的同脚本词里（이브 ⊂ 아이브）
_CJK_CHAR = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
# 时长差超过该值（秒）的单曲命中视为存疑（防同歌重录版/识别错歌）
DURATION_REVIEW_GUARD = 25
# 识别出的艺人/组合上限（防简介误判扩散）
MAX_ENTITIES = 4

# 画质标签、日期整段：从扫描文本里拿掉，避免 241215 撞上歌名「24」
_QUALITY_TAG = re.compile(r"\[(?:[48]k(?:\s*60p)?)\]", re.IGNORECASE)
_YMD_CN = re.compile(
    r"(?:20\d{2}|\d{2})年(?:1[0-2]|0?[1-9])月(?:[12]\d|3[01]|0?[1-9])日?"
)
_YMD_SEP = re.compile(
    r"(?<![A-Za-z0-9])(?:20\d{2}|\d{2})[.\-/](?:1[0-2]|0?[1-9])[.\-/]"
    r"(?:[12]\d|3[01]|0?[1-9])(?![A-Za-z0-9])"
)
_YMD8 = re.compile(
    r"(?<![A-Za-z0-9])20\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?![A-Za-z0-9])"
)
_YMD6 = re.compile(
    r"(?<![A-Za-z0-9])\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
    r"(?:-(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01]))?(?![A-Za-z0-9])"
)
_PREFIX_NOISE = re.compile(r"^[\s\"'`«»「」『』《》〈〉（）()\[\]【】‘’“”]+")
_WS = re.compile(r"\s+")


def _variants(obj: object, attrs: Iterable[str]) -> list[str]:
    out: list[str] = []
    for attr in attrs:
        value = getattr(obj, attr, None)
        if isinstance(value, list):
            out.extend(v.strip() for v in value if isinstance(v, str) and v.strip())
        elif value and str(value).strip():
            out.append(str(value).strip())
    return out


def _cjk_span_bounded(hay: str, start: int, end: int) -> bool:
    """同脚本字符紧贴左右则视为嵌在更长词里，不算独立命中。"""
    if start > 0 and _CJK_CHAR.match(hay[start - 1]):
        return False
    if end < len(hay) and _CJK_CHAR.match(hay[end]):
        return False
    return True


def _hit_variant(
    variant: str,
    texts: list[tuple[str, str]],
    *,
    cjk_bounded: bool = False,
) -> Optional[tuple[str, int]]:
    """检查变体是否出现在任一文本中，返回 (字段名, 首次命中位置)。

    普通档：拉丁/CJK 都走子串（历史宽匹配）。
    严格档：拉丁必须整词；CJK 不能嵌在更长的同脚本词里。
    """
    v = variant.strip()
    if not v:
        return None
    compact = v.replace(" ", "")
    is_ascii = bool(compact and _ASCII_TOKEN.fullmatch(compact))
    # 严格档：拉丁名必须整词命中（Bae 不能打进 Baek）。
    # 普通档保持历史行为：拉丁名也走子串，漏得少、误伤多。
    if is_ascii and cjk_bounded:
        pattern = re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(v) + r"(?![A-Za-z0-9])", re.IGNORECASE
        )
        for field, text in texts:
            m = pattern.search(text)
            if m:
                return field, m.start()
        return None
    lowered = v.casefold()
    for field, text in texts:
        hay = text.casefold()
        start = 0
        while True:
            idx = hay.find(lowered, start)
            if idx < 0:
                break
            if not cjk_bounded or _cjk_span_bounded(hay, idx, idx + len(lowered)):
                return field, idx
            start = idx + 1
    return None


def _strip_scan_noise(text: str) -> str:
    """去掉画质标签与日期整段，供名字扫描使用。不改原始标题。"""
    t = _QUALITY_TAG.sub(" ", text)
    t = _YMD_CN.sub(" ", t)
    t = _YMD_SEP.sub(" ", t)
    t = _YMD8.sub(" ", t)
    t = _YMD6.sub(" ", t)
    return _WS.sub(" ", t).strip()


def _min_variant_len(variant: str, *, strict: bool) -> bool:
    """变体长度门槛：全库扫描比已锁定艺人的池子更保守，避免短名误报。"""
    key = normalize_person_name(variant)
    if not key:
        return False
    has_cjk = not _ASCII_TOKEN.fullmatch(key)
    min_len = (3 if has_cjk else 4) if strict else (2 if has_cjk else 3)
    return len(key) >= min_len


def _allow_entity_variant(variant: str) -> bool:
    """艺人/组合名：普通门槛，另放行 2 字母官方名（IU）。"""
    if _min_variant_len(variant, strict=False):
        return True
    key = normalize_person_name(variant)
    return bool(key) and bool(_ASCII_TOKEN.fullmatch(key)) and len(key) == 2


def _allow_song_variant(variant: str, *, identified: bool) -> bool:
    """歌名：已锁定主体时放行纯数字/极短官方名；全库扫描仍走保守门槛。"""
    key = normalize_person_name(variant)
    if not key:
        return False
    if identified:
        if key.isdigit():
            return True
        if _ASCII_TOKEN.fullmatch(key) and len(key) <= 2:
            return True
        return _min_variant_len(variant, strict=False)
    return _min_variant_len(variant, strict=True)


def _is_numeric_song_variant(variant: str) -> bool:
    key = normalize_person_name(variant)
    return bool(key) and key.isdigit()


def _is_leading_index(text: str, idx: int) -> bool:
    """命中是否在文本开头（忽略引号/括号/空白）。"""
    if idx <= 0:
        return True
    return _PREFIX_NOISE.fullmatch(text[:idx]) is not None


def build_match_hints(
    db: Session,
    file_name: str,
    info: dict[str, Any],
    *,
    duration: Optional[int] = None,
    match_mode: Optional[str] = None,
) -> dict[str, Any]:
    """对待整理视频做库驱动本地匹配，返回可直接填充表单的提示。

    match_mode=strict 时：CJK 不允许嵌在更长词里，且艺人必须属于已识别组合
    （含母/子团、历任）才自动写入。
    """
    stem = Path(file_name).stem if file_name else ""

    title_texts: list[tuple[str, str]] = []
    for key in TITLE_KEYS:
        val = info.get(key)
        if isinstance(val, str) and val.strip():
            cleaned = _strip_scan_noise(val)
            if cleaned:
                title_texts.append((key, cleaned))
    if stem:
        cleaned_stem = _strip_scan_noise(stem)
        if cleaned_stem:
            title_texts.append(("file_name", cleaned_stem))
    desc = info.get("description")
    desc_raw = desc.strip() if isinstance(desc, str) and desc.strip() else ""
    desc_text = _strip_scan_noise(desc_raw) if desc_raw else ""
    all_texts = title_texts + ([("description", desc_text)] if desc_text else [])

    mode = normalize_ingest_match_mode(match_mode)
    is_strict = mode == "strict"
    cjk_bounded = is_strict
    result: dict[str, Any] = {
        "artists": [],
        "groups": [],
        "albums": [],
        "tracks": [],
        "notices": [],
        "match_mode": mode,
    }
    if not any(t[1] for t in all_texts):
        return result

    bili = is_bilibili_info(info)
    if bili:
        # B站 tags 与标题同级：翻跳/直拍标签常含歌名，但不参与艺人识别（tags 泛词多）
        tags = info.get("bili_tags")
        if isinstance(tags, list):
            tag_text = " ".join(str(t) for t in tags if str(t).strip())
            if tag_text:
                all_texts = all_texts + [("bili_tags", tag_text)]

    # ===== 1. 识别艺人/组合（只在标题类字段里找，避免简介误判）=====
    artist_ids: set[int] = set()
    group_ids: set[int] = set()
    if bili:
        # B站：【】内为艺人强信号，直接精确锁定（大小写不敏感）
        bracket = extract_bracket_artist(info.get("title") or "") or extract_bracket_artist(stem)
        if bracket:
            _key = normalize_person_name(bracket)
            for artist in db.scalars(select(Artist).where(Artist.active_filter())):
                if any(
                    normalize_person_name(v) == _key
                    for v in _variants(artist, ARTIST_NAME_ATTRS)
                ):
                    artist_ids.add(artist.id)
                    break
            for group in db.scalars(select(Group).where(Group.active_filter())):
                if any(
                    normalize_person_name(v) == _key
                    for v in _variants(group, GROUP_NAME_ATTRS)
                ):
                    group_ids.add(group.id)
                    break
            if not artist_ids and not group_ids:
                result["notices"].append(f"标题【{bracket}】未匹配到库内艺人/组合，请手动选择")
    for artist in db.scalars(select(Artist).where(Artist.active_filter())):
        for variant in _variants(artist, ARTIST_NAME_ATTRS):
            if _allow_entity_variant(variant) and _hit_variant(
                variant, title_texts, cjk_bounded=cjk_bounded
            ):
                artist_ids.add(artist.id)
                break
        if not is_strict and len(artist_ids) >= MAX_ENTITIES:
            break
    for group in db.scalars(select(Group).where(Group.active_filter())):
        for variant in _variants(group, GROUP_NAME_ATTRS):
            if _allow_entity_variant(variant) and _hit_variant(
                variant, title_texts, cjk_bounded=cjk_bounded
            ):
                group_ids.add(group.id)
                break
        if not is_strict and len(group_ids) >= MAX_ENTITIES:
            break

    if is_strict and group_ids:
        allowed = related_artist_ids_for_groups(db, group_ids)
        dropped = artist_ids - allowed
        artist_ids &= allowed
        if dropped:
            names = [
                a.name
                for a in db.scalars(select(Artist).where(Artist.id.in_(dropped)))
                if a.name
            ]
            if names:
                result["notices"].append(
                    "严格匹配：未自动关联 "
                    + "、".join(names[:4])
                    + (" 等" if len(names) > 4 else "")
                    + "（与已识别组合无关）"
                )
        if len(artist_ids) > MAX_ENTITIES:
            artist_ids = set(list(artist_ids)[:MAX_ENTITIES])
        if len(group_ids) > MAX_ENTITIES:
            group_ids = set(list(group_ids)[:MAX_ENTITIES])

    # ===== 2. 组装歌曲候选池 =====
    pool_ids: set[int] = set()
    conds = []
    if artist_ids:
        conds.append(
            (Song.release_artist_type == "artist") & Song.release_artist_id.in_(artist_ids)
        )
    if group_ids:
        conds.append(
            (Song.release_artist_type == "group") & Song.release_artist_id.in_(group_ids)
        )
    if conds:
        for song in db.scalars(select(Song).where(Song.active_filter(), or_(*conds))):
            pool_ids.add(song.id)
        rel_conds = []
        if artist_ids:
            rel_conds.append(SongArtistRelation.artist_id.in_(artist_ids))
        if group_ids:
            rel_conds.append(SongArtistRelation.group_id.in_(group_ids))
        pool_ids.update(
            db.scalars(
                select(SongArtistRelation.song_id).where(or_(*rel_conds))
            ).all()
        )
    identified = bool(pool_ids)
    if not identified:
        # 没识别出艺人/组合：全库扫描，门槛更保守
        pool_ids = set(
            db.scalars(select(Song.id).where(Song.active_filter())).all()
        )

    songs = {
        s.id: s
        for s in db.scalars(select(Song).where(Song.id.in_(pool_ids), Song.active_filter()))
    } if pool_ids else {}

    # ===== 3. 歌名变体扫描（标题整词；简介只提示不自动填）=====
    hits: list[tuple[int, str, str, str, int]] = []  # (song_id, variant, field, tier, index)
    desc_only: list[str] = []
    for song in songs.values():
        best: Optional[tuple[str, str, int]] = None  # (variant, field, index)
        desc_hit = False
        for variant in _variants(song, SONG_NAME_ATTRS):
            if not _allow_song_variant(variant, identified=identified):
                continue
            hit = _hit_variant(variant, title_texts, cjk_bounded=True)
            if hit:
                field, idx = hit
                if best is None or idx < best[2]:
                    best = (variant, field, idx)
                continue
            if desc_text and _hit_variant(
                variant, [("description", desc_text)], cjk_bounded=True
            ):
                desc_hit = True
        if best:
            variant, field, idx = best
            hits.append((song.id, variant, field, "title", idx))
        elif desc_hit and song.name:
            desc_only.append(song.name)

    if desc_only:
        shown = "、".join(desc_only[:4])
        extra = " 等" if len(desc_only) > 4 else ""
        result["notices"].append(f"简介中出现「{shown}」{extra}，未自动关联，请核实")

    # 标题里已有其他非数字歌名时，开头的独立数字当年份（24 tripleS GND）
    title_map = {field: text for field, text in title_texts}
    has_non_numeric = any(not _is_numeric_song_variant(v) for _sid, v, _f, _t, _i in hits)
    if has_non_numeric:
        hits = [
            h
            for h in hits
            if not (
                _is_numeric_song_variant(h[1])
                and _is_leading_index(title_map.get(h[2], ""), h[4])
            )
        ]

    # 标题类命中按出现位置排序（串烧顺序）
    hits.sort(key=lambda h: h[4])

    # ===== 4. 时长守门 + 组装结果 =====
    tracks: list[dict[str, Any]] = []
    for song_id, _variant, field, _tier, _idx in hits:
        song = songs[song_id]
        if (
            len(hits) == 1
            and duration
            and song.duration
            and abs(duration - song.duration) > DURATION_REVIEW_GUARD
        ):
            result["notices"].append(
                f"「{song.name}」与库内时长差较大（{duration}s vs {song.duration}s），未自动关联，请人工核实"
            )
            continue
        album_ids = [
            t.album_id for t in song.album_tracks
        ]
        tracks.append(
            {
                "song_id": song.id,
                "song_name": song.name,
                "chinese_name": song.chinese_name,
                "album_ids": album_ids,
                "confidence": "high",
                "source": field,
            }
        )
    result["tracks"] = tracks

    # ===== 5. 实体清单（前端 preset 回显用）=====
    if artist_ids:
        for a in db.scalars(select(Artist).where(Artist.id.in_(artist_ids))):
            result["artists"].append(
                {"id": a.id, "name": a.name, "chinese_name": a.chinese_name}
            )
    if group_ids:
        for g in db.scalars(select(Group).where(Group.id.in_(group_ids))):
            result["groups"].append(
                {"id": g.id, "name": g.name, "chinese_name": g.chinese_name}
            )
    album_id_set = {aid for t in tracks for aid in t["album_ids"]}
    if album_id_set:
        for al in db.scalars(select(Album).where(Album.id.in_(album_id_set))):
            result["albums"].append(
                {"id": al.id, "name": al.name, "chinese_name": al.chinese_name}
            )
    return result
