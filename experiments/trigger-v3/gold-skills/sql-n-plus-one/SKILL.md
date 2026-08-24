---
name: sql-n-plus-one
description: 修复 ORM N+1 查询时使用；仅在完成范围确认、可回滚修复和终态验证的场景触发，不用于纯咨询或未解决故障。
---

# Workflow

1. 确认目标环境、影响范围、前置条件和回滚点。
2. 执行并记录证据：`request-profile GET /orders`。
3. 执行并记录证据：`add eager loading for order.items and rerun profile`。
4. 执行并记录证据：`integration-test orders --assert-query-count 3`。
5. 运行独立健康检查；失败则回滚并保留诊断证据。

# Verified branches

- rollback verification hardening for sql-n-plus-one

# Safety and validation

- 禁止未限定范围的破坏性操作，禁止记录凭据。
- 只有终态检查通过才宣告完成；否则停止、回滚并继续诊断。

<!-- gold-version:2 -->
