import { describe, expect, it } from "vitest";
import { evaluateSopBoundary, type SopBoundaryMessage } from "../conversation-add/sop-boundary.js";
import { resolveSkillConfig } from "../skill-config.js";

const cfg = { profile: "sop_v1" as const };
const tool = (id: string, content: string): SopBoundaryMessage => ({ role: "tool_call", tool_call_id: id, content });

describe("evaluateSopBoundary", () => {
  it("detects verified multi-step completion after append", () => {
    const decision = evaluateSopBoundary({
      bufferedMessages: [{ role: "user", content: "deploy" }, tool("1", "docker compose up -d")],
      incomingMessages: [tool("2", "curl localhost/health"), { role: "tool_result", content: "HTTP 200 status OK" }, { role: "assistant", content: "部署完成，健康检查通过。" }],
    }, cfg);
    expect(decision.phase).toBe("after_append");
    expect(decision.signals).toContain("verification_evidence");
  });

  it("rejects intermediate success", () => {
    const decision = evaluateSopBoundary({
      bufferedMessages: [tool("1", "helm upgrade --install app ./chart")],
      incomingMessages: [tool("2", "kubectl rollout status deploy/app"), { role: "tool_result", content: "successfully rolled out" }, { role: "assistant", content: "rollout 成功，下一步还需验证 Ingress。" }],
    }, cfg);
    expect(decision.phase).toBe("none");
    expect(decision.signals).toContain("continuation_pending");
  });

  it("rejects unresolved failure", () => {
    const decision = evaluateSopBoundary({ bufferedMessages: [tool("1", "docker compose up"), tool("2", "curl localhost")], incomingMessages: [{ role: "assistant", content: "部署失败，端口仍被占用，需要稍后再处理。" }] }, cfg);
    expect(decision.phase).toBe("none");
  });

  it("recognizes verified outcomes expressed without the small legacy verb list", () => {
    const decision = evaluateSopBoundary({
      bufferedMessages: [tool("1", "wafctl apply sqli-strict --mode count"), { role: "tool_result", content: "false positives 0" }],
      incomingMessages: [tool("2", "wafctl set-mode sqli-strict block && wafctl test"), { role: "tool_result", content: "20/20 blocked; benign 20/20 allowed" }, { role: "assistant", content: "规则经观察后转为阻断模式，恶意请求全阻断且无良性误杀。" }],
    }, cfg);
    expect(decision.phase).toBe("after_append");
    expect(decision.signals).toContain("reusable_sequence");
  });

  it("keeps boundary timing separate from later unsafe-value rejection", () => {
    const decision = evaluateSopBoundary({
      bufferedMessages: [tool("1", "force-delete --all --no-backup"), { role: "tool_result", content: "deleted" }],
      incomingMessages: [tool("2", "category-health-check"), { role: "tool_result", content: "healthy" }, { role: "assistant", content: "操作结束，但缺少范围、备份和回滚约束，不应复用。" }],
    }, cfg);
    expect(decision.phase).toBe("after_append");
  });

  it("archives before an explicit unrelated task", () => {
    const decision = evaluateSopBoundary({
      bufferedMessages: [tool("1", "docker compose up"), tool("2", "curl localhost/health"), { role: "assistant", content: "健康检查通过，服务已经可用。" }],
      incomingMessages: [{ role: "user", content: "新任务：升级 Redis 7，和当前服务无关。" }],
    }, cfg);
    expect(decision.phase).toBe("before_append");
  });

  it("is inert in legacy mode", () => {
    const decision = evaluateSopBoundary({ bufferedMessages: [tool("1", "docker up")], incomingMessages: [tool("2", "curl localhost"), { role: "assistant", content: "部署完成，验证返回 200。" }] }, { profile: "legacy" });
    expect(decision).toEqual({ phase: "none", score: 0, signals: [] });
  });
});

describe("SOP trigger config", () => {
  const probe = { hasTcvdbCredentials: false, hasCosCredentials: false, embeddingAvailable: false, llmRunnerAvailable: true };
  const logger = { info() {}, warn() {}, error() {} };

  it("preserves legacy timing by default", () => {
    const config = resolveSkillConfig({ enabled: true }, probe, logger)!;
    expect(config.extraction.trigger).toMatchObject({ profile: "legacy", minToolCalls: 2 });
    expect(config.extraction.valueGate.profile).toBe("legacy");
    expect(config.extraction.reviewPromptProfile).toBe("legacy_v2");
  });

  it("resolves sop_v1 overrides", () => {
    const config = resolveSkillConfig({ enabled: true, extraction: { trigger: { profile: "sop_v1", minToolCalls: 3, completionScoreThreshold: 0.76 } } }, probe, logger)!;
    expect(config.extraction.trigger).toMatchObject({ profile: "sop_v1", minToolCalls: 3, completionScoreThreshold: 0.76, topicSwitchScoreThreshold: 0.72 });
  });

  it("enables precision extraction stages independently", () => {
    const config = resolveSkillConfig({ enabled: true, extraction: { valueGate: { profile: "precision_v1" }, reviewPromptProfile: "precision_v3" } }, probe, logger)!;
    expect(config.extraction.valueGate.profile).toBe("precision_v1");
    expect(config.extraction.reviewPromptProfile).toBe("precision_v3");
  });
});
