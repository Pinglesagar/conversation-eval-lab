"""The reports, checked the way the rest of this repository checks things.

The interesting assertions here are not "a file was written". They are:

  * a DECLARED failure must not turn CI red, because three of the five rows in
    this corpus exist to fail and a badge that is always red is a badge nobody
    reads;
  * an UNDECLARED failure must turn CI red, or the gate is decorative;
  * every failure row must carry the quote that proves it, because a failure
    list without evidence cannot be triaged.

Both directions are tested, which is the house rule: a check that has only ever
been seen to pass has not been seen to work.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lab.report.excel import write_excel
from lab.report.junit import JUnitCase, JUnitSuite
from roleplay.demo import run_demo
from roleplay.reporting import build_excel_report, build_junit_suite, write_reports

openpyxl = pytest.importorskip("openpyxl")


@pytest.fixture(scope="module")
def outcome(capsys_disabled: None = None):  # noqa: ANN001 - fixture plumbing
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()):
        return run_demo()


# --------------------------------------------------------------------------- #
# JUnit XML
# --------------------------------------------------------------------------- #
def test_junit_is_well_formed_and_counts_its_own_cases() -> None:
    suite = JUnitSuite(cases=[JUnitCase(name="a"), JUnitCase(name="b", failure_message="boom")])
    root = ET.fromstring(suite.to_xml())
    assert root.tag == "testsuites"
    assert root.get("tests") == "2"
    assert root.get("failures") == "1"


def test_a_declared_failure_does_not_turn_ci_red(outcome) -> None:
    """The load-bearing one.

    Three of the five rows declare an `expected_failure`. They come back red on a
    healthy tree, and if the JUnit file reported them as failures the build would
    be permanently broken and the signal worthless.
    """
    declared_rows = [s for s in outcome.corpus if s.expected_failure]
    assert declared_rows, "the corpus no longer declares any expected failure"

    suite = build_junit_suite(outcome)
    assert suite.failures == 0, "a declared failure was reported to CI as a failure"

    by_name = {c.name: c for c in suite.cases}
    for scenario in declared_rows:
        case = by_name[scenario.id]
        assert case.failure_message is None
        assert "declared failures" in case.stdout, (
            f"{scenario.id} failed as declared but the XML does not say so"
        )


def test_an_undeclared_failure_does_turn_ci_red(outcome) -> None:
    """The mirror. Strip a row's declaration and the same run must go red."""
    scenario = next(s for s in outcome.corpus if s.expected_failure)
    original = scenario.expected_failure
    object.__setattr__(scenario, "expected_failure", None) if hasattr(
        scenario, "__dataclass_fields__"
    ) else scenario.__dict__.update(expected_failure=None)
    try:
        suite = build_junit_suite(outcome)
        assert suite.failures >= 1, "an undeclared failure did not reach CI"
        case = next(c for c in suite.cases if c.name == scenario.id)
        assert case.failure_message
        assert case.failure_detail, "a CI failure with no detail cannot be triaged"
    finally:
        scenario.__dict__.update(expected_failure=original)


# --------------------------------------------------------------------------- #
# the workbook
# --------------------------------------------------------------------------- #
def test_every_scenario_appears_exactly_once(outcome) -> None:
    report = build_excel_report(outcome)
    ids = [r.scenario_id for r in report.scenarios]
    assert sorted(ids) == sorted(s.id for s in outcome.corpus)
    assert len(ids) == len(set(ids))


def test_every_failure_row_carries_its_evidence(outcome) -> None:
    """A failure list without quotes cannot be triaged and cannot be checked."""
    report = build_excel_report(outcome)
    assert report.failures, "the run produced no failing check at all"
    for row in report.failures:
        assert row.evidence.strip(), f"{row.scenario_id}/{row.contract} has no evidence"
        assert row.expected.strip(), f"{row.scenario_id}/{row.contract} has no expected outcome"
        assert row.actual.strip(), f"{row.scenario_id}/{row.contract} has no actual outcome"


def test_declared_and_undeclared_are_counted_separately(outcome) -> None:
    report = build_excel_report(outcome)
    assert report.failed_declared >= 1
    assert report.failed_undeclared == 0
    assert report.headline().startswith("PASS")
    assert report.passed + report.failed_declared + report.failed_undeclared == report.total


def test_every_metric_states_its_denominator(outcome) -> None:
    """The house rule, enforced on the sheet a stakeholder reads."""
    report = build_excel_report(outcome)
    assert report.metrics
    for metric in report.metrics:
        assert metric.denominator.strip(), f"{metric.metric} has no denominator"
        assert metric.source.strip(), f"{metric.metric} names no command to reproduce it"


def test_the_notes_name_every_failing_check(outcome) -> None:
    report = build_excel_report(outcome)
    body = "\n".join(report.notes)
    for row in report.failures:
        assert row.contract in body, f"{row.contract} is missing from the notes"
        assert row.scenario_id in body, f"{row.scenario_id} is missing from the notes"


def test_the_workbook_has_the_five_sheets_and_opens(tmp_path: Path, outcome) -> None:
    path = write_excel(build_excel_report(outcome), tmp_path / "r.xlsx")
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Summary", "Scenarios", "Failures", "Metrics", "Notes"]
    assert wb["Scenarios"].max_row == len(outcome.corpus) + 1
    assert wb["Scenarios"]["A1"].value == "Scenario"


def test_no_cell_exceeds_what_excel_will_hold(tmp_path: Path, outcome) -> None:
    """Excel's own cap is 32,767 characters; a cell near it is unreadable anyway."""
    path = write_excel(build_excel_report(outcome), tmp_path / "r.xlsx")
    wb = openpyxl.load_workbook(path)
    for sheet in wb.worksheets:
        for row in sheet.iter_rows(values_only=True):
            for value in row:
                if isinstance(value, str):
                    assert len(value) <= 2000, f"{sheet.title}: a cell is {len(value)} chars"


def test_write_reports_emits_both_and_names_them(tmp_path: Path, outcome) -> None:
    paths = write_reports(outcome, tmp_path, run_label="test")
    assert set(paths) == {"junit", "excel"}
    assert paths["junit"].exists() and paths["excel"].exists()
    ET.fromstring(paths["junit"].read_text())


def test_junit_needs_no_third_party_dependency() -> None:
    """The format every CI system reads must never be the one that cannot be produced."""
    source = Path("lab/report/junit.py").read_text()
    assert "openpyxl" not in source
    assert "import xml.etree.ElementTree" in source
