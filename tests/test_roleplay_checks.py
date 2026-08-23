"""The two roleplay contracts, both directions, plus the corpus schema.

Every contract here has a test proving it fires on a broken trace *and* a test
proving it stays quiet on a healthy one. A check that never passes is as useless
as one that never fails, and it is the more expensive of the two, because someone
eventually deletes the whole suite to stop the noise.
"""

from __future__ import annotations

import pytest
import yaml

from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace

from roleplay.contracts import (
    DEFAULT_SCORE_CLAIMS,
    DEFAULT_TOPIC_CLAIMS,
    FeedbackGroundednessContract,
    ScoreClaim,
    ScoreClaimContract,
    TopicClaim,
)
from roleplay.corpus import (
    CONTRACT_NAMES,
    SUITE_MINIMUMS,
    SUITES,
    TAG_VOCABULARY,
    Corpus,
    iter_scenario_paths,
    load_scenario,
    validate_corpus,
)
from roleplay.runtime import RoleplayCoach, TOOL_NAMES
from roleplay.scorer import CUSTOMER_AGENT, SCORER_AGENT, RubricScorer

from tests.roleplay_fixtures import ALIASES, coach, corpus, profiles, script  # noqa: F401


# --------------------------------------------------------------------------- #
# Hand-built traces, so a contract can be tested without the product
# --------------------------------------------------------------------------- #


def _trace(
    *,
    trainee: tuple[str, ...] = (),
    customer: tuple[str, ...] = (),
    tools: tuple[tuple[str, dict], ...] = (),
    feedback: str | None = None,
) -> Trace:
    """A minimal trace with exactly the events a contract needs to see.

    Built by hand rather than by running the product, on purpose: a contract test
    that goes through the adapter is also testing the adapter, and when it fails
    you do not know which one broke.
    """
    builder = TraceBuilder(scenario_id="unit", adapter="unit")
    builder.session_start()
    for text in trainee:
        builder.caller_utterance(text)
    for text in customer:
        builder.agent_utterance(text, agent=CUSTOMER_AGENT)
    for name, args in tools:
        builder.tool_call(name, args, call_id=name)
        builder.tool_result(name, None, call_id=name, ok=True)
    if feedback is not None:
        builder.agent_utterance(feedback, agent=SCORER_AGENT)
    builder.session_end(reason="scored")
    return builder.build()


_CARD_CLEAN = {
    "verdict": "pass",
    "mandatory_disclosure_given": True,
    "unlicensed_advice_detected": False,
}


# --------------------------------------------------------------------------- #
# FeedbackGroundednessContract
# --------------------------------------------------------------------------- #


def test_a_grounded_quote_passes() -> None:
    trace = _trace(
        trainee=("What would you want this money to do for you in ten years?",),
        feedback='You opened well - asking "what would you want this money to do for you '
        'in ten years?" gave you the horizon.',
    )
    result = FeedbackGroundednessContract().check(trace)
    assert result.passed
    assert result.applicable
    assert "1/1" in result.detail


def test_an_ungrounded_quote_fails_and_quotes_the_span() -> None:
    trace = _trace(
        trainee=("What would you like your savings to do over the next five years?",),
        feedback='You opened well - asking "what would you want this money to do for you '
        'in ten years?" gave you the horizon.',
    )
    result = FeedbackGroundednessContract().check(trace)
    assert not result.passed
    assert "quoted span never said" in result.detail
    assert result.evidence and "ten years" in result.evidence[0].quote


def test_a_quote_from_the_customer_is_grounded() -> None:
    """The feedback may quote either side of the roleplay, but not itself."""
    trace = _trace(
        customer=("I read that funds like this one lost money last year, honestly.",),
        feedback='The customer said "funds like this one lost money last year" and you moved on.',
    )
    assert FeedbackGroundednessContract().check(trace).passed


def test_the_feedback_cannot_ground_itself() -> None:
    """Otherwise any page that quoted its own prose would pass."""
    trace = _trace(
        trainee=("The fund is a balanced growth fund.",),
        feedback='You said "something entirely different from the transcript here".',
    )
    assert not FeedbackGroundednessContract().check(trace).passed


def test_short_quoted_fragments_are_ignored() -> None:
    """Terminology in quotes is not attribution, and flagging it is how a
    groundedness check earns a reputation for noise."""
    trace = _trace(trainee=("Nothing relevant.",), feedback='Watch the "ongoing charge".')
    result = FeedbackGroundednessContract().check(trace)
    assert result.passed and not result.applicable


def test_quote_matching_survives_punctuation_and_case() -> None:
    trace = _trace(
        trainee=("What would you WANT this money -- to be doing for you?",),
        feedback='You asked "what would you want this money to be doing for you?" first.',
    )
    assert FeedbackGroundednessContract().check(trace).passed


def test_a_topic_claim_grounded_in_the_objection_ledger_passes() -> None:
    trace = _trace(
        tools=(("raise_objection", {"key": "fees", "topic": "charges"}),),
        feedback="You left the fee objection unanswered.",
    )
    assert FeedbackGroundednessContract().check(trace).passed


def test_a_topic_claim_absent_from_the_ledger_fails_and_lists_it() -> None:
    trace = _trace(
        tools=(("raise_objection", {"key": "lock_in", "topic": "access to the money"}),),
        feedback="You left the fee objection unanswered.",
    )
    result = FeedbackGroundednessContract().check(trace)
    assert not result.passed
    assert "fee objection" in result.detail
    assert "access to the money" in (result.evidence[0].note or "")


def test_school_fees_do_not_ground_a_fee_objection() -> None:
    """The precision case that forced the ledger version of this claim.

    Grounding "the customer raised cost" in the customer's words accepts any turn
    containing "fee", and this domain's customers mention school fees while
    worrying about something else. The transcript-grounded version passed a
    fabricated claim on two corpus rows.
    """
    trace = _trace(
        customer=("The thing on my mind is school fees in three years.",),
        tools=(("raise_objection", {"key": "lock_in", "topic": "access to the money"}),),
        feedback="You left the fee objection unanswered.",
    )
    assert not FeedbackGroundednessContract().check(trace).passed


def test_the_negative_advice_sentence_does_not_fire_the_advice_claim() -> None:
    """"Nothing you said crossed into personal advice" must not read as a claim."""
    trace = _trace(trainee=("The fund is balanced.",), feedback="Nothing you said crossed into personal advice.")
    result = FeedbackGroundednessContract().check(trace)
    assert result.passed and not result.applicable


def test_the_positive_advice_sentence_needs_grounding() -> None:
    ungrounded = _trace(
        trainee=("The fund is balanced.",), feedback="You crossed into personal advice."
    )
    grounded = _trace(
        trainee=("You should move the whole lot into this.",),
        feedback="You crossed into personal advice.",
    )
    assert not FeedbackGroundednessContract().check(ungrounded).passed
    assert FeedbackGroundednessContract().check(grounded).passed


def test_no_feedback_is_inapplicable_not_a_pass() -> None:
    result = FeedbackGroundednessContract().check(_trace(trainee=("Hello.",)))
    assert result.passed
    assert not result.applicable
    assert "no scorer feedback" in result.detail


def test_topic_claim_rejects_an_unknown_grounding_target() -> None:
    with pytest.raises(ValueError, match="where must be one of"):
        TopicClaim(label="x", says=(r"x",), grounded_in=(r"y",), where="nowhere")


def test_every_default_topic_claim_compiles_and_is_reachable() -> None:
    """A claim whose `says` pattern nothing can produce is a check that cannot fire."""
    assert DEFAULT_TOPIC_CLAIMS
    for claim in DEFAULT_TOPIC_CLAIMS:
        assert claim.says and claim.grounded_in


# --------------------------------------------------------------------------- #
# ScoreClaimContract
# --------------------------------------------------------------------------- #


def test_a_backed_disclosure_claim_passes() -> None:
    trace = _trace(
        tools=(
            ("record_disclosure", {"code": "capital_at_risk"}),
            ("score_session", dict(_CARD_CLEAN)),
        ),
        feedback="The mandatory risk disclosure was given and is recorded.",
    )
    result = ScoreClaimContract().check(trace)
    assert result.passed
    assert "2/2" in result.detail


def test_an_unbacked_disclosure_claim_fails() -> None:
    trace = _trace(
        tools=(("score_session", dict(_CARD_CLEAN)),),
        feedback="The mandatory risk disclosure was given and is recorded.",
    )
    result = ScoreClaimContract().check(trace)
    assert not result.passed
    assert "record_disclosure never happened" in result.detail
    assert result.evidence[0].quote == "mandatory_disclosure_given=True"


def test_a_clean_advice_claim_is_refuted_by_a_compliance_flag() -> None:
    trace = _trace(
        tools=(
            ("record_disclosure", {"code": "capital_at_risk"}),
            ("flag_compliance_risk", {"kind": "personal_recommendation", "turn": 3}),
            ("score_session", dict(_CARD_CLEAN)),
        ),
        feedback="Nothing you said crossed into personal advice.",
    )
    result = ScoreClaimContract().check(trace)
    assert not result.passed
    assert "recorded flag_compliance_risk" in result.detail
    assert any("flag_compliance_risk" in e.quote for e in result.evidence)


def test_a_claim_made_only_in_prose_is_still_a_claim() -> None:
    """The trainee reads the sentence and never sees the JSON."""
    trace = _trace(
        tools=(("score_session", {"verdict": "pass"}),),
        feedback="The mandatory risk disclosure was given and is recorded.",
    )
    result = ScoreClaimContract().check(trace)
    assert not result.passed
    assert "feedback prose" in result.detail


def test_a_claim_in_both_channels_names_both() -> None:
    trace = _trace(
        tools=(("score_session", dict(_CARD_CLEAN)),),
        feedback="The mandatory risk disclosure was given and is recorded.",
    )
    assert "score card and feedback" in ScoreClaimContract().check(trace).detail


def test_a_card_asserting_the_opposite_makes_the_claim_dormant() -> None:
    """`unlicensed_advice_detected=True` is not the claim this contract checks."""
    trace = _trace(
        tools=(
            ("flag_compliance_risk", {"kind": "personal_recommendation"}),
            ("record_disclosure", {"code": "capital_at_risk"}),
            ("score_session", {"verdict": "fail", "unlicensed_advice_detected": True}),
        ),
    )
    result = ScoreClaimContract().check(trace)
    assert result.passed
    assert not result.applicable


def test_a_missing_score_card_fails_rather_than_reporting_vacuously() -> None:
    """A session that was never graded is a missing grade, not nothing to check."""
    result = ScoreClaimContract().check(_trace(trainee=("Hello.",)))
    assert not result.passed
    assert "never graded" in result.detail


def test_require_score_false_downgrades_that_to_vacuous() -> None:
    result = ScoreClaimContract(require_score=False).check(_trace(trainee=("Hello.",)))
    assert result.passed
    assert not result.applicable


def test_a_score_claim_must_assert_something_about_the_trace() -> None:
    with pytest.raises(ValueError, match="neither requires nor refutes"):
        ScoreClaim(label="empty", key="k")


def test_every_default_score_claim_names_its_evidence() -> None:
    for claim in DEFAULT_SCORE_CLAIMS:
        assert claim.requires or claim.refutes
        for tool in claim.requires + claim.refutes:
            assert tool in TOOL_NAMES, f"{claim.label} names a tool the product lacks"


# --------------------------------------------------------------------------- #
# The corpus schema
# --------------------------------------------------------------------------- #


def test_the_corpus_validates(corpus: Corpus) -> None:
    report = validate_corpus()
    assert report.ok, report.render()
    assert len(corpus) == report.files_seen


def test_every_file_on_disk_is_accounted_for(corpus: Corpus) -> None:
    """Nothing outside the suite directories is parsed as a scenario."""
    on_disk = {str(p) for p in iter_scenario_paths()}
    assert {s.source for s in corpus} == on_disk
    assert not any("customers" in path for path in on_disk)


def test_suite_minimums_are_met(corpus: Corpus) -> None:
    counts = corpus.suite_counts()
    for suite in SUITES:
        assert counts[suite] >= SUITE_MINIMUMS[suite], f"{suite}: {counts[suite]}"


def test_every_tag_is_exercised(corpus: Corpus) -> None:
    """An unused tag is a coverage gap, and it should be a test failure rather
    than an aspiration in a document."""
    unused = [tag for tag, n in corpus.tag_counts().items() if n == 0]
    assert not unused, f"tags described but never used: {unused}"


def test_the_human_column_has_both_labels(corpus: Corpus) -> None:
    """A calibration set with one class in it measures nothing: a constant answer
    scores perfectly on whichever rate happens to be defined."""
    verdicts = corpus.human_verdict_counts()
    assert verdicts["pass"] >= 5 and verdicts["fail"] >= 5


def test_every_row_states_a_reason_for_its_label(corpus: Corpus) -> None:
    for scenario in corpus:
        assert len(scenario.expectation.reason.split()) >= 15, scenario.id


def test_every_row_declares_contracts_that_can_fire(corpus: Corpus) -> None:
    for scenario in corpus:
        names = [c.name for c in scenario.contracts()]
        assert names, f"{scenario.id} declares no contracts"
        assert len(names) == len(set(names))
        assert set(names) <= CONTRACT_NAMES


def test_every_test_alias_resolves(corpus: Corpus) -> None:
    """A short name in the fixtures pointing at a deleted row must fail here, not
    as a KeyError inside an unrelated behavioural test."""
    for alias, scenario_id in ALIASES.items():
        assert corpus.by_id(scenario_id).id == scenario_id, alias
    assert set(ALIASES.values()) == {s.id for s in corpus}


def test_ids_locate_their_own_files(corpus: Corpus) -> None:
    for scenario in corpus:
        assert scenario.source is not None
        assert scenario.source.endswith(f"{scenario.suite}/{scenario.id}.yaml")
        assert scenario.id.startswith(f"{scenario.suite}-")


def test_a_required_trainee_phrase_absent_from_the_script_is_rejected(tmp_path) -> None:
    """Rule 2, the strong form: the stimulus is data in the same file as the
    assertion, so an aspirational compliance row cannot be written."""
    row = {
        "id": "pitch-broken",
        "title": "A row that asserts a stimulus it lacks",
        "customer": "cautious_saver",
        "tags": ["discovery"],
        "trainee": {"turns": ["The fund is a balanced growth fund."]},
        "expectation": {
            "human_verdict": "fail",
            "reason": "This reason is long enough to satisfy the minimum length rule.",
        },
        "trainee_phrases": {"required": ["no real risk"]},
    }
    directory = tmp_path / "pitch"
    directory.mkdir()
    path = directory / "pitch-broken.yaml"
    path.write_text(yaml.safe_dump(row), encoding="utf-8")
    with pytest.raises(Exception, match="does not appear in trainee.turns"):
        load_scenario(path)


def test_an_expected_failure_naming_an_undeclared_contract_is_rejected(tmp_path) -> None:
    row = {
        "id": "pitch-broken",
        "title": "A known gap nobody is watching",
        "customer": "cautious_saver",
        "tags": ["discovery"],
        "trainee": {"turns": ["The fund is a balanced growth fund."]},
        "expectation": {
            "human_verdict": "fail",
            "reason": "This reason is long enough to satisfy the minimum length rule.",
        },
        "score_claims": False,
        "feedback_grounded": False,
        "expected_failure": {
            "contracts": ["score-claims-backed"],
            "expectation": "This expectation is long enough to pass validation.",
        },
    }
    directory = tmp_path / "pitch"
    directory.mkdir()
    path = directory / "pitch-broken.yaml"
    path.write_text(yaml.safe_dump(row), encoding="utf-8")
    with pytest.raises(Exception, match="which this scenario does not declare"):
        load_scenario(path)


def test_a_consistency_floor_inside_the_tolerance_is_rejected() -> None:
    from roleplay.corpus import ConsistencySpec

    with pytest.raises(Exception, match="inside the declared tolerance"):
        ConsistencySpec(tolerance=4.0, expected_spread=2, expectation="a" * 30)


def test_a_consistency_floor_without_prose_is_rejected() -> None:
    from roleplay.corpus import ConsistencySpec

    with pytest.raises(Exception, match="no expectation prose"):
        ConsistencySpec(expected_spread=3)


def test_unknown_keys_in_a_row_are_errors(tmp_path) -> None:
    directory = tmp_path / "pitch"
    directory.mkdir()
    path = directory / "pitch-broken.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "id": "pitch-broken",
                "title": "A row with a typo in a key",
                "customer": "cautious_saver",
                "tags": ["discovery"],
                "trainee": {"turns": ["The fund is balanced."]},
                "expectation": {
                    "human_verdict": "pass",
                    "reason": "This reason is long enough to satisfy the minimum length.",
                },
                "notez": "typo",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        load_scenario(path)


def test_the_tag_vocabulary_documents_every_tag() -> None:
    """Validation and documentation in one object; an undocumented tag is illegal."""
    assert all(len(v.split()) >= 3 for v in TAG_VOCABULARY.values())


# --------------------------------------------------------------------------- #
# The corpus against the product
# --------------------------------------------------------------------------- #


def test_no_row_fails_a_contract_it_does_not_declare(corpus: Corpus) -> None:
    """The regression gate, as a test. Every red must be a declared red.

    This is the assertion that makes the pack's headline numbers trustworthy: a
    finding is either in a row's `expected_failure` or it is new, and a new one
    fails the build rather than blending into a wall of amber.
    """
    surprises: list[str] = []
    for scenario in corpus:
        result = RoleplayCoach(scorer=RubricScorer()).run(
            scenario_id=scenario.id,
            trainee_turns=scenario.trainee.turns,
            profile=corpus.profile_for(scenario),
            jurisdiction=scenario.jurisdiction,
            language=scenario.language,
        )
        report = scenario.contract_set().run(result.trace)
        for failure in report.failures():
            if not scenario.expects_failure_of(failure.name):
                surprises.append(f"{scenario.id}: {failure.name}: {failure.detail}")
        for name in scenario.expected_failure.contracts if scenario.expected_failure else ():
            entry = report.by_name(name)
            if entry is not None and entry.passed:
                surprises.append(f"{scenario.id}: {name} was expected to fail and passed")
    assert not surprises, "\n".join(surprises)


def test_the_scorer_agrees_with_the_human_column_on_exactly_the_expected_rows(
    corpus: Corpus,
) -> None:
    """Pinned so a change in the scorer's agreement is a visible diff, not a drift.

    The three misses are all compliance misses and the one false alarm is the
    Spanish row. That composition, not just the count, is the finding.
    """
    disagreements = set()
    for scenario in corpus:
        card = RoleplayCoach(scorer=RubricScorer()).run(
            scenario_id=scenario.id,
            trainee_turns=scenario.trainee.turns,
            profile=corpus.profile_for(scenario),
            jurisdiction=scenario.jurisdiction,
            language=scenario.language,
        ).card
        if card.verdict != scenario.expectation.human_verdict:
            disagreements.add(scenario.id)
    assert disagreements == {
        "compliance-explicit-unlicensed-advice",
        "compliance-missing-risk-disclosure",
        "compliance-no-real-risk-reassurance",
        "locale-es-mx-registered-spanish-disclosure",
    }
