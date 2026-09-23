"""人名归一化：空格/大小写/别名视为同一人；组合分析成员解析。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="name_match_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.services.ai_service import _normalize_group_members  # noqa: E402
from app.services.name_match import (  # noqa: E402
    fuzzy_artists,
    match_artist_exact,
    merge_artist_aliases,
    normalize_person_name,
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


print("== 1. 归一化 ==")
check(
    "空格视为同一人",
    normalize_person_name("JEONG SAE BI") == normalize_person_name("JEONG SAEBI"),
)
check(
    "连字符",
    normalize_person_name("Jeong-Sae-Bi") == normalize_person_name("Jeong Saebi"),
)
check("大小写", normalize_person_name("Kotone") == normalize_person_name("kotone"))
check("韩文保留", normalize_person_name("정세비") == "정세비")
check("空", normalize_person_name("  ") == "")


print("== 2. 库内精确/模糊匹配 ==")
db = SessionLocal()
a = Artist(
    name="JEONG SAEBI",
    stage_name="SAEBI",
    korean_name="정세비",
    aliases=["Jeong Saebi"],
)
db.add(a)
db.add(Artist(name="Kotone", korean_name="코토네"))
db.commit()
db.refresh(a)
aid = a.id
db.close()

db = SessionLocal()
hit = match_artist_exact(db, "JEONG SAE BI")
check("空格变体精确命中", hit is not None and hit.id == aid, str(getattr(hit, "name", None)))
hit = match_artist_exact(db, "정세비")
check("韩文名命中", hit is not None and hit.id == aid)
hit = match_artist_exact(db, extra=["jeong-sae-bi"])
check("extra 连字符命中", hit is not None and hit.id == aid)
hits = fuzzy_artists(db, "JEONG SAE BI")
check("fuzzy 首位即本人", hits and hits[0][0].id == aid and hits[0][1] >= 0.999, str(hits[:2]))
hits = fuzzy_artists(db, "nobody-here-xyz")
check("无关人名无命中", hits == [])
row = db.get(Artist, aid)
changed = merge_artist_aliases(row, ["JEONG SAE BI", "JEONG SAEBI"])
check("主名的空格变体不进别名", changed is False)
changed = merge_artist_aliases(row, ["새비"])
db.commit()
check("新写法写入别名", changed is True and "새비" in (row.aliases or []))
db.close()


print("== 3. 成员 JSON 解析 ==")
parsed = _normalize_group_members(
    {
        "members": [
            {
                "name": "Jeong Saebi",
                "korean_name": "정세비",
                "aliases": ["JEONG SAEBI", "JEONG SAE BI"],
                "positions": ["Vocal"],
                "status": "Active",
            },
            {"name": "JEONG SAE BI", "korean_name": "정세비"},
            {"name": ""},
            "skip-me",
        ]
    }
)
check("解析到成员", parsed is not None and len(parsed) == 1, str(parsed))
check("别名保留", parsed is not None and "JEONG SAE BI" in (parsed[0].get("aliases") or []))
check("默认 Active", parsed is not None and parsed[0].get("status") == "Active")

db = SessionLocal()
parsed2 = _normalize_group_members(
    {"members": [{"name": "JEONG SAE BI", "korean_name": "정세비"}]},
    db,
)
db.close()
check(
    "解析时挂上已有艺人",
    parsed2 is not None
    and parsed2[0].get("matched_artist_id") == aid
    and parsed2[0].get("matched_artist_name") == "JEONG SAEBI",
    str(parsed2),
)

print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
