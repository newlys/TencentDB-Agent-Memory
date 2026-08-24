import fs from "node:fs";
import path from "node:path";

type TaskRun = {
  profile: string; taskId: string; seed: number; firstAttempt: boolean; eligible: boolean; passed: boolean;
  turns: number; agentInputTokens: number; agentOutputTokens: number; cacheMissTokens?: number; sequence: number;
};
type SkillEvent = {
  profile: string; type: "eligible_boundary" | "skill_create" | "skill_update" | "skill_hit" | "distillation" | "boundary_judge" | "rerank";
  taskId?: string; skillId?: string; sequence: number; inputTokens?: number; outputTokens?: number; actuallyInjected?: boolean;
};
type Input = { schemaVersion: number; taskRuns: TaskRun[]; skillEvents: SkillEvent[] };

const inputPath = process.argv[2];
if (!inputPath) throw new Error("usage: tsx compute-system-metrics.ts <run.json> [output.json]");
const input = JSON.parse(fs.readFileSync(inputPath, "utf8")) as Input;
const outputPath = process.argv[3] || path.join(path.dirname(inputPath), `${path.basename(inputPath, ".json")}-metrics.json`);
validate(input);
const profiles = [...new Set(input.taskRuns.map((item) => item.profile))];
const metrics = Object.fromEntries(profiles.map((profile) => [profile, profileMetrics(profile)]));
const baselineName = profiles.includes("baseline") ? "baseline" : profiles[0]!;
const paired = Object.fromEntries(profiles.filter((profile) => profile !== baselineName).map((profile) => [profile, pairedMetrics(baselineName, profile)]));
const output = { schemaVersion: 1, source: path.resolve(inputPath), profiles: metrics, pairedAgainst: baselineName, paired };
fs.writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`);
console.log(JSON.stringify(output, null, 2));

function profileMetrics(profile: string) {
  const runs = input.taskRuns.filter((item) => item.profile === profile && item.firstAttempt && item.eligible);
  const events = input.skillEvents.filter((item) => item.profile === profile);
  const overheadTokens = events.filter((item) => ["distillation", "boundary_judge", "rerank"].includes(item.type)).reduce((sum, item) => sum + (item.inputTokens ?? 0) + (item.outputTokens ?? 0), 0);
  const perRunTokens = runs.map((item) => item.agentInputTokens + item.agentOutputTokens + (item.cacheMissTokens ?? 0));
  const extracted = new Map(events.filter((item) => item.type === "skill_create" && item.skillId).map((item) => [item.skillId!, item]));
  const injectedHits = events.filter((item) => item.type === "skill_hit" && item.actuallyInjected);
  const laterHits = injectedHits.filter((item) => item.skillId && extracted.has(item.skillId) && item.sequence > extracted.get(item.skillId)!.sequence);
  const hitSkills = new Set(laterHits.map((item) => item.skillId!));
  const hitTasks = new Set(injectedHits.map((item) => item.taskId).filter(Boolean));
  const boundaries = events.filter((item) => item.type === "eligible_boundary").length;
  const creates = events.filter((item) => item.type === "skill_create").length;
  const updates = events.filter((item) => item.type === "skill_update").length;
  const pass = mean(runs.map((item) => Number(item.passed)));
  return {
    eligibleTasks: runs.length,
    passAt1: estimate(pass, wilson(runs.filter((item) => item.passed).length, runs.length)),
    averageTurns: estimate(mean(runs.map((item) => item.turns)), clusterBootstrapMean(runs, (item) => item.turns)),
    averageAgentTokens: estimate(mean(perRunTokens), clusterBootstrapMean(runs, (item) => item.agentInputTokens + item.agentOutputTokens + (item.cacheMissTokens ?? 0))),
    averageTotalTokensIncludingSkillOverhead: (sum(perRunTokens) + overheadTokens) / Math.max(1, runs.length),
    skillOverheadTokens: overheadTokens,
    eligibleBoundaries: boundaries,
    skillCreates: creates,
    skillUpdates: updates,
    skillExtractionRate: estimate(creates / Math.max(1, boundaries), wilson(creates, boundaries)),
    skillReuseRate: estimate(hitSkills.size / Math.max(1, extracted.size), wilson(hitSkills.size, extracted.size)),
    taskSkillHitRate: estimate(hitTasks.size / Math.max(1, runs.length), wilson(hitTasks.size, runs.length)),
  };
}

function pairedMetrics(baseline: string, candidate: string) {
  const base = new Map(input.taskRuns.filter((item) => item.profile === baseline && item.firstAttempt && item.eligible).map((item) => [`${item.taskId}:${item.seed}`, item]));
  const pairs = input.taskRuns.filter((item) => item.profile === candidate && item.firstAttempt && item.eligible && base.has(`${item.taskId}:${item.seed}`)).map((item) => ({ base: base.get(`${item.taskId}:${item.seed}`)!, candidate: item, taskId: item.taskId }));
  const passDeltas = pairs.map((pair) => Number(pair.candidate.passed) - Number(pair.base.passed));
  const turnDeltas = pairs.map((pair) => pair.candidate.turns - pair.base.turns);
  const tokenDeltas = pairs.map((pair) => (pair.candidate.agentInputTokens + pair.candidate.agentOutputTokens + (pair.candidate.cacheMissTokens ?? 0)) - (pair.base.agentInputTokens + pair.base.agentOutputTokens + (pair.base.cacheMissTokens ?? 0)));
  return {
    pairedRuns: pairs.length,
    passAt1Delta: estimate(mean(passDeltas), bootstrapValues(passDeltas, pairs.map((pair) => pair.taskId))),
    averageTurnDelta: estimate(mean(turnDeltas), bootstrapValues(turnDeltas, pairs.map((pair) => pair.taskId))),
    averageAgentTokenDelta: estimate(mean(tokenDeltas), bootstrapValues(tokenDeltas, pairs.map((pair) => pair.taskId))),
    negativeTransferRate: pairs.filter((pair) => pair.base.passed && !pair.candidate.passed).length / Math.max(1, pairs.length),
    positiveTransferRate: pairs.filter((pair) => !pair.base.passed && pair.candidate.passed).length / Math.max(1, pairs.length),
  };
}

function validate(data: Input): void {
  if (!Array.isArray(data.taskRuns) || !Array.isArray(data.skillEvents)) throw new Error("taskRuns and skillEvents arrays are required");
  const keys = new Set<string>();
  for (const item of data.taskRuns) {
    const key = `${item.profile}:${item.taskId}:${item.seed}:${item.firstAttempt}`;
    if (keys.has(key)) throw new Error(`duplicate task run ${key}`);
    keys.add(key);
    if (item.turns < 0 || item.agentInputTokens < 0 || item.agentOutputTokens < 0) throw new Error(`negative metric in ${key}`);
  }
  for (const event of data.skillEvents) if ((event.inputTokens ?? 0) < 0 || (event.outputTokens ?? 0) < 0) throw new Error("negative event tokens");
}
function estimate(value: number, interval: [number, number]) { return { value, ci95: { low: interval[0], high: interval[1] } }; }
function wilson(success: number, total: number): [number, number] { if (!total) return [0, 0]; const z = 1.959963984540054; const p = success / total; const d = 1 + z * z / total; const c = (p + z * z / (2 * total)) / d; const m = z * Math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / d; return [Math.max(0, c - m), Math.min(1, c + m)]; }
function clusterBootstrapMean<T extends { taskId: string }>(values: T[], getter: (item: T) => number): [number, number] { return bootstrapValues(values.map(getter), values.map((item) => item.taskId)); }
function bootstrapValues(values: number[], clusters: string[]): [number, number] {
  if (!values.length) return [0, 0];
  const byCluster = new Map<string, number[]>();
  values.forEach((value, index) => byCluster.set(clusters[index]!, [...(byCluster.get(clusters[index]!) ?? []), value]));
  const names = [...byCluster.keys()]; const estimates: number[] = []; let state = 0x5eed1234;
  const random = () => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 2 ** 32; };
  for (let iteration = 0; iteration < 2000; iteration++) { const sample: number[] = []; for (let i = 0; i < names.length; i++) sample.push(...byCluster.get(names[Math.floor(random() * names.length)]!)!); estimates.push(mean(sample)); }
  estimates.sort((a, b) => a - b); return [estimates[Math.floor(estimates.length * 0.025)]!, estimates[Math.floor(estimates.length * 0.975)]!];
}
function mean(values: number[]): number { return values.length ? sum(values) / values.length : 0; }
function sum(values: number[]): number { return values.reduce((a, b) => a + b, 0); }
