/** Zero-token SOP boundary detection for Skill extraction. */

export type SopBoundaryProfile = "legacy" | "sop_v1";
export type SopBoundaryPhase = "none" | "before_append" | "after_append";

export interface SopBoundaryMessage {
  role: string;
  content: string;
  tool_name?: string;
  tool_call_id?: string;
}

export interface SopBoundaryConfig {
  profile: SopBoundaryProfile;
  minToolCalls: number;
  completionScoreThreshold: number;
  topicSwitchScoreThreshold: number;
}

export const DEFAULT_SOP_BOUNDARY_CONFIG: SopBoundaryConfig = {
  profile: "legacy",
  minToolCalls: 2,
  completionScoreThreshold: 0.68,
  topicSwitchScoreThreshold: 0.72,
};

export interface SopBoundaryInput {
  bufferedMessages: SopBoundaryMessage[];
  incomingMessages: SopBoundaryMessage[];
}

export interface SopBoundaryDecision {
  phase: SopBoundaryPhase;
  score: number;
  signals: string[];
}

const COMPLETION_RE = /(?:部署|配置|修复|恢复|备份|迁移|升级|安装|流程|任务).{0,12}(?:完成|成功)|(?:已|已经)(?:完成|修复|恢复|部署|配置|解决)|(?:已|后|结果|最终).{0,48}(?:通过|成功|正常|恢复|完成|稳定|生效|收敛|归零|降至|降到|一致|正确|可用|可访问|阻断|拒绝|在线|连通|达标|存在|保留|运行|发布|持久化|启用)|completed successfully|successfully (?:deployed|installed|configured|restored|fixed)|all checks? pass(?:ed)?/iu;
const OUTCOME_RE = /完成|成功|通过|正常|恢复|健康|生效|稳定|收敛|归零|降至|降到|一致|正确|可用|可访问|阻断|拒绝|在线|连通|达标|存在|保留|运行|发布|持久化|启用|结束|上线|有效|验证|更新|上传|激活|最小权限|拿到|完整|扩容|不会变更|已(?:创建|删除|摘除|应用|写入|替换|定位)|无(?:错误|差异|变更|误报|误杀|漏洞|中断|冲突)|\b(?:completed|fixed|resolved|deployed|restored|succeeded|healthy|passed|verified|valid|ready|running|active)\b/iu;
const VERIFICATION_RE = /(?:验证|检查|测试|health|healthy|rollout|dry[- ]?run|冒烟|连通性|返回|状态|复检|回放|探针|压测).{0,28}(?:通过|成功|正常|ok|200|healthy|一致|稳定|达标)|(?:http(?:\/\d(?:\.\d)?)?\s*200|status(?:code)?.{0,8}(?:ok|200)|exit (?:code )?0|test is successful|successfully rolled out|\b(?:passed|healthy|verified|valid|ready|running|active|condition met)\b|(?:errors?|failures?|findings?|conflicts?)\s*[=:]?\s*0|0% (?:loss|errors?))/iu;
const IMPLICIT_SUCCESS_RE = /(?:健康检查|验证|测试|连通性|探针|压测|复检).{0,20}(?:通过|成功|正常|稳定|达标)|服务.{0,8}(?:可用|正常|健康)|(?:返回|status(?:code)?).{0,8}(?:200|ok)|exit (?:code )?0|\b(?:passed|healthy|verified|valid|ready|running|active)\b/iu;
const FAILURE_RE = /\b(?:error|failed|failure|inactive|denied|refused|timeout|timed out)\b|失败|报错|错误|未完成|尚未|不能|无法|需(?:要)?先|稍后再|停在这里/iu;
const CONTINUATION_RE = /下一步|接下来|还需|尚需|待验证|稍后继续|继续(?:处理|排查|执行|验证)|before (?:we|it) can|next[, ]/iu;
const RESOLVED_FAILURE_RE = /(?:失败率|错误率|丢包率).{0,12}(?:归零|降至|降到|恢复|0)|无(?:错误|失败|冲突|丢包)|\b(?:errors?|failures?|loss)\s*[=:]?\s*0\b/iu;
const NEW_TASK_RE = /^(?:另外|新任务|换个任务|接下来请|现在请|再帮我|unrelated|new task)|和.{0,20}无关/iu;
const EXECUTION_RE = /(?:^|\s)(?:docker|kubectl|helm|curl|systemctl|service|nginx|certbot|pg_dump|pg_restore|psql|podman|logrotate|ssh|npm|pnpm|yarn|python|pytest|git)\b|(?:\.\/|\/etc\/|\/var\/)/iu;

/**
 * Detect a reusable workflow boundary. `before_append` prevents a new task from
 * contaminating the previous SOP; `after_append` closes a verified workflow.
 */
export function evaluateSopBoundary(
  input: SopBoundaryInput,
  override: Partial<SopBoundaryConfig> = {},
): SopBoundaryDecision {
  const cfg = { ...DEFAULT_SOP_BOUNDARY_CONFIG, ...override };
  if (cfg.profile === "legacy") return noBoundary();

  const bufferedToolCalls = countToolCalls(input.bufferedMessages);
  const incomingFirst = input.incomingMessages[0];
  if (bufferedToolCalls >= cfg.minToolCalls && incomingFirst?.role === "user") {
    const priorText = joinedContent(input.bufferedMessages.slice(-6));
    const incomingText = String(incomingFirst.content ?? "").trim();
    const explicitSwitch = NEW_TASK_RE.test(incomingText);
    const priorVerified = IMPLICIT_SUCCESS_RE.test(priorText) && !hasUnresolvedFailure(priorText);
    const similarity = lexicalSimilarity(priorText, incomingText);
    let score = 0;
    const signals: string[] = [];
    if (explicitSwitch) {
      score += 0.48;
      signals.push("explicit_topic_switch");
    }
    if (priorVerified) {
      score += 0.34;
      signals.push("prior_verified_outcome");
    }
    if (similarity < 0.08) {
      score += 0.12;
      signals.push("low_lexical_overlap");
    }
    if (score >= cfg.topicSwitchScoreThreshold) {
      return { phase: "before_append", score: roundScore(score), signals };
    }
  }

  const combined = [...input.bufferedMessages, ...input.incomingMessages];
  const toolCalls = countToolCalls(combined);
  if (toolCalls < cfg.minToolCalls) return noBoundary();

  const last = lastSubstantive(combined);
  // Successful tool output alone is commonly an intermediate step. Wait for
  // the assistant's conclusion before extracting.
  if (!last || last.role !== "assistant") return noBoundary();

  const tail = joinedContent(combined.slice(-8));
  const conclusion = String(last.content ?? "");
  let score = 0;
  const signals: string[] = [];
  if (COMPLETION_RE.test(conclusion) || OUTCOME_RE.test(conclusion)) {
    score += 0.36;
    signals.push("explicit_completion");
  }
  if (VERIFICATION_RE.test(tail)) {
    score += 0.28;
    signals.push("verification_evidence");
  }
  score += 0.18;
  signals.push("multi_step_execution");
  if (countDistinctCommands(combined) >= 2 || toolCalls >= 3) {
    score += 0.15;
    signals.push("reusable_sequence");
  }
  if (FAILURE_RE.test(conclusion) && !RESOLVED_FAILURE_RE.test(conclusion)) {
    score -= 0.38;
    signals.push("unresolved_failure");
  }
  if (CONTINUATION_RE.test(conclusion)) {
    score -= 0.32;
    signals.push("continuation_pending");
  }

  if (score >= cfg.completionScoreThreshold) {
    return { phase: "after_append", score: roundScore(score), signals };
  }
  return { phase: "none", score: roundScore(score), signals };
}

export function countToolCalls(messages: SopBoundaryMessage[]): number {
  return messages.reduce((n, message) => n + (message.role === "tool_call" ? 1 : 0), 0);
}

function noBoundary(): SopBoundaryDecision {
  return { phase: "none", score: 0, signals: [] };
}

function lastSubstantive(messages: SopBoundaryMessage[]): SopBoundaryMessage | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    if (message && String(message.content ?? "").trim()) return message;
  }
  return undefined;
}

function joinedContent(messages: SopBoundaryMessage[]): string {
  return messages.map((message) => String(message.content ?? "")).join("\n");
}

function hasUnresolvedFailure(text: string): boolean {
  const tail = text.split(/\r?\n/).filter(Boolean).slice(-3).join("\n");
  return FAILURE_RE.test(tail) && !IMPLICIT_SUCCESS_RE.test(tail);
}

function countDistinctCommands(messages: SopBoundaryMessage[]): number {
  const commands = new Set<string>();
  for (const message of messages) {
    if (message.role !== "tool_call") continue;
    const content = String(message.content ?? "").trim();
    const parts = content.match(/[A-Za-z0-9_.-]+/g)?.slice(0, 2).map((part) => part.toLowerCase()) ?? [];
    const first = parts.join(":");
    // Tool names in real agent traces are open-ended (cloud CLIs, internal
    // control planes, PowerShell cmdlets). Distinct executed calls are useful
    // sequence evidence even when their binary is not in a static allow-list.
    if (first && (EXECUTION_RE.test(content) || content.length >= 3)) commands.add(first);
  }
  return commands.size;
}

function lexicalSimilarity(a: string, b: string): number {
  const left = tokens(a);
  const right = tokens(b);
  if (left.size === 0 || right.size === 0) return 0;
  let intersection = 0;
  for (const token of left) if (right.has(token)) intersection++;
  return intersection / (left.size + right.size - intersection);
}

function tokens(text: string): Set<string> {
  const values = text.toLowerCase().match(/[a-z][a-z0-9_.-]{2,}|[\p{Script=Han}]{2,}/gu) ?? [];
  return new Set(values.filter((value) => !/^(?:the|and|with|this|that|已经|完成|任务|帮我)$/.test(value)));
}

function roundScore(value: number): number {
  return Math.round(Math.max(0, Math.min(1, value)) * 1000) / 1000;
}
