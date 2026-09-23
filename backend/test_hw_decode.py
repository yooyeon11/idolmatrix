"""硬件解码（VAAPI 全链路）决策逻辑验证 —— 不依赖真实 GPU。

为什么能离线测：硬解判定完全建立在「能力探测结果」之上，探测本身被 mock 掉之后，
决策矩阵、滤镜链、命令参数都是纯逻辑，可在任何机器（含 Windows 开发机）上确定性地验。

覆盖：
  1. _canon_codec 编码名归一
  2. _parse_vainfo 解析（Intel iHD 与 AMD radeonsi 两种真实输出样本）
  3. _should_hw_decode 决策矩阵（off / 未编译解码器 / 能力未知 / 编码不在可解列表 / 可解 / force）
  4. select() 硬解路径的命令参数与滤镜链
  5. select() 关闭硬解时严格回到历史行为（软解 + hwupload）
  6. 多渲染节点时的节点挑选
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.core.config import settings
from app.media import hw_manager as hwm
from app.media.hw_manager import HardwareAccelerationManager

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


# ===== 真实 vainfo 输出样本 =====
# Intel 12 代 iHD：注意 VAProfileH264High 出现两次（VLD / EncSlice），
# 这是最容易写错的地方 —— 覆盖式赋值会把 h264 判成不可硬解。
VAINFO_INTEL_IHD = """libva info: VA-API version 1.24.0
libva info: Trying to open /usr/lib/jellyfin-ffmpeg/lib/dri/iHD_drv_video.so
libva info: Found init function __vaDriverInit_1_24
libva info: va_openDriver() returns 0
vainfo: VA-API version: 1.24 (libva 2.24.0)
vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics - 26.1.3 (e0f1b22)
vainfo: Supported profile and entrypoints
      VAProfileMPEG2Simple            :\tVAEntrypointVLD
      VAProfileMPEG2Main              :\tVAEntrypointVLD
      VAProfileH264Main               :\tVAEntrypointVLD
      VAProfileH264High               :\tVAEntrypointVLD
      VAProfileH264High               :\tVAEntrypointEncSlice
      VAProfileHEVCMain               :\tVAEntrypointVLD
      VAProfileHEVCMain               :\tVAEntrypointEncSlice
      VAProfileHEVCMain10             :\tVAEntrypointVLD
      VAProfileVP9Profile0            :\tVAEntrypointVLD
      VAProfileVP9Profile2            :\tVAEntrypointVLD
      VAProfileAV1Profile0            :\tVAEntrypointVLD
      VAProfileAV1Profile1            :\tVAEntrypointVLD
"""

# AMD radeonsi：同一行里 VLD + EncSlice 用逗号分隔，且不含 AV1 的 Profile1
VAINFO_AMD_RADEONSI = """vainfo: VA-API version: 1.24 (libva 2.24.0)
vainfo: Driver version: Mesa Gallium driver 25.2.0 for AMD Radeon 780M (radeonsi, phoenix, LLVM 20.1.0)
vainfo: Supported profile and entrypoints
      VAProfileMPEG2Simple            :\tVAEntrypointVLD
      VAProfileH264Main               :\tVAEntrypointVLD, VAEntrypointEncSlice
      VAProfileH264High               :\tVAEntrypointVLD, VAEntrypointEncSlice
      VAProfileHEVCMain               :\tVAEntrypointVLD, VAEntrypointEncSlice
      VAProfileHEVCMain10             :\tVAEntrypointVLD, VAEntrypointEncSlice
      VAProfileVP9Profile0            :\tVAEntrypointVLD
      VAProfileAV1Profile0            :\tVAEntrypointVLD
"""

# 只报告编码能力（无 VLD）——不能算可硬解
VAINFO_ENCODE_ONLY = """vainfo: VA-API version: 1.24 (libva 2.24.0)
vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics - 26.1.3
vainfo: Supported profile and entrypoints
      VAProfileH264High               :\tVAEntrypointEncSlice
      VAProfileHEVCMain               :\tVAEntrypointEncSlice
"""


def make_mgr(vaapi_ok=True, decoders=("h264", "hevc", "vp9", "av1"), decode_mode="auto"):
    """构造一个探测结果已固定的 manager（不触碰真实 GPU / vainfo）。"""
    mgr = HardwareAccelerationManager()
    mgr.reset()
    mgr._qsv = {"available": False, "checks": [], "message": "mock"}
    mgr._vaapi = {"available": vaapi_ok, "checks": [], "message": "mock"}
    mgr._probed = True
    mgr._check_encoder = lambda name: True
    mgr._check_decoder = lambda name: True
    mgr._vaapi_decode = {
        "available": True,
        "decoders": sorted(decoders),
        "profiles": {},
        "driver": "mock driver",
        "version": "1.24.0",
        "message": "mock",
    }
    settings.transcode_hw_decode = decode_mode
    settings.transcode_hwaccel = "auto"
    return mgr


def main():
    print("== 1. _canon_codec 编码名归一 ==")
    for raw, want in [("H264", "h264"), ("avc1", "h264"), ("h265", "hevc"),
                      ("av01", "av1"), ("VP09", "vp9"), ("", ""), (None, "")]:
        check(f"{raw!r} -> {want!r}", hwm._canon_codec(raw) == want, hwm._canon_codec(raw))

    print("\n== 2. _parse_vainfo（Intel iHD 样本）==")
    intel = hwm._parse_vainfo(VAINFO_INTEL_IHD)
    check("可硬解集合", intel["decoders"] == ["av1", "h264", "hevc", "mpeg2video", "vp9"], intel["decoders"])
    check("h264 未被 EncSlice 覆盖", "h264" in intel["decoders"], "（同 profile 多行必须累加 entrypoint）")
    check("h264 同时含 VLD 与 EncSlice",
          sorted(intel["profiles"]["VAProfileH264High"]) == ["VAEntrypointEncSlice", "VAEntrypointVLD"],
          intel["profiles"]["VAProfileH264High"])
    check("驱动名解析", "Intel iHD" in intel["driver"], intel["driver"])
    check("libva 版本解析", intel["version"].startswith("1.24"), intel["version"])

    print("\n== 3. _parse_vainfo（AMD radeonsi 样本，厂商无关性）==")
    amd = hwm._parse_vainfo(VAINFO_AMD_RADEONSI)
    check("可硬解集合", amd["decoders"] == ["av1", "h264", "hevc", "mpeg2video", "vp9"], amd["decoders"])
    check("同行 VLD+EncSlice 正确识别",
          sorted(amd["profiles"]["VAProfileH264High"]) == ["VAEntrypointEncSlice", "VAEntrypointVLD"],
          amd["profiles"]["VAProfileH264High"])
    check("驱动名解析", "radeonsi" in amd["driver"], amd["driver"])

    print("\n== 4. 只有编码能力（无 VLD）不算可硬解 ==")
    enc_only = hwm._parse_vainfo(VAINFO_ENCODE_ONLY)
    check("decoders 为空", enc_only["decoders"] == [], enc_only["decoders"])

    print("\n== 5. _should_hw_decode 决策矩阵 ==")
    mgr = make_mgr()
    ok, why = mgr._should_hw_decode("av1")
    check("av1 可硬解", ok is True, why)
    ok, why = mgr._should_hw_decode("AV01")
    check("别名 av01 归一后可硬解", ok is True, why)
    ok, why = mgr._should_hw_decode("mpeg4")
    check("不在可解列表 -> 否", ok is False and "不在" in why, why)
    ok, why = mgr._should_hw_decode("")
    check("源编码未知 -> 否", ok is False and "未知" in why, why)

    mgr = make_mgr(decode_mode="off")
    ok, why = mgr._should_hw_decode("av1")
    check("off -> 否", ok is False and "关闭" in why, why)

    mgr = make_mgr(decode_mode="force")
    ok, why = mgr._should_hw_decode("mpeg4")
    check("force -> 是（跳过能力探测）", ok is True, why)

    mgr = make_mgr()
    mgr._check_decoder = lambda name: False
    ok, why = mgr._should_hw_decode("av1")
    check("ffmpeg 未编译解码器 -> 否", ok is False and "未编译" in why, why)

    mgr = make_mgr()
    mgr._vaapi_decode = {"available": False, "decoders": [], "profiles": {}, "driver": "",
                         "version": "", "message": "未找到 vainfo 工具"}
    ok, why = mgr._should_hw_decode("av1")
    check("能力未知 -> 否（保守）", ok is False and "vainfo" in why, why)

    print("\n== 6. 滤镜链构建 ==")
    check("硬解 + 缩放", HardwareAccelerationManager._build_vaapi_hwdecode_filter("1920:1080")
          == "scale_vaapi=w=1920:h=1080:format=nv12",
          HardwareAccelerationManager._build_vaapi_hwdecode_filter("1920:1080"))
    check("硬解 + 不缩放（仍需转 nv12）",
          HardwareAccelerationManager._build_vaapi_hwdecode_filter(None) == "scale_vaapi=format=nv12",
          HardwareAccelerationManager._build_vaapi_hwdecode_filter(None))
    check("软解路径保持历史行为（含 hwupload）",
          HardwareAccelerationManager._build_vaapi_filter("1920:1080")
          == "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,format=nv12,hwupload",
          HardwareAccelerationManager._build_vaapi_filter("1920:1080"))

    print("\n== 7. select() 硬解路径命令参数 ==")
    mgr = make_mgr()
    sel = mgr.select(preference="auto", video_codec="h264", scale="1920:1080", source_codec="av1")
    check("method=vaapi", sel.method == "vaapi", sel.method)
    check("decode_mode=hw", sel.decode_mode == "hw", sel.decode_mode)
    check("含 -hwaccel vaapi", "-hwaccel" in sel.decode_args and "vaapi" in sel.decode_args, str(sel.decode_args))
    check("含 -hwaccel_output_format vaapi",
          "-hwaccel_output_format" in sel.decode_args and "vaapi" in sel.decode_args, str(sel.decode_args))
    check("绑定指定 hw 设备", "va" in sel.decode_args and "-hwaccel_device" in sel.decode_args, str(sel.decode_args))
    check("仍保留 init_hw_device", any("vaapi=va:" in a for a in sel.decode_args), str(sel.decode_args))
    check("滤镜走 scale_vaapi", sel.video_filter == "scale_vaapi=w=1920:h=1080:format=nv12", sel.video_filter)
    check("滤镜不含 hwupload（帧本就在显存）", "hwupload" not in (sel.video_filter or ""), sel.video_filter)
    check("诊断注明全链路硬解", "全链路硬解" in str(sel.diagnostic.get("note")), sel.diagnostic.get("note"))

    print("\n== 8. select() 不缩放时的硬解滤镜 ==")
    mgr = make_mgr()
    sel = mgr.select(preference="auto", video_codec="h264", scale=None, source_codec="vp9")
    check("滤镜 = scale_vaapi=format=nv12", sel.video_filter == "scale_vaapi=format=nv12", sel.video_filter)
    check("decode_mode=hw", sel.decode_mode == "hw", sel.decode_mode)

    print("\n== 9. select() 关闭硬解 = 完全回到历史行为 ==")
    mgr = make_mgr(decode_mode="off")
    sel = mgr.select(preference="auto", video_codec="h264", scale="1920:1080", source_codec="av1")
    check("method=vaapi", sel.method == "vaapi", sel.method)
    check("decode_mode=sw", sel.decode_mode == "sw", sel.decode_mode)
    check("无 -hwaccel", "-hwaccel" not in sel.decode_args, str(sel.decode_args))
    check("decode_args 与旧版一致",
          sel.decode_args[1].startswith("vaapi=va:") and "-filter_hw_device" in sel.decode_args,
          str(sel.decode_args))
    check("滤镜回到 hwupload 版",
          sel.video_filter == "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,format=nv12,hwupload",
          sel.video_filter)
    check("诊断说明未启用硬解的原因", "未启用硬解原因" in str(sel.diagnostic.get("note")),
          sel.diagnostic.get("note"))

    print("\n== 10. 能力不足时自动降级为软解 + 硬编 ==")
    mgr = make_mgr(decoders=("h264",))  # GPU 只能解 h264，源是 av1
    sel = mgr.select(preference="auto", video_codec="h264", scale="1280:720", source_codec="av1")
    check("仍走 vaapi（编码侧不变）", sel.method == "vaapi", sel.method)
    check("解码降级为 sw", sel.decode_mode == "sw", sel.decode_mode)
    check("滤镜为软解版", "hwupload" in (sel.video_filter or ""), sel.video_filter)

    print("\n== 11. 多渲染节点：挑第一个真能初始化的 ==")
    mgr = make_mgr()
    nodes = ["/dev/dri/renderD128", "/dev/dri/renderD129"]
    orig_nodes, orig_probe = hwm._dri_render_nodes, mgr._probe_device_init
    hwm._dri_render_nodes = lambda: list(nodes)
    mgr._probe_device_init = lambda arg, label: (not arg.endswith("renderD128"), "mock")
    try:
        picked = mgr._pick_vaapi_node()
        check("跳过初始化失败的 renderD128，选 renderD129", picked == "/dev/dri/renderD129", picked)
        mgr._vaapi_node = None
        mgr._probe_device_init = lambda arg, label: (False, "mock")
        picked = mgr._pick_vaapi_node()
        check("全部失败时退回第一个", picked == "/dev/dri/renderD128", picked)
        mgr._vaapi_node = None
        hwm._dri_render_nodes = lambda: []
        picked = mgr._pick_vaapi_node()
        check("无设备时用默认路径且不探测", picked == hwm.DEFAULT_RENDER_NODE, picked)
    finally:
        hwm._dri_render_nodes = orig_nodes
        mgr._probe_device_init = orig_probe

    print("\n== 12. _summary 暴露硬解信息 ==")
    mgr = make_mgr()
    s = mgr._summary()
    check("含 decode", "decode" in s and s["decode"]["decoders"] == ["av1", "h264", "hevc", "vp9"], str(s.get("decode")))
    check("含 decode_mode", s.get("decode_mode") == "auto", s.get("decode_mode"))

    # 复原全局设置，避免影响同进程内的其它用例
    settings.transcode_hw_decode = "auto"

    print(f"\n===== 硬解决策验证：{PASS} 通过 / {FAIL} 失败 =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
