import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { evaluateSkillValue } from "../../../MemoryCore/src/core/skill/skill-value-gate.js";
import type { ExtractMessage } from "../../../MemoryCore/src/core/skill/types.js";

type ValueCase = { id: string; split: string; kind: string; shouldExtract: boolean; reason: string; messages: ExtractMessage[] };
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const dataset = JSON.parse(fs.readFileSync(path.join(root, "datasets", "skill-value-cases.json"), "utf8")) as { cases: ValueCase[] };

const details = dataset.cases.map((item) => ({ ...item, result: evaluateSkillValue(item.messages, "precision_v1") }));
const positives = details.filter((item) => item.shouldExtract);
const negatives = details.filter((item) => !item.shouldExtract);
const skipped = details.filter((item) => item.result.decision === "skip");
const unsafeFalseSkips = skipped.filter((item) => item.shouldExtract);
const metrics = {
  cases: details.length,
  positiveCases: positives.length,
  negativeCases: negatives.length,
  llmCallsAvoided: skipped.length,
  llmCallAvoidanceRate: skipped.length / details.length,
  skipPrecision: skipped.filter((item) => !item.shouldExtract).length / Math.max(1, skipped.length),
  positivePassThroughRecall: positives.filter((item) => item.result.decision !== "skip").length / Math.max(1, positives.length),
  unsafeFalseSkips: unsafeFalseSkips.map((item) => item.id),
  decisions: Object.fromEntries(["extract", "review", "skip"].map((decision) => [decision, details.filter((item) => item.result.decision === decision).length])),
};
const resultsDir = path.join(root, "results");
fs.mkdirSync(resultsDir, { recursive: true });
fs.writeFileSync(path.join(resultsDir, "value-gate-eval.json"), `${JSON.stringify({ generatedAt: new Date().toISOString(), metrics, details }, null, 2)}\n`);
const md = [
  "# precision_v1 value-gate evaluation", "",
  `Cases: ${metrics.cases} (${metrics.positiveCases} should extract / ${metrics.negativeCases} should not).`, "",
  "| Metric | Result |", "|---|---:|",
  `| LLM calls avoided | ${metrics.llmCallsAvoided} (${pct(metrics.llmCallAvoidanceRate)}) |`,
  `| Skip precision | ${pct(metrics.skipPrecision)} |`,
  `| Positive pass-through recall | ${pct(metrics.positivePassThroughRecall)} |`,
  `| Unsafe false skips | ${metrics.unsafeFalseSkips.length} |`, "",
  "The gate is a cost/safety prefilter, not the final extraction classifier. `review` and `extract` both continue to the reviewer LLM.", "",
].join("\n");
fs.writeFileSync(path.join(resultsDir, "value-gate-eval.md"), md);
console.log(md);

function pct(value: number): string { return `${(value * 100).toFixed(1)}%`; }
