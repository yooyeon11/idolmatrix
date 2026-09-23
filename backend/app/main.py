"""FastAPI 应用入口。

启动时：
1. 创建必要目录
2. 初始化数据库表
3. 检测 FFmpeg 可用性（仅日志告警，不阻断启动）
4. 启动后台线程：转码缓存周期清理、incoming 扫描落库

若存在前端构建产物（frontend/dist），则由本服务直接伺服 SPA，
实现单容器（后端 + 前端静态文件）部署，便于 NAS/Docker 环境。
"""

from __future__ import annotations

import logging
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__ as APP_VERSION
from app.api.router import api_router
from app.core.auth_gate import AuthGateMiddleware
from app.core.config import PROJECT_ROOT, settings
from app.core.http_limits import BodyLimitMiddleware, SecurityHeadersMiddleware
from app.core.database import SessionLocal, init_db
from app.core.ffmpeg import check_available
from app.media.ffmpeg_engine import FfmpegEngineError
from app.services.data_health import invalidate_health_cache
from app.services.library_service import cleanup_missing_videos
from app.services.playback_manager import PlaybackError
from app.services.transcode_manager import transcode_manager
from app.tasks.scan_task import incoming_scan_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 前端构建产物目录：backend 目录的上层 /frontend/dist
FRONTEND_DIST = PROJECT_ROOT.parent / "frontend" / "dist"


def _setup_frontend(app: FastAPI) -> bool:
    """若存在 frontend/dist，挂载静态资源并接管 SPA 路由。

    返回是否启用；未启用时 / 仍返回 JSON 摘要，保持纯后端可用（开发模式）。
    """
    index_file = FRONTEND_DIST / "index.html"
    if not index_file.is_file():
        logger.info("未检测到前端构建产物（%s），不伺服 SPA", FRONTEND_DIST)
        return False

    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    logger.info("前端构建产物已挂载：%s", FRONTEND_DIST)

    @app.get("/", include_in_schema=False)
    def spa_index():
        return FileResponse(index_file, headers={"Cache-Control": "no-store"})

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        # /api 未匹配路由保持 404，不能回落 index.html
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        # 防路径穿越：full_path 是已解码的路径，%2e%2e 之类的编码点段
        # 解码后可拼出 dist 之外的文件（.env、数据库等），必须先规范化
        # 再校验仍位于 FRONTEND_DIST 内。
        try:
            target = (FRONTEND_DIST / full_path).resolve()
            target.relative_to(FRONTEND_DIST.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not Found") from None
        if target.is_file():
            return FileResponse(target)
        return FileResponse(index_file, headers={"Cache-Control": "no-store"})

    return True


def _cleanup_missing_library() -> None:
    """启动时只扫描疑似失效记录，不写库、不删文件。清理必须在设置里确认。"""
    try:
        db = SessionLocal()
        try:
            result = cleanup_missing_videos(db, dry_run=True)
            if result.get("skipped_unmounted"):
                logger.warning(
                    "启动扫描疑似失效视频已跳过（目录疑似未挂载）: %s",
                    result.get("unmounted_roots"),
                )
            elif result.get("missing"):
                logger.info(
                    "启动扫描到 %s 条疑似失效视频（未自动清理，请在设置中确认）",
                    result.get("missing"),
                )
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("启动扫描失效视频失败（不影响启动）：%s", e)


def _recover_stale_playback() -> None:
    """启动时恢复遗留播放/转码会话（Case 9）。

    服务器 / Docker 重启后：
    - 转码会话处于 running 但 PID 已不存在 → 标记 failed（僵尸任务清理）
    - 转码会话处于 running 且 PID 仍存活 → 终止，避免孤儿 FFmpeg
    - 过期闲置的会话与缓存目录 → 按策略清理
    """
    try:
        db = SessionLocal()
        try:
            recovered = transcode_manager.recover_stale(db)
            cleaned = transcode_manager.cleanup_expired(db)
            logger.info(
                "播放会话恢复完成：recover=%s cleanup=%s", recovered, cleaned
            )
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("启动时播放会话恢复失败（不影响启动）：%s", e)


def _periodic_cleanup_loop(interval: float, stop: "threading.Event") -> None:
    """后台周期任务：回收闲置转码缓存。失效视频不自动清理。"""
    logger.info("转码缓存定时清理任务已启动（间隔 %ss）", int(interval))
    while not stop.is_set():
        try:
            stop.wait(interval)
        except Exception:  # noqa: BLE001
            break
        if stop.is_set():
            break
        try:
            db = SessionLocal()
            try:
                cleaned = transcode_manager.cleanup_expired(db)
                swept = transcode_manager.sweep_max_age(db)
                from app.services.auth_service import cleanup_sessions

                sessions = cleanup_sessions(db)
                logger.info(
                    "周期清理完成：transcode_stopped=%s playback_closed=%s dirs_removed=%s swept=%s sessions=%s",
                    cleaned.get("transcode_stopped"),
                    cleaned.get("playback_closed"),
                    cleaned.get("dirs_removed"),
                    swept,
                    sessions,
                )
            finally:
                db.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("周期清理失败（不影响服务）：%s", e)
    logger.info("转码缓存定时清理任务已退出")


def create_app() -> FastAPI:
    settings.ensure_directories()
    init_db()
    # 跨站放行开关只读环境变量，不再查库（产品层已无该设置项）
    from app.services.app_settings import init_cross_origin_flag

    init_cross_origin_flag()
    from app.services.auth_service import apply_startup_auth

    apply_startup_auth()
    _recover_stale_playback()
    _cleanup_missing_library()

    ok, msg = check_available()
    if ok:
        logger.info("FFmpeg 自检通过: %s", msg)
    else:
        logger.warning("FFmpeg 自检失败: %s", msg)

    openapi_on = bool(settings.auth_openapi)
    app = FastAPI(
        title=settings.app_name,
        version=APP_VERSION,
        description="K-pop 音乐视频媒体库后端 API",
        docs_url="/docs" if openapi_on else None,
        redoc_url="/redoc" if openapi_on else None,
        openapi_url="/openapi.json" if openapi_on else None,
    )

    @app.on_event("startup")
    async def _start_periodic_cleanup():
        app.state._cleanup_stop = threading.Event()
        app.state._cleanup_thread = threading.Thread(
            target=_periodic_cleanup_loop,
            args=(settings.transcode_cleanup_interval_seconds, app.state._cleanup_stop),
            daemon=True,
        )
        app.state._cleanup_thread.start()

    @app.on_event("shutdown")
    async def _stop_periodic_cleanup():
        stop_event = getattr(app.state, "_cleanup_stop", None)
        if stop_event is not None:
            stop_event.set()

    @app.on_event("startup")
    async def _start_incoming_scan():
        app.state._scan_stop = threading.Event()
        app.state._scan_thread = threading.Thread(
            target=incoming_scan_loop,
            args=(settings.incoming_scan_interval_seconds, app.state._scan_stop),
            daemon=True,
            name="incoming-scan",
        )
        app.state._scan_thread.start()

    @app.on_event("shutdown")
    async def _stop_incoming_scan():
        stop_event = getattr(app.state, "_scan_stop", None)
        if stop_event is not None:
            stop_event.set()

    # 后加的中间件先处理请求：体积分 → CORS → 安全头 → 登录门禁
    app.add_middleware(AuthGateMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(BodyLimitMiddleware)

    @app.middleware("http")
    async def _invalidate_health_cache_on_write(request: Request, call_next):
        """任何成功的写请求（POST/PATCH/PUT/DELETE，/api 下）都使体检报告缓存失效。

        资料库列表端点（/api/db/*）内嵌的体检结果走 TTL 缓存（v3.4.7，NAS 上一次
        体检 ~3.3s；v3.5.6 修掉 path_mismatch 的 O(视频×艺人) 热点后降到 ~1/6）；
        数据被编辑后靠这里保证行内「问题数」下一次请求就更新，不用等 TTL 过期。
        失效操作本身极轻（清一个 dict），失败也不该影响响应。

        ⚠ 例外（v3.5.6）：`/api/entity-locks/*` 是**纯前端字段锁**的存取（锁只约束
        「AI 分析 / 站点获取」的写入），`data_health` 的 19 项检查一个都不读它 ——
        而工作台每翻一页、每锁一个字段都会 PUT 一次，跟着清缓存等于「用户每点一下
        锁，后面每次进资料库都要重算一遍全库体检」。故这里跳过。
        若将来有检查项要读 EntityFieldLock，必须把这条例外去掉。
        """
        response = await call_next(request)
        try:
            path = request.url.path
            if (
                request.method in {"POST", "PATCH", "PUT", "DELETE"}
                and path.startswith("/api")
                and not path.startswith("/api/entity-locks")
                and response.status_code < 400
            ):
                invalidate_health_cache()
        except Exception:  # noqa: BLE001 —— 缓存失效失败不影响业务响应
            pass
        return response

    @app.exception_handler(PlaybackError)
    async def playback_error_handler(request: Request, exc: PlaybackError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(FfmpegEngineError)
    async def ffmpeg_error_handler(request: Request, exc: FfmpegEngineError):
        code = exc.status_code or 500
        headers = {}
        if code in (429, 503):
            headers["Retry-After"] = "2"
        return JSONResponse(
            status_code=code, content={"detail": str(exc)}, headers=headers
        )

    app.include_router(api_router)

    if not _setup_frontend(app):

        @app.get("/", tags=["root"])
        def root():
            return {
                "app": settings.app_name,
                "version": APP_VERSION,
                "docs": "/docs",
                "ffmpeg_available": ok,
            }

    if (settings.auth_required or "auto").strip().lower() == "false":
        logger.warning("AUTH_REQUIRED=false：登录门禁已关闭，行为与 2.1.x 匿名相同")
    logger.info(
        "%s v%s 启动完成（SPA=%s，docs=%s）",
        settings.app_name,
        APP_VERSION,
        "已挂载" if (FRONTEND_DIST / "index.html").is_file() else "未挂载",
        "/docs" if settings.auth_openapi else "关闭",
    )
    logger.info(
        "SQLite 数据库: %s（WAL 已启用，请确保已挂卷并定期 sqlite3 .backup 备份）",
        settings.db_path,
    )
    return app


app = create_app()
