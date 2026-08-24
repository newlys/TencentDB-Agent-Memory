import type { ExtractMessage } from "./types.js";

export type SkillValueGateProfile = "legacy" | "precision_v1";
export type SkillValueDecision = "extract" | "review" | "skip";

export interface SkillValueGateResult {
  decision: SkillValueDecision;
  score: number;
  signals: string[];
}

const SECRET_RE = /(?:api[_-]?key|access[_-]?token|private[_-]?key|password|passwd|secret)\s*[:=]\s*['"]?[A-Za-z0-9_\-\/.+=]{8,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/iu;
const DO_NOT_RETAIN_RE = /不要(?:记住|保存|沉淀|提取)|do not (?:remember|save|retain)|forget this/iu;
const PREFERENCE_RE = /(?:以后|今后|始终|每次|务必|请记住|团队约定).{0,30}(?:要|不要|使用|先|必须)|\b(?:always|never|every time|please remember)\b/iu;
const DURABLE_CONTEXT_RE = /(?:架构|拓扑|固定|约定|规范|标准流程|长期|入口|依赖关系|canonical|architecture|convention|workflow)/iu;
const COMPLETION_RE = /(?:完成|成功|已修复|已恢复|已部署|解决|通过|正常|健康|生效|稳定|收敛|归零|降至|降到|一致|正确|可用|可访问|阻断|拒绝|在线|连通|达标|存在|保留|运行|发布|持久化|启用|已(?:创建|删除|摘除|应用|写入|替换|定位)|无(?:错误|差异|变更|误报|误杀|漏洞|中断))|\b(?:completed|fixed|resolved|deployed|restored|succeeded|healthy|passed|verified|valid|ready|running|active)\b/iu;
const VERIFY_RE = /(?:验证|测试|检查|冒烟|健康|返回|复检|压测|探针|回放).{0,28}(?:通过|正常|成功|200|ok|稳定|达标|一致)|(?:无(?:错误|误报|误杀|漏洞|中断)|错误率归零|失败率归零)|\b(?:verified|tests? pass(?:ed)?|health(?:y)?|http 200|status(?:code)? 200|exit 0|pong|errors? 0|failures? 0|findings? 0)\b/iu;
const FAILURE_RE = /(?:失败|错误|报错|尚未|未完成|不能|无法|稍后)|\b(?:failed|error|denied|refused|unresolved|pending|not complete)\b/iu;
const EXPECTED_REJECTION_RE = /(?:预期|应当|成功|正确).{0,20}(?:拒绝|阻断|429|denied)|(?:拒绝|阻断).{0,20}(?:符合|预期|正确)|(?:合法|允许|私网).{0,28}(?:通过|allowed).{0,40}(?:拒绝|阻断|denied)|(?:可用|保留|发布).{0,28}(?:拒绝|阻断|denied)|(?:公网|恶意|无证|未签名).{0,24}(?:拒绝|阻断|无授权|denied)|\b(?:denied|blocked|rejected|429).{0,24}(?:as expected|expected|while .* allowed)\b/iu;
const ZERO_FAILURE_RE = /(?:errors?|failures?|findings?|conflicts?|drops?|error rate)\s*[=:]?\s*0\b|\b0(?:\.0+)?%\s*(?:errors?|failures?|loss)\b|无(?:错误|失败|冲突|丢包)/iu;
const UNSAFE_RE = /(?:不需要|无需).{0,12}(?:确认|备份|范围|审批)|(?:缺少|没有).{0,16}(?:范围|备份|回滚|约束)|\b(?:force-delete\s+--all|--no-backup|rm\s+-rf\s+\/(?:\s|$)|terraform\s+apply\s+-auto-approve)\b/iu;

/** Cheap pre-LLM gate that only auto-skips high-confidence negatives. */
export function evaluateSkillValue(
  messages: ExtractMessage[],
  profile: SkillValueGateProfile = "legacy",
): SkillValueGateResult {
  if (profile === "legacy") return { decision: "review", score: 0.5, signals: ["legacy_passthrough"] };

  const text = messages.map((message) => message.content ?? "").join("\n");
  const tail = messages.slice(-5).map((message) => message.content ?? "").join("\n");
  const toolCalls = messages.filter((message) => message.role === "tool_call").length;

  if (DO_NOT_RETAIN_RE.test(text)) return { decision: "skip", score: 0, signals: ["explicit_no_retention"] };
  if (SECRET_RE.test(text)) return { decision: "skip", score: 0, signals: ["secret_material"] };
  if (UNSAFE_RE.test(text)) return { decision: "skip", score: 0.02, signals: ["unsafe_unscoped_action"] };
  if (PREFERENCE_RE.test(text)) return { decision: "extract", score: 0.9, signals: ["explicit_durable_preference"] };

  if (toolCalls >= 2) {
    const baseSignals = ["multi_step_execution"];
    const failed = FAILURE_RE.test(tail) && !EXPECTED_REJECTION_RE.test(tail) && !ZERO_FAILURE_RE.test(tail);
    const completed = COMPLETION_RE.test(tail);
    const verified = VERIFY_RE.test(tail);
    if (failed && !(completed && verified)) {
      return { decision: "skip", score: 0.12, signals: [...baseSignals, "unresolved_failure"] };
    }
    if (completed && verified) {
      return { decision: "extract", score: 0.92, signals: [...baseSignals, "verified_completion"] };
    }
    return { decision: "review", score: 0.55, signals: [...baseSignals, "ambiguous_outcome"] };
  }

  // Chinese encodes substantially more information per character than English;
  // 120 chars is enough for a non-trivial architecture/context candidate.
  if (DURABLE_CONTEXT_RE.test(text) && text.length >= 120) {
    return { decision: "review", score: 0.58, signals: ["possible_durable_context"] };
  }
  if (toolCalls <= 1 && text.length < 4000) {
    return { decision: "skip", score: 0.1, signals: [toolCalls === 0 ? "no_execution_evidence" : "single_trivial_step"] };
  }
  return { decision: "review", score: 0.5, signals: ["ambiguous_value"] };
}
