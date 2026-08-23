"""The advisory corpus: eighteen rows, seven KPI groups, four disclosure registers.

WHAT THIS FILE IS DEFENDING
---------------------------
The advisory rows make claims a booking scenario cannot make. Each one names the
KPI groups it grades and, where it is a divergence row, the register entry that
makes the same transcript pass in one regime and fail in another. Both of those
are *references*, and an unresolvable reference in an eval corpus does not crash
— it quietly grades nothing and reports green. So the tests here are almost
entirely reference-integrity tests:

*   a KPI id that no group defines is coverage that does not exist;
*   a register entry id that no register holds is a rule nobody wrote down;
*   a divergence row whose regimes all agree demonstrates nothing;
*   a register entry with no citation is the one thing this repo must not carry,
    because the whole argument is care with evidence.

The loader already refuses most of these at parse time. These tests assert that
it does, on the real corpus, so a future relaxation of the loader shows up as a
red test rather than as eighteen rows that still load.
"""

from __future__ import annotations

import pytest

from lab.trace.build import TraceBuilder
from roleplay.advisory import (
    ADVISORY_ROOT,
    ADVISORY_SUITE_MINIMUMS,
    ADVISORY_SUITES,
    KPI_IDS,
    REGIMES,
    REGISTER_KINDS,
    gate_groups,
    load_registers,
)
from roleplay.corpus import Corpus, iter_scenario_paths, load_scenario, validate_advisory_corpus
from roleplay.scorecard import GROUPS, KPIS

#: The corpus is deliberately, declaredly eighteen rows. A demo whose row count
#: drifts is a demo nobody reads all of, so the number is asserted rather than
#: described.
EXPECTED_ROWS = 18


@pytest.fixture(scope="module")
def advisory() -> Corpus:
    validation = validate_advisory_corpus()
    assert validation.ok, "\n".join(i.render() for i in validation.errors)
    return validation.corpus


# --------------------------------------------------------------------------- #
# It loads, and it is the corpus we think it is
# --------------------------------------------------------------------------- #


def test_the_whole_corpus_loads_with_the_existing_loader(advisory: Corpus) -> None:
    """Eighteen rows, through `roleplay.corpus`, with no second loader."""
    assert len(advisory) == EXPECTED_ROWS
    on_disk = {str(p) for p in iter_scenario_paths(ADVISORY_ROOT, suites=ADVISORY_SUITES)}
    assert {s.source for s in advisory} == on_disk
    assert len(on_disk) == EXPECTED_ROWS
    # registers/ and customers/ are data, not scenarios, and must never be parsed
    # as rows — the failure mode is a suite whose count is inflated by its own
    # reference tables.
    assert not any("registers" in path or "customers" in path for path in on_disk)


def test_suite_minimums_are_met_and_nothing_extra_crept_in(advisory: Corpus) -> None:
    counts = advisory.suite_counts()
    assert set(counts) == set(ADVISORY_SUITES)
    for suite, floor in ADVISORY_SUITE_MINIMUMS.items():
        assert counts[suite] >= floor, f"{suite}: {counts[suite]} < {floor}"
    assert sum(counts.values()) == EXPECTED_ROWS


def test_the_human_column_carries_both_labels(advisory: Corpus) -> None:
    """Neither rate is computed over a handful of items."""
    verdicts = advisory.human_verdict_counts()
    assert verdicts["pass"] >= 5, verdicts
    assert verdicts["fail"] >= 5, verdicts


def test_every_row_names_a_persona_that_exists(advisory: Corpus) -> None:
    for scenario in advisory:
        assert scenario.customer in advisory.profiles, scenario.id


# --------------------------------------------------------------------------- #
# The KPI ladder
# --------------------------------------------------------------------------- #


def test_every_kpi_id_referenced_by_a_row_exists(advisory: Corpus) -> None:
    """The assertion this corpus most needs.

    A row that grades `XX` grades nothing, and a coverage table built from the
    rows would report `XX: 1` next to six real groups without ever noticing.
    """
    for scenario in advisory:
        assert scenario.kpis, f"{scenario.id} grades no KPI group"
        unknown = [k for k in scenario.kpis if k not in KPI_IDS]
        assert not unknown, f"{scenario.id} references undefined KPI group(s) {unknown}"


def test_every_kpi_group_is_covered_by_at_least_one_row(advisory: Corpus) -> None:
    """The other direction, which is the one that catches a thin suite.

    Seven declared groups and six exercised is a proposal with a hole in it, and
    the hole is invisible from the row side.
    """
    counts = advisory.kpi_counts()
    assert set(counts) == set(KPI_IDS)
    uncovered = [kpi for kpi, n in counts.items() if n == 0]
    assert not uncovered, f"KPI groups defined but graded by no row: {uncovered}"


def test_the_corpus_and_the_scorecard_share_one_registry() -> None:
    """There is exactly one definition of the seven groups, and it is not here.

    The corpus validates `kpis` against `roleplay.scorecard.GROUPS`. If a second
    registry ever appears — in this module, in a report, in a document that got
    copied — the ids drift and every join between a row and a score silently
    matches nothing. This test is the tripwire for that.
    """
    assert KPI_IDS == frozenset(GROUPS)
    assert {k.group for k in KPIS} == set(GROUPS)


def test_compliance_is_the_only_group_that_is_gates_all_the_way_down() -> None:
    """Gates are counted, never averaged.

    Other groups carry a single gate KPI alongside scored ones and still have a
    number. CG has no number at all, and a reporting surface that averages it into
    a session score has invented one — which is the failure that makes a compliance
    breach look like a lost mark.
    """
    assert gate_groups() == ("CG",)
    assert all(k.is_gate and k.max_points == 0 for k in KPIS if k.group == "CG")


# --------------------------------------------------------------------------- #
# The registers
# --------------------------------------------------------------------------- #


def test_every_regime_has_a_register_and_every_entry_is_cited() -> None:
    registers = load_registers()
    assert set(registers) == set(REGIMES)
    for regime, register in registers.items():
        assert register.entries, regime
        for entry in register.entries.values():
            assert entry.kind in REGISTER_KINDS
            assert entry.source.strip()
            assert entry.research.strip()
            assert entry.id.startswith(f"{regime}-")


def test_the_registers_carry_both_drafting_traditions() -> None:
    """regulators.md §8: the four regimes split into form-of-words rules and
    substance rules, and an instrument that assumes one of them is wrong half the
    time. A register holding only one kind cannot express the split, so the
    presence of both is a property of the data worth pinning."""
    registers = load_registers()
    kinds = {e.kind for r in registers.values() for e in r.entries.values()}
    assert {"verbatim", "substance", "prescribed-unit"} <= kinds, kinds
    # Recorded absence, so a cross-market checker cannot invent a requirement in
    # the regime that does not impose it (§6 D2).
    assert registers["reg-bi"].of_kind("not-required"), "Reg BI's absences are the point"


# --------------------------------------------------------------------------- #
# The divergence rows, which are the corpus's whole argument
# --------------------------------------------------------------------------- #


def test_divergence_rows_disagree_and_say_why(advisory: Corpus) -> None:
    rows = advisory.suite("divergence")
    assert len(rows) == ADVISORY_SUITE_MINIMUMS["divergence"]
    for scenario in rows:
        spec = scenario.divergence
        assert spec is not None, f"{scenario.id} is in the divergence suite with no split"
        assert len({r.verdict for r in spec.regimes}) == 2, scenario.id
        assert len(spec.regimes) >= 2, scenario.id
        # The row's own verdict is one of the listed ones, and it is the one the
        # human column claims. The loader enforces this; the test says so on the
        # real data, because the two columns silently drifting apart is how a
        # hand-labelled set stops being a reference.
        own = {r.regime: r for r in spec.regimes}[scenario.regime]
        assert own.verdict == scenario.expectation.human_verdict, scenario.id


def test_all_four_regimes_appear_as_a_primary_verdict(advisory: Corpus) -> None:
    """A suite that only ever grades under one regulator has demonstrated
    portability of its vocabulary and not of its logic."""
    graded = {s.regime for s in advisory if s.regime}
    assert graded == set(REGIMES), sorted(graded)


def test_a_divergence_row_cannot_cite_an_entry_that_does_not_exist(tmp_path) -> None:
    """The reference-integrity check, exercised on a deliberately broken row.

    Asserted here rather than trusted, because this is the check that stops the
    intellectual core of the pack from being decorative: without it, `rule` is
    prose and the regime split rests on nothing.
    """
    source = next(iter_scenario_paths(ADVISORY_ROOT, suites=ADVISORY_SUITES))
    broken = tmp_path / "divergence"
    broken.mkdir()
    target = broken / source.name
    target.write_text(
        source.read_text(encoding="utf-8").replace(
            "register_entry: fca-adviser-charging-only",
            "register_entry: fca-no-such-requirement",
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception) as caught:
        load_scenario(target, suites=ADVISORY_SUITES)
    assert "fca-no-such-requirement" in str(caught.value)


# --------------------------------------------------------------------------- #
# The row-level discipline the pack claims for itself
# --------------------------------------------------------------------------- #


def test_every_row_says_why_a_simpler_row_could_not_test_it(advisory: Corpus) -> None:
    """Eighteen rows is a budget, and this is how it is defended.

    The sentence is not decoration: it is the author stating what the row buys
    over the cheapest thing that looks like it. A row that cannot answer the
    question is a row that should not be in a corpus this size.
    """
    missing = [s.id for s in advisory if "Why not a simpler row:" not in s.notes]
    assert not missing, missing


def test_near_miss_rows_all_fail(advisory: Corpus) -> None:
    """The whole family is defined by being wrongly passed elsewhere. A near-miss
    row labelled pass has lost the property that made it worth writing."""
    for scenario in advisory.suite("nearmiss"):
        assert scenario.expectation.human_verdict == "fail", scenario.id
        assert scenario.expected_failure is not None, scenario.id


def test_the_clause_triad_is_a_controlled_comparison(advisory: Corpus) -> None:
    """One clause, one customer, one regime, three handlings — so a verdict
    difference is attributable to the clause turn and to nothing else."""
    rows = {s.id: s for s in advisory.suite("clause")}
    assert set(rows) == {
        "clause-surrender-value-explained",
        "clause-surrender-value-recited",
        "clause-surrender-value-understated",
    }
    assert len({s.customer for s in rows.values()}) == 1
    assert len({s.regime for s in rows.values()}) == 1
    shared = {s.id: s.trainee.turns[:3] for s in rows.values()}
    assert len({turns for turns in shared.values()}) == 1, "the shared prefix has drifted"
    assert rows["clause-surrender-value-explained"].expectation.human_verdict == "pass"
    assert rows["clause-surrender-value-understated"].expectation.human_verdict == "fail"


def test_the_survival_suite_can_pass_a_call_that_ends(advisory: Corpus) -> None:
    """A suite that only rewards persistence teaches persistence. If the graceful
    exit is not labelled pass, the corpus is arguing against itself."""
    exit_row = advisory.by_id("survival-graceful-exit-is-the-pass")
    assert exit_row.expectation.human_verdict == "pass"
    assert "CS" in exit_row.kpis


# --------------------------------------------------------------------------- #
# Can the declared checks actually fail? A check that cannot fail is worse than
# no check, because it reports green forever and nobody looks at it again.
#
# Everything above this line is reference integrity: the row says what it means
# to say. These tests are the other half — that what it says is *executable*, and
# that it discriminates. For each row we build the exact behaviour the row's own
# `expected_failure` predicts a naive product will produce (the disclosure is
# present, so nothing a presence-based check looks for is missing; no compliance
# flag is raised and the session is scored a pass) and require the row's declared
# contract to go red on it. Then we build the compliant behaviour and require the
# same contract to go green, because a contract that fails everything discriminates
# no better than one that passes everything.
# --------------------------------------------------------------------------- #


def _run(scenario, *, disclosures: int, flag: bool, verdict: str, extra_turns=()):
    """A trace carrying only the events these contracts read."""
    builder = TraceBuilder(scenario_id=scenario.id, adapter="advisory-discrimination")
    builder.session_start()
    for text in tuple(scenario.trainee.turns) + tuple(extra_turns):
        builder.caller_utterance(text)
    for i in range(disclosures):
        builder.tool_call("record_disclosure", {"code": f"c{i}"}, call_id=f"rd{i}")
        builder.tool_result("record_disclosure", None, call_id=f"rd{i}", ok=True)
    # Any other tool the row expects, so the only thing under test is the
    # compliance flag and the verdict rather than an unrelated missing clause.
    for name in scenario.tools.expected:
        if name in {"record_disclosure", "flag_compliance_risk", "score_session"}:
            continue
        builder.tool_call(name, {"key": "not_now"}, call_id=name)
        builder.tool_result(name, None, call_id=name, ok=True)
    if flag:
        builder.tool_call("flag_compliance_risk", {"reason": "understated"}, call_id="fl")
        builder.tool_result("flag_compliance_risk", None, call_id="fl", ok=True)
    builder.tool_call("score_session", {"verdict": verdict, "closing": 4}, call_id="ss")
    builder.tool_result("score_session", None, call_id="ss", ok=True)
    builder.session_end(reason="scored")
    return builder.build()


def _tool_contract(scenario):
    for contract in scenario.contracts():
        if getattr(contract, "name", "") == "tools":
            return contract
    raise AssertionError(f"{scenario.id} declares no tool contract")


def _named(scenario, name):
    for contract in scenario.contracts():
        if getattr(contract, "name", "") == name:
            return contract
    raise AssertionError(f"{scenario.id} declares no {name!r} contract")


#: The three near-miss rows plus the understated clause: the four rows in the pack
#: whose whole point is that a keyword matcher passes them.
FALSE_PASS_ROWS = (
    "nearmiss-charges-disclosed-after-the-ask",
    "nearmiss-restricted-advice-buried-in-a-long-turn",
    "nearmiss-warning-addressed-to-the-partner",
    "clause-surrender-value-understated",
)


@pytest.mark.parametrize("scenario_id", FALSE_PASS_ROWS)
def test_the_declared_check_goes_red_on_the_false_pass(scenario_id: str, advisory: Corpus) -> None:
    """The bad run is the one the row predicts: disclosed, unflagged, scored pass."""
    scenario = advisory.by_id(scenario_id)
    result = _tool_contract(scenario).check(
        _run(scenario, disclosures=3, flag=False, verdict="pass")
    )
    assert not result.passed, f"{scenario_id}: the false pass was not caught -- {result.detail}"
    assert result.applicable
    # And it must fail for the stated reason rather than incidentally.
    assert "flag_compliance_risk" in result.detail, result.detail


@pytest.mark.parametrize("scenario_id", FALSE_PASS_ROWS)
def test_the_same_check_goes_green_on_the_compliant_run(
    scenario_id: str, advisory: Corpus
) -> None:
    """Otherwise the row above proves only that the contract fails everything."""
    scenario = advisory.by_id(scenario_id)
    result = _tool_contract(scenario).check(
        _run(scenario, disclosures=3, flag=True, verdict="fail")
    )
    assert result.passed, f"{scenario_id}: the compliant run was refused -- {result.detail}"


def test_the_graceful_exit_passes_and_still_discriminates(advisory: Corpus) -> None:
    """The row that must pass, and the two ways it must still be able to fail.

    A suite of failures teaches a scorer that severity is the answer, so this row
    exists to pass. But a row that *only* passes is not a check, so both forbidden
    behaviours are exercised: the fourth attempt after a decline, and a compliance
    flag raised on a clean call.
    """
    scenario = advisory.by_id("survival-graceful-exit-is-the-pass")
    clean = _run(scenario, disclosures=0, flag=False, verdict="pass")
    for name in ("tools", "trainee-phrases"):
        result = _named(scenario, name).check(clean)
        assert result.passed, f"the graceful exit must pass {name} -- {result.detail}"

    # The fourth attempt the row forbids, in the adviser's own words.
    pushy = _run(
        scenario,
        disclosures=0,
        flag=False,
        verdict="pass",
        extra_turns=("Before you go, one last thing - can I send the illustration anyway?",),
    )
    phrases = _named(scenario, "trainee-phrases").check(pushy)
    assert not phrases.passed, "a fourth attempt after a decline must not pass"

    # A compliance flag on a call where nothing went wrong is also a defect.
    flagged = _run(scenario, disclosures=0, flag=True, verdict="pass")
    tools = _named(scenario, "tools").check(flagged)
    assert not tools.passed, "a forbidden compliance flag must not pass"
