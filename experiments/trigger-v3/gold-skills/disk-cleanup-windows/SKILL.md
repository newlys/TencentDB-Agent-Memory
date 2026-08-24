---
name: disk-cleanup-windows
description: 安全清理 Windows 临时文件时使用；仅在完成范围确认、可回滚修复和终态验证的场景触发，不用于纯咨询或未解决故障。
---

# Workflow

1. 确认目标环境、影响范围、前置条件和回滚点。
2. 执行并记录证据：`Get-PSDrive C`。
3. 执行并记录证据：`Get-ChildItem C:\Windows\Temp -File | Where LastWriteTime -lt (Get-Date).AddDays(-14) | Remove-Item -Force`。
4. 执行并记录证据：`Get-PSDrive C; Get-Service ContosoApi`。
5. 运行独立健康检查；失败则回滚并保留诊断证据。

# Verified branches

- new-version compatibility check for disk-cleanup-windows
- stale coordination-state cleanup for disk-cleanup-windows

# Safety and validation

- 禁止未限定范围的破坏性操作，禁止记录凭据。
- 只有终态检查通过才宣告完成；否则停止、回滚并继续诊断。

<!-- gold-version:3 -->
