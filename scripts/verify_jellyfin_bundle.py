#!/usr/bin/env python3
"""构建前静态校验：docker/jellyfin 里的 ffmpeg 到底能不能在这个镜像里跑起来？

为什么需要
----------
8.1 起 jellyfin-ffmpeg 从「107MB 自包含静态单体」改成了
「413KB 启动器 + lib/ 下的 libavcodec.so.62 等共享库」，
libav* 是 DT_NEEDED **硬依赖** —— 少一个 ffmpeg 直接起不来。
而本机（Windows）没有 Docker / Linux，跑不了 `ldd`。

本脚本用纯 Python 读 ELF，把**整个依赖闭包**算出来，逐项判定：
  · 能在 docker/jellyfin/lib（含 Dockerfile 生成的 soname 别名）里找到 → 自带，OK
  · 找不到，但属于基础镜像 / apt 已装的系统库             → OK
  · 找不到且不在允许清单里                                → **报错**（镜像里会起不来）

用法
----
    python scripts/verify_jellyfin_bundle.py            # 默认校验 docker/jellyfin
    python scripts/verify_jellyfin_bundle.py <目录>

退出码：0 = 通过；1 = 有无法解析的依赖（必须先修再建镜像）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from elfscan import is_elf, read_elf, soname_links  # noqa: E402

# 允许由基础镜像 / apt 提供的系统库（不随 docker/jellyfin 带出）。
#
# ⚠ 这份清单 = 官方 deb 里 `Depends:` 字段的 soname 映射，不是随手凑的。
#   官方包靠 apt 解析依赖，而我们是手工解包 COPY，**绕过了依赖解析** ——
#   所以 Dockerfile 的 apt 清单必须显式装齐这些包，否则 libavcodec.so.62
#   会因为没有 libx264.so.164 之类而**整个加载失败**（DT_NEEDED 是硬依赖，
#   ffmpeg 直接起不来，不是回退软转）。用 `dpkg-deb -f <deb> Depends` 可复核。
#
#   soname               ← Debian 包名
#   libc.so.6 等         ← libc6（基础镜像自带）
#   libgcc_s.so.1        ← libgcc-s1（基础镜像自带）
#   libstdc++.so.6       ← libstdc++6
#   libelf.so.1          ← libelf1
#   libexpat.so.1        ← libexpat1
#   libzstd.so.1         ← libzstd1
#   libgmp.so.10         ← libgmp10
#   libgnutls.so.30      ← libgnutls30
#   libpciaccess.so.0    ← libpciaccess0   （libdrm_intel 需要）
#   libudev.so.1         ← libudev1
#   libbluray.so.2       ← libbluray2
#   libmp3lame.so.0      ← libmp3lame0
#   libopenmpt.so.0      ← libopenmpt0
#   libopus.so.0         ← libopus0
#   libvorbis.so.0       ← libvorbis0a
#   libvorbisenc.so.2    ← libvorbisenc2
#   libvpx.so.7          ← libvpx7
#   libwebp.so.7         ← libwebp7
#   libwebpmux.so.3      ← libwebpmux3
#   libx264.so.164       ← libx264-164
#   libx265.so.199       ← libx265-199
#   libzvbi.so.0         ← libzvbi0
#   libOpenCL.so.1       ← ocl-icd-libopencl1
#
# 注：这些系统库自身的**间接**依赖（如 libgnutls30 → libnettle8 …）由 apt 自动解析，
#     本脚本不追（机器上没有那些文件），因此只把 docker/jellyfin 内的 ELF 视为根。
SYSTEM_LIBS = {
    # glibc / 动态加载器
    "libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0", "libmvec.so.1",
    "ld-linux-x86-64.so.2", "librt.so.1", "libresolv.so.2", "libutil.so.1",
    "libnsl.so.1", "libcrypt.so.1",
    # 编译器运行时
    "libgcc_s.so.1", "libstdc++.so.6", "libgomp.so.1",
    # 官方 Depends 里的其余部分
    "libelf.so.1", "libexpat.so.1", "libzstd.so.1", "libgmp.so.10",
    "libgnutls.so.30", "libpciaccess.so.0", "libudev.so.1", "libbluray.so.2",
    "libmp3lame.so.0", "libopenmpt.so.0", "libopus.so.0", "libvorbis.so.0",
    "libvorbisenc.so.2", "libvpx.so.7", "libwebp.so.7", "libwebpmux.so.3",
    "libx264.so.164", "libx265.so.199", "libzvbi.so.0", "libOpenCL.so.1",
}

# 闭包起点：会被执行的入口
ENTRY_NAMES = ("ffmpeg", "ffprobe", "vainfo")
# 这些子目录里的 .so 不会被 DT_NEEDED 引用，而是被**按绝对路径 dlopen**：
#   · lib/dri/*.so      —— libva 按 LIBVA_DRIVERS_PATH 找的 VA-API 驱动
#   · lib/libmfx-gen/*  —— oneVPL 的 Intel GPU runtime 插件
# 必须单独当根来查，否则它们的依赖（如 libdrm_intel → libpciaccess）会漏掉。
DLOPEN_DIRS = ("lib/dri", "lib/libmfx-gen")


def build_catalog(root: Path) -> tuple[dict, dict]:
    """返回 (可解析名字->路径, 真实文件列表)。

    可解析名字 = 「真实文件名」+「Dockerfile 会生成的 soname 别名」+「库自身的 SONAME」。
    三者都算，因为 ld.so 既可能按 soname 找，也可能按文件名找；
    而 dri 驱动是被 libva 以绝对路径 dlopen 的，靠自身 RPATH 解析依赖。
    """
    libdir = root / "lib"
    real_files = [p for p in libdir.rglob("*") if p.is_file() and is_elf(p)]
    catalog: dict = {}

    for p in real_files:
        catalog.setdefault(p.name, p)
        info = read_elf(p) or {}
        if info.get("soname"):
            catalog.setdefault(info["soname"], p)

    # 顶层 lib/*.so.* 的 soname 别名（Dockerfile 的扫描规则只作用于 lib 根层）
    top = [p for p in libdir.iterdir() if p.is_file() and is_elf(p)]
    for alias, target in soname_links([p.name for p in top]).items():
        src = libdir / target
        if src.is_file():
            catalog.setdefault(alias, src)
    return catalog, {p.name: p for p in real_files}


def verify(root: Path) -> int:
    """校验一个 docker/jellyfin 目录；返回 0 = 通过，1 = 有无法解析的依赖。"""
    if not (root / "lib").is_dir():
        print(f"✗ 找不到 {root}/lib")
        return 1

    print(f"== 校验 {root} ==")
    version_file = root / "VERSION"
    if version_file.is_file():
        print(f"jellyfin-ffmpeg 版本：{version_file.read_text(encoding='utf-8').strip()}")

    catalog, real = build_catalog(root)
    entries = [root / n for n in ENTRY_NAMES if (root / n).is_file()]
    for sub in DLOPEN_DIRS:
        d = root / sub
        if d.is_dir():
            entries.extend(sorted(p for p in d.iterdir() if p.is_file() and is_elf(p)))
    print(f"根节点 {len(entries)} 个：{', '.join(p.name for p in entries[:6])}"
          + (f" …（其余 {len(entries) - 6} 个）" if len(entries) > 6 else ""))
    print(f"可解析库名 {len(catalog)} 个（真实文件 {len(real)} 个）\n")

    missing_system: dict = {}
    resolved_bundle: set = set()
    broken: list = []
    seen: set = set()
    queue = list(entries)

    while queue:
        cur = queue.pop()
        info = read_elf(cur)
        if not info:
            continue
        for name in info["needed"]:
            if name in seen:
                continue
            seen.add(name)
            hit = catalog.get(name)
            if hit is not None:
                resolved_bundle.add(name)
                queue.append(hit)
            elif name in SYSTEM_LIBS:
                missing_system.setdefault(name, []).append(cur.name)
            else:
                broken.append((cur.name, name))

    print("=== 由镜像自带解析的依赖 ===")
    for n in sorted(resolved_bundle):
        print(f"  ✓ {n}")
    print("\n=== 由基础镜像 / apt 提供的依赖 ===")
    for n in sorted(missing_system):
        users = sorted(set(missing_system[n]))
        print(f"  · {n}   ← {', '.join(users)}")

    if broken:
        print("\n=== ✗ 无法解析（镜像里会直接起不来）===")
        for owner, name in broken:
            print(f"  ✗ {name}   ← {owner}")
        print("\n修法：把对应文件补进 docker/jellyfin/lib（重跑 fetch 脚本），")
        print("      或在 Dockerfile 的 apt 清单里补包，并把包名加进本脚本的 SYSTEM_LIBS。")
        return 1

    print("\n✅ 依赖闭包完整：所有 DT_NEEDED 都能解析。")
    print("   （这只是静态可解析性；GPU 是否真能硬解仍需在真机跑 /api/system/hw-accel 确认）")
    return 0


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "docker/jellyfin")
    return verify(root)


if __name__ == "__main__":
    sys.exit(main())
