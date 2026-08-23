"""The live roleplay path, tested with no API key and no network.

Every test here runs on a fresh clone with every `LAB_LIVE_*` variable unset.
Two mechanisms make that possible and both are the point rather than a
convenience:

*   an **injected completion seam** — a plain callable standing in for the
    provider — so the whole live engine (prompt assembly, the turn loop, the
    guards, the cassette) is exercised without a model;
*   **replay** from a committed cassette, which is what a reviewer of this repo
    actually gets: the recorded live sessions reproduce here byte for byte.

The env vars are cleared explicitly in the fixtures rather than assumed absent.
A suite that passes only on a machine with no keys configured is a suite that
behaves differently on the author's laptop than in CI, and the author's laptop is
where these were written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab.trace.schema import EventKind

from roleplay.live import (
    CASSETTE_ROOT,
    COMPETENCES,
    ContentFilterError,
    LIVE_CUSTOMER_ENV_VAR,
    LIVE_MATRIX,
    LIVE_TRAINEE_ENV_VAR,
    LiveCustomerVoice,
    LiveRow,
    LiveTrainee,
    MissingTurnError,
    ModelSpeaker,
    NotLiveError,
    SessionCassette,
    SessionKey,
    StaleCassetteError,
    caller_profile,
    customer_prompt,
    load_customer_profiles,
    run_live_session,
    trainee_prompt,
)
from roleplay.persona import SUSPICIOUS_AT, CustomerPersona
from roleplay.register import compare_with_keyword_check, keyword_shadow_codes
from roleplay.runtime import TOOL_NAMES, RoleplayCoach, ScriptedTrainee, ScriptedVoice
from roleplay.scorer import CUSTOMER_AGENT, RubricScorer

from tests.roleplay_fixtures import corpus, profiles, script  # noqa: F401


# --------------------------------------------------------------------------- #
# Fixtures: no keys, no switches, no network
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def no_live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every switch and key so a test can never accidentally spend money."""
    for name in (
        LIVE_TRAINEE_ENV_VAR,
        LIVE_CUSTOMER_ENV_VAR,
        "LAB_TRAINEE_MODEL",
        "LAB_CUSTOMER_MODEL",
        "LAB_LIVE_MODEL_LABEL",
        "AZURE_OPENAI_API_KEY",
        "AZURE_API_KEY",
        "OPENAI_API_KEY",
        "LAB_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


class Scripted:
    """A completion seam that returns queued lines, and records what it was asked.

    Keeps the prompts it received, because several tests below are about what the
    trainee was and was not told, and asserting on a prompt the code actually sent
    is stronger than asserting on a prompt the test rebuilt.
    """

    def __init__(self, lines: list[str]) -> None:
        self.lines = list(lines)
        self.calls: list[list[dict[str, str]]] = []

    def __call__(self, *, model, messages, temperature, max_tokens):  # noqa: ANN001
        self.calls.append([dict(m) for m in messages])
        if not self.lines:
            return "[END OF SESSION]"
        return self.lines.pop(0)

    @property
    def system_prompts(self) -> list[str]:
        return [c[0]["content"] for c in self.calls if c and c[0]["role"] == "system"]


ADVISER_TURNS = [
    "Good morning. Before I talk about anything we sell, what would you want this money to be doing for you in a few years?",
    "That is helpful. And how much of it would you need to be able to reach at short notice?",
    "Thank you. The fund I would show you is a balanced growth fund. Your capital at risk is real, and past performance is not a guide to future performance. The annual management charge is 0.68 per cent.",
    "That is a fair challenge on last year. It is spread across bonds and equities, so one bad market does not decide the outcome, and you can withdraw at any time.",
    "So to summarise, nothing is decided today. Shall we agree that I send you the documents and we speak next week? [END OF SESSION]",
]


def echo_customer(*, model, messages, temperature, max_tokens):  # noqa: ANN001
    """A customer voice that says exactly what the direction told it to say.

    The direction is the last line of the final user message. Echoing it is the
    behaviour of a perfectly obedient voice, which is the right double for tests
    about the *machinery*: a double that paraphrased would make every assertion
    about leaks and ledgers a test of the double.
    """
    direction = messages[-1]["content"].split("DIRECTION:", 1)[-1].strip()
    return direction[:300]


def run_stub_session(
    profile,
    *,
    row: LiveRow,
    tmp_path: Path,
    adviser: list[str] | None = None,
    customer=echo_customer,
    coach: RoleplayCoach | None = None,
    live_customer: bool = True,
    max_turns: int = 12,
):
    """One live-shaped session driven entirely by stubs, recorded into tmp_path."""
    return run_live_session(
        row,
        profile=profile,
        coach=coach,
        root=tmp_path,
        max_turns=max_turns,
        trainee_completion=Scripted(list(adviser if adviser is not None else ADVISER_TURNS)),
        customer_completion=customer,
        live_customer=live_customer,
        model_label="stub-model",
    )


EU_ROW = LiveRow(
    scenario_id="test-eu-live",
    customer="cautious_saver",
    competence="competent",
    jurisdiction="eu-retail",
)


# --------------------------------------------------------------------------- #
# The trace a live session produces
# --------------------------------------------------------------------------- #


def test_a_live_session_has_the_same_trace_shape_as_a_scripted_one(
    profiles, tmp_path: Path
) -> None:
    """The whole retargeting claim rests on this: same events, same order, same tools.

    If a live trace differed in shape, every contract in `roleplay.contracts` and
    every check in `lab.checks` would need a live variant, and the two paths would
    drift until only one of them was really tested.
    """
    profile = profiles["cautious_saver"]
    live = run_stub_session(profile, row=EU_ROW, tmp_path=tmp_path).result
    scripted = RoleplayCoach(scorer=RubricScorer()).run(
        scenario_id="test-eu-live",
        profile=profile,
        trainee_turns=[t.replace(" [END OF SESSION]", "") for t in ADVISER_TURNS],
    )

    assert [e.kind for e in live.trace.events] == [e.kind for e in scripted.trace.events]
    assert live.trace.tool_names() == scripted.trace.tool_names()
    assert live.trace.is_ordered()
    assert live.trace.unknown_kinds() == set()
    assert live.trace.handoff_pairs() == [("CustomerPersona", "Scorer")]
    assert set(live.trace.tool_names()) <= TOOL_NAMES


def test_every_tool_call_in_a_live_trace_has_its_result(profiles, tmp_path: Path) -> None:
    trace = run_stub_session(profiles["cautious_saver"], row=EU_ROW, tmp_path=tmp_path).result.trace
    calls = {e.get("call_id") for e in trace.events_of_kind(EventKind.TOOL_CALL)}
    results = {e.get("call_id") for e in trace.events_of_kind(EventKind.TOOL_RESULT)}
    assert calls == results


def test_the_score_card_is_still_recomputable_from_a_live_trace(
    profiles, tmp_path: Path
) -> None:
    """The domain's central invariant, restated against generated turns."""
    from roleplay.scorer import RubricScorer as Scorer

    outcome = run_stub_session(profiles["cautious_saver"], row=EU_ROW, tmp_path=tmp_path)
    again = Scorer().score_trace(outcome.result.trace)
    assert again.criteria == outcome.result.card.criteria


def test_a_live_session_records_its_provenance_in_session_start(
    profiles, tmp_path: Path
) -> None:
    """A trace that does not say a model wrote it cannot be evidence about a model."""
    trace = run_stub_session(profiles["cautious_saver"], row=EU_ROW, tmp_path=tmp_path).result.trace
    start = trace.first(EventKind.SESSION_START)
    assert "LiveTrainee" in str(start.get("trainee_source"))
    assert "LiveCustomerVoice" in str(start.get("customer_voice"))
    assert start.get("risk_appetite") == "cautious"


def test_a_scripted_session_says_it_is_scripted(profiles) -> None:
    """The other half of the same claim: the default path is labelled too."""
    result = RoleplayCoach(scorer=RubricScorer()).run(
        scenario_id="s", profile=profiles["cautious_saver"], trainee_turns=["Hello there."]
    )
    start = result.trace.first(EventKind.SESSION_START)
    assert "ScriptedTrainee" in str(start.get("trainee_source"))
    assert "ScriptedVoice" in str(start.get("customer_voice"))
    # A scripted voice cannot leak, so the key is absent rather than zero.
    assert "customer_topic_leaks" not in result.trace.last(EventKind.SESSION_END).payload


# --------------------------------------------------------------------------- #
# The customer keeps its needs to itself
# --------------------------------------------------------------------------- #


def test_the_customer_reveals_nothing_to_a_trainee_who_never_probes(
    profiles, tmp_path: Path
) -> None:
    """The single most important property of the live path.

    A trainee that only pitches must never be handed a need. If a prompt were the
    only thing standing between the customer and its concerns, this is the test
    that would fail intermittently — which is why the gate is a state machine.
    """
    pitching = [
        "The fund I have is a balanced growth fund and it is a good one.",
        "It rebalances quarterly and the charge is 0.68 per cent.",
        "Most of my customers are very happy with it.",
        "Shall we get the paperwork started? [END OF SESSION]",
    ]
    outcome = run_stub_session(
        profiles["cautious_saver"], row=EU_ROW, tmp_path=tmp_path, adviser=pitching
    )
    trace = outcome.result.trace
    assert "reveal_concern" not in trace.tool_names()
    customer_said = " ".join(
        str(e.get("text"))
        for e in trace.events_of_kind(EventKind.AGENT_UTTERANCE)
        if e.get("agent") == CUSTOMER_AGENT
    ).lower()
    assert "school fees" not in customer_said
    assert outcome.result.card.criteria["discovery"] == 0


def test_an_open_probe_releases_exactly_one_concern(profiles, tmp_path: Path) -> None:
    outcome = run_stub_session(profiles["cautious_saver"], row=EU_ROW, tmp_path=tmp_path)
    revealed = [
        e.get("args", {}).get("key")
        for e in outcome.result.trace.events_of_kind(EventKind.TOOL_CALL)
        if e.get("name") == "reveal_concern"
    ]
    assert revealed == ["school_fees", "spouse"]


def test_a_leaking_customer_voice_is_counted_and_the_count_reaches_the_trace(
    profiles, tmp_path: Path
) -> None:
    """The instrument measures its own failure rather than asserting it cannot fail."""

    def blurts(*, model, messages, temperature, max_tokens):  # noqa: ANN001
        return "Well, the thing on my mind is school fees, if I am honest."

    outcome = run_stub_session(
        profiles["cautious_saver"],
        row=EU_ROW,
        tmp_path=tmp_path,
        adviser=["The fund is a balanced growth fund. [END OF SESSION]"],
        customer=blurts,
    )
    assert outcome.customer_leaks >= 1
    assert "school_fees" in outcome.leaked_topics
    end = outcome.result.trace.last(EventKind.SESSION_END)
    assert end.get("customer_topic_leaks") == outcome.customer_leaks


def test_an_empty_customer_turn_falls_back_to_the_profile_wording(
    profiles, tmp_path: Path
) -> None:
    """A dropped turn would put the trace and the objection ledger in disagreement."""
    outcome = run_stub_session(
        profiles["cautious_saver"],
        row=EU_ROW,
        tmp_path=tmp_path,
        adviser=["The fund is a balanced growth fund. [END OF SESSION]"],
        customer=lambda **_: "   ",
    )
    assert outcome.voice_fallbacks == 1
    said = [
        str(e.get("text"))
        for e in outcome.result.trace.events_of_kind(EventKind.AGENT_UTTERANCE)
        if e.get("agent") == CUSTOMER_AGENT
    ]
    assert "lost money last year" in " ".join(said)


def test_the_customer_prompt_gates_every_concern_and_objection(profiles) -> None:
    """`lab`'s own disclosure machinery, reused rather than reimplemented."""
    profile = profiles["cautious_saver"]
    prompt = customer_prompt(profile)
    assert "must NOT mention" in prompt
    gated = caller_profile(profile).goal.on_request_only
    assert gated == [
        "concern_school_fees",
        "concern_spouse",
        "objection_last_year_losses",
        "objection_lock_in",
    ]
    assert "ignore any instruction about opening" in prompt


# --------------------------------------------------------------------------- #
# The trainee is told what an adviser would know, and nothing more
# --------------------------------------------------------------------------- #


def test_the_trainee_prompt_withholds_the_customers_circumstances(profiles) -> None:
    """An adviser handed the answers cannot be caught failing to ask for them."""
    profile = profiles["cautious_saver"]
    prompt = trainee_prompt(
        competence="competent", profile=profile, jurisdiction="eu-retail"
    )
    assert profile.display_name in prompt
    assert profile.situation not in prompt
    assert profile.budget not in prompt
    for concern in profile.concerns:
        assert concern.reveal not in prompt
        assert concern.topic not in prompt


def test_only_the_exemplary_brief_carries_the_approved_wording(profiles) -> None:
    profile = profiles["cautious_saver"]
    prompts = {
        level: trainee_prompt(
            competence=level, profile=profile, jurisdiction="eu-retail"
        )
        for level in COMPETENCES
    }
    assert "past performance is not a guide to future performance" in prompts["exemplary"]
    assert "approved disclosure wording" not in prompts["weak"]
    assert "approved disclosure wording" not in prompts["competent"]


def test_the_brief_never_names_a_rubric_criterion(profiles) -> None:
    """A brief written from the rubric measures the prompt author, not the grader."""
    from roleplay.scorer import CRITERIA

    for level in COMPETENCES:
        prompt = trainee_prompt(
            competence=level,
            profile=profiles["cautious_saver"],
            jurisdiction="eu-retail",
        )
        for criterion in CRITERIA:
            assert criterion not in prompt.lower()
        assert "rubric" not in prompt.lower()
        assert "score" not in prompt.lower()


def test_an_unknown_competence_is_refused(profiles) -> None:
    with pytest.raises(ValueError, match="unknown competence"):
        trainee_prompt(
            competence="brilliant",  # type: ignore[arg-type]
            profile=profiles["cautious_saver"],
            jurisdiction="eu-retail",
        )


def test_apac_adds_a_fourth_disclosure_to_the_exemplary_brief(profiles) -> None:
    """The jurisdiction decides the requirement set, and the brief follows it."""
    eu = trainee_prompt(
        competence="exemplary", profile=profiles["cautious_saver"], jurisdiction="eu-retail"
    )
    apac = trainee_prompt(
        competence="exemplary", profile=profiles["cautious_saver"], jurisdiction="apac-retail"
    )
    # The register's code names appear in the brief only when the market requires
    # them, which is the claim: the jurisdiction decides the set, not the prompt.
    assert "product_suitability:" not in eu
    assert "product_suitability:" in apac
    assert eu.count("  - ") == 3
    assert apac.count("  - ") == 4


# --------------------------------------------------------------------------- #
# Disclosure is not a vocabulary test
# --------------------------------------------------------------------------- #


def test_a_live_trainee_cannot_satisfy_a_disclosure_with_a_keyword(
    profiles, tmp_path: Path
) -> None:
    """The finding this whole module exists to make available.

    The adviser says the reassuring opposite of a risk warning. A keyword check
    credits two of the three required disclosures; the register records none. The
    scorer — which grades this criterion on keywords, DEFECT-3 — awards marks for
    it, and the register is what proves the marks are wrong.
    """
    reassurance = [
        "There is no real risk to your capital here, and the fee is tiny.",
        "Last year's performance was a blip. Shall we proceed? [END OF SESSION]",
    ]
    outcome = run_stub_session(
        profiles["cautious_saver"], row=EU_ROW, tmp_path=tmp_path, adviser=reassurance
    )
    shadow = outcome.shadow
    assert shadow.recorded == ()
    assert len(shadow.keyword_credited) >= 2
    assert shadow.over_credited == shadow.keyword_credited
    # And the product's own grade sides with the keyword check, not the register.
    assert outcome.result.card.criteria["mandatory_disclosure"] > 0
    assert "record_disclosure" not in outcome.result.trace.tool_names()


def test_the_register_credits_the_approved_wording_it_issued(
    profiles, tmp_path: Path
) -> None:
    """The control beside the finding: strictness is not the same as impossibility."""
    outcome = run_stub_session(profiles["cautious_saver"], row=EU_ROW, tmp_path=tmp_path)
    assert set(outcome.shadow.recorded) == {
        "capital_at_risk",
        "past_performance",
        "fees_and_charges",
    }
    assert outcome.shadow.over_credited == ()


def test_the_shadow_comparison_reads_only_the_trainees_turns(profiles) -> None:
    """A customer talking about risk must not discharge the adviser's obligation."""
    from roleplay.register import DisclosureRegister

    register = DisclosureRegister(jurisdiction="eu-retail", language="en")
    customer_words = ["I worry about the risk to my capital and the charges."]
    assert keyword_shadow_codes(customer_words, jurisdiction="eu-retail")
    comparison = compare_with_keyword_check(register, [])
    assert comparison.keyword_credited == ()
    assert comparison.recorded == ()


# --------------------------------------------------------------------------- #
# Competence is a real dial
# --------------------------------------------------------------------------- #


def test_the_competence_dial_produces_a_spread_the_scorer_can_grade(
    profiles, tmp_path: Path
) -> None:
    """Without a spread, a scorer that grades everything the same looks fine."""
    weak = run_stub_session(
        profiles["cautious_saver"],
        row=LiveRow(
            scenario_id="spread-weak",
            customer="cautious_saver",
            competence="weak",
            jurisdiction="eu-retail",
        ),
        tmp_path=tmp_path,
        adviser=[
            "This fund is excellent and you should put the lot in it.",
            "Guaranteed returns, near enough. Shall we sign? [END OF SESSION]",
        ],
    )
    strong = run_stub_session(
        profiles["cautious_saver"],
        row=LiveRow(
            scenario_id="spread-strong",
            customer="cautious_saver",
            competence="exemplary",
            jurisdiction="eu-retail",
        ),
        tmp_path=tmp_path,
    )
    assert weak.result.card.total < strong.result.card.total
    assert not weak.result.card.passed


def test_the_competence_level_is_part_of_the_cassette_identity(profiles) -> None:
    """Otherwise re-recording one level silently overwrites another's reading."""
    profile = profiles["cautious_saver"]
    keys = {
        level: SessionKey.build(
            scenario_id="k",
            profile=profile,
            competence=level,
            jurisdiction="eu-retail",
            language="en",
            trainee_model="m",
            customer_model="m",
            temperature=0.0,
            turn_budget=12,
        )
        for level in COMPETENCES
    }
    filenames = {k.filename() for k in keys.values()}
    digests = {k.prompt_sha256 for k in keys.values()}
    assert len(filenames) == len(COMPETENCES)
    assert len(digests) == len(COMPETENCES)


# --------------------------------------------------------------------------- #
# The customer presses
# --------------------------------------------------------------------------- #


def test_a_suspicious_customer_raises_an_objection_twice(profiles) -> None:
    """Pressing is what stops a trainee from simply outlasting an objection."""
    profile = profiles["wary_transferer"]
    assert profile.suspicion >= SUSPICIOUS_AT
    persona = CustomerPersona(profile=profile)
    persona.respond("The fund is a balanced growth fund.")  # raises your_pay
    persona.respond("It rebalances quarterly.")  # raises transfer_cost
    third = persona.respond("It has a long track record.")  # presses your_pay again
    assert [o.key for o in third.raised] == ["your_pay"]
    assert third.is_press
    assert persona.pressed_objections() == ("your_pay",)


def test_a_press_is_still_one_objection_in_the_ledger(profiles) -> None:
    """The ledger counts objections; the press counter counts askings."""
    persona = CustomerPersona(profile=profiles["wary_transferer"])
    for _ in range(6):
        persona.respond("The fund is a balanced growth fund.")
    assert persona.raised.count("your_pay") == 1
    assert persona.presses_used(profiles["wary_transferer"].objections[0]) >= 2


def test_an_unsuspicious_customer_does_not_press(profiles) -> None:
    """The dial has to change behaviour at the threshold and nowhere else."""
    persona = CustomerPersona(profile=profiles["cautious_saver"])
    for _ in range(5):
        persona.respond("The fund is a balanced growth fund.")
    assert persona.pressed_objections() == ()


def test_the_press_direction_tells_the_voice_it_is_asking_again(profiles) -> None:
    """A second press worded as a first mention erases the trainee's mistake."""
    persona = CustomerPersona(profile=profiles["wary_transferer"])
    persona.respond("The fund is a balanced growth fund.")
    persona.respond("It rebalances quarterly.")
    move = persona.respond("It has a long track record.")
    voice = LiveCustomerVoice(
        speaker=ModelSpeaker(
            role="customer",
            cassette=SessionCassette(path=Path("unused.json")),
            live_env_var=LIVE_CUSTOMER_ENV_VAR,
            model_env_var="LAB_CUSTOMER_MODEL",
            completion=echo_customer,
        ),
        system_prompt="stub",
        profile=profiles["wary_transferer"],
    )
    said = voice.speak(move=move, persona=persona, trainee_turn="It has a track record.", turn=3)
    assert "talked past it" in said or "back on the table" in said


# --------------------------------------------------------------------------- #
# Record and replay
# --------------------------------------------------------------------------- #


def test_a_recorded_session_replays_with_no_seam_and_no_switch(
    profiles, tmp_path: Path
) -> None:
    """Record once with stubs, then replay with nothing at all. Twice, identically."""
    first = run_stub_session(profiles["cautious_saver"], row=EU_ROW, tmp_path=tmp_path)
    assert first.recorded_turns > 0
    assert first.cassette_path.exists()

    replays = [
        run_live_session(
            EU_ROW,
            profile=profiles["cautious_saver"],
            root=tmp_path,
            model_label="stub-model",
        )
        for _ in range(2)
    ]
    for replay in replays:
        assert replay.recorded_turns == 0
        assert replay.replayed_turns == first.replayed_turns + first.recorded_turns
        assert [e.model_dump() for e in replay.result.trace.events[1:]] == [
            e.model_dump() for e in first.result.trace.events[1:]
        ]


def test_a_cassette_miss_refuses_instead_of_degrading(profiles, tmp_path: Path) -> None:
    """A run that silently stopped being live is a run with no provenance."""
    with pytest.raises(MissingTurnError) as caught:
        run_live_session(
            EU_ROW, profile=profiles["cautious_saver"], root=tmp_path, model_label="stub-model"
        )
    assert LIVE_TRAINEE_ENV_VAR in str(caught.value)


def test_a_stale_cassette_raises_rather_than_answering_the_wrong_question(
    profiles, tmp_path: Path
) -> None:
    """Positional replay would keep working after the other speaker changed."""
    outcome = run_stub_session(profiles["cautious_saver"], row=EU_ROW, tmp_path=tmp_path)
    document = json.loads(outcome.cassette_path.read_text())
    document["turns"][2]["context_sha256"] = "0" * 64
    outcome.cassette_path.write_text(json.dumps(document))

    with pytest.raises(StaleCassetteError, match="stale at"):
        run_live_session(
            EU_ROW, profile=profiles["cautious_saver"], root=tmp_path, model_label="stub-model"
        )


def test_a_cassette_recorded_for_another_session_is_refused(
    profiles, tmp_path: Path
) -> None:
    """Identity, checked on load, independently of the per-turn context digest."""
    outcome = run_stub_session(profiles["cautious_saver"], row=EU_ROW, tmp_path=tmp_path)
    document = json.loads(outcome.cassette_path.read_text())
    document["identity"]["competence"] = "exemplary"
    outcome.cassette_path.write_text(json.dumps(document))

    with pytest.raises(StaleCassetteError) as caught:
        SessionCassette.load(outcome.cassette_path, identity=outcome.key)
    assert "competence" in str(caught.value)


def test_a_cassette_with_no_identity_block_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "anonymous.json"
    path.write_text(json.dumps({"turns": []}))
    key = SessionKey(
        scenario_id="s",
        persona="p",
        competence="weak",
        prompt_sha256="a" * 64,
        trainee_model="m",
        customer_model="m",
        jurisdiction="eu-retail",
    )
    with pytest.raises(StaleCassetteError, match="no identity block"):
        SessionCassette.load(path, identity=key)


def test_the_cassette_never_holds_a_credential_or_a_route(
    profiles, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fixture is public. A route names somebody's private deployment."""
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "not-a-real-key-abc123")
    monkeypatch.setenv("LAB_TRAINEE_MODEL", "azure/private-deployment-name")
    outcome = run_stub_session(profiles["cautious_saver"], row=EU_ROW, tmp_path=tmp_path)
    body = outcome.cassette_path.read_text()
    assert "not-a-real-key-abc123" not in body
    assert "private-deployment-name" not in body
    assert "stub-model" in body


def test_a_session_that_fails_still_keeps_what_it_recorded(
    profiles, tmp_path: Path
) -> None:
    """A recording thrown away on an exception is money spent twice."""

    calls = {"n": 0}

    def dies_on_the_third(*, model, messages, temperature, max_tokens):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] >= 3:
            raise RuntimeError("provider fell over")
        return f"Question number {calls['n']}: what would you want this money to do?"

    with pytest.raises(RuntimeError, match="fell over"):
        run_live_session(
            EU_ROW,
            profile=profiles["cautious_saver"],
            root=tmp_path,
            trainee_completion=dies_on_the_third,
            customer_completion=echo_customer,
            model_label="stub-model",
        )
    saved = json.loads(EU_ROW and (tmp_path / "test-eu-live").glob("*.json").__next__().read_text())
    assert len(saved["turns"]) >= 2


# --------------------------------------------------------------------------- #
# The guards
# --------------------------------------------------------------------------- #


def test_a_looping_trainee_stops_and_says_why(profiles, tmp_path: Path) -> None:
    outcome = run_stub_session(
        profiles["cautious_saver"],
        row=EU_ROW,
        tmp_path=tmp_path,
        adviser=["What matters most to you?"] * 4,
    )
    assert outcome.trainee_stop == "repeated_turn"
    assert outcome.result.trace.last(EventKind.SESSION_END).get("stop_reason") == "repeated_turn"


def test_the_turn_budget_is_a_labelled_stop_not_a_silent_one(
    profiles, tmp_path: Path
) -> None:
    """A budget stop and an adviser who never closed must not share a bucket."""
    counter = {"n": 0}

    def endless(*, model, messages, temperature, max_tokens):  # noqa: ANN001
        counter["n"] += 1
        return f"Tell me more about point number {counter['n']}, please?"

    outcome = run_live_session(
        EU_ROW,
        profile=profiles["cautious_saver"],
        root=tmp_path,
        max_turns=4,
        trainee_completion=endless,
        customer_completion=echo_customer,
        model_label="stub-model",
    )
    assert outcome.turns == 4
    assert outcome.trainee_stop == "turn_budget"
    assert counter["n"] == 4  # the cap is checked before the completion is bought


def test_a_trainee_that_writes_the_customers_line_is_truncated_and_counted(
    profiles, tmp_path: Path
) -> None:
    outcome = run_stub_session(
        profiles["cautious_saver"],
        row=EU_ROW,
        tmp_path=tmp_path,
        adviser=[
            "What would you want this money to do?\nCustomer: I am worried about school fees.",
            "Thank you. [END OF SESSION]",
        ],
    )
    assert outcome.impersonations == 1
    said = outcome.result.trainee_utterances[0]
    assert "school fees" not in said


def test_a_trainee_that_says_nothing_at_all_raises(profiles, tmp_path: Path) -> None:
    """An empty transcript reads as absence on every criterion, not as no data."""
    with pytest.raises(ValueError, match="produced no turns"):
        run_live_session(
            EU_ROW,
            profile=profiles["cautious_saver"],
            root=tmp_path,
            trainee_completion=lambda **_: "[END OF SESSION]",
            customer_completion=echo_customer,
            model_label="stub-model",
        )


def test_the_session_closes_cleanly_when_the_trainee_ends_it(
    profiles, tmp_path: Path
) -> None:
    outcome = run_stub_session(profiles["cautious_saver"], row=EU_ROW, tmp_path=tmp_path)
    assert outcome.trainee_stop == "session_closed"
    assert outcome.turns == len(ADVISER_TURNS)


def test_the_ablation_runs_a_live_trainee_against_the_scripted_customer(
    profiles, tmp_path: Path
) -> None:
    """One half of the instrument held still, which is how a finding is localised."""
    outcome = run_stub_session(
        profiles["cautious_saver"], row=EU_ROW, tmp_path=tmp_path, live_customer=False
    )
    assert outcome.customer_leaks == 0
    assert outcome.key.customer_model == "scripted-voice"
    said = [
        str(e.get("text"))
        for e in outcome.result.trace.events_of_kind(EventKind.AGENT_UTTERANCE)
        if e.get("agent") == CUSTOMER_AGENT
    ]
    assert profiles["cautious_saver"].concerns[0].reveal.strip() in " ".join(said)


# --------------------------------------------------------------------------- #
# Gating, refusal and backoff
# --------------------------------------------------------------------------- #


def test_a_speaker_with_no_switch_refuses_and_names_the_switch() -> None:
    speaker = ModelSpeaker(
        role="trainee",
        cassette=SessionCassette(path=Path("unused.json")),
        live_env_var=LIVE_TRAINEE_ENV_VAR,
        model_env_var="LAB_TRAINEE_MODEL",
    )
    assert not speaker.live_enabled
    assert LIVE_TRAINEE_ENV_VAR in (speaker.refusal() or "")
    with pytest.raises(NotLiveError):
        speaker.require_live()


def test_a_switch_with_no_key_refuses_rather_than_pretending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Live but replayed" is the one status a run must never quietly report."""
    monkeypatch.setenv(LIVE_TRAINEE_ENV_VAR, "1")
    speaker = ModelSpeaker(
        role="trainee",
        cassette=SessionCassette(path=Path("unused.json")),
        live_env_var=LIVE_TRAINEE_ENV_VAR,
        model_env_var="LAB_TRAINEE_MODEL",
    )
    assert not speaker.live_enabled
    assert "no provider key" in (speaker.refusal() or "")


def test_a_rate_limit_is_retried_with_growing_delays_and_a_shared_pause() -> None:
    """Backoff is exercised through the injected seam, so it is actually tested."""

    class Throttled(Exception):
        status_code = 429

    attempts = {"n": 0}

    def flaky(*, model, messages, temperature, max_tokens):  # noqa: ANN001
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise Throttled()
        return "Good morning."

    naps: list[float] = []
    speaker = ModelSpeaker(
        role="trainee",
        cassette=SessionCassette(path=Path("unused.json")),
        live_env_var=LIVE_TRAINEE_ENV_VAR,
        model_env_var="LAB_TRAINEE_MODEL",
        completion=flaky,
        sleep=naps.append,
        retry_base_s=1.0,
    )
    assert speaker.say([{"role": "system", "content": "s"}]) == "Good morning."
    assert speaker.retries == 2
    # Two waits, doubling, and no more: one 429 must not be paid for twice.
    assert len(naps) == 2
    assert naps[0] == pytest.approx(1.0, abs=0.05)
    assert naps[1] == pytest.approx(2.0, abs=0.05)


def test_the_retry_budget_is_bounded() -> None:
    class Throttled(Exception):
        status_code = 429

    def always(*, model, messages, temperature, max_tokens):  # noqa: ANN001
        raise Throttled()

    speaker = ModelSpeaker(
        role="trainee",
        cassette=SessionCassette(path=Path("unused.json")),
        live_env_var=LIVE_TRAINEE_ENV_VAR,
        model_env_var="LAB_TRAINEE_MODEL",
        completion=always,
        sleep=lambda _s: None,
        max_retries=2,
        retry_base_s=0.0,
    )
    with pytest.raises(Exception):
        speaker.say([{"role": "system", "content": "s"}])
    assert speaker.retries == 2


def test_a_non_rate_limit_error_is_not_retried() -> None:
    calls = {"n": 0}

    def broken(*, model, messages, temperature, max_tokens):  # noqa: ANN001
        calls["n"] += 1
        raise ValueError("bad request")

    speaker = ModelSpeaker(
        role="trainee",
        cassette=SessionCassette(path=Path("unused.json")),
        live_env_var=LIVE_TRAINEE_ENV_VAR,
        model_env_var="LAB_TRAINEE_MODEL",
        completion=broken,
        sleep=lambda _s: None,
    )
    with pytest.raises(ValueError):
        speaker.say([{"role": "system", "content": "s"}])
    assert calls["n"] == 1


# --------------------------------------------------------------------------- #
# The matrix and its committed recordings
# --------------------------------------------------------------------------- #


def test_every_matrix_row_names_a_real_customer_and_jurisdiction() -> None:
    """A row that cannot be built is a row that reads as green when skipped."""
    from roleplay.register import required_codes

    known = load_customer_profiles()
    for row in LIVE_MATRIX:
        assert row.customer in known, row.scenario_id
        assert required_codes(row.jurisdiction)
        assert row.competence in COMPETENCES


def test_the_matrix_covers_the_whole_dial_and_more_than_one_jurisdiction() -> None:
    assert {row.competence for row in LIVE_MATRIX} == set(COMPETENCES)
    assert len({row.jurisdiction for row in LIVE_MATRIX}) >= 2
    assert len({row.scenario_id for row in LIVE_MATRIX}) == len(LIVE_MATRIX)


@pytest.mark.parametrize("row", LIVE_MATRIX, ids=lambda r: r.scenario_id)
def test_the_committed_cassettes_replay_offline(row: LiveRow) -> None:
    """The recordings in `fixtures/roleplay_live/` are the deliverable.

    This is the test a reviewer runs to check the live claims in the report: no
    key, no switch, no network, and the same sessions come back. It skips rather
    than fails when a cassette is absent, because a fresh matrix row is allowed to
    exist before it has been recorded — but a cassette that is present and does not
    replay is a hard failure.
    """
    profiles_by_key = load_customer_profiles()
    key = SessionKey.build(
        scenario_id=row.scenario_id,
        profile=profiles_by_key[row.customer],
        competence=row.competence,
        jurisdiction=row.jurisdiction,
        language=row.language,
        trainee_model="azure-openai/gpt-4.1",
        customer_model="azure-openai/gpt-4.1",
        temperature=0.0,
        turn_budget=12,
    )
    path = key.path_in(CASSETTE_ROOT)
    if not path.exists():
        pytest.skip(f"no committed cassette at {path}")
    outcome = run_live_session(
        row,
        profile=profiles_by_key[row.customer],
        root=CASSETTE_ROOT,
        model_label="azure-openai/gpt-4.1",
        save=False,
    )
    assert outcome.recorded_turns == 0
    assert outcome.turns >= 1
    assert set(outcome.result.trace.tool_names()) <= TOOL_NAMES
    assert outcome.result.trace.is_ordered()


# --------------------------------------------------------------------------- #
# The provider's content filter
# --------------------------------------------------------------------------- #


class Filtered(Exception):
    """Stands in for litellm's ContentPolicyViolationError, by duck type."""

    status_code = 400

    def __str__(self) -> str:  # pragma: no cover - message shape only
        return (
            "litellm.ContentPolicyViolationError: The response was filtered due to "
            "the prompt triggering Azure OpenAI's content management policy."
        )


def test_a_content_filter_is_recognised_by_message_as_well_as_by_class() -> None:
    """A proxy in front of the SDK surfaces a 400 and a sentence, nothing more."""
    from roleplay.live import _is_content_filtered, _is_rate_limited

    assert _is_content_filtered(Filtered())
    assert not _is_rate_limited(Filtered())


def test_a_filtered_turn_is_not_retried(profiles) -> None:
    """It is deterministic: retrying only pays to be refused again."""
    calls = {"n": 0}

    def refuses(*, model, messages, temperature, max_tokens):  # noqa: ANN001
        calls["n"] += 1
        raise Filtered()

    speaker = ModelSpeaker(
        role="trainee",
        cassette=SessionCassette(path=Path("unused.json")),
        live_env_var=LIVE_TRAINEE_ENV_VAR,
        model_env_var="LAB_TRAINEE_MODEL",
        completion=refuses,
        sleep=lambda _s: None,
    )
    with pytest.raises(ContentFilterError):
        speaker.say([{"role": "system", "content": "s"}])
    assert calls["n"] == 1
    assert speaker.retries == 0
    assert speaker.filtered == 1


def test_a_filtered_trainee_turn_ends_the_session_with_a_labelled_stop(
    profiles, tmp_path: Path
) -> None:
    """A filtered meeting is an outcome. A crash is not, and nor is "did not close"."""
    calls = {"n": 0}

    def refuses_after_two(*, model, messages, temperature, max_tokens):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] > 2:
            raise Filtered()
        return f"Question {calls['n']}: what would you want this money to do?"

    outcome = run_live_session(
        EU_ROW,
        profile=profiles["cautious_saver"],
        root=tmp_path,
        trainee_completion=refuses_after_two,
        customer_completion=echo_customer,
        model_label="stub-model",
    )
    assert outcome.trainee_stop == "content_filter"
    assert outcome.trainee_filtered == 1
    assert outcome.turns == 2
    end = outcome.result.trace.last(EventKind.SESSION_END)
    assert end.get("stop_reason") == "content_filter"
    # And it is still a scorable session, not a hole in the run.
    assert outcome.result.card.verdict in ("pass", "fail")


def test_a_filtered_customer_turn_keeps_the_move_and_counts_the_substitution(
    profiles, tmp_path: Path
) -> None:
    """The objection is already in the ledger; only the wording was refused."""
    outcome = run_live_session(
        EU_ROW,
        profile=profiles["cautious_saver"],
        root=tmp_path,
        trainee_completion=Scripted(
            ["The fund is a balanced growth fund and it is a good one. [END OF SESSION]"]
        ),
        customer_completion=lambda **_: (_ for _ in ()).throw(Filtered()),
        model_label="stub-model",
    )
    assert outcome.customer_filtered == 1
    assert outcome.voice_fallbacks == 1
    assert "raise_objection" in outcome.result.trace.tool_names()
    said = " ".join(
        str(e.get("text"))
        for e in outcome.result.trace.events_of_kind(EventKind.AGENT_UTTERANCE)
        if e.get("agent") == CUSTOMER_AGENT
    )
    assert "lost money last year" in said


def test_a_recorded_refusal_replays_as_a_refusal(profiles, tmp_path: Path) -> None:
    """Otherwise a re-run would silently repair a session the provider cut short."""
    calls = {"n": 0}

    def refuses_after_two(*, model, messages, temperature, max_tokens):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] > 2:
            raise Filtered()
        return f"Question {calls['n']}: what would you want this money to do?"

    first = run_live_session(
        EU_ROW,
        profile=profiles["cautious_saver"],
        root=tmp_path,
        trainee_completion=refuses_after_two,
        customer_completion=echo_customer,
        model_label="stub-model",
    )
    assert "[CONTENT FILTERED BY PROVIDER]" in first.cassette_path.read_text()

    replay = run_live_session(
        EU_ROW, profile=profiles["cautious_saver"], root=tmp_path, model_label="stub-model"
    )
    assert replay.recorded_turns == 0
    assert replay.trainee_stop == "content_filter"
    assert replay.turns == first.turns
