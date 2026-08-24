import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

type Message = { role: string; content: string; [key: string]: unknown };
type TestCase = { id: string; environment: string; chunks: Message[][]; gold: { boundaryAfter: number[]; boundaryBefore: number[] } };
type Family = { id: string; software: string; cases: TestCase[]; [key: string]: unknown };
type Dataset = { schemaVersion: number; description: string; families: Family[] };

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const source = JSON.parse(fs.readFileSync(path.join(root, "datasets", "sop-boundaries.json"), "utf8")) as Dataset;
const families = source.families.map((family) => ({
  id: `${family.id}-robustness`,
  software: family.software,
  sourceFamily: family.id,
  split: "metamorphic_robustness",
  cases: family.cases.flatMap((testCase) => variants(testCase)),
}));

const output = {
  schemaVersion: 1,
  description: "Metamorphic robustness variants. Expected SOP cuts are invariant under harmless output noise, acknowledgements, and conversation/add batching changes.",
  provenance: { source: "sop-boundaries.json", transformations: ["tool-output-noise", "post-completion-ack", "merge-first-batches", "split-dense-batch"] },
  families,
};
fs.writeFileSync(path.join(root, "datasets", "sop-boundaries-robustness.json"), `${JSON.stringify(output, null, 2)}\n`);
console.log(`wrote ${families.reduce((sum, family) => sum + family.cases.length, 0)} robustness cases`);

function variants(testCase: TestCase): TestCase[] {
  const cuts = canonicalCuts(testCase);
  const noiseChunks = clone(testCase.chunks).map((chunk) => chunk.map((message) => message.role === "tool_result"
    ? { ...message, content: `[2026-08-24T00:00:00Z] INFO command finished\n${message.content}\nmetrics: elapsed_ms=127` }
    : message));

  const ackChunks = clone(testCase.chunks);
  ackChunks.push([{ role: "user", content: "收到，谢谢。" }]);

  const mergedChunks = clone(testCase.chunks);
  let mergedCuts = [...cuts];
  if (mergedChunks.length >= 2) {
    mergedChunks.splice(0, 2, [...mergedChunks[0]!, ...mergedChunks[1]!]);
    mergedCuts = mergedCuts.map((cut) => cut >= 2 ? cut - 1 : cut);
  }

  const splitChunks = clone(testCase.chunks);
  let splitCuts = [...cuts];
  const splitIndex = splitChunks.findIndex((chunk) => chunk.length >= 2);
  if (splitIndex >= 0) {
    const chunk = splitChunks[splitIndex]!;
    const at = Math.ceil(chunk.length / 2);
    splitChunks.splice(splitIndex, 1, chunk.slice(0, at), chunk.slice(at));
    splitCuts = splitCuts.map((cut) => cut > splitIndex ? cut + 1 : cut);
  }

  return [
    make(testCase, "noise", noiseChunks, cuts),
    make(testCase, "ack", ackChunks, cuts),
    make(testCase, "merged", mergedChunks, mergedCuts),
    make(testCase, "split", splitChunks, splitCuts),
  ];
}

function make(sourceCase: TestCase, suffix: string, chunks: Message[][], cuts: number[]): TestCase & { transformation: string; sourceCase: string } {
  const boundaryBefore = cuts.filter((cut) => cut < chunks.length);
  const boundaryAfter = cuts.filter((cut) => cut === chunks.length).map(() => chunks.length - 1);
  return { id: `${sourceCase.id}--${suffix}`, sourceCase: sourceCase.id, transformation: suffix, environment: sourceCase.environment, chunks, gold: { boundaryAfter, boundaryBefore } };
}

function canonicalCuts(testCase: TestCase): number[] {
  return [...testCase.gold.boundaryAfter.map((index) => index + 1), ...testCase.gold.boundaryBefore].sort((a, b) => a - b);
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
