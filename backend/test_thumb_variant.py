"""视频封面变体：`w` 参数按展示宽度出缩放图 + ETag 条件请求 + 源图更小不放大。

背景：封面落盘是视频原始分辨率（1080p/4K）或 sidecar 原图，
列表位只展示 180~320 CSS px。带 `w` 请求时服务端下采样一次并缓存到
derived/video-thumb-cache/；不传 `w` 时行为与改动前一致。

无 cv2 时（resized_variant 依赖 OpenCV）变体路径不可用，
此时必须优雅回退原图而不是报错 —— 本脚本会区分这两种情况。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_TMP = Path(tempfile.mkdtemp(prefix="thumb_variant_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_TMP / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_TMP / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_TMP / "incoming")
os.environ["AUTH_REQUIRED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.services.img_variant import (  # noqa: E402
    DEFAULT_QUALITY,
    VIDEO_THUMB_CACHE_DIRNAME,
    resized_variant,
)
from app.services.library_service import _purge_derived_for  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def skip(name, why):
    global SKIP
    SKIP += 1
    print(f"  SKIP  {name}  ({why})")


try:
    import cv2
    import numpy as np

    HAVE_CV2 = True
except Exception:  # noqa: BLE001
    HAVE_CV2 = False

settings.ensure_directories()
init_db()

# 源图：有 cv2 时造一张 1200×800 的真 JPEG（带噪声，压缩后体积可观，
# 这样「变体明显更小」才有意义）；没有 cv2 时用最小合法 JPEG 字节。
SRC_W, SRC_H = 1200, 800
if HAVE_CV2:
    rng = np.random.default_rng(7)
    ramp = np.linspace(0, 255, SRC_W, dtype=np.float32)[None, :, None]
    base = np.repeat(ramp, SRC_H, axis=0).repeat(3, axis=2)
    noise = rng.normal(0, 26, (SRC_H, SRC_W, 3)).astype(np.float32)
    img = np.clip(base + noise, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    assert ok
    COVER_BYTES = buf.tobytes()
else:
    COVER_BYTES = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
    )

lib = settings.library_dir
lib.mkdir(parents=True, exist_ok=True)
(lib / "clip.mp4").write_bytes(b"fake-mp4")
(lib / "clip.jpg").write_bytes(COVER_BYTES)

db = SessionLocal()
mv = MusicVideo(
    name="clip",
    ingestion_status="library",
    video_type="OfficialMV",
    file_path="clip.mp4",
    file_name="clip.mp4",
)
db.add(mv)
db.commit()
db.refresh(mv)
mid = mv.id
db.close()

print(f"源封面 {len(COVER_BYTES) / 1024:.1f} KB（{SRC_W}×{SRC_H}），cv2 可用：{HAVE_CV2}")

with TestClient(app) as client:
    print("== A. 不传 w：行为与改动前一致（原图）==")
    r = client.get(f"/api/music-videos/{mid}/thumbnail")
    check("200", r.status_code == 200, str(r.status_code))
    check("返回原图字节", r.content == COVER_BYTES, f"{len(r.content)}B")
    check("Content-Type", r.headers.get("content-type", "").startswith("image/jpeg"),
          r.headers.get("content-type"))
    etag = r.headers.get("etag")
    check("带 ETag", bool(etag), str(etag))
    check(
        "缓存语义仍为 private",
        "private" in r.headers.get("cache-control", ""),
        r.headers.get("cache-control"),
    )
    check("带 Last-Modified", bool(r.headers.get("last-modified")))

    print("== B. 条件请求：命中 ETag 返回 304 且不重传正文 ==")
    r304 = client.get(f"/api/music-videos/{mid}/thumbnail", headers={"If-None-Match": etag})
    check("304", r304.status_code == 304, str(r304.status_code))
    check("正文为空", r304.content == b"", f"{len(r304.content)}B")
    check("304 仍带 ETag", r304.headers.get("etag") == etag)

    print("== C. 带 w：出缩放变体 ==")
    r480 = client.get(f"/api/music-videos/{mid}/thumbnail", params={"w": 480})
    check("200", r480.status_code == 200, str(r480.status_code))
    if HAVE_CV2:
        small = cv2.imdecode(np.frombuffer(r480.content, np.uint8), cv2.IMREAD_COLOR)
        check("变体宽度为 480", small is not None and small.shape[1] == 480,
              str(None if small is None else small.shape))
        check(
            "等比保持（1200×800 → 480×320）",
            small is not None and small.shape[0] == 320,
            str(None if small is None else small.shape),
        )
        check(
            "体积明显小于原图",
            len(r480.content) < len(COVER_BYTES) / 2,
            f"{len(r480.content) / 1024:.1f}KB vs {len(COVER_BYTES) / 1024:.1f}KB",
        )
        cache_dir = settings.derived_dir / VIDEO_THUMB_CACHE_DIRNAME
        cached = list(cache_dir.glob(f"{mid}-*-w480-q85.jpg"))
        check("变体落在独立缓存目录", bool(cached), str(cached[:1]))
        if cached:
            first_mtime = cached[0].stat().st_mtime_ns
            client.get(f"/api/music-videos/{mid}/thumbnail", params={"w": 480})
            check("二次请求复用缓存不重做", cached[0].stat().st_mtime_ns == first_mtime)
        check("变体与实体图片缓存隔离", not list((settings.derived_dir / "entity-img-cache").glob("*.jpg")))
    else:
        skip("变体形状/体积/缓存断言", "本机无 cv2，走回退路径")
        check("无 cv2 时优雅回退原图", r480.content == COVER_BYTES, f"{len(r480.content)}B")

    print("== D. 源图比 w 更小时不放大 ==")
    r2000 = client.get(f"/api/music-videos/{mid}/thumbnail", params={"w": 1920})
    check("200", r2000.status_code == 200, str(r2000.status_code))
    check(
        "回退原图（不放大）",
        r2000.content == COVER_BYTES,
        f"{len(r2000.content)}B",
    )

    print("== E. 参数校验 ==")
    check("w=99999 拒绝", client.get(f"/api/music-videos/{mid}/thumbnail", params={"w": 99999}).status_code == 422)
    check("w=10 拒绝", client.get(f"/api/music-videos/{mid}/thumbnail", params={"w": 10}).status_code == 422)
    check("w 非数字拒绝", client.get(f"/api/music-videos/{mid}/thumbnail", params={"w": "abc"}).status_code == 422)

    print("== F. 变体文件名与质量参数 ==")
    if HAVE_CV2:
        src_file = settings.derived_dir / "thumbnails" / f"{mid}.jpg"
        v_default = resized_variant(src_file, 320)
        v_q85 = resized_variant(
            src_file, 320, quality=85, cache_dirname=VIDEO_THUMB_CACHE_DIRNAME
        )
        check("默认质量文件名不含 -q 后缀", bool(v_default) and "-q" not in v_default.name,
              v_default.name if v_default else "None")
        check("非默认质量带 -qNN 后缀", bool(v_q85) and v_q85.name.endswith("-w320-q85.jpg"),
              v_q85.name if v_q85 else "None")
        check("默认质量常量未被改动", DEFAULT_QUALITY == 92, str(DEFAULT_QUALITY))
        v_gif = resized_variant(settings.derived_dir / "thumbnails" / f"{mid}.gif", 320)
        check("源不存在时返回 None", v_gif is None)
    else:
        skip("变体文件名断言", "本机无 cv2")

print("== G. 删除视频时清理变体缓存 ==")
variant_dir = settings.derived_dir / VIDEO_THUMB_CACHE_DIRNAME
variant_dir.mkdir(parents=True, exist_ok=True)
# 前缀陷阱：id=1 的变体不能把 id=101 的一起删掉（文件名分隔符是 "-"）
other_variant = variant_dir / f"{mid + 100}-abc-w480-q85.jpg"
other_variant.write_bytes(b"x")
db = SessionLocal()
mv_row = db.get(MusicVideo, mid)
_purge_derived_for(db, mv_row)
db.close()
if HAVE_CV2:
    left = list(variant_dir.glob(f"{mid}-*"))
    check("变体缓存被清理", not left, str(left))
else:
    skip("变体缓存被清理", "本机无 cv2，未生成变体")
check("不误删其它视频的变体", other_variant.is_file())
check(
    "原封面一并清理",
    not (settings.derived_dir / "thumbnails" / f"{mid}.jpg").exists(),
)

print(f"\n{PASS} passed, {FAIL} failed, {SKIP} skipped")
sys.exit(1 if FAIL else 0)
