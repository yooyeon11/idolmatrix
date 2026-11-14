"""ai2 真实模型冒烟：只跑 artist_create 的 draft 步，重点检查 schema 遵从度。

用法：python smoke_ai2_real.py <base_url> <model> <api_key> [keyword] [kind]
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="ai2_real_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")
os.environ["STORAGE_AI_KNOWLEDGE_DIR"] = str(_tmp / "ai_knowledge")

base_url, model, api_key = sys.argv[1], sys.argv[2], sys.argv[3]
keyword = sys.argv[4] if len(sys.argv) > 4 else "Sullyoon"
kind = sys.argv[5] if len(sys.argv) > 5 else "artist"

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal, init_db  # noqa: E402
from app.services import app_settings  # noqa: E402

settings.ensure_directories()
init_db()

db = SessionLocal()
app_settings.write_sections(
    db,
    {"ai2": {"enabled": True, "provider": "openai-compatible", "base_url": base_url, "api_key": api_key, "model": model}},
)
db.commit()

from app.ai2 import tasks  # noqa: E402
from app.ai2.tasks import artist_create as ac  # noqa: E402

# 用固定信源夹具替代 QQ 音乐（本地未配置数据源），只验证 LLM 的 schema 遵从度
_fixture = [
    {"name": "Sullyoon", "singer_name": "Sullyoon", "artist": "NMIXX", "country": "韩国",
     "birthday": "2004-01-26", "debut": "2022-02-22", "alias": ["설윤", "Seol Yoon"]},
]
_orig_run_tool = ac.run_tool
def _fake_run_tool(name, ctx, params):
    if name == "qqmusic.search":
        return _fixture
    return _orig_run_tool(name, ctx, params)
ac.run_tool = _fake_run_tool

run = tasks.run_task("artist_create", db, {"kind": kind, "keyword": keyword, "dry_run": True})
print("=== RUN STATUS ===", run.get("status"), "| error:", run.get("error"))
print("=== RUN STEPS ===", run.get("steps"))
result = run.get("result") or {}
if not result:
    print("=== RAW RUN ===")
    print(json.dumps(run, ensure_ascii=False, default=str)[:2000])
    sys.exit(0)

print("=== RESULT KEYS ===", sorted(result.keys()))
print("=== CREATED ===", result.get("created"), "reason:", result.get("reason"))
draft = result.get("draft") or {}
print("=== DRAFT TYPES ===", {k: type(v).__name__ for k, v in draft.items()})
allowed = ac._ALLOWED_ARTIST_FIELDS if kind == "artist" else ac._ALLOWED_GROUP_FIELDS
unknown = [k for k in draft if k not in allowed]
print("=== DRAFT KEYS ===", sorted(draft.keys()))
print("=== UNKNOWN KEYS (应被清洗掉，正常为空) ===", unknown)
print("=== DRAFT ===")
print(json.dumps({k: str(v) for k, v in draft.items()}, ensure_ascii=False, indent=2))
print("=== ISSUES ===", json.dumps(result.get("issues") or [], ensure_ascii=False))
print("=== STEPS ===")
for s in result.get("steps") or []:
    print(" -", s.get("step"), s.get("ok"), str(s.get("detail"))[:120])
raw = result.get("draft_raw")
if raw:
    print("=== RAW KEYS ===", sorted(raw.keys()))
    print("=== RAW EXTRA (模型多给的字段) ===", [k for k in raw if k not in allowed and k not in ("issues", "evidence")])
