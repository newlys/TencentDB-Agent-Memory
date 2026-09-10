import { describe, expect, it, vi } from "vitest";

import { TaskAwareSkillInjector } from "../task-aware-skill-injector.js";
import type { AgentContext } from "../../types.js";
import type { CoreSkillClient } from "../../../skill/core-client.js";
import { fallbackLatestUserQuery } from "../../../guard-adapter.js";
import { buildConfig } from "../../../config.js";

const runtime = {
  enabled: true,
  llmBaseUrl: "https://llm.example/v1",
  apiKey: "test-key",
  model: "test-model",
  timeoutMs: 1_000,
  searchTopK: 3,
  contextCharBudget: 3_200,
  maxRecentQueries: 6,
};

const coreSkill = {
  endpoint: "http://core.example",
  serviceToken: "local",
  serviceId: "default",
  timeoutMs: 1_000,
  routingProfile: "static" as const,
};

function response(content: Record<string, unknown>): Response {
  return new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify(content) } }] }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function context(turnSeq: number, taskAnchor: string): AgentContext {
  return {
    messages: [
      { role: "system", blocks: [{ type: "text", content: "system" }] },
      { role: "user", blocks: [{ type: "text", content: taskAnchor || "tool result" }] },
    ],
    requestParams: {},
    metadata: {
      protocol: "anthropic",
      traceId: `trace-${turnSeq}`,
      keyId: "key",
      modelId: "model",
      stream: true,
      agentSource: "claude-code",
      sessionKey: "repo-session",
      turnSeq,
      custom: {
        taskAnchor,
        session: {
          session_id: "repo-session",
          team_id: "team",
          agent_id: "agent",
          user_id: "user",
          space_id: "default",
        },
      },
    },
  };
}

describe("TaskAwareSkillInjector", () => {
  it("keeps Baseline defaults off and loads the documented opt-in config", () => {
    const baseline = buildConfig({ configFile: "__missing_task_aware_test__.yaml" });
    expect(baseline.skillRuntime.injectSessionAvailableSkills).toBe(true);
    expect(baseline.skillRuntime.injectSkillTools).toBe(true);
    expect(baseline.skillRuntime.taskAware.enabled).toBe(false);

    const previous = process.env.DEEPSEEK_API_KEY;
    process.env.DEEPSEEK_API_KEY = "documented-test-key";
    try {
      const configured = buildConfig({
        configFile: "../benchmarks/product-task-aware/proxy.task-aware.yaml.example",
      });
      expect(configured.skillRuntime.taskAware.enabled).toBe(true);
      expect(configured.skillRuntime.taskAware.apiKey).toBe("documented-test-key");
      expect(configured.skillRuntime.injectSessionAvailableSkills).toBe(false);
      expect(configured.skillRuntime.injectSkillTools).toBe(false);
    } finally {
      if (previous === undefined) delete process.env.DEEPSEEK_API_KEY;
      else process.env.DEEPSEEK_API_KEY = previous;
    }
  });

  it("detects public human queries without the private routing extension", () => {
    expect(fallbackLatestUserQuery([
      { role: "user", content: [{ type: "text", text: "Fix the parser." }] },
    ])).toBe("Fix the parser.");
    expect(fallbackLatestUserQuery([
      { role: "user", content: [{ type: "tool_result", tool_use_id: "1", content: "ok" }] },
    ])).toBe("");
    expect(fallbackLatestUserQuery([
      { role: "user", content: "<system-reminder>noise</system-reminder> Real request" },
    ])).toBe("Real request");
  });

  it("materializes once, persists through tool loops, and archives only on a new task", async () => {
    const searchSkills = vi.fn()
      .mockResolvedValueOnce({ items: [{
        skill_id: "skill-a", name: "explicit-input-precedence", description: "Merge values safely",
        version: 1, score: 4.2, snippet: "Prefer explicit input over fallback.",
      }] })
      .mockResolvedValueOnce({ items: [] });
    const post = vi.fn().mockResolvedValue({
      skill_id: "skill-a", name: "explicit-input-precedence", version: 1,
      content: "---\nname: explicit-input-precedence\n---\n## When to use\nWhen explicit input competes with fallback.\n## Workflow\n1. Preserve the explicit value.\n2. Apply fallback only when absent.\n## Validation\nTest both explicit and absent inputs.",
    });
    const forceArchive = vi.fn().mockResolvedValue({ status: "archived" });
    const client = { searchSkills, post, forceArchive } as unknown as CoreSkillClient;
    const fetcher = vi.fn()
      // First task selector.
      .mockResolvedValueOnce(response({ decision: "view", rank: 1, reason: "same workflow" }))
      // Same-task boundary.
      .mockResolvedValueOnce(response({ taskBoundary: false }))
      // New-task boundary.
      .mockResolvedValueOnce(response({ taskBoundary: true }));
    const injector = new TaskAwareSkillInjector({ coreSkill, runtime }, fetcher, client);

    const first = await injector.execute(context(1, "Explicit CLI input is overwritten by the default."));
    expect(first).toHaveLength(1);
    expect(first[0]!.content).toContain("explicit-input-precedence");
    expect(first[0]!.content).toContain("Preserve the explicit value");

    const toolLoop = await injector.execute(context(1, ""));
    expect(toolLoop[0]!.content).toBe(first[0]!.content);
    expect(searchSkills).toHaveBeenCalledTimes(1);

    const oracle = await injector.execute(context(2, "Also preserve the explicit empty string."));
    expect(oracle[0]!.content).toBe(first[0]!.content);
    expect(forceArchive).not.toHaveBeenCalled();

    const nextTask = await injector.execute(context(3, "Collect repeated header values in order."));
    expect(nextTask).toEqual([]);
    expect(forceArchive).toHaveBeenCalledTimes(1);
    expect(searchSkills).toHaveBeenCalledTimes(2);
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("fails open when the task-aware LLM is unavailable", async () => {
    const client = {
      searchSkills: vi.fn().mockResolvedValue({ items: [] }),
      forceArchive: vi.fn(),
      post: vi.fn(),
    } as unknown as CoreSkillClient;
    const fetcher = vi.fn().mockRejectedValue(new Error("network down"));
    const injector = new TaskAwareSkillInjector({ coreSkill, runtime }, fetcher, client);

    expect(await injector.execute(context(1, "First task"))).toEqual([]);
    expect(await injector.execute(context(2, "Possibly another task"))).toEqual([]);
  });
});
