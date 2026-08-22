"""Deterministic checks over a trace.

WHAT THIS DEMONSTRATES
----------------------
Anything that can be checked in code should be checked in code. An LLM judge is
the right tool for "was this answer helpful"; it is the wrong tool for "was
create_booking actually called", which is a fact about the event stream and
should be asserted with zero variance, zero cost and zero API key.

Empty in the foundation commit. The checks themselves — decision-vs-action
(did the agent do what it said it did), no-re-ask (did a handoff lose context),
and field propagation (did a value the caller gave survive to the tool call) —
are built in the step that adds the system under test, so that each check ships
with a scenario that provably fails without it.
"""
