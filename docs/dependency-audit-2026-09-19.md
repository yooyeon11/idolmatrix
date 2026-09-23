# 依赖与死代码审计（2026-09-19，对应代码版本 3.2.55）

方法：AST 解析后端全部 `*.py` 的 import 与 `requirements.txt` 对账；正则解析前端
`src/**/*.{ts,vue}` + `vite.config.ts` 的 import 说明符与 `package.json` 对账；
再按「零引用」扫描源码文件与仓库散落产物。全部只读，未改动任何文件。

---

## 结论速览

| 类别 | 结论 |
|---|---|
| 前端运行时依赖（7 条） | **没有无用依赖**，7 条全部在真实使用 |
| 前端构建工具链（8 条 devDeps） | 全部在用（toolchain 不产生 import，静态扫描看不见） |
| 后端依赖（9 条） | 全部有用；有 1 条「隐形依赖」`python-multipart`（无 import 但必须保留） |
| 后端「用了但没声明」 | 3 条：`numpy`（建议补）、`certifi`（可不管）、`starlette`（可不管） |
| 源码死代码 | 1 个后端文件 + 5 个前端组件（确定零引用） |
| 遗留脚本 | 2 个 macOS 遗留（`test_acceptance.py`、`e2e_cases.py`），Windows 上必失败 |
| 仓库垃圾 | 15 个 `.DS_Store`、10 个无引用 PNG、`docs/` 113MB 等 |

**一句话：依赖清单是干净的，没有"装了没用"的运行时依赖；真正的问题是死代码、
MAC 遗留脚本和仓库垃圾文件。**

---

## 一、依赖对账

### 1.1 前端 `package.json` —— 无冗余

| 依赖 | 引用文件数 | 判定 |
|---|---|---|
| `vue` | 80 | 使用 |
| `vue-router` | 35 | 使用（另由 auto-import 注入） |
| `naive-ui` | 32 | 使用 |
| `pinia` | 7 | 使用（vite.config 的 auto-import imports 列表） |
| `axios` | 2 | 使用 |
| `video.js` | 2 | 使用 |
| `hls.js` | 1 | **使用**（曾被注释误导） |

**`hls.js` 需要澄清**：`VideoPlayer.vue:4` 的注释写着"已纳入 video.js VHS；
这里引入以备后续显式使用"，看起来像预留。实际 `applySource()` 里有活的 HLS 分支：

```
if (isHls && !USE_NATIVE_HLS && Hls.isSupported()) {  // VideoPlayer.vue:408
  hls = new Hls({ ... }); hls.attachMedia(media); hls.loadSource(src.src)
```

m3u8（转码播放）在桌面端就是走它，**不能删**。建议把那条过时注释改掉，免得下次又被误判。

devDependencies 8 条（`@types/node`、`@vue/tsconfig`、`typescript`、`vue-tsc`、
`vite`、`@vitejs/plugin-vue`、`unplugin-auto-import`、`unplugin-vue-components`）
静态扫描显示"0 引用"，但都是工具链用法（`tsconfig.json` extends `@vue/tsconfig`、
`types:["node"]`、`npm run build` 调 `vue-tsc`），**全部保留**。

`vfonts` 只出现在 `main.ts` 的注释里（当时已删除 import，注释记录了原因）；
它不在 `package.json` 中，作为 naive-ui 的传递依赖存在 —— **现状正确，无需动作**。

### 1.2 后端 `requirements.txt` —— 全部有用，但有一条隐形依赖

| 声明 | 代码可见性 | 判定 |
|---|---|---|
| `fastapi` / `uvicorn[standard]` | 大量 import / `run_dev.py` | 使用 |
| `SQLAlchemy` / `pydantic` / `pydantic-settings` | 大量 import | 使用 |
| `alembic` | `core/database.py` 启动时 `command.upgrade` | 使用 |
| `argon2-cffi` | `core/security.py` | 使用 |
| `opencv-python-headless` | `img_variant.py`（延迟导入 + 降级） | 使用（可选特性） |
| **`python-multipart`** | **从未 import** | **必须保留** |

`python-multipart` 是 FastAPI/Starlette 解析 `UploadFile` / `File()` / `Form()` 的
运行时依赖，缺了会在声明表单参数时直接报 "Form data requires 'python-multipart'"。
本项目 `api/photos.py`、`api/uploads.py` 都在用。**建议在 requirements 里加一行注释
说明它是隐形依赖**，否则后人做依赖清理时会误删。

### 1.3 反向：import 了但没声明

| 模块 | 位置 | 风险 | 建议 |
|---|---|---|---|
| `numpy` | `services/img_variant.py:82` | **中**：靠 opencv 传递带入；本机 venv 里 opencv/numpy **都没装**，该路径实际处于降级状态 | 显式声明 `numpy`，或把图像相关依赖整体挪到可选 extras |
| `certifi` | `services/proxy_config.py:77` | 低：`try/except` 优雅降级（仅 macOS 官方 Python 缺根证书时需要） | 可不声明 |
| `starlette` | 7 处（`api/playback.py`、`core/auth_gate.py` 等） | 低：由 fastapi 保证；但版本被间接锁定（当前 fastapi 0.141.1 → starlette 1.6.0） | 可不声明，升级 fastapi 时留意 |

### 1.4 环境层面的冗余（本机 venv）

本机 venv 实际装了 35 个包，其中：

- **`pypdf==6.17.0` —— 全仓库零引用**（含文档、脚本、Dockerfile），纯多余。
- `httpx` —— 应用代码不 import，只有 FastAPI `TestClient` 用得上。
- `pytest==9.1.1` —— **49 个测试脚本要用，但 `requirements.txt` 没声明**
  → 干净环境照 requirements 装完跑不了测试。建议加 `requirements-dev.txt`。
- venv 里是**裸 uvicorn**（没有 httptools/uvloop/watchfiles/PyYAML），而 requirements
  写的是 `uvicorn[standard]` —— 本机与声明不一致（`run_dev.py --reload` 会退化到
  StatReload 轮询）。不影响功能，但说明本机 venv 不是按 requirements 建起来的。

---

## 二、死代码（零引用，可安全删除）

### 2.1 `backend/app/services/_rebuild_snippet.py`（321 行，确定可删）

文件内容只有一句 docstring + 一个巨型字符串常量 `REBUILD = '''...'''`
（"以下内容将插入 source_sync.py"），最后一行是 `print("REBUILD ready")`。
**全仓无任何模块 import 它**，属于一次性手术的剪刀残留。
（被它描述的函数早已落进 `services/source_sync.py`，3322 行，正常运行。）

### 2.2 前端 5 个从未使用的组件（确定可删）

| 文件 | 证据 |
|---|---|
| `src/components/db/GrowthSheet.vue` | 全仓仅出现在自动生成的 `components.d.ts` |
| `src/components/db/MemberTrajectory.vue` | 同上 |
| `src/components/HeroVideoPickerModal.vue` | 同上 |
| `src/components/RemoteArtistSelect.vue` | 同上 |
| `src/components/SaRadioGroup.vue` | 同上 |

已核对 **dist 产物中出现次数 = 0**：unplugin-vue-components 会做摇树，未使用组件不会
进 bundle，所以它们**不影响加载速度，只占仓库体积**。删除后 `components.d.ts` 会自动
更新（该文件由插件生成）。

---

## 三、遗留脚本与工具

| 文件 | 状态 | 说明 |
|---|---|---|
| `backend/test_acceptance.py`（17.8KB） | **已失效** | 硬编码 `http://127.0.0.1:8010` + `/Users/zhanghaoran/Downloads/mv/backend`，且需手工先起服务。记忆里已多次标注"非回归，别当失败" |
| `backend/e2e_cases.py`（15KB） | **已失效** | 同样硬编码 Mac 路径与 8010，还依赖 `/tmp/mv_e2e/*.mp4` 夹具；有可运行的 `main()`，但本机跑不通 |
| `backend/run_dev.py` | 在用 | 开发服务器（`--reload`），保留 |
| `backend/app/cli.py` | 在用 | 容器内维护命令（reset-owner-password / fix-short-flags） |
| `Dockerfile.local` | 已无场景 | 注释写明"本地测试用（Apple Silicon arm64）"，当前开发机是 Windows/x86 → 实际不会再被用到（历史保留即可） |
| `.dockerignore` 中的 `deploy/` | 空引用 | 该目录并不存在（预留） |

两个失效脚本都已在 `.dockerignore` 中排除，**不会进生产镜像**，只是污染仓库与
"全量回归"的观感。

---

## 四、仓库垃圾与体积

| 项 | 体积 | 说明 |
|---|---|---|
| `docs/` | **113 MB** / 139 项 | 截图归档（有意保留），但已是仓库最大单块 |
| `gui-test-screenshots/` | 8.6 MB | 历史验证截图，未被任何文档引用 |
| 根目录散落 PNG ×10 | ~2.1 MB | `cover-variant-compare`、`hero-zoom`、`p2-*`（4 个）、`stats-dark`、`edit-modal-*`（3 个）——**README 引用次数全为 0** |
| `export/` | 888 KB | 2026-09-17 记忆导出（目录 + zip） |
| 根目录 `idolmatrix-记忆导出-2026-09-17.md` | 224 KB | 与 `export/` 内同名文件**重复** |
| `.DS_Store` ×15 | — | 根、`.workbuddy/`、`backend/`、`backend/app/api/`、`backend/schema_revisions/`、`docker/`、`docs/`、`frontend/src/` 等；`.gitignore` 已含规则，但文件仍在盘上 |
| `.gy-shots/` | 296 KB | 2 张临时 shot |
| `backend/.env.bak-20260915` | 0.8 KB | 已被 `*.bak` 规则覆盖 |
| `frontend/dist` | 9.3 MB | 构建产物（`dist/` 已 ignore，但本地要伺服） |
| `__pycache__` / `.pytest_cache` ×2 | — | 已 ignore |

---

## 五、建议动作（按优先级）

**P0｜零风险，随时可做**
1. 删 `backend/app/services/_rebuild_snippet.py`；
2. 删 5 个零引用前端组件；
3. 清 15 个 `.DS_Store` + `backend/.env.bak-20260915`；顺手删根目录重复的记忆导出 md。

**P1｜需要你拍板（有历史价值）**
4. 两个 macOS 遗留脚本：改写 `BASE_URL = os.getenv("E2E_BASE_URL", ...)` 复活，或直接删；
5. 根目录 10 个无引用 PNG + `gui-test-screenshots/` + `.gy-shots/`：归档进 `docs/` 或删。

**P2｜依赖卫生**
6. `requirements.txt`：给 `python-multipart` 加"隐形依赖"注释；显式声明 `numpy`；
7. 新增 `requirements-dev.txt`（pytest、httpx），让测试可在干净环境复现；
8. 本机 venv 卸掉 `pypdf`。

**P3｜体积治理**
9. `docs/` 113MB：按版本定期清理，或把老截图移出仓库（Git LFS / 外置归档）。

> ⚠️ 以上均**未执行**，仅审计。确认后我可以按 P0 → P1 分批处理（每批删完即核验）。
