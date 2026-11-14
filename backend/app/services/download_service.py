"""下载队列：串行执行 yt-dlp，完成后把成品移入 incoming 并立即触发扫描。

## 关键口径（业主 2026-10-18）
1. 产物落 `settings.incoming_dir`；
2. 命名模板 `%(upload_date>%y-%m-%d)s %(title)s[%(uploader)s] (%(id)s)`；
3. 画质 `bestvideo+bestaudio`（+ 合并成 mkv）；
4. **下载完整才进待整理**：全程写在 `incoming/.downloading/<job_id>/`（隐藏目录，
   扫描器明确跳过），yt-dlp 退出码为 0 后才把文件整体移动进 incoming 根，
   随后立刻跑一次扫描落库，前端「待整理」马上能看到。

## 并发
单 worker 串行（`download_max_concurrent` 预留但默认 1）：NAS 磁盘 IO 与
yt-dlp 的多线程分片叠加会互相拖慢，串行反而更快且进度可读。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.download import ACTIVE_STATUS, DownloadJob
from app.services import ytdlp_service
from app.services.ytdlp_service import YtdlpError

logger = logging.getLogger(__name__)

# 库内既有命名口径（与 O:\音乐视频 存量一致）
NAME_TEMPLATE = "%(upload_date>%y-%m-%d)s %(title)s[%(uploader)s] (%(id)s)"

# 进度落库节流（秒）：yt-dlp 每秒能刷十几行，全写库会把 SQLite 打满
_PROGRESS_FLUSH_SECONDS = 1.5
# 失败时保留 stderr 末尾多少行
_ERR_TAIL_LINES = 12


def incoming_root() -> Path:
    return settings.incoming_dir


def temp_root() -> Path:
    """半成品目录：incoming 下的隐藏目录，与目标同一文件系统（rename 原子）。"""
    p = incoming_root() / ".downloading"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _collect_archive_ids(moved_paths: list[Path]) -> list[str]:
    """从产出侧车 .info.json 里收集视频 id（单条 / 播放列表都适用）。

    yt-dlp 每个成品都会写一份 info.json，里面 `id` 就是平台视频 id；
    播放列表任务一次产出多条，逐个收集才能把整批都记进下载存档。
    """
    import json

    ids: list[str] = []
    for p in moved_paths:
        if not p.name.endswith(".info.json"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        vid = str(data.get("id") or "").strip()
        if vid and vid not in ids:
            ids.append(vid)
    return ids


SUB_LANGS = "zh-Hans,en"


def fetch_subtitles(job_id: int) -> dict:
    """给已完成的任务补下字幕（只下字幕，不动视频）。

    - 用 yt-dlp `--skip-download`，输出到视频所在目录、与视频同名
    - 语言：中文优先（简/繁/自动）+ 英文兜底
    - 结果写回 job.outputs，前端刷新即可看到
    """
    import json as _json

    db = SessionLocal()
    try:
        job = db.get(DownloadJob, job_id)
        if job is None:
            raise ValueError("任务不存在")
        if job.status != "done":
            raise ValueError("只有已完成的下载可以补字幕")
        names = [n for n in (job.output_path or "").split(";") if n]
        video = next(
            (n for n in names if Path(n).suffix.lower() in {".mkv", ".mp4", ".webm", ".mov"}),
            None,
        )
        if not video:
            raise ValueError("找不到成品视频（output_path 为空）")

        root = incoming_root()
        stem = Path(video).stem
        out_tpl = str(root / f"{stem}.%(ext)s")
        args = [
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            SUB_LANGS,
            "--convert-subs",
            "srt",
            "--no-playlist",
            "-o",
            out_tpl,
            job.url,
        ]
        argv, env = ytdlp_service.command()
        cmd = argv + ytdlp_service.common_args() + args
        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=900
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-400:]
            raise YtdlpError(f"yt-dlp 退出码 {proc.returncode}：{tail}")

        files = sorted(root.glob(f"{stem}.*.srt"))
        rows = []
        try:
            rows = _json.loads(job.outputs or "[]")
            if not isinstance(rows, list):
                rows = []
        except _json.JSONDecodeError:
            rows = []
        have = {r.get("name") for r in rows if isinstance(r, dict)}
        for f in files:
            if f.name in have:
                continue
            rows.append(
                {
                    "kind": "subtitle",
                    "name": f.name,
                    "size": f.stat().st_size,
                    "width": None,
                    "height": None,
                }
            )
        job.outputs = _json.dumps(rows, ensure_ascii=False)
        db.commit()
        return {"ok": True, "files": [f.name for f in files], "count": len(files)}
    finally:
        db.close()


def _job_tmp(job_id: int) -> Path:
    p = temp_root() / str(job_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


class _Worker:
    """单线程下载执行器。"""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._cancel: set[int] = set()
        self._lock = threading.Lock()
        self._current_job_id: Optional[int] = None

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="download-worker", daemon=True)
        self._thread.start()
        logger.info("YouTube 下载队列已启动")

    def stop(self) -> None:
        self._stop.set()

    # ---------- 对外操作 ----------
    def request_cancel(self, job_id: int) -> None:
        with self._lock:
            self._cancel.add(job_id)

    def is_canceling(self, job_id: int) -> bool:
        with self._lock:
            return job_id in self._cancel

    def _clear_cancel(self, job_id: int) -> None:
        with self._lock:
            self._cancel.discard(job_id)

    # ---------- 主循环 ----------
    def _loop(self) -> None:
        while not self._stop.is_set():
            job_id = self._claim_next()
            if job_id is None:
                self._stop.wait(2.0)
                continue
            try:
                self._run(job_id)
            except Exception as e:  # noqa: BLE001
                logger.exception("下载任务 %s 异常", job_id)
                self._finish(job_id, "failed", error=f"内部错误：{e}")
            finally:
                self._clear_cancel(job_id)
                self._current_job_id = None

    def _claim_next(self) -> Optional[int]:
        db = SessionLocal()
        try:
            job = db.scalars(
                select(DownloadJob)
                .where(DownloadJob.status == "queued")
                .order_by(DownloadJob.id.asc())
                .limit(1)
            ).first()
            if not job:
                return None
            job.status = "downloading"
            job.started_at = datetime.utcnow()
            job.progress = 0.0
            db.commit()
            self._current_job_id = job.id
            return job.id
        finally:
            db.close()

    # ---------- 单个任务 ----------
    def _run(self, job_id: int) -> None:
        db = SessionLocal()
        try:
            job = db.get(DownloadJob, job_id)
            if not job:
                return
            url = job.url
            mode = job.mode or "video"
        finally:
            db.close()

        tmp = _job_tmp(job_id)
        # 清掉上一轮残留（重试场景）
        for f in tmp.iterdir():
            try:
                f.unlink()
            except OSError:
                pass

        download_subtitles = bool(getattr(job, "download_subtitles", False))

        out_tpl = str(tmp / f"{NAME_TEMPLATE}.%(ext)s")
        args = [
            "--newline",
            "--no-part",
            "-f",
            settings.ytdlp_format,
            "--merge-output-format",
            settings.ytdlp_merge_format,
            # 侧车元数据：json + 最清晰封面，**basename 与视频完全一致**
            # （模板不含扩展名，yt-dlp 会分别产出 <stem>.mkv / <stem>.info.json / <stem>.jpg）
            "--write-info-json",
            "--write-thumbnail",
            # 封面统一转成 jpg：YouTube 常给 webp，转一下兼容性最好；
            # 没有配置 ffmpeg 时跳过转换（保留原始格式，同样同名）
            *(
                ["--convert-thumbnails", "jpg"]
                if (settings.ffmpeg_path or "").strip()
                else []
            ),
            "--retries",
            "5",
            "--fragment-retries",
            "5",
            "--concurrent-fragments",
            "4",
            "-o",
            out_tpl,
        ]
        if download_subtitles:
            args += [
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs",
                SUB_LANGS,
                "--convert-subs",
                "srt",
            ]
        if mode == "playlist":
            args += ["--yes-playlist", "--ignore-errors", "--no-overwrites"]
        else:
            args += ["--no-playlist", "--no-overwrites", "--ignore-errors"]
        args.append(url)

        try:
            argv, env = ytdlp_service.command()
            cmd = argv + ytdlp_service.common_args() + args
        except YtdlpError as e:
            self._finish(job_id, "failed", error=str(e))
            return

        logger.info("开始下载 #%s（%s）：%s", job_id, mode, url)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
        except OSError as e:
            self._finish(job_id, "failed", error=f"无法启动 yt-dlp：{e}")
            return

        tail: list[str] = []
        last_flush = 0.0
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                tail.append(line)
                if len(tail) > _ERR_TAIL_LINES:
                    tail.pop(0)

                if self.is_canceling(job_id):
                    proc.terminate()
                    break

                info = ytdlp_service.parse_progress(line)
                if info:
                    now = time.time()
                    if now - last_flush >= _PROGRESS_FLUSH_SECONDS:
                        last_flush = now
                        self._update_progress(
                            job_id,
                            percent=info["percent"],
                            total=info["total_bytes"],
                            speed=ytdlp_service.human_speed(info["speed_bytes"]),
                            eta=info["eta"],
                        )
                elif line.startswith("[Merger]") or line.startswith("[ExtractAudio]"):
                    self._update_status(job_id, "processing")
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

        if self.is_canceling(job_id):
            self._cleanup_tmp(tmp)
            self._finish(job_id, "canceled", error="已取消")
            return

        if proc.returncode != 0:
            self._cleanup_tmp(tmp)
            err = "\n".join(tail[-_ERR_TAIL_LINES:]) or f"yt-dlp 退出码 {proc.returncode}"
            self._finish(job_id, "failed", error=err)
            return

        # ---- 成功：把成品移入 incoming 根 ----
        moved_paths = self._move_into_incoming(tmp)
        if not moved_paths:
            self._finish(job_id, "failed", error="yt-dlp 正常退出但没有产出文件（可能被 --no-overwrites 跳过）")
            return

        moved = [p.name for p in moved_paths]
        self._fill_meta_from_info(job_id, moved)
        self._finish(
            job_id, "done", output=";".join(moved), outputs=self._collect_outputs(moved_paths)
        )
        # 本地下载存档（incoming/download-archive.txt）：记录视频 id，此后即使文件被移走
        # 或尚未入库，也不会重复下载同一支。与 Stacher 的存档各写各的，需要时手动合并。
        try:
            from app.services import archive_service

            ids = _collect_archive_ids(moved_paths)
            job_vid = getattr(job, "video_id", None)
            if job_vid and str(job_vid) not in ids:
                ids.insert(0, str(job_vid))
            for vid in ids:
                archive_service.append(vid)
        except Exception as e:  # noqa: BLE001 — 存档失败绝不能影响下载结果
            logger.warning("写入下载存档失败（不影响任务）：%s", e)
        self._cleanup_tmp(tmp)
        # 立刻刷新「待整理」：用 force_paths 明确告诉扫描器「这些文件已写完」，
        # 跳过 30s 静置窗口，下载完成当场就能在待整理里看到
        try:
            from app.tasks.scan_task import run_scan_background

            run_scan_background(force_paths={str(p) for p in moved_paths})
        except Exception as e:  # noqa: BLE001
            logger.warning("下载完成后触发扫描失败（下一轮定时扫描会补上）：%s", e)

    # ---------- 辅助 ----------
    def _move_into_incoming(self, tmp: Path) -> list[Path]:
        """把临时目录里的成品移入 incoming 根，返回最终绝对路径列表。"""
        root = incoming_root()
        dest_paths: list[Path] = []
        for f in sorted(tmp.iterdir()):
            if not f.is_file():
                continue
            target = root / f.name
            # 极端撞名（同 id 重复下载）时追加序号
            n = 1
            while target.exists():
                n += 1
                target = root / f"{f.stem} ({n}){f.suffix}"
            shutil.move(str(f), str(target))
            dest_paths.append(target)
        return dest_paths

    def _cleanup_tmp(self, tmp: Path) -> None:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except OSError:
            pass

    def _fill_meta_from_info(self, job_id: int, moved: list[str]) -> None:
        """用 .info.json 回填标题 / 时长 / 视频 ID（失败不影响任务成功）。"""
        import json

        info_name = next((n for n in moved if n.endswith(".info.json")), None)
        if not info_name:
            return
        try:
            data = json.loads((incoming_root() / info_name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        db = SessionLocal()
        try:
            job = db.get(DownloadJob, job_id)
            if not job:
                return
            job.title = (data.get("title") or job.title or "")[:300] or None
            job.uploader = (data.get("uploader") or job.uploader or "")[:200] or None
            dur = data.get("duration")
            if isinstance(dur, (int, float)):
                job.duration = int(dur)
            if data.get("id"):
                job.video_id = str(data["id"])[:64]
            db.commit()
        finally:
            db.close()

    def _update_progress(
        self, job_id: int, *, percent: float, total: Optional[int], speed: str, eta: str
    ) -> None:
        db = SessionLocal()
        try:
            job = db.get(DownloadJob, job_id)
            if not job or job.status not in ACTIVE_STATUS:
                return
            job.progress = max(0.0, min(100.0, percent))
            if total:
                job.total_bytes = total
                job.downloaded_bytes = int(total * percent / 100)
            job.speed = speed or None
            job.eta = eta or None
            db.commit()
        finally:
            db.close()

    def _update_status(self, job_id: int, status: str) -> None:
        db = SessionLocal()
        try:
            job = db.get(DownloadJob, job_id)
            if job and job.status in ACTIVE_STATUS:
                job.status = status
                db.commit()
        finally:
            db.close()

    def _collect_outputs(self, paths: list[Path]) -> str:
        """统计落盘文件：视频/封面/元数据/字幕各自的体积与分辨率。"""
        import json as _json

        rows: list[dict] = []
        for p in paths:
            try:
                size = p.stat().st_size
            except OSError:
                continue
            ext = p.suffix.lower()
            name = p.name
            if ext in {".mkv", ".mp4", ".webm", ".mov"}:
                kind = "video"
            elif ext in {".json"}:
                kind = "json"
            elif ext in {".jpg", ".jpeg", ".png", ".webp"}:
                kind = "cover"
            elif ext in {".srt", ".ass", ".vtt"}:
                kind = "subtitle"
            else:
                continue
            width = height = None
            try:
                if kind == "video":
                    import subprocess

                    from app.config import settings as _s

                    ff = (_s.ffprobe_path or "ffprobe").strip()
                    out = subprocess.run(
                        [
                            ff, "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height",
                            "-of", "csv=p=0:s=x", str(p),
                        ],
                        capture_output=True, text=True, timeout=20,
                    ).stdout.strip()
                    if "x" in out:
                        w, h = out.split("x")[:2]
                        width, height = int(w), int(h)
                elif kind == "cover":
                    from PIL import Image

                    with Image.open(p) as im:
                        width, height = im.size
            except Exception:  # noqa: BLE001
                pass
            rows.append(
                {
                    "kind": kind,
                    "name": name,
                    "size": size,
                    "width": width,
                    "height": height,
                }
            )
        order = {"video": 0, "cover": 1, "json": 2, "subtitle": 3}
        rows.sort(key=lambda r: (order.get(r["kind"], 9), r["name"]))
        return _json.dumps(rows, ensure_ascii=False)

    def _finish(
        self,
        job_id: int,
        status: str,
        *,
        error: Optional[str] = None,
        output: Optional[str] = None,
        outputs: Optional[str] = None,
    ) -> None:
        db = SessionLocal()
        try:
            job = db.get(DownloadJob, job_id)
            if not job:
                return
            job.status = status
            job.finished_at = datetime.utcnow()
            if error is not None:
                job.error = error[:4000]
            if output is not None:
                job.output_path = output[:2000]
                job.progress = 100.0
            if outputs is not None:
                job.outputs = outputs
            if status == "failed":
                job.speed = None
                job.eta = None
            db.commit()
            logger.info("下载任务 #%s → %s", job_id, status)
        finally:
            db.close()


worker = _Worker()
