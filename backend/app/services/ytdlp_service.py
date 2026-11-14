"""yt-dlp 集成层：二进制探测 / 自更新 / 命令构造 / 元数据解析。

## 为什么把二进制放在可写卷
业主口径：**不重建镜像也能把 yt-dlp 升到最新版**。做法是把官方静态二进制
（`yt-dlp_linux`，单文件、自带 Python）落到 `settings.ytdlp_install_dir`
（默认 `/data/tools`，随卷持久化），容器重建后依然是最新版。

探测顺序（第一个能跑的胜出）：
1. `settings.ytdlp_path`（显式指定，最高优先级）
2. `<ytdlp_install_dir>/yt-dlp`（自更新落点，**可写**）
3. `PATH` 里的 `yt-dlp`（镜像内置基线，只读）
4. `python -m yt_dlp`（pip 安装兜底）

> 备注：没有采用逆向 Stacher（github.com/morluto/rea）的路线 —— 那条路要复刻它的
> 私有协议与打包格式，维护成本高且随时会被上游改动打断。yt-dlp 官方本身就提供
> 静态二进制与稳定 CLI，直接拉官方 release 更可靠，效果等价：
> 容器内一条命令即可升级到当天的最新版。
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import time
import sys
import json
import logging
from pathlib import Path
from typing import Optional

from app.core.config import settings

# 官方 release 的静态二进制（x86_64/arm64 自动区分）
# 只保留 nightly（业主 2026-10-31 口径）：YouTube 风控跟进最快，稳定版不再提供。
_CHANNEL_REPO = {
    "nightly": "yt-dlp/yt-dlp-nightly-builds",
}
CHANNELS = tuple(_CHANNEL_REPO)
_NIGHTLY = "nightly"

# 下载直链的镜像顺序：官方 → gh-proxy（ghfast.top 已于 2026-10 实测 403/超时，移除）
# 为什么要镜像：Clash 环境下 release-assets.githubusercontent.com 会间歇性
# `SSL: UNEXPECTED_EOF_WHILE_READING`（本人 AGENTS.md 记录过的同一故障模式），
# 重试同一域名无效，换镜像立竿见影。镜像只是转发，字节与官方一致。
_RELEASE_MIRRORS = (
    "{url}",
    "https://gh-proxy.com/{url}",
)

logger = logging.getLogger(__name__)

_TIMEOUT_VERSION = 20
_TIMEOUT_PROBE = 90


class YtdlpError(RuntimeError):
    """yt-dlp 相关的可读错误（直接展示给前端）。"""


def get_channel(db=None) -> str:
    """当前通道：**恒为 nightly**。

    以前支持 stable/nightly 二选一并持久化到 app_settings；2026-10-31 起只保留 nightly，
    历史库里残留的 `ytdlp_channel=stable` 直接忽略（不删数据，回滚方便）。
    """
    return _NIGHTLY


def set_channel(channel: str, db=None) -> str:
    """只允许 nightly；传 stable/其他值给人话报错（保留函数是为了兼容旧调用点）。"""
    channel = (channel or "").strip().lower()
    if channel and channel != _NIGHTLY:
        raise YtdlpError("yt-dlp 现在只提供 Nightly 版本，稳定版已下线")
    return _NIGHTLY


def mask_proxy(proxy: str) -> str:
    """隐藏代理地址里的账号密码，用于界面展示。"""
    if not proxy:
        return ""
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(proxy)
        if parts.username or parts.password:
            host = parts.hostname or ""
            if parts.port:
                host = f"{host}:{parts.port}"
            return urlunsplit((parts.scheme, f"***@{host}", parts.path, "", ""))
    except Exception:  # noqa: BLE001
        pass
    return proxy


def get_proxy() -> str:
    """生效的下载代理 —— **复用设置里的「站点获取代理」**。

    全站出网（TheAudioDB / 封面下载 / yt-dlp）共用同一份配置，
    不另设开关：用户在设置页开一次，下载器与更新器都跟着走。

    历史坑：设置里常填 http://host.docker.internal:7897 —— 这个名字**只有容器内**
    能解析（Docker Desktop 内部 DNS / compose 的 extra_hosts）。在宿主机直接跑
    后端时它必然 getaddrinfo 失败，表现为「更新没走代理」。这里做一次方向性纠正：
    非容器环境把 host.docker.internal 换成 127.0.0.1，容器内保持原样。
    """
    from app.services import proxy_config

    return proxy_config.enabled_url()


def proxy_source() -> str:
    return "settings" if get_proxy() else "none"


def _release_url(channel: str) -> str:
    repo = _CHANNEL_REPO["nightly"]
    return f"https://github.com/{repo}/releases/latest/download/{_machine_asset()}"


def _machine_asset() -> str:
    """按「平台 + CPU 架构」挑官方 release 资产名。

    容器里永远是 yt-dlp_linux*（生产路径）；Windows/macOS 只是本地开发兜底 ——
    早先只判断架构，在 Windows 上会下到 ELF 文件，运行时报
    [WinError 193] 不是有效的 Win32 应用程序。
    """
    if os.name == "nt":
        return "yt-dlp.exe"
    if sys.platform == "darwin":
        return "yt-dlp_macos"
    machine = (os.uname().machine if hasattr(os, "uname") else "") or ""
    if machine in ("aarch64", "arm64"):
        return "yt-dlp_linux_aarch64"
    return "yt-dlp_linux"


def install_dir() -> Path:
    """自更新落点：优先配置值，不可写时退回 <incoming 父目录>/tools。"""
    configured = (settings.ytdlp_install_dir or "").strip()
    candidates: list[Path] = []
    if configured:
        cand = Path(configured)
        # Windows 本地开发："/data/tools" 没有盘符，会被解析成「当前盘的 \data\tools」，
        # 既不可预期也会往仓库所在盘乱建目录 —— 只在**带盘符的绝对路径**时才采用。
        if os.name != "nt" or bool(cand.drive):
            candidates.append(cand)
    # 本地开发没有 /data：退到数据目录旁边，保证可写
    candidates.append(settings.incoming_dir.parent / "tools")
    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except OSError:
            continue
    raise YtdlpError("yt-dlp 安装目录不可写，请检查 /data 卷挂载或 YTDLP_INSTALL_DIR 配置")


def binary_path() -> Optional[Path]:
    """返回可用的 yt-dlp 可执行文件；找不到返回 None。"""
    explicit = (settings.ytdlp_path or "").strip()
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
    try:
        installed = install_dir() / "yt-dlp"
        if installed.is_file():
            return installed
    except YtdlpError:
        pass
    which = shutil.which("yt-dlp")
    if which:
        return Path(which)
    return None


def _pylib_dir() -> Path:
    """pip --target 安装的独立版本目录（自更新兜底路径）。"""
    return install_dir() / "pylib"


def command() -> tuple[list[str], dict[str, str]]:
    """构造「怎么调用 yt-dlp」：返回 (argv, env)。

    优先级：独立二进制 → pip --target 装的版本（自更新兜底）→ 系统模块。
    """
    env = dict(os.environ)
    binary = binary_path()
    if binary:
        return [str(binary)], env

    lib = _pylib_dir()
    if (lib / "yt_dlp").is_dir():
        env["PYTHONPATH"] = f"{lib}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
        return [sys.executable, "-m", "yt_dlp"], env

    try:
        import yt_dlp  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise YtdlpError(
            "未找到 yt-dlp。请到「设置 → YouTube 下载」点一次「更新 yt-dlp」，"
            "或用 docker exec 安装后重试。"
        ) from e
    return [sys.executable, "-m", "yt_dlp"], env


def common_args() -> list[str]:
    """所有调用共用参数：代理 / cookies / 静默色彩。"""
    args: list[str] = ["--no-color", "--ignore-config"]
    proxy = get_proxy()
    if proxy:
        # yt-dlp 自己能吃 http(s)/socks5 代理；这里与更新器共用同一份配置
        args += ["--proxy", proxy]
    cookies = (settings.ytdlp_cookies_file or "").strip()
    if cookies and Path(cookies).is_file():
        args += ["--cookies", cookies]
    # 用镜像内自带的 ffmpeg（jellyfin 构建），保证 bestvideo+bestaudio 能合并
    ffmpeg = (settings.ffmpeg_path or "").strip()
    if ffmpeg:
        args += ["--ffmpeg-location", ffmpeg]
    return args


def run(args: list[str], timeout: int = _TIMEOUT_PROBE) -> subprocess.CompletedProcess:
    argv, env = command()
    return subprocess.run(
        argv + common_args() + args,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def version() -> dict:
    """返回 {available, version, path, install_dir, updatable}。"""
    try:
        binary = binary_path()
        out = run(["--version"], timeout=_TIMEOUT_VERSION)
        text = (out.stdout or "").strip().splitlines()
        return {
            "available": out.returncode == 0,
            "version": text[0] if text else "",
            "path": str(binary) if binary else "python -m yt_dlp",
            "install_dir": str(install_dir()),
            "updatable": True,
            "channel": get_channel(),
            "proxy": mask_proxy(get_proxy()),
            "proxy_source": proxy_source(),
        }
    except (YtdlpError, subprocess.TimeoutExpired, OSError) as e:
        return {
            "available": False,
            "version": "",
            "path": "",
            "install_dir": (settings.ytdlp_install_dir or ""),
            "updatable": False,
            "channel": get_channel(),
            "proxy": mask_proxy(get_proxy()),
            "proxy_source": proxy_source(),
            "error": str(e),
        }


def _http_get(url: str, timeout: int = 60) -> bytes:
    """带代理的 GET（小响应，如自检探针）—— 复用全站代理配置。"""
    import urllib.request

    from app.services import proxy_config

    req = urllib.request.Request(url, headers={"User-Agent": "idolmatrix-ytdlp-updater"})
    with proxy_config.urlopen(req, timeout=timeout) as r:
        return r.read()


def _http_download(url: str, dest: Path, timeout: int = 60, deadline: int = 300) -> int:
    """流式下载到文件：边下边写，避免把几十 MB 全塞内存、也便于观察进度。

    - 	imeout：单次 socket 操作的超时（无数据才触发）
    - deadline：整个下载的总时限，防止「一直缓慢滴数据」把任务拖到天荒地老
    """
    import urllib.request

    from app.services import proxy_config

    req = urllib.request.Request(url, headers={"User-Agent": "idolmatrix-ytdlp-updater"})
    started = time.time()
    total = 0
    checked_speed = False
    with proxy_config.urlopen(req, timeout=timeout) as r, dest.open("wb") as fh:
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            fh.write(chunk)
            total += len(chunk)
            elapsed = time.time() - started
            # 20 秒后测一次平均速度：太慢就早早换镜像，别让用户干等十分钟
            if not checked_speed and elapsed >= 20:
                checked_speed = True
                kbps = total / 1024 / elapsed
                if kbps < 120:
                    raise YtdlpError(f"该源太慢（{kbps:.0f}KB/s，已下载 {total // 1024}KB），换下一个")
            if elapsed > deadline:
                raise YtdlpError(f"下载超时（{deadline}s 内只拿到 {total // 1024}KB）")
    return total


def _download_standalone(target: Path, channel: str = _NIGHTLY) -> None:
    """拉对应通道的官方静态二进制：官方直链 → gh-proxy → ghfast。

    每次尝试都走同一个代理配置；任一镜像成功即停。
    """
    url = _release_url(channel)
    errors: list[str] = []
    tmp = target.with_suffix(".new")
    used = ""
    for pattern in _RELEASE_MIRRORS:
        candidate = pattern.format(url=url)
        host = candidate.split("/")[2] if "//" in candidate else candidate
        started = time.time()
        try:
            size = _http_download(candidate, tmp, timeout=45, deadline=240)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{host}: {type(e).__name__}: {str(e)[:80]}")
            logger.warning("yt-dlp 下载失败（%s），换下一个镜像：%s", host, e)
            tmp.unlink(missing_ok=True)
            continue
        if size < 1_000_000:
            errors.append(f"{host}: 返回内容过小（{size}B，疑似错误页）")
            tmp.unlink(missing_ok=True)
            continue
        tmp.chmod(0o755)
        os.replace(tmp, target)
        used = host
        logger.info(
            "yt-dlp 二进制已从 %s 下载：%.1fMB / %.1fs", host, size / 1024 / 1024, time.time() - started
        )
        break

    if not used:
        raise YtdlpError("所有下载源都失败 —— " + "；".join(errors))


def _install_via_pip(target_dir: Path, channel: str = _NIGHTLY) -> None:
    """兜底：把 yt-dlp 整包装到 <dir>/pylib（随卷持久化，容器重建仍在）。

    这条路径的意义：部分网络下 GitHub 的下载直链会 SSL EOF（实测 Clash 常见），
    但 PyPI 通常可用，而且 pip 支持 --proxy。
    """
    lib = target_dir / "pylib"
    lib.mkdir(parents=True, exist_ok=True)
    argv = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "--upgrade",
        "--target",
        str(lib),
    ]
    # 只有 nightly（PyPI 上的预发布）
    argv.append("--pre")
    argv.append("yt-dlp")
    # 收紧网络参数：默认 pip 会指数退避重试到天荒地老
    argv += ["--timeout", "20", "--retries", "1"]
    proxy = get_proxy()
    if proxy:
        argv += ["--proxy", proxy]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        raise YtdlpError("pip 安装 yt-dlp 失败：" + " / ".join(tail))


def _pc_in_container() -> bool:
    from app.services import proxy_config

    return proxy_config._in_container()


def _assert_proxy_reachable(proxy: str) -> None:
    """代理主机解析不了就提前报人话，别让用户对着 SSL/超时猜。"""
    import socket
    from urllib.parse import urlsplit

    try:
        host = urlsplit(proxy).hostname
        port = urlsplit(proxy).port or (443 if proxy.startswith("https") else 80)
    except Exception:  # noqa: BLE001
        return
    if not host:
        return
    try:
        socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as e:
        hint = ""
        if "host.docker.internal" in proxy and not _pc_in_container():
            hint = (
                "（host.docker.internal 只有容器内能解析；宿主机上跑请改成 127.0.0.1，"
                "或在设置 → 站点获取代理里填宿主局域网 IP）"
            )
        raise YtdlpError(f"代理地址无法解析：{host}:{port} — {e}{hint}") from e


def update(channel: Optional[str] = None) -> dict:
    """把 yt-dlp 升到最新 **Nightly**，返回更新后的状态。

    只保留 nightly（业主 2026-10-31 口径）。参数 channel 仅为兼容旧客户端保留：
    传 stable 也不报错，直接忽略（老前端还在传）。

    两条路径都落到**可写卷**（默认 /data/tools），容器重建后依然有效：
      1. 官方静态二进制 → <dir>/yt-dlp（最快，无 Python 依赖）
      2. pip --target   → <dir>/pylib（GitHub 直链被墙时的兜底）
    """
    if channel and channel.strip().lower() != _NIGHTLY:
        logger.info("yt-dlp 只维护 nightly，忽略请求里的通道：%s", channel)
    channel = _NIGHTLY

    proxy = get_proxy()
    if proxy:
        _assert_proxy_reachable(proxy)

    target_dir = install_dir()
    errors: list[str] = []
    try:
        _download_standalone(target_dir / "yt-dlp", channel)
    except Exception as e:  # noqa: BLE001
        errors.append(f"静态二进制：{e}")
        logger.warning("yt-dlp 静态二进制下载失败，回退 pip：%s", e)
        try:
            _install_via_pip(target_dir, channel)
        except Exception as e2:  # noqa: BLE001
            errors.append(f"pip：{e2}")
            raise YtdlpError("升级失败 —— " + "；".join(errors)) from e2

    info = version()
    if not info.get("available"):
        raise YtdlpError("升级后仍无法运行 yt-dlp：" + (info.get("error") or "未知原因"))
    info["channel"] = channel
    info["updated_channel"] = channel
    return info


_PROGRESS_RE = re.compile(
    r"\[download\]\s+(?P<pct>\d+(?:\.\d+)?)%\s+of\s+~?\s*(?P<size>[\d.]+)(?P<unit>[KMGT]?i?B)"
    r"(?:\s+at\s+(?P<speed>[\d.]+)(?P<sunit>[KMGT]?i?B)/s)?"
    r"(?:\s+ETA\s+(?P<eta>[\d:]+))?"
)

_UNIT = {
    "B": 1,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
}


def parse_progress(line: str) -> Optional[dict]:
    """解析 yt-dlp 的下载进度行。"""
    m = _PROGRESS_RE.search(line or "")
    if not m:
        return None
    unit = m.group("unit")
    size = float(m.group("size")) * _UNIT.get(unit, 1)
    speed = None
    if m.group("speed"):
        speed = float(m.group("speed")) * _UNIT.get(m.group("sunit") or "B", 1)
    return {
        "percent": float(m.group("pct")),
        "total_bytes": int(size),
        "speed_bytes": int(speed) if speed else None,
        "eta": m.group("eta") or "",
    }


def human_speed(bps: Optional[int]) -> str:
    if not bps:
        return ""
    for unit, div in (("GiB/s", 1024**3), ("MiB/s", 1024**2), ("KiB/s", 1024)):
        if bps >= div:
            return f"{bps / div:.1f}{unit}"
    return f"{bps}B/s"


def probe(url: str, *, playlist: bool = False) -> dict:
    """不下载，只取元数据。

    - 单视频：`--dump-single-json`
    - 播放列表：`--flat-playlist --dump-single-json`（只列条目，不快照每条详情）
    """
    args = ["--dump-single-json", "--no-warnings"]
    if playlist:
        args.append("--flat-playlist")
    else:
        args.append("--no-playlist")
    args.append(url)
    out = run(args, timeout=_TIMEOUT_PROBE)
    if out.returncode != 0:
        msg = (out.stderr or out.stdout or "").strip().splitlines()
        raise YtdlpError(msg[-1] if msg else "yt-dlp 解析失败")
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as e:
        raise YtdlpError(f"解析 yt-dlp 输出失败：{e}") from e


def test_network(channel: Optional[str] = None) -> dict:
    """自检：按当前代理逐个试下载源，回答「到底走没走代理」。

    只读 256KB 就断开，够验证 TLS + 重定向 + 限速，不浪费流量。
    """
    channel = _NIGHTLY
    url = _release_url(channel)
    proxy = get_proxy()
    results = []
    for pattern in _RELEASE_MIRRORS:
        candidate = pattern.format(url=url)
        host = candidate.split("/")[2] if "//" in candidate else candidate
        started = time.time()
        try:
            import urllib.request as _ur

            from app.services import proxy_config

            req = _ur.Request(candidate, headers={"User-Agent": "idolmatrix-ytdlp-updater"})
            with proxy_config.urlopen(req, timeout=25) as r:
                chunk = r.read(262144)
                results.append(
                    {
                        "source": host,
                        "ok": True,
                        "ms": int((time.time() - started) * 1000),
                        "bytes": len(chunk),
                        "final_host": (r.geturl().split("/")[2] if "//" in r.geturl() else ""),
                        "error": "",
                    }
                )
        except Exception as e:  # noqa: BLE001
            results.append(
                {
                    "source": host,
                    "ok": False,
                    "ms": int((time.time() - started) * 1000),
                    "bytes": 0,
                    "final_host": "",
                    "error": f"{type(e).__name__}: {str(e)[:120]}",
                }
            )
    return {
        "channel": channel,
        "proxy": mask_proxy(proxy),
        "proxy_source": proxy_source(),
        "results": results,
    }
