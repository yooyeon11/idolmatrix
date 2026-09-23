"""首页主舞台：hide_from_home 过滤 + 只抽有关联视频的实体 + 直拍官方优先排序。"""
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="home_hero_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.database import SessionLocal, init_db  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.music_video import (  # noqa: E402
    MusicVideo,
    music_video_artists,
    music_video_groups,
)
from app.api.home import (  # noqa: E402
    _pick_entities,
    _query_entity_fancams,
    hero_stage,
    hero_stage_fancams,
)

init_db()

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
try:
    a1 = Artist(
        name="Shown",
        english_name="Shown U",
        avatar_path="avatars/artists/1.jpg",
        hide_from_home=False,
    )
    a2 = Artist(name="Hidden", avatar_path="avatars/artists/2.jpg", hide_from_home=True)
    a3 = Artist(name="NoAvatar", banner_path="banners/artists/3.jpg", hide_from_home=False)
    g1 = Group(name="IVE", avatar_path="avatars/groups/1.jpg", hide_from_home=False)
    g2 = Group(name="HiddenG", avatar_path="avatars/groups/2.jpg", hide_from_home=True)
    g3 = Group(name="AESPA", avatar_path="avatars/groups/3.jpg", hide_from_home=False)
    db.add_all([a1, a2, a3, g1, g2, g3])
    db.commit()
    for obj in (a1, a2, a3, g1, g2, g3):
        db.refresh(obj)

    # 新口径：还没有任何视频关联时，主舞台池必须为空
    check(
        "无视频关联时主舞台为空",
        _pick_entities(db, Artist, music_video_artists, "artist_id", 20) == [],
    )

    mv_off = MusicVideo(
        name="Official Fancam",
        ingestion_status="library",
        video_type="OfficialFancam",
        video_types=["OfficialFancam"],
        performance_date=date(2024, 1, 1),
    )
    mv_per = MusicVideo(
        name="Personal Fancam",
        ingestion_status="library",
        video_type="PersonalFancam",
        video_types=["PersonalFancam"],
        performance_date=date(2025, 1, 1),
        subject_artist_id=a1.id,
    )
    mv_fan = MusicVideo(
        name="Generic Fancam",
        ingestion_status="library",
        video_type="Fancam",
        video_types=["Fancam"],
        performance_date=date(2025, 6, 1),
    )
    mv_mv = MusicVideo(
        name="Official MV",
        ingestion_status="library",
        video_type="OfficialMV",
        video_types=["OfficialMV"],
    )
    mv_dead = MusicVideo(
        name="Deleted MV",
        ingestion_status="library",
        video_type="OfficialMV",
        video_types=["OfficialMV"],
    )
    mv_dead.deleted_at = datetime.now()  # 软删除：不算「有关联视频」
    mv_pending = MusicVideo(
        name="Incoming MV",
        ingestion_status="incoming",
        video_type="OfficialMV",
        video_types=["OfficialMV"],
    )
    db.add_all([mv_off, mv_per, mv_fan, mv_mv, mv_dead, mv_pending])
    db.commit()
    for mv in (mv_off, mv_fan):
        db.execute(
            music_video_artists.insert().values(music_video_id=mv.id, artist_id=a1.id)
        )
    # g3：有关联库内视频 → 应进池；g1：只有软删除 / 未入库视频 → 应被排除
    db.execute(music_video_groups.insert().values(music_video_id=mv_mv.id, group_id=g3.id))
    for mv in (mv_dead, mv_pending):
        db.execute(
            music_video_groups.insert().values(music_video_id=mv.id, group_id=g1.id)
        )
    db.commit()

    picked = _pick_entities(db, Artist, music_video_artists, "artist_id", 20)
    mag_ids = {a.id for a in picked}
    check("主舞台排除 hide_from_home", a2.id not in mag_ids)
    check("主舞台要求头像", a3.id not in mag_ids)
    check("主舞台要求库内视频关联", a1.id in mag_ids, str(mag_ids))
    picked_g = _pick_entities(db, Group, music_video_groups, "group_id", 20)
    g_ids = {g.id for g in picked_g}
    check("组合有库内视频才进主舞台", g3.id in g_ids and g1.id not in g_ids, str(g_ids))
    check("主舞台排除隐藏组合", g2.id not in g_ids)

    cams = _query_entity_fancams(db, "artist", a1.id, limit=4)
    names = [m.name for m in cams]
    check("不含官方 MV", "Official MV" not in names, str(names))
    check("官方直拍排第一", bool(names) and names[0] == "Official Fancam", str(names))
    check("含 subject 个人直拍", "Personal Fancam" in names, str(names))

    result = hero_stage(db=db, limit=8)
    ids = {(it.type, it.id) for it in result.items}
    check("API 不含隐藏", ("artist", a2.id) not in ids and ("group", g2.id) not in ids)
    check(
        "API 排除无关联视频的实体",
        ("artist", a3.id) not in ids and ("group", g1.id) not in ids,
        str(ids),
    )
    check(
        "API 每帧都有库内视频",
        bool(result.items) and all(it.video_count > 0 for it in result.items),
        [(it.type, it.name, it.video_count) for it in result.items],
    )
    check("API 返回 social_media 字段", all(hasattr(it, "social_media") for it in result.items))
    shown = next((it for it in result.items if it.type == "artist" and it.id == a1.id), None)
    check("API 返回 english_name", shown is not None and shown.english_name == "Shown U")

    fancam_res = hero_stage_fancams("artist", a1.id, db=db, limit=4)
    check("fancams API 有结果", len(fancam_res.items) >= 1)
    check(
        "fancams API 官方优先",
        fancam_res.items[0].name == "Official Fancam",
        [i.name for i in fancam_res.items],
    )
finally:
    db.close()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
