import { createRequire } from "node:module";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const root = resolve(scriptDir, "..");
const repoRoot = resolve(root, "../..");
const profile = process.env.EVAL_PROFILE || "adaptive_v1";
if (!["static", "adaptive_v1"].includes(profile)) throw new Error(`Invalid EVAL_PROFILE: ${profile}`);
const dataset = JSON.parse(readFileSync(resolve(root, "datasets/coding-families.json"), "utf8"));
const endpoint = process.env.TDAI_CORE_URL || "http://127.0.0.1:8420";
const token = process.env.TDAI_CORE_TOKEN || "local-dev-memory-key";
const serviceId = process.env.TDAI_SERVICE_ID || "default";
const team_id = process.env.TDAI_TEAM_ID || "team-dyf7fb74wi";
const agent_id = process.env.TDAI_AGENT_ID || "agt-dyf7zr5fjh";
const requireFromCore = createRequire(resolve(repoRoot, "MemoryCore/package.json"));
const { getEncoding } = requireFromCore("js-tiktoken");
const encoding = getEncoding("cl100k_base");

async function listing(task, gold) {
  const complexity = task.phases.length <= 1 ? "simple" : task.phases.length === 2 ? "moderate" : "complex";
  const started = performance.now();
  const response = await fetch(`${endpoint}/v3/skill/listing`, {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "x-tdai-service-id": serviceId, "content-type": "application/json" },
    body: JSON.stringify({
      team_id, agent_id,
      query: `${task.title}. Reuse coding guidance for the ${gold} task family.`,
      char_budget: profile === "adaptive_v1" ? 4000 : 8000,
      routing_context: { complexity, predicted_phases: task.phases, current_phase: task.phases[0] },
    }),
  });
  const body = await response.json();
  if (!response.ok || body.code !== 0) throw new Error(JSON.stringify(body));
  return { ...body.data, measured_total_ms: performance.now() - started };
}

const rows = [];
for (const family of dataset.families) {
  for (const task of family.tasks.filter((item) => item.split === "evaluation")) {
    const result = await listing(task, family.gold_skill);
    const names = result.hits.map((hit) => hit.name);
    const rankIndex = names.indexOf(family.gold_skill);
    const rank = rankIndex < 0 ? null : rankIndex + 1;
    const k = names.length;
    const d = result.diagnostics || {};
    rows.push({
      family: family.id, task_id: task.id, gold_skill: family.gold_skill,
      pass_retrieval: rank !== null, precision_at_k: k ? (rank ? 1 / k : 0) : 0,
      recall_at_k: rank ? 1 : 0, mrr: rank ? 1 / rank : 0,
      ndcg_at_k: rank ? 1 / Math.log2(rank + 1) : 0,
      rank, final_k: k, injection_chars: result.listing.length,
      injection_tokens_cl100k: d.listing_tokens_cl100k ?? encoding.encode(result.listing).length,
      complexity: d.complexity ?? (task.phases.length === 2 ? "moderate" : "complex"),
      predicted_phases: task.phases, complexity_k: d.complexity_k ?? null,
      confidence_k: d.confidence_k ?? null, budget_k: d.budget_k ?? null,
      candidate_count: d.candidate_count ?? null, reranked_count: d.reranked_count ?? null,
      bm25_ms: d.bm25_ms ?? null, rerank_ms: d.rerank_ms ?? null,
      total_route_ms: d.total_ms ?? result.measured_total_ms,
      rerank_input_tokens: d.rerank_input_tokens ?? 0,
      rerank_output_tokens: d.rerank_output_tokens ?? 0,
      fallback_reason: d.fallback_reason ?? null,
    });
    process.stdout.write(`[routing:${profile}] ${family.id}/${task.id}: rank=${rank ?? "miss"}, K=${k}\n`);
  }
}
encoding.free?.();
const avg = (field) => rows.reduce((sum, row) => sum + (Number(row[field]) || 0), 0) / rows.length;
const sortedLatency = rows.map((row) => row.total_route_ms).sort((a, b) => a - b);
const summary = {
  schema_version: 1, routing_profile: profile, cases: rows.length,
  skill_retrieval_hit_rate: avg("recall_at_k"), precision_at_k: avg("precision_at_k"),
  recall_at_k: avg("recall_at_k"), mrr: avg("mrr"), ndcg_at_k: avg("ndcg_at_k"),
  avg_final_k: avg("final_k"), avg_injection_tokens_cl100k: avg("injection_tokens_cl100k"),
  avg_bm25_ms: avg("bm25_ms"), avg_rerank_ms: avg("rerank_ms"), avg_total_route_ms: avg("total_route_ms"),
  p95_total_route_ms: sortedLatency[Math.ceil(sortedLatency.length * 0.95) - 1], rows,
};
const results = resolve(root, "results");
mkdirSync(results, { recursive: true });
writeFileSync(resolve(results, `routing-${profile}.json`), JSON.stringify(summary, null, 2));
const columns = Object.keys(rows[0]);
const csv = [columns.join(","), ...rows.map((row) => columns.map((key) => JSON.stringify(row[key] ?? "")).join(","))].join("\n");
writeFileSync(resolve(results, `routing-${profile}.csv`), csv);
process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
