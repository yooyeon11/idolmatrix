# K-pop 媒体库（kpop-media）

一个面向 K-pop 音乐视频与直拍（Fancam）的本地媒体管理软件，第一期工程。
支持内容管理、关系型元数据、播放、转码、待整理入库流程。

---

## 1. 架构说明

### 1.1 总体设计

```
┌─────────────────────────────────────────────────────────────┐
│                      浏览器 (Vue 3 SPA)                      │
│  Naive UI · Vue Router · Pinia · axios · video.js + hls.js   │
└───────────────────────────┬─────────────────────────────────┘
                            │ /api  (dev: vite proxy)
┌───────────────────────────▼─────────────────────────────────┐
│                    FastAPI (uvicorn)                          │
│  api/  ─ schemas/  ─ services/  ─ media/  ─ tasks/           │
└───────────┬───────────────────────────────┬─────────────────┘
            │                                 │
   ┌────────▼─────────┐              ┌────────▼────────┐
   │  SQLAlchemy 2.0  │              │  FFmpeg/ffprobe │
   │  SQLite (默认)    │              │  (subprocess)   │
   └──────────────────┘              └─────────────────┘
            │                                 │
   ┌────────▼─────────────────────────────────▼─────────┐
   │  storage/  incoming(待整理) · library(正式) · derived │
   └─────────────────────────────────────────────────────┘
```

### 1.2 关键设计原则

1. **数据库是唯一事实来源**：所有元数据落 SQLite，不依赖文件名推断。
2. **Solo Artist 与 Group 平等**：都用 `release_artist_type + release_artist_id` 多态发行主体（不带外键，应用层校验）。
3. **能推导的不重复存**：组合成员数、视频可播性等都查表计算。
4. **技术字段由程序写入**：duration / codec / 分辨率等统一由 `ffprobe` 探测后回填，不靠人工。
5. **媒体处理一律走 FFmpeg**：禁止纯 Python 解码/转码；统一 `subprocess` 调用，参数/日志/退出码完全可控。
6. **配置集中**：所有路径、FFmpeg 路径、转码参数都走 `pydantic-settings`，从 `.env` 加载。
7. **文件移动有日志校验**：入库移动前后都算 `sha256`，比对一致才算成功，并写 `file_move_logs`。

### 1.3 目录结构

```
mv/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI 入口，含启动自检
│   │   ├── core/                    # config / database / deps / ffmpeg 自检
│   │   ├── models/                  # 11 个 ORM 实体
│   │   ├── schemas/                 # Pydantic v2 IO 契约
│   │   ├── api/                     # 路由（聚合在 router.py）
│   │   ├── services/                # 库扫描 / 入库 / 转码 / 文件服务
│   │   ├── media/                   # ffprobe / ffmpeg 封装
│   │   └── tasks/                   # 后台任务入口
│   ├── requirements.txt
│   ├── .env.example
│   └── run_dev.py
├── frontend/
│   ├── src/
│   │   ├── main.ts · App.vue
│   │   ├── api/                     # axios 实例 + 各实体 API 模块
│   │   ├── stores/                  # Pinia (app)
│   │   ├── router/                  # 路由
│   │   ├── types/                   # 与后端对齐的 TS 类型 + 枚举
│   │   ├── utils/                   # format 时长/大小/分辨率
│   │   ├── layouts/                 # MainLayout
│   │   ├── components/              # Sidebar / Header / VideoPlayer / icons
│   │   ├── views/                   # 9 个核心视图
│   │   └── styles/
│   ├── package.json · vite.config.ts · tsconfig.json
│   └── .env
├── storage/                         # 自动创建（gitignore）
│   ├── incoming/  library/  derived/
├── data/                            # SQLite 文件（gitignore）
├── Dockerfile                       # NAS 单容器镜像（内置完整 FFmpeg）
├── docker-compose.yml               # 绿联 NAS 编排（自定义目录挂载）
├── .env.nas.example                 # NAS 目录/端口配置模板
└── README.md
```

---

## 2. 数据模型（已实现）

按规范实现 11 个实体 + 2 个任务/日志表：

| 实体 | 表名 | 关键关系 |
|---|---|---|
| Artist | `artists` | memberships / company_relations |
| Group | `groups`（避开保留字） | memberships / company_relations |
| GroupMembership | `group_memberships` | Artist ↔ Group，含 status/positions |
| Company | `companies` | 自关联 parent， Artist/GroupCompanyRelation |
| ArtistCompanyRelation | `artist_company_relations` | Artist ↔ Company |
| GroupCompanyRelation | `group_company_relations` | Group ↔ Company |
| Album | `albums` | 多态发行主体，label→Company |
| AlbumTrack | `album_tracks` | Album ↔ Song，唯一约束 (album,disc,track) |
| Song | `songs` | 多态发行主体，artist_relations / credits / music_videos |
| SongArtistRelation | `song_artist_relations` | XOR 约束：artist_id 与 group_id 恰一非空 |
| Credits | `credits` | Song 制作人员 |
| MusicVideo | `music_videos` | 核心实体，含技术字段 + ingestion_status |
| TranscodeTask | `transcode_tasks` | pending/processing/done/failed 状态机 |
| FileMoveLog | `file_move_logs` | 文件移动日志 + 哈希校验 |

---

## 3. FFmpeg 封装设计

### 3.1 为什么用 subprocess 而非绑定库

- 参数完全可控，转码命令可读、可审计
- 完整捕获 stderr 与退出码，错误诊断直接
- 跨平台一致行为
- 可解析 `-progress` 输出做实时进度上报

### 3.2 模块划分

**`app/media/ffprobe.py`**
- `probe(path) -> ProbeResult`：单次调用 `ffprobe -show_format -show_streams -print_format json`
- 解析出 `duration / width / height / video_codec / audio_codec / frame_rate / bitrate / file_size`
- 帧率支持 `30000/1001` 分式解析

**`app/media/ffmpeg.py`**
- `TranscodeParams`：默认 `libx264 + veryfast + crf 23 + aac 192k + scale 1280:-2`，可从 `.env` 覆盖
- `transcode(source, output, params, on_progress)`：
  - 命令含 `-movflags +faststart`（浏览器快速起播）
  - 逐行解析 `out_time_ms` 换算 0-100 进度回调
  - 失败抛 `FFmpegError`，stderr 截尾 500 字
- `extract_thumbnail(source, output, at_seconds=5)`：单帧 seek；若片长短于 5 秒自动取中点
- `is_browser_playable(video_codec, audio_codec)`：H.264 + AAC 视为可直接播放

### 3.3 调用链

```
扫描入库: scan_incoming → probe → 写 MusicVideo 技术字段
入库移动: ingest_video → move_to_library (sha256 校验) → extract_thumbnail
转码:    POST /transcode → BackgroundTasks → transcode(-progress) → 回写 transcode_path
播放:    GET /music-videos/{id}/stream[-transcode] → FileResponse (支持 Range)
保底:    POST /music-videos/{id}/open-in-system-player → os.startfile / open / xdg-open
```

---

## 4. 启动方式

### 4.1 环境要求

- Python 3.10+（spec 要求 3.11+，本机 3.10 实测可用，因代码全程 `from __future__ import annotations`）
- Node 18+ / npm 9+
- FFmpeg / ffprobe（已检测到 6.1.1；缺失也不阻断启动，转码相关功能不可用）

### 4.2 后端

```powershell
cd backend
python -m pip install -r requirements.txt

# 复制环境变量模板并按需修改（FFmpeg 路径、存储路径等）
copy .env.example .env

# 开发启动
python run_dev.py
# 或： python -m uvicorn app.main:app --reload --port 8010
```

启动后会自动：创建 `storage/` 与 `data/` 目录 → 初始化数据库表 → 检测 FFmpeg（结果在日志与 `/api/system/ffmpeg`）。

> 后端固定监听 `127.0.0.1:8010`（`SERVER_PORT` 环境变量可覆盖），避开本机其他服务常用的 8000 端口。

API 文档：`http://127.0.0.1:8010/docs`

### 4.3 前端

```powershell
cd frontend
npm install

# 开发启动（固定 5173，自动代理 /api 到 8010）
npm run dev
```

打开 `http://localhost:5173`，默认进入「仪表盘」。

### 4.4 使用流程

1. 把待整理视频丢进 `storage/incoming/`（支持 mp4/mkv/mov/avi/flv/webm 等）
2. 前端「待整理」页点「扫描 incoming 目录」
3. 列表里点「入库」，补全名称/类型/歌曲/来源后确认
4. 程序自动：写库 → 移动到 `storage/library/<group>/<title>` → 截封面
5. 「视频库」里点开详情：
   - 兼容格式直接网页播放（video.js）
   - 不兼容格式点「提交转码」生成 H.264+AAC
   - 点「系统播放器打开」走 `os.startfile` 保底

### 4.5 部署到绿联 x86 NAS（Docker 单容器）

前端构建为静态文件后由后端直接伺服，**一个容器同时提供网页与 API**，镜像内置**完整最新版 FFmpeg**（jellyfin-ffmpeg，自带 Intel QSV 硬件编解码支持）。待整理、正式库等目录通过卷挂载**完全自定义**。

**NAS 上不构建镜像**（NAS 拉 GitHub 极慢且易失败），采用「开发机构建 → 导出 tar → NAS 导入」的方式：

**第一步：开发机构建并导出镜像（只需一次）**

```bash
# 在项目根目录执行（Mac 上会自动走 amd64 仿真，构建较慢属正常）
# 注意：tag 必须与 docker-compose.yml 中 image: 的 tag 完全一致
docker build --platform linux/amd64 -t idolmatrix:1.0.1 .
docker save idolmatrix:1.0.1 -o idolmatrix-1.0.1.tar
```

**第二步：NAS 上导入并启动**

1. 在 NAS 上安装 Docker（UGOS Pro 应用中心 → Docker）
2. 把 `idolmatrix-1.0.1.tar`、`docker-compose.yml`、`.env.nas.example` 上传到 NAS，例如 `/volume1/docker/kpop-media/`
3. 导入镜像：`docker load -i idolmatrix-1.0.1.tar`
4. （可选）自定义路径：把 `.env.nas.example` 复制为 `.env` 并修改；不复制则默认 `/volume1/kpop/{incoming,library,derived,db}`
5. 启动：`docker compose up -d`（**必须是 `image:` 启动，不要加 `build:`**）
6. 浏览器打开 `http://<NAS-IP>:8010`（默认端口，`.env` 中 `HOST_PORT` 可改）

**⭐ SQLite 数据库卷与备份（重要）：**

- 数据库文件为 `DB_DIR/kpop_media.db`，位于绑定的 `DB_DIR` 卷中（`docker-compose.yml` 已把 `${DB_DIR}:/data/db` 挂进容器，默认 `/volume1/kpop/db`）。**`DB_DIR` 必须挂卷**，否则容器删除/重建会清空全部元数据（媒体库、播放历史、扫描进度）。
- 数据库启用了 **WAL** 模式：直接 `cp` 拷贝 `.db` 单文件可能漏掉 WAL 中尚未合并的提交。请用 `sqlite3` 在线备份（运行中即可安全执行）：
  ```bash
  sqlite3 /volume1/kpop/db/kpop_media.db ".backup '/volume1/kpop/db/kpop_media.backup.db'"
  ```
  建议在 NAS 上配 cron 每日自动备份，例如每天 03:00：
  ```bash
  0 3 * * * sqlite3 /volume1/kpop/db/kpop_media.db ".backup '/volume1/kpop/db/kpop_media.backup.db'"
  ```
- 恢复：先把 `.backup.db` 拷回 `kpop_media.db`（容器停止时），再 `docker compose up -d`。

**Intel 核显硬件转码（i5-1235U 等 Alder Lake+ 核显）：**

- 转码默认自动探测硬件：`TRANSCODE_HWACCEL=auto` 时优先 **VAAPI**（软解 + `h264_vaapi` 硬编，iHD 驱动在绿联 NAS i5-1235U 上已验证稳定）；设备不存在或初始化失败自动回退软件 `libx264`。**QSV 不在自动路径中**（此环境下完整硬解硬编易报 `Unsupported ratecontrol / pixel format`，仅显式 `TRANSCODE_HWACCEL=qsv` 时启用，供高级用户试验）
- 强制/关闭可在 `.env` 中设 `TRANSCODE_HWACCEL=qsv`、`vaapi`、`none` 或 `cpu`（后两者均为强制软件转码）；强制硬件探测失败也会自动回退软件转码
- 画质默认档：能 Direct Play（mp4/m4v/mov + h264/aac 等可直接播放）时选原画；否则要转码——硬件转码可用（VAAPI/QSV）默认选 ≤1080p 的最高非原画档，纯软件转码默认选 ≤720p 的最高非原画档，避免 4K 源一上来就按原画质长时间转码。用户在齿轮里手动选档后不再覆盖
- 排查：先在 NAS 宿主机执行 `ls -la /dev/dri/renderD128` 确认设备存在；`docker-compose.yml` 已配置 `devices: - /dev/dri:/dev/dri` 透传；容器内执行 `docker exec kpop-media ffmpeg -hide_banner -y -init_hw_device vaapi=va:/dev/dri/renderD128 -filter_hw_device va -f lavfi -i testsrc=size=128x128:rate=15:duration=0.3 -vf format=nv12,hwupload -c:v h264_vaapi -qp 28 -f null -` 可直测 VAAPI **真编码**是否可用
- VAAPI 会否「设备初始化失败、回退软转」关键看镜像是否带齐 **VA-API 运行库**：若日志报 `libva-drm.so.2 / libdrm.so.2: cannot open shared object file`，说明镜像缺 `libva-drm2 / libdrm2`（`--no-install-recommends` 装驱动时不会自动带上），需重建镜像（镜内已显式安装 `libva2 libva-drm2 libva-x11-2 libdrm2`，且 `LD_LIBRARY_PATH` 已包含 `/usr/lib/x86_64-linux-gnu`、`LIBVA_DRIVERS_PATH=/usr/lib/x86_64-linux-gnu/dri`）。**这属于镜像依赖问题，仅重启容器无效，必须重建镜像后 `docker load` 新 tar**
- 系统页「硬件加速自检」会逐项报告 QSV / VAAPI 的设备节点、编码器、设备初始化结果及完整错误链；VAAPI 需**成功试编一帧 h264_vaapi** 才判定为可用，避免「设备 init 通过、实际软转」的假绿
- 分片未写完：转码进行中请求尚未产出的分片时返回 **503 + Retry-After**（仅当该分片已写入磁盘 playlist、或后一片已存在时才返回 200），播放器自动重试续播，不会因缺失分片直接报错退出；分片响应 `Cache-Control: no-store`

**自定义目录说明：**

| 配置项 | 默认路径 | 用途 |
|---|---|---|
| `INCOMING_DIR` | `/volume1/kpop/incoming` | 待整理文件夹，丢视频进去后前端扫描入库 |
| `LIBRARY_DIR` | `/volume1/kpop/library` | 正式库文件夹，入库后的视频存放位置 |
| `DERIVED_DIR` | `/volume1/kpop/derived` | 派生文件（缩略图 / 转码产物；其中 `transcodes/` 下窗口目录会在引用归零后延迟删除，并定时清理 >24h 的孤儿目录，绝不触碰 `library/` 原始文件） |
| `DB_DIR` | `/volume1/kpop/db` | SQLite 数据库目录（**必须挂卷**，备份见上方「SQLite 数据库卷与备份」） |
| `HOST_PORT` | `8010` | 对外访问端口 |

> 提示：升级代码后重新构建导出新 tar（tag 递增，如 `0.9.6`，并同步修改 `docker-compose.yml` 中的 `image:`），NAS 上 `docker load` + `docker compose up -d` 即可，媒体文件与数据库都在 NAS 侧目录中，不会丢失。

---

## 5. 第一期已完成 vs 未做

### 已完成

- 前后端可运行脚手架（实测：后端 import + init_db + 3 个 API 200；前端 type-check 通过 + dev 代理打通）
- 11 个实体模型 + Pydantic v2 schemas
- Artist / Group / Membership / Song / MusicVideo 完整 CRUD API
- 待整理流程：扫描（ffprobe 探测 + 哈希去重）→ 入库（写库 + 移动 + 校验 + 截封面）
- 库浏览：按组合/成员/歌曲/视频类型查看（列表 + 筛选）
- 播放：原文件 / 转码版双源 video.js 播放器 + 系统播放器保底
- 转码：H.264+AAC，含进度上报与状态机
- Dashboard 数量统计 + 视频类型分布
- FFmpeg 自检 + 路径可配置 + 中文界面

### 明确不做（与 spec 一致）

多用户权限 / AI 自动分析（仅预留接口）/ HLS 自适应 / 字幕多音轨 / 移动端适配

---

## 6. 第一期后续建议实现优先级

按价值/依赖排序，建议依次推进：

### P0 — 体验闭环（最高优先）

1. **入库时按 Song/Group 自动推导目录结构**
   现在入库平铺到 `library/<group>/<title>`，但 group 来自 song 关系。完善 `build_library_path`：从 `song_id` 反查 group，套用 `LIBRARY_PATH_TEMPLATE`。
2. **MusicVideo 编辑表单**
   详情页目前只读，需要可编辑元数据（关联歌曲、直拍对象、来源等）的弹窗。
3. **MusicVideo 列表封面缩略图卡片视图**
   现在是纯表格，K-pop 场景视觉信息重要，加封面卡片网格切换。

### P1 — 元数据深度

4. **Group / Artist 详情页**
   组合详情：成员列表 + 在库视频；艺人详情：所在组合 + 直拍列表。
5. **Album / AlbumTrack 管理 UI**
   后端已就绪，前端缺页面；建议放在 SongsView 的 Tab 里。
6. **SongArtistRelation 编辑器**
   给歌曲挂多个艺人/组合并标角色（Primary/Featured/Remixer）。
7. **Credits 管理 UI**（可选，优先级低）

### P2 — 转码与播放增强

8. **转码任务取消 + 失败重试**
   现在只能新建；加取消（kill 子进程）与一键重试。
9. **转码预设选择**
   详情页提交转码时可选 720p/1080p/原尺寸 + CRF。
10. **播放历史 / 上次进度**
    前端 localStorage 记录每视频的播放位置，下次续播。

### P3 — 工程化

11. **Alembic 迁移**
    现用 `create_all`；上生产或模型变更前必须接入 Alembic。
12. **扫描增量缓存**
    大目录扫描慢，把 `scan_incoming` 结果缓存到表，按 mtime 增量更新。
13. **批量入库**
    待整理页支持多选 + 批量套用同一元数据（同组合同场演出）。
14. **PostgreSQL 切换演练**
    改 `DATABASE_URL` 即可，但建议补一次 PG 环境冒烟测试。
15. **AI 自动分析接口预留**
    定义 `analysis_jobs` 表 + `POST /music-videos/{id}/analyze` 占位，后续接 Whisper 转写 / 人脸识别。
16. **单元测试**
    重点覆盖 `file_service`（哈希校验、移动）、`ffprobe._parse`（帧率/编码）、`library_service.ingest_video`。

---

## 7. 配置项速查（`backend/.env`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/kpop_media.db` | 改 `postgresql+psycopg://...` 切 PG |
| `STORAGE_INCOMING_DIR` | `./storage/incoming` | 待整理扫描入口 |
| `STORAGE_LIBRARY_DIR` | `./storage/library` | 正式库 |
| `STORAGE_DERIVED_DIR` | `./storage/derived` | 转码/封面 |
| `LIBRARY_PATH_TEMPLATE` | 空（平铺） | 支持 `{group}/{song}/{video_type}/{year}` |
| `FFMPEG_PATH` / `FFPROBE_PATH` | 空（自动检测 PATH） | Windows 可填绝对路径 |
| `TRANSCODE_VIDEO_CRF` | `23` | 越小质量越高、文件越大 |
| `TRANSCODE_SCALE` | `1280:-2` | `-2` 表示按比例 |

---

## 8. 验证记录（本机）

- 后端 `import app.main` + `init_db()` 通过
- `GET /api/system/health` → 200
- `GET /api/dashboard/stats` → 正确空统计
- `GET /api/music-videos` → 分页结构正确
- `GET /api/library/scan` → 0 文件（incoming 为空）
- 前端 `npm run type-check` 通过
- 前端 dev server `:5173` 启动，`/api` 代理到 `:8010` 验证 200
- FFmpeg 6.1.1 / ffprobe 6.1.1 已检测可用
