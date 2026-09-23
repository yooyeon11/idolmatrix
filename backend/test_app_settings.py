"""跨设备应用设置：读写与缺省补齐。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="app_settings_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.security import is_kept_secret, is_private_client_ip  # noqa: E402
from app.models.app_setting import AppSetting  # noqa: E402
from app.services.app_settings import (  # noqa: E402
    read_all,
    read_ingest_match_mode,
    redact_for_client,
    write_sections,
)
from app.services.auth_service import AuthError, bootstrap_owner  # noqa: E402

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


db = SessionLocal()
try:
    empty = read_all(db)
    check("空库有默认分区", empty.ingest_ai.enabled is False and empty.hero.stage_type == "artist")
    check("不再暴露 ingest 分区（内容与匹配栏已下线）", not hasattr(empty, "ingest"))
    check("详情页主图字段已移除", "detail_hero_media" not in empty.hero.model_dump())
    check("自动化能力三开关已移除", "auto_describe" not in empty.ingest_ai.model_dump())
    check("不再暴露首页标题 AI 分区", not hasattr(empty, "home_title_ai"))

    saved = write_sections(
        db,
        {
            "hero": {"title": "焦点只留给一个人", "tagline": "姬振把歌唱近了", "sub": "白光很硬。", "video_id": 12},
            "ingest_ai": {
                "enabled": True,
                "provider": "openai-compatible",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test",
                "model": "gpt-4o-mini",
            },
        },
    )
    check("旧首页标题字段不再回读", "title" not in saved.hero.model_dump())
    check("写入 video_id", saved.hero.video_id == 12)
    check("写入 API Key", saved.ingest_ai.api_key == "sk-test")
    check("未写的分区保持默认", saved.mtphotos.enabled is False)

    db2 = SessionLocal()
    again = read_all(db2)
    check("重新读取仍在", again.hero.video_id == 12 and again.ingest_ai.model == "gpt-4o-mini")
    db2.close()

    partial = write_sections(db, {"ingest_ai": {"model": "gpt-4o"}})
    check("部分更新不丢 Key", partial.ingest_ai.api_key == "sk-test" and partial.ingest_ai.model == "gpt-4o")
    blank = write_sections(db, {"ingest_ai": {"api_key": "", "model": "gpt-4o-mini"}})
    check("空 Key 视为不改", blank.ingest_ai.api_key == "sk-test" and blank.ingest_ai.model == "gpt-4o-mini")
    masked = write_sections(db, {"ingest_ai": {"api_key": "********"}})
    check("占位符不覆盖 Key", masked.ingest_ai.api_key == "sk-test")
    public = redact_for_client(masked)
    check("回读脱敏为空", public.ingest_ai.api_key == "")
    check("回读标记已配置", public.ingest_ai.api_key_set is True)
    check("局域网 IP", is_private_client_ip("192.168.1.8") and is_private_client_ip("testclient"))
    check("公网 IP", not is_private_client_ip("8.8.8.8"))
    check("空串视为保留密钥", is_kept_secret("") and is_kept_secret("********"))
    try:
        bootstrap_owner(
            "pubowner", "secret123", "", token="", ip="8.8.8.8", user_agent="test"
        )
        check("公网无口令初始化应失败", False)
    except AuthError as e:
        check("公网无口令初始化 403", e.status == 403, e.detail)
    check("行已落库", db.get(AppSetting, "hero") is not None)

    # 详情页主图设置已移除：写入 detail_hero_media 不再产生该字段（固定头像）
    media = write_sections(db, {"hero": {"detail_hero_media": "banner"}})
    check("写入详情页主图不再生效", "detail_hero_media" not in media.hero.model_dump())
    # 「自动化能力」三开关已移除：写入也不产生字段，存量值不回读
    auto_write = write_sections(db, {"ingest_ai": {"auto_describe": True, "auto_tag": True}})
    check(
        "写入自动化能力不再生效",
        not any(
            k in auto_write.ingest_ai.model_dump() for k in ("auto_describe", "auto_tag", "auto_subject")
        ),
    )
    # 「内容与匹配」栏下线：ingest 分区不在白名单里，写入被忽略、回读不再出现，档位恒为 strict
    ingest_saved = write_sections(db, {"ingest": {"match_mode": "normal"}})
    check("写入已下线的 ingest 分区不生效", not hasattr(ingest_saved, "ingest"))
    check("读取口径恒为严格", read_ingest_match_mode(db) == "strict")
    stripped = write_sections(
        db, {
            "hero": {
                "title": "旧文案",
                "display_mode": "classic",
                "home_theme": "magazine",
                "home_magazine_style": "panel",
                "detail_hero_preview": True,
                "detail_hero_media": "banner",
            }
        }
    )
    dumped = stripped.hero.model_dump()
    check("旧首页主题字段不再回读", "home_theme" not in dumped)
    check("旧杂志样式字段不再回读", "home_magazine_style" not in dumped)
    check("旧 Hero 预览开关不再回读", "detail_hero_preview" not in dumped)
    check("旧详情页主图字段不再回读", "detail_hero_media" not in dumped)
    check("旧展示模式字段不再回读", "display_mode" not in dumped)
    check("旧 tagline 字段不再回读", "tagline" not in dumped)
finally:
    db.close()

print(f"\n===== 结果: PASS={PASS} FAIL={FAIL} =====")
sys.exit(0 if FAIL == 0 else 1)
