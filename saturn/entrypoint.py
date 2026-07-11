"""Dependency-light console dispatch for Saturn.

The machine helper is selected before importing the interactive Typer command
surface, keeping renderer, viewer, and provider dependencies out of helper
processes.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "helper":
        from .helper import HelperError, run_job

        if len(args) != 2:
            # Keep the helper protocol machine-readable even for invocation errors.
            import json

            print(json.dumps({"event": "error", "phase": "invalid_invocation",
                              "fraction": 1.0, "message": "usage: saturn helper JOB.json"},
                             separators=(",", ":")), flush=True)
            return 2
        try:
            run_job(Path(args[1]))
        except (HelperError, OSError):
            return 1
        return 0

    from .cli import app

    app(args=args, prog_name="saturn")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
