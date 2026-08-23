"""Computed regime verdicts: the numbers are pinned, and `kind` does the work.

WHAT THIS FILE IS DEFENDING
---------------------------
`roleplay.regime_eval` is the module that turns `scenarios/advisory/registers/*`
from documentation into an instrument. Two classes of thing can go wrong with it,
and they need different tests.

**The prose can drift from the evidence.** Every agreement figure this repo prints
about the advisory corpus is computed by `RegimeEvaluator`, so the figures are
pinned here — 16 of 18 rows, one abstention, six of six divergence blocks
computing opposite verdicts, and the two named disagreements by id. If a probe is
retuned and the numbers move, these tests go red and whoever moved them has to
update the claim as well as the code. A repo whose README quotes a rate no test
asserts is a repo whose README is eventually wrong.

**The `kind` field can quietly stop mattering.** The whole argument for the
register being data rather than a keyword list is that a verbatim requirement, a
prescribed unit, a prohibition, a gate and a recorded absence need genuinely
different logic. So each of those has a test built on a synthetic trace: a
paraphrase of a verbatim rule must MISS, the same paraphrase must SATISFY the MAS
substance counterpart, a prohibited arrangement must fail on presence, a
`not-required` omission must PASS, and a disclosure that arrives after the
recommendation must miss on POSITION — on a trace whose timestamps are all
identical, so a positional rule is the only thing that could have caught it.
"""

from __future__ import annotations

import pytest

from lab.clock import FakeClock
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace
from roleplay.advisory import load_registers
from roleplay.corpus import Corpus, validate_advisory_corpus
from roleplay.regime_eval import (
    FALSE_PASS_ROWS,
    PROBES,
    STATUSES,
    VERDICTS,
    ComputedRow,
    RegimeEvaluator,
    run_corpus,
)

# --------------------------------------------------------------------------- #
# The pinned numbers. Every one of these is quoted in a report or a docstring.
# --------------------------------------------------------------------------- #

EXPECTED_ROWS = 18

#: Computed verdict per row, under the regime the row is graded in. Pinned in
#: full rather than as a count, because a count can stay at 16 while two rows swap
#: places, and two rows swapping places is a different instrument.
EXPECTED_VERDICTS: dict[str, str] = {
    "divergence-commission-volunteered-four-verdicts": "fail",
    "divergence-cooling-off-duration-and-trigger": "pass",
    "divergence-two-products-one-meeting-mas-carveout": "pass",
    "divergence-unsolicited-note-failing-to-advise": "fail",
    "divergence-verbal-close-nothing-in-writing": "pass",
    "nearmiss-charges-disclosed-after-the-ask": "fail",
    "nearmiss-restricted-advice-buried-in-a-long-turn": "fail",
    "nearmiss-warning-addressed-to-the-partner": "undecidable",
    "clause-surrender-value-explained": "pass",
    "clause-surrender-value-recited": "fail",
    "clause-surrender-value-understated": "fail",
    "conflict-skip-the-questions-and-the-warnings": "fail",
    "conflict-vulnerable-customer-keen-to-sign": "fail",
    "conflict-what-would-you-do-obliged": "fail",
    "survival-graceful-exit-is-the-pass": "pass",
    "survival-hostile-opening-decoupled": "pass",
    "lang-codeswitched-risk-warning": "pass",
    "lang-indirect-refusal-recorded-as-open": "pass",
}

#: The two rows where the computed verdict and the hand label differ, and they are
#: different kinds of disagreement. Both are reported as findings rather than
#: tuned away, so both are asserted.
EXPECTED_DISAGREEMENTS: dict[str, str] = {
    # The instrument has no field for a disclosure's addressee, so it abstains.
    # The hand label is right and the evaluator says it cannot tell.
    "nearmiss-warning-addressed-to-the-partner": "undecidable",
    # The register is clean and the session is not: this row fails on reading an
    # indirect refusal as an open outcome, which no register entry addresses.
    "lang-indirect-refusal-recorded-as-open": "pass",
}

EXPECTED_CONFUSION: dict[str, int] = {
    "pass/pass": 7,
    "pass/fail": 0,
    "pass/undecidable": 0,
    "fail/pass": 1,
    "fail/fail": 9,
    "fail/undecidable": 1,
}


@pytest.fixture(scope="module")
def advisory() -> Corpus:
    validation = validate_advisory_corpus()
    assert validation.ok, "\n".join(i.render() for i in validation.errors)
    return validation.corpus


@pytest.fixture(scope="module")
def computed(advisory: Corpus) -> dict[str, ComputedRow]:
    """Every row run once, and every regime it names computed. The whole report."""
    return run_corpus(advisory)


# --------------------------------------------------------------------------- #
# Reference integrity: an unprobed requirement must not read green
# --------------------------------------------------------------------------- #


def test_every_register_entry_has_a_probe() -> None:
    """36 entries, 36 probes. A requirement nothing grades is worse than none."""
    entries = {e.id for register in load_registers().values() for e in register.entries.values()}
    assert len(entries) == 36
    missing = sorted(entries - set(PROBES))
    assert not missing, f"register entries with no probe: {missing}"
    extra = sorted(set(PROBES) - entries)
    assert not extra, f"probes for entries no register holds: {extra}"


def test_every_probe_states_its_assumption() -> None:
    """The mapping from a cited requirement to a pattern is always an assumption.

    The requirement is sourced and the probe is not, and this repo's rule is that
    a reader sees which is which. A probe with an empty `basis` would print a
    verdict with a citation and no statement of what was assumed to reach it.
    """
    silent = sorted(p.entry_id for p in PROBES.values() if len(p.basis) < 20)
    assert not silent, f"probes with no stated assumption: {silent}"


def test_the_vocabularies_are_what_the_report_says() -> None:
    assert STATUSES == ("satisfied", "missed", "not-applicable", "instrument-gap")
    assert VERDICTS == ("pass", "fail", "undecidable")


# --------------------------------------------------------------------------- #
# The pinned run
# --------------------------------------------------------------------------- #


def test_the_computed_verdicts_are_the_reported_ones(computed: dict[str, ComputedRow]) -> None:
    assert len(computed) == EXPECTED_ROWS
    actual = {row_id: row.own.verdict for row_id, row in computed.items()}
    assert actual == EXPECTED_VERDICTS


def test_agreement_with_the_hand_labels_is_sixteen_of_eighteen(
    computed: dict[str, ComputedRow]
) -> None:
    """The headline, with its denominator. In-sample, and the CLI says so."""
    disagreements = {
        row_id: row.own.verdict for row_id, row in computed.items() if not row.agrees
    }
    assert disagreements == EXPECTED_DISAGREEMENTS
    assert len(computed) - len(disagreements) == 16


def test_the_confusion_matrix_is_pinned(computed: dict[str, ComputedRow]) -> None:
    from roleplay.regime_eval import _confusion

    assert _confusion(computed) == EXPECTED_CONFUSION


def test_exactly_one_row_is_an_honest_abstention(computed: dict[str, ComputedRow]) -> None:
    """And it abstains on the entry whose answer needs a field nobody has."""
    undecidable = [
        row_id for row_id, row in computed.items() if row.own.verdict == "undecidable"
    ]
    assert undecidable == ["nearmiss-warning-addressed-to-the-partner"]
    verdict = computed["nearmiss-warning-addressed-to-the-partner"].own
    gaps = [e.entry_id for e in verdict.gaps]
    assert gaps == ["fca-support-retail-customer-understanding"]
    assert "addressee" in verdict.gaps[0].reason


def test_every_divergence_block_computes_opposite_verdicts(
    computed: dict[str, ComputedRow]
) -> None:
    """The real test of the corpus: the same transcript, two regimes, two answers.

    Six blocks — the five divergence rows plus the vulnerable-customer row, which
    carries a block because a pooled cross-market score would average a hard
    procedural breach with a non-event.
    """
    blocks = 0
    for row in computed.values():
        scenario = row.scenario
        if scenario.divergence is None:
            continue
        blocks += 1
        verdicts = {v.verdict for v in row.verdicts.values()}
        assert len(verdicts) > 1, f"{scenario.id}: computed verdicts do not diverge: {verdicts}"
        assert "fail" in verdicts and "pass" in verdicts, f"{scenario.id}: {verdicts}"
    assert blocks == 6


def test_the_named_entry_agrees_with_the_block_on_every_regime(
    computed: dict[str, ComputedRow]
) -> None:
    """18 regime verdicts, entry-scoped, which is the claim a block actually makes."""
    pairs = agree = 0
    for row in computed.values():
        scenario = row.scenario
        if scenario.divergence is None:
            continue
        for block in scenario.divergence.regimes:
            pairs += 1
            verdict = row.verdicts[block.regime]
            entry = next(
                (e for e in verdict.entries if e.entry_id == block.register_entry), None
            )
            assert entry is not None, f"{scenario.id}: {block.register_entry} not probed"
            expected = (
                {"satisfied", "not-applicable"} if block.verdict == "pass" else {"missed"}
            )
            agree += entry.status in expected
    assert (agree, pairs) == (18, 18)


def test_the_whole_register_adds_two_failures_the_blocks_do_not_name(
    computed: dict[str, ComputedRow]
) -> None:
    """16/18 register-scoped, and both deviations are on one row.

    Under MAS, `divergence-commission-volunteered-four-verdicts` also fails MAS-3
    ¶36 — the same rule that makes `divergence-verbal-close-nothing-in-writing`
    fail under MAS, on the same close-with-nothing-furnished shape. Under the SFC
    it also fails the more-assistance requirement, because the customer says she
    does not follow and gets no more explanation. Both are register-scoped
    findings about a row whose block is entry-scoped, and neither means the block
    is wrong.
    """
    deviations = {}
    for row_id, row in computed.items():
        scenario = row.scenario
        if scenario.divergence is None:
            continue
        for block in scenario.divergence.regimes:
            verdict = row.verdicts[block.regime]
            if verdict.verdict != block.verdict:
                deviations[(row_id, block.regime)] = tuple(e.entry_id for e in verdict.missed)
    assert set(deviations) == {
        ("divergence-commission-volunteered-four-verdicts", "mas"),
        ("divergence-commission-volunteered-four-verdicts", "sfc-ia"),
    }
    assert deviations[("divergence-commission-volunteered-four-verdicts", "mas")] == (
        "mas-recommendation-document-before-signing",
    )


def test_a_gate_miss_is_marked_decisive(computed: dict[str, ComputedRow]) -> None:
    """A gate fails the session regardless of any score, and says so in the reason."""
    row = computed["divergence-unsolicited-note-failing-to-advise"].own
    gates = [e for e in row.missed if e.decisive]
    assert [e.entry_id for e in gates] == ["sfc-ia-unsolicited-derivative-duty-to-advise"]
    assert "regardless of any score" in row.reason()


def test_the_scorer_still_passes_sessions_the_register_fails(
    computed: dict[str, ComputedRow]
) -> None:
    """The seeded compliance miss, measured against the register rather than asserted.

    `roleplay/SEEDED_DEFECTS.md` documents a scorer that grades the disclosure
    criterion on vocabulary. This is that defect expressed as a disagreement with
    a computed register verdict, and it is deliberately NOT fixed: the rows below
    are certified by the product and fail on a cited paragraph.
    """
    false_passes = sorted(
        row_id
        for row_id, row in computed.items()
        if row.own.verdict == "fail" and row.scorer_verdict == "pass"
    )
    assert false_passes == [
        "conflict-what-would-you-do-obliged",
        "divergence-commission-volunteered-four-verdicts",
        "nearmiss-charges-disclosed-after-the-ask",
        "nearmiss-restricted-advice-buried-in-a-long-turn",
    ]


def test_the_naive_control_over_credits_what_the_register_catches(
    computed: dict[str, ComputedRow]
) -> None:
    """The number the near-miss corpus exists to produce, measured.

    Three register entries across the four rows are credited by a check over the
    register's own vocabulary and missed by the register, every one of them on
    POSITION — and one of the four rows is passed outright by the naive check
    while the register cannot decide it.
    """
    evaluator = RegimeEvaluator()
    over_credited: dict[str, tuple[str, ...]] = {}
    naive_passes = []
    for row_id in FALSE_PASS_ROWS:
        row = computed[row_id]
        shadow = evaluator.naive_shadow(row.result.trace, regime=row.scenario.regime)
        if shadow.over_credited:
            over_credited[row_id] = shadow.over_credited
        if shadow.naive_verdict == "pass" and row.own.verdict != "pass":
            naive_passes.append(row_id)
        for entry_id in shadow.over_credited:
            assert dict(shadow.miss_classes)[entry_id] == "position"
    assert sum(len(v) for v in over_credited.values()) == 3
    assert naive_passes == ["nearmiss-warning-addressed-to-the-partner"]


# --------------------------------------------------------------------------- #
# `kind` drives the logic. One synthetic trace per kind.
# --------------------------------------------------------------------------- #


def _trace(*turns: str, scenario_id: str = "synthetic") -> Trace:
    """A trace of adviser turns and nothing else, with every timestamp identical.

    `FakeClock` never advances unless something asks it to, and nothing here does,
    so every event carries `ts=0.0`. That is deliberate: a positional rule
    compared on `ts` cannot fail on this trace, so the timing tests below only
    pass if the rule is decided on event-stream position. This repo has fixed that
    bug once already — see `lab.checks.contracts._sequence`.
    """
    builder = TraceBuilder(scenario_id=scenario_id, adapter="regime-eval-test", clock=FakeClock())
    builder.session_start()
    for turn in turns:
        builder.caller_utterance(turn)
    builder.session_end(reason="scored")
    trace = builder.build()
    assert {event.ts for event in trace.events} == {0.0}
    return trace


def _status(trace: Trace, *, regime: str, entry_id: str) -> str:
    verdict = RegimeEvaluator().evaluate(trace, regime=regime)
    entry = next((e for e in verdict.entries if e.entry_id == entry_id), None)
    assert entry is not None, f"{entry_id} not probed under {regime}"
    return entry.status


_DISCOVERY_TURN = "Before anything else, what would you want this money to do in ten years?"
_RECOMMENDATION_TURN = (
    "The fund I have in mind is a balanced growth fund and your capital is at risk."
)
_CHARGES_TURN = "Our advice charge is one thousand six hundred pounds, and the fund's annual management charge is 0.68 per cent a year."
_CLOSE_TURN = "So shall we get the paperwork started?"


def test_a_paraphrase_of_a_verbatim_requirement_misses_and_satisfies_the_substance_twin() -> None:
    """The same sentence, two drafting traditions, two answers. §8's whole point.

    COBS 4.5A.10R prescribes a form of words; MAS-2 ¶25(c) prescribes a meaning.
    One paraphrase, and the instrument has to give different answers or the `kind`
    field is decoration.
    """
    paraphrase = _trace(
        _DISCOVERY_TURN,
        "The fund I have in mind is a balanced growth fund. Past performance is no guide "
        "to what happens next.",
        _CLOSE_TURN,
    )
    assert _status(paraphrase, regime="fca", entry_id="fca-past-performance-verbatim") == "missed"
    assert (
        _status(paraphrase, regime="mas", entry_id="mas-past-performance-substance") == "satisfied"
    )

    prescribed = _trace(
        _DISCOVERY_TURN,
        "The fund I have in mind is a balanced growth fund. Past performance is not a "
        "reliable indicator of future results.",
        _CLOSE_TURN,
    )
    assert (
        _status(prescribed, regime="fca", entry_id="fca-past-performance-verbatim") == "satisfied"
    )


def test_a_prohibited_arrangement_fails_on_presence_and_is_lawful_next_door() -> None:
    """`prohibition`: the presence of the thing fails. Disclosing it does not cure it."""
    trace = _trace(
        _DISCOVERY_TURN,
        _RECOMMENDATION_TURN,
        "On the cost, the provider pays us a commission of three per cent of what you invest.",
        _CLOSE_TURN,
    )
    assert _status(trace, regime="fca", entry_id="fca-adviser-charging-only") == "missed"
    # The same sentence, in the regime where the arrangement is lawful and the duty
    # is to disclose the amount.
    assert _status(trace, regime="mas", entry_id="mas-commission-amount") == "satisfied"
    verdict = RegimeEvaluator().evaluate(trace, regime="fca")
    assert verdict.verdict == "fail"
    assert any(e.decisive for e in verdict.missed)


def test_a_not_required_omission_passes() -> None:
    """`not-required`: the omission is the rule being followed.

    A verbal recommendation closed on the call with no suitability report: a
    breach under the FCA, and not a requirement at all under Reg BI. If the
    `not-required` entry did not stop it, a cross-market checker would import the
    UK requirement into a market that does not have one.
    """
    trace = _trace(
        "Before I recommend anything I am going to send you our disclosure document, and I "
        "will wait until you have it in front of you.",
        "What would you want this money to do for you?",
        "So the rollover I would suggest is the one you can actually log into, at forty to "
        "sixty basis points a year.",
        "Do you want me to start it today?",
    )
    reg_bi = RegimeEvaluator().evaluate(trace, regime="reg-bi")
    assert reg_bi.verdict == "pass"
    absence = next(e for e in reg_bi.entries if e.entry_id == "reg-bi-no-suitability-report")
    assert absence.status == "not-applicable"
    assert "does not impose this requirement" in absence.reason

    fca = RegimeEvaluator().evaluate(trace, regime="fca")
    assert (
        _status(trace, regime="fca", entry_id="fca-suitability-report-before-conclusion")
        == "missed"
    )
    assert fca.verdict == "fail"


def test_a_timing_violation_fails_on_position_and_not_on_the_clock() -> None:
    """`timing`: the same three turns, reordered, and only the order differs.

    Both traces carry identical timestamps (see `_trace`), so a rule compared on
    `ts` would pass both. One passes and one misses, which means position decided
    it — and the reason names the two turn indices.
    """
    in_time = _trace(_DISCOVERY_TURN, _CHARGES_TURN, _RECOMMENDATION_TURN, _CLOSE_TURN)
    too_late = _trace(_DISCOVERY_TURN, _RECOMMENDATION_TURN, _CHARGES_TURN, _CLOSE_TURN)
    entry_id = "fca-charging-structure-in-writing-before"
    assert _status(in_time, regime="fca", entry_id=entry_id) == "satisfied"
    assert _status(too_late, regime="fca", entry_id=entry_id) == "missed"

    verdict = RegimeEvaluator().evaluate(too_late, regime="fca")
    late = next(e for e in verdict.entries if e.entry_id == entry_id)
    assert late.miss_class == "position"
    assert "turn 2" in late.reason and "turn 1" in late.reason


def test_a_gate_cannot_be_waived_by_the_person_it_protects() -> None:
    """`gate`: the duty runs to the firm, and the customer cannot discharge it."""
    trace = _trace(
        "You called back, so I will be quick. Two questions and then the numbers.",
        "Fine - no questions then. The transfer is a stocks-and-shares ISA into our "
        "balanced growth fund at sixty-eight basis points a year.",
        "On your instruction then: the full balance across.",
    )
    entry_id = "fca-must-not-recommend-on-insufficient-information"
    assert _status(trace, regime="fca", entry_id=entry_id) == "missed"
    verdict = RegimeEvaluator().evaluate(trace, regime="fca")
    gate = next(e for e in verdict.entries if e.entry_id == entry_id)
    assert gate.decisive
    assert gate.miss_class == "waiver"
    assert verdict.verdict == "fail"


def test_a_prescribed_unit_needs_the_figure_and_the_trigger() -> None:
    """`prescribed-unit`: 14 days from receipt is right in one regime and wrong in two.

    Not a keyword difference and not a threshold: three numbers and three start
    triggers, decided arithmetically. This is the divergence row's logic on a
    synthetic transcript, so a change in the corpus cannot quietly carry it.
    """
    trace = _trace(
        "What would make you decide against this after you had signed?",
        "You get at least fourteen days to examine the policy, and the fourteen days start "
        "from the date the policy document reaches you. There is a surrender value in the "
        "illustration.",
    )
    assert _status(trace, regime="mas", entry_id="mas-free-look-14-days") == "satisfied"
    assert _status(trace, regime="fca", entry_id="fca-cancellation-30-days-life") == "missed"
    assert _status(trace, regime="sfc-ia", entry_id="sfc-ia-cooling-off-21-days") == "missed"

    # And the unit, not just the topic: a whole percentage point satisfies the SFC
    # ceiling and one and a half per cent of the same sum does not.
    whole = _trace(
        _DISCOVERY_TURN,
        _RECOMMENDATION_TURN,
        "The monetary benefit we receive is capped at two per cent of the amount you invest.",
    )
    fractional = _trace(
        _DISCOVERY_TURN,
        _RECOMMENDATION_TURN,
        "The commission I receive is one and a half per cent of the amount you invest.",
    )
    entry_id = "sfc-ia-monetary-benefit-percentage-ceiling"
    assert _status(whole, regime="sfc-ia", entry_id=entry_id) == "satisfied"
    assert _status(fractional, regime="sfc-ia", entry_id=entry_id) == "missed"
    # The same two sentences are both amounts, so MAS is satisfied by both. That
    # asymmetry is the register being keyed by unit rather than by market.
    assert _status(fractional, regime="mas", entry_id="mas-commission-amount") == "satisfied"


def test_an_uncalibrated_judge_is_named_and_does_not_gate() -> None:
    """The judge story: routed through `lab.judges`, refused, reported, not decisive.

    `lab.judges.registry` exists to stop an unmeasured judge deciding anything.
    The probes that need one name it, ask for it, and record the registry's answer
    as a residue on a status decided by the deterministic limb alone.
    """
    trace = _trace(
        _DISCOVERY_TURN,
        _RECOMMENDATION_TURN,
        "If you stopped at two years in the surrender value would be close to nothing.",
        "Have I put that clearly?",
        _CLOSE_TURN,
    )
    verdict = RegimeEvaluator().evaluate(trace, regime="fca")
    entry = next(
        e for e in verdict.entries if e.entry_id == "fca-support-retail-customer-understanding"
    )
    assert entry.status == "satisfied"
    assert "disclosure_understood" in entry.residue
    assert "not registered" in entry.residue
    assert "does not gate" in entry.residue


def test_an_assumption_labelled_detector_reports_and_does_not_decide() -> None:
    """The minimisation detector fires on the understated clause and decides nothing.

    `call_craft.md` A-16 labels it an ASSUMPTION with a named false-positive mode
    and no calibrated TNR, so the honest thing is to print it and let another entry
    carry the verdict. The row still fails — on the missing understanding check —
    and the detector's output is evidence a human can read.
    """
    trace = _trace(
        "When your income stopped, what did you stop paying?",
        "There is a version of this policy at a hundred and twenty a month.",
        "Technically, yes, if you surrendered in the first couple of years you would get "
        "back less than you had paid in. But in practice that is not what happens. Most "
        "people keep these going.",
        "So shall we get it in place?",
    )
    verdict = RegimeEvaluator().evaluate(trace, regime="fca")
    fair = next(e for e in verdict.entries if e.entry_id == "fca-fair-clear-not-misleading")
    assert fair.status == "satisfied"
    assert "ADVISORY DETECTOR" in fair.reason
    assert "no calibrated TNR" in fair.reason
    assert verdict.verdict == "fail"
    assert [e.entry_id for e in verdict.missed] == ["fca-support-retail-customer-understanding"]


def test_the_assumption_labelled_detector_stays_quiet_on_its_nearest_negative(
    computed: dict[str, ComputedRow]
) -> None:
    """A detector that fires on everything discriminates no better than none.

    `clause-surrender-value-explained` shares its first three turns with the
    understated variant and states the same limitation in stronger terms — it is
    the nearest available negative for this detector, and the row's own notes say
    so. If the adjacency detector fired here too, its output would be worth
    nothing even as evidence.
    """
    from roleplay.regime_eval import _minimisation_adjacency, Transcript
    from roleplay.scorer import session_view

    for row_id, should_fire in (
        ("clause-surrender-value-understated", True),
        ("clause-surrender-value-explained", False),
        ("clause-surrender-value-recited", False),
    ):
        tx = Transcript.from_view(session_view(computed[row_id].result.trace))
        assert bool(_minimisation_adjacency(tx)) is should_fire, row_id


def test_an_unknown_regime_raises_rather_than_returning_a_clean_sheet() -> None:
    """An empty requirement set is indistinguishable from full compliance."""
    trace = _trace(_DISCOVERY_TURN)
    with pytest.raises(KeyError):
        RegimeEvaluator().evaluate(trace, regime="apac-retail")


# --------------------------------------------------------------------------- #
# Every probe must be able to fail. A check that cannot fail is worse than none.
# --------------------------------------------------------------------------- #

#: The hostile transcript that makes each entry MISS, for the entries no row in
#: the corpus fails. Written because the corpus sweep is not a reachability
#: proof: eleven of the thirty-six entries are never missed by any of the
#: eighteen rows under any regime, and "never fired here" and "cannot fire" look
#: identical from the outside. One of them turned out to be the second kind —
#: `fca-fair-clear-not-misleading` used a refutation-only decider with an empty
#: pattern set, so it returned `satisfied` on "this is risk-free and you cannot
#: lose", and two rows' computed PASS rested on it alone.
_HOSTILE: dict[str, tuple[str, tuple[str, ...]]] = {
    "fca-fair-clear-not-misleading": (
        "fca",
        (_DISCOVERY_TURN, _RECOMMENDATION_TURN, "This is risk-free and you cannot lose.", _CLOSE_TURN),
    ),
    "mas-commission-amount": (
        "mas",
        (
            _DISCOVERY_TURN,
            _RECOMMENDATION_TURN,
            "The provider pays us a commission on this, and I will not say how much.",
            _CLOSE_TURN,
        ),
    ),
    "mas-oral-performance-needs-simultaneous-writing": (
        "mas",
        (
            _DISCOVERY_TURN,
            _RECOMMENDATION_TURN,
            "That fund returned 9 per cent last year and grew by a third over five.",
            _CLOSE_TURN,
        ),
    ),
    "mas-free-look-14-days": (
        "mas",
        (
            _DISCOVERY_TURN,
            _RECOMMENDATION_TURN,
            "There is a cooling-off period of seven days from receipt of the policy document.",
            _CLOSE_TURN,
        ),
    ),
    "reg-bi-fees-standardised-ranges-acceptable": (
        "reg-bi",
        (_DISCOVERY_TURN, _RECOMMENDATION_TURN, _CLOSE_TURN),
    ),
    "reg-bi-conflict-elimination-time-limited-incentives": (
        "reg-bi",
        (
            _DISCOVERY_TURN,
            _RECOMMENDATION_TURN,
            "Frankly there is a sales contest on and I can get you the better rate.",
            _CLOSE_TURN,
        ),
    ),
    "sfc-ia-language-of-the-client-s-choice": (
        "sfc-ia",
        (
            _DISCOVERY_TURN,
            "You would rather have this in Cantonese, but the documents are only in "
            "English and that is what you will get.",
            _RECOMMENDATION_TURN,
            _CLOSE_TURN,
        ),
    ),
    "sfc-ia-benefit-illustration-no-emphasis-on-non-guaranteed": (
        "sfc-ia",
        (
            _DISCOVERY_TURN,
            _RECOMMENDATION_TURN,
            "The projected value at an assumed rate of 6 per cent gets you to two "
            "hundred thousand, and that is the number to hold on to.",
            _CLOSE_TURN,
        ),
    ),
}


def test_every_entry_that_can_be_breached_has_an_input_that_breaches_it(
    computed: dict[str, ComputedRow]
) -> None:
    """`missed` is reachable for every probed entry that is not a carve-out.

    Two sources of evidence, and neither alone is enough. The corpus shows an
    entry firing on a real transcript; `_HOSTILE` shows it firing at all. An
    entry that appears in neither is decoration — it contributes a `satisfied`
    to every verdict it touches and can never subtract one, which is the
    "instrument that cannot fail" this repo's own report language calls worse
    than no instrument.
    """
    registers = load_registers()
    evaluator = RegimeEvaluator()
    fired_on_corpus: set[str] = set()
    for row in computed.values():
        # Every regime, not only the ones the row names: an entry that no row
        # *names* can still be exercised by a transcript, and the question here is
        # whether the check works at all.
        for verdict in evaluator.evaluate_all(row.result.trace).values():
            fired_on_corpus.update(e.entry_id for e in verdict.missed)

    unreachable: list[str] = []
    for regime, register in sorted(registers.items()):
        for entry_id, entry in register.entries.items():
            if entry.kind == "not-required":
                continue
            if entry_id in fired_on_corpus:
                continue
            hostile = _HOSTILE.get(entry_id)
            if hostile is None:
                unreachable.append(f"{entry_id} (no corpus row fails it and no hostile input)")
                continue
            hostile_regime, turns = hostile
            status = _status(_trace(*turns), regime=hostile_regime, entry_id=entry_id)
            if status != "missed":
                unreachable.append(f"{entry_id} (hostile input returned {status!r})")
    assert not unreachable, "entries with no reachable failure:\n  " + "\n  ".join(unreachable)


def test_a_carve_out_entry_cannot_be_made_to_miss(computed: dict[str, ComputedRow]) -> None:
    """The other direction: `not-required` must never fail, on any input.

    `not-required` is decided before the transcript is looked at, and this holds
    that shortcut in place. If a carve-out could miss, a cross-market checker
    would import a requirement into the market that does not have one — which is
    the failure the four registers exist to make impossible.
    """
    registers = load_registers()
    hostile = _trace(
        "Frankly there is a sales contest on and this month only I can hold the rate.",
        "You have said you know the risks, so no questions then.",
        "This is risk-free and you cannot lose, with guaranteed returns of six per cent.",
        _CLOSE_TURN,
    )
    evaluator = RegimeEvaluator()
    checked = 0
    for regime, register in sorted(registers.items()):
        verdict = evaluator.evaluate(hostile, regime=regime)
        for entry in verdict.entries:
            if register.entries[entry.entry_id].kind != "not-required":
                continue
            checked += 1
            assert entry.status == "not-applicable", (regime, entry.entry_id, entry.status)
    assert checked == 5, f"expected five carve-out entries across the registers, saw {checked}"


def test_the_misleading_check_does_not_fire_on_a_denial_of_the_thing_it_looks_for() -> None:
    """A guarantee check that matches "I would never say this is guaranteed" is useless.

    The roleplay pack already carries `compliance-cautious-tone-crosses-anyway`
    for exactly this trap: a blocklist on "guaranteed returns" against a trainee
    who said the opposite. The refutation patterns on the FCA fair-clear entry are
    unnegatable forms for that reason, and this holds them to it.
    """
    for turn in (
        "I would never say this is guaranteed, and I am not promising you anything.",
        "Your capital is at risk and you could get back less than you put in.",
        "There is no guarantee attached to any of this.",
    ):
        trace = _trace(_DISCOVERY_TURN, _RECOMMENDATION_TURN, turn, _CLOSE_TURN)
        assert (
            _status(trace, regime="fca", entry_id="fca-fair-clear-not-misleading") == "satisfied"
        ), turn


def test_a_verbatim_disclosure_after_the_close_misses_on_position() -> None:
    """The prescribed words, said too late. `timing` is not decoration either.

    COBS 6.2B.33R's restricted-advice disclosure is due "in good time before
    providing advice" — the register entry says so in its own `timing` field — and
    a verbatim check with no positional rule graded it on presence alone, so the
    prescribed term said after the paperwork question satisfied it. Same trace
    shape as the other timing tests: every `ts` is identical, so only a rule
    decided on event-stream position can tell these two apart.
    """
    disclosure = "I should say we give restricted advice - we only look at our own panel."
    in_time = _trace(_DISCOVERY_TURN, disclosure, _RECOMMENDATION_TURN, _CLOSE_TURN)
    too_late = _trace(_DISCOVERY_TURN, _RECOMMENDATION_TURN, _CLOSE_TURN, disclosure)
    entry = "fca-restricted-advice-oral-disclosure"
    assert _status(in_time, regime="fca", entry_id=entry) == "satisfied"
    assert _status(too_late, regime="fca", entry_id=entry) == "missed"


def test_the_same_waiver_sentence_is_refused_under_every_regime_that_declares_it() -> None:
    """A declared waiver pattern must be enforced whatever the entry's `kind`.

    "You have said you know the risks, so I will take you at your word" is the
    customer purporting to discharge a duty the firm owes. Three entries across
    three regimes declare that pattern set — the FCA gate at COBS 9A.2.13R, Reg
    BI's care obligation, and the SFC's "reasonable in all the circumstances" —
    and the waiver limb used to be read only under `kind: gate`, so the identical
    sentence failed the FCA and was silently ignored by the other two. Fourteen
    declared patterns that decided nothing is the same defect as a check that
    cannot fail, one indirection further away.
    """
    trace = _trace(
        _DISCOVERY_TURN,
        "You have said you know the risks, so I will take you at your word.",
        _RECOMMENDATION_TURN,
        _CLOSE_TURN,
    )
    for regime, entry_id in (
        ("fca", "fca-must-not-recommend-on-insufficient-information"),
        ("reg-bi", "reg-bi-care-obligation-binds-the-recommendation"),
        ("sfc-ia", "sfc-ia-suitability-reasonable-in-all-circumstances"),
    ):
        verdict = RegimeEvaluator().evaluate(trace, regime=regime)
        entry = next(e for e in verdict.entries if e.entry_id == entry_id)
        assert entry.status == "missed", (regime, entry_id, entry.status)
        assert entry.miss_class == "waiver", (regime, entry_id, entry.miss_class)
        # And the discovery limb is not what caught it: the adviser did ask.
        assert "waived rather than met" in entry.reason


def test_a_declared_waiver_pattern_is_never_left_unenforced() -> None:
    """Structural: no probe may declare `forbidden` patterns that nothing consults.

    The behavioural test above covers the three entries that have them today. This
    one covers the next one somebody adds: `_decide` reads `forbidden` on the
    prohibition path, on the waiver path, and inside `_negative_only`, so a probe
    whose only failing limb is a pattern set nothing reaches would be a silent
    no-op — and the reachability test would only catch it if that entry had no
    other way to fail.
    """
    registers = load_registers()
    unenforced: list[str] = []
    for register in registers.values():
        for entry_id, entry in register.entries.items():
            probe = PROBES[entry_id]
            if not probe.forbidden:
                continue
            reached = (
                entry.kind in {"prohibition", "gate", "substance"}
                or getattr(probe.decider, "refutation_only", False)
            )
            if not reached:
                unenforced.append(f"{entry_id} (kind {entry.kind!r}, decider bypasses forbidden)")
    assert not unenforced, "probes declaring patterns nothing reads:\n  " + "\n  ".join(unenforced)
