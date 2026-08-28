"""`python -m lab.selection` — the selection layer's entry point.

Deliberately its own entry point rather than an `evallab` subcommand: the CLI is
owned elsewhere and the selection layer is wired into it separately, once every
stage has landed. Until then:

    python -m lab.selection --changed-since HEAD~1        # the report
    python -m lab.selection --changed-since HEAD~1 --json # the same, machine-readable
    python -m lab.selection --runner-args                 # arguments for `evallab run`
    python -m lab.selection --calibrate                   # the grader's own number

The import is inside `_run` so that `python -m lab.selection --help` and any
tooling that merely imports the package pay nothing for the dependency graph
underneath, and so that `lab/selection/__init__.py` can stay import-free.
"""

from __future__ import annotations


def _run() -> int:
    from lab.selection.select import main

    return main()


if __name__ == "__main__":  # pragma: no cover - exercised through select.main()
    raise SystemExit(_run())
