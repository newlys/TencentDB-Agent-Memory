---
name: ipv6-dualstack
description: 为服务启用 IPv6 双栈时使用；仅在完成范围确认、可回滚修复和终态验证的场景触发，不用于纯咨询或未解决故障。
---

# Workflow

1. 确认目标环境、影响范围、前置条件和回滚点。
2. 执行并记录证据：`networkctl inspect service-net`。
3. 执行并记录证据：`networkctl enable-ipv6 service-net 2001:db8:40::/64 && rollout gateway`。
4. 执行并记录证据：`curl -6 -fsS https://api.example/health && curl -4 -fsS https://api.example/health`。
5. 运行独立健康检查；失败则回滚并保留诊断证据。

# Verified branches

- new-version compatibility check for ipv6-dualstack
- stale coordination-state cleanup for ipv6-dualstack

# Safety and validation

- 禁止未限定范围的破坏性操作，禁止记录凭据。
- 只有终态检查通过才宣告完成；否则停止、回滚并继续诊断。

<!-- gold-version:3 -->
