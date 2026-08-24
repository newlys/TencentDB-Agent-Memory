import { spawnSync } from "node:child_process";
import { cpSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, "../../..");
const baselineRoot = resolve(scriptDir, "..");
const fixturesRoot = join(baselineRoot, "cases/task2");
const profile = process.env.EVAL_PROFILE || "static";
if (!["static", "adaptive_v1", "sop_v1"].includes(profile)) throw new Error(`Invalid EVAL_PROFILE: ${profile}`);
const runId = `task2-${profile}-${Date.now().toString(36)}`;
const workRoot = join(baselineRoot, "results/raw/task2-workdirs", runId);
const rawRoot = join(baselineRoot, "results/raw/task2", runId);
mkdirSync(workRoot, { recursive: true });
mkdirSync(rawRoot, { recursive: true });
mkdirSync(join(baselineRoot, "results"), { recursive: true });

const userKey = process.env.TDAI_BASELINE_USER_KEY;
if (!userKey) throw new Error("TDAI_BASELINE_USER_KEY is required");
const nodePath = process.env.WORKBUDDY_NODE || join(process.env.USERPROFILE, ".workbuddy/binaries/node/versions/22.22.2/node.exe");
const cliPath = process.env.WORKBUDDY_CLI || "D:/WorkBuddy/resources/app.asar.unpacked/cli/dist/codebuddy.js";
const caseIds = ["slugify", "retry", "lru-cache", "merge-intervals", "parse-duration", "config-merge"];
const startedAt = new Date().toISOString();

async function skillCount() {
  const response = await fetch("http://127.0.0.1:8420/v3/skill/list", {
    method: "POST",
    headers: { authorization: "Bearer local-dev-memory-key", "x-tdai-service-id": "default", "content-type": "application/json" },
    body: JSON.stringify({ team_id: "team-dyf7fb74wi", agent_id: "agt-dyf7zr5fjh", limit: 1, offset: 0 }),
  });
  const body = await response.json();
  return body.data?.total ?? null;
}

function extractCloudCalls(messages) {
  const calls = [];
  for (const message of messages) {
    if (message?.type !== "function_call") continue;
    const text = `${message.arguments || ""} ${message.providerData?.argumentsDisplayText || ""}`;
    if (text.includes("/skill-bridge/")) calls.push({ category: "skill", text });
    else if (text.includes("/memory-bridge/")) calls.push({ category: "memory", text });
    else if (text.includes(":8430/tools/")) calls.push({ category: "knowledge", text });
  }
  return calls;
}

const skillCountBefore = await skillCount();
const rows = [];
for (let i = 0; i < caseIds.length; i++) {
  const id = caseIds[i];
  const source = join(fixturesRoot, id);
  const cwd = join(workRoot, id);
  cpSync(source, cwd, { recursive: true });
  const pretest = spawnSync(nodePath, ["--test"], { cwd, encoding: "utf8", timeout: 30_000 });
  const sessionId = `t2-${id}-${Date.now().toString(36)}`;
  const prompt = "修复当前目录中的实现，使全部 node --test 测试通过。不要修改测试文件。请自主完成：先检查代码和测试，实施最小正确修复，最后运行测试验证。";
  const run = spawnSync(nodePath, [
    cliPath, "-p", "--output-format", "json", "--model", "glm-5.1",
    "--tools", "default", "--permission-mode", "bypassPermissions",
    "--max-turns", "20", "--no-session-persistence", "--session-id", sessionId, prompt,
  ], {
    cwd, encoding: "utf8", timeout: 480_000, maxBuffer: 64 * 1024 * 1024,
    env: { ...process.env, CODEBUDDY_BASE_URL: "http://127.0.0.1:8096/workbuddy/default", CODEBUDDY_API_KEY: userKey, CODEBUDDY_CODE_API_KEY: userKey },
  });
  const raw = run.stdout || run.stderr || "";
  writeFileSync(join(rawRoot, `${id}.json`), raw);
  let messages = [];
  try { messages = JSON.parse(run.stdout); } catch { /* keep failure */ }
  const result = messages.find((message) => message?.type === "result");
  const calls = extractCloudCalls(messages);
  const posttest = spawnSync(nodePath, ["--test"], { cwd, encoding: "utf8", timeout: 30_000 });
  writeFileSync(join(rawRoot, `${id}.test.txt`), `${posttest.stdout || ""}${posttest.stderr || ""}`);
  rows.push({
    id, session_id: sessionId,
    initially_failing: pretest.status !== 0,
    pass: posttest.status === 0,
    agent_success: result?.subtype === "success" && result?.is_error !== true,
    process_exit_code: run.status,
    skill_hit: calls.some((call) => call.category === "skill"),
    cloud_categories: [...new Set(calls.map((call) => call.category))],
    cloud_call_count: calls.length,
    turns: result?.num_turns ?? null,
    input_tokens: result?.usage?.input_tokens ?? null,
    output_tokens: result?.usage?.output_tokens ?? null,
    cache_read_tokens: result?.usage?.cache_read_input_tokens ?? null,
    duration_ms: result?.duration_ms ?? null,
  });
  process.stdout.write(`[task2] ${i + 1}/${caseIds.length} ${id}: tests=${rows.at(-1).pass ? "pass" : "fail"}, skill=${rows.at(-1).skill_hit ? "hit" : "miss"}\n`);
}

// Allow the local extraction queue to consume any archive created by the last case.
await new Promise((resolveWait) => setTimeout(resolveWait, 15_000));
const completedAt = new Date().toISOString();
const skillCountAfter = await skillCount();
const coreLog = readFileSync(join(baselineRoot, "runtime/core.stdout.log"), "utf8");
for (const row of rows) {
  const escaped = row.session_id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  row.archive_triggered = new RegExp(`session_id: '${escaped}'[\\s\\S]{0,4000}?archived: true`).test(coreLog);
}

const captures = readFileSync(join(baselineRoot, "results/raw/llm-capture/index.jsonl"), "utf8")
  .trim().split(/\r?\n/).filter(Boolean).map(JSON.parse)
  .filter((entry) => entry.at >= startedAt && entry.at <= completedAt);
const skillExtractionCaptures = captures.filter((entry) => entry.kind === "skill_extraction");
let distillationInputTokensLowerBound = 0;
for (const entry of skillExtractionCaptures) {
  const rawBody = readFileSync(join(baselineRoot, "results/raw/llm-capture", `${entry.request_id}.json`), "utf8");
  // Conservative language-independent lower bound used only when an extraction
  // request exists. Exact provider usage is unavailable on the Core path.
  distillationInputTokensLowerBound += Math.ceil(rawBody.length / 4);
}

const avg = (field) => rows.reduce((sum, row) => sum + (row[field] || 0), 0) / rows.length;
const agentTokens = rows.reduce((sum, row) => sum + (row.input_tokens || 0) + (row.output_tokens || 0), 0);
const summary = {
  schema_version: 1,
  routing_profile: profile,
  run_id: runId,
  started_at: startedAt,
  completed_at: completedAt,
  agent: "WorkBuddy CLI",
  requested_model: "glm-5.1",
  upstream_model: "qwen3.7-plus",
  cases: rows.length,
  pass_at_1: rows.filter((row) => row.pass).length / rows.length,
  agent_completion_rate: rows.filter((row) => row.agent_success).length / rows.length,
  skill_hit_rate: rows.filter((row) => row.skill_hit).length / rows.length,
  archive_trigger_rate: rows.filter((row) => row.archive_triggered).length / rows.length,
  skill_count_before: skillCountBefore,
  skill_count_after: skillCountAfter,
  extracted_skill_delta: skillCountAfter - skillCountBefore,
  avg_turns: avg("turns"),
  avg_agent_input_tokens: avg("input_tokens"),
  avg_agent_output_tokens: avg("output_tokens"),
  total_agent_tokens: agentTokens,
  skill_distillation_requests: skillExtractionCaptures.length,
  skill_distillation_input_tokens_lower_bound: distillationInputTokensLowerBound,
  avg_total_tokens_including_skill_distillation_lower_bound: (agentTokens + distillationInputTokensLowerBound) / rows.length,
  rows,
};
const resultStem = `task2-${profile}`;
writeFileSync(join(baselineRoot, `results/${resultStem}.json`), JSON.stringify(summary, null, 2));
const pct = (value) => `${(value * 100).toFixed(1)}%`;
const markdown = `# Task 2 — ${profile}\n\n` +
  `- Coding cases: ${summary.cases}\n` +
  `- pass@1: ${pct(summary.pass_at_1)}\n` +
  `- Agent completion rate: ${pct(summary.agent_completion_rate)}\n` +
  `- Skill hit rate: ${pct(summary.skill_hit_rate)}\n` +
  `- Archive trigger rate: ${pct(summary.archive_trigger_rate)}\n` +
  `- Extracted Skill delta: ${summary.extracted_skill_delta}\n` +
  `- Average turns: ${summary.avg_turns.toFixed(1)}\n` +
  `- Average agent input/output tokens: ${summary.avg_agent_input_tokens.toFixed(0)} / ${summary.avg_agent_output_tokens.toFixed(0)}\n` +
  `- Average total tokens including observed Skill distillation (lower bound): ${summary.avg_total_tokens_including_skill_distillation_lower_bound.toFixed(0)}\n`;
writeFileSync(join(baselineRoot, `results/${resultStem}.md`), markdown);
process.stdout.write(`${markdown}\n`);
