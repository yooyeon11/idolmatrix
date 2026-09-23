"""入库 AI：类型映射、URL 规整、响应抽取、简介截断、歌曲/组合查重。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="ai_ingest_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.song import Song  # noqa: E402
from app.services.ai_service import (  # noqa: E402
    _clip_video_info,
    _infer_fancam_type,
    _normalize,
    _skip_json_object,
    completions_url,
    fill_saved_ai_cfg,
    message_text,
)
from app.services.app_settings import write_sections  # noqa: E402
from app.services.ai_service import _canonical_video_type  # noqa: E402
from app.services.name_match import fuzzy_groups, fuzzy_songs  # noqa: E402

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


print("== 1. completions URL ==")
check(
    "裸域名补 /v1",
    completions_url("https://api.openai.com") == "https://api.openai.com/v1/chat/completions",
    completions_url("https://api.openai.com"),
)
check(
    "已有 /v1 不重复",
    completions_url("https://api.openai.com/v1") == "https://api.openai.com/v1/chat/completions",
)
check(
    "Gemini 原生地址走 openai 兼容层",
    completions_url("https://generativelanguage.googleapis.com/v1beta")
    == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
)
check(
    "已是完整 completions 地址原样返回",
    completions_url("http://127.0.0.1:11434/v1/chat/completions")
    == "http://127.0.0.1:11434/v1/chat/completions",
)

print("== 2. json_object 跳过 ==")
check("Gemini 跳过 json_object", _skip_json_object({"provider": "gemini"}))
check("Ollama 跳过 json_object", _skip_json_object({"provider": "ollama"}))
check(
    "OpenAI 兼容不跳过",
    not _skip_json_object({"provider": "openai-compatible", "base_url": "https://api.openai.com/v1"}),
)

print("== 3. 响应抽取 ==")
check(
    "普通 content 字符串",
    message_text({"choices": [{"message": {"content": '{"a":1}'}}]}) == '{"a":1}',
)
check(
    "content 数组",
    message_text({"choices": [{"message": {"content": [{"type": "text", "text": '{"a":1}'}]}}]})
    == '{"a":1}',
)
check(
    "reasoning_content 兜底",
    message_text({"choices": [{"message": {"content": "", "reasoning_content": '{"a":1}'}}]})
    == '{"a":1}',
)

print("== 4. 直拍类型 ==")
check("笼统 FanCam → Fancam", _canonical_video_type("FanCam") == "Fancam")
check("组合直拍同义词", _canonical_video_type("组合直拍") == "Fancam")
check("官方直拍同义词", _canonical_video_type("官方直拍") == "Fancam")
check("group fancam 推断", _infer_fancam_type("groupfancam") == "Fancam")
norm = _normalize({"video_type": "FanCam"})
check(
    "笼统 FanCam 归一成 Fancam",
    norm.get("video_type") == "Fancam" and "Fancam" in (norm.get("video_types") or []),
    str(norm),
)
norm_g = _normalize({"video_type": "Group FanCam"})
check(
    "Group FanCam 统一归 Fancam（不细分）",
    norm_g.get("video_type") == "Fancam",
    str(norm_g),
)
norm_b = _normalize({"video_types": ["BehindTheScenes"]})
check(
    "花絮归一成 SpecialVideo",
    "SpecialVideo" in (norm_b.get("video_types") or []),
    str(norm_b),
)

print("== 4b. 混剪类型 ==")
check("MIX混剪 直通", _canonical_video_type("MIX混剪") == "MixEdit")
check("MIX 精确同义词", _canonical_video_type("MIX") == "MixEdit")
check("Mix Edit 同义词", _canonical_video_type("Mix Edit") == "MixEdit")
check("混剪子串命中（打歌混剪）", _canonical_video_type("打歌混剪") == "MixEdit")
check("remix 不误判为混剪", _canonical_video_type("Remix") is None)
norm_mix = _normalize({"video_types": ["MIX混剪"]})
check(
    "混剪归一成 MixEdit",
    "MixEdit" in (norm_mix.get("video_types") or []),
    str(norm_mix),
)

print("== 5. 简介截断 ==")
clipped = _clip_video_info({"title": "t", "description": "x" * 2500, "resolved_relations": {}})
check("丢 resolved_relations", "resolved_relations" not in clipped)
check("简介截到 2000+省略号", clipped["description"].endswith("…") and len(clipped["description"]) == 2001)

print("== 6. 歌曲/组合模糊 ==")
db = SessionLocal()
db.add(Song(name="Girls Never Die", english_name="Girls Never Die", korean_name="걸스 네버 다이"))
db.add(Group(name="tripleS", chinese_name="三重奏", english_name="tripleS"))
db.commit()
db.close()

db = SessionLocal()
songs = fuzzy_songs(db, "girlsneverdie")
check("英文曲名能糊到韩英混写", songs and songs[0][0].name == "Girls Never Die", str(songs[:1]))
groups = fuzzy_groups(db, "triples")
check("组合忽略大小写", groups and groups[0][0].name == "tripleS", str(groups[:1]))
db.close()

print("== 7. null 字段不进归一化结果 ==")
empty = _normalize({"name": None, "event_name": "", "chinese_description": "  "})
check("空字符串和 null 不出现", empty == {}, str(empty))
filled = _normalize({"name": "240511 tripleS - GND", "performance_date": "2024-05-11"})
check("有值才出现", "name" in filled and "performance_date" in filled, str(filled))

print("== 8. 空 Key 回退已保存配置 ==")
db = SessionLocal()
write_sections(
    db,
    {
        "ingest_ai": {
            "enabled": True,
            "provider": "openai-compatible",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-saved",
            "model": "gpt-4o-mini",
        }
    },
)
db.close()
filled = fill_saved_ai_cfg(
    {
        "provider": "openai-compatible",
        "base_url": "https://api.example.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
    }
)
check("空 api_key 用已保存", filled.get("api_key") == "sk-saved", str(filled.get("api_key")))
filled_keep = fill_saved_ai_cfg({"api_key": "sk-typed", "base_url": "https://x", "model": "m"})
check("请求带 Key 优先", filled_keep.get("api_key") == "sk-typed")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
