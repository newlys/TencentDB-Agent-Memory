---
name: iis-cert-binding
description: 轮换 IIS HTTPS 证书绑定时使用；仅在完成范围确认、可回滚修复和终态验证的场景触发，不用于纯咨询或未解决故障。
---

# Workflow

1. 确认目标环境、影响范围、前置条件和回滚点。
2. 执行并记录证据：`Get-ChildItem Cert:\LocalMachine\My | Where Subject -Match api`。
3. 执行并记录证据：`Set-WebBinding -Name Api -BindingInformation '*:443:api.example' -PropertyName certificateHash -Value NEW123`。
4. 执行并记录证据：`Invoke-WebRequest https://api.example/health`。
5. 运行独立健康检查；失败则回滚并保留诊断证据。

# Verified branches

- new-version compatibility check for iis-cert-binding
- stale coordination-state cleanup for iis-cert-binding

# Safety and validation

- 禁止未限定范围的破坏性操作，禁止记录凭据。
- 只有终态检查通过才宣告完成；否则停止、回滚并继续诊断。

<!-- gold-version:3 -->
