# 艺人/组合添加工作流（P2 落地，本文件为种子版）

## 步骤序列（硬编码于任务模板 artist_create）

1. 读 `memory/MEMORY.md` 与本工作流，注入上下文（超 3000 字触发蒸馏）。
2. LLM（draft 步）按艺人名生成采集计划：要查哪些字段、哪些信源。
3. 只读工具取数：`catalog.search_entities`（查重）+ QQ 音乐信源。
4. `consistency.py` 库内一致性步：检索同类条目，统计命名惯例作为硬约束。
5. LLM 组装实体草稿（JSON Schema 强校验；命名不符库内惯例由确定性代码打回）。
6. 独立核验步（verify）：只给原始信源数据，不给草稿上下文，逐字段核对。
7. 写入（经 service 层校验，dry-run 预览可确认）。
8. 生成核对清单 + 变更报告 HTML 落 `reports/`。
9. 回写记忆（分级：fact 自动 / conclusion 带证据 / method 走 rules_pending）。

## 红线

- LLM 不得直写库；写入仅经白名单工具。
- 缺字段宁可 null，严禁编造。
- 命名规范以库内现有同类条目为准，不以 LLM 想法为准。
