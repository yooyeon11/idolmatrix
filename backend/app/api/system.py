"""系统路由：健康检查、FFmpeg 自检、配置概览、缓存管理。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import __version__ as APP_VERSION
from app.core.config import settings
from app.core.database import get_db
from app.core.ffmpeg import check_available, resolve_binaries
from app.media.hw_manager import hw_manager
from app.services.transcode_manager import transcode_manager

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name, "version": APP_VERSION}


@router.get("/hw-accel")
def hw_accel_status():
    """返回 QSV + VAAPI 硬件转码逐项诊断结果，说明当前是否调用 GPU 及原因。

    前端可据此提示用户：为什么没用 GPU、如何启用（挂载 /dev/dri、
    设置 TRANSCODE_HWACCEL 等）。QSV 不可用时 VAAPI 会作为第二候选。

    v3.4.2 起额外报告**硬解能力**：
      · decode.decoders —— 本机 GPU 实际能硬解的编码（来自 vainfo，Intel/AMD 通用）
      · decode_mode     —— auto / force / off（TRANSCODE_HW_DECODE）
      · decode.driver   —— 实际加载的驱动名与版本（判断是不是回退到了老驱动）
    """
    report = hw_manager.diagnose_report()
    qsv = report["qsv"]
    vaapi = report["vaapi"]
    decode = report.get("decode") or {}
    method = report["method"]
    available = report["available"]
    # 顶层 checks 供 UI 平铺展示：QSV / VAAPI 两条链路的检查项里有公共项
    # （device_node、ffmpeg_bin …），直接相加会出现重复行，这里按内容去重并保持顺序。
    merged = list(qsv.get("checks", [])) + list(vaapi.get("checks", []))
    seen: set = set()
    checks = []
    for c in merged:
        key = (c.get("name"), c.get("label"), c.get("detail"), c.get("passed"))
        if key in seen:
            continue
        seen.add(key)
        checks.append(c)
    if method == "qsv":
        message = qsv.get("message", "")
    elif method == "vaapi":
        message = vaapi.get("message", "")
    else:
        message = " | ".join(
            d.get("message", f"{label} 不可用")
            for label, d in (("QSV", qsv), ("VAAPI", vaapi))
        )
    decoders = decode.get("decoders") or []
    if available and decoders:
        message = f"{message} 硬解可用编码：{'、'.join(decoders)}。"
    elif available:
        message = f"{message} 未确认硬解能力（解码将走 CPU）。"
    return {
        "configured": (settings.transcode_hwaccel or "auto").lower(),
        "enabled": available,
        "method": method,
        "available": available,
        "checks": checks,
        "qsv": qsv,
        "vaapi": vaapi,
        "message": message,
        # ---- 硬解（解码侧）----
        "decode_mode": report.get("decode_mode", "auto"),
        "decode": decode,
        "decode_enabled": bool(available and decoders),
    }


@router.get("/ffmpeg")
def ffmpeg_status():
    ok, message = check_available()
    bins = resolve_binaries()
    return {
        "available": ok,
        "message": message,
        "ffmpeg_path": bins.ffmpeg or None,
        "ffprobe_path": bins.ffprobe or None,
        "ffmpeg_version": bins.ffmpeg_version,
        "ffprobe_version": bins.ffprobe_version,
    }


@router.get("/config")
def get_config():
    """返回前端需要的非敏感配置。"""
    return {
        "app_name": settings.app_name,
        "storage": {
            "incoming": str(settings.incoming_dir),
            "library": str(settings.library_dir),
            "derived": str(settings.derived_dir),
        },
        "transcode_defaults": {
            "video_codec": settings.transcode_video_codec,
            "audio_codec": settings.transcode_audio_codec,
            "scale": settings.transcode_scale,
        },
        "database_url": (
            "sqlite"
            if settings.database_url.startswith("sqlite")
            else settings.database_url.split("@")[-1]
        ),
    }


@router.get("/cache")
def get_cache_stats():
    """返回播放转码缓存统计（目录数、文件数、总大小）。"""
    return transcode_manager.get_cache_stats()


@router.post("/cache/clear")
def clear_cache(db: Session = Depends(get_db)):
    """一键清空全部播放转码缓存（保留当前活跃会话引用的目录）。"""
    result = transcode_manager.purge_all_cache(db)
    remaining = transcode_manager.get_cache_stats()
    return {**result, "remaining_bytes": remaining["total_bytes"]}


@router.post("/cleanup-transcodes")
def cleanup_transcodes(db: Session = Depends(get_db)):
    """手动触发一次转码缓存清理（引用归零延迟删除 + 24h 定期大扫除）。

    仅操作 derived/transcodes，绝不触碰 library/incoming。
    """
    cleaned = transcode_manager.cleanup_expired(db)
    swept = transcode_manager.sweep_max_age(db)
    remaining = transcode_manager.get_cache_stats()
    return {
        **cleaned,
        "swept": swept,
        "remaining_bytes": remaining["total_bytes"],
    }
