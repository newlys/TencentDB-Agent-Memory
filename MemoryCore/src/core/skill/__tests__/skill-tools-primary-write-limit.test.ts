import { describe, expect, it, vi } from "vitest";
import type { SkillCore } from "../skill-core.js";
import { createSkillTools, type ExtractedSkillCandidate } from "../skill-tools.js";

const execute = async (tool: unknown, input: Record<string, unknown>): Promise<string> =>
  (tool as { execute(args: Record<string, unknown>): Promise<string> }).execute(input);

describe("reviewer primary-write limit", () => {
  it("counts only successful primary mutations and binds supporting files to that skill", async () => {
    const core = {
      create: vi.fn()
        .mockRejectedValueOnce(new Error("transient"))
        .mockResolvedValueOnce({ skill_id: "skl-1", version: 1, description: "workflow" }),
      update: vi.fn(),
      writeFiles: vi.fn().mockResolvedValue({ skill_id: "skl-1", version: 2, name: "workflow" }),
    } as unknown as SkillCore;
    const audit: ExtractedSkillCandidate[] = [];
    const tools = createSkillTools({
      core, user_id: "user", team_id: "team", agent_id: "agent", task_id: "task",
      auditSink: audit, maxPrimaryWrites: 1,
    });

    const beforePrimary = JSON.parse(await execute(tools.skill_files_write, {
      skill_id: "skl-1", path: "scripts/check.sh", content: "echo ok", expected_version: 1,
    }));
    expect(beforePrimary.error).toBe("SUPPORTING_FILE_SKILL_MISMATCH");

    const failed = JSON.parse(await execute(tools.skill_create, { name: "workflow", content: "x" }));
    expect(failed.error).toBe("INTERNAL");
    const created = JSON.parse(await execute(tools.skill_create, { name: "workflow", content: "x" }));
    expect(created.ok).toBe(true);

    const wrongSkill = JSON.parse(await execute(tools.skill_files_write, {
      skill_id: "skl-2", path: "scripts/check.sh", content: "echo bad", expected_version: 1,
    }));
    expect(wrongSkill.error).toBe("SUPPORTING_FILE_SKILL_MISMATCH");

    const sameSkill = JSON.parse(await execute(tools.skill_files_write, {
      skill_id: "skl-1", path: "scripts/check.sh", content: "echo ok", expected_version: 1,
    }));
    expect(sameSkill.ok).toBe(true);
    expect(core.writeFiles).toHaveBeenCalledTimes(1);
    expect(audit.map((item) => item.action)).toEqual(["create", "files_write"]);
  });
});
