# Custom longitudinal benchmark

The reusable engine lives in:

```text
experiments/click-benchmark/
experiments/longitudinal-benchmark/
experiments/query-boundary-l15-v1/
```

Task data is intentionally distributed separately.  Import packages must
provide a pinned repository, task definitions, damage and Gold patches, hidden
grader, Oracle responses, reset metadata, and a suite manifest as documented in
`experiments/click-benchmark/BENCHMARK_PLATFORM_GUIDE.md`.

After placing the imported suites and editing `plan.example.json`, the complete
preflight + background launch is one command:

```powershell
.\benchmarks\custom-platform\run.ps1 `
  -Plan .\benchmarks\custom-platform\plan.example.json `
  -Run my-ours-v3-run
```

Use `-Variant no-skill`, `baseline`, or `ours_v3`.  The wrapper writes a frozen
copy of the selected plan, starts the existing background launcher, and prints
the live `progress.json` and final `result.json` paths.
