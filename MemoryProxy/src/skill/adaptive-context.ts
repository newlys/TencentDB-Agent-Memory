import { createHash } from "node:crypto";
import type { AgentContext } from "../injection/types.js";

export type AdaptivePhase = "explore" | "implement" | "test" | "debug" | "review";
export type AdaptiveComplexity = "simple" | "moderate" | "complex";

export interface AdaptiveRoutingContext {
  query: string;
  complexity: AdaptiveComplexity;
  predicted_phases: AdaptivePhase[];
  current_phase: AdaptivePhase;
  recent_actions: string[];
  signature: string;
}

function textOf(ctx: AgentContext, role: "system" | "user" | "assistant"): string {
  return ctx.messages.filter((m) => m.role === role)
    .flatMap((m) => m.blocks.filter((b) => b.type === "text").map((b) => b.content))
    .join("\n");
}

function latestTextOf(ctx: AgentContext, role: "user" | "assistant"): string {
  const message = [...ctx.messages].reverse().find((item) => item.role === role);
  return message?.blocks.filter((block) => block.type === "text").map((block) => block.content).join("\n") ?? "";
}

function taskTextFromUser(raw: string): string {
  const tagged = [...raw.matchAll(/<user_query>([\s\S]*?)<\/user_query>/gi)].at(-1)?.[1]?.trim();
  return (tagged || raw).slice(-2048);
}

function unique<T>(values: T[]): T[] { return [...new Set(values)]; }

/** Keep only the user-selected task section; the Agent prompt often contains
 * generic words such as test/debug/review and must not inflate complexity. */
function taskTextFromSystem(system: string): string {
  const sessionBlocks = [...system.matchAll(/<session_context>([\s\S]*?)<\/session_context>/gi)];
  return sessionBlocks.map((match) => {
    const body = match[1] ?? "";
    const task = body.match(/(?:^|\n)\[Task\]\s*\n([\s\S]*?)(?=\n\[[^\]]+\]|$)/i);
    return task?.[1] ?? "";
  }).filter(Boolean).join("\n").slice(-2048);
}

export function analyzeAdaptiveRoutingContext(ctx: AgentContext): AdaptiveRoutingContext {
  const user = taskTextFromUser(latestTextOf(ctx, "user"));
  const system = taskTextFromSystem(textOf(ctx, "system"));
  const assistant = latestTextOf(ctx, "assistant").slice(-1200);
  const task = `${system}\n${user}`.toLowerCase();
  const recent = `${assistant}\n${user}`.toLowerCase();
  const phases: AdaptivePhase[] = [];

  if (/architect|design|explor|understand|investigate|分析|架构|调研|理解|重构/.test(task)) phases.push("explore");
  if (/implement|add|create|write|change|refactor|fix|修复|实现|新增|修改|重构|typo/.test(task)) phases.push("implement");
  if (/test|spec|coverage|verify|测试|验证|覆盖率/.test(task)) phases.push("test");
  if (/debug|error|fail|bug|exception|trace|排错|报错|失败/.test(task)) phases.push("debug");
  if (/review|audit|security|inspect|审查|评审|安全/.test(task)) phases.push("review");
  const predicted = unique<AdaptivePhase>(phases.length > 0 ? phases : ["implement"]);

  let current: AdaptivePhase = "implement";
  if (/test|spec|coverage|测试|验证/.test(recent)) current = "test";
  if (/debug|error|failed|exception|stack trace|报错|失败/.test(recent)) current = "debug";
  if (/review|diff|audit|审查|评审/.test(recent)) current = "review";
  if (/explor|inspect|read|search|understand|分析|查看|搜索/.test(recent)) current = "explore";

  const complexity: AdaptiveComplexity = predicted.length <= 1
    ? "simple"
    : predicted.length === 2 ? "moderate" : "complex";
  const recentActions = unique(ctx.messages.slice(-8).flatMap((message) =>
    message.blocks.filter((block) => block.type === "tool_use")
      .map((block) => String(block.metadata?.tool_name ?? block.metadata?.tool_id ?? "tool")),
  )).slice(0, 8);
  const query = [user || system, `current phase: ${current}`, `required phases: ${predicted.join(", ")}`,
    recentActions.length ? `recent actions: ${recentActions.join(", ")}` : ""]
    .filter(Boolean).join("\n").slice(0, 2048);
  const signature = createHash("sha256").update(`${ctx.metadata.turnSeq ?? 0}|${current}|${query}`).digest("hex");
  return { query, complexity, predicted_phases: predicted, current_phase: current, recent_actions: recentActions, signature };
}
