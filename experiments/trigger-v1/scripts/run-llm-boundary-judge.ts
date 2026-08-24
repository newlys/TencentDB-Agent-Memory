import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

type Message = { role: string; content: string; tool_call_id?: string };
type TestCase = { id: string; chunks: Message[][] };
type Dataset = { families: Array<{ id: string; cases: TestCase[] }> };
type JudgeResult = { phase: "none" | "before_append" | "after_append"; confidence: number; reason: string };

const here = path.dirname(fileURLToPath(import.meta.url));
const experimentRoot = path.resolve(here, "..");
const dataset = JSON.parse(fs.readFileSync(path.join(experimentRoot, "datasets", "sop-boundaries.json"), "utf8")) as Dataset;
const apiKey = process.env.DASHSCOPE_API_KEY;
const baseUrl = (process.env.AFAC_QWEN_BASE_URL || "https://dashscope.aliyuncs.com/compatible-mode/v1").replace(/\/$/, "");
const model = process.env.TDAI_TRIGGER_JUDGE_MODEL || "qwen3.7-plus";
if (!apiKey) throw new Error("DASHSCOPE_API_KEY is required for the optional LLM judge experiment");

const resultsDir = path.join(experimentRoot, "results");
const cachePath = path.join(resultsDir, "llm-judge-predictions.json");
fs.mkdirSync(resultsDir, { recursive: true });
const existing = fs.existsSync(cachePath) ? JSON.parse(fs.readFileSync(cachePath, "utf8")) : { predictions: {} };
const predictions: Record<string, Array<{ phase: string; index: number; confidence: number; reason: string }>> = existing.predictions ?? {};
let judgeCalls = 0;
let inputChars = 0;
let promptTokens = 0;
let completionTokens = 0;

async function main(): Promise<void> {
for (const family of dataset.families) {
  for (const testCase of family.cases) {
    if (predictions[testCase.id]) continue;
    const events: Array<{ phase: string; index: number; confidence: number; reason: string }> = [];
    let buffer: Message[] = [];
    for (let index = 0; index < testCase.chunks.length; index++) {
      const incoming = testCase.chunks[index]!;
      if (!isCandidate(buffer, incoming)) {
        buffer.push(...incoming);
        continue;
      }
      const payload = renderCandidate(buffer, incoming);
      inputChars += payload.length;
      const judged = await judge(payload);
      judgeCalls++;
      promptTokens += judged.usage?.prompt_tokens ?? 0;
      completionTokens += judged.usage?.completion_tokens ?? 0;
      const result = parseJudge(judged.content);
      if (result.phase === "before_append") {
        events.push({ phase: result.phase, index, confidence: result.confidence, reason: result.reason });
        buffer = [...incoming];
      } else {
        buffer.push(...incoming);
        if (result.phase === "after_append") {
          events.push({ phase: result.phase, index, confidence: result.confidence, reason: result.reason });
          buffer = [];
        }
      }
    }
    predictions[testCase.id] = events;
    fs.writeFileSync(cachePath, `${JSON.stringify({ model, generatedAt: new Date().toISOString(), judgeCalls, inputChars, promptTokens, completionTokens, predictions }, null, 2)}\n`);
    console.log(`${family.id}/${testCase.id}: ${events.map((event) => `${event.phase}:${event.index}`).join(",") || "none"}`);
  }
}

fs.writeFileSync(cachePath, `${JSON.stringify({ model, generatedAt: new Date().toISOString(), judgeCalls, inputChars, promptTokens, completionTokens, predictions }, null, 2)}\n`);
console.log(JSON.stringify({ model, judgeCalls, inputChars, promptTokens, completionTokens, cases: Object.keys(predictions).length }, null, 2));
}

void main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});

function isCandidate(buffer: Message[], incoming: Message[]): boolean {
  const toolCalls = [...buffer, ...incoming].filter((message) => message.role === "tool_call").length;
  if (toolCalls < 2) return false;
  const last = incoming.findLast((message) => String(message.content ?? "").trim());
  return last?.role === "assistant" || (incoming[0]?.role === "user" && buffer.length > 0);
}

function renderCandidate(buffer: Message[], incoming: Message[]): string {
  const compact = (messages: Message[]) => messages.slice(-14).map((message) => ({ role: message.role, content: String(message.content ?? "").slice(0, 1200) }));
  return JSON.stringify({ buffered: compact(buffer), incoming: compact(incoming) });
}

async function judge(candidate: string): Promise<{ content: string; usage?: { prompt_tokens?: number; completion_tokens?: number } }> {
  const response = await fetch(`${baseUrl}/chat/completions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      model,
      enable_thinking: false,
      temperature: 0,
      max_tokens: 180,
      response_format: { type: "json_object" },
      messages: [
        {
          role: "system",
          content: "You label Skill extraction boundaries in agent transcripts. Return JSON only: {phase:'none'|'before_append'|'after_append',confidence:0..1,reason:'short'}. after_append means the agent actually executed a reusable multi-step SOP and reached a verified successful final outcome in incoming. Intermediate success, advice, read-only inspection, pending work, or unresolved failure is none. before_append means the buffered executed workflow has a verified successful outcome and incoming starts an unrelated new task; archive buffered only. Be conservative.",
        },
        { role: "user", content: candidate },
      ],
    }),
  });
  if (!response.ok) throw new Error(`judge HTTP ${response.status}: ${(await response.text()).slice(0, 500)}`);
  const body = await response.json() as { choices?: Array<{ message?: { content?: string } }>; usage?: { prompt_tokens?: number; completion_tokens?: number } };
  return { content: body.choices?.[0]?.message?.content ?? "", usage: body.usage };
}

function parseJudge(raw: string): JudgeResult {
  const text = raw.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
  const parsed = JSON.parse(text) as Partial<JudgeResult>;
  const phase = parsed.phase;
  if (phase !== "none" && phase !== "before_append" && phase !== "after_append") throw new Error(`invalid judge phase: ${raw}`);
  return { phase, confidence: Math.max(0, Math.min(1, Number(parsed.confidence) || 0)), reason: String(parsed.reason ?? "") };
}
