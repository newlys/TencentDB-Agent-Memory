# Custom longitudinal benchmark

The reusable engine lives in:

```text
experiments/click-benchmark/
experiments/longitudinal-benchmark/
experiments/query-boundary-l15-v1/
```

The published Custom A package includes all three frozen suites: pinned source
URLs/commits, task definitions, damage and Gold patches, hidden graders,
Oracle responses, reset metadata, Dockerfiles, protocols, and manifests. The
bootstrap script clones each upstream repository at its exact commit and builds
the locked local images. API keys and generated runtime data are not included.

With Docker Desktop, Python 3.10+, Git, and a DeepSeek key, the
complete source bootstrap, image build, contract preflight, and background Ours
launch is one command from the repository root:

```powershell
$env:DEEPSEEK_API_KEY = '<your key>'
.\benchmarks\custom-platform\run.ps1 `
  -Plan .\benchmarks\custom-platform\plan.custom-a.json `
  -Run my-ours-v3-run
```

Use `-Variant no-skill`, `baseline`, or `ours_v3`.  The wrapper writes a frozen
copy of the selected plan, starts the existing background launcher, and prints
the live `progress.json` and final `result.json` paths.

After the first successful bootstrap, add `-SkipBootstrap` to reuse the cloned
repositories, installed Node dependencies, and Docker build cache.
If the host does not already provide Node.js 22, the bootstrap downloads the
fixed Node.js 22.22.2 Windows archive and verifies it against the official
SHA-256 manifest before use.

## Published Custom A assets

The executable suites are stored in-place:

```text
experiments/click-benchmark/private/
experiments/flask-benchmark/private/
experiments/fastapi-benchmark/private/
```

Each suite contains its active `manifest.json`, common `protocol.json`,
repository/image configuration, hidden grader, Gold annotations, and one
directory per Task containing `task.json`, `damage.patch`, and `gold.patch`.
The task definition includes the initial user query, deterministic grader-state
priority, predefined Oracle replies, success condition, and user-turn limit.

The source repositories themselves are deliberately not copied into this Git
repository. `bootstrap.ps1` reads each suite's public upstream URL and immutable
40-character commit, creates `repo/click`, `repo/flask`, and `repo/fastapi`, and
checks out those exact commits. Task workspaces are still independently rebuilt
and reset by the benchmark driver; only the intended Repo-level Claude and
Proxy/Memory sessions remain continuous.

The first run writes progress immediately and then continues in the background:

```text
experiments/longitudinal-benchmark/runs/<run>/progress.json
experiments/longitudinal-benchmark/runs/<run>/result.json
```

A successful launch reaches `RUNNING / SESSION_RUNNING / AGENT_RUNNING`. A full
completion has `status=COMPLETED`; task entries retain grader results, reset
verification, user/internal turns, model/tool calls, token usage, Boundary,
Extraction, Retrieval, and Skill-consumption records.

## Method/runtime boundary

The published `ours_v3` experiment uses the production MemoryCore archive,
reviewer, Skill store, search, and Proxy injection switches. Its query-only
Task Boundary and pre-agent Skill selection/materialization are intentionally
hosted by the benchmark controller in `experiments/click-benchmark/session_driver.py`.
They are not silently enabled for every ordinary `/v3/skill/conversation/add`
request. This keeps the upstream-compatible Baseline unchanged and makes the
experimental variable explicit.

Boundary failure is conservative and fail-open: the current request remains in
the active Task (`same_task`) so an unavailable memory subsystem cannot abort
the Coding Agent's task. Model and endpoint defaults can be overridden with
`BENCHMARK_NODE`, `BENCHMARK_OPENAI_BASE_URL`, `BENCHMARK_ANTHROPIC_BASE_URL`,
`BENCHMARK_SELECTOR_MODEL`, and `BENCHMARK_EXTRACTION_MODEL`.

## Published Skill artifacts

The active Skill heads produced by the reported Custom A Ours_v3 run are
preserved as ordinary Markdown under:

```text
benchmarks/custom-platform/artifacts/custom-a-ours-v3-r5/skills/
```

`manifest.json` records the original Skill IDs, versions, content hashes, and
source run. The reported artifacts remain labelled `r5`; the one-click plan is
`r6`, which generalizes the prompts and preserves executable Markdown structure
during Skill consumption without changing the Boundary or extraction lifecycle.
These files are observational experiment outputs; they are not
preloaded by the one-click evaluation. To export another completed run:

```powershell
python .\benchmarks\custom-platform\export_skills.py `
  --database <run>\ours_v3\core-data\vectors.db `
  --output <destination> `
  --run-id <run-name>
```
