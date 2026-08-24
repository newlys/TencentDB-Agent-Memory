import { createServer } from "node:http";
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { Readable } from "node:stream";

const port = Number(process.env.BASELINE_CAPTURE_PORT || 8431);
const upstream = String(process.env.BASELINE_CAPTURE_UPSTREAM || "").replace(/\/+$/, "");
const outputRoot = resolve(process.env.BASELINE_CAPTURE_DIR || "experiments/baseline/results/raw/llm-capture");
if (!upstream) throw new Error("BASELINE_CAPTURE_UPSTREAM is required");
mkdirSync(outputRoot, { recursive: true });

function getSystemText(body) {
  if (typeof body.instructions === "string") return body.instructions;
  if (!Array.isArray(body.messages)) return "";
  return body.messages.filter((m) => m?.role === "system").map((m) =>
    typeof m.content === "string" ? m.content : Array.isArray(m.content)
      ? m.content.map((part) => part?.text || "").join("\n") : "").join("\n");
}

function blockLength(text, start, end) {
  const from = text.indexOf(start);
  if (from < 0) return 0;
  const to = text.indexOf(end, from);
  return to < 0 ? text.length - from : to + end.length - from;
}

const server = createServer((req, res) => {
  let raw = "";
  req.setEncoding("utf8");
  req.on("data", (chunk) => { raw += chunk; });
  req.on("end", async () => {
    let body;
    try { body = raw ? JSON.parse(raw) : {}; } catch { body = null; }
    const requestId = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    if (body) {
      const system = getSystemText(body);
      const record = {
        at: new Date().toISOString(), request_id: requestId, path: req.url,
        session_id: req.headers["x-conversation-id"] || req.headers["x-session-id"] || null,
        kind: system.includes("You are CodeBuddy Code") ? "workbuddy"
          : system.includes("You are a Skill Review Agent") ? "skill_extraction"
          : "core_memory",
        model: body.model, stream: body.stream === true,
        messages_count: Array.isArray(body.messages) ? body.messages.length : undefined,
        system_chars: system.length,
        blocks: {
          skill_tools: blockLength(system, "<skill_tools>", "</skill_tools>"),
          available_skills: blockLength(system, "<available_skills>", "</available_skills>"),
          memory_tools: blockLength(system, "<tdai_memory_tools>", "</tdai_memory_tools>"),
          memory_guide: blockLength(system, "<memory-tools-guide>", "</memory-tools-guide>"),
          profile_memory: blockLength(system, "<tdai_profile_memory>", "</tdai_profile_memory>"),
          knowledge_tools: blockLength(system, "<knowledge_tools>", "</knowledge_tools>"),
        },
      };
      appendFileSync(join(outputRoot, "index.jsonl"), `${JSON.stringify(record)}\n`);
      writeFileSync(join(outputRoot, `${requestId}.json`), JSON.stringify(body));
    }
    try {
      const headers = { ...req.headers };
      delete headers.host;
      delete headers["content-length"];
      const upstreamResponse = await fetch(`${upstream}${req.url}`, {
        method: req.method, headers,
        body: req.method === "GET" || req.method === "HEAD" ? undefined : raw,
        duplex: "half",
      });
      const outHeaders = Object.fromEntries(upstreamResponse.headers.entries());
      delete outHeaders["content-encoding"];
      delete outHeaders["content-length"];
      res.writeHead(upstreamResponse.status, outHeaders);
      if (upstreamResponse.body) Readable.fromWeb(upstreamResponse.body).pipe(res);
      else res.end();
    } catch (error) {
      res.writeHead(502, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: { message: error instanceof Error ? error.message : String(error) } }));
    }
  });
});

server.listen(port, "127.0.0.1", () => {
  process.stdout.write(`baseline LLM capture proxy listening on http://127.0.0.1:${port}\n`);
});
