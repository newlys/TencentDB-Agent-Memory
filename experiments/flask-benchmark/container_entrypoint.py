from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> None:
    workspace = Path(os.environ.get("BENCHMARK_WORKSPACE", "/workspace"))
    if workspace.exists():
        os.chdir(workspace)

    src = workspace / "src"
    current = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = str(src) if not current else f"{src}{os.pathsep}{current}"

    args = sys.argv[1:]
    if not args:
        args = ["/bin/bash"]
    elif args[0].endswith(".py"):
        args = [sys.executable, *args]

    os.execvpe(args[0], args, os.environ)


if __name__ == "__main__":
    main()
