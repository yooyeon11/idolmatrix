"""临时验证脚本：hw_manager 的 select() 分支逻辑（mock 探测结果，不依赖真实 GPU）。

验证改造后的 VAAPI 优先策略：
  auto = VAAPI → CPU（QSV 不再自动选用）
  所有源编码统一走「软解 + VAAPI 硬编」
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.media.hw_manager import HardwareAccelerationManager, hw_manager

PASS = 0


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}")
    else:
        print(f"  FAIL  {name}  {detail}")


def make_mgr(qsv_ok=False, vaapi_ok=False):
    mgr = HardwareAccelerationManager()
    mgr.reset()
    mgr._qsv = {"available": qsv_ok, "checks": [], "message": "mock"}
    mgr._vaapi = {"available": vaapi_ok, "checks": [], "message": "mock"}
    mgr._probed = True
    mgr._check_encoder = lambda name: True  # mock：假设编码器已编译
    return mgr


def main():
    print("== 1. import 与单例 ==")
    check("singleton", hw_manager is HardwareAccelerationManager())

    print("== 2. 无任何硬件: auto -> CPU, crf 分支 ==")
    mgr = make_mgr(False, False)
    sel = mgr.select(preference="auto", video_codec="h264", preset="veryfast", quality=23)
    check("method=cpu", sel.method == "cpu", sel.method)
    check("encoder=libx264", sel.encoder == "libx264", sel.encoder)
    check("crf in encode_args", "-crf" in sel.encode_args and "23" in sel.encode_args, str(sel.encode_args))
    check("pixel=yuv420p", sel.pixel_format == "yuv420p")
    check("no decode_args", sel.decode_args == [])

    print("== 3. 用户指定 qsv 但不可用 -> 回退 CPU ==")
    mgr = make_mgr(False, False)
    sel = mgr.select(preference="qsv")
    check("method=cpu (fallback)", sel.method == "cpu", sel.method)

    print("== 4. 用户指定 vaapi 但不可用 -> 回退 CPU ==")
    mgr = make_mgr(False, False)
    sel = mgr.select(preference="vaapi")
    check("method=cpu (fallback)", sel.method == "cpu", sel.method)

    print("== 5. 用户指定 none -> 强制 CPU ==")
    mgr = make_mgr(True, True)
    sel = mgr.select(preference="none")
    check("method=cpu (forced)", sel.method == "cpu", sel.method)

    print("== 5b. 用户指定 cpu -> 强制 CPU（不得落入 auto 启用 VAAPI）==")
    mgr = make_mgr(True, True)
    sel = mgr.select(preference="cpu")
    check("method=cpu (explicit)", sel.method == "cpu", sel.method)
    check("encoder=libx264", sel.encoder == "libx264", sel.encoder)
    check("无硬件 decode_args", sel.decode_args == [], str(sel.decode_args))
    check("crf in encode_args", "-crf" in sel.encode_args, str(sel.encode_args))

    print("== 6. auto + VAAPI 可用 -> VAAPI 硬编路径 (h264_vaapi, 软解+hwupload) ==")
    mgr = make_mgr(True, True)  # QSV 和 VAAPI 都可用，auto 应选 VAAPI
    sel = mgr.select(preference="auto", video_codec="h264", preset="veryfast", quality=23, scale="1920:1080")
    check("method=vaapi", sel.method == "vaapi", sel.method)
    check("encoder=h264_vaapi", sel.encoder == "h264_vaapi", sel.encoder)
    check("decode init_hw_device vaapi=va", any("vaapi=va:" in a for a in sel.decode_args), str(sel.decode_args))
    check("decode filter_hw_device va", "-filter_hw_device" in sel.decode_args and "va" in sel.decode_args, str(sel.decode_args))
    check("encode_args 为空（码率由 engine 控制）", sel.encode_args == [], str(sel.encode_args))
    check("pixel=nv12", sel.pixel_format == "nv12")
    check("vaapi filter with scale", sel.video_filter == "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,format=nv12,hwupload", sel.video_filter)

    print("== 6b. auto + VAAPI + source_codec=vp9 -> 统一 VAAPI 路径（不再区分混合路径） ==")
    mgr = make_mgr(True, True)
    sel = mgr.select(preference="auto", video_codec="h264", preset="veryfast", quality=23, source_codec="vp9", scale="1920:1080")
    check("method=vaapi (vp9 统一路径)", sel.method == "vaapi", sel.method)
    check("encoder=h264_vaapi", sel.encoder == "h264_vaapi", sel.encoder)
    check("decode init_hw_device vaapi=va", any("vaapi=va:" in a for a in sel.decode_args), str(sel.decode_args))
    check("filter 统一 hwupload", sel.video_filter == "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,format=nv12,hwupload", sel.video_filter)

    print("== 6c. source_codec=av1 无缩放 -> VAAPI 裸 hwupload ==")
    mgr = make_mgr(True, True)
    sel = mgr.select(preference="auto", source_codec="av1")
    check("method=vaapi (av1)", sel.method == "vaapi", sel.method)
    check("filter bare hwupload", sel.video_filter == "format=nv12,hwupload", sel.video_filter)
    check("decode init_hw_device vaapi=va", any("vaapi=va:" in a for a in sel.decode_args), str(sel.decode_args))

    print("== 6d. source_codec 未传 -> 默认 VAAPI 路径（向后兼容） ==")
    mgr = make_mgr(True, True)
    sel = mgr.select(preference="auto", video_codec="h264", preset="veryfast", quality=23)
    check("默认 VAAPI", sel.method == "vaapi", sel.method)
    check("decode init_hw_device vaapi=va", any("vaapi=va:" in a for a in sel.decode_args), str(sel.decode_args))

    print("== 6e. 显式 preference=qsv -> QSV 完整硬件路径（仅显式可用） ==")
    mgr = make_mgr(True, True)
    sel = mgr.select(preference="qsv", video_codec="h264", preset="ultrafast", quality=23, scale="1920:1080")
    check("method=qsv (explicit)", sel.method == "qsv", sel.method)
    check("encoder=h264_qsv", sel.encoder == "h264_qsv", sel.encoder)
    check("decode 完整硬件", sel.decode_args == ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"], str(sel.decode_args))
    check("global_quality in args", "-global_quality" in sel.encode_args, str(sel.encode_args))
    check("look_ahead 0", "-look_ahead" in sel.encode_args and "0" in sel.encode_args, str(sel.encode_args))
    check("preset mapped ultrafast->veryfast", sel.preset == "veryfast", sel.preset)
    check("pixel=nv12", sel.pixel_format == "nv12")
    check("scale_qsv 滤镜", sel.video_filter == "scale_qsv=w=1920:h=1080", sel.video_filter)

    print("== 7. auto + 仅 VAAPI 可用 -> VAAPI 分支 (hevc_vaapi, 码率由 engine 控制) ==")
    mgr = make_mgr(False, True)
    sel = mgr.select(preference="auto", video_codec="hevc", preset="veryfast", quality=23, target_height=1080)
    check("method=vaapi", sel.method == "vaapi", sel.method)
    check("encoder=hevc_vaapi", sel.encoder == "hevc_vaapi", sel.encoder)
    check("encode_args 为空", sel.encode_args == [], str(sel.encode_args))
    check("init_hw_device in decode", any("vaapi=va:" in a for a in sel.decode_args), str(sel.decode_args))
    check("filter hwupload", sel.video_filter == "format=nv12,hwupload", sel.video_filter)

    print("== 8. VAAPI scale 分支 ==")
    mgr = make_mgr(False, True)
    sel = mgr.select(preference="vaapi", scale="1280:720")
    check("filter with scale", sel.video_filter == "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,format=nv12,hwupload", sel.video_filter)

    print("== 9. VAAPI 无 scale ==")
    mgr = make_mgr(False, True)
    sel = mgr.select(preference="vaapi")
    check("filter bare hwupload", "format=nv12,hwupload" == sel.video_filter, sel.video_filter)

    print("== 10. hevc cpu 分支 ==")
    mgr = make_mgr(False, False)
    sel = mgr.select(preference="auto", video_codec="hevc")
    check("encoder=libx265", sel.encoder == "libx265", sel.encoder)

    print("== 11. available_method 优先级（VAAPI only） ==")
    mgr = make_mgr(True, True)
    check("vaapi preferred (both available)", mgr.available_method() == "vaapi", mgr.available_method())
    mgr = make_mgr(False, True)
    check("vaapi only", mgr.available_method() == "vaapi", mgr.available_method())
    mgr = make_mgr(True, False)  # 仅 QSV 可用，VAAPI 不可用
    check("qsv only -> None (auto 不选 QSV)", mgr.available_method() is None, str(mgr.available_method()))
    mgr = make_mgr(False, False)
    check("none", mgr.available_method() is None, str(mgr.available_method()))

    print("== 12. to_dict 可序列化 ==")
    mgr = make_mgr(False, False)
    d = mgr.select().to_dict()
    check("dict keys", {"method", "encoder", "decode_args", "encode_args", "pixel_format"} <= set(d), str(sorted(d)))

    print(f"\n== 结果: {PASS} 项通过 ==")


if __name__ == "__main__":
    main()
