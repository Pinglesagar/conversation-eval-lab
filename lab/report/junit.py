"""JUnit XML — the interchange format every CI system already reads.

WHY THIS FORMAT AND NOT A NICER ONE
-----------------------------------
Nothing consumes a bespoke report. Jenkins, GitLab CI, GitHub Actions, Azure
DevOps and TestRail all read JUnit XML, and every one of them draws the same
conclusions from it: how many ran, how many failed, which ones, and how long.
Emitting it costs a hundred lines and buys the whole ecosystem, which is why it
is the first reporter here rather than the third.

The schema is old, loose and unversioned. That is a real cost and it is worth
knowing about rather than discovering: there is no official XSD, tools disagree
about optional attributes, and the only fields you can rely on everywhere are
the ones written below. Anything richer belongs in the Excel workbook or the
markdown report, not here.

WHAT MAPS ONTO WHAT
-------------------
A *scenario* is a testcase. The suite is the corpus. A scenario whose contracts
all passed is a pass; one with an undeclared failure is a `<failure>`; one whose
failure the corpus DECLARED is neither — it is a pass with a note, because the
row did exactly what it said it would.

That last mapping is the only opinionated one here, and it is the honest one: a
corpus where 3 of 5 rows declare an expected failure would otherwise report 60%
failure on every green build, and a red CI badge that is always red is a badge
nobody reads.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.dom import minidom

__all__ = ["JUnitCase", "JUnitSuite", "write_junit"]


@dataclass
class JUnitCase:
    """One scenario, as CI understands it."""

    name: str
    classname: str = "roleplay"
    time_s: float = 0.0
    #: Set when the scenario failed in a way the corpus did not declare.
    failure_message: str | None = None
    #: The long form: expected, actual, and the evidence.
    failure_detail: str | None = None
    #: Set when the scenario could not run at all — a harness problem, not a finding.
    error_message: str | None = None
    skipped_reason: str | None = None
    #: Free text carried through as `system-out`, so a declared failure still
    #: says so in a CI log without turning the build red.
    stdout: str = ""

    @property
    def status(self) -> str:
        if self.error_message:
            return "ERROR"
        if self.failure_message:
            return "FAIL"
        if self.skipped_reason:
            return "SKIP"
        return "PASS"


@dataclass
class JUnitSuite:
    name: str = "conversation-eval-lab"
    cases: list[JUnitCase] = field(default_factory=list)
    #: Run-level notes. Written as a `properties` block, which every reader
    #: tolerates and some display.
    properties: dict[str, str] = field(default_factory=dict)

    @property
    def failures(self) -> int:
        return sum(1 for c in self.cases if c.failure_message)

    @property
    def errors(self) -> int:
        return sum(1 for c in self.cases if c.error_message)

    @property
    def skipped(self) -> int:
        return sum(1 for c in self.cases if c.skipped_reason)

    @property
    def time_s(self) -> float:
        return round(sum(c.time_s for c in self.cases), 3)

    def to_element(self) -> ET.Element:
        suite = ET.Element(
            "testsuite",
            {
                "name": self.name,
                "tests": str(len(self.cases)),
                "failures": str(self.failures),
                "errors": str(self.errors),
                "skipped": str(self.skipped),
                "time": f"{self.time_s:.3f}",
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
        if self.properties:
            props = ET.SubElement(suite, "properties")
            for key, value in sorted(self.properties.items()):
                ET.SubElement(props, "property", {"name": key, "value": str(value)})
        for case in self.cases:
            node = ET.SubElement(
                suite,
                "testcase",
                {"name": case.name, "classname": case.classname, "time": f"{case.time_s:.3f}"},
            )
            if case.error_message:
                err = ET.SubElement(node, "error", {"message": case.error_message, "type": "harness"})
                err.text = case.failure_detail or case.error_message
            elif case.failure_message:
                fail = ET.SubElement(
                    node, "failure", {"message": case.failure_message, "type": "contract"}
                )
                fail.text = case.failure_detail or case.failure_message
            elif case.skipped_reason:
                ET.SubElement(node, "skipped", {"message": case.skipped_reason})
            if case.stdout:
                ET.SubElement(node, "system-out").text = case.stdout
        return suite

    def to_xml(self) -> str:
        """Pretty-printed, because a diff of a report should be readable."""
        suites = ET.Element(
            "testsuites",
            {
                "name": self.name,
                "tests": str(len(self.cases)),
                "failures": str(self.failures),
                "errors": str(self.errors),
                "time": f"{self.time_s:.3f}",
            },
        )
        suites.append(self.to_element())
        raw = ET.tostring(suites, encoding="unicode")
        return minidom.parseString(raw).toprettyxml(indent="  ")


def write_junit(suite: JUnitSuite, path: str | Path) -> Path:
    """Write the suite to `path` and return it."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(suite.to_xml(), encoding="utf-8")
    return target
