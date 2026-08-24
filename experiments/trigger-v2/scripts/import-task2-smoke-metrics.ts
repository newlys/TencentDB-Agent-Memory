import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

type Row = { id: string; pass: boolean; turns: number | null; input_tokens: number | null; output_tokens: number | null; cache_read_tokens?: number | null; archive_triggered?: boolean; skill_hit?: boolean };
type Summary = { rows: Row[]; skill_distillation_input_tokens_lower_bound?: number; skill_distillation_requests?: number };
const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");
const mappings = [
  ["baseline", "task2-baseline.json"],
  ["static", "task2-static.json"],
  ["adaptive_v1", "task2-adaptive_v1.json"],
  ["sop_v1", "task2-sop_v1.json"],
] as const;
const taskRuns: unknown[] = [];
const skillEvents: unknown[] = [];
let sequence = 0;
for (const [profile, filename] of mappings) {
  const summary = JSON.parse(fs.readFileSync(path.join(repo, "experiments", "baseline", "results", filename), "utf8")) as Summary;
  for (const row of summary.rows) {
    sequence++;
    taskRuns.push({ profile, taskId: row.id, seed: 0, firstAttempt: true, eligible: true, passed: row.pass, turns: row.turns ?? 0, agentInputTokens: row.input_tokens ?? 0, agentOutputTokens: row.output_tokens ?? 0, cacheMissTokens: 0, sequence });
    if (row.archive_triggered) skillEvents.push({ profile, type: "eligible_boundary", taskId: row.id, sequence });
    // Historical runner only recorded a boolean hit, not the originating
    // skill_id. Preserve task coverage but do not fabricate a reuse numerator.
    if (row.skill_hit) skillEvents.push({ profile, type: "skill_hit", taskId: row.id, skillId: `historical-unattributed-${row.id}`, sequence, actuallyInjected: true });
  }
  if ((summary.skill_distillation_input_tokens_lower_bound ?? 0) > 0) skillEvents.push({ profile, type: "distillation", sequence: ++sequence, inputTokens: summary.skill_distillation_input_tokens_lower_bound, outputTokens: 0 });
}
const output = { schemaVersion: 1, description: "Historical six-case smoke import. Not a powered benchmark; hit skill IDs were unavailable.", taskRuns, skillEvents };
const target = path.join(repo, "experiments", "trigger-v2", "results", "task2-smoke-events.json");
fs.writeFileSync(target, `${JSON.stringify(output, null, 2)}\n`);
console.log(target);
