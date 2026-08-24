import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

type Message = { role: "user" | "assistant" | "tool_call" | "tool_result"; content: string };
type RootCase = { id: string; category: string; expectedAction: "create" | "update" | "nothing"; boundaryExpected: boolean; riskClass: string; messages: Message[] };
type Observation = RootCase & { rootId: string; variant: string; correlatedGroup: string; chunks: Message[][]; goldCutAfterChunk: number | null };

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const source = JSON.parse(fs.readFileSync(path.join(root, "datasets", "skill-scale-frozen.json"), "utf8")) as { cases: RootCase[]; categories: Array<{ id: string; label: string }> };
const observations: Observation[] = [];

for (const item of source.cases) {
  const variants = [
    canonical(item),
    oneMessagePerBatch(item),
    mergedBatches(item),
    acknowledgementTail(item),
    telemetryNoise(item),
  ];
  observations.push(...variants.map(({ variant, chunks, goldCutAfterChunk }) => ({
    ...item,
    id: `${item.id}--v-${variant}`,
    rootId: item.id,
    variant,
    correlatedGroup: item.id,
    chunks,
    goldCutAfterChunk,
  })));
}

const counts = {
  semanticRoots: source.cases.length,
  observations: observations.length,
  variantsPerRoot: 5,
  expectedCreateRoots: source.cases.filter((item) => item.expectedAction === "create").length,
  categories: source.categories.length,
  byVariant: Object.fromEntries(["canonical", "single-message", "merged", "ack-tail", "telemetry-noise"].map((variant) => [variant, observations.filter((item) => item.variant === variant).length])),
};

const output = {
  schemaVersion: 2,
  frozen: true,
  description: "Metamorphic streaming observations derived from frozen semantic roots. Variants are correlated and must not be treated as independent confidence-interval samples.",
  counts,
  categories: source.categories,
  observations,
};
fs.writeFileSync(path.join(root, "datasets", "skill-scale-observations.json"), `${JSON.stringify(output, null, 2)}\n`);
console.log(JSON.stringify(counts, null, 2));

function canonical(item: RootCase) {
  const chunks: Message[][] = [];
  let current: Message[] = [];
  for (const message of item.messages) {
    if (message.role === "user" || message.role === "assistant") {
      if (current.length) chunks.push(current);
      current = [message];
    } else current.push(message);
  }
  if (current.length) chunks.push(current);
  return v("canonical", chunks, item.boundaryExpected);
}

function oneMessagePerBatch(item: RootCase) {
  return v("single-message", item.messages.map((message) => [message]), item.boundaryExpected);
}

function mergedBatches(item: RootCase) {
  const base = canonical(item).chunks;
  const chunks: Message[][] = [];
  for (let i = 0; i < base.length; i += 2) chunks.push([...(base[i] ?? []), ...(base[i + 1] ?? [])]);
  return v("merged", chunks, item.boundaryExpected);
}

function acknowledgementTail(item: RootCase) {
  const base = canonical(item).chunks;
  if (!item.boundaryExpected) return v("ack-tail", [...base, [{ role: "user", content: "收到。" }, { role: "assistant", content: "好的。" }]], false);
  return { variant: "ack-tail", chunks: [...base, [{ role: "user", content: "收到，确认本次结果。" }, { role: "assistant", content: "已确认。" }]], goldCutAfterChunk: base.length - 1 };
}

function telemetryNoise(item: RootCase) {
  const prefix: Message[] = [
    { role: "assistant", content: "先记录只读运行环境，避免把环境差异误判为修复结果。" },
    { role: "tool_call", content: "date -u +%FT%TZ && uname -s" },
    { role: "tool_result", content: "2026-08-24T01:00:00Z\nLinux" },
  ];
  const chunks = [[item.messages[0]!, ...prefix], ...canonical({ ...item, messages: item.messages.slice(1) }).chunks];
  return v("telemetry-noise", chunks, item.boundaryExpected);
}

function v(variant: string, chunks: Message[][], boundaryExpected: boolean) {
  return { variant, chunks, goldCutAfterChunk: boundaryExpected ? chunks.length - 1 : null };
}
