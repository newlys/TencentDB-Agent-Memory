"""No-model validation entry point for a longitudinal experiment plan."""
import argparse
import json
from pathlib import Path

from longitudinal_driver import resolve_plan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--check-images", action="store_true")
    args = parser.parse_args()
    plan = resolve_plan(args.plan, check_images=args.check_images)
    print(json.dumps({
        "status": "READY",
        "paid_calls": False,
        "experiment_id": plan["experiment_id"],
        "variant": plan["variant"],
        "sessions": plan["sessions"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
