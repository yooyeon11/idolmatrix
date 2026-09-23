"""合作舞台（CollabStage）：类型枚举、入库目录 合作舞台[/歌曲]、短路顺序。

v3.2.55 用户定稿：合作舞台是**多主体**类型（A×B / 跨团 / 跨代际），
目录不带主体段 —— 取任一主体都会把另一方的作品挪到错误的人名下。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="collab_stage_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.api.library import _TITLE_TYPE_ZH  # noqa: E402
from app.core.database import Base, engine  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.models.song import Song  # noqa: E402
from app.services.ai_service import VIDEO_TYPES, _canonical_video_type  # noqa: E402
from app.services.library_service import auto_organize_rel  # noqa: E402
from app.services.video_meta import COLLAB_VIDEO_TYPE, is_short_video  # noqa: E402

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


def organize(video_types, *, groups=(), artists=(), songs=(), is_short=False, is_solo=False):
    mv = MusicVideo(name="clip", video_types=list(video_types), is_short=is_short, is_solo=is_solo)
    for g in groups:
        mv.groups.append(Group(name=g))
    for a in artists:
        mv.artists.append(Artist(name=a, stage_name=a))
    for s in songs:
        mv.songs.append(Song(name=s))
    return auto_organize_rel(mv)


def posix(p):
    return p.as_posix() if p is not None else "None"


# ===== AI 枚举 + 同义词 =====
check("CollabStage 在合法类型中", "CollabStage" in VIDEO_TYPES)
check("合法类型共 11 个", len(VIDEO_TYPES) == 11, str(len(VIDEO_TYPES)))
check("同义词 合作舞台 → CollabStage", _canonical_video_type("合作舞台") == "CollabStage")
check("同义词 联合舞台 → CollabStage", _canonical_video_type("联合舞台") == "CollabStage")
check("同义词 collab → CollabStage", _canonical_video_type("collab") == "CollabStage")
check("大小写/空格 折叠 Collab Stage → CollabStage", _canonical_video_type("Collab Stage") == "CollabStage")
check("枚举原值直通", _canonical_video_type("CollabStage") == "CollabStage")
check("特别舞台 未被带偏 → SpecialStage", _canonical_video_type("特别舞台") == "SpecialStage")
check("练习室 未被带偏 → SpecialStage", _canonical_video_type("练习室") == "SpecialStage")

# ===== 标题中文标签 =====
check("标题类型标签 = 合作舞台", _TITLE_TYPE_ZH.get("CollabStage") == "合作舞台")

# ===== 短视频判定不受影响 =====
check(
    "合作舞台 不构成短视频",
    is_short_video(["CollabStage"], "CollabStage") is False,
)

# ===== 入库目录：合作舞台[/歌曲] =====
p = organize(["CollabStage"], groups=["IVE", "NewJeans"], songs=["Attention"])
check("合作舞台 多组合 → 不带主体段", posix(p) == "合作舞台/Attention", posix(p))

p = organize(["CollabStage"], groups=["TripleS"], artists=["Yooyeon"], songs=["Generation"])
check("合作舞台 带艺人也不进主体段", posix(p) == "合作舞台/Generation", posix(p))

p = organize(["CollabStage"], groups=["IVE"], songs=["Attention", "Hype Boy"])
check("合作舞台 多首歌取首曲（不被串烧抢）", posix(p) == "合作舞台/Attention", posix(p))

p = organize(["CollabStage"], groups=["IVE"])
check("合作舞台 缺歌曲只到根层", posix(p) == "合作舞台", posix(p))

p = organize(["CollabStage"], songs=["Attention"])
check("合作舞台 无任何主体仍归位（不阻断）", posix(p) == "合作舞台/Attention", posix(p))

p = organize(["CollabStage"], songs=[])
check("合作舞台 无主体无歌曲仍归位", posix(p) == "合作舞台", posix(p))

# ===== 短路优先级 =====
p = organize(["CollabStage", "CoverStage"], groups=["IVE"], songs=["Attention"])
check("合作舞台 优先于翻唱", posix(p) == "合作舞台/Attention", posix(p))

p = organize(["CollabStage", "OfficialMV"], groups=["IVE"], songs=["Attention"])
check("合作舞台 + MV 仍走合作舞台根", posix(p) == "合作舞台/Attention", posix(p))

p = organize(["CollabStage", "Other"], groups=["IVE"], songs=["Attention"])
check("合作舞台 优先于非表演根", posix(p) == "合作舞台/Attention", posix(p))

p = organize(["CollabStage", "MixEdit"], groups=["IVE"], songs=["Attention"])
check(
    "MIX混剪 优先于合作舞台（走常规路径）",
    posix(p) == "IVE/Attention/Live",
    posix(p),
)

p = organize(["CollabStage"], groups=["IVE"], songs=["Attention"], is_short=True)
check("短视频 优先于合作舞台", posix(p) == "短视频/IVE", posix(p))

# ===== 常量与实现同源 =====
check("COLLAB_VIDEO_TYPE 常量值", COLLAB_VIDEO_TYPE == "CollabStage")

print(f"\n===== 结果: PASS={PASS} FAIL={FAIL} =====")
sys.exit(0 if FAIL == 0 else 1)
