import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SKILL_REVIEW_PROMPT } from "../../../MemoryCore/src/core/skill/prompts/skill-review-prompt.js";
import { SKILL_REVIEW_PROMPT_V3 } from "../../../MemoryCore/src/core/skill/prompts/skill-review-prompt-v3.js";
import { SKILL_REVIEW_PROMPT_V4 } from "../../../MemoryCore/src/core/skill/prompts/skill-review-prompt-v4.js";

type Message = { role: string; content: string };
type ValueCase = { id: string; kind: "sop" | "background" | "preference" | "none"; shouldExtract: boolean; messages: Message[] };
type Write = { name: string; content: string; action: string };
type EvalResult = { caseId: string; kind: string; shouldExtract: boolean; wrote: boolean; writes: Write[]; finalText: string; promptTokens: number; completionTokens: number; quality: number; qualityChecks: Record<string, boolean> };

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const datasetName = process.env.TDAI_REVIEW_DATASET || "skill-value-cases.json";
const datasetStem = path.basename(datasetName, ".json");
const cases = (JSON.parse(fs.readFileSync(path.join(root, "datasets", datasetName), "utf8")) as { cases: ValueCase[] }).cases;
const apiKey = process.env.DASHSCOPE_API_KEY;
const baseUrl = (process.env.AFAC_QWEN_BASE_URL || "https://dashscope.aliyuncs.com/compatible-mode/v1").replace(/\/$/, "");
const model = process.env.TDAI_TRIGGER_JUDGE_MODEL || "qwen3.7-plus";
if (!apiKey) throw new Error("DASHSCOPE_API_KEY is required");

const positiveSampleSize = Number(process.env.TDAI_REVIEW_POSITIVES || 12);
const negativeSampleSize = Number(process.env.TDAI_REVIEW_NEGATIVES || 12);
const sample = stratifiedSample(cases, positiveSampleSize, negativeSampleSize);
const allProfiles = [
  { id: "legacy_v2", prompt: SKILL_REVIEW_PROMPT },
  { id: "precision_v3", prompt: SKILL_REVIEW_PROMPT_V3 },
  { id: "balanced_v4", prompt: SKILL_REVIEW_PROMPT_V4 },
];
const requestedProfiles = new Set((process.env.TDAI_REVIEW_PROFILES || "legacy_v2,precision_v3,balanced_v4").split(","));
const profiles = allProfiles.filter((profile) => requestedProfiles.has(profile.id));
const outputBase = datasetName === "skill-value-cases.json" ? "review-prompt-eval" : `review-prompt-eval-${datasetStem}`;
const outputPath = path.join(root, "results", `${outputBase}.json`);
const cache = fs.existsSync(outputPath) ? JSON.parse(fs.readFileSync(outputPath, "utf8")) as { results?: Record<string, EvalResult[]> } : {};
const allResults: Record<string, EvalResult[]> = cache.results ?? {};

async function main(): Promise<void> {
  for (const profile of profiles) {
    const existing = new Map((allResults[profile.id] ?? []).map((item) => [item.caseId, item]));
    for (const testCase of sample) {
      if (existing.has(testCase.id)) continue;
      const result = await runReviewer(profile.prompt, testCase);
      existing.set(testCase.id, result);
      allResults[profile.id] = [...existing.values()];
      persist();
      console.log(`${profile.id}/${testCase.id}: wrote=${result.wrote} quality=${result.quality}`);
    }
  }
  persist();
  const summary = Object.fromEntries(profiles.map((profile) => [profile.id, summarize(allResults[profile.id] ?? [])]));
  fs.writeFileSync(path.join(root, "results", `${outputBase}.md`), renderMarkdown(summary));
  console.log(renderMarkdown(summary));
}

async function runReviewer(systemPrompt: string, testCase: ValueCase): Promise<EvalResult> {
  const transcript = testCase.messages.map((message) => `<<past-${message.role}>>\n${message.content}`).join("\n\n") + "\n\n<<end-of-transcript>>";
  const messages: Array<Record<string, unknown>> = [{ role: "user", content: transcript }];
  const writes: Write[] = [];
  let finalText = "";
  let promptTokens = 0;
  let completionTokens = 0;
  for (let iteration = 0; iteration < 5; iteration++) {
    const response = await fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model, enable_thinking: false, temperature: 0, max_tokens: 1600, system: undefined, messages: [{ role: "system", content: systemPrompt }, ...messages], tools }),
    });
    if (!response.ok) throw new Error(`review HTTP ${response.status}: ${(await response.text()).slice(0, 500)}`);
    const body = await response.json() as { choices?: Array<{ message?: { content?: string; tool_calls?: Array<{ id: string; type: string; function: { name: string; arguments: string } }> } }>; usage?: { prompt_tokens?: number; completion_tokens?: number } };
    promptTokens += body.usage?.prompt_tokens ?? 0;
    completionTokens += body.usage?.completion_tokens ?? 0;
    const assistant = body.choices?.[0]?.message ?? {};
    messages.push({ role: "assistant", content: assistant.content ?? "", tool_calls: assistant.tool_calls });
    const toolCalls = assistant.tool_calls ?? [];
    if (toolCalls.length === 0) {
      finalText = assistant.content ?? "";
      break;
    }
    for (const call of toolCalls) {
      const args = safeJson(call.function.arguments);
      const result = executeTool(call.function.name, args, testCase, writes);
      messages.push({ role: "tool", tool_call_id: call.id, content: JSON.stringify(result) });
    }
  }
  const qualityResult = grade(testCase, writes);
  return { caseId: testCase.id, kind: testCase.kind, shouldExtract: testCase.shouldExtract, wrote: writes.length > 0, writes, finalText, promptTokens, completionTokens, quality: qualityResult.score, qualityChecks: qualityResult.checks };
}

function executeTool(name: string, args: Record<string, unknown>, testCase: ValueCase, writes: Write[]): unknown {
  const duplicate = testCase.id === "duplicate-existing";
  if (name === "skill_list") return duplicate ? [{ skill_id: "existing-1", name: "nginx-502-triage", description: "Diagnose and repair Nginx upstream 502 errors", version: 1 }] : [];
  if (name === "skill_view") return { skill_id: "existing-1", version: 1, name: "nginx-502-triage", content: "---\nname: nginx-502-triage\ndescription: Diagnose Nginx upstream 502\n---\n## Workflow\nInspect error log, check upstream, restart it, verify HTTP 200." };
  if (["skill_create", "skill_update", "skill_patch", "skill_files_write"].includes(name)) {
    const content = String(args.content ?? args.new_string ?? "");
    writes.push({ action: name, name: String(args.name ?? (duplicate ? "nginx-502-triage" : "unknown")), content });
    return { ok: true, skill_id: duplicate ? "existing-1" : `new-${writes.length}`, version: 2 };
  }
  return { error: "UNKNOWN_TOOL" };
}

function grade(testCase: ValueCase, writes: Write[]): { score: number; checks: Record<string, boolean> } {
  if (writes.length === 0) return { score: 0, checks: {} };
  const content = writes.map((write) => write.content).join("\n");
  const checks: Record<string, boolean> = {
    frontmatter: /^---[\s\S]*?name:\s*[a-z0-9-]+[\s\S]*?description:/m.test(content),
    applicability: /when to use|何时使用|适用|触发/iu.test(content),
    noSecrets: !/(?:CorrectHorseBatteryStaple|BEGIN PRIVATE KEY|sk-test-)/u.test(content),
    compactEnough: content.length > 120 && content.length < 8000,
  };
  if (testCase.kind === "sop") {
    checks.workflow = /workflow|步骤|流程/iu.test(content);
    checks.validation = /validation|验证|检查/iu.test(content);
    checks.boundary = /when not to use|不适用|不要使用/iu.test(content);
    const commands = testCase.messages.filter((message) => message.role === "tool_call").map((message) => message.content.match(/[A-Za-z0-9_.-]+/)?.[0]?.toLowerCase()).filter(Boolean) as string[];
    checks.grounded = commands.length === 0 || commands.some((command) => content.toLowerCase().includes(command));
  } else {
    checks.scoped = /scope|范围|团队|项目|when|适用/iu.test(content);
  }
  const values = Object.values(checks);
  return { score: Math.round(100 * values.filter(Boolean).length / values.length), checks };
}

function summarize(results: EvalResult[]) {
  const positives = results.filter((item) => item.shouldExtract);
  const negatives = results.filter((item) => !item.shouldExtract);
  const tp = positives.filter((item) => item.wrote).length;
  const fp = negatives.filter((item) => item.wrote).length;
  const precision = tp / Math.max(1, tp + fp);
  const recall = tp / Math.max(1, positives.length);
  return {
    cases: results.length, precision, recall,
    f1: 2 * precision * recall / Math.max(0.0001, precision + recall),
    falseExtractionRate: fp / Math.max(1, negatives.length),
    averagePositiveQuality: average(positives.filter((item) => item.wrote).map((item) => item.quality)),
    promptTokens: results.reduce((sum, item) => sum + item.promptTokens, 0),
    completionTokens: results.reduce((sum, item) => sum + item.completionTokens, 0),
  };
}

function renderMarkdown(summary: Record<string, ReturnType<typeof summarize>>): string {
  return ["# Skill reviewer prompt evaluation", "", `${sample.length}-case stratified sample (${sample.filter((item) => item.shouldExtract).length} extract / ${sample.filter((item) => !item.shouldExtract).length} no-extract), same model and tool simulator.`, "", "| Profile | Precision | Recall | F1 | False extraction | Positive quality | Prompt tokens | Completion tokens |", "|---|---:|---:|---:|---:|---:|---:|---:|", ...Object.entries(summary).map(([id, value]) => `| ${id} | ${pct(value.precision)} | ${pct(value.recall)} | ${pct(value.f1)} | ${pct(value.falseExtractionRate)} | ${value.averagePositiveQuality.toFixed(1)} | ${value.promptTokens} | ${value.completionTokens} |`), ""].join("\n");
}

function stratifiedSample(input: ValueCase[], positives: number, negatives: number): ValueCase[] {
  const pos = input.filter((item) => item.shouldExtract);
  const neg = input.filter((item) => !item.shouldExtract);
  return [...spread(pos, positives), ...spread(neg, negatives)];
}
function spread(items: ValueCase[], count: number): ValueCase[] { return Array.from({ length: Math.min(count, items.length) }, (_, index) => items[Math.floor(index * items.length / count)]!); }
function safeJson(raw: string): Record<string, unknown> { try { return JSON.parse(raw) as Record<string, unknown>; } catch { return {}; } }
function average(values: number[]): number { return values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0; }
function pct(value: number): string { return `${(value * 100).toFixed(1)}%`; }
function persist(): void { fs.writeFileSync(outputPath, `${JSON.stringify({ model, sampleIds: sample.map((item) => item.id), results: allResults }, null, 2)}\n`); }

const tools = [
  { type: "function", function: { name: "skill_list", description: "List skills; call first", parameters: { type: "object", properties: { query: { type: "string" } } } } },
  { type: "function", function: { name: "skill_view", description: "View a skill", parameters: { type: "object", properties: { skill_id: { type: "string" } }, required: ["skill_id"] } } },
  { type: "function", function: { name: "skill_create", description: "Create a skill", parameters: { type: "object", properties: { name: { type: "string" }, content: { type: "string" } }, required: ["name", "content"] } } },
  { type: "function", function: { name: "skill_update", description: "Update full skill", parameters: { type: "object", properties: { skill_id: { type: "string" }, content: { type: "string" }, expected_version: { type: "number" } }, required: ["skill_id", "content", "expected_version"] } } },
  { type: "function", function: { name: "skill_patch", description: "Patch a skill", parameters: { type: "object", properties: { skill_id: { type: "string" }, old_string: { type: "string" }, new_string: { type: "string" }, expected_version: { type: "number" } }, required: ["skill_id", "old_string", "new_string", "expected_version"] } } },
  { type: "function", function: { name: "skill_files_write", description: "Write supporting file", parameters: { type: "object", properties: { skill_id: { type: "string" }, path: { type: "string" }, content: { type: "string" }, expected_version: { type: "number" } }, required: ["skill_id", "path", "content", "expected_version"] } } },
];

void main().catch((error) => { console.error(error instanceof Error ? error.message : String(error)); process.exitCode = 1; });
