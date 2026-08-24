import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const skills = JSON.parse(readFileSync(resolve(root, "gold-skills.json"), "utf8"));
const endpoint = process.env.TDAI_CORE_URL || "http://127.0.0.1:8420";
const token = process.env.TDAI_CORE_TOKEN || "local-dev-memory-key";
const serviceId = process.env.TDAI_SERVICE_ID || "default";
const ids = {
  user_id: process.env.TDAI_USER_ID || "usr-dw4keqm922",
  team_id: process.env.TDAI_TEAM_ID || "team-dyf7fb74wi",
  agent_id: process.env.TDAI_AGENT_ID || "agt-dyf7zr5fjh",
};

async function call(path, body) {
  const response = await fetch(`${endpoint}${path}`, {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "x-tdai-service-id": serviceId, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok || result.code !== 0) throw new Error(`${path}: ${response.status} ${JSON.stringify(result)}`);
  return result.data;
}

for (const skill of skills) {
  let existing;
  try { existing = await call("/v3/skill/get-by-name", { ...ids, skill_name: skill.name }); } catch { /* absent */ }
  if (existing?.skill_id) {
    process.stdout.write(`[gold] exists ${skill.name} (${existing.skill_id})\n`);
    continue;
  }
  const content = `---\nname: ${JSON.stringify(skill.name)}\ndescription: ${JSON.stringify(skill.description)}\n---\n\n# ${skill.name}\n\n${skill.body}\n`;
  const created = await call("/v3/skill/create", {
    ...ids, name: skill.name, content,
    metadata: { evaluation_track: "controlled_gold_skill", source: "adaptive-v1/gold-skills.json" },
  });
  process.stdout.write(`[gold] created ${skill.name} (${created.skill_id})\n`);
}
