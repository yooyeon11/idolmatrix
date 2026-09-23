"""待整理列表 is_duplicate 读侧复核（v3.2.27）。

背景：is_duplicate 是扫描时落库的快照。失效视频清空（软删）后，
待整理列表若直接读快照会残留「已存在」标记。/api/library/scan 现在读侧按当前库复核。

只测服务层（scan_endpoint 的核心逻辑同款查询），不起 HTTP 服务。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="scan_dup_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.incoming_file import IncomingFile  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from sqlalchemy import select  # noqa: E402

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


def recheck(db):
    """与 app/api/library.py::scan_endpoint 读侧复核同款逻辑。"""
    from app.api.library import scan_endpoint

    items = scan_endpoint(db)
    return {i.path: i.is_duplicate for i in items}


db = SessionLocal()

KEY = "/incoming/OH MY GIRL ARIN fancam.mp4"
NAME = "OH MY GIRL ARIN fancam.mp4"
SIZE = 123456789

row = IncomingFile(file_path=KEY, file_name=NAME, file_size=SIZE, is_duplicate=True)
db.add(row)
db.commit()

print("== 1. 库里没有对应记录 → 复核为 False（旧快照残留清除） ==")
result = recheck(db)
check("无 mv → False", result.get(KEY) is False, result)
db.refresh(row)
check("快照已回写", row.is_duplicate is False, f"row.is_duplicate={row.is_duplicate}")

print("== 2. 库里有同名同大小 → True ==")
mv = MusicVideo(name=NAME, file_name=NAME, file_size=SIZE, ingestion_status="library")
db.add(mv)
db.commit()
result = recheck(db)
check("有 mv → True", result.get(KEY) is True, result)

print("== 3. 失效清空（软删）→ False（本次修复的场景） ==")
mv.soft_delete()
db.commit()
result = recheck(db)
check("mv 软删 → False", result.get(KEY) is False, result)

print("== 4. 名同大小不同 → False（口径与扫描一致：文件名+大小） ==")
mv.deleted_at = None
mv.file_size = SIZE + 1
db.commit()
result = recheck(db)
check("大小不同 → False", result.get(KEY) is False, result)

db.close()
print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
