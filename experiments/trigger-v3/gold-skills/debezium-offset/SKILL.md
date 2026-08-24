---
name: debezium-offset
description: 修复 Debezium offset 不一致时使用；仅在完成范围确认、可回滚修复和终态验证的场景触发，不用于纯咨询或未解决故障。
---

# Workflow

1. 确认目标环境、影响范围、前置条件和回滚点。
2. 执行并记录证据：`connectorctl status inventory`。
3. 执行并记录证据：`verify snapshot window && connectorctl restart inventory --snapshot-mode when_needed`。
4. 执行并记录证据：`connectorctl wait inventory --caught-up && rowcount-compare inventory`。
5. 运行独立健康检查；失败则回滚并保留诊断证据。

# Verified branches

- rollback verification hardening for debezium-offset

# Safety and validation

- 禁止未限定范围的破坏性操作，禁止记录凭据。
- 只有终态检查通过才宣告完成；否则停止、回滚并继续诊断。

<!-- gold-version:2 -->
