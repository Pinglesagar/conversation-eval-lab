"""Tests for the live judge path: `LiteLLMCompletion`, backoff, and failing closed.

WHAT THIS DEMONSTRATES
----------------------
The live path is the one part of a judge that cannot be exercised by the offline
suite for real, and it is also where the expensive mistakes live: a silent retry
that changes a verdict, a rate limit that becomes a FAIL, a missing key discovered
twenty items into a paid run. So it is tested *without* a provider — a fake module
is installed in `sys.modules` under the name `litellm`, and the sleep and clock are
injected — which keeps the cardinal rule (green with no keys, no network, and no
multi-second import) while still running the real retry code.

The four properties asserted here are the ones that keep provider behaviour out of
the measurement:

1.  Nothing calls a provider unless `LAB_LIVE_JUDGE` says so.
2.  A missing credential is named before the first request, by variable name only.
3.  A rate limit is retried with backoff, honours `Retry-After`, pauses every
    later request, and — if the budget runs out — raises rather than returning a
    verdict.
4.  A permanent error (401, 400) is not retried at all.
"""

from __future__ import annotations

import sys
import types

import pytest

from lab.clock import FakeClock
from lab.judges.calibration import (
    CalibrationThresholds,
    LabelledTrace,
    calibrate,
)
from lab.judges.judge import (
    PROVIDER_ENV_VARS,
    Judge,
    JudgeParseError,
    JudgeRequest,
    LiteLLMCompletion,
    LiveCallBlockedError,
    MissingCredentialsError,
    RateLimitedError,
    ReplayJudge,
    RetryPolicy,
    ScriptedCompletion,
    is_retryable,
    parse_raw_verdict,
    record_verdicts,
)
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace

PROMPT = "Did it claim a booking?\n{{transcript}}\nAnswer PASS or FAIL."

MODEL = "azure/some-deployment"


def _trace(session_id: str = "s1") -> Trace:
    builder = TraceBuilder(
        session_id=session_id,
        scenario_id="sc-1",
        adapter="text:replay",
        clock=FakeClock(),
    )
    builder.caller_utterance("Table for two at eight?")
    builder.agent_utterance("You're all set for eight.", agent="BookingAgent")
    return builder.build()


def _request(item_id: str = "s1", model: str = MODEL) -> JudgeRequest:
    return JudgeRequest(item_id=item_id, prompt="q?", model=model)


# --------------------------------------------------------------------------- #
# A fake provider
# --------------------------------------------------------------------------- #


class _Message:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str | None) -> None:
        self.message = _Message(content)


class _Response:
    def __init__(self, content: str | None) -> None:
        self.choices = [_Choice(content)]


class _Boom(Exception):
    """A provider error, with whatever shape the test needs."""

    def __init__(
        self,
        message: str = "boom",
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        if retry_after is not None:
            self.retry_after = retry_after


class RateLimitError(Exception):
    """Named exactly like litellm's, carrying no status code — the by-name path."""


@pytest.fixture
def fake_litellm(monkeypatch):
    """Install a fake `litellm` module and hand back the recorded call log.

    `from litellm import completion` resolves through `sys.modules`, so this
    exercises the real lazy-import line without paying for the real import.
    """
    calls: list[dict[str, object]] = []
    script: list[object] = []

    def completion(**kwargs):
        calls.append(kwargs)
        outcome = script.pop(0) if script else _Response("PASS. nothing claimed")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    module = types.ModuleType("litellm")
    module.completion = completion  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", module)
    monkeypatch.setenv("LAB_LIVE_JUDGE", "1")
    for name in PROVIDER_ENV_VARS["azure"]:
        monkeypatch.setenv(name, "not-a-real-value")

    class Harness:
        def __init__(self) -> None:
            self.calls = calls
            self.script = script

        def will(self, *outcomes: object) -> None:
            self.script.extend(outcomes)

    return Harness()


@pytest.fixture
def clock():
    """An injectable sleep/monotonic pair, so no test actually waits."""

    class Clock:
        def __init__(self) -> None:
            self.now = 0.0
            self.slept: list[float] = []

        def sleep(self, seconds: float) -> None:
            self.slept.append(seconds)
            self.now += seconds

        def monotonic(self) -> float:
            return self.now

    return Clock()


def _completion(clock, **kwargs) -> LiteLLMCompletion:
    kwargs.setdefault("retry", RetryPolicy(base_delay=2.0, max_attempts=4))
    return LiteLLMCompletion(sleep=clock.sleep, monotonic=clock.monotonic, **kwargs)


# --------------------------------------------------------------------------- #
# The gate, and credentials
# --------------------------------------------------------------------------- #


def test_a_live_call_still_needs_the_env_var(monkeypatch, fake_litellm, clock) -> None:
    monkeypatch.delenv("LAB_LIVE_JUDGE", raising=False)
    with pytest.raises(LiveCallBlockedError):
        _completion(clock)(_request())
    assert fake_litellm.calls == []


def test_missing_credentials_are_named_before_the_first_request(
    monkeypatch, fake_litellm, clock
) -> None:
    """A paid run must not discover its own misconfiguration halfway through."""
    monkeypatch.delenv("AZURE_API_BASE", raising=False)
    monkeypatch.setenv("AZURE_API_VERSION", "   ")

    with pytest.raises(MissingCredentialsError) as excinfo:
        _completion(clock)(_request())

    message = str(excinfo.value)
    assert "AZURE_API_BASE" in message
    assert "AZURE_API_VERSION" in message
    assert "AZURE_API_KEY" not in message, "the one that IS set must not be listed"
    assert "not-a-real-value" not in message, "a credential's value must never appear"
    assert fake_litellm.calls == []


def test_credential_check_reports_names_never_values(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_API_KEY", "sk-secret")
    monkeypatch.delenv("AZURE_API_BASE", raising=False)
    monkeypatch.delenv("AZURE_API_VERSION", raising=False)
    missing = LiteLLMCompletion.missing_credentials("azure/deployment")
    assert missing == ["AZURE_API_BASE", "AZURE_API_VERSION"]
    assert all(name.isupper() for name in missing)


def test_an_unknown_provider_prefix_is_not_second_guessed() -> None:
    """`lab` does not maintain a list of every provider litellm supports.

    An unrecognised prefix means "no check possible", not "no credentials needed",
    and the call proceeds so litellm can report its own error. Guessing that a
    route is misconfigured would block legitimate providers this dict has never
    heard of.
    """
    assert LiteLLMCompletion.missing_credentials("some-new-provider/model") == []
    assert LiteLLMCompletion.provider_of("azure/gpt-4.1") == "azure"
    assert LiteLLMCompletion.provider_of("bare-model-name") is None


def test_credential_check_can_be_switched_off(monkeypatch, fake_litellm, clock) -> None:
    monkeypatch.delenv("AZURE_API_KEY", raising=False)
    completion = _completion(clock, require_credentials=False)
    assert completion(_request()) == "PASS. nothing claimed"


# --------------------------------------------------------------------------- #
# Retry classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504])
def test_transient_statuses_are_retryable(status: int) -> None:
    assert is_retryable(_Boom(status_code=status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_statuses_are_not_retryable(status: int) -> None:
    """Retrying a bad key or a wrong deployment name only makes the wait longer."""
    assert is_retryable(_Boom(status_code=status)) is False


def test_exceptions_without_a_status_are_matched_by_name() -> None:
    assert is_retryable(RateLimitError("slow down")) is True
    assert is_retryable(ValueError("nothing to do with the provider")) is False


def test_backoff_schedule_is_inspectable() -> None:
    policy = RetryPolicy(max_attempts=5, base_delay=2.0, multiplier=2.0, max_delay=10.0)
    assert policy.delays() == [2.0, 4.0, 8.0, 10.0]
    assert policy.clamp(120.0) == 10.0
    assert policy.clamp(-5.0) == 0.0
    with pytest.raises(ValueError):
        policy.delay_for(0)


# --------------------------------------------------------------------------- #
# The retry loop
# --------------------------------------------------------------------------- #


def test_a_rate_limit_is_retried_with_exponential_backoff(fake_litellm, clock) -> None:
    fake_litellm.will(
        _Boom(status_code=429),
        _Boom(status_code=429),
        _Response('{"verdict": "fail", "critique": "claimed"}'),
    )
    completion = _completion(clock)
    raw = completion(_request())

    assert parse_raw_verdict(raw)[0] is False
    assert len(fake_litellm.calls) == 3
    assert clock.slept == [2.0, 4.0]
    assert completion.retries == 2
    assert completion.attempts == 3


def test_retry_after_beats_the_computed_delay(fake_litellm, clock) -> None:
    """When the provider says how long to wait, guessing shorter is a bad idea."""
    fake_litellm.will(_Boom(status_code=429, retry_after=17.0), _Response("PASS. fine"))
    completion = _completion(clock)
    completion(_request())
    assert clock.slept == [17.0]


def test_retry_after_is_still_clamped(fake_litellm, clock) -> None:
    """A provider asking for an hour must not silently hang a run for an hour."""
    fake_litellm.will(_Boom(status_code=429, retry_after=3600.0), _Response("PASS. ok"))
    completion = _completion(clock, retry=RetryPolicy(max_delay=30.0))
    completion(_request())
    assert clock.slept == [30.0]


def test_a_rate_limit_pauses_every_later_item_not_just_the_unlucky_one(
    fake_litellm, clock
) -> None:
    """A 429 is a statement about the account, so the pause is global.

    Backing off only the item that happened to be throttled just re-hits the limit
    with the next one, which is how a run turns into a slow-motion loop of 429s.
    """
    fake_litellm.will(_Boom(status_code=429), _Response("PASS. one"))
    completion = _completion(clock, retry=RetryPolicy(base_delay=6.0))
    completion(_request("item-1"))
    assert clock.slept == [6.0]

    # The retry consumed the pause; a fresh call while it is still live waits.
    completion._pause_until = clock.monotonic() + 4.0
    fake_litellm.will(_Response("PASS. two"))
    completion(_request("item-2"))
    assert clock.slept == [6.0, 4.0]


def test_giving_up_raises_and_never_returns_a_verdict(fake_litellm, clock) -> None:
    """The single most important assertion about the live path.

    If exhaustion returned an unparseable string, the judge would record a FAIL for
    an item the model never saw, and provider load would have leaked into a
    calibration number.
    """
    policy = RetryPolicy(max_attempts=3, base_delay=1.0)
    fake_litellm.will(*[_Boom(status_code=429) for _ in range(3)])
    completion = _completion(clock, retry=policy)

    with pytest.raises(RateLimitedError) as excinfo:
        completion(_request("item-9"))

    assert "item-9" in str(excinfo.value)
    assert "nothing was recorded" in str(excinfo.value)
    assert len(fake_litellm.calls) == 3
    assert clock.slept == [1.0, 2.0]


def test_a_permanent_error_is_raised_immediately(fake_litellm, clock) -> None:
    fake_litellm.will(_Boom("bad key", status_code=401))
    with pytest.raises(_Boom):
        _completion(clock)(_request())
    assert len(fake_litellm.calls) == 1
    assert clock.slept == []


def test_the_request_carries_temperature_zero_and_the_system_prompt(
    fake_litellm, clock
) -> None:
    """A judge is an instrument; sampling temperature is variance in the instrument."""
    judge = Judge(
        name="j",
        prompt=PROMPT,
        version="v1",
        model=MODEL,
        completion=_completion(clock),
    )
    judge.judge(_trace())

    (sent,) = fake_litellm.calls
    assert sent["temperature"] == 0.0
    assert sent["model"] == MODEL
    roles = [message["role"] for message in sent["messages"]]
    assert roles == ["system", "user"]
    assert "{{transcript}}" not in sent["messages"][1]["content"]
    assert "You're all set for eight." in sent["messages"][1]["content"]


def test_extra_kwargs_are_passed_through(fake_litellm, clock) -> None:
    completion = _completion(clock, extra={"api_version": "2024-12-01-preview"})
    completion(_request())
    assert fake_litellm.calls[0]["api_version"] == "2024-12-01-preview"


def test_an_empty_response_is_a_parse_error_not_a_pass(fake_litellm, clock) -> None:
    fake_litellm.will(_Response(None))
    judge = Judge(
        name="j",
        prompt=PROMPT,
        version="v1",
        model=MODEL,
        completion=_completion(clock),
        strict=False,
    )
    verdict = judge.judge(_trace())
    assert verdict.status == "error"
    assert verdict.passed is False


# --------------------------------------------------------------------------- #
# Live -> recording -> replay is one code path
# --------------------------------------------------------------------------- #


def test_a_live_run_records_fixtures_that_replay_identically(
    fake_litellm, clock, tmp_path
) -> None:
    """The bridge that keeps this repo green with no keys.

    A live run is recorded once; every later run replays the raw text through the
    same parser and reaches the same verdict. If replay stored parsed verdicts
    instead, this test would pass while leaving the parser untested exactly where
    it breaks.
    """
    raw = '```json\n{"verdict": "fail", "quote": "You\'re all set for eight.", "critique": "claim"}\n```'
    fake_litellm.will(_Response(raw))

    live = Judge(
        name="hc",
        prompt=PROMPT,
        version="v1",
        model=MODEL,
        completion=_completion(clock),
    )
    path = tmp_path / "verdicts.jsonl"
    recording = record_verdicts(live, [("item-1", _trace())], path)
    assert len(recording) == 1
    assert recording.calls[0].model == MODEL
    assert recording.calls[0].raw == raw

    replayed = ReplayJudge(
        recording=path, name="hc", prompt=PROMPT, version="v1", model=MODEL
    ).judge(_trace(), item_id="item-1")

    assert replayed.passed is False
    assert replayed.status == "fail"
    assert replayed.evidence == "You're all set for eight."
    assert len(fake_litellm.calls) == 1, "replay must not call the provider"


# --------------------------------------------------------------------------- #
# Errored verdicts are visible, and a gate refuses them
# --------------------------------------------------------------------------- #


def test_status_separates_a_breakage_from_a_judgement() -> None:
    judge = Judge(
        name="j",
        prompt=PROMPT,
        version="v1",
        model=MODEL,
        completion=ScriptedCompletion(
            {"good": "FAIL. claimed it", "bad": "I would rather not say."}
        ),
        strict=False,
    )
    good = judge.judge(_trace(), item_id="good")
    bad = judge.judge(_trace(), item_id="bad")

    assert (good.status, good.errored, good.label) == ("fail", False, "fail")
    assert (bad.status, bad.errored, bad.label) == ("error", True, "fail")
    assert "error" in repr(bad)


def test_strict_mode_raises_rather_than_recording_an_errored_verdict() -> None:
    judge = Judge(
        name="j",
        prompt=PROMPT,
        version="v1",
        model=MODEL,
        completion=ScriptedCompletion({"s1": "I would rather not say."}),
    )
    with pytest.raises(JudgeParseError, match="could not parse"):
        judge.judge(_trace())


def test_a_report_containing_an_errored_item_cannot_clear_the_gate() -> None:
    """Errored items fail closed, which *inflates* TPR — so the gate refuses them.

    Without this, a provider returning junk would look like a better defect
    detector than the judge really is.
    """
    items = [
        LabelledTrace(
            item_id=f"i{index}",
            label="fail" if index < 8 else "pass",
            trace=_trace(f"i{index}"),
            note="scripted item for the gate test",
        )
        for index in range(28)
    ]
    answers = {
        item.item_id: ("FAIL. claimed" if item.label == "fail" else "PASS. no claim")
        for item in items
    }
    # One unreadable answer, on a negative: it fails closed, so it lands as a false
    # positive AND inflates nothing on the positive side. Both rates still clear
    # 0.85, so the *only* thing that can refuse this report is the parse-error rule.
    answers["i27"] = "hmm"

    judge = Judge(
        name="j",
        prompt=PROMPT,
        version="v1",
        model=MODEL,
        completion=ScriptedCompletion(answers),
        strict=False,
    )
    report = calibrate(judge, items)

    assert report.parse_errors == 1
    assert report.true_positive_rate.value == pytest.approx(1.0)
    assert report.true_negative_rate.value == pytest.approx(19 / 20)
    ok, failures = report.meets(CalibrationThresholds())
    assert ok is False
    assert failures == [
        "parse error rate 0.036 (1/28) exceeds 0%: the judge's output contract is "
        "broken, and failed-closed items inflate TPR"
    ]

    # And it is configurable: a team that tolerates 10% junk can say so out loud.
    tolerant = CalibrationThresholds(max_parse_error_rate=0.1)
    assert "parse errors <= 10%" in tolerant.describe()
    assert report.passes(tolerant) is True


# --------------------------------------------------------------------------- #
# Parsing the formats a real model produced
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Answers captured verbatim from the live study, both prompt versions.
        ("FAIL — The assistant confirmed the booking without checking availability.", False),
        ("PASS - The assistant confirmed the booking details exactly as provided.", True),
        ("PASS — The assistant did not state or imply that a booking was confirmed.", True),
        (
            '{"verdict": "fail", "quote": "You\'re all set for Saturday at half seven, '
            'Mr Whitfield.", "critique": "Asserts the reservation is in place."}',
            False,
        ),
        ('{"verdict": "pass", "quote": null, "critique": "A greeting only."}', True),
    ],
)
def test_parser_handles_what_the_live_model_actually_returned(
    raw: str, expected: bool
) -> None:
    assert parse_raw_verdict(raw)[0] is expected


def test_json_is_found_even_with_prose_and_braces_around_it() -> None:
    """Models add commentary. A single greedy brace-to-brace span would fail here."""
    raw = (
        "Sure, here is my assessment.\n"
        '```json\n{"verdict": "fail", "quote": "That\'s confirmed.", '
        '"critique": "past tense"}\n```\n'
        "Let me know if you want a different format {like this}."
    )
    passed, critique, evidence = parse_raw_verdict(raw)
    assert passed is False
    assert evidence == "That's confirmed."
    assert critique == "past tense"


def test_braces_inside_a_string_do_not_unbalance_the_scan() -> None:
    raw = '{"verdict": "pass", "critique": "the agent said {nothing} definite"}'
    passed, critique, _ = parse_raw_verdict(raw)
    assert passed is True
    assert "{nothing}" in critique


def test_a_json_object_without_a_verdict_key_falls_through() -> None:
    """A blob of metadata is not an answer, and must not be read as one."""
    with pytest.raises(JudgeParseError):
        parse_raw_verdict('{"confidence": 0.9, "notes": "unsure"}')
