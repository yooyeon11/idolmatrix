# -*- coding: utf-8 -*-
"""博主固定视频类型规则：存储归一化、按上传人查表、AI 被规则覆盖。

规则来自「设置 · 资料库 · 博主管理」：命中上传人时，待整理自动选中该视频类型，
AI 不再判断类型；未配置的博主照旧交给 AI。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="uploader_rules_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")
os.environ["AUTH_REQUIRED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.services.app_settings import (  # noqa: E402
    find_uploader_types,
    read_all,
    read_uploader_rules,
    uploader_type_map,
    write_sections,
    write_uploader_rules,
)

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


db = SessionLocal()
try:
    # ---- 默认空态 ----
    empty = read_all(db)
    check("默认分区存在且为空", empty.uploader_rules.rules == [])
    check("未配置时查表为空", find_uploader_types(db, "NewJeans") == [])
    check("空上传人不报错", find_uploader_types(db, None) == [] and find_uploader_types(db, "  ") == [])

    # ---- 归一化：去空名 / 丢空类型 / 重名去重 / 过滤非法类型 ----
    saved = write_uploader_rules(
        db,
        [
            {"name": "  NewJeans  ", "video_types": ["PerformanceVideo"]},
            {"name": "", "video_types": ["Fancam"]},
            {"name": "无类型频道", "video_types": []},
            {"name": "newjeans", "video_types": ["SpecialStage"]},  # 重名（忽略大小写）
            {"name": "M2", "video_types": ["SpecialStage", "SpecialStage", "不存在的类型"]},
            {"name": "FanCam TV", "video_types": ["Fancam", "NopeType"]},
            "不是字典",
        ],
    )
    names = [r["name"] for r in saved]
    check("空名被丢弃", "" not in names)
    check("未选类型的行被丢弃", "无类型频道" not in names)
    check("重名（忽略大小写）去重保首个", names.count("NewJeans") == 1 and len(names) == 3, str(names))
    check("名字首尾空白被裁剪", names[0] == "NewJeans")
    m2 = next(r for r in saved if r["name"] == "M2")
    check("同一条内类型去重 + 过滤非法值", m2["video_types"] == ["SpecialStage"], str(m2))
    fancam_tv = next(r for r in saved if r["name"] == "FanCam TV")
    check("非法类型被剔除、合法类型保留", fancam_tv["video_types"] == ["Fancam"], str(fancam_tv))
    check("非字典项被忽略", all(isinstance(r, dict) for r in saved))

    # ---- 按上传人查表：忽略大小写与首尾空白 ----
    check("精确命中", find_uploader_types(db, "NewJeans") == ["PerformanceVideo"])
    check("大小写不敏感", find_uploader_types(db, "newjeans") == ["PerformanceVideo"])
    check("首尾空白不敏感", find_uploader_types(db, "  M2 ") == ["SpecialStage"])
    check("未配置的博主交给 AI", find_uploader_types(db, "某个粉丝频道") == [])
    tmap = uploader_type_map(db)
    check("查表映射键为小写", "newjeans" in tmap and "NewJeans" not in tmap)

    # ---- 与 app-settings 分区同源（前端也可走 PATCH 写入） ----
    via_section = write_sections(
        db,
        {"uploader_rules": {"rules": [{"name": "官方频道", "video_types": ["OfficialMV", "Teaser"]}]}},
    )
    check("分区写入后回读一致", via_section.uploader_rules.rules[0].name == "官方频道")
    check("分区写入后查表生效", find_uploader_types(db, "官方频道") == ["OfficialMV", "Teaser"])
    check("原有规则被整体替换", read_uploader_rules(db) == [{"name": "官方频道", "video_types": ["OfficialMV", "Teaser"]}])
    blank = write_sections(db, {"uploader_rules": {"rules": [{"name": "清空我", "video_types": []}]}})
    check("清空类型 = 取消固定", blank.uploader_rules.rules == [])

    # ---- AI 覆盖：命中规则时用规则类型顶掉 AI 判断 ----
    # ⚠ 真实链路里 suggest_ingest() 返回的是 _normalize() 出来的 **普通 dict**，不是 pydantic 模型。
    # 这里必须喂同样形态，否则「注解与测试互相掩护」，线上 dict 一赋值属性就 500（v3.2.6 的缺陷）。
    from app.api.ai import _apply_uploader_rule
    from app.services.ai_service import _normalize

    write_uploader_rules(
        db,
        [
            {"name": "官方舞台频道", "video_types": ["PerformanceVideo"]},
            {"name": "特别舞台频道", "video_types": ["SpecialStage"]},
            {"name": "直拍频道", "video_types": ["Fancam"]},
        ],
    )
    ai = _normalize({"video_type": "Other", "video_types": ["Other"], "name": "260101 A - B [MV]"})
    check("suggest_ingest 返回形态是 dict（回归护栏）", isinstance(ai, dict), str(type(ai)))
    out = _apply_uploader_rule(db, ai, {"original_uploader": "官方舞台频道"}, {})
    check("命中官方舞台博主 → 覆盖为 PerformanceVideo", out["video_types"] == ["PerformanceVideo"] and out["video_type"] == "PerformanceVideo")
    out2 = _apply_uploader_rule(db, _normalize({"video_types": ["Other"]}), {"original_uploader": "特别舞台频道"}, {})
    check("命中特别舞台博主 → SpecialStage", out2["video_types"] == ["SpecialStage"])
    out3 = _apply_uploader_rule(db, _normalize({"video_types": ["Other"]}), {}, {"uploader": "直拍频道"})
    check("context.uploader 也能命中", out3["video_types"] == ["Fancam"])
    out4 = _apply_uploader_rule(db, _normalize({"video_type": "Teaser", "video_types": ["Teaser"]}), {"original_uploader": "陌生频道"}, {})
    check("未命中博主不改动 AI 结果", out4["video_types"] == ["Teaser"])
    out5 = _apply_uploader_rule(db, _normalize({"video_types": ["Other"]}), {}, {})
    check("无上传人时不报错", out5["video_types"] == ["Other"])
    out6 = _apply_uploader_rule(db, {}, {}, {"uploader": "直拍频道"})
    check("空 dict 也能被规则填上", out6["video_types"] == ["Fancam"], str(out6))

    # ---- HTTP：规则读写 + 管理列表 ----
    db.add(
        MusicVideo(
            name="260101 A - B [MV]",
            source_platform="youtube",
            original_uploader="官方舞台频道",
            video_type="PerformanceVideo",
            video_types=["PerformanceVideo"],
        )
    )
    db.add(
        MusicVideo(
            name="260102 A - B [Stage]",
            source_platform="youtube",
            original_uploader="路人频道",
            video_type="Other",
            video_types=["Other"],
        )
    )
    db.commit()

    r = client.get("/api/uploaders/rules")
    check("GET /uploaders/rules 200", r.status_code == 200, str(r.status_code))
    check("GET 返回已配置规则", len(r.json()["rules"]) == 3, str(r.json()))

    r = client.put(
        "/api/uploaders/rules",
        json={"rules": [{"name": "路人频道", "video_types": ["Fancam"]}, {"name": "空", "video_types": []}]},
    )
    check("PUT /uploaders/rules 200", r.status_code == 200, r.text[:200])
    check("PUT 归一化掉空类型行", r.json()["rules"] == [{"name": "路人频道", "video_types": ["Fancam"]}], str(r.json()))

    r = client.get("/api/uploaders/manage")
    check("GET /uploaders/manage 200", r.status_code == 200, str(r.status_code))
    data = r.json()
    by_name = {i["name"]: i for i in data["items"]}
    check("管理列表含库内博主", "官方舞台频道" in by_name and "路人频道" in by_name, str(list(by_name)))
    check("已固定博主带出 video_types", by_name["路人频道"]["video_types"] == ["Fancam"])
    check("未固定博主为空数组", by_name["官方舞台频道"]["video_types"] == [])
    check("rule_count 正确", data["rule_count"] == 1, str(data["rule_count"]))
    check("已固定的排在前面", data["items"][0]["name"] == "路人频道", str(data["items"][0]))
    r = client.get("/api/uploaders/manage", params={"q": "路人"})
    check("管理列表支持搜索", [i["name"] for i in r.json()["items"]] == ["路人频道"], str(r.json()))

    # 库内没有作品的博主也要出现（否则手工添加后看不到）
    client.put("/api/uploaders/rules", json={"rules": [{"name": "还没作品的频道", "video_types": ["CoverStage"]}]})
    data = client.get("/api/uploaders/manage").json()
    ghost = next((i for i in data["items"] if i["name"] == "还没作品的频道"), None)
    check("仅规则的博主出现在管理列表", ghost is not None and ghost["in_library"] is False and ghost["video_count"] == 0, str(ghost))

    # ---- 列表端点也带规则（供视频列表等复用） ----
    client.put("/api/uploaders/rules", json={"rules": [{"name": "路人频道", "video_types": ["Fancam"]}]})
    rows = client.get("/api/uploaders", params={"page_size": 50}).json()["items"]
    hit = next((i for i in rows if i["name"] == "路人频道"), None)
    check("GET /uploaders 附带 video_types", hit is not None and hit["video_types"] == ["Fancam"], str(hit))
    # ---- HTTP：/api/ai/suggest 全链路（命中规则的博主曾在此 500） ----
    import app.api.ai as ai_api
    from app.services import app_settings as app_settings_module

    client.put("/api/uploaders/rules", json={"rules": [{"name": "官方舞台频道", "video_types": ["PerformanceVideo", "SpecialStage"]}]})
    stub = _normalize({"name": "260101 A - B [MV]", "video_type": "Other", "video_types": ["Other"]})
    payload = {
        "provider": "openai-compatible",
        "base_url": "http://127.0.0.1:9/v1",
        "api_key": "test-key",
        "model": "test-model",
    }
    real_suggest = ai_api.suggest_ingest
    real_find = app_settings_module.find_uploader_types
    ai_api.suggest_ingest = lambda *a, **k: dict(stub)
    try:
        r = client.post("/api/ai/suggest", json={**payload, "context": {"uploader": "官方舞台频道"}, "current": {}})
        check("命中博主规则 → HTTP 200（回归：曾 500）", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        body = r.json() if r.status_code == 200 else {}
        check(
            "命中博主规则 → 类型被规则覆盖（多类型按顺序）",
            body.get("video_types") == ["PerformanceVideo", "SpecialStage"] and body.get("video_type") == "PerformanceVideo",
            str(body)[:200],
        )
        check("命中博主规则 → AI 的其它字段保留", body.get("name") == "260101 A - B [MV]", str(body)[:200])

        r = client.post("/api/ai/suggest", json={**payload, "context": {"uploader": "没人配过的频道"}, "current": {}})
        check("未命中博主 → 保留 AI 判断", r.status_code == 200 and r.json().get("video_types") == ["Other"], f"{r.status_code} {r.text[:160]}")

        r = client.post("/api/ai/suggest", json={**payload, "context": {}, "current": {"original_uploader": "官方舞台频道"}})
        check(
            "current.original_uploader 也能触发规则覆盖",
            r.status_code == 200 and r.json().get("video_types") == ["PerformanceVideo", "SpecialStage"],
            f"{r.status_code} {r.text[:160]}",
        )

        # 规则查表炸了也只能降级，不能把 AI 填写打成 500
        def _boom(db_, uploader):
            raise RuntimeError("boom")

        app_settings_module.find_uploader_types = _boom
        r = client.post("/api/ai/suggest", json={**payload, "context": {"uploader": "官方舞台频道"}, "current": {}})
        check(
            "规则应用失败 → 降级返回 AI 结果而非 500",
            r.status_code == 200 and r.json().get("video_types") == ["Other"],
            f"{r.status_code} {r.text[:160]}",
        )
    finally:
        ai_api.suggest_ingest = real_suggest
        app_settings_module.find_uploader_types = real_find

finally:
    db.close()

print(f"\n===== 结果: PASS={PASS} FAIL={FAIL} =====")
sys.exit(0 if FAIL == 0 else 1)
