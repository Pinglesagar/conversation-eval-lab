"""The spoken-call path, tested with no API key, no network and no spend.

WHAT THIS FILE IS DEFENDING
---------------------------
`roleplay.spoken` joins two tiers that never met: the audio tier, which proved
single utterances survive a real TTS -> STT round trip, and the roleplay tier,
which graded whole conversations in text. The join has exactly three properties
worth protecting, and every test here is one of them:

1.  **The scorer grades what was HEARD.** A disclosure the adviser said and the
    channel mangled must not be credited. `test_scorer_grades_the_heard_text` and
    the `channel_effect` tests hold that line by construction rather than by
    reading the code.
2.  **The default path is untouched.** The audio seam in `roleplay.runtime` is a
    duck-typed `take_audio_note()`; a speaker without it must produce the
    byte-identical trace it produced before the seam existed. That is
    `test_text_session_trace_is_unchanged_by_the_seam`.
3.  **A fresh clone reproduces the committed call.** Not "reads a summary of it"
    — *recomputes* it: the committed notes drive the production loop, and the
    trace, the register and both score cards come out again. The pinned numbers
    in `TestCommittedCall` are what stop a prose claim about the call from
    drifting away from the call.

Two engine doubles do the work. `ChannelTTS` stashes the text it was handed and
returns a tone; `ChannelSTT` reads that stash and returns it through a `mangle`
callable the test chooses. Together they are a channel with a dial on it: set
`mangle` to the identity and recognition is perfect, set it to drop a word and a
recognition error flows into the graded conversation exactly as a real
mishearing would. That is the only way to test "a mis-heard disclosure loses its
credit" without buying a mishearing from a vendor and hoping it recurs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import pytest

from lab.trace.schema import EventKind
from lab.voice.engines.base import DEFAULT_SAMPLE_RATE, SynthesisResult, Transcription

from roleplay import spoken
from roleplay.live import (
    CUSTOMER_MAX_TOKENS,
    TRAINEE_MAX_TOKENS,
    LiveCustomerVoice,
    LiveTrainee,
    ModelSpeaker,
    NotLiveError,
    SessionCassette,
    SessionKey,
    customer_prompt,
    load_customer_profiles,
    trainee_prompt,
)
from roleplay.runtime import RoleplayCoach, ScriptedTrainee
from roleplay.scorer import CUSTOMER_AGENT, RubricScorer, session_view


# --------------------------------------------------------------------------- #
# The channel doubles
# --------------------------------------------------------------------------- #

#: Seconds of tone per word. A round number so a duration in a trace can be
#: checked by counting words rather than by trusting the double.
SECONDS_PER_WORD: float = 0.25


class ChannelTTS:
    """Tone out, text stashed, so the paired STT double can complete the round trip.

    A real synthesiser destroys the text — that is its job — and a double that
    handed the text straight to the recogniser would not be testing a channel at
    all. This one keeps the two legs genuinely separate (the STT double receives
    audio and reads the stash by position, exactly as the real pair receives
    audio and reads it acoustically) while remaining deterministic.
    """

    def __init__(self, log: list[str], *, hz: float = 180.0) -> None:
        self.log = log
        self.hz = hz
        self.name = "tts:test-channel"
        self.is_replay = False
        self.model_id = "test-channel-v1"
        self.apply_text_normalization = "on"
        self.characters_spent = 0
        self.credits_spent = 0
        self.cached_lines = 0
        self.requests = 0

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return f"{self.name} (test double, {SECONDS_PER_WORD}s per word)"

    def synthesise(
        self, text: str, *, sample_rate: int = DEFAULT_SAMPLE_RATE, voice: str | None = None
    ) -> SynthesisResult:
        self.log.append(text)
        self.characters_spent += len(text)
        self.credits_spent += len(text)
        self.requests += 1
        samples = max(1, len(text.split())) * int(SECONDS_PER_WORD * sample_rate)
        audio = 0.2 * np.sin(2 * np.pi * self.hz * np.arange(samples) / sample_rate)
        return SynthesisResult(
            audio=audio,
            sample_rate=sample_rate,
            engine=self.name,
            voice=voice,
            synthesis_s=0.01,
            text=text,
            # A published spoken form, so the WER reference is the one
            # `WER_NORMALISATION.md` requires rather than the input string.
            spoken_text=text,
        )


class ChannelSTT:
    """Transcribes the stashed text through `mangle` — a recognition-error dial."""

    def __init__(
        self, log: list[str], mangle: Callable[[str], str] | None = None
    ) -> None:
        self.log = log
        self.mangle = mangle or (lambda text: text)
        self.name = "stt:test-channel"
        self.is_replay = False
        self.want_display = True
        self.requests = 0
        self.audio_seconds = 0.0
        self.index = 0

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return f"{self.name} (test double)"

    def transcribe(
        self, audio: np.ndarray, *, sample_rate: int = DEFAULT_SAMPLE_RATE
    ) -> Transcription:
        heard = self.mangle(self.log[self.index])
        self.index += 1
        # Two requests per clip, like the real engine with `want_display=True`,
        # so `_submitted_seconds` is exercised against a double that bills the
        # same way rather than one that quietly agrees with it.
        self.requests += 2
        self.audio_seconds += len(audio) / float(sample_rate)
        return Transcription(
            text=heard,
            engine=self.name,
            provenance="engine",
            confidence=0.93,
            language="en",
            transcribe_s=0.02,
            formatting="raw",
            display_text=heard.upper(),
        )


# --------------------------------------------------------------------------- #
# A scripted spoken session, driven by injected completions
# --------------------------------------------------------------------------- #

#: An exemplary adviser: an early status disclosure, discovery, a risk warning,
#: and a close. Written here rather than drawn from the corpus because these
#: tests need to know exactly which words carry which requirement — that is what
#: makes "the channel dropped a disclosure" a checkable claim.
ADVISER_TURNS: tuple[str, ...] = (
    "Good morning Ms Bergstrom, thank you for coming in. Before we begin I should "
    "say that I am a restricted adviser, so I can only recommend products from our "
    "own range, and I will explain how I am paid. What would you like to get out "
    "of our conversation today?",
    "That is helpful, thank you. Can you tell me a little more about what worries "
    "you when you think about investing?",
    # Two registered disclosures, in the register's own wording — `capital_at_risk`
    # and `past_performance`. The exact phrasing matters: these tests are about
    # what the channel does to a disclosure that *would* have been credited, so a
    # turn that was never going to be credited would prove nothing.
    "I understand completely. I should be clear that the value of your investment "
    "can fall, so it is worth going in with that in mind. Past performance is not "
    "a guide to future performance.",
    "Given everything you have told me, shall we book a follow-up next week to go "
    "through the paperwork together? [END]",
)

CUSTOMER_TURNS: tuple[str, ...] = (
    "I would like my savings to work harder, but I am nervous about losing money.",
    "My worry is that the market falls right after I put the money in.",
    "That is reassuring, but I would still like to be careful about this.",
    "Yes, let us book that follow-up.",
)


def _completions(script: Sequence[str]) -> Callable[..., str]:
    """An injected completion seam that reads down a script."""
    remaining = list(script)

    def complete(**_: object) -> str:
        if not remaining:
            raise AssertionError("the session asked for more turns than the test wrote")
        return remaining.pop(0)

    return complete


class SpokenSession:
    """One scripted spoken session and everything a test needs to assert on it."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        mangle: Callable[[str], str] | None = None,
        character_cap: int = spoken.DEFAULT_CHARACTER_CAP,
        max_turns: int = 4,
        adviser: Sequence[str] = ADVISER_TURNS,
    ) -> None:
        # Pinned, deliberately not `SPOKEN_ROW.customer`: these tests are about
        # the channel, and following whichever persona the recorded call happens
        # to use would make them change behaviour every time that choice changes.
        profiles = load_customer_profiles()
        self.profile = profiles["cautious_saver"]
        key = SessionKey.build(
            scenario_id="spoken-test",
            profile=self.profile,
            competence="exemplary",
            jurisdiction="eu-retail",
            language="en",
            trainee_model="test-double",
            customer_model="test-double",
            temperature=0.0,
            turn_budget=max_turns,
        )
        cassette = SessionCassette.load(tmp_path / "cassette.jsonl", identity=key)
        cassette.identity = key

        def speaker(role: str, script: Sequence[str], max_tokens: int) -> ModelSpeaker:
            return ModelSpeaker(
                role=role,
                cassette=cassette,
                live_env_var="LAB_UNUSED_IN_TESTS",
                model_env_var="LAB_UNUSED_IN_TESTS",
                model_label="test-double",
                max_tokens=max_tokens,
                completion=_completions(script),
            )

        inner_trainee = LiveTrainee(
            speaker=speaker("trainee", adviser, TRAINEE_MAX_TOKENS),
            system_prompt=trainee_prompt(
                competence="exemplary",
                profile=self.profile,
                jurisdiction="eu-retail",
                language="en",
            ),
            max_turns=max_turns,
        )
        inner_voice = LiveCustomerVoice(
            speaker=speaker("customer", CUSTOMER_TURNS, CUSTOMER_MAX_TOKENS),
            system_prompt=customer_prompt(self.profile),
            profile=self.profile,
        )

        self.log: list[str] = []
        self.tts = ChannelTTS(self.log)
        self.stt = ChannelSTT(self.log, mangle)
        self.ledger = spoken.SpokenLedger(character_cap=character_cap)
        self.trainee = spoken.SpokenTrainee(
            inner=inner_trainee,
            channel=spoken.AudioChannel(
                tts=self.tts,
                stt=self.stt,
                voice="voice-adviser",
                speaker="trainee",
                ledger=self.ledger,
            ),
        )
        self.voice = spoken.SpokenCustomerVoice(
            inner=inner_voice,
            channel=spoken.AudioChannel(
                tts=self.tts,
                stt=self.stt,
                voice="voice-customer",
                speaker="customer",
                ledger=self.ledger,
            ),
        )
        self.max_turns = max_turns
        self.conversation = RoleplayCoach(scorer=RubricScorer()).converse(
            scenario_id="spoken-test",
            profile=self.profile,
            trainee=self.trainee,
            customer_voice=self.voice,
            jurisdiction="eu-retail",
            language="en",
            max_turns=max_turns,
            session_id="spoken-test-001",
            adapter=spoken.SPOKEN_ADAPTER,
        )

    @property
    def notes(self) -> list[spoken.AudioTurnNote]:
        return self.ledger.notes

    def card(self):
        return RubricScorer().score_trace(self.conversation.trace)

    def effect(self) -> spoken.ChannelEffect:
        return spoken.channel_effect(
            self.notes,
            profile=self.profile,
            scenario_id="spoken-test",
            jurisdiction="eu-retail",
            language="en",
            max_turns=self.max_turns,
            trainee_stop=self.trainee.stop_reason or "unknown",
            customer_leaks=self.voice.leaks,
            trainee_repr=repr(self.trainee),
            voice_repr=repr(self.voice),
        )


@pytest.fixture()
def session(tmp_path: Path) -> SpokenSession:
    """A faithful channel: everything sent is heard."""
    return SpokenSession(tmp_path)


# --------------------------------------------------------------------------- #
# The seam: the default path must be untouched
# --------------------------------------------------------------------------- #


class TestTheSeamIsInvisibleWithoutAudio:
    def test_text_session_trace_is_unchanged_by_the_seam(self) -> None:
        """A speaker with no `take_audio_note` produces the trace it always did.

        The audio seam is a duck-typed method lookup in the loop. This is the
        test that says the lookup missing is not merely tolerated but is the
        *identical* path: same event kinds, same confidence, same engine labels.
        """
        profile = load_customer_profiles()["cautious_saver"]
        conversation = RoleplayCoach(scorer=RubricScorer()).converse(
            scenario_id="text-control",
            profile=profile,
            trainee=ScriptedTrainee(("Hello, I am a restricted adviser.", "Shall we book a follow-up?")),
            jurisdiction="eu-retail",
            language="en",
            session_id="text-control-001",
        )
        inbound = conversation.trace.events_of_kind(EventKind.TRANSCRIPT_IN)
        assert inbound, "a text session still records what the harness received"
        for event in inbound:
            assert event.get("confidence") == 1.0
            assert event.engine == "stt:scripted"
            # The honest spoken-path keys must be absent, not empty: a text turn
            # has no "text sent to a synthesiser", and a null in that field would
            # read as "sent nothing".
            assert "text_sent" not in event.payload
            assert "display_text_unscored" not in event.payload

    def test_adapter_defaults_to_text_when_not_told_otherwise(self) -> None:
        profile = load_customer_profiles()["cautious_saver"]
        conversation = RoleplayCoach(scorer=RubricScorer()).converse(
            scenario_id="text-control",
            profile=profile,
            trainee=ScriptedTrainee(("Hello.",)),
            session_id="text-control-002",
        )
        assert conversation.trace.adapter == "roleplay:text"


# --------------------------------------------------------------------------- #
# The channel itself
# --------------------------------------------------------------------------- #


class TestTheChannel:
    def test_every_turn_crosses_tts_and_stt(self, session: SpokenSession) -> None:
        assert len(session.notes) == 8  # four adviser turns, four customer turns
        assert [n.speaker for n in session.notes] == ["trainee", "customer"] * 4
        assert session.tts.requests == 8
        assert all(n.tts_engine == "tts:test-channel" for n in session.notes)
        assert all(n.stt_engine == "stt:test-channel" for n in session.notes)

    def test_each_side_has_its_own_voice(self, session: SpokenSession) -> None:
        """Two voices, so the assembled recording is listenable as a dialogue."""
        assert session.trainee.channel.voice != session.voice.channel.voice

    def test_note_carries_both_texts_and_the_evidence(self, session: SpokenSession) -> None:
        note = session.notes[0]
        assert note.text_sent == ADVISER_TURNS[0]
        assert note.text_heard == ADVISER_TURNS[0]  # faithful channel
        assert note.spoken_form == ADVISER_TURNS[0]
        assert note.reference_source == "spoken-form"
        assert note.display_text == ADVISER_TURNS[0].upper()
        assert note.stt_confidence == pytest.approx(0.93)
        assert len(note.audio_sha256) == 64
        assert note.duration_s > 0
        assert note.characters == len(ADVISER_TURNS[0])

    def test_wer_reference_prefers_the_spoken_form(self) -> None:
        """The rule from WER_NORMALISATION.md, asserted rather than assumed."""
        base = dict(
            speaker="trainee", order=0, text_sent="sent", text_heard="heard",
            clip_key="k", audio_sha256="d" * 64, duration_s=1.0, num_bytes=10,
            tts_engine="t", stt_engine="s", characters=4, characters_charged=4,
        )
        published = spoken.AudioTurnNote(**base, spoken_form="the spoken form")
        assert published.wer_reference == "the spoken form"
        assert published.reference_source == "spoken-form"
        withheld = spoken.AudioTurnNote(**base)
        assert withheld.wer_reference == "sent"
        assert withheld.reference_source == "caller-input"

    def test_display_text_is_never_the_scored_string(self, session: SpokenSession) -> None:
        """`display_text` is upper-cased by the double, so any leak is loud."""
        for note in session.notes:
            assert note.text_heard != note.display_text
        for event in session.conversation.trace.events_of_kind(EventKind.CALLER_UTTERANCE):
            assert not str(event.get("text", "")).isupper()


# --------------------------------------------------------------------------- #
# The trace, and what the scorer sees
# --------------------------------------------------------------------------- #


class TestTheTrace:
    def test_adapter_says_spoken(self, session: SpokenSession) -> None:
        assert session.conversation.trace.adapter == "roleplay:spoken"

    def test_inbound_transcripts_carry_the_real_engine_and_confidence(
        self, session: SpokenSession
    ) -> None:
        """Where the text path wrote `confidence=1.0, engine="text:live"`."""
        for event in session.conversation.trace.events_of_kind(EventKind.TRANSCRIPT_IN):
            assert event.engine == "stt:test-channel"
            assert event.get("confidence") == pytest.approx(0.93)
            assert event.get("formatting") == "raw"
            assert event.get("text_sent")

    def test_both_texts_are_on_every_spoken_utterance(self, session: SpokenSession) -> None:
        caller = session.conversation.trace.events_of_kind(EventKind.CALLER_UTTERANCE)
        assert len(caller) == 4
        for event in caller:
            assert "text_sent" in event.payload
        agent = [
            e
            for e in session.conversation.trace.events_of_kind(EventKind.AGENT_UTTERANCE)
            if e.get("agent") == CUSTOMER_AGENT
        ]
        assert len(agent) == 4
        for event in agent:
            assert "text_sent" in event.payload
            assert event.get("confidence") == pytest.approx(0.93)

    def test_audio_evidence_is_on_the_trace(self, session: SpokenSession) -> None:
        emitted = session.conversation.trace.events_of_kind(EventKind.AUDIO_EMITTED)
        assert len(emitted) == 8
        digests = {str(e.get("audio_sha256")) for e in emitted}
        assert len(digests) == 8, "eight different utterances must be eight clips"
        assert {e.actor for e in emitted} == {"caller", "agent"}

    def test_the_scorer_reads_the_spoken_session_unchanged(
        self, session: SpokenSession
    ) -> None:
        """`session_view` is not forked: the same projection, over a spoken trace."""
        view = session_view(session.conversation.trace)
        assert len(view.trainee_turns) == 4
        assert len(view.customer_turns) == 4
        assert view.jurisdiction == "eu-retail"
        card = session.card()
        assert card.verdict in {"pass", "fail"}
        assert card.max_total == 20


# --------------------------------------------------------------------------- #
# The point of the whole exercise: grading what was heard
# --------------------------------------------------------------------------- #


def _drop_the_risk_warning(text: str) -> str:
    """A plausible mishearing that destroys one mandatory disclosure and nothing else.

    "fall" -> "four" is the kind of substitution a recogniser really makes on a
    short, unstressed word, and it lands squarely on the registered wording of
    `capital_at_risk` while leaving `past_performance` in the same turn intact.
    That separation is the point: it makes the resulting ledger difference
    attributable to one word rather than to a turn being garbled in general.
    """
    return text.replace(
        "the value of your investment can fall",
        "the value of your investment can four",
    )


class TestTheScorerGradesWhatWasHeard:
    def test_heard_text_reaches_the_scorer_not_sent_text(self, tmp_path: Path) -> None:
        session = SpokenSession(tmp_path, mangle=lambda t: t.replace("Bergstrom", "Berkstrom"))
        view = session_view(session.conversation.trace)
        assert "Berkstrom" in view.trainee_turns[0]
        assert "Bergstrom" not in view.trainee_turns[0]
        # And the sent text is still on the event, so the delta is readable.
        first = session.conversation.trace.events_of_kind(EventKind.CALLER_UTTERANCE)[0]
        assert "Bergstrom" in str(first.get("text_sent"))

    def test_the_adviser_hears_the_mangled_customer(self, tmp_path: Path) -> None:
        """Recognition error propagates into the next turn's prompt, as in production."""
        session = SpokenSession(tmp_path, mangle=lambda t: t.replace("market", "market's"))
        adviser_history = [
            entry["content"]
            for entry in session.trainee.inner.history
            if entry["role"] == "user"
        ]
        assert any("market's" in entry for entry in adviser_history)

    def test_the_adviser_remembers_its_own_words_not_the_mishearing(
        self, tmp_path: Path
    ) -> None:
        """An adviser knows what they said. Only the *other* side hears the channel."""
        session = SpokenSession(tmp_path, mangle=lambda t: t.replace("Bergstrom", "Berkstrom"))
        spoken_by_adviser = [
            entry["content"]
            for entry in session.trainee.inner.history
            if entry["role"] == "assistant"
        ]
        assert any("Bergstrom" in entry for entry in spoken_by_adviser)


class TestRecognitionDeltas:
    def test_a_faithful_channel_has_none(self, session: SpokenSession) -> None:
        assert spoken.recognition_deltas(session.notes) == []

    def test_a_mishearing_is_reported_with_both_counts(self, tmp_path: Path) -> None:
        session = SpokenSession(tmp_path, mangle=_drop_the_risk_warning)
        deltas = spoken.recognition_deltas(session.notes)
        assert len(deltas) == 1
        delta = deltas[0]
        assert delta.speaker == "trainee"
        assert delta.turn == 3
        assert delta.normalised_errors > 0
        assert delta.normalised_reference_words > 0
        assert delta.raw_reference_words > 0
        assert delta.reference_source == "spoken-form"
        # Never a naked rate: the description carries numerator and denominator.
        assert f"{delta.normalised_errors}/{delta.normalised_reference_words}" in delta.describe()

    def test_punctuation_only_turn_is_skipped_not_crashed(self) -> None:
        """`wer` refuses a zero denominator; a wordless turn must not end the call."""
        note = spoken.AudioTurnNote(
            speaker="customer", order=0, text_sent="...", text_heard="...",
            clip_key="k", audio_sha256="d" * 64, duration_s=0.1, num_bytes=4,
            tts_engine="t", stt_engine="s", characters=3, characters_charged=3,
        )
        assert spoken.recognition_deltas([note]) == []


class TestChannelEffect:
    def test_a_faithful_channel_changed_nothing(self, session: SpokenSession) -> None:
        effect = session.effect()
        assert not effect.changed_outcome
        assert effect.heard_total == effect.sent_total
        assert effect.heard_disclosures == effect.sent_disclosures
        assert "no grading outcome changed" in effect.describe()

    def test_a_mis_heard_disclosure_loses_its_credit(self, tmp_path: Path) -> None:
        """The finding this whole module exists to be able to make.

        The adviser gives the risk warning; the channel turns "fall" into "call";
        the register never sees the required wording, so the disclosure is not
        credited — and `channel_effect` catches it by re-grading the same
        conversation as spoken and diffing, rather than by anyone noticing.
        """
        session = SpokenSession(tmp_path, mangle=_drop_the_risk_warning)
        effect = session.effect()
        assert effect.changed_outcome
        lost = [c for c in effect.sent_disclosures if c not in effect.heard_disclosures]
        assert lost, "the mangled turn should cost at least one disclosure code"
        described = effect.describe()
        assert "THE CHANNEL CHANGED A GRADING OUTCOME" in described
        assert "disclosures said but not credited as heard" in described


# --------------------------------------------------------------------------- #
# The budget, the assembly, and the gate
# --------------------------------------------------------------------------- #


class TestTheBudget:
    def test_the_cap_stops_the_call_before_a_model_turn_is_bought(
        self, tmp_path: Path
    ) -> None:
        session = SpokenSession(tmp_path, character_cap=700)
        assert session.trainee.stop_reason == "character_budget"
        assert session.ledger.characters_submitted <= 700
        # Stopped early, so fewer than the four scripted adviser turns were spoken.
        assert len([n for n in session.notes if n.speaker == "trainee"]) < 4

    def test_a_budget_stop_is_not_a_closed_session(self, tmp_path: Path) -> None:
        """Two endings that must never share a bucket: the harness capped the
        spend, and the adviser never closed."""
        session = SpokenSession(tmp_path, character_cap=700)
        assert session.trainee.stop_reason != "session_closed"
        assert session.trainee.stop_reason != "turn_budget"

    def test_the_ledger_accounts_for_every_character(self, session: SpokenSession) -> None:
        assert session.ledger.characters_submitted == sum(
            len(n.text_sent) for n in session.notes
        )
        assert session.ledger.characters_charged == session.tts.characters_spent

    def test_a_cached_line_is_submitted_but_not_charged(self) -> None:
        note = spoken.AudioTurnNote(
            speaker="trainee", order=0, text_sent="hello there", text_heard="hello there",
            clip_key="k", audio_sha256="d" * 64, duration_s=1.0, num_bytes=10,
            tts_engine="t", stt_engine="s", characters=11, characters_charged=0,
        )
        ledger = spoken.SpokenLedger()
        ledger.add(note, np.zeros(16))
        assert ledger.characters_submitted == 11
        assert ledger.characters_charged == 0


class TestAssembly:
    def test_turns_are_concatenated_in_order_with_gaps(self) -> None:
        clips = [np.full(8, 0.5), np.full(4, -0.5)]
        assembled = spoken.assemble_call(clips, gap_s=0.5, sample_rate=8)
        assert assembled.size == 8 + 4 + 4  # both clips plus one four-sample gap
        assert assembled[8:12].tolist() == [0.0, 0.0, 0.0, 0.0]

    def test_no_trailing_gap(self) -> None:
        assembled = spoken.assemble_call([np.full(4, 0.5)], gap_s=0.5, sample_rate=8)
        assert assembled.size == 4

    def test_an_empty_call_is_not_a_recording(self) -> None:
        with pytest.raises(ValueError, match="empty call"):
            spoken.assemble_call([])


class TestTheGate:
    def test_a_bare_environment_names_everything_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in (
            spoken.LIVE_SPOKEN_ENV_VAR,
            "ELEVENLABS_API_KEY",
            "DEEPGRAM_API_KEY",
            "LAB_TRAINEE_MODEL",
            "LAB_CUSTOMER_MODEL",
            "LAB_SCORER_MODEL",
            "AZURE_OPENAI_API_KEY",
            "AZURE_API_KEY",
            "OPENAI_API_KEY",
            "LAB_KEY",
        ):
            monkeypatch.delenv(name, raising=False)
        missing = spoken.missing_for_live()
        assert len(missing) == 7, missing
        with pytest.raises(NotLiveError) as caught:
            spoken.require_live()
        message = str(caught.value)
        # All of it at once. Being told one blocker per run turns setup into
        # seven failed runs.
        for fragment in ("LAB_LIVE_SPOKEN", "ELEVENLABS_API_KEY", "DEEPGRAM_API_KEY",
                         "LAB_TRAINEE_MODEL", "LAB_CUSTOMER_MODEL", "LAB_SCORER_MODEL"):
            assert fragment in message
        assert "replay_spoken_call" in message

    def test_the_opt_in_implies_the_whole_path(self) -> None:
        """One flag, because a call with a replaying half is not a spoken call."""
        assert set(spoken.IMPLIED_SWITCHES) == {
            "LAB_LIVE_TTS",
            "LAB_LIVE_STT",
            "LAB_LIVE_TRAINEE",
            "LAB_LIVE_CUSTOMER",
            "LAB_LIVE_SCORER",
        }

    def test_main_replays_by_default_and_spends_nothing(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
        assert spoken.main([]) == 0
        assert "SPOKEN CALL" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# The committed call
# --------------------------------------------------------------------------- #

#: What the one committed live call scored. Pinned here, as literals, because a
#: number that lives only in prose drifts: a README saying the spoken call
#: scored 12/20 and a fixture that now replays 11/20 will disagree silently for
#: as long as nobody re-reads both. These assertions are the thing that fails.
EXPECTED_TURNS: int = 16
EXPECTED_DETERMINISTIC_TOTAL: int = 12
EXPECTED_DETERMINISTIC_VERDICT: str = "fail"
EXPECTED_LIVE_TOTAL: int = 16
EXPECTED_LIVE_VERDICT: str = "fail"
EXPECTED_CHARACTERS: int = 3_014
EXPECTED_DETERMINISTIC_CRITERIA: dict[str, int] = {
    "discovery": 0,
    "objection_handling": 4,
    "mandatory_disclosure": 4,
    "no_unlicensed_advice": 4,
    "closing": 0,
}
EXPECTED_LIVE_CRITERIA: dict[str, int] = {
    "discovery": 4,
    "objection_handling": 4,
    "mandatory_disclosure": 0,
    "no_unlicensed_advice": 4,
    "closing": 4,
}


@pytest.fixture(scope="module")
def result() -> spoken.SpokenCallResult:
    """The committed call, replayed once and shared by the assertions below.

    Module-scoped because the replay re-runs the whole production loop twice more
    for the channel-effect counterfactual, and doing that per assertion would
    make this file the slowest in the suite for no extra coverage.
    """
    return spoken.replay_spoken_call()


class TestCommittedCall:
    """The one spoken call, replayed offline with zero keys.

    Every number here is *recomputed* from the committed notes by the production
    loop — the trace, the register, the persona ledgers and the deterministic
    card are all produced again, not read out of `scorecards.json`. Only the
    live scorer's answer is read back, and even that is held to its prompt
    digest, so editing the rubric raises instead of silently re-using a grade
    that belonged to a different question.
    """

    def test_the_fixtures_are_committed(self) -> None:
        for path in (
            spoken.FULL_CALL_WAV,
            spoken.MANIFEST_PATH,
            spoken.TRACE_PATH,
            spoken.SCORECARDS_PATH,
            spoken.SCORER_RECORDING_PATH,
        ):
            assert path.is_file(), f"{path} is not committed"

    def test_the_recording_is_the_call(self) -> None:
        """The WAV a reader can play is the audio the manifest describes."""
        manifest = json.loads(spoken.MANIFEST_PATH.read_text(encoding="utf-8"))
        spoken.verify_recording(spoken.SPOKEN_DIR, manifest)

    def test_it_replays_with_no_keys(
        self, result: spoken.SpokenCallResult, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("ELEVENLABS_API_KEY", "DEEPGRAM_API_KEY", "LAB_LIVE_SPOKEN"):
            monkeypatch.delenv(name, raising=False)
        assert len(result.notes) == EXPECTED_TURNS
        assert result.trace.adapter == spoken.SPOKEN_ADAPTER

    def test_the_deterministic_score_is_pinned(
        self, result: spoken.SpokenCallResult
    ) -> None:
        assert result.deterministic_card.total == EXPECTED_DETERMINISTIC_TOTAL
        assert result.deterministic_card.verdict == EXPECTED_DETERMINISTIC_VERDICT
        assert dict(result.deterministic_card.criteria) == EXPECTED_DETERMINISTIC_CRITERIA

    def test_the_live_score_is_pinned(self, result: spoken.SpokenCallResult) -> None:
        assert result.live_card is not None
        assert result.live_card.total == EXPECTED_LIVE_TOTAL
        assert result.live_card.verdict == EXPECTED_LIVE_VERDICT
        assert dict(result.live_card.criteria) == EXPECTED_LIVE_CRITERIA

    def test_agreeing_verdicts_hide_criterion_level_disagreement(
        self, result: spoken.SpokenCallResult
    ) -> None:
        """Both scorers said FAIL, and they disagree about almost every reason.

        Pinned because "the two scorers agreed" is the sentence a reader will
        take away, and on this call it is true only at the coarsest resolution:
        three of five criteria are maximally apart (0 vs 4), in both directions.
        A harness that reported agreement on the verdict and stopped would be
        reporting a coincidence as a corroboration.
        """
        assert result.scorers_agree is True
        assert result.deterministic_card.verdict == result.live_card.verdict
        disagreements = {
            name
            for name in EXPECTED_DETERMINISTIC_CRITERIA
            if EXPECTED_DETERMINISTIC_CRITERIA[name] != EXPECTED_LIVE_CRITERIA[name]
        }
        assert disagreements == {"discovery", "mandatory_disclosure", "closing"}
        for name in disagreements:
            assert abs(
                EXPECTED_DETERMINISTIC_CRITERIA[name] - EXPECTED_LIVE_CRITERIA[name]
            ) == 4

    def test_the_spend_is_pinned(self, result: spoken.SpokenCallResult) -> None:
        """The character count is evidence about a budget, so it is pinned too."""
        assert result.characters_submitted == EXPECTED_CHARACTERS
        assert result.characters_submitted <= spoken.DEFAULT_CHARACTER_CAP

    def test_the_scorecards_file_agrees_with_the_replay(
        self, result: spoken.SpokenCallResult
    ) -> None:
        """The committed summary must not drift from what the code recomputes."""
        document = json.loads(spoken.SCORECARDS_PATH.read_text(encoding="utf-8"))
        assert document["deterministic"]["total"] == result.deterministic_card.total
        assert document["deterministic"]["verdict"] == result.deterministic_card.verdict
        assert result.live_card is not None
        assert document["live"]["total"] == result.live_card.total
        assert document["verdicts_agree"] == result.scorers_agree

    def test_every_turn_carries_both_texts(self, result: spoken.SpokenCallResult) -> None:
        for note in result.notes:
            assert note.text_sent
            assert note.clip_key
            assert len(note.audio_sha256) == 64
            assert note.turn is not None

    def test_the_channel_effect_is_recorded_either_way(
        self, result: spoken.SpokenCallResult
    ) -> None:
        """Whether or not the channel changed a grade, the answer is measured."""
        effect = result.effect
        assert effect.heard_total >= 0 and effect.sent_total >= 0
        assert isinstance(effect.changed_outcome, bool)
        assert effect.describe()

    def test_the_channel_erased_the_discovery_criterion(
        self, result: spoken.SpokenCallResult
    ) -> None:
        """THE FINDING OF THE RUN, pinned so it cannot quietly stop being true.

        `roleplay.persona.classify_trainee_turn` decides that a turn is a
        question by `body.endswith("?")`. A scored transcript is `smart_format=
        False` — which `WER_NORMALISATION.md` requires, because the prettified
        string turns "seven thirty" into "07:30" and fabricates a word error rate
        — and the verbatim string carries **no punctuation at all**. So no spoken
        turn can ever end in a question mark, every adviser question classifies
        as `pitch`, and the `discovery` criterion is structurally unreachable on
        any spoken call.

        Two individually correct decisions, composing into a silent scoring
        failure that neither one is wrong about on its own. The adviser really
        did ask questions — five of the eight turns end in one as spoken — and
        the deterministic scorer gave discovery 0/4 anyway.

        This test asserts the mechanism, not just the number, so that fixing
        either half (a punctuation-independent classifier, or a scored transcript
        that carries sentence boundaries) makes it fail and demand a re-read.
        """
        from roleplay.persona import classify_trainee_turn

        trainee = [n for n in result.notes if n.speaker == "trainee"]
        assert len(trainee) == 8

        # Not one heard turn carries a question mark.
        assert not any("?" in n.text_heard for n in trainee)
        # The adviser did ask questions: five of the eight sent turns end in one,
        # and those are exactly the five whose classification the channel changed.
        asked = [n for n in trainee if n.text_sent.rstrip().endswith("?")]
        assert len(asked) == 5

        changed = [
            n
            for n in trainee
            if classify_trainee_turn(n.text_sent) != classify_trainee_turn(n.text_heard)
        ]
        assert len(changed) == 5
        # Every one of them collapsed into the same bucket.
        assert {classify_trainee_turn(n.text_heard) for n in changed} == {"pitch"}
        assert {classify_trainee_turn(n.text_sent) for n in changed} == {
            "closed_question",
            "open_probe",
        }
        # And that is what took discovery to zero.
        assert result.effect.sent_criteria["discovery"] == 2
        assert result.effect.heard_criteria["discovery"] == 0

    def test_identical_totals_are_not_evidence_of_no_effect(
        self, result: spoken.SpokenCallResult
    ) -> None:
        """The trap this call walked into, pinned so the lesson survives.

        Graded on heard text and on sent text, this call totals 12/20 both ways
        and its disclosure ledger is identical. A harness that compared totals —
        or compared verdicts, or compared the register — would have concluded the
        audio channel changed nothing. It changed two criteria, by two points
        each, in opposite directions. `ChannelEffect` compares the criteria
        *individually* for exactly this reason.
        """
        assert result.effect.heard_total == result.effect.sent_total
        assert result.effect.heard_verdict == result.effect.sent_verdict
        assert result.effect.heard_disclosures == result.effect.sent_disclosures
        assert result.effect.changed_outcome is True
        moved = {
            name
            for name, value in result.effect.heard_criteria.items()
            if result.effect.sent_criteria[name] != value
        }
        assert moved == {"discovery", "objection_handling"}

    def test_the_register_survived_the_channel_intact(
        self, result: spoken.SpokenCallResult
    ) -> None:
        """No mandatory disclosure was said and then lost to a mishearing.

        Worth pinning as the negative result it is: seven turns picked up
        recognition deltas, and none of them touched registered wording. The
        disclosures that were credited as heard are exactly the ones credited as
        spoken — so on this call the channel's damage was to *classification*,
        not to compliance content.
        """
        assert result.effect.heard_disclosures == result.effect.sent_disclosures
        assert result.effect.heard_disclosures == ["capital_at_risk", "fees_and_charges"]
        assert len(result.deltas) == 7

    def test_deltas_are_mostly_absorbed_by_normalisation(
        self, result: spoken.SpokenCallResult
    ) -> None:
        """Raw error counts far exceed normalised ones, and that is the point.

        Every delta here is scored against the synthesiser's spoken form, and
        the raw figure counts punctuation and casing as errors. Quoting the raw
        number as a recognition error rate is the trap `WER_NORMALISATION.md`
        exists to prevent; both travel on every delta so a reader can see the
        gap rather than be handed one number.
        """
        for delta in result.deltas:
            assert delta.raw_errors >= delta.normalised_errors
            assert delta.reference_source == "spoken-form"
        assert sum(d.raw_errors for d in result.deltas) > 4 * sum(
            d.normalised_errors for d in result.deltas
        )

    def test_the_report_quotes_no_naked_percentage(
        self, result: spoken.SpokenCallResult
    ) -> None:
        """Every rate in the report arrives with its denominator beside it."""
        report = result.report()
        assert "/20" in report
        assert "NOT an agent latency" in report
        for delta in result.deltas:
            assert f"/{delta.normalised_reference_words}" in report
