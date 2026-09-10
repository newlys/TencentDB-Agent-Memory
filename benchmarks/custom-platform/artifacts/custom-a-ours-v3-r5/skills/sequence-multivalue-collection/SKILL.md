---
name: sequence-multivalue-collection
description: Restore collection of every repeated occurrence of a parameter into its declared sequence shape (list, tuple, set, frozenset, deque) when the field annotation is a sequence, while leaving scalar-typed fields on the original single-value read path so scalar semantics, defaults, and missing-value validation stay byte-for-byte unchanged.
---

# Sequence multivalue collection

A parameter can be supplied more than once under the same name (e.g. `?tags=red&tags=blue`, repeated headers). When a read helper only takes the first value (`values.get(alias, None)`), every occurrence but one is silently lost for sequence-declared fields. This SOP restores full repeated-value collection for fields whose declared annotation is a sequence, gated so scalar fields never enter the new path.

## When to use

- A field is declared as a sequence (`list[X]`, `tuple[X, ...]`, fixed-length `tuple[int, int]`, `set[X]`, `frozenset`, `deque`, `typing.Sequence[X]`) and the source container can hold repeated values under one key (multi-dict style sources such as query/header/form parameter bags).
- Symptom: "a list/tuple query (or header) parameter keeps only one occurrence" while scalar parameters behave correctly.
- The framework already routes values through a shared single-value extraction helper; scalar parameters must keep their exact prior behavior.

## When not to use

- Single-valued scalar parameters (str/int/bool/float, and str/bytes even though they are collections) — they must stay on the original `get` path.
- JSON body parsing or other sources where one key genuinely maps to one value.
- Cases where last/first-wins is the intended semantic.

## Required inputs

- The extraction helper where a single value is read from the multi-value container (the `get(alias, None)` call to convert).
- The sequence-detection predicate that classifies a declared annotation: it must return False for `str`/`bytes` and for non-sequence annotations, and it should unwrap `Annotated`/`Union`/`Optional` wrappers so `Optional[list[str]]` is still detected as a sequence.
- Knowledge of which container types support retrieving all values for a key (a `getlist`-equivalent); scalar sources keep `get`.

## Workflow

1. Locate the shared single-value read, e.g. `value = values.get(alias, None)`.
2. Add a guarded branch that runs only when **all** of these hold:
   - the field annotation is a sequence (per the predicate, i.e. not str/bytes, wrappers unwrapped), and
   - the source container is one of the multi-value-capable types.
   Inside that branch read all occurrences: `value = values.getlist(alias)` (or equivalent collect-all call).
3. Keep the original `values.get(alias, None)` as the `else` branch so every scalar and every non-matching source type is untouched.
4. Confirm the downstream coercion still receives the full list: the pydantic/validator layer turns the collected list into the declared shape (tuple, set, list) and enforces fixed-length tuples. Do not pre-dedup or pre-truncate in the read helper.
5. Verify the missing-value path: an empty `getlist` result must still flow through the pre-existing empty-value guard (`len(value) == 0`) that produces the default/None for optional fields and a validation error for required fields.

## Decision rules

- The branch condition, not the concrete parameter name or a global flag, is what separates sequence fields from scalars. Gate strictly on the annotation predicate.
- Sequence detection must treat `str`/`bytes` as scalars (`_annotation_is_sequence` style), otherwise repeated scalar params would suddenly take the multivalue path.
- Only switch to collect-all when the container actually preserves multiple values per key; otherwise `getlist` may be meaningless or absent.

## Validation

- Regression behavior checks with a real client covering: scalar with repeated occurrences still returns one value / first-value semantics; scalar absent returns default; required scalar missing returns 422; required scalar with bad type returns 422.
- Sequence with repeated occurrences returns all values in order (`tags=red&tags=blue` -> `["red", "blue"]`).
- Declared shape is preserved: `tuple[int, int]` returns a real tuple, `set[str]` a real set (assert via the response's type metadata because JSON round-trip serializes tuples/sets as arrays), `list[str]` a list.
- Fixed-length tuple enforcement still works: extra occurrences -> 422, missing element -> 422.
- Missing required sequence -> 422; absent optional sequence -> None/default.
- Run the affected parameter suites (scalar + list + tuple, across query/header/form) in addition to the focused behavior script.

## Failure handling / rollback

- If scalar tests regress, the guard is too broad: re-check the annotation predicate for str/bytes and confirm scalars fall through to `get`.
- If required-missing validation breaks, ensure empty `getlist` results reach the pre-existing empty-value guard rather than being treated as present.
- If fixed-length tuple shape is wrong, the coercion layer must receive the full list unchanged; undo any premature dedup/truncation in the read helper.
- Rollback = restore `values.get(alias, None)` in the sequence branch; scalar behavior is unchanged either way.

## Pitfalls

- Do not assert tuple/set equality on the JSON-decoded response body; tuples and sets serialize to arrays. Assert the declared type via `type(...).__name__` metadata the endpoint returns.
- The scalar guard must also exclude `str`/`bytes`, which are technically sequences but must stay single-valued.
- Empty repeated collections and truly-absent parameters are different states after the fix (`[]` vs `None`); keep routing both through the existing validation/default logic.
- A blanket change of `get` to `getlist` for all fields would silently alter scalar behavior — the annotation gate is the whole point.
