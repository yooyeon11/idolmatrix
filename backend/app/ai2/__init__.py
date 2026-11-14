"""ai2 — idolMatrix 内置 AI 子系统（2026-10-21 重建）。

分层（见方案文档 idolmatrix-ai-rebuild-plan.md）：
- llm.py        provider 无关的 LLM 客户端，支持步骤级模型覆盖
- registry.py   工具白名单注册表（LLM 只能调用注册过的工具）
- knowledge.py  知识层（skills / memory / reports / rules_pending，文件即真相）
- tasks/        任务模板：硬编码步骤序列，LLM 只在步骤内做判断
- tools/        具体工具实现（只读为主，写库工具必须经 service 层）

设计红线：
1. LLM 不允许生成 SQL / 直写数据库；一切写操作经白名单工具。
2. 核验步与执行步上下文隔离（独立 LLM 调用，只给原始数据）。
3. 规则/工作流变更必须走 rules_pending 人工审批闸。
"""

__all__ = ["llm", "registry", "knowledge"]
