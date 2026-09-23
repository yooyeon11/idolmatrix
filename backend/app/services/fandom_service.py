"""kpop.fandom.com（Fandom K-pop Wiki）数据源。

MediaWiki 标准 api.php：搜索、首段摘要、页面主图、首段 wikitext（解析 infobox
拿成员名单 / 出道日期 / 公司等结构化字段）。无需 API key；外部请求沿用代理
设置与统一 UA。调用方自带节流（候选列表 ≤8 次请求），本模块不做批量。

许可注意：Fandom 文本为 CC BY-SA，入库/展示时请在 sources 或 message 中
带上来源标注；图片走 static.wikia.nocookie.net，下载前需确认可用。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from app.models import Artist, Group
from app.models.membership import GroupMembership
from app.services.audiodb_service import ProviderError, download_image
from app.services.wiki_service import _http_json

_API = "https://kpop.fandom.com/api.php"
_TIMEOUT = 20
_SOURCE = "K-pop Fandom"

# infobox 字段别名（wiki 编辑者写法不统一，做容错映射）
_MEMBER_KEYS = ("members", "current_members", "current", "membership")
_FORMER_KEYS = ("former_members", "past_members")
_DEBUT_KEYS = ("debut", "debut_date", "debuted")
_COMPANY_KEYS = ("company", "companies", "label", "labels", "agency", "agencies")
_FANDOM_KEYS = ("fandom_name", "fandomname", "fandom")
_ALIAS_KEYS = ("birth_name", "native_name", "korean_name", "real_name", "other_names")


def _api_get(params: dict[str, Any], timeout: int = _TIMEOUT) -> dict[str, Any]:
    payload = {"format": "json", "formatversion": 2, **params}
    return _http_json(_API, payload, timeout=timeout, error_hint="K-pop Fandom")


# ===== 基础查询 =====

def search_pages(q: str, *, limit: int = 6) -> list[dict[str, str]]:
    """全文搜索，返回 [{title, snippet}]；无结果为空列表。"""
    q = (q or "").strip()
    if not q:
        return []
    try:
        data = _api_get({"action": "query", "list": "search", "srsearch": q, "srlimit": limit})
    except Exception:
        return []
    out: list[dict[str, str]] = []
    for r in (data.get("query") or {}).get("search") or []:
        title = (r.get("title") or "").strip()
        if title:
            out.append({"title": title, "snippet": _strip_html(r.get("snippet") or "")})
    return out


def query_pages_meta(titles: list[str]) -> dict[str, dict[str, Optional[str]]]:
    """批量取首段摘要 + 页面主图：{title: {extract, image}}。"""
    titles = [t for t in titles if t][:8]
    if not titles:
        return {}
    try:
        data = _api_get(
            {
                "action": "query",
                "prop": "extracts|pageimages",
                "explaintext": 1,
                "exintro": 1,
                "exlimit": "max",
                "piprop": "original",
                "titles": "|".join(titles),
            }
        )
    except Exception:
        return {}
    out: dict[str, dict[str, Optional[str]]] = {}
    for p in (data.get("query") or {}).get("pages") or []:
        title = (p.get("title") or "").strip()
        if not title:
            continue
        out[title] = {
            "extract": (p.get("extract") or "").strip() or None,
            "image": ((p.get("original") or {}).get("source")) or None,
        }
    return out


def page_wikitext_full(title: str) -> Optional[str]:
    """取整页 wikitext（含 discography 等章节）。失败返回 None。"""
    title = (title or "").strip()
    if not title:
        return None
    try:
        data = _api_get({"action": "parse", "page": title, "prop": "wikitext"})
    except Exception:  # noqa: BLE001
        return None
    return ((data.get("parse") or {}).get("wikitext")) or None


def page_wikitext_lead(title: str) -> Optional[str]:

    """页面首段 wikitext（含 infobox 模板）。"""
    try:
        data = _api_get({"action": "parse", "page": title, "prop": "wikitext", "section": 0})
    except Exception:
        return None
    text = (data.get("parse") or {}).get("wikitext") or ""
    return text.strip() or None


# ===== wikitext 解析 =====

def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def _lead_plain_text(wikitext: Optional[str], max_len: int = 600) -> Optional[str]:
    """把导语 wikitext 清洗成纯文本段落（Fandom extracts 接口常返回空时的兜底）。

    去除注释/引用/文件链接/嵌套模板，转换内链为显示文本，取前几段非空内容。
    """
    if not wikitext:
        return None
    text = re.sub(r"<!--.*?-->", " ", wikitext, flags=re.S)
    text = re.sub(r"<ref[^>]*/>", " ", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", " ", text, flags=re.S)
    text = re.sub(r"\[\[(?:File|Image|文件|图像):[^\]]*\]\]", " ", text, flags=re.I)
    # 去除平衡的 {{...}} 模板（可嵌套）
    while True:
        start = text.find("{{")
        if start < 0:
            break
        depth = 0
        end = -1
        for i in range(start, len(text) - 1):
            if text[i : i + 2] == "{{":
                depth += 1
            elif text[i : i + 2] == "}}":
                depth -= 1
                if depth == 0:
                    end = i + 2
                    break
        if end < 0:
            text = text[:start]
            break
        text = text[:start] + " " + text[end:]
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"'{2,3}", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in text.split("\n")]
    paragraphs = [p for p in paragraphs if p]
    out: List[str] = []
    total = 0
    for p in paragraphs:
        if total + len(p) > max_len and out:
            break
        out.append(p)
        total += len(p)
        if total >= max_len:
            break
    return " ".join(out).strip() or None


def _extract_first_template(wikitext: str) -> Optional[str]:
    """取第一个平衡的 {{...}} 模板块。"""
    start = wikitext.find("{{")
    if start < 0:
        return None
    depth = 0
    i = start
    n = len(wikitext)
    while i < n:
        if wikitext.startswith("{{", i):
            depth += 1
            i += 2
            continue
        if wikitext.startswith("}}", i):
            depth -= 1
            i += 2
            if depth == 0:
                return wikitext[start:i]
            continue
        i += 1
    return None


def _find_infobox_template(wikitext: str) -> Optional[str]:
    """找第一个「名字含 infobox / 이름」的平衡模板块。

    页面开头常有 Quote / small 等装饰模板（部分条目 infobox 不在最前），
    只取第一个模板会在这些页面上解析为空。
    """
    wikitext = wikitext or ""
    i = 0
    n = len(wikitext)
    while i < n:
        start = wikitext.find("{{", i)
        if start < 0:
            return None
        depth = 0
        j = start
        while j < n:
            if wikitext.startswith("{{", j):
                depth += 1
                j += 2
                continue
            if wikitext.startswith("}}", j):
                depth -= 1
                j += 2
                if depth == 0:
                    break
                continue
            j += 1
        if depth != 0:
            return None
        tpl = wikitext[start:j]
        name = tpl[2:-2].strip()[:80].lower()
        if "infobox" in name or "이름" in name:
            return tpl
        i = j
    return None


def _split_top_level(body: str) -> list[str]:
    """按顶层 | 切分（跳过 {{}} 与 [[]] 内部的竖线）。"""
    parts: list[str] = []
    buf: list[str] = []
    i, n, depth = 0, len(body), 0
    while i < n:
        two = body[i : i + 2]
        if two in ("{{", "[["):
            depth += 1
            buf.append(two)
            i += 2
            continue
        if two in ("}}", "]]"):
            depth = max(0, depth - 1)
            buf.append(two)
            i += 2
            continue
        ch = body[i]
        if ch == "|" and depth == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _clean_value(v: str) -> str:
    """wikitext 值 → 纯文本。`<br>` 转成换行，保留成员等列表的条目边界。"""
    v = re.sub(r"<ref[^>]*/>", "", v)
    v = re.sub(r"<ref[^>]*>.*?</ref>", "", v, flags=re.S)
    v = re.sub(r"<br\s*/?>", "\n", v, flags=re.I)
    while "{{" in v:
        stripped = re.sub(
            r"\{\{([^{}]*)\}\}",
            lambda m: m.group(1).split("|")[-1].strip(),
            v,
        )
        if stripped == v:
            break
        v = stripped
    v = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]", r"\1", v)
    v = v.replace("'''", "").replace("''", "")
    v = re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", " ", v))
    lines = [ln.strip(" \t*") for ln in v.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def parse_infobox(wikitext: str) -> dict[str, str]:
    """解析首段 wikitext 里的 infobox 模板参数（key 全部小写下划线化，值已清理）。"""
    tpl = _find_infobox_template(wikitext or "")
    if not tpl:
        return {}
    body = tpl[2:-2]
    parts = _split_top_level(body)
    if len(parts) < 2:
        return {}
    template_name = parts[0].strip().lower()
    if "infobox" not in template_name and "이름" not in template_name:
        return {}
    out: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        key = re.sub(r"\s+", "_", k.strip().lower())
        val = _clean_value(v)
        if key and val:
            out.setdefault(key, val)
    return out


def parse_infobox_raw(wikitext: str) -> dict[str, str]:
    """同 parse_infobox，但值保留 wikitext 原文（成员条目需解析链接 target）。"""
    tpl = _find_infobox_template(wikitext or "")
    if not tpl:
        return {}
    body = tpl[2:-2]
    parts = _split_top_level(body)
    if len(parts) < 2:
        return {}
    template_name = parts[0].strip().lower()
    if "infobox" not in template_name and "이름" not in template_name:
        return {}
    out: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        key = re.sub(r"\s+", "_", k.strip().lower())
        val = v.strip()
        if key and val:
            out.setdefault(key, val)
    return out


def _first(info: dict[str, str], keys: tuple[str, ...]) -> Optional[str]:
    for k in keys:
        v = info.get(k)
        if v:
            return v
    return None


def _split_entries(raw: str) -> list[str]:
    """raw wikitext 值按条目切分：`<br>`、换行、wiki `*` 列表均为边界。"""
    parts = re.split(r"<br\s*/?>|\n\s*\**\s*", raw)
    return [p.strip() for p in parts if p.strip()]


def _parse_member(raw_entry: str) -> Optional[dict[str, str]]:
    """成员条目 → {name, korean_name}。

    name 取首个 wiki 链接里非韩文的一侧（display 优先，韩文 display 回落
    link target）；korean_name 取条目中的韩文片段。
    """
    text = _clean_value(raw_entry)
    if not text:
        return None
    korean_m = re.search(r"[가-힣]+", text)
    korean = korean_m.group(0) if korean_m else None

    name: Optional[str] = None
    for target, display in re.findall(r"\[\[([^\]|]*)(?:\|([^\]]*))?\]\]", raw_entry):
        target = (target or "").strip()
        display = (display or "").strip()
        cand = display if display and not re.search(r"[가-힣]", display) else target
        if cand and not re.search(r"[가-힣]", cand):
            name = cand
            break
    if not name:
        cleaned_first = text.split("\n")[0]
        name = re.sub(r"[（(]?[가-힣]+[）)]?", "", cleaned_first).strip(" ,;/·-")
    if not name:
        name = korean or ""
    if not name:
        return None
    return {"name": name, "korean_name": korean, "page": target or name}


def _parse_date(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", v)
    if m and m.group(1).lower() in months:
        try:
            return date(int(m.group(3)), months[m.group(1).lower()], int(m.group(2))).isoformat()
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", v)
    if m and m.group(2).lower() in months:
        try:
            return date(int(m.group(3)), months[m.group(2).lower()], int(m.group(1))).isoformat()
        except ValueError:
            return None
    m = re.search(r"(\d{4})[.年/\-]\s*(\d{1,2})?[.月/\-]?\s*(\d{1,2})?", v)
    if m:
        year = int(m.group(1))
        # 缺月或日时不要编造 1 月 1 日；只返回年份供展示，入库需完整 YYYY-MM-DD
        if not m.group(2) or not m.group(3):
            return str(year)
        try:
            return date(year, int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    return None


def _members_from(info: dict[str, str], keys: tuple[str, ...]) -> list[dict[str, str]]:
    raw = _first(info, keys)
    if not raw:
        return []
    out: list[dict[str, str]] = []
    for entry in _split_entries(raw):
        m = _parse_member(entry)
        if m and m not in out:
            out.append(m)
    return out[:30]


# ===== 对外入口 =====

def fetch_detail(title: str) -> dict[str, Any]:
    """取页面详情：摘要 + 主图 + infobox 结构化字段。页面不存在时抛 ProviderError。"""
    title = (title or "").strip()
    meta = query_pages_meta([title]).get(title)
    if meta is None or (meta.get("extract") is None and meta.get("image") is None):
        # 二次确认页面是否真的不存在（摘要与主图都可能缺）
        try:
            data = _api_get({"action": "query", "titles": title})
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"Fandom 请求失败: {e}") from e
        pages = (data.get("query") or {}).get("pages") or []
        if any(p.get("missing") for p in pages):
            raise ProviderError("Fandom 上没有这个条目")

    wikitext = page_wikitext_lead(title)
    info = parse_infobox(wikitext) if wikitext else {}
    info_raw = parse_infobox_raw(wikitext) if wikitext else {}
    kind = (
        "group"
        if any(k in info for k in _MEMBER_KEYS + _FORMER_KEYS)
        or "group" in (wikitext or "")[:200].lower()
        else "artist"
    )
    # 成员条目需要 raw wikitext（解析 [[链接|显示]] 的 target 作为规范名）
    members = _members_from(info_raw, _MEMBER_KEYS)
    former = _members_from(info_raw, _FORMER_KEYS)
    aliases = [
        _clean_value(info[k])
        for k in _ALIAS_KEYS
        if info.get(k) and _clean_value(info[k]) not in (title,)
    ]

    fields: dict[str, Any] = {}
    debut = _parse_date(_first(info, _DEBUT_KEYS))
    if debut:
        fields["debut_date"] = debut
    company = _first(info, _COMPANY_KEYS)
    if company:
        fields["company"] = company
    fandom_name = _first(info, _FANDOM_KEYS)
    if fandom_name:
        fields["fandom_name"] = fandom_name
    if members:
        fields["members"] = members
    if former:
        fields["former_members"] = former
    if aliases:
        fields["aliases"] = aliases[:8]

    meta_bits: list[str] = []
    if debut:
        meta_bits.append(f"出道 {debut}")
    if company:
        meta_bits.append(f"公司 {company}")
    if members:
        meta_bits.append(f"成员 {len(members)} 人")

    return {
        "external_id": f"fandom:{title}",
        "name": title,
        "kind": kind,
        "biography": (meta.get("extract") if meta else None) or _lead_plain_text(wikitext),
        "biography_lang": "en" if (meta and meta.get("extract")) else None,
        "thumb": meta.get("image") if meta else None,
        "logo": None,
        "fanart": None,
        "banner": None,
        "wide": None,
        "gallery": [meta["image"]] if (meta and meta.get("image")) else [],
        "source": _SOURCE,
        "meta": " · ".join(meta_bits) or None,
        "fandom_fields": fields,
    }


def build_search_candidates(q: str, *, limit: int = 4) -> list[dict[str, Any]]:
    """站点获取候选：搜索 + 批量摘要/主图，shape 与 external_providers 候选一致。"""
    pages = search_pages(q, limit=limit + 2)
    if not pages:
        return []
    titles = [p["title"] for p in pages[:limit]]
    meta = query_pages_meta(titles)
    out: list[dict[str, Any]] = []
    for t in titles:
        m = meta.get(t) or {}
        snippet = next((p["snippet"] for p in pages if p["title"] == t), "")
        out.append(
            {
                "external_id": f"fandom:{t}",
                "name": t,
                "kind": "group",  # 候选阶段不做类型判定，详情阶段由 infobox 修正
                "thumbnail": m.get("image"),
                "bio_excerpt": m.get("extract") or snippet or None,
                "source": _SOURCE,
            }
        )
    return out


def download_avatar(image_url: str) -> tuple[bytes, str]:
    """下载 Fandom 页面主图（走统一图片下载：UA/重试/类型校验）。"""
    return download_image(image_url)


# ===== 二期：结构化字段应用（fetch-external 调用） =====

def apply_fandom_extras(
    db: Session, kind: str, entity: Any, fields: dict[str, Any]
) -> list[str]:
    """把 infobox 字段应用到实体：出道日期填充、别名合并、组合成员导入。

    只填空缺（出道日期）、只合并不存在的别名、成员按归一化名匹配去重；
    返回给前端展示的消息列表。
    """
    from app.services.name_match import match_artist_exact, merge_artist_aliases

    messages: list[str] = []
    if not fields:
        return messages

    debut = fields.get("debut_date")
    if debut:
        try:
            if entity.debut_date is None:
                entity.debut_date = date.fromisoformat(str(debut))
                messages.append(f"已填入出道日期 {debut}")
        except (TypeError, ValueError):
            pass

    aliases = fields.get("aliases") or []
    if aliases and kind == "artists" and hasattr(entity, "aliases"):
        if merge_artist_aliases(entity, [str(a) for a in aliases]):
            messages.append("已补充别名：" + "、".join(str(a) for a in aliases))

    members = fields.get("members") or []
    if members and kind == "groups":
        created: list[str] = []
        existed: list[str] = []
        for m in members[:20]:
            name = str(m.get("name") or "").strip()
            if not name:
                continue
            korean = str(m.get("korean_name") or "").strip() or None
            hit = match_artist_exact(db, name, extra=[korean] if korean else None)
            if hit is not None:
                artist = hit
                existed.append(artist.chinese_name or artist.name)
            else:
                artist = Artist(name=name, korean_name=korean)
                db.add(artist)
                db.flush()
                created.append(artist.chinese_name or artist.name)
            already = db.query(GroupMembership).filter(
                GroupMembership.group_id == entity.id,
                GroupMembership.artist_id == artist.id,
                GroupMembership.status == "Active",
            ).first()
            if already is None:
                db.add(GroupMembership(group_id=entity.id, artist_id=artist.id, status="Active"))
        if created:
            messages.append("已从 Fandom 建立成员：" + "、".join(created))
        if existed:
            messages.append("成员已存在，跳过：" + "、".join(existed))

    return messages
