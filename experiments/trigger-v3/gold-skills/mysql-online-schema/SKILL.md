---
name: mysql-online-schema
description: 执行 MySQL 在线表结构变更时使用；仅在完成范围确认、可回滚修复和终态验证的场景触发，不用于纯咨询或未解决故障。
---

# Workflow

1. 确认目标环境、影响范围、前置条件和回滚点。
2. 执行并记录证据：`pt-online-schema-change --alter 'ADD COLUMN trace_id varchar(64)' D=app,t=requests --dry-run`。
3. 执行并记录证据：`pt-online-schema-change --alter 'ADD COLUMN trace_id varchar(64)' D=app,t=requests --execute`。
4. 执行并记录证据：`mysql -e 'SHOW COLUMNS FROM app.requests LIKE "trace_id"'`。
5. 运行独立健康检查；失败则回滚并保留诊断证据。

# Verified branches

- new-version compatibility check for mysql-online-schema
- stale coordination-state cleanup for mysql-online-schema

# Safety and validation

- 禁止未限定范围的破坏性操作，禁止记录凭据。
- 只有终态检查通过才宣告完成；否则停止、回滚并继续诊断。

<!-- gold-version:3 -->
