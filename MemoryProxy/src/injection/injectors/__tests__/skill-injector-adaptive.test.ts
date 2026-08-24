import { describe, expect, it, vi } from "vitest";
import { SkillInjector } from "../skill-injector.js";
import type { AgentContext } from "../../types.js";
import type { CoreSkillClient } from "../../../skill/core-client.js";

function ctx(user: string, turnSeq = 1): AgentContext {
  return {
    messages: [{ role: "user", blocks: [{ type: "text", content: user }] }],
    requestParams: {},
    metadata: {
      protocol: "openai", traceId: "trace", keyId: "key", modelId: "model",
      stream: false, agentSource: "workbuddy", turnSeq,
      custom: { session: { team_id: "team", agent_id: "agent", space_id: "space" } },
    },
  };
}

function client() {
  return {
    listListing: vi.fn().mockResolvedValue({
      mode: "search",
      listing: "<available_skills>\n- retry: retry HTTP requests\n</available_skills>",
      hits: [{ skill_id: "1", version: 1, name: "retry" }],
      diagnostics: { selected_k: 1 },
    }),
  } as unknown as CoreSkillClient;
}

describe("SkillInjector adaptive routing", () => {
  it("keeps the legacy session_init strategy by default", () => {
    expect(new SkillInjector({ coreSkill: {} as never }).cacheStrategy).toBe("session_init");
  });

  it("reroutes dynamically and caches an identical route signature", async () => {
    const fake = client();
    const injector = new SkillInjector({
      coreSkill: { routingProfile: "adaptive_v1" } as never,
    }, fake);
    expect(injector.cacheStrategy).toBe("none");

    const first = await injector.execute(ctx("Implement retry and tests"));
    const second = await injector.execute(ctx("Implement retry and tests"));
    expect(fake.listListing).toHaveBeenCalledTimes(1);
    expect(first[0]?.metadata?.diagnostics).toMatchObject({ selected_k: 1, route_cache_hit: false });
    expect(second[0]?.metadata?.diagnostics).toMatchObject({ selected_k: 1, route_cache_hit: true });

    await injector.execute(ctx("The retry tests failed with an error", 2));
    expect(fake.listListing).toHaveBeenCalledTimes(2);
  });
});
