import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { evaluateSopBoundary, type SopBoundaryMessage } from "../../../MemoryCore/src/core/skill/conversation-add/sop-boundary.js";
import { evaluateSkillValue } from "../../../MemoryCore/src/core/skill/skill-value-gate.js";

type RootCase = { id: string; category: string; expectedAction: "create" | "update" | "nothing"; boundaryExpected: boolean; riskClass: string; messages: SopBoundaryMessage[] };
type Observation = { id: string; rootId: string; variant: string; category: string; boundaryExpected: boolean; chunks: SopBoundaryMessage[][]; goldCutAfterChunk: number | null };
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const roots = (JSON.parse(fs.readFileSync(path.join(root, "datasets", "skill-scale-frozen.json"), "utf8")) as { cases: RootCase[] }).cases;
const observations = (JSON.parse(fs.readFileSync(path.join(root, "datasets", "skill-scale-observations.json"), "utf8")) as { observations: Observation[] }).observations;

const boundaryRows = observations.map((item) => ({ ...item, predictions: predictBoundaries(item) }));
const boundaryTp = boundaryRows.filter((item) => item.goldCutAfterChunk !== null && item.predictions.includes(item.goldCutAfterChunk)).length;
const boundaryFn = boundaryRows.filter((item) => item.goldCutAfterChunk !== null && !item.predictions.includes(item.goldCutAfterChunk)).length;
const boundaryFp = boundaryRows.reduce((sum, item) => sum + item.predictions.filter((cut) => cut !== item.goldCutAfterChunk).length, 0);
const valueRows = roots.map((item) => ({ ...item, gate: evaluateSkillValue(item.messages, "precision_v1") }));
const shouldWrite = (item: RootCase) => item.expectedAction === "create" || item.expectedAction === "update";
const unsafe = new Set(["secret", "opt_out", "unsafe"]);
const valueFalseSkips = valueRows.filter((item) => shouldWrite(item) && item.gate.decision === "skip");
const unsafePasses = valueRows.filter((item) => unsafe.has(item.riskClass) && item.gate.decision !== "skip");
const avoided = valueRows.filter((item) => item.gate.decision === "skip").length;

const result = {
  schemaVersion: 1,
  dataset: { roots: roots.length, observations: observations.length, categories: new Set(roots.map((item) => item.category)).size },
  boundary: {
    tp: boundaryTp, fp: boundaryFp, fn: boundaryFn,
    precision: divide(boundaryTp, boundaryTp + boundaryFp),
    recall: divide(boundaryTp, boundaryTp + boundaryFn),
    f1: f1(boundaryTp, boundaryFp, boundaryFn),
    exactObservationRate: divide(boundaryRows.filter((item) => item.predictions.length === (item.goldCutAfterChunk === null ? 0 : 1) && (item.goldCutAfterChunk === null || item.predictions[0] === item.goldCutAfterChunk)).length, boundaryRows.length),
    byVariant: Object.fromEntries([...new Set(boundaryRows.map((item) => item.variant))].map((variant) => {
      const rows = boundaryRows.filter((item) => item.variant === variant);
      const tp = rows.filter((item) => item.goldCutAfterChunk !== null && item.predictions.includes(item.goldCutAfterChunk)).length;
      const fn = rows.filter((item) => item.goldCutAfterChunk !== null && !item.predictions.includes(item.goldCutAfterChunk)).length;
      const fp = rows.reduce((sum, item) => sum + item.predictions.filter((cut) => cut !== item.goldCutAfterChunk).length, 0);
      return [variant, { precision: divide(tp, tp + fp), recall: divide(tp, tp + fn), f1: f1(tp, fp, fn), tp, fp, fn }];
    })),
  },
  valueGate: {
    callsAvoided: avoided,
    callsAvoidedRate: avoided / roots.length,
    positivePassThroughRecall: 1 - valueFalseSkips.length / roots.filter(shouldWrite).length,
    falseSkips: valueFalseSkips.map((item) => item.id),
    unsafePasses: unsafePasses.map((item) => ({ id: item.id, decision: item.gate.decision, signals: item.gate.signals })),
    decisionCounts: Object.fromEntries(["extract", "review", "skip"].map((decision) => [decision, valueRows.filter((item) => item.gate.decision === decision).length])),
  },
};
fs.mkdirSync(path.join(root, "results"), { recursive: true });
fs.writeFileSync(path.join(root, "results", "scale-static-eval.json"), `${JSON.stringify({ ...result, boundaryDetails: boundaryRows, valueDetails: valueRows }, null, 2)}\n`);
fs.writeFileSync(path.join(root, "results", "scale-static-eval.md"), render(result));
console.log(render(result));

function predictBoundaries(item: Observation): number[] {
  const cuts: number[] = [];
  let buffer: SopBoundaryMessage[] = [];
  for (let index = 0; index < item.chunks.length; index++) {
    const incoming = item.chunks[index]!;
    const decision = evaluateSopBoundary({ bufferedMessages: buffer, incomingMessages: incoming }, { profile: "sop_v1", completionScoreThreshold: 0.68 });
    if (decision.phase === "before_append") { cuts.push(index - 1); buffer = [...incoming]; }
    else { buffer.push(...incoming); if (decision.phase === "after_append") { cuts.push(index); buffer = []; } }
  }
  return [...new Set(cuts)];
}
function divide(a: number, b: number): number { return b ? a / b : 0; }
function f1(tp: number, fp: number, fn: number): number { const p = divide(tp, tp + fp); const r = divide(tp, tp + fn); return divide(2 * p * r, p + r); }
function pct(value: number): string { return `${(value * 100).toFixed(1)}%`; }
function render(r: typeof result): string {
  return `# trigger-v2 static scale evaluation\n\n- Roots: ${r.dataset.roots}; observations: ${r.dataset.observations}; categories: ${r.dataset.categories}\n- Boundary P/R/F1: ${pct(r.boundary.precision)} / ${pct(r.boundary.recall)} / ${pct(r.boundary.f1)}\n- Exact observation rate: ${pct(r.boundary.exactObservationRate)}\n- Value-gate calls avoided: ${r.valueGate.callsAvoided} (${pct(r.valueGate.callsAvoidedRate)})\n- Positive pass-through recall: ${pct(r.valueGate.positivePassThroughRecall)}\n- Unsafe candidates passed to reviewer: ${r.valueGate.unsafePasses.length}\n- False skips: ${r.valueGate.falseSkips.length}\n`;
}
