"""The Excel workbook — the report a QA lead opens to decide what to do next.

WHY EXCEL, WHEN JUNIT XML ALREADY EXISTS
----------------------------------------
They answer different questions. JUnit XML answers "is the build red", for a
machine. This answers "which rows failed, what did we expect, what actually
happened, and what is the evidence" — for a person who has to triage, and who
will sort, filter and paste into a ticket. Those are different artefacts and
trying to make one do both produces something that does neither.

WHAT IS ON EACH SHEET, AND WHY
------------------------------
    Summary    the run, the headline counts, and the verdict — with every rate
               carrying its denominator, which is this repository's house rule
    Scenarios  one row per scenario. The triage sheet: status, the human verdict
               beside the grader's, expected outcome, actual outcome, and a note
    Failures   one row per FAILING CHECK, not per scenario. A scenario can fail
               two contracts for one cause and three for three causes, and the
               difference is the whole of triage
    Metrics    every number the run measured, each with its denominator and its
               source command, so a figure in a slide can be traced back
    Notes      the run's own prose: what was found, what was declared, what was
               assumed

THE COLUMN THAT MATTERS MOST
----------------------------
`Declared?`. Three of the five rows in this corpus are *expected* to fail — they
exist to prove the grader is broken. A report that shows them as red is worse
than useless, because the reader learns to ignore red. So every failing row says
whether the corpus declared it, and the Summary counts the two kinds separately.
An undeclared failure is the only kind that should move anybody.

NO FORMULAS, DELIBERATELY
-------------------------
Every cell is a value. A spreadsheet whose numbers are computed by the
spreadsheet can disagree with the run that produced it, and then there are two
answers and no way to choose. The arithmetic happens in Python, is tested, and
lands here as text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["ScenarioRow", "FailureRow", "MetricRow", "ExcelReport", "write_excel"]

#: One font everywhere. Calibri is the Excel default and renders identically on
#: every machine that will open this; a report that looks different on the
#: reader's laptop invites a conversation about fonts instead of about failures.
_FONT = "Calibri"

_HEADER_FILL = "1F2D50"
_PASS_FILL = "E2EFDA"
_FAIL_FILL = "FCE4EC"
_DECLARED_FILL = "FFF2CC"
_ZEBRA_FILL = "F5F5F2"


@dataclass
class ScenarioRow:
    """One scenario, as a triage row."""

    scenario_id: str
    title: str
    status: str                      # PASS | FAIL | FAIL (declared)
    human_verdict: str               # what a competent reviewer said
    grader_verdict: str              # what the product's rubric said
    agreement: str                   # agrees | DIFFERS
    score: str                       # "19/20"
    checks_passed: str               # "2/4"
    expected_outcome: str            # from the scenario's own expectation
    actual_outcome: str              # what the run produced
    declared: str                    # yes | no | -
    failing_contracts: str           # comma separated, or ""
    notes: str = ""


@dataclass
class FailureRow:
    """One FAILING CHECK — not one failing scenario."""

    scenario_id: str
    contract: str
    declared: str                    # yes | no
    expected: str                    # what the check required
    actual: str                      # what the check found
    error: str                       # the one-line diagnosis
    evidence: str                    # the quote from the trace
    trace_ref: str = ""              # where to reopen the claim


@dataclass
class MetricRow:
    """One measured number, with its denominator and where it came from."""

    metric: str
    value: str
    denominator: str
    source: str
    note: str = ""


@dataclass
class ExcelReport:
    title: str = "conversation-eval-lab — run report"
    subject: str = ""
    verdict: str = ""
    scenarios: list[ScenarioRow] = field(default_factory=list)
    failures: list[FailureRow] = field(default_factory=list)
    metrics: list[MetricRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    run_label: str | None = None

    # ---------------------------------------------------------------- counts
    @property
    def total(self) -> int:
        return len(self.scenarios)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.scenarios if r.status == "PASS")

    @property
    def failed_declared(self) -> int:
        return sum(1 for r in self.scenarios if r.status.startswith("FAIL") and r.declared == "yes")

    @property
    def failed_undeclared(self) -> int:
        return sum(1 for r in self.scenarios if r.status.startswith("FAIL") and r.declared != "yes")

    def headline(self) -> str:
        """The verdict, stated the way the gate states it."""
        if self.failed_undeclared:
            return f"FAIL — {self.failed_undeclared} undeclared failure(s)"
        return "PASS — every failure was declared by the corpus"


def _style_header(ws: Any, ncols: int) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill

    for col in range(1, ncols + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(name=_FONT, bold=True, color="FFFFFF", size=11)
        cell.fill = PatternFill("solid", start_color=_HEADER_FILL)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"


def _autosize(ws: Any, widths: Sequence[int]) -> None:
    from openpyxl.utils import get_column_letter

    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width


def _write_rows(ws: Any, rows: Sequence[Sequence[str]], *, wrap_from: int = 0) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill

    for r, row in enumerate(rows, start=2):
        for c, value in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.font = Font(name=_FONT, size=10)
            cell.alignment = Alignment(vertical="top", wrap_text=c > wrap_from)
        if r % 2 == 1:
            for c in range(1, len(row) + 1):
                ws.cell(row=r, column=c).fill = PatternFill("solid", start_color=_ZEBRA_FILL)


def _colour_status(ws: Any, column: int, nrows: int) -> None:
    """Green for a pass, amber for a declared failure, pink for a real one."""
    from openpyxl.styles import Font, PatternFill

    for r in range(2, nrows + 2):
        cell = ws.cell(row=r, column=column)
        text = str(cell.value or "")
        if text == "PASS":
            fill, colour = _PASS_FILL, "1B7A3D"
        elif "declared" in text:
            fill, colour = _DECLARED_FILL, "854F0B"
        elif text.startswith("FAIL"):
            fill, colour = _FAIL_FILL, "A32D2D"
        else:
            continue
        cell.fill = PatternFill("solid", start_color=fill)
        cell.font = Font(name=_FONT, size=10, bold=True, color=colour)


def write_excel(report: ExcelReport, path: str | Path) -> Path:
    """Write the workbook. Requires `openpyxl` — `pip install -e ".[report]"`."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    # -------------------------------------------------------------- Summary
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = report.title
    ws["A1"].font = Font(name=_FONT, size=16, bold=True, color=_HEADER_FILL)
    ws["A2"] = report.subject
    ws["A2"].font = Font(name=_FONT, size=11, color="55534D")
    rows = [
        ("Generated", datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")),
        ("Run label", report.run_label or "-"),
        ("Verdict", report.headline()),
        ("", ""),
        ("Scenarios run", f"{report.total}"),
        ("Passed", f"{report.passed}/{report.total}"),
        ("Failed — declared by the corpus", f"{report.failed_declared}/{report.total}"),
        ("Failed — UNDECLARED", f"{report.failed_undeclared}/{report.total}"),
        ("Failing checks", f"{len(report.failures)}"),
        ("", ""),
        ("How to read this",
         "A declared failure is the row working: it exists to prove the grader is "
         "broken. Only an UNDECLARED failure should move anybody."),
    ]
    for i, (label, value) in enumerate(rows, start=4):
        ws.cell(row=i, column=1, value=label).font = Font(name=_FONT, size=10, bold=True)
        cell = ws.cell(row=i, column=2, value=value)
        cell.font = Font(name=_FONT, size=10)
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    _autosize(ws, [34, 92])

    # ------------------------------------------------------------ Scenarios
    ws = wb.create_sheet("Scenarios")
    header = ["Scenario", "Title", "Status", "Declared?", "Human verdict", "Grader verdict",
              "Agreement", "Score", "Checks passed", "Expected outcome", "Actual outcome",
              "Failing contracts", "Notes"]
    ws.append(header)
    _write_rows(ws, [[r.scenario_id, r.title, r.status, r.declared, r.human_verdict,
                      r.grader_verdict, r.agreement, r.score, r.checks_passed,
                      r.expected_outcome, r.actual_outcome, r.failing_contracts, r.notes]
                     for r in report.scenarios], wrap_from=9)
    _style_header(ws, len(header))
    _colour_status(ws, 3, len(report.scenarios))
    _autosize(ws, [46, 40, 16, 11, 14, 14, 11, 9, 13, 60, 60, 34, 60])
    ws.auto_filter.ref = f"A1:{chr(64 + len(header))}{len(report.scenarios) + 1}"

    # ------------------------------------------------------------- Failures
    ws = wb.create_sheet("Failures")
    header = ["Scenario", "Contract", "Declared?", "Expected", "Actual",
              "Error / diagnosis", "Evidence from the trace", "Trace"]
    ws.append(header)
    _write_rows(ws, [[f.scenario_id, f.contract, f.declared, f.expected, f.actual,
                      f.error, f.evidence, f.trace_ref] for f in report.failures], wrap_from=3)
    _style_header(ws, len(header))
    _autosize(ws, [46, 24, 11, 54, 54, 60, 78, 30])
    if report.failures:
        ws.auto_filter.ref = f"A1:{chr(64 + len(header))}{len(report.failures) + 1}"

    # -------------------------------------------------------------- Metrics
    ws = wb.create_sheet("Metrics")
    header = ["Metric", "Value", "Denominator", "Reproduce with", "Note"]
    ws.append(header)
    _write_rows(ws, [[m.metric, m.value, m.denominator, m.source, m.note]
                     for m in report.metrics], wrap_from=3)
    _style_header(ws, len(header))
    _autosize(ws, [46, 20, 26, 46, 76])

    # ---------------------------------------------------------------- Notes
    ws = wb.create_sheet("Notes")
    ws.append(["#", "Note"])
    _write_rows(ws, [[str(i), n] for i, n in enumerate(report.notes, start=1)], wrap_from=1)
    _style_header(ws, 2)
    _autosize(ws, [6, 140])

    wb.save(target)
    return target
