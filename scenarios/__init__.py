"""The scenario corpus: what the simulated caller wants, and what must be true afterwards.

Scenarios are YAML data — one file per row, grouped into four suites by
directory: `happy/`, `edge/`, `adversarial/` and `voice/`. Shared personas live
in `personas/`. Nothing in this package executes a scenario; `loader.py` parses
and validates the corpus into `lab.simulator` callers and `lab.checks`
contracts, and `lab.simulator.driver` runs them.

Validate the corpus before trusting a result built from it:

    python -m scenarios.loader --summary

The package is deliberately excluded from the installed distribution (see
`pyproject.toml`): the corpus is the case study's data, not part of the reusable
`lab` harness, and shipping it inside a library would make one restaurant's
evaluation rows look like part of the framework's API.
"""

from __future__ import annotations
