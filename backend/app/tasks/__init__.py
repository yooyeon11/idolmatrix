"""后台任务：incoming 扫描落库。

扫描落库线程由 main.py 启动事件拉起（app.tasks.scan_task.incoming_scan_loop）；
run_scan_background 供手动触发 / 测试同步执行一轮。
后续可替换为 Celery / RQ / arq。任务函数不直接持有请求 Session，
而是自建 Session 以避免请求生命周期影响。

注：旧版「转码后台任务」已随 Phase 8 重构移除；实时播放转码改为
TranscodeManager 管理的常驻 FFmpeg 进程（TranscodeSession），不再需要后台任务队列。
"""

from app.tasks.scan_task import incoming_scan_loop, run_scan_background

__all__ = ["run_scan_background", "incoming_scan_loop"]
