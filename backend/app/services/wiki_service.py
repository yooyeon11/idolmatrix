"""Wikidata / Wikipedia 资料获取服务。

用途：
1. 为 AI 实体分析（analyze_entity）提供「参考资料」——把组合成员、艺人经历、
   出道/出生日期、维基简介等确定性数据注入提示词，让模型做阅读理解而不是回忆，
   从根本上避免「经历编造」和「信息过时」。
2. 为外部头像/简介补全（external_providers）提供 Wikidata 条目搜索与详情。

数据口径：
- 组合成员：团体条目的 P527（has part，含 P580/P582 加入/退出时间限定符）
  为主，SPARQL 反查 P463（member of）为辅，两者合并去重。
- 艺人经历：艺人条目的 P463（member of）限定符。
- 出道日期（组合）：P571（inception）；出生日期：P569（date of birth）。
- 简介：Wikipedia REST summary（优先中文站，回退英文站）。

全部使用标准库 urllib，不引入额外依赖。网络/解析失败一律返回 None
（搜索类）或抛 ProviderError（详情类，由路由转 HTTPException），
绝不吞掉业务错误。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services import proxy_config
from app.services.audiodb_service import ProviderError

logger = logging.getLogger(__name__)

_WD_API = "https://www.wikidata.org/w/api.php"
_WD_SPARQL = "https://query.wikidata.org/sparql"
# Wikimedia 系对 UA 有合规要求，带项目说明的 UA 命中率更高
_UA = "KpopMediaLibrary/1.0 (self-hosted K-pop media library; urllib)"

_MAX_MEMBERS = 30
_EXTRACT_LIMIT = 1500

# 描述关键词打分：用于从搜索结果中挑选「正确」的条目
_GROUP_HINTS = ("group", "band", "girl group", "boy band", "orchestra", "trio", "duo", "组合", "乐团")
_ARTIST_HINTS = ("singer", "rapper", "dancer", "songwriter", "musician", "actress", "actor", "idol", "歌手", "演员")
_SONG_HINTS = ("song", "single by", "song by", "歌曲")
_ALBUM_HINTS = ("album", "extended play", "ep by", "single by", "专辑")
_COMPANY_HINTS = ("company", "label", "record label", "entertainment", "agency", "公司", "娱乐")


def _http_json(
    url: str,
    params: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    error_hint: str = "Wikidata",
) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    timeout = timeout if timeout is not None else settings.audiodb_timeout
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with proxy_config.urlopen(req, timeout=timeout) as resp:
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


# ---------------------------------------------------------------------------
# 基础 API 封装
# ---------------------------------------------------------------------------

def search_entities(term: str, limit: int = 7) -> List[Dict[str, str]]:
    """wbsearchentities 检索，返回 [{qid, label, description}]。失败返回空列表。"""
    term = (term or "").strip()
    if not term:
        return []
    try:
        data = _http_json(
            _WD_API,
            {
                "action": "wbsearchentities",
                "format": "json",
                "type": "item",
                "language": "en",
                "uselang": "en",
                "limit": str(limit),
                "search": term,
            },
        )
    except ProviderError as e:
        logger.info("[wiki] 检索失败（%s）: %s", term, e)
        return []
    out: List[Dict[str, str]] = []
    for r in data.get("search") or []:
        qid = r.get("id") or ""
        label = (r.get("label") or "").strip()
        if qid and label:
            out.append({"qid": qid, "label": label, "description": (r.get("description") or "").strip()})
    return out


def _entities(qids: List[str], with_claims: bool = True) -> Dict[str, dict]:
    """wbgetentities 批量获取；返回 {qid: entity}，缺失的 qid 不会出现。"""
    qids = [q for q in qids if q]
    if not qids:
        return {}
    props = "labels|descriptions|aliases|sitelinks"
    if with_claims:
        props = "labels|descriptions|aliases|sitelinks|claims"
    try:
        data = _http_json(
            _WD_API,
            {
                "action": "wbgetentities",
                "format": "json",
                "ids": "|".join(qids[:50]),
                "props": props,
                "languages": "en|ko|zh-hans|zh",
            },
        )
    except ProviderError as e:
        logger.info("[wiki] 获取实体失败: %s", e)
        return {}
    return {qid: ent for qid, ent in (data.get("entities") or {}).items() if "missing" not in ent}


def _label(entity: dict, lang: str) -> Optional[str]:
    v = (entity.get("labels") or {}).get(lang) or {}
    s = (v.get("value") or "").strip()
    return s or None


def _description(entity: dict) -> Optional[str]:
    labels = entity.get("descriptions") or {}
    for lang in ("zh-hans", "zh", "en", "ko"):
        v = labels.get(lang) or {}
        s = (v.get("value") or "").strip()
        if s:
            return s
    return None


def _aliases(entity: dict, lang: str, limit: int = 8) -> List[str]:
    out: List[str] = []
    for a in (entity.get("aliases") or {}).get(lang) or []:
        s = (a.get("value") or "").strip()
        if s:
            out.append(s)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# claims 解析
# ---------------------------------------------------------------------------

def _date_of_time(value: Optional[dict]) -> Optional[str]:
    """Wikidata 时间值转日期字符串；precision 不足时返回部分日期。"""
    if not isinstance(value, dict):
        return None
    t = value.get("time") or ""
    m = re.fullmatch(r"[+-](\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}Z", t)
    if not m:
        return None
    precision = value.get("precision") or 0
    if precision >= 11:
        return m.group(1)
    if precision == 10:
        return m.group(1)[:7]
    if precision == 9:
        return m.group(1)[:4]
    return None


def _value_claims(entity: dict, prop: str) -> List[dict]:
    out = []
    for c in (entity.get("claims") or {}).get(prop) or []:
        if c.get("mainsnak", {}).get("snaktype") == "value":
            out.append(c)
    return out


def _claim_entity_id(claim: dict) -> Optional[str]:
    v = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
    if isinstance(v, dict):
        qid = v.get("id")
        return qid if isinstance(qid, str) and qid.startswith("Q") else None
    return None


def _claim_time(claim: dict) -> Optional[str]:
    v = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
    return _date_of_time(v if isinstance(v, dict) else None)


def _qualifier_time(claim: dict, prop: str) -> Optional[str]:
    for q in (claim.get("qualifiers") or {}).get(prop) or []:
        if q.get("snaktype") == "value":
            v = q.get("datavalue", {}).get("value")
            s = _date_of_time(v if isinstance(v, dict) else None)
            if s:
                return s
    return None


def _image_url(entity: dict) -> Optional[str]:
    """P18 → Commons Special:FilePath（带宽度参数，服务器负责缩放）。"""
    for claim in _value_claims(entity, "P18"):
        v = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(v, str) and v.strip():
            name = v.strip().replace(" ", "_")
            return (
                "https://commons.wikimedia.org/wiki/Special/FilePath/"
                f"{urllib.parse.quote(name)}?width=480"
            )
    return None


def _wikipedia_extract(entity: dict) -> tuple[Optional[str], Optional[str]]:
    """按 sitelink 取维基百科摘要；返回 (extract, lang)。"""
    links = entity.get("sitelinks") or {}
    for site, lang in (("zhwiki", "zh"), ("enwiki", "en")):
        title = (links.get(site) or {}).get("title")
        if not title:
            continue
        try:
            host = "zh.wikipedia.org" if lang == "zh" else "en.wikipedia.org"
            data = _http_json(
                f"https://{host}/api/rest_v1/page/summary/{urllib.parse.quote(title.replace(' ', '_'))}",
                error_hint=f"Wikipedia({lang})",
                timeout=10,
            )
            extract = (data.get("extract") or "").strip()
            if extract:
                return extract[:_EXTRACT_LIMIT], lang
        except ProviderError as e:
            logger.info("[wiki] 摘要获取失败（%s）: %s", title, e)
    return None, None


# ---------------------------------------------------------------------------
# SPARQL：反查组合成员（P463 member of）
# ---------------------------------------------------------------------------

def _sparql_members(group_qid: str) -> List[Dict[str, Any]]:
    """SPARQL 查询所有 P463 指向该组合的艺人（含加入/退出时间）。失败返回空。"""
    query = (
        "SELECT ?person ?start ?end WHERE {"
        f" ?person wdt:P463 wd:{group_qid} ."
        " OPTIONAL { ?person p:P463 ?st . ?st pq:P580 ?start . }"
        " OPTIONAL { ?person p:P463 ?st2 . ?st2 pq:P582 ?end . }"
        "} LIMIT 60"
    )
    try:
        data = _http_json(
            _WD_SPARQL,
            {"query": query, "format": "json"},
            timeout=20,
            error_hint="Wikidata SPARQL",
        )
    except ProviderError as e:
        logger.info("[wiki] SPARQL 成员反查失败: %s", e)
        return []
    out = []
    for binding in (data.get("results") or {}).get("bindings") or []:
        person = ((binding.get("person") or {}).get("value") or "").rsplit("/", 1)[-1]
        if not person.startswith("Q"):
            continue
        start = _date_of_time({"time": (binding.get("start") or {}).get("value", ""), "precision": 11}) or None
        end = _date_of_time({"time": (binding.get("end") or {}).get("value", ""), "precision": 11}) or None
        out.append({"qid": person, "join_date": start, "leave_date": end})
    return out


# ---------------------------------------------------------------------------
# 条目选择（搜索结果打分）
# ---------------------------------------------------------------------------

_KIND_HINTS = {
    "group": _GROUP_HINTS,
    "artist": _ARTIST_HINTS,
    "song": _SONG_HINTS,
    "album": _ALBUM_HINTS,
    "company": _COMPANY_HINTS,
}


def _entity_type_of(entity_type: str) -> str:
    if entity_type == "groups":
        return "group"
    if entity_type == "artists":
        return "artist"
    if entity_type == "songs":
        return "song"
    if entity_type == "albums":
        return "album"
    if entity_type == "companies":
        return "company"
    return "other"


def _pick_entity(results: List[Dict[str, str]], term: str, kind: str) -> Optional[str]:
    """从搜索结果中挑最匹配的条目。

    人物/组合：精确标签匹配优先，描述关键词加分（软偏好）。
    歌曲/专辑/公司：必须有描述关键词命中（硬门槛，避免同名人物误配）。
    """
    if not results:
        return None
    hints = _KIND_HINTS.get(kind)
    t = (term or "").strip().lower()
    best_qid: Optional[str] = None
    best_score = 0
    for i, r in enumerate(results):
        score = 0
        label = (r.get("label") or "").lower()
        if label == t:
            score += 4
        elif t and t in label:
            score += 2
        desc = (r.get("description") or "").lower()
        if hints:
            hit = any(h in desc for h in hints)
            if kind in ("group", "artist"):
                if hit:
                    score += 2
            else:
                if not hit:
                    score = 0  # 硬门槛
                else:
                    score += 1
        if score > 0:
            score += max(0, 3 - i)  # 靠前的结果微加分
        if score > best_score:
            best_score = score
            best_qid = r["qid"]
    return best_qid if best_score > 0 else None


# ---------------------------------------------------------------------------
# 实体详情（供 external_providers 使用）
# ---------------------------------------------------------------------------

def get_entity_info(qid: str, kind: str) -> Optional[Dict[str, Any]]:
    """获取单个实体的完整信息（标签/日期/成员或经历/图片/维基摘要）。

    kind: group / artist / song / album / company / other
    失败返回 None（调用方自行降级）。
    """
    entities = _entities([qid])
    entity = entities.get(qid)
    if not entity:
        return None

    info: Dict[str, Any] = {
        "qid": qid,
        "label": _label(entity, "en") or _label(entity, "ko") or _label(entity, "zh-hans"),
        "label_ko": _label(entity, "ko"),
        "label_zh": _label(entity, "zh-hans") or _label(entity, "zh"),
        "description": _description(entity),
        "image": _image_url(entity),
    }
    extract, extract_lang = _wikipedia_extract(entity)
    info["wikipedia_extract"] = extract
    info["wikipedia_lang"] = extract_lang

    if kind == "group":
        for claim in _value_claims(entity, "P571"):
            info["inception"] = _claim_time(claim)
            break
        info["members"] = _group_members(entity, qid)
    elif kind == "artist":
        for claim in _value_claims(entity, "P569"):
            info["birth_date"] = _claim_time(claim)
            break
        info["memberships"] = _artist_memberships(entity)
    return info


def _group_members(entity: dict, group_qid: str) -> List[Dict[str, Any]]:
    """组合成员：P527（含 P580/P582 限定符）∪ SPARQL P463，按 QID 去重。"""
    merged: Dict[str, Dict[str, Any]] = {}
    for claim in _value_claims(entity, "P527"):
        mqid = _claim_entity_id(claim)
        if not mqid:
            continue
        merged[mqid] = {
            "qid": mqid,
            "join_date": _qualifier_time(claim, "P580"),
            "leave_date": _qualifier_time(claim, "P582"),
        }
    for row in _sparql_members(group_qid):
        existing = merged.get(row["qid"])
        if existing:
            existing["join_date"] = existing.get("join_date") or row.get("join_date")
            existing["leave_date"] = existing.get("leave_date") or row.get("leave_date")
        else:
            merged[row["qid"]] = row
    if not merged:
        return []
    qids = list(merged.keys())[:_MAX_MEMBERS]
    member_ents = _entities(qids, with_claims=True)
    out = []
    for mqid in qids:
        ent = member_ents.get(mqid) or {}
        birth = next((_claim_time(c) for c in _value_claims(ent, "P569")), None)
        out.append(
            {
                "qid": mqid,
                "name": _label(ent, "en") or _label(ent, "ko") or row["qid"],
                "korean_name": _label(ent, "ko"),
                "chinese_name": _label(ent, "zh-hans") or _label(ent, "zh"),
                "birth_date": birth,
                "description": _description(ent),
                "join_date": row.get("join_date"),
                "leave_date": row.get("leave_date"),
            }
        )
    out.sort(key=lambda r: (r.get("join_date") or "9999", r["name"]))
    return out


def get_sub_units(group_qid: str) -> List[Dict[str, Any]]:
    """查询小分队：P463 指向本组合、且自身是音乐组合（Q215380 子类）的条目。

    一次带回小分队名与其成员名（供生长引擎建小分队 + 铺成员）。失败返回空。
    """
    query = (
        "SELECT ?unit ?unitLabel ?member ?memberLabel WHERE {"
        f" ?unit wdt:P463 wd:{group_qid} ."
        " ?unit wdt:P31/wdt:P279* wd:Q215380 ."
        " OPTIONAL { ?member wdt:P463 ?unit . }"
        ' SERVICE wikibase:label { bd:serviceParam wikibase:language "en,ko,zh". }'
        "} LIMIT 300"
    )
    try:
        data = _http_json(
            _WD_SPARQL,
            {"query": query, "format": "json"},
            timeout=25,
            error_hint="Wikidata SPARQL",
        )
    except ProviderError as e:
        logger.info("[wiki] 小分队查询失败: %s", e)
        return []
    units: Dict[str, Dict[str, Any]] = {}
    for binding in (data.get("results") or {}).get("bindings") or []:
        def _qid(field: str) -> str:
            return ((binding.get(field) or {}).get("value") or "").rsplit("/", 1)[-1]

        def _label(field: str) -> str:
            return (binding.get(field) or {}).get("value") or ""

        uqid = _qid("unit")
        if not uqid.startswith("Q"):
            continue
        unit = units.setdefault(uqid, {"qid": uqid, "name": _label("unitLabel") or uqid, "members": []})
        mqid = _qid("member")
        mlabel = _label("memberLabel")
        if mqid.startswith("Q") and mlabel and mlabel != unit["name"]:
            if mlabel not in unit["members"]:
                unit["members"].append(mlabel)
    out = list(units.values())
    out.sort(key=lambda u: u["name"])
    return out




def get_group_members_zhwiki(page_title: str) -> Optional[List[Dict[str, Any]]]:
    """解析中文维基百科组合条目的成员表（渲染 HTML）。

    返回 [{stage_en, stage_cn, chinese_name, birth_date, birth_place, status}]，
    条目不存在/无成员表返回 None，有表无数据返回 []。
    表结构（izna/tripleS 等 K-pop 条目通用）：列 = 艺名(中/英/韩/日) + 本名 +
    出生日期出生地（+排名/担当等），th 行「现任成员/过往成员」切分状态。
    """
    import re as _re

    try:
        data = _http_json(
            _WD_API.replace("www.wikidata.org/w/api.php", "zh.wikipedia.org/w/api.php"),
            {
                "action": "parse",
                "page": page_title,
                "prop": "text",
                "variant": "zh-cn",
                "format": "json",
                "formatversion": "2",
            },
            timeout=25,
            error_hint="中文维基百科",
        )
    except ProviderError as e:
        logger.info("[wiki] 中文维基成员表获取失败（%s）: %s", page_title, e)
        return None
    html = (data.get("parse") or {}).get("text") or ""
    if not html:
        return None

    def _clean(fragment: str) -> str:
        t = _re.sub(r"<[^>]+>", "", fragment)
        t = t.replace("&#160;", " ").replace("&nbsp;", " ")
        t = _re.sub(r"\[\d+\]", "", t)
        return _re.sub(r"\s+", " ", t).strip()

    # 定位成员表：找含「艺名」表头的 <table> 区段
    anchor = html.find("艺名")
    if anchor < 0:
        anchor = html.find("藝名")
    if anchor < 0:
        return []
    tbl_start = html.rfind("<table", 0, anchor)
    tbl_end = html.find("</table>", anchor)
    if tbl_start < 0 or tbl_end < 0:
        return []
    table = html[tbl_start:tbl_end]

    rows = _re.findall(r"<tr[^>]*>(.*?)</tr>", table, _re.S)
    members: List[Dict[str, Any]] = []
    status = "Active"
    for row in rows:
        cells = [_clean(c) for c in _re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, _re.S)]
        if not cells:
            continue
        joined = " ".join(cells)
        if "现任成员" in joined or "現任成員" in joined:
            status = "Active"
            continue
        if "过往成员" in joined or "過往成員" in joined or "前成員" in joined:
            status = "Former"
            continue
        if any(h in joined for h in ("艺名", "藝名", "本名", "出生日期", "中文", "英文", "韩文", "韓文", "日文")):
            continue  # 表头行
        latin_cells = [c for c in cells if _re.fullmatch(r"[A-Z][A-Z .'’\-]{2,30}", c)]
        if len(cells) < 5 or not latin_cells:
            continue
        stage_en = latin_cells[0].title().replace(" ", "")
        stage_cn = cells[0] if cells[0] != latin_cells[0] else ""
        # 本名单元格：含「／」或全名分隔符的那个
        full_cell = next(
            (c for c in cells if "／" in c or ("，" in c and "年" not in c)), ""
        )
        # 生日单元格：含 (YYYY-MM-DD) 的那个
        birth_cell = next((c for c in cells if _re.search(r"\(\d{4}-\d{2}-\d{2}\)", c)), "")
        birth_m = _re.search(r"(\d{4})-(\d{2})-(\d{2})", birth_cell)
        birth_date = (
            f"{birth_m.group(1)}-{birth_m.group(2)}-{birth_m.group(3)}"
            if birth_m
            else None
        )
        birth_place = ""
        pm = _re.search(r"）\s*([^（）]*?)\s*$", birth_cell)
        if pm:
            birth_place = _re.sub(r"\[.*?\]", "", pm.group(1)).strip()
        # 中文名：日文名取「／」前整段汉字（富岡 茉衣 → 富岡茉衣）；
        # 韩文名取「／」后的 hanja（방지민／房智玟 → 房智玟）
        cn = ""
        if full_cell:
            hanji_runs = _re.findall(r"[\u4e00-\u9fff々]{2,8}", full_cell)
            if "／" in full_cell:
                pre, _, post = full_cell.partition("／")
                pre_runs = _re.findall(r"[\u4e00-\u9fff々]{2,8}", pre)
                post_runs = _re.findall(r"[\u4e00-\u9fff々]{2,8}", post)
                cjk_pre = "".join(pre_runs)
                cjk_pre_k = _re.findall(r"[가-힣]", pre)
                cn = (
                    cjk_pre
                    if cjk_pre and not cjk_pre_k
                    else (post_runs[-1] if post_runs else "")
                )
            elif hanji_runs:
                cn = "".join(hanji_runs)
        # infobox 的「过往成员」字段交叉核对（表内分段缺失时兜底）
        past_m = _re.search(r"过往成员</th>\s*<td[^>]*>(.*?)</td>", html, _re.S) or _re.search(
            r"過往成員</th>\s*<td[^>]*>(.*?)</td>", html, _re.S
        )
        past_names = _re.findall(r"[\u4e00-\u9fff々]{2,8}", _clean(past_m.group(1))) if past_m else []
        for mrec in members:
            if mrec["status"] == "Active" and any(
                p in mrec["chinese_name"] or p in mrec["stage_cn"] for p in past_names
            ):
                mrec["status"] = "Former"
        members.append(
            {
                "stage_en": stage_en,
                "stage_cn": stage_cn,
                "chinese_name": cn or stage_cn,
                "birth_date": birth_date,
                "birth_place": birth_place,
                "status": status,
            }
        )
    return members




def get_page_text_zhwiki(page_title: str) -> Optional[str]:
    """取中文维基条目渲染后纯文本（简化字）。失败返回 None。"""
    import re as _re

    try:
        data = _http_json(
            _WD_API.replace("www.wikidata.org/w/api.php", "zh.wikipedia.org/w/api.php"),
            {
                "action": "parse",
                "page": page_title,
                "prop": "text",
                "variant": "zh-cn",
                "format": "json",
                "formatversion": "2",
            },
            timeout=25,
            error_hint="中文维基百科",
        )
    except ProviderError as e:
        logger.info("[wiki] 中文维基页面文本获取失败（%s）: %s", page_title, e)
        return None
    html = (data.get("parse") or {}).get("text") or ""
    if not html:
        return None
    text = _re.sub(r"<style[\s\S]*?</style>", " ", html)
    text = _re.sub(r"<[^>]+>", " ", text)
    return _re.sub(r"[ \t\n]+", " ", text).strip()


def _artist_memberships(entity: dict) -> List[Dict[str, Any]]:
    """艺人组合经历：P463 限定符 P580/P582。"""
    rows: List[Dict[str, Any]] = []
    for claim in _value_claims(entity, "P463"):
        gqid = _claim_entity_id(claim)
        if not gqid:
            continue
        rows.append(
            {
                "qid": gqid,
                "join_date": _qualifier_time(claim, "P580"),
                "leave_date": _qualifier_time(claim, "P582"),
            }
        )
    if not rows:
        return []
    group_ents = _entities([r["qid"] for r in rows], with_claims=False)
    out = []
    for r in rows:
        ent = group_ents.get(r["qid"]) or {}
        out.append(
            {
                "group_name": _label(ent, "en") or _label(ent, "ko") or r["qid"],
                "korean_name": _label(ent, "ko"),
                "join_date": r.get("join_date"),
                "leave_date": r.get("leave_date"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# AI 参考资料（供 analyze_entity 使用）
# ---------------------------------------------------------------------------

def _search_terms(entity_type: str, current: Dict[str, Any]) -> List[str]:
    """从当前表单值里按优先级收集检索词。"""
    keys = ["name", "english_name", "stage_name", "korean_name", "chinese_name"]
    terms: List[str] = []
    for k in keys:
        v = current.get(k)
        if isinstance(v, str) and v.strip():
            s = v.strip()
            if s not in terms:
                terms.append(s)
    return terms[:4]


def fetch_reference(entity_type: str, current: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """抓取参考资料，供注入 AI 提示词。

    返回 {"wikidata": {...}, "sources_for_fields": [...]}；
    抓不到合适条目时返回 None（AI 退回原有工作方式）。
    """
    kind = _entity_type_of(entity_type)
    if kind == "other":
        return None
    for term in _search_terms(entity_type, current):
        results = search_entities(term)
        qid = _pick_entity(results, term, kind)
        if not qid:
            continue
        info = get_entity_info(qid, kind)
        if not info:
            continue

        wikidata: Dict[str, Any] = {}
        if info.get("label"):
            wikidata["官方名称"] = info["label"]
        if info.get("label_zh"):
            wikidata["中文名"] = info["label_zh"]
        if info.get("label_ko"):
            wikidata["韩文名"] = info["label_ko"]
        if info.get("description"):
            wikidata["条目描述"] = info["description"]
        if info.get("wikipedia_extract"):
            wikidata["维基百科简介"] = info["wikipedia_extract"]
        if kind == "group":
            if info.get("inception"):
                wikidata["成立/出道日期"] = info["inception"]
            if info.get("members"):
                wikidata["成员名单（含加入/退出时间）"] = info["members"]
        elif kind == "artist":
            if info.get("birth_date"):
                wikidata["出生日期"] = info["birth_date"]
            if info.get("memberships"):
                wikidata["组合经历（含加入/退出时间）"] = info["memberships"]
        if not wikidata:
            return None

        source_name = f"Wikidata（{info['qid']}）"
        sources: List[Dict[str, str]] = []
        for field, key in (
            ("debut_date", "成立/出道日期"),
            ("birth_date", "出生日期"),
        ):
            if wikidata.get(key):
                sources.append({"field": field, "value": str(wikidata[key]), "source": source_name})
        if info.get("members"):
            names = ", ".join(m["name"] for m in info["members"][:5])
            sources.append(
                {"field": "members", "value": f"{len(info['members'])} 名成员（{names}…）", "source": source_name + " P527/P463"}
            )
        if info.get("memberships"):
            names = ", ".join(m["group_name"] for m in info["memberships"][:5])
            sources.append(
                {"field": "group_memberships", "value": names, "source": source_name + " P463"}
            )
        if info.get("wikipedia_extract"):
            lang = "中文" if info.get("wikipedia_lang") == "zh" else "英文"
            sources.append(
                {"field": "description", "value": info["wikipedia_extract"][:120], "source": f"Wikipedia（{lang}）"}
            )
        return {"wikidata": wikidata, "sources_for_fields": sources}
    return None
