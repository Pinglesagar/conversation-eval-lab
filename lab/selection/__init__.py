"""Deciding *which* scenarios a change can possibly affect.

This package is the selection layer. It does not run anything: it decides what
to hand to the runner's existing `--suite` / `--tag` / `--scenario` filters.

Three stages, each the cheapest tool that answers its question:

===========================  =================================================
`lab.selection.diff`         what changed — `git diff` plus AST parsing, down
                             to functions, classes and string literals, so a
                             reworded prompt counts as a change. No model.
`lab.selection.trace_map`    which scenarios touch it — derived from the
                             committed traces, because this repository records
                             every run as an ordered stream of typed events.
                             Nobody declares this map, so nobody can let it go
                             stale by forgetting to.
`lab.selection.select`       the join, and the only place that ever says
                             *skip*. Fail-safe by default, additive overrides,
                             and a calibration number with its denominator.
===========================  =================================================

    python -m lab.selection --changed-since HEAD~1

Nothing here is wired into the `evallab` CLI yet; that happens separately, once
the whole selection layer has landed. Each stage carries its own entry point in
the meantime.

The package `__init__` is deliberately empty of imports so that each stage can
be used — and tested — on its own, and so that importing `lab.selection` costs
nothing:

    from lab.selection.select import select
    from lab.selection.trace_map import build_trace_map
"""

from __future__ import annotations
