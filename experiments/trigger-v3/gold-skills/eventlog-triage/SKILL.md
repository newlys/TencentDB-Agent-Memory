---
name: eventlog-triage
description: 定位并恢复 Windows 应用崩溃时使用；仅在完成范围确认、可回滚修复和终态验证的场景触发，不用于纯咨询或未解决故障。
---

# Workflow

1. 确认目标环境、影响范围、前置条件和回滚点。
2. 执行并记录证据：`Get-WinEvent -FilterHashtable @{LogName='Application';Id=1000} -MaxEvents 5`。
3. 执行并记录证据：`Repair-WindowsFeature VC-Runtime; Restart-Service ContosoApi`。
4. 执行并记录证据：`Test-NetConnection localhost -Port 8181`。
5. 运行独立健康检查；失败则回滚并保留诊断证据。

# Verified branches

- new-version compatibility check for eventlog-triage
- stale coordination-state cleanup for eventlog-triage

# Safety and validation

- 禁止未限定范围的破坏性操作，禁止记录凭据。
- 只有终态检查通过才宣告完成；否则停止、回滚并继续诊断。

<!-- gold-version:3 -->
