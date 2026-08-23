"""Ground truth for the scorer: labels derived by rule, and the arguable ones dropped.

THE PROBLEM THIS SOLVES
-----------------------
Calibrating a scorer needs a reference, and the reference is the part people
fudge. The tempting move is to write the label next to the session, from memory,
while writing the session — at which point the scorer is being measured against
its author's intentions rather than against anything checkable, and every
disagreement is settled by whoever wrote the row.

So the labels here are **derived**, mechanically, from facts the product itself
recorded during the session: the disclosure register (`record_disclosure` events)
and the in-session compliance flagger (`flag_compliance_risk` events). Those are
ledgers, not opinions. The rules that read them are four sentences long, they are
stated in one place, and any reader can re-derive every label in the set from the
committed traces without trusting this module.

THE FOUR RULES
--------------
    R1  a code the jurisdiction requires is absent from the register     -> fail
    R2  the in-session flagger raised a compliance flag                  -> fail
    R3  register complete, no flag, the business was asked for, and every
        objection the customer raised was also resolved                  -> pass
    R4  anything else                                                    -> AMBIGUOUS

R1 and R2 are the rubric's own outright-failure clauses, transcribed. R3 is the
conjunction of every condition the rubric rewards. R4 is everything the declared
facts do not settle.

WHY R4 IS THE MOST IMPORTANT RULE
---------------------------------
An ambiguous item guessed becomes a permanent, invisible error term: the scorer is
marked wrong for agreeing with one defensible reading, the confusion matrix moves,
and nobody can tell afterwards which cells are measurement and which are the
labeller's coin-flip. Excluding those items costs sample size, which is a cost
that shows up in the report as a smaller `n`, and the report says how many were
dropped and why. A visible smaller number beats an invisible wrong one.

Concretely, R4 catches two situations that look nothing alike and are equally
unsettleable:

*   **Compliant, and never asked for the business.** A reviewer certifying a
    consultation and a reviewer refusing a salesperson who cannot close are both
    right; the rubric scores closing out of 4 and does not make it dispositive.
*   **Compliant, and an objection left hanging.** "Engaged with rather than
    acknowledged and abandoned" is a judgement about a conversation. The ledger
    records that an objection was raised and not resolved, which is evidence, and
    it is not the same thing as a rule.

WHERE THE SESSIONS COME FROM
----------------------------
Two sources, deliberately unequal:

*   `scenarios/roleplay/` — the fifteen regression rows. Each already declares a
    reviewer's `human_verdict`, so these rows get a second, independent label and
    the two are **cross-checked**. A row where the rule and the reviewer disagree
    is excluded as CONTESTED rather than resolved in either direction: two
    defensible authorities disagreeing is the definition of an item that should
    not be in a confusion matrix.
*   `roleplay/labelset.yaml` — the label pack, written for this measurement.

WHY THE TRACES ARE COMMITTED AND NOT REGENERATED
------------------------------------------------
`labels.jsonl` holds the traces themselves, and the calibration study replays
against that file rather than re-running the persona. That is not caching. A
calibration report is a statement about one fixed set of inputs, and a set that is
regenerated on every run from live code is a set that changes underneath the
report whenever anything upstream changes — the persona's wording, a profile's
fields, the latency model. The prompt digest would move, the recordings would go
stale, and the numbers would be attributed to a rubric edit that never happened.
Committing the traces makes the reference set an artefact with a checksum, which
is what `CalibrationReport.labels_sha256` is for.

`roleplay.labels.verify_pack()` is the guard that keeps the file honest: it
re-derives every label from the committed trace and refuses any mismatch, so the
file cannot drift away from the rules that are supposed to have produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lab.judges.calibration import LabelledTrace, load_labels, write_labels
from lab.trace.schema import Trace

from roleplay.persona import CustomerProfile
from roleplay.register import JURISDICTIONS, required_codes
from roleplay.runtime import RoleplayCoach
from roleplay.scorer import RubricScorer, SessionView, session_view

__all__ = [
    "RULES",
    "RuleName",
    "RuleLabel",
    "LabelRow",
    "LabelPack",
    "LABELSET_PATH",
    "LABELS_PATH",
    "rule_label",
    "load_pack",
    "build_rows",
    "labelled_and_excluded",
    "committed_labels",
    "write_committed_labels",
    "verify_pack",
    "exclusion_summary",
]

#: The rules, each with the sentence that states it. Documentation and vocabulary
#: in one object: a rule name that is not a key here cannot be cited by a row, so
#: a typo in `asserts.rule` is a load error rather than an unwatched expectation.
RULES: dict[str, str] = {
    "R1": (
        "a disclosure code the jurisdiction requires is absent from the session's "
        "register, which the rubric makes an outright failure whatever the total"
    ),
    "R2": (
        "the product's own in-session flagger raised a compliance flag, which the "
        "rubric makes dispositive"
    ),
    "R3": (
        "the register is complete, no flag was raised, the business was asked for, "
        "and every objection the customer raised was also resolved"
    ),
    "R4": (
        "the declared facts do not settle it: compliant but with the ask or an "
        "objection outstanding, which two competent reviewers may read differently"
    ),
}

RuleName = Literal["R1", "R2", "R3", "R4"]

LABELSET_PATH: Path = Path(__file__).resolve().parent / "labelset.yaml"
LABELS_PATH: Path = Path(__file__).resolve().parent / "scorer_study" / "labels.jsonl"


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RuleLabel:
    """One derived label: the verdict, the rule that produced it, and the facts.

    `grounds` holds every fact that fired, not just the first one. A session can
    be missing two disclosures *and* carry a flag, and a report that printed only
    the winning rule would describe a one-fault session where there were three.
    """

    label: Literal["pass", "fail", "ambiguous"]
    rule: RuleName
    reason: str
    grounds: tuple[str, ...] = ()

    @property
    def decided(self) -> bool:
        """True when this label can go into a confusion matrix."""
        return self.label in ("pass", "fail")

    def describe(self) -> str:
        joined = "; ".join(self.grounds) if self.grounds else "no ledger findings"
        return f"{self.label.upper()} by {self.rule}: {self.reason} ({joined})"


def rule_label(view: SessionView) -> RuleLabel:
    """Derive the label for one session from its own ledgers.

    Order is R1, R2, R3, R4 and it is not arbitrary: the two outright-failure
    clauses are checked before the reward conditions, because the rubric says a
    session fails "whatever it totals". Reversing them would let a polished
    session with a missing disclosure reach R3.
    """
    required = required_codes(view.jurisdiction)
    recorded = set(view.disclosed_codes)
    missing = tuple(code for code in required if code not in recorded)

    flags = tuple(
        str(flag.get("kind", "unspecified")) for flag in view.compliance_flags
    )

    raised = {str(o.get("key")) for o in view.objections_raised}
    resolved = {str(o.get("key")) for o in view.objections_resolved}
    unresolved = tuple(sorted(raised - resolved))

    asked = any(kind == "close_attempt" for kind in view.turn_kinds())

    grounds: list[str] = [
        f"register {len(recorded & set(required))}/{len(required)} for {view.jurisdiction}"
    ]
    if missing:
        grounds.append(f"missing {', '.join(missing)}")
    if flags:
        grounds.append(f"compliance flag(s): {', '.join(flags)}")
    if unresolved:
        grounds.append(f"objection(s) left unresolved: {', '.join(unresolved)}")
    grounds.append("asked for the business" if asked else "never asked for the business")

    if missing:
        return RuleLabel(
            label="fail",
            rule="R1",
            reason=(
                f"{view.jurisdiction} requires {len(required)} disclosure(s) and "
                f"{len(missing)} of them was never recorded: {', '.join(missing)}"
            ),
            grounds=tuple(grounds),
        )
    if flags:
        return RuleLabel(
            label="fail",
            rule="R2",
            reason=(
                "the product's own flagger recorded "
                f"{len(flags)} compliance flag(s) ({', '.join(flags)}), which the "
                "rubric treats as dispositive"
            ),
            grounds=tuple(grounds),
        )
    if asked and not unresolved:
        return RuleLabel(
            label="pass",
            rule="R3",
            reason=(
                f"every disclosure {view.jurisdiction} requires is recorded, no "
                "compliance flag was raised, every objection raised was resolved, "
                "and the trainee asked for the business"
            ),
            grounds=tuple(grounds),
        )

    unsettled = []
    if not asked:
        unsettled.append("the business was never asked for")
    if unresolved:
        unsettled.append(f"{len(unresolved)} objection(s) left unresolved")
    return RuleLabel(
        label="ambiguous",
        rule="R4",
        reason=(
            "compliant on every declared fact, but "
            + " and ".join(unsettled)
            + " — the rubric scores both out of 4 rather than making either "
            "dispositive, so two competent reviewers may reasonably differ"
        ),
        grounds=tuple(grounds),
    )


# --------------------------------------------------------------------------- #
# The label pack file
# --------------------------------------------------------------------------- #


class _Asserts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Literal["pass", "fail", "ambiguous"]
    rule: str

    @model_validator(mode="after")
    def _validate(self) -> "_Asserts":
        if self.rule not in RULES:
            raise ValueError(
                f"asserts.rule {self.rule!r} is not a rule; legal: {sorted(RULES)}"
            )
        if (self.rule == "R4") != (self.label == "ambiguous"):
            raise ValueError(
                "R4 is the ambiguous rule and the only one: a row asserting "
                f"rule={self.rule!r} with label={self.label!r} contradicts itself"
            )
        if self.rule in ("R1", "R2") and self.label != "fail":
            raise ValueError(f"{self.rule} can only produce a fail, not {self.label!r}")
        if self.rule == "R3" and self.label != "pass":
            raise ValueError(f"R3 can only produce a pass, not {self.label!r}")
        return self


class LabelRow(BaseModel):
    """One session in the label pack: the stimulus and the author's prediction."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str = Field(min_length=8)
    customer: str
    turns: tuple[str, ...] = Field(min_length=1)
    asserts: _Asserts
    jurisdiction: str | None = None
    language: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _validate(self) -> "LabelRow":
        if self.jurisdiction is not None and self.jurisdiction not in JURISDICTIONS:
            raise ValueError(
                f"{self.id}: jurisdiction {self.jurisdiction!r} unknown; "
                f"legal: {sorted(JURISDICTIONS)}"
            )
        return self


class LabelPack(BaseModel):
    """The label pack file, validated.

    `corpus_rows` is the roster: the corpus scenario ids that belong to this
    measurement, enumerated so that adding a scenario to `scenarios/roleplay/`
    cannot silently change the labelled set. See the header of `labelset.yaml`.
    """

    model_config = ConfigDict(extra="forbid")

    sessions: tuple[LabelRow, ...] = Field(min_length=1)
    corpus_rows: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> "LabelPack":
        seen: set[str] = set()
        for row in self.sessions:
            if row.id in seen:
                raise ValueError(f"duplicate label-pack id {row.id!r}")
            seen.add(row.id)
        roster: set[str] = set()
        for item_id in self.corpus_rows:
            if item_id in roster:
                raise ValueError(f"duplicate corpus row {item_id!r} in the roster")
            if item_id in seen:
                raise ValueError(
                    f"{item_id!r} is both a roster corpus row and a pack session; "
                    "one item would be graded twice under one id"
                )
            roster.add(item_id)
        return self

    def __iter__(self) -> Iterator[LabelRow]:  # type: ignore[override]
        return iter(self.sessions)

    def __len__(self) -> int:
        return len(self.sessions)


def load_pack(path: str | Path | None = None) -> LabelPack:
    """Load and validate the label pack. `safe_load` only: this file is data."""
    source = Path(path) if path is not None else LABELSET_PATH
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    return LabelPack.model_validate(data)


# --------------------------------------------------------------------------- #
# Building the set
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BuiltRow:
    """One session, its trace, its derived label, and why it is in or out."""

    item_id: str
    trace: Trace
    derived: RuleLabel
    source: str
    declared: str | None = None
    excluded_because: str | None = None

    @property
    def included(self) -> bool:
        return self.excluded_because is None


def _conversation(
    coach: RoleplayCoach,
    *,
    item_id: str,
    turns: Sequence[str],
    profile: CustomerProfile,
    jurisdiction: str | None,
    language: str | None,
) -> Trace:
    """Run stage one only — the talking, with no grade in the trace.

    `converse` rather than `run`, for the reason `RoleplayConversation` gives: an
    input that cannot contain the answer is cheaper to trust than an input a
    reader has to verify does not contain it.
    """
    return coach.converse(
        scenario_id=item_id,
        trainee_turns=list(turns),
        profile=profile,
        session_id=f"label-{item_id}",
        jurisdiction=jurisdiction,
        language=language,
    ).trace


def build_rows(
    *,
    corpus: Any | None = None,
    pack: LabelPack | None = None,
    coach: RoleplayCoach | None = None,
) -> list[BuiltRow]:
    """Run every session in both sources and derive its label.

    Corpus rows are cross-checked against their declared `human_verdict`; pack
    rows are checked against their declared `asserts`. A pack row whose prediction
    is wrong raises — the pack is authored, so a wrong prediction is a broken row.
    A corpus row that disagrees is *excluded*, not raised on — the corpus is not
    mine to overrule, and a disagreement between two defensible authorities is
    exactly the kind of item that must stay out of the arithmetic.
    """
    from roleplay.corpus import load_corpus  # local: avoids an import cycle

    resolved_corpus = corpus if corpus is not None else load_corpus()
    resolved_pack = pack if pack is not None else load_pack()
    driver = coach if coach is not None else RoleplayCoach(scorer=RubricScorer())

    rows: list[BuiltRow] = []

    # The roster, not the whole corpus. A row named in the roster that has since
    # left the corpus is an error: a labelled set that silently shrinks is worse
    # than one that fails to build, because the report still prints a number.
    by_id = {scenario.id: scenario for scenario in resolved_corpus}
    if resolved_pack.corpus_rows:
        missing = [name for name in resolved_pack.corpus_rows if name not in by_id]
        if missing:
            raise KeyError(
                f"labelset.yaml rosters corpus row(s) {missing} that the corpus no "
                "longer contains. Restore the scenario, or remove it from "
                "corpus_rows and re-record — the labelled set may not shrink quietly."
            )
        selected = [by_id[name] for name in resolved_pack.corpus_rows]
    else:  # pragma: no cover - an empty roster means "every row", for ad-hoc use
        selected = list(resolved_corpus)

    for scenario in selected:
        trace = _conversation(
            driver,
            item_id=scenario.id,
            turns=scenario.trainee.turns,
            profile=resolved_corpus.profile_for(scenario),
            jurisdiction=scenario.jurisdiction,
            language=scenario.language,
        )
        derived = rule_label(session_view(trace))
        declared = scenario.expectation.human_verdict
        excluded: str | None = None
        if not derived.decided:
            excluded = (
                f"AMBIGUOUS by {derived.rule}: {derived.reason}. The corpus reviewer "
                f"called it {declared!r}; no declared fact settles it, so it is "
                "excluded rather than resolved in either direction."
            )
        elif derived.label != declared:
            excluded = (
                f"CONTESTED: the rule derives {derived.label!r} by {derived.rule} "
                f"({derived.reason}) and the corpus reviewer declared {declared!r}. "
                "Two defensible readings disagree, so the item is excluded."
            )
        rows.append(
            BuiltRow(
                item_id=scenario.id,
                trace=trace,
                derived=derived,
                source="corpus",
                declared=declared,
                excluded_because=excluded,
            )
        )

    # The corpus already holds the loaded profiles. Reading them from it rather
    # than re-globbing the directory means the pack and the corpus cannot end up
    # practising against two different versions of the same customer.
    profiles = resolved_corpus.profiles
    for row in resolved_pack:
        try:
            profile = profiles[row.customer]
        except KeyError:
            raise KeyError(
                f"{row.id}: unknown customer {row.customer!r}; "
                f"the corpus defines {sorted(profiles)}"
            ) from None
        trace = _conversation(
            driver,
            item_id=row.id,
            turns=row.turns,
            profile=profile,
            jurisdiction=row.jurisdiction,
            language=row.language,
        )
        derived = rule_label(session_view(trace))
        if (derived.label, derived.rule) != (row.asserts.label, row.asserts.rule):
            raise ValueError(
                f"{row.id}: the pack predicts {row.asserts.label!r} by "
                f"{row.asserts.rule}, and the session it actually produces is "
                f"{derived.label!r} by {derived.rule} ({derived.reason}). "
                "Fix the script or fix the prediction; a row that does not create "
                "the situation it claims to create measures nothing."
            )
        rows.append(
            BuiltRow(
                item_id=row.id,
                trace=trace,
                derived=derived,
                source="pack",
                excluded_because=(
                    None
                    if derived.decided
                    else f"AMBIGUOUS by {derived.rule}: {derived.reason}"
                ),
            )
        )

    return rows


def labelled_and_excluded(
    rows: Sequence[BuiltRow] | None = None,
) -> tuple[list[LabelledTrace], list[BuiltRow]]:
    """Split built rows into the labelled set and the exclusions.

    The excluded rows are *returned*, not discarded. A calibration report that
    says "n=21" without saying which seven items were dropped and why is a report
    whose denominator cannot be audited, and this repo treats a naked percentage
    as a defect.
    """
    built = list(rows) if rows is not None else build_rows()
    items = [
        LabelledTrace(
            item_id=row.item_id,
            label=row.derived.label,  # narrowed to pass/fail by `included`
            trace=row.trace,
            note=f"{row.derived.rule}: {row.derived.reason}",
            labeller=f"rule:{row.derived.rule}",
        )
        for row in built
        if row.included
    ]
    return items, [row for row in built if not row.included]


def write_committed_labels(
    path: str | Path | None = None, rows: Sequence[BuiltRow] | None = None
) -> Path:
    """Write the labelled set to JSONL — the artefact of record.

    Committed, unlike `roleplay.calibration.written_labels`, and for a reason
    that has changed since that function was written: this file is what the live
    study replays against, so it must be pinned rather than derived. The digest in
    every calibration report refers to it.
    """
    target = Path(path) if path is not None else LABELS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    items, _ = labelled_and_excluded(rows)
    return write_labels(items, target)


def committed_labels(path: str | Path | None = None) -> list[LabelledTrace]:
    """The checked-in labelled set, read from disk.

    Read from the file rather than regenerated, because the file is what the
    report's `labels_sha256` refers to and what a reviewer actually reads.
    """
    return load_labels(Path(path) if path is not None else LABELS_PATH)


def verify_pack(items: Sequence[LabelledTrace] | None = None) -> list[str]:
    """Re-derive every committed label from its own committed trace.

    Returns the mismatches, empty when the file is honest. This is the guard that
    makes the committed labels trustworthy without trusting the process that wrote
    them: the trace is in the file, the rules are in this module, and anybody can
    check that one produces the other. It needs no corpus, no persona and no
    profiles, so it keeps working when those change.
    """
    resolved = list(items) if items is not None else committed_labels()
    problems: list[str] = []
    for item in resolved:
        derived = rule_label(session_view(item.trace))
        if derived.label != item.label:
            problems.append(
                f"{item.item_id}: committed label {item.label!r} but the trace "
                f"derives {derived.label!r} by {derived.rule} ({derived.reason})"
            )
        elif not derived.decided:
            problems.append(
                f"{item.item_id}: committed as a labelled item, but the trace "
                f"derives AMBIGUOUS by {derived.rule} and should be excluded"
            )
    return problems


def exclusion_summary(excluded: Sequence[BuiltRow]) -> str:
    """The exclusion list, in full, with the reason for each.

    Printed next to every calibration table in this study. The count belongs to
    the denominator and the reasons belong to whoever is deciding whether to
    believe it.
    """
    if not excluded:
        return "no items excluded: every session in both sources was decidable by rule"
    lines = [f"{len(excluded)} item(s) excluded from the metrics:"]
    for row in excluded:
        lines.append(f"  {row.item_id} [{row.source}]")
        lines.append(f"      {row.excluded_because}")
    return "\n".join(lines)
