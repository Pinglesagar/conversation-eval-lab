"""`python -m roleplay.demo` — the whole pack in one run, offline, no API keys.

WHAT IT PRINTS, AND WHY IN THIS ORDER
-------------------------------------
    1. corpus validation      the dataset is a dataset before it is evidence
    2. per-row contracts      what each session did, and which reds were declared
    3. score consistency      the same performance graded five times, warm and cold
    4. scorer calibration     the grader measured against the human column, and gated
    5. the findings           three defects, named, with the check that caught each

Each step makes the next believable. A finding from an unvalidated corpus is an
anecdote; a rate from an uncalibrated grader is a number with no units; and a
consistency verdict without its control localises nothing.

TWO VERDICTS, AND THEY ARE NOT THE SAME VERDICT
-----------------------------------------------
The findings are **red**: the product under test has three real defects and this
run reports all of them. The **exit code** is about something else — whether the
run matched what the corpus said to expect. Every declared `expected_failure`
must fire, no undeclared contract may fail, every declared spread floor must be
met, and the calibration gate must refuse the grader. That is a regression gate
over findings rather than a health check on the product, and conflating the two is
how a suite ends up either permanently red and ignored, or green and blind.

So: a green exit with a red report means "nothing moved since the last review". A
red exit means a finding appeared, disappeared, or changed shape, and somebody
has to look.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from lab.checks.engine import CheckReport, aggregate
from lab.judges.calibration import CalibrationThresholds

from roleplay.calibration import calibrate_scorer, gate_report, render_disagreements
from roleplay.consistency import ConsistencyReport, measure_consistency
from roleplay.corpus import Corpus, Scenario, validate_corpus
from roleplay.runtime import RoleplayCoach, RoleplayResult
from roleplay.scorer import PASS_TOTAL, RubricScorer

__all__ = ["DemoOutcome", "run_demo", "main"]

_RULE = "=" * 78


@dataclass
class DemoOutcome:
    """Everything the run found, plus the list of things that were not expected."""

    corpus: Corpus
    results: dict[str, RoleplayResult] = field(default_factory=dict)
    reports: dict[str, CheckReport] = field(default_factory=dict)
    consistency: dict[str, ConsistencyReport] = field(default_factory=dict)
    surprises: list[str] = field(default_factory=list)
    gate_cleared: bool = True
    gate_reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the run matched the corpus's declared expectations."""
        return not self.surprises


def _row(scenario: Scenario, result: RoleplayResult, report: CheckReport) -> str:
    agree = "agrees" if result.card.verdict == scenario.expectation.human_verdict else "DIFFERS"
    return (
        f"  {scenario.id:<46} human={scenario.expectation.human_verdict:<4} "
        f"scorer={result.card.verdict:<4} ({result.card.total:2d}/20) {agree:<7} "
        f"checks {report.passed}/{report.applicable} pass, {report.failed} fail, "
        f"{report.vacuous} vacuous"
    )


def run_demo(*, k_override: int | None = None) -> DemoOutcome:
    """Run every stage and collect the outcome. Prints as it goes."""
    print(_RULE)
    print("1. CORPUS VALIDATION")
    print(_RULE)
    validation = validate_corpus()
    print(validation.render(coverage=True))
    if not validation.ok:
        outcome = DemoOutcome(corpus=validation.corpus)
        outcome.surprises.append("the corpus does not validate; nothing below is evidence")
        return outcome

    corpus = validation.corpus
    outcome = DemoOutcome(corpus=corpus)

    print()
    print(_RULE)
    print("2. ONE RUN PER ROW  (a fresh scoring service each, so the curve is out of scope)")
    print(_RULE)
    for scenario in corpus:
        result = RoleplayCoach(scorer=RubricScorer()).run(
            scenario_id=scenario.id,
            trainee_turns=scenario.trainee.turns,
            profile=corpus.profile_for(scenario),
            session_id=f"demo-{scenario.id}",
            jurisdiction=scenario.jurisdiction,
            language=scenario.language,
        )
        report = scenario.contract_set().run(result.trace)
        outcome.results[scenario.id] = result
        outcome.reports[scenario.id] = report
        print(_row(scenario, result, report))

        for failure in report.failures():
            declared = scenario.expects_failure_of(failure.name)
            tag = "declared" if declared else "UNDECLARED"
            print(f"      [{tag}] {failure.name}: {failure.detail}")
            for item in failure.evidence[:2]:
                print(f"          {item.render()}")
            if not declared:
                outcome.surprises.append(
                    f"{scenario.id}: {failure.name} failed and no expected_failure declares it"
                )
        for name in scenario.expected_failure.contracts if scenario.expected_failure else ():
            result_for = report.by_name(name)
            if result_for is not None and result_for.passed:
                outcome.surprises.append(
                    f"{scenario.id}: {name} is declared as an expected failure and passed; "
                    "either it was fixed or the row stopped exercising it"
                )

    rolled = aggregate(list(outcome.reports.values()), suite="roleplay")
    print()
    print(rolled.render())

    print()
    print(_RULE)
    print("3. SCORE CONSISTENCY  (identical transcript, k repeats, warm service vs cold)")
    print(_RULE)
    for scenario in corpus:
        spec = scenario.consistency
        if spec is None:
            continue
        report = measure_consistency(
            scenario_id=scenario.id,
            trainee_turns=scenario.trainee.turns,
            profile=corpus.profile_for(scenario),
            k=k_override or spec.k,
            tolerance=spec.tolerance,
        )
        outcome.consistency[scenario.id] = report
        print(report.render())
        if spec.expected_spread is not None and report.warm_spread.spread < spec.expected_spread:
            outcome.surprises.append(
                f"{scenario.id}: warm spread is {report.warm_spread.spread} pt, below the "
                f"declared floor of {spec.expected_spread}; the curve may have been fixed"
            )
        if spec.expected_flips is not None and report.warm_spread.verdict_flips < spec.expected_flips:
            outcome.surprises.append(
                f"{scenario.id}: warm run flipped {report.warm_spread.verdict_flips} time(s), "
                f"below the declared floor of {spec.expected_flips}"
            )
        if spec.control and report.cold_spread.spread > spec.tolerance:
            outcome.surprises.append(
                f"{scenario.id}: the cold control arm is not flat (spread "
                f"{report.cold_spread.spread} pt), so the instability is no longer "
                "localised to state held between sessions"
            )
        print()

    print(_RULE)
    print("4. SCORER CALIBRATION  (the grader is a judge; measure it, then gate on it)")
    print(_RULE)
    report, judge, items = calibrate_scorer(corpus)
    print(report.to_text())
    print()
    thresholds = CalibrationThresholds()
    cleared, reasons = gate_report(report, judge, thresholds=thresholds)
    outcome.gate_cleared = cleared
    outcome.gate_reasons = list(reasons)
    print(f"calibration gate ({thresholds.describe()}): "
          f"{'CLEARED' if cleared else 'REFUSED'}")
    for reason in reasons:
        print(f"  - {reason}")
    if cleared:
        outcome.surprises.append(
            "the scorer cleared the calibration gate; on this build it is expected to be "
            "refused on recall, so either the scorer improved or the label set moved"
        )
    print()
    print(render_disagreements(report))

    print()
    print(_RULE)
    print("5. WHAT WAS FOUND")
    print(_RULE)
    print(_findings(outcome))
    return outcome


def _findings(outcome: DemoOutcome) -> str:
    """The three defects, each with the check that caught it and the row it fired on."""
    lines: list[str] = []

    unstable = [
        (sid, r)
        for sid, r in outcome.consistency.items()
        if r.warm_spread.spread > r.warm_spread.tolerance
    ]
    lines.append("SCORE INSTABILITY")
    if unstable:
        for sid, r in unstable:
            lines.append(
                f"  {sid}: identical input scored {list(r.warm_spread.scores)} "
                f"(spread {r.warm_spread.spread} pt, {r.warm_spread.verdict_flips} verdict "
                f"flip(s) at the {PASS_TOTAL}/20 threshold); the cold control arm scored "
                f"{list(r.cold_spread.scores)}"
            )
        lines.append(
            "  caught by: pass^k over identical repeats plus a score-spread verdict, with a "
            "cold control arm to localise it to cross-session state"
        )
    else:
        lines.append("  none observed")

    hallucinated = [
        sid
        for sid, rep in outcome.reports.items()
        if (r := rep.by_name("feedback-grounded")) is not None and not r.passed
    ]
    lines.append("")
    lines.append("HALLUCINATED FEEDBACK")
    for sid in hallucinated:
        detail = outcome.reports[sid]["feedback-grounded"].detail
        lines.append(f"  {sid}: {detail}")
    lines.append(
        "  caught by: FeedbackGroundednessContract - every quoted span and every "
        "presupposed topic in the feedback must be present in the session"
    )

    compliance = [
        sid
        for sid, rep in outcome.reports.items()
        if (r := rep.by_name("score-claims-backed")) is not None and not r.passed
    ]
    lines.append("")
    lines.append("COMPLIANCE MISS")
    for sid in compliance:
        detail = outcome.reports[sid]["score-claims-backed"].detail
        lines.append(f"  {sid}: {detail}")
    lines.append(
        "  caught by: ScoreClaimContract - a factual claim on the score card must agree "
        "with the session's own disclosure register and compliance flags"
    )
    lines.append("")
    lines.append(
        f"regression gate: {'PASS' if outcome.ok else 'FAIL'} "
        f"({len(outcome.surprises)} surprise(s))"
    )
    for surprise in outcome.surprises:
        lines.append(f"  ! {surprise}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "-k",
        type=int,
        default=None,
        help="override the repeat count on every consistency row (default: the row's own)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="also write the calibration report (text and markdown) into this directory",
    )
    args = parser.parse_args(argv)

    outcome = run_demo(k_override=args.k)

    if args.out:
        report, judge, _ = calibrate_scorer(outcome.corpus)
        written = report.write(Path(args.out), stem="roleplay_scorer_calibration")
        print()
        for kind, path in sorted(written.items()):
            print(f"wrote {kind}: {path}")

    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit(main())
