import { describe, expect, it } from "vitest";
import { evaluateSkillValue } from "../skill-value-gate.js";
import type { ExtractMessage } from "../types.js";
import { SkillExtractor } from "../skill-extractor.js";

const run = (messages: ExtractMessage[]) => evaluateSkillValue(messages, "precision_v1");

describe("evaluateSkillValue", () => {
  it("passes a verified multi-step SOP", () => {
    expect(run([{ role: "tool_call", content: "docker compose up -d" }, { role: "tool_result", content: "started" }, { role: "tool_call", content: "curl /health" }, { role: "tool_result", content: "HTTP 200" }, { role: "assistant", content: "部署完成，健康检查通过。" }])).toMatchObject({ decision: "extract" });
  });
  it("skips unresolved failures", () => {
    expect(run([{ role: "tool_call", content: "deploy" }, { role: "tool_call", content: "verify" }, { role: "assistant", content: "验证失败，问题尚未解决。" }])).toMatchObject({ decision: "skip" });
  });
  it("captures explicit durable preferences without tools", () => {
    expect(run([{ role: "user", content: "以后每次改代码都必须先运行现有测试。" }])).toMatchObject({ decision: "extract" });
  });
  it("never sends secrets to the extraction LLM", () => {
    expect(run([{ role: "user", content: "api_key=sk-test-1234567890abcdef" }])).toMatchObject({ decision: "skip", signals: ["secret_material"] });
  });
  it("skips unsafe unscoped destructive workflows", () => {
    expect(run([{ role: "user", content: "不需要确认范围或备份。" }, { role: "tool_call", content: "force-delete --all --no-backup" }, { role: "tool_result", content: "deleted" }, { role: "tool_call", content: "health" }, { role: "tool_result", content: "healthy" }])).toMatchObject({ decision: "skip", signals: ["unsafe_unscoped_action"] });
  });
  it("does not confuse expected access denial with an unresolved failure", () => {
    expect(run([{ role: "tool_call", content: "apply firewall allowlist" }, { role: "tool_result", content: "applied" }, { role: "tool_call", content: "test allowed and denied sources" }, { role: "tool_result", content: "private allowed; public denied as expected" }, { role: "assistant", content: "规则已生效，合法来源通过，公网来源按预期被拒绝，验证完成。" }])).not.toMatchObject({ decision: "skip" });
  });
  it("preserves baseline passthrough", () => {
    expect(evaluateSkillValue([{ role: "user", content: "hello" }], "legacy")).toMatchObject({ decision: "review" });
  });
});

describe("SkillExtractor value-gate integration", () => {
  it("does not invoke the LLM for a high-confidence negative", async () => {
    let calls = 0;
    const extractor = new SkillExtractor({
      core: {} as never,
      valueGateProfile: "precision_v1",
      runner: { run: async () => { calls++; return "unexpected"; } },
    });
    const result = await extractor.extract({
      user_id: "u", team_id: "t", agent_id: "a",
      messages: [{ role: "user", content: "这个命令是什么意思？" }, { role: "assistant", content: "它显示当前目录。" }],
    });
    expect(calls).toBe(0);
    expect(result).toEqual({ candidates: [], text: "Nothing to save." });
  });
});
