#!/usr/bin/env node
/** One-shot query-only L1.5 decision for the live benchmark driver. */
import { createHash } from "node:crypto";
import process from "node:process";

import {
  TASK_BOUNDARY_SYSTEM_PROMPT,
  buildTaskBoundaryUserPrompt,
} from "../../MemoryCore/src/offload_server/prompts/task-boundary-prompt.js";
import { parseTaskBoundaryResponse } from "../../MemoryCore/src/offload_server/parsers/task-boundary-parser.js";

type JsonObject = Record<string, unknown>;
type Request = { recentQueries: string[]; currentQuery: string };

function endpoint(baseUrl: string): string {
  const normalized = baseUrl.replace(/\/$/, "");
  return normalized.endsWith("/chat/completions") ? normalized : `${normalized}/chat/completions`;
}

function usageFrom(body: JsonObject): { input: number; output: number; total: number } {
  const usage = (body.usage ?? {}) as JsonObject;
  const input = Number(usage.prompt_tokens ?? usage.input_tokens ?? 0);
  const output = Number(usage.completion_tokens ?? usage.output_tokens ?? 0);
  return { input, output, total: Number(usage.total_tokens ?? input + output) };
}

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks).toString("utf8");
}

async function main(): Promise<void> {
  const input = JSON.parse(await readStdin()) as Request;
  if (!Array.isArray(input.recentQueries) || input.recentQueries.some((item) => typeof item !== "string")) {
    throw new Error("recentQueries must be an array of strings");
  }
  if (typeof input.currentQuery !== "string" || !input.currentQuery.trim()) {
    throw new Error("currentQuery must be a non-empty string");
  }

  const baseUrl = process.env.BOUNDARY_LLM_BASE_URL ?? "https://api.deepseek.com";
  const apiKey = process.env.BOUNDARY_LLM_API_KEY ?? process.env.DEEPSEEK_API_KEY ?? "";
  const model = process.env.BOUNDARY_LLM_MODEL ?? "deepseek-v4-flash";
  const timeoutMs = Number(process.env.BOUNDARY_LLM_TIMEOUT_MS ?? 120_000);
  const maxTokens = Number(process.env.BOUNDARY_LLM_MAX_OUTPUT_TOKENS ?? 2048);
  const maxAttempts = Number(process.env.BOUNDARY_LLM_MAX_ATTEMPTS ?? 2);
  if (!apiKey) throw new Error("BOUNDARY_LLM_API_KEY or DEEPSEEK_API_KEY is required");

  const userPrompt = buildTaskBoundaryUserPrompt(input.recentQueries, input.currentQuery);
  const requestPayload = {
    model,
    messages: [
      { role: "system", content: TASK_BOUNDARY_SYSTEM_PROMPT },
      { role: "user", content: userPrompt },
    ],
    temperature: 0,
    reasoning_effort: "low",
    max_tokens: maxTokens,
    response_format: { type: "json_object" },
  };
  const started = performance.now();
  const attempts: JsonObject[] = [];
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const callStarted = performance.now();
    try {
      const response = await fetch(endpoint(baseUrl), {
        method: "POST",
        headers: { "content-type": "application/json", authorization: `Bearer ${apiKey}` },
        body: JSON.stringify(requestPayload),
        signal: AbortSignal.timeout(timeoutMs),
      });
      const responseText = await response.text();
      if (!response.ok) throw new Error(`HTTP ${response.status}: ${responseText.slice(0, 500)}`);
      const body = JSON.parse(responseText) as JsonObject;
      const choice = ((body.choices as JsonObject[] | undefined)?.[0] ?? {}) as JsonObject;
      const message = (choice.message ?? {}) as JsonObject;
      const raw = String(message.content || message.reasoning_content || "");
      const parsed = parseTaskBoundaryResponse(raw);
      const usage = usageFrom(body);
      attempts.push({ attempt, latency_ms: performance.now() - callStarted, usage, parsed: Boolean(parsed) });
      if (!parsed) continue;
      process.stdout.write(JSON.stringify({
        schema_version: "query_boundary_live/1.0",
        decision: parsed.taskBoundary ? "new_task" : "same_task",
        taskBoundary: parsed.taskBoundary,
        recent_queries: input.recentQueries,
        current_query: input.currentQuery,
        llm_called: true,
        llm_attempts: attempt,
        llm_latency_ms: performance.now() - started,
        usage,
        model,
        system_prompt_sha256: createHash("sha256").update(TASK_BOUNDARY_SYSTEM_PROMPT).digest("hex"),
        user_prompt_sha256: createHash("sha256").update(userPrompt).digest("hex"),
        attempts,
      }));
      return;
    } catch (error) {
      attempts.push({
        attempt,
        latency_ms: performance.now() - callStarted,
        error: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
      });
    }
  }
  throw new Error(`L1.5 boundary failed after ${maxAttempts} attempts: ${JSON.stringify(attempts)}`);
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack ?? error.message : String(error)}\n`);
  process.exitCode = 1;
});
