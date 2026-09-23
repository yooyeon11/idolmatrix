#!/usr/bin/env python3
"""抓取 jellyfin-ffmpeg 产物并解包到指定目录（纯 Python，不依赖 dpkg-deb / ar / xz）。

为什么需要这个脚本
------------------
`docker/jellyfin/` 下的 ffmpeg/ffprobe + lib/ 是 jellyfin-ffmpeg 官方产物。以前靠
「官网下载 → 网盘/zip 传到开发机」，而 zip 会把 .so 的**符号链接降级成只含目标文件名的
文本文件**（11~23 字节），进镜像后 dlopen 报 "file too short"，VAAPI/QSV 静默回退软转。
本脚本在源头就按真实文件解包，且不产出任何符号链接（链接一律由 Dockerfile 扫描生成），
从根上消除这一类故障。

用法
----
    python scripts/fetch_jellyfin_ffmpeg.py --list
    python scripts/fetch_jellyfin_ffmpeg.py --tag v8.1.2-5 --flavor deb
    python scripts/fetch_jellyfin_ffmpeg.py --tag v8.1.2-5 --flavor portable

    --flavor deb       jellyfin-ffmpeg8_<ver>-<rev>-bookworm_amd64.deb
    --flavor portable  jellyfin-ffmpeg_<ver>-<rev>_portable_linux64-gpl.tar.xz

    --list            列出版本

解包结果落在 --out（默认 ``./.ffmpeg-dist/<tag>/<flavor>/``）；加 ``--apply docker/jellyfin``
即可直接把 ffmpeg / ffprobe / vainfo / lib / share 落地，旧内容按 VERSION 标记备份到
``.ffmpeg-dist/_backup-<旧 tag>/``。

落地后的**依赖完整性**由 ``scripts/verify_jellyfin_bundle.py`` 静态校验（未构建镜像也能发现缺库）。
"""

from __future__ import annotations

import argparse
import io
import json
import lzma
import os
import re
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "jellyfin/jellyfin-ffmpeg"
API = f"https://api.github.com/repos/{REPO}/releases"
UA = {"User-Agent": "idolmatrix-fetch-jellyfin-ffmpeg", "Accept": "application/vnd.github+json"}

PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")


# ---------------------------------------------------------------- 网络

def _open(url: str, timeout: int = 30, use_proxy: bool = False):
    if use_proxy and PROXY:
        handler = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
        opener = urllib.request.build_opener(handler)
    else:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(urllib.request.Request(url, headers=UA), timeout=timeout)


def list_releases(limit: int = 15) -> list[dict]:
    with _open(f"{API}?per_page={limit}") as resp:
        return json.load(resp)


def pick_asset(release: dict, flavor: str) -> dict:
    """按 flavor 挑选资产：deb → bookworm amd64；portable → linux64-gpl。"""
    # ⚠ 注意命名分隔符：`jellyfin-ffmpeg8_8.1.2-5-bookworm_amd64.deb`
    #   版本段是 `<上游版本>-<修订号>`，其后接 `-<发行版>`（连字符），末尾 `_<架构>`。
    if flavor == "deb":
        pat = re.compile(r"^jellyfin-ffmpeg\d*_[\d.]+-\d+-bookworm_amd64\.deb$")
    else:
        pat = re.compile(r"^jellyfin-ffmpeg_[\d.]+-\d+_portable_linux64-gpl\.tar\.xz$")
    for a in release["assets"]:
        if pat.match(a["name"]):
            return a
    names = ", ".join(a["name"] for a in release["assets"])
    raise SystemExit(f"未找到 flavor={flavor} 的资产。可用：{names}")


def download(url: str, dest: Path, expect_size: int = 0) -> Path:
    """带续传与进度输出的下载；直连失败自动改用代理重试。

    `expect_size` 给了就启用**缓存复用**：命中即跳过下载（升级时省掉几十 MB 重下）。
    用「字节数完全相等」判断，不用哈希 —— 文件名里已带版本，release API 给的大小足够挡半截文件。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and expect_size and dest.stat().st_size == expect_size:
        print(f"  [缓存] {dest.name} 已在本地（{expect_size / 1048576:.1f} MB），跳过下载")
        return dest
    part = dest.with_suffix(dest.suffix + ".part")

    for use_proxy in (False, True):
        got = part.stat().st_size if part.exists() else 0
        headers = dict(UA)
        if got:
            headers["Range"] = f"bytes={got}-"
        tag = "代理" if use_proxy else "直连"
        try:
            with _open(url, timeout=60, use_proxy=use_proxy) as resp:
                total = int(resp.headers.get("Content-Length") or 0) + (got if resp.status == 206 else 0)
                if resp.status != 206:
                    got = 0
                mode = "ab" if got else "wb"
                t0, last = time.time(), time.time()
                with part.open(mode) as fh:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
                        got += len(chunk)
                        if time.time() - last > 1.0:
                            last = time.time()
                            speed = got / max(time.time() - t0, 0.01) / 1048576
                            pct = f" {got * 100 // total}%" if total else ""
                            print(f"\r  [{tag}] {got/1048576:7.1f} MB{pct}  {speed:5.1f} MB/s", end="", flush=True)
            print()
            if total and part.stat().st_size < total:
                raise urllib.error.ContentTooShortError("下载不完整", None)
            part.replace(dest)
            return dest
        except Exception as e:  # noqa: BLE001 — 直连/代理都要试一遍，失败原因需回报
            print(f"\n  [{tag}] 失败：{type(e).__name__}: {e}")
            if use_proxy:
                raise
    return dest


# ---------------------------------------------------------------- 解包

def read_ar_members(blob: bytes) -> dict[str, bytes]:
    """解析 Unix ar 归档（.deb 的外层容器）。"""
    if not blob.startswith(b"!<arch>\n"):
        raise ValueError("不是 ar 归档（.deb 头缺失）")
    pos, members = 8, {}
    while pos + 60 <= len(blob):
        header = blob[pos : pos + 60]
        name = header[0:16].decode("ascii", "replace").strip().rstrip("/")
        size = int(header[48:58].decode("ascii", "replace").strip() or 0)
        pos += 60
        members[name] = blob[pos : pos + size]
        pos += size + (size & 1)  # 2 字节对齐
    return members


def decompress_auto(blob: bytes) -> bytes:
    """按魔数识别 xz / gzip / zstd 并解压。"""
    if blob[:6] == b"\xfd7zXZ\x00":
        return lzma.decompress(blob)
    if blob[:2] == b"\x1f\x8b":
        import gzip

        return gzip.decompress(blob)
    if blob[:4] == b"\x28\xb5\x2f\xfd":
        try:
            from compression import zstd  # Python 3.14+

            return zstd.decompress(blob)
        except ImportError:
            try:
                import zstandard  # type: ignore

                return zstandard.ZstdDecompressor().decompress(blob)
            except ImportError:
                raise SystemExit("该 .deb 用 zstd 压缩，请用 Python 3.14+ 或 pip install zstandard")
    raise ValueError(f"未知压缩格式，魔数={blob[:8]!r}")


def safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """解包 tar，跳过越权路径与符号链接/设备节点（链接由 Dockerfile 统一生成）。"""
    dest = dest.resolve()
    for m in tar.getmembers():
        target = (dest / m.name).resolve()
        if not str(target).startswith(str(dest)):
            continue  # 路径穿越，丢弃
        if m.issym() or m.islnk() or m.ischr() or m.isblk() or m.isfifo():
            continue  # 不落符号链接：避免 Windows 上退化/失败，交给 Dockerfile 生成
        tar.extract(m, dest, set_attrs=True, numeric_owner=False)


def extract_deb(deb: Path, out: Path) -> list[str]:
    members = read_ar_members(deb.read_bytes())
    data_name = next((n for n in members if n.startswith("data.tar")), None)
    if not data_name:
        raise SystemExit(f"{deb.name} 内未找到 data.tar.*（成员：{list(members)}）")
    raw = decompress_auto(members[data_name])
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        safe_extract(tar, out)
    return [data_name]


def extract_tar_xz(archive: Path, out: Path) -> list[str]:
    with tarfile.open(archive) as tar:
        safe_extract(tar, out)
    return [archive.name]


# ---------------------------------------------------------------- ELF 校验
# ELF 解析（DT_NEEDED / SONAME / RPATH）统一实现在 scripts/elfscan.py，供本脚本与
# verify_jellyfin_bundle.py 共用；此处不再重复实现。


def report(out: Path) -> None:
    print("\n=== 解包结果 ===")
    top = sorted(p for p in out.iterdir())
    for p in top:
        if p.is_dir():
            total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            print(f"  {p.name}/  ({total/1048576:.1f} MB, {sum(1 for _ in p.rglob('*') if _.is_file())} files)")
        else:
            print(f"  {p.name}  ({p.stat().st_size/1048576:.1f} MB)")

    ffmpeg = next(out.rglob("ffmpeg"), None)
    if ffmpeg and ffmpeg.is_file():
        need = elf_needed(ffmpeg)
        print(f"\n=== {ffmpeg.relative_to(out)} DT_NEEDED（{len(need)} 项）===")
        for n in sorted(need):
            print("   ", n)

    libdir = next((p for p in out.rglob("lib") if p.is_dir()), None)
    if libdir:
        print(f"\n=== {libdir.relative_to(out)} 真实文件 ===")
        for f in sorted(libdir.rglob("*")):
            if f.is_file():
                print(f"    {f.relative_to(libdir)}  {f.stat().st_size:>12,} B")


def find_payload(out: Path) -> Path:
    """定位解包树里真正的 jellyfin-ffmpeg 根（含 ffmpeg/ffprobe/lib 的那层）。"""
    for cand in out.rglob("jellyfin-ffmpeg"):
        if (cand / "ffmpeg").is_file():
            return cand
    for cand in out.rglob("ffmpeg"):
        if (cand.parent / "lib").is_dir():
            return cand.parent
    raise SystemExit(f"在 {out} 下找不到 ffmpeg + lib 的布局")


def apply_to(payload: Path, dest: Path, tag: str) -> None:
    """把解包树落地到 docker/jellyfin/，旧内容按「旧版本号」备份。

    只复制 ffmpeg / ffprobe / vainfo / lib / share，并写一个 VERSION 标记文件；
    **不复制符号链接**（解包阶段已丢弃，链接统一由 Dockerfile 扫描生成）。
    """
    if dest.exists():
        marker = dest / "VERSION"
        old_tag = marker.read_text(encoding="utf-8").strip() if marker.is_file() else "unknown"
        old = dest.parent.parent / ".ffmpeg-dist" / f"_backup-{old_tag}"
        if old.exists():
            shutil.rmtree(old)
        shutil.copytree(dest, old)
        print(f"旧内容已备份 → {old}")
        for item in dest.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    dest.mkdir(parents=True, exist_ok=True)

    copied = []
    for name in ("ffmpeg", "ffprobe", "vainfo"):
        src = payload / name
        if src.is_file():
            shutil.copy2(src, dest / name)
            copied.append(name)
    for name in ("lib", "share"):
        src = payload / name
        if src.is_dir():
            shutil.copytree(src, dest / name)
            copied.append(name + "/")
    (dest / "VERSION").write_text(tag + "\n", encoding="utf-8")

    print(f"\n落地 → {dest}   (VERSION={tag})")
    print(f"  已复制：{', '.join(copied)}")
    total = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    links = sum(1 for f in dest.rglob("*") if f.is_symlink())
    print(f"  目录体积 {total/1048576:.1f} MB，符号链接 {links} 个（应为 0，由 Dockerfile 生成）")
    for name in ("ffmpeg", "ffprobe", "vainfo"):
        p = dest / name
        if p.is_file():
            print(f"  {name}: {p.stat().st_size:,} B")


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="抓取并解包 jellyfin-ffmpeg 产物")
    ap.add_argument("--tag", help="release tag，如 v8.1.2-5；缺省取最新正式版")
    ap.add_argument("--flavor", choices=["deb", "portable"], default="deb")
    ap.add_argument("--out", type=Path, help="解包目标目录")
    ap.add_argument("--apply", type=Path, metavar="DEST",
                    help="把解包结果落地到该目录（如 docker/jellyfin），旧内容自动备份")
    ap.add_argument("--list", action="store_true", help="只列出版本")
    args = ap.parse_args()

    if args.list:
        for r in list_releases():
            print(f"{r['tag_name']:<18} {r['published_at'][:10]}  prerelease={r['prerelease']}")
        return 0

    if args.tag:
        with _open(f"{API}/tags/{args.tag}") as resp:
            release = json.load(resp)
    else:
        release = next((r for r in list_releases() if not r["prerelease"]), None)
        if not release:
            raise SystemExit("未取到正式版 release")

    asset = pick_asset(release, args.flavor)
    print(f"release : {release['tag_name']}  ({release['published_at'][:10]})")
    print(f"asset   : {asset['name']}  {asset['size']/1048576:.1f} MB")

    out = args.out or Path(".ffmpeg-dist") / release["tag_name"] / args.flavor
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    cache = Path(".ffmpeg-dist") / "_downloads"
    archive = download(asset["browser_download_url"], cache / asset["name"], asset["size"])

    if archive.suffix == ".deb":
        members = extract_deb(archive, out)
    else:
        members = extract_tar_xz(archive, out)
    print(f"解包   : {', '.join(members)} → {out}")
    report(out)
    if args.apply:
        apply_to(find_payload(out), args.apply, release["tag_name"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
