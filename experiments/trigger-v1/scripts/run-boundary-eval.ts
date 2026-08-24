import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  countToolCalls,
  evaluateSopBoundary,
  type SopBoundaryMessage,
} from "../../../MemoryCore/src/core/skill/conversation-add/sop-boundary.js";

type Chunk = SopBoundaryMessage[];
type Case = {
  id: string;
  environment: string;
  chunks: Chunk[];
  gold: { boundaryAfter: number[]; boundaryBefore: number[] };
};
type Dataset = { families: Array<{ id: string; software: string; cases: Case[] }> };
type Event = { phase: "before_append" | "after_append"; index: number };
type Prediction = { event: Event; score: number; signals: string[] };

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const datasetName = process.env.TDAI_TRIGGER_DATASET || "sop-boundaries.json";
const dataset = JSON.parse(fs.readFileSync(path.join(root, "datasets", datasetName), "utf8")) as Dataset;
const resultSuffix = path.basename(datasetName, ".json").replace(/^sop-boundaries$/, "boundary-eval").replace(/^sop-boundaries-/, "boundary-eval-");

const strategies = [
  ...[2, 3, 5, 10].map((threshold) => ({
    id: `fixed_tool_calls_${threshold}`,
    predict: (testCase: Case) => runFixedCount(testCase, threshold),
    judgeCalls: 0,
  })),
  ...[0.60, 0.68, 0.76, 0.84].map((threshold) => ({
    id: `sop_v1_score_${threshold.toFixed(2)}`,
    predict: (testCase: Case) => runSopV1(testCase, threshold),
    judgeCalls: 0,
  })),
];

const llmPredictionsPath = path.join(root, "results", "llm-judge-predictions.json");
if (datasetName === "sop-boundaries.json" && fs.existsSync(llmPredictionsPath)) {
  const cached = JSON.parse(fs.readFileSync(llmPredictionsPath, "utf8")) as { model?: string; judgeCalls?: number; predictions?: Record<string, Array<{ phase: Event["phase"]; index: number; confidence: number; reason: string }>> };
  strategies.push({
    id: `llm_judge_${cached.model ?? "unknown"}`,
    predict: (testCase: Case) => (cached.predictions?.[testCase.id] ?? []).map((item) => ({ event: { phase: item.phase, index: item.index }, score: item.confidence, signals: [item.reason] })),
    judgeCalls: cached.judgeCalls ?? 0,
  });
}

const rows = strategies.map((strategy) => {
  let tp = 0;
  let fp = 0;
  let fn = 0;
  let exact = 0;
  let cases = 0;
  const details: Array<{ family: string; caseId: string; gold: string[]; predicted: string[] }> = [];

  for (const family of dataset.families) {
    for (const testCase of family.cases) {
      cases++;
      const gold = goldEvents(testCase);
      const predicted = strategy.predict(testCase);
      // A boundary after batch i and before batch i+1 produce the same archive
      // contents. Score the canonical cut position, not API-call timing.
      const goldKeys = new Set(gold.map(canonicalCutKey));
      const predictedKeys = new Set(predicted.map((item) => canonicalCutKey(item.event)));
      for (const key of predictedKeys) goldKeys.has(key) ? tp++ : fp++;
      for (const key of goldKeys) if (!predictedKeys.has(key)) fn++;
      if (setEqual(goldKeys, predictedKeys)) exact++;
      details.push({ family: family.id, caseId: testCase.id, gold: [...goldKeys], predicted: [...predictedKeys] });
    }
  }

  const precision = safeDivide(tp, tp + fp);
  const recall = safeDivide(tp, tp + fn);
  const f1 = safeDivide(2 * precision * recall, precision + recall);
  return {
    strategy: strategy.id,
    cases,
    truePositives: tp,
    falsePositives: fp,
    falseNegatives: fn,
    precision,
    recall,
    f1,
    exactCaseAccuracy: safeDivide(exact, cases),
    extractionRate: safeDivide(tp + fp, cases),
    redundantTriggerRate: safeDivide(fp, tp + fp),
    missedSopRate: safeDivide(fn, tp + fn),
    judgeCalls: strategy.judgeCalls,
    details,
  };
});

rows.sort((a, b) => b.f1 - a.f1 || b.exactCaseAccuracy - a.exactCaseAccuracy || a.extractionRate - b.extractionRate);
const resultsDir = path.join(root, "results");
fs.mkdirSync(resultsDir, { recursive: true });
fs.writeFileSync(path.join(resultsDir, `${resultSuffix}.json`), `${JSON.stringify({ generatedAt: new Date().toISOString(), dataset: datasetStats(), rows }, null, 2)}\n`);
fs.writeFileSync(path.join(resultsDir, `${resultSuffix}.csv`), toCsv(rows));
fs.writeFileSync(path.join(resultsDir, `${resultSuffix}.md`), toMarkdown(rows));

console.log(toMarkdown(rows));

function runFixedCount(testCase: Case, threshold: number): Prediction[] {
  const predictions: Prediction[] = [];
  let buffer: Chunk = [];
  for (let index = 0; index < testCase.chunks.length; index++) {
    buffer.push(...testCase.chunks[index]!);
    if (countToolCalls(buffer) >= threshold || byteLength(buffer) >= 40 * 1024) {
      predictions.push({ event: { phase: "after_append", index }, score: 1, signals: [`tool_calls_${threshold}`] });
      buffer = [];
    }
  }
  return predictions;
}

function runSopV1(testCase: Case, completionScoreThreshold: number): Prediction[] {
  const predictions: Prediction[] = [];
  let buffer: Chunk = [];
  for (let index = 0; index < testCase.chunks.length; index++) {
    const incoming = testCase.chunks[index]!;
    const decision = evaluateSopBoundary(
      { bufferedMessages: buffer, incomingMessages: incoming },
      { profile: "sop_v1", completionScoreThreshold },
    );
    if (decision.phase === "before_append") {
      predictions.push({ event: { phase: "before_append", index }, score: decision.score, signals: decision.signals });
      buffer = [...incoming];
    } else {
      buffer.push(...incoming);
      if (decision.phase === "after_append") {
        predictions.push({ event: { phase: "after_append", index }, score: decision.score, signals: decision.signals });
        buffer = [];
      }
    }
  }
  return predictions;
}

function goldEvents(testCase: Case): Event[] {
  return [
    ...testCase.gold.boundaryAfter.map((index) => ({ phase: "after_append" as const, index })),
    ...testCase.gold.boundaryBefore.map((index) => ({ phase: "before_append" as const, index })),
  ];
}

function canonicalCutKey(event: Event): string {
  return `cut:${event.phase === "after_append" ? event.index + 1 : event.index}`;
}

function byteLength(messages: Chunk): number {
  return messages.reduce((sum, message) => sum + Buffer.byteLength(message.content ?? "", "utf8"), 0);
}

function safeDivide(numerator: number, denominator: number): number {
  return denominator === 0 ? 0 : numerator / denominator;
}

function setEqual(a: Set<string>, b: Set<string>): boolean {
  return a.size === b.size && [...a].every((value) => b.has(value));
}

function datasetStats() {
  const cases = dataset.families.flatMap((family) => family.cases);
  const positives = cases.reduce((sum, item) => sum + item.gold.boundaryAfter.length + item.gold.boundaryBefore.length, 0);
  return { families: dataset.families.length, cases: cases.length, positiveBoundaries: positives, chunks: cases.reduce((sum, item) => sum + item.chunks.length, 0) };
}

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function toCsv(data: typeof rows): string {
  const headers = ["strategy", "precision", "recall", "f1", "exact_case_accuracy", "extraction_rate", "redundant_trigger_rate", "missed_sop_rate", "tp", "fp", "fn"];
  const lines = data.map((row) => [row.strategy, row.precision, row.recall, row.f1, row.exactCaseAccuracy, row.extractionRate, row.redundantTriggerRate, row.missedSopRate, row.truePositives, row.falsePositives, row.falseNegatives].join(","));
  return `${headers.join(",")}\n${lines.join("\n")}\n`;
}

function toMarkdown(data: typeof rows): string {
  const stats = datasetStats();
  const lines = [
    "# trigger-v1 boundary evaluation",
    "",
    `Dataset: ${stats.families} families, ${stats.cases} cases, ${stats.chunks} batches, ${stats.positiveBoundaries} positive boundaries.`,
    "",
    "| Strategy | Precision | Recall | F1 | Exact cases | Extractions/case | Redundant | Missed SOP | Judge calls |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ...data.map((row) => `| ${row.strategy} | ${pct(row.precision)} | ${pct(row.recall)} | ${pct(row.f1)} | ${pct(row.exactCaseAccuracy)} | ${row.extractionRate.toFixed(2)} | ${pct(row.redundantTriggerRate)} | ${pct(row.missedSopRate)} | ${row.judgeCalls} |`),
    "",
    "`fixed_tool_calls_10` is the current tool-count baseline (the 40KB cap is also simulated).",
    "All `sop_v1` variants are deterministic and have zero judge calls.",
    "",
  ];
  return `${lines.join("\n")}\n`;
}
