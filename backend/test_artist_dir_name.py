"""重名艺人目录消歧（v3.2.40）验收测试。

规则（用户定稿）：同一个展示名（stage_name → chinese_name → name）的艺人，
按 id 升序第一个保留原名，其后追加中文名 —— `Yuna(유나)`；
重名艺人缺中文名（空 / 与展示名相同 / 组内重复）时不给目录名，
该艺人作**路径根层主体**（短视频 / 串烧 / 翻唱 / 非表演 / solo 常规路径）时
阻断入库（ArtistDirNameConflict），必须补中文名或手动指定路径。

只跑本文件：
    python test_artist_dir_name.py
"""
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="artist_dir_name_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.models.song import Song  # noqa: E402
from app.services.library_service import (  # noqa: E402
    ArtistDirNameConflict,
    artist_dir_names,
    auto_organize_rel,
    suggest_relocate_path,
    tolerant_auto_organize_rel,
)

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


def posix(p):
    return p.as_posix() if p is not None else "None"


# ===== 夹具：重名 + 唯一 + 软删 =====
db = SessionLocal()


def mk_artist(name, stage=None, chinese=None, deleted=False):
    a = Artist(name=name, stage_name=stage, chinese_name=chinese)
    if deleted:
        a.deleted_at = datetime(2024, 1, 1)
    db.add(a)
    return a


# 重名组 Yuna：1 保留原名、2 加中文名、3 缺中文名（阻断）
a_yuna_1 = mk_artist("Yuna", "Yuna", "俞娜")
a_yuna_2 = mk_artist("Yuna", "Yuna", "刘娜")
a_yuna_3 = mk_artist("Yuna", "Yuna", "")
# 唯一名：不受影响
a_karina = mk_artist("Karina", "Karina", "卡瑞娜")
a_minji = mk_artist("Minji")
# 中文名与展示名相同（重名组 Same × 2）→ 第二个阻断
a_same_1 = mk_artist("Same", "Same", "")
a_same_2 = mk_artist("Same", "Same", "Same")
# 组内中文名重复（Dup × 3，后两个中文名同为「同」）→ 第三个阻断
a_dup_1 = mk_artist("Dup", "Dup", "同")
a_dup_2 = mk_artist("Dup", "Dup", "同")
a_dup_3 = mk_artist("Dup", "Dup", "同")
# 软删同名不算重名：活跃 Gone 应保持原名
mk_artist("Gone", "Gone", "", deleted=True)
a_gone = mk_artist("Gone", "Gone", "")
# 空白中文名视作缺失（Blank × 2）
a_blank_1 = mk_artist("Blank", "Blank", "空白")
a_blank_2 = mk_artist("Blank", "Blank", "   ")
db.commit()

ids = {
    "yuna1": a_yuna_1.id,
    "yuna2": a_yuna_2.id,
    "yuna3": a_yuna_3.id,
    "karina": a_karina.id,
    "minji": a_minji.id,
    "same2": a_same_2.id,
    "dup3": a_dup_3.id,
    "gone": a_gone.id,
    "blank2": a_blank_2.id,
}

# ===== 1. 消歧表本身 =====
print("== 1. artist_dir_names：重名组按 id 升序消歧 ==")
table = artist_dir_names(db)


def dir_of(key):
    info = table.get(ids[key])
    return info.dir_name if info else "<不在表中>"


check("唯一的艺人保持展示名", dir_of("karina") == "Karina", dir_of("karina"))
check("无中文名的唯一艺人也保持展示名", dir_of("minji") == "Minji", dir_of("minji"))
check("重名组第一个保留原名", dir_of("yuna1") == "Yuna", dir_of("yuna1"))
check(
    "重名组第二个追加中文名",
    dir_of("yuna2") == "Yuna(刘娜)",
    dir_of("yuna2"),
)
check("重名组缺中文名的拿不到目录名", dir_of("yuna3") is None, dir_of("yuna3"))
check(
    "缺中文名的原因可读",
    "缺少中文名" in (table[ids["yuna3"]].reason or ""),
    table[ids["yuna3"]].reason,
)
check("中文名与展示名相同 → 阻断", dir_of("same2") is None, dir_of("same2"))
check(
    "中文名与展示名相同的原因可读",
    "展示名相同" in (table[ids["same2"]].reason or ""),
    table[ids["same2"]].reason,
)
check("组内中文名重复 → 第三个阻断", dir_of("dup3") is None, dir_of("dup3"))
check(
    "组内中文名重复的原因可读",
    "重复" in (table[ids["dup3"]].reason or ""),
    table[ids["dup3"]].reason,
)
check("软删同名不参与重名", dir_of("gone") == "Gone", dir_of("gone"))
check("空白中文名视作缺失 → 阻断", dir_of("blank2") is None, dir_of("blank2"))
check(
    "空白中文名的原因可读",
    "缺少中文名" in (table[ids["blank2"]].reason or ""),
    table[ids["blank2"]].reason,
)
check("消歧表不为 None 的名字留空段", all(
    (info.dir_name is None) or info.dir_name.strip() for info in table.values()
))


# ===== 2. 路径规则：根层主体（用 id 直挂的轻量 MV）=====
def mv_of(video_types, *, artist_id=None, artist_name="probe", artist_stage=None,
          group_name=None, songs=(), is_short=False, is_solo=False, perf=None,
          names=None):
    """构造轻量 MV：艺人用手写 id 的游离对象，命中消歧表即可（不碰 Session）。

    artist_name / artist_stage 需与真实艺人一致，否则回退分支拿到的是探针名。
    """
    mv = MusicVideo(
        name="clip",
        video_types=list(video_types),
        is_short=is_short,
        is_solo=is_solo,
        performance_date=perf,
    )
    if artist_id is not None:
        probe = Artist(name=artist_name, stage_name=artist_stage)
        probe.id = artist_id
        mv.artists.append(probe)
    if group_name:
        mv.groups.append(Group(name=group_name))
    for s in songs:
        mv.songs.append(Song(name=s))
    return auto_organize_rel(mv, artist_names=names)


print("== 2. 根层主体：短视频 / 串烧 / 翻唱 / 非表演 / solo 常规 ==")
p = mv_of(["ShortVideo"], artist_id=ids["yuna1"], is_short=True, names=table)
check("短视频 重名第一个 → 原名", posix(p) == "短视频/Yuna", posix(p))

p = mv_of(["ShortVideo"], artist_id=ids["yuna2"], is_short=True, names=table)
check("短视频 重名第二个 → 加中文名", posix(p) == "短视频/Yuna(刘娜)", posix(p))

p = mv_of(
    ["PerformanceVideo"],
    artist_id=ids["yuna2"],
    songs=["Gee", "Hush"],
    perf=date(2024, 5, 1),
    names=table,
)
check("串烧（2 首歌）重名第二个 → 加中文名", posix(p) == "串烧/Yuna(刘娜)/240501", posix(p))

p = mv_of(["CoverStage"], artist_id=ids["yuna2"], songs=["Gee"], names=table)
check("翻唱 重名第二个 → 加中文名", posix(p) == "翻唱/Yuna(刘娜)/Gee", posix(p))

p = mv_of(["Other"], artist_id=ids["yuna2"], names=table)
check("非表演 重名第二个 → 加中文名", posix(p) == "非表演视频/Yuna(刘娜)", posix(p))

p = mv_of(["PerformanceVideo"], artist_id=ids["yuna2"], songs=["Gee"], is_solo=True, names=table)
check("常规 solo 主体 重名第二个 → 加中文名", posix(p) == "Yuna(刘娜)/Gee/Live", posix(p))

p = mv_of(["PerformanceVideo"], artist_id=ids["karina"], songs=["Gee"], is_solo=True, names=table)
check("唯一艺人路径不变", posix(p) == "Karina/Gee/Live", posix(p))


# ===== 3. 阻断：根层主体缺中文名 =====
print("== 3. 根层主体缺中文名 → 抛 ArtistDirNameConflict ==")


def expect_conflict(name, fn):
    try:
        got = fn()
    except ArtistDirNameConflict as exc:
        check(name, True, f"阻断：{exc}")
        return exc
    check(name, False, f"未阻断，得到 {posix(got) if isinstance(got, Path) else got}")
    return None


expect_conflict(
    "短视频 缺中文名 → 阻断",
    lambda: mv_of(["ShortVideo"], artist_id=ids["yuna3"], artist_name="Yuna",
                  artist_stage="Yuna", is_short=True, names=table),
)
expect_conflict(
    "串烧 缺中文名 → 阻断",
    lambda: mv_of(["PerformanceVideo"], artist_id=ids["yuna3"], artist_name="Yuna",
                  artist_stage="Yuna", songs=["Gee", "Hush"], names=table),
)
expect_conflict(
    "翻唱 缺中文名 → 阻断",
    lambda: mv_of(["CoverStage"], artist_id=ids["yuna3"], artist_name="Yuna",
                  artist_stage="Yuna", songs=["Gee"], names=table),
)
expect_conflict(
    "非表演 缺中文名 → 阻断",
    lambda: mv_of(["Other"], artist_id=ids["yuna3"], artist_name="Yuna",
                  artist_stage="Yuna", names=table),
)
expect_conflict(
    "常规 solo 主体 缺中文名 → 阻断",
    lambda: mv_of(["PerformanceVideo"], artist_id=ids["yuna3"], artist_name="Yuna",
                  artist_stage="Yuna", songs=["Gee"], is_solo=True, names=table),
)
expect_conflict(
    "组内中文名重复的第三个 → 阻断",
    lambda: mv_of(["ShortVideo"], artist_id=ids["dup3"], artist_name="Dup",
                  artist_stage="Dup", is_short=True, names=table),
)
exc = expect_conflict(
    "阻断异常带艺人 id 与可读说明",
    lambda: mv_of(["ShortVideo"], artist_id=ids["yuna3"], artist_name="Yuna",
                  artist_stage="Yuna", is_short=True, names=table),
)
check(
    "异常是 ValueError（API 可直接映射 400）",
    isinstance(exc, ValueError),
    type(exc).__name__ if exc else "",
)
check(
    "异常文案含「补全中文名」指引",
    bool(exc) and "补全" in str(exc) and "中文名" in str(exc),
    str(exc) if exc else "",
)
check(
    "异常文案含手动指定路径的兜底",
    bool(exc) and "手动指定" in str(exc),
    str(exc) if exc else "",
)

print("== 4. 非根层（组合成员直拍）不阻断，只做一致命名 ==")
p = mv_of(["PerformanceVideo"], artist_id=ids["yuna3"], artist_name="Yuna",
          artist_stage="Yuna", group_name="ITZY", songs=["Gee"], names=table)
check("成员直拍 缺中文名不阻断（回退原名）", posix(p) == "ITZY/Gee/Yuna", posix(p))

p = mv_of(["PerformanceVideo"], artist_id=ids["yuna2"], artist_name="Yuna",
          artist_stage="Yuna", group_name="ITZY", songs=["Gee"], names=table)
check("成员直拍 有消歧名则用消歧名", posix(p) == "ITZY/Gee/Yuna(刘娜)", posix(p))

p = mv_of(["PerformanceVideo"], artist_id=ids["yuna2"], artist_name="Yuna",
          artist_stage="Yuna", group_name="ITZY", songs=["Gee"], is_solo=True, names=table)
check("solo + 有组合 → 走根层（组合不参与）", posix(p) == "Yuna(刘娜)/Gee/Live", posix(p))


# ===== 5. 容忍版 / 无消歧表回退 =====
print("== 5. 容忍版与回退 ==")
mv_short = MusicVideo(name="clip", video_types=["ShortVideo"], is_short=True)
probe = Artist(name="Yuna", stage_name="Yuna")
probe.id = ids["yuna3"]
mv_short.artists.append(probe)
check(
    "tolerant 版遇阻断返回 None（不抛）",
    tolerant_auto_organize_rel(mv_short, artist_names=table) is None,
)
mv_plain = MusicVideo(name="clip", video_types=["ShortVideo"], is_short=True)
probe2 = Artist(name="Yuna", stage_name="Yuna")
probe2.id = ids["yuna2"]
mv_plain.artists.append(probe2)
check(
    "不传消歧表 → 回退展示名（单测直构的既有行为）",
    posix(auto_organize_rel(mv_plain)) == "短视频/Yuna",
    posix(auto_organize_rel(mv_plain)),
)
check(
    "组合名从不消歧（同名组合不受影响）",
    posix(mv_of(["PerformanceVideo"], group_name="Yuna", songs=["Gee"], names=table))
    == "Yuna/Gee/Live",
)


# ===== 6. 走 Session：db 参数与 mv 所属 Session 推断 =====
print("== 6. db 参数 / object_session 推断 ==")
mv_db = MusicVideo(
    name="db clip",
    video_type="ShortVideo",
    video_types=["ShortVideo"],
    is_short=True,
    ingestion_status="library",
    file_path="短视频/Yuna/db clip.mp4",
)
mv_db.artists = [db.get(Artist, ids["yuna2"])]
db.add(mv_db)
db.commit()
db.refresh(mv_db)

check(
    "显式 db → 用消歧名",
    posix(auto_organize_rel(mv_db, db=db)) == "短视频/Yuna(刘娜)",
    posix(auto_organize_rel(mv_db, db=db)),
)
check(
    "不传 db → 从 mv 所属 Session 推断",
    posix(auto_organize_rel(mv_db)) == "短视频/Yuna(刘娜)",
    posix(auto_organize_rel(mv_db)),
)
sugg = suggest_relocate_path(mv_db, db=db)
check(
    "suggest_relocate_path 同口径（保留原文件名）",
    posix(sugg) == "短视频/Yuna(刘娜)/db clip.mp4",
    posix(sugg),
)

mv_db3 = MusicVideo(
    name="db clip3",
    video_type="ShortVideo",
    video_types=["ShortVideo"],
    is_short=True,
    ingestion_status="library",
    file_path="短视频/Yuna/db clip3.mp4",
)
mv_db3.artists = [db.get(Artist, ids["yuna3"])]
db.add(mv_db3)
db.commit()
db.refresh(mv_db3)
expect_conflict(
    "已入库视频 更改存储路径（自动）→ 同样阻断",
    lambda: suggest_relocate_path(mv_db3, db=db),
)
check(
    "已入库视频 容忍版返回 None（体检不因此报错）",
    tolerant_auto_organize_rel(mv_db3, db=db) is None,
)

print(f"\n===== 结果: PASS={PASS} FAIL={FAIL} =====")
db.close()
sys.exit(1 if FAIL else 0)
