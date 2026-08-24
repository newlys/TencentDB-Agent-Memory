import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(root, "../..");
const read = (path) => JSON.parse(readFileSync(path, "utf8"));
const routingStatic = read(resolve(root, "results/routing-static.json"));
const routingAdaptive = read(resolve(root, "results/routing-adaptive_v1.json"));
const task1Path = (profile) => resolve(repoRoot, `experiments/baseline/results/task1-${profile}.json`);
const task2Path = (profile) => resolve(repoRoot, `experiments/baseline/results/task2-${profile}.json`);
const task1 = existsSync(task1Path("static")) && existsSync(task1Path("adaptive_v1"))
  ? { static: read(task1Path("static")), adaptive: read(task1Path("adaptive_v1")) } : null;
const task2 = existsSync(task2Path("static")) && existsSync(task2Path("adaptive_v1"))
  ? { static: read(task2Path("static")), adaptive: read(task2Path("adaptive_v1")) } : null;
const delta = (oldValue, newValue) => ({
  old: oldValue, new: newValue, absolute: newValue - oldValue,
  percent: oldValue === 0 ? null : ((newValue - oldValue) / oldValue) * 100,
});
const metrics = {
  routing: {
    hit_rate: delta(routingStatic.skill_retrieval_hit_rate, routingAdaptive.skill_retrieval_hit_rate),
    precision_at_k: delta(routingStatic.precision_at_k, routingAdaptive.precision_at_k),
    mrr: delta(routingStatic.mrr, routingAdaptive.mrr),
    ndcg_at_k: delta(routingStatic.ndcg_at_k, routingAdaptive.ndcg_at_k),
    final_k: delta(routingStatic.avg_final_k, routingAdaptive.avg_final_k),
    injection_tokens: delta(routingStatic.avg_injection_tokens_cl100k, routingAdaptive.avg_injection_tokens_cl100k),
    route_latency_ms: delta(routingStatic.avg_total_route_ms, routingAdaptive.avg_total_route_ms),
  },
  task1: task1 ? {
    effective_call_rate: delta(task1.static.effective_call_rate, task1.adaptive.effective_call_rate),
    false_call_rate: delta(task1.static.false_call_rate, task1.adaptive.false_call_rate),
    correct_tool_selection_rate: delta(task1.static.correct_tool_selection_rate, task1.adaptive.correct_tool_selection_rate),
    injection_tokens: delta(task1.static.injection.avg_tokens_cl100k, task1.adaptive.injection.avg_tokens_cl100k),
  } : null,
  task2: task2 ? {
    pass_at_1: delta(task2.static.pass_at_1, task2.adaptive.pass_at_1),
    avg_turns: delta(task2.static.avg_turns, task2.adaptive.avg_turns),
    avg_total_tokens: delta(task2.static.avg_total_tokens_including_skill_distillation_lower_bound, task2.adaptive.avg_total_tokens_including_skill_distillation_lower_bound),
    skill_hit_rate: delta(task2.static.skill_hit_rate, task2.adaptive.skill_hit_rate),
  } : null,
};
const fmt = (value, pct = false) => pct ? `${(value * 100).toFixed(1)}%` : Number(value).toFixed(2);
const row = (name, item, pct = false) => `| ${name} | ${fmt(item.old, pct)} | ${fmt(item.new, pct)} | ${fmt(item.absolute, pct)} | ${item.percent == null ? "n/a" : `${item.percent.toFixed(1)}%`} |`;
let markdown = `# adaptive_v1 A/B report\n\n## Controlled same-repository routing\n\n| Metric | static | adaptive_v1 | absolute delta | relative change |\n|---|---:|---:|---:|---:|\n`;
markdown += row("Skill retrieval hit rate", metrics.routing.hit_rate, true) + "\n";
markdown += row("Precision@K", metrics.routing.precision_at_k) + "\n";
markdown += row("MRR", metrics.routing.mrr) + "\n";
markdown += row("nDCG@K", metrics.routing.ndcg_at_k) + "\n";
markdown += row("Final K", metrics.routing.final_k) + "\n";
markdown += row("Injected cl100k tokens", metrics.routing.injection_tokens) + "\n";
markdown += row("Route latency ms", metrics.routing.route_latency_ms) + "\n";
if (metrics.task1) {
  markdown += `\n## Task 1 regression\n\n| Metric | static | adaptive_v1 | absolute delta | relative change |\n|---|---:|---:|---:|---:|\n`;
  markdown += row("Effective call rate", metrics.task1.effective_call_rate, true) + "\n";
  markdown += row("False call rate", metrics.task1.false_call_rate, true) + "\n";
  markdown += row("Correct tool selection", metrics.task1.correct_tool_selection_rate, true) + "\n";
  markdown += row("Injected cl100k tokens", metrics.task1.injection_tokens) + "\n";
}
if (metrics.task2) {
  markdown += `\n## WorkBuddy coding smoke A/B\n\n| Metric | static | adaptive_v1 | absolute delta | relative change |\n|---|---:|---:|---:|---:|\n`;
  markdown += row("pass@1", metrics.task2.pass_at_1, true) + "\n";
  markdown += row("Average turns", metrics.task2.avg_turns) + "\n";
  markdown += row("Average total tokens", metrics.task2.avg_total_tokens) + "\n";
  markdown += row("Skill tool hit rate", metrics.task2.skill_hit_rate, true) + "\n";
}
markdown += `\nNatural-extraction and controlled-Gold-Skill results remain separate. Coding success without a relevant Skill hit is not counted as Skill benefit.\n`;
mkdirSync(resolve(root, "results"), { recursive: true });
writeFileSync(resolve(root, "results/comparison.json"), JSON.stringify({ schema_version: 1, metrics }, null, 2));
writeFileSync(resolve(root, "results/comparison.md"), markdown);
process.stdout.write(markdown);
