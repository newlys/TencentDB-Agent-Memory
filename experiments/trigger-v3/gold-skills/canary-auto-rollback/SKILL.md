---
name: canary-auto-rollback
description: 验证金丝雀自动回滚时使用；仅在完成范围确认、可回滚修复和终态验证的场景触发，不用于纯咨询或未解决故障。
---

# Workflow

1. 确认目标环境、影响范围、前置条件和回滚点。
2. 执行并记录证据：`deployctl canary checkout:v41 --traffic 10`。
3. 执行并记录证据：`sloctl watch checkout --window 10m`。
4. 执行并记录证据：`deployctl status checkout && curl -fsS https://checkout/health`。
5. 运行独立健康检查；失败则回滚并保留诊断证据。

# Verified branches

- new-version compatibility check for canary-auto-rollback
- stale coordination-state cleanup for canary-auto-rollback

# Safety and validation

- 禁止未限定范围的破坏性操作，禁止记录凭据。
- 只有终态检查通过才宣告完成；否则停止、回滚并继续诊断。

<!-- gold-version:3 -->
