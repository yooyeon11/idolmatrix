---
name: idolmatrix-ingest
description: idolMatrix 视频入库（待整理区 → 正式库）的标准作业流程与验收口径。当需要把 `<待整理目录>` 里的新素材入库、核对入库清单、处理机位/场次字段、或排查入库报错时使用。含 imflow CLI 用法、批次文件格式、10 条已知陷阱守卫、四维核验标准。
---

# idolMatrix 入库（待整理 → 正式库）

## 0 三条铁律

1. **不要现场重写规则**。命名/落位/机位/场次的权威定义都在代码里，改规则只改那几处：
   - 命名与落位：`tools/ingest/rules.py`（单一来源）
   - 机位 / 场次：`backend/app/core/vocab.py` ＋ `backend/app/services/video_meta.py`
   - 入库时服务端真正执行的口径：`backend/app/services/library_service.py`（`resolve_camera_angle` / `resolve_session_label`）
2. **一批 = 一个 JSON + 5 条命令**。不要为一批新建脚本。
3. **入库前必须先跑 `plan` 看审计**，18~21 维有任何 ⚠ 都要先解释清楚再提交。

工具目录：本 skill 同级 `tools/ingest/`（当前位于 `<ingest 工具目录>`，迁移时整目录复制）。

---

## 1 标准流程（5 步）

```bash
cd <项目>/tools/ingest

python imflow.py probe  <batch>            # ① 扫描 + 新到/离场/dup 对账（只读）
#   把「新到」条目写进 batches/<batch>.json
python imflow.py plan   <batch>            # ② 推断+规则 → 计划 + 审计 + 清单
python imflow.py mkent  <batch>            # ③ 如需新建实体（先 dry-run，再 --go）
python imflow.py ingest <batch>            # ④ 守卫 + 打印计划（不提交）
python imflow.py ingest <batch> --only 1 --go    #    试点 1 条
python imflow.py ingest <batch> --skip 1 --go    #    跑余量
python imflow.py verify <batch>            # ⑤ 四维核验
```

其他子命令：`doc`（重出清单）、`fields`（逐条字段清单，`--pending` 只看未入库）、`audit`（重跑审计）、`lock/unlock`（批次锁）。

**批次锁**：并行会话会互相覆盖，`plan/ingest` 会自动加锁，冲突时报错；确认对方已结束可加 `--force-lock`。

---

## 2 批次文件 `batches/<batch>.json`

### 形态 A：有 ytid（YouTube 下载，带 `.info.json`）

```json
{
  "batch": "20261018-xxx", "kind": "ytid",
  "items": [
    { "ytid": "XXXXXXXXXXX",
      "subj": "主体显示名", "ev": "活动名", "tail": "尾标",
      "perf": "2026-01-01", "pub": "2026-01-02",
      "vt": ["PerformanceVideo"], "g": [34], "a": [], "sids": [1066],
      "sdisp": ["曲名显示名"], "solo": false,
      "ident": "艺人田姬振", "ch": null,
      "participants": [210],
      "dest_dir": null, "note": "逐条依据 / 待确认" }
  ]
}
```

- **缺项才推断**：`subj/g/a/sids/vt/ev/tail/perf` 留空时由推断层从「文件名 + title + description」猜。
- **显式空值 `[]` 永远胜出**，不会被推断回填（`Andless` 曾因此被误挂）。
- `dest_dir` 留 `null` → 落位取服务端 `preview-path`；填了就覆盖（记为「手改」）。

### 形态 B：无 ytid（电视录像，`.ts`/`.avi`，无 `.info.json`）

```json
{ "batch": "...", "kind": "no-ytid",
  "no_ytid": {
    "pattern": "^\\[(\\d{6})\\]\\s*(.+?)\\.(ts|mp4|mkv|avi)$",
    "default_group_ids": [56], "default_subject": "KARA",
    "default_video_types": ["PerformanceVideo"],
    "song_map": { "Honey": {"sid": 2960, "disp": "Honey"} },
    "prog_map": { "KBS Music Bank": "Music Bank" },
    "tail_keep": { "SBS Inkigayo Idol Special": "SBS Inkigayo Idol Special" },
    "specials": { "090816|KARA - Again & Again (SBS Inkigayo Idol Special)": { } },
    "specials_by_fn": { "<完整文件名>": { } },
    "files": [{ "file_name": "...", "duration": 213, "width": 1920 }]
  } }
```

- 常规条由文件名自动解析；**只有偏离默认的才写 `specials`**。
- 文件名无规律时用 `specials_by_fn`（按完整文件名逐条定义）。
- 入库后文件搬离待整理区 → 用 `files` 留一份清单 + 批次标 `"archived": true`，即可随时重跑复现。

---

## 3 机位 / 场次（3.9.9x 新字段）

| 字段 | 口径 |
|---|---|
| `camera_angle` | **只有官方舞台（`PerformanceVideo`）才填**（业主 2026-10-23 收敛）。规范值：`FanCam`／`FaceCam`／`FullCam`／`BroadcastCam`（label「放送机位」）／`Tower Cam`／`K-Choreo`／`OneTake`／`MultiCam`／`SkyCam`／`其他`。**`SpecialStage`（特别舞台）／`Fancam`／`CoverStage`／`CollabStage`／`OfficialMV`／`ShortVideo` 一律不填**。官方舞台内部：尾标有机位词→归一；无机位词→`BroadcastCam`。**画幅不是机位**（`Vertical FanCam`→`FanCam`）；`K-Choreo Tower Cam`→`Tower Cam`；**词表外写法 400 阻断** |
| `session_label` | 格式 `YYMMDD - 活动名`；日期取 `performance_date`（缺失回退 `published_date`）；**活动名只认 `event_name`，缺失就留空** —— ★ 2026-10-26 业主：「活动名空→回退频道名这个不合理」，频道（`M2`/`tripleS official`/`유연하게`/`BlueRose`）不是活动，回退出来的是假场次。等 `event_name` 补齐再生成。**不参与落位目录** |

- **所有视频都要有场次**（业主 2026-10-22，含 MV）；**机位只写官方舞台**（2026-10-23）。
- **活动名用中文**（业主 2026-10-22，不要对齐库内写法）：**栏目/节目名保留英文**（`M COUNTDOWN`／`Music Bank`／`Show! MusicCore`／`SBS Inkigayo`）；**非栏目活动一律中文**（库内先例 `世宗青年商人节`／`KAIST 校庆`／`忠清南道残疾人体育大会开幕式`）。韩文→中文映射维护在 **`rules.EV_ZH`**，`build_plan` 在生成简介前自动翻译。
  - ★ **例外 1**：**年度品牌节要带年份**（业主 2026-10-26）—— `2026 NOL Festival`、`2025 KBS Song Festival`。理由：纯品牌名跨年无法区分，库内有此先例。
  - ★ **例外 2**：**品牌名不意译** —— `NOL Festival`／`HANTEO MUSIC FESTIVAL`／`THE FIRST TAKE` 保留原文。
- 两者由 `flowlib.cam_angle_of()` / `session_of()` 在 `plan` 阶段算好，并**显式写进入库 payload**（`_payload` 的 `camera_angle` / `session_label`），不依赖服务端二次推断。

### 3.1 ★ 混剪 ≠ 串烧（业主 2026-10-27）

这两个词在库里是**两种不同的落位**，不能混用：

| 概念 | 定义 | `video_types` | 落位 | 库内实例 |
|---|---|---|---|---|
| **混剪** | **同一首歌**的多场演出镜头剪在一起 | `MixEdit` | **`{团体}/{曲}/{主体}`**（与普通直拍同目录） | `NMIXX/O.O/Live`、`IVE/After LIKE/Jang Wonyoung`、`TripleS/Girls Never Die/Live` |
| **串烧** | 一支视频里**连唱多首不同的歌** | 按内容定（常 `Fancam`/`PerformanceVideo`） | **`串烧/{主体}/{YYMMDD}`** | `串烧/Bang Jeemin/260919/…Supercrush + Racecar + SIGN + METRONOME…` |

- 库内 11 条 `MixEdit` 里 **8 条**落在 `{团体}/{曲}/…`，只有**多曲**的那条（IZ*ONE 串烧）落 `串烧/`。
- 混剪视频的 `event_name` / `performance_date` / `session_label` 通常**留空**（多场拼合，无单一日期），巡演/各场信息写进 `chinese_description`，且**分场列举**（`ANDLESS in Seoul`、`ANDLESS in Taipei`、`ANDLESS in Macau`），不合并成 `Seoul&Taipei&Macau`。
### 3.2 ★ 落位首段用**实体名原文大小写**，不要手抄老库目录名（业主 2026-10-27）

- 团体段来自实体 `name` 字段，**原样使用**：`tripleS`（不是 `TripleS`）、`izna`、`KiiiKiii`、`A2O LITE`。
- 老库（9080）里遗留的 `TripleS`／`IZ_ONE`／`Jo Yuri` 是历史写法，**新库不要跟**。
- 同理 `dest_dir` **尽量不要手写覆写** —— 让 `preview-path` 推导；手写极易抄错大小写和成员段写法。
- 成员段取 `artist.name`（如 `YooYeon`、`SeoYeon`、`Jeemin`），不是 `Kim YooYeon` 这种带姓写法（除非实体本身就叫那个）。

### 3.3 ★ 改已入库视频的落位：用 `relocate`，不要 DELETE 重入（2026-10-27 实测）

```http
POST /api/music-videos/{id}/relocate
{"destination_rel": "tripleS/Andless/YooYeon/<完整文件名>"}
```

- `destination_rel` 是**含文件名的完整相对路径**（相对正式库根）；`relocate` 会连带迁移同名伴随文件（`.webp`／`.info.json`），有哈希校验和 `FileMoveLog`。
- 传 `null`／省略 → 由 `suggest_relocate_path()` 按自动整理规则推导（与入库同规则）。
- 路径相同返回 400「新路径与当前路径相同，无需移动」。
- ⚠️ `PATCH /api/music-videos/{id}` 带 `file_path` **返回 200 但静默忽略**（DB 不变），别拿它当移动手段。
- 所以「落位写错了」现在**不用**移回待整理再 DELETE 重入 —— 直接 `relocate` 即可。

- ⚠️ **旧版服务端清不掉机位**（传 `None`/空串会被当成「没给值」又从尾标推回）；已在 `music_videos.py`＋`library_service.py` 加类型门控，需重新部署才生效。
- ⚠️ **替换库内视频时封面必须跟着走**：待整理里没有同名 `.webp` 边车时，服务端会**截帧**生成 `.jpg`，原封面就丢了。重入前把旧 `.webp` 复制到待整理、与新视频**同 stem**。
- ⚠️ 改 `vocab.py` 后**必须重建镜像 + NAS 部署**才生效（线上镜像可能落后于代码）。

---

## 4 十条已知陷阱（已固化为守卫，不要绕过）

| # | 陷阱 | 守卫 |
|---|---|---|
| 1 | 谚文兼容字母 `ㅣ`(U+3163) 被当音节 → 主体+曲目双落空 | 边界判定只认音节 `\uac00-\ud7a3` |
| 2 | 曲名里的 `Swan` 被当成艺人 `a#284` | 艺人名是曲名子串即告警 |
| 3 | `Min Ju` 误挂 `a#235 Min` | ≤4 字符 ASCII 艺人名必须独立成词 |
| 4 | **同音实体**：`a#29 ChaeWon`(金采湲·tripleS) vs `a#118 Chaewon`(金采源·IZ*ONE) | 按主体团核对，**只挂一个** |
| 5 | 巡演名 ≠ 曲目（`ANDLESS in Seoul`） | 曲名与巡演标记**紧邻且只隔空白**才跳过；`Showcase ‘Shooting Star’` 这类不跳过 |
| 6 | 同名曲目复用会派生错误 `cover_from` | **同名且同原唱**才复用（`Sign` 教训）|
| 7 | 多曲 `CollabStage` 服务端只取首个团体 | 落位一律 `preview-path` + `rules.PATH_FIXUP`，**禁止手改 dest** |
| 8 | `[K-Choreo]` 标题里也含 `직캠` → 被判 `FanCam` | `K-Choreo` 规则排在 `직캠` **之前** |
| 9 | 活动名只写在 `description` 里（`SBS MEGA Concert`） | `find_event` 兜底扫**库内既有 event_name 词表** |
| 10 | 活动名候选误取成员名（`Summer`） | 候选**不得等于主体名/曲名**，且只在 name/原题里匹配 |

其他易错：`is_solo` 指**艺人独立活动**（不是「成员直拍」）→ 成员直拍一律 `false`；封面来自**图片边车**（不是抽帧）；MV 简介用「发布」不用「表演」；简介曲名用**官方英文名**不用韩文。

---

## 5 验收标准（`verify` 四维，必须全绿）

| 维度 | 标准 |
|---|---|
| ① 逐条回读 | 每条 `name`/`file_path`/`event_name`/日期/`chinese_description`/`video_types`/曲目/团体/`participants` 与计划**逐字一致** |
| ② 曲目 `video_count` | 每个曲目的增量 = 本批引用次数 |
| ③ 全库 diff | 新增条数 = 计划 id 集合；**既有条目意外变更 0 处** |
| ④ 待整理区 | 本批原文件已不在区内 |

`--apply` 类写操作一律**写完回读断言**，不符即中止。

---

## 6 常见返工点

- **试点先跑 1 条**：`--only 1 --go` 验通路径再跑余量，能提前暴露字段/落位问题。
- **`scan` 对大文件有登记延迟**（1.96 GB 曾等约 200 s）→ 轮询，别急着 ingest。
- **替换库内视频时，封面必须跟着走**：入库时若待整理里**没有同名 `.webp` 边车**，服务端会**截帧**生成 `.jpg` 封面 —— 原封面就丢了。
  替换前先把旧条目的 `.webp` 备份出来，重入时把该 `.webp` 复制到待整理、**与新视频同 stem**，`thumbnail_path` 才会是 `.webp`。
- **替换库内视频**：服务端无替换接口，`PATCH` 对 `file_path`/技术元数据静默忽略 → 只能
  「移回待整理 → `DELETE` 旧条目 → `ingest` 写回原落位」，**条目 id 会变**。
- **全库核对不要现场读代码**，跑一条命令即可：
  ```bash
  cd backend && python scripts/audit_library.py [--scope path:前缀|group:id|id:起-止] [--apply]
  ```
  明细落 `backend/out/audit/`，stdout 只有 4~5 行汇总。

---

## 7 快速自检（改完工具后必跑）

```bash
cd <项目>/tools/ingest
python regress.py 20261015-kara-honey 3293 3333   # 41 条 no-ytid 规则路径回归
python migrate_v50.py                             # 33 条 ytid 路径回归 + 推断层回归
```

两者都应输出「**逐字一致 / 实质差异 0**」。任何差异都说明改了规则但没同步守卫。

---

## 8 把工具搬进本项目时的清单

目标：`<项目>/tools/ingest/`（与本 skill 同级）。**只复制代码与批次样例，不要复制派生产物。**

| 复制 | 说明 |
|---|---|
| `imflow.py` / `flowlib.py` / `rules.py` / `rules_ext.py` / `imtools.py` | 代码 |
| `im.py` | API 客户端 —— ⚠️ **见下方密钥说明** |
| `regress.py` / `seed_archived.py` | 回归与回填工具 |
| `batches/*.json` | 批次样例（可直接当模板） |
| `README.md` | 工具说明（与本 skill 互为索引） |

| 不要复制 | 原因 |
|---|---|
| `out/` `snap/` `docs/` `__pycache__/` | 派生产物，体积大且可再生 |
| `library_cache.json` `scan_cache.json` | 本地缓存，含全库快照 |

### ⚠️ 密钥（务必先定）

`im.py` 里 `API_TOKEN = os.environ.get("IDOLMATRIX_API_TOKEN") or "<硬编码 token>"`。
**本项目是 git 仓库，硬编码 token 不能进版本库。** 迁移时二选一：

1. **推荐**：把 `im.py` 的硬编码值清空（`or ""`），token 放 **`.env.local`**（本项目 `.gitignore`
   只忽略了 `.env.local`，**`.env` 并没有被忽略，别放那里**），启动前
   `export IDOLMATRIX_API_TOKEN=...`；
2. 或把 `tools/ingest/im.py` 加进 `.gitignore`，仓库里只放 `im.py.example`。

### 已知待办（迁移后应尽快做）

`rules.py` 的 `CAMRE` / `CAM_DESC` 与 `backend/app/core/vocab.py` 的 `CAMERA_RULES` /
`CAMERA_LABELS` 是**两份同构定义**（后者注释里已写明「imflow 侧应从同一份定义读取」）。
建议迁移后让 `flowlib` 直接 `import` `app.core.vocab`，消除漂移风险 ——
改完**必须重跑第 7 节的两条回归**。
