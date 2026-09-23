"""incoming 扫描后台任务。

后台线程周期把 incoming 目录的扫描结果落库（incoming_files 表），
后续列表/统计接口改读表，不再每次实时扫盘 + 全量 ffprobe。

线程由 main.py 启动事件拉起（与转码缓存清理线程同一模式），
每轮自建 Session，失败只记日志不影响服务。
"""

from __future__ import annotations

import logging
import threading

from app.core.database import SessionLocal
from app.services.incoming_scan_service import sync_incoming_files

logger = logging.getLogger(__name__)

_STATS_CHANGE_KEYS = ("added", "updated", "removed", "probe_failed")


def run_scan_background() -> dict:
    """同步执行一次扫描落库（手动触发 / 测试用）。"""
    db = SessionLocal()
    try:
        return sync_incoming_files(db)
    finally:
        db.close()


def incoming_scan_loop(interval: float, stop: threading.Event) -> None:
    """后台周期扫描落库：启动即扫一轮，之后每 interval 秒一轮。

    独立线程运行；stop 置位后退出。首轮立即执行，让表在启动后尽快可用。
    """
    logger.info("待整理扫描落库任务已启动（间隔 %ss）", int(interval))
    while not stop.is_set():
        try:
            db = SessionLocal()
            try:
                stats = sync_incoming_files(db)
            finally:
                db.close()
            if stats.get("skipped_unmounted"):
                logger.warning("incoming 扫描已跳过（目录疑似未挂载/为空）")
            elif stats.get("skipped_running"):
                logger.info("incoming 扫描已在进行中，本轮跳过")
            elif any(stats.get(k) for k in _STATS_CHANGE_KEYS):
                logger.info(
                    "incoming 扫描落库完成：scanned=%s added=%s updated=%s "
                    "removed=%s settling=%s probe_failed=%s",
                    stats.get("scanned"),
                    stats.get("added"),
                    stats.get("updated"),
                    stats.get("removed"),
                    stats.get("skipped_settling"),
                    stats.get("probe_failed"),
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("incoming 扫描失败（不影响服务）：%s", e)
        if stop.wait(interval):
            break
    logger.info("待整理扫描落库任务已退出")
