import { describe, expect, it } from "vitest";

import { SkillConversationAddHandler, type AddConversationInput } from "../conversation-add/add-handler.js";

function baseInput(messages: AddConversationInput["messages"]): AddConversationInput {
  return {
    instance_id: "default",
    session_id: "session-1",
    space_id: "space-1",
    user_id: "user-1",
    team_id: "team-1",
    agent_id: "agent-1",
    messages,
  };
}

function harness(initialMessages: Array<Record<string, unknown>>, initialToolCalls: number) {
  let current = { messages: initialMessages };
  let meta = {
    session_id: "session-1", space_id: "space-1", user_id: "user-1", team_id: "team-1", agent_id: "agent-1",
    tool_call_count: initialToolCalls, byte_count: 100, last_appended_at_ms: 1,
  };
  const archives: Array<Array<Record<string, unknown>>> = [];
  const buffer = {
    readCurrent: async () => current,
    readMeta: async () => meta,
    writeCurrent: async (_session: unknown, value: typeof current) => { current = value; },
    writeMeta: async (_session: unknown, value: typeof meta) => { meta = value; },
  };
  const trigger = {
    archive: async (input: { bufferAtTrigger: { messages: Array<Record<string, unknown>> } }) => {
      archives.push(input.bufferAtTrigger.messages);
      return { taskId: `task-${archives.length}`, archivedAtMs: 1000 + archives.length, archiveKey: `archive-${archives.length}` };
    },
  };
  const handler = new SkillConversationAddHandler({
    buffer: buffer as never,
    trigger: trigger as never,
    boundaryConfig: { profile: "sop_v1" },
    now: () => 2000,
  });
  return { handler, archives, getCurrent: () => current, getMeta: () => meta };
}

describe("SkillConversationAddHandler SOP boundaries", () => {
  it("archives the combined workflow after verified completion", async () => {
    const h = harness([], 0);
    const result = await h.handler.handle(baseInput([
      { role: "user", content: "部署应用" },
      { role: "tool_call", tool_call_id: "1", content: "docker compose up -d" },
      { role: "tool_result", tool_call_id: "1", content: "Started" },
      { role: "tool_call", tool_call_id: "2", content: "curl localhost/health" },
      { role: "tool_result", tool_call_id: "2", content: "HTTP 200 status OK" },
      { role: "assistant", content: "部署完成，健康检查通过。" },
    ]));

    expect(result.archived?.reason).toBe("sop_boundary");
    expect(h.archives[0]).toHaveLength(6);
    expect(h.getCurrent().messages).toEqual([]);
  });

  it("archives before a topic switch and retains the new task", async () => {
    const previous = [
      { role: "tool_call", tool_call_id: "1", content: "docker compose up -d" },
      { role: "tool_call", tool_call_id: "2", content: "curl localhost/health" },
      { role: "assistant", content: "健康检查通过，服务已经可用。" },
    ];
    const h = harness(previous, 2);
    const incoming = [{ role: "user" as const, content: "新任务：升级 Redis，和当前服务无关。" }];
    const result = await h.handler.handle(baseInput(incoming));

    expect(result.archived?.reason).toBe("sop_boundary");
    expect(result.archived?.boundary?.phase).toBe("before_append");
    expect(h.archives[0]).toEqual(previous);
    expect(h.getCurrent().messages).toEqual(incoming);
    expect(h.getMeta().tool_call_count).toBe(0);
  });
});
