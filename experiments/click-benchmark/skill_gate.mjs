import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

const CONFIG_ENV = "TDAI_TASK_SKILL_GATE_CONFIG_B64";
const STATE_ROOT = process.env.TDAI_TASK_SKILL_GATE_STATE_ROOT || "/session/skill-gates";
const REASONS = new Set([
  "outcome_mismatch",
  "applicability_mismatch",
  "workflow_mismatch",
  "lexical_only",
]);

function emit(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

function fail(message, exitCode = 2, extra = {}) {
  emit({ gate: { status: "INVALID", message, ...extra } });
  process.exitCode = exitCode;
}

function decodeConfig() {
  const encoded = process.env[CONFIG_ENV];
  if (!encoded) throw new Error(`${CONFIG_ENV} is missing`);
  const value = JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));
  if (value?.schema_version !== 1 || !value.task_token || !Array.isArray(value.candidates)) {
    throw new Error("invalid task skill gate configuration");
  }
  if (!/^[a-f0-9]{24}$/.test(value.task_token)) throw new Error("invalid task token");
  if (value.candidates.length > 3 || value.candidates.some((item) => !item?.name)) {
    throw new Error("invalid task skill candidates");
  }
  return value;
}

async function readExisting(statePath) {
  try {
    return JSON.parse(await readFile(statePath, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

async function publishState(statePath, value) {
  await mkdir(path.dirname(statePath), { recursive: true });
  const temporary = `${statePath}.${process.pid}.${randomUUID()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await rename(temporary, statePath);
}

function attemptSummary(attempt, response, body, error) {
  return {
    attempt,
    http_status: response?.status ?? null,
    business_code: body?.code ?? null,
    error: error ? String(error?.message ?? error) : null,
  };
}

async function fetchSkill(config, candidate) {
  const attempts = [];
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    let response;
    let body;
    try {
      response = await fetch(
        `${config.proxy_base_url.replace(/\/$/, "")}/skill-bridge/v3/skill/get-by-name`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-tdai-service-id": config.service_id,
            "x-conversation-id": config.session_id,
          },
          body: JSON.stringify({
            skill_name: candidate.name,
            include_content: true,
            include_manifest: true,
          }),
          signal: AbortSignal.timeout(15_000),
        },
      );
      const text = await response.text();
      try {
        body = JSON.parse(text);
      } catch {
        body = { code: null, message: "invalid JSON response", raw: text.slice(0, 1000) };
      }
      attempts.push(attemptSummary(attempt, response, body, null));
      if (response.ok && (body?.code === 0 || body?.code === 200)) {
        return { ok: true, body, attempts };
      }
      const retryable = [408, 429].includes(response.status) || response.status >= 500;
      if (!retryable || attempt === 2) return { ok: false, body, attempts };
    } catch (error) {
      attempts.push(attemptSummary(attempt, response, body, error));
      if (attempt === 2) return { ok: false, body: null, attempts };
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return { ok: false, body: null, attempts };
}

async function main() {
  let config;
  try {
    config = decodeConfig();
  } catch (error) {
    fail(String(error?.message ?? error));
    return;
  }

  const [action, taskToken, ...args] = process.argv.slice(2);
  if (taskToken !== config.task_token) {
    fail("task token does not match the current task");
    return;
  }
  const statePath = path.join(STATE_ROOT, `${taskToken}.json`);
  const existing = await readExisting(statePath);
  if (existing) {
    if (action === "reject-after-view" && existing.status === "VIEWED") {
      const reason = args[0];
      if (!REASONS.has(reason)) {
        fail("reject-after-view requires one valid mismatch reason");
        return;
      }
      const updated = {
        ...existing,
        post_view_decision: "REJECT_AFTER_VIEW",
        post_view_reason: reason,
        post_view_recorded_at: new Date().toISOString(),
      };
      await publishState(statePath, updated);
      emit({ gate: updated });
      return;
    }
    fail("task skill gate is already closed", 3, {
      prior_status: existing.status,
      prior_action: existing.action,
    });
    return;
  }

  const baseState = {
    schema_version: 1,
    task_token: taskToken,
    recorded_at: new Date().toISOString(),
  };

  if (action === "view") {
    const index = Number(args[0]);
    const candidate = Number.isInteger(index) ? config.candidates[index - 1] : null;
    if (!candidate) {
      fail("candidate index is out of range");
      return;
    }
    const result = await fetchSkill(config, candidate);
    const resolvedSkillId = result.body?.data?.skill_id ?? null;
    if (
      result.ok
      && candidate.skill_id
      && resolvedSkillId
      && resolvedSkillId !== candidate.skill_id
    ) {
      result.ok = false;
      result.validation_error = `expected ${candidate.skill_id}, received ${resolvedSkillId}`;
    }
    if (!result.ok) {
      const state = {
        ...baseState,
        action: "VIEW",
        status: "VIEW_FAILED",
        candidate_index: index,
        skill_id: candidate.skill_id ?? null,
        skill_name: candidate.name,
        attempts: result.attempts,
        validation_error: result.validation_error ?? null,
      };
      await publishState(statePath, state);
      emit({ gate: state, skill_response: result.body });
      process.exitCode = 4;
      return;
    }
    const skillContent = typeof result.body?.data?.content === "string"
      ? result.body.data.content
      : JSON.stringify(result.body?.data?.content ?? "");
    const state = {
      ...baseState,
      action: "VIEW",
      status: "VIEWED",
      candidate_index: index,
      skill_id: resolvedSkillId ?? candidate.skill_id ?? null,
      skill_name: result.body?.data?.name ?? candidate.name,
      skill_version: result.body?.data?.version ?? null,
      content_chars: skillContent.length,
      content_bytes: Buffer.byteLength(skillContent, "utf8"),
      content_sha256: createHash("sha256").update(skillContent).digest("hex"),
      attempts: result.attempts,
    };
    await publishState(statePath, state);
    emit({ gate: state, skill_response: result.body });
    return;
  }

  if (action === "reject-all") {
    if (config.candidates.length === 0) {
      fail("reject-all is not valid when there are no candidates");
      return;
    }
    const reasons = {};
    for (const value of args) {
      const match = /^(\d+):(outcome_mismatch|applicability_mismatch|workflow_mismatch|lexical_only)$/.exec(value);
      if (!match) {
        fail(`invalid rejection reason: ${value}`);
        return;
      }
      reasons[match[1]] = match[2];
    }
    const expected = config.candidates.map((_, index) => String(index + 1));
    if (
      expected.some((index) => !REASONS.has(reasons[index]))
      || Object.keys(reasons).length !== expected.length
    ) {
      fail("reject-all requires exactly one valid reason for every candidate");
      return;
    }
    const state = { ...baseState, action: "REJECT_ALL", status: "REJECTED", reasons };
    await publishState(statePath, state);
    emit({ gate: state });
    return;
  }

  fail("action must be view, reject-all, or reject-after-view");
}

main().catch((error) => fail(String(error?.stack ?? error), 5));
