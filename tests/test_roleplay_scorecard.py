"""The scorecard's own invariants, and the one that matters most: a gate cannot be outscored.

WHAT THIS FILE IS FOR
---------------------
`roleplay/scorecard.py` is a registry of things a person gets certified against.
Two classes of defect in it are worse than a wrong number:

1.  **A gate that can be averaged away.** If a compliance requirement contributes
    to a total, it is a heavily-weighted criterion and not a gate, and a session
    with a missing disclosure can pass by being charming elsewhere. The headline
    test here is exactly that case: a session that clears the points threshold and
    fails one gate must fail.
2.  **A row that certifies on an undeclared basis.** A KPI asserting a disclosure
    requirement with no citation and no assumption label is the single worst thing
    this repo could contain, because the whole argument is care with evidence.
    `_validate` refuses it at import; the tests below prove `_validate` would
    actually catch it, rather than trusting that it does.

Everything else here is internal consistency: the detectors named in the registry
resolve to code that exists, the rubric this scorecard extends has no criterion
that quietly fell off the end, and the denominator can only shrink in ways the
report prints.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import replace

import pytest

from roleplay import scorecard as sc
from roleplay.scorer import CRITERIA

# --------------------------------------------------------------------------- #
# The registry is complete and internally consistent
# --------------------------------------------------------------------------- #


def test_every_group_is_populated_within_the_design_budget() -> None:
    """Seven groups, 3-5 KPIs each. The budget is the point, not an accident.

    A scorecard nobody reads all of certifies nobody, so the upper bound is as
    load-bearing as the lower one and `_validate` enforces both.
    """
    assert set(sc.GROUPS) == {"CS", "DI", "OH", "CE", "CG", "CL", "LL"}
    for group in sc.GROUPS:
        assert 3 <= len(sc.by_group(group)) <= 5, group
    assert len(sc.KPIS) == sum(len(sc.by_group(g)) for g in sc.GROUPS)


def test_ids_are_unique_and_group_prefixed() -> None:
    ids = [k.id for k in sc.KPIS]
    assert len(ids) == len(set(ids))
    for kpi in sc.KPIS:
        assert kpi.id.startswith(f"{kpi.group}-")


def test_every_kpi_declares_a_detector() -> None:
    """The requirement from the brief, asserted directly rather than inferred."""
    for kpi in sc.KPIS:
        assert kpi.detector.name.strip(), kpi.id
        assert kpi.detector.note.strip(), kpi.id
        assert kpi.detector.kind in sc.DETECTOR_KINDS, kpi.id


def test_every_kpi_is_sourced_or_labelled_an_assumption() -> None:
    """No third category. Every row cites docs/_research/ or declares an assumption."""
    for kpi in sc.KPIS:
        assert kpi.basis or kpi.assumptions, kpi.id


def test_every_kpi_states_a_denominator_and_what_it_excludes() -> None:
    for kpi in sc.KPIS:
        assert kpi.denominator.strip(), kpi.id
        assert kpi.excludes.strip(), kpi.id


def test_rate_kpis_do_not_claim_a_session_denominator() -> None:
    """A rate whose denominator is 'the session' is a rate with no denominator.

    Cheap, and it catches the copy-paste that turns a per-event average into a
    number nobody can interpret.
    """
    for kpi in sc.KPIS:
        if "rate" in kpi.scale or "per " in kpi.scale or "averaged" in kpi.scale:
            assert "n=1" not in kpi.denominator, kpi.id


# --------------------------------------------------------------------------- #
# Gates are excluded from the score total, structurally
# --------------------------------------------------------------------------- #


def test_gates_and_diagnostics_carry_no_points() -> None:
    """The structural half of 'never averaged into a score'.

    `points_available` sums `max_points` over every row without filtering, which is
    only safe because a non-SCORE row cannot carry points. This test is what makes
    that unfiltered sum legitimate.
    """
    for kpi in sc.KPIS:
        if kpi.gate_or_score == "SCORE":
            assert kpi.max_points > 0, kpi.id
        else:
            assert kpi.max_points == 0, kpi.id


def test_the_score_total_is_exactly_the_scored_rows() -> None:
    assert sc.points_available() == sum(k.max_points for k in sc.scored())
    assert sc.points_available() == sum(k.max_points for k in sc.KPIS)
    assert sum(k.max_points for k in sc.gates()) == 0


def test_every_gate_ladders_to_licence_to_operate() -> None:
    """A gate pointed at a growth metric is a gate somebody will trade away."""
    for kpi in sc.gates():
        assert kpi.business_metric == "licence_to_operate", kpi.id


def test_every_kpi_names_a_business_metric_from_the_closed_vocabulary() -> None:
    """The ladder, enforced: a behaviour that cannot name the metric it leads is out."""
    for kpi in sc.KPIS:
        assert kpi.business_metric in sc.BUSINESS_METRICS, kpi.id


# --------------------------------------------------------------------------- #
# Judges cannot gate on their own
# --------------------------------------------------------------------------- #


def test_judge_detectors_declare_that_they_need_calibration() -> None:
    for kpi in sc.KPIS:
        assert kpi.detector.requires_calibration == (kpi.detector.kind == "judge"), kpi.id


def test_the_judge_dependent_kpis_are_a_pinned_set() -> None:
    """Adding a judge to the scorecard should be a deliberate act, not a drift.

    Every one of these is unusable until a calibration report is committed and
    clears `lab.judges.require_calibrated`. The repo has a measured reason for the
    caution: one judge prompt scored TPR 0.250 (2/8) with TNR 1.000 (16/16) and
    kappa 0.308 — it missed six of eight real failures — and the gate refused it
    (`lab/judges/hallucinated_confirmation/calibration_v1.md`).
    """
    judged = {k.id for k in sc.KPIS if k.detector.kind == "judge"}
    assert judged == {"CS-3", "DI-4", "OH-2", "CE-2", "CG-3", "CL-3", "LL-1"}


def test_no_gate_rests_on_an_uncalibrated_judge_alone() -> None:
    """A gate that cannot run until someone calibrates a judge is not a gate."""
    for kpi in sc.gates():
        if kpi.detector.requires_calibration:
            assert kpi.detector.fallback, kpi.id


# --------------------------------------------------------------------------- #
# The detectors named actually exist
# --------------------------------------------------------------------------- #

_DOTTED = re.compile(r"\b((?:lab|roleplay)(?:\.[A-Za-z_]\w*)+)")


def _resolve(dotted: str) -> object:
    """Import as far as possible, then getattr the rest. Raises if it does not exist."""
    parts = dotted.split(".")
    module = None
    depth = 0
    for i in range(len(parts), 0, -1):
        try:
            module = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        depth = i
        break
    assert module is not None, dotted
    obj: object = module
    for attr in parts[depth:]:
        obj = getattr(obj, attr)
    return obj


def test_every_named_detector_resolves_to_real_code() -> None:
    """Documentation that rots is worse than none, so the registry is executable.

    Every `lab.*` or `roleplay.*` symbol mentioned in a detector's name, note or
    fallback must exist. Rename `PhraseContract` and this test fails, rather than
    the scorecard quietly citing a contract nobody can run.
    """
    found = 0
    for kpi in sc.KPIS:
        haystack = " ".join(
            [kpi.detector.name, kpi.detector.note, kpi.detector.fallback or ""]
        )
        for dotted in _DOTTED.findall(haystack):
            _resolve(dotted)
            found += 1
    assert found >= 15, "the scan found suspiciously few dotted references"


def test_the_contracts_the_research_told_us_to_reuse_are_the_ones_cited() -> None:
    """The five primitives `docs/_research/call_craft.md` names, all actually used.

    That file's recommendations are specific about reuse: `NoProgressContract` for
    the stall case, `NoReAskContract` for the block case,
    `FieldPropagationContract` for asked-then-ignored discovery, `PhraseContract`
    for position-based tests. If the scorecard stopped citing one of them, either
    the research or the scorecard moved and somebody should say which.
    """
    cited = " ".join(
        f"{k.detector.name} {k.detector.note} {k.detector.fallback or ''}" for k in sc.KPIS
    )
    for primitive in (
        "PhraseContract",
        "NoReAskContract",
        "NoProgressContract",
        "FieldPropagationContract",
        "ToolContract",
        "ArgPredicate",
    ):
        assert primitive in cited, primitive


# --------------------------------------------------------------------------- #
# It extends rubric_v1 rather than forking it
# --------------------------------------------------------------------------- #


def test_every_rubric_v1_criterion_has_a_successor_kpi() -> None:
    """The claim in the module docstring, made falsifiable.

    `roleplay.scorer.CRITERIA` is the live rubric the product ships. If a
    criterion has no successor here, this scorecard is a fork with a migration
    story nobody wrote.
    """
    assert set(sc.RUBRIC_V1_SUCCESSORS) == set(CRITERIA)
    known = {k.id for k in sc.KPIS}
    for criterion, successors in sc.RUBRIC_V1_SUCCESSORS.items():
        assert successors, criterion
        assert set(successors) <= known, criterion


def test_the_two_outright_fail_clauses_of_rubric_v1_became_gates() -> None:
    """rubric_v1 fails a session outright for a missing disclosure or for personal
    advice. Both must land on GATE rows, or the scorecard has silently softened
    two dispositive rules into criteria."""
    for criterion in ("mandatory_disclosure", "no_unlicensed_advice"):
        successors = [sc.by_id(i) for i in sc.RUBRIC_V1_SUCCESSORS[criterion]]
        assert successors
        assert all(k.is_gate for k in successors), criterion


# --------------------------------------------------------------------------- #
# The validator would actually catch a bad row
# --------------------------------------------------------------------------- #


def _good() -> sc.KPI:
    return sc.by_id("CS-1")


@pytest.mark.parametrize(
    "mutation, fragment",
    [
        ({"basis": (), "assumptions": ()}, "sourced or labelled an assumption"),
        ({"business_metric": "vibes"}, "closed vocabulary"),
        ({"denominator": "  "}, "declares no denominator"),
        ({"excludes": ""}, "declares no exclusions"),
        ({"gate_or_score": "GATE"}, "max_points == 0"),
        ({"detector": sc.Detector(kind="contract", name="", note="x")}, "declares no detector"),
        (
            {"detector": sc.Detector(kind="judge", name="j", note="n", requires_calibration=False)},
            "requires_calibration must be True exactly for judges",
        ),
    ],
)
def test_validate_rejects_an_indefensible_row(monkeypatch, mutation, fragment) -> None:
    """`_validate` runs at import, so nothing here proves it works. This does."""
    monkeypatch.setattr(sc, "KPIS", (replace(_good(), **mutation),) + sc.KPIS[1:])
    with pytest.raises(ValueError, match=fragment):
        sc._validate()


def test_validate_rejects_a_gate_that_ladders_to_a_growth_metric(monkeypatch) -> None:
    gate = replace(sc.by_id("CG-1"), business_metric="call_conversion")
    monkeypatch.setattr(sc, "KPIS", tuple(gate if k.id == "CG-1" else k for k in sc.KPIS))
    with pytest.raises(ValueError, match="must ladder to licence_to_operate"):
        sc._validate()


def test_validate_rejects_a_judge_gated_gate_with_no_fallback(monkeypatch) -> None:
    """The rule that keeps an uncalibrated gate from silently not running."""
    broken = replace(
        sc.by_id("DI-4"),
        detector=sc.Detector(kind="judge", name="j", note="n", requires_calibration=True),
    )
    monkeypatch.setattr(sc, "KPIS", tuple(broken if k.id == "DI-4" else k for k in sc.KPIS))
    with pytest.raises(ValueError, match="deterministic fallback"):
        sc._validate()


def test_validate_rejects_an_overstuffed_group(monkeypatch) -> None:
    extras = (replace(sc.by_id("CS-1"), id="CS-8"), replace(sc.by_id("CS-1"), id="CS-9"))
    monkeypatch.setattr(sc, "KPIS", sc.KPIS + extras)
    with pytest.raises(ValueError, match="design budget is 3-5"):
        sc._validate()


# --------------------------------------------------------------------------- #
# Scoring a session
# --------------------------------------------------------------------------- #


def _outcomes(
    *,
    points_per_scored: int | None = None,
    failed_gates: tuple[str, ...] = (),
    not_applicable: tuple[str, ...] = (),
) -> list[sc.KPIOutcome]:
    """A full set of outcomes: every KPI reported, which `score_session` requires.

    `points_per_scored=None` means full marks, which is the useful default here —
    most tests want a session that passes on points so that a gate failure is the
    only thing that can change the verdict.
    """
    out: list[sc.KPIOutcome] = []
    for kpi in sc.KPIS:
        if kpi.id in not_applicable:
            out.append(sc.KPIOutcome(kpi_id=kpi.id, applicable=False))
        elif kpi.is_scored:
            awarded = kpi.max_points if points_per_scored is None else min(points_per_scored, kpi.max_points)
            out.append(sc.KPIOutcome(kpi_id=kpi.id, points=awarded))
        elif kpi.is_gate:
            out.append(sc.KPIOutcome(kpi_id=kpi.id, gate_passed=kpi.id not in failed_gates))
        else:
            out.append(sc.KPIOutcome(kpi_id=kpi.id, evidence="WER 0.11; CMI 0.0; text control run present"))
    return out


def test_a_clean_session_passes_and_prints_both_figures() -> None:
    score = sc.score_session(_outcomes())
    assert score.verdict == "pass"
    assert score.points == score.points_available == sc.points_available()
    assert score.gates_applicable == len(sc.gates())
    assert score.gates_failed == ()
    line = score.summary_line()
    # Never a naked percentage: the fraction and the threshold travel with it.
    assert f"{score.points}/{score.points_available}" in line
    assert f"threshold {score.pass_points}/{score.points_available}" in line
    assert f"{score.gates_passed}/{score.gates_applicable} gates" in line


def test_a_gate_failure_fails_a_session_that_otherwise_totals_a_pass() -> None:
    """The headline invariant. A gate cannot be outscored.

    Full marks on every scored KPI, one missing required disclosure. rubric_v1
    already says a session fails outright whatever it totals if a required
    disclosure is missing; this is that clause, enforced by arithmetic that has no
    way to express the alternative.
    """
    clean = sc.score_session(_outcomes())
    assert clean.verdict == "pass"

    gated = sc.score_session(_outcomes(failed_gates=("CG-1",)))
    assert gated.points == clean.points, "the points total is untouched by the gate"
    assert gated.points >= gated.pass_points, "and it still clears the threshold"
    assert gated.verdict == "fail"
    assert gated.gates_failed == ("CG-1",)
    assert "CG-1" in gated.summary_line()


@pytest.mark.parametrize("gate_id", [k.id for k in sc.gates()])
def test_any_single_gate_failure_is_dispositive(gate_id: str) -> None:
    """Every gate, not just the disclosure one. A gate that only sometimes gates is
    a criterion with a strong name."""
    score = sc.score_session(_outcomes(failed_gates=(gate_id,)))
    assert score.verdict == "fail"
    assert score.gates_failed == (gate_id,)


def test_points_alone_can_still_fail_a_session_with_every_gate_passed() -> None:
    """The other direction: compliant and incompetent is a fail, not a pass.

    Zero on every scored KPI, every gate passed. A scorecard where compliance
    alone certifies somebody to sell is a compliance checklist wearing a coaching
    product's name.
    """
    score = sc.score_session(_outcomes(points_per_scored=0))
    assert score.gates_failed == ()
    assert score.points == 0
    assert score.verdict == "fail"


def test_the_threshold_is_the_stated_fraction_of_what_was_available() -> None:
    available = sc.points_available()
    score = sc.score_session(_outcomes())
    assert 0 < score.pass_points <= available
    # Rounded up, never down: at 70% of 53 the bar is 38, not 37.
    assert score.pass_points >= sc.PASS_FRACTION * available
    assert score.pass_points - 1 < sc.PASS_FRACTION * available


def test_a_not_applicable_kpi_leaves_both_numerator_and_denominator() -> None:
    """The denominator-safe rule, applied to the scorecard itself.

    An objection-handling KPI in a session with no objection must not score zero
    (that punishes the adviser for a scenario they did not choose) and must not
    score full (that hides the gap). It leaves the measurement, and the report
    prints the reduced denominator.
    """
    full = sc.score_session(_outcomes())
    reduced = sc.score_session(_outcomes(not_applicable=("OH-1",)))
    oh1 = sc.by_id("OH-1")
    assert reduced.points_available == full.points_available - oh1.max_points
    assert reduced.points == full.points - oh1.max_points
    assert reduced.pass_points < full.pass_points
    assert reduced.verdict == "pass"
    assert "OH-1" in reduced.not_applicable
    assert "n/a: 1" in reduced.summary_line()


def test_a_suppressed_call_survival_group_shrinks_the_denominator_rather_than_scoring_zero() -> None:
    """The vulnerability conflict, in arithmetic. See SCORECARD.md §1.

    When the correct behaviour is to stop the call, every call-survival KPI is
    inapplicable. Reporting 0/9 on call survival for a session where stopping was
    right would train precisely the wrong behaviour, so the group leaves the
    denominator and the compliance gate carries the verdict.
    """
    cs_ids = tuple(k.id for k in sc.by_group("CS"))
    score = sc.score_session(_outcomes(not_applicable=cs_ids))
    assert score.points_available == sc.points_available() - sum(
        sc.by_id(i).max_points for i in cs_ids
    )
    assert set(cs_ids) <= set(score.not_applicable)
    assert score.verdict == "pass"


def test_the_diagnostic_never_moves_the_verdict() -> None:
    """LL-4 is an instrument reading. It is mandatory to report and forbidden to score."""
    ll4 = sc.by_id("LL-4")
    assert ll4.gate_or_score == "DIAGNOSTIC"
    assert ll4.max_points == 0

    reported = sc.score_session(_outcomes())
    absent = sc.score_session(_outcomes(not_applicable=("LL-4",)))
    assert reported.points == absent.points
    assert reported.points_available == absent.points_available
    assert reported.verdict == absent.verdict == "pass"
    assert reported.gates_applicable == absent.gates_applicable


# --------------------------------------------------------------------------- #
# score_session refuses to guess
# --------------------------------------------------------------------------- #


def test_a_missing_outcome_is_an_error_not_a_zero() -> None:
    """A KPI that silently vanishes changes the denominator without appearing in
    the report — the same defect class as a naked percentage, by a quieter route."""
    partial = [o for o in _outcomes() if o.kpi_id != "CE-1"]
    with pytest.raises(ValueError, match="CE-1"):
        sc.score_session(partial)


def test_outcomes_must_match_their_row_type() -> None:
    good = _outcomes()

    def swap(kpi_id: str, outcome: sc.KPIOutcome) -> list[sc.KPIOutcome]:
        return [outcome if o.kpi_id == kpi_id else o for o in good]

    with pytest.raises(ValueError, match="is a SCORE; gate_passed must be None"):
        sc.score_session(swap("CS-1", sc.KPIOutcome("CS-1", points=2, gate_passed=True)))

    with pytest.raises(ValueError, match="points must be None"):
        sc.score_session(swap("CG-1", sc.KPIOutcome("CG-1", points=3, gate_passed=True)))

    with pytest.raises(ValueError, match="outside 0..2"):
        sc.score_session(swap("CS-1", sc.KPIOutcome("CS-1", points=99)))

    with pytest.raises(ValueError, match="reported no verdict"):
        sc.score_session(swap("CG-1", sc.KPIOutcome("CG-1")))

    with pytest.raises(ValueError, match="reported no points"):
        sc.score_session(swap("CS-1", sc.KPIOutcome("CS-1")))


def test_unknown_and_duplicate_outcomes_are_refused() -> None:
    with pytest.raises(KeyError, match="unknown KPI"):
        sc.score_session(_outcomes() + [sc.KPIOutcome("XX-9", points=1)])
    with pytest.raises(ValueError, match="two outcomes"):
        sc.score_session(_outcomes() + [sc.KPIOutcome("CS-1", points=1)])


def test_by_id_and_by_group_raise_on_a_typo() -> None:
    """A typo must not read as 'nothing required here'."""
    with pytest.raises(KeyError):
        sc.by_id("CS-99")
    with pytest.raises(KeyError):
        sc.by_group("ZZ")
