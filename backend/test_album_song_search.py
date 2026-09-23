"""专辑精简搜索（/albums/brief）测试：专辑名命中 + 曲目名命中 + 提示语字段。

场景来自真实痛点：用户只记得歌名（如 Gee），不知道专辑名，要在专辑下拉里
按歌名把「收录这首歌的专辑」搜出来，并按「主体 - 专辑 - 歌曲」显示。
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="album_song_search_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.api.albums import list_brief  # noqa: E402
from app.schemas.album import AlbumBrief  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.album import Album, AlbumTrack  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.song import Song, SongArtistRelation  # noqa: E402

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

# ===== 夹具 =====
snsd = Group(name="Girls' Generation", chinese_name="少女时代")
ive = Group(name="IVE")
yuna = Artist(name="Yuna", stage_name="YUNA", chinese_name="俞娜")
db.add_all([snsd, ive, yuna])
db.flush()

# A. 专辑名就叫 Gee（专辑名命中）
al_gee = Album(name="Gee", chinese_name="Gee 中文版", album_type="MiniAlbum")
# B. 专辑名不含 Gee，但收录了歌 Gee（曲目命中，组合发行主体）
al_snsd1 = Album(
    name="Girls' Generation",
    album_type="FullAlbum",
    release_artist_type="group",
    release_artist_id=snsd.id,
)
# C. 曲目命中的专辑，主体是艺人（且只有 relation，没有 release_artist）
al_ive = Album(name="I've IVE", album_type="FullAlbum")
# D. 名字沾边但曲目无关的干扰项
al_other = Album(name="Geegee Remix Pack", album_type="Single")
# E. 已软删除专辑（不该出现）
al_deleted = Album(name="Gee (Deleted)", album_type="Single", deleted_at=datetime(2024, 1, 1))

db.add_all([al_gee, al_snsd1, al_ive, al_other, al_deleted])
db.flush()

# 歌曲：名字 / 中文名 命中
s_gee = Song(name="Gee", chinese_name="Gee", release_artist_type="group", release_artist_id=snsd.id)
s_gee_cn = Song(name="Gee (Chinese Ver.)", release_artist_type="group", release_artist_id=snsd.id)
s_gee_rel = Song(name="Gee", release_artist_type=None, release_artist_id=None)
s_after = Song(name="After LIKE", release_artist_type="group", release_artist_id=ive.id)
# 已软删除歌曲（不该带出专辑）
s_deleted = Song(name="Gee Old", deleted_at=datetime(2024, 1, 1))

db.add_all([s_gee, s_gee_cn, s_gee_rel, s_after, s_deleted])
db.flush()
db.add(
    SongArtistRelation(song_id=s_gee_rel.id, artist_id=yuna.id, order=0)
)
db.flush()

db.add_all(
    [
        AlbumTrack(album_id=al_snsd1.id, song_id=s_gee.id, track_number=1),
        AlbumTrack(album_id=al_snsd1.id, song_id=s_gee_cn.id, track_number=2),
        AlbumTrack(album_id=al_ive.id, song_id=s_gee_rel.id, track_number=3),
        AlbumTrack(album_id=al_ive.id, song_id=s_after.id, track_number=4),
        AlbumTrack(album_id=al_other.id, song_id=s_after.id, track_number=1),
        AlbumTrack(album_id=al_deleted.id, song_id=s_deleted.id, track_number=1),
    ]
)
db.commit()

# ===== 1. song_hits=false：只按专辑名（默认档，与改动前行为一致）=====
# ⚠ 直接调用时 `song_hits` 的默认值仍是 FastAPI 的 `Query(False)` 对象（真值），
#   所以这里必须显式传 False；HTTP 上的缺省行为用 curl 单独验证。
print("== 1. song_hits=false 只按专辑名 ==")
rows = list_brief(db, q="Gee", song_hits=False)
names = {r.name for r in rows}
check("专辑名命中 Gee / Geegee Remix Pack", {"Gee", "Geegee Remix Pack"} <= names, names)
check("不掺入曲目命中的专辑", "Girls' Generation" not in names and "I've IVE" not in names, names)
check("不带 matched_song_*", all(getattr(r, "matched_song_id", None) is None for r in rows))
check(
    "song_hits=false 时返回 AlbumBrief（v3.2.44 起统一带 owner 兜底字段）",
    isinstance(rows[0], AlbumBrief),
)

print("== 2. 无关键字时全量（song_hits 不生效） ==")
rows_all = list_brief(db, q=None, song_hits=True)
check("空关键字返回全部活跃专辑（软删的不算）", len(rows_all) == 4, len(rows_all))

# ===== 3. 曲目命中：只记得歌名也能找到专辑 =====
print("== 3. song_hits=true：按歌名搜到专辑 ==")
rows = list_brief(db, q="Gee", song_hits=True)
by_id = {r.id: r for r in rows}
check("专辑名命中项仍在", al_gee.id in by_id and al_other.id in by_id)
check("曲目命中项被捞出", al_snsd1.id in by_id and al_ive.id in by_id, sorted(by_id))

r_snsd = by_id.get(al_snsd1.id)
check(
    "曲目命中带出歌曲名",
    r_snsd is not None and r_snsd.matched_song_id in (s_gee.id, s_gee_cn.id),
    r_snsd.matched_song_id if r_snsd else None,
)
check("曲目命中带出歌名", r_snsd is not None and r_snsd.matched_song_name, r_snsd.matched_song_name if r_snsd else None)
check(
    "组合发行主体 → 组合中文名优先",
    r_snsd is not None and r_snsd.matched_song_owner == "少女时代",
    r_snsd.matched_song_owner if r_snsd else None,
)

r_ive = by_id.get(al_ive.id)
check(
    "无发行主体 → 退到歌曲关系名（艺人中文名）",
    r_ive is not None and r_ive.matched_song_owner == "俞娜",
    r_ive.matched_song_owner if r_ive else None,
)

r_by_name = by_id.get(al_gee.id)
check("专辑名命中项不写 matched_song_id", r_by_name is not None and r_by_name.matched_song_id is None)

print("== 4. 大小写不敏感 ==")
lower_rows = list_brief(db, q="gee", song_hits=True)
lower_ids = {r.id for r in lower_rows}
check("小写 gee 同样命中专辑名与曲目", {al_gee.id, al_snsd1.id, al_ive.id} <= lower_ids, sorted(lower_ids))

print("== 5. 中文名命中曲目 ==")
cn_rows = list_brief(db, q="Chinese", song_hits=True)
check("按歌曲中文名/名命中专辑", al_snsd1.id in {r.id for r in cn_rows}, [r.name for r in cn_rows])

print("== 6. 软删除实体不入选 ==")
check(
    "软删专辑不出现在专辑名命中",
    al_deleted.id not in {r.id for r in list_brief(db, q="Gee", song_hits=False)},
)
check(
    "软删歌曲不带出专辑",
    all(r.matched_song_id != s_deleted.id for r in list_brief(db, q="Gee", song_hits=True)),
)

print("== 7. 去重：同名专辑只出一条 ==")
gee_rows = [r for r in list_brief(db, q="Gee", song_hits=True) if r.id == al_gee.id]
check("专辑名命中的专辑只出现一次", len(gee_rows) == 1, len(gee_rows))

print("== 8. 无关键字 + song_hits 边界 ==")
check("空串关键字不炸", isinstance(list_brief(db, q="", song_hits=True), list))
check("查不到时返回空列表", list_brief(db, q="zzz-no-such", song_hits=True) == [])

print("== 9. 上限保护 ==")
from app.api.albums import _BRIEF_LIMIT  # noqa: E402

bulk = [Album(name=f"Bulk {i}") for i in range(_BRIEF_LIMIT + 20)]
db.add_all(bulk)
db.flush()
bulk_songs = [Song(name="BulkHit") for _ in bulk]
db.add_all(bulk_songs)
db.flush()
for al, sg in zip(bulk, bulk_songs):
    db.add(AlbumTrack(album_id=al.id, song_id=sg.id, track_number=1))
db.commit()
over_rows = list_brief(db, q="Bulk", song_hits=True)
check(f"结果不超过 {_BRIEF_LIMIT} 条", len(over_rows) <= _BRIEF_LIMIT, len(over_rows))

print("== 10. 专辑自身发行主体 owner（下拉无歌曲关联时的兜底提示）==")
name_rows = list_brief(db, q="Geegee")
check(
    "专辑名命中路径也带 owner 字段（无主体为 None）",
    len(name_rows) == 1 and name_rows[0].owner is None,
    [(r.name, r.owner) for r in name_rows],
)
rows = list_brief(db, q="Gee", song_hits=True)
r_owned = next((r for r in rows if r.id == al_snsd1.id), None)
check("有发行主体的专辑 owner=组合中文名", r_owned is not None and r_owned.owner == "少女时代",
      r_owned.owner if r_owned else None)
r_rel = next((r for r in rows if r.id == al_ive.id), None)
check("主体只挂在歌（relation）上的专辑 owner=None", r_rel is not None and r_rel.owner is None,
      r_rel.owner if r_rel else None)
check(
    "matched_song_owner 与 owner 互不干扰（同一行各取各的）",
    r_owned is not None and r_owned.matched_song_owner == "少女时代" and r_owned.owner == "少女时代",
    (r_owned.matched_song_owner, r_owned.owner) if r_owned else None,
)
# 无关键字（下拉一点开的预取）与非 song_hits 路径也要带 owner —— 兜底提示不能只对搜索生效
prefetch = list_brief(db, q=None)
check(
    "无关键字预取同样带 owner 字段",
    len(prefetch) > 0 and all(r.owner is None or isinstance(r.owner, str) for r in prefetch),
    len(prefetch),
)
plain = list_brief(db, q="Girls' Generation")
check(
    "纯专辑名搜索（不带 song_hits）也带 owner",
    len(plain) == 1 and plain[0].owner == "少女时代",
    [(r.name, r.owner) for r in plain],
)

db.close()
print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
