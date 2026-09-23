"""短视频判定口径：只看视频类型是否含 ShortVideo，与时长无关。

背景：历史实现用「类型含 ShortVideo **或** 时长 < 70 秒」派生 is_short，
导致 70 秒以内的 MIX 混剪 / 预告被塞进短视频列表（类型是 MixEdit，却出现在 /shorts）。
现在 is_short 的唯一口径 = video_types 含 ShortVideo，时长完全不参与判断。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_TMP = Path(tempfile.mkdtemp(prefix="short_flag_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_TMP / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_TMP / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_TMP / "incoming")
os.environ["AUTH_REQUIRED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.cli import fix_short_flags  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.models.song import Song  # noqa: E402
from app.services.video_meta import is_short_video  # noqa: E402

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


# ===== A. 纯函数：口径只认类型 =====
print("\n[A] is_short_video 纯函数")
check("类型含 ShortVideo → 短视频", is_short_video(["ShortVideo"]) is True)
check("MixEdit + 90 秒 → 不是短视频", is_short_video(["MixEdit"]) is False)
check("video_type 单值 ShortVideo → 短视频", is_short_video([], "ShortVideo") is True)
check("video_type 单值 MixEdit → 不是短视频", is_short_video([], "MixEdit") is False)
check("空类型 → 不是短视频（兜底 Other）", is_short_video(None, None) is False)
check("多选含 ShortVideo → 短视频", is_short_video(["MixEdit", "ShortVideo"]) is True)
check(
    "多选含 ShortVideo（顺序无关）",
    is_short_video(["ShortVideo", "Fancam"]) is True,
)
check("Other / Teaser / SpecialVideo 都不算", not any(
    is_short_video([t]) for t in ("Other", "Teaser", "SpecialVideo", "CoverStage")
))

# ===== B. 入库/预览路径：时长 < 70 秒不再触发短视频目录 =====
print("\n[B] 路径推导（preview-path 与入库共用 auto_organize_rel）")
db = SessionLocal()
group = Group(name="IVE")
song = Song(name="Attention")
db.add(group)
db.add(song)
db.flush()
gid, sid = group.id, song.id
db.commit()
db.close()

with TestClient(app) as client:
    res = client.post(
        "/api/library/preview-path",
        json={
            "file_name": "a.mp4",
            "video_types": ["MixEdit"],
            "video_type": "MixEdit",
            "duration": 20,
            "group_ids": [gid],
            "song_ids": [sid],
        },
    )
    check("MixEdit 预览 200", res.status_code == 200, str(res.status_code))
    rel_mix = res.json().get("relative_path", "")
    check("MixEdit + 20 秒 → 不进短视频目录", not rel_mix.startswith("短视频"), rel_mix)
    check(
        "MixEdit + 20 秒 → 走 MIX/常规表演目录",
        rel_mix == "IVE/Attention/Live/a.mp4",
        rel_mix,
    )

    res = client.post(
        "/api/library/preview-path",
        json={
            "file_name": "b.mp4",
            "video_types": ["ShortVideo"],
            "video_type": "ShortVideo",
            "duration": 300,
            "group_ids": [gid],
        },
    )
    rel_short_type = res.json().get("relative_path", "")
    check(
        "ShortVideo 类型 + 300 秒 → 仍进短视频目录（类型说了算）",
        rel_short_type == "短视频/IVE/b.mp4",
        rel_short_type,
    )

    res = client.post(
        "/api/library/preview-path",
        json={
            "file_name": "c.mp4",
            "video_types": ["Teaser"],
            "video_type": "Teaser",
            "duration": 30,
            "group_ids": [gid],
        },
    )
    rel_teaser = res.json().get("relative_path", "")
    check(
        "Teaser + 30 秒 → 不进短视频目录",
        not rel_teaser.startswith("短视频"),
        rel_teaser,
    )

# ===== C. PATCH 重算：旧误标数据在编辑时被纠正 =====
print("\n[C] PATCH 重算 is_short")
db = SessionLocal()
rows = [
    # 存量误标：MixEdit 但 20 秒 → 历史 is_short=True
    MusicVideo(
        name="legacy-mix-20s",
        ingestion_status="library",
        video_type="MixEdit",
        video_types=["MixEdit"],
        duration=20,
        file_path="legacy-mix-20s.mp4",
        file_name="legacy-mix-20s.mp4",
        is_short=True,
    ),
    # 真短视频：类型 ShortVideo，时长很长也应算短视频
    MusicVideo(
        name="short-type-300s",
        ingestion_status="library",
        video_type="ShortVideo",
        video_types=["ShortVideo"],
        duration=300,
        file_path="short-type-300s.mp4",
        file_name="short-type-300s.mp4",
        is_short=True,
    ),
    # 非短视频类型 + 45 秒 + 无标记
    MusicVideo(
        name="special-45s",
        ingestion_status="library",
        video_type="SpecialVideo",
        video_types=["SpecialVideo"],
        duration=45,
        file_path="special-45s.mp4",
        file_name="special-45s.mp4",
        is_short=None,
    ),
    # 第二条存量误标：留给 CLI 重算用例
    MusicVideo(
        name="legacy-teaser-30s",
        ingestion_status="library",
        video_type="Teaser",
        video_types=["Teaser"],
        duration=30,
        file_path="legacy-teaser-30s.mp4",
        file_name="legacy-teaser-30s.mp4",
        is_short=True,
    ),
]
for r in rows:
    db.add(r)
db.flush()
ids = {r.name: r.id for r in rows}
db.commit()
db.close()

with TestClient(app) as client:
    rid = ids["legacy-mix-20s"]
    res = client.patch(f"/api/music-videos/{rid}", json={"name": "legacy-mix-20s-edited"})
    check("PATCH 200", res.status_code == 200, str(res.status_code))
    check(
        "MixEdit + 20 秒 → 编辑后 is_short 被纠正为 False",
        res.json().get("is_short") is False,
        str(res.json().get("is_short")),
    )

    rid = ids["short-type-300s"]
    res = client.patch(f"/api/music-videos/{rid}", json={"name": "short-type-300s-edited"})
    check(
        "ShortVideo 类型 + 300 秒 → 编辑后仍 is_short=True",
        res.json().get("is_short") is True,
        str(res.json().get("is_short")),
    )

    rid = ids["special-45s"]
    res = client.patch(f"/api/music-videos/{rid}", json={"name": "special-45s-edited"})
    check(
        "SpecialVideo + 45 秒 → 编辑后 is_short=False",
        res.json().get("is_short") is False,
        str(res.json().get("is_short")),
    )

    # 显式携带 is_short 的调用方仍可覆盖（API 逃生口，UI 不会传）
    rid = ids["legacy-mix-20s"]
    res = client.patch(f"/api/music-videos/{rid}", json={"is_short": True})
    check(
        "显式 is_short=true 仍被尊重",
        res.json().get("is_short") is True,
        str(res.json().get("is_short")),
    )
    client.patch(f"/api/music-videos/{rid}", json={"is_short": False})

# ===== D. 列表过滤：短视频列表只认类型 =====
print("\n[D] /api/music-videos?is_short= 过滤")
with TestClient(app) as client:
    res = client.get(
        "/api/music-videos",
        params={"ingestion_status": "library", "is_short": True, "page_size": 100},
    )
    check("is_short=true 200", res.status_code == 200, str(res.status_code))
    shorts = {i["name"] for i in res.json().get("items", [])}
    # 存量误标的 legacy-teaser-30s（Teaser + 30 秒）此刻仍在列表里 —— 正是 CLI 要修的存量问题
    check(
        "短视频列表只按类型判定，不再收 20 秒 MixEdit",
        shorts == {"short-type-300s-edited", "legacy-teaser-30s"},
        str(shorts),
    )

    res = client.get(
        "/api/music-videos",
        params={"ingestion_status": "library", "is_short": False, "page_size": 100},
    )
    longs = {i["name"] for i in res.json().get("items", [])}
    check(
        "非短视频列表含 20 秒 MixEdit",
        "legacy-mix-20s-edited" in longs,
        str(longs),
    )
    check(
        "非短视频列表含 45 秒 SpecialVideo",
        "special-45s-edited" in longs,
        str(longs),
    )

# ===== E. CLI 一次性重算存量数据 =====
print("\n[E] app.cli fix-short-flags 重算")
rc = fix_short_flags()
check("CLI 返回 0", rc == 0, str(rc))
db = SessionLocal()
teaser = db.get(MusicVideo, ids["legacy-teaser-30s"])
check(
    "Teaser + 30 秒（存量 is_short=True）→ 重算为 False",
    teaser.is_short is False,
    str(teaser.is_short),
)
short_row = db.get(MusicVideo, ids["short-type-300s"])
check("ShortVideo 行重算后仍为 True", short_row.is_short is True, str(short_row.is_short))
db.close()

# ===== F. CLI 重算后：存量误标整体退出短视频列表 =====
print("\n[F] 重算后的短视频列表")
with TestClient(app) as client:
    res = client.get(
        "/api/music-videos",
        params={"ingestion_status": "library", "is_short": True, "page_size": 100},
    )
    shorts = {i["name"] for i in res.json().get("items", [])}
    check(
        "重算后短视频列表只剩 ShortVideo 类型",
        shorts == {"short-type-300s-edited"},
        str(shorts),
    )

print(f"\n通过 {PASS} / {PASS + FAIL}")
if FAIL:
    print(f"失败 {FAIL}")
sys.exit(1 if FAIL else 0)
