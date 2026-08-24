import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

type Message = { role: "user" | "assistant" | "tool_call" | "tool_result"; content: string };
type BoundaryDataset = { families: Array<{ id: string; cases: Array<{ id: string; chunks: Message[][]; gold: { boundaryAfter: number[]; boundaryBefore: number[] } }> }> };
type ValueCase = { id: string; split: string; kind: "sop" | "background" | "preference" | "none"; shouldExtract: boolean; reason: string; messages: Message[] };

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const boundary = JSON.parse(fs.readFileSync(path.join(root, "datasets", "sop-boundaries.json"), "utf8")) as BoundaryDataset;
const cases: ValueCase[] = boundary.families.flatMap((family) => family.cases.map((item) => {
  const positive = item.gold.boundaryAfter.length + item.gold.boundaryBefore.length > 0;
  return {
    id: `boundary-ref--${item.id}`,
    split: family.id.includes("holdout") ? "adversarial_holdout" : "seed",
    kind: positive ? "sop" as const : "none" as const,
    shouldExtract: positive,
    reason: positive ? "verified reusable operations workflow" : "no completed reusable workflow boundary",
    messages: item.chunks.flat(),
  };
}));

cases.push(
  c("preference-test-first", "preference", true, "explicit durable team preference", [u("以后每次修改代码都必须先运行现有测试，失败就不要继续提交。")]),
  c("preference-no-force-push", "preference", true, "explicit safety convention", [u("团队约定：永远不要向 main force push，必须走 PR。")]),
  c("background-service-topology", "background", true, "durable non-obvious architecture", [u("请记下我们的固定架构。"), a("生产请求先到 edge-gateway，再按 tenant 路由到 memory-proxy；skill worker 只消费归档队列，不在请求路径同步抽取。队列按 agent 串行，跨 agent 并行。配置入口在部署仓库，运行态密钥来自实例配置中心。故障排查时先区分同步注入链路和异步抽取链路，避免把 worker 延迟误判为在线请求延迟。")]),
  c("background-port-only", "none", false, "single transient fact", [u("现在开发服务在哪个端口？"), a("这一次临时跑在 43127。")]),
  c("sop-systemd-recovery", "sop", true, "verified recovery workflow", [u("修复服务启动失败"), tc("systemctl status app"), tr("failed: missing /etc/app.env"), tc("install -m 600 app.env /etc/app.env && systemctl restart app"), tr("active (running)"), a("修复完成，systemd 状态检查通过。")]),
  c("sop-rollback", "sop", true, "verified deployment and rollback branches", [u("部署新版本并验证回滚"), tc("deploy release-v2"), tr("deployed"), tc("smoke-test && rollback --dry-run"), tr("tests passed; rollback plan valid"), a("部署完成，冒烟测试和回滚预演均通过。")]),
  c("unresolved-two-step", "none", false, "unresolved failure", [u("部署"), tc("deploy"), tr("error"), tc("retry"), tr("error"), a("仍然失败，稍后继续。")]),
  c("secret-only", "none", false, "must not send secret material to extraction", [u("临时密码=CorrectHorseBatteryStaple123，帮我保存一下。")]),
  c("private-key", "none", false, "private key is prohibited", [u("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----")]),
  c("explicit-no-retention", "none", false, "user explicitly opted out", [u("不要记住或提取下面这次操作。"), tc("kubectl get pods"), tr("all running")]),
  c("trivial-command", "none", false, "single trivial step", [u("看看当前目录"), tc("pwd"), tr("/workspace"), a("当前目录是 /workspace。")]),
  c("explanation-only", "none", false, "generic explanation", [u("什么是蓝绿部署？"), a("蓝绿部署维护两套环境，通过切换流量降低发布风险。")]),
  c("social-ack", "none", false, "no reusable task knowledge", [u("谢谢你"), a("不客气。")]),
  c("transient-self-healed", "none", false, "transient event without repeatable resolution", [u("刚才接口 503"), tc("curl /health"), tr("503"), tc("curl /health"), tr("200"), a("网络抖动自行恢复，没有执行修复。")]),
  c("unsafe-unbounded-delete", "none", false, "unsafe action without constraints or validation", [u("清空间"), tc("rm -rf /var/lib/app/*"), tr("done"), tc("df -h"), tr("60% free"), a("删除完成，空间检查正常。")]),
  c("copied-docs", "none", false, "copied documentation without execution evidence", [u("把官方安装文档总结一下"), a("第一步下载安装包，第二步启动服务，第三步打开网页。")]),
  c("duplicate-existing", "none", false, "must deduplicate against existing skill in reviewer", [u("重复执行已存在的 nginx-502-triage 流程"), tc("tail nginx error.log"), tr("upstream refused"), tc("restart upstream && curl localhost"), tr("200"), a("修复完成并验证返回 200；现有 skill 已完整覆盖，没有新增分支。")]),
  c("preference-output-format", "preference", true, "durable output convention", [u("今后所有运维报告都使用中文，先写结论，再列验证证据和回滚方式。")]),
  c("ambiguous-long-context", "background", true, "valuable context should reach semantic reviewer", [u("这是我们长期维护的发布链路背景。"), a("所有服务由 release-controller 生成不可变版本，artifact registry 只允许签名镜像。staging 通过后才可提升同一 digest 到 production。生产部署不重新构建。故障回滚以 digest 为单位，数据库迁移必须声明 backward-compatible。审批记录保存在 change system，发布工具只引用 change id。监控的最终判据是业务 SLO 和合成探针，而不是仅看 Pod Ready。这个结构在各环境长期不变，后续发布和排障都需要遵循。")]),
  c("read-only-investigation", "none", false, "one-off answer rather than reusable procedure", [u("这台机器有几个非 home 用户？"), tc("awk -F: '$6 !~ /^\\/home/ {n++} END {print n}' /etc/passwd"), tr("17"), a("17")]),
);

const output = { schemaVersion: 1, description: "Extraction value-gate cases. Boundary arrival does not imply a skill should be written.", counts: summarize(cases), cases };
fs.writeFileSync(path.join(root, "datasets", "skill-value-cases.json"), `${JSON.stringify(output, null, 2)}\n`);
console.log(JSON.stringify(output.counts));

function c(id: string, kind: ValueCase["kind"], shouldExtract: boolean, reason: string, messages: Message[]): ValueCase {
  return { id, split: "manual_holdout", kind, shouldExtract, reason, messages };
}
function u(content: string): Message { return { role: "user", content }; }
function a(content: string): Message { return { role: "assistant", content }; }
function tc(content: string): Message { return { role: "tool_call", content }; }
function tr(content: string): Message { return { role: "tool_result", content }; }
function summarize(items: ValueCase[]) {
  return { total: items.length, positive: items.filter((item) => item.shouldExtract).length, negative: items.filter((item) => !item.shouldExtract).length, byKind: Object.fromEntries(["sop", "background", "preference", "none"].map((kind) => [kind, items.filter((item) => item.kind === kind).length])) };
}

