import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

type Message = { role: string; content: string };
type Case = { id: string; category: string; expectedAction: "create" | "update" | "nothing"; boundaryExpected: boolean; riskClass: string; messages: Message[]; existingSkill?: unknown };

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const rootPath = path.join(root, "datasets", "skill-scale-frozen.json");
const obsPath = path.join(root, "datasets", "skill-scale-observations.json");
const data = JSON.parse(fs.readFileSync(rootPath, "utf8")) as { frozen: boolean; cases: Case[]; categories: Array<{ id: string }> };
const obs = JSON.parse(fs.readFileSync(obsPath, "utf8")) as { observations: Array<{ id: string; rootId: string; variant: string; chunks: Message[][]; goldCutAfterChunk: number | null }> };
const errors: string[] = [];
const warnings: string[] = [];

check(data.frozen, "root dataset must be frozen");
check(unique(data.cases.map((item) => item.id)), "root case ids must be unique");
check(unique(obs.observations.map((item) => item.id)), "observation ids must be unique");
check(obs.observations.length >= 1000, `expected >=1000 observations, got ${obs.observations.length}`);
check(data.cases.filter((item) => item.expectedAction === "create").length >= 100, "expected >=100 create roots");

for (const category of data.categories) {
  const items = data.cases.filter((item) => item.category === category.id);
  check(items.filter((item) => item.expectedAction === "create").length >= 10, `${category.id}: fewer than 10 create roots`);
  check(items.some((item) => item.expectedAction === "update"), `${category.id}: missing update lifecycle case`);
  check(items.some((item) => item.riskClass === "duplicate"), `${category.id}: missing duplicate case`);
  for (const risk of ["unresolved", "advice_only", "opt_out", "unsafe", "secret"]) check(items.some((item) => item.riskClass === risk), `${category.id}: missing ${risk}`);
}

for (const item of data.cases) {
  check(item.messages.length >= 2, `${item.id}: too few messages`);
  if (item.expectedAction === "create" || item.expectedAction === "update") {
    const calls = item.messages.filter((message) => message.role === "tool_call");
    const results = item.messages.filter((message) => message.role === "tool_result");
    check(calls.length >= 3, `${item.id}: positive case needs >=3 executed/validation calls`);
    check(calls.length === results.length, `${item.id}: tool call/result mismatch`);
    check(item.boundaryExpected, `${item.id}: write case must have completed boundary`);
    check(/通过|成功|正常|恢复|完成|healthy|passed|valid|收敛|稳定|生效|阻断|拒绝|降至|降到|一致|正确|在线|健康|可访问|归零|完整|连通|达标|存在|保留|路由|运行|写入|验证|上线|观察到|拿到|无差异|无变更|无错误|可用|扩容|发布|持久化|启用|不会|改用|已/u.test(item.messages.at(-1)?.content ?? ""), `${item.id}: conclusion lacks explicit verification`);
  }
  if (item.expectedAction === "update") check(Boolean(item.existingSkill), `${item.id}: update requires existingSkill`);
  const containsSecretMarker = item.messages.some((message) => /CorrectHorseBatteryStaple|sk-test-/u.test(message.content));
  check(containsSecretMarker === (item.riskClass === "secret"), `${item.id}: secret marker outside secret negative or missing marker`);
}

for (const item of obs.observations) {
  check(item.chunks.length > 0 && item.chunks.every((chunk) => chunk.length > 0), `${item.id}: empty chunk`);
  if (item.goldCutAfterChunk !== null) check(item.goldCutAfterChunk >= 0 && item.goldCutAfterChunk < item.chunks.length, `${item.id}: invalid gold cut`);
}

const createCases = data.cases.filter((item) => item.expectedAction === "create");
let nearDuplicatePairs = 0;
for (let i = 0; i < createCases.length; i++) {
  for (let j = i + 1; j < createCases.length; j++) {
    const score = jaccard(tokens(createCases[i]!.messages), tokens(createCases[j]!.messages));
    if (score >= 0.82) {
      nearDuplicatePairs++;
      warnings.push(`near duplicate ${score.toFixed(2)}: ${createCases[i]!.id} <> ${createCases[j]!.id}`);
    }
  }
}
check(nearDuplicatePairs === 0, `create roots contain ${nearDuplicatePairs} near-duplicate pairs >=0.82 Jaccard`);

const report = {
  schemaVersion: 1,
  valid: errors.length === 0,
  roots: data.cases.length,
  observations: obs.observations.length,
  categories: data.categories.length,
  expectedCreateRoots: createCases.length,
  expectedUpdates: data.cases.filter((item) => item.expectedAction === "update").length,
  negativesAndDuplicates: data.cases.filter((item) => item.expectedAction === "nothing").length,
  nearDuplicatePairs,
  hashes: { roots: sha256(rootPath), observations: sha256(obsPath) },
  errors,
  warnings,
};
fs.mkdirSync(path.join(root, "results"), { recursive: true });
fs.writeFileSync(path.join(root, "results", "dataset-validation.json"), `${JSON.stringify(report, null, 2)}\n`);
console.log(JSON.stringify(report, null, 2));
if (errors.length) process.exitCode = 1;

function check(ok: boolean, message: string): void { if (!ok) errors.push(message); }
function unique(values: string[]): boolean { return new Set(values).size === values.length; }
function tokens(messages: Message[]): Set<string> {
  return new Set(messages.flatMap((message) => message.content.toLowerCase().match(/[a-z][a-z0-9_.-]{2,}|[\p{Script=Han}]{2,}/gu) ?? []).filter((token) => !["完成","通过","验证","成功","检查","恢复","apply","status","health"].includes(token)));
}
function jaccard(a: Set<string>, b: Set<string>): number { const intersection = [...a].filter((item) => b.has(item)).length; return intersection / Math.max(1, a.size + b.size - intersection); }
function sha256(file: string): string { return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex").toUpperCase(); }
