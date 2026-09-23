# -*- coding: utf-8 -*-
"""preview-path 端点回归：草稿歌曲（库里还没有、入库时才建）计入串烧判定。

场景：AI 入库识别出多首新歌（如「260823 ODD YOUTH Summer Part 1」busking），
曲目行挂为「待新建」草稿。预览必须与入库同口径（入库先建歌再算路径），
否则表单预览不显示串烧路径。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="preview_draft_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")
os.environ["AUTH_REQUIRED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.song import Song  # noqa: E402

Base.metadata.create_all(bind=engine)
client = TestClient(app)

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


# 库里只有组合和一首歌，另一首是新歌（模拟半新场景）
db = SessionLocal()
db.add(Group(name="ODD YOUTH"))
song_a = Song(name="Song A")
db.add(song_a)
db.commit()
db.refresh(song_a)
song_a_id = song_a.id
db.close()

base = {
    "file_name": "260823 ODD YOUTH Summer Part 1.mp4",
    "title": "260823 ODD YOUTH - Song A & Song B [COEX ON STAGE Busking PersonalFancam]",
    "video_type": "PersonalFancam",
    "video_types": ["PersonalFancam"],
    "is_short": False,
    "performance_date": "2026-08-23",
    "song_ids": [],
    "group_ids": [1],
}

# ① 两首草稿歌 → 串烧（与入库最终落盘同口径）
r = client.post("/api/library/preview-path", json={**base, "draft_song_names": ["Song A", "Song B"]})
check("两首草稿歌触发串烧", r.status_code == 200 and r.json()["relative_path"].startswith("串烧/ODD YOUTH/260823/"), r.json().get("relative_path"))

# ② 一首已关联 + 一首草稿 → 仍触发串烧
r = client.post("/api/library/preview-path", json={**base, "song_ids": [song_a_id], "draft_song_names": ["Song B"]})
check("草稿歌计入曲数（1+1=2 触发）", r.status_code == 200 and r.json()["relative_path"].startswith("串烧/ODD YOUTH/260823/"), r.json().get("relative_path"))

# ③ 仅一首草稿 → 不触发串烧，走个人直拍常规路径
r = client.post("/api/library/preview-path", json={**base, "draft_song_names": ["Song A"]})
check("单曲草稿走常规路径", r.status_code == 200 and not r.json()["relative_path"].startswith("串烧/"), r.json().get("relative_path"))

# ④ 无草稿字段（旧前端兼容）→ 行为不变
r = client.post("/api/library/preview-path", json={**base})
check("不传草稿字段行为不变", r.status_code == 200 and not r.json()["relative_path"].startswith("串烧/"), r.json().get("relative_path"))

print(f"\n===== 结果: PASS={PASS} FAIL={FAIL} =====")
sys.exit(0 if FAIL == 0 else 1)
