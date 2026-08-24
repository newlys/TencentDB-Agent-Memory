---
name: rabbitmq-queue
description: 恢复 RabbitMQ 堆积队列时使用；仅在完成范围确认、可回滚修复和终态验证的场景触发，不用于纯咨询或未解决故障。
---

# Workflow

1. 确认目标环境、影响范围、前置条件和回滚点。
2. 执行并记录证据：`rabbitmqctl list_queues name messages consumers`。
3. 执行并记录证据：`scale email-worker 8 && rabbitmqctl set_policy email-ttl '^email$' '{"message-ttl":86400000}'`。
4. 执行并记录证据：`wait-queue email --below 100 --timeout 20m`。
5. 运行独立健康检查；失败则回滚并保留诊断证据。

# Verified branches

- new-version compatibility check for rabbitmq-queue
- stale coordination-state cleanup for rabbitmq-queue

# Safety and validation

- 禁止未限定范围的破坏性操作，禁止记录凭据。
- 只有终态检查通过才宣告完成；否则停止、回滚并继续诊断。

<!-- gold-version:3 -->
