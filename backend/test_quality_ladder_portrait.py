"""画质档位必须按「短边」判定：竖屏与横屏同名档位行为必须一致。

背景（NAS 真实库实测，872 条里竖屏占 39%，几乎全是 AV1/VP9 + Opus）：
- 横屏 3840×2160 选 2160p → **直出** ✅
- 竖屏 2160×3840 选 2160p → 旧实现判「需要降分辨率」强制转码，还缩成 1216×2160
  （只剩约 1/3 像素），同一个档位横竖不对称
- 竖屏 1080×1920 的菜单里甚至出现 1440p（比源还高一档），选了缩成 810×1440

根因：档位门槛一律用 `height`，而统计侧 `video_meta.RESOLUTION_TIERS` 早已统一按
`min(width, height)`。本测试锁定短边口径，并**要求横屏行为零变化**（回归护栏）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_TMP = Path(tempfile.mkdtemp(prefix="quality_ladder_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_TMP / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_TMP / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_TMP / "incoming")

from app.media.client_capabilities import ClientCapabilities  # noqa: E402
from app.media.media_info import MediaInfo, MediaStream  # noqa: E402
from app.media.transcoding_profile import (  # noqa: E402
    available_qualities,
    long_side,
    quality_rung,
    resolve_target_dimensions,
    short_side,
)
from app.services.playback_resolver import (  # noqa: E402
    PLAY_MODE_DIRECT_PLAY,
    PLAY_MODE_DIRECT_STREAM,
    PLAY_MODE_TRANSCODE,
    _original_meets_request,
    resolve_playback_strategy,
)

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


def mk_info(*, width, height, vcodec="av1", acodec="opus", container="mkv",
            pixfmt="yuv420p", bit_rate=25_000_000):
    vs = MediaStream(
        index=0, codec_type="video", codec_name=vcodec,
        width=width, height=height, pixel_format=pixfmt,
        frame_rate=30.0, bit_rate=bit_rate, is_default=True,
    )
    aas = MediaStream(
        index=1, codec_type="audio", codec_name=acodec,
        channels=2, language="kor", is_default=True, bit_rate=192_000,
    )
    return MediaInfo(
        path=f"/fake/{width}x{height}.{container}", container=container,
        duration=300.0, bit_rate=bit_rate,
        video_streams=[vs], audio_streams=[aas],
    )


def dims(decision):
    return f"{decision.width}x{decision.height}"


def main():
    web = ClientCapabilities.modern_browser()

    # ===== A. 短边 / 长边辅助 =====
    print("== A. short_side / long_side ==")
    check("横屏 3840×2160 短边=2160", short_side(3840, 2160) == 2160)
    check("竖屏 2160×3840 短边=2160", short_side(2160, 3840) == 2160)
    check("竖屏 1080×1920 短边=1080", short_side(1080, 1920) == 1080)
    check("横屏 7680×4320 短边=4320", short_side(7680, 4320) == 4320)
    check("长边 竖屏 2160×3840 = 3840", long_side(2160, 3840) == 3840)
    check("宽缺失 → 退回高", short_side(0, 1920) == 1920)
    check("高缺失 → 退回宽", short_side(1920, None) == 1920)
    check("两边都缺 → 0", short_side(0, 0) == 0)

    # ===== B. available_qualities（短边）=====
    print("== B. available_qualities 按短边 ==")
    full = ["original", "2160p", "1080p", "720p"]
    fhd = ["original", "1080p", "720p"]
    check("横屏 4K 菜单完整", available_qualities(3840, 2160) == full, available_qualities(3840, 2160))
    check("竖屏 4K 菜单=横屏 4K（关键修复）", available_qualities(2160, 3840) == full, available_qualities(2160, 3840))
    check("竖屏 FHD 菜单（v3.4.4 档位收敛后）", available_qualities(1080, 1920) == fhd, available_qualities(1080, 1920))
    check("横屏 FHD 菜单", available_qualities(1920, 1080) == fhd, available_qualities(1920, 1080))
    check("竖屏 8K 菜单完整", available_qualities(4320, 7680) == full)
    check(
        "720P 源不出 1080p（回归）",
        available_qualities(1280, 720) == ["original", "720p"],
        available_qualities(1280, 720),
    )
    check("宽缺失 → 只 original（不猜朝向）", available_qualities(0, 1920) == ["original"])
    check("高缺失 → 只 original", available_qualities(1920, 0) == ["original"])
    check(
        "客户端短边上限 1080 → 裁掉 2160p（两边口径一致）",
        available_qualities(3840, 2160, client_max_height=1080, client_max_width=1920) == fhd,
        available_qualities(3840, 2160, client_max_height=1080, client_max_width=1920),
    )

    # ===== C. _original_meets_request（短边）=====
    print("== C. _original_meets_request 按短边 ==")
    check("original 恒满足", _original_meets_request("original", mk_info(width=4320, height=7680)))
    check("竖屏 4K 选 2160p 已满足（关键修复）", _original_meets_request("2160p", mk_info(width=2160, height=3840)))
    check("横屏 4K 选 2160p 已满足（回归）", _original_meets_request("2160p", mk_info(width=3840, height=2160)))
    check("竖屏 FHD 选 1080p 已满足（关键修复）", _original_meets_request("1080p", mk_info(width=1080, height=1920)))
    check("横屏 FHD 选 1080p 已满足（回归）", _original_meets_request("1080p", mk_info(width=1920, height=1080)))
    check("横屏 FHD 选 720p 不满足（回归）", not _original_meets_request("720p", mk_info(width=1920, height=1080)))
    check("竖屏 FHD 选 720p 不满足", not _original_meets_request("720p", mk_info(width=1080, height=1920)))
    check("竖屏 4K 选 1080p 不满足（降档转码路径）", not _original_meets_request("1080p", mk_info(width=2160, height=3840)))
    check("未知档位回退 original（恒满足）", _original_meets_request("1440p", mk_info(width=2160, height=3840)))
    check("横屏 8K 选 2160p 不满足", not _original_meets_request("2160p", mk_info(width=7680, height=4320)))

    # ===== D. resolve_target_dimensions（压短边、保朝向）=====
    print("== D. resolve_target_dimensions 压短边保朝向 ==")

    def tgt(q, w, h):
        return resolve_target_dimensions(quality_rung(q), w, h)

    check("横屏 4K→1080p = 1920×1080（回归）", tgt("1080p", 3840, 2160) == (1920, 1080))
    check("横屏 4K→720p = 1280×720（回归）", tgt("720p", 3840, 2160) == (1280, 720), tgt("720p", 3840, 2160))
    check("竖屏 4K→1080p = 1080×1920（旧为 608×1080）", tgt("1080p", 2160, 3840) == (1080, 1920), tgt("1080p", 2160, 3840))
    check("竖屏 4K 选 2160p 不缩放", tgt("2160p", 2160, 3840) == (2160, 3840))
    check("竖屏 FHD 选 1080p 不缩放", tgt("1080p", 1080, 1920) == (1080, 1920))
    check("竖屏 FHD→720p = 720×1280", tgt("720p", 1080, 1920) == (720, 1280), tgt("720p", 1080, 1920))
    check("横屏 FHD→720p = 1280×720（回归）", tgt("720p", 1920, 1080) == (1280, 720))
    check("竖屏 8K→2160p = 2160×3840", tgt("2160p", 4320, 7680) == (2160, 3840), tgt("2160p", 4320, 7680))
    check("original 不缩放", tgt("original", 4320, 7680) == (4320, 7680))
    check("档位只降不升（480p 源选 2160p 不变大）", tgt("2160p", 854, 480) == (854, 480), tgt("2160p", 854, 480))
    check("超宽 3840×800→1080p 受长边上限约束 = 1920×400", tgt("1080p", 3840, 800) == (1920, 400), tgt("1080p", 3840, 800))
    check("双数对齐（奇数源）", all(v % 2 == 0 for v in tgt("720p", 3839, 2161)))

    # ===== E. 端到端决策（横竖对称 + 横屏零回归）=====
    print("== E. resolve_playback_strategy 端到端（现代浏览器能力）==")

    def decide(w, h, q, **kw):
        return resolve_playback_strategy(mk_info(width=w, height=h, **kw), web, q)

    d = decide(2160, 3840, "original")
    check("竖屏 4K original → direct_stream", d.play_mode == PLAY_MODE_DIRECT_STREAM, d.play_mode)
    check("竖屏 4K original → fMP4 / 视频 copy", d.container == "hls_fmp4" and d.video_codec == "copy", f"{d.container}/{d.video_codec}")

    d = decide(2160, 3840, "2160p")
    check("竖屏 4K 选 2160p → direct_stream（核心修复）", d.play_mode == PLAY_MODE_DIRECT_STREAM, d.play_mode)
    check("竖屏 4K 选 2160p 不缩分辨率", dims(d) == "2160x3840", dims(d))

    d = decide(3840, 2160, "2160p")
    check("横屏 4K 选 2160p → direct_stream（回归）", d.play_mode == PLAY_MODE_DIRECT_STREAM, d.play_mode)

    d = decide(1080, 1920, "1080p")
    check("竖屏 FHD 选 1080p → direct_stream（核心修复）", d.play_mode == PLAY_MODE_DIRECT_STREAM, d.play_mode)

    d = decide(1920, 1080, "1080p")
    check("横屏 FHD 选 1080p → direct_stream（回归）", d.play_mode == PLAY_MODE_DIRECT_STREAM, d.play_mode)

    d = decide(2160, 3840, "1080p")
    check("竖屏 4K 选 1080p → transcode", d.play_mode == PLAY_MODE_TRANSCODE, d.play_mode)
    check("竖屏 4K 选 1080p 输出保朝向 1080×1920", dims(d) == "1080x1920", dims(d))

    d = decide(3840, 2160, "1080p")
    check("横屏 4K 选 1080p → transcode 1920×1080（回归）", d.play_mode == PLAY_MODE_TRANSCODE and dims(d) == "1920x1080", dims(d))

    d = decide(4320, 7680, "2160p")
    check("竖屏 8K 选 2160p → transcode", d.play_mode == PLAY_MODE_TRANSCODE, d.play_mode)
    check("竖屏 8K 选 2160p 输出 2160×3840", dims(d) == "2160x3840", dims(d))

    d = decide(7680, 4320, "2160p")
    check("横屏 8K 选 2160p → transcode 3840×2160（回归）", d.play_mode == PLAY_MODE_TRANSCODE and dims(d) == "3840x2160", dims(d))

    # 横屏 H.264 回归（对应 test_playback_manager G1.5 / G1.6）
    d = resolve_playback_strategy(
        mk_info(width=1920, height=1080, container="mp4", vcodec="h264", acodec="aac"), web, "720p"
    )
    check("横屏 1080p 源选 720p → transcode 1280×720（回归）", d.play_mode == PLAY_MODE_TRANSCODE and dims(d) == "1280x720", dims(d))

    d = resolve_playback_strategy(
        mk_info(width=1280, height=720, container="mp4", vcodec="h264", acodec="aac"), web, "720p"
    )
    check("横屏 720p 源选 720p → direct_play（回归）", d.play_mode == PLAY_MODE_DIRECT_PLAY, d.play_mode)

    d = resolve_playback_strategy(
        mk_info(width=720, height=1280, container="mp4", vcodec="h264", acodec="aac"), web, "720p"
    )
    check("竖屏 720p 源选 720p → direct_play（关键修复）", d.play_mode == PLAY_MODE_DIRECT_PLAY, d.play_mode)

    # ===== F. 能力档位没有被误改 =====
    print("== F. 能力档位 ==")
    check("modern_browser 含 av1", web.supports_video_codec("av1"))
    check("modern_browser 含 vp9", web.supports_video_codec("vp9"))
    check("modern_browser 含 opus", web.supports_audio_codec("opus"))
    check("modern_browser 不设分辨率上限", web.max_height == 0 and web.max_width == 0)
    check("modern_browser 能 remux MKV（容器表含 webm）", web.supports_container("webm"))
    dflt = ClientCapabilities.defaults()
    check("defaults 仍保守：无 av1", not dflt.supports_video_codec("av1"))
    check("defaults 仍保守：短边上限 1080", dflt.max_height == 1080 and dflt.max_width == 1920)
    d = resolve_playback_strategy(mk_info(width=2160, height=3840), dflt, "original")
    check("defaults 对竖屏 4K → transcode（保守兜底不变）", d.play_mode == PLAY_MODE_TRANSCODE, d.play_mode)

    print(f"\n===== 画质档位（短边口径）验证：{PASS} 通过 / {FAIL} 失败 =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
