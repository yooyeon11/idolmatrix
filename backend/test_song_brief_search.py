"""歌曲精简搜索（/songs/brief 的 deep / with_owner / album_hits）测试。

场景来自真实痛点：AI 没匹配上时用户要**手动找歌**，但下拉原先只按
「歌曲名 / 中文名」匹配 —— 别名、韩文名、英文名、以及「这条歌收在哪张专辑」
都搜不到；命中后也只显示歌名，库里 I AM / Supernova / Too Hot 这类同名歌曲
根本分不清选哪一个。

三个开关默认全关 → AI 关联（linkOrCreateAiRel）等老调用行为不变。
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="song_brief_search_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.api.songs import _ALIAS_SCAN, _BRIEF_LIMIT, list_brief  # noqa: E402
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
yuna = Artist(name="Yuna", stage_name="YUNA", chinese_name="俞娜")
db.add_all([snsd, yuna])
db.flush()

s_gee = Song(
    name="Gee",
    chinese_name="爱情买卖",
    aliases=["지", "重逢的世界"],
    release_artist_type="group",
    release_artist_id=snsd.id,
)
# 同名歌曲两条：靠 owner 区分（组合主体 / 无发行主体只有艺人关系）
s_super_g = Song(name="Supernova", release_artist_type="group", release_artist_id=snsd.id)
s_super_a = Song(name="Supernova")
# 各语言名命中的三条
s_alias = Song(name="Into the New World", aliases=["다시 만난 세계"])
s_kr = Song(name="Ditto", korean_name="디토")
s_en = Song(name="Hype Boy", english_name="Hypeboy")
# 专辑名命中：歌名跟专辑名毫无关系
al_nj = Album(name="NewJeans 1st EP", album_type="MiniAlbum")
s_att = Song(name="Attention")
# 软删歌曲：别名与专辑都能命中，但都不该出现
s_dead = Song(
    name="Dead Aliased",
    aliases=["다시 만난 세계", "死别名"],
    deleted_at=datetime(2024, 1, 1),
)
db.add_all([s_gee, s_super_g, s_super_a, s_alias, s_kr, s_en, s_att, s_dead, al_nj])
db.flush()
db.add(SongArtistRelation(song_id=s_super_a.id, artist_id=yuna.id, order=0))
# 专辑命中的那首也挂主体，用来验证 album_hits 与 with_owner 能叠加
db.add(SongArtistRelation(song_id=s_att.id, artist_id=yuna.id, order=0))
db.add_all(
    [
        AlbumTrack(album_id=al_nj.id, song_id=s_att.id, track_number=1),
        AlbumTrack(album_id=al_nj.id, song_id=s_dead.id, track_number=2),
    ]
)
db.commit()

# ⚠ 直调路由函数时 `deep` / `with_owner` / `album_hits` 的默认值是 FastAPI 的
#   `Query(False)` 对象（**真值**），所以这里必须显式传参；HTTP 上的缺省行为
#   （三个都不传 = 老行为）另用 curl 验证。
OFF = dict(deep=False, with_owner=False, album_hits=False)


def call(**kw):
    return list_brief(db, **{**OFF, **kw})


# ===== 1. 默认档：只按歌曲名 / 中文名，不带任何新字段 =====
print("== 1. 默认档（三个开关全关）行为不变 ==")
rows = call(q="Gee")
check("歌曲名命中", [r.id for r in rows] == [s_gee.id], [r.name for r in rows])
check("不带 owner", all(r.owner is None for r in rows))
check("不带 matched_field / matched_album", all(r.matched_field is None and r.matched_album_id is None for r in rows))
check("中文名仍命中", s_gee.id in {r.id for r in call(q="爱情买卖")})
check("默认档搜不到别名", call(q="다시 만난") == [])
check("默认档搜不到韩文名", call(q="디토") == [])
check("默认档搜不到专辑", call(q="NewJeans 1st") == [])
check("空关键字返回全部活跃歌曲（软删不算）", len(call(q=None)) == 7, len(call(q=None)))

# ===== 2. deep：别名 / 韩文名 / 英文名 =====
print("== 2. deep=true 扩展字段搜索 ==")
rows = call(q="다시 만난", deep=True)
check("韩文别名命中（JSON 转义下仍能搜到）", [r.id for r in rows] == [s_alias.id], [r.name for r in rows])
check("带 matched_field=alias", rows and rows[0].matched_field == "alias", rows[0].matched_field if rows else None)
check(
    "带 matched_value=别名原文",
    rows and rows[0].matched_value == "다시 만난 세계",
    rows[0].matched_value if rows else None,
)

rows = call(q="重逢", deep=True)
check("中文别名命中", [r.id for r in rows] == [s_gee.id], [r.name for r in rows])

rows = call(q="디토", deep=True)
check(
    "韩文名命中 + 写 matched_field/value",
    rows
    and [r.id for r in rows] == [s_kr.id]
    and rows[0].matched_field == "korean_name"
    and rows[0].matched_value == "디토",
    [(r.name, r.matched_field, r.matched_value) for r in rows],
)

rows = call(q="Hypeboy", deep=True)
check(
    "英文名命中 + 写 matched_field/value",
    rows
    and [r.id for r in rows] == [s_en.id]
    and rows[0].matched_field == "english_name"
    and rows[0].matched_value == "Hypeboy",
    [(r.name, r.matched_field, r.matched_value) for r in rows],
)

rows = call(q="gee", deep=True)
check("大小写不敏感且同时命中主名与别名", {r.id for r in rows} == {s_gee.id} or s_gee.id in {r.id for r in rows}, [r.name for r in rows])

print("== 3. 主名命中优先于别名命中 ==")
rows = call(q="Gee", deep=True)
check("主名命中的排最前", rows and rows[0].id == s_gee.id, [r.name for r in rows])
check("主名命中不写 matched_field", rows and rows[0].matched_field is None, rows[0].matched_field if rows else None)

# ===== 4. with_owner：同名歌曲消歧 =====
print("== 4. with_owner=true 带主体名 ==")
rows = call(q="Supernova", with_owner=True)
by_id = {r.id: r for r in rows}
check("同名两条都在", {s_super_g.id, s_super_a.id} <= set(by_id), sorted(by_id))
check(
    "组合发行主体 → 组合中文名优先",
    by_id[s_super_g.id].owner == "少女时代",
    by_id[s_super_g.id].owner,
)
check(
    "无发行主体 → 退到歌曲关系（艺人中文名）",
    by_id[s_super_a.id].owner == "俞娜",
    by_id[s_super_a.id].owner,
)
prefetch = {r.id: r for r in call(q=None, with_owner=True)}
check(
    "空关键字预取也带 owner（下拉一点开就要能区分）",
    prefetch[s_gee.id].owner == "少女时代" and prefetch[s_super_a.id].owner == "俞娜",
    [(r.name, r.owner) for r in prefetch.values()],
)
check(
    "没有主体的歌曲 owner 留空（不编造）",
    prefetch[s_alias.id].owner is None,
    prefetch[s_alias.id].owner,
)

# ===== 5. album_hits：按所属专辑名找歌 =====
print("== 5. album_hits=true 按专辑名找歌 ==")
rows = call(q="NewJeans 1st", album_hits=True)
check("专辑内歌曲被捞出", [r.id for r in rows] == [s_att.id], [r.name for r in rows])
check("带 matched_album_id", rows and rows[0].matched_album_id == al_nj.id, rows[0].matched_album_id if rows else None)
check("带 matched_album_name", rows and rows[0].matched_album_name == "NewJeans 1st EP", rows[0].matched_album_name if rows else None)
check("专辑命中不写 matched_field", rows and rows[0].matched_field is None)
rows = call(q="NewJeans 1st", album_hits=True, with_owner=True)
check("可与 with_owner 叠加", rows and rows[0].owner is not None, rows[0].owner if rows else None)

print("== 6. 三开关叠加：主名/别名/专辑命中混排 ==")
rows = call(q="Gee", deep=True, with_owner=True, album_hits=True)
check("互不干扰，主名命中仍在前", rows and rows[0].id == s_gee.id, [r.name for r in rows])

# ===== 7. 软删除过滤 =====
print("== 7. 软删除实体不入选 ==")
check("软删歌曲的别名不入选", s_dead.id not in {r.id for r in call(q="다시 만난", deep=True)})
check("软删歌曲不从专辑带出", s_dead.id not in {r.id for r in call(q="NewJeans 1st", album_hits=True)})

# ===== 8. 边界 =====
print("== 8. 边界 ==")
check("空串关键字不炸", isinstance(call(q="", deep=True, with_owner=True, album_hits=True), list))
check("查不到返回空列表", call(q="zzz-no-such", deep=True, with_owner=True, album_hits=True) == [])
check("别名池上限常量存在且为正", _ALIAS_SCAN > 0, _ALIAS_SCAN)

print("== 9. 上限保护 ==")
bulk = [Song(name=f"BulkHit {i}") for i in range(_BRIEF_LIMIT + 20)]
db.add_all(bulk)
db.commit()
over = call(q="BulkHit", deep=True, with_owner=True, album_hits=True)
check(f"结果不超过 {_BRIEF_LIMIT} 条", len(over) <= _BRIEF_LIMIT, len(over))

# ===== 10. 同名歌曲全量列出（下拉要能选到第二个） =====
print("== 10. 同名歌曲两条都能被搜到 ==")
same = call(q="Supernova", with_owner=True, deep=True)
check("两条同名都在结果里", len(same) == 2, len(same))

db.close()
print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
