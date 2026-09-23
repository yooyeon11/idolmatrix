"""pytest 全局隔离网：任何测试都不得碰用户的真实库。

为什么需要（2026-09-19 事故，务必先读）
--------------------------------------
`backend/` 下的验证脚本是「独立运行」风格：在 import 任何 app 模块**之前**把自己的
`DATABASE_URL` 指向临时目录。注释里也写了「settings 单例一旦实例化即固定」。

独立运行（`python test_xxx.py`）没问题；**但被 pytest 收集时就不成立了**：
pytest 会先 import 排在前面的测试模块，那一步已经把 `app.core.config.settings`
实例化成真实库（`backend/data/kpop_media.db`）并建好 `engine`。此后本模块再改
`os.environ["DATABASE_URL"]` 已经**无效**，于是 `Base.metadata.create_all()` 与
`SessionLocal()` 全部落到真实库上 —— 实测 `pytest test_hw_decode.py
test_resolution_filter.py` 让真实库从 78 行涨到 91 行（静默写入 13 条夹具）。

对策
----
1) 在 collection 之前（conftest 早于所有 test 模块被 import）就把进程级
   `DATABASE_URL` / `STORAGE_*` 指到临时目录，作为兜底安全网；
2) 首个测试前断言 `settings.database_url` 不是真实库 —— 一旦有人绕过安全网，
   直接**报错**而不是默默写脏；
3) 会话结束时对比真实库 `music_videos` 行数，变了就大声报警。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_REAL_DB = (_ROOT / "data" / "kpop_media.db").resolve()
_TMP = Path(tempfile.mkdtemp(prefix="im_pytest_"))


def _sqlite_file(url: str) -> Path | None:
    """把 sqlite:///… 的连接串还原成本地文件路径；非 sqlite / 空值返回 None。"""
    if not url or not url.startswith("sqlite"):
        return None
    tail = url.split("///", 1)[-1].split("?", 1)[0]
    try:
        return Path(tail).resolve()
    except (OSError, ValueError):
        return None


# ---- ① 安全网：collection 之前生效 ----
if _sqlite_file(os.environ.get("DATABASE_URL", "")) in (None, _REAL_DB):
    os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
for _key, _sub in (
    ("STORAGE_DERIVED_DIR", "derived"),
    ("STORAGE_LIBRARY_DIR", "library"),
    ("STORAGE_INCOMING_DIR", "incoming"),
):
    os.environ.setdefault(_key, str(_TMP / _sub))
os.environ.setdefault("AUTH_REQUIRED", "false")


def _real_db_rows() -> int | None:
    """真实库 music_videos 行数；库不存在时返回 None。"""
    if not _REAL_DB.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{_REAL_DB}?mode=ro", uri=True)
        try:
            return con.execute("SELECT COUNT(*) FROM music_videos").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return None


_ROWS_AT_START = _real_db_rows()
_GUARD_DONE = False


def pytest_configure(config) -> None:  # noqa: ARG001
    """首个测试前拦一道：settings 指向真实库 = 隔离失效，立即报错。"""
    global _GUARD_DONE
    if _GUARD_DONE:
        return
    _GUARD_DONE = True
    from app.core.config import settings  # 延迟导入：必须晚于上面的 env 写入

    bound = _sqlite_file(settings.database_url)
    if bound == _REAL_DB:
        raise RuntimeError(
            "测试隔离失效：settings.database_url 指向了真实库 "
            f"({_REAL_DB})。这多半是因为某个测试模块在 app 模块已被导入之后才改 "
            "os.environ['DATABASE_URL']（settings 是单例，改了也不生效）。\n"
            "修法：把 env 写入放在该文件 import app.* 之前，或改走 conftest 的临时库。"
        )


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    """收尾对比：真实库行数被改动 → 大声报警并让 pytest 以失败退出。"""
    now = _real_db_rows()
    if _ROWS_AT_START is None or now is None or now == _ROWS_AT_START:
        return
    print(
        "\n" + "!" * 72 + "\n"
        f"!! 真实库被测试写入：music_videos {_ROWS_AT_START} → {now} 行\n"
        f"!! 路径：{_REAL_DB}\n"
        "!! 说明某个测试没有走临时库，请检查其 DATABASE_URL 是否设在了 import app 之前。\n"
        + "!" * 72
    )
    session.exitstatus = 1
