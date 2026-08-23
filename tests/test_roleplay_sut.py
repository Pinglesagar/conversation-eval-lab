"""The roleplay system under test: register, persona, scorer, adapter.

Tests the *product*, not the checks. `test_roleplay_checks.py` covers the
contracts, and `test_roleplay_evaluation.py` covers consistency and calibration.

Every seeded defect gets a test that proves it reproduces AND a test that proves
the neighbouring behaviour is intact, because a defect with no control beside it is
a symptom rather than a boundary.
"""

from __future__ import annotations

import pytest

from lab.clock import FakeClock
from lab.trace.schema import EventKind

from roleplay.persona import CustomerPersona, classify_trainee_turn, load_profiles
from roleplay.register import (
    DisclosureRegister,
    JURISDICTIONS,
    REGISTERED_PHRASINGS,
    normalise,
    required_codes,
)
from roleplay.runtime import TOOL_NAMES, RoleplayCoach
from roleplay.scorer import (
    CRITERIA,
    MAX_PER_CRITERION,
    PASS_TOTAL,
    RubricScorer,
    SessionView,
    session_view,
)

from tests.roleplay_fixtures import coach, corpus, profiles, script  # noqa: F401


# --------------------------------------------------------------------------- #
# The disclosure register
# --------------------------------------------------------------------------- #


def test_every_jurisdiction_requires_only_known_codes() -> None:
    """A requirement naming a code that does not exist can never be satisfied."""
    from roleplay.register import DISCLOSURE_CODES

    for market, codes in JURISDICTIONS.items():
        unknown = [c for c in codes if c not in DISCLOSURE_CODES]
        assert not unknown, f"{market} requires unknown code(s) {unknown}"


def test_every_required_code_has_phrasings_in_every_language() -> None:
    """A code with no registered phrasing in a language is unsatisfiable there.

    This is the corpus rule "every assertion must be able to fire", applied to the
    register: a Spanish session could not discharge a requirement whose only
    registered wording is English, and the trainee would be marked down for a
    sentence there is no way to say.
    """
    needed = {code for codes in JURISDICTIONS.values() for code in codes}
    for language, table in REGISTERED_PHRASINGS.items():
        missing = [code for code in needed if not table.get(code)]
        assert not missing, f"{language} has no phrasings for {missing}"


def test_unknown_jurisdiction_raises_rather_than_returning_nothing() -> None:
    """An empty requirement set reads as full compliance; a typo must not."""
    with pytest.raises(KeyError):
        required_codes("no-such-market")


def test_register_records_the_first_matching_phrasing_only_once() -> None:
    register = DisclosureRegister(jurisdiction="eu-retail", language="en")
    first = register.observe("Your capital at risk is real here.", turn=1)
    second = register.observe("Again, capital at risk.", turn=2)
    assert [r.code for r in first] == ["capital_at_risk"]
    assert second == []
    assert register.satisfied_codes() == ("capital_at_risk",)
    assert register.missing_codes() == ("past_performance", "fees_and_charges")
    assert not register.complete


def test_register_is_accent_insensitive() -> None:
    """A disclosure must not depend on the trainee's keyboard."""
    register = DisclosureRegister(jurisdiction="eu-retail", language="es")
    created = register.observe("Puede recuperar menos de lo que invierté.", turn=1)
    assert [r.code for r in created] == ["capital_at_risk"]


def test_register_summary_carries_a_denominator() -> None:
    register = DisclosureRegister(jurisdiction="apac-retail")
    assert "0/4" in register.summary()
    assert "missing" in register.summary()


def test_normalise_collapses_punctuation_and_case() -> None:
    assert normalise("Capital  AT-risk!") == "capital at risk"


# --------------------------------------------------------------------------- #
# Turn classification and the persona
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What would you want this money to do?", "open_probe"),
        ("How much would you need to reach quickly?", "open_probe"),
        ("Is that right?", "closed_question"),
        ("The fund holds sixty per cent equities.", "pitch"),
        ("Shall we get the paperwork started?", "close_attempt"),
        ("You should move the whole lot into this.", "advice"),
        ("Guaranteed returns of six per cent.", "advice"),
        ("There is no real risk here.", "advice"),
        ("You should move it all in, shall we get started?", "advice"),
    ],
)
def test_turn_classification(text: str, expected: str) -> None:
    """Advice outranks a close: the compliance consequence is the bigger one."""
    assert classify_trainee_turn(text) == expected


def test_persona_reveals_a_concern_only_when_probed(profiles) -> None:
    persona = CustomerPersona(profile=profiles["cautious_saver"])
    pitched = persona.respond("The fund holds sixty per cent equities.")
    assert pitched.revealed == ()
    probed = persona.respond("What would you want this money to do for you?")
    assert [c.key for c in probed.revealed] == ["school_fees"]


def test_terse_persona_needs_two_probes(profiles) -> None:
    persona = CustomerPersona(profile=profiles["reluctant_minimal"])
    assert persona.respond("What matters most to you here?").revealed == ()
    second = persona.respond("How would you feel about some ups and downs?")
    assert [c.key for c in second.revealed] == ["redundancy"]
    assert second.text == "Might be made redundant. That is it."


def test_assertive_persona_reraises_an_unhandled_objection(profiles) -> None:
    persona = CustomerPersona(profile=profiles["aggressive_challenger"])
    persona.respond("The fund is a balanced growth fund.")  # raises fees
    persona.respond("It rebalances quarterly.")  # raises whose_interest
    third = persona.respond("It has a long track record.")
    # The bank is empty, so the customer returns to the oldest unhandled item
    # rather than moving on. That ordering is what keeps an ignored objection
    # visible in the ledger for as long as it stays ignored.
    assert [o.key for o in third.raised] == ["fees"]
    assert "not answered" in third.text


def test_persona_is_a_pure_function_of_profile_and_turns(profiles) -> None:
    """Two personas fed the same turns must agree, or nothing downstream is stable."""
    turns = [
        "What would you want this money to do?",
        "The fund is a balanced growth fund.",
        "That is a fair point about last year; it is diversified.",
    ]
    first = CustomerPersona(profile=profiles["cautious_saver"])
    second = CustomerPersona(profile=profiles["cautious_saver"])
    assert [first.respond(t).text for t in turns] == [second.respond(t).text for t in turns]


def test_a_profile_objection_must_declare_how_it_can_be_handled(tmp_path) -> None:
    """Otherwise the objection-handling score is pinned at zero for that profile."""
    path = tmp_path / "broken.yaml"
    path.write_text(
        "key: broken\ndisplay_name: X\nsituation: Y\n"
        "objections:\n  - key: a\n    topic: t\n    says: s\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="handled_by"):
        load_profiles(tmp_path)


# --------------------------------------------------------------------------- #
# The adapter
# --------------------------------------------------------------------------- #


def test_a_session_produces_an_ordered_trace_with_both_stages(coach, script) -> None:
    result = coach.run(**script("exemplary"))
    trace = result.trace
    assert trace.is_ordered()
    assert trace.unknown_kinds() == set()
    assert trace.first(EventKind.SESSION_START) is not None
    assert trace.last(EventKind.SESSION_END).get("reason") == "scored"
    # Stage two is inside the same session, so the boundary is assertable.
    assert trace.handoff_pairs() == [("CustomerPersona", "Scorer")]
    assert "score_session" in trace.tool_names()


def test_every_tool_call_has_a_correlated_result(coach, script) -> None:
    trace = coach.run(**script("exemplary")).trace
    calls = {
        e.get("call_id")
        for e in trace.events_of_kind(EventKind.TOOL_CALL)
    }
    results = {
        e.get("call_id")
        for e in trace.events_of_kind(EventKind.TOOL_RESULT)
    }
    assert calls == results
    assert len(calls) == len(trace.tool_names())


def test_only_declared_tools_are_ever_called(coach, script) -> None:
    for name in ("exemplary", "advice", "spanish"):
        trace = coach.run(**script(name)).trace
        assert set(trace.tool_names()) <= TOOL_NAMES


def test_conversation_only_traces_contain_no_score(coach, script) -> None:
    """The calibration set must not hand the instrument its own answer."""
    conversation = coach.converse(**script("exemplary"))
    assert "score_session" not in conversation.trace.tool_names()
    assert conversation.trace.last(EventKind.SESSION_END).get("reason") == "roleplay_ended"


def test_converse_is_independent_of_scoring_state(script) -> None:
    """Stage one must not move when the service has graded other sessions.

    Everything after `session_start` must be byte-identical, and `session_start`
    itself must *not* be, because it records how warm the scoring service was.
    Recording that is the point: a trace whose provenance omits the state of the
    thing that graded it cannot be used to reopen a disputed grade.
    """
    warm = RoleplayCoach(scorer=RubricScorer())
    cold = RoleplayCoach(scorer=RubricScorer())
    for _ in range(4):
        warm.run(**script("exemplary"))
    a = warm.converse(**script("advice"), session_id="fixed").trace
    b = cold.converse(**script("advice"), session_id="fixed").trace

    assert [e.model_dump() for e in a.events[1:]] == [e.model_dump() for e in b.events[1:]]
    assert a.events[0].get("scorer_cohort_size") == 4
    assert b.events[0].get("scorer_cohort_size") == 0
    assert a.events[0].get("scorer_adjustment") == -4
    assert b.events[0].get("scorer_adjustment") == 0


def test_timestamps_come_from_the_injected_clock(script) -> None:
    """Latency is produced by the system, not asserted by the harness."""
    clock = FakeClock()
    result = RoleplayCoach(scorer=RubricScorer()).run(**script("exemplary"), clock=clock)
    assert result.trace.duration() > 0.0
    assert result.trace.duration() == pytest.approx(clock.now())
    # The scoring pass is the slowest single step in the session.
    assert clock.now() > RoleplayCoach(scorer=RubricScorer()).latency.scoring_s


def test_an_empty_script_is_refused_rather_than_scored(coach, profiles) -> None:
    with pytest.raises(ValueError, match="no trainee turns"):
        coach.run(
            scenario_id="empty", trainee_turns=[], profile=profiles["cautious_saver"]
        )


def test_run_refuses_both_a_coach_and_a_scorer(profiles) -> None:
    from roleplay.runtime import run_roleplay

    with pytest.raises(ValueError, match="not both"):
        run_roleplay(
            scenario_id="x",
            trainee_turns=["What would you want?"],
            profile=profiles["cautious_saver"],
            coach=RoleplayCoach(scorer=RubricScorer()),
            scorer=RubricScorer(),
        )


# --------------------------------------------------------------------------- #
# The score card
# --------------------------------------------------------------------------- #


def test_the_score_card_is_recomputable_from_the_trace_alone(coach, script) -> None:
    """The repo's central invariant, restated for this domain."""
    result = coach.run(**script("exemplary"))
    again = RubricScorer().score(session_view(result.trace))
    assert again.criteria == result.card.criteria
    assert again.raw_total == result.card.raw_total


def test_score_card_arithmetic_is_internally_consistent(coach, script) -> None:
    card = coach.run(**script("exemplary")).card
    assert set(card.criteria) == set(CRITERIA)
    assert card.raw_total == sum(card.criteria.values())
    assert card.max_total == MAX_PER_CRITERION * len(CRITERIA)
    assert card.total == max(0, min(card.max_total, card.raw_total + card.adjustment))
    assert card.passed == (card.total >= PASS_TOTAL)
    assert card.percent == pytest.approx(100.0 * card.total / card.max_total, abs=0.05)


def test_the_view_holds_evidence_the_scorer_does_not_read(coach, script) -> None:
    """DEFECT-3, stated at the type level: the input was there and was ignored."""
    result = coach.run(**script("advice"))
    view = session_view(result.trace)
    assert view.compliance_flags, "the in-session flagger should have fired"
    assert result.card.claims["unlicensed_advice_detected"] is False


# --------------------------------------------------------------------------- #
# DEFECT-1: the cohort curve
# --------------------------------------------------------------------------- #


def test_identical_input_scores_differently_on_a_warm_service(script) -> None:
    scorer = RubricScorer()
    coach = RoleplayCoach(scorer=scorer)
    totals = [coach.run(**script("consistency")).card.total for _ in range(5)]
    assert totals == [16, 15, 14, 13, 12]
    assert len({t >= PASS_TOTAL for t in totals}) == 2, "the verdict must flip"


def test_identical_input_is_stable_on_a_cold_service(script) -> None:
    """The control. Without it the finding is 'scores move', which localises nothing."""
    totals = [
        RoleplayCoach(scorer=RubricScorer()).run(**script("consistency")).card.total
        for _ in range(5)
    ]
    assert totals == [16, 16, 16, 16, 16]


def test_reset_restores_the_cold_behaviour(script) -> None:
    """The one-line fix, exercised so the answer key's claim is checkable."""
    scorer = RubricScorer()
    coach = RoleplayCoach(scorer=scorer)
    totals = []
    for _ in range(5):
        totals.append(coach.run(**script("consistency")).card.total)
        scorer.reset()
    assert totals == [16, 16, 16, 16, 16]


def test_the_curve_is_bounded(script) -> None:
    scorer = RubricScorer()
    coach = RoleplayCoach(scorer=scorer)
    for _ in range(30):
        coach.run(**script("consistency"))
    assert abs(scorer.adjustment) <= scorer.curve_limit


# --------------------------------------------------------------------------- #
# DEFECT-2: templated feedback
# --------------------------------------------------------------------------- #


def test_high_discovery_feedback_quotes_the_template_exemplar(coach, script) -> None:
    card = coach.run(**script("terse")).card
    assert card.criteria["discovery"] >= 3
    assert '"what would you want this money to be doing for you in ten years?"' in card.feedback


def test_low_objection_feedback_names_fees_whatever_was_raised(coach, script) -> None:
    result = coach.run(**script("unanswered"))
    assert result.card.criteria["objection_handling"] <= 1
    assert "fee objection" in result.card.feedback
    topics = {
        e.get("args", {}).get("topic")
        for e in result.trace.events_of_kind(EventKind.TOOL_CALL)
        if e.get("name") == "raise_objection"
    }
    assert not any("fee" in str(t) for t in topics)


def test_clean_sessions_get_the_negative_advice_sentence(coach, script) -> None:
    """The two advice sentences must be distinguishable, or every clean row is red."""
    clean = coach.run(**script("exemplary")).card.feedback
    assert "Nothing you said crossed into personal advice." in clean
    assert "You crossed into personal advice." not in clean


# --------------------------------------------------------------------------- #
# DEFECT-3: vocabulary in place of the ledger
# --------------------------------------------------------------------------- #


def test_reassurance_scores_the_disclosure_criterion(coach, script) -> None:
    """The sentence that denies the risk satisfies the criterion that requires it."""
    result = coach.run(**script("reassurance"))
    assert result.register.satisfied_codes() == ()
    assert result.card.criteria["mandatory_disclosure"] >= 3
    assert result.card.claims["mandatory_disclosure_given"] is True


def test_absence_and_inversion_score_identically(coach, script) -> None:
    """A keyword cannot tell a warning from its negation; both contain the keyword."""
    absent = coach.run(**script("missing")).card.criteria["mandatory_disclosure"]
    inverted = RoleplayCoach(scorer=RubricScorer()).run(
        **script("reassurance")
    ).card.criteria["mandatory_disclosure"]
    assert absent == inverted


def test_the_advice_blocklist_catches_exactly_what_it_lists(coach, script) -> None:
    """The control for DEFECT-3: the criterion works, its coverage is two phrasings."""
    caught = coach.run(**script("guaranteed")).card
    missed = RoleplayCoach(scorer=RubricScorer()).run(**script("advice")).card
    assert caught.criteria["no_unlicensed_advice"] == 0
    assert missed.criteria["no_unlicensed_advice"] == MAX_PER_CRITERION


def test_the_english_keyword_list_under_credits_a_spanish_session(coach, script) -> None:
    """DEFECT-3's second symptom, and it fails in the opposite direction."""
    result = coach.run(**script("spanish"))
    assert result.register.complete, "the register handles Spanish correctly"
    assert result.card.criteria["mandatory_disclosure"] == 0
    assert result.card.verdict == "fail"


def test_criteria_computed_from_ledgers_are_correct(coach, script) -> None:
    """Objection handling reads the ledger and gets the right answer.

    The point of this test is the contrast: the same file does the same job
    properly one method away, so the finding is about a mechanism and not about
    the scorer being generally careless.
    """
    handled = coach.run(**script("exemplary")).card
    ignored = RoleplayCoach(scorer=RubricScorer()).run(**script("unanswered")).card
    assert handled.criteria["objection_handling"] == MAX_PER_CRITERION
    assert ignored.criteria["objection_handling"] == 0


def test_a_reraised_objection_is_counted_once(profiles) -> None:
    """Otherwise an insistent customer lowers the score the trainee earned."""
    view = SessionView(
        objections_raised=({"key": "fees"}, {"key": "fees"}, {"key": "trust"}),
        objections_resolved=({"key": "fees"},),
    )
    assert RubricScorer()._objection_handling(view) == 2


def test_no_objections_raised_is_not_full_marks() -> None:
    """A criterion with nothing to assess must not be a free four."""
    assert RubricScorer()._objection_handling(SessionView()) == 3
