# Benchmark Platform

这里同时包含可复用测评引擎和第一个 Click suite。完整的导入、验证、启动、进度与结果说明见
[BENCHMARK_PLATFORM_GUIDE.md](BENCHMARK_PLATFORM_GUIDE.md)。

最短的无付费预检：

```powershell
python experiments/click-benchmark/validate_import.py --suite experiments/click-benchmark
python -m unittest discover -s experiments/click-benchmark/tests -v
```

当前支持两个真实连续会话 variant：`no-skill` 与 TencentDB-Agent-Memory 原生 `baseline`。
`ours` 仅预留扩展位，待方法确定后再接入。正式请求均为英文。
