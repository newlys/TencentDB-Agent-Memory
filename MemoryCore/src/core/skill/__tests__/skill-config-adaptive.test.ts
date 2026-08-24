import { describe, expect, it } from "vitest";
import { resolveSkillConfig } from "../skill-config.js";

const probe = {
  hasTcvdbCredentials: false,
  hasCosCredentials: false,
  embeddingAvailable: false,
  llmRunnerAvailable: true,
};
const logger = { info() {}, warn() {}, error() {} };

describe("adaptive skill routing config", () => {
  it("preserves static routing as the default", () => {
    const config = resolveSkillConfig({ enabled: true }, probe, logger)!;
    expect(config.routing.profile).toBe("static");
    expect(config.routing.searchTopK).toBe(20);
  });

  it("resolves adaptive_v1 defaults and overrides", () => {
    const config = resolveSkillConfig({
      enabled: true,
      routing: {
        profile: "adaptive_v1",
        adaptive: { candidateTopK: 32, complexityK: { complex: 7 } },
      },
    }, probe, logger)!;
    expect(config.routing.adaptive).toMatchObject({
      candidateTopK: 32,
      complexityK: { simple: 4, moderate: 6, complex: 7 },
      absoluteScoreThreshold: 0.25,
      relativeScoreThreshold: 0.60,
      scoreGapThreshold: 0.20,
      maxListingChars: 4000,
    });
  });
});
