import { getEncoding } from "js-tiktoken";

export type SkillTaskPhase = "explore" | "implement" | "test" | "debug" | "review";
export type SkillTaskComplexity = "simple" | "moderate" | "complex";

export interface AdaptiveSkillCandidate {
  skill_id: string;
  name: string;
  description: string;
  version: number;
  score: number;
}

export interface SkillRerankResult {
  index: number;
  score: number;
}

export interface SkillReranker {
  rerank(query: string, documents: string[], topN: number): Promise<SkillRerankResult[]>;
  getLastUsage?(): { inputTokens?: number; outputTokens?: number };
}

export interface AdaptiveSelectionConfig {
  complexityK: Record<SkillTaskComplexity, number>;
  absoluteScoreThreshold: number;
  relativeScoreThreshold: number;
  scoreGapThreshold: number;
  maxListingChars: number;
}

export interface AdaptiveSelectionResult {
  selected: AdaptiveSkillCandidate[];
  complexityK: number;
  confidenceK: number;
  budgetK: number;
  listing: string;
  listingChars: number;
  listingTokens: number;
}

const encoder = getEncoding("cl100k_base");

export function complexityFromPhases(phases: readonly SkillTaskPhase[]): SkillTaskComplexity {
  const count = new Set(phases).size;
  if (count <= 1) return "simple";
  if (count === 2) return "moderate";
  return "complex";
}

function render(items: readonly AdaptiveSkillCandidate[]): string {
  if (items.length === 0) return "<available_skills>\n(none)\n</available_skills>";
  return `<available_skills>\n${items.map((s) => `- ${s.name}: ${s.description}`).join("\n")}\n</available_skills>`;
}

export function selectAdaptiveSkills(
  ranked: readonly AdaptiveSkillCandidate[],
  complexity: SkillTaskComplexity,
  config: AdaptiveSelectionConfig,
  confidenceEnabled = true,
): AdaptiveSelectionResult {
  const complexityK = Math.max(0, config.complexityK[complexity]);
  let confidenceK = Math.min(ranked.length, complexityK);

  if (confidenceEnabled) {
    const top = ranked[0]?.score ?? 0;
    if (top < config.absoluteScoreThreshold) {
      confidenceK = 0;
    } else {
      confidenceK = 0;
      const cap = Math.min(ranked.length, complexityK);
      for (let i = 0; i < cap; i++) {
        const current = ranked[i]!;
        if (current.score / top < config.relativeScoreThreshold) break;
        if (i > 0 && ranked[i - 1]!.score - current.score >= config.scoreGapThreshold) break;
        confidenceK++;
      }
    }
  }

  const confidenceSelected = ranked.slice(0, Math.min(complexityK, confidenceK));
  const budgetSelected: AdaptiveSkillCandidate[] = [];
  for (const candidate of confidenceSelected) {
    const next = [...budgetSelected, candidate];
    if (render(next).length > config.maxListingChars) break;
    budgetSelected.push(candidate);
  }
  const listing = render(budgetSelected);
  return {
    selected: budgetSelected,
    complexityK,
    confidenceK,
    budgetK: budgetSelected.length,
    listing,
    listingChars: listing.length,
    listingTokens: encoder.encode(listing).length,
  };
}

export class QwenSkillReranker implements SkillReranker {
  private lastUsage: { inputTokens?: number; outputTokens?: number } = {};
  constructor(private readonly options: {
    baseUrl: string;
    apiKey: string;
    model?: string;
    timeoutMs?: number;
    fetcher?: typeof fetch;
  }) {}

  async rerank(query: string, documents: string[], topN: number): Promise<SkillRerankResult[]> {
    const fetcher = this.options.fetcher ?? globalThis.fetch.bind(globalThis);
    const base = this.options.baseUrl.replace(/\/$/, "")
      .replace(/\/compatible-mode\/v1$/i, "/compatible-api/v1")
      .replace(/\/compatible-api\/v1\/reranks$/i, "/compatible-api/v1");
    const response = await fetcher(`${base}/reranks`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${this.options.apiKey}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: this.options.model ?? "qwen3-rerank",
        query,
        documents,
        top_n: Math.min(topN, documents.length),
        instruct: "Rank reusable coding skills by how much they help complete the current task and phase.",
      }),
      signal: AbortSignal.timeout(this.options.timeoutMs ?? 5000),
    });
    if (!response.ok) throw new Error(`reranker HTTP ${response.status}`);
    const body = await response.json() as {
      results?: Array<{ index?: number; relevance_score?: number; score?: number }>;
      usage?: { total_tokens?: number; input_tokens?: number; output_tokens?: number };
    };
    if (!Array.isArray(body.results)) throw new Error("reranker response missing results");
    this.lastUsage = {
      inputTokens: body.usage?.input_tokens ?? body.usage?.total_tokens,
      outputTokens: body.usage?.output_tokens,
    };
    return body.results.map((row) => ({
      index: Number(row.index),
      score: Number(row.relevance_score ?? row.score),
    })).filter((row) => Number.isInteger(row.index) && Number.isFinite(row.score));
  }

  getLastUsage(): { inputTokens?: number; outputTokens?: number } { return this.lastUsage; }
}
