# =============================================================
# K-pop 媒体库 —— 绿联 x86 NAS 单容器镜像（多阶段构建）
# 与 Dockerfile 的唯一差别：基础镜像走 daocloud 镜像源、apt 不切国内源。
# Stage 1: Node 构建前端静态文件
# Stage 2: Python 运行时 + jellyfin-ffmpeg 8.1（转码 / 缩略图 / 硬解）
# =============================================================

# ---------- Stage 1: 构建前端 ----------
FROM docker.m.daocloud.io/library/node:20-alpine AS frontend-build

WORKDIR /build

# 先装依赖，利用层缓存
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# 拷贝源码并构建
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: 运行时 ----------
FROM docker.m.daocloud.io/library/python:3.11-slim-bookworm AS runtime

# ===== FFmpeg 的系统侧依赖 =====
# ★ 这份清单 = 官方 deb 的 `Depends:` 字段，不是随手凑的，可用
#   `dpkg-deb -f jellyfin-ffmpeg8_<ver>-bookworm_amd64.deb Depends` 复核。
#   必须装齐：官方包是交给 apt 解析依赖的，而我们是手工解包后 COPY，
#   **绕过了依赖解析** —— libavcodec.so.62 对 libx264/libx265/libopus/libvpx…
#   是 DT_NEEDED **硬依赖**，少任何一个 ffmpeg 就整个加载失败。
#   改完这里请跑 `python scripts/verify_jellyfin_bundle.py` 静态复查依赖闭包。
#   （libc6 / libgcc-s1 基础镜像已带，未重复列出。）
#
# 有意**不装**两类东西：
#   · 发行版 VA-API 驱动（intel-media-va-driver 等）：官方 deb 也不依赖它，
#     因为包内已自带 iHD / i965 / radeonsi 三家驱动 + libigdgmm，是一套自洽版本。
#   · 发行版 libva2：bookworm 只有 2.17.0，缺 vaMapBuffer2 符号，
#     而 jellyfin-ffmpeg 需要 ≥2.21 —— 被误加载会直接 abort。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libstdc++6 libelf1 libexpat1 libzstd1 libgmp10 libgnutls30 \
        libpciaccess0 libudev1 libbluray2 libmp3lame0 libopenmpt0 libopus0 \
        libvorbis0a libvorbisenc2 libvpx7 libwebp7 libwebpmux3 \
        libx264-164 libx265-199 libzvbi0 ocl-icd-libopencl1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ===== FFmpeg 8.1（jellyfin-ffmpeg 官方构建，专为媒体转码定制）=====
# 来源：https://github.com/jellyfin/jellyfin-ffmpeg/releases/tag/v8.1.2-5
# 取用：python scripts/fetch_jellyfin_ffmpeg.py --tag v8.1.2-5 --flavor deb --apply docker/jellyfin
#
# ★ 8.1 起打包结构变了（从 7.x 升到 8.x 必须知道，否则会踩「缺库起不来」）：
#   · 旧 7.1.4 的 ffmpeg 是 107MB 自包含静态单体，只 dlopen 取 libva/libvpl；
#   · 8.1 的 ffmpeg 只有 413KB，编解码器在 lib/ 的 libavcodec.so.62 等共享库里，
#     这些是 DT_NEEDED **硬依赖** —— 少任何一个 ffmpeg 直接起不来。
#   · 实测所有 .so 与 dri 驱动都带 RPATH=/usr/lib/jellyfin-ffmpeg/lib，
#     库之间互相解析并不依赖环境变量；下面的 LD_LIBRARY_PATH 只是 dlopen 的兜底。
#   · lib/ 里是**多厂商驱动集**，故意不按当前机器型号裁剪 —— 换 NAS / 换显卡无需改镜像：
#       Intel：iHD（12 代+）/ i965（老核显）/ libmfxhw64（8~11 代 QSV）
#       AMD  ：radeonsi + libdrm_amdgpu / libdrm_radeon + libvulkan_radeon
#   · 另带 vainfo（VA-API 能力自检工具），供 /api/system/hw-accel 报告"GPU 能解哪些编码"。
COPY docker/jellyfin/ffmpeg /usr/local/bin/ffmpeg
COPY docker/jellyfin/ffprobe /usr/local/bin/ffprobe
COPY docker/jellyfin/vainfo /usr/local/bin/vainfo
COPY docker/jellyfin/lib /usr/lib/jellyfin-ffmpeg/lib
COPY docker/jellyfin/share /usr/lib/jellyfin-ffmpeg/share

# 确保可执行（源文件经压缩 / 网盘传输后执行位可能丢失）
RUN chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe /usr/local/bin/vainfo

# ===== 重建共享库的 soname 符号链接（自动反推，不再手抄版本号）=====
# 为什么需要：lib/ 来自官方 deb，其中的 soname 链接（如 libva.so.2 → libva.so.2.2400.0）
# 是**符号链接**；仓库经 zip / 网盘传输时会被降级成「只含目标文件名的文本文件」
# （11~23 字节），进镜像后 dlopen 报 "file too short"，VAAPI/QSV 静默回退软转。
#
# 所以仓库里**只存放真实文件、不存放任何链接**，链接一律在镜像内扫描生成：
#   1) 先删掉 <1KB 的假 .so（真实库最小也有 14KB），兼容"已把文本占位提交进仓库"的历史状态；
#   2) 对每个 libfoo.so.a.b.c 生成 libfoo.so.a / libfoo.so.a.b / libfoo.so 三级别名。
# 实测：对 8.1.2-5 可 100% 重建官方包内全部 69 个链接（另多出 28 个无害别名）。
# ⚠ 因此以后升级 ffmpeg 只需重跑 scripts/fetch_jellyfin_ffmpeg.py，本段一行都不用改。
RUN set -eux; \
    d="/usr/lib/jellyfin-ffmpeg/lib"; \
    find "$d" -maxdepth 1 -type f -name '*.so*' -size -1k -delete; \
    cd "$d"; \
    for f in *.so.*; do \
        if [ ! -e "$f" ]; then continue; fi; \
        if [ -L "$f" ]; then continue; fi; \
        base="${f%%.so.*}"; \
        ver="${f#*.so.}"; \
        acc=""; \
        for p in $(printf '%s' "$ver" | tr '.' ' '); do \
            acc="${acc:+$acc.}$p"; \
            n="$base.so.$acc"; \
            if [ "$n" != "$f" ]; then ln -sfn "$f" "$n"; fi; \
        done; \
        ln -sfn "$f" "$base.so"; \
    done; \
    echo "重建符号链接 $(find . -maxdepth 1 -type l | wc -l) 个"

# ===== VA-API / QSV 运行时环境：有意「什么都不写死」=====
# 这是"换设备也能用"的关键，改动前请先读 Dockerfile 里同一段说明并实测。
#   · 不设 LIBVA_DRIVER_NAME：jellyfin 把 libva 的驱动名开关改叫
#     LIBVA_DRIVER_NAME_JELLYFIN（标准名它根本不读 —— 写 LIBVA_DRIVER_NAME=iHD
#     在这里是**无效的**），而它默认会扫描驱动目录逐个试到成功，Intel / AMD 通吃。
#   · 不设 LIBVA_DRIVERS_PATH：jellyfin 版 libva 内置的默认搜索路径本身就是多厂商多目录：
#       /usr/lib/jellyfin-ffmpeg/lib/dri : /usr/lib/x86_64-linux-gnu/dri
#       : /usr/lib/dri : /usr/local/lib/dri
#     需要强制指定时才覆盖，例如 LIBVA_DRIVER_NAME_JELLYFIN=radeonsi。
#   · LD_LIBRARY_PATH 只指向自带目录，给 dlopen("libva.so.2" / "libvpl.so.2") 兜底。
#     ⚠ 不要再把 /usr/lib/x86_64-linux-gnu 加进来：那会让 python 等进程的系统同名库
#       （libz / libxml2 / libfreetype…）被自带更新版本遮蔽。
ENV LD_LIBRARY_PATH=/usr/lib/jellyfin-ffmpeg/lib

WORKDIR /app

# 先拷贝依赖清单并安装（利用 Docker 层缓存）
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# 拷贝后端代码与前端构建产物
COPY backend/ /app/backend/
COPY --from=frontend-build /build/dist /app/frontend/dist

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app/backend
EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8010/api/system/health',timeout=3)"

# 生产模式：绑定 0.0.0.0 供容器外访问（run_dev.py 仅限本机开发）
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
