import { describe, expect, it, vi } from "vitest";
import {
  complexityFromPhases,
  QwenSkillReranker,
  selectAdaptiveSkills,
  type AdaptiveSkillCandidate,
} from "./adaptive-routing.js";

const candidate = (name: string, score: number, description = "useful workflow"): AdaptiveSkillCandidate => ({
  skill_id: `skill-${name}`,
  version: 1,
  name,
  description,
  score,
});
const config = {
  complexityK: { simple: 4, moderate: 6, complex: 8 },
  absoluteScoreThreshold: 0.25,
  relativeScoreThreshold: 0.60,
  scoreGapThreshold: 0.20,
  maxListingChars: 4000,
};

describe("adaptive skill selection", () => {
  it("maps predicted phase count to task complexity", () => {
    expect(complexityFromPhases(["implement"])).toBe("simple");
    expect(complexityFromPhases(["implement", "test"])).toBe("moderate");
    expect(complexityFromPhases(["explore", "implement", "test"])).toBe("complex");
  });

  it("uses relative confidence to drop a relevance cliff", () => {
    const result = selectAdaptiveSkills([
      candidate("a", 0.92), candidate("b", 0.88), candidate("c", 0.85),
      candidate("d", 0.41), candidate("e", 0.38),
    ], "complex", config);
    expect(result.selected.map((s) => s.name)).toEqual(["a", "b", "c"]);
    expect(result.confidenceK).toBe(3);
  });

  it("returns no skills when the best candidate is below the absolute floor", () => {
    expect(selectAdaptiveSkills([candidate("a", 0.24)], "simple", config).selected).toEqual([]);
  });

  it("never cuts a skill line to fit the character budget", () => {
    const result = selectAdaptiveSkills([
      candidate("a", 0.9, "x".repeat(30)), candidate("b", 0.8, "y".repeat(200)),
    ], "simple", { ...config, maxListingChars: 100 });
    expect(result.selected.map((s) => s.name)).toEqual(["a"]);
    expect(result.listing).not.toContain("truncated");
  });
});

describe("QwenSkillReranker", () => {
  it("normalizes the compatible-mode endpoint and parses scores", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({
      results: [{ index: 1, relevance_score: 0.9 }, { index: 0, relevance_score: 0.4 }],
    }), { status: 200, headers: { "content-type": "application/json" } }));
    const reranker = new QwenSkillReranker({
      baseUrl: "https://workspace.example/compatible-mode/v1",
      apiKey: "secret",
      fetcher: fetcher as typeof fetch,
    });
    await expect(reranker.rerank("query", ["a", "b"], 2)).resolves.toEqual([
      { index: 1, score: 0.9 }, { index: 0, score: 0.4 },
    ]);
    expect(fetcher.mock.calls[0]![0]).toBe("https://workspace.example/compatible-api/v1/reranks");
  });
});
