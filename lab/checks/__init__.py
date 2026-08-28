"""Deterministic checks over a trace — the assertion engine.

WHAT THIS DEMONSTRATES
----------------------
Anything that can be checked in code should be checked in code. An LLM judge is
the right tool for "was this answer helpful"; it is the wrong tool for "was
`create_booking` actually called", which is a fact about the event stream and
should be asserted with zero variance, zero cost and zero API key.

So this package is a declarative contract language over a `Trace`. Scenario
authors write what must be true as data; the engine reports what was, with the
offending events quoted. Nothing here touches a network or a model.

    from lab.checks import (
        ContractSet, ToolContract, PromiseContract, NoReAskContract,
        FieldPropagationContract, ArgPredicate, TrackedField,
    )

    suite = ContractSet(
        name="large-party-booking",
        contracts=(
            ToolContract(
                expected=("search_tables", "create_booking|hold_table"),
                max_calls={"search_tables": 3},
                args=(ArgPredicate("create_booking", "party_size", ref="party_size"),),
            ),
            PromiseContract(),
            NoReAskContract(fields=(TrackedField("party_size", context_key="party_size"),)),
        ),
    )
    report = suite.run(trace, {"party_size": 6})
    print(report.render())

THE CONTRACTS, AND THE FAILURE EACH ONE OWNS
--------------------------------------------
    ToolContract              wrong, missing, repeated or misparameterised calls
    PromiseContract           the agent said it did something and did not      <- flagship
    NoReAskContract           a handoff lost context and the caller repeats themselves
    FieldPropagationContract  a value never reached the tool argument that needed it
    NoProgressContract        the conversation is looping without advancing
    PhraseContract            required disclosures, forbidden language

`PromiseContract` is the one to read first. It cross-references two channels that
are almost always evaluated separately — what the agent said, and what the agent
did — because the highest-severity failure in any system that both talks and acts
lives exactly in the gap between them, where neither a transcript review nor a
tool-call audit can see it.

THE NAME THE FIELD USES FOR TWO OF THESE: GUARDRAILS
----------------------------------------------------
`ToolContract(forbidden=...)` and `PhraseContract(forbidden=...)` are guardrail
assertions in the ordinary sense — a tool this conversation must never call, a
string the agent must never say — declared as scenario data rather than written
as code. In this repository's corpus, 20 of the 55 rows declare at least one
forbidden tool and 6 declare forbidden phrases; 23 carry at least one of the two,
and 10 of those 23 are in the adversarial suite.

Two qualifications, because the word is often used for something else:

*   These are guardrails in the **testing** sense. They run against a recorded
    trace, offline, after the fact. Nothing here sits in front of a running agent
    and blocks an output, and nothing here is a content-safety or PII classifier.
    A runtime guardrail library and this are complementary, not alternatives: one
    decides at request time, this one tells you whether the decision was right.
*   A guardrail that stopped applying is the failure mode that matters, and it is
    why `applicable=False` exists as a third result. A forbidden-tool contract on
    a conversation that never reached the tool is not evidence of safety, so it is
    counted and printed as a gap instead of as a pass.

See `docs/VOCABULARY.md` for the rest of the mapping between this repository's
vocabulary and the field's.

DESIGN COMMITMENTS
------------------
* **Both directions are tested.** Every contract has tests proving it fires on a
  broken trace *and* stays quiet on a healthy one. A check that never passes is
  as useless as one that never fails, and it is the more expensive of the two,
  because someone eventually deletes the whole suite to stop the noise.
* **Silence is visible.** A contract with nothing to assert returns
  `applicable=False`, not a pass. Vacuous results are counted and printed
  separately so a suite cannot drift into being green and empty.
* **Every rate has a numerator and a denominator.** No function here returns a
  bare percentage.
* **Evidence, not verdicts.** Each result quotes the trace events that justify
  it, so a reviewer can check the claim against the JSONL without trusting the
  harness.
"""

from __future__ import annotations

from lab.checks.contracts import (
    CONFIRMATION_FRAMES,
    DEFAULT_ASK_PATTERNS,
    DEFAULT_HEDGES,
    DEFAULT_PROMISES,
    DEFAULT_ATTRIBUTIONS,
    DEFAULT_REFUSALS,
    ArgPredicate,
    Contract,
    FieldPropagationContract,
    NoProgressContract,
    NoReAskContract,
    Ordering,
    PhraseContract,
    Promise,
    PromiseContract,
    ToolContract,
    TrackedField,
)
from lab.checks.engine import (
    CheckReport,
    CheckStat,
    ContractSet,
    SuiteAggregate,
    aggregate,
    run_contracts,
)
from lab.checks.result import CheckResult, Evidence, quote_event

__all__ = [
    # results
    "CheckResult",
    "Evidence",
    "quote_event",
    # contracts
    "Contract",
    "TrackedField",
    "ArgPredicate",
    "Ordering",
    "ToolContract",
    "Promise",
    "PromiseContract",
    "NoReAskContract",
    "FieldPropagationContract",
    "NoProgressContract",
    "PhraseContract",
    # defaults worth overriding per scenario
    "DEFAULT_PROMISES",
    "DEFAULT_HEDGES",
    "DEFAULT_REFUSALS",
    "DEFAULT_ATTRIBUTIONS",
    "DEFAULT_ASK_PATTERNS",
    "CONFIRMATION_FRAMES",
    # engine
    "ContractSet",
    "CheckReport",
    "CheckStat",
    "SuiteAggregate",
    "run_contracts",
    "aggregate",
]
