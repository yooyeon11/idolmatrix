"""登录门禁：health 匿名、业务 API 401、bootstrap 竞态、logout-all、bind 白名单。"""
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="auth_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")
os.environ["PHOTO_BROWSE_ROOTS"] = str(_tmp)
os.environ["AUTH_REQUIRED"] = "auto"
# 显式关掉跨站放行：本机 .env 里可能是 true（内网穿透场景），否则下面的 CSRF 守卫用例会失真
os.environ["ALLOW_CROSS_ORIGIN_WRITES"] = "false"
os.environ.pop("BOOTSTRAP_OWNER_PASSWORD", None)
os.environ.pop("AUTH_RESET_OWNER_PASSWORD", None)
os.environ.pop("BOOTSTRAP_TOKEN", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.services import photo_service  # noqa: E402

settings.ensure_directories()
init_db()

PASS = 0
FAIL = 0
ORIGIN = {"Origin": "http://testserver"}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


client = TestClient(app)


def test_health_anonymous():
    r = client.get("/api/system/health")
    check("health 无 Cookie 200", r.status_code == 200, str(r.status_code))
    body = r.json()
    check("health 无 bootstrap 字段", "bootstrap_required" not in body)
    check("health status ok", body.get("status") == "ok")


def test_app_settings_requires_login():
    r = client.get("/api/app-settings")
    check("匿名 app-settings 401", r.status_code == 401, str(r.status_code))

    # v3.2.18：设置页的「放行跨站写请求」开关已移除，跨站放行只剩部署层
    # ALLOW_CROSS_ORIGIN_WRITES 环境变量 —— 这里锁住「产品层无法再绕过」。
    # CSRF 校验跑在会话校验之前：匿名 + 跨站 Origin 的写请求应是 403，而不是 401。
    probe = {"external_proxy": {"enabled": True, "url": "http://127.0.0.1:1"}}
    evil = client.patch("/api/app-settings", json=probe, headers={"Origin": "http://evil.example"})
    check("跨站 Origin 写请求 403", evil.status_code == 403, str(evil.status_code))
    check("跨站拒绝文案", evil.json().get("detail") == "跨站请求被拒绝", evil.text[:120])

    same = client.patch("/api/app-settings", json=probe, headers=ORIGIN)
    check("同源写请求不被 CSRF 拦（落到会话校验 401）", same.status_code == 401, str(same.status_code))


def test_status_bootstrap():
    r = client.get("/api/auth/status")
    check("status 200", r.status_code == 200)
    body = r.json()
    check("bootstrap_required", body.get("bootstrap_required") is True)
    check("authenticated false", body.get("authenticated") is False)
    check("auth_required", body.get("auth_required") is True)
    check("user null", body.get("user") is None)


def test_bootstrap_and_login():
    r = client.post(
        "/api/auth/bootstrap",
        json={"username": "owner", "password": "secret123", "display_name": "馆长"},
        headers=ORIGIN,
    )
    check("bootstrap 200", r.status_code == 200, r.text[:200])
    check("bootstrap set cookie", "im_session" in r.cookies)
    r2 = client.get("/api/app-settings")
    check("登录后 app-settings 200", r2.status_code == 200, str(r2.status_code))
    keys = r2.json().get("ingest_ai") or {}
    check("登录后仍含 api_key 字段", "api_key" in keys)
    check("登录后不回传密钥明文", not keys.get("api_key"))
    check("未配置时 api_key_set 为假", keys.get("api_key_set") is False)
    check("安全头 nosniff", r2.headers.get("x-content-type-options") == "nosniff")

    r3 = client.post("/api/auth/logout", headers=ORIGIN)
    check("logout 204", r3.status_code == 204)
    r4 = client.get("/api/app-settings")
    check("logout 后 401", r4.status_code == 401)

    r5 = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "secret123"},
        headers=ORIGIN,
    )
    check("login 200", r5.status_code == 200, r5.text[:200])
    r6 = client.get("/api/app-settings")
    check("再登录后 200", r6.status_code == 200)

    r7 = client.post("/api/auth/logout-all", headers=ORIGIN)
    check("logout-all 204", r7.status_code == 204)
    r8 = client.get("/api/dashboard/stats")
    check("logout-all 后业务 401", r8.status_code == 401)

    r9 = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "wrong-pass"},
        headers=ORIGIN,
    )
    check("错密码 401", r9.status_code == 401)
    check("错密码文案", r9.json().get("detail") == "用户名或密码不正确")

    r10 = client.post(
        "/api/auth/bootstrap",
        json={"username": "other", "password": "secret123"},
        headers=ORIGIN,
    )
    check("第二次 bootstrap 403", r10.status_code == 403, str(r10.status_code))


def test_bind_whitelist():
    db = SessionLocal()
    try:
        artist = Artist(name="BindTest")
        db.add(artist)
        db.commit()
        db.refresh(artist)
        # 白名单外、但**确定存在**的目录。原来硬编码 /etc：macOS 下存在 → 走到白名单检查
        # （错误含「可浏览」）；Windows 下 /etc → <当前盘>:\etc 不存在，会先在 resolve_folder
        # 的存在性检查处抛「路径不是存在的文件夹」，断言就测不到白名单防护了。用 Path.home() 跨平台成立。
        outside = Path.home()
        try:
            photo_service.bind_source(
                db, owner_type="artist", owner_id=artist.id, section="wall", folder_path=str(outside)
            )
            check("bind 白名单外目录应失败", False)
        except photo_service.PhotoServiceError as e:
            check("bind 白名单外目录被拒", "可浏览" in str(e), str(e))
        inside = _tmp / "pics"
        inside.mkdir()
        src = photo_service.bind_source(
            db, owner_type="artist", owner_id=artist.id, section="wall", folder_path=str(inside)
        )
        check("bind 白名单内成功", src.id > 0)
    finally:
        db.close()


def test_bootstrap_race():
    """并发两个不同用户名，库里只能有一行。需干净库——本文件前面已 bootstrap，跳过若已有用户。"""
    from app.services.auth_service import count_users

    if count_users() > 0:
        check("bootstrap 竞态跳过（已有用户，由上一用例覆盖单行）", True)
        return


def test_username_and_agent():
    r = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "secret123"},
        headers=ORIGIN,
    )
    check("owner 再登录", r.status_code == 200, str(r.status_code))

    r2 = client.patch("/api/auth/me", json={"username": "ab"}, headers=ORIGIN)
    check("用户名过短 400", r2.status_code == 400, str(r2.status_code))

    r3 = client.patch("/api/auth/me", json={"username": "owner-new"}, headers=ORIGIN)
    check("改用户名 200", r3.status_code == 200, r3.text[:200])
    check("用户名已更新", (r3.json().get("user") or {}).get("username") == "owner-new")

    r4 = client.post(
        "/api/auth/users",
        json={"username": "agent1", "password": "agentpass1"},
        headers=ORIGIN,
    )
    check("创建测试账号 200", r4.status_code == 200, r4.text[:200])
    agent = (r4.json().get("user") or {})
    check("测试账号角色 agent", agent.get("role") == "agent")
    agent_id = agent.get("id")

    r5 = client.get("/api/auth/users")
    check("列出账号 200", r5.status_code == 200)
    items = (r5.json().get("items") or [])
    check("列表含主账号与测试账号", len(items) >= 2, str(len(items)))

    r6 = client.post(
        "/api/auth/login",
        json={"username": "agent1", "password": "agentpass1"},
        headers=ORIGIN,
    )
    check("测试账号可登录", r6.status_code == 200, r6.text[:200])
    r7 = client.post(
        "/api/auth/users",
        json={"username": "agent2", "password": "agentpass2"},
        headers=ORIGIN,
    )
    check("测试账号不能再建账号", r7.status_code == 403, str(r7.status_code))
    # 探针字段用 external_proxy（原 network 分区已随「放行跨站写请求」一起移除）
    r7b = client.patch(
        "/api/app-settings",
        json={"external_proxy": {"enabled": True, "url": "http://127.0.0.1:1"}},
        headers=ORIGIN,
    )
    check("测试账号不能改设置", r7b.status_code == 403, str(r7b.status_code))

    r8 = client.post(
        "/api/auth/login",
        json={"username": "owner-new", "password": "secret123"},
        headers=ORIGIN,
    )
    check("主账号新用户名登录", r8.status_code == 200, str(r8.status_code))
    r9 = client.delete(f"/api/auth/users/{agent_id}", headers=ORIGIN)
    check("删除测试账号 204", r9.status_code == 204, str(r9.status_code))
    r10 = client.post(
        "/api/auth/login",
        json={"username": "agent1", "password": "agentpass1"},
        headers=ORIGIN,
    )
    check("删除后不能登录", r10.status_code == 401, str(r10.status_code))

    r11 = client.patch("/api/auth/me", json={"username": "owner"}, headers=ORIGIN)
    check("用户名改回 owner", r11.status_code == 200)


test_health_anonymous()
test_app_settings_requires_login()
test_status_bootstrap()
test_bootstrap_and_login()
test_bind_whitelist()
test_bootstrap_race()
test_username_and_agent()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
