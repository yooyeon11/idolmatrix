"""临时验证脚本：ffmpeg_engine 的命令构建 + 进度解析 + 进程生命周期。"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import app.media.ffmpeg_engine as fe_mod

fe_mod.require_ffmpeg = lambda: "/usr/bin/ffmpeg"  # mock：测试环境无 ffmpeg

from app.media.ffmpeg_engine import (
    FfmpegEngine,
    HlsOutputSpec,
    _parse_clock,
    ffmpeg_engine,
)
from app.media.hw_manager import HwSelection

PASS = 0


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}")
    else:
        print(f"  FAIL  {name}  {detail}")


def cpu_hw():
    return HwSelection(
        method="cpu", video_codec="h264", encoder="libx264",
        encode_args=["-preset", "veryfast", "-crf", "23"],
        pixel_format="yuv420p", video_filter="scale=1920:1080",
    )


def qsv_hw():
    return HwSelection(
        method="qsv", video_codec="h264", encoder="h264_qsv",
        decode_args=["-hwaccel", "qsv"],
        encode_args=["-preset", "veryfast", "-global_quality", "23"],
        pixel_format="nv12", video_filter=None,
    )


def vaapi_hw():
    return HwSelection(
        method="vaapi", video_codec="h264", encoder="h264_vaapi",
        decode_args=["-init_hw_device", "vaapi=hw:/dev/dri/renderD128", "-filter_hw_device", "hw"],
        encode_args=["-rc_mode", "CQP", "-qp", "23", "-compression_level", "7"],
        pixel_format="nv12", video_filter="scale=-2:1080,format=nv12,hwupload",
    )


def main():
    eng = FfmpegEngine()
    tmp = Path(tempfile.mkdtemp(prefix="ffengine_"))
    spec = HlsOutputSpec(output_dir=tmp / "out", playlist_name="playlist.m3u8")
    prog = tmp / "prog.txt"

    print("== 1. CPU 转码命令 ==")
    cmd = eng.build_hls_transcode_command(
        "/media/v.mp4", spec, cpu_hw(), height=1080, framerate=30.0,
        video_bitrate=4_500_000, audio_bitrate=192_000, progress_file=prog,
    )
    s = " ".join(cmd)
    check("ffmpeg first", cmd[0].endswith("ffmpeg"), cmd[0])
    check("has -y -hide_banner", "-hide_banner" in cmd and "-y" in cmd)
    check("no -ss at 0", "-ss" not in cmd)
    check("cpu no hwaccel", "-hwaccel" not in cmd and "-init_hw_device" not in cmd)
    check("-c:v libx264", "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "libx264")
    check("-crf 23", "-crf" in cmd and cmd[cmd.index("-crf") + 1] == "23")
    check("pix_fmt yuv420p", "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "yuv420p")
    check("vf scale", "-vf" in cmd and cmd[cmd.index("-vf") + 1] == "scale=1920:1080")
    check("-r 30", "-r" in cmd and cmd[cmd.index("-r") + 1] == "30")
    check("capped crf: -maxrate 4500000",
          "-maxrate" in cmd and cmd[cmd.index("-maxrate") + 1] == "4500000")
    check("capped crf: -bufsize 9000000",
          "-bufsize" in cmd and cmd[cmd.index("-bufsize") + 1] == "9000000")
    check("cpu 无 -b:v（避免与 -crf 双速率控制模式歧义）", "-b:v" not in cmd, s)
    check("audio aac 192000 ac2",
          "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "aac"
          and "-b:a" in cmd and cmd[cmd.index("-b:a") + 1] == "192000"
          and "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "2")
    check("hls mpegts", "-hls_segment_type" in cmd and cmd[cmd.index("-hls_segment_type") + 1] == "mpegts")
    check("hls_flags 含 independent_segments", "-hls_flags" in cmd and cmd[cmd.index("-hls_flags") + 1] == "independent_segments", s)
    check("hls_flags 不含 append_list", "-hls_flags" in cmd and "append_list" not in cmd[cmd.index("-hls_flags") + 1], s)
    check("无 init.mp4（MPEG-TS 无 init 段）", not any(a.endswith("init.mp4") for a in cmd), s)
    check("segment pattern .ts", any("segment_%05d.ts" in a for a in cmd))
    check("muxdelay fix", "-muxdelay" in cmd and cmd[cmd.index("-muxdelay") + 1] == "0"
          and "-muxpreload" in cmd and cmd[cmd.index("-muxpreload") + 1] == "0")
    check("progress file", "-progress" in cmd)
    check("playlist last", cmd[-1].endswith("playlist.m3u8"), cmd[-1])

    print("== 2. seek: -ss 放在 -i 前 ==")
    cmd = eng.build_hls_transcode_command("/media/v.mp4", spec, cpu_hw(), start_offset=2100.0)
    idx = cmd.index("-ss")
    check("ss present", idx >= 0)
    check("ss value 2100", cmd[idx + 1] == "2100.000")
    check("ss before -i", cmd[idx + 2] == "-i")

    print("== 2b. CPU 无码率 → 纯 CRF（无 maxrate/bufsize）==")
    cmd = eng.build_hls_transcode_command("/media/v.mp4", spec, cpu_hw())
    check("无 -maxrate", "-maxrate" not in cmd)
    check("无 -b:v", "-b:v" not in cmd)

    print("== 3. QSV 命令（无码率 → ICQ）==")
    cmd = eng.build_hls_transcode_command("/media/v.mp4", spec, qsv_hw())
    check("-hwaccel qsv before -i", cmd.index("-hwaccel") < cmd.index("-i"))
    check("-c:v h264_qsv", cmd[cmd.index("-c:v") + 1] == "h264_qsv")
    check("global_quality", "-global_quality" in cmd)
    check("硬件路径不加 -pix_fmt（nv12 由滤镜链落定）", "-pix_fmt" not in cmd)

    print("== 3b. QSV 有码率 → VBR（剔除 -global_quality）==")
    cmd = eng.build_hls_transcode_command(
        "/media/v.mp4", spec, qsv_hw(), video_bitrate=6_000_000
    )
    s = " ".join(cmd)
    check("global_quality 已剔除", "-global_quality" not in cmd, s)
    check("-b:v 6000000", "-b:v" in cmd and cmd[cmd.index("-b:v") + 1] == "6000000")
    check("-maxrate 8000000", "-maxrate" in cmd and cmd[cmd.index("-maxrate") + 1] == "8000000")
    check("-preset 保留", "-preset" in cmd and cmd[cmd.index("-preset") + 1] == "veryfast")

    print("== 3c. VAAPI 有码率 → VBR ==")
    cmd = eng.build_hls_transcode_command(
        "/media/v.mp4", spec, vaapi_hw(), video_bitrate=4_000_000
    )
    check("-b:v 4000000", "-b:v" in cmd and cmd[cmd.index("-b:v") + 1] == "4000000")
    check("-maxrate 5333333", cmd[cmd.index("-maxrate") + 1] == str(int(4_000_000 * 4 / 3)))
    check("-bufsize 8000000", cmd[cmd.index("-bufsize") + 1] == "8000000")

    print("== 4. VAAPI 命令 ==")
    cmd = eng.build_hls_transcode_command("/media/v.mp4", spec, vaapi_hw())
    check("init_hw_device before -i", cmd.index("-init_hw_device") < cmd.index("-i"))
    check("-c:v h264_vaapi", cmd[cmd.index("-c:v") + 1] == "h264_vaapi")
    check("rc_mode CQP", cmd[cmd.index("-rc_mode") + 1] == "CQP")
    check("-qp 23", cmd[cmd.index("-qp") + 1] == "23")
    check("vf hwupload", cmd[cmd.index("-vf") + 1] == "scale=-2:1080,format=nv12,hwupload")

    print("== 5. Remux 命令（Direct Stream）==")
    cmd = eng.build_hls_remux_command("/media/v.mkv", spec, audio_codec="aac")
    check("-c:v copy", cmd[cmd.index("-c:v") + 1] == "copy")
    check("-c:a aac", cmd[cmd.index("-c:a") + 1] == "aac")
    check("no crf", "-crf" not in cmd)
    check("no pix_fmt", "-pix_fmt" not in cmd)
    check("hls mpegts present", "-hls_segment_type" in cmd)
    cmd = eng.build_hls_remux_command("/media/v.mkv", spec)
    check("audio copy default", cmd[cmd.index("-c:a") + 1] == "copy")
    spec_fmp4 = HlsOutputSpec(
        output_dir=tmp / "out", playlist_name="playlist.m3u8", segment_type="fmp4"
    )
    cmd = eng.build_hls_remux_command("/media/v.mkv", spec_fmp4)
    check("fmp4 segment type", cmd[cmd.index("-hls_segment_type") + 1] == "fmp4")
    check("fmp4 init filename", "-hls_fmp4_init_filename" in cmd)
    check("fmp4 m4s pattern", any(str(x).endswith(".m4s") for x in cmd), str(cmd[-8:]))

    print("== 6. 进度解析 ==")
    pf = tmp / "prog2.txt"
    pf.write_text("key=value\nout_time_us=1000000\nout_time_ms=3000000\nprogress=continue\n")
    r = eng.read_progress(pf, total_duration=60)
    check("out_time_sec=3.0", abs(r.get("out_time_sec", 0) - 3.0) < 1e-6, str(r))
    check("percent=5", r.get("percent") == 5, str(r))

    print("== 7. _parse_clock ==")
    check("h:m:s", abs(_parse_clock("01:02:03.50") - 3723.5) < 1e-9)
    check("m:s", abs(_parse_clock("02:03.50") - 123.5) < 1e-9)
    check("s", abs(_parse_clock("12.5") - 12.5) < 1e-9)
    check("bad None", _parse_clock("N/A") is None)

    print("== 8. 进程生命周期（用 sleep 模拟，不依赖 ffmpeg）==")
    import subprocess as sp

    sleep_bin = shutil.which("sleep")
    if sleep_bin:
        sh_cmd = f"{sleep_bin} 30"
        proc_file = tmp / "prog3.txt"
        proc = eng.start(
            [sleep_bin, "30"],
            progress_file=proc_file,
            stderr_log=tmp / "err.log",
            total_duration=60,
        )
        time.sleep(0.5)
        check("pid set", proc.pid and proc.pid > 0, str(proc.pid))
        check("running", proc.running)
        check("in active", any(p.pid == proc.pid for p in eng.active_processes()))
        d = proc.to_dict()
        check("to_dict cmd", "sleep" in d["cmd"] and d["pid"] == proc.pid)
        code = proc.stop(timeout=3)
        check("stop returns code", code == 0 or code == -15 or code == 15, f"code={code}")
        proc.exit_event.wait(3)
        check("exit_event set", proc.exit_event.is_set())
        check("not running after stop", not proc.running)
        check("stopped_at set", proc.stopped_at is not None)
        dropped = False
        for _ in range(50):
            if all(p.pid != proc.pid for p in eng.active_processes()):
                dropped = True
                break
            time.sleep(0.05)
        check("exit 后自动从 _active 移除", dropped)
        eng.drop_process(proc.pid)
        check("dropped from active", all(p.pid != proc.pid for p in eng.active_processes()))
    else:
        check("sleep available", False)

    print("== 9. stderr 监听 ==")
    sh_bin = shutil.which("sh")
    if sh_bin:
        proc = eng.start(
            [sh_bin, "-c", "echo line-one-err 1>&2; echo line-two-err 1>&2; sleep 5"],
            progress_file=tmp / "prog4.txt",
        )
        time.sleep(1.0)
        tail = proc.stderr_tail(10)
        check("stderr captured", any("line-one-err" in ln for ln in tail), str(tail))
        proc.stop(timeout=3)
    else:
        check("sh available", False)

    print("== 10. _parse_stats_line ==")
    proc = None  # 直接用纯函数验证
    sample = (
        "frame=  120 fps= 30.0 q=28.0 size=    1024KiB "
        "time=00:00:04.00 bitrate= 2048.0kbits/s speed= 1.25x"
    )
    from app.media.ffmpeg_engine import FfmpegProcess, _parse_clock as pc

    class Fake:
        pid = 9999
        stderr = None

    fp = FfmpegProcess(cmd=[], proc=Fake(), progress_file=Path(tmp) / "x")
    fp._parse_stats_line(sample)
    st = fp.stats()
    check("stats fps", st.get("fps") == "30.0", str(st))
    check("stats speed", st.get("speed") == "1.25x", str(st))
    check("stats out_time", st.get("out_time") == "00:00:04.00", str(st))
    prog = fp.progress()
    check("progress out_time_sec=4", abs(prog.get("out_time_sec", 0) - 4.0) < 1e-6, str(prog))

    print(f"\n== 结果: {PASS} 项通过 ==")


if __name__ == "__main__":
    main()
