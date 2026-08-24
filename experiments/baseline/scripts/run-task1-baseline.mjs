import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, "../../..");
const baselineRoot = resolve(scriptDir, "..");
const profile = process.env.EVAL_PROFILE || "static";
if (!["static", "adaptive_v1"].includes(profile)) throw new Error(`Invalid EVAL_PROFILE: ${profile}`);
const rawRoot = join(baselineRoot, `results/raw/task1-${profile}`);
const captureRoot = join(baselineRoot, "results/raw/llm-capture");
const captureIndexPath = join(captureRoot, "index.jsonl");
mkdirSync(rawRoot, { recursive: true });
mkdirSync(join(baselineRoot, "results"), { recursive: true });

const userKey = process.env.TDAI_BASELINE_USER_KEY;
if (!userKey) throw new Error("TDAI_BASELINE_USER_KEY is required");
const nodePath = process.env.WORKBUDDY_NODE || join(process.env.USERPROFILE, ".workbuddy/binaries/node/versions/22.22.2/node.exe");
const cliPath = process.env.WORKBUDDY_CLI || "D:/WorkBuddy/resources/app.asar.unpacked/cli/dist/codebuddy.js";
const cases = JSON.parse(readFileSync(join(baselineRoot, "cases/task1-cases.json"), "utf8"));
const startedAt = new Date().toISOString();
const reuseRaw = process.env.BASELINE_REUSE_RAW === "true";

function cloudCalls(messages) {
  const calls = [];
  for (const message of messages) {
    if (message?.type !== "function_call") continue;
    const text = `${message.arguments || ""} ${message.providerData?.argumentsDisplayText || ""}`;
    let category = null;
    if (text.includes("/skill-bridge/")) category = "skill";
    else if (text.includes("/memory-bridge/")) category = "memory";
    else if (text.includes(":8430/tools/")) category = "knowledge";
    if (category) calls.push({ category, name: message.name, arguments: message.arguments });
  }
  return calls;
}

const rows = [];
for (let i = 0; i < cases.length; i++) {
  const test = cases[i];
  const rawPath = join(rawRoot, `${test.id}.json`);
  let sessionId = `t1-${test.id}-${Date.now().toString(36)}`;
  let run;
  if (!reuseRaw || !existsSync(rawPath)) run = spawnSync(nodePath, [
    cliPath, "-p", "--output-format", "json", "--model", "glm-5.1",
    "--tools", "default", "--permission-mode", "bypassPermissions",
    "--max-turns", "10", "--no-session-persistence", "--session-id", sessionId,
    test.prompt,
  ], {
    cwd: repoRoot,
    encoding: "utf8",
    timeout: 240_000,
    maxBuffer: 32 * 1024 * 1024,
    env: {
      ...process.env,
      CODEBUDDY_BASE_URL: "http://127.0.0.1:8096/workbuddy/default",
      CODEBUDDY_API_KEY: userKey,
      CODEBUDDY_CODE_API_KEY: userKey,
    },
  });
  const raw = run ? (run.stdout || run.stderr || "") : readFileSync(rawPath, "utf8");
  if (run) writeFileSync(rawPath, raw);
  if (!run) {
    const rawSession = raw.match(new RegExp(`t1-${test.id}-[a-z0-9]+`))?.[0];
    const captureSession = existsSync(captureIndexPath)
      ? readFileSync(captureIndexPath, "utf8").match(new RegExp(`t1-${test.id}-[a-z0-9]+`, "g"))?.at(-1)
      : null;
    sessionId = rawSession || captureSession || sessionId;
  }
  let messages = [];
  try { messages = JSON.parse(raw); } catch { /* recorded as failed below */ }
  const result = messages.find((message) => message?.type === "result");
  const calls = cloudCalls(messages);
  const categories = [...new Set(calls.map((call) => call.category))];
  const expectedCalled = test.expected === "none" ? categories.length === 0 : categories.includes(test.expected);
  const wrongCategories = categories.filter((category) => category !== test.expected);
  rows.push({
    id: test.id,
    expected: test.expected,
    session_id: sessionId,
    process_exit_code: run?.status ?? null,
    success: result?.subtype === "success" && result?.is_error !== true,
    expected_called: expectedCalled,
    correct_selection: expectedCalled && wrongCategories.length === 0,
    categories,
    cloud_call_count: calls.length,
    turns: result?.num_turns ?? null,
    input_tokens: result?.usage?.input_tokens ?? null,
    output_tokens: result?.usage?.output_tokens ?? null,
    cache_read_tokens: result?.usage?.cache_read_input_tokens ?? null,
    duration_ms: result?.duration_ms ?? null,
  });
  process.stdout.write(`[task1] ${i + 1}/${cases.length} ${test.id}: ${rows.at(-1).success ? "ok" : "failed"}, calls=${categories.join(",") || "none"}\n`);
}

const requireFromCore = createRequire(join(repoRoot, "MemoryCore/package.json"));
const { getEncoding } = requireFromCore("js-tiktoken");
const encoding = getEncoding("cl100k_base");
const captureIndex = readFileSync(captureIndexPath, "utf8").trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);

function systemText(body) {
  return (body.messages || []).filter((m) => m.role === "system").map((m) => typeof m.content === "string" ? m.content : "").join("\n");
}
function extract(text, start, end) {
  const from = text.indexOf(start);
  if (from < 0) return "";
  const to = text.indexOf(end, from);
  return text.slice(from, to < 0 ? text.length : to + end.length);
}
const markers = {
  skill_tools: ["<skill_tools>", "</skill_tools>"],
  available_skills: ["## Skills (mandatory)", "</available_skills>"],
  memory_tools: ["<tdai_memory_tools>", "</tdai_memory_tools>"],
  memory_guide: ["<memory-tools-guide>", "</memory-tools-guide>"],
  profile_memory: ["<tdai_profile_memory>", "</tdai_profile_memory>"],
  knowledge_tools: ["<knowledge_tools>", "</knowledge_tools>"],
};

for (const row of rows) {
  const captures = captureIndex.filter((entry) => String(entry.session_id || "").endsWith(row.session_id));
  const first = captures[0];
  if (!first) continue;
  const body = JSON.parse(readFileSync(join(captureRoot, `${first.request_id}.json`), "utf8"));
  const system = systemText(body);
  const blocks = {};
  let combined = "";
  for (const [name, [start, end]] of Object.entries(markers)) {
    const value = extract(system, start, end);
    blocks[name] = { chars: value.length, tokens: value ? encoding.encode(value).length : 0 };
    combined += value;
  }
  row.injection = {
    system_chars: system.length,
    total_chars: Object.values(blocks).reduce((sum, block) => sum + block.chars, 0),
    total_tokens: Object.values(blocks).reduce((sum, block) => sum + block.tokens, 0),
    hash: createHash("sha256").update(combined).digest("hex"),
    blocks,
  };
}
if (typeof encoding.free === "function") encoding.free();

const positives = rows.filter((row) => row.expected !== "none");
const negatives = rows.filter((row) => row.expected === "none");
const withInjection = rows.filter((row) => row.injection);
const hashes = new Map();
for (const row of withInjection) hashes.set(row.injection.hash, (hashes.get(row.injection.hash) || 0) + 1);
const avg = (items, field) => items.length ? items.reduce((sum, item) => sum + (item[field] || 0), 0) / items.length : 0;
const summary = {
  schema_version: 1,
  routing_profile: profile,
  started_at: startedAt,
  completed_at: new Date().toISOString(),
  agent: "WorkBuddy CLI",
  requested_model: "glm-5.1",
  upstream_model: "qwen3.7-plus",
  cases: rows.length,
  positive_cases: positives.length,
  negative_cases: negatives.length,
  success_rate: rows.filter((row) => row.success).length / rows.length,
  effective_call_rate: positives.filter((row) => row.expected_called).length / positives.length,
  correct_tool_selection_rate: positives.filter((row) => row.correct_selection).length / positives.length,
  false_call_rate: negatives.filter((row) => row.categories.length > 0).length / negatives.length,
  avg_input_tokens: avg(rows, "input_tokens"),
  avg_turns: avg(rows, "turns"),
  injection: {
    avg_chars: withInjection.reduce((sum, row) => sum + row.injection.total_chars, 0) / withInjection.length,
    avg_tokens_cl100k: withInjection.reduce((sum, row) => sum + row.injection.total_tokens, 0) / withInjection.length,
    unique_prefix_hashes: hashes.size,
    prefix_stability_rate: Math.max(...hashes.values()) / withInjection.length,
  },
  rows,
};

const resultStem = `task1-${profile}`;
writeFileSync(join(baselineRoot, `results/${resultStem}.json`), JSON.stringify(summary, null, 2));
const pct = (value) => `${(value * 100).toFixed(1)}%`;
const markdown = `# Task 1 — ${profile}\n\n` +
  `- Cases: ${summary.cases} (${summary.positive_cases} relevant, ${summary.negative_cases} negative)\n` +
  `- Run success: ${pct(summary.success_rate)}\n` +
  `- Effective call rate: ${pct(summary.effective_call_rate)}\n` +
  `- Correct tool selection: ${pct(summary.correct_tool_selection_rate)}\n` +
  `- False call rate: ${pct(summary.false_call_rate)}\n` +
  `- Average injected size: ${summary.injection.avg_tokens_cl100k.toFixed(0)} cl100k tokens / ${summary.injection.avg_chars.toFixed(0)} chars\n` +
  `- Prefix stability: ${pct(summary.injection.prefix_stability_rate)} (${summary.injection.unique_prefix_hashes} unique injection hashes)\n` +
  `- Average total input tokens: ${summary.avg_input_tokens.toFixed(0)}\n` +
  `- Average turns: ${summary.avg_turns.toFixed(1)}\n`;
writeFileSync(join(baselineRoot, `results/${resultStem}.md`), markdown);
process.stdout.write(`${markdown}\n`);
