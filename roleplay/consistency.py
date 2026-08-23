"""Score consistency: what an identical performance scores k times over.

THE QUESTION THIS ANSWERS
-------------------------
Not "does the scorer pass this session". That is a question about one run, and a
number that moves between runs makes it unanswerable. The question is: *hand the
same performance to the grader k times and what comes back?* If the answer is a
range, the product is unreliable, and the size of the range is the product's
error bar — which every downstream claim (certification, coaching, a manager's
dashboard) inherits whether or not anyone has written it down.

TWO VERDICTS, DELIBERATELY SEPARATE
-----------------------------------
`lab.simulator.passk` already answers the binary half: k runs, how many passed,
STABLE_PASS / FLAKY / STABLE_FAIL. That machinery is reused unmodified, because
"the pass/fail verdict flipped between runs" means the same thing in any domain.

What it cannot express is the *magnitude*. A rubric score is an ordinal, and two
scenarios can both be FLAKY at 3/5 while one moves by a point and the other moves
by six. So `ScoreSpread` reports mean, population standard deviation, min, max and
the full ordered list of scores, and scores that spread against a stated
tolerance. Both verdicts are printed together and neither is derived from the
other: a run can be STABLE_PASS and still move four points inside the pass band,
which is a real finding a binary gate cannot see.

WHY THE CONTROL IS THE INTERESTING HALF
---------------------------------------
`run_pass_k` documents, correctly, that its `run` callable must build a fresh
agent per repeat — an agent carrying conversation state from the last repeat is
measuring history, not stability. Applied naively here that advice *hides the
defect*: the seeded instability lives in state the scoring **service** holds
across sessions, and a harness that builds a fresh service per repeat measures a
cold process that nobody deploys.

So this module runs it both ways and reports the pair:

    warm   k sessions against one long-lived scorer   — the production shape
    cold   k sessions, a fresh scorer each time       — the control

A difference between the two localises the instability to shared state without
anybody having to be told where to look, exactly as the boundary pair in the
booking case study localises a defect to a threshold. Identical results in both
would mean the instability is inside a single session's grading instead, and the
next place to look would be the model call rather than the process.

The general lesson, which is not domain-specific: **a stability harness that
resets more than the deployment does cannot see state-leak instability.** Choosing
what to reset between repeats is a measurement decision, and it belongs in the
report next to the number it produced.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Literal, Sequence

from lab.simulator.passk import (
    PassKPolicy,
    RunOutcome,
    StabilityVerdict,
    verdict_from_outcomes,
)
from lab.trace.schema import Trace

from roleplay.persona import CustomerProfile
from roleplay.runtime import RoleplayCoach, RoleplayResult
from roleplay.scorer import PASS_TOTAL, RubricScorer

__all__ = [
    "SpreadVerdict",
    "ScoreSpread",
    "ConsistencyReport",
    "spread_of",
    "measure_consistency",
]

SpreadVerdict = Literal["WITHIN_TOLERANCE", "OUTSIDE_TOLERANCE"]


@dataclass(frozen=True)
class ScoreSpread:
    """What k identical runs scored, and whether that is acceptable.

    `tolerance` is in rubric points and is stated by the caller, never defaulted
    silently to something generous. A tolerance nobody can see is not a standard —
    the same argument `lab.judges.CalibrationThresholds` makes about its 0.85, and
    the reason `describe()` always prints it.
    """

    scenario_id: str
    scores: tuple[int, ...]
    max_total: int
    tolerance: float
    label: str = ""

    @property
    def k(self) -> int:
        return len(self.scores)

    @property
    def mean(self) -> float:
        return round(sum(self.scores) / self.k, 3) if self.k else 0.0

    @property
    def stdev(self) -> float:
        """Population standard deviation, not sample.

        These k runs are the entire population of interest — every run of one
        fixed transcript against one build — rather than a sample drawn from a
        larger one, so dividing by k is the honest denominator. It also keeps the
        figure defined at k=1 (0.0) instead of raising, which matters because k=1
        is a legitimate, and legitimately weak, thing to report.
        """
        if self.k == 0:
            return 0.0
        mean = sum(self.scores) / self.k
        return round(math.sqrt(sum((s - mean) ** 2 for s in self.scores) / self.k), 3)

    @property
    def minimum(self) -> int:
        return min(self.scores) if self.scores else 0

    @property
    def maximum(self) -> int:
        return max(self.scores) if self.scores else 0

    @property
    def spread(self) -> int:
        """Max minus min, in rubric points. The number a manager would ask for."""
        return self.maximum - self.minimum

    @property
    def verdict(self) -> SpreadVerdict:
        return "WITHIN_TOLERANCE" if self.spread <= self.tolerance else "OUTSIDE_TOLERANCE"

    @property
    def ok(self) -> bool:
        return self.verdict == "WITHIN_TOLERANCE"

    @property
    def verdict_flips(self) -> int:
        """How many times the pass/fail verdict changed across the ordered runs.

        Distinct from "how many failed". Three passes then two fails is one flip;
        alternating pass/fail five times is four, and the second is a much worse
        product even though both report 3/5. Order is meaningful here precisely
        because these runs are not independent samples — that is the finding.
        """
        verdicts = [s >= PASS_TOTAL for s in self.scores]
        return sum(1 for a, b in zip(verdicts, verdicts[1:], strict=False) if a != b)

    def describe(self) -> str:
        tag = f"{self.label} " if self.label else ""
        return (
            f"{tag}{self.verdict} {self.scenario_id}: k={self.k} identical runs scored "
            f"{list(self.scores)} -- mean {self.mean}/{self.max_total}, "
            f"sd {self.stdev}, range {self.minimum}-{self.maximum} "
            f"(spread {self.spread} pt, tolerance {self.tolerance} pt), "
            f"{self.verdict_flips} pass/fail flip(s) at threshold {PASS_TOTAL}"
        )


def spread_of(
    scenario_id: str,
    scores: Sequence[int],
    *,
    max_total: int,
    tolerance: float,
    label: str = "",
) -> ScoreSpread:
    """Build a `ScoreSpread` from scores computed elsewhere.

    Separate from `measure_consistency` for the same reason
    `lab.simulator.passk.verdict_from_outcomes` is separate from `run_pass_k`: a
    suite that ran its repeats in CI shards, or replayed them from stored traces,
    must be scored by exactly the same code as one that ran them in a loop.
    """
    return ScoreSpread(
        scenario_id=scenario_id,
        scores=tuple(int(s) for s in scores),
        max_total=max_total,
        tolerance=tolerance,
        label=label,
    )


@dataclass(frozen=True)
class ConsistencyReport:
    """The warm/cold pair, with both verdict families for each.

    Four verdicts, printed together, because that is the smallest set from which
    a reader can reach the conclusion themselves rather than being told it.
    """

    scenario_id: str
    warm_spread: ScoreSpread
    cold_spread: ScoreSpread
    warm_stability: StabilityVerdict
    cold_stability: StabilityVerdict

    @property
    def localises_to_shared_state(self) -> bool:
        """True when the cold control is clean and the warm run is not.

        The one inference this object is willing to make, and it is a narrow one:
        identical input, same code, the only difference being whether the scoring
        service was reused. It says nothing about *which* piece of state.
        """
        return self.cold_spread.spread == 0 and self.warm_spread.spread > 0

    def render(self) -> str:
        lines = [
            f"score consistency -- {self.scenario_id}",
            f"  warm (one long-lived scorer, the production shape)",
            f"    {self.warm_spread.describe()}",
            f"    {self.warm_stability.describe()}",
            f"  cold (a fresh scorer per repeat, the control)",
            f"    {self.cold_spread.describe()}",
            f"    {self.cold_stability.describe()}",
        ]
        if self.localises_to_shared_state:
            lines.append(
                "  -> the cold control is flat and the warm run is not, so the "
                "instability is in state the scoring service holds between sessions, "
                "not in the grading of any one session"
            )
        return "\n".join(lines)


def _outcomes(scores: Sequence[int], traces: Sequence[Trace]) -> list[RunOutcome]:
    """Turn per-run scores into pass^k outcomes carrying their own evidence."""
    outcomes: list[RunOutcome] = []
    for index, (score, trace) in enumerate(zip(scores, traces, strict=True)):
        passed = score >= PASS_TOTAL
        outcomes.append(
            RunOutcome(
                index=index,
                passed=passed,
                session_id=trace.session_id,
                evidence=f"scored {score} against a pass threshold of {PASS_TOTAL}",
                failed_checks=[] if passed else ["rubric-total-below-threshold"],
            )
        )
    return outcomes


def measure_consistency(
    *,
    scenario_id: str,
    trainee_turns: Sequence[str],
    profile: CustomerProfile,
    k: int = 5,
    tolerance: float = 0.0,
    policy: PassKPolicy | None = None,
    make_scorer: Callable[[], RubricScorer] | None = None,
) -> ConsistencyReport:
    """Run one identical transcript k times, warm and cold, and score both ways.

    Args:
        scenario_id: Identifier carried into every verdict.
        trainee_turns: The fixed trainee performance. Identical every repeat —
            that is the experiment.
        profile: Which customer the trainee practised against.
        k: Repeats per arm. Both arms use the same k so the two spreads are
            directly comparable.
        tolerance: Acceptable spread in rubric points. Defaults to 0: a rubric
            score that moves at all on identical input is a defect, and a
            non-zero tolerance should be an argued exception rather than the
            starting position.
        policy: pass^k policy. Defaults to unanimity.
        make_scorer: Factory for a scorer, so a caller can vary the curve
            settings. Defaults to `RubricScorer()`.

    Returns:
        A `ConsistencyReport`.
    """
    if k < 2:
        raise ValueError(
            f"k must be at least 2 to say anything about consistency, got {k!r}; "
            "one run cannot disagree with itself"
        )
    factory = make_scorer if make_scorer is not None else RubricScorer

    def arm(*, warm: bool) -> tuple[list[int], list[Trace]]:
        coach = RoleplayCoach(scorer=factory()) if warm else None
        scores: list[int] = []
        traces: list[Trace] = []
        for index in range(k):
            active = coach if coach is not None else RoleplayCoach(scorer=factory())
            result: RoleplayResult = active.run(
                scenario_id=scenario_id,
                trainee_turns=trainee_turns,
                profile=profile,
                session_id=f"{scenario_id}-{'warm' if warm else 'cold'}-{index}",
            )
            scores.append(result.card.total)
            traces.append(result.trace)
        return scores, traces

    warm_scores, warm_traces = arm(warm=True)
    cold_scores, cold_traces = arm(warm=False)
    max_total = 20

    return ConsistencyReport(
        scenario_id=scenario_id,
        warm_spread=spread_of(
            scenario_id, warm_scores, max_total=max_total, tolerance=tolerance, label="warm"
        ),
        cold_spread=spread_of(
            scenario_id, cold_scores, max_total=max_total, tolerance=tolerance, label="cold"
        ),
        warm_stability=verdict_from_outcomes(
            scenario_id,
            _outcomes(warm_scores, warm_traces),
            policy=policy,
            label="warm: one long-lived scorer",
        ),
        cold_stability=verdict_from_outcomes(
            scenario_id,
            _outcomes(cold_scores, cold_traces),
            policy=policy,
            label="cold: fresh scorer per repeat",
        ),
    )
