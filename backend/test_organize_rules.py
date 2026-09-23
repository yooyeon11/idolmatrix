"""入库路径规则 v1.1（三步判定模型）验收测试。

覆盖：特殊根短路顺序（短视频 > 串烧 > 翻唱 > 非表演）、主体双口径、
跳层 / 日期占位 / 无关联阻断等统一行为规范。
"""
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="organize_rules_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.database import Base, engine  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.models.song import Song  # noqa: E402
from app.services.library_service import auto_organize_rel  # noqa: E402

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


def organize(
    video_types,
    *,
    group=None,
    artist=None,
    songs=(),
    is_short=False,
    is_solo=False,
    performance_date=None,
):
    mv = MusicVideo(
        name="clip",
        video_types=list(video_types),
        is_short=is_short,
        is_solo=is_solo,
        performance_date=performance_date,
    )
    if group:
        mv.groups.append(Group(name=group))
    if artist:
        mv.artists.append(Artist(name=artist, stage_name=artist))
    for s in songs:
        mv.songs.append(Song(name=s))
    return auto_organize_rel(mv)


def posix(p):
    return p.as_posix() if p is not None else "None"


# ===== ②-0 MIX混剪（v1.3：只保留优先级短路，路径逻辑与粉丝直拍相同 → 常规路径）=====
p = organize(["MixEdit"], group="IVE", artist="Wonyoung", songs=["Attention"])
check("MIX混剪 成员直拍 → 组合/歌曲/成员名", posix(p) == "IVE/Attention/Wonyoung", posix(p))

p = organize(["MixEdit"], group="IVE", songs=["Attention"])
check("MIX混剪 组合主体 → 组合/歌曲/Live", posix(p) == "IVE/Attention/Live", posix(p))

p = organize(["MixEdit"], artist="IU", songs=["Eight"])
check("MIX混剪 solo → 艺人/歌曲/MIX混剪", posix(p) == "IU/Eight/MIX混剪", posix(p))

p = organize(["MixEdit"], group="IVE", artist="Wonyoung")
check("MIX混剪 缺歌曲跳层 成员名", posix(p) == "IVE/Wonyoung", posix(p))

p = organize(["MixEdit"], artist="IU")
check("MIX混剪 缺歌曲跳层 仅艺人", posix(p) == "IU/MIX混剪", posix(p))

p = organize(
    ["MixEdit"], group="IZNA", songs=["INTRO", "Lemon Tang"],
    performance_date=date(2026, 8, 17),
)
check("MIX混剪 优先于串烧根（歌曲层取首曲）", posix(p) == "IZNA/INTRO/Live", posix(p))

p = organize(["MixEdit"], group="IVE", songs=["Attention"], is_short=True)
check("MIX混剪 优先于短视频根", posix(p) == "IVE/Attention/Live", posix(p))

p = organize(["MixEdit"], group="IVE", artist="Wonyoung", songs=["Attention"], is_short=True)
check("MIX混剪 优先于短视频根（成员直拍）", posix(p) == "IVE/Attention/Wonyoung", posix(p))

p = organize(["MixEdit", "OfficialMV"], group="Twice", songs=["TT"])
check("MIX混剪 组合主体 kind 同口径（带 OfficialMV → MV）", posix(p) == "Twice/TT/MV", posix(p))

p = organize(["MixEdit"], songs=["Attention"])
check("MIX混剪 无主体阻断（None）", p is None, posix(p))

# ===== ② 特殊根短路顺序：短视频 > 合作舞台 > 串烧 > 翻唱 =====
p = organize(["PerformanceVideo"], group="IVE", artist="Wonyoung", songs=["Attention"], is_short=True)
check("短视频优先（有艺人用艺人）", posix(p) == "短视频/Wonyoung", posix(p))

p = organize(["PerformanceVideo"], group="IVE", songs=["Attention"], is_short=True)
check("短视频 无艺人用组合", posix(p) == "短视频/IVE", posix(p))

p = organize(
    ["Fancam"], group="IZNA", artist="Jiwoo",
    songs=["INTRO", "Lemon Tang"], is_short=True,
)
check("短视频优先于串烧", posix(p) == "短视频/Jiwoo", posix(p))

p = organize(
    ["CollabStage"], group="IZNA", artist="Jiwoo",
    songs=["INTRO", "Lemon Tang"], performance_date=date(2026, 8, 17),
)
check("合作舞台优先于串烧（多曲也不带主体/日期段）", posix(p) == "合作舞台/INTRO", posix(p))

p = organize(
    ["CollabStage"], group="IZNA", songs=["INTRO"], is_short=True,
)
check("短视频优先于合作舞台", posix(p) == "短视频/IZNA", posix(p))

p = organize(
    ["Fancam"], group="IZNA", artist="Jiwoo",
    songs=["INTRO", "Lemon Tang"], performance_date=date(2026, 8, 17),
)
check("串烧（≥2 曲）艺人优先 + 日期 yymmdd", posix(p) == "串烧/Jiwoo/260817", posix(p))

p = organize(["Fancam"], group="tripleS", songs=["A", "B"])
check("串烧 无艺人用组合 + 缺日期占位", posix(p) == "串烧/tripleS/未注日期", posix(p))

p = organize(
    ["CoverStage"], group="MEOVV", artist="ANNA",
    songs=["As If It's Your Last"],
)
check("翻唱 艺人优先", posix(p) == "翻唱/ANNA/As If It's Your Last", posix(p))

p = organize(["CoverStage"], group="MEOVV", artist="ANNA")
check("翻唱 缺歌曲跳层", posix(p) == "翻唱/ANNA", posix(p))

p = organize(["CoverStage"], artist=None, group=None, songs=["Attention"])
check("翻唱 无主体阻断（None）", p is None, posix(p))

p = organize(["Other"], group="IVE")
check("非表演视频 Other 类型触发（无艺人用组合）", posix(p) == "非表演视频/IVE", posix(p))

p = organize(["Other"], group="IVE", artist="Wonyoung")
check("非表演视频 艺人优先于组合（直拍场景）", posix(p) == "非表演视频/Wonyoung", posix(p))

p = organize(["Other"], artist="IU")
check("非表演视频 仅艺人用艺人", posix(p) == "非表演视频/IU", posix(p))

p = organize(["Teaser"], group="IVE")
check("非表演类型（预告）仍进非表演根", posix(p) == "非表演视频/IVE", posix(p))

p = organize(["OfficialMV", "Other"], group="Twice", songs=["The Feels"])
check("非表演+MV 仍走非表演根", posix(p) == "非表演视频/Twice", posix(p))

p = organize(["Other"], group="IVE", is_short=True)
check("短视频优先于非表演视频", posix(p) == "短视频/IVE", posix(p))

# ===== 常规路径：主体（组合 > solo 艺人 > 仅艺人）+ 形式标签 =====
p = organize(["Fancam", "PerformanceVideo"], group="Red Velvet", artist="Irene", songs=["Surfin' Boy"])
check("个人直拍归组合 组合/歌曲/成员", posix(p) == "Red Velvet/Surfin' Boy/Irene", posix(p))

p = organize(["Fancam"], group="Red Velvet", artist="Irene")
check("个人直拍 缺歌曲跳层", posix(p) == "Red Velvet/Irene", posix(p))

p = organize(["PerformanceVideo"], group="Red Velvet", artist="Irene", songs=["Surfin' Boy"])
check("舞台+成员归成员名（方案1 取消类型限制）", posix(p) == "Red Velvet/Surfin' Boy/Irene", posix(p))

p = organize(["OfficialMV"], group="Twice", artist="Nayeon", songs=["TT"])
check("官方MV带成员归成员名（方案1 副作用）", posix(p) == "Twice/TT/Nayeon", posix(p))

p = organize(["Fancam"], artist="HeeJin", songs=["Algorithm"], is_solo=True)
check("solo 直拍归艺人名下", posix(p) == "HeeJin/Algorithm/Live", posix(p))

p = organize(["Fancam"], artist="HeeJin", songs=["Algorithm"])
check("无组合直拍归艺人名下（未勾 solo）", posix(p) == "HeeJin/Algorithm/Live", posix(p))

p = organize(["Fancam"], group="IZNA", songs=["IZNA"])
check("组合直拍 组合/歌曲/Live", posix(p) == "IZNA/IZNA/Live", posix(p))

p = organize(["PerformanceVideo"], group="IZNA", songs=["IZNA"])
check("现场演出 组合/歌曲/Live", posix(p) == "IZNA/IZNA/Live", posix(p))

p = organize(["PerformanceVideo"], group="IZNA")
check("现场演出 缺歌曲跳层", posix(p) == "IZNA/Live", posix(p))

p = organize(["OfficialMV"], group="Twice", songs=["The Feels"])
check("官方 MV 组合/歌曲/MV", posix(p) == "Twice/The Feels/MV", posix(p))

p = organize(["OfficialMV"], artist="IU", songs=["Eight"])
check("官方MV 无组合用艺人", posix(p) == "IU/Eight/MV", posix(p))

p = organize(["FanMeeting"], group="IZNA", songs=["Talk"])
check("其余类型组合归档为 Live", posix(p) == "IZNA/Talk/Live", posix(p))

# ===== 统一行为规范 =====
p = organize(["Other"], group=None, artist=None, songs=["Attention"])
check("无任何关联返回 None（入库阻断）", p is None, posix(p))

p = organize(["Other"])
check("无类型无关联返回 None", p is None, posix(p))

print(f"\n===== 结果: PASS={PASS} FAIL={FAIL} =====")
sys.exit(0 if FAIL == 0 else 1)
