"""The roleplay adapter seam: any two-method `Trainee` as the adviser under test.

`roleplay.runtime.Trainee` has always been two methods. What this file tests is the
plumbing that lets something *other* than the built-in model trainee satisfy them
through `run_live_session` and `run_spoken_call`: the `LAB_TRAINEE_FACTORY` env
var, the `--trainee-factory` flag, the three runnable examples under
`examples/adapters/`, and — most important — that the default path is exactly
what both runners hardcoded before the seam existed, so every committed cassette
and the committed spoken call still replay unchanged.

Every test here runs with every key and switch unset. The HTTP example is
exercised against a loopback server started inside the test; nothing leaves the
machine.
"""

from __future__ import annotations

import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from roleplay import spoken
from roleplay.live import (
    CASSETTE_ROOT,
    LIVE_CUSTOMER_ENV_VAR,
    LIVE_MATRIX,
    LIVE_TRAINEE_ENV_VAR,
    TRAINEE_FACTORY_ENV_VAR,
    TRAINEE_MAX_TOKENS,
    TRAINEE_MODEL_ENV_VAR,
    LiveRow,
    LiveTrainee,
    ModelSpeaker,
    SessionCassette,
    SessionKey,
    TraineeContext,
    TraineeFactoryError,
    build_trainee,
    load_customer_profiles,
    model_trainee,
    resolve_trainee_factory,
    run_live_session,
    trainee_prompt,
)
from roleplay import live as live_module
from roleplay.runtime import RoleplayCoach, ScriptedVoice, Trainee
from roleplay.scorer import RubricScorer

from tests.roleplay_fixtures import corpus, profiles  # noqa: F401

ECHO = "examples.adapters.echo_trainee:build_trainee"
HTTP = "examples.adapters.http_trainee:build_trainee"
CALLABLE = "examples.adapters.callable_trainee:build_trainee"
EXAMPLES = (ECHO, HTTP, CALLABLE)

ROW = LiveRow(
    scenario_id="adapter-eu-test",
    customer="cautious_saver",
    competence="competent",
    jurisdiction="eu-retail",
)


@pytest.fixture(autouse=True)
def no_live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No switch, no key, no factory: the state a fresh clone is in."""
    for name in (
        TRAINEE_FACTORY_ENV_VAR,
        "TRAINEE_HTTP_URL",
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


def context_for(profile, *, cassette: SessionCassette | None = None, max_turns: int = 12):
    return TraineeContext(
        scenario_id=ROW.scenario_id,
        profile=profile,
        competence=ROW.competence,
        jurisdiction=ROW.jurisdiction,
        language=ROW.language,
        max_turns=max_turns,
        model_label="stub-model",
        cassette=cassette,
    )


def run_custom(profile, tmp_path: Path, factory=None, **kwargs):
    """One session against the scripted customer: no model anywhere in the loop."""
    return run_live_session(
        ROW,
        profile=profile,
        root=tmp_path,
        live_customer=False,
        model_label="stub-model",
        trainee_factory=factory,
        **kwargs,
    )


def payload_of(trace, kind: str) -> dict:
    (event,) = [e for e in trace.events if e.kind == kind]
    return event.payload


# --------------------------------------------------------------------------- #
# The contract, and the three examples
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("dotted", EXAMPLES)
def test_each_example_builds_something_that_satisfies_the_protocol(profiles, dotted) -> None:
    factory = resolve_trainee_factory(dotted)
    trainee = factory(context_for(profiles["cautious_saver"]))
    assert isinstance(trainee, Trainee)
    # The trace records `repr(trainee)` as `trainee_source`; a default object repr
    # would put a memory address into a fixture, so every example defines one.
    assert "object at 0x" not in repr(trainee)


@pytest.mark.parametrize("dotted", EXAMPLES)
def test_each_example_is_small(dotted) -> None:
    """The examples are the documentation. ~40 lines of code, docstring excluded."""
    module = importlib.import_module(dotted.split(":")[0])
    source = Path(module.__file__).read_text(encoding="utf-8")
    body = source.split('"""', 2)[2]  # everything after the module docstring
    code_lines = [line for line in body.splitlines() if line.strip()]
    assert len(code_lines) <= 45, f"{dotted}: {len(code_lines)} non-blank lines of code"


def test_a_custom_trainee_runs_end_to_end_and_is_graded(profiles, tmp_path: Path) -> None:
    """The whole pipeline — loop, register, persona, scorer — on a trainee that
    is not a model, selected by dotted path, with no key in the environment."""
    outcome = run_custom(profiles["cautious_saver"], tmp_path, factory=ECHO)

    assert outcome.turns == 3
    assert outcome.trainee_stop == "echo_budget"
    card = outcome.card
    assert card.verdict in {"pass", "fail"}
    assert 0 <= card.total <= card.max_total == 20
    assert set(card.criteria) == set(RubricScorer().score_trace(outcome.result.trace).criteria)

    trace = outcome.result.trace
    start = payload_of(trace, "session_start")
    assert start["trainee_source"] == "EchoTrainee(turns=3)"
    assert start["customer_voice"] == "ScriptedVoice()"
    end = payload_of(trace, "session_end")
    assert end["stop_reason"] == "echo_budget"
    assert end["turns"] == 3
    # The register and the persona ran on the custom trainee's words.
    assert "load_customer_profile" in set(trace.tool_names())
    first = [e for e in trace.events if e.kind == "caller_utterance"][0]
    assert first.payload["text"].startswith("Good morning.")
    # Nothing was recorded or replayed: there is no model behind this trainee.
    assert outcome.recorded_turns == 0 and outcome.replayed_turns == 0
    assert outcome.impersonations == 0 and outcome.trainee_filtered == 0


def test_the_env_var_selects_the_factory(
    profiles, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TRAINEE_FACTORY_ENV_VAR, CALLABLE)
    outcome = run_custom(profiles["cautious_saver"], tmp_path)
    start = payload_of(outcome.result.trace, "session_start")
    assert start["trainee_source"] == "CallableTrainee(sample_agent)"
    assert outcome.turns == 2
    assert outcome.trainee_stop == "agent_ended"


def test_an_explicit_factory_beats_the_env_var(
    profiles, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TRAINEE_FACTORY_ENV_VAR, CALLABLE)
    outcome = run_custom(profiles["cautious_saver"], tmp_path, factory=ECHO)
    assert payload_of(outcome.result.trace, "session_start")["trainee_source"].startswith(
        "EchoTrainee("
    )


def test_a_plain_callable_is_accepted_as_the_factory(profiles, tmp_path: Path) -> None:
    """In-process callers need not go through a dotted string."""
    seen: list[TraineeContext] = []

    class Two:
        stop_reason = None

        def open(self):
            return "Good morning. What brings you in today?"

        def reply(self, customer_turn):
            self.stop_reason = "one_and_done"
            return None

        def __repr__(self):
            return "Two()"

    def factory(context: TraineeContext):
        seen.append(context)
        return Two()

    outcome = run_custom(profiles["cautious_saver"], tmp_path, factory=factory, max_turns=7)
    assert outcome.turns == 1 and outcome.trainee_stop == "one_and_done"
    (context,) = seen
    assert context.scenario_id == ROW.scenario_id
    assert context.jurisdiction == "eu-retail" and context.language == "en"
    assert context.max_turns == 7 and context.profile.key == "cautious_saver"
    assert context.cassette is not None  # the runner always supplies one


def test_the_http_example_runs_against_a_local_endpoint(
    profiles, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stdlib HTTP adapter, against a loopback server that speaks its protocol."""
    requests: list[dict] = []
    lines = iter(
        [
            "Good morning. What would you want this money to be doing in five years?",
            "And how much of it might you need at short notice?",
            "Thank you. Nothing is decided today; shall I send the documents over?",
        ]
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            reply = json.dumps({"reply": next(lines, None)}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):  # silence the test output
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("TRAINEE_HTTP_URL", f"http://127.0.0.1:{server.server_port}/reply")
        outcome = run_custom(profiles["cautious_saver"], tmp_path, factory=HTTP)
    finally:
        server.shutdown()
        server.server_close()

    assert outcome.turns == 3
    assert outcome.trainee_stop == "agent_ended"
    assert [r["event"] for r in requests] == ["open", "reply", "reply", "reply"]
    assert requests[0]["scenario_id"] == ROW.scenario_id
    assert [r.get("turn") for r in requests[1:]] == [1, 2, 3]
    assert all(r["customer_turn"] for r in requests[1:])
    start = payload_of(outcome.result.trace, "session_start")
    assert start["trainee_source"].startswith("HttpTrainee(url='http://127.0.0.1:")


# --------------------------------------------------------------------------- #
# The two ways a trainee says "I am done"
# --------------------------------------------------------------------------- #


def test_open_returning_none_declines_cleanly(profiles, tmp_path: Path) -> None:
    """A trainee that will not start produces no transcript, and the harness says
    so in one sentence that carries the trainee's own reason — it does not score
    an empty session, and it does not crash on a missing attribute."""

    class Declines:
        stop_reason = "declined_out_of_hours"

        def open(self):
            return None

        def reply(self, customer_turn):  # pragma: no cover - never reached
            raise AssertionError("reply() must not be asked after open() declined")

        def __repr__(self):
            return "Declines()"

    with pytest.raises(ValueError) as caught:
        run_custom(profiles["cautious_saver"], tmp_path, factory=lambda context: Declines())
    message = str(caught.value)
    assert "produced no turns" in message
    assert "declined_out_of_hours" in message
    assert ROW.scenario_id in message


def test_reply_returning_none_stops_with_a_readable_stop_reason(
    profiles, tmp_path: Path
) -> None:
    class HandsOff:
        stop_reason: str | None = None

        def open(self):
            return "Good morning. Let me check who should take this."

        def reply(self, customer_turn):
            self.stop_reason = "handed_off_to_human"
            return None

        def __repr__(self):
            return "HandsOff()"

    outcome = run_custom(profiles["cautious_saver"], tmp_path, factory=lambda c: HandsOff())
    assert outcome.turns == 1
    assert outcome.trainee_stop == "handed_off_to_human"
    assert payload_of(outcome.result.trace, "session_end")["stop_reason"] == "handed_off_to_human"
    assert outcome.row_line().endswith("stop=handed_off_to_human")


def test_a_trainee_with_no_stop_reason_attribute_still_reads(profiles, tmp_path: Path) -> None:
    """The protocol is two methods. `stop_reason` is optional, and its absence is
    named as such rather than raised on."""

    class Bare:
        def open(self):
            return "Good morning. How can I help?"

        def reply(self, customer_turn):
            return None

        def __repr__(self):
            return "Bare()"

    outcome = run_custom(profiles["cautious_saver"], tmp_path, factory=lambda c: Bare())
    assert outcome.turns == 1
    assert payload_of(outcome.result.trace, "session_end")["stop_reason"] == "no_reply"
    assert outcome.trainee_stop == "unknown"


# --------------------------------------------------------------------------- #
# The default path is what the two runners hardcoded before the seam
# --------------------------------------------------------------------------- #


def test_the_default_factory_builds_the_model_trainee_exactly_as_before(
    profiles, tmp_path: Path
) -> None:
    assert resolve_trainee_factory(None) is model_trainee
    cassette = SessionCassette.load(tmp_path / "c.json", identity=None)
    profile = profiles["cautious_saver"]
    context = TraineeContext(
        scenario_id=ROW.scenario_id,
        profile=profile,
        competence=ROW.competence,
        jurisdiction=ROW.jurisdiction,
        language=ROW.language,
        max_turns=9,
        model_label="stub-model",
        temperature=0.25,
        cassette=cassette,
        trainee_model="some/route",
    )
    trainee = build_trainee(context)
    assert isinstance(trainee, LiveTrainee)
    assert trainee.max_turns == 9
    assert trainee.system_prompt == trainee_prompt(
        competence=ROW.competence, profile=profile, jurisdiction=ROW.jurisdiction, language="en"
    )
    speaker = trainee.speaker
    assert isinstance(speaker, ModelSpeaker)
    assert speaker.role == "trainee"
    assert speaker.cassette is cassette
    assert speaker.live_env_var == LIVE_TRAINEE_ENV_VAR
    assert speaker.model_env_var == TRAINEE_MODEL_ENV_VAR
    assert speaker.model == "some/route"
    assert speaker.model_label == "stub-model"
    assert speaker.temperature == 0.25
    assert speaker.max_tokens == TRAINEE_MAX_TOKENS
    assert speaker.completion is None


def _committed_rows() -> list[LiveRow]:
    profiles_by_key = load_customer_profiles()
    rows = []
    for row in LIVE_MATRIX:
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
        if key.path_in(CASSETTE_ROOT).exists():
            rows.append(row)
    return rows


@pytest.mark.parametrize("row", _committed_rows(), ids=lambda r: r.scenario_id)
def test_a_committed_cassette_replays_identically_through_the_seam(row: LiveRow) -> None:
    """Two replays of one committed recording: through `run_live_session` with the
    factory unset, and through the pre-seam construction — `ModelSpeaker` plus
    `LiveTrainee`, built inline — driven by the same coach. Same utterances, same
    card. If the default factory changed the prompt by one character, the digests
    would miss and the first replay would raise before this compared anything.
    """
    profile = load_customer_profiles()[row.customer]
    label = "azure-openai/gpt-4.1"
    through_seam = run_live_session(
        row, profile=profile, root=CASSETTE_ROOT, model_label=label, save=False
    )
    assert through_seam.recorded_turns == 0
    assert through_seam.replayed_turns >= through_seam.turns >= 1

    key = SessionKey.build(
        scenario_id=row.scenario_id,
        profile=profile,
        competence=row.competence,
        jurisdiction=row.jurisdiction,
        language=row.language,
        trainee_model=label,
        customer_model=label,
        temperature=0.0,
        turn_budget=12,
    )
    cassette = SessionCassette.load(key.path_in(CASSETTE_ROOT), identity=key)
    inline_trainee = LiveTrainee(
        speaker=ModelSpeaker(
            role="trainee",
            cassette=cassette,
            live_env_var=LIVE_TRAINEE_ENV_VAR,
            model_env_var=TRAINEE_MODEL_ENV_VAR,
            model_label=label,
            temperature=0.0,
            max_tokens=TRAINEE_MAX_TOKENS,
        ),
        system_prompt=trainee_prompt(
            competence=row.competence,
            profile=profile,
            jurisdiction=row.jurisdiction,
            language=row.language,
        ),
        max_turns=12,
    )
    inline_voice = live_module.LiveCustomerVoice(
        speaker=ModelSpeaker(
            role="customer",
            cassette=cassette,
            live_env_var=LIVE_CUSTOMER_ENV_VAR,
            model_env_var=live_module.CUSTOMER_MODEL_ENV_VAR,
            model_label=label,
            temperature=0.0,
            max_tokens=live_module.CUSTOMER_MAX_TOKENS,
        ),
        system_prompt=live_module.customer_prompt(profile),
        profile=profile,
    )
    inline = RoleplayCoach(scorer=RubricScorer()).run(
        scenario_id=row.scenario_id,
        profile=profile,
        trainee=inline_trainee,
        customer_voice=inline_voice,
        jurisdiction=row.jurisdiction,
        language=row.language,
        max_turns=12,
        session_id=f"{row.scenario_id}-{row.competence}",
    )
    assert through_seam.result.trainee_utterances == inline.trainee_utterances
    assert through_seam.card.total == inline.card.total
    assert dict(through_seam.card.criteria) == dict(inline.card.criteria)
    assert through_seam.trainee_stop == (inline_trainee.stop_reason or "unknown")
    seam_trace, inline_trace = through_seam.result.trace, inline.trace
    assert [e.kind for e in seam_trace.events] == [e.kind for e in inline_trace.events]
    assert (
        payload_of(seam_trace, "session_start")["trainee_source"]
        == payload_of(inline_trace, "session_start")["trainee_source"]
    )


def test_the_committed_spoken_call_replays_to_the_same_scorecard_bytes(
    tmp_path: Path,
) -> None:
    """`replay_spoken_call()` recomputes the whole call; the scorecards it writes
    must be byte for byte the committed file. This is the offline half of the
    byte-for-byte gate, applied to the one spoken fixture."""
    result = spoken.replay_spoken_call()
    written = tmp_path / spoken.SCORECARDS_PATH.name
    spoken._write_scorecards(written, result)
    assert written.read_bytes() == spoken.SCORECARDS_PATH.read_bytes()


def test_spoken_wraps_whatever_the_seam_returns(profiles) -> None:
    """`SpokenTrainee.inner` is any `Trainee`, not the model one specifically."""
    trainee = build_trainee(context_for(profiles["cautious_saver"]), factory=ECHO)
    wrapped = spoken.SpokenTrainee.__dataclass_fields__["inner"]
    assert isinstance(trainee, Trainee)
    assert wrapped.type in ("Trainee", Trainee)


def test_missing_for_live_drops_the_adviser_route_for_an_external_trainee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_line = f"{TRAINEE_MODEL_ENV_VAR} (the adviser's litellm route)"
    assert route_line in spoken.missing_for_live()
    assert route_line not in spoken.missing_for_live(external_trainee=True)
    monkeypatch.setenv(TRAINEE_FACTORY_ENV_VAR, ECHO)
    assert route_line not in spoken.missing_for_live()
    # Everything else is still demanded: the customer and the scorer are models.
    assert any("customer's litellm route" in line for line in spoken.missing_for_live())


# --------------------------------------------------------------------------- #
# Bad configuration fails in a sentence that names the setting
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "dotted, expect",
    [
        ("no_such_module_xyz:build_trainee", "ModuleNotFoundError"),
        ("examples.adapters.echo_trainee:no_such_name", "AttributeError"),
    ],
)
def test_a_bad_dotted_path_names_the_path(dotted: str, expect: str) -> None:
    with pytest.raises(TraineeFactoryError) as caught:
        resolve_trainee_factory(dotted)
    message = str(caught.value)
    assert dotted in message
    assert expect in message
    assert "package.module:callable" in message
    assert "examples/adapters/" in message


def test_a_path_to_something_not_callable_is_refused() -> None:
    with pytest.raises(TraineeFactoryError, match="roleplay.live:CASSETTE_ROOT.*not.*callable"):
        resolve_trainee_factory("roleplay.live:CASSETTE_ROOT")


def test_a_factory_that_returns_a_non_trainee_is_refused(profiles) -> None:
    def wrong(context):
        return object()

    with pytest.raises(TraineeFactoryError) as caught:
        build_trainee(context_for(profiles["cautious_saver"]), factory=wrong)
    message = str(caught.value)
    assert "wrong" in message and "open()" in message and "reply(" in message


def test_the_env_var_is_named_when_it_is_the_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TRAINEE_FACTORY_ENV_VAR, "no_such_module_xyz:build_trainee")
    with pytest.raises(TraineeFactoryError, match=TRAINEE_FACTORY_ENV_VAR):
        resolve_trainee_factory(None)


def test_the_live_runner_exits_2_on_a_bad_factory_before_any_session(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = live_module.main(
        ["--trainee-factory", "no_such_module_xyz:build_trainee", "--root", str(tmp_path)]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "no_such_module_xyz:build_trainee" in captured.err
    assert "Traceback" not in captured.err
    assert not list(tmp_path.iterdir())  # no session was started


def test_the_spoken_runner_exits_2_on_a_bad_factory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = spoken.main(["--trainee-factory", "no_such_module_xyz:build_trainee"])
    captured = capsys.readouterr()
    assert code == 2
    assert "no_such_module_xyz:build_trainee" in captured.err
    assert "Traceback" not in captured.err


def test_the_live_runner_runs_a_custom_trainee_across_the_matrix(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The one command from docs/ADAPTER.md, end to end, offline."""
    code = live_module.main(
        [
            "--trainee-factory",
            ECHO,
            "--scripted-customer",
            "--only",
            "live-eu-cautious",
            "--root",
            str(tmp_path),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "stop reasons: echo_budget=3" in out
    assert "turns recorded live this run: 0; replayed from cassette: 0" in out
