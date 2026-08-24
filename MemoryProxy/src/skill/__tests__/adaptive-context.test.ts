import { describe, expect, it } from "vitest";
import { analyzeAdaptiveRoutingContext } from "../adaptive-context.js";
import type { AgentContext } from "../../injection/types.js";

function context(system: string, user: string, assistant = ""): AgentContext {
  return {
    messages: [
      { role: "system", blocks: [{ type: "text", content: system }] },
      { role: "assistant", blocks: [{ type: "text", content: assistant }] },
      { role: "user", blocks: [{ type: "text", content: user }] },
    ],
    requestParams: {},
    metadata: { protocol: "openai", traceId: "t", keyId: "k", modelId: "m", stream: false, agentSource: "workbuddy", turnSeq: 1 },
  };
}

function taskSystem(task: string, agentPrompt = ""): string {
  return `<session_context>\n[Agent]\nprompt:\n${agentPrompt}\n\n[Task]\ndescription: ${task}\n</session_context>`;
}

describe("adaptive routing context", () => {
  it("keeps a typo task simple", () => {
    const result = analyzeAdaptiveRoutingContext(context("", "Fix a typo in README"));
    expect(result.complexity).toBe("simple");
    expect(result.predicted_phases).toEqual(["implement"]);
  });

  it("recognizes a multi-phase refactor", () => {
    const result = analyzeAdaptiveRoutingContext(context(
      taskSystem("Refactor authentication architecture, implement the backend and add security tests"),
      "Start by exploring the repository",
    ));
    expect(result.complexity).toBe("complex");
    expect(result.predicted_phases).toEqual(expect.arrayContaining(["explore", "implement", "test", "review"]));
    expect(result.current_phase).toBe("explore");
  });

  it("changes the route signature when the task phase changes", () => {
    const explore = analyzeAdaptiveRoutingContext(context(taskSystem("Refactor auth and test it"), "inspect files"));
    const debug = analyzeAdaptiveRoutingContext(context(taskSystem("Refactor auth and test it"), "the tests failed with an error"));
    expect(explore.signature).not.toBe(debug.signature);
    expect(debug.current_phase).toBe("debug");
  });

  it("ignores generic phase words in the agent prompt", () => {
    const result = analyzeAdaptiveRoutingContext(context(
      taskSystem("Fix a typo in README", "Always inspect, test, debug and review every implementation"),
      "Fix the spelling mistake",
    ));
    expect(result.complexity).toBe("simple");
    expect(result.predicted_phases).toEqual(["implement"]);
  });

  it("extracts WorkBuddy user_query instead of its memory reminder", () => {
    const result = analyzeAdaptiveRoutingContext(context(
      "",
      `<system-reminder>Always plan, test, debug, review and inspect memory.</system-reminder>\n`
        + `<user_query>Fix a typo in README</user_query>`,
    ));
    expect(result.complexity).toBe("simple");
    expect(result.query).toContain("Fix a typo in README");
    expect(result.query).not.toContain("system-reminder");
  });
});
