---
name: blackbox-probe
description: 增加外部黑盒探针时使用；仅在完成范围确认、可回滚修复和终态验证的场景触发，不用于纯咨询或未解决故障。
---

# Workflow

1. 确认目标环境、影响范围、前置条件和回滚点。
2. 执行并记录证据：`promtool check config blackbox.yml`。
3. 执行并记录证据：`apply probe for https://checkout/health from three regions`。
4. 执行并记录证据：`probecheck checkout --regions ap-sg,eu-fr,us-va`。
5. 运行独立健康检查；失败则回滚并保留诊断证据。

# Verified branches

- rollback verification hardening for blackbox-probe

# Safety and validation

- 禁止未限定范围的破坏性操作，禁止记录凭据。
- 只有终态检查通过才宣告完成；否则停止、回滚并继续诊断。

<!-- gold-version:2 -->
