"""资料源同步测试：mock 抓取函数（无网络），验证 diff 生成、白名单写入、锁跳过、来源记录。"""
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="source_sync_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.entity_field_lock import EntityFieldLock  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.services import source_sync  # noqa: E402
from app.services.source_sync import (  # noqa: E402
    SourceError,
    apply_source,
    preview_source,
    search_candidates,
)

init_db()
Base.metadata.create_all(bind=engine)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


db = SessionLocal()

# ===== 造数据 =====
g = Group(
    name="Test Group",
    korean_name="테스트",
    debut_date=date(2020, 1, 1),
    external_links=[{"url": "https://example.com/old", "source": "手动"}],
)
db.add(g)
db.commit()
UID = g.uid

FAKE_FANDOM = {
    "external_id": "fandom:Test Group",
    "name": "Test Group",
    "biography": "Fandom biography text.",
    "thumb": None,
    "source": "K-pop Fandom",
    "fandom_fields": {
        "debut_date": "2020-01-01",  # 与库内相同 → 不应出现在 diff
        "company": "Some Agency",
        "members": [{"name": "A"}, {"name": "B"}],
        "aliases": ["테스트"],
    },
}

FAKE_WIKI = {
    "qid": "Q999",
    "label": "Test Group",
    "label_ko": "테스트",
    "label_zh": "测试组合",
    "description": "south korean group",
    "image": None,
    "wikipedia_extract": "Wiki extract text.",
    "inception": "2021-03-04T00:00:00Z",
    "members": [{"qid": "Q1", "name": "A", "join_date": "2020-01-01", "leave_date": None}],
}

original_fandom = source_sync.fandom_service.fetch_detail
original_wiki = source_sync.wiki_service.get_entity_info
source_sync.fandom_service.fetch_detail = lambda title: FAKE_FANDOM
source_sync.wiki_service.get_entity_info = lambda qid, kind: FAKE_WIKI

try:
    print("== 1. URL 识别 ==")
    check("fandom URL", source_sync._detect("https://kpop.fandom.com/wiki/Test_Group") == ("fandom", "Test_Group"))
    check("wikidata URL", source_sync._detect("https://www.wikidata.org/wiki/Q999") == ("wikidata", "Q999"))
    try:
        source_sync._detect("https://example.com/x")
        check("不支持域名报错", False)
    except SourceError:
        check("不支持域名报错", True)

    print("== 2. Fandom 预览 diff ==")
    pv = preview_source(db, UID, "https://kpop.fandom.com/wiki/Test_Group")
    fields = {i["field"]: i for i in pv["items"]}
    check("相同出道日不进 diff", "debut_date" not in fields, list(fields))
    check("简介进 diff（库内为空）", fields.get("description", {}).get("proposed") == "Fandom biography text.")
    print("DEBUG errors:", pv.get("errors"), "extra:", pv.get("extra"))
    check("公司作为 extra 信息（不自动写入）", pv["extra"]["company_raw"] == "Some Agency")
    check("成员作为 extra 预览", len(pv["extra"]["members"]) == 2)

    print("== 3. Wikidata 预览 diff ==")
    pv2 = preview_source(db, UID, "https://www.wikidata.org/wiki/Q999")
    f2 = {i["field"]: i for i in pv2["items"]}
    check("inception 归一为日期", f2.get("debut_date", {}).get("proposed") == "2021-03-04", f2.get("debut_date"))
    check("英文名（库内为空）进 diff 且标记新增", f2.get("english_name", {}).get("is_new") is True)
    check("相同韩文名不进 diff", "korean_name" not in f2, list(f2))
    check("中文名进 diff", f2.get("chinese_name", {}).get("proposed") == "测试组合")
    check("wiki 提取进 diff", f2.get("description", {}).get("proposed") == "Wiki extract text.")
    check("成员预览带 P580/P582", pv2["extra"]["members"][0]["join_date"] == "2020-01-01")

    print("== 4. 合并（apply） ==")
    res = apply_source(db, UID, "https://www.wikidata.org/wiki/Q999", {
        "chinese_name": "测试组合",
        "description": "Wiki extract text.",
        "name": "HACKED",  # 白名单外字段必须被忽略
    })
    db.expire_all()
    g2 = db.query(Group).filter(Group.uid == UID).one()
    check("中文名已写入", g2.chinese_name == "测试组合")
    check("描述已写入", g2.description == "Wiki extract text.")
    check("白名单外字段被忽略", g2.name == "Test Group")
    check("applied 只含白名单内字段", set(res["applied"]) == {"chinese_name", "description"}, res["applied"])
    check("来源 URL 记入 external_links", any(
        isinstance(l, dict) and l.get("url") == "https://www.wikidata.org/wiki/Q999"
        for l in g2.external_links
    ))
    check("已有链接未重复丢失", any(l.get("url") == "https://example.com/old" for l in g2.external_links))

    print("== 5. 锁定字段跳过 ==")
    db.add(EntityFieldLock(entity_type="groups", entity_id=g2.id, locks={"description": True}))
    db.commit()
    res2 = apply_source(db, UID, "https://www.wikidata.org/wiki/Q999", {
        "chinese_name": "新中文",
        "description": "覆盖描述",
    })
    db.expire_all()
    g3 = db.query(Group).filter(Group.uid == UID).one()
    check("锁定字段跳过", "description" in res2["skipped"] and g3.description == "Wiki extract text.")
    check("未锁字段正常写入", g3.chinese_name == "新中文")
    check("来源 URL 不重复追加", len([l for l in g3.external_links if l.get("url") == "https://www.wikidata.org/wiki/Q999"]) == 1)

    print("== 6. 搜索候选（mock） ==")
    source_sync.fandom_service.build_search_candidates = lambda q, limit=4: [
        {"title": "Test Group", "meta": "出道 2020"}
    ]
    source_sync.wiki_service.search_entities = lambda q, limit=7: [
        {"qid": "Q999", "label": "Test Group", "description": "group"}
    ]
    sc = search_candidates("Test Group")
    check("两类来源候选合并", {i["source_type"] for i in sc["items"]} == {"fandom", "wikidata"})
    check("fandom 候选带 URL", any(i["url"].endswith("/wiki/Test_Group") for i in sc["items"]))

finally:
    source_sync.fandom_service.fetch_detail = original_fandom
    source_sync.wiki_service.get_entity_info = original_wiki


# ============ 7. 生长引擎 ============
print("== 7. 生长引擎 ==")
import datetime as _dt  # noqa: E402
from app.models.membership import GroupMembership  # noqa: E402
from app.services.source_sync import apply_growth, preview_sources  # noqa: E402

# 造一个空组合（无成员无专辑）
g_grow = Group(name="Grow Group")
db.add(g_grow)
db.commit()

# 多来源：fandom 给成员 A/B（无日期）+ 前成员 C；wiki 给成员 A（带进出日期，韩文名）+ 成员 D
source_sync.fandom_service.fetch_detail = lambda title: {
    "biography": "bio", "thumb": None, "source": "K-pop Fandom",
    "fandom_fields": {
        "debut_date": "2021-05-01",
        "company": "Grow Agency",
        "members": [{"name": "Member A"}, {"name": "Member B", "korean_name": "비"}],
        "former_members": [{"name": "Member C"}],
    },
}
source_sync.wiki_service.get_entity_info = lambda qid, kind: {
    "qid": "Q1", "label": "Grow Group", "label_ko": None, "label_zh": None,
    "description": None, "image": None, "wikipedia_extract": "wiki bio",
    "inception": "2021-05-01",
    "members": [
        {"qid": "Q10", "name": "Member A", "korean_name": "에이", "join_date": "2021-05-01", "leave_date": None},
        {"qid": "Q11", "name": "Member D", "korean_name": "디", "join_date": "2021-06-01", "leave_date": "2022-06-01"},
    ],
}
pv = preview_sources(db, g_grow.uid, [
    "https://kpop.fandom.com/wiki/Grow_Group",
    "https://www.wikidata.org/wiki/Q1",
])
mp = {p["name"]: p for p in pv["member_proposals"]}
check("成员 A 双来源合并为一条提案", set(mp["Member A"]["sources"]) == {"K-pop Fandom", "Wikidata"}, mp["Member A"]["sources"])
check("成员 A 日期来自 wikidata", mp["Member A"]["join_date"] == "2021-05-01")
check("成员 B 为 create 且带韩文名", mp["Member B"]["action"] == "create" and mp["Member B"]["korean_name"] == "비")
check("成员 C 来自前成员名单 → Former", mp["Member C"]["action"] == "create" and mp["Member C"]["status"] == "Former")
check("成员 D 带退出日期 → Former", mp["Member D"]["status"] == "Former" and mp["Member D"]["leave_date"] == "2022-06-01")
check("字段 diff：fandom/wiki 简介冲突 → 2 条 conflict", 
      len([i for i in pv["items"] if i["field"] == "description" and i["conflict"]]) == 2,
      [i["sources"] for i in pv["items"] if i["field"] == "description"])
check("一致字段合并且标注双来源", any(
    i["field"] == "debut_date" and not i["conflict"] and len(i["sources"]) == 2
    for i in pv["items"]
))

# 专辑提案（mock itunes/deezer）
from app.services import album_external_service as aes  # noqa: E402
aes.search_itunes_albums = lambda q: [
    {"external_id": "itunes:us:1", "name": "Grow Album", "artist": "Grow Group",
     "cover_url": None, "year": "2021", "source": "itunes", "track_count": 3},
    {"external_id": "itunes:us:2", "name": "Other Artist Album", "artist": "Someone Else",
     "cover_url": None, "year": "2021", "source": "itunes", "track_count": 5},
]
aes.search_deezer_albums = lambda q: [
    {"external_id": "deezer:9", "name": "Grow Album (Deluxe)", "artist": "Grow Group",
     "cover_url": None, "year": "2021", "source": "deezer", "track_count": 8},
]
pv2 = preview_sources(db, g_grow.uid, ["https://kpop.fandom.com/wiki/Grow_Group"])
al = {a["name"]: a for a in pv2["album_proposals"]}
check("本组合专辑进提案", al.get("Grow Album", {}).get("action") == "create")
check("他人专辑被过滤", "Other Artist Album" not in al)
check("不同版本专辑各自成提案", al.get("Grow Album (Deluxe)", {}).get("action") == "create")

# apply：字段 + 成员 + 专辑（曲目 mock）
aes.get_itunes_tracklist = lambda external_id: {
    "external_id": external_id, "name": "Grow Album", "tracks": [
        {"disc_number": 1, "track_number": 1, "name": "Track One", "duration": 100},
        {"disc_number": 1, "track_number": 2, "name": "Track Two", "duration": 110},
    ],
}
res = apply_growth(
    db, g_grow.uid,
    fields={"debut_date": "2021-05-01"},
    members=[p for p in pv["member_proposals"] if p["action"] != "exists"],
    albums=[a for a in pv2["album_proposals"] if a["name"] == "Grow Album"],
    source_urls=["https://kpop.fandom.com/wiki/Grow_Group"],
)
db.expire_all()
g_check = db.query(Group).filter(Group.uid == g_grow.uid).one()
from app.models.artist import Artist  # noqa: E402
from app.models.album import Album, AlbumTrack  # noqa: E402
from app.models.song import Song  # noqa: E402
members_now = db.query(GroupMembership).filter(GroupMembership.group_id == g_check.id).all()
albums_now = db.query(Album).filter(Album.release_artist_id == g_check.id, Album.release_artist_type == "group").all()
songs_now = db.query(Song).filter(Song.release_artist_id == g_check.id, Song.release_artist_type == "group").all()
artists_now = db.query(Artist).filter(Artist.name.like("Member %")).all()
check("出道日写入", g_check.debut_date is not None and g_check.debut_date.isoformat() == "2021-05-01")
check("4 名成员关系建立", len(members_now) == 4, len(members_now))
check("4 名艺人为新建", len(artists_now) == 4)
check("1 张专辑建立", len(albums_now) == 1)
check("2 首歌曲建立 + 2 条曲目", len(songs_now) == 2 and res["tracks_created"] == 2, (len(songs_now), res["tracks_created"]))
check("来源记录", any("fandom.com" in (l.get("url") or "") for l in g_check.external_links if isinstance(l, dict)))

# 重复 apply 不重复创建
res2 = apply_growth(
    db, g_grow.uid,
    fields={}, members=pv["member_proposals"], albums=[], source_urls=[],
)
check("重复合并不重建成员", res2["members_skipped"] >= 4, res2)


# ============ 8. 成员名归并 ============
print("== 8. 成员名归并 ==")
g_dedup = Group(name="Dedup Group")
db.add(g_dedup)
db.commit()

source_sync.fandom_service.fetch_detail = lambda title: {
    "biography": None, "thumb": None, "source": "K-pop Fandom",
    "fandom_fields": {
        "members": [
            {"name": "Kim YooYeon"},
            {"name": "Yoon SeoYeon"},
            {"name": "Karina"},
        ],
        "former_members": [],
    },
}
source_sync.wiki_service.get_entity_info = lambda qid, kind: {
    "qid": "Q5", "label": "Dedup Group", "label_ko": None, "label_zh": None,
    "description": None, "image": None, "wikipedia_extract": None,
    "inception": None,
    "members": [
        {"qid": "Q51", "name": "Kim YuYeon", "korean_name": "김유연", "join_date": None, "leave_date": None},
        {"qid": "Q52", "name": "SeoYeon Yoon", "korean_name": "윤서연", "join_date": None, "leave_date": None},
        {"qid": "Q53", "name": "Yu JiMin", "korean_name": "유지민", "join_date": None, "leave_date": None},
    ],
}
pv3 = preview_sources(db, g_dedup.uid, [
    "https://kpop.fandom.com/wiki/Dedup_Group",
    "https://www.wikidata.org/wiki/Q5",
])
names = sorted(m["name"] for m in pv3["member_proposals"])
yooyeon = next(m for m in pv3["member_proposals"] if "YuYeon" in m["name"] or "YooYeon" in m["name"])
check("拼法差异合并（YooYeon/YuYeon 同姓）", yooyeon["name"] in ("Kim YooYeon", "Kim YuYeon") and len(yooyeon["sources"]) == 2, yooyeon["sources"])
seoyeon = next(m for m in pv3["member_proposals"] if "eoYeon" in m["name"])
check("词序差异合并（Yoon SeoYeon / SeoYeon Yoon）", len(seoyeon["sources"]) == 2)
check("Karina 与 Yu JiMin 不被误合并", "Karina" in names and not any(
    m["name"] == "Karina" and len(m["sources"]) > 1 for m in pv3["member_proposals"]
))
check("韩文名随合并带入", yooyeon.get("korean_name") == "김유연")
check("总人数正确（5 条来源记录归并为 4 人）", len(pv3["member_proposals"]) == 4, names)


# ============ 9. 驼峰/艺名-本名归并 ============
print("== 9. 驼峰与艺名本名归并 ==")
g_camel = Group(name="Camel Group")
db.add(g_camel)
db.commit()
source_sync.fandom_service.fetch_detail = lambda title: {
    "biography": None, "thumb": None, "source": "K-pop Fandom",
    "fandom_fields": {
        "members": [{"name": "ChaeYeon"}, {"name": "SoHyun"}, {"name": "Seo DaHyun"}],
        "former_members": [],
    },
}
source_sync.wiki_service.get_entity_info = lambda qid, kind: {
    "qid": "Q9", "label": "Camel Group", "label_ko": None, "label_zh": None,
    "description": None, "image": None, "wikipedia_extract": None, "inception": None,
    "members": [
        {"qid": "Q91", "name": "Kim Chae-yeon", "korean_name": "김채연", "join_date": None, "leave_date": None},
        {"qid": "Q92", "name": "Park So-hyun", "korean_name": "박소현", "join_date": None, "leave_date": None},
        {"qid": "Q93", "name": "Seo Da-hyun", "korean_name": "서다현", "join_date": None, "leave_date": None},
    ],
}
pv4 = preview_sources(db, g_camel.uid, [
    "https://kpop.fandom.com/wiki/Camel_Group",
    "https://www.wikidata.org/wiki/Q9",
])
check("3 对艺名/本名合并为 3 人", len(pv4["member_proposals"]) == 3, [m["name"] for m in pv4["member_proposals"]])
chaeyeon = next(m for m in pv4["member_proposals"] if "Chae" in m["name"] or "chae" in m["name"])
check("合并后标注双来源", len(chaeyeon["sources"]) == 2, chaeyeon["sources"])
check("韩文名带入", chaeyeon.get("korean_name") in ("김채연", "박소현", "서다현"))


# ============ 10. 小分队生长 + 成员档案 ============
print("== 10. 小分队生长 + 成员档案 ==")
g_su = Group(name="SU Group")
db.add(g_su)
db.commit()

source_sync.fandom_service.fetch_detail = lambda title: {
    "biography": None, "thumb": None, "source": "K-pop Fandom",
    "fandom_fields": {"members": [{"name": "SU Main"}], "former_members": []},
}
source_sync.wiki_service.get_entity_info = lambda qid, kind: {
    "qid": "Q77", "label": "SU Group", "label_ko": None, "label_zh": None,
    "description": None, "image": None, "wikipedia_extract": None,
    "inception": "2023-01-01",
    "members": [
        {"qid": "Q80", "name": "SU Main", "korean_name": None,
         "birth_date": "2000-03-01T00:00:00Z", "description": "main vocalist of SU Group",
         "join_date": "2023-01-01", "leave_date": None},
    ],
}
source_sync.wiki_service.get_sub_units = lambda qid: [
    {"qid": "Q81", "name": "SU Sub Unit", "members": ["SU Main", "SU Newbie"]},
]
pv5 = preview_sources(db, g_su.uid, ["https://www.wikidata.org/wiki/Q77"])
check("小分队提案生成", any(
    u["name"] == "SU Sub Unit" and u["action"] == "create" and set(u["member_names"]) == {"SU Main", "SU Newbie"}
    for u in pv5["subunit_proposals"]
), pv5["subunit_proposals"])
mem_main = next(m for m in pv5["member_proposals"] if m["name"] == "SU Main")
check("成员提案带档案字段（生日+描述）", 
      mem_main["artist_fields"].get("birth_date") == "2000-03-01"
      and mem_main["artist_fields"].get("description") == "main vocalist of SU Group",
      mem_main["artist_fields"])

res3 = apply_growth(
    db, g_su.uid,
    fields={},
    members=[m for m in pv5["member_proposals"] if m["action"] != "exists"],
    albums=[],
    source_urls=[],
    subunits=[u for u in pv5["subunit_proposals"] if u["action"] == "create"],
)
db.expire_all()
g_check2 = db.query(Group).filter(Group.uid == g_su.uid).one()
from app.models.group import Group as G2  # noqa: E402
subs = db.query(G2).filter(G2.parent_group_id == g_check2.id, G2.deleted_at.is_(None)).all()
check("小分队已创建且指向完整体", len(subs) == 1 and subs[0].group_type == "Sub-unit", [(x.name, x.group_type) for x in subs])
su_members = db.query(GroupMembership).filter(GroupMembership.group_id == subs[0].id).all()
check("小分队成员 2 人（共享成员挂接 + 新成员创建）", len(su_members) == 2, len(su_members))
su_main_artist = next(a for a in db.query(Artist).filter(Artist.name == "SU Main").all())
check("成员生日写入艺人档案", su_main_artist.birth_date is not None and su_main_artist.birth_date.isoformat() == "2000-03-01")
check("成员描述写入艺人档案", su_main_artist.description == "main vocalist of SU Group")
check("计数正确", res3["subunits_created"] == 1 and res3["members_created"] == 2, res3)

db.close()
print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
