import { SKILL_REVIEW_PROMPT_V3 } from "./skill-review-prompt-v3.js";

/** Balanced precision/recall calibration built on the frozen precision_v3 prompt. */
export const SKILL_REVIEW_PROMPT_V4 = `${SKILL_REVIEW_PROMPT_V3}

## Balanced calibration (overrides an overly strict reading of the gate)
- Explicit durable preferences and team conventions are valuable even when they are one sentence and use no tools.
- Durable architecture/context is valuable without execution when it is scope-stable, non-obvious, and would prevent rediscovery.
- For SOPs, terminal evidence such as HTTP 200, PONG, active/running, a passing test, a valid backup listing, or persisted data counts as verification even if the assistant does not use the word “verified”.
- If the final user message starts an unrelated task, evaluate the completed workflow immediately before that message on its own; do not let the new task erase the prior SOP.
- A recovery branch, platform-specific constraint, or reliable validation method can save meaningful work even when the command sequence is short.
- Project/user scope is sufficient. Do not require universal applicability.

Still return Nothing to save for advice-only conversations, unresolved failures, trivial one-off answers, unsafe unbounded actions, secrets, and exact duplicates.`;

