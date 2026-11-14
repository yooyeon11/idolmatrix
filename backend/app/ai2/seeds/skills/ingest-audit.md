# 入库清单核对工作流（P3 落地，本文件为种子版）

## 步骤序列（硬编码于任务模板 ingest_audit）

1. 读记忆与本工作流。
2. 子进程调用 `G:\coding\idolmatrix-ingest` 的 `imflow.py verify` / `regress`（规则单一来源，不重新实现）。
3. LLM（analyze 步）只做差异归因与建议，**不碰入库写操作**。
4. `consistency.py` 核对同活动/同放送局条目的命名一致性。
5. 生成核对报告 HTML 落 `reports/`；发现新规则写 `rules_pending/` 草案等人工确认。

## 红线

- 核对任务默认只读；任何写操作需要显式确认。
- 规则修订永远走人工审批闸，agent 只能起草。
