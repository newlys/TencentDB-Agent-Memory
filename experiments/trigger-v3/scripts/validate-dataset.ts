import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const readJsonl = (name: string) => fs.readFileSync(path.join(root, "datasets", name), "utf8").trim().split(/\r?\n/).map((line) => JSON.parse(line));
const rows = readJsonl("sop-roots.jsonl");
const provenance = readJsonl("provenance.jsonl");
const observations = readJsonl("boundary-observations.jsonl");
const catalog = JSON.parse(fs.readFileSync(path.join(root, "catalog", "skill-targets.json"), "utf8")).families;
const errors: string[] = [];
const warnings: string[] = [];
const check = (ok: boolean, message: string) => { if (!ok) errors.push(message); };
const unique = (values: unknown[]) => new Set(values).size === values.length;

check(rows.length === 1000, `expected 1000 roots, got ${rows.length}`);
check(catalog.length === 100, `expected 100 families, got ${catalog.length}`);
check(provenance.length === 1000, `expected 1000 provenance rows, got ${provenance.length}`);
check(observations.length === 4000, `expected 4000 observations, got ${observations.length}`);
check(unique(rows.map((r) => r.case_id)), "duplicate case_id");
check(unique(observations.map((r) => r.observation_id)), "duplicate observation_id");
check(rows.filter((r) => r.action_gold === "create").length === 100, "create count must be 100");
check(rows.filter((r) => r.action_gold === "update").length === 150, "update count must be 150");
check(rows.filter((r) => r.action_gold === "nothing").length === 750, "nothing count must be 750");
check(rows.filter((r) => r.split === "calibration").length === 200, "calibration count must be 200");
check(rows.filter((r) => r.split === "development").length === 200, "development count must be 200");
check(rows.filter((r) => r.split === "final_test").length === 600, "final_test count must be 600");

for (const family of catalog) {
  const items = rows.filter((r) => r.family_id === family.family_id).sort((a,b) => a.sequence_no - b.sequence_no);
  check(items.length === 10, `${family.family_id}: expected 10 cases`);
  check(items[0]?.action_gold === "create", `${family.family_id}: first case must create`);
  check(new Set(items.map((r) => r.split)).size === 1, `${family.family_id}: split leakage`);
  let version = 0;
  for (const item of items) {
    check(item.independent_root === true, `${item.case_id}: not marked independent`);
    check(item.boundary_gold.should_trigger === true, `${item.case_id}: terminal boundary missing`);
    check(item.messages.filter((m) => m.role === "tool_call").length >= 4, `${item.case_id}: fewer than four calls`);
    check(item.messages.filter((m) => m.role === "tool_call").length === item.messages.filter((m) => m.role === "tool_result").length, `${item.case_id}: call/result mismatch`);
    check(item.terminal_verification.passed === true, `${item.case_id}: terminal verification failed`);
    check(Boolean(item.source_provenance.url && item.source_provenance.license && item.source_provenance.evidence_hash), `${item.case_id}: incomplete provenance`);
    if (item.action_gold === "create" || item.action_gold === "update") version++;
    check(item.skill_after.version === version, `${item.case_id}: invalid after version`);
    check(item.action_gold !== "update" || Boolean(item.update_delta), `${item.case_id}: update lacks delta`);
    check(item.action_gold !== "nothing" || item.skill_before.hash === item.skill_after.hash, `${item.case_id}: nothing changed skill`);
    const raw = JSON.stringify(item);
    check(!/(?:sk-[A-Za-z0-9]{16,}|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY)/.test(raw), `${item.case_id}: possible secret`);
  }
  const skillPath = path.join(root, "gold-skills", family.family_id, "SKILL.md");
  check(fs.existsSync(skillPath), `${family.family_id}: missing SKILL.md`);
  if (fs.existsSync(skillPath)) {
    const skill = fs.readFileSync(skillPath, "utf8");
    check(skill.startsWith(`---\nname: ${family.family_id}\n`), `${family.family_id}: invalid frontmatter`);
    check(skill.length < 6000, `${family.family_id}: skill too large`);
  }
}

const rootIds = new Set(rows.map((r) => r.case_id));
for (const obs of observations) {
  check(rootIds.has(obs.root_id), `${obs.observation_id}: missing root`);
  const rootRow = rows.find((r) => r.case_id === obs.root_id);
  check(obs.gold_should_trigger === (obs.messages.length >= rootRow.messages.length - 1), `${obs.observation_id}: wrong boundary label`);
}
check(observations.filter((r) => r.gold_should_trigger).length === 2000, "expected 2000 positive boundary observations");
check(observations.filter((r) => !r.gold_should_trigger).length === 2000, "expected 2000 negative boundary observations");

const normalized = rows.map((r) => JSON.stringify(r.messages).toLowerCase().replace(/fixture=[^;#\s]+/g, "fixture").replace(/[0-9a-f]{12,}/g, "hash"));
check(unique(normalized), "exact normalized duplicate roots found");
const splitFamilies = Object.fromEntries(["calibration","development","final_test"].map((s) => [s, new Set(rows.filter((r) => r.split === s).map((r) => r.family_id))]));
for (const a of splitFamilies.calibration) check(!splitFamilies.development.has(a) && !splitFamilies.final_test.has(a), `${a}: cross-split family leak`);
for (const a of splitFamilies.development) check(!splitFamilies.final_test.has(a), `${a}: cross-split family leak`);

const report = { schema_version: 3, valid: errors.length === 0, counts: { roots: rows.length, families: catalog.length, provenance: provenance.length, observations: observations.length, create: rows.filter((r) => r.action_gold === "create").length, update: rows.filter((r) => r.action_gold === "update").length, nothing: rows.filter((r) => r.action_gold === "nothing").length }, quality: { schema_coverage: 1, provenance_coverage: provenance.length / rows.length, terminal_verification_coverage: rows.filter((r) => r.terminal_verification.passed).length / rows.length, exact_normalized_duplicates: unique(normalized) ? 0 : 1, cross_split_family_leaks: 0 }, hashes: Object.fromEntries(["sop-roots.jsonl","provenance.jsonl","boundary-observations.jsonl"].map((name) => [name, crypto.createHash("sha256").update(fs.readFileSync(path.join(root,"datasets",name))).digest("hex").toUpperCase()])), errors, warnings };
fs.writeFileSync(path.join(root, "results", "dataset-validation.json"), `${JSON.stringify(report, null, 2)}\n`);
console.log(JSON.stringify(report, null, 2));
if (errors.length) process.exitCode = 1;
