# Task-aware Skill pipeline — final revision

Release identifier:

```text
variant            = ours_v3
boundary_profile   = query_only_l15
extraction_profile = task_scoped_sop_v2
retrieval_profile  = task_scoped_skill_consumption_v3
method_revision    = task-skill-consumption-controller-r5
```

## Method

1. The frozen L1.5 classifier receives only recent user queries from the active
   task and the current user query.  Assistant/tool traces, task IDs, grader
   state, Gold families, and reference patches are excluded.
2. On `new_task`, the previous predicted task is force-archived.  Extraction is
   asynchronous and uses the native TencentDB worker and SkillExtractor.
3. The opt-in `task_sop_v2` reviewer saves at most one executable, reusable SOP.
   Repository background and preferences are not standalone Skills; identity
   is based on intended outcome, applicability, and core workflow.
4. Native BM25 retrieves at most three candidates using only the current task's
   anchor query.  A small task-only selector chooses at most one candidate.
5. The controller materializes the selected Skill from Core, audits the full
   content hash, deterministically retains the useful workflow sections, and
   places that compact workflow in the current task context before repository
   work.  The Coding Agent no longer has to voluntarily call `skill_view`.
6. Oracle/same-task turns reuse the same context.  A new task replaces it.  A
   repo switch flushes the old repo's final task while retaining the shared
   experiment Skill store.

## Backward compatibility and experimental isolation

The new Core/Proxy switches are opt-in:

```yaml
# Baseline defaults (unchanged)
skillRuntime:
  injectSessionAvailableSkills: true
  injectSkillTools: true

# ours_v3
skillRuntime:
  injectSessionAvailableSkills: false
  injectSkillTools: false
```

`reviewPromptProfile` defaults to `legacy_v2`, and `maxPrimaryWrites` defaults
to `0` (unlimited).  Therefore a normal upstream configuration retains native
Baseline behavior.  Only the Ours driver enables `task_sop_v2`, one primary
write, unreachable native archive thresholds, and task-scoped consumption.

## Cost and reliability instrumentation

- Extraction records total LLM usage, falling back to the sum of tool-loop
  steps when provider-level `totalUsage` is absent.
- Proxy prewarm and injection execution use the same `spaceId`, preventing a
  changing Skill listing from destroying provider prefix-cache reuse.
- Both benchmark runners publish atomic JSON progress, preserve completed
  checkpoints, and distinguish verifier failure from infrastructure failure.
- SWE-Together additionally stops on provider 402, uncached-token budget
  overflow, or sustained cache-hit ratio below the configured floor.
