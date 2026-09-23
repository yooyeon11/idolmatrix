"""入库匹配档：普通保持宽匹配，严格挡住短名误挂与无关艺人。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="ingest_match_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.membership import GroupMembership  # noqa: E402
from app.models.song import Song  # noqa: E402
from app.services.ai_context import build_library_candidates  # noqa: E402
from app.services.app_settings import read_all, read_ingest_match_mode, write_sections  # noqa: E402
from app.services.incoming_match_service import (  # noqa: E402
    _strip_scan_noise,
    build_match_hints,
)
from app.services.name_match import related_artist_ids_for_groups  # noqa: E402

settings.ensure_directories()
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


def names(rows):
    return {r["name"] for r in rows}


def song_names(hints):
    return {t["song_name"] for t in hints["tracks"]}


db = SessionLocal()

ive = Group(name="IVE", korean_name="아이브")
fromis = Group(name="fromis_9", korean_name="프로미스나인")
loona = Group(name="LOONA")
db.add_all([ive, fromis, loona])
db.flush()

yves = Artist(name="Yves", korean_name="이브")
jiheon = Artist(name="Baek Jiheon", korean_name="백지헌")
bae = Artist(name="Bae")
db.add_all([yves, jiheon, bae])
db.flush()

db.add_all(
    [
        GroupMembership(group_id=loona.id, artist_id=yves.id, status="Active"),
        GroupMembership(group_id=fromis.id, artist_id=jiheon.id, status="Active"),
    ]
)
# NMIXX Bae 与 fromis_9 无关，不建 membership
db.commit()
db.refresh(ive)
db.refresh(fromis)
db.refresh(loona)
db.refresh(yves)
db.refresh(jiheon)
db.refresh(bae)

print("== 1. 本地匹配 IVE / Yves ==")
ive_title = {"title": "IVE 아이브 ATTITUDE MV"}
normal = build_match_hints(db, "ive.mp4", ive_title, match_mode="normal")
strict = build_match_hints(db, "ive.mp4", ive_title, match_mode="strict")
check("普通档认出 IVE", "IVE" in names(normal["groups"]), names(normal["groups"]))
check("普通档误挂 Yves", "Yves" in names(normal["artists"]), names(normal["artists"]))
check("严格档认出 IVE", "IVE" in names(strict["groups"]), names(strict["groups"]))
check("严格档不挂 Yves", "Yves" not in names(strict["artists"]), names(strict["artists"]))
check("严格档回写 match_mode", strict["match_mode"] == "strict")

print("== 2. 本地匹配 fromis_9 / Bae ==")
fromis_title = {"title": "fromis_9 Baek Jiheon FanCam"}
n2 = build_match_hints(db, "jiheon.mp4", fromis_title, match_mode="normal")
s2 = build_match_hints(db, "jiheon.mp4", fromis_title, match_mode="strict")
check("普通档认出 fromis_9", "fromis_9" in names(n2["groups"]))
check("严格档认出 fromis_9", "fromis_9" in names(s2["groups"]))
check("严格档挂上白智宪", "Baek Jiheon" in names(s2["artists"]), names(s2["artists"]))
check("严格档不挂 Bae", "Bae" not in names(s2["artists"]), names(s2["artists"]))
check("普通档 Bae 会嵌进 Baek", "Bae" in names(n2["artists"]), names(n2["artists"]))

print("== 2b. 合作舞台：标题里有无关艺人整词 ==")
collab = build_match_hints(
    db, "collab.mp4", {"title": "fromis_9 with Bae special stage"}, match_mode="strict"
)
check("合作舞台仍认 fromis_9", "fromis_9" in names(collab["groups"]))
check("合作舞台不自动挂 Bae", "Bae" not in names(collab["artists"]), names(collab["artists"]))
check(
    "合作舞台有未关联提示",
    any("Bae" in n and "无关" in n for n in collab["notices"]),
    collab["notices"],
)

print("== 3. 独立短名在严格档仍可命中 ==")
s3 = build_match_hints(db, "yves.mp4", {"title": "Yves 이브 Solo Stage"}, match_mode="strict")
check("严格档独立 이브 仍认 Yves", "Yves" in names(s3["artists"]), names(s3["artists"]))

print("== 4. 关系闸含母队成员 ==")
sub = Group(name="fromis_9 sub", parent_group_id=fromis.id, group_type="Sub-unit")
db.add(sub)
db.commit()
db.refresh(sub)
related = related_artist_ids_for_groups(db, [sub.id])
check("小分队闸包含母队白智宪", jiheon.id in related, related)
check("小分队闸不含 Bae", bae.id not in related)

print("== 5. AI 上下文切词污染 ==")
def _has_artist(lines, name):
    return any(line == name or line.startswith(name + " /") for line in (lines or []))


ctx_n = build_library_candidates(db, "fromis_9 Baek Jiheon FanCam", match_mode="normal")
ctx_s = build_library_candidates(db, "fromis_9 Baek Jiheon FanCam", match_mode="strict")
check(
    "普通档切词会把 Bae 送进 prompt",
    _has_artist(ctx_n.get("matched_artists"), "Bae"),
    ctx_n.get("matched_artists"),
)
check(
    "严格档 prompt 不含 Bae",
    not _has_artist(ctx_s.get("matched_artists"), "Bae"),
    ctx_s.get("matched_artists"),
)
check(
    "严格档 prompt 可含白智宪",
    _has_artist(ctx_s.get("matched_artists"), "Baek Jiheon"),
    ctx_s.get("matched_artists"),
)

print("== 6. 剥日期 / 画质标签 ==")
check("YYMMDD", _strip_scan_noise("241215 tripleS - 24") == "tripleS - 24")
check("YYYYMMDD", _strip_scan_noise("20241215 IRENE") == "IRENE")
check("点分日期", _strip_scan_noise("24.12.15 tripleS concert") == "tripleS concert")
check("中文日期", _strip_scan_noise("24年12月15日 舞台") == "舞台")
check("画质+日期", _strip_scan_noise("[4K] 260301 24 tripleS Fancam") == "24 tripleS Fancam")
check("独立 24 保留", _strip_scan_noise("24 triples XXXXXX") == "24 triples XXXXXX")
check("日期区间", _strip_scan_noise("260404-0405 tripleS") == "tripleS")

print("== 7. 发布者不参与艺人匹配 ==")
momo = Artist(name="Momo", stage_name="Momo", chinese_name="平井桃")
db.add(momo)
db.commit()
db.refresh(momo)
momo_up = build_match_hints(
    db,
    "ive.mp4",
    {"title": "IVE ATTITUDE MV", "uploader": "Wink眨宝 momo", "channel": "Momo"},
    match_mode="strict",
)
check("发布者 momo 不挂艺人", "Momo" not in names(momo_up["artists"]), names(momo_up["artists"]))
check("频道 Momo 不挂艺人", "Momo" not in names(momo_up["artists"]))
check("标题仍认 IVE", "IVE" in names(momo_up["groups"]), names(momo_up["groups"]))
momo_title = build_match_hints(
    db, "momo.mp4", {"title": "Momo - Fancy Performance"}, match_mode="strict"
)
check("标题里的 Momo 仍挂", "Momo" in names(momo_title["artists"]), names(momo_title["artists"]))

print("== 8. 歌名 24 vs 日期 ==")
triples = Group(name="tripleS", korean_name="트리플에스")
db.add(triples)
db.flush()
s24 = Song(name="24", release_artist_type="group", release_artist_id=triples.id)
gnd = Song(name="Girls Never Die", release_artist_type="group", release_artist_id=triples.id)
db.add_all([s24, gnd])
db.commit()
db.refresh(triples)

def _song_case(title, mode="strict"):
    return build_match_hints(db, "clip.mp4", {"title": title}, match_mode=mode)

h_date = _song_case("241215 tripleS - Girls Never Die")
check("日期前缀不挂 24", "24" not in song_names(h_date), song_names(h_date))
check("日期前缀仍挂 GND", "Girls Never Die" in song_names(h_date), song_names(h_date))

h_end = _song_case("241215 tripleS - 24")
check("剥日期后末尾 24 自动挂", song_names(h_end) == {"24"}, song_names(h_end))

h_lead = _song_case("24 triples XXXXXX")
check("开头独立 24 + 团名自动挂", song_names(h_lead) == {"24"}, song_names(h_lead))
check("开头 24 认出 tripleS", "tripleS" in names(h_lead["groups"]), names(h_lead["groups"]))

h_year = _song_case("24 tripleS Girls Never Die")
check("开头 24 当年份，只挂 GND", song_names(h_year) == {"Girls Never Die"}, song_names(h_year))

h_tag = _song_case("[4K] 260301 24 tripleS Kim YooYeon Fancam")
check("画质+日期后的开头 24 仍挂", song_names(h_tag) == {"24"}, song_names(h_tag))

h_quote = _song_case("'24' tripleS Official Stage")
check("引号里的 24 自动挂", song_names(h_quote) == {"24"}, song_names(h_quote))

h_plain = _song_case("24 triples fancam", mode="normal")
check("普通档开头 24 也挂", song_names(h_plain) == {"24"}, song_names(h_plain))

h_no_group = _song_case("24 hours highlight reel")
check("没有团名时不靠 24 全库乱挂", "24" not in song_names(h_no_group), song_names(h_no_group))

h_desc = build_match_hints(
    db,
    "clip.mp4",
    {"title": "tripleS Official Stage", "description": "tonight performing 24 on stage"},
    match_mode="strict",
)
check("简介里的 24 不自动填", song_names(h_desc) == set(), song_names(h_desc))
check(
    "简介命中只提示",
    any("24" in n and "简介" in n for n in h_desc["notices"]),
    h_desc["notices"],
)

print("== 9. 别名/粉丝名不参与匹配 ==")
# 线上真实事故：aespa aliases=['에스파','aespa','æspa','艾斯帕','MY']（MY=粉丝名），
# "OH MY GIRL ARIN ..." 里的独立单词 MY 整词命中 → 误挂 aespa。
aespa = Group(
    name="aespa", korean_name="에스파", aliases=["에스파", "æspa", "艾斯帕", "MY"]
)
db.add(aespa)
db.commit()
db.refresh(aespa)
h_fandom = build_match_hints(
    db,
    "ohmygirl_arin_fancam.mp4",
    {"title": "OH MY GIRL ARIN 4K FANCAM 241215"},
    match_mode="strict",
)
check(
    "粉丝名 MY 不再把标题挂到 aespa",
    "aespa" not in names(h_fandom["groups"]),
    names(h_fandom["groups"]),
)
# 歌曲别名同理：歌的 aliases 不再参与标题扫描
song_alias = Song(
    name="Drama", release_artist_type="group", release_artist_id=aespa.id,
    aliases=["드라마", "Armageddon"],
)
db.add(song_alias)
db.commit()
h_song_alias = build_match_hints(
    db, "drama_mv.mp4", {"title": "aespa 에스파 Drama Official MV"}, match_mode="strict"
)
check("正式歌名仍命中", "Drama" in song_names(h_song_alias), song_names(h_song_alias))
h_alias_only = build_match_hints(
    db, "clip.mp4", {"title": "Armageddon stage fancam 4K"}, match_mode="strict"
)
check("歌名别名不自动挂", song_names(h_alias_only) == set(), song_names(h_alias_only))

print("== 10. 设置读写 ==")
empty_mode = read_ingest_match_mode(db)
# 「内容与匹配」栏已下线（v3.2.21）：ingest 分区不在白名单，档位恒为严格
check("未配置时读严格", empty_mode == "strict")
saved = write_sections(db, {"ingest": {"match_mode": "normal"}})
check(
    "写入已下线分区不生效、读取仍严格",
    not hasattr(saved, "ingest") and read_ingest_match_mode(db) == "strict",
)
again = read_all(db)
check("重新读取仍严格", not hasattr(again, "ingest") and read_ingest_match_mode(db) == "strict")

db.close()
print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
