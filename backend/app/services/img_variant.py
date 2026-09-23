"""图片的缩放变体：按需生成并缓存到 derived/<cache_dirname>/。

首页/详情页在小尺寸展示位（头像胶囊、轮播横幅）原本直接出原图
（站点获取保存的 TheAudioDB 原始文件，无任何压缩）。外网/慢速链路下
一次并发拉取十几张原图很容易塞满隧道，部分请求失败后前端会显示占位图。
缩放变体把单图体积降到原图的约 1/10；cv2 缺失或解码失败时回退原图。

同一套机制也服务于视频封面（见 api/music_videos.get_thumbnail）：
视频抽帧默认按视频原始分辨率落盘，列表位只展示 180~320px，
带 `w` 请求时从这里出变体。实体图片与视频封面用不同的
cache_dirname 隔离，便于各自纳管清理。
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings

# 实体图片的默认缓存目录与质量（保持既有文件名与体积不变）
DEFAULT_CACHE_DIRNAME = "entity-img-cache"
# 视频封面变体的缓存目录（api/music_videos 与 library_service 的清理逻辑共用一个字面量）
VIDEO_THUMB_CACHE_DIRNAME = "video-thumb-cache"
DEFAULT_QUALITY = 92
_MIN_W = 64
_MAX_W = 1920
_MIN_Q = 40
_MAX_Q = 100
_CV2 = None
_CV2_CHECKED = False


def _load_cv2():
    global _CV2, _CV2_CHECKED
    if not _CV2_CHECKED:
        _CV2_CHECKED = True
        try:
            import cv2  # type: ignore

            _CV2 = cv2
        except Exception:
            _CV2 = None
    return _CV2


def resized_variant(
    src: Path,
    width: int,
    *,
    quality: int = DEFAULT_QUALITY,
    cache_dirname: str = DEFAULT_CACHE_DIRNAME,
) -> Path | None:
    """返回指定宽度的 JPEG 变体路径；无需缩放或失败时返回 None（调用方回退原图）。

    缓存文件名带源文件 mtime：图被替换后自然生成新变体，旧变体成为垃圾文件
    由库清理统一回收。质量参与命名（非默认质量才带 `-qNN`），
    这样升级默认质量不会让既有缓存失效、也不会和别处的同宽变体撞名。

    `cache_dirname` 用于隔离不同来源的变体（实体图片 / 视频封面）。
    """
    try:
        width = max(_MIN_W, min(_MAX_W, int(width)))
        quality = max(_MIN_Q, min(_MAX_Q, int(quality)))
    except (TypeError, ValueError):
        return None
    if src.suffix.lower() == ".gif":
        # 动图缩放会丢帧，直接回退原图
        return None
    try:
        st = src.stat()
    except OSError:
        return None
    cv2 = _load_cv2()
    if cv2 is None:
        return None
    cache = settings.derived_dir / cache_dirname
    q_suffix = "" if quality == DEFAULT_QUALITY else f"-q{quality}"
    dest = cache / f"{src.stem}-{st.st_mtime_ns:x}-w{width}{q_suffix}.jpg"
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    try:
        import numpy as np

        img = cv2.imdecode(np.fromfile(str(src), dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]
        if w <= width:
            return None
        nh = max(1, round(h * width / w))
        small = cv2.resize(img, (width, nh), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            return None
        cache.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.stem + ".part.jpg")
        part.write_bytes(buf.tobytes())
        part.replace(dest)
        return dest
    except Exception:
        # 半成品清理：任何一步失败都不能留下损坏的缓存文件
        try:
            dest.with_name(dest.stem + ".part.jpg").unlink(missing_ok=True)
        except OSError:
            pass
        return None
