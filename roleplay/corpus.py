"""The roleplay corpus: YAML data, validated into contracts before it is trusted.

A DOMAIN SIBLING, NOT A FORK
----------------------------
`scenarios/loader.py` validates the booking corpus. This validates the roleplay
corpus. They share no code and they share every rule, which is the honest way to
retarget a corpus: the *pattern* is reusable, the *vocabularies* are domain data,
and pretending otherwise produces a loader with a tool-name list from two products
in it. Nothing here imports the booking loader and nothing there knows this
exists; `lab/` is what both of them depend on.

The four rules, restated because they are the whole value of having a schema:

1.  **Closed vocabularies.** Tools, tags, suites, jurisdictions and contract
    names are closed sets. A typo is an error with the legal values listed.

2.  **Every assertion must be *able* to fire.** This corpus gets a rule the
    booking corpus cannot have, and it is the strongest one here: a scenario's
    trainee turns are *data in the same file* as its assertions, so a required
    trainee phrase can be checked against the script at load time. A row that
    claims the trainee says "no real risk" and whose script never says it is
    rejected — before it can run, pass, and be counted as compliance coverage.
    That class of row is how a suite ends up green and empty.

3.  **`expected_failure` is an expectation about the system, not a note.** It
    names the contracts this build is expected to fail and says, in prose, what
    is expected to be observed. Names are validated against the contracts the
    scenario actually declares. It is not a skip: the contract still runs, and
    the day it starts passing, the corpus notices.

4.  **Collect, then report.** `validate_corpus` never raises on bad data. It
    returns every issue in every file, because the person fixing a corpus wants
    the whole list.

WHAT A ROW DECLARES THAT A BOOKING ROW CANNOT
---------------------------------------------
`expectation.human_verdict` — what a competent reviewer would say about this
session, with a reason. That field is the corpus's own ground truth, and it is
what makes `roleplay.calibration` possible: the scorer is the instrument, the
human column is the reference, and the disagreement between them is the finding.
A rubric product without a human column somewhere is a product whose accuracy is
unmeasured by construction.

WHAT THE 70 ROWS ARE, AND WHAT THEY ARE NOT
-------------------------------------------
The corpus is a **targeted probe set**, not a sample of anything. Rows were
written to make specific failures reachable — a disclosure register keyed by
market, an advice blocklist with two entries, a feedback template with a
hard-coded exemplar — so the composition is deliberately adversarial in the
places the product is weakest. Two consequences have to be stated wherever these
numbers are quoted, because a reader who assumes otherwise will over-read them:

*   The rates in `roleplay.calibration` (TPR, TNR, kappa) are measurements of the
    scorer **on this set**. They are not field rates and they are not prevalence
    estimates. Doubling the number of jurisdiction rows would move the TPR
    without anything about the product changing.
*   The pass/fail balance of the human column is a design choice, kept close to
    even so that neither rate is computed over a handful of items.

THE LABELLING RULE, STATED ONCE
-------------------------------
Two rules decide `human_verdict` and both are applied to every row, because a
label that is argued case by case is not a reference:

1.  **A required disclosure is discharged only in registered wording.** A
    paraphrase that a sympathetic reviewer would accept is labelled a fail, and
    the row's `notes` say so. The register is deliberately strict (see
    `roleplay.register`) and the human column is strict with it, so that any
    disagreement between them is about the *scorer* and not about how generous
    two instruments happen to be.
2.  **Otherwise the label is the rubric's own arithmetic**, threshold included.
    Several rows are therefore labelled `pass` on sessions nobody would want to
    certify — a customer ignored four times, a spouse objection steamrollered,
    a forecast offered in place of an answer. Those rows carry the `known-gap`
    tag and their reasons say plainly that the label is the rubric's verdict and
    not an endorsement. They are the pack's evidence for what five criteria
    cannot express, and softening them into fails would hide the argument by
    making the scorer look worse than it is.

ROW FAMILIES
------------
    locale       three disclosure registers, one script run across markets,
                 near-miss paraphrases, and the two multilingual directions
    compliance   the advice boundary, from an explicit request for a
                 recommendation to a cautious sentence that crosses anyway
    objection    objections that must be engaged rather than acknowledged,
                 including one raised four times
    pitch        discovery failure modes, the closing family, and the
                 scorer-stress rows that exist to catch a bad grader
    consistency  the same transcript graded k times, warm service against cold
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lab.checks import (
    ArgPredicate,
    Contract,
    ContractSet,
    NoProgressContract,
    Ordering,
    PhraseContract,
    ToolContract,
)

from roleplay.contracts import (
    DEFAULT_SCORE_CLAIMS,
    DEFAULT_TOPIC_CLAIMS,
    FeedbackGroundednessContract,
    ScoreClaimContract,
)
from roleplay.persona import CustomerProfile, load_profiles
from roleplay.register import JURISDICTIONS
from roleplay.runtime import TOOL_NAMES

__all__ = [
    "SUITES",
    "SUITE_MINIMUMS",
    "TAG_VOCABULARY",
    "CONTRACT_NAMES",
    "ARG_OPS",
    "CORPUS_ROOT",
    "PROFILE_DIR",
    "Suite",
    "ArgSpec",
    "ToolSpec",
    "PhraseSpec",
    "Expectation",
    "ConsistencySpec",
    "ExpectedFailure",
    "Scenario",
    "Corpus",
    "ValidationIssue",
    "CorpusValidation",
    "load_scenario",
    "load_corpus",
    "validate_corpus",
    "iter_scenario_paths",
    "main",
]

#: Suite = subdirectory = id prefix, exactly as in the booking corpus. One idea
#: in three places on purpose: a scenario id from a result row locates its file
#: with no lookup, and a file's suite is unambiguous.
Suite = Literal["pitch", "compliance", "objection", "consistency", "locale"]
SUITES: tuple[str, ...] = ("pitch", "compliance", "objection", "consistency", "locale")

#: Smallest shippable corpus per suite. Asserted by the tests rather than here, so
#: a partial corpus is loadable while it is being written and simply not shippable.
SUITE_MINIMUMS: dict[str, int] = {
    "pitch": 18,
    "compliance": 12,
    "objection": 12,
    "consistency": 2,
    "locale": 18,
}

#: The tag vocabulary, each with the line that says what it means: documentation
#: and validation in one object. The tests assert every tag here is exercised by
#: at least one scenario, so an unused tag is a test failure rather than an
#: aspiration.
TAG_VOCABULARY: dict[str, str] = {
    # --- what the session is about
    "discovery": "the trainee's questioning is the thing under test",
    "objection-handling": "the customer's objection bank is worked through",
    "closing": "the trainee asks, or fails to ask, for the business",
    # --- compliance surface
    "disclosure": "a mandatory disclosure is required and either given or not",
    "compliance-gate": "the session must fail on compliance grounds alone",
    "unlicensed-advice": "the trainee makes a personal recommendation",
    "jurisdiction": "the required disclosure set is not the default market's",
    # --- customer behaviour
    "aggressive-customer": "objections are raised unprompted and repeated",
    "monosyllabic-customer": "the customer volunteers nothing and answers in a word",
    "multilingual": "the session is not conducted in English",
    # --- what is being measured about the scorer
    "score-consistency": "the row exists to be run k times, not once",
    "feedback-groundedness": "the row exists to test what the feedback claims",
    "cohort-curve": "the row exercises the scorer's cross-session state",
    "control": "the row must stay green; it is the counterpart to a failing row",
    "borderline": "sits deliberately next to the pass threshold",
    "scorer-stress": "the row is built to catch a bad scorer, not a bad trainee",
    # --- how the disclosure register is being probed
    "market-parity": "one script run in more than one market, so a red names the market",
    "near-miss": "the wording is close to a registered phrasing and does not satisfy it",
    "recovery": "the trainee approaches a boundary and pulls back before crossing it",
    "known-gap": "the row documents a limitation of this pack's own checks",
}

#: Contract names a scenario may compile to, and therefore the only names
#: `expected_failure.contracts` may mention.
CONTRACT_NAMES: frozenset[str] = frozenset(
    {
        "tools",
        "trainee-phrases",
        "score-claims-backed",
        "feedback-grounded",
        "no-progress",
    }
)

#: Operators accepted by `lab.checks.ArgPredicate`, restated so a YAML typo is a
#: load error rather than a ValueError in the middle of a run.
ARG_OPS: frozenset[str] = frozenset(
    {
        "eq",
        "ne",
        "contains",
        "tokens",
        "matches",
        "in",
        "gt",
        "gte",
        "lt",
        "lte",
        "present",
        "absent",
        "truthy",
    }
)
VALUE_FREE_OPS: frozenset[str] = frozenset({"present", "absent", "truthy"})
MATCH_MODES: frozenset[str] = frozenset({"icontains", "tokens", "eq"})

CORPUS_ROOT: Path = Path(__file__).resolve().parent.parent / "scenarios" / "roleplay"
PROFILE_DIR: Path = CORPUS_ROOT / "customers"

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _check_tool(name: str, *, where: str) -> str:
    parts = [p.strip() for p in name.split("|") if p.strip()]
    if not parts:
        raise ValueError(f"{where}: empty tool name")
    unknown = [p for p in parts if p not in TOOL_NAMES]
    if unknown:
        raise ValueError(
            f"{where}: unknown tool(s) {unknown}; the product exposes {sorted(TOOL_NAMES)}"
        )
    return name


class _Block(BaseModel):
    """Base for every YAML block: unknown keys are errors, not decoration."""

    model_config = ConfigDict(extra="forbid")


class ArgSpec(_Block):
    """A condition on one argument of one tool call; see `lab.checks.ArgPredicate`."""

    tool: str
    arg: str = Field(min_length=1)
    op: str = "eq"
    value: Any = None
    match: str = "icontains"
    quantifier: str = "any"
    label: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "ArgSpec":
        _check_tool(self.tool, where="tools.args.tool")
        if "|" in self.tool:
            raise ValueError(
                f"tools.args.tool must name one tool, got the OR-group {self.tool!r}; "
                "write one predicate per tool so the report names which one failed"
            )
        if self.op not in ARG_OPS:
            raise ValueError(f"tools.args.op {self.op!r} unknown; legal: {sorted(ARG_OPS)}")
        if self.match not in MATCH_MODES:
            raise ValueError(f"tools.args.match {self.match!r} unknown; legal: {sorted(MATCH_MODES)}")
        if self.quantifier not in ("any", "all"):
            raise ValueError(f"tools.args.quantifier must be any/all, got {self.quantifier!r}")
        if self.op in VALUE_FREE_OPS:
            if self.value is not None:
                raise ValueError(f"tools.args.op {self.op!r} takes no value, but one was given")
        elif self.value is None:
            raise ValueError(f"tools.args.op {self.op!r} needs a value")
        return self

    def build(self) -> ArgPredicate:
        return ArgPredicate(
            tool=self.tool,
            arg=self.arg,
            op=self.op,
            value=self.value,
            match=self.match,
            quantifier=self.quantifier,
            label=self.label,
        )


class OrderingSpec(_Block):
    """`first` must be called before `then`; see `lab.checks.Ordering`."""

    first: str
    then: str
    strict: bool = False

    @model_validator(mode="after")
    def _validate(self) -> "OrderingSpec":
        _check_tool(self.first, where="tools.ordering.first")
        _check_tool(self.then, where="tools.ordering.then")
        return self

    def build(self) -> Ordering:
        return Ordering(first=self.first, then=self.then, strict=self.strict)


class ToolSpec(_Block):
    """What the product must and must not do during this session."""

    expected: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    min_calls: dict[str, int] = Field(default_factory=dict)
    max_calls: dict[str, int] = Field(default_factory=dict)
    ordering: tuple[OrderingSpec, ...] = ()
    args: tuple[ArgSpec, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> "ToolSpec":
        for name in self.expected:
            _check_tool(name, where="tools.expected")
        for name in self.forbidden:
            _check_tool(name, where="tools.forbidden")
        for label, mapping in (("min_calls", self.min_calls), ("max_calls", self.max_calls)):
            for name in mapping:
                if "|" in name:
                    raise ValueError(
                        f"tools.{label}: {name!r} is an OR-group; counting a disjunction "
                        "is ambiguous, so name one tool"
                    )
                _check_tool(name, where=f"tools.{label}")
        overlap = set(self.expected) & set(self.forbidden)
        if overlap:
            raise ValueError(
                f"tools: {sorted(overlap)} appear in both expected and forbidden, "
                "so the contract can never be satisfied"
            )
        return self

    @property
    def declares_anything(self) -> bool:
        return bool(
            self.expected
            or self.forbidden
            or self.min_calls
            or self.max_calls
            or self.ordering
            or self.args
        )

    def build(self) -> ToolContract:
        return ToolContract(
            name="tools",
            expected=tuple(self.expected),
            forbidden=tuple(self.forbidden),
            min_calls=dict(self.min_calls),
            max_calls=dict(self.max_calls),
            ordering=tuple(o.build() for o in self.ordering),
            args=tuple(a.build() for a in self.args),
        )


class PhraseSpec(_Block):
    """Language the *trainee* must and must not use.

    An inversion worth naming. In the booking corpus a phrase contract constrains
    the agent, and constraining the simulated caller there would be checking the
    harness. Here the trainee is the *stimulus*: a compliance row is only testing
    something if the offending sentence is genuinely in the script, and this block
    is what makes the stimulus an asserted fact rather than an author's intention.
    `Scenario` also checks these against the declared turns at load time, so the
    assertion cannot be aspirational.
    """

    required: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    regex: bool = False

    @property
    def declares_anything(self) -> bool:
        return bool(self.required or self.forbidden)

    def build(self) -> PhraseContract:
        return PhraseContract(
            name="trainee-phrases",
            required=tuple(self.required),
            forbidden=tuple(self.forbidden),
            regex=self.regex,
            actor="caller",
        )


class TraineeSpec(_Block):
    """The trainee's rehearsed performance: the stimulus, fixed and reviewable."""

    role: str = "retail investment adviser"
    turns: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> "TraineeSpec":
        if not self.turns:
            raise ValueError(
                "trainee.turns is empty; an empty transcript scores as absence on every "
                "criterion, which is not the same as no data"
            )
        blank = [i for i, t in enumerate(self.turns, start=1) if not t.strip()]
        if blank:
            raise ValueError(f"trainee.turns {blank} are blank")
        return self


class Expectation(_Block):
    """What a competent human reviewer would say about this session, and why.

    `reason` is required and non-trivial. On a hand-labelled set the single
    largest source of apparent scorer error is a wrong label, and the reason is
    what lets a reviewer decide, months later, whether the scorer was wrong or
    the label was. Requiring a sentence costs a sentence.
    """

    human_verdict: Literal["pass", "fail"]
    reason: str = Field(min_length=20)

    @property
    def should_pass(self) -> bool:
        return self.human_verdict == "pass"


class ConsistencySpec(_Block):
    """How many identical repeats this row is run for, and what spread is allowed.

    `expected_spread` / `expected_flips` are this block's version of
    `expected_failure`, and they exist because instability is not something a
    per-run contract can express. A contract sees one trace; the finding here is a
    property of five. Declaring the instability as a floor rather than as prose
    means the day the curve is fixed, this row goes red for the right reason — the
    corpus notices a repair exactly as it notices a regression.

    Attributes:
        k: Repeats per arm.
        tolerance: Acceptable spread, in rubric points. Zero unless argued.
        control: Also run the cold arm — a fresh scoring service per repeat.
        expected_spread: Points of spread the warm arm is expected to show, at
            least. None means "no instability expected here".
        expected_flips: Pass/fail flips the warm arm is expected to show, at least.
        expectation: Required prose whenever either floor is set.
    """

    k: int = Field(default=5, ge=2)
    tolerance: float = Field(default=0.0, ge=0.0)
    control: bool = True
    expected_spread: int | None = Field(default=None, ge=1)
    expected_flips: int | None = Field(default=None, ge=1)
    expectation: str = ""

    @model_validator(mode="after")
    def _validate(self) -> "ConsistencySpec":
        declares = self.expected_spread is not None or self.expected_flips is not None
        if declares and len(self.expectation.strip()) < 20:
            raise ValueError(
                "consistency declares an expected spread or flip count but no "
                "expectation prose; a floor with no stated reason is a number nobody "
                "can review when it changes"
            )
        if self.expected_spread is not None and self.expected_spread <= self.tolerance:
            raise ValueError(
                f"consistency.expected_spread ({self.expected_spread}) is inside the "
                f"declared tolerance ({self.tolerance}), so the row expects an "
                "instability it also declares acceptable"
            )
        return self


class ExpectedFailure(_Block):
    """Contracts this build is expected to fail on this row, and what to expect."""

    contracts: tuple[str, ...] = ()
    since: str = ""
    expectation: str = Field(min_length=20)

    @model_validator(mode="after")
    def _validate(self) -> "ExpectedFailure":
        unknown = [c for c in self.contracts if c not in CONTRACT_NAMES]
        if unknown:
            raise ValueError(
                f"expected_failure.contracts names unknown contract(s) {unknown}; "
                f"legal: {sorted(CONTRACT_NAMES)}"
            )
        if not self.contracts:
            raise ValueError(
                "expected_failure declares no contracts, so it is a note rather than an "
                "expectation; delete the block or name what is expected to fail"
            )
        return self


class Scenario(_Block):
    """One roleplay row: the stimulus, the human verdict, and the assertions."""

    id: str
    title: str = Field(min_length=8)
    customer: str
    tags: tuple[str, ...] = ()
    trainee: TraineeSpec
    expectation: Expectation
    jurisdiction: str | None = None
    language: str | None = None
    tools: ToolSpec = Field(default_factory=ToolSpec)
    trainee_phrases: PhraseSpec = Field(default_factory=PhraseSpec)
    score_claims: bool = True
    feedback_grounded: bool = True
    no_progress: bool = False
    consistency: ConsistencySpec | None = None
    expected_failure: ExpectedFailure | None = None
    notes: str = ""

    # Filled in by the loader, not by the file.
    suite: str = ""
    source: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "Scenario":
        if not _ID_RE.match(self.id):
            raise ValueError(f"id {self.id!r} must be lower-case words joined by hyphens")
        unknown = [t for t in self.tags if t not in TAG_VOCABULARY]
        if unknown:
            raise ValueError(
                f"tags {unknown} are not in the vocabulary; legal: {sorted(TAG_VOCABULARY)}"
            )
        if not self.tags:
            raise ValueError("tags is empty; a row with no tags cannot be counted in coverage")
        if self.jurisdiction is not None and self.jurisdiction not in JURISDICTIONS:
            raise ValueError(
                f"jurisdiction {self.jurisdiction!r} unknown; legal: {sorted(JURISDICTIONS)}"
            )

        # Rule 2, the strong form: a phrase assertion about the stimulus is
        # checkable against the stimulus, here, now, without running anything.
        script = " \n ".join(self.trainee.turns)
        for phrase in self.trainee_phrases.required:
            found = (
                re.search(phrase, script, re.IGNORECASE)
                if self.trainee_phrases.regex
                else phrase.lower() in script.lower()
            )
            if not found:
                raise ValueError(
                    f"trainee_phrases.required {phrase!r} does not appear in trainee.turns, "
                    "so the row asserts a stimulus it does not contain and the check can "
                    "only ever fail for the wrong reason"
                )
        for phrase in self.trainee_phrases.forbidden:
            found = (
                re.search(phrase, script, re.IGNORECASE)
                if self.trainee_phrases.regex
                else phrase.lower() in script.lower()
            )
            if found:
                raise ValueError(
                    f"trainee_phrases.forbidden {phrase!r} appears in trainee.turns, so the "
                    "row fails on its own script rather than on the product's behaviour"
                )

        if self.expected_failure is not None:
            declared = {c.name for c in self.contracts()}
            missing = [c for c in self.expected_failure.contracts if c not in declared]
            if missing:
                raise ValueError(
                    f"expected_failure names {missing}, which this scenario does not declare, "
                    f"so the known gap is watched by nothing. Declared: {sorted(declared)}"
                )
        return self

    # ------------------------------------------------------------- compiling

    def contracts(self) -> tuple[Contract, ...]:
        """Compile the declared assertions into `lab.checks` contracts.

        Blocks that declare nothing produce no contract at all, rather than an
        empty one. An empty contract reports a vacuous pass on every trace, and a
        suite whose green count is padded with vacuous passes is worse than a
        smaller suite: the number goes up and the coverage does not.
        """
        built: list[Contract] = []
        if self.tools.declares_anything:
            built.append(self.tools.build())
        if self.trainee_phrases.declares_anything:
            built.append(self.trainee_phrases.build())
        if self.score_claims:
            built.append(ScoreClaimContract(claims=DEFAULT_SCORE_CLAIMS))
        if self.feedback_grounded:
            built.append(FeedbackGroundednessContract(topics=DEFAULT_TOPIC_CLAIMS))
        if self.no_progress:
            built.append(NoProgressContract(name="no-progress"))
        return tuple(built)

    def contract_set(self) -> ContractSet:
        return ContractSet(name=self.id, contracts=self.contracts())

    def expects_failure_of(self, contract_name: str) -> bool:
        return (
            self.expected_failure is not None
            and contract_name in self.expected_failure.contracts
        )

    def summary(self) -> str:
        return (
            f"{self.id} [{self.suite}] human={self.expectation.human_verdict} "
            f"customer={self.customer} turns={len(self.trainee.turns)} "
            f"contracts={len(self.contracts())} tags={','.join(self.tags)}"
        )


# --------------------------------------------------------------------------- #
# Loading and validating
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ValidationIssue:
    """One problem with one file. Collected, never raised mid-walk."""

    path: str
    message: str
    kind: str = "error"

    def render(self) -> str:
        return f"[{self.kind.upper()}] {self.path}: {self.message}"


@dataclass
class Corpus:
    """Every scenario, plus the customer profiles they name."""

    scenarios: tuple[Scenario, ...] = ()
    profiles: dict[str, CustomerProfile] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.profiles is None:
            self.profiles = {}

    def __iter__(self) -> Iterator[Scenario]:
        return iter(self.scenarios)

    def __len__(self) -> int:
        return len(self.scenarios)

    def by_id(self, scenario_id: str) -> Scenario:
        for scenario in self.scenarios:
            if scenario.id == scenario_id:
                return scenario
        raise KeyError(f"no scenario {scenario_id!r}; have {[s.id for s in self.scenarios]}")

    def suite(self, name: str) -> tuple[Scenario, ...]:
        return tuple(s for s in self.scenarios if s.suite == name)

    def suite_counts(self) -> dict[str, int]:
        counts = Counter(s.suite for s in self.scenarios)
        return {suite: counts.get(suite, 0) for suite in SUITES}

    def tag_counts(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for scenario in self.scenarios:
            counts.update(scenario.tags)
        return {tag: counts.get(tag, 0) for tag in sorted(TAG_VOCABULARY)}

    def profile_for(self, scenario: Scenario) -> CustomerProfile:
        return self.profiles[scenario.customer]

    def human_verdict_counts(self) -> dict[str, int]:
        counts = Counter(s.expectation.human_verdict for s in self.scenarios)
        return {"pass": counts.get("pass", 0), "fail": counts.get("fail", 0)}


@dataclass
class CorpusValidation:
    """Everything wrong with the corpus, and enough counts to judge its shape."""

    corpus: Corpus
    issues: tuple[ValidationIssue, ...] = ()
    files_seen: int = 0

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.kind == "error")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.kind == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self, *, coverage: bool = False) -> str:
        lines = [
            f"{len(self.corpus)}/{self.files_seen} scenario files loaded; "
            f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)"
        ]
        for issue in self.issues:
            lines.append("  " + issue.render())
        if coverage:
            counts = self.corpus.suite_counts()
            lines.append("  suites:")
            for suite in SUITES:
                minimum = SUITE_MINIMUMS[suite]
                flag = "" if counts[suite] >= minimum else f"  <- below the minimum of {minimum}"
                lines.append(f"    {suite}: {counts[suite]}{flag}")
            unused = [tag for tag, n in self.corpus.tag_counts().items() if n == 0]
            lines.append(
                f"  tags: {len(TAG_VOCABULARY) - len(unused)}/{len(TAG_VOCABULARY)} exercised"
                + (f"; unused: {', '.join(unused)}" if unused else "")
            )
            verdicts = self.corpus.human_verdict_counts()
            lines.append(
                f"  human verdicts: {verdicts['pass']} pass, {verdicts['fail']} fail "
                f"({len(self.corpus)} rows)"
            )
            expected = [s.id for s in self.corpus if s.expected_failure is not None]
            lines.append(f"  rows with a declared expected failure: {len(expected)}")
        return "\n".join(lines)


def iter_scenario_paths(root: Path | str = CORPUS_ROOT) -> Iterator[Path]:
    """Every scenario file, sorted, across the suite directories.

    Restricted to the suite directories so `customers/` is never parsed as a
    scenario, and sorted so a validation report is diffable between runs.
    """
    base = Path(root)
    for suite in SUITES:
        directory = base / suite
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            yield path


def load_scenario(path: str | Path) -> Scenario:
    """Load and validate one scenario. Raises on anything wrong with it."""
    resolved = Path(path)
    data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected a YAML mapping, got {type(data).__name__}")
    suite = resolved.parent.name
    if suite not in SUITES:
        raise ValueError(f"{resolved} is not in a suite directory; legal: {list(SUITES)}")
    scenario = Scenario(**data, suite=suite, source=str(resolved))
    if scenario.id != resolved.stem:
        raise ValueError(
            f"id {scenario.id!r} does not match the filename {resolved.stem!r}; a result "
            "row must locate its file without a lookup"
        )
    if not scenario.id.startswith(f"{suite}-"):
        raise ValueError(f"id {scenario.id!r} must start with its suite prefix {suite!r}-")
    return scenario


def load_corpus(root: Path | str = CORPUS_ROOT) -> Corpus:
    """Load every scenario and profile. Raises on the first bad file.

    For a report of *every* problem, use `validate_corpus`.
    """
    base = Path(root)
    profiles = load_profiles(base / "customers")
    scenarios = tuple(load_scenario(p) for p in iter_scenario_paths(base))
    for scenario in scenarios:
        if scenario.customer not in profiles:
            raise ValueError(
                f"{scenario.id}: unknown customer profile {scenario.customer!r}; "
                f"have {sorted(profiles)}"
            )
    return Corpus(scenarios=scenarios, profiles=profiles)


def validate_corpus(root: Path | str = CORPUS_ROOT) -> CorpusValidation:
    """Load everything, collect every problem, raise nothing."""
    base = Path(root)
    issues: list[ValidationIssue] = []
    profiles: dict[str, CustomerProfile] = {}
    try:
        profiles = load_profiles(base / "customers")
    except Exception as exc:  # noqa: BLE001 - a bad profile is an issue, not a crash
        issues.append(ValidationIssue(path=str(base / "customers"), message=str(exc)))

    loaded: list[Scenario] = []
    files = 0
    seen_ids: dict[str, str] = {}
    for path in iter_scenario_paths(base):
        files += 1
        try:
            scenario = load_scenario(path)
        except ValidationError as exc:
            for error in exc.errors():
                where = ".".join(str(p) for p in error["loc"]) or "(root)"
                issues.append(
                    ValidationIssue(path=str(path), message=f"{where}: {error['msg']}")
                )
            continue
        except Exception as exc:  # noqa: BLE001
            issues.append(ValidationIssue(path=str(path), message=str(exc)))
            continue

        if scenario.id in seen_ids:
            issues.append(
                ValidationIssue(
                    path=str(path),
                    message=f"duplicate id {scenario.id!r}, first seen in {seen_ids[scenario.id]}",
                )
            )
            continue
        if profiles and scenario.customer not in profiles:
            issues.append(
                ValidationIssue(
                    path=str(path),
                    message=(
                        f"unknown customer profile {scenario.customer!r}; have {sorted(profiles)}"
                    ),
                )
            )
            continue
        seen_ids[scenario.id] = str(path)
        loaded.append(scenario)

    return CorpusValidation(
        corpus=Corpus(scenarios=tuple(loaded), profiles=profiles),
        issues=tuple(issues),
        files_seen=files,
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m roleplay.corpus` — validate; non-zero exit on any error."""
    parser = argparse.ArgumentParser(description="Validate the roleplay scenario corpus.")
    parser.add_argument("--root", default=str(CORPUS_ROOT))
    parser.add_argument("--coverage", action="store_true", help="print suite and tag coverage")
    parser.add_argument("--list", action="store_true", help="one line per scenario")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args(argv)

    report = validate_corpus(args.root)
    if args.json:
        print(
            json.dumps(
                {
                    "files_seen": report.files_seen,
                    "loaded": len(report.corpus),
                    "ok": report.ok,
                    "issues": [
                        {"path": i.path, "kind": i.kind, "message": i.message}
                        for i in report.issues
                    ],
                    "suites": report.corpus.suite_counts(),
                    "tags": report.corpus.tag_counts(),
                    "human_verdicts": report.corpus.human_verdict_counts(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if report.ok else 1

    print(report.render(coverage=args.coverage))
    if args.list:
        for scenario in report.corpus:
            print("  " + scenario.summary())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
