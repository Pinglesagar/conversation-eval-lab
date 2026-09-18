"""Turn one demo run into the two reports a team actually consumes.

WHY THIS LIVES IN `roleplay/` AND NOT IN `lab/`
-----------------------------------------------
`lab.report.excel` and `lab.report.junit` know how to write a workbook and an
XML file. They do not know what a scenario is, what a declared failure means, or
which of this domain's contracts is worth a column. That knowledge is domain
knowledge, so the translation lives here and the writers stay reusable.

THE ONE JUDGEMENT THIS MODULE MAKES
-----------------------------------
Whether a failing row is a *finding* or the row *working*.

Three of the five rows in this corpus declare an `expected_failure`: they exist
to demonstrate that the grader is broken, and they are supposed to come back red.
A report that shows them as failures alongside a genuine regression teaches the
reader to ignore red, which is the one outcome a report must never produce.

So every row carries `Declared?`, the Summary counts the two kinds separately,
and the exit-worthy number — the one the gate reads — is the undeclared count.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from lab.report.excel import ExcelReport, FailureRow, MetricRow, ScenarioRow, write_excel
from lab.report.junit import JUnitCase, JUnitSuite, write_junit

if TYPE_CHECKING:  # pragma: no cover - typing only
    from roleplay.demo import DemoOutcome

__all__ = ["build_excel_report", "build_junit_suite", "write_reports"]

#: Cap on a single cell. Excel's own limit is 32,767 characters; long before
#: that a cell stops being readable, and a reader who needs more should open the
#: trace. The report names the trace for exactly that reason.
_CELL_CAP = 900


def _clip(text: str, cap: int = _CELL_CAP) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= cap else text[: cap - 1] + "…"


def _expected_outcome(scenario: Any) -> str:
    """What a competent reviewer said should happen, and why."""
    verdict = scenario.expectation.human_verdict
    return _clip(f"{verdict.upper()} — {scenario.expectation.reason}")


def _actual_outcome(result: Any, report: Any) -> str:
    card = result.card
    return _clip(
        f"{card.verdict.upper()} {card.total}/{card.max_total} "
        f"({card.percent}%); {report.passed}/{report.applicable} applicable checks passed, "
        f"{report.failed} failed, {report.vacuous} vacuous"
    )


def build_excel_report(outcome: DemoOutcome, *, run_label: str | None = None) -> ExcelReport:
    """Translate a run into the workbook model."""
    scenarios: list[ScenarioRow] = []
    failures: list[FailureRow] = []

    for scenario in outcome.corpus:
        result = outcome.results.get(scenario.id)
        report = outcome.reports.get(scenario.id)
        if result is None or report is None:
            continue

        failing = [r for r in report.results if not r.passed]
        declared_names = {
            name for name in (scenario.expected_failure.contracts if scenario.expected_failure else ())
        }
        undeclared = [r for r in failing if r.name not in declared_names]

        if not failing:
            status, declared = "PASS", "-"
        elif undeclared:
            status, declared = "FAIL", "no"
        else:
            status, declared = "FAIL (declared)", "yes"

        human = scenario.expectation.human_verdict
        grader = result.card.verdict
        scenarios.append(
            ScenarioRow(
                scenario_id=scenario.id,
                title=scenario.title,
                status=status,
                human_verdict=human,
                grader_verdict=grader,
                agreement="agrees" if human == grader else "DIFFERS",
                score=f"{result.card.total}/{result.card.max_total}",
                checks_passed=f"{report.passed}/{report.applicable}",
                expected_outcome=_expected_outcome(scenario),
                actual_outcome=_actual_outcome(result, report),
                declared=declared,
                failing_contracts=", ".join(r.name for r in failing),
                notes=_clip(scenario.notes),
            )
        )

        for check in failing:
            is_declared = check.name in declared_names
            expectation = (
                scenario.expected_failure.expectation
                if is_declared and scenario.expected_failure
                else "every declared clause holds"
            )
            failures.append(
                FailureRow(
                    scenario_id=scenario.id,
                    contract=check.name,
                    declared="yes" if is_declared else "no",
                    expected=_clip(expectation),
                    actual=_clip(check.detail),
                    error=_clip(check.error or check.detail),
                    evidence=_clip(
                        " | ".join(e.render() for e in check.evidence[:3]) or "(no quote recorded)"
                    ),
                    trace_ref=f"{scenario.id} (in memory; re-run `make roleplay-demo`)",
                )
            )

    metrics = _metrics(outcome)
    notes = _notes(outcome)

    return ExcelReport(
        subject=f"{len(scenarios)} scenarios · the roleplay pack",
        scenarios=scenarios,
        failures=failures,
        metrics=metrics,
        notes=notes,
        run_label=run_label,
    )


def _metrics(outcome: DemoOutcome) -> list[MetricRow]:
    """Every number the run measured, each with its denominator."""
    rows: list[MetricRow] = []
    total = len(outcome.results)
    clean = sum(1 for r in outcome.reports.values() if r.failed == 0)
    agree = sum(
        1
        for s in outcome.corpus
        if s.id in outcome.results
        and outcome.results[s.id].card.verdict == s.expectation.human_verdict
    )
    applicable = sum(r.applicable for r in outcome.reports.values())
    passed = sum(r.passed for r in outcome.reports.values())
    vacuous = sum(r.vacuous for r in outcome.reports.values())

    rows.append(MetricRow("Scenarios with no failing check", str(clean), f"of {total}",
                          "make roleplay-demo",
                          "Not a quality score: three rows are SUPPOSED to fail."))
    rows.append(MetricRow("Grader agrees with the human column", str(agree), f"of {total}",
                          "make roleplay-demo",
                          "The disagreements are the findings, not the errors."))
    rows.append(MetricRow("Applicable checks passed", str(passed), f"of {applicable}",
                          "make roleplay-demo", ""))
    rows.append(MetricRow("Vacuous checks", str(vacuous), f"of {applicable + vacuous}",
                          "make roleplay-demo",
                          "A contract with nothing to assert. Counted separately so a "
                          "suite cannot go green by going silent."))

    for sid, report in outcome.consistency.items():
        warm = report.warm_spread
        cold = report.cold_spread
        rows.append(MetricRow(
            f"Score spread on identical input — {sid}",
            f"{warm.spread} pt",
            f"over {len(warm.scores)} repeats",
            "make roleplay-demo",
            f"warm service scored {list(warm.scores)}; the cold control arm scored "
            f"{list(cold.scores)}. The control arm is what localises it to cross-session state.",
        ))
    return rows


def _notes(outcome: DemoOutcome) -> list[str]:
    """The prose a reader needs to interpret the sheets, including every failure."""
    notes: list[str] = [
        "HOW TO READ THIS REPORT. The system under test is a sales-coaching product "
        "that grades trainee advisers out of 20. It carries three deliberately seeded "
        "defects. The suite exists to catch them, so a failing row is usually the "
        "suite working.",
        "DECLARED vs UNDECLARED. A row with an `expected_failure` block has stated in "
        "advance which contract it expects to fail and why. That is the row working. "
        "An UNDECLARED failure is the only kind that should move anybody, and it is "
        "the number the CI gate reads.",
    ]

    for scenario in outcome.corpus:
        report = outcome.reports.get(scenario.id)
        if report is None or report.failed == 0:
            continue
        declared = {n for n in (scenario.expected_failure.contracts if scenario.expected_failure else ())}
        for check in report.results:
            if check.passed:
                continue
            tag = "DECLARED" if check.name in declared else "UNDECLARED"
            notes.append(f"[{tag}] {scenario.id} — {check.name}: {_clip(check.detail, 400)}")

    if outcome.surprises:
        notes.append("SURPRISES — things the corpus did not predict:")
        notes.extend(f"  {s}" for s in outcome.surprises)
    else:
        notes.append("No surprises: every failure was declared, and every declared "
                     "failure reproduced.")

    if not outcome.gate_cleared:
        notes.append("CALIBRATION GATE: REFUSED. " + "; ".join(outcome.gate_reasons))
    return notes


def build_junit_suite(outcome: DemoOutcome) -> JUnitSuite:
    """Translate a run into JUnit XML, the CI interchange format."""
    suite = JUnitSuite(
        name="roleplay",
        properties={
            "scenarios": str(len(outcome.results)),
            "corpus": "scenarios/roleplay",
            "gate_cleared": str(outcome.gate_cleared),
        },
    )
    for scenario in outcome.corpus:
        report = outcome.reports.get(scenario.id)
        result = outcome.results.get(scenario.id)
        if report is None or result is None:
            continue
        declared = {n for n in (scenario.expected_failure.contracts if scenario.expected_failure else ())}
        undeclared = [r for r in report.results if not r.passed and r.name not in declared]
        expected_reds = [r for r in report.results if not r.passed and r.name in declared]

        case = JUnitCase(name=scenario.id, classname="roleplay")
        if undeclared:
            first = undeclared[0]
            case.failure_message = f"{first.name}: {_clip(first.detail, 200)}"
            case.failure_detail = "\n".join(
                f"[{r.name}] {r.detail}\n" + "\n".join("    " + e.render() for e in r.evidence[:3])
                for r in undeclared
            )
        if expected_reds:
            case.stdout = "declared failures (the row working): " + ", ".join(
                r.name for r in expected_reds
            )
        suite.cases.append(case)
    return suite


def write_reports(
    outcome: DemoOutcome,
    out_dir: str | Path = "reports",
    *,
    run_label: str | None = None,
) -> dict[str, Path]:
    """Write both reports. Returns {kind: path}."""
    out = Path(out_dir)
    written: dict[str, Path] = {
        "junit": write_junit(build_junit_suite(outcome), out / "junit.xml"),
    }
    try:
        written["excel"] = write_excel(
            build_excel_report(outcome, run_label=run_label), out / "results.xlsx"
        )
    except ImportError:  # pragma: no cover - only without the extra installed
        pass
    return written
