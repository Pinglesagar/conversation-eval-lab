"""The three rows as data, with a schema that enforces the tier's admission rule.

WHY THESE ARE NOT `scenarios.loader.Scenario` ROWS
--------------------------------------------------
Every other row in this repository is a `Scenario`: a persona, a goal, and
contracts over what the agent did. A `Scenario` must declare at least one
contract, because the loader's central rule is that a row asserting nothing is a
row that passes for free.

A transport row cannot honour that rule, and the reason is not a gap in the
loader — it is that **there is no conversation here**. This tier has no caller, no
model and no tool calls: it publishes known audio into a real room and measures
when it comes out the other side. `tools:`, `promises:`, `phrases:` and
`no_re_ask:` are all inapplicable, and satisfying the loader would mean declaring
a contract that cannot fail — exactly the silent-green defect the loader exists to
prevent. So the tier has its own schema, with its own closed vocabulary and its
own assertions, and `tests/test_voice_transport.py` validates every row against
it. Reusing a model that does not fit, in order to be counted in a corpus that
measures something else, would make both numbers worse.

THE ADMISSION RULE, IN CODE
---------------------------
`why_transport` is a required field with a minimum length. A row belongs in this
tier only if it can say what real transport gives it that in-process cannot, and
the field is validated rather than advisory because "we ran it over the network to
be realistic" is how a fast deterministic suite turns into a slow flaky one. Three
rows, three distinct justifications; a fourth row would have to write a fourth.

ASSERTIONS, AND ONE CHARACTERISATION
------------------------------------
Two of the three rows assert properties of the *instrument* rather than of a
product: that the gap is measurable and bounded, that the loss-free control really
is loss-free. That is deliberate. The tier's job is to produce three credible
numbers, and the way those numbers go wrong is that the harness's own pacing or
buffering is what got measured — so the assertions guard against that, and the
findings are reported rather than graded.

The lifecycle row is different: `expected_verdict` pins the behaviour that was
actually observed, so that a change in it fails the row and gets read by a human.
It says `recovered-turn-lost`, which is a *bad* outcome for a product and the
correct outcome for this harness — nothing here retransmits an interrupted
utterance, and neither does a production voice agent. Pinning it is the difference
between "we know what happens on reconnect" and "we hope somebody would notice".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lab.voice.transport.measure import (
    DegradationComparison,
    DeliveryGapMeasurement,
    LifecycleObservation,
    LifecycleVerdict,
)

__all__ = [
    "ROW_DIR",
    "TRANSPORT_CATEGORIES",
    "TRANSPORT_TAG_VOCABULARY",
    "DegradationAssertion",
    "DeliveryGapAssertion",
    "LifecycleAssertion",
    "RowOutcome",
    "TransportRow",
    "coverage",
    "evaluate_degradation",
    "evaluate_delivery_gap",
    "evaluate_lifecycle",
    "load_rows",
]

#: Where the rows live. Sibling to the audio tier's own directory rather than
#: inside it, because these rows are validated by a different schema and run by a
#: different runner — filing them together would invite a reader to count them in
#: one total.
ROW_DIR: Path = Path(__file__).resolve().parents[3] / "scenarios" / "audio" / "transport"

#: One category per row, and exactly three categories, because there are exactly
#: three things that only exist in transport. Adding a category means claiming a
#: fourth, which should be an argument in a review rather than a new string.
TRANSPORT_CATEGORIES: dict[str, str] = {
    "delivery-gap": (
        "the interval between an agent-side response existing and its arrival at a "
        "listener — the part an agent-side latency figure omits by construction"
    ),
    "transport-degradation": (
        "real loss and real pacing over a live connection, measured against the "
        "file-based perturbation ladder that claims to stand in for them"
    ),
    "connection-lifecycle": (
        "a participant drops mid-utterance and rejoins; whether the connection "
        "recovers, and separately whether the turn does"
    ),
}

#: The tier's closed tag vocabulary. Kept separate from
#: `scenarios.loader.AUDIO_TAG_VOCABULARY` and asserted disjoint from it in the
#: tests, on that dictionary's own argument one level up: if `webrtc` were legal
#: on an in-process audio row, an in-process row could claim coverage of the one
#: thing only transport can test.
TRANSPORT_TAG_VOCABULARY: dict[str, str] = {
    "webrtc": "runs over a real WebRTC session rather than in process",
    "non-gating": (
        "advisory in CI by design: a flaky network test that blocks a merge trains "
        "people to bypass the gate"
    ),
    "real-time": "cannot be sped up — the row's duration is its audio's duration",
    "receiver-side": "the measurement is taken where the listener is, not where the agent is",
    "injected-loss": "frames are deliberately withheld at the sender",
    "same-session-control": (
        "carries a baseline arm recorded in the SAME live session with the condition "
        "switched off. Named for the session rather than borrowing the audio tier's "
        "`control-arm`, which means something else there — separating a vendor "
        "limitation from a product defect — and a tag that means two things is a "
        "coverage table that cannot be read"
    ),
    "mid-utterance": "the disruption lands inside a turn rather than between turns",
    "characterisation": (
        "pins the behaviour observed today so that a change in it fails the row "
        "rather than passing quietly"
    ),
}


class _Block(BaseModel):
    """Closed model: an unknown key is an error, not a comment."""

    model_config = ConfigDict(extra="forbid")


class DeliveryGapAssertion(_Block):
    """What must hold for the delivery-gap row to have measured anything.

    `min_gap_ms` is the load-bearing one and it is a *lower* bound, which looks
    backwards until you see what it is guarding. If the measured gap were zero,
    the receiver-side instant would be the agent-side instant, which would mean
    the harness had not actually measured delivery — and the row would then be
    reporting agreement with the figure it exists to contradict.
    """

    min_samples: int = Field(default=10, ge=2)
    min_gap_ms: float = Field(default=5.0, gt=0.0)
    max_gap_ms: float = Field(default=500.0, gt=0.0)
    max_stdev_ms: float = Field(
        default=60.0,
        gt=0.0,
        description=(
            "Scatter ceiling. A gap that is right on average and unstable per turn "
            "is not usable as a product figure, and for live in-call coaching the "
            "variance is the risk: a suggestion is either in the moment or not."
        ),
    )

    @model_validator(mode="after")
    def _ordered(self) -> "DeliveryGapAssertion":
        if self.min_gap_ms >= self.max_gap_ms:
            raise ValueError(
                f"min_gap_ms ({self.min_gap_ms}) must be below max_gap_ms "
                f"({self.max_gap_ms}) or no measurement can satisfy both"
            )
        return self


class DegradationAssertion(_Block):
    """What must hold for the degradation comparison to be worth reading.

    Both bounds are on the *instrument*, not on the transport. The comparison's
    conclusion — whether a file perturbation resembles real loss — is a finding
    and is reported whatever it turns out to be; what would invalidate it is the
    harness's own pacing starving the channel, which is what the control arm
    detects.
    """

    min_injected_loss: float = Field(
        default=0.10,
        gt=0.0,
        lt=1.0,
        description="Loss must be big enough that concealment has something to conceal.",
    )
    max_control_excess: float = Field(
        default=0.05,
        gt=0.0,
        lt=1.0,
        description=(
            "Ceiling on how much MORE silence the loss-free arm shows than the "
            "unperturbed file. One-sided on purpose: the failure this guards against "
            "is the harness starving the transport, which *adds* silence to an arm "
            "that should have none added.\n\n"
            "An earlier version of this field was a ceiling on the control arm's "
            "silent fraction outright, at 5%. That was wrong, and the row failed on "
            "it: real speech contains quiet frames of its own — this clip reads 17.6% "
            "silent in the file with nothing done to it — so an absolute ceiling was "
            "a check on the clip rather than on the harness. Measured, the control arm "
            "comes in *below* the file baseline, because a codec's output has a noise "
            "floor a raw file does not, which is the same reason each side of the "
            "comparison needs its own baseline."
        ),
    )
    min_transport_excess: float = Field(
        default=0.02,
        gt=0.0,
        lt=1.0,
        description=(
            "Floor on how much silence the loss arm adds over the control arm. If "
            "loss adds nothing measurable, the comparison is vacuous — either the "
            "injection did not happen or concealment was total — and reporting a "
            "ratio computed from it would be reporting noise."
        ),
    )
    require_control_arm: bool = Field(default=True)


class LifecycleAssertion(_Block):
    """What must hold on a drop and rejoin. See the module docstring on pinning."""

    expected_verdict: LifecycleVerdict = "recovered-turn-lost"
    max_listener_silence_ms: float = Field(
        default=6_000.0,
        gt=0.0,
        description=(
            "Ceiling on the transport-level recovery: drop to the far side being "
            "subscribed again. A recovery slower than this is a dropped call in every "
            "way the caller can tell. Asserted on the transport figure rather than on "
            "what the listener heard, because the latter includes quiet this harness "
            "leaves on purpose after a rejoin — a ceiling that could fail on the "
            "harness's own settle time would be a ceiling on the wrong thing."
        ),
    )
    min_frames_before_drop: int = Field(
        default=10,
        ge=1,
        description="The drop must land inside the turn, not before it started.",
    )


class TransportRow(_Block):
    """One transport row: what it measures, why it needs a real network, what must hold."""

    id: str
    title: str = Field(min_length=8)
    category: str
    tags: list[str] = Field(min_length=1)
    measures: str = Field(
        min_length=30, description="What figure this row produces, in one sentence."
    )
    why_transport: str = Field(
        min_length=60,
        description=(
            "Why an in-process adapter cannot produce it. The tier's admission "
            "rule; see the module docstring."
        ),
    )
    duration_cap_s: float = Field(gt=0.0, le=180.0)
    fixture: str = Field(description="Committed recording this row's numbers come from.")
    notes: str = Field(min_length=20)

    delivery_gap: DeliveryGapAssertion | None = None
    degradation: DegradationAssertion | None = None
    lifecycle: LifecycleAssertion | None = None

    @model_validator(mode="after")
    def _validate(self) -> "TransportRow":
        if not self.id.startswith("audio-transport-"):
            raise ValueError(
                f"id {self.id!r} must start with 'audio-transport-' so a result row "
                "names its own tier and file"
            )
        if self.category not in TRANSPORT_CATEGORIES:
            raise ValueError(
                f"unknown category {self.category!r}; the tier has exactly three: "
                f"{sorted(TRANSPORT_CATEGORIES)}. A fourth needs an argument, not a string"
            )
        unknown = sorted(set(self.tags) - set(TRANSPORT_TAG_VOCABULARY))
        if unknown:
            raise ValueError(
                f"unknown tag(s) {unknown}; the vocabulary is closed — add to "
                f"TRANSPORT_TAG_VOCABULARY with a definition, or use one of "
                f"{sorted(TRANSPORT_TAG_VOCABULARY)}"
            )
        duplicates = sorted({tag for tag in self.tags if self.tags.count(tag) > 1})
        if duplicates:
            raise ValueError(f"duplicate tag(s) {duplicates}")
        if set(self.tags) & set(TRANSPORT_CATEGORIES):
            raise ValueError(
                "the category is a field, not a tag; listing it twice lets the two "
                "disagree"
            )

        declared = {
            "delivery-gap": self.delivery_gap,
            "transport-degradation": self.degradation,
            "connection-lifecycle": self.lifecycle,
        }
        mine = declared.pop(self.category)
        if mine is None:
            raise ValueError(
                f"a {self.category!r} row must declare its own assertion block, or it "
                "asserts nothing and passes for free"
            )
        stray = sorted(name for name, block in declared.items() if block is not None)
        if stray:
            raise ValueError(
                f"a {self.category!r} row also declares the assertion block(s) for "
                f"{stray}; one row, one category, one set of assertions"
            )
        return self

    @property
    def category_definition(self) -> str:
        return TRANSPORT_CATEGORIES[self.category]

    def summary_line(self) -> str:
        return f"{self.id}  [{self.category}]  {self.title}  ({self.duration_cap_s:.0f}s cap)"


class RowOutcome(BaseModel):
    """A row's verdict, with the reasons it reached it and the findings it produced.

    `NOT-RUN` is a first-class verdict rather than a skip. A live tier that has not
    been run is not passing, and a report that shows three blanks is honest in a way
    that a report showing nothing is not.
    """

    model_config = ConfigDict(extra="forbid")

    row_id: str
    category: str
    verdict: Literal["PASS", "FAIL", "NOT-RUN"]
    reasons: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    def describe(self) -> str:
        head = f"{self.row_id}: {self.verdict}"
        if self.reasons:
            return head + " — " + "; ".join(self.reasons)
        return head


def _not_run(row: TransportRow, why: str) -> RowOutcome:
    return RowOutcome(
        row_id=row.id, category=row.category, verdict="NOT-RUN", reasons=[why]
    )


def evaluate_delivery_gap(
    row: TransportRow, measurement: DeliveryGapMeasurement | None
) -> RowOutcome:
    """Score the delivery-gap row against its own assertion block."""
    assertion = row.delivery_gap
    assert assertion is not None, "validated at load"
    if measurement is None:
        return _not_run(row, "no recording was available for this row")
    if not measurement.reportable:
        return RowOutcome(
            row_id=row.id,
            category=row.category,
            verdict="FAIL",
            reasons=[f"the measurement refused to report: {measurement.refusal}"],
        )
    assert measurement.distribution is not None
    distribution = measurement.distribution
    reasons: list[str] = []
    if distribution.n < assertion.min_samples:
        reasons.append(
            f"{distribution.n} turn(s) measured, {assertion.min_samples} required"
        )
    mean_ms = (distribution.mean_s or 0.0) * 1000.0
    if mean_ms < assertion.min_gap_ms:
        reasons.append(
            f"mean gap {mean_ms:.1f} ms is below the {assertion.min_gap_ms:.1f} ms floor, "
            "so the receiver-side instant is indistinguishable from the agent-side one "
            "and delivery was not actually measured"
        )
    if mean_ms > assertion.max_gap_ms:
        reasons.append(
            f"mean gap {mean_ms:.1f} ms exceeds the {assertion.max_gap_ms:.1f} ms ceiling; "
            "that is a broken path, not a slow one"
        )
    stdev_ms = (distribution.stdev_s or 0.0) * 1000.0
    if stdev_ms > assertion.max_stdev_ms:
        reasons.append(
            f"per-turn scatter {stdev_ms:.1f} ms exceeds the {assertion.max_stdev_ms:.1f} ms "
            "ceiling, so no single figure represents a turn"
        )
    findings = [
        f"the agent-side figure for these {distribution.n} turn(s) is 0.0 ms of delivery "
        f"time by construction; the measured delivery gap is {mean_ms:.1f} ms "
        f"(stdev {stdev_ms:.1f} ms)"
    ]
    return RowOutcome(
        row_id=row.id,
        category=row.category,
        verdict="FAIL" if reasons else "PASS",
        reasons=reasons,
        findings=findings,
    )


def evaluate_degradation(
    row: TransportRow, comparison: DegradationComparison | None
) -> RowOutcome:
    """Score the degradation row. The agreement verdict is a finding, not a grade."""
    assertion = row.degradation
    assert assertion is not None, "validated at load"
    if comparison is None:
        return _not_run(row, "no recording was available for this row")
    if not comparison.reportable:
        return RowOutcome(
            row_id=row.id,
            category=row.category,
            verdict="FAIL",
            reasons=[f"the comparison refused to report: {comparison.refusal}"],
        )
    reasons: list[str] = []
    nominal = comparison.nominal_loss or 0.0
    if nominal < assertion.min_injected_loss:
        reasons.append(
            f"injected loss {nominal:.1%} is below the {assertion.min_injected_loss:.1%} "
            "floor, too little for concealment to be observable"
        )
    if assertion.require_control_arm and comparison.control is None:
        reasons.append(
            "no loss-free control arm in the same session, so the loss arm's silent "
            "fraction cannot be attributed to loss"
        )
    if comparison.control is not None and comparison.file_baseline is not None:
        control_silent = comparison.control.silent_fraction or 0.0
        baseline_silent = comparison.file_baseline.silent_fraction or 0.0
        excess = control_silent - baseline_silent
        if excess > assertion.max_control_excess:
            reasons.append(
                f"the loss-free control arm was {control_silent:.1%} silent against a "
                f"file baseline of {baseline_silent:.1%} — {excess:.1%} more, above the "
                f"{assertion.max_control_excess:.1%} ceiling. The harness under-fed the "
                "transport, so the loss arm's figure is its own pacing"
            )
    transport_excess = comparison.transport_excess
    if transport_excess is None:
        reasons.append(
            "the transport's added silence could not be computed, so there is no "
            "normalised figure to compare"
        )
    elif transport_excess < assertion.min_transport_excess:
        reasons.append(
            f"loss added only {transport_excess:.1%} silence over the control arm, "
            f"below the {assertion.min_transport_excess:.1%} floor; the comparison is "
            "vacuous rather than favourable"
        )
    return RowOutcome(
        row_id=row.id,
        category=row.category,
        verdict="FAIL" if reasons else "PASS",
        reasons=reasons,
        findings=list(comparison.findings),
    )


def evaluate_lifecycle(
    row: TransportRow, observation: LifecycleObservation | None
) -> RowOutcome:
    """Score the lifecycle row against the behaviour it pins."""
    assertion = row.lifecycle
    assert assertion is not None, "validated at load"
    if observation is None:
        return _not_run(row, "no recording was available for this row")
    if not observation.reportable:
        return RowOutcome(
            row_id=row.id,
            category=row.category,
            verdict="FAIL",
            reasons=[f"the observation refused to report: {observation.refusal}"],
        )
    reasons: list[str] = []
    if observation.verdict != assertion.expected_verdict:
        reasons.append(
            f"verdict is {observation.verdict!r} but the row pins "
            f"{assertion.expected_verdict!r}; the reconnect behaviour changed and a "
            "human should read why"
        )
    # Asserted on the transport-level figure, not on what the listener heard: the
    # latter includes quiet the harness left on purpose after rejoining, and a
    # ceiling that fails because of the harness's own settle time would be a
    # ceiling on the wrong thing. The listener's figure is reported instead.
    recovery = observation.transport_recovery_s
    if recovery is None:
        reasons.append(
            "the transport-level recovery could not be measured (no re-subscription "
            "was recorded), so recovery time is unknown rather than fast"
        )
    elif recovery * 1000.0 > assertion.max_listener_silence_ms:
        reasons.append(
            f"the far side was not receiving again until {recovery * 1000.0:.0f} ms "
            f"after the drop, above the {assertion.max_listener_silence_ms:.0f} ms ceiling"
        )
    if observation.frames_pushed_before_drop < assertion.min_frames_before_drop:
        reasons.append(
            f"only {observation.frames_pushed_before_drop} frame(s) were pushed before "
            f"the drop, fewer than the {assertion.min_frames_before_drop} required for "
            "it to be mid-utterance"
        )
    return RowOutcome(
        row_id=row.id,
        category=row.category,
        verdict="FAIL" if reasons else "PASS",
        reasons=reasons,
        findings=list(observation.findings),
    )


def load_rows(directory: Path | str = ROW_DIR) -> list[TransportRow]:
    """Load and validate every row, sorted by id so a report is diffable.

    Raises on the first invalid file rather than collecting issues — three rows is
    not a corpus, and the collect-then-report treatment the scenario loader gives
    a hundred files would be ceremony here.
    """
    base = Path(directory)
    rows: list[TransportRow] = []
    for path in sorted(base.glob("*.yaml")):
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(body, Mapping):
            raise ValueError(f"{path.name}: a row file must contain a mapping")
        row = TransportRow.model_validate(body)
        if row.id != path.stem:
            raise ValueError(
                f"{path.name}: declares id {row.id!r}; the filename and the id must "
                "match so a failing row names its own file"
            )
        rows.append(row)
    if not rows:
        raise FileNotFoundError(f"no transport rows found in {base}")
    return rows


def coverage(rows: Sequence[TransportRow]) -> dict[str, Any]:
    """Counts a report can print without recomputing them: categories and tags.

    Counts, never percentages: three rows make every percentage a rounding of
    one-third, and a naked percentage is a defect in this repository.
    """
    return {
        "rows": len(rows),
        "categories": {
            name: sum(1 for row in rows if row.category == name)
            for name in TRANSPORT_CATEGORIES
        },
        "tags": {
            tag: sum(1 for row in rows if tag in row.tags)
            for tag in TRANSPORT_TAG_VOCABULARY
        },
    }
