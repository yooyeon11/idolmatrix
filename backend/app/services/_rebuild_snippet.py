"""source_sync 缺失函数重建（历史版本在对话中可追溯，逻辑以当前测试为准）。"""

# 以下内容将插入 source_sync.py
REBUILD = '''
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


def _member_fact(facts: Dict[str, dict], order: List[str], m: dict) -> Optional[dict]:
    """把一条来源成员记录归并进 facts（见 _member_proposals 的同一人判定）。"""
    name = _norm(m.get("name"))
    if not name:
        return None
    nk = _norm_person(name)
    ko = _norm(m.get("korean_name"))
    target: Optional[dict] = None
    for key in order:
        f = facts[key]
        if nk and f["_nk"] == nk:
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
            "korean_name": None,
            "birth_date": None,
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
    if m.get("birth_date") and not target.get("birth_date"):
        target["birth_date"] = m["birth_date"]
    if m.get("description") and not target.get("description"):
        target["description"] = m["description"]
    if m.get("_source"):
        target["sources"].add(m["_source"])
    return target


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
            "artist_fields": {
                "birth_date": f.get("birth_date"),
                "description": f.get("description"),
            },
        }
        if artist is None:
            out.append({"action": "create", **base})
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


def preview_sources(db: Session, uid: str, urls: List[str]) -> dict:
    """抓取多个来源，按字段合并成一张 diff 表。只读，不写库。

    多来源对同一字段的提案：
    - 完全一致 → 合并为一条，sources 标注全部来源（交叉印证）
    - 不一致   → 每个来源一条，conflict=True，由用户裁决
    """
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
            pv = (
                _fandom_preview(key, group_view)
                if kind == "fandom"
                else _wikidata_preview(key, group_view)
            )
            pv["items"] = [i for i in pv["items"] if i and not i["same"]]
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
    return {
        "sources": [
            {"source_type": pv["source_type"], "label": pv["source_label"], "url": pv["source_url"]}
            for pv in previews
        ],
        "items": merged,
        "member_proposals": _member_proposals(db, group, extras),
        "subunit_proposals": _subunit_proposals(db, group, extras),
        "album_proposals": _album_proposals(db, group),
        "extra": _extra_merge(extras),
        "errors": errors,
    }


def apply_source(db: Session, uid: str, url: str, fields: Dict[str, str]) -> dict:
    """（兼容保留）单来源标量字段合并。新代码请用 apply_growth。"""
    return apply_growth(db, uid, fields, [], [], [url])


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
'''
print("REBUILD ready")
