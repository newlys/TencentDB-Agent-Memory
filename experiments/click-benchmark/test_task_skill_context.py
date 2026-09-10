import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).with_name("session_driver.py")
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("session_driver", MODULE_PATH)
driver = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(driver)


class TaskSkillContextTest(unittest.TestCase):
    def test_materialized_context_preserves_structure_and_negative_constraints(self):
        content = """---
name: reusable-workflow
description: Example
---

## When to use
- Use when A applies.

## When not to use
- Do not use when B applies.

## Required inputs
- Input one.

## Workflow
1. Inspect state.
2. If X:
   - perform Y
3. Otherwise perform Z.

## Decision rules
- Preserve the branch.

## Validation
- Run the focused check.

## Failure handling / rollback
- Restore the prior state.
"""
        consumption = {
            "status": "MATERIALIZED",
            "selected_candidate": {"name": "reusable-workflow"},
            "content": content,
        }
        rendered = driver.render_materialized_skill_context(consumption)
        self.assertIn("### When Not To Use", rendered)
        self.assertIn("### Required Inputs", rendered)
        self.assertIn("### Failure Handling / Rollback", rendered)
        self.assertIn("1. Inspect state.\n2. If X:\n   - perform Y", rendered)
        self.assertNotIn("repository archaeology", rendered)
        self.assertLessEqual(len(rendered), driver.OURS_V3_SKILL_CONTEXT_CHAR_BUDGET)


if __name__ == "__main__":
    unittest.main()
