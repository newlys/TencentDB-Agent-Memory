---
name: all-path-resource-cleanup
description: Make registered cleanup callbacks / resource teardown (exit stacks, with-resource scopes, call-on-close registries) run on every exit path — normal completion, reported error, abort, and early exit — instead of only when a scope exits without an exception, while preserving outward behavior such as exit codes and error messages.
---

# All-path resource cleanup

## When to use
A scope or context object registers resources/callbacks (e.g. a context-manager stack, `with_resource`, `call_on_close`, try/finally-style registries) that are torn down on normal completion but stay open when the scope exits because of an error, abort, or explicit early exit. Task wording is typically: "resources are released on success but remain open when the command reports an error or aborts — make cleanup reliable on failure and early exit too, without changing exit codes, error messages, or normal behavior."

## When not to use
- Cleanup already runs on all paths (plain `try/finally`, `ExitStack` used correctly) — there is no gap to fix.
- The failure is a wording/UX message change with no control-flow gap.
- The task intentionally changes which failures surface (cleanup is supposed to swallow or alter the exception).

## Required inputs
- The code path where cleanup is skipped: the scope-exit / `__exit__` / exception handler that conditionally runs teardown.
- The registry of registered cleanup callbacks/resources and how teardown is invoked on it.
- The outward contract to preserve: exit codes, printed error/abort messages, normal-completion teardown behavior.

## Workflow
1. Reproduce the gap: run the normal path and confirm teardown runs; run an error path, abort path, and early-exit path and confirm teardown is skipped. Record exit codes/output for each path as the invariant baseline.
2. Locate the success-only guard: find the condition gating teardown at scope exit — typically `if <outermost scope> and exc_type is None:` or an exception-type branch that skips the teardown call when an exception is propagating.
3. Remove the exception guard so teardown executes whenever the outermost scope pops: `if <outermost scope>:` calling the teardown with the active exception info.
4. Preserve exception semantics: pass the propagating exception (`exc_type, exc_value, tb`) into the teardown/exit stack rather than swallowing it. The exception continues to propagate, so callers (user context managers, error handlers) still observe the failure and outward exit codes/messages are unchanged.
5. Keep scope-depth handling intact: run teardown only at the outermost scope level so nested/inner scopes do not double-close or tear down prematurely; verify grouped/nested usage tears down each scope's resources exactly once.
6. Validate with a parametrized test matrix.

## Decision rules
- Teardown must run when the outermost scope pops on *every* path: normal completion, `fail`-style reported error, `abort`-style silent early exit, raised exception, and explicit exit code.
- The propagating exception is the handoff mechanism: teardown sees it (context managers may inspect it), but nothing should catch/suppress it in the scope-exit path.
- Only remove the exception guard at the scope boundary that owns teardown; do not weaken guards that legitimately limit teardown to one scope level.

## Validation
Parametrize the same scenario over exit methods (reported error / abort / raise) plus the pre-existing normal and nested-exception cases:
- Resource entered and exited flags equal on every path (`entered == [True]`, `exited == [True]`), including when the exit happens via exception.
- Outward invariants unchanged per path: expected exit code and error/abort output text.
- Context-manager teardown observers still receive the active exception when they raise inside the scope (existing exception-visibility tests still pass).
- Full surrounding test modules pass; confirm the new regression test fails on the original code and passes after the fix.

## Failure handling / rollback
- If teardown begins double-running or running too early, restore the outermost-scope depth guard and keep only the exception-guard removal.
- If outward exit codes or messages change, the exception is being swallowed or re-raised differently — revert to re-raising/propagating the original exception unchanged.
- Fallback: revert the one-line guard change; the new regression test must then fail, proving the fix was both necessary and sufficient.

## Pitfalls
- The typical defect is a single extra condition (`and exc_type is None`) that looks harmless but makes teardown unreachable on failure paths — search scope-exit methods for exception-type conditions wrapping teardown calls.
- Guarding teardown on "no exception" conflates "scope exited cleanly" with "resource lifecycle complete"; the fix separates error *reporting* from resource *cleanup*.
- Do not move teardown into an exception handler only — that still misses abort/early-exit paths that unwind without an exception reaching a catch-all.
- Keep normal-completion behavior byte-identical; the fix should only add teardown on paths that previously skipped it.

## Evidence
Click `Context` (`src/click/core.py`): `Context.__exit__` unwound the registered exit stack only when `self._depth == 0 and exc_type is None`, so `ctx.fail`, `ctx.abort`, or a raised `ClickException` inside the `with ctx:` scope left `with_resource`/`call_on_close` resources open. Removing `and exc_type is None` made `_close_with_exception_info(exc_type, exc_value, tb)` run on every outermost pop; the exception still propagated, preserving exit codes (`fail`→2 + `Error: boom`, `abort`→1 + `Aborted!`, raise→1 + `Error: boom`) and nested/group scope teardown.
