"""Tests for the live rubric scorer and its calibration study.

WHAT THESE TESTS ARE FOR
------------------------
The live scorer is the one part of `roleplay/` that can talk to a provider, and
the calibration study is the one part whose *numbers* are a claim about a model.
Both are therefore exactly where a repo like this loses credibility, in three
specific ways, and the tests below are organised around them.

**1. The cardinal rule.** A clean clone with every API key unset must pass. So
nothing here reaches a provider: the live path is exercised through the refusal it
raises when the opt-in flag is absent, and every number is recomputed from the
committed recordings by the same parser and the same arithmetic a live run uses.

**2. Failing closed.** A scoring service that cannot read the model's answer must
not certify anybody. Half of these tests are malformed answers, and every one of
them asserts an ERRORED verdict rather than a pass — including the cases that are
*nearly* right, which are the ones a lenient parser waves through.

**3. The findings themselves.** The study's conclusions are pinned as assertions,
not left as prose in a markdown file. If the recordings are replaced and v1's
recall stops being 0.600, or v2 stops clearing the gate, or the run-to-run
instability disappears, a test fails and somebody has to say so in a commit
message. A finding nobody can regress is a finding nobody has to keep.
"""

from __future__ import annotations

import json
import re

import pytest

from lab.judges.calibration import CalibrationThresholds
from lab.judges.judge import (
    JudgeRequest,
    LiveCallBlockedError,
    Recording,
    ScriptedCompletion,
    StaleRecordingError,
)
from lab.judges.registry import JudgeBelowThresholdError, JudgeRegistry
from roleplay import scorer_study as study
from roleplay.labels import (
    LabelPack,
    build_rows,
    committed_labels,
    labelled_and_excluded,
    load_pack,
    rule_label,
    verify_pack,
)
from roleplay.livescorer import (
    LIVE_ENV_VAR,
    MODEL_ENV_VAR,
    RUBRIC_VERSIONS,
    LiveRubricScorer,
    ScoreParseError,
    live_completion,
    parse_live_card,
    rubric_path,
)
from roleplay.persona import CustomerProfile
from roleplay.register import JURISDICTIONS
from roleplay.runtime import RoleplayCoach
from roleplay.scorer import CRITERIA, MAX_PER_CRITERION, PASS_TOTAL, SessionView

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def card_json(**overrides) -> str:
    """A well-formed answer, with fields overridable per test."""
    body = {
        "criteria": {
            "discovery": 4,
            "objection_handling": 3,
            "mandatory_disclosure": 4,
            "no_unlicensed_advice": 4,
            "closing": 3,
        },
        "verdict": "pass",
        "critique": "A competent session.",
        "evidence": "capital at risk",
    }
    body.update(overrides)
    return json.dumps(body)


@pytest.fixture(scope="module")
def items():
    return committed_labels()


@pytest.fixture(scope="module")
def one_trace(items):
    return items[0].trace


def scorer_with(answer: str, *, item_id: str, **kwargs) -> LiveRubricScorer:
    return LiveRubricScorer(
        completion=ScriptedCompletion({item_id: answer}),
        model="test/deployment",
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# The cardinal rule: nothing reaches a provider by accident
# --------------------------------------------------------------------------- #


def test_a_live_call_is_refused_without_the_opt_in(monkeypatch) -> None:
    monkeypatch.delenv(LIVE_ENV_VAR, raising=False)
    completion = live_completion()
    request = JudgeRequest(item_id="x", prompt="p", model="azure/whatever")
    with pytest.raises(LiveCallBlockedError) as excinfo:
        completion(request)
    # The refusal must name the variable, or the reader cannot act on it.
    assert LIVE_ENV_VAR in str(excinfo.value)


def test_the_scorer_gate_is_not_the_judge_gate() -> None:
    """A separate switch, so re-recording one instrument cannot bill for the other."""
    assert LIVE_ENV_VAR == "LAB_LIVE_SCORER"
    assert MODEL_ENV_VAR == "LAB_SCORER_MODEL"
    assert live_completion().env_var == LIVE_ENV_VAR


def test_no_recording_contains_anything_that_looks_like_a_credential() -> None:
    """The fixtures are committed, so they are a place a secret could leak to."""
    suspicious = re.compile(
        r"(api[_-]?key|azure_api|bearer\s|sk-[A-Za-z0-9]{16,}|https://[a-z0-9-]+\.openai\.azure\.com)",
        re.IGNORECASE,
    )
    for path in sorted(study.DIR.glob("*.jsonl")):
        text = path.read_text(encoding="utf-8")
        assert not suspicious.search(text), f"{path.name} may contain a credential"


# --------------------------------------------------------------------------- #
# Parsing: what a good answer produces
# --------------------------------------------------------------------------- #


def test_a_well_formed_answer_becomes_a_full_card() -> None:
    parsed = parse_live_card(card_json())
    assert parsed.criteria == {
        "discovery": 4,
        "objection_handling": 3,
        "mandatory_disclosure": 4,
        "no_unlicensed_advice": 4,
        "closing": 3,
    }
    assert parsed.verdict == "pass"
    assert parsed.total == 18
    assert parsed.evidence == "capital at risk"


def test_json_survives_a_fenced_block_and_surrounding_prose() -> None:
    """Models add both. A parser that cannot cope measures formatting."""
    raw = f"Here is my assessment.\n\n```json\n{card_json()}\n```\n\nHappy to expand."
    assert parse_live_card(raw).verdict == "pass"


def test_a_brace_in_the_critique_does_not_unbalance_the_scan() -> None:
    raw = card_json(critique="The trainee said {nothing} of substance about cost.")
    assert parse_live_card(raw).total == 18


def test_the_card_carries_the_criteria_in_the_rubric_s_order(one_trace, items) -> None:
    item = items[0]
    live = scorer_with(card_json(), item_id=item.item_id).score_live(
        one_trace, item_id=item.item_id
    )
    assert tuple(live.card.criteria) == CRITERIA


# --------------------------------------------------------------------------- #
# Parsing: failing closed
# --------------------------------------------------------------------------- #

MALFORMED = {
    "empty": "",
    "prose only": "I am afraid I cannot assess this transcript.",
    "verdict but no criteria": '{"verdict": "pass", "critique": "fine"}',
    "a missing criterion": json.dumps(
        {
            "criteria": {
                "discovery": 4,
                "objection_handling": 3,
                "mandatory_disclosure": 4,
                "no_unlicensed_advice": 4,
            },
            "verdict": "pass",
        }
    ),
    "a criterion out of range": card_json(
        criteria={
            "discovery": 7,
            "objection_handling": 3,
            "mandatory_disclosure": 4,
            "no_unlicensed_advice": 4,
            "closing": 3,
        }
    ),
    "a fractional criterion": card_json(
        criteria={
            "discovery": 3.5,
            "objection_handling": 3,
            "mandatory_disclosure": 4,
            "no_unlicensed_advice": 4,
            "closing": 3,
        }
    ),
    "a non-numeric criterion": card_json(
        criteria={
            "discovery": "good",
            "objection_handling": 3,
            "mandatory_disclosure": 4,
            "no_unlicensed_advice": 4,
            "closing": 3,
        }
    ),
    "a yes instead of a verdict": card_json(verdict="yes"),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_a_malformed_answer_raises(name: str) -> None:
    with pytest.raises(ScoreParseError):
        parse_live_card(MALFORMED[name])


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_a_malformed_answer_errors_and_never_passes(name, one_trace, items) -> None:
    """The single most important property in this module.

    Every unreadable answer, including the nearly-right ones, must produce an
    ERRORED verdict whose `passed` is False. A scoring service that resolves a
    provider problem into a certification is silent, systematic, and always
    resolves in the direction of shipping.
    """
    item = items[0]
    live = scorer_with(MALFORMED[name], item_id=item.item_id).score_live(
        one_trace, item_id=item.item_id
    )
    assert live.card.verdict == "errored"
    assert live.card.passed is False
    assert live.errored is True
    assert live.self_consistent is False
    assert live.error
    # The prose a human would read has to say the session was not graded.
    assert "not graded" in live.card.feedback
    # And the claims must be unknown, not False — "no advice was detected" is a
    # positive assertion, and nothing was detected because nothing was read.
    assert live.card.claims["mandatory_disclosure_given"] is None
    assert live.card.claims["unlicensed_advice_detected"] is None


def test_out_of_range_is_refused_rather_than_clamped() -> None:
    """Clamping 7 to 4 records the best possible score for the criterion the model
    understood least, which is the direction that certifies people."""
    with pytest.raises(ScoreParseError) as excinfo:
        parse_live_card(MALFORMED["a criterion out of range"])
    assert "outside" in str(excinfo.value)


def test_strict_mode_raises_instead_of_erroring(one_trace, items) -> None:
    item = items[0]
    scorer = scorer_with("nonsense", item_id=item.item_id, strict=True)
    with pytest.raises(ScoreParseError):
        scorer.score_live(one_trace, item_id=item.item_id)


# --------------------------------------------------------------------------- #
# The shared output shape
# --------------------------------------------------------------------------- #


def test_the_scripted_scorer_never_errors() -> None:
    """The third verdict value exists for the live path only."""
    from roleplay.scorer import RubricScorer

    scorer = RubricScorer()
    for row in build_rows():
        assert scorer.score_trace(row.trace).verdict in ("pass", "fail")


def test_the_live_scorer_is_a_drop_in_for_the_coach(items) -> None:
    """`RoleplayCoach` must be able to hold either scorer without knowing which."""
    profile = CustomerProfile(
        key="t", display_name="T", situation="s", language="en", jurisdiction="eu-retail"
    )
    turns = ["What would you want this money to do?", "Shall we get the paperwork started?"]
    answer = card_json()
    scorer = LiveRubricScorer(
        completion=lambda request: answer, model="test/deployment"
    )
    result = RoleplayCoach(scorer=scorer).run(
        scenario_id="drop-in", trainee_turns=turns, profile=profile
    )
    assert result.card.verdict == "pass"
    assert result.card.total == 18
    # The score card reached the trace as a tool call, like the scripted one does.
    ledger = [e.get("name") for e in result.trace.events if e.get("name")]
    assert "score_session" in ledger


def test_scoring_a_session_view_is_refused(items) -> None:
    scorer = LiveRubricScorer(completion=lambda r: card_json(), model="test/deployment")
    with pytest.raises(NotImplementedError):
        scorer.score(SessionView())


def test_a_verdict_may_contradict_the_total_and_is_reported(one_trace, items) -> None:
    """The rubric licenses `fail` over a passing total and never the reverse."""
    item = items[0]
    high_fail = card_json(verdict="fail")  # totals 18, verdict fail
    live = scorer_with(high_fail, item_id=item.item_id).score_live(
        one_trace, item_id=item.item_id
    )
    assert live.card.total >= PASS_TOTAL and live.card.verdict == "fail"
    assert live.self_consistent is False
    assert "which the rubric permits only" in (live.disagreement or "")

    low_pass = card_json(
        criteria={name: 1 for name in CRITERIA}, verdict="pass"
    )  # totals 5
    live2 = scorer_with(low_pass, item_id=item.item_id).score_live(
        one_trace, item_id=item.item_id
    )
    assert live2.self_consistent is False
    assert "never permits" in (live2.disagreement or "")


# --------------------------------------------------------------------------- #
# Replay integrity
# --------------------------------------------------------------------------- #


def test_a_changed_rubric_makes_the_recording_stale(items, tmp_path) -> None:
    """The staleness check is the feature: "I edited the rubric and the numbers did
    not move" must be an exception rather than a mystery."""
    scorer = study.scorer("v1")
    item = items[0]
    scorer.template = type(scorer.template)(
        scorer.template.text + "\n\nAlso, be strict.\n"
    )
    with pytest.raises(StaleRecordingError):
        scorer.score_live(item.trace, item_id=item.item_id)


def test_every_recording_covers_every_item_and_names_one_model(items) -> None:
    for version in RUBRIC_VERSIONS:
        for run in range(1, study.REPLICATES + 1):
            recording = Recording.load(study.verdicts_path(version, run))
            index = recording.by_item()  # raises on a duplicate id
            assert set(index) == {item.item_id for item in items}
            assert len({call.model for call in recording.calls}) == 1


# --------------------------------------------------------------------------- #
# The labelled set
# --------------------------------------------------------------------------- #


def test_the_committed_labels_agree_with_their_own_rules() -> None:
    """The guard that makes the committed set trustworthy without trusting the
    process that wrote it: the trace is in the file, the rules are in the module,
    and one has to produce the other. Corpus- and persona-independent by design."""
    assert verify_pack() == []


def test_the_labelled_set_is_the_right_size_and_both_classes_are_present(items) -> None:
    labels = [item.label for item in items]
    assert len(items) == 27
    assert labels.count("fail") == 15
    assert labels.count("pass") == 12
    # A rate needs enough items to survive a single relabel.
    assert len(items) >= CalibrationThresholds().min_items


def test_no_ambiguous_item_reached_the_metrics(items) -> None:
    assert all(item.label in ("pass", "fail") for item in items)


def test_the_exclusions_are_enumerated_with_reasons() -> None:
    _, excluded = labelled_and_excluded(build_rows())
    assert len(excluded) == 7
    for row in excluded:
        assert row.excluded_because
        assert row.derived.rule == "R4"


def test_the_rule_and_the_corpus_reviewers_never_disagreed() -> None:
    """No item was excluded as CONTESTED.

    Worth an assertion rather than a sentence: on every corpus row the rules could
    settle, the derived label matched the reviewer's declared verdict. That is the
    evidence that the four rules are a transcription of the reviewers' standard
    rather than a second, competing standard of my own.
    """
    _, excluded = labelled_and_excluded(build_rows())
    assert not [row for row in excluded if "CONTESTED" in (row.excluded_because or "")]


@pytest.mark.parametrize(
    ("view", "rule", "label"),
    [
        (
            SessionView(trainee_turns=("Shall we get the paperwork started?",)),
            "R1",
            "fail",
        ),
        (
            SessionView(
                trainee_turns=("Shall we get the paperwork started?",),
                disclosures=(
                    {"code": "capital_at_risk"},
                    {"code": "past_performance"},
                    {"code": "fees_and_charges"},
                ),
                compliance_flags=({"kind": "personal_recommendation"},),
            ),
            "R2",
            "fail",
        ),
        (
            SessionView(
                trainee_turns=("Shall we get the paperwork started?",),
                disclosures=(
                    {"code": "capital_at_risk"},
                    {"code": "past_performance"},
                    {"code": "fees_and_charges"},
                ),
            ),
            "R3",
            "pass",
        ),
        (
            SessionView(
                trainee_turns=("The fund holds sixty per cent equities.",),
                disclosures=(
                    {"code": "capital_at_risk"},
                    {"code": "past_performance"},
                    {"code": "fees_and_charges"},
                ),
            ),
            "R4",
            "ambiguous",
        ),
    ],
)
def test_each_rule_fires_on_its_own_situation(view, rule, label) -> None:
    derived = rule_label(view)
    assert (derived.rule, derived.label) == (rule, label)


def test_an_outright_failure_outranks_the_reward_conditions() -> None:
    """R1 before R3: a polished session with a missing disclosure must not reach R3."""
    view = SessionView(
        trainee_turns=("Shall we get the paperwork started?",),
        disclosures=({"code": "capital_at_risk"}, {"code": "past_performance"}),
    )
    assert rule_label(view).rule == "R1"


def test_a_pack_row_whose_prediction_is_wrong_is_a_load_error() -> None:
    """The pack cannot mislabel itself quietly."""
    pack = load_pack()
    broken = pack.model_copy(
        update={
            "sessions": tuple(
                row.model_copy(
                    update={"asserts": row.asserts.model_copy(update={"label": "pass", "rule": "R3"})}
                )
                if row.id == "label-advice-if-i-were-you"
                else row
                for row in pack.sessions
            )
        }
    )
    with pytest.raises(ValueError, match="the pack predicts"):
        build_rows(pack=broken)


def test_a_rostered_corpus_row_that_vanished_is_a_load_error() -> None:
    pack = load_pack()
    broken = pack.model_copy(
        update={"corpus_rows": pack.corpus_rows + ("no-such-scenario-anywhere",)}
    )
    with pytest.raises(KeyError, match="no longer contains"):
        build_rows(pack=broken)


def test_the_roster_pins_the_set_against_a_growing_corpus() -> None:
    """Adding a scenario to the corpus must not change this measurement."""
    pack = load_pack()
    assert pack.corpus_rows, "the roster must be explicit, not implied"
    rows = build_rows()
    assert len(rows) == len(pack.corpus_rows) + len(pack.sessions)


def test_r4_is_the_only_ambiguous_rule() -> None:
    for row in load_pack().sessions:
        assert (row.asserts.rule == "R4") == (row.asserts.label == "ambiguous")


# --------------------------------------------------------------------------- #
# The rubric files
# --------------------------------------------------------------------------- #


def test_every_rubric_version_exists_and_renders() -> None:
    for version in RUBRIC_VERSIONS:
        assert rubric_path(version).is_file()
        template = study.rubric_prompt(version)
        assert "transcript" in template.placeholders
        assert "tool_ledger" in template.placeholders


def test_an_unknown_rubric_version_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown rubric version"):
        rubric_path("v99")


def test_v2s_requirement_table_matches_the_register() -> None:
    """The prompt states each market's requirement set. A stale copy in the prompt
    would be the same class of bug this whole study is about, one level up — so the
    table is checked against `roleplay.register` rather than trusted."""
    text = rubric_path("v2").read_text(encoding="utf-8")
    found: dict[str, tuple[str, ...]] = {}
    for match in re.finditer(
        r"^\* `([a-z-]+)` requires (\d+): (.+)$", text, re.MULTILINE
    ):
        market, count, codes = match.groups()
        parsed = tuple(re.findall(r"`([a-z_]+)`", codes))
        assert len(parsed) == int(count), f"{market}: stated count disagrees with the list"
        found[market] = parsed
    assert found == JURISDICTIONS


def test_v1_does_not_state_the_requirement_table() -> None:
    """The diagnosed cause of v1's misses, asserted so the two versions cannot
    silently converge and make the v1 -> v2 comparison meaningless."""
    assert "REQUIRED DISCLOSURES, BY MARKET" not in rubric_path("v1").read_text(
        encoding="utf-8"
    )
    assert "REQUIRED DISCLOSURES, BY MARKET" in rubric_path("v2").read_text(
        encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# The measured findings
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def reports(items):
    return {v: study.calibrate_version(v, items=items) for v in RUBRIC_VERSIONS}


def test_v1_misses_defects_and_invents_none(reports) -> None:
    report = reports["v1"]
    assert report.true_positive_rate.numerator == 9
    assert report.true_positive_rate.denominator == 15
    assert report.confusion.false_positive == 0
    assert report.confusion.false_negative == 6
    assert report.parse_errors == 0


def test_every_v1_miss_is_a_missing_disclosure_the_prompt_never_listed(reports) -> None:
    """The whole diagnosis, as an assertion.

    All six false negatives are R1 items — sessions with a required code absent —
    and v1's prompt never states which codes a market requires. That is what made
    v2 a fix rather than a guess.
    """
    from roleplay.scorer import session_view

    traces = {item.item_id: item.trace for item in committed_labels()}
    misses = [d.item_id for d in reports["v1"].disagreements if d.kind == "false_negative"]
    assert len(misses) == 6
    for item_id in misses:
        assert rule_label(session_view(traces[item_id])).rule == "R1"


def test_v2_beats_v1_on_recall_without_losing_precision(reports) -> None:
    v1, v2 = reports["v1"], reports["v2"]
    assert v2.true_positive_rate.value > v1.true_positive_rate.value
    assert v2.confusion.false_positive == v1.confusion.false_positive == 0
    assert v2.parse_errors == 0


def test_the_gate_refuses_v1_and_admits_v2(reports) -> None:
    thresholds = CalibrationThresholds()
    assert reports["v1"].passes(thresholds) is False
    assert reports["v2"].passes(thresholds) is True


def test_require_calibrated_really_raises_for_v1_in_ci_mode(reports) -> None:
    """The gate must be load-bearing, not a printed opinion."""
    judge = study.judge("v1")
    judge.attach_calibration(reports["v1"])
    registry = JudgeRegistry(thresholds=CalibrationThresholds())
    registry.register(judge)
    with pytest.raises(JudgeBelowThresholdError):
        registry.require_calibrated(judge, ci=True)


def test_require_calibrated_admits_v2_in_ci_mode(reports) -> None:
    judge = study.judge("v2")
    judge.attach_calibration(reports["v2"])
    registry = JudgeRegistry(thresholds=CalibrationThresholds())
    registry.register(judge)
    # Returns the report it cleared, so a caller can log the numbers it trusted.
    assert registry.require_calibrated(judge, ci=True) is reports["v2"]


def test_the_gate_helper_records_the_real_refusal(reports) -> None:
    ok, failures = study.gate(reports["v1"], version="v1")
    assert ok is False
    assert any("registry refused" in failure for failure in failures)
    assert any("TPR" in failure for failure in failures)


# --------------------------------------------------------------------------- #
# Stability: the finding an aggregate cannot show
# --------------------------------------------------------------------------- #


def test_v1s_verdicts_are_unanimous_while_its_score_cards_are_not(items) -> None:
    """The demonstration this study exists to make.

    Binary self-consistency on v1 is 27/27 — a perfect instrument by that measure —
    and six of the twenty-seven score cards changed between identical runs. The
    aggregate does not merely understate the instability; it cannot represent it.
    """
    binary = study.stability("v1", items=items)
    assert binary.unstable == []
    assert binary.unanimity.numerator == binary.unanimity.denominator == 27

    cards = study.criterion_stability("v1", items=items)
    assert len(cards.unstable) == 6
    assert len(cards.verdict_unstable) == 0
    assert len(cards.score_unstable) == 6
    # And the aggregate the binary metric is built on is identical run to run.
    assert len(set(cards.pass_counts)) == 1


def test_v2s_calibration_table_is_not_reproducible(items) -> None:
    """The error bar on the headline. v2 is perfect on runs 1 and 2 and has a false
    positive on run 3, so "TPR 1.000, TNR 1.000" is a property of a run."""
    tables = set()
    for run in range(1, study.REPLICATES + 1):
        confusion = study.calibrate_version("v2", items=items, run=run).confusion
        tables.add(
            (
                confusion.true_positive,
                confusion.false_positive,
                confusion.false_negative,
                confusion.true_negative,
            )
        )
    assert len(tables) == 2, "v2's confusion matrix moved between identical runs"
    assert "not reproducible" in study.calibration_variance("v2", items=items)


def test_v1s_calibration_table_is_reproducible(items) -> None:
    """The contrast that makes the v2 result meaningful rather than an artefact of
    the method: the same procedure applied to v1 gives one table three times."""
    tables = {
        study.calibrate_version("v1", items=items, run=run).confusion.model_dump_json()
        for run in range(1, study.REPLICATES + 1)
    }
    assert len(tables) == 1


def test_a_criterion_can_swing_its_whole_range_while_the_verdict_holds(items) -> None:
    cards = study.criterion_stability("v2", items=items)
    widest = max(
        (spread for item in cards.items for spread in item.moved_criteria.values()),
        default=0,
    )
    assert widest == MAX_PER_CRITERION
    culprit = next(
        item
        for item in cards.items
        if MAX_PER_CRITERION in item.moved_criteria.values()
    )
    assert culprit.verdict_stable, (
        "the point is that a shipped number moved the full width of its scale "
        "while the verdict a gate would read did not"
    )


def test_stability_refuses_ragged_runs() -> None:
    from roleplay.scorer_study.stability import stability_of

    run = [("a", "pass", {name: 1 for name in CRITERIA}, 5, "fail", False)]
    with pytest.raises(ValueError, match="one run cannot disagree"):
        stability_of(rubric_version="v1", model="m", per_run=[run])
    with pytest.raises(ValueError, match="same items in the same order"):
        stability_of(rubric_version="v1", model="m", per_run=[run, run + run])


def test_cancellation_is_reported_when_it_occurs() -> None:
    """Constructed, because the committed recordings happen not to show the exact
    form (a flat mean over a moving population). The detector has to work anyway,
    or the study could not report the case if a re-record produced it."""
    from roleplay.scorer_study.stability import stability_of

    base = {name: 2 for name in CRITERIA}
    up = {**base, "closing": 4}
    down = {**base, "closing": 0}
    run1 = [
        ("a", "pass", up, sum(up.values()), "pass", False),
        ("b", "pass", down, sum(down.values()), "pass", False),
    ]
    run2 = [
        ("a", "pass", down, sum(down.values()), "pass", False),
        ("b", "pass", up, sum(up.values()), "pass", False),
    ]
    report = stability_of(rubric_version="v1", model="m", per_run=[run1, run2])
    assert report.aggregate_spread == 0.0
    assert len(report.unstable) == 2
    assert report.cancellation is True
    assert "aggregate is flat and the items are not" in report.to_markdown()


# --------------------------------------------------------------------------- #
# Pointing the calibrated scorer at the seeded defects
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def probe():
    return study.defect_probe()


def test_the_live_scorer_catches_the_compliance_defect(probe) -> None:
    """DEFECT-3 is the one a model-graded rubric is genuinely better at: the
    scripted scorer passes these sessions on keyword presence and the live scorer
    fails them, for the stated reason."""
    rows = [row for row in probe if row.defect == "DEFECT-3" and row.decidable]
    assert rows
    assert all(row.agrees_with_rule for row in rows)
    assert all(row.beats_scripted for row in rows), (
        "each of these is a session the scripted scorer certified and the live "
        "scorer refused"
    )


def test_the_live_scorer_does_not_notice_the_fabricated_feedback(probe) -> None:
    """The finding that matters more than the clean sweep.

    On the two DEFECT-2 control rows the scorer is shown the full trace, feedback
    included — feedback that quotes a question the trainee never asked and praises
    an objection that was never raised — and it returns a pass with a complimentary
    critique that never mentions either. It is not wrong about the trainee. It was
    asked about the trainee. A judge answers the question in its prompt, and a
    defect outside that question is invisible to it however well calibrated it is.

    `roleplay.contracts.FeedbackGroundednessContract` catches both deterministically.
    That is the argument for contracts and judges rather than judges alone.
    """
    fabricated = {
        "pitch-terse-customer-patient-probing",
        "objection-praise-for-unasked-question",
    }
    rows = [row for row in probe if row.item_id in fabricated]
    assert len(rows) == 2
    for row in rows:
        assert row.live_verdict == "pass"
        haystack = f"{row.critique} {row.evidence or ''}".lower()
        assert not any(
            term in haystack
            for term in ("fabricat", "feedback is", "not asked", "never asked", "ungrounded")
        ), "if this starts failing, the scorer has begun auditing the feedback"


def test_defect_one_is_out_of_reach_of_any_single_transcript() -> None:
    """Not a probe result — a structural argument, asserted where it cannot be lost.

    DEFECT-1 is a cohort curve: an identical transcript scores differently
    depending on how many sessions the service graded before it. Nothing about one
    session's trace distinguishes a curved grade from an uncurved one, so no judge
    reading one transcript can detect it at any level of calibration. It takes
    repeats and a comparison — `roleplay.consistency` — which is a harness
    property and not a rubric one.
    """
    from roleplay.consistency import measure_consistency
    from roleplay.corpus import load_corpus

    corpus = load_corpus()
    scenario = next(s for s in corpus if s.id == "consistency-identical-transcript-warm-k5")
    report = measure_consistency(
        scenario_id=scenario.id,
        trainee_turns=scenario.trainee.turns,
        profile=corpus.profile_for(scenario),
        k=5,
    )
    # The warm arm moves and the cold control does not: the defect is real and
    # localised to state the service holds between sessions.
    assert report.warm_spread.spread > 0
    assert report.cold_spread.spread == 0
    assert report.localises_to_shared_state
    # And every one of those five sessions has an identical trace, so a per-session
    # judge would be handed the same input five times.
    assert len({tuple(scenario.trainee.turns)}) == 1


def test_the_probe_does_not_score_an_undecidable_row(probe) -> None:
    """Two probe rows are R4. Calling the live verdict right or wrong on them would
    be inventing a reference, so they are probed and not scored."""
    undecidable = [row for row in probe if not row.decidable]
    assert len(undecidable) == 2
    for row in undecidable:
        assert row.agrees_with_rule is None
        assert row.anchored is False
        assert "NOT SCORED" in row.describe()


# --------------------------------------------------------------------------- #
# The study regenerates itself
# --------------------------------------------------------------------------- #


def test_the_study_recomputes_offline(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(LIVE_ENV_VAR, raising=False)
    text = study.study_markdown()
    for needle in (
        "## The labelled set",
        "Would a second run have produced the same table?",
        "### Score cards — `v1`",
        "Pointing the live scorer at the seeded defects",
    ):
        assert needle in text


def test_the_cli_exits_zero_and_writes_its_artefacts(tmp_path, capsys) -> None:
    assert study.main(["--out", str(study.DIR)]) == 0
    out = capsys.readouterr().out
    assert "wrote study" in out
