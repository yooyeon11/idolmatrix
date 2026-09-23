"""资料源同步：外部百科 → 数据库工作台的确定性字段同步。

流程（与「AI 直写」对立）：
1. preview：给定来源 URL，抓取结构化字段（Fandom infobox / Wikidata 声明），
   与库内当前值逐字段比对，产出「字段级 diff 提案」。绝不写库。
2. apply：用户勾选采纳的字段 → 白名单内标量字段写入（🔒锁定的字段跳过），
   来源 URL 记入 external_links（按 url 去重）。

AI 在这条链路里的角色被刻意弱化：抓取是确定性的（infobox/声明 → 字段映射），
冲突取舍由人完成。步 4「补全员 A」可在百科抓取后对空缺字段做 AI 提案，
每条 AI 字段必须打标（field_origins=ai），且禁止编造生日/出入团日期；
人审勾选后才经 apply 写入。

MVP 范围：组合（groups）的标量字段。成员/公司关系的同步涉及实体创建与
合并，作为预览信息展示、暂不自动写入。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.group import Group
from app.services import fandom_service, wiki_service
from app.services import ai_service

# 允许通过资料源写入的字段白名单（均为无副作用的标量字段）
APPLICABLE_FIELDS = {
    "debut_date": "出道日期",
    "korean_name": "韩文名",
    "english_name": "英文名",
    "chinese_name": "中文名",
    "description": "描述",
    "tagline": "一句话简介",
}

_FANDOM_URL_RE = re.compile(r"kpop\.fandom\.com/wiki/([^?#]+)")
_WIKIDATA_URL_RE = re.compile(r"wikidata\.org/(?:wiki/)?(Q\d+)")
_WIKIPEDIA_URL_RE = re.compile(
    r"(?:([a-z]{2,3})\.)?wikipedia\.org/wiki/([^?#]+)", re.I
)
_BAIDU_URL_RE = re.compile(r"baike\.baidu\.com/(?:item/[^?#]+|wiki/\d+)", re.I)

# 生长步骤别名：数字或命名均可
_STEP_ALIASES = {
    "1": "identity",
    "identity": "identity",
    "identity_company": "identity",
    "company": "company",
    "companies": "company",
    "2": "subunits",
    "subunits": "subunits",
    "subunit": "subunits",
    "3": "members",
    "members": "members",
    "member": "members",
    "4": "member_details",
    "member_details": "member_details",
    "details": "member_details",
    "5": "albums",
    "albums": "albums",
    "album": "albums",
    "6": "tracks",
    "tracks": "tracks",
    "songs": "tracks",
    "song": "tracks",
}


def _normalize_step(step: Optional[str]) -> Optional[str]:
    if step is None:
        return None
    s = str(step).strip().lower()
    if not s:
        return None
    return _STEP_ALIASES.get(s, s)


class SourceError(Exception):
    """来源解析 / 抓取失败。"""


def _detect(url: str) -> tuple[str, str]:
    """识别来源类型与条目键。返回 (kind, key)。"""
    url = (url or "").strip()
    m = _FANDOM_URL_RE.search(url)
    if m:
        return "fandom", m.group(1)
    m = _WIKIDATA_URL_RE.search(url)
    if m:
        return "wikidata", m.group(1)
    m = _WIKIPEDIA_URL_RE.search(url)
    if m:
        lang = (m.group(1) or "en").lower()
        title = m.group(2)
        return "wikipedia", f"{lang}:{title}"
    if _BAIDU_URL_RE.search(url):
        return "baidu", url
    raise SourceError(
        "暂支持 kpop.fandom.com / wikidata.org / wikipedia.org / baike.baidu.com 链接"
    )


def _norm(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _clean_date(v: Any) -> Optional[str]:
    """归一日期：取 YYYY-MM-DD 前缀（Wikidata 可能带精度后缀或负纪年）。"""
    s = _norm(v)
    if not s:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    m = re.match(r"(\d{4})", s)
    return m.group(1) if m else s


def _current(group: Group, field: str) -> Optional[str]:
    return _norm(getattr(group, field, None))


def _fandom_preview(title: str, group: Group, info_raw: Optional[dict] = None) -> dict:
    detail = fandom_service.fetch_detail(title)
    ff = detail.get("fandom_fields") or {}
    if info_raw is None:
        # 小分队发现（associated）与成员富集都需要原始 infobox
        _wt = fandom_service.page_wikitext_lead(title)
        info_raw = fandom_service.parse_infobox_raw(_wt) if _wt else {}
    ff_members = [
        dict(m, page=m.get("page") or "") for m in (ff.get("members") or [])[:30] if isinstance(m, dict)
    ]
    _enrich_members_fandom(ff_members, group_name=title)
    if info_raw is None:
        # 小分队发现需要原始 infobox（associated 字段），此处补抓一次
        _wt = fandom_service.page_wikitext_lead(title)
        info_raw = fandom_service.parse_infobox_raw(_wt) if _wt else {}
    items: List[dict] = []
    if ff.get("debut_date"):
        items.append(_diff_item(group, "debut_date", _clean_date(ff["debut_date"]), "K-pop Fandom"))
    if detail.get("biography"):
        bio = _norm(detail["biography"])
        translated = ai_service.translate_to_simplified(bio)
        if translated:
            bio = translated
            items.append(_diff_item(group, "description", bio, "K-pop Fandom（已译）"))
        else:
            items.append(_diff_item(group, "description", bio, "K-pop Fandom"))
    for alias in (ff.get("aliases") or [])[:1]:
        if _norm(alias) and not _current(group, "korean_name"):
            # infobox 的 native/korean name 常混在别名里，仅当库内韩文名为空时提议
            items.append(_diff_item(group, "korean_name", _norm(alias), "K-pop Fandom"))
    return {
        "source_type": "fandom",
        "source_label": f"K-pop Fandom · {title}",
        "source_url": f"https://kpop.fandom.com/wiki/{title}",
        "items": items,
        "extra": {
            "company_raw": _norm(ff.get("company")),
            "fandom_name": _norm(ff.get("fandom_name")),
            "sub_units": _fandom_sub_units(title, info_raw),
            "members": [
                {
                    "name": m.get("name"),
                    "korean_name": m.get("korean_name"),
                    "page": _norm(m.get("page")) or _norm(m.get("_fandom_page")),
                    "birth_date": _clean_date(m.get("birth_date")),
                    "birth_place": _norm(m.get("birth_place")),
                    "occupation": _norm(m.get("occupation")),
                    "chinese_name": _norm(m.get("chinese_name")),
                    "description": _norm(m.get("description")),
                    "positions": m.get("positions"),
                    "_source": "K-pop Fandom",
                }
                for m in ff_members
            ],
            "former_members": [
                {"name": m.get("name"), "korean_name": m.get("korean_name"), "_source": "K-pop Fandom"}
                for m in (ff.get("former_members") or [])[:30]
            ],
            "biography": _norm(detail.get("biography")),
            "thumb": detail.get("thumb"),
        },
    }


def _wikidata_preview(qid: str, group: Group) -> dict:
    info = wiki_service.get_entity_info(qid, "group")
    if info is None:
        raise SourceError(f"Wikidata 条目 {qid} 获取失败")
    items: List[dict] = []
    if info.get("inception"):
        items.append(_diff_item(group, "debut_date", _clean_date(info["inception"]), "Wikidata"))
    if _norm(info.get("label")):
        items.append(_diff_item(group, "english_name", _norm(info["label"]), "Wikidata"))
    if _norm(info.get("label_ko")):
        items.append(_diff_item(group, "korean_name", _norm(info["label_ko"]), "Wikidata"))
    if _norm(info.get("label_zh")):
        items.append(_diff_item(group, "chinese_name", _norm(info["label_zh"]), "Wikidata"))
    if _norm(info.get("wikipedia_extract")):
        wiki_desc = _norm(info["wikipedia_extract"])
        wiki_desc_translated = ai_service.translate_to_simplified(wiki_desc)
        if wiki_desc_translated:
            items.append(
                _diff_item(group, "description", wiki_desc_translated, "Wikipedia（已译）")
            )
        else:
            items.append(
                _diff_item(group, "description", wiki_desc, "Wikipedia")
            )
    members = [
        {
            "name": m.get("name"),
            "korean_name": m.get("korean_name"),
            "chinese_name": m.get("chinese_name"),
            "birth_date": _clean_date(m.get("birth_date")),
            "description": _norm(m.get("description")),
            "join_date": _clean_date(m.get("join_date")),
            "leave_date": _clean_date(m.get("leave_date")),
            "_source": "Wikidata",
        }
        for m in (info.get("members") or [])[:30]
    ]
    sub_units = [
        {"name": u.get("name"), "members": list((u.get("members") or [])[:40]), "source": "Wikidata"}
        for u in wiki_service.get_sub_units(qid)
    ]
    return {
        "source_type": "wikidata",
        "source_label": f"Wikidata · {qid}",
        "source_url": f"https://www.wikidata.org/wiki/{qid}",
        "items": items,
        "extra": {
            "members": members,
            "sub_units": sub_units,
            "thumb": info.get("image"),
            "description": _norm(info.get("description")),
        },
    }


def _wikipedia_preview(lang_title: str, group: Group) -> dict:
    """从维基百科摘要页抽取身份字段；成员/小分队走 Wikidata 若能解析到 QID。"""
    import urllib.parse
    import urllib.request

    lang, _, title = lang_title.partition(":")
    if not title:
        lang, title = "en", lang_title
    title = urllib.parse.unquote(title.replace("_", " "))
    host = f"{lang}.wikipedia.org"
    url = f"https://{host}/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
    summary = {}
    try:
        req = urllib.request.Request(
            f"https://{host}/api/rest_v1/page/summary/{urllib.parse.quote(title.replace(' ', '_'))}",
            headers={"User-Agent": "IdolMatrix/1.0 (catalog-growth)"},
        )
        from app.services import proxy_config as _pcfg

        with _pcfg.urlopen(req, timeout=12) as resp:
            import json
            summary = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"维基百科摘要获取失败：{e}") from e
    extract = _norm(summary.get("extract"))
    # 中文维基：成员表含官方中文名/生日/出生地，喂给成员生长提案
    zh_members: List[dict] = []
    if lang == "zh":
        try:
            for r in wiki_service.get_group_members_zhwiki(title) or []:
                zh_members.append(
                    {
                        "name": r.get("stage_en") or r.get("stage_cn"),
                        "korean_name": None,
                        "chinese_name": r.get("chinese_name"),
                        "birth_date": r.get("birth_date"),
                        "birth_place": r.get("birth_place"),
                        "former": r.get("status") == "Former",
                        "_source": "中文维基百科",
                    }
                )
        except Exception:  # noqa: BLE001
            pass
    label = _norm(summary.get("title")) or title
    items: List[dict] = []
    if label:
        # 中文维基 → chinese_name；其他 → english_name
        field = "chinese_name" if lang == "zh" else "english_name"
        items.append(_diff_item(group, field, label, "Wikipedia"))
    if extract:
        items.append(_diff_item(group, "description", extract, "Wikipedia"))
    # 尝试用标题搜 Wikidata 补成员（失败则仅身份字段）
    extra: dict = {"members": [], "sub_units": [], "description": extract}
    try:
        hits = wiki_service.search_entities(label, limit=3) or []
        qid = None
        for h in hits:
            if h.get("qid"):
                qid = h["qid"]
                break
        if qid:
            wd = _wikidata_preview(qid, group)
            # 合并成员/小分队，但字段以 Wikipedia 摘要为准（已在 items）
            extra["members"] = (wd.get("extra") or {}).get("members") or []
            extra["sub_units"] = (wd.get("extra") or {}).get("sub_units") or []
            extra["thumb"] = (wd.get("extra") or {}).get("thumb")
        # 中文维基成员表：官方中文名/生日/出生地（izna 这类成员条目无中文标签时的重要来源）
        if lang == "zh":
            try:
                zh_rows = wiki_service.get_group_members_zhwiki(title) or []
            except Exception:  # noqa: BLE001
                zh_rows = []
            existing_names = {_norm(m.get("name")).casefold() for m in extra.get("members") or []}
            for r in zh_rows:
                nm = r.get("stage_en") or r.get("stage_cn") or ""
                if not nm or _norm(nm).casefold() in existing_names:
                    continue
                existing_names.add(_norm(nm).casefold())
                extra.setdefault("members", []).append(
                    {
                        "name": nm,
                        "korean_name": None,
                        "chinese_name": r.get("chinese_name"),
                        "birth_date": r.get("birth_date"),
                        "birth_place": r.get("birth_place"),
                        "former": r.get("status") == "Former",
                        "_source": "中文维基百科",
                    }
                )
    except Exception:  # noqa: BLE001
        pass
    return {
        "source_type": "wikipedia",
        "source_label": f"Wikipedia · {lang}:{title}",
        "source_url": url,
        "items": items,
        "extra": extra,
    }


def _baidu_preview(url: str, group: Group) -> dict:
    """百度百科：P0 至少接受手动 URL；尽力抽取标题/简介，失败也返回可记录来源。"""
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
        # 不阻断：仍允许把链接记入来源
        return {
            "source_type": "baidu",
            "source_label": "百度百科（抽取受限）",
            "source_url": url,
            "items": [],
            "extra": {"members": [], "sub_units": [], "baidu_note": f"页面抓取失败：{e}"},
        }
    items: List[dict] = []
    if title:
        items.append(_diff_item(group, "chinese_name", title, "百度百科"))
    if extract:
        baidu_desc_translated = ai_service.translate_to_simplified(extract)
        if baidu_desc_translated:
            items.append(_diff_item(group, "description", baidu_desc_translated, "百度百科（已译）"))
        else:
            items.append(_diff_item(group, "description", extract, "百度百科"))
    return {
        "source_type": "baidu",
        "source_label": f"百度百科 · {title or '条目'}",
        "source_url": url,
        "items": items,
        "extra": {"members": [], "sub_units": [], "description": extract, "baidu_note": None},
    }


def _diff_item(group: Group, field: str, proposed: str, source: str) -> dict:
    current = _current(group, field)
    if field not in APPLICABLE_FIELDS:
        return {}
    return {
        "field": field,
        "label": APPLICABLE_FIELDS[field],
        "current": current,
        "proposed": proposed,
        "source": source,
        "same": (current or "") == (proposed or ""),
        "is_new": current is None,
    }


def preview_source(db: Session, uid: str, url: str) -> dict:
    """抓取单个来源并生成字段级 diff。只读，不写库。"""
    return preview_sources(db, uid, [url])


def _fandom_template_date(raw: Optional[str]) -> Optional[str]:
    """解析 Fandom 的 {{Birth date and age|2001|2|9}} 类模板 → YYYY-MM-DD。

    仅有年份（或缺月/日）时返回年份字符串，绝不编造 01-01。
    """
    s = _norm(raw)
    if not s or "{{" not in s:
        return None
    m = re.search(r"\{\{\s*[Bb]irth[^}|]*\|([^}]+)\}\}", s)
    if not m:
        return None
    parts = [q.strip().lower() for q in m.group(1).split("|")]
    year = next((q for q in parts if re.fullmatch(r"\d{4}", q)), None)
    if not year:
        return None
    rest = [q for q in parts[parts.index(year) + 1 :] if re.fullmatch(r"\d{1,2}", q)]
    if len(rest) < 2:
        return year
    from datetime import date as _date

    try:
        return _date(year=int(year), month=int(rest[0]), day=int(rest[1])).isoformat()
    except (ValueError, TypeError):
        return year


def _fandom_is_bad_title(title: str) -> bool:
    low = (title or "").lower()
    return (
        not title
        or "disambiguation" in low
        or "disambig" in low
        or "list of" in low
        or low.startswith("category:")
        or low.startswith("template:")
    )


def _fandom_pick_from_disambig(wikitext: str, name: str, group_name: Optional[str]) -> Optional[str]:
    """消歧义页：优先挑 [[Name (Group)|...]] 链接。"""
    name_n = _norm_name(name)
    group_n = _norm_name(group_name) if group_name else ""
    links = re.findall(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]", wikitext or "")
    ranked: List[tuple] = []
    for target, display in links:
        target = (target or "").strip()
        if _fandom_is_bad_title(target):
            continue
        tn = _norm_name(target)
        dn = _norm_name(display or "")
        base = _norm_name(target.split("(")[0])
        if name_n not in (tn, dn, base) and not tn.startswith(name_n):
            continue
        score = 0
        if group_n and group_n in tn:
            score += 5
        if base == name_n or tn.startswith(name_n):
            score += 2
        if "(" in target and ")" in target:
            score += 1
        ranked.append((score, target))
    if not ranked:
        return None
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return ranked[0][1]


def _fandom_resolve_member_page(
    name: Optional[str],
    group_name: Optional[str] = None,
    korean_name: Optional[str] = None,
) -> Optional[str]:
    """按成员名（+ 组合语境）搜索 kpop.fandom 成员页标题；无结果返回 None。"""
    name = _norm(name)
    if not name:
        return None
    group_name = _norm(group_name)
    korean_name = _norm(korean_name)
    queries: List[str] = []
    if group_name:
        queries.append(f"{name} ({group_name})")
        queries.append(f"{name} {group_name}")
    queries.append(name)
    if korean_name and korean_name != name:
        queries.append(korean_name)
    name_n = _norm_name(name)
    group_n = _norm_name(group_name) if group_name else ""
    seen_q: set = set()
    candidates: List[str] = []

    def _consider(title: str) -> None:
        t = _norm(title) or ""
        if t and not _fandom_is_bad_title(t) and t not in candidates:
            candidates.append(t)

    for q in queries:
        qn = q.strip().lower()
        if not qn or qn in seen_q:
            continue
        seen_q.add(qn)
        for p in fandom_service.search_pages(q, limit=8) or []:
            _consider(p.get("title") or "")

    if group_name:
        _consider(f"{name} ({group_name})")

    def _score(title: str) -> int:
        tn = _norm_name(title)
        base = _norm_name(title.split("(")[0])
        score = 0
        if base == name_n:
            score += 4
        if tn == name_n:
            score += 2
        if group_n and group_n in tn:
            score += 6
        if title.lower().startswith(name.lower()):
            score += 1
        return score

    ranked = sorted(candidates, key=lambda t: (-_score(t), t))
    for title in ranked:
        wt = fandom_service.page_wikitext_lead(title)
        if not wt:
            continue
        head = wt[:280].lower()
        if "{{disambig" in head or "{{disambiguation" in head or "may refer to" in head:
            picked = _fandom_pick_from_disambig(wt, name, group_name)
            if picked:
                return picked
            continue
        return title

    # 最后：即使候选被 bad_title 滤掉，也可能搜到消歧义页本身
    for q in queries[:2]:
        for p in fandom_service.search_pages(q, limit=5) or []:
            title = _norm(p.get("title")) or ""
            if not title:
                continue
            wt = fandom_service.page_wikitext_lead(title)
            if not wt:
                continue
            head = wt[:280].lower()
            if "{{disambig" in head or "may refer to" in head:
                picked = _fandom_pick_from_disambig(wt, name, group_name)
                if picked:
                    return picked
    return None



def _enrich_members_fandom(
    entries: List[dict],
    max_workers: int = 8,
    group_name: Optional[str] = None,
    *,
    allow_search: bool = False,
    deadline: Optional[float] = None,
    errors_out: Optional[List[str]] = None,
) -> None:
    """并行抓取成员自己的 Fandom 页面，就地补充生日/出生地/职业/担当/简介。

    allow_search=True 时（步4），若条目缺少 page，会按姓名 + 组合语境搜索 Fandom。
    组合页预览默认只跟随 infobox 已有 page 标题，避免误搜与多余网络请求。
    仅使用页面 infobox / 摘要，不调用 LLM 编造。
    单人失败不影响其他人；可选 deadline（time.monotonic）到点后停止新任务。
    """
    import time

    entries = [e for e in entries if isinstance(e, dict)]
    if not entries:
        return
    soft_errors = errors_out if errors_out is not None else []

    def _past_deadline() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    # 先解析缺失的成员页标题（可并行）；单人失败写入 soft_errors，不拖垮整批
    need_page = [
        e
        for e in entries
        if allow_search and not _norm(e.get("page")) and _norm(e.get("name"))
    ]
    if need_page and not _past_deadline():
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _resolve(e: dict) -> Optional[str]:
            if _past_deadline():
                return "deadline"
            try:
                page = _fandom_resolve_member_page(
                    e.get("name"), group_name=group_name, korean_name=e.get("korean_name")
                )
                if page:
                    e["page"] = page
                return None
            except Exception as ex:  # noqa: BLE001
                return f"{e.get('name') or '?'}: 搜索失败 {ex}"

        with ThreadPoolExecutor(max_workers=min(max_workers, len(need_page))) as pool:
            futs = {pool.submit(_resolve, e): e for e in need_page}
            for fut in as_completed(futs):
                msg = fut.result()
                if msg and msg != "deadline":
                    soft_errors.append(msg)
                    if len(soft_errors) <= 8 and errors_out is not None:
                        pass

    targets = [e for e in entries if _norm(e.get("page"))]
    if not targets or _past_deadline():
        return

    def _one(e: dict) -> None:
        if _past_deadline():
            return
        try:
            page = e["page"]
            wt = fandom_service.page_wikitext_lead(page)
            if not wt:
                return
            head = wt[:280].lower()
            if "{{disambig" in head or "{{disambiguation" in head or "may refer to" in head:
                picked = _fandom_pick_from_disambig(wt, e.get("name") or page, group_name)
                if not picked:
                    return
                page = picked
                e["page"] = page
                wt = fandom_service.page_wikitext_lead(page)
                if not wt:
                    return
            info = fandom_service.parse_infobox(wt)
            info_raw_m = fandom_service.parse_infobox_raw(wt)
            if not info and not info_raw_m:
                return
            # 生日必须从 raw 解析：{{Birth date and age|2001|2|9}} 被清洗后只剩 "9"
            birth = _fandom_template_date(
                info_raw_m.get("birth_date")
            ) or fandom_service._parse_date(info.get("birth_date"))
            if birth and not e.get("birth_date"):
                e["birth_date"] = birth
            for src_key, dst_key in (
                ("birth_place", "birth_place"),
                ("occupation", "occupation"),
            ):
                v = _norm(info.get(src_key))
                if v and not e.get(dst_key):
                    e[dst_key] = v
            # 中文名偶见于 native / other names
            for k in ("native_name", "korean_name", "birth_name", "other_names"):
                raw = _norm(info.get(k))
                if not raw:
                    continue
                # 含汉字片段时记作 chinese_name 候选
                zh = re.search(r"[一-鿿]{2,}", raw)
                if zh and not e.get("chinese_name"):
                    e["chinese_name"] = zh.group(0)
                    break
            pos_raw = _norm(info.get("positions") or info.get("position"))
            if pos_raw and not e.get("positions"):
                pos = [q.strip() for q in re.split(r"[、,/]+", pos_raw) if q.strip()]
                e["positions"] = pos[:4]
            # 简介：页面摘要（非 LLM）
            if not e.get("description"):
                meta = fandom_service.query_pages_meta([page]).get(page) or {}
                extract = _norm(meta.get("extract"))
                if extract:
                    e["description"] = extract[:600]
            e["_source"] = e.get("_source") or "K-pop Fandom"
            e["_fandom_page"] = page
            e["_fandom_url"] = _fandom_page_url(page)
        except Exception as ex:  # noqa: BLE001
            soft_errors.append(f"{e.get('name') or e.get('page') or '?'}: 抓取失败 {ex}")

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(max_workers, len(targets))) as pool:
        list(pool.map(_one, targets))


def _fandom_sub_units(group_name: str, info_raw: dict, max_units: int = 12) -> List[dict]:
    """从 Fandom 的 associated 字段发现小分队：双向验证（对方页面也关联本组合）。

    associated 可能包含厂牌/外部合作（如 SMTOWN），单向提及不可信；
    对方页面也提及本组合、且自身有成员名单，才认定为小分队。
    """
    group_name = (group_name or "").strip()
    if not group_name or not info_raw:
        return []
    raw = info_raw.get("associated") or info_raw.get("subunit") or info_raw.get("sub-unit") or ""
    links = re.findall(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]", raw)
    names: List[str] = []
    for target, display in links:
        n = (display or target).strip()
        if (
            n
            and _norm_name(n) != _norm_name(group_name)
            and all(_norm_name(n) != _norm_name(x) for x in names)
        ):
            names.append(n)
    if not names:
        return []

    from concurrent.futures import ThreadPoolExecutor

    def _check(name: str) -> Optional[dict]:
        try:
            wt = fandom_service.page_wikitext_lead(name)
            if not wt:
                return None
            other_raw = fandom_service.parse_infobox_raw(wt)
            assoc = _norm((other_raw.get("associated") or "") + " " + (other_raw.get("label") or ""))
            if _norm_name(group_name) not in _norm_name(assoc):
                return None
            detail = fandom_service._members_from(other_raw, fandom_service._MEMBER_KEYS)
            former = fandom_service._members_from(other_raw, fandom_service._FORMER_KEYS)
            return {
                "name": name,
                "members": [
                    {"name": m.get("name"), "korean_name": m.get("korean_name"), "_source": "K-pop Fandom"}
                    for m in detail[:40]
                ],
                "former_members": [
                    {"name": m.get("name"), "korean_name": m.get("korean_name"), "_source": "K-pop Fandom"}
                    for m in former[:40]
                ],
                "source": "K-pop Fandom",
            }
        except Exception:  # noqa: BLE001
            return None

    units: List[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for r in pool.map(_check, names[:max_units]):
            if r is not None:
                units.append(r)
    return units


def _extra_merge(extras: List[dict]) -> dict:
    """合并多来源的附加信息：成员去重并集、小分队并集、公司/简介取首个非空。"""
    members: List[dict] = []
    seen: set = set()
    companies: List[str] = []
    thumb = None
    description = None
    sub_units: List[dict] = []
    seen_units: set = set()
    for ex in extras:
        for m in ex.get("members") or []:
            name = _norm(m.get("name"))
            if not name or name in seen:
                continue
            seen.add(name)
            members.append(m)
        for c in [ex.get("company_raw")] if ex.get("company_raw") else []:
            if c not in companies:
                companies.append(c)
        thumb = thumb or ex.get("thumb")
        description = description or ex.get("description")
        for u in ex.get("sub_units") or []:
            uname = _norm_name(u.get("name"))
            if not uname or uname in seen_units:
                continue
            seen_units.add(uname)
            merged_members = sorted({(_norm(x) or "") for x in (u.get("members") or [])} - {""})
            for other in extras:
                if other is ex:
                    continue
                for ou in other.get("sub_units") or []:
                    if _norm_name(ou.get("name")) == uname:
                        merged_members = sorted(
                            set(merged_members) | {_norm(x) for x in (ou.get("members") or [])} - {""}
                        )
            sub_units.append(
                {"name": u.get("name"), "members": merged_members, "source": u.get("source")}
            )
    out: dict = {"members": members}
    if companies:
        out["company_raw"] = "；".join(companies)
    if sub_units:
        out["sub_units"] = sub_units
    if thumb:
        out["thumb"] = thumb
    if description:
        out["description"] = description
    return out


def _norm_name(s: Any) -> str:
    return re.sub(r"\s+", " ", _norm(s) or "").casefold()


def _split_company_names(raw: Any) -> List[str]:
    """把 infobox 公司字段拆成可读名称（去 wiki 链接 / <br> / 分隔符）。"""
    text = str(raw or "")
    text = re.sub(r"<br\s*/?>", ";", text, flags=re.I)
    text = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    out: List[str] = []
    seen: set = set()
    for part in re.split(r"[;；、|/]+", text):
        name = _norm(part)
        if not name or len(name) < 2:
            continue
        key = _norm_name(name)
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out[:8]


def _match_company_by_name(db: Session, name: str):
    from app.models.company import Company

    needle = _norm_name(name)
    if not needle:
        return None
    for c in db.scalars(select(Company).where(Company.deleted_at.is_(None))):
        variants = [c.name, c.chinese_name, c.english_name, c.korean_name]
        if isinstance(c.aliases, list):
            variants.extend(a for a in c.aliases if isinstance(a, str))
        if any(_norm_name(v) == needle for v in variants if v):
            return c
    return None


def _company_proposals(db: Session, group: Group, extras: List[dict]) -> List[dict]:
    """从来源 company_raw 产出公司关系提案（create / link / exists）。"""
    from app.models.company import GroupCompanyRelation

    names: List[str] = []
    seen: set = set()
    for ex in extras:
        for n in _split_company_names(ex.get("company_raw")):
            key = _norm_name(n)
            if key in seen:
                continue
            seen.add(key)
            names.append(n)
    if not names:
        return []
    existing_ids = {
        rel.company_id
        for rel in db.scalars(
            select(GroupCompanyRelation).where(GroupCompanyRelation.group_id == group.id)
        )
    }
    out: List[dict] = []
    for name in names:
        company = _match_company_by_name(db, name)
        if company is not None and company.id in existing_ids:
            action = "exists"
            cid = company.id
        elif company is not None:
            action = "link"
            cid = company.id
        else:
            action = "create"
            cid = None
        out.append(
            {
                "action": action,
                "name": name,
                "company_id": cid,
                "role": "经纪公司",
                "sources": ["来源页"],
            }
        )
    return out


def _subunit_proposals(db: Session, group: Group, extras: List[dict]) -> List[dict]:
    """小分队生长提案：来源里 P463 指向本组合的音乐组合。

    action: create（建小分队 + 铺成员）/ exists（已有同名子组合）。
    """
    from app.models.group import Group as GroupModel

    facts: Dict[str, dict] = {}
    order: List[str] = []
    for ex in extras:
        for u in ex.get("sub_units") or []:
            name = _norm(u.get("name"))
            if not name or _norm_name(name) == _norm_name(group.name):
                continue
            key = _norm_name(name)
            if key not in facts:
                facts[key] = {"name": name, "member_entries": [], "sources": set()}
                order.append(key)
            f = facts[key]
            if u.get("source"):
                f["sources"].add(u["source"])
            existing_names = {_norm_name(e.get("name")) for e in f["member_entries"]}
            for mn in u.get("members") or []:
                entry = mn if isinstance(mn, dict) else {"name": _norm(mn)}
                ename = _norm(entry.get("name"))
                if ename and ename not in existing_names:
                    f["member_entries"].append(entry)
                    existing_names.add(ename)

    if not order:
        return []
    children = {
        _norm_name(c.name): c
        for c in db.scalars(
            select(GroupModel).where(
                GroupModel.deleted_at.is_(None),
                GroupModel.parent_group_id == group.id,
            )
        )
    }
    out: List[dict] = []
    for key in order:
        f = facts[key]
        action = "exists" if key in children else "create"
        entries = f["member_entries"][:40]
        out.append(
            {
                "action": action,
                "name": f["name"],
                "member_names": [_norm(e.get("name")) or "" for e in entries],
                "member_entries": entries,
                "sources": sorted(f["sources"]),
            }
        )
    out.sort(key=lambda r: (r["action"] == "exists", r["name"]))
    return out


def _is_placeholder_release_date(d) -> bool:
    """历史 year→01-01 占位日：月日均为 1 时视为可被完整发行日覆盖。"""
    if d is None:
        return True
    try:
        return int(d.month) == 1 and int(d.day) == 1
    except Exception:  # noqa: BLE001
        return False


def _album_release_needs_update(album_release_date, proposal_release_date) -> bool:
    """提案有完整 YYYY-MM-DD，且库内为空或疑似年份占位时需要更新。"""
    prop = _clean_date(proposal_release_date)
    if not prop or len(prop) != 10:
        return False
    if album_release_date is None:
        return True
    if not _is_placeholder_release_date(album_release_date):
        return False
    try:
        from datetime import date as _date_cls

        return _date_cls.fromisoformat(prop) != album_release_date
    except ValueError:
        return False


def _ai_albums_from_pages(
    db: Session,
    group: Group,
    page_texts: List[tuple],
) -> tuple:
    """把来源页面文本交给入库 AI，提取组合的正式音乐发行物。

    返回 (proposals, meta)。proposals 与音乐平台候选同构（name/year/type/
    sources/extractor），由调用方与平台候选交叉确认合并。
    """
    cfg, skip = _ingest_ai_cfg(db)
    if not cfg:
        return [], {"status": "skipped", "error": skip}
    texts = [(label, (text or "")[:9000]) for label, text in page_texts if text]
    if not texts:
        return [], {"status": "skipped", "error": "没有可用页面文本"}

    group_name = (group.name or "").strip()
    system = (
        f"你是 K-pop 资料整理助手。从给出的百科/维基页面文本中，"
        f"列出组合「{group_name}」的正式音乐发行物（正规专辑/迷你专辑/EP/单曲）。"
        "只列页面中明确归属于该组合的发行物；其他歌手的同名内容、合辑中客串的不要。"
        "不确定的不要列。只返回 JSON 对象。"
    )
    body = "\n\n".join(f"【{label}】\n{text}" for label, text in texts)
    user = (
        f"{body}\n\n"
        '返回 JSON：{"albums":[{"name":"发行物名","type":"专辑/迷你专辑/EP/单曲","year":"YYYY"}]}'
    )
    from app.services.ai_service import chat_completion, parse_json

    try:
        raw = chat_completion(cfg, [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ], timeout=120)
        data = parse_json(raw)
    except Exception as e:  # noqa: BLE001
        return [], {"status": "error", "error": str(e)[:240]}

    from app.models.album import Album

    existing = {
        _norm_name(a.name)
        for a in db.scalars(
            select(Album).where(
                Album.deleted_at.is_(None),
                Album.release_artist_type == "group",
                Album.release_artist_id == group.id,
            )
        )
    }
    junk = ("karaoke", "tribute", "instrumental", "cover")
    out: List[dict] = []
    seen: set = set()
    for a in data.get("albums") or []:
        if not isinstance(a, dict):
            continue
        name = _norm(a.get("name"))
        if not name:
            continue
        low = name.casefold()
        if any(j in low for j in junk):
            continue
        key = _norm_name(name)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "action": "exists" if key in existing else "create",
                "name": name,
                "year": _norm(a.get("year")),
                "type": _norm(a.get("type")),
                "external_id": None,
                "cover_url": None,
                "track_count": None,
                "sources": ["AI·页面提取"],
                "extractor": "ai",
            }
        )
    meta = {"status": "ok", "extracted": len(out)}
    return out, meta


def _album_proposals(db: Session, group: Group) -> tuple:
    return _album_proposals_for(
        db,
        name=group.name,
        entity_type="group",
        entity_id=group.id,
        debut_year=group.debut_date.year if group.debut_date else None,
    )


def _album_proposals_for(
    db: Session,
    *,
    name: str,
    entity_type: str,
    entity_id: int,
    debut_year: Optional[int] = None,
) -> tuple:
    """用 iTunes / Deezer 搜索名称 → 专辑生长提案（组合与艺人共用）。

    只保留歌手名与名称匹配的候选；与库内同名专辑去重。
    已在库但提案有更完整发行日/封面时标记 update（可勾选写入）；
    纯重复标记 exists（仍可强制覆盖可写字段）。
    曲目表在合并时抓取（预览阶段不逐张拉取，避免几十次外部请求）。
    """
    from app.models.album import Album
    from app.services import album_external_service as aes

    query = (name or "").strip()
    if not query:
        return []
    candidates: List[dict] = []
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(
                lambda f: _safe_album_search(f, query),
                (aes.search_itunes_albums, aes.search_deezer_albums, aes.search_musicbrainz_release_groups),
            )
        )
    for rows in results:
        candidates.extend(rows)

    group_norm = _norm_name(name)
    existing_by_name = {
        _norm_name(a.name): a
        for a in db.scalars(
            select(Album).where(
                Album.deleted_at.is_(None),
                Album.release_artist_type == entity_type,
                Album.release_artist_id == entity_id,
            )
        ).all()
    }

    seen: Dict[str, dict] = {}
    out: List[dict] = []
    singles: List[dict] = []
    for c in candidates:
        name = _norm(c.get("name"))
        if not name:
            continue
        artist = _norm_name(c.get("artist"))
        # 严格相等：歌手名归一化后必须与组合名完全一致。
        # 子串匹配会把同名/近名歌手（如西班牙歌手 Izan、柏柏尔乐队 Izanzaren）都放进来。
        if artist != group_norm:
            continue
        low = name.casefold()
        if any(
            j in low
            for j in (
                "karaoke", "tribute", "instrumental", "cover",
                "made popular by", "originally performed", "lullaby",
            )
        ):
            continue
        # 类型识别（单曲专辑也是合法专辑，保留；仅用于展示徽章）。
        # 优先级：名称后缀 > 曲数 > 来源标记（iTunes 把数位单曲的 collectionType 也写成 album）
        rtype = (c.get("record_type") or "").lower() or None
        tc = c.get("track_count")
        if low.endswith("- single"):
            rtype = "single"
        elif low.endswith("- ep"):
            rtype = "ep"
        elif rtype == "album" and isinstance(tc, int) and tc <= 2:
            rtype = "single"
        elif not rtype and isinstance(tc, int):
            rtype = "single" if tc <= 2 else ("ep" if tc <= 6 else "album")
        key = _norm_name(name)
        alb = existing_by_name.get(key)
        if alb is not None:
            prop_rd = c.get("release_date")
            needs_date = _album_release_needs_update(alb.release_date, prop_rd)
            needs_cover = bool(c.get("cover_url") and not getattr(alb, "cover_path", None))
            action = "update" if (needs_date or needs_cover) else "exists"
            patch: Dict[str, Any] = {}
            clean_rd = _clean_date(prop_rd)
            if needs_date and clean_rd and len(clean_rd) == 10:
                patch["release_date"] = clean_rd
            if needs_cover:
                patch["cover_url"] = c.get("cover_url")
            db_rd = alb.release_date.isoformat() if alb.release_date else None
            row = {
                "action": action,
                "name": name,
                "record_type": rtype or ("ep" if isinstance(tc, int) and 3 <= tc <= 6 else None),
                "year": c.get("year") or _date_str_year(alb.release_date),
                "release_date": prop_rd or db_rd,
                "track_count": c.get("track_count"),
                "external_id": c.get("external_id"),
                "cover_url": c.get("cover_url"),
                "sources": [c.get("source") or ""],
                "album_id": alb.id,
                "db_release_date": db_rd,
                "patch": patch,
            }
            if key in seen:
                prev = seen[key]
                src = c.get("source") or ""
                if src and src not in prev["sources"]:
                    prev["sources"].append(src)
                # 合并更完整的发行日 / 封面；必要时升级为 update
                if clean_rd and len(clean_rd) == 10:
                    prev_rd = _clean_date(prev.get("release_date"))
                    if not prev_rd or len(prev_rd) != 10:
                        prev["release_date"] = prop_rd
                if c.get("cover_url") and not prev.get("cover_url"):
                    prev["cover_url"] = c.get("cover_url")
                if c.get("external_id") and not prev.get("external_id"):
                    prev["external_id"] = c.get("external_id")
                if c.get("track_count") and not prev.get("track_count"):
                    prev["track_count"] = c.get("track_count")
                if action == "update" and prev.get("action") != "update":
                    prev["action"] = "update"
                if needs_date and clean_rd and len(clean_rd) == 10:
                    prev.setdefault("patch", {})["release_date"] = clean_rd
                if needs_cover and c.get("cover_url"):
                    prev.setdefault("patch", {})["cover_url"] = c.get("cover_url")
                continue
            seen[key] = row
            out.append(row)
            continue
        if key in seen:
            seen[key]["sources"].append(c.get("source") or "")
            # 补全更完整的发行日
            if c.get("release_date") and not seen[key].get("release_date"):
                seen[key]["release_date"] = c.get("release_date")
            if c.get("cover_url") and not seen[key].get("cover_url"):
                seen[key]["cover_url"] = c.get("cover_url")
            if c.get("external_id") and not seen[key].get("external_id"):
                seen[key]["external_id"] = c.get("external_id")
            continue
        row = {
            "action": "create",
            "name": name,
            "record_type": rtype,
            "year": c.get("year"),
            "release_date": c.get("release_date"),
            "track_count": c.get("track_count"),
            "external_id": c.get("external_id"),
            "cover_url": c.get("cover_url"),
            "sources": [c.get("source") or ""],
        }
        seen[key] = row
        out.append(row)
    _action_rank = {"create": 0, "update": 1, "fill_tracks": 1, "exists": 2}

    # 早于出道日（-1 年容差）的候选不可能是该主体的发行物
    #（MusicBrainz 偶有同名其他艺人的条目，如 2012 年的 Chiasma）
    if debut_year:
        out = [
            r
            for r in out
            if not (r.get("year") and str(r.get("year")).isdigit() and int(r["year"]) < debut_year - 1)
        ]

    # 跨来源同名合并：去掉「 - Single / - EP」后缀后同名的归并为一条，来源并集
    import re as _re2

    def _base(nm: str) -> str:
        return _re2.sub(r"\s*-\s*(single|ep)\s*$", "", nm.strip(), flags=_re2.I).casefold()

    merged: Dict[str, dict] = {}
    merged_order: List[dict] = []
    for r in out:
        b = _base(r["name"])
        prev = merged.get(b)
        if prev is None:
            merged[b] = r
            merged_order.append(r)
            continue
        for src in r.get("sources") or []:
            if src not in prev["sources"]:
                prev["sources"].append(src)
        if r.get("track_count") and not prev.get("track_count"):
            prev["track_count"] = r["track_count"]
        if r.get("release_date") and not prev.get("release_date"):
            prev["release_date"] = r["release_date"]
        if r.get("record_type") and not prev.get("record_type"):
            prev["record_type"] = r["record_type"]
        if r.get("external_id") and not prev.get("external_id"):
            prev["external_id"] = r["external_id"]
        if r.get("action") == "update" and prev.get("action") != "update":
            prev["action"] = "update"
    out = merged_order

    out.sort(key=lambda r: (_action_rank.get(r["action"], 9), r.get("year") or "9999", r["name"]))
    note = None
    if not any(r.get("action") == "create" for r in out):
        note = (
            f"iTunes / Deezer / MusicBrainz 未找到歌手名与「{name}」完全一致的专辑。"
            "常见原因：名称拼写有误（如 izan ↔ izna）、主体太新未被收录，"
            "或专辑以其他艺名发行。请核对名称后重试。"
        )
    return out, [], note


def _safe_album_search(fetch, query: str) -> List[dict]:
    try:
        return fetch(query) or []
    except Exception:  # noqa: BLE001
        return []


def _fetch_tracklist_bundle(external_id: str) -> dict:
    """返回 {tracks, release_date, year}；失败抛异常由调用方处理。"""
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


def _fetch_tracklist(external_id: str) -> List[dict]:
    return _fetch_tracklist_bundle(external_id).get("tracks") or []


def _fetch_tracklists_parallel(items: List[dict]) -> tuple:
    """并行抓取：({external_id: tracks}, {external_id: release_date})，失败的键缺席。"""
    ids = [i for i in {_norm(a.get("external_id")) for a in items} if i]
    if not ids:
        return {}, {}
    out: Dict[str, List[dict]] = {}
    dates: Dict[str, str] = {}
    from concurrent.futures import ThreadPoolExecutor

    def _safe(eid: str):
        try:
            return eid, _fetch_tracklist_bundle(eid)
        except Exception:  # noqa: BLE001
            return eid, None

    with ThreadPoolExecutor(max_workers=min(6, len(ids))) as pool:
        for eid, bundle in pool.map(_safe, ids):
            if bundle is not None:
                out[eid] = bundle.get("tracks") or []
                rd = _norm(bundle.get("release_date"))
                if rd and len(rd) >= 10:
                    dates[eid] = rd[:10]
    return out, dates


def apply_growth(
    db: Session,
    uid: str,
    fields: Dict[str, str],
    members: List[dict],
    albums: List[dict],
    source_urls: List[str],
    subunits: Optional[List[dict]] = None,
    companies: Optional[List[dict]] = None,
    step: Optional[str] = None,
) -> dict:
    """合并用户勾选的提案：标量字段 + 成员生长 + 专辑/歌曲生长。

    - 🔒 锁定的标量字段跳过
    - 成员：link → 建成员关系；create → 建艺人 + 成员关系
    - 专辑：建专辑（发行主体=组合）+ 抓取曲目表 → 建 Song + AlbumTrack
    - 全部来源 URL 记入 external_links（按 url 去重）
    """
    from datetime import date as date_cls

    from app.models.album import Album, AlbumTrack
    from app.models.artist import Artist
    from app.models.entity_field_lock import EntityFieldLock
    from app.models.membership import GroupMembership
    from app.models.song import Song
    from app.services import album_external_service as aes

    group = db.scalar(select(Group).where(Group.uid == uid, Group.deleted_at.is_(None)))
    if group is None:
        raise SourceError("组合不存在")

    lock_row = db.scalar(
        select(EntityFieldLock).where(
            EntityFieldLock.entity_type == "groups",
            EntityFieldLock.entity_id == group.id,
        )
    )
    locked = (lock_row.locks or {}) if lock_row else {}

    applied = {
        "fields": [],
        "fields_skipped_locked": [],
        "members_created": 0,
        "members_linked": 0,
        "members_skipped": 0,
        "companies_created": 0,
        "companies_linked": 0,
        "companies_skipped": 0,
        "albums_created": 0,
        "albums_updated": 0,
        "albums_skipped": 0,
        "songs_created": 0,
        "tracks_created": 0,
        "tracklist_failed": 0,
        "subunits_created": 0,
        "subunit_members_created": 0,
        "artist_fields_filled": 0,
        "albums_year_only": 0,
        "note_year_only_albums": (
            "仅有年份的专辑不写 release_date（避免 YYYY-01-01）；UI 仍显示 year。"
        ),
    }

    step_key = _normalize_step(step)
    # 分步时只处理本步相关载荷；省略 step 则全量（向后兼容）
    do_fields = step_key in (None, "identity")
    do_members = step_key in (None, "members", "member_details")
    do_subunits = step_key in (None, "subunits")
    do_companies = step_key in (None, "identity", "company")
    do_albums = step_key in (None, "albums", "tracks")
    include_tracks = step_key in (None, "tracks")  # 步5只建碟；步6/全量才抓曲目
    if step_key == "albums":
        include_tracks = False
    if step_key == "company":
        fields, members, albums, subunits = {}, [], [], []
    elif step_key == "identity":
        members, albums, subunits = [], [], []
    elif step_key == "subunits":
        fields, members, albums = {}, [], []
    elif step_key == "members":
        fields, albums, subunits = {}, [], []
        # 成员轨迹步：跳过已在库的 enrich
        members = [m for m in (members or []) if m.get("action") in ("create", "link")]
    elif step_key == "member_details":
        fields, albums, subunits = {}, [], []
        # 详情步：对已有成员补档案（action=enrich / exists）
        members = [m for m in (members or []) if m.get("action") in ("enrich", "exists", "link")]
    elif step_key == "albums":
        fields, members, subunits = {}, [], []
        albums = [a for a in (albums or []) if a.get("action") != "fill_tracks"]
    elif step_key == "tracks":
        fields, members, subunits = {}, [], []
        # 曲目步：优先 fill_tracks；若仍传 create 则建碟+曲

    # ---- 标量字段（日期字段需转 date 对象）----
    date_fields = {"debut_date"}
    for field, value in ((fields or {}).items() if do_fields else []):
        if field not in APPLICABLE_FIELDS:
            continue
        if locked.get(field):
            applied["fields_skipped_locked"].append(field)
            continue
        if field in date_fields:
            clean = _clean_date(value)
            if not clean or len(clean) != 10:
                continue
            try:
                value = date_cls.fromisoformat(clean)
            except ValueError:
                continue
        setattr(group, field, value)
        applied["fields"].append(field)

    # ---- 公司关系 ----
    if do_companies:
        from app.models.company import Company, GroupCompanyRelation

        for c in companies or []:
            name = _norm(c.get("name"))
            if not name:
                continue
            action = c.get("action")
            if action == "exists":
                applied["companies_skipped"] += 1
                continue
            company = None
            if action == "link" and c.get("company_id"):
                company = db.get(Company, c.get("company_id"))
                if company is None or getattr(company, "deleted_at", None) is not None:
                    applied["companies_skipped"] += 1
                    continue
            if company is None:
                company = _match_company_by_name(db, name)
            created = False
            if company is None:
                company = Company(name=name)
                db.add(company)
                db.flush()
                created = True
            dup = db.scalar(
                select(GroupCompanyRelation).where(
                    GroupCompanyRelation.group_id == group.id,
                    GroupCompanyRelation.company_id == company.id,
                )
            )
            if dup:
                applied["companies_skipped"] += 1
                continue
            db.add(
                GroupCompanyRelation(
                    group_id=group.id,
                    company_id=company.id,
                    status="Active",
                    role=_norm(c.get("role")) or "经纪公司",
                )
            )
            if created:
                applied["companies_created"] += 1
            else:
                applied["companies_linked"] += 1

    # ---- 成员生长 ----
    for m in (members or [] if do_members else []):
        name = _norm(m.get("name"))
        if not name:
            continue
        action = m.get("action")
        join_d = _clean_date(m.get("join_date"))
        leave_d = _clean_date(m.get("leave_date"))
        status = m.get("status") or ("Former" if (leave_d or m.get("former")) else "Active")
        try:
            join_v = date_cls.fromisoformat(join_d) if join_d and len(join_d) == 10 else None
            leave_v = date_cls.fromisoformat(leave_d) if leave_d and len(leave_d) == 10 else None
        except ValueError:
            join_v = leave_v = None
        if join_v and leave_v and join_v > leave_v:
            applied["members_skipped"] += 1
            continue

        af = m.get("artist_fields") or {}
        birth = _clean_date(af.get("birth_date"))
        try:
            birth_v = (
                date_cls.fromisoformat(birth) if birth and len(birth) == 10 else None
            )
        except ValueError:
            birth_v = None
        artist = None
        if action == "link":
            artist = db.scalar(select(Artist).where(Artist.id == m.get("artist_id")))
            if artist is None or getattr(artist, "deleted_at", None) is not None:
                applied["members_skipped"] += 1
                continue
        elif action == "create":
            from app.services.name_match import match_artist_exact

            # 幂等保护：重复合并时可能已有同名艺人（上次合并创建的）
            artist = match_artist_exact(db, name, _norm(m.get("korean_name")))
            if artist is None:
                artist = Artist(
                    name=name,
                    korean_name=_norm(m.get("korean_name")),
                    chinese_name=_norm(af.get("chinese_name")),
                    birth_date=birth_v,
                    birth_place=_norm(af.get("birth_place")),
                    occupation=_norm(af.get("occupation")),
                    description=_norm(af.get("description")),
                    gender=group.gender_type,
                )
                db.add(artist)
                db.flush()
                if birth_v:
                    applied["artist_fields_filled"] += 1
                if _norm(af.get("description")):
                    applied["artist_fields_filled"] += 1
                applied["members_created"] += 1
        elif action in ("enrich", "exists"):
            artist = db.scalar(select(Artist).where(Artist.id == m.get("artist_id")))
            if artist is None:
                from app.services.name_match import match_artist_exact
                artist = match_artist_exact(db, name, _norm(m.get("korean_name")))
            if artist is None:
                applied["members_skipped"] += 1
                continue
            a_lock = db.scalar(
                select(EntityFieldLock).where(
                    EntityFieldLock.entity_type == "artists",
                    EntityFieldLock.entity_id == artist.id,
                )
            )
            a_locked = (a_lock.locks or {}) if a_lock else {}
            for fld, val in (
                ("birth_date", birth_v),
                ("description", _norm(af.get("description"))),
                ("korean_name", _norm(m.get("korean_name"))),
                ("chinese_name", _norm(af.get("chinese_name"))),
                ("english_name", _norm(af.get("english_name"))),
                ("stage_name", _norm(af.get("stage_name"))),
                ("birth_place", _norm(af.get("birth_place"))),
                ("occupation", _norm(af.get("occupation"))),
            ):
                if not val or a_locked.get(fld):
                    continue
                # 仅填空字段，不覆盖已有值
                if getattr(artist, fld, None):
                    continue
                setattr(artist, fld, val)
                applied["artist_fields_filled"] += 1
            # 担当：补空的 membership.positions（人审勾选后写入）
            positions = m.get("positions")
            if isinstance(positions, str):
                positions = [p.strip() for p in re.split(r"[、,/]+", positions) if p.strip()]
            if positions:
                ms_row = db.scalar(
                    select(GroupMembership).where(
                        GroupMembership.group_id == group.id,
                        GroupMembership.artist_id == artist.id,
                    )
                )
                if ms_row is not None and not (ms_row.positions or []):
                    ms_row.positions = positions[:8]
                    applied["artist_fields_filled"] += 1
            continue
        else:
            applied["members_skipped"] += 1
            continue

        dup = db.scalar(
            select(GroupMembership).where(
                GroupMembership.group_id == group.id,
                GroupMembership.artist_id == artist.id,
                GroupMembership.status == status,
            )
        )
        if dup:
            applied["members_skipped"] += 1
            continue
        positions = m.get("positions")
        if isinstance(positions, str):
            positions = [p.strip() for p in re.split(r"[、,/]+", positions) if p.strip()]
        db.add(
            GroupMembership(
                group_id=group.id,
                artist_id=artist.id,
                join_date=join_v,
                leave_date=leave_v,
                status=status,
                positions=positions or None,
            )
        )
        if action == "link":
            a_lock = db.scalar(
                select(EntityFieldLock).where(
                    EntityFieldLock.entity_type == "artists",
                    EntityFieldLock.entity_id == artist.id,
                )
            )
            a_locked = (a_lock.locks or {}) if a_lock else {}
            for fld, val in (
                ("birth_date", birth_v),
                ("description", _norm(af.get("description"))),
                ("korean_name", _norm(m.get("korean_name"))),
                ("chinese_name", _norm(af.get("chinese_name"))),
                ("english_name", _norm(af.get("english_name"))),
                ("stage_name", _norm(af.get("stage_name"))),
                ("birth_place", _norm(af.get("birth_place"))),
                ("occupation", _norm(af.get("occupation"))),
            ):
                if not val or getattr(artist, fld, None) or a_locked.get(fld):
                    continue
                setattr(artist, fld, val)
                applied["artist_fields_filled"] += 1
            applied["members_linked"] += 1

    # ---- 小分队生长 ----
    for su in (subunits or [] if do_subunits else []):
        su_name = _norm(su.get("name"))
        if not su_name or su.get("action") == "exists":
            continue
        dup = next(
            (
                x
                for x in db.scalars(
                    select(Group).where(
                        Group.deleted_at.is_(None),
                        Group.parent_group_id == group.id,
                    )
                )
                if _norm_name(x.name) == _norm_name(su_name)
            ),
            None,
        )
        if dup:
            applied["subunits_created"] += 0
            continue
        sub = Group(
            name=su_name,
            parent_group_id=group.id,
            group_type="Sub-unit",
            gender_type=group.gender_type,
        )
        db.add(sub)
        db.flush()
        applied["subunits_created"] += 1
        # 铺小分队成员：优先挂接已有艺人（含本组合刚生长出的成员）
        from app.services.name_match import match_artist_exact

        member_items = su.get("member_entries") or [
            {"name": mn} for mn in (su.get("member_names") or [])
        ]
        for mi in member_items:
            mnn = _norm(mi.get("name"))
            if not mnn:
                continue
            sub_artist = match_artist_exact(db, mnn, _norm(mi.get("korean_name")))
            created = False
            if sub_artist is None:
                sub_artist = Artist(name=mnn, korean_name=_norm(mi.get("korean_name")))
                db.add(sub_artist)
                db.flush()
                created = True
                applied["members_created"] += 1
            m_dup = db.scalar(
                select(GroupMembership).where(
                    GroupMembership.group_id == sub.id,
                    GroupMembership.artist_id == sub_artist.id,
                    GroupMembership.status == "Active",
                )
            )
            if m_dup is None:
                db.add(
                    GroupMembership(
                        group_id=sub.id,
                        artist_id=sub_artist.id,
                        status="Active",
                    )
                )
                if created:
                    applied["subunit_members_created"] += 1

    # ---- 专辑 / 歌曲生长 ----
    album_items = albums or [] if do_albums else []
    # fill_tracks：给已有专辑补曲目
    fill_items = [a for a in album_items if a.get("action") == "fill_tracks" and a.get("album_id")]
    # update / exists：刷新已有专辑可写元数据（不删曲目、不重建碟）
    update_items = [
        a
        for a in album_items
        if a.get("action") in ("update", "exists")
        and (a.get("album_id") or _norm(a.get("name")))
    ]
    create_items = [
        a for a in album_items if a.get("action") not in ("exists", "fill_tracks", "update")
    ]
    if include_tracks or fill_items:
        tracklist_map, tracklist_dates = _fetch_tracklists_parallel(
            [a for a in (fill_items + create_items) if include_tracks or a.get("action") == "fill_tracks"]
        )
    else:
        tracklist_map, tracklist_dates = {}, {}

    def _owned_album(album_id) -> Optional[Album]:
        album = db.get(Album, album_id)
        if album is None or getattr(album, "deleted_at", None) is not None:
            return None
        if album.release_artist_type != "group" or album.release_artist_id != group.id:
            return None
        return album

    for a in fill_items:
        album = _owned_album(a.get("album_id"))
        if album is None:
            applied["albums_skipped"] += 1
            continue
        external_id = _norm(a.get("external_id"))
        if not external_id:
            applied["albums_skipped"] += 1
            continue
        tracks = tracklist_map.get(external_id, []) if external_id else []
        if external_id and external_id not in tracklist_map:
            applied["tracklist_failed"] += 1
            continue
        # 补全已有专辑缺失的完整发行日（避免历史 year→01-01；此处只填空）
        if album.release_date is None and external_id:
            rd = _clean_date(a.get("release_date")) or _clean_date(tracklist_dates.get(external_id))
            if rd and len(rd) == 10:
                try:
                    album.release_date = date_cls.fromisoformat(rd)
                except ValueError:
                    pass
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
            song = next(
                (
                    x
                    for x in db.scalars(
                        select(Song).where(
                            Song.deleted_at.is_(None),
                            Song.release_artist_type == "group",
                            Song.release_artist_id == group.id,
                        )
                    )
                    if _norm_name(x.name) == _norm_name(tname)
                ),
                None,
            )
            if song is None:
                song = Song(
                    name=tname,
                    release_artist_type="group",
                    release_artist_id=group.id,
                )
                db.add(song)
                db.flush()
                applied["songs_created"] += 1
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
            applied["tracks_created"] += 1

    for a in update_items:
        album = None
        aid = a.get("album_id")
        if aid is not None:
            album = _owned_album(aid)
        if album is None:
            aname = _norm(a.get("name"))
            if aname:
                album = next(
                    (
                        x
                        for x in db.scalars(
                            select(Album).where(
                                Album.deleted_at.is_(None),
                                Album.release_artist_type == "group",
                                Album.release_artist_id == group.id,
                            )
                        )
                        if _norm_name(x.name) == _norm_name(aname)
                    ),
                    None,
                )
        if album is None or getattr(album, "deleted_at", None) is not None:
            applied["albums_skipped"] += 1
            continue

        alb_lock_row = db.scalar(
            select(EntityFieldLock).where(
                EntityFieldLock.entity_type == "albums",
                EntityFieldLock.entity_id == album.id,
            )
        )
        alb_locked = (alb_lock_row.locks or {}) if alb_lock_row else {}
        changed = False

        if not alb_locked.get("release_date"):
            rd = _clean_date(a.get("release_date"))
            if rd and len(rd) == 10:
                try:
                    new_d = date_cls.fromisoformat(rd)
                except ValueError:
                    new_d = None
                if new_d is not None and album.release_date is None:
                    album.release_date = new_d
                    changed = True

        # 封面：仅补空（尊重 cover_path 锁）；已有完整发行日/封面不覆盖
        cover_url = _norm(a.get("cover_url"))
        if cover_url and not alb_locked.get("cover_path"):
            if not album.cover_path:
                try:
                    from app.services import cover_service

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

    for a in create_items:
        name = _norm(a.get("name"))
        if not name:
            continue
        dup = next(
            (
                x
                for x in db.scalars(
                    select(Album).where(
                        Album.deleted_at.is_(None),
                        Album.release_artist_type == "group",
                        Album.release_artist_id == group.id,
                    )
                )
                if _norm_name(x.name) == _norm_name(name)
            ),
            None,
        )
        if dup:
            applied["albums_skipped"] += 1
            continue
        year = _norm(a.get("year"))
        release_raw = _clean_date(a.get("release_date"))
        external_id_pre = _norm(a.get("external_id"))
        # 曲目抓取时常能拿到完整发行日（尤其 Deezer album detail / iTunes lookup）
        if (not release_raw or len(release_raw) != 10) and external_id_pre:
            release_raw = _clean_date(tracklist_dates.get(external_id_pre))
        release_v = None
        if release_raw and len(release_raw) == 10:
            try:
                release_v = date_cls.fromisoformat(release_raw)
            except ValueError:
                release_v = None
        elif year and len(year) == 4:
            # 仅有年份：不写 YYYY-01-01，保留提案 year 供 UI 展示
            applied["albums_year_only"] += 1
        album = Album(
            name=name,
            release_date=release_v,
            release_artist_type="group",
            release_artist_id=group.id,
        )
        db.add(album)
        db.flush()
        applied["albums_created"] += 1

        external_id = _norm(a.get("external_id"))
        tracks: List[dict] = []
        if include_tracks and external_id:
            if external_id in tracklist_map:
                tracks = tracklist_map[external_id]
            else:
                applied["tracklist_failed"] += 1
        # 曲目号归一：缺失/重复时按顺序补位，避免撞 (album, disc, track) 唯一约束
        used_positions: set = set()
        normalized_tracks: List[dict] = []
        for idx, t in enumerate(tracks, start=1):
            tname = _norm(t.get("name"))
            if not tname:
                continue
            disc = _to_int(t.get("disc_number")) or 1
            tn = _to_int(t.get("track_number")) or idx
            while (disc, tn) in used_positions:
                tn += 1
            used_positions.add((disc, tn))
            normalized_tracks.append({**t, "disc_number": disc, "track_number": tn})
        for t in normalized_tracks:
            tname = _norm(t.get("name"))
            song = next(
                (
                    x
                    for x in db.scalars(
                        select(Song).where(
                            Song.deleted_at.is_(None),
                            Song.release_artist_type == "group",
                            Song.release_artist_id == group.id,
                        )
                    )
                    if _norm_name(x.name) == _norm_name(tname)
                ),
                None,
            )
            if song is None:
                song = Song(
                    name=tname,
                    release_artist_type="group",
                    release_artist_id=group.id,
                )
                db.add(song)
                db.flush()
                applied["songs_created"] += 1
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
                    disc_number=t["disc_number"],
                    track_number=t["track_number"],
                )
            )
            applied["tracks_created"] += 1

    # ---- 来源记录 ----
    links = list(group.external_links or [])
    for url in source_urls or []:
        if not _norm(url):
            continue
        if any(isinstance(l, dict) and l.get("url") == url for l in links):
            continue
        try:
            kind, _ = _detect(url)
        except SourceError:
            kind = "other"
        source_label = {
            "fandom": "K-pop Fandom",
            "wikidata": "Wikidata",
            "wikipedia": "Wikipedia",
            "baidu": "百度百科",
        }.get(kind, kind)
        links.append(
            {
                "url": url,
                "source": source_label,
                "synced_at": datetime.utcnow().isoformat(),
            }
        )
    group.external_links = links

    db.commit()
    return applied


# ===== 重建的函数（见对话记录）=====

def _norm_person(s: Any) -> str:
    """人名归一：拆驼峰（SeoDaHyun → seo da hyun）、去音调/标点、词序排序。"""
    import unicodedata

    s = _norm(s)
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)  # 驼峰拆分
    s = re.sub(r"[^0-9A-Za-z가-힣 ]+", " ", s)
    tokens = sorted(t.casefold() for t in s.split() if t)
    return " ".join(tokens)


def _similar(a: str, b: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _to_int(v: Any) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _fandom_page_url(title: str) -> str:
    return f"https://kpop.fandom.com/wiki/{(title or '').strip().replace(' ', '_')}"


def _person_tokens(s: str) -> list:
    """人名 token 化：拆驼峰、去音调/标点、小写，保留原始音节顺序。"""
    import unicodedata

    s = _norm(s)
    if not s:
        return []
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    s = re.sub(r"[^0-9A-Za-z가-힣 ]+", " ", s)
    return [t.casefold() for t in s.split() if t]


def _name_variants(s: str) -> set:
    """人名连写变体：原始音节顺序 + 字母序排序（覆盖姓名换序/驼峰/连写差异）。"""
    tokens = _person_tokens(s)
    if not tokens:
        return set()
    return {"".join(tokens), "".join(sorted(tokens))}


def _member_fact(facts: Dict[str, dict], order: List[str], m: dict) -> Optional[dict]:
    """把一条来源成员记录归并进 facts（见 _member_proposals 的同一人判定）。"""
    name = _norm(m.get("name"))
    if not name:
        return None
    nk = _norm_person(name)
    ko = _norm(m.get("korean_name"))
    target: Optional[dict] = None
    nv = _name_variants(name)
    for key in order:
        f = facts[key]
        if nk and f["_nv"] & nv:
            target = f
            break
        if ko and f["korean_name"] and _norm(f["korean_name"]) == ko:
            target = f
            break
    if target is None and nk:
        nk_tokens = set(nk.split())
        for key in order:
            f = facts[key]
            fk = f["_nk"]
            if not fk:
                continue
            fk_tokens = set(fk.split())
            shared = nk_tokens & fk_tokens
            # 艺名 ⊂ 本名（ChaeYeon ⊂ Kim Chae-yeon），至少 2 个 token 重合
            if len(shared) >= 2 and (nk_tokens <= fk_tokens or fk_tokens <= nk_tokens):
                target = f
                break
        if target is None:
            # 拉丁连写包含合并：Bang Jee Min ↔ Jeemin（去空格后短名被长名包含，短名 ≥5 字符）
            joined = nk.replace(" ", "")
            if len(joined) >= 5:
                for key in order:
                    f = facts[key]
                    for fk_joined in f["_nv"]:
                        if not fk_joined:
                            continue
                        short, long_ = sorted((fk_joined, joined), key=len)
                        if len(short) >= 5 and short in long_:
                            target = f
                            break
                    if target is not None:
                        break
        if target is None and " " in nk:
            # 模糊合并从严：相似度 ≥0.84 且「姓氏 token」一致（Kim YuYeon ↔ Kim YooYeon）
            for key in order:
                f = facts[key]
                fk = f["_nk"]
                if not fk or " " not in fk:
                    continue
                if fk.split()[0] == nk.split()[0] and _similar(nk, fk) >= 0.84:
                    target = f
                    break
    if target is None:
        target = {
            "name": name,
            "_nk": nk,
            "_nv": _name_variants(name),
            "korean_name": None,
            "chinese_name": None,
            "birth_date": None,
            "birth_place": None,
            "occupation": None,
            "positions": None,
            "description": None,
            "join_date": None,
            "leave_date": None,
            "former": False,
            "sources": set(),
        }
        facts[id(target)] = target
        order.append(id(target))
    if ko and not target["korean_name"]:
        target["korean_name"] = ko
    cn = _norm(m.get("chinese_name"))
    if cn and not target.get("chinese_name"):
        target["chinese_name"] = cn
    if m.get("birth_date") and not target.get("birth_date"):
        target["birth_date"] = m["birth_date"]
    for fld in ("chinese_name", "birth_place", "occupation", "description"):
        v = _norm(m.get(fld))
        if v and not target.get(fld):
            target[fld] = v
    if m.get("positions") and not target.get("positions"):
        target["positions"] = m["positions"]
    if m.get("_source"):
        target["sources"].add(m["_source"])
    return target


def _pages_from_extras(extras: List[dict]) -> Dict[str, str]:
    """从已抓取来源里收集 成员名 → Fandom page 标题（优先有 page 字段的）。"""
    out: Dict[str, str] = {}
    for ex in extras or []:
        for m in list(ex.get("members") or []) + list(ex.get("former_members") or []):
            if not isinstance(m, dict):
                continue
            name = _norm(m.get("name"))
            page = _norm(m.get("page")) or _norm(m.get("_fandom_page"))
            if name and page:
                key = _norm_name(name)
                if key and key not in out:
                    out[key] = page
    return out


def _artist_empty_fields(artist) -> dict:
    """返回艺人当前仍为空、可供补全的字段快照。"""
    def _empty(v):
        if v is None:
            return True
        if isinstance(v, str) and not v.strip():
            return True
        return False

    return {
        "birth_date": _empty(getattr(artist, "birth_date", None)),
        "birth_place": _empty(getattr(artist, "birth_place", None)),
        "chinese_name": _empty(getattr(artist, "chinese_name", None)),
        "english_name": _empty(getattr(artist, "english_name", None)),
        "stage_name": _empty(getattr(artist, "stage_name", None)),
        "occupation": _empty(getattr(artist, "occupation", None)),
        "description": _empty(getattr(artist, "description", None)),
    }


# 补全员 A：AI 可提案的字段白名单（v1 刻意排除 birth_date / join_date / leave_date）
_AI_GAP_FIELD_WHITELIST = (
    "chinese_name",
    "english_name",
    "stage_name",
    "birth_place",
    "occupation",
    "description",
)
_AI_GAP_CHUNK = 6


def _origin_from_sources(sources: List[str]) -> str:
    joined = " ".join(sources or []).lower()
    if "fandom" in joined:
        return "fandom"
    if "wikidata" in joined:
        return "wikidata"
    if "wikipedia" in joined or "维基" in joined:
        return "wikipedia"
    if "baidu" in joined or "百度" in joined:
        return "baidu"
    return "crawl"


def _seed_field_origins(proposal: dict, default_origin: Optional[str] = None) -> None:
    """为已有提案值补齐 field_origins（不覆盖已标注）。"""
    origins = dict(proposal.get("field_origins") or {})
    origin = default_origin or _origin_from_sources(proposal.get("sources") or [])
    af = proposal.get("artist_fields") or {}
    for k, v in af.items():
        if v and k not in origins:
            origins[k] = origin
    positions = proposal.get("positions")
    if positions and "positions" not in origins:
        origins["positions"] = origin
    proposal["field_origins"] = origins


def _proposal_has_fillable_fields(proposal: dict) -> bool:
    af = proposal.get("artist_fields") or {}
    keys = (
        "birth_date",
        "chinese_name",
        "english_name",
        "stage_name",
        "birth_place",
        "occupation",
        "description",
    )
    if any(af.get(k) for k in keys):
        return True
    positions = proposal.get("positions")
    return bool(positions)


def _ingest_ai_cfg(db: Session) -> tuple:
    """读取入库 AI 配置。

    返回 (cfg|None, skip_reason|None)。未启用/未配齐时 cfg=None 并给出可读原因。
    """
    try:
        from app.services.app_settings import read_all as read_app_settings
    except Exception as e:  # noqa: BLE001
        return None, f"无法读取应用设置：{e}"
    try:
        settings = read_app_settings(db).ingest_ai
    except Exception as e:  # noqa: BLE001
        return None, f"无法读取入库 AI 设置：{e}"
    if not getattr(settings, "enabled", False):
        return None, "入库 AI 未启用（设置 → AI / 入库补全）"
    cfg = {
        "provider": getattr(settings, "provider", None) or "openai-compatible",
        "base_url": (getattr(settings, "base_url", None) or "").strip(),
        "api_key": getattr(settings, "api_key", None) or "",
        "model": (getattr(settings, "model", None) or "").strip(),
    }
    if not cfg["base_url"] or not cfg["model"]:
        return None, "入库 AI 已启用但未配置 base_url 或 model"
    return cfg, None


def _ensure_detail_stubs_for_ai(
    db: Session,
    group: Group,
    proposals: List[dict],
) -> List[dict]:
    """为仍有空缺的在籍成员补 enrich 占位，供 AI 填空（无百科命中时也能提案）。"""
    from app.models.artist import Artist
    from app.models.membership import GroupMembership

    by_id = {p.get("artist_id"): p for p in proposals if p.get("artist_id") is not None}
    out = list(proposals)
    memberships = list(
        db.scalars(select(GroupMembership).where(GroupMembership.group_id == group.id))
    )
    for ms in memberships:
        if ms.artist_id in by_id:
            continue
        artist = db.scalar(select(Artist).where(Artist.id == ms.artist_id))
        if artist is None or getattr(artist, "deleted_at", None) is not None:
            continue
        empties = _artist_empty_fields(artist)
        pos_empty = not (ms.positions or [])
        # AI 白名单空缺（不含生日）；生日仅来自百科
        ai_gap = any(empties.get(k) for k in _AI_GAP_FIELD_WHITELIST) or pos_empty
        if not ai_gap and not empties.get("birth_date"):
            continue
        if not ai_gap:
            # 仅生日空且无 AI 可填字段：不建 stub（等百科）
            continue
        name = _norm(artist.name) or _norm(getattr(artist, "stage_name", None))
        if not name:
            continue
        stub = {
            "action": "enrich",
            "name": name,
            "korean_name": _norm(artist.korean_name),
            "join_date": ms.join_date.isoformat() if ms.join_date else None,
            "leave_date": ms.leave_date.isoformat() if ms.leave_date else None,
            "status": ms.status or "Active",
            "sources": [],
            "artist_id": artist.id,
            "artist_uid": getattr(artist, "uid", None),
            "positions": None,
            "artist_fields": {
                "birth_date": None,
                "chinese_name": None,
                "english_name": None,
                "stage_name": None,
                "birth_place": None,
                "occupation": None,
                "description": None,
            },
            "field_origins": {},
        }
        out.append(stub)
        by_id[artist.id] = stub
    out.sort(key=lambda p: (p.get("status") != "Active", p.get("name") or ""))
    return out


def _ai_gap_fill_members(
    db: Session,
    group: Group,
    proposals: List[dict],
    cfg: Optional[dict] = None,
    source_context: Optional[str] = None,
) -> Tuple[List[dict], dict]:
    """补全员 A：对提案中仍空的白名单字段调用入库 AI 提案填空。

    - 不发明 birth_date / join_date / leave_date
    - AI 字段写入 artist_fields，并在 field_origins 标 ai
    - 失败时保留百科提案，返回 meta 供前端提示
    """
    import json
    import logging

    from app.models.artist import Artist
    from app.services.ai_service import chat_completion, parse_json

    logger = logging.getLogger("app.source_sync")
    meta = {"status": "skipped", "filled_fields": 0, "members_touched": 0, "error": None}
    if cfg is None:
        cfg, skip_reason = _ingest_ai_cfg(db)
        if skip_reason:
            meta["error"] = skip_reason
    if not cfg:
        if not meta.get("error"):
            meta["error"] = "入库 AI 未配置"
        return proposals, meta

    # 准备待补全条目
    jobs: List[dict] = []
    for p in proposals:
        af = dict(p.get("artist_fields") or {})
        # 保证键存在
        for k in _AI_GAP_FIELD_WHITELIST:
            af.setdefault(k, None)
        af.setdefault("birth_date", af.get("birth_date"))
        p["artist_fields"] = af
        _seed_field_origins(p)

        artist = None
        if p.get("artist_id") is not None:
            artist = db.scalar(select(Artist).where(Artist.id == p["artist_id"]))
        empties = _artist_empty_fields(artist) if artist is not None else {
            k: not af.get(k) for k in (
                "birth_date", "birth_place", "chinese_name", "english_name",
                "stage_name", "occupation", "description",
            )
        }
        missing = [k for k in _AI_GAP_FIELD_WHITELIST if empties.get(k) and not af.get(k)]
        pos_missing = not (p.get("positions") or [])
        # 若库内已有 positions，不要用 AI 覆盖
        if artist is not None:
            # positions 在 membership，提案里已带；空才算 missing
            pass
        if not missing and not pos_missing:
            continue
        known = {k: af.get(k) for k in (
            "birth_date", "chinese_name", "english_name", "stage_name",
            "birth_place", "occupation", "description",
        ) if af.get(k)}
        if p.get("positions"):
            known["positions"] = p["positions"]
        if artist is not None:
            for k in _AI_GAP_FIELD_WHITELIST:
                v = getattr(artist, k, None)
                if v and k not in known:
                    known[k] = v.isoformat() if hasattr(v, "isoformat") else v
        snippet = _norm(af.get("description")) or ""
        jobs.append({
            "proposal": p,
            "name": p.get("name"),
            "korean_name": p.get("korean_name"),
            "missing": missing + (["positions"] if pos_missing else []),
            "known": known,
            "snippet": snippet[:400],
        })

    if not jobs:
        meta["status"] = "ok"
        return proposals, meta

    group_name = _norm(group.name) or _norm(getattr(group, "english_name", None)) or ""
    system = (
        "你是 K-pop 艺人档案补全助手（补全员 A）。"
        "只根据已知字段、来源摘录与短摘要，对「缺失字段」给出高把握的补全；不确定则返回 null。"
        "严禁编造生日、出道日、加入/退出日期或任何精确日期。"
        "若提供了「来源摘录」：优先采信摘录内容，摘录与你的记忆冲突时以摘录为准；"
        "摘录中没有的信息才可使用模型知识，且 basis 须以「模型知识，建议核实」开头。"
        "中文名优先沿用摘录/已知字段中出现的官方译名。"
        "description 用简短中文（≤120 字）；positions 为短字符串数组（如 Main Vocal）。"
        "只返回 JSON 对象。"
    )

    filled_fields = 0
    members_touched = 0
    errors: List[str] = []

    for i in range(0, len(jobs), _AI_GAP_CHUNK):
        chunk = jobs[i : i + _AI_GAP_CHUNK]
        payload_members = [
            {
                "name": j["name"],
                "korean_name": j.get("korean_name"),
                "missing_fields": j["missing"],
                "known_fields": j["known"],
                "page_snippet": j["snippet"] or None,
            }
            for j in chunk
        ]
        context_block = ""
        if source_context:
            context_block = (
                "来源摘录（来自已抓取的百科/维基页面，优先采信）：\n"
                f"{source_context[:6000]}\n\n"
            )
        user = (
            f"组合：{group_name}\n"
            f"{context_block}"
            "请仅为下列成员的 missing_fields 补全，已知字段不要改动。"
            "禁止输出 birth_date / join_date / leave_date。\n"
            f"{json.dumps({'members': payload_members}, ensure_ascii=False)}\n\n"
            "返回 JSON：\n"
            '{"members":[{"name":"与输入同名","fields":{"chinese_name":null,'
            '"english_name":null,"stage_name":null,"birth_place":null,'
            '"occupation":null,"description":null,"positions":null},"basis":"一句依据"}]}'
        )
        try:
            raw = chat_completion(
                cfg,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                timeout=120,
            )
            data = parse_json(raw)
        except Exception as e:  # noqa: BLE001
            logger.warning("AI gap-fill chunk failed: %s", e)
            errors.append(str(e)[:200])
            continue

        by_name = {}
        for item in data.get("members") or []:
            if isinstance(item, dict) and item.get("name"):
                by_name[_norm_name(item["name"])] = item

        for j in chunk:
            item = by_name.get(_norm_name(j["name"]))
            if not item:
                continue
            fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
            # 兼容扁平写法
            if not fields:
                fields = {k: item.get(k) for k in list(_AI_GAP_FIELD_WHITELIST) + ["positions"]}
            p = j["proposal"]
            af = p.setdefault("artist_fields", {})
            origins = dict(p.get("field_origins") or {})
            touched = False
            for key in _AI_GAP_FIELD_WHITELIST:
                if key not in j["missing"]:
                    continue
                if af.get(key):
                    continue
                val = fields.get(key)
                if val is None:
                    continue
                if isinstance(val, str):
                    val = val.strip()
                if not val:
                    continue
                # 拒绝日期形态误填进非日期字段以外的；description 截断
                if key == "description" and isinstance(val, str):
                    val = val[:200]
                af[key] = val
                origins[key] = "ai"
                filled_fields += 1
                touched = True
            if "positions" in j["missing"] and not p.get("positions"):
                pos = fields.get("positions")
                cleaned: List[str] = []
                if isinstance(pos, list):
                    cleaned = [_norm(x) for x in pos if _norm(x)]
                elif isinstance(pos, str) and pos.strip():
                    cleaned = [q.strip() for q in re.split(r"[、,/]+", pos) if q.strip()]
                if cleaned:
                    p["positions"] = cleaned[:6]
                    origins["positions"] = "ai"
                    filled_fields += 1
                    touched = True
            if touched:
                basis = _norm(item.get("basis") or item.get("ai_basis"))
                if basis:
                    p["ai_basis"] = basis[:240]
                # 标记来源含 AI（不重复）
                srcs = list(p.get("sources") or [])
                if "AI 补全" not in srcs:
                    srcs.append("AI 补全")
                p["sources"] = srcs
                members_touched += 1
            p["field_origins"] = origins
            p["artist_fields"] = af

    meta["filled_fields"] = filled_fields
    meta["members_touched"] = members_touched
    if errors and not filled_fields:
        meta["status"] = "error"
        meta["error"] = errors[0]
    else:
        meta["status"] = "ok"
        if errors:
            meta["error"] = errors[0]
    return proposals, meta


def _member_details_enrich_proposals(
    db: Session,
    group: Group,
    extras: List[dict],
) -> tuple:
    """步4：对库内在籍成员按人搜索/抓取 Fandom 页，产出 enrich 提案。

    优先使用 extras 里已有的 fandom member page 标题；否则按姓名+组合搜索。
    只提案艺人库内仍为空的字段。返回 (proposals, discovered_sources, soft_errors)。
    """
    from app.models.membership import GroupMembership
    from app.models.artist import Artist

    memberships = list(
        db.scalars(
            select(GroupMembership).where(GroupMembership.group_id == group.id)
        )
    )
    if not memberships:
        return [], [], []

    page_map = _pages_from_extras(extras)
    entries: List[dict] = []

    for ms in memberships:
        artist = db.scalar(select(Artist).where(Artist.id == ms.artist_id))
        if artist is None or getattr(artist, "deleted_at", None) is not None:
            continue
        empties = _artist_empty_fields(artist)
        if not any(empties.values()):
            continue  # 档案已满，跳过搜索
        name = _norm(artist.name) or _norm(getattr(artist, "stage_name", None))
        if not name:
            continue
        page = page_map.get(_norm_name(name))
        entry = {
            "name": name,
            "korean_name": _norm(artist.korean_name),
            "page": page or "",
            "artist_id": artist.id,
            "artist_uid": getattr(artist, "uid", None),
            "join_date": ms.join_date.isoformat() if ms.join_date else None,
            "leave_date": ms.leave_date.isoformat() if ms.leave_date else None,
            "status": ms.status or "Active",
            "positions": ms.positions,
            "_empties": empties,
        }
        entries.append(entry)

    if not entries:
        return [], [], []

    import time

    soft_errors: List[str] = []
    # 给后续 AI 留出时间：百科搜索/抓取最多约 150s
    deadline = time.monotonic() + 150
    _enrich_members_fandom(
        entries,
        group_name=_norm(group.name) or _norm(group.english_name),
        allow_search=True,
        deadline=deadline,
        errors_out=soft_errors,
    )

    proposals: List[dict] = []
    sources_by_url: Dict[str, dict] = {}
    for e in entries:
        empties = e.get("_empties") or {}
        birth = _clean_date(e.get("birth_date")) if empties.get("birth_date") else None
        # 仅完整 YYYY-MM-DD 才提案生日；年份不编造 01-01、也不写入 date 列
        if birth and len(birth) != 10:
            birth = None
        af = {
            "birth_date": birth,
            "chinese_name": _norm(e.get("chinese_name")) if empties.get("chinese_name") else None,
            "english_name": _norm(e.get("english_name")) if empties.get("english_name") else None,
            "stage_name": _norm(e.get("stage_name")) if empties.get("stage_name") else None,
            "birth_place": _norm(e.get("birth_place")) if empties.get("birth_place") else None,
            "occupation": _norm(e.get("occupation")) if empties.get("occupation") else None,
            "description": _norm(e.get("description")) if empties.get("description") else None,
        }
        if not any(af.values()) and not e.get("positions"):
            continue
        src_url = _norm(e.get("_fandom_url"))
        src_label = f"K-pop Fandom · {e.get('_fandom_page') or e.get('page') or e.get('name')}"
        sources = ["K-pop Fandom"]
        if src_url:
            sources_by_url[src_url] = {
                "source_type": "fandom",
                "label": src_label,
                "url": src_url,
            }
        item = {
            "action": "enrich",
            "name": e["name"],
            "korean_name": e.get("korean_name"),
            "join_date": e.get("join_date"),
            "leave_date": e.get("leave_date"),
            "status": e.get("status") or "Active",
            "sources": sources,
            "artist_id": e.get("artist_id"),
            "artist_uid": e.get("artist_uid"),
            "positions": e.get("positions"),
            "artist_fields": af,
            "field_origins": {},
        }
        _seed_field_origins(item, "fandom")
        proposals.append(item)

    proposals.sort(key=lambda p: (p["status"] != "Active", p["name"]))
    return proposals, list(sources_by_url.values()), soft_errors


def _member_proposals(db: Session, group: Group, extras: List[dict]) -> List[dict]:
    """把多来源的成员信息合并成生长提案。

    action: link（库内已有艺人，只建关系）/ create（建艺人 + 关系）/
    exists（同状态关系已存在，展示为已完成）。
    """
    from app.models.membership import GroupMembership
    from app.services.name_match import match_artist_exact

    facts: Dict[str, dict] = {}
    order: List[str] = []
    for ex in extras:
        for m in ex.get("members") or []:
            f = _member_fact(facts, order, m)
            if f is not None:
                f["join_date"] = f["join_date"] or _clean_date(m.get("join_date"))
                f["leave_date"] = f["leave_date"] or _clean_date(m.get("leave_date"))
        for m in ex.get("former_members") or []:
            f = _member_fact(facts, order, m)
            if f is not None:
                f["former"] = True
                f["leave_date"] = f["leave_date"] or _clean_date(m.get("leave_date"))

    out: List[dict] = []
    for key in order:
        f = facts[key]
        status = "Former" if (f["leave_date"] or f["former"]) else "Active"
        artist = match_artist_exact(db, f["name"], f["korean_name"])
        base = {
            "name": f["name"],
            "korean_name": f["korean_name"],
            "join_date": f["join_date"],
            "leave_date": f["leave_date"],
            "status": status,
            "sources": sorted(f["sources"]),
            "positions": f.get("positions"),
            "artist_fields": {
                "birth_date": f.get("birth_date"),
                "chinese_name": f.get("chinese_name"),
                "birth_place": f.get("birth_place"),
                "occupation": f.get("occupation"),
                "description": f.get("description"),
            },
        }
        if artist is None:
            # 与其他提案共享 token 但未合并 → 标疑似重复，前端默认不勾选
            suspect = sorted(
                {
                    other["name"]
                    for other_key in order
                    if (other := facts[other_key]) is not f
                    if _similar(f["_nk"], other["_nk"]) >= 0.5
                    and (set(f["_nk"].split()) & set(other["_nk"].split()))
                }
            )
            item = {"action": "create", **base}
            if suspect:
                item["possible_duplicate_of"] = suspect
            out.append(item)
            continue
        exists = db.scalar(
            select(GroupMembership).where(
                GroupMembership.group_id == group.id,
                GroupMembership.artist_id == artist.id,
                GroupMembership.status == status,
            )
        )
        if exists:
            out.append(
                {
                    "action": "exists",
                    "artist_id": artist.id,
                    "artist_uid": artist.uid,
                    **base,
                }
            )
        else:
            out.append(
                {
                    "action": "link",
                    "artist_id": artist.id,
                    "artist_uid": artist.uid,
                    **base,
                }
            )
    out.sort(key=lambda p: (p["action"] == "exists", p["status"] != "Active", p["name"]))
    return out


def preview_source(db: Session, uid: str, url: str) -> dict:
    """抓取单个来源并生成字段级 diff。只读，不写库。"""
    return preview_sources(db, uid, [url])


def preview_sources(
    db: Session,
    uid: str,
    urls: List[str],
    step: Optional[str] = None,
    targets: Optional[List[str]] = None,
) -> dict:
    """抓取多个来源，按字段合并成一张 diff 表。只读，不写库。

    多来源对同一字段的提案：
    - 完全一致 → 合并为一条，sources 标注全部来源（交叉印证）
    - 不一致   → 每个来源一条，conflict=True，由用户裁决

    step: 可选，仅返回该步相关提案。
    targets: 可选，多选目标（identity/members/subunits/albums），优先于 step。
    """
    step_key = _normalize_step(step)
    wanted = {_normalize_step(t) for t in (targets or [])}
    wanted.discard(None)
    if wanted:
        step_key = None
    group = db.scalar(select(Group).where(Group.uid == uid, Group.deleted_at.is_(None)))
    if group is None:
        raise SourceError("组合不存在")
    clean_urls = [(i, u.strip()) for i, u in enumerate(urls) if u and u.strip()]
    ordered: List[Optional[dict]] = [None] * len(urls)
    errors: List[str] = []

    # 线程内不得触碰 SQLAlchemy 会话：先把组合标量字段快照成普通对象
    from types import SimpleNamespace

    group_view = SimpleNamespace(
        **{c.name: getattr(group, c.name) for c in Group.__table__.columns}
    )

    def _fetch_one(idx: int, url: str) -> None:
        try:
            kind, key = _detect(url)
            if kind == "fandom":
                pv = _fandom_preview(key, group_view)
            elif kind == "wikidata":
                pv = _wikidata_preview(key, group_view)
            elif kind == "wikipedia":
                pv = _wikipedia_preview(key, group_view)
            else:
                pv = _baidu_preview(key, group_view)
            pv["items"] = [i for i in pv["items"] if i and not i.get("same")]
            ordered[idx] = pv
        except SourceError as e:
            errors.append(f"{url}：{e}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}：抓取失败 {e}")

    if clean_urls:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(4, len(clean_urls))) as pool:
            list(pool.map(lambda pair: _fetch_one(pair[0], pair[1]), clean_urls))
    previews: List[dict] = [pv for pv in ordered if pv is not None]

    # 按字段归并
    by_field: Dict[str, dict] = {}
    order: List[str] = []
    for pv in previews:
        for it in pv["items"]:
            f = it["field"]
            if f not in by_field:
                by_field[f] = {"label": it["label"], "current": it["current"], "entries": []}
                order.append(f)
            by_field[f]["entries"].append(
                {"proposed": it["proposed"], "source": it["source"], "source_url": pv["source_url"]}
            )

    merged: List[dict] = []
    for f in order:
        info = by_field[f]
        values = {e["proposed"] for e in info["entries"]}
        if len(values) == 1:
            e0 = info["entries"][0]
            merged.append(
                {
                    "field": f,
                    "label": info["label"],
                    "current": info["current"],
                    "proposed": e0["proposed"],
                    "sources": [e["source"] for e in info["entries"]],
                    "source_urls": sorted({e["source_url"] for e in info["entries"]}),
                    "conflict": False,
                    "is_new": info["current"] is None,
                }
            )
        else:
            for e in info["entries"]:
                merged.append(
                    {
                        "field": f,
                        "label": info["label"],
                        "current": info["current"],
                        "proposed": e["proposed"],
                        "sources": [e["source"]],
                        "source_urls": [e["source_url"]],
                        "conflict": True,
                        "is_new": info["current"] is None,
                    }
                )

    merged.sort(key=lambda i: (i["field"] != "debut_date", i["field"]))
    extras = [pv["extra"] for pv in previews]
    member_proposals = _member_proposals(db, group, extras)
    subunit_proposals = _subunit_proposals(db, group, extras)
    company_proposals = _company_proposals(db, group, extras)
    album_proposals, album_singles, album_note = _album_proposals(db, group)

    # 专辑步骤：AI 解析来源页面文本，与平台候选交叉确认
    ai_albums: List[dict] = []
    ai_album_meta: dict = {}
    if (step_key == "albums" or (wanted and "albums" in wanted)) and urls:
        page_texts: List[tuple] = []
        group = db.scalar(select(Group).where(Group.uid == uid, Group.deleted_at.is_(None)))
        zh_title = None
        fandom_title = None
        for u in urls:
            try:
                kind, key = _detect(u)
            except SourceError:
                continue
            if kind == "wikidata" and not zh_title:
                try:
                    ents = wiki_service._entities([key])
                    zh_title = ((ents.get(key) or {}).get("sitelinks") or {}).get("zhwiki", {}).get("title")
                except Exception:  # noqa: BLE001
                    pass
            elif kind == "fandom" and not fandom_title:
                fandom_title = key
        if zh_title:
            t = wiki_service.get_page_text_zhwiki(zh_title)
            if t:
                page_texts.append((f"中文维基百科·{zh_title}", t))
        if fandom_title:
            t = fandom_service.page_wikitext_full(fandom_title)
            if t:
                page_texts.append((f"K-pop Fandom·{fandom_title}", t[:12000]))
        if page_texts:
            ai_albums, ai_album_meta = _ai_albums_from_pages(db, group, page_texts)

            # 交叉确认：AI 提取与平台候选同名 → 合并来源并排到最前
            def _rank(item):
                confirmed = len(item.get("sources") or []) >= 2
                return (0 if confirmed else 1, item.get("name"))

            by_name = {_norm_name(a["name"]): a for a in album_proposals}
            confirmed_names = set()
            for ai_a in ai_albums:
                key = _norm_name(ai_a["name"])
                hit = by_name.get(key)
                if hit:
                    for src in ai_a["sources"]:
                        if src not in hit["sources"]:
                            hit["sources"].append(src)
                    confirmed_names.add(key)
                else:
                    album_proposals.append(ai_a)
                    confirmed_names.add(key)
            for key in confirmed_names:
                by_name.pop(key, None)
            album_proposals.sort(key=_rank)
        if ai_album_meta.get("error"):
            errors.append(f"AI 专辑提取：{ai_album_meta['error']}")
        album_note = album_note or (
            f"音乐平台候选经来源交叉确认；AI 从来源页面提取了 {ai_album_meta.get('extracted', 0)} 张。"
            if ai_album_meta.get("status") == "ok"
            else (ai_album_meta.get("error") or None)
        )
    extra = _extra_merge(extras)
    if album_singles:
        extra["singles_summary"] = (
            f"另有 {len(album_singles)} 首数位单曲："
            + "、".join(_norm(x.get("name")) for x in album_singles[:8])
            + ("…" if len(album_singles) > 8 else "")
        )

    # 空专辑：给出候选碟，不自动选定、不预拉曲目（先选碟再补曲）
    track_proposals: List[dict] = []
    if (wanted and "albums" in wanted) or step_key in ("albums", "tracks"):
        track_proposals = _track_fill_proposals(db, group)

    fandom_srcs: List[dict] = []
    if wanted:
        if "identity" not in wanted:
            merged = []
        if "members" not in wanted:
            member_proposals = []
        else:
            member_proposals = [m for m in member_proposals if m.get("action") != "exists"]
        if "subunits" not in wanted:
            subunit_proposals = []
        if "company" not in wanted:
            company_proposals = []
        if "albums" not in wanted:
            album_proposals = []
            track_proposals = []
        else:
            by_id = {a.get("album_id"): a for a in album_proposals if a.get("album_id")}
            for f in track_proposals:
                existing = by_id.get(f.get("album_id"))
                if existing is not None:
                    existing["candidates"] = f.get("candidates") or []
                    existing["needs_tracks"] = True
                else:
                    album_proposals.append(f)
            track_proposals = []
    elif step_key == "identity":
        member_proposals, subunit_proposals, album_proposals, track_proposals = [], [], [], []
    elif step_key == "subunits":
        merged, member_proposals, album_proposals, track_proposals = [], [], [], []
    elif step_key == "members":
        merged = []
        subunit_proposals, album_proposals, track_proposals = [], [], []
        member_proposals = [m for m in member_proposals if m.get("action") != "exists"]
    elif step_key == "member_details":
        merged = []
        subunit_proposals, album_proposals, track_proposals = [], [], []
        # 1) 来源页已带出的档案字段 → enrich
        detail = []
        seen_ids: set = set()
        from app.models.artist import Artist as _Artist

        _DETAIL_AF_KEYS = (
            "birth_date",
            "chinese_name",
            "english_name",
            "stage_name",
            "birth_place",
            "occupation",
            "description",
        )
        for m in member_proposals:
            if m.get("action") not in ("exists", "link"):
                continue
            af = dict(m.get("artist_fields") or {})
            artist = None
            if m.get("artist_id") is not None:
                artist = db.scalar(select(_Artist).where(_Artist.id == m["artist_id"]))
            if artist is not None:
                empties = _artist_empty_fields(artist)
                af = {k: (af.get(k) if empties.get(k) else None) for k in _DETAIL_AF_KEYS}
            if any(af.get(k) for k in _DETAIL_AF_KEYS) or m.get("positions"):
                item = {**m, "action": "enrich", "artist_fields": af, "field_origins": dict(m.get("field_origins") or {})}
                _seed_field_origins(item)
                detail.append(item)
                if m.get("artist_id") is not None:
                    seen_ids.add(m["artist_id"])
        # 2) 对库内在籍成员按人搜索 Fandom（urls 为空/仅有组合页时尤其关键）
        #    单人失败不拖垮整步；超时后仍继续 AI
        enrich_more, fandom_srcs, enrich_errs = [], [], []
        try:
            enrich_more, fandom_srcs, enrich_errs = _member_details_enrich_proposals(
                db, group, extras
            )
        except Exception as e:  # noqa: BLE001
            errors.append(f"成员百科补全部分失败（已继续）：{e}")
        for msg in (enrich_errs or [])[:12]:
            errors.append(f"成员百科：{msg}")
        for m in enrich_more:
            aid = m.get("artist_id")
            if aid is not None and aid in seen_ids:
                # 合并：用搜索结果填补来源提案里仍空的字段
                existing = next((x for x in detail if x.get("artist_id") == aid), None)
                if existing is None:
                    detail.append(m)
                    continue
                eaf = existing.setdefault("artist_fields", {})
                eorig = existing.setdefault("field_origins", {})
                for k, v in (m.get("artist_fields") or {}).items():
                    if v and not eaf.get(k):
                        eaf[k] = v
                        eorig[k] = (m.get("field_origins") or {}).get(k) or "fandom"
                if m.get("positions") and not existing.get("positions"):
                    existing["positions"] = m["positions"]
                    eorig["positions"] = (m.get("field_origins") or {}).get("positions") or "fandom"
                for s in m.get("sources") or []:
                    if s not in existing["sources"]:
                        existing["sources"].append(s)
            else:
                detail.append(m)
                if aid is not None:
                    seen_ids.add(aid)
        # 3) 补全员 A：即使 urls 为空 / 百科部分失败也跑；未配置则写明原因
        ai_cfg, ai_skip = _ingest_ai_cfg(db)
        ai_meta = {
            "status": "skipped",
            "filled_fields": 0,
            "members_touched": 0,
            "error": ai_skip,
        }
        try:
            if ai_cfg:
                detail = _ensure_detail_stubs_for_ai(db, group, detail)
                # 汇总已抓取来源的原文摘录，供 AI 综合而非凭记忆补全
                ctx_lines: List[str] = []
                for pv in previews:
                    ex = pv.get("extra") or {}
                    label = pv.get("source_label") or pv.get("source_type") or "来源"
                    if ex.get("description"):
                        ctx_lines.append(f"【{label}·简介】{ex['description'][:260]}")
                    for m in ex.get("members") or []:
                        bits = [
                            f"{m.get(k)}"
                            for k in ("name", "chinese_name", "korean_name", "birth_date", "birth_place", "positions")
                            if m.get(k)
                        ]
                        if bits:
                            ctx_lines.append("· " + "，".join(bits) + f"（{m.get('_source') or label}）")
                detail, ai_meta = _ai_gap_fill_members(
                    db, group, detail, ai_cfg, source_context="\n".join(ctx_lines)
                )
        except Exception as e:  # noqa: BLE001
            ai_meta = {
                "status": "error",
                "filled_fields": 0,
                "members_touched": 0,
                "error": str(e)[:240],
            }
            errors.append(f"AI 补全异常（已保留百科结果）：{e}")
        # 去掉仍无任何可写档案字段的空提案
        member_proposals = [m for m in detail if _proposal_has_fillable_fields(m)]
        # 把 AI 状态挂到 extra（下方合并进返回）
        extras.append({"_ai_gap_fill": ai_meta})
    elif step_key == "albums":
        merged, member_proposals, subunit_proposals, track_proposals = [], [], [], []
    elif step_key == "tracks":
        merged, member_proposals, subunit_proposals = [], [], []
        album_proposals = track_proposals
        track_proposals = []

    sources_out = [
        {"source_type": pv["source_type"], "label": pv["source_label"], "url": pv["source_url"]}
        for pv in previews
    ]
    if fandom_srcs:
        seen_u = {s.get("url") for s in sources_out}
        for s in fandom_srcs:
            u = s.get("url")
            if u and u not in seen_u:
                sources_out.append(s)
                seen_u.add(u)

    ai_gap_fill = None
    for ex in extras:
        if isinstance(ex, dict) and ex.get("_ai_gap_fill") is not None:
            ai_gap_fill = ex.get("_ai_gap_fill")
    extra_out = {**extra, "step": step_key}
    if step_key == "member_details":
        extra_out["ai_gap_fill"] = ai_gap_fill or {"status": "skipped", "error": "未知状态"}
        st = (ai_gap_fill or {}).get("status")
        err = (ai_gap_fill or {}).get("error")
        if st == "error" and err:
            errors.append(f"AI 补全失败：{err}")
        elif st == "skipped" and err:
            errors.append(f"AI 未参与：{err}")

    return {
        "sources": sources_out,
        "items": merged,
        "member_proposals": member_proposals,
        "subunit_proposals": subunit_proposals,
        "company_proposals": company_proposals,
        "album_proposals": album_proposals,
        "single_proposals": album_singles,
        "album_note": album_note,
        "extra": extra_out,
        "errors": errors,
    }


def apply_source(db: Session, uid: str, url: str, fields: Dict[str, str]) -> dict:
    """（兼容保留）单来源标量字段合并，返回旧结构。新代码请用 apply_growth。"""
    res = apply_growth(db, uid, fields, [], [], [url])
    return {
        "applied": res["fields"],
        "skipped": res["fields_skipped_locked"],
        **{k: v for k, v in res.items() if k not in ("fields", "fields_skipped_locked")},
    }


def _date_str_year(d) -> Optional[str]:
    if d is None:
        return None
    try:
        return str(d)[:4]
    except Exception:  # noqa: BLE001
        return None


def _track_fill_proposals(db: Session, group: Group) -> List[dict]:
    """为曲目数为 0 的已有专辑，搜索外部曲目表，产出 fill_tracks 提案。"""
    from concurrent.futures import ThreadPoolExecutor

    from app.models.album import Album, AlbumTrack
    from app.services import album_external_service as aes

    albums = db.scalars(
        select(Album).where(
            Album.deleted_at.is_(None),
            Album.release_artist_type == "group",
            Album.release_artist_id == group.id,
        )
    ).all()
    out: List[dict] = []
    for alb in albums:
        track_n = len(db.scalars(select(AlbumTrack).where(AlbumTrack.album_id == alb.id)).all())
        if track_n > 0:
            continue
        query = f"{group.name} {alb.name}"
        candidates: List[dict] = []
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        lambda f: _safe_album_search(f, query),
                        (aes.search_itunes_albums, aes.search_deezer_albums),
                    )
                )
            for batch in results:
                candidates.extend(batch or [])
        except Exception:  # noqa: BLE001
            candidates = []
        best = None
        for c in candidates:
            if _norm_name(c.get("name")) == _norm_name(alb.name):
                best = c
                break
        exact = [
            c
            for c in candidates
            if _norm_name(c.get("name")) == _norm_name(alb.name)
        ]
        rest = [c for c in candidates if c not in exact]
        seen_eid: set = set()
        cand_out = []
        for c in exact + rest:
            eid = c.get("external_id")
            if not eid or eid in seen_eid:
                continue
            seen_eid.add(eid)
            cand_out.append(
                {
                    "external_id": eid,
                    "name": c.get("name"),
                    "artist": c.get("artist"),
                    "year": c.get("year"),
                    "track_count": c.get("track_count"),
                    "cover_url": c.get("cover_url"),
                    "source": c.get("source"),
                }
            )
            if len(cand_out) >= 8:
                break
        out.append(
            {
                "action": "fill_tracks",
                "name": alb.name,
                "year": _date_str_year(alb.release_date),
                "track_count": 0,
                "external_id": None,
                "cover_url": None,
                "sources": [],
                "album_id": alb.id,
                "tracks_preview": [],
                "candidates": cand_out,
            }
        )
    return out


def preview_album_tracklist(db: Session, uid: str, album_id: int, external_id: str) -> dict:
    """先选碟再补曲：按用户选定的外部碟拉取曲目预览。只读。"""
    from app.models.album import Album

    group = db.scalar(select(Group).where(Group.uid == uid, Group.deleted_at.is_(None)))
    if group is None:
        raise SourceError("组合不存在")
    album = db.get(Album, album_id)
    if (
        album is None
        or getattr(album, "deleted_at", None) is not None
        or album.release_artist_type != "group"
        or album.release_artist_id != group.id
    ):
        raise SourceError("专辑不存在或不属于该组合")
    eid = _norm(external_id)
    if not eid:
        raise SourceError("请先选择一张外部碟")
    try:
        bundle = _fetch_tracklist_bundle(eid)
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"曲目表获取失败: {e}") from e
    tracks = bundle.get("tracks") or []
    return {
        "album_id": album.id,
        "album_name": album.name,
        "external_id": eid,
        "release_date": _clean_date(bundle.get("release_date")),
        "tracks_preview": [
            {
                "name": t.get("name"),
                "track_number": t.get("track_number"),
                "disc_number": t.get("disc_number") or 1,
            }
            for t in tracks[:80]
        ],
    }


def search_candidates(q: str) -> dict:
    """组合/条目搜索候选：Fandom 页面 + Wikidata 实体。"""
    out: List[dict] = []
    try:
        for c in fandom_service.build_search_candidates(q, limit=4):
            title = c.get("title") or c.get("name") or ""
            out.append(
                {
                    "source_type": "fandom",
                    "title": title,
                    "snippet": c.get("snippet") or c.get("meta") or "",
                    "url": _fandom_page_url(title),
                }
            )
    except Exception:  # noqa: BLE001
        pass
    try:
        for e in wiki_service.search_entities(q, limit=4) or []:
            qid = e.get("qid") or ""
            if not qid:
                continue
            out.append(
                {
                    "source_type": "wikidata",
                    "title": e.get("label") or qid,
                    "snippet": e.get("description") or "",
                    "url": f"https://www.wikidata.org/wiki/{qid}",
                }
            )
    except Exception:  # noqa: BLE001
        pass
    return {"items": out[:8]}

