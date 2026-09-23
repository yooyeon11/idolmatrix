"""外观与显示跨设备同步（app_settings.appearance，2026-09-22）。

覆盖：默认值 / 全量读写 / 非法值归一化 / 与其它分区互不影响 / 部分字段提交的已知行为。
⚠ 用**临时 SQLite**，绝不碰真实库。
跑法：cd backend && python test_appearance_settings.py（不要交给 pytest 收集）
"""

import os
import tempfile
import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 临时库只建 app_settings 表 → 写 hero 分区时「封面焦点检测」会查不到 music_videos 表并打日志，
# 那是预期噪声（单条失败不影响保存），这里静音掉。
logging.disable(logging.ERROR)

from app.models.app_setting import AppSetting
from app.schemas.app_settings import AppSettingsPatch
from app.services.app_settings import read_all, write_sections

PASS = 0
FAIL = 0


def check(label: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  PASS {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}\n    got  = {got!r}\n    want = {want!r}")


tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
engine = create_engine(f"sqlite:///{tmp.name}")
AppSetting.__table__.create(engine)
Session = sessionmaker(bind=engine)
db = Session()

try:
    print("=== 1. 默认值（空库）===")
    got = read_all(db).appearance
    check("theme 默认 dark", got.theme, "dark")
    check("accent 默认 blue", got.accent, "blue")
    check("show_covers 默认 True", got.show_covers, True)
    check("home_hero_image 默认 avatar", got.home_hero_image, "avatar")

    print("\n=== 2. 全量写入 → 回读 ===")
    write_sections(
        db,
        {
            "appearance": {
                "theme": "light",
                "accent": "rose",
                "show_covers": False,
                "home_hero_image": "banner",
            }
        },
    )
    got = read_all(db).appearance
    check("theme 落库", got.theme, "light")
    check("accent 落库", got.accent, "rose")
    check("show_covers 落库", got.show_covers, False)
    check("home_hero_image 落库", got.home_hero_image, "banner")

    print("\n=== 3. 非法 / 历史值归一化 ===")
    for bad, expect in (
        ({"theme": "violet"}, "dark"),
        ({"theme": ""}, "dark"),
        ({"accent": "violet"}, "blue"),
        ({"accent": "green"}, "blue"),
        ({"home_hero_image": "test"}, "avatar"),
        ({"home_hero_image": None}, "avatar"),
    ):
        write_sections(db, {"appearance": {**read_all(db).appearance.model_dump(), **bad}})
        field = next(iter(bad.keys()))
        check(f"{field}={bad[field]!r} → {expect}", getattr(read_all(db).appearance, field), expect)

    write_sections(
        db,
        {"appearance": {**read_all(db).appearance.model_dump(), "show_covers": "no"}},
    )
    check("show_covers 非布尔 → True", read_all(db).appearance.show_covers, True)

    print("\n=== 4. 与其它分区互不影响 ===")
    write_sections(db, {"appearance": {"theme": "light", "accent": "blue", "show_covers": True, "home_hero_image": "avatar"}})
    write_sections(db, {"hero": {"video_ids": [7, 9], "stage_type": "video"}})
    check("写入 hero 后 appearance.theme 仍为 light", read_all(db).appearance.theme, "light")
    write_sections(db, {"appearance": {"theme": "dark", "accent": "rose", "show_covers": False, "home_hero_image": "banner"}})
    check("写 appearance 不动 hero.video_ids", read_all(db).hero.video_ids, [7, 9])
    check("写 appearance 不动 hero.stage_type", read_all(db).hero.stage_type, "video")

    print("\n=== 5. PATCH 语义：支持部分字段（exclude_unset 是递归的）===")
    patch = AppSettingsPatch.model_validate({"appearance": {"theme": "light", "accent": "blue", "show_covers": True, "home_hero_image": "avatar"}})
    check("sections() 只含 appearance", list(patch.sections().keys()), ["appearance"])
    partial = AppSettingsPatch.model_validate({"appearance": {"theme": "light"}})
    dumped = partial.sections()["appearance"]
    check("只传 theme 就只带 theme（内层不会被默认值污染）", dumped, {"theme": "light"})
    # 部分提交只改传了的字段，其余保持库里现值
    write_sections(
        db,
        {"appearance": {"theme": "dark", "accent": "blue", "show_covers": True, "home_hero_image": "avatar"}},
    )
    write_sections(db, {"appearance": {"show_covers": False}})
    after = read_all(db).appearance
    check("部分提交改 show_covers", after.show_covers, False)
    check("部分提交不动 theme", after.theme, "dark")
    check("部分提交不动 accent", after.accent, "blue")
    check("部分提交不动 home_hero_image", after.home_hero_image, "avatar")

    print("\n=== 6. 存量库缺 appearance 行（老库升级）===")
    db.query(AppSetting).filter(AppSetting.key == "appearance").delete()
    db.commit()
    got = read_all(db).appearance
    check("缺行时回落默认 theme", got.theme, "dark")
    check("缺行时回落默认 accent", got.accent, "blue")
finally:
    db.close()
    engine.dispose()
    try:
        os.unlink(tmp.name)
    except OSError:
        pass

print(f"\n{PASS} PASS / {FAIL} FAIL")
raise SystemExit(1 if FAIL else 0)
