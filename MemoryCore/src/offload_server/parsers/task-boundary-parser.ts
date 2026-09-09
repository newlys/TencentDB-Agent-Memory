import { extractJson } from "./json-utils.js";

export interface TaskBoundaryJudgment {
  taskBoundary: boolean;
}

/** Parse a boundary response strictly; string booleans and missing fields fail. */
export function parseTaskBoundaryResponse(raw: string): TaskBoundaryJudgment | null {
  const parsed = extractJson<Record<string, unknown>>(raw);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  if (Object.keys(parsed).length !== 1 || !("taskBoundary" in parsed)) return null;
  if (typeof parsed.taskBoundary !== "boolean") return null;
  return { taskBoundary: parsed.taskBoundary };
}
