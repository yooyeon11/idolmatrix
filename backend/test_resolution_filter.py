"""浏览页分辨率筛选：SQL 侧档位过滤与 Python 侧归档必须同口径。

背景：统计页「分辨率构成」按短边 min(width,height) 归档（竖屏直拍 1080×1920 不能算 4K）。
浏览页筛选若换了口径，就会出现「菜单写着 1080P 12 条、点进去却是别的结果」。
SQL 侧不依赖方言特有的 least()/min()：短边 >= lo ⟺ 宽高都 >= lo；短边 < hi ⟺ 宽高任一 < hi。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_TMP = Path(tempfile.mkdtemp(prefix="resolution_filter_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_TMP / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_TMP / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_TMP / "incoming")
os.environ["AUTH_REQUIRED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.api.music_videos import _filter_resolution  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.services.video_meta import (  # noqa: E402
    RESOLUTION_TIERS,
    RESOLUTION_UNKNOWN,
    resolution_ranges,
    resolution_tier,
)

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


# (键, 宽, 高, 短视频?)；覆盖每个档位 + 两种缺字段形态 + 一条短视频
SAMPLES = [
    ("s-8k", 7680, 4320, False),
    ("s-4k-portrait", 2160, 3840, False),
    ("s-2k", 2560, 1440, False),
    ("s-1080p-landscape", 1920, 1080, False),
    ("s-1080p-portrait", 1080, 1920, False),
    ("s-720p", 1280, 720, False),
    ("s-480p", 854, 480, False),
    ("s-360p", 640, 360, False),
    ("s-240p", 426, 240, False),
    ("s-sd", 256, 144, False),
    ("s-unknown-both", None, None, False),
    ("s-unknown-width", 1920, None, False),
    ("s-short-1080p", 1920, 1080, True),
]

db = SessionLocal()
by_name: dict[str, int] = {}
for i, (name, width, height, is_short) in enumerate(SAMPLES):
    mv = MusicVideo(
        name=name,
        ingestion_status="library",
        video_type="ShortVideo" if is_short else "OfficialMV",
        file_path=f"clip-{i}.mp4",
        file_name=f"clip-{i}.mp4",
        width=width,
        height=height,
        is_short=True if is_short else None,
    )
    db.add(mv)
    db.flush()
    by_name[name] = mv.id
db.commit()
db.close()


def sql_ids(resolution: str, *, exclude_shorts: bool = False) -> set[int]:
    s = SessionLocal()
    try:
        stmt = select(MusicVideo.id).where(
            MusicVideo.deleted_at.is_(None),
            MusicVideo.ingestion_status == "library",
        )
        if exclude_shorts:
            stmt = stmt.where(MusicVideo.is_short.is_not(True))
        stmt = _filter_resolution(stmt, resolution)
        return set(s.scalars(stmt))
    finally:
        s.close()


def py_ids(tier: str, *, exclude_shorts: bool = False) -> set[int]:
    out = set()
    for name, width, height, is_short in SAMPLES:
        if exclude_shorts and is_short:
            continue
        if resolution_tier(width, height) == tier:
            out.add(by_name[name])
    return out


print("--- 1. 每个档位：SQL 结果 == Python 归档结果 ---")
for key, _floor in RESOLUTION_TIERS:
    got = sql_ids(key)
    want = py_ids(key)
    check(f"{key} 档位一致", got == want, f"sql={len(got)} py={len(want)}")

check(
    "unknown 档只命中缺宽高",
    sql_ids(RESOLUTION_UNKNOWN) == py_ids(RESOLUTION_UNKNOWN)
    and by_name["s-unknown-both"] in sql_ids(RESOLUTION_UNKNOWN)
    and by_name["s-unknown-width"] in sql_ids(RESOLUTION_UNKNOWN)
    and by_name["s-1080p-landscape"] not in sql_ids(RESOLUTION_UNKNOWN),
    str(len(sql_ids(RESOLUTION_UNKNOWN))),
)

print("--- 2. 竖屏直拍按短边归档，不被算成 4K ---")
check(
    "1080×1920 落 1080p 而非 4k",
    by_name["s-1080p-portrait"] in sql_ids("1080p")
    and by_name["s-1080p-portrait"] not in sql_ids("4k"),
)
check(
    "2160×3840 落 4k",
    by_name["s-4k-portrait"] in sql_ids("4k")
    and by_name["s-4k-portrait"] not in sql_ids("1080p"),
)

print("--- 3. 多选 / 无效值 / 组合条件 ---")
multi = sql_ids("4k,1080p")
check(
    "多选 = 并集",
    multi == sql_ids("4k") | sql_ids("1080p"),
    str(len(multi)),
)
check("无效档位名不过滤（等价全量）", sql_ids("bogus") == sql_ids(""), str(len(sql_ids("bogus"))))
check("空串不过滤", sql_ids("") == sql_ids(", ,") == sql_ids(""),
      str(len(sql_ids(""))))
check(
    "is_short=false 与档位叠加",
    by_name["s-short-1080p"] not in sql_ids("1080p", exclude_shorts=True)
    and by_name["s-short-1080p"] in sql_ids("1080p"),
)

print("--- 4. 档位表本身：连续、不重叠、覆盖全部正短边 ---")
ranges = resolution_ranges()
check("档位数与 RESOLUTION_TIERS 一致", len(ranges) == len(RESOLUTION_TIERS), str(len(ranges)))
check(
    "最高档无上限，其余上界 = 相邻高档地板",
    ranges[0][2] is None and all(r[2] == ranges[i - 1][1] for i, r in enumerate(ranges) if i > 0),
    str(ranges),
)
check("最低档下界为 1（短边 > 0 必落入某档）", ranges[-1][1] == 1, str(ranges[-1]))


def covered(short: int) -> bool:
    return any(lo <= short and (hi is None or short < hi) for _key, lo, hi in ranges)


check("短边 1/479/1080/4000/9999 均被覆盖", all(covered(s) for s in (1, 479, 1080, 4000, 9999)))
check("0 与负数不落任何档（归 unknown）", not covered(0))

print("--- 5. 接口层：facets / 列表 / 文件夹浏览 ---")
with TestClient(app) as client:
    res = client.get(
        "/api/music-videos/resolution-facets",
        params={"ingestion_status": "library", "is_short": False},
    )
    check("facets 200", res.status_code == 200, str(res.status_code))
    items = res.json().get("items", [])
    keys = [i["key"] for i in items]
    counts = {i["key"]: i["count"] for i in items}
    check(
        "facets 顺序为高清→低清、unknown 收尾",
        keys == [k for k, _f in RESOLUTION_TIERS] + [RESOLUTION_UNKNOWN],
        str(keys),
    )
    check(
        "facets 计数与归档一致",
        counts.get("1080p") == 2
        and counts.get("unknown") == 2
        and all(i["count"] > 0 for i in items),
        str(counts),
    )
    check("facets 不含零条档位", all(i["count"] > 0 for i in items), str(items))

    res = client.get(
        "/api/music-videos/resolution-facets",
        params={"ingestion_status": "library"},
    )
    counts_all = {i["key"]: i["count"] for i in res.json().get("items", [])}
    check("facets 不传 is_short 时含短视频", counts_all.get("1080p") == 3, str(counts_all))

    res = client.get(
        "/api/music-videos",
        params={"ingestion_status": "library", "resolution": "1080p", "page_size": 100},
    )
    check("列表 200", res.status_code == 200, str(res.status_code))
    got_ids = {i["id"] for i in res.json()["items"]}
    check(
        "列表 resolution=1080p 命中且仅命中 1080p",
        got_ids == py_ids("1080p") or got_ids == py_ids("1080p") | {by_name["s-short-1080p"]},
        str(sorted(got_ids)),
    )
    check(
        "列表 total 与命中条数一致",
        res.json()["total"] == len(got_ids),
        f"total={res.json()['total']} items={len(got_ids)}",
    )

    res = client.get(
        "/api/music-videos",
        params={"ingestion_status": "library", "resolution": "8k", "page_size": 100},
    )
    check(
        "列表 resolution=8k 只有那一条",
        [i["name"] for i in res.json()["items"]] == ["s-8k"],
        str([i["name"] for i in res.json()["items"]]),
    )

    res = client.get(
        "/api/music-videos/folder-browse",
        params={"prefix": "", "resolution": "unknown", "is_short": False, "page_size": 100},
    )
    check("folder-browse 200", res.status_code == 200, str(res.status_code))
    check(
        "folder-browse 带 resolution 过滤生效",
        res.json()["video_total"] == len(py_ids(RESOLUTION_UNKNOWN)),
        f"video_total={res.json()['video_total']}",
    )

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
