"""A BFSI sales-roleplay coach — the second system under test in this repo.

WHY A SECOND DOMAIN EXISTS AT ALL
---------------------------------
`tablemate/` is a restaurant-booking assistant. This is an AI sales-roleplay
coach for regulated financial advice: a trainee practises a pitch against an AI
customer persona, and the product then grades the trainee against a rubric and
writes them feedback. Nothing about the two domains is alike — different actors,
different tools, different failure modes, different regulator.

What is alike is everything underneath. `lab/` is imported unchanged. The trace
schema, the contract engine, the judge and calibration machinery, and the pass^k
stability primitives all work here without a line of modification, and `lab/` has
never heard of this package. That asymmetry is the point being demonstrated: an
eval framework earns its name by surviving a change of domain, and the only way
to show that is to change the domain.

WHAT IS UNDER TEST, AND IT IS TWO THINGS
----------------------------------------
    roleplay.persona    the AI customer the trainee talks to.  Deliberately clean.
    roleplay.scorer     the grader.  Every seeded defect lives here.

Splitting them matters. A roleplay product's conversational half and its
evaluative half fail in unrelated ways, and a suite that cannot say which one
broke will report "the roleplay is bad" for a year. With the persona correct by
construction, every finding in this pack is attributable to the scorer.

THE THREE DEFECTS, WHICH ARE THE THREE RISKS
--------------------------------------------
They mirror, one for one, the failure classes a scoring product actually ships:

    score instability     the same performance graded differently twice
    hallucinated feedback prose about a conversation that did not happen
    a compliance miss     a certified session that should have failed

Each is a real code path, reachable by ordinary use, deterministic on every
machine, and documented in exactly one place: `roleplay/SEEDED_DEFECTS.md`.
Nothing in `lab/` knows they exist, and no module in this package mentions a
defect it does not itself contain.

WHERE THINGS ARE
----------------
    roleplay.register     jurisdictional disclosure requirements, as records
    roleplay.advisory     the advisory corpus's vocabularies and the four registers
    roleplay.regime_eval  the registers computed: one verdict per regime, per entry
    roleplay.persona      the customer: concerns, objections, manner
    roleplay.scorer       the rubric, the score card, the feedback
    roleplay.runtime      the adapter: one session, one trace, two stages
    roleplay.contracts    the two checks this domain needs and `lab` cannot guess
    roleplay.corpus       the scenario schema and its closed vocabularies
    roleplay.consistency  score spread and pass^k over identical repeats
    roleplay.calibration  the scorer measured against human labels
    roleplay.labels       the labelled set, with a reason per label
    roleplay.demo         `python -m roleplay.demo`, or `make roleplay-demo`

Only `roleplay.runtime`, `roleplay.contracts`, `roleplay.consistency`,
`roleplay.calibration`, `roleplay.corpus` and `roleplay.regime_eval` import from
`lab`. Everything that decides, acts, grades or remembers is `lab`-free — with
`regime_eval` the one that has to be argued rather than asserted: it consumes a
`Trace`, borrows `lab.checks.text` for the matching primitives, and asks
`lab.judges.registry` whether a model-graded limb is allowed to decide anything.
It answers no, which is the point.
"""

from roleplay.runtime import RoleplayCoach, RoleplayResult, run_roleplay
from roleplay.scorer import RubricScorer, ScoreCard

__all__ = [
    "RoleplayCoach",
    "RoleplayResult",
    "RubricScorer",
    "ScoreCard",
    "run_roleplay",
]
