"""Tests for the judge itself: rendering, parsing, replay, versioning.

WHAT THIS DEMONSTRATES
----------------------
The judge's job is to be boring and predictable, so these tests pin down the
places where a judge is normally *not* predictable: what the model is actually
shown, what happens when its output does not match the contract, and what happens
when a prompt is edited after verdicts were recorded against it.

The parser gets the most attention. It is the component whose failure mode is
silent — a parser that guesses produces verdicts nobody asked for — and the
assertion that matters most in this file is `test_unparseable_output_never_passes`.
"""

from __future__ import annotations

import pytest

from lab.clock import FakeClock
from lab.judges.judge import (
    Judge,
    JudgeError,
    JudgeParseError,
    LiteLLMCompletion,
    LiveCallBlockedError,
    MissingRecordingError,
    PromptTemplate,
    PromptTemplateError,
    Recording,
    ReplayJudge,
    ScriptedCompletion,
    StaleRecordingError,
    model_from_env,
    parse_raw_verdict,
    prompt_digest,
    record_verdicts,
    render_tool_ledger,
    render_transcript,
)
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace

PROMPT = "Grade this call.\n\n{{transcript}}\n\nAnswer PASS or FAIL."


def _trace(session_id: str = "s1") -> Trace:
    """A short booking call that calls a tool and then claims a confirmation."""
    clock = FakeClock()
    builder = TraceBuilder(
        scenario_id="booking/party_of_six",
        adapter="text",
        session_id=session_id,
        clock=clock,
    )
    builder.session_start()
    clock.advance(0.5)
    builder.caller_utterance("Table for six on Friday at eight?")
    clock.advance(0.5)
    builder.agent_handoff("GreeterAgent", "BookingAgent", reason="booking request")
    clock.advance(0.5)
    call = builder.tool_call("search_tables", {"party_size": 6, "time": "20:00"})
    clock.advance(0.5)
    builder.tool_result("search_tables", call_id=call.get("call_id"), ok=True)
    clock.advance(0.5)
    builder.agent_utterance("That's confirmed for six on Friday.", agent="BookingAgent")
    clock.advance(0.5)
    builder.session_end(turns=1)
    return builder.build()


def _judge(answers: dict[str, str], **kwargs: object) -> Judge:
    defaults: dict[str, object] = {
        "name": "test_judge",
        "prompt": PROMPT,
        "version": "v1",
        "model": "test/stub",
        "completion": ScriptedCompletion(answers),
    }
    defaults.update(kwargs)
    return Judge(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Prompt templates
# --------------------------------------------------------------------------- #


def test_unknown_placeholder_fails_at_construction() -> None:
    """A typo'd field must fail when the judge is built, not at grading time."""
    with pytest.raises(PromptTemplateError) as exc:
        PromptTemplate("Grade {{transcirpt}}")
    assert "transcirpt" in str(exc.value)


def test_prompt_without_transcript_is_rejected() -> None:
    """A judge prompt with no transcript would grade nothing at all."""
    with pytest.raises(PromptTemplateError):
        PromptTemplate("Is this call fine? Answer PASS or FAIL.")


def test_prompt_may_contain_literal_json() -> None:
    """The reason placeholders are `{{...}}` rather than `str.format`.

    Judge prompts specify a JSON output contract. Under `str.format` the literal
    `{"verdict": "pass"}` in a prompt raises at render time — a crash caused
    entirely by the templating layer having an opinion about braces.
    """
    template = PromptTemplate(
        'Answer with {"verdict": "pass"} or {"verdict": "fail"}.\n{{transcript}}'
    )
    rendered = template.render({"transcript": "caller: hello"})
    assert '{"verdict": "pass"}' in rendered
    assert "caller: hello" in rendered


def test_digest_changes_with_the_prompt() -> None:
    assert prompt_digest("a") != prompt_digest("b")
    assert PromptTemplate("x {{transcript}}").digest == prompt_digest("x {{transcript}}")


# --------------------------------------------------------------------------- #
# What the judge is shown
# --------------------------------------------------------------------------- #


def test_transcript_excludes_tools_by_default() -> None:
    """The default rendering is utterances only — the scoping decision, tested.

    A judge that can see the tool ledger can answer "was the claim true" by
    lookup, which is a deterministic check's job. This test is what stops that
    from happening by accident during a refactor.
    """
    rendered = render_transcript(_trace())
    assert "caller: Table for six on Friday at eight?" in rendered
    assert "agent (BookingAgent): That's confirmed for six on Friday." in rendered
    assert "search_tables" not in rendered
    assert "[tool" not in rendered
    assert "handoff" not in rendered


def test_transcript_can_include_tools_when_asked() -> None:
    rendered = render_transcript(_trace(), include_tools=True)
    assert "[tool call] search_tables" in rendered
    assert "[tool result] search_tables -> ok" in rendered
    assert "[handoff] GreeterAgent -> BookingAgent" in rendered


def test_tool_ledger_pairs_calls_with_outcomes() -> None:
    assert "search_tables" in render_tool_ledger(_trace())
    assert "-> ok" in render_tool_ledger(_trace())


def test_tool_ledger_says_so_when_no_tools_ran() -> None:
    clock = FakeClock()
    builder = TraceBuilder(scenario_id="x", adapter="text", session_id="s", clock=clock)
    builder.session_start()
    builder.agent_utterance("Good evening.", agent="GreeterAgent")
    assert render_tool_ledger(builder.build()) == "(no tools were called)"


def test_rendered_prompt_hides_tools_from_this_judge() -> None:
    judge = _judge({"s1": "PASS. fine"})
    assert "search_tables" not in judge.render(_trace())


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PASS. Nothing was claimed.", True),
        ("FAIL. It said the table was booked.", False),
        ("**fail** — past tense claim", False),
        ("VERDICT: PASS\nThe assistant only offered.", True),
        ("verdict = fail; it confirmed", False),
        ('{"verdict": "fail", "critique": "claimed"}', False),
        ('{"verdict": "pass", "critique": "offer only"}', True),
        ('```json\n{"verdict": "fail", "critique": "claimed"}\n```', False),
        ('Here you go:\n{"pass": true, "reason": "offer only"}', True),
    ],
)
def test_parser_accepts_the_formats_models_actually_emit(raw: str, expected: bool) -> None:
    passed, critique, _ = parse_raw_verdict(raw)
    assert passed is expected
    assert critique


def test_parser_extracts_quoted_evidence_from_json() -> None:
    _, critique, evidence = parse_raw_verdict(
        '{"verdict": "fail", "quote": "You\'re all set.", "critique": "claim"}'
    )
    assert evidence == "You're all set."
    assert critique == "claim"


def test_parser_refuses_to_guess() -> None:
    """No third fallback that scans for the word "fail" anywhere in the text.

    A critique explaining why an answer did *not* fail contains "fail" too. A
    parser that guesses invents verdicts; this one raises and lets the caller
    decide.
    """
    with pytest.raises(JudgeParseError):
        parse_raw_verdict("The assistant did not make a claim that would fail this check.")
    with pytest.raises(JudgeParseError):
        parse_raw_verdict("")


def test_yes_and_no_are_not_verdicts() -> None:
    """"No" is not an answer a parser can read without knowing the question.

    "No, it didn't claim a booking" is a *pass* under this judge's rubric and a
    *fail* under one phrased the other way round. Accepting bare yes/no would make
    the verdict depend on prompt phrasing — a silent, systematic error. Inside a
    JSON object the key supplies the polarity, so `{"pass": true}` is fine.
    """
    with pytest.raises(JudgeParseError):
        parse_raw_verdict("no, the assistant did not claim it")
    with pytest.raises(JudgeParseError):
        parse_raw_verdict("Yes — it claimed the booking was made.")
    assert parse_raw_verdict('{"pass": false, "critique": "claimed"}')[0] is False


def test_unparseable_output_never_passes() -> None:
    """The most important assertion in this file.

    Strict mode raises; lenient mode records a FAIL flagged as a parse error.
    Neither returns "pass". A judge that defaults to pass turns a provider outage
    into a green build.
    """
    strict = _judge({"s1": "I'm not sure, sorry."})
    with pytest.raises(JudgeParseError):
        strict.judge(_trace())

    lenient = _judge({"s1": "I'm not sure, sorry."}, strict=False)
    verdict = lenient.judge(_trace())
    assert verdict.passed is False
    assert verdict.parse_error is True
    assert verdict.raw == "I'm not sure, sorry."


# --------------------------------------------------------------------------- #
# Verdicts
# --------------------------------------------------------------------------- #


def test_verdict_carries_provenance() -> None:
    verdict = _judge({"s1": 'FAIL. it claimed a booking'}).judge(_trace())
    assert verdict.item_id == "s1"
    assert verdict.label == "fail"
    assert verdict.judge == "test_judge"
    assert verdict.prompt_version == "v1"
    assert verdict.model == "test/stub"
    assert verdict.raw == "FAIL. it claimed a booking"


def test_judge_all_grades_in_order() -> None:
    judge = _judge({"a": "PASS. clean", "b": "FAIL. claimed"})
    verdicts = judge.judge_all([_trace("a"), _trace("b")])
    assert [v.item_id for v in verdicts] == ["a", "b"]
    assert [v.label for v in verdicts] == ["pass", "fail"]


def test_item_id_defaults_to_the_session_id() -> None:
    judge = _judge({"other-session": "PASS. fine"})
    assert judge.judge(_trace("other-session")).item_id == "other-session"


# --------------------------------------------------------------------------- #
# Replay
# --------------------------------------------------------------------------- #


def test_record_then_replay_round_trips(tmp_path) -> None:
    """The offline path is the live path with one object swapped.

    Recording stores the model's *raw* text, so replay re-runs the parser. A
    recording of parsed verdicts would leave the parser untested exactly where it
    is most likely to break.
    """
    trace = _trace()
    live_ish = _judge({"s1": 'FAIL. it claimed a booking'})
    path = tmp_path / "verdicts.jsonl"
    recording = record_verdicts(live_ish, [("s1", trace)], path)
    assert len(recording) == 1
    assert recording.calls[0].prompt_sha256 == live_ish.request(trace).prompt_sha256

    replay = ReplayJudge(
        recording=path, name="test_judge", prompt=PROMPT, version="v1", model="test/stub"
    )
    verdict = replay.judge(trace)
    assert verdict.passed is False
    assert verdict.raw == "FAIL. it claimed a booking"


def test_editing_the_prompt_invalidates_the_recording(tmp_path) -> None:
    """A stale recording raises instead of quietly answering the old question.

    This is the mechanism behind "a prompt change invalidates a calibration". Edit
    a prompt, replay old verdicts, and the numbers would describe a prompt that no
    longer exists.
    """
    path = tmp_path / "verdicts.jsonl"
    record_verdicts(_judge({"s1": "PASS. fine"}), [("s1", _trace())], path)

    edited = ReplayJudge(
        recording=path,
        name="test_judge",
        prompt=PROMPT + "\nBe strict.",
        version="v1",
        model="test/stub",
    )
    with pytest.raises(StaleRecordingError) as exc:
        edited.judge(_trace())
    assert "stale" in str(exc.value)


def test_stale_recording_can_be_inspected_deliberately(tmp_path) -> None:
    path = tmp_path / "verdicts.jsonl"
    record_verdicts(_judge({"s1": "PASS. fine"}), [("s1", _trace())], path)
    lenient = ReplayJudge(
        recording=path,
        strict_prompt_hash=False,
        name="test_judge",
        prompt=PROMPT + "\nBe strict.",
        version="v1",
        model="test/stub",
    )
    assert lenient.judge(_trace()).passed is True


def test_missing_recording_entry_raises(tmp_path) -> None:
    path = tmp_path / "verdicts.jsonl"
    record_verdicts(_judge({"s1": "PASS. fine"}), [("s1", _trace())], path)
    replay = ReplayJudge(
        recording=path, name="test_judge", prompt=PROMPT, version="v1", model="test/stub"
    )
    with pytest.raises(MissingRecordingError):
        replay.judge(_trace("never-recorded"))


def test_duplicate_item_in_a_recording_is_an_error() -> None:
    """Which of two recorded answers replays must not depend on file order."""
    call = {
        "item_id": "s1",
        "judge": "j",
        "prompt_version": "v1",
        "model": "m",
        "prompt_sha256": "x",
        "raw": "PASS. a",
    }
    recording = Recording.model_validate({"calls": [call, {**call, "raw": "FAIL. b"}]})
    with pytest.raises(ValueError, match="two entries"):
        recording.by_item()


def test_replay_judge_refuses_a_completion_argument(tmp_path) -> None:
    path = tmp_path / "verdicts.jsonl"
    Recording().save(path)
    with pytest.raises(JudgeError):
        ReplayJudge(
            recording=path,
            completion=ScriptedCompletion({}),
            name="j",
            prompt=PROMPT,
            version="v1",
            model="m",
        )


# --------------------------------------------------------------------------- #
# The live path stays shut unless asked
# --------------------------------------------------------------------------- #


def test_live_calls_are_opt_in(monkeypatch) -> None:
    """The cardinal rule of the repo, enforced at the one place it could break.

    Note the judge is built with a model that does not exist: the point is that
    the gate raises *before* any provider lookup, so a stray live judge in a test
    suite fails in microseconds instead of hanging on a socket.
    """
    monkeypatch.delenv("LAB_LIVE_JUDGE", raising=False)
    judge = Judge(
        name="j", prompt=PROMPT, version="v1", model="does-not-exist/model",
        completion=LiteLLMCompletion(),
    )
    with pytest.raises(LiveCallBlockedError):
        judge.judge(_trace())


def test_live_gate_reads_the_env_var(monkeypatch) -> None:
    completion = LiteLLMCompletion()
    monkeypatch.delenv("LAB_LIVE_JUDGE", raising=False)
    assert completion.enabled() is False
    monkeypatch.setenv("LAB_LIVE_JUDGE", "1")
    assert completion.enabled() is True
    monkeypatch.setenv("LAB_LIVE_JUDGE", "0")
    assert completion.enabled() is False


def test_no_default_model_anywhere(monkeypatch) -> None:
    """`lab` ships no model id. Providers, prices and ids move; a pinned default
    silently bills whoever forgot to look."""
    monkeypatch.delenv("LAB_JUDGE_MODEL", raising=False)
    with pytest.raises(JudgeError, match="no judge model configured"):
        model_from_env()
    monkeypatch.setenv("LAB_JUDGE_MODEL", "provider/some-model")
    assert model_from_env() == "provider/some-model"

    with pytest.raises(TypeError):
        Judge(name="j", prompt=PROMPT, version="v1")  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Versioning
# --------------------------------------------------------------------------- #


def test_with_prompt_drops_the_calibration() -> None:
    """A new prompt has not been measured, so it must not inherit old numbers."""
    judge = _judge({"s1": "PASS. fine"})
    from lab.judges.calibration import LabelledTrace, calibrate

    calibrate(
        judge,
        [LabelledTrace(item_id="s1", label="pass", trace=_trace(), note="clean")],
    )
    assert judge.calibration is not None

    v2 = judge.with_prompt(PROMPT + "\nBe careful.", version="v2")
    assert v2.calibration is None
    assert v2.version == "v2"
    assert v2.name == judge.name
    assert v2.model == judge.model


def test_attach_calibration_refuses_a_foreign_report() -> None:
    """"This judge is calibrated" must not degrade into "some judge was"."""
    from lab.judges.calibration import LabelledTrace, calibrate

    judge = _judge({"s1": "PASS. fine"})
    report = calibrate(
        judge,
        [LabelledTrace(item_id="s1", label="pass", trace=_trace(), note="clean")],
    )
    other = _judge({"s1": "PASS. fine"}, version="v2")
    with pytest.raises(JudgeError, match="refusing to attach"):
        other.attach_calibration(report)


def test_repr_says_whether_it_is_calibrated() -> None:
    assert "UNCALIBRATED" in repr(_judge({}))
