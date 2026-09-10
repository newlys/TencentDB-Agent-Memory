---
name: final-matching-release-cleanup
description: Make teardown/cleanup for an object that can be acquired (pushed) more than once run exactly once, at the final matching release that clears the outstanding-owner count, so intermediate releases leave the resource active for outer owners and single-acquire behavior stays unchanged.
---

# Final-matching-release cleanup (deferred teardown for nested/reentrant acquisition)

## When to use
- An object/scope can be acquired (pushed, entered) more than once — the same instance is re-acquired while already active, either directly (`ctx.push(); ctx.push()`) or through internal machinery that re-enters the currently active object (`with <active ctx>:` for streaming/preservation) — and a release (pop) tears it down before the last owner is done.
- Typical symptoms:
  - "Pushing the same context twice makes the first pop tear it down immediately, leaving the outer owner with an inactive context; the outer pop then raises 'not pushed'."
  - "Intermediate pops must keep the resource active; cleanup should run exactly once at the final matching pop; single-push behavior should stay unchanged."
- Auditing any acquire/release pair that keeps an outstanding-acquisition counter and gates teardown on that counter after an off-by-one change or refactor.

## When not to use
- Cleanup is skipped on some exit *path* (error/abort/early exit) while it runs on clean exit — that is an all-path-cleanup gap, not a premature-final-release problem.
- Distinct nested objects each torn down at their own pop (ordinary stack discipline) with no same-object re-acquisition.
- Localized wording/error-message corrections or a fix with no lifecycle/refcount logic.

## Required inputs
- The acquire method (`push`/`__enter__`) that increments the outstanding-owner counter and performs one-time side effects only on the first acquisition (binding a token, opening a session, routing, sending signals).
- The release method (`pop`/`__exit__`) with the decrement and the early-return threshold that decides when teardown runs.
- The documented intended semantics (docstring/comments often already state it: "cleanup only once it has been popped as many times as it was pushed").
- A minimal repro harness and the active/teardown observables (active-context query, teardown callback event list, signal counters).

## Workflow
1. Reproduce with a minimal script over acquisition counts (1, 2, 3) of the *same* object: push N times, then pop N times, recording after each pop whether the context is still active and whether teardown fired. Confirm the bug shape: for N=2 the first pop already tears down (active-state false, teardown fired, token cleared), and the outer pop raises "Cannot pop ... it is not pushed".
2. Read the release method. Identify the outstanding-owner counter (`_push_count`), its decrement, and the early-return guard that decides whether teardown runs. Read the acquire method to see which one-time side effects are already gated on "not currently acquired" (token set, signals, session open, matching) — those should already fire once per outermost acquire and need no change.
3. Diagnose the off-by-one threshold: the guard returns early only while 2+ owners remain *after* the decrement (`if count > 1: return`), so a pop that leaves exactly 1 outstanding owner falls through into teardown — tearing down while an outer owner still holds the object. The invariant is: teardown runs only when no owner remains, so return early whenever *any* owner remains after the decrement (`if count > 0: return`).
4. Apply the one-line guard change (`> 1` → `> 0`). Do not touch the acquire-side one-time gates.
5. Validate over the acquisition-count matrix (1, 2, 3): intermediate pops keep the context active with no teardown and no popped signal; the final matching pop runs teardown exactly once and deactivates. Assert single-push behavior is byte-identical.
6. Also exercise real nested-use machinery that re-acquires the *same* currently-active object (e.g. a streaming wrapper that does `with ctx:` on the active context, or a preserved test-client context). These prove the outer-owner scenario in its production shape: after the inner holder's pop the outer machinery still sees an active context, and teardown fires exactly once when the last use ends.
7. Add regression tests: (a) same-object double push then pops — intermediate pop keeps active with empty teardown events, final pop tears down once; (b) request-context variant when the same class backs both; (c) single-push test asserting teardown plus pushed/popped signals fire once (this test must pass both before and after the fix, proving behavior unchanged); (d) a nested-use test through the real re-acquire machinery (stream-like: create the wrapper, pop the outer context, then close the wrapper — final matching pop runs teardown once).
8. Prove tests are meaningful: temporarily restore the buggy guard and confirm the new tests (and any pre-existing nested-use tests) fail, then re-apply the fix and confirm they pass. Run the surrounding test modules, then the full suite; attribute unrelated failures via a baseline check.

## Decision rules
- Teardown runs only when the outstanding-owner count reaches 0 at the final matching release. Any owner remaining after a pop must keep the object active with no teardown.
- The acquire side already gates one-time effects (token set, signals, session open, routing) on "not currently acquired"; leave that gate intact and change only the release-side threshold.
- Single-acquire behavior is an invariant: one push + one pop must produce the same teardown and signal sequence before and after the fix.
- Distinct context objects each tear down at their own final pop; do not confuse that with the same-object re-acquisition case when counting teardown events.

## Validation
- Active-state and teardown-event assertions after every intermediate and final pop for counts 1, 2, 3: active until the last pop, teardown events `== [teardown]` exactly at the last pop, `[]` before it.
- Signal counters (`pushed`, `popped`) equal one each across an outermost acquire/release pair.
- Nested-use scenario through real machinery: outer pop leaves the object active, closing/finishing the nested use runs teardown exactly once.
- New regression tests fail on the original guard and pass with the fix; the single-push test passes under both. Full suite green except pre-existing unrelated failures.

## Failure handling / rollback
- If an intermediate pop still tears down, the threshold is still wrong (guard is checking the wrong side of the count) — return early while any owner remains.
- If single-push behavior changed (teardown no longer runs, or signals fire differently), the acquire-side one-time gates were disturbed or the threshold now skips the count-0 case — verify `count > 0` still falls through when count reaches 0.
- If a nested *different*-object scenario appears to tear down early, check the harness: teardown handlers run per distinct context object, so event lists accumulate across objects; assert active-state per object via the raw context-var lookup, not proxy identity.
- Fallback: restore the original guard; the new regression tests must then fail, proving the fix was necessary and sufficient.

## Pitfalls
- Test-harness proxy identity: `current_app is app` is always False because it is a proxy — query the active context with `has_app_context()`/`has_request_context()` or `_cv_app.get(None) is ctx`.
- Counting teardown events across multiple distinct context objects is misleading — the same-object double-push case must track events while only that object is involved.
- In production the "nested use" usually comes from internal machinery re-entering the currently active object (streaming wrappers, preserved contexts), not from a user double-push — grep for `with <active ctx>:` patterns to find the outer-owner scenario.
- The defect hides in single-push usage: count 1→0 never hit the buggy `> 1` branch, so ordinary tests pass; always test the conflict combination (2+ acquisitions, first pop) explicitly.
- A docstring/comment already stating "cleanup only after popped as many times as pushed" is a strong hint that the release guard is the intended invariant — trust it over the literal guard.

## Evidence
Flask `AppContext` (`src/flask/ctx.py`): `push` increments `_push_count` and only binds the contextvar token / sends `appcontext_pushed` / opens the session and matches routing when `_cv_token is None` (first acquisition). `pop` decremented and returned early only when `_push_count > 1`, so the first pop of a context pushed twice (2→1) fell through into teardown — running `do_teardown_request`/`do_teardown_appcontext`, closing the request, resetting the token, and sending `appcontext_popped` — leaving the outer owner with an inactive context whose next pop raised "Cannot pop ... it is not pushed". Changing the guard to `> 0` deferred cleanup to the final matching pop. This also un-broke real nested uses where `stream_with_context` re-pushes the currently active context via `with ctx:` and the test client preserves and re-enters contexts; with the buggy guard those pre-existing tests (`test_context_refcounts`, `test_client_pop_all_preserved`, all streaming tests) failed with the same premature-teardown RuntimeError. Single-push behavior (1→0, teardown fires) was identical under both guards.
