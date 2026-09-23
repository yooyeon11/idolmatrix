"""数据体检引擎测试：造坏数据，验证各项检查能抓到、好数据不误报。"""
import os
import sys
import tempfile
import datetime
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="data_health_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.album import Album, AlbumTrack  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.company import Company, GroupCompanyRelation  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.membership import GroupMembership  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.models.song import Song, SongArtistRelation  # noqa: E402
from app.services.data_health import run_health_report  # noqa: E402

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


def issues_of(report, key=None, severity=None):
    return [
        i
        for i in report["issues"]
        if (key is None or i["check"] == key)
        and (severity is None or i["severity"] == severity)
    ]


db = SessionLocal()

# ============ 场景 1：空库 ============
report = run_health_report(db)
print("== 1. 空库不误报 ==")
check("空库分数为 100", report["score"] == 100, report["score"])
check("空库无问题", report["total_issues"] == 0)
check("空库统计全 0", sum(report["entity_stats"].values()) == 0)

# ============ 场景 2：健康的小库 ============
print("== 2. 健康数据不误报 ==")
g1 = Group(
    name="tripleS",
    debut_date=date(2022, 10, 13),
    group_type="Girl Group",
    external_links=[{"url": "https://kpop.fandom.com/wiki/tripleS"}],
)
a1 = Artist(name="Yoon SeoYeon", birth_date=date(2003, 4, 29))
a2 = Artist(name="Jeong HyeRin", birth_date=date(2003, 4, 14))
c1 = Company(name="MODHAUS")
db.add_all([g1, a1, a2, c1])
db.flush()
db.add_all(
    [
        GroupMembership(
            group_id=g1.id, artist_id=a1.id, join_date=date(2022, 7, 8), status="Active"
        ),
        GroupMembership(
            group_id=g1.id, artist_id=a2.id, join_date=date(2022, 7, 8), status="Active"
        ),
        GroupCompanyRelation(group_id=g1.id, company_id=c1.id, status="Active"),
    ]
)
alb1 = Album(name="ASSEMBLE", release_date=date(2023, 2, 13), release_artist_type="group", release_artist_id=g1.id)
db.add(alb1)
db.flush()
s1 = Song(name="Beam", release_artist_type="group", release_artist_id=g1.id)
db.add(s1)
db.flush()
db.add(AlbumTrack(album_id=alb1.id, song_id=s1.id, track_number=1))
mv1 = MusicVideo(
    name="Beam MV",
    file_hash="hash-beam-1",
    ingestion_status="library",
    video_type="OfficialMV",
)
db.add(mv1)
db.flush()
from app.models.music_video import music_video_groups, music_video_songs  # noqa: E402

db.execute(
    music_video_groups.insert().values(music_video_id=mv1.id, group_id=g1.id)
)
db.execute(music_video_songs.insert().values(music_video_id=mv1.id, song_id=s1.id))
db.commit()

report = run_health_report(db)
check("健康库分数 100", report["score"] == 100, report["score"])
check("健康库零问题", report["total_issues"] == 0, report["total_issues"])
check("实体统计正确", report["entity_stats"]["groups"] == 1 and report["entity_stats"]["artists"] == 2)

# ============ 场景 3：各类坏数据 ============
print("== 3. 坏数据全部被抓到 ==")
# 3.1 日期矛盾 + 公司关系日期矛盾
a_bad = Artist(name="DateConflict", birth_date=date(2000, 1, 1))
db.add(a_bad)
db.flush()
db.add(
    GroupMembership(
        group_id=g1.id, artist_id=a_bad.id,
        join_date=date(2023, 1, 1), leave_date=date(2022, 1, 1), status="Former",
    )
)
c2 = Company(name="BadDates Inc.")
db.add(c2)
db.flush()
db.add(
    GroupCompanyRelation(
        group_id=g1.id, company_id=c2.id,
        start_date=date(2024, 1, 1), end_date=date(2023, 1, 1), status="Former",
    )
)
# 3.2 同名艺人 / 同主体同名歌曲
db.add(Artist(name="Yoon SeoYeon", birth_date=date(2003, 4, 29)))
db.flush()
s_dup = Song(name="Beam", release_artist_type="group", release_artist_id=g1.id)
db.add(s_dup)
# 3.3 孤立歌曲（无演唱者/专辑/MV）
s_orphan = Song(name="Orphan Song")
db.add(s_orphan)
# 3.4 缺演唱者但被专辑收录（warning 而非 error）
s_noperf = Song(name="NoPerformer Song")
db.add(s_noperf)
db.flush()
db.add(AlbumTrack(album_id=alb1.id, song_id=s_noperf.id, track_number=2))
# 3.5 悬空：软删除艺人但保留成员关系
a_ghost = Artist(name="Ghost Member", birth_date=date(1999, 9, 9))
db.add(a_ghost)
db.flush()
db.add(
    GroupMembership(
        group_id=g1.id, artist_id=a_ghost.id, join_date=date(2022, 1, 1), status="Active"
    )
)
db.flush()
a_ghost.deleted_at = a_ghost.deleted_at or datetime.datetime.utcnow()
# 3.6 缺失日期：组合缺出道日 / 专辑缺发行日 / 艺人缺生日
g_nodate = Group(name="No Debut Group")
alb_nodate = Album(name="No Date Album", release_artist_type="group", release_artist_id=g1.id)
a_nobirth = Artist(name="NoBirthday")
db.add_all([g_nodate, alb_nodate, a_nobirth])
# 3.7 孤立公司 + 无成员组合 + 无曲目专辑
c_orphan = Company(name="Orphan Label")
g_empty = Group(name="Empty Group", debut_date=date(2020, 1, 1))
alb_empty = Album(name="Empty Album", release_date=date(2021, 1, 1))
db.add_all([c_orphan, g_empty, alb_empty])
db.commit()

report = run_health_report(db)
found = {i["check"] for i in report["issues"]}
check("成员日期矛盾被抓到", "membership_dates" in found)
check("公司关系日期矛盾被抓到", "company_relation_dates" in found)
check("同名艺人被抓到", "duplicate_artists" in found)
check("同主体同名歌曲被抓到", "duplicate_songs" in found)
check("孤立歌曲被抓到（error）", any(
    i["check"] == "song_orphan" and i["entity_name"] == "Orphan Song"
    for i in report["issues"]
))
check("缺演唱者歌（warning，不误报为孤立）", any(
    i["check"] == "song_no_performer" and i["entity_name"] == "NoPerformer Song"
    for i in report["issues"]
))
check("孤立歌未同时报缺演唱者", not any(
    i["entity_name"] == "Orphan Song" and i["check"] == "song_no_performer"
    for i in report["issues"]
))
check("悬空成员关系被抓到", any(
    i["check"] == "membership_dangling" and "Ghost Member" in i["title"]
    for i in report["issues"]
))
check("组合缺出道日被抓到", any(
    i["check"] == "missing_dates" and i["entity_name"] == "No Debut Group"
    for i in report["issues"]
))
check("专辑缺发行日被抓到", any(
    i["check"] == "missing_dates" and i["entity_name"] == "No Date Album"
    for i in report["issues"]
))
check("艺人缺生日被抓到（hint）", any(
    i["check"] == "missing_dates" and i["entity_name"] == "NoBirthday"
    for i in report["issues"]
))
check("软删除艺人不被体检扫描", not any(
    i["entity_name"] == "Ghost Member" for i in report["issues"]
))
check("孤立公司被抓到（hint）", any(
    i["check"] == "company_no_relations" and i["entity_name"] == "Orphan Label"
    for i in report["issues"]
))
check("无成员组合被抓到", any(
    i["check"] == "group_no_members" and i["entity_name"] == "Empty Group"
    for i in report["issues"]
))
check("无曲目专辑被抓到（hint）", any(
    i["check"] == "album_no_tracks" and i["entity_name"] == "Empty Album"
    for i in report["issues"]
))
check("健康库的组合未被误报", not any(
    i["check"] == "group_no_members" and i["entity_name"] == "tripleS"
    for i in report["issues"]
))

# ============ 场景 4：分数与排序 ============
print("== 4. 分数与排序 ==")
check("分数下降且在 0-100", 0 < report["score"] < 100, report["score"])
check("error 数 >= 5", report["counts"]["error"] >= 5, report["counts"])
order = {"error": 0, "warning": 1, "hint": 2}
seq = [order[i["severity"]] for i in report["issues"]]
check("问题按严重度排序", seq == sorted(seq))
check("每条问题都有 check/entity_type/entity_id", all(
    i["check"] and i["entity_type"] and i["entity_id"] for i in report["issues"]
))
check("艺人问题带 deep_link", any(
    i["deep_link"] and i["deep_link"].startswith("/artists/")
    for i in report["issues"]
))
check("checks 清单完整", len(report["checks"]) >= 15)

# ============ 场景 5：修复后恢复 ============
print("== 5. 修复后分数回升 ==")
# 删掉孤立歌曲
orphan = db.query(Song).filter(Song.name == "Orphan Song").one()
db.delete(orphan)
db.commit()
report2 = run_health_report(db)
check("孤立歌修复后不再报", not any(
    i["check"] == "song_orphan" for i in report2["issues"]
))
check("分数回升", report2["score"] > report["score"], f"{report['score']} -> {report2['score']}")

# ============ 场景 6：路径与入库规则不符 ============
print("== 6. 路径检测 ==")
from app.models.music_video import music_video_groups, music_video_songs  # noqa: E402

_lib = Path(os.environ["STORAGE_LIBRARY_DIR"])
# 磁盘上真实目录名是 TripleS（大写 S），规则推导出 tripleS → 大小写归并后视为一致
(_lib / "TripleS" / "MV").mkdir(parents=True, exist_ok=True)

mv_path_ok = MusicVideo(
    name="Path Ok", file_hash="hash-path-ok", ingestion_status="library",
    video_type="OfficialMV", file_path="TripleS/Beam/MV/Path Ok.mp4",
)
mv_path_bad = MusicVideo(
    name="Path Bad", file_hash="hash-path-bad", ingestion_status="library",
    video_type="OfficialMV", file_path="tripleS/Wrong/Path Bad.mp4",
)
mv_path_abs = MusicVideo(
    name="Path Abs", file_hash="hash-path-abs", ingestion_status="library",
    video_type="OfficialMV", file_path="/somewhere/incoming/Path Abs.mp4",
)
mv_path_none = MusicVideo(
    name="Path None", file_hash="hash-path-none", ingestion_status="library",
    video_type="OfficialMV",
)
mv_path_incoming = MusicVideo(
    name="Path Incoming", file_hash="hash-path-inc", ingestion_status="incoming",
    video_type="OfficialMV", file_path="tripleS/Wrong/Path Incoming.mp4",
)
db.add_all([mv_path_ok, mv_path_bad, mv_path_abs, mv_path_none, mv_path_incoming])
db.flush()
for _mv in (mv_path_ok, mv_path_bad, mv_path_abs, mv_path_none, mv_path_incoming):
    db.execute(music_video_groups.insert().values(music_video_id=_mv.id, group_id=g1.id))
    db.execute(music_video_songs.insert().values(music_video_id=_mv.id, song_id=s1.id))
db.commit()

report = run_health_report(db)
path_issues = [i for i in report["issues"] if i["check"] == "path_mismatch"]
check("路径不符被抓到", len(path_issues) == 1, [i["entity_name"] for i in path_issues])
check("路径不符指向 Path Bad", path_issues and path_issues[0]["entity_name"] == "Path Bad")
check(
    "detail 同时给出实际与规则路径",
    path_issues
    and "tripleS/Wrong" in path_issues[0]["detail"]
    and "TripleS/Beam/MV" in path_issues[0]["detail"],
    path_issues[0]["detail"] if path_issues else "",
)
check("路径不符是 warning", path_issues and path_issues[0]["severity"] == "warning")
check(
    "路径不符带视频深链",
    path_issues and (path_issues[0]["deep_link"] or "").startswith("/videos/"),
    path_issues[0]["deep_link"] if path_issues else "",
)
check("大小写差异不误报（TripleS vs tripleS）", not any(
    i["entity_name"] == "Path Ok" and i["check"] == "path_mismatch"
    for i in report["issues"]
))
check("绝对路径（未移动文件入库）不报路径", not any(
    i["entity_name"] == "Path Abs" and i["check"] == "path_mismatch"
    for i in report["issues"]
))
check("没有记录路径不报路径", not any(
    i["entity_name"] == "Path None" and i["check"] == "path_mismatch"
    for i in report["issues"]
))
check("待整理（incoming）不参与路径体检", not any(
    i["entity_name"] == "Path Incoming" and i["check"] == "path_mismatch"
    for i in report["issues"]
))

# ============ 场景 7：上传日期 vs 表演日期跨度 ============
print("== 7. 日期跨度检测 ==")
mv_gap = MusicVideo(
    name="Gap Video", file_hash="hash-gap", ingestion_status="library",
    video_type="OfficialMV",
    published_date=date(2024, 11, 25), performance_date=date(2024, 5, 1),
)
mv_gap60 = MusicVideo(
    name="Gap 60", file_hash="hash-gap60", ingestion_status="library",
    video_type="OfficialMV",
    published_date=date(2024, 6, 1), performance_date=date(2024, 4, 2),
)
mv_gap59 = MusicVideo(
    name="Gap 59", file_hash="hash-gap59", ingestion_status="library",
    video_type="OfficialMV",
    published_date=date(2024, 6, 1), performance_date=date(2024, 4, 3),
)
mv_gap_rev = MusicVideo(
    name="Gap Reversed", file_hash="hash-gaprev", ingestion_status="library",
    video_type="OfficialMV",
    published_date=date(2024, 1, 1), performance_date=date(2024, 5, 1),
)
mv_gap_same = MusicVideo(
    name="Gap Same", file_hash="hash-gapsame", ingestion_status="library",
    video_type="OfficialMV",
    published_date=date(2024, 5, 1), performance_date=date(2024, 5, 1),
)
mv_gap_one = MusicVideo(
    name="Gap One Side", file_hash="hash-gapone", ingestion_status="library",
    video_type="OfficialMV", published_date=date(2024, 5, 1),
)
db.add_all([mv_gap, mv_gap60, mv_gap59, mv_gap_rev, mv_gap_same, mv_gap_one])
db.flush()
for _mv in (mv_gap, mv_gap60, mv_gap59, mv_gap_rev, mv_gap_same, mv_gap_one):
    db.execute(music_video_groups.insert().values(music_video_id=_mv.id, group_id=g1.id))
    db.execute(music_video_songs.insert().values(music_video_id=_mv.id, song_id=s1.id))
db.commit()

report = run_health_report(db)
span = {i["entity_name"]: i for i in report["issues"] if i["check"] == "date_span"}
check("差 208 天被抓到", "Gap Video" in span)
check("差 60 天（阈值）被抓到", "Gap 60" in span)
check("差 59 天不报", "Gap 59" not in span)
check("上传早于表演也报（-121 天）", "Gap Reversed" in span)
check("相差 0 天不报", "Gap Same" not in span)
check("只有单侧日期不报", "Gap One Side" not in span)
check("日期跨度是 warning", span["Gap Video"]["severity"] == "warning")
check("天数写进标题", "208" in span["Gap Video"]["title"], span["Gap Video"]["title"])
check(
    "两个日期都写进 detail",
    "2024-11-25" in span["Gap Video"]["detail"]
    and "2024-05-01" in span["Gap Video"]["detail"],
    span["Gap Video"]["detail"],
)
check(
    "上传早于表演的文案指出可能填错",
    "填错" in span["Gap Reversed"]["detail"],
    span["Gap Reversed"]["detail"],
)
check("日期跨度带视频深链", (span["Gap Video"]["deep_link"] or "").startswith("/videos/"))

# ============ 场景 8：标注无问题（豁免） ============
print("== 8. 标注无问题 ==")
from app.services.health_ignores import (  # noqa: E402
    add_ignore,
    clear_ignores,
    read_ignores,
    remove_ignore,
)

base_span_count = len(span)
base_total = report["total_issues"]
base_score = report["score"]
target = span["Gap Video"]
add_ignore(
    db,
    key=target["issue_key"],
    signature=target["signature"],
    check=target["check"],
    entity_type=target["entity_type"],
    entity_id=target["entity_id"],
    title=target["title"],
)
check("豁免已落库", len(read_ignores(db)) == 1)

report_ig = run_health_report(db)
now_span = [i for i in report_ig["issues"] if i["check"] == "date_span"]
check("被标注的条目不再出现", all(i["entity_name"] != "Gap Video" for i in now_span))
check(
    "被标注的条目进入「已忽略」区",
    any(i["entity_name"] == "Gap Video" for i in report_ig["ignored_issues"]),
)
check("ignored_count 正确", report_ig["ignored_count"] == 1, report_ig["ignored_count"])
check("总问题数减 1", report_ig["total_issues"] == base_total - 1,
      f"{base_total} -> {report_ig['total_issues']}")
check("分数回升（豁免不计分）", report_ig["score"] > base_score,
      f"{base_score} -> {report_ig['score']}")
check("counts 里 warning 减 1", report_ig["counts"]["warning"] == report["counts"]["warning"] - 1)
_ck = {c["key"]: c["count"] for c in report_ig["checks"]}
check("检查项计数同步减少（口径一致）", _ck["date_span"] == base_span_count - 1,
      f"{base_span_count} -> {_ck['date_span']}")
check("其它条目不受影响", len(now_span) == base_span_count - 1)

# 数据变了 → 豁免自动失效、重新提示
mv_gap_row = db.query(MusicVideo).filter(MusicVideo.name == "Gap Video").one()
mv_gap_row.published_date = date(2025, 12, 1)
db.commit()
report_changed = run_health_report(db)
reopened = [
    i for i in report_changed["issues"]
    if i["check"] == "date_span" and i["entity_name"] == "Gap Video"
]
check("数据变化后重新提示", len(reopened) == 1)
check("重新提示带 reopened 标记", reopened and reopened[0]["reopened"] is True)
check("重新提示时不再算已忽略", report_changed["ignored_count"] == 0)

# 恢复提示
remove_ignore(db, target["issue_key"])
report_rm = run_health_report(db)
back = [
    i for i in report_rm["issues"]
    if i["check"] == "date_span" and i["entity_name"] == "Gap Video"
]
check("恢复提示后回到主清单", len(back) == 1 and back[0]["reopened"] is False)
check("豁免清单已清空", not read_ignores(db))

# 批量清空
add_ignore(db, key=target["issue_key"], signature=target["signature"])
add_ignore(db, key="foo|bar|1", signature="sig-x")
check("可登记多条豁免", len(read_ignores(db)) == 2)
clear_ignores(db)
check("一键恢复全部提示", not read_ignores(db))

db.close()
print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
