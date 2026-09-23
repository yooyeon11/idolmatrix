"""硬件加速管理器：VAAPI / QSV / CPU 的统一探测、选择与回退。

设计目标（v3.4.2 起改造）
-------------------------
**厂商无关**。不写死任何 GPU 型号、厂商或驱动名 —— 换 NAS / 换显卡不需要改代码：

  · 设备节点 —— 扫描 /dev/dri/renderD*（Intel / AMD / 其它厂商共用同一套 render 节点，
                且实际节点可能是 renderD129 而不是 renderD128），逐个真初始化，取第一个能用的。
  · 解码能力 —— 跑 ``vainfo`` 读它报告的 VAProfile / VAEntrypoint 表。
                这是「这块 GPU 到底能解哪些编码」的唯一可靠来源，
                格式在 Intel(iHD/i965) 与 AMD(radeonsi) 上是同一套。
  · 编码能力 —— ``ffmpeg -encoders`` 里是否编入 h264_vaapi / hevc_vaapi / *_qsv。
  · 真的能跑 —— 用 h264_vaapi / h264_qsv **真编一帧**。
                ⚠ 只做 device_init 会得到「自检绿、实际软转」的假阳性（本项目踩过）。
  任何一项探不到就退回下一档，最差退到 CPU 软转 —— 绝不靠"猜"。

优先级
------
  auto        = VAAPI → CPU
  qsv / vaapi = 用户显式指定；不可用则回退 CPU
  none / cpu  = 强制 CPU

VAAPI 的两条路子（由 ``TRANSCODE_HW_DECODE`` 控制）
--------------------------------------------------
  hw（auto 且能力探测通过）：**GPU 解码 → scale_vaapi → GPU 编码**，CPU 基本不参与。
      这是本版本新增的「发挥 GPU 能力」路径：以前解码一直在 CPU 上做，
      对 57% AV1 / 42% VP9 的片库来说，解码正是瓶颈。
  sw（探测不通过 / 显式关闭）：软解 → ``format=nv12,hwupload`` → GPU 编码（历史行为，
      永远可用的保守档）。

QSV 为什么仍不进 auto
---------------------
QSV（oneVPL）是 Intel 专有，且在此前环境里报过 Unsupported ratecontrol /
frame rate / pixel format；auto 里放它会让"能用"这件事依赖具体机型。
现在改为：仅显式 preference="qsv" 时启用，且**必须通过真实试编探测**，
探测失败自动回退 CPU（见 ``_probe_qsv_encode``）。
"""

from __future__ import annotations

import glob
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.ffmpeg import FFmpegNotFoundError, require_ffmpeg

# ===== 设备路径（Docker 透传的 GPU 渲染节点）=====
# 默认值只作兜底；实际节点由 _dri_render_nodes() 扫描决定
DEFAULT_RENDER_NODE = "/dev/dri/renderD128"
QSV_DEVICE_PATH = DEFAULT_RENDER_NODE
VAAPI_DEVICE_PATH = DEFAULT_RENDER_NODE

# x264 独有、QSV 不支持的 preset，映射为 QSV 最接近的档位
QSV_PRESET_MAP = {
    "ultrafast": "veryfast",
    "superfast": "veryfast",
    "placebo": "veryslow",
}

# (硬件方法, 目标视频编码) -> ffmpeg 编码器名
_ENCODER_TABLE = {
    ("qsv", "h264"): "h264_qsv",
    ("qsv", "hevc"): "hevc_qsv",
    ("vaapi", "h264"): "h264_vaapi",
    ("vaapi", "hevc"): "hevc_vaapi",
    ("cpu", "h264"): "libx264",
    ("cpu", "hevc"): "libx265",
}

_HW_METHODS = ("qsv", "vaapi")

# ===== VA-API 解码能力判定表（厂商无关：只看 profile 名）=====
# ffprobe 的 codec_name -> VA profile 前缀。Intel iHD / i965 与 AMD radeonsi
# 报告的 profile 取名一致，因此同一张表对两家都成立。
_VA_PROFILE_PREFIX = {
    "h264": "VAProfileH264",
    "hevc": "VAProfileHEVC",
    "vp8": "VAProfileVP8",
    "vp9": "VAProfileVP9",
    "av1": "VAProfileAV1",
    "mpeg2video": "VAProfileMPEG2",
    "vc1": "VAProfileVC1",
    "jpeg": "VAProfileJPEG",
}

# codec_name 的常见别名 -> 规范名（MSE/容器里可能出现 avc1 / h265 / av01）
_CODEC_ALIAS = {
    "avc1": "h264",
    "avc3": "h264",
    "h265": "hevc",
    "hev1": "hevc",
    "av01": "av1",
    "vp09": "vp9",
    "vp08": "vp8",
}

# VLD = Video (decode)；只有带这个 entrypoint 的 profile 才算可硬解
_VAINFO_PROFILE_RE = re.compile(r"^\s*(VAProfile[A-Za-z0-9_]+)\s*:\s*(.+?)\s*$")
# vainfo 的版本行：`vainfo: VA-API version: 1.24 (libva 2.24.0)`
_VAINFO_VERSION_RE = re.compile(r"VA-API version:\s*(.+?)\s*$")


def _canon_codec(name: Optional[str]) -> str:
    """把各种写法的编码名归一到 ffprobe 的 codec_name。"""
    c = (name or "").strip().lower()
    return _CODEC_ALIAS.get(c, c)


def _parse_vainfo(text: str) -> dict:
    """解析 vainfo 输出 → {"profiles", "decoders", "driver", "version"}。

    抽成纯函数是为了可测：不同驱动/版本的行格式有差异，靠样本固定行为比靠真机稳。
    只有带 ``VAEntrypointVLD``（Video decode）的 profile 才算**可硬解** ——
    EncSlice 之类是编码能力，不能算进来。
    """
    profiles: Dict[str, List[str]] = {}
    for line in (text or "").splitlines():
        m = _VAINFO_PROFILE_RE.match(line)
        if m:
            # ⚠ 同一 profile 会**分行**出现，例如：
            #     VAProfileH264High : VAEntrypointVLD
            #     VAProfileH264High : VAEntrypointEncSlice
            # 必须累加 entrypoint，直接赋值会让后一行覆盖前一行 ——
            # h264 就会因为最后一行是 EncSlice 而被误判成「不可硬解」。
            entries = profiles.setdefault(m.group(1), [])
            entries.extend(p.strip() for p in m.group(2).split(",") if p.strip())
    decoders = sorted(
        codec
        for codec, prefix in _VA_PROFILE_PREFIX.items()
        if any(
            name.startswith(prefix) and any("VAEntrypointVLD" in e for e in entries)
            for name, entries in profiles.items()
        )
    )
    driver = version = ""
    for line in (text or "").splitlines():
        if "Driver version" in line and ":" in line:
            driver = line.split(":", 1)[1].strip()
        elif _VAINFO_VERSION_RE.search(line):
            # ⚠ 不能用 split("version")：行里 "version" 会出现多次，
            # 且前面还有一行 "libva info: VA-API version 1.24.0"（无冒号）。
            version = _VAINFO_VERSION_RE.search(line).group(1).strip()
    return {"profiles": profiles, "decoders": decoders, "driver": driver, "version": version}


def _dri_render_nodes() -> List[str]:
    """返回实际存在的 /dev/dri/renderD* 列表（升序）。"""
    return sorted(glob.glob("/dev/dri/renderD*"))


def _find_dri_render_node() -> str:
    """返回第一个存在的渲染节点；一个都没有时返回默认路径。

    部分 NAS 上渲染节点是 renderD129，探测与实际转码都必须指向真实节点，
    否则 VAAPI / QSV 会被误判为不可用。
    """
    nodes = _dri_render_nodes()
    return nodes[0] if nodes else DEFAULT_RENDER_NODE


def _find_vainfo() -> Optional[str]:
    """定位 vainfo：配置项 > ffmpeg 同目录 > PATH。找不到返回 None。

    vainfo 由 jellyfin-ffmpeg 官方包一并提供（Dockerfile 已 COPY 到 /usr/local/bin）。
    它用 VA-API 的通用 interface 报告「驱动 + 可解码 profile」，Intel / AMD 通用。
    """
    configured = (settings.vainfo_path or "").strip()
    if configured and Path(configured).is_file():
        return configured
    try:
        beside = Path(require_ffmpeg()).parent / "vainfo"
        if beside.is_file():
            return str(beside)
    except FFmpegNotFoundError:
        pass
    return shutil.which("vainfo")


@dataclass
class HwSelection:
    """一次转码的硬件决策结果（最终实际使用方式）。"""

    method: str  # cpu / qsv / vaapi
    video_codec: str  # h264 / hevc（语义名）
    encoder: str  # libx264 / h264_qsv / h264_vaapi ...
    decode_args: List[str] = field(default_factory=list)  # 放在 -i 前
    encode_args: List[str] = field(default_factory=list)  # -c:v 后的编码参数
    pixel_format: str = "yuv420p"
    video_filter: Optional[str] = None
    preset: Optional[str] = None
    decode_mode: str = "sw"  # hw=GPU 解码 / sw=CPU 解码 / none=CPU 全软
    diagnostic: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class HardwareAccelerationManager:
    """硬件转码探测与选择的单例管理组件。

    探测结果进程内缓存；容器 / 驱动变更后调用 reset() 重新探测。
    """

    _instance: Optional["HardwareAccelerationManager"] = None

    def __new__(cls) -> "HardwareAccelerationManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # 幂等初始化：探测结果只在首次 probe() 时写入
        if not hasattr(self, "_qsv"):
            self._qsv: dict = {}
            self._vaapi: dict = {}
            self._vaapi_decode: dict = {}
            self._probed: bool = False
            self._encoders: Optional[str] = None
            self._decoders: Optional[str] = None
            self._vaapi_node: Optional[str] = None

    # ===== 探测 =====

    def probe(self, force: bool = False) -> dict:
        """探测 QSV + VAAPI 可用性；force=True 时强制重新探测。"""
        if self._probed and not force:
            return self._summary()
        try:
            self._qsv = self._diagnose("qsv")
            self._vaapi = self._diagnose("vaapi")
        except FFmpegNotFoundError:
            # ffmpeg 缺失：无法探测，硬件统一视为不可用
            self._qsv = {"available": False, "checks": [], "message": "ffmpeg 不可用，无法探测硬件"}
            self._vaapi = {"available": False, "checks": [], "message": "ffmpeg 不可用，无法探测硬件"}
        self._probed = True
        return self._summary()

    def reset(self) -> None:
        """清除缓存，强制下次调用时重新探测（容器 / 驱动变更后使用）。"""
        self._probed = False
        self._qsv = {}
        self._vaapi = {}
        self._vaapi_decode = {}
        self._encoders = None
        self._decoders = None
        self._vaapi_node = None

    def _summary(self) -> dict:
        return {
            "available": self.available_method() is not None,
            "method": self.available_method(),
            "qsv": self._qsv,
            "vaapi": self._vaapi,
            "decode": self.vaapi_decode_caps(),
            "decode_mode": (settings.transcode_hw_decode or "auto").lower(),
        }

    # ===== 查询 =====

    def available_method(self) -> Optional[str]:
        """auto 路径优先级：VAAPI 优先，QSV 不再自动选用。"""
        if self._vaapi.get("available"):
            return "vaapi"
        return None

    def is_available(self, method: str) -> bool:
        return bool((self._qsv if method == "qsv" else self._vaapi).get("available"))

    def diagnose_report(self) -> dict:
        """完整诊断报告（供状态接口 / 排查使用）。"""
        return self.probe()

    def vaapi_decode_caps(self, force: bool = False) -> dict:
        """VA-API 解码能力（vainfo 结果，进程内缓存）。

        返回 ``{"available", "decoders", "profiles", "driver", "version", "message"}``。
        ``decoders`` 是**可硬解**的编码名列表（如 ["av1","h264","hevc","vp9"]）。
        探测失败时 available=False，调用方应保守走软解。
        """
        if self._vaapi_decode and not force:
            return self._vaapi_decode
        try:
            self._vaapi_decode = self._probe_vaapi_decode()
        except Exception as e:  # 探测本身不允许影响转码流程
            self._vaapi_decode = {
                "available": False,
                "decoders": [],
                "profiles": {},
                "driver": "",
                "version": "",
                "message": f"VA-API 解码能力探测异常：{type(e).__name__}: {e}",
            }
        return self._vaapi_decode

    # ===== 选择 =====

    def select(
        self,
        preference: str = "auto",
        video_codec: str = "h264",
        preset: str = "veryfast",
        quality: int = 23,
        scale: Optional[str] = None,
        target_height: Optional[int] = None,
        source_codec: Optional[str] = None,
    ) -> HwSelection:
        """按优先级选择本次转码的硬件方案，返回实际使用的配置。

        scale 是**精确目标尺寸** "W:H"（由 transcoding_profile.resolve_target_dimensions
        算出、已保持宽高比与偶数对齐），不是外接矩形 —— 硬解路径据此直接给
        scale_vaapi 传 w/h，不需要 force_original_aspect_ratio。
        """
        self.probe()
        video_codec = (video_codec or "h264").lower()
        source_codec = _canon_codec(source_codec)

        method: Optional[str] = None
        preference = (preference or "auto").lower()
        if preference in ("none", "cpu"):
            method = None
        elif preference in _HW_METHODS:
            # 用户显式指定：指定硬件可用则用它，否则回退 CPU
            if self.is_available(preference):
                method = preference
        else:  # auto — VAAPI only (QSV excluded from auto path)
            method = self.available_method()

        # 编码器支持校验：硬件存在但未编译对应编码器 → 回退
        if method in _HW_METHODS:
            encoder = _ENCODER_TABLE.get((method, video_codec))
            if not encoder or not self._check_encoder(encoder):
                method = None

        if method is None:
            method = "cpu"
            encoder = _ENCODER_TABLE[("cpu", video_codec)]
            decode_args: List[str] = []
            encode_args: List[str] = [
                "-preset", preset, "-crf", str(quality),
            ]
            pixel_format = "yuv420p"
            video_filter = f"scale={scale}" if scale else None
            decode_mode = "none"
            note = "CPU 软件转码（libx264/libx265）"
        elif method == "qsv":
            # QSV 完整硬件路径（仅显式 preference="qsv" 时使用）
            # auto 路径不选用 QSV：Intel 专有 + 历史上有 ratecontrol/pixel format 兼容问题
            encoder = _ENCODER_TABLE[("qsv", video_codec)]
            encode_args = [
                "-preset", QSV_PRESET_MAP.get(preset, preset),
                "-global_quality", str(quality),
                "-look_ahead", "0",
            ]
            pixel_format = "nv12"
            # 不加 -init_hw_device：-hwaccel qsv 会用默认渲染节点自建设备，
            # 而 QSV 路径的帧经 -hwaccel_output_format qsv 已是 qsv 表面，
            # scale_qsv 不需要 -filter_hw_device。探测（_probe_qsv_encode）另说，见其说明。
            decode_args = [
                "-hwaccel", "qsv",
                "-hwaccel_output_format", "qsv",
            ]
            video_filter = self._build_qsv_scale_filter(scale)
            decode_mode = "hw"
            note = f"QSV 硬件转码（{encoder} 硬解+硬编，显式指定）"
        else:  # vaapi
            encoder = _ENCODER_TABLE[("vaapi", video_codec)]
            node = self._pick_vaapi_node()
            decode_args = [
                "-init_hw_device", f"vaapi=va:{node}",
                "-filter_hw_device", "va",
            ]
            # 码率控制由 ffmpeg_engine 根据 video_bitrate 动态决定：
            # 有码率 → VBR (-b:v -maxrate -bufsize)；无码率 → CQP fallback (-qp)
            encode_args = []
            pixel_format = "nv12"

            hw_decode, decode_reason = self._should_hw_decode(source_codec)
            if hw_decode:
                # GPU 解码 → GPU 缩放/格式转换 → GPU 编码（帧始终留在显存里）
                decode_args += [
                    "-hwaccel", "vaapi",
                    "-hwaccel_output_format", "vaapi",
                    "-hwaccel_device", "va",
                ]
                video_filter = self._build_vaapi_hwdecode_filter(scale)
                decode_mode = "hw"
                note = f"VAAPI 全链路硬解（{source_codec} 硬解 → {encoder} 硬编）"
            else:
                # 保守档：软解 + 软缩放 + 上传显存 + 硬编（历史行为，永远可用）
                video_filter = self._build_vaapi_filter(scale)
                decode_mode = "sw"
                note = (
                    f"VAAPI 硬件转码（软解 {source_codec or '未知编码'} + {encoder} 硬编）"
                    f"；未启用硬解原因：{decode_reason}"
                )

        return HwSelection(
            method=method,
            video_codec=video_codec,
            encoder=encoder,
            decode_args=decode_args,
            encode_args=encode_args,
            pixel_format=pixel_format,
            video_filter=video_filter,
            preset=QSV_PRESET_MAP.get(preset, preset) if method == "qsv" else preset,
            decode_mode=decode_mode,
            diagnostic={"note": note, **self._summary()},
        )

    def _should_hw_decode(self, source_codec: str) -> Tuple[bool, str]:
        """判断本次源编码能否走 GPU 解码。返回 (能否, 人类可读原因)。

        保守原则：**任何一项不确定就返回 False**，退回软解 + 硬编。
        代价只是 CPU 忙一点；反过来（猜错）会导致 ffmpeg 直接起不来。
        """
        mode = (settings.transcode_hw_decode or "auto").strip().lower()
        if mode in ("off", "none", "cpu", "false", "0"):
            return False, "已显式关闭（TRANSCODE_HW_DECODE=off）"
        codec = _canon_codec(source_codec)
        if not codec:
            return False, "源编码未知"
        decoder = f"{codec}_vaapi"
        if not self._check_decoder(decoder):
            return False, f"当前 ffmpeg 未编译 {decoder} 解码器"
        if mode == "force":
            return True, f"已配置 force，直接尝试 {decoder}"
        caps = self.vaapi_decode_caps()
        if not caps.get("available"):
            return False, caps.get("message") or "未能确认 GPU 解码能力"
        decoders = caps.get("decoders") or []
        if codec not in decoders:
            have = "、".join(decoders) or "无"
            return False, f"{codec} 不在该 GPU 可硬解列表（可解：{have}）"
        return True, f"{decoder} 可硬解"

    # ===== 内部探测实现 =====

    def _diagnose(self, method: str) -> dict:
        """逐项探测指定硬件方法（qsv / vaapi）。"""
        device_path = _find_dri_render_node()
        encoder = _ENCODER_TABLE.get((method, "h264"), "")
        checks = []

        has_device = Path(device_path).exists()
        checks.append(
            {
                "name": "device_node",
                "label": "GPU 设备节点",
                "passed": has_device,
                "detail": (
                    f"{device_path} 存在（共发现 {len(_dri_render_nodes())} 个渲染节点）"
                    if has_device
                    else "未发现 /dev/dri/renderD*（Docker 需透传 --device /dev/dri）"
                ),
            }
        )
        if not has_device:
            return self._conclude(method, False, checks, encoder)

        has_encoder = self._check_encoder(encoder)
        checks.append(
            {
                "name": "encoder",
                "label": f"{encoder} 编码器",
                "passed": has_encoder,
                "detail": f"{encoder} 可用" if has_encoder else f"当前 ffmpeg 未编译 {encoder}",
            }
        )
        if not has_encoder:
            return self._conclude(method, False, checks, encoder)

        init_arg = "qsv=hw" if method == "qsv" else f"vaapi=hw:{device_path}"
        init_ok, detail = self._probe_device_init(init_arg, method.upper())
        checks.append(
            {
                "name": "device_init",
                "label": f"{method.upper()} 设备初始化",
                "passed": init_ok,
                "detail": detail,
            }
        )
        if not init_ok:
            if method == "vaapi":
                checks.append(
                    {
                        "name": "encode_test",
                        "label": "VAAPI 真实编码（h264_vaapi 试编一帧）",
                        "passed": False,
                        "detail": "设备初始化未通过，跳过真编码测试",
                    }
                )
            return self._conclude(method, False, checks, encoder)

        if method == "vaapi":
            enc_ok, enc_detail = self._probe_vaapi_encode(device_path)
            checks.append(
                {
                    "name": "encode_test",
                    "label": "VAAPI 真实编码（h264_vaapi 试编一帧）",
                    "passed": enc_ok,
                    "detail": enc_detail,
                }
            )
            caps = self.vaapi_decode_caps()
            decoders = caps.get("decoders") or []
            checks.append(
                {
                    "name": "decode_capability",
                    "label": "VAAPI 硬解能力（vainfo）",
                    "passed": bool(decoders),
                    "detail": (
                        f"可硬解：{'、'.join(decoders)}"
                        + (f"｜驱动：{caps.get('driver')}" if caps.get("driver") else "")
                        if decoders
                        else (caps.get("message") or "未能确认可硬解编码")
                    ),
                }
            )
            return self._conclude(method, enc_ok, checks, encoder)

        # QSV：同样要真编一帧 —— device_init 通过 ≠ 能编
        enc_ok, enc_detail = self._probe_qsv_encode()
        checks.append(
            {
                "name": "encode_test",
                "label": "QSV 真实编码（h264_qsv 试编一帧）",
                "passed": enc_ok,
                "detail": enc_detail,
            }
        )
        return self._conclude(method, enc_ok, checks, encoder)

    def _conclude(
        self, method: str, available: bool, checks: list, encoder: str
    ) -> dict:
        label = method.upper()
        if available:
            message = f"{label} 硬件转码可用：将调用 GPU（{encoder}）硬编。"
        else:
            failed = [c for c in checks if not c["passed"]]
            reason = "；".join(c["detail"] for c in failed) or "未知原因"
            message = (
                f"当前未使用 {label} 硬转，原因：{reason}。"
                "已自动回退（VAAPI / CPU 软件转码）。"
            )
        return {"available": available, "checks": checks, "message": message}

    def _probe_device_init(self, init_device_arg: str, label: str) -> tuple:
        """真实初始化一次硬件设备，返回 (是否成功, 可读详情)。"""
        try:
            res = subprocess.run(
                [
                    require_ffmpeg(),
                    "-init_hw_device",
                    init_device_arg,
                    "-f",
                    "lavfi",
                    "-i",
                    "nullsrc=s=64x64:d=0.1",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return False, str(e)
        if res.returncode == 0:
            return True, "驱动可用，初始化成功"
        return False, self._stderr_digest(
            res.stderr,
            ("error", "fail", "cannot", "libva", "drm", "qsv", "vaapi",
             "mfx", "permission", "denied", "i/o", "input/output"),
            f"{label} 设备初始化失败",
        )

    def _probe_vaapi_encode(self, device_path: str) -> tuple:
        """真实用 VAAPI 编码一帧，验证「设备 init 通过 ≠ 真能硬编」。"""
        try:
            res = subprocess.run(
                [
                    require_ffmpeg(),
                    "-hide_banner",
                    "-y",
                    "-init_hw_device",
                    f"vaapi=va:{device_path}",
                    "-filter_hw_device",
                    "va",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=128x128:rate=15:duration=0.3",
                    "-vf",
                    "format=nv12,hwupload",
                    "-c:v",
                    "h264_vaapi",
                    "-qp",
                    "28",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return False, str(e)
        if res.returncode == 0:
            return True, "h264_vaapi 真实编码成功"
        return False, self._stderr_digest(
            res.stderr,
            ("error", "fail", "cannot", "libva", "drm", "encode", "surface",
             "unsupported", "permission", "denied", "i/o", "input/output"),
            "h264_vaapi 试编失败",
        )

    def _probe_qsv_encode(self) -> tuple:
        """真实用 QSV 编码一帧。

        device_init 通过 ≠ 能编：QSV 历史上正是在真实编码这一步报
        「Unsupported ratecontrol / frame rate / pixel format」而失败，
        只做 init 会给出「自检绿、实际软转」的假阳性。

        ⚠ 探测用 `-init_hw_device qsv=hw` 是为了让 lavfi 测试图能 hwupload 上显存；
        生产命令用的是 `-hwaccel qsv`（自建设备、帧直接落在 qsv 表面）——
        两者设备建立方式不同，但**被测的编码器与速率控制参数一致**，
        即真正会失败的那一步是一致的。
        """
        try:
            res = subprocess.run(
                [
                    require_ffmpeg(),
                    "-hide_banner",
                    "-y",
                    "-init_hw_device",
                    "qsv=hw",
                    "-filter_hw_device",
                    "hw",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=128x128:rate=15:duration=0.3",
                    "-vf",
                    "format=nv12,hwupload",
                    "-c:v",
                    "h264_qsv",
                    "-global_quality",
                    "28",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return False, str(e)
        if res.returncode == 0:
            return True, "h264_qsv 真实编码成功"
        return False, self._stderr_digest(
            res.stderr,
            ("error", "fail", "cannot", "libvpl", "mfx", "drm", "encode",
             "unsupported", "ratecontrol", "pixel format", "permission", "denied"),
            "h264_qsv 试编失败",
        )

    def _probe_vaapi_decode(self) -> dict:
        """跑 vainfo，解析出「本机 GPU 可硬解哪些编码」。

        vainfo 的 profile/entrypoint 表在 Intel(iHD/i965) 与 AMD(radeonsi) 上格式一致，
        因此这里不需要区分厂商。只有带 ``VAEntrypointVLD``（Video decode）的 profile
        才算可硬解 —— 编码用的 EncSlice 不算。

        ⚠ 不能只看「有 *Profile* 字样」：驱动常把不支持的 profile 也列出来，
        必须看 entrypoint。
        """
        empty = {"available": False, "decoders": [], "profiles": {}, "driver": "", "version": ""}
        vainfo = _find_vainfo()
        if not vainfo:
            return {**empty, "message": "未找到 vainfo 工具，无法确认 GPU 解码能力（保守：软解）"}

        last_err = ""
        for extra in (["--display", "drm"], []):
            try:
                res = subprocess.run(
                    [vainfo, *extra],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as e:
                last_err = str(e)
                continue
            text = (res.stdout or "") + "\n" + (res.stderr or "")
            parsed = _parse_vainfo(text)
            if parsed["profiles"]:
                decoders = parsed["decoders"]
                return {
                    "available": True,
                    **parsed,
                    "message": (
                        f"可硬解：{'、'.join(decoders)}" if decoders else "驱动未报告任何可硬解编码（只支持硬编）"
                    ),
                }
            last_err = (
                (res.stderr or text).strip().splitlines()[-1]
                if (res.stderr or text).strip()
                else f"vainfo 退出码 {res.returncode}"
            )
        return {**empty, "message": f"vainfo 未报告任何 profile：{last_err[:200]}"}

    def _pick_vaapi_node(self) -> str:
        """选一个真正能初始化的渲染节点（多显卡机器上取第一个可用的）。

        一个节点都没有时直接返回默认路径、不做任何子进程探测 ——
        这样在没有 /dev/dri 的环境（开发机）里不会产生额外开销。
        """
        if self._vaapi_node:
            return self._vaapi_node
        nodes = _dri_render_nodes()
        if not nodes:
            return DEFAULT_RENDER_NODE
        if len(nodes) == 1:
            self._vaapi_node = nodes[0]
            return nodes[0]
        for node in nodes:
            ok, _ = self._probe_device_init(f"vaapi=probe:{node}", "VAAPI")
            if ok:
                self._vaapi_node = node
                return node
        self._vaapi_node = nodes[0]
        return nodes[0]

    @staticmethod
    def _stderr_digest(stderr: str, keywords: tuple, fallback: str) -> str:
        """从 ffmpeg stderr 里挑出最有信息量的几行。"""
        lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
        hits = [ln for ln in lines if any(k in ln.lower() for k in keywords)]
        return " | ".join((hits or lines)[-3:]) or fallback

    # ===== ffmpeg 能力查询（带缓存，避免每次 select 都开子进程）=====

    def _ffmpeg_list(self, what: str) -> str:
        """缓存 ``ffmpeg -encoders`` / ``-decoders`` 的输出。

        查不到 ffmpeg（FFmpegNotFoundError）时返回空串，
        让上层按「未编译」处理 —— 能力探测不允许把转码流程带崩。
        """
        cached = self._encoders if what == "encoders" else self._decoders
        if cached is not None:
            return cached
        try:
            res = subprocess.run(
                [require_ffmpeg(), f"-{what}"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            out = res.stdout if res.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError, FFmpegNotFoundError):
            out = ""
        if what == "encoders":
            self._encoders = out
        else:
            self._decoders = out
        return out

    def _check_encoder(self, name: str) -> bool:
        """查询当前 ffmpeg 是否编译了指定编码器。"""
        return bool(name) and name in self._ffmpeg_list("encoders")

    def _check_decoder(self, name: str) -> bool:
        """查询当前 ffmpeg 是否编译了指定解码器。"""
        return bool(name) and name in self._ffmpeg_list("decoders")

    # ===== 滤镜链构建 =====

    @staticmethod
    def _build_qsv_scale_filter(scale: Optional[str]) -> Optional[str]:
        """QSV 完整硬件路径的缩放：帧已落在 GPU（qsv 表面），用 scale_qsv 硬缩放。"""
        if not scale:
            return None
        parts = str(scale).split(":")
        if len(parts) == 2 and parts[0] and parts[1]:
            return f"scale_qsv=w={parts[0]}:h={parts[1]}"
        return f"scale_qsv={scale}"

    @staticmethod
    def _build_vaapi_hwdecode_filter(scale: Optional[str]) -> str:
        """硬解路径的滤镜链：帧已在显存（vaapi 表面），用 scale_vaapi 缩放 + 转 nv12。

        - ``scale_vaapi`` 全程在 GPU 上做，不产生「下载 → 上传」往返。
        - **必须显式 format=nv12**：h264_vaapi / hevc_vaapi 只吃 nv12，
          而 10-bit 源硬解出来是 p010 —— 在这一步转回 8-bit（输出本来也是 8-bit）。
        - scale 是精确目标尺寸（见 resolve_target_dimensions），
          因此不需要 force_original_aspect_ratio，scale_vaapi 也不支持该参数。
        """
        parts = str(scale).split(":") if scale else []
        if len(parts) == 2 and parts[0] and parts[1]:
            return f"scale_vaapi=w={parts[0]}:h={parts[1]}:format=nv12"
        return "scale_vaapi=format=nv12"

    @staticmethod
    def _build_vaapi_filter(scale: Optional[str]) -> str:
        """软解路径的 VAAPI 硬编滤镜链：软件缩放 → nv12 → hwupload 上传显存。

        force_original_aspect_ratio=decrease 确保等比缩放，不拉伸画面。
        """
        base = "format=nv12,hwupload"
        if not scale:
            return base
        parts = str(scale).split(":")
        # force_divisible_by=2：VAAPI 硬编要求宽高为偶数，非 16:9 源会直接炸
        if len(parts) == 2 and parts[0] and parts[1]:
            return (
                f"scale={parts[0]}:{parts[1]}:force_original_aspect_ratio=decrease"
                f":force_divisible_by=2,{base}"
            )
        return f"scale={scale}:force_original_aspect_ratio=decrease:force_divisible_by=2,{base}"


def get_hw_manager() -> HardwareAccelerationManager:
    return HardwareAccelerationManager()


hw_manager = get_hw_manager()
