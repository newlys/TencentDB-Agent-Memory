---
name: layered-input-precedence
description: Restore or preserve precedence among layered input sources for a value, so an explicit higher-priority source (user-supplied CLI argument, direct render_template keyword, per-request/per-instance override) is never overwritten by an implicit fallback source (environment variable, config map, app-wide config, declared default, context-processor injection), while keeping each fallback live when the higher-priority source is absent.
---

# Layered input precedence (explicit beats implicit fallback)

## When to use
- A value can be supplied through several ordered sources, and the intended precedence is explicit user input > implicit runtime/environment/provider injection > declared default, but a lower-priority source is observed to override a higher-priority one.
- Typical symptoms:
  - "when env var FOO is set, command-line values supplied to an optional variadic argument are ignored" — values come back from the environment instead of the command line. The same root cause can appear for any multi-value/optional parameter whose fallback lookup is keyed on a parameter trait rather than on absence of a higher-priority value.
  - "values passed directly to render_template are replaced when a context processor returns the same key" — the processor's value renders instead of the explicitly passed value. Same shape for app-level, blueprint-level, and default context processors, or any layered provider injection merged into a shared dict.
  - "a request-specific / per-instance override of a limit is ignored whenever the application also defines the config key (e.g. per-request max_form_parts always returns the app-wide MAX_FORM_PARTS)" — the value's getter reads the config/base layers directly and never consults the per-instance attribute its own setter writes. Same shape whenever an object/request-level override coexists with a global config value and a library/class default.
- Auditing a layered resolution/merge function after adding a new source, changing arity/multi-value handling, or reordering provider layers.

## When not to use
- One-off wording/error-message fixes or localized corrections with no source-resolution/merge logic.
- Work that does not involve merging values from multiple sources or preserving their ordering.

## Required inputs
- The definition of the value and its ordered source bindings (e.g. envvar/config keys for a CLI option; the render-context dict plus registered context processors for a template; a per-instance attribute plus a config key plus the base-class default for a request/object property), including how each source represents "no value" (unique sentinel, None, empty collection, or simply an absent key).
- The merge function that combines the sources (often named like `consume_value`/`resolve_value`, a shared `update_template_context` called by every rendering entry point, or a property getter plus setter pair).
- Whether the higher-priority layer's container is mutated in place by the merge (dict.copy() semantics matter for a snapshot strategy).
- A minimal repro harness covering each source combination.

## Workflow
1. Reproduce with a minimal script exercising each source combination: explicit/high-priority layer only; implicit layer only; both set (the conflict); implicit absent; implicit set-but-empty. Confirm which combination misbehaves; non-conflicting combinations are typically already correct.
2. Read the merge/resolution function. Enumerate the ordered layer chain and note the guard or update order for each layer, including every implicit sub-layer scope (e.g. default/app provider, then blueprint providers).
3. Diagnose the root cause. Common shapes:
   - Gate bug: a fallback lookup runs even though the higher-priority layer already produced a value, because the guard tests a parameter trait (e.g. `nargs == -1`, or an unconditional provider chain) instead of "higher layer is absent". Observable only when the conflicting implicit source is actually set non-empty.
   - Missing re-apply: the merge function snapshots the explicit layer ("keep a copy to re-apply") and then runs the lower layers but never re-applies the snapshot, so any lower layer returning the same key overwrites the explicit value.
   - Missing high-priority gate: the getter/resolver returns the implicit layers (config, then base default) directly and never reads the per-instance attribute its setter stores, so the highest-priority layer is unreachable. Observable whenever the config/default is set. Detectable by diffing against a sibling getter for a neighboring value that already implements the correct chain.
4. Fix with the strategy matching the shape:
   - Gate-based: gate each fallback lookup strictly on every higher layer being absent, e.g. change `if value is UNSET or self.nargs == -1:` to `if value is UNSET:`. First normalize the current layer's "no value" representation to the shared sentinel (empty variadic/multi-value collections may need converting, e.g. `()` -> `UNSET`, so the next fallback still fills in).
   - Snapshot-and-re-apply: before mutating, copy the explicit/higher-priority container (`orig_ctx = context.copy()`), run every lower layer in its intended order into the shared container, then re-apply the snapshot last (`context.update(orig_ctx)`). Because the snapshot is applied after all lower layers, only conflicting keys are restored to the explicit value and non-conflicting injected keys survive.
   - Missing high-priority gate: prepend the instance check so the override returns first and lower layers only run when it is absent: `if self._max_form_parts is not None: return self._max_form_parts`, then the config lookup, then `super()`. Mirror the ordering already proven by sibling getters for the same concept. Because the override is cleared by setting it to the sentinel (`None`), the sentinel means "absent" and must still expose the config value — never let the unset attribute become an active override.
5. Preserve every fallback path: after the fix verify the implicit-only case still fills values, empty/absent implicit still falls through to the next layer, and the high-priority source wins on each conflicting key while non-conflicting lower-layer values still appear.
6. Add regression tests: (a) explicit value supplied while a lower layer provides the same key, asserting the explicit value wins and a non-conflicting lower-layer value still appears; (b) same key supplied only by the lower layer, asserting it is still used; (c) for per-instance/request overrides: override set with and without the app context both return the override, a fresh instance without an override returns the config value, and clearing the override (setting it to `None`) restores the config fallback. Cover both single-scope and multi-scope implicit layers (e.g. blueprint processors plus app providers).
7. Prove the tests are meaningful: temporarily restore the buggy behavior and confirm the new tests fail, then re-apply the fix and confirm they pass.
8. Run the surrounding test modules, then the full suite; attribute unrelated failures via a baseline check (git stash on a pristine tree).

## Decision rules
- Precedence: explicit/user value > implicit provider layer(s) > declared default; lower layers only supply what higher layers did not. For an object/request property the chain is per-instance override > app-wide config > library/class default.
- A layer that is set-but-empty or absent must not block deeper fallbacks. A per-instance override cleared to the sentinel (`None`) is absent and must fall back to config again.
- In a keyed merge, precedence is per-key: an explicit key wins; a key provided only by a lower layer still comes through.
- Ordered implicit sub-layers (e.g. app-wide provider, then more-specific blueprint providers) all run before the final explicit re-apply; later sub-layers normally win within the implicit group.
- Parameter traits (variadic, multiplicity, arity) must not by themselves reopen a fallback lookup that would overwrite a value already obtained from a higher layer.
- When the shared container is mutated in place, always snapshot before applying lower layers, and re-apply only after the whole chain (every provider scope) has run.

## Validation
- Matrix check: each source combination returns the value from the highest-priority present layer; absent/empty cases fall through to the next layer; non-conflicting lower-layer values are preserved.
- Regression tests fail on the original code and pass with the fix (verified by temporarily reverting the fix).
- Existing behavior tests for all fallback paths still pass; full suite green except pre-existing unrelated failures.

## Failure handling / rollback
- If a previously passing behavior test regresses, check whether the fix made a fallback unreachable, changed the "absent" representation, or re-applied the snapshot too early (e.g. before a signal/extension handler mutates the context). Re-check before reverting.
- If full-suite output shows a failure, confirm via `git stash` whether it is pre-existing and unrelated (e.g. importlib.metadata/package-install errors) before attributing it to the change; only failures that appear with the change and disappear on revert are regressions.

## Pitfalls
- Variadic/multi-value arguments that match nothing are often parsed as empty collections and normalized to the "no value" sentinel elsewhere in the pipeline; find and respect that normalization or the implicit-only path breaks after a gate-based fix.
- The observable bug appears only when the conflicting implicit source is actually set/present, which hides the defect in environments where it is usually unset — always test the conflict combination explicitly.
- A comment already stating the intended precedence ("The values passed to render_template take precedence. Keep a copy to re-apply...") is a strong hint that a re-apply step was intended but never executed — grep for the snapshot variable to confirm it is actually used after the lower layers run.
- When several sibling getters implement the same layered chain, the one that skips its own per-instance attribute while its neighbors check theirs is the bug — diff the getters before theorizing about the config.
- Restore precedence at the end of the whole chain (after every provider scope, including blueprint processors), not just after the first/global provider.
- Do not "fix" by deleting a fallback layer: the contract is that the implicit layer still applies when no explicit value is given.
