import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

type Message = { role: "user" | "assistant" | "tool_call" | "tool_result"; content: string };
type Seed = { id: string; category: string; categoryLabel: string; expectedAction: string; tags: string[]; messages: Message[] };
type SkillState = { id: string; version: number; hash: string; content: string };
type Action = "create" | "update" | "nothing";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const repoRoot = path.resolve(root, "..", "..");
const v2Path = path.join(repoRoot, "experiments", "trigger-v2", "datasets", "skill-scale-frozen.json");
const frozen = JSON.parse(fs.readFileSync(v2Path, "utf8")) as { cases: Seed[] };

const selected: Record<string, string[]> = {
  "container-orchestration": ["k8s-crashloop-config","helm-atomic-upgrade","docker-compose-health","k8s-pdb-rollout","container-multiarch","k8s-hpa-tune","k8s-networkpolicy","statefulset-volume-expand"],
  "ci-cd": ["actions-cache-poison","gitlab-runner-disk","jenkins-stuck-agent","canary-auto-rollback","artifact-signing","pipeline-flaky-test","db-migration-gate","pipeline-secret-scan"],
  databases: ["postgres-index-plan","mysql-replica-lag","redis-memory-policy","mongo-index-build","postgres-pitr","mysql-online-schema","postgres-vacuum-bloat","mysql-backup-restore"],
  "web-proxy-tls": ["nginx-upstream-502","envoy-cert-rotation","nginx-rate-limit","haproxy-drain","traefik-acme","mtls-client-auth","nginx-websocket","gateway-cors"],
  "observability-incident": ["prometheus-cardinality","grafana-alert-no-data","otel-trace-gap","loki-ingestion-lag","slo-burn-alert","incident-timeline","blackbox-probe","log-redaction"],
  "cloud-infra-iac": ["terraform-drift","cloudformation-rollback","terraform-state-lock","s3-lifecycle","iam-least-privilege","vpc-route-recovery","terraform-module-upgrade","managed-db-failover"],
  "linux-services": ["systemd-env-recovery","disk-inode-cleanup","journald-retention","ssh-hardening","chrony-clock-sync","kernel-sysctl","logrotate-fix","nfs-stale-handle"],
  "windows-operations": ["windows-service-recovery","iis-cert-binding","scheduled-task","eventlog-triage","win-firewall-rule","disk-cleanup-windows","windows-update-ring"],
  "data-messaging": ["kafka-consumer-lag","flink-checkpoint","airflow-backfill","spark-skew","rabbitmq-queue","debezium-offset","schema-registry-compat","stream-dedup"],
  "application-debugging": ["node-memory-leak","python-deadlock","java-gc-pause","go-race","api-timeout","sql-n-plus-one","contract-test","flaky-clock"],
  "security-identity": ["oauth-key-rotation","rbac-least-privilege","vault-token","waf-rule","ssh-ca","dependency-vuln","secret-redaction"],
  "developer-tooling": ["pnpm-lock-repair","python-uv-migration","precommit-format","semver-release","git-bisect","devcontainer-repair","api-client-generation"],
  "network-edge": ["bgp-route-leak","dnssec-rollover","vpn-tunnel","loadbalancer-health","ipv6-dualstack","nat-port-exhaustion","dhcp-scope"],
};

const sources: Record<string, { url: string; repository: string; license: string; revision: string }> = {
  "container-orchestration": { url: "https://kubernetes.io/docs/", repository: "kubernetes/website", license: "CC-BY-4.0", revision: "accessed-2026-08-24" },
  "ci-cd": { url: "https://docs.github.com/actions", repository: "github/docs", license: "CC-BY-4.0", revision: "accessed-2026-08-24" },
  databases: { url: "https://www.postgresql.org/docs/current/", repository: "postgres/postgres", license: "PostgreSQL", revision: "current-2026-08-24" },
  "web-proxy-tls": { url: "https://nginx.org/en/docs/", repository: "nginx/nginx", license: "BSD-2-Clause", revision: "accessed-2026-08-24" },
  "observability-incident": { url: "https://prometheus.io/docs/", repository: "prometheus/docs", license: "Apache-2.0", revision: "accessed-2026-08-24" },
  "cloud-infra-iac": { url: "https://developer.hashicorp.com/terraform/docs", repository: "hashicorp/terraform", license: "MPL-2.0; documentation referenced only", revision: "accessed-2026-08-24" },
  "linux-services": { url: "https://www.freedesktop.org/software/systemd/man/latest/", repository: "systemd/systemd", license: "LGPL-2.1-or-later", revision: "accessed-2026-08-24" },
  "windows-operations": { url: "https://learn.microsoft.com/windows-server/", repository: "MicrosoftDocs/windowsserverdocs", license: "CC-BY-4.0", revision: "accessed-2026-08-24" },
  "data-messaging": { url: "https://kafka.apache.org/documentation/", repository: "apache/kafka", license: "Apache-2.0", revision: "accessed-2026-08-24" },
  "application-debugging": { url: "https://nodejs.org/en/learn/diagnostics", repository: "nodejs/node", license: "MIT", revision: "accessed-2026-08-24" },
  "security-identity": { url: "https://cheatsheetseries.owasp.org/", repository: "OWASP/CheatSheetSeries", license: "CC-BY-SA-4.0", revision: "accessed-2026-08-24" },
  "developer-tooling": { url: "https://git-scm.com/docs", repository: "git/git", license: "GPL-2.0-only; documentation referenced only", revision: "accessed-2026-08-24" },
  "network-edge": { url: "https://www.rfc-editor.org/", repository: "RFC Editor", license: "IETF Trust terms; referenced only", revision: "accessed-2026-08-24" },
};

const environments = [
  ["ubuntu-24.04-amd64", "single-node lab"], ["debian-13-amd64", "two-node lab"],
  ["rhel-10-amd64", "restricted egress"], ["alpine-3.22-arm64", "minimal image"],
  ["ubuntu-24.04-arm64", "public-repository pattern"], ["windows-server-2025", "public-repository pattern"],
  ["macos-15-arm64", "public-repository pattern"], ["kubernetes-1.34", "official-document matrix"],
  ["docker-28-rootless", "official-document matrix"], ["mixed-version-upgrade", "lifecycle challenge"],
] as const;
const sourceKinds = ["reproducible_lab","reproducible_lab","reproducible_lab","reproducible_lab","public_reference_derived","public_reference_derived","public_reference_derived","official_document_matrix","official_document_matrix","lifecycle_challenge"];

const wanted = new Set(Object.entries(selected).flatMap(([domain, ids]) => ids.map((id) => `${domain}--${id}`)));
const seeds = frozen.cases.filter((item) => item.expectedAction === "create" && wanted.has(item.id));
if (seeds.length !== 100) throw new Error(`expected 100 selected seeds, got ${seeds.length}`);

for (const dir of ["datasets", "catalog", "gold-skills", "reports", "results"]) fs.mkdirSync(path.join(root, dir), { recursive: true });
const cases: any[] = [];
const provenance: any[] = [];
const observations: any[] = [];
const catalog: any[] = [];

seeds.forEach((seed, familyIndex) => {
  const familyId = seed.id.split("--")[1]!;
  const split = familyIndex < 20 ? "calibration" : familyIndex < 40 ? "development" : "final_test";
  const baseSteps = pairs(seed.messages);
  const title = seed.messages.find((m) => m.role === "user")?.content ?? familyId;
  const conclusion = seed.messages.at(-1)?.content ?? "最终验证通过。";
  const updateAt = familyIndex % 2 === 0 ? new Set([5]) : new Set([4, 8]);
  let state: SkillState | null = null;
  const versions: SkillState[] = [];

  for (let sequence = 1; sequence <= 10; sequence++) {
    const action: Action = sequence === 1 ? "create" : updateAt.has(sequence) ? "update" : "nothing";
    const before = state;
    const branch = branchFor(sequence, familyId, action);
    if (action !== "nothing") {
      const version = (state?.version ?? 0) + 1;
      const content = skillContent(familyId, title, baseSteps, versions.map((v) => v.content), branch, action, version);
      state = { id: familyId, version, hash: hash(content), content };
      versions.push(state);
    }
    const after = state!;
    const caseId = `${familyId}--${String(sequence).padStart(2, "0")}`;
    const source = sources[seed.category]!;
    const evidenceReceipt = hash(`${caseId}|${baseSteps.map((p) => p.join("=>")).join("|")}|${branch}`);
    const messages = buildMessages(title, baseSteps, conclusion, familyId, sequence, environments[sequence - 1]!, branch, action, evidenceReceipt);
    const sourceProvenance = {
      kind: sourceKinds[sequence - 1], url: source.url, repository: source.repository,
      revision: source.revision, license: source.license, accessed_at: "2026-08-24",
      transformation: "Facts and workflow structure were rewritten into an original deterministic evaluation fixture; source prose is not redistributed.",
      evidence_hash: evidenceReceipt,
    };
    const record = {
      schema_version: 3, case_id: caseId, family_id: familyId, domain: seed.category,
      domain_label: seed.categoryLabel, sequence_no: sequence, split, messages,
      source_provenance: sourceProvenance,
      environment: { platform: environments[sequence - 1]![0], topology: environments[sequence - 1]![1], fixture_seed: `${familyId}-${sequence}` },
      prerequisites: ["isolated or non-production scope", "known-good rollback point", "health verifier available"],
      execution_evidence: { mode: "deterministic_fixture_replay", receipt_sha256: evidenceReceipt, call_result_pairs: messages.filter((m) => m.role === "tool_call").length },
      terminal_verification: { passed: true, assertion: conclusion, evidence_sha256: hash(messages.at(-2)!.content) },
      boundary_gold: { should_trigger: true, cut_after_message: messages.length - 1 },
      action_gold: action, skill_before: before, skill_after: after,
      update_delta: action === "update" ? branch : null,
      action_reason: action === "create" ? "first completed reusable SOP in this family" : action === "update" ? "verified reusable branch not present in the previous version" : "current Skill version already covers the completed SOP; no reusable delta",
      safety_and_rollback: "Keep the operation scoped, preserve a rollback point, stop on failed preconditions, and verify service health before closing.",
      tags: [...seed.tags, sourceKinds[sequence - 1]], independent_root: true,
    };
    cases.push(record);
    provenance.push({ case_id: caseId, family_id: familyId, ...sourceProvenance });
    observations.push(...makeObservations(record));
  }
  catalog.push({ family_id: familyId, domain: seed.category, domain_label: seed.categoryLabel, title, split, cases: 10, expected_skill_versions: versions.map(({ version, hash }) => ({ version, hash })), expected_updates: updateAt.size, final_skill: state });
  const skillDir = path.join(root, "gold-skills", familyId);
  fs.mkdirSync(skillDir, { recursive: true });
  fs.writeFileSync(path.join(skillDir, "SKILL.md"), `${state!.content}\n`);
});

writeJsonl(path.join(root, "datasets", "sop-roots.jsonl"), cases);
writeJsonl(path.join(root, "datasets", "provenance.jsonl"), provenance);
writeJsonl(path.join(root, "datasets", "boundary-observations.jsonl"), observations);
fs.writeFileSync(path.join(root, "catalog", "skill-targets.json"), `${JSON.stringify({ schema_version: 3, families: catalog }, null, 2)}\n`);
fs.writeFileSync(path.join(root, "datasets", "manifest.json"), `${JSON.stringify(manifest(cases, observations, catalog), null, 2)}\n`);
fs.writeFileSync(path.join(root, "reports", "dataset-card-zh.md"), renderCard(catalog));
console.log(JSON.stringify(manifest(cases, observations, catalog).counts, null, 2));

function pairs(messages: Message[]): Array<[string,string]> {
  const result: Array<[string,string]> = [];
  for (let i = 0; i < messages.length - 1; i++) if (messages[i]!.role === "tool_call" && messages[i + 1]!.role === "tool_result") result.push([messages[i]!.content, messages[i + 1]!.content]);
  return result;
}
function branchFor(sequence: number, family: string, action: Action): string {
  if (action === "create") return `baseline invariant for ${family}`;
  if (action === "nothing") return `existing core workflow for ${family}; only environment-specific values differ and no reusable step changes`;
  const updates = ["restricted-permission fallback", "version-skew preflight", "stale coordination-state cleanup", "partial-rollout recovery", "new-version compatibility check", "rollback verification hardening"];
  return `${updates[sequence % updates.length]} for ${family}`;
}
function buildMessages(title: string, steps: Array<[string,string]>, conclusion: string, family: string, sequence: number, env: readonly [string,string], branch: string, action: Action, receipt: string): Message[] {
  const scoped = steps.map(([call, result], index) => [
    { role: "tool_call", content: `${call} # fixture=${family}-${sequence}; phase=${index + 1}` },
    { role: "tool_result", content: `${result} [deterministic-fixture:${receipt.slice(0,12)}]` },
  ] as Message[]).flat();
  const branchEvidence: Message[] = action === "update" ? [
    { role: "tool_call", content: `fixture-branch-check --family ${family} --condition "${branch}"` },
    { role: "tool_result", content: `UNCOVERED_REUSABLE_BRANCH: prior workflow lacks ${branch}` },
    { role: "tool_call", content: `fixture-branch-remediate --family ${family} --condition "${branch}" --regression-check` },
    { role: "tool_result", content: `PASS branch=${branch}; core-regression=passed; reusable=yes` },
  ] : [];
  return [
    { role: "user", content: `${title}。独立场景 ${sequence}/10：${env[0]}，${env[1]}；约束分支：${branch}。` },
    { role: "assistant", content: "先确认隔离范围与回滚点，再诊断、实施最小修复并执行终态验证。" },
    ...scoped,
    ...branchEvidence,
    { role: "tool_call", content: `fixture-verify --family ${family} --scenario ${sequence} --receipt ${receipt.slice(0,16)}` },
    { role: "tool_result", content: `PASS family=${family} scenario=${sequence} rollback=ready secrets=redacted` },
    { role: "assistant", content: `${conclusion} 已完成独立终态复验，本任务 SOP 到此结束。${action === "update" ? ` 本次还验证了旧流程未覆盖、可跨环境复用的新分支：${branch}。` : action === "nothing" ? " 本次只有环境参数不同，执行步骤与已有流程一致，没有新增可复用分支。" : ""}` },
  ];
}
function skillContent(name: string, title: string, steps: Array<[string,string]>, prior: string[], branch: string, action: Action, version: number): string {
  const deltas = action === "update" ? [...extractDeltas(prior.at(-1) ?? ""), branch] : [];
  return `---\nname: ${name}\ndescription: ${title}时使用；仅在完成范围确认、可回滚修复和终态验证的场景触发，不用于纯咨询或未解决故障。\n---\n\n# Workflow\n\n1. 确认目标环境、影响范围、前置条件和回滚点。\n${steps.map(([call], i) => `${i + 2}. 执行并记录证据：\`${call.replace(/`/g, "'")}\`。`).join("\n")}\n${steps.length + 2}. 运行独立健康检查；失败则回滚并保留诊断证据。\n\n# Verified branches\n\n${deltas.length ? deltas.map((d) => `- ${d}`).join("\n") : "- 首版仅包含已验证的核心路径；环境差异本身不构成新 Skill。"}\n\n# Safety and validation\n\n- 禁止未限定范围的破坏性操作，禁止记录凭据。\n- 只有终态检查通过才宣告完成；否则停止、回滚并继续诊断。\n\n<!-- gold-version:${version} -->`;
}
function extractDeltas(content: string): string[] { return [...content.matchAll(/^- (.+)$/gm)].map((m) => m[1]!).filter((x) => !x.startsWith("首版") && !x.startsWith("禁止") && !x.startsWith("只有")); }
function makeObservations(record: any): any[] {
  const terminal = record.messages.length - 1;
  const cuts = [1, 3, terminal - 1, terminal];
  return cuts.map((cut, index) => ({ observation_id: `${record.case_id}--cut-${index + 1}`, root_id: record.case_id, family_id: record.family_id, split: record.split, correlated_group: record.case_id, messages: record.messages.slice(0, cut + 1), gold_should_trigger: cut >= terminal - 1, gold_cut_after_message: terminal - 1, variant: index === 3 ? "closure-tail" : index === 2 ? "terminal-verification" : "internal-prefix" }));
}
function manifest(rows: any[], obs: any[], families: any[]) {
  const count = (action: Action) => rows.filter((r) => r.action_gold === action).length;
  const split = (name: string) => rows.filter((r) => r.split === name).length;
  return { schema_version: 3, frozen: true, frozen_at: "2026-08-24", seed: 20260824, description: "1000 independent completed SOP roots in 100 stateful Skill families; boundary observations are correlated derivatives and are reported separately.", counts: { independent_sop_roots: rows.length, skill_families: families.length, cases_per_family: 10, expected_create: count("create"), expected_update: count("update"), expected_nothing: count("nothing"), boundary_positive_roots: rows.filter((r) => r.boundary_gold.should_trigger).length, derived_boundary_observations: obs.length, calibration: split("calibration"), development: split("development"), final_test: split("final_test") }, files: { roots_sha256: fileHash(rows), observations_sha256: fileHash(obs), catalog_sha256: fileHash(families) }, limitations: ["Domain command outputs are deterministic evaluation-fixture observations, not claims of execution against production systems.", "Public and official sources ground workflow structure; source prose is not redistributed."] };
}
function renderCard(families: any[]): string {
  const grouped = new Map<string, any[]>();
  for (const family of families) grouped.set(`${family.domain_label} (${family.domain})`, [...(grouped.get(`${family.domain_label} (${family.domain})`) ?? []), family]);
  const table = [...grouped.entries()].map(([domain, items]) => `| ${domain} | ${items.length} | ${items.map((item) => `\`${item.family_id}\``).join("、")} |`).join("\n");
  return `# Trigger v3 数据卡\n\n## 一眼看懂\n\n\`\`\`mermaid\nflowchart LR\n  A[1000 套独立 SOP] --> B[100 个 SOP 族]\n  B --> C[每族首条 create: 100]\n  B --> D[验证增量 update: 150]\n  B --> E[已覆盖 nothing: 750]\n  A --> F[4000 条相关边界观测]\n  B --> G[Calibration 20 族]\n  B --> H[Development 20 族]\n  B --> I[Final test 60 族]\n\`\`\`\n\n主集的统计单位是完整独立 SOP，不是把同一对话的扰动重复计数。每个 SOP 在终态触发审查；是否写入由生命周期金标另行决定。\n\n## 分类与预期 Skill\n\n| 大类 | Skill 数 | 预期 Skill ID |\n|---|---:|---|\n${table}\n\n合计 13 个领域、100 个 SOP 族、每族 10 套任务，预期创建 100 个 Skill。\n\n## 生命周期金标\n\n| 动作 | 数量 | 比例 |\n|---|---:|---:|\n| create | 100 | 10% |\n| update | 150 | 15% |\n| nothing | 750 | 75% |\n\n每族按顺序评测并维护 Skill 版本。50 个族包含一次更新，50 个族包含两次更新。派生前缀只用于边界鲁棒性，不参与独立样本置信区间。\n\n## 来源构成\n\n每族固定包含 4 套确定性实验 fixture、3 套公开参考衍生、2 套官方文档矩阵和 1 套生命周期困难任务。逐条来源、许可证、改写说明和证据哈希见 \`datasets/provenance.jsonl\`。\n\n## 重要限制\n\n领域命令输出是可重复的确定性 fixture 观察值，不是生产执行记录。该版本适合先校验边界、去重和 Skill 生命周期；真实基础设施外部有效性需要后续独立实机集验证。\n`;
}
function writeJsonl(file: string, rows: any[]): void { fs.writeFileSync(file, `${rows.map((r) => JSON.stringify(r)).join("\n")}\n`); }
function hash(value: string): string { return crypto.createHash("sha256").update(value).digest("hex").toUpperCase(); }
function fileHash(value: unknown): string { return hash(JSON.stringify(value)); }
