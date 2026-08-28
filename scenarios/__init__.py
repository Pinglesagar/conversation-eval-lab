"""The scenario corpus: what the simulated caller wants, and what must be true afterwards.

Scenarios are YAML data — one file per row, grouped into four suites by
directory: `happy/`, `edge/`, `adversarial/` and `voice/`. Shared personas live
in `personas/`. Nothing in this package executes a scenario; `loader.py` parses
and validates the corpus into `lab.simulator` callers and `lab.checks`
contracts, and `lab.simulator.driver` runs them.

Validate the corpus before trusting a result built from it:

    python -m scenarios.loader --summary

TWO NAMES THE FIELD USES FOR WHAT IS IN HERE
--------------------------------------------
**A golden dataset, and the validation that keeps it one.** These are curated,
versioned rows with declared expectations, and `evallab validate --coverage`
does more than check that the YAML is well-formed: it **fails the load on an
assertion that could never fire** — a tracked field no turn supplies, an argument
predicate referencing a fact the scenario does not carry, an `expected_failure`
naming a contract the row does not declare. That is why a successful validate
ends with "every assertion can fire" rather than "every file parsed". Prevention
at load time is a stronger property than detection at run time, and it is what
stops a suite drifting into being green and empty. Coverage prints with its
denominators: suites, per-tag counts, tools constrained, perturbations used, and
the rows that declare an expected failure.

**Red-teaming, in the narrow sense.** `adversarial/` holds 12 of the 55 rows:
prompt injection smuggled inside a booking name, a dietary note, a policy
question and a fake system turn; impersonation; over-reach onto another party's
booking; requests to recite the instructions and list the tools. They are
adversarial inputs, hand-written, committed as data and re-run on every commit —
which is red-teaming in the narrow sense and is *not* a generated attack suite, a
fuzzer, or anything that grows on its own. Twelve rows written by one person is a
corpus, not coverage.

The corpus earns the name anyway, because the rows fail in interesting ways
against a model: on the committed live run the adversarial suite scores 9/12
STABLE_PASS, 2/12 FLAKY, 1/12 STABLE_FAIL, and the failures are a table belonging
to somebody else being modified, and an injected instruction being carried out.
See `docs/VOCABULARY.md`, which maps the rest of this repository's vocabulary
onto the field's.

The package is deliberately excluded from the installed distribution (see
`pyproject.toml`): the corpus is the case study's data, not part of the reusable
`lab` harness, and shipping it inside a library would make one restaurant's
evaluation rows look like part of the framework's API.
"""

from __future__ import annotations
