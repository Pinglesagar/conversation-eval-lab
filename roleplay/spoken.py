"""One full advisory call through real audio, graded end to end.

WHAT THIS MODULE IS FOR
-----------------------
Before this module, the repository's audio tier and its conversation tier never
met: the audio suite proves single utterances survive a real TTS -> STT round
trip, and the roleplay tier grades full multi-turn sessions — in text. This
module is the stitching. It drives the *existing* live conversation loop
(`roleplay.live` supplies the model trainee and the model customer,
`roleplay.runtime` owns the loop) and routes every turn through the *existing*
real engines (`lab.voice.engines.elevenlabs_tts`, `lab.voice.engines.
deepgram_stt`): the speaking side's text is synthesised, the audio is
transcribed, and the text the OTHER side receives — and the trace records, and
the scorer grades — is what the recogniser heard.

That last clause is the whole design, so it is stated as a rule:

    **The scorer grades what was heard, never what was sent.**

A disclosure the adviser said but the channel mangled is a disclosure the
register does not credit, exactly as in production. The trace carries both
strings, clearly named (`text_sent` beside the heard text on every spoken
event), so the gap between them is readable per turn — and `recognition_deltas`
plus `channel_effect` measure whether any gap changed a grading outcome.

WHY A WRAPPER AND NOT A FORK
----------------------------
`roleplay.scorer.session_view` is a pure function of trace events, and the loop
in `roleplay.runtime` emits those events. So the audio channel is two thin
wrappers — one around the live trainee, one around the live customer voice —
that intercept the text at the only two places it crosses between speakers.
Each wrapper leaves an `AudioTurnNote` for the loop to collect
(`runtime._take_audio_note`), and the note emits the turn's trace events itself:
the heard text with its real engine and confidence where the text path wrote
`confidence=1.0, engine="text:live"`. Nothing in the scorer, the persona
machine, the register or the contracts changes; they read a spoken session
exactly as they read a text one. Extending `roleplay.live` instead was
considered and rejected: that module's subject is record/replay of *model
turns*, and the audio channel is orthogonal to it — the cassette records what
the models said, this module records what the channel did to it.

THE CARDINAL RULE STILL HOLDS
-----------------------------
A fresh clone with every key unset replays the committed call:
`replay_spoken_call()` rebuilds the speakers from `fixtures/audio/spoken_call/
manifest.json` and re-runs the same loop, so the trace, the register, and the
deterministic score are *recomputed* by the same code paths, not read back from
a summary. The live scorer's answer replays from a committed recording whose
prompt digest must still match. Tests pin the scores so prose cannot drift.

The live path is opt-in behind `LAB_LIVE_SPOKEN=1`, which implies the four
switches it is made of (TTS, STT, trainee, customer) plus the live scorer.
A refusal names everything missing, not just the first thing noticed.

BUDGETS ARE MEASUREMENT SETTINGS
--------------------------------
ElevenLabs characters are the binding cost (a free allowance that does not
renew). Three guards, from soft to hard: this module stops asking for new turns
once the next turn could pass `character_cap`; the engine's own credit budget
refuses a line that would pass it; and every synthesis is digest-cached, so a
re-run of an unchanged call costs zero. The exact spend is read off the
engine's ledger and written into the manifest — a number, with its unit, never
a guess.

WHAT THE LATENCY FIGURES ARE, AND ARE NOT
-----------------------------------------
`synthesis_s`, `transcribe_s` and `model_turn_s` are direct wall-clock
measurements of harness-side vendor calls: ElevenLabs synthesis, the Deepgram
scored request (the optional display request is excluded), and the LLM turn
(retab backoff included). None of them is an agent voice-response latency —
this loop is half-duplex and file-based, so no such figure exists here, and
none is quoted. The trace's `ts` values come from the FakeClock latency model
and are labelled as modelled, not measured. The timing-gate calibration
(`fixtures/calibration_report.json`, verdict PASS) covers trace-recovered
latencies; the per-turn wall clocks here are reported beside it, not through it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from lab.trace.build import TraceBuilder
from lab.trace.io import write_jsonl
from lab.trace.schema import Trace
from lab.voice.engines.audiofile import read_audio, write_audio
from lab.voice.engines.base import (
    DEFAULT_SAMPLE_RATE,
    Audio,
    STTEngine,
    TTSEngine,
    audio_digest,
)
from lab.voice.engines.clipcache import clip_cache_key
from lab.voice.engines.deepgram_stt import DEEPGRAM_KEY_ENV_VAR, DeepgramSTT
from lab.voice.engines.elevenlabs_tts import (
    DEFAULT_AGENT_VOICE,
    DEFAULT_CALLER_VOICE,
    DEFAULT_ELEVENLABS_MODEL,
    ELEVENLABS_KEY_ENV_VAR,
    ElevenLabsTTS,
    credits_for,
)
from lab.voice.engines.stt import LIVE_STT_ENV_VAR
from lab.voice.engines.tts import LIVE_TTS_ENV_VAR
from lab.voice.wer import UtteranceWER, normalise, wer

from roleplay.live import (
    CASSETTE_ROOT,
    CUSTOMER_MODEL_ENV_VAR,
    KEY_ENV_VARS,
    LIVE_CUSTOMER_ENV_VAR,
    LIVE_TRAINEE_ENV_VAR,
    MODEL_LABEL_ENV_VAR,
    TRAINEE_FACTORY_ENV_VAR,
    TRAINEE_MODEL_ENV_VAR,
    CUSTOMER_MAX_TOKENS,
    LiveCustomerVoice,
    LiveRow,
    ModelSpeaker,
    NotLiveError,
    SessionCassette,
    SessionKey,
    TraineeContext,
    TraineeFactory,
    TraineeFactoryError,
    build_trainee,
    customer_prompt,
    load_customer_profiles,
    resolve_trainee_factory,
)
from roleplay.livescorer import (
    LIVE_ENV_VAR as LIVE_SCORER_ENV_VAR,
    MODEL_ENV_VAR as SCORER_MODEL_ENV_VAR,
    LiveRubricScorer,
    live_completion,
    recording_completion,
    replay_completion,
)
from roleplay.persona import CustomerProfile, CustomerPersona, PersonaTurn
from roleplay.runtime import RoleplayCoach, RoleplayConversation, Trainee
from roleplay.scorer import RubricScorer, ScoreCard, session_view

__all__ = [
    "LIVE_SPOKEN_ENV_VAR",
    "IMPLIED_SWITCHES",
    "SPOKEN_ADAPTER",
    "SPOKEN_DIR",
    "FULL_CALL_WAV",
    "MANIFEST_PATH",
    "TRACE_PATH",
    "SCORECARDS_PATH",
    "SCORER_RECORDING_PATH",
    "SPOKEN_ROW",
    "SPOKEN_SESSION_ID",
    "ADVISER_VOICE",
    "CUSTOMER_VOICE",
    "DEFAULT_CHARACTER_CAP",
    "DEFAULT_TURN_HEADROOM",
    "DEFAULT_MAX_TURNS",
    "TURN_GAP_S",
    "SCORER_RUBRIC_VERSION",
    "AudioTurnNote",
    "SpokenLedger",
    "AudioChannel",
    "SpokenTrainee",
    "SpokenCustomerVoice",
    "FixtureSpokenTrainee",
    "FixtureSpokenVoice",
    "RecognitionDelta",
    "ChannelEffect",
    "SpokenCallResult",
    "assemble_call",
    "verify_recording",
    "recognition_deltas",
    "channel_effect",
    "missing_for_live",
    "require_live",
    "run_spoken_call",
    "replay_spoken_call",
    "main",
]

# --------------------------------------------------------------------------- #
# Switches, paths, and the one scenario
# --------------------------------------------------------------------------- #

#: The single opt-in for the whole spoken path. Setting it implies the switches
#: below — one flag, because a spoken call that ran with the trainee live and
#: the TTS replaying would be a chimera nobody asked for.
LIVE_SPOKEN_ENV_VAR: str = "LAB_LIVE_SPOKEN"

#: What `LAB_LIVE_SPOKEN=1` turns on. The live scorer is included: the deliverable
#: is a call graded by *both* scorers, and recording the conversation without the
#: grade would strand half the fixture.
IMPLIED_SWITCHES: tuple[str, ...] = (
    LIVE_TTS_ENV_VAR,
    LIVE_STT_ENV_VAR,
    LIVE_TRAINEE_ENV_VAR,
    LIVE_CUSTOMER_ENV_VAR,
    LIVE_SCORER_ENV_VAR,
)

#: Trace adapter label. Not `roleplay:text`, because it is not.
SPOKEN_ADAPTER: str = "roleplay:spoken"

#: Where the committed spoken-call fixtures live.
SPOKEN_DIR: Path = Path(__file__).resolve().parent.parent / "fixtures" / "audio" / "spoken_call"
FULL_CALL_WAV: Path = SPOKEN_DIR / "full_call.wav"
MANIFEST_PATH: Path = SPOKEN_DIR / "manifest.json"
TRACE_PATH: Path = SPOKEN_DIR / "trace.jsonl"
SCORECARDS_PATH: Path = SPOKEN_DIR / "scorecards.json"
SCORER_RECORDING_PATH: Path = SPOKEN_DIR / "scorer_recording.jsonl"

#: The one scenario this pack runs spoken. The exemplary adviser against the
#: aggressive challenger: discovery, two objections that pull directly on the
#: eu-retail register — charges, and whose interest the adviser serves — and a
#: close. Chosen because it is the matchup with the most register activity per
#: character of synthesis, which is what a hard character budget wants: the
#: charges objection makes the `fees_and_charges` disclosure a natural thing for
#: an adviser to say, where a scenario that never mentions money in that way
#: would leave the requirement untested.
#:
#: **Why not the cautious saver**, which the first attempt used. That call was
#: cut short after two adviser turns: the customer model's paraphrase of the
#: persona's liquidity objection — "and how long would my money be tied up if I
#: went ahead" — was refused by the provider's content filter, which ends the
#: session with `stop_reason="content_filter"`. The refusal reproduces on its
#: own with a one-line system prompt, and the persona's *own* committed wording
#: of the same objection passes, so the trigger is the model's phrasing on the
#: day and not the corpus. It is recorded here because it is a real hazard for
#: anyone running conversational evals against a filtered provider — a benign
#: retail-finance sentence can end a session — and because a scenario swapped
#: without saying why is a result nobody can audit. Nothing about the grade was
#: involved in the choice: the discarded run never reached a gradeable call.
SPOKEN_ROW: LiveRow = LiveRow(
    scenario_id="spoken-eu-challenger-exemplary",
    customer="aggressive_challenger",
    competence="exemplary",
    jurisdiction="eu-retail",
    notes="The full advisory call through real TTS and real STT, turn by turn.",
)

#: Pinned so the live run and every replay render byte-identical scorer prompts
#: (the session id is a template field of the rubric prompt).
SPOKEN_SESSION_ID: str = "spoken-call-001"

#: Two premade voices, one per side, so the recording is listenable as a
#: dialogue rather than as one voice reading a script. George advises; Alice is
#: the customer (the committed sessions name this persona Mr Novak, so the voice
#: and the name disagree about gender — the voices are chosen for *contrast*,
#: which is what makes the turn boundaries audible). Both are on the measured
#: premade allowlist, the enforceable half of the cost guard.
ADVISER_VOICE: str = DEFAULT_CALLER_VOICE  # George — british male
CUSTOMER_VOICE: str = DEFAULT_AGENT_VOICE  # Alice — british female

#: ElevenLabs characters this call may submit for synthesis, both sides
#: combined, cached or not. The committed *text* run of this matchup spent 3,945
#: characters over 22 turns — about 180 per utterance — so 3,400 buys a full
#: call at the turn budget below with room for the channel to make it longer,
#: and it sits inside the engine's own credit budget at the 0.5x flash
#: multiplier, so the hard guard never has to fire for the soft one to work.
DEFAULT_CHARACTER_CAP: int = 3_400

#: The soft stop: no new turn is requested once fewer than this many characters
#: remain under the cap. One generous adviser turn, measured off the committed
#: sessions (the longest was 468 characters).
DEFAULT_TURN_HEADROOM: int = 480

#: Trainee-turn budget. The committed text run of this matchup took eleven
#: adviser turns to close, which at ~180 characters an utterance would not fit
#: the cap; nine is what the budget buys, and a call that runs out of turns
#: stops as `turn_budget` rather than being reported as an adviser who never
#: closed. The two endings must not share a bucket.
DEFAULT_MAX_TURNS: int = 9

#: Silence between turns in the assembled recording, seconds. Long enough that
#: the speaker change is audible, short enough not to pad the file.
TURN_GAP_S: float = 0.35

#: The rubric the live scorer is asked. v2 is the iterated version the scorer
#: study calibrated last; see `roleplay/scorer_study`.
SCORER_RUBRIC_VERSION: str = "v2"

_SR: int = DEFAULT_SAMPLE_RATE


# --------------------------------------------------------------------------- #
# The per-turn record: text_sent -> audio -> text_heard
# --------------------------------------------------------------------------- #


class AudioTurnNote(BaseModel):
    """The audio journey of one spoken turn, and the emitter of its trace events.

    One object per utterance that crossed the channel, carrying both texts with
    unambiguous names:

        text_sent    what the speaking model produced and the synthesiser was
                     handed;
        spoken_form  the words the synthesiser states it actually spoke
                     (`normalized_alignment`), when the model publishes a
                     trustworthy one — the only valid WER reference, per
                     `lab/voice/engines/WER_NORMALISATION.md`;
        text_heard   what the recogniser returned, verbatim
                     (`smart_format=false`) — the string the other side
                     received and the scorer grades;
        display_text the vendor-prettified transcript, display only, carried
                     under a key that names its own prohibition.

    The note emits the turn's trace events itself (`emit_caller` /
    `emit_agent`) so the honest payload lives in exactly one place, and the
    runtime loop stays ignorant of audio.
    """

    model_config = ConfigDict(extra="forbid")

    speaker: str = Field(pattern="^(trainee|customer)$")
    order: int = Field(ge=0, description="Position in speaking order, both sides.")
    turn: int | None = Field(default=None, description="The loop's turn index, set at emit time.")
    text_sent: str
    spoken_form: str | None = None
    text_heard: str
    display_text: str | None = None
    stt_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    clip_key: str
    audio_sha256: str
    duration_s: float = Field(ge=0.0)
    num_bytes: int = Field(ge=0)
    synthesis_s: float | None = Field(
        default=None, description="TTS wall clock, seconds. None when the clip replayed from cache."
    )
    transcribe_s: float | None = Field(
        default=None, description="Deepgram scored-request wall clock, seconds. Excludes the display request."
    )
    model_turn_s: float | None = Field(
        default=None, description="LLM wall clock for producing text_sent, retries included."
    )
    #: The synthesiser's own identity string, which embeds the model *and the
    #: voice* — `tts:elevenlabs/eleven_flash_v2_5/<voice id>`. It names the voice
    #: that actually spoke this turn, because the call builds one engine per
    #: voice; a single engine driven with a per-call voice argument would stamp
    #: every turn with its constructor's voice and silently mislabel one side.
    tts_engine: str
    stt_engine: str
    tts_replayed: bool = False
    characters: int = Field(ge=0, description="len(text_sent) — what a fresh synthesis would bill.")
    characters_charged: int = Field(ge=0, description="Characters actually billed this run (0 on a cache hit).")

    @property
    def wer_reference(self) -> str:
        """The string `text_heard` must be scored against. Spoken form when published."""
        return self.spoken_form or self.text_sent

    @property
    def reference_source(self) -> str:
        return "spoken-form" if self.spoken_form else "caller-input"

    # ---------------------------------------------------------------- emission

    def _require_heard(self, heard_text: str) -> None:
        if heard_text != self.text_heard:
            raise RuntimeError(
                f"instrument fault: the loop is recording {heard_text!r} for a turn "
                f"whose audio note heard {self.text_heard!r}. The note and the "
                "conversation have desynchronised; nothing about this session can "
                "be trusted."
            )

    def emit_caller(self, builder: TraceBuilder, *, turn: int, heard_text: str) -> None:
        """The trainee's spoken turn: audio evidence, honest transcript, utterance."""
        self._require_heard(heard_text)
        self.turn = turn
        builder.audio_emitted(
            actor="caller",
            engine=self.tts_engine,
            num_bytes=self.num_bytes,
            duration_s=round(self.duration_s, 6),
            turn=turn,
            clip_key=self.clip_key,
            audio_sha256=self.audio_sha256,
            text_sent=self.text_sent,
            spoken_form=self.spoken_form,
            reference_source=self.reference_source,
            synthesis_s=None if self.synthesis_s is None else round(self.synthesis_s, 6),
            replayed=self.tts_replayed,
        )
        builder.transcript_in(
            self.text_heard,
            confidence=self.stt_confidence,
            engine=self.stt_engine,
            formatting="raw",
            provenance="engine",
            text_sent=self.text_sent,
            transcribe_s=None if self.transcribe_s is None else round(self.transcribe_s, 6),
            display_text_unscored=self.display_text,
        )
        builder.caller_utterance(
            self.text_heard, turn=turn, engine=self.stt_engine, text_sent=self.text_sent
        )

    def emit_agent(
        self, builder: TraceBuilder, *, turn: int, agent: str, heard_text: str
    ) -> None:
        """The customer's spoken turn: same evidence, agent-side event kinds."""
        self._require_heard(heard_text)
        self.turn = turn
        builder.agent_audio_first_byte(turn=turn, engine=self.tts_engine)
        builder.agent_utterance(
            self.text_heard,
            agent=agent,
            turn=turn,
            engine=self.stt_engine,
            text_sent=self.text_sent,
            confidence=self.stt_confidence,
            display_text_unscored=self.display_text,
        )
        builder.transcript_out(
            self.text_sent,
            engine=self.tts_engine,
            spoken_form=self.spoken_form,
            reference_source=self.reference_source,
            synthesis_s=None if self.synthesis_s is None else round(self.synthesis_s, 6),
        )
        builder.audio_emitted(
            actor="agent",
            engine=self.tts_engine,
            num_bytes=self.num_bytes,
            duration_s=round(self.duration_s, 6),
            turn=turn,
            clip_key=self.clip_key,
            audio_sha256=self.audio_sha256,
            transcribe_s=None if self.transcribe_s is None else round(self.transcribe_s, 6),
            replayed=self.tts_replayed,
        )
        builder.agent_audio_complete(turn=turn, num_bytes=self.num_bytes)


# --------------------------------------------------------------------------- #
# The channel
# --------------------------------------------------------------------------- #


@dataclass
class SpokenLedger:
    """The call's running audio account: clips in speaking order, characters spent.

    One ledger shared by both channels, because the budget is per call, not per
    speaker, and the assembled recording needs the clips interleaved exactly as
    they were spoken.
    """

    character_cap: int = DEFAULT_CHARACTER_CAP
    turn_headroom: int = DEFAULT_TURN_HEADROOM
    characters_submitted: int = 0
    characters_charged: int = 0
    notes: list[AudioTurnNote] = field(default_factory=list)
    clips: list[Audio] = field(default_factory=list)

    @property
    def exhausted(self) -> bool:
        """True when the next turn could pass the cap. Checked *before* asking
        the model for a turn, because the point of the check is the money."""
        if self.character_cap <= 0:
            return False
        return self.characters_submitted + self.turn_headroom > self.character_cap

    def add(self, note: AudioTurnNote, audio: Audio) -> None:
        self.notes.append(note)
        self.clips.append(np.asarray(audio, dtype=np.float64))
        self.characters_submitted += note.characters
        self.characters_charged += note.characters_charged


@dataclass
class AudioChannel:
    """text -> TTS -> samples -> STT -> text, one direction, one voice."""

    tts: TTSEngine
    stt: STTEngine
    voice: str
    speaker: str
    ledger: SpokenLedger

    def transmit(self, text_sent: str, *, model_turn_s: float | None = None) -> AudioTurnNote:
        """Send one utterance through the channel and record the whole journey."""
        synth = self.tts.synthesise(text_sent, sample_rate=_SR, voice=self.voice)
        heard = self.stt.transcribe(synth.audio, sample_rate=_SR)
        note = AudioTurnNote(
            speaker=self.speaker,
            order=len(self.ledger.notes),
            text_sent=text_sent,
            spoken_form=synth.spoken_text,
            text_heard=heard.text,
            display_text=heard.display_text,
            stt_confidence=heard.confidence,
            clip_key=clip_cache_key(
                text=text_sent,
                voice=self.voice,
                model=str(getattr(self.tts, "model_id", self.tts.name)),
                output_format=f"pcm_{_SR}",
                normalisation=str(getattr(self.tts, "apply_text_normalization", "on")),
            ),
            audio_sha256=audio_digest(synth.audio, _SR),
            duration_s=synth.duration_s,
            num_bytes=synth.num_bytes,
            synthesis_s=synth.synthesis_s,
            transcribe_s=heard.transcribe_s,
            model_turn_s=model_turn_s,
            tts_engine=synth.engine,
            stt_engine=heard.engine,
            tts_replayed=synth.replayed,
            characters=len(text_sent),
            characters_charged=0 if synth.replayed else len(text_sent),
        )
        self.ledger.add(note, synth.audio)
        return note


def _speaker_repr(kind: str, *, tts: str, stt: str, voice: str) -> str:
    """One repr shared by the live wrapper and its fixture replayer, so the
    `session_start` provenance of a replayed trace is byte-identical to the
    recording's."""
    return f"{kind}(tts={tts!r}, stt={stt!r}, voice={voice!r})"


# --------------------------------------------------------------------------- #
# The two wrappers
# --------------------------------------------------------------------------- #


@dataclass
class SpokenTrainee:
    """The live trainee, heard through the channel.

    Satisfies `roleplay.runtime.Trainee`. The inner trainee keeps its own words
    in its own history — an adviser knows what they said — while the loop, the
    register, the persona and the scorer all receive what the recogniser heard.
    The character cap is enforced here, before a model turn is bought, because
    a stop decided after synthesis is a stop that already spent the characters.

    `inner` is any `Trainee`, not specifically the model-backed one: whatever
    `build_trainee` returned — the built-in adviser or an external agent behind
    `LAB_TRAINEE_FACTORY` — is heard through the same channel.
    """

    inner: Trainee
    channel: AudioChannel
    _stop: str | None = None
    _pending: AudioTurnNote | None = None

    @property
    def planned_turns(self) -> int:
        return int(getattr(self.inner, "planned_turns", 0))

    @property
    def stop_reason(self) -> str | None:
        return self._stop or getattr(self.inner, "stop_reason", None)

    def open(self) -> str | None:
        return self._through(self.inner.open)

    def reply(self, customer_turn: str) -> str | None:
        return self._through(lambda: self.inner.reply(customer_turn))

    def take_audio_note(self) -> AudioTurnNote | None:
        note, self._pending = self._pending, None
        return note

    def _through(self, produce: Any) -> str | None:
        if self._stop is not None:
            return None
        if self.channel.ledger.exhausted:
            # The soft budget stop. Named its own reason: "the harness capped
            # the spend" and "the adviser never closed" must not share a bucket.
            self._stop = "character_budget"
            return None
        started = time.perf_counter()
        text = produce()
        elapsed = time.perf_counter() - started
        if text is None:
            return None
        note = self.channel.transmit(text, model_turn_s=elapsed)
        self._pending = note
        return note.text_heard

    def __repr__(self) -> str:
        return _speaker_repr(
            "SpokenTrainee",
            tts=self.channel.tts.name,
            stt=self.channel.stt.name,
            voice=self.channel.voice,
        )


@dataclass
class SpokenCustomerVoice:
    """The live customer voice, heard through the channel.

    Satisfies `roleplay.runtime.CustomerVoice`. The state machine still decides
    every move and the inner voice still words it; this wrapper only owns the
    journey from those words to what the adviser hears. The leak audit stays on
    the inner voice's own words — a leak is about what the customer *said*, and
    holding the channel's mishearings against the persona would corrupt the
    instrument-health number.
    """

    inner: LiveCustomerVoice
    channel: AudioChannel
    _pending: AudioTurnNote | None = None

    def speak(
        self,
        *,
        move: PersonaTurn,
        persona: CustomerPersona,
        trainee_turn: str,
        turn: int,
    ) -> str:
        started = time.perf_counter()
        text = self.inner.speak(
            move=move, persona=persona, trainee_turn=trainee_turn, turn=turn
        )
        elapsed = time.perf_counter() - started
        note = self.channel.transmit(text, model_turn_s=elapsed)
        self._pending = note
        return note.text_heard

    def take_audio_note(self) -> AudioTurnNote | None:
        note, self._pending = self._pending, None
        return note

    # Instrument-health passthroughs, read by the loop and the report.
    @property
    def leaks(self) -> int:
        return int(getattr(self.inner, "leaks", 0) or 0)

    @property
    def leaked_topics(self) -> list[str]:
        return list(getattr(self.inner, "leaked_topics", ()) or ())

    @property
    def fallbacks(self) -> int:
        return int(getattr(self.inner, "fallbacks", 0) or 0)

    @property
    def filtered_turns(self) -> int:
        return int(getattr(self.inner, "filtered_turns", 0) or 0)

    def __repr__(self) -> str:
        return _speaker_repr(
            "SpokenCustomerVoice",
            tts=self.channel.tts.name,
            stt=self.channel.stt.name,
            voice=self.channel.voice,
        )


# --------------------------------------------------------------------------- #
# Fixture speakers: the committed call, re-driven offline
# --------------------------------------------------------------------------- #


@dataclass
class FixtureSpokenTrainee:
    """Replays the trainee side of a recorded spoken call from its notes.

    Feeds the loop the same heard texts, exposes the same audio notes, reports
    the same stop reason — so the trace, the register and the persona ledgers
    are *recomputed* by the production loop rather than read from a summary.
    """

    notes: list[AudioTurnNote]
    final_stop: str
    planned_turns: int
    repr_of: str
    _index: int = 0
    _pending: AudioTurnNote | None = None

    @property
    def stop_reason(self) -> str | None:
        return self.final_stop if self._index >= len(self.notes) else "speaking"

    def open(self) -> str | None:
        return self._next()

    def reply(self, customer_turn: str) -> str | None:
        return self._next()

    def _next(self) -> str | None:
        if self._index >= len(self.notes):
            return None
        note = self.notes[self._index]
        self._index += 1
        self._pending = note
        return note.text_heard

    def take_audio_note(self) -> AudioTurnNote | None:
        note, self._pending = self._pending, None
        return note

    def __repr__(self) -> str:
        return self.repr_of


@dataclass
class FixtureSpokenVoice:
    """Replays the customer side. Same contract as the live wrapper, including
    the instrument-health counters the trace's `session_end` reads."""

    notes: list[AudioTurnNote]
    leaks: int
    repr_of: str
    _index: int = 0
    _pending: AudioTurnNote | None = None

    def speak(
        self,
        *,
        move: PersonaTurn,
        persona: CustomerPersona,
        trainee_turn: str,
        turn: int,
    ) -> str:
        if self._index >= len(self.notes):
            raise RuntimeError(
                "the loop asked for a customer turn the recording does not hold: "
                f"turn {self._index + 1} of {len(self.notes)} recorded. The replay "
                "has diverged from the recorded conversation."
            )
        note = self.notes[self._index]
        self._index += 1
        self._pending = note
        return note.text_heard

    def take_audio_note(self) -> AudioTurnNote | None:
        note, self._pending = self._pending, None
        return note

    def __repr__(self) -> str:
        return self.repr_of


# --------------------------------------------------------------------------- #
# Assembly, deltas, and the channel-effect check
# --------------------------------------------------------------------------- #


def assemble_call(
    clips: Sequence[Audio], *, gap_s: float = TURN_GAP_S, sample_rate: int = _SR
) -> Audio:
    """Concatenate the turns, in speaking order, with a short silence between.

    One mono buffer at the pipeline rate. The gap is silence rather than
    cross-fade because the recording is evidence, not production audio: every
    sample in it is either a synthesised turn or a declared gap.
    """
    if not clips:
        raise ValueError("no clips to assemble — an empty call is not a recording")
    gap = np.zeros(int(round(gap_s * sample_rate)), dtype=np.float64)
    parts: list[Audio] = []
    for position, clip in enumerate(clips):
        if position:
            parts.append(gap)
        parts.append(np.asarray(clip, dtype=np.float64))
    return np.concatenate(parts)


class RecognitionDelta(BaseModel):
    """One turn where what was heard differs from what was spoken.

    Scored against the spoken form when the synthesiser published one, per
    `WER_NORMALISATION.md`; `reference_source` says which reference this row
    used. Raw and normalised are both carried, with their edit counts — never
    a naked rate.
    """

    model_config = ConfigDict(extra="forbid")

    order: int
    speaker: str
    turn: int | None
    reference: str
    reference_source: str
    text_sent: str
    text_heard: str
    raw_errors: int
    raw_reference_words: int
    normalised_errors: int
    normalised_reference_words: int
    stt_confidence: float | None

    def describe(self) -> str:
        return (
            f"turn {self.turn} ({self.speaker}): {self.normalised_errors}/"
            f"{self.normalised_reference_words} normalised word errors "
            f"({self.raw_errors}/{self.raw_reference_words} raw, vs the "
            f"{self.reference_source} reference)\n"
            f"    spoken : {self.reference}\n"
            f"    heard  : {self.text_heard}"
        )


def _delta_of(note: AudioTurnNote) -> tuple[RecognitionDelta | None, UtteranceWER | None]:
    """This turn's recognition delta, or None when the channel was faithful.

    Faithful means zero *normalised* errors: raw disagreements between a
    written reference and a verbatim transcript are formatting, and reporting
    them as recognition would be the exact trap `WER_NORMALISATION.md` exists
    to prevent. The raw counts still travel on every delta that does surface.
    """
    reference = note.wer_reference
    # `wer` refuses a reference with no words, correctly: a zero denominator has
    # no honest value. A turn that is punctuation or whitespace only is not a
    # recognition failure to report, it is a turn with nothing to score, so it
    # is skipped here rather than allowed to raise mid-call.
    if not normalise(reference).split():
        return None, None
    scored = wer(reference, note.text_heard)
    if scored.normalised.errors == 0:
        return None, scored
    return (
        RecognitionDelta(
            order=note.order,
            speaker=note.speaker,
            turn=note.turn,
            reference=reference,
            reference_source=note.reference_source,
            text_sent=note.text_sent,
            text_heard=note.text_heard,
            raw_errors=scored.raw.errors,
            raw_reference_words=scored.raw.reference_words,
            normalised_errors=scored.normalised.errors,
            normalised_reference_words=scored.normalised.reference_words,
            stt_confidence=note.stt_confidence,
        ),
        scored,
    )


def recognition_deltas(notes: Sequence[AudioTurnNote]) -> list[RecognitionDelta]:
    """Every turn whose heard text differs from its reference, in order."""
    out: list[RecognitionDelta] = []
    for note in notes:
        delta, _ = _delta_of(note)
        if delta is not None:
            out.append(delta)
    return out


class ChannelEffect(BaseModel):
    """Did any recognition delta change a grading outcome?

    Answered by measurement, not inspection: the same conversation is re-graded
    with every `text_heard` swapped for its `text_sent` — the call the models
    *meant* to have — and the two deterministic score cards and disclosure
    ledgers are diffed. Any difference here is a finding about the channel.
    """

    model_config = ConfigDict(extra="forbid")

    heard_total: int
    sent_total: int
    heard_verdict: str
    sent_verdict: str
    heard_criteria: dict[str, int]
    sent_criteria: dict[str, int]
    heard_disclosures: list[str]
    sent_disclosures: list[str]

    @property
    def changed_outcome(self) -> bool:
        return (
            self.heard_verdict != self.sent_verdict
            or self.heard_criteria != self.sent_criteria
            or self.heard_disclosures != self.sent_disclosures
        )

    def describe(self) -> str:
        if not self.changed_outcome:
            return (
                "no grading outcome changed: verdict, all five criteria and the "
                "disclosure ledger are identical graded on heard text and on sent text "
                f"({self.heard_total}/20 both ways)"
            )
        lines = ["THE CHANNEL CHANGED A GRADING OUTCOME:"]
        if self.heard_total == self.sent_total:
            # The trap this measurement exists to avoid. Criteria that move by
            # equal amounts in opposite directions leave the total untouched, so
            # a comparison of totals — or of verdicts — reports "no effect" on a
            # call the channel demonstrably changed. Said out loud, because a
            # reader who sees the totals agree will otherwise stop reading.
            lines.append(
                f"  note: both gradings total {self.heard_total}/20, so a check on "
                "the total alone would have found nothing. The criteria below moved "
                "in opposite directions and cancelled out."
            )
        if self.heard_verdict != self.sent_verdict:
            lines.append(
                f"  verdict: {self.sent_verdict} as spoken -> {self.heard_verdict} as heard"
            )
        for name in sorted(set(self.heard_criteria) | set(self.sent_criteria)):
            heard, sent = self.heard_criteria.get(name), self.sent_criteria.get(name)
            if heard != sent:
                lines.append(f"  {name}: {sent} as spoken -> {heard} as heard")
        missing = [c for c in self.sent_disclosures if c not in self.heard_disclosures]
        gained = [c for c in self.heard_disclosures if c not in self.sent_disclosures]
        if missing:
            lines.append(
                f"  disclosures said but not credited as heard: {', '.join(missing)}"
            )
        if gained:
            lines.append(
                f"  disclosures credited as heard but not said: {', '.join(gained)}"
            )
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Driving the loop from notes
# --------------------------------------------------------------------------- #


def _converse_from_notes(
    notes: Sequence[AudioTurnNote],
    *,
    profile: CustomerProfile,
    scenario_id: str,
    jurisdiction: str,
    language: str,
    max_turns: int,
    trainee_stop: str,
    customer_leaks: int,
    trainee_repr: str,
    voice_repr: str,
    session_id: str = SPOKEN_SESSION_ID,
) -> RoleplayConversation:
    """Re-run the production loop with the recorded notes as both speakers."""
    trainee = FixtureSpokenTrainee(
        notes=[n for n in notes if n.speaker == "trainee"],
        final_stop=trainee_stop,
        planned_turns=max_turns,
        repr_of=trainee_repr,
    )
    voice = FixtureSpokenVoice(
        notes=[n for n in notes if n.speaker == "customer"],
        leaks=customer_leaks,
        repr_of=voice_repr,
    )
    coach = RoleplayCoach(scorer=RubricScorer())
    return coach.converse(
        scenario_id=scenario_id,
        profile=profile,
        trainee=trainee,
        customer_voice=voice,
        jurisdiction=jurisdiction,
        language=language,
        max_turns=max_turns,
        session_id=session_id,
        adapter=SPOKEN_ADAPTER,
    )


def channel_effect(
    notes: Sequence[AudioTurnNote],
    *,
    profile: CustomerProfile,
    scenario_id: str,
    jurisdiction: str,
    language: str,
    max_turns: int,
    trainee_stop: str,
    customer_leaks: int,
    trainee_repr: str,
    voice_repr: str,
) -> ChannelEffect:
    """Grade the call as heard and as sent, and diff the outcomes."""

    def graded(turn_notes: Sequence[AudioTurnNote]) -> tuple[ScoreCard, list[str]]:
        conversation = _converse_from_notes(
            turn_notes,
            profile=profile,
            scenario_id=scenario_id,
            jurisdiction=jurisdiction,
            language=language,
            max_turns=max_turns,
            trainee_stop=trainee_stop,
            customer_leaks=customer_leaks,
            trainee_repr=trainee_repr,
            voice_repr=voice_repr,
        )
        card = RubricScorer().score_trace(conversation.trace)
        return card, sorted(conversation.register.satisfied_codes())

    as_heard = list(notes)
    # The counterfactual: the channel was perfect, so every side heard exactly
    # what the other said. `text_sent` stands in for `text_heard`; the persona
    # machine and the register then re-decide everything from those words.
    as_sent = [
        note.model_copy(update={"text_heard": note.text_sent}) for note in notes
    ]
    heard_card, heard_codes = graded(as_heard)
    sent_card, sent_codes = graded(as_sent)
    return ChannelEffect(
        heard_total=heard_card.total,
        sent_total=sent_card.total,
        heard_verdict=heard_card.verdict,
        sent_verdict=sent_card.verdict,
        heard_criteria=dict(heard_card.criteria),
        sent_criteria=dict(sent_card.criteria),
        heard_disclosures=heard_codes,
        sent_disclosures=sent_codes,
    )


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def missing_for_live(*, external_trainee: bool | None = None) -> list[str]:
    """Everything standing between this process and a live spoken call, by name.

    `external_trainee` says whether the adviser comes from a trainee factory
    rather than the built-in model trainee; None reads `LAB_TRAINEE_FACTORY`. An
    external adviser needs no litellm route of its own, so that line is dropped
    from the list — the customer's route and the scorer's are still required.
    """
    if external_trainee is None:
        external_trainee = bool(os.environ.get(TRAINEE_FACTORY_ENV_VAR))
    missing: list[str] = []
    if not os.environ.get(LIVE_SPOKEN_ENV_VAR):
        missing.append(f"{LIVE_SPOKEN_ENV_VAR}=1 (the spoken-call opt-in)")
    if not os.environ.get(ELEVENLABS_KEY_ENV_VAR):
        missing.append(f"{ELEVENLABS_KEY_ENV_VAR} (synthesis)")
    if not os.environ.get(DEEPGRAM_KEY_ENV_VAR):
        missing.append(f"{DEEPGRAM_KEY_ENV_VAR} (recognition)")
    if not any(os.environ.get(name) for name in KEY_ENV_VARS):
        missing.append(
            f"a model-provider key (one of {', '.join(KEY_ENV_VARS)}) for the two "
            "speakers and the scorer"
        )
    if not external_trainee and not os.environ.get(TRAINEE_MODEL_ENV_VAR):
        missing.append(f"{TRAINEE_MODEL_ENV_VAR} (the adviser's litellm route)")
    if not os.environ.get(CUSTOMER_MODEL_ENV_VAR):
        missing.append(f"{CUSTOMER_MODEL_ENV_VAR} (the customer's litellm route)")
    if not os.environ.get(SCORER_MODEL_ENV_VAR):
        missing.append(f"{SCORER_MODEL_ENV_VAR} (the live scorer's litellm route)")
    return missing


def require_live(*, external_trainee: bool | None = None) -> None:
    """Refuse to spend unless everything is in place, naming all of it at once.

    On success, sets the five implied switches, so a caller who set
    `LAB_LIVE_SPOKEN=1` has genuinely turned on the whole path — a spoken call
    with a replaying half is not a spoken call.
    """
    missing = missing_for_live(external_trainee=external_trainee)
    if missing:
        raise NotLiveError(
            "a live spoken call needs everything below, and this environment is "
            "missing:\n  - " + "\n  - ".join(missing) + "\n"
            "Set what is missing, or stay offline: replay_spoken_call() replays "
            "the committed call with zero keys."
        )
    for switch in IMPLIED_SWITCHES:
        os.environ.setdefault(switch, "1")


# --------------------------------------------------------------------------- #
# The result
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SpokenCallResult:
    """One spoken call, graded twice, with its channel accounting."""

    trace: Trace
    notes: tuple[AudioTurnNote, ...]
    deterministic_card: ScoreCard
    live_card: ScoreCard | None
    live_raw: str | None
    deltas: tuple[RecognitionDelta, ...]
    effect: ChannelEffect
    call_duration_s: float
    characters_submitted: int
    characters_charged: int
    tts_credits_spent: int
    #: Lines served from the digest cache on this run. With every line cached the
    #: run bills nothing, which says the cache worked and says nothing about what
    #: the call cost to make: `characters_submitted` is that number.
    cached_lines: int
    #: Distinct audio in the call, seconds — each clip counted once. This is the
    #: length of the conversation, not the bill.
    deepgram_seconds: float
    #: Seconds actually submitted to the vendor. Larger than `deepgram_seconds`
    #: whenever the display transcript was fetched, because that is a *second*
    #: request over the *same* clip: `want_display=True` doubles the metered
    #: seconds and leaves the audio length unchanged. Reporting only the first
    #: number would understate the spend by exactly a factor of two, so both are
    #: carried and the report prints both with their difference explained.
    deepgram_submitted_seconds: float
    deepgram_requests: int
    trainee_stop: str
    customer_leaks: int
    scorer_rubric: str = SCORER_RUBRIC_VERSION
    #: `"recorded"` when this result came from a live run, `"replayed"` when it
    #: was rebuilt from the committed manifest. It exists for one reason: the
    #: three wall clocks on a note are *not* equally meaningful in both modes,
    #: and the report must not print a number under a label the number does not
    #: deserve. On replay the utterances are read from the recorded transcript,
    #: so `model_turn_s` measures a dictionary lookup — on the committed
    #: artifact, a third of a millisecond. Printed as `min/mean/max` it rounds
    #: to `0.00s` under a heading that says LLM, which reads as a model that
    #: answered instantly. `synthesis_s` already avoids this by being `None` on
    #: a cache hit; `model_turn_s` has no such natural signal, so the mode is
    #: carried here instead of being inferred from a suspiciously small float.
    source: str = "replayed"

    @property
    def scorers_agree(self) -> bool | None:
        if self.live_card is None:
            return None
        return self.live_card.verdict == self.deterministic_card.verdict

    def report(self) -> str:
        """The run, in the order a reader needs it: what, spend, grades, deltas, timing."""
        trainee_notes = [n for n in self.notes if n.speaker == "trainee"]
        customer_notes = [n for n in self.notes if n.speaker == "customer"]
        lines = [
            "SPOKEN CALL",
            "-" * 78,
            f"  scenario   {self.trace.scenario_id} ({SPOKEN_ROW.customer} vs a "
            f"{SPOKEN_ROW.competence} adviser, {SPOKEN_ROW.jurisdiction})",
            f"  turns      {len(trainee_notes)} adviser + {len(customer_notes)} customer "
            f"= {len(self.notes)} spoken turns; adviser stop: {self.trainee_stop}",
            f"  recording  {self.call_duration_s:.1f}s assembled "
            f"({TURN_GAP_S}s gaps between turns) at {FULL_CALL_WAV}",
            "",
            "SPEND",
            "-" * 78,
            f"  ElevenLabs characters submitted: {self.characters_submitted} "
            f"(cap {DEFAULT_CHARACTER_CAP}) — what this call costs to synthesise "
            "from cold, and the figure to read as its price",
            f"  ElevenLabs billed on THIS run: {self.characters_charged} characters "
            f"({self.tts_credits_spent} credits at the model multiplier), "
            f"{self.cached_lines} of {len(self.notes)} lines served from the digest "
            "cache — a re-run of an unchanged call bills 0, which is a property of "
            "the cache and not a discount on the call",
            f"  Deepgram: {self.deepgram_seconds:.1f}s of distinct call audio, "
            f"{self.deepgram_submitted_seconds:.1f}s submitted over "
            f"{self.deepgram_requests} request(s) — the display transcript is a "
            "second request over the same clip, so metered seconds are double the "
            "audio length, not double the audio",
            "",
            "GRADES (both scorers, same trace, grading what was HEARD)",
            "-" * 78,
            f"  deterministic  {self.deterministic_card.summary_line()}",
        ]
        if self.live_card is not None:
            lines.append(
                f"  live LLM ({self.scorer_rubric})  {self.live_card.summary_line()}"
            )
            lines.append(
                f"  agreement: verdicts {'AGREE' if self.scorers_agree else 'DISAGREE'} "
                f"({self.deterministic_card.verdict} vs {self.live_card.verdict}); "
                f"totals {self.deterministic_card.total}/20 vs {self.live_card.total}/20"
            )
        else:
            lines.append("  live LLM scorer: not run (no recording, no live switch)")
        lines += ["", "RECOGNITION DELTAS (text_heard vs the spoken-form reference)", "-" * 78]
        if not self.deltas:
            lines.append(
                f"  none: all {len(self.notes)} turns transcribed with zero normalised "
                "word errors"
            )
        else:
            for delta in self.deltas:
                lines.append("  " + delta.describe().replace("\n", "\n  "))
        lines += ["", "CHANNEL EFFECT ON GRADING", "-" * 78, "  " + self.effect.describe().replace("\n", "\n  ")]
        lines += ["", "PER-TURN WALL CLOCK (harness-side vendor calls; NOT an agent latency)", "-" * 78]
        model_turns = [n.model_turn_s for n in self.notes if n.model_turn_s is not None]
        for label, values, empty in (
            (
                "TTS synthesis_s   ",
                [n.synthesis_s for n in self.notes if n.synthesis_s is not None],
                "n=0 (every clip served from the digest cache, so nothing was synthesised to time)",
            ),
            (
                "STT transcribe_s  ",
                [n.transcribe_s for n in self.notes if n.transcribe_s is not None],
                "n=0 (no recognition request was made)",
            ),
        ):
            if values:
                lines.append(
                    f"  {label} n={len(values)}  min {min(values):.2f}s  "
                    f"mean {sum(values) / len(values):.2f}s  max {max(values):.2f}s"
                )
            else:
                lines.append(f"  {label} {empty}")
        # `model_turn_s` is the one clock whose meaning depends on the mode, and
        # printing it as a latency on replay would be the report's only dishonest
        # line: it measures a read from the recorded transcript, which rounds to
        # 0.00s under a heading that says LLM.
        if self.source == "replayed":
            largest = f"{max(model_turns) * 1000:.2f}ms" if model_turns else "n/a"
            lines.append(
                "  LLM model_turn_s   NOT QUOTED on replay: the utterances are read "
                f"from the recorded transcript, so this clock times a dictionary "
                f"lookup (largest {largest}), not a model call. Re-record to measure it."
            )
        elif model_turns:
            lines.append(
                f"  LLM model_turn_s   n={len(model_turns)}  min {min(model_turns):.2f}s  "
                f"mean {sum(model_turns) / len(model_turns):.2f}s  max {max(model_turns):.2f}s"
            )
        else:
            lines.append("  LLM model_turn_s   n=0 (no model was called)")
        lines.append(
            "  synthesis_s excludes cache hits; transcribe_s excludes the display "
            "request; model_turn_s includes retry backoff. Trace ts values are the "
            "FakeClock latency model, labelled modelled, not measured. No voice-"
            "response latency is quoted: this loop is half-duplex by design "
            "(timing gate for trace-recovered latencies: "
            "fixtures/calibration_report.json, verdict PASS)."
        )
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Live run
# --------------------------------------------------------------------------- #


def _sum_of(engines: Sequence[TTSEngine], counter: str) -> int:
    """One call's total for a per-engine counter, across both voices.

    The call has one budget; the synthesis has two engines, because the engine
    identity has to name the voice that actually spoke. Any spend figure must
    therefore be summed rather than read off whichever instance is in scope — a
    ledger read from one of two engines reports half the bill and looks fine.
    """
    return sum(int(getattr(engine, counter, 0) or 0) for engine in engines)


def _submitted_seconds(stt: STTEngine) -> float:
    """Seconds the recogniser was actually billed for, not the call's length.

    `DeepgramSTT.audio_seconds` counts each clip once — it measures the
    conversation. When a display transcript is wanted the same clip is posted a
    second time, so the vendor meters it twice. The multiplier is read off the
    engine rather than assumed, so switching `want_display` off makes this
    number fall to the audio length by itself.
    """
    seconds = float(getattr(stt, "audio_seconds", 0.0) or 0.0)
    return seconds * (2.0 if getattr(stt, "want_display", False) else 1.0)


def _score_both(
    trace: Trace,
    *,
    live_scorer: LiveRubricScorer | None,
) -> tuple[ScoreCard, ScoreCard | None, str | None]:
    """Both grades over one trace. The deterministic scorer is always run cold —
    one session, no cohort curve — so the number is a function of the trace."""
    deterministic = RubricScorer().score_trace(trace)
    if live_scorer is None:
        return deterministic, None, None
    live = live_scorer.score_live(trace)
    return deterministic, live.card, live.raw


def run_spoken_call(
    *,
    root: str | Path = CASSETTE_ROOT,
    out_dir: str | Path = SPOKEN_DIR,
    max_turns: int = DEFAULT_MAX_TURNS,
    character_cap: int = DEFAULT_CHARACTER_CAP,
    model_label: str | None = None,
    trainee_factory: str | TraineeFactory | None = None,
) -> SpokenCallResult:
    """Run THE spoken call live, record everything, and write the fixtures.

    `trainee_factory` is the same seam `roleplay.live.run_live_session` has: a
    callable or `module:callable` path building the adviser under test, falling
    back to `LAB_TRAINEE_FACTORY`, then to the built-in model trainee. An external
    adviser still speaks through the real TTS -> STT channel and is graded on
    what the recogniser heard.

    One call, by design: the scenario, the session id and the fixture paths are
    module constants because the deliverable is a single auditable artifact,
    not a matrix. Everything the run produces is written under `out_dir`:

        full_call.wav           the whole call, one playable 16 kHz mono WAV
        manifest.json           per-turn: who spoke, text_sent, spoken form,
                                text_heard, confidence, digests, wall clocks
        trace.jsonl             the conversation trace both scorers graded
        scorecards.json         both grades, and whether they agree
        scorer_recording.jsonl  the live scorer's raw answer, for offline replay

    plus the model-turn cassette under `fixtures/roleplay_live/<scenario>/`.
    """
    external = trainee_factory is not None or bool(os.environ.get(TRAINEE_FACTORY_ENV_VAR))
    require_live(external_trainee=external)
    profiles = load_customer_profiles()
    profile = profiles[SPOKEN_ROW.customer]
    label = model_label or os.environ.get(MODEL_LABEL_ENV_VAR) or "unspecified-model"

    key = SessionKey.build(
        scenario_id=SPOKEN_ROW.scenario_id,
        profile=profile,
        competence=SPOKEN_ROW.competence,
        jurisdiction=SPOKEN_ROW.jurisdiction,
        language=SPOKEN_ROW.language,
        trainee_model=label,
        customer_model=label,
        temperature=0.0,
        turn_budget=max_turns,
    )
    cassette = SessionCassette.load(key.path_in(root), identity=key)
    cassette.identity = key
    cassette.provenance = {
        "temperature": 0.0,
        "max_turns": max_turns,
        "live_customer": True,
        "note": (
            "Spoken-call turns. Each side's words crossed a real TTS -> STT round "
            "trip before the other side received them; the audio journey is in "
            "fixtures/audio/spoken_call/manifest.json."
        ),
    }
    inner_trainee = build_trainee(
        TraineeContext(
            scenario_id=SPOKEN_ROW.scenario_id,
            profile=profile,
            competence=SPOKEN_ROW.competence,
            jurisdiction=SPOKEN_ROW.jurisdiction,
            language=SPOKEN_ROW.language,
            max_turns=max_turns,
            model_label=label,
            temperature=0.0,
            cassette=cassette,
        ),
        factory=trainee_factory,
    )
    customer_speaker = ModelSpeaker(
        role="customer",
        cassette=cassette,
        live_env_var=LIVE_CUSTOMER_ENV_VAR,
        model_env_var=CUSTOMER_MODEL_ENV_VAR,
        model_label=label,
        temperature=0.0,
        max_tokens=CUSTOMER_MAX_TOKENS,
    )
    inner_voice = LiveCustomerVoice(
        speaker=customer_speaker,
        system_prompt=customer_prompt(profile),
        profile=profile,
    )

    # flash v2.5 with normalisation "on": the two conditions under which the
    # spoken form is published, which is the only valid WER reference here.
    #
    # **One engine instance per voice, not one engine used with two voices.** The
    # engine's identity string embeds the voice it was built with — that is
    # deliberate, because "ElevenLabs got worse" is not a finding and "this voice
    # on this model got worse" is — and that identity is what lands in the
    # `engine` field of every trace event it produces. A single instance driven
    # with a per-call `voice=` argument would label all sixteen turns with
    # whichever voice it was constructed with, so half the trace would name a
    # voice that never spoke. Two instances cost nothing (they share the clip
    # cache) and keep the label true.
    #
    # The credit budget is derived from this call's character cap rather than
    # left at its default, so the two guards are one decision instead of two
    # numbers that can drift apart. Each instance carries the whole call's
    # budget because the *shared* ledger cap below is the real control; these are
    # backstops, and a backstop that fires at half the intended spend would stop
    # a legitimate call.
    budget = credits_for(
        "x" * (character_cap + DEFAULT_TURN_HEADROOM), DEFAULT_ELEVENLABS_MODEL
    )
    adviser_tts = ElevenLabsTTS(voice_id=ADVISER_VOICE, credit_budget=budget)
    customer_tts = ElevenLabsTTS(voice_id=CUSTOMER_VOICE, credit_budget=budget)
    engines = (adviser_tts, customer_tts)
    stt = DeepgramSTT(language="en", smart_format=False, want_display=True)
    ledger = SpokenLedger(character_cap=character_cap)
    trainee = SpokenTrainee(
        inner=inner_trainee,
        channel=AudioChannel(
            tts=adviser_tts, stt=stt, voice=ADVISER_VOICE, speaker="trainee", ledger=ledger
        ),
    )
    voice = SpokenCustomerVoice(
        inner=inner_voice,
        channel=AudioChannel(
            tts=customer_tts, stt=stt, voice=CUSTOMER_VOICE, speaker="customer", ledger=ledger
        ),
    )

    coach = RoleplayCoach(scorer=RubricScorer())
    try:
        conversation = coach.converse(
            scenario_id=SPOKEN_ROW.scenario_id,
            profile=profile,
            trainee=trainee,
            customer_voice=voice,
            jurisdiction=SPOKEN_ROW.jurisdiction,
            language=SPOKEN_ROW.language,
            max_turns=max_turns,
            session_id=SPOKEN_SESSION_ID,
            adapter=SPOKEN_ADAPTER,
        )
    finally:
        # Money already spent survives a crash: the model turns are in the
        # cassette and the clips are in the digest cache.
        cassette.save()

    trace = conversation.trace
    trainee_stop = trainee.stop_reason or "unknown"

    # Every clip that was paid for must appear in the trace. A note keeps
    # `turn=None` until the loop collects it and emits its events, so a note
    # still holding None is an utterance that was synthesised, transcribed and
    # then dropped on the floor — the loop hitting its own turn cap on an
    # utterance the trainee had already spoken, say. It is money spent on audio
    # the recording would contain and the transcript would not, and the manifest
    # and the trace would disagree about how many turns the call had. Cheap to
    # check, and impossible to notice later.
    orphaned = [n for n in ledger.notes if n.turn is None]
    if orphaned:
        raise RuntimeError(
            f"{len(orphaned)} synthesised turn(s) never reached the trace "
            f"(speakers: {', '.join(n.speaker for n in orphaned)}). The clips are in "
            "the digest cache so the spend is not lost, but this call's manifest "
            "and trace would disagree about how many turns were spoken. Re-run with "
            "a turn budget the speakers can respect."
        )

    # The live scorer, recorded so the grade replays offline forever.
    from lab.judges.judge import RetryPolicy  # local: provider-path only

    wrapper = recording_completion(
        live_completion(retry=RetryPolicy()), rubric_version=SCORER_RUBRIC_VERSION
    )
    live_scorer = LiveRubricScorer(
        completion=wrapper,
        model=os.environ[SCORER_MODEL_ENV_VAR],
        rubric_version=SCORER_RUBRIC_VERSION,
    )
    deterministic, live_card, live_raw = _score_both(trace, live_scorer=live_scorer)
    # The committed recording carries the public model label, never the route:
    # a route names somebody's private deployment and a fixture is public.
    for call in wrapper.recording.calls:
        call.model = label

    deltas = recognition_deltas(ledger.notes)
    effect = channel_effect(
        ledger.notes,
        profile=profile,
        scenario_id=SPOKEN_ROW.scenario_id,
        jurisdiction=SPOKEN_ROW.jurisdiction,
        language=SPOKEN_ROW.language,
        max_turns=max_turns,
        trainee_stop=trainee_stop,
        customer_leaks=voice.leaks,
        trainee_repr=repr(trainee),
        voice_repr=repr(voice),
    )

    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    call_audio = assemble_call(ledger.clips)
    write_audio(directory / FULL_CALL_WAV.name, call_audio, _SR)
    write_jsonl(trace, directory / TRACE_PATH.name)
    wrapper.recording.save(directory / SCORER_RECORDING_PATH.name)

    manifest = {
        "session": {
            "scenario_id": SPOKEN_ROW.scenario_id,
            "session_id": SPOKEN_SESSION_ID,
            "adapter": SPOKEN_ADAPTER,
            "persona": SPOKEN_ROW.customer,
            "competence": SPOKEN_ROW.competence,
            "jurisdiction": SPOKEN_ROW.jurisdiction,
            "language": SPOKEN_ROW.language,
            "model_label": label,
            "temperature": 0.0,
            "turn_budget": max_turns,
            "character_cap": character_cap,
            "trainee_stop": trainee_stop,
            "customer_leaks": voice.leaks,
            "voice_fallbacks": voice.fallbacks,
            "trainee_repr": repr(trainee),
            "voice_repr": repr(voice),
            "scorer_rubric": SCORER_RUBRIC_VERSION,
        },
        "engines": {
            "tts_adviser": adviser_tts.name,
            "tts_customer": customer_tts.name,
            "stt": stt.name,
            "adviser_voice": ADVISER_VOICE,
            "customer_voice": CUSTOMER_VOICE,
        },
        "turns": [note.model_dump(mode="json") for note in ledger.notes],
        "assembly": {
            "gap_s": TURN_GAP_S,
            "sample_rate": _SR,
            "duration_s": round(float(call_audio.size) / _SR, 6),
            "audio_sha256": audio_digest(call_audio, _SR),
        },
        "spend": {
            "elevenlabs_characters_submitted": ledger.characters_submitted,
            "elevenlabs_characters_charged": _sum_of(engines, "characters_spent"),
            "elevenlabs_credits_charged": _sum_of(engines, "credits_spent"),
            "elevenlabs_cached_lines": _sum_of(engines, "cached_lines"),
            "deepgram_audio_seconds": round(stt.audio_seconds, 3),
            "deepgram_submitted_seconds": round(_submitted_seconds(stt), 3),
            "deepgram_requests": stt.requests,
        },
    }
    (directory / MANIFEST_PATH.name).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    result = SpokenCallResult(
        trace=trace,
        notes=tuple(ledger.notes),
        deterministic_card=deterministic,
        live_card=live_card,
        live_raw=live_raw,
        deltas=tuple(deltas),
        effect=effect,
        call_duration_s=float(call_audio.size) / _SR,
        characters_submitted=ledger.characters_submitted,
        characters_charged=_sum_of(engines, "characters_spent"),
        tts_credits_spent=_sum_of(engines, "credits_spent"),
        cached_lines=_sum_of(engines, "cached_lines"),
        deepgram_seconds=stt.audio_seconds,
        deepgram_submitted_seconds=_submitted_seconds(stt),
        deepgram_requests=stt.requests,
        trainee_stop=trainee_stop,
        customer_leaks=voice.leaks,
        source="recorded",
    )
    _write_scorecards(directory / SCORECARDS_PATH.name, result)
    _verify_replays(result, directory)
    return result


def _verify_replays(live: SpokenCallResult, directory: Path) -> None:
    """Replay the fixtures that were just written, and refuse to disagree with them.

    The cardinal rule is that a fresh clone with no keys reproduces this call. That
    is a claim about files, and the only moment it can be checked for free is now,
    while the live result is still in memory — an hour from now the trace is gone
    and a mismatch is an unfalsifiable argument about which run was right. So the
    recording is replayed immediately and held to the live numbers: same
    deterministic card, same live card, same channel effect.

    A failure here means the fixtures do not reproduce the call they were written
    from, and it is raised rather than warned about. The audio, the cassette and
    the scorer recording are all on disk by this point, so nothing that was paid
    for is lost — the call can be re-derived once the divergence is fixed.
    """
    replayed = replay_spoken_call(directory=directory)
    mismatches: list[str] = []
    if replayed.deterministic_card != live.deterministic_card:
        mismatches.append(
            f"deterministic card: live {live.deterministic_card.summary_line()} "
            f"vs replayed {replayed.deterministic_card.summary_line()}"
        )
    if replayed.live_card != live.live_card:
        mismatches.append("live scorer card differs between the run and its replay")
    if replayed.effect != live.effect:
        mismatches.append("channel effect differs between the run and its replay")
    if [d.text_heard for d in replayed.deltas] != [d.text_heard for d in live.deltas]:
        mismatches.append("recognition deltas differ between the run and its replay")
    if mismatches:
        raise RuntimeError(
            "the committed fixtures do not reproduce the call they were written "
            "from, so the offline replay would report a different result than the "
            "run that paid for it:\n  - " + "\n  - ".join(mismatches) + "\n"
            f"Nothing is lost — the audio, the cassette and the scorer recording are "
            f"all under {directory} — but the replay path needs fixing before this "
            "call can be committed as evidence."
        )


def _card_json(card: ScoreCard) -> dict[str, Any]:
    return {
        "criteria": dict(card.criteria),
        "total": card.total,
        "max_total": card.max_total,
        "verdict": card.verdict,
        "claims": dict(card.claims),
        "feedback": card.feedback,
    }


def _write_scorecards(path: Path, result: SpokenCallResult) -> None:
    document = {
        "deterministic": _card_json(result.deterministic_card),
        "live": None if result.live_card is None else _card_json(result.live_card),
        "scorer_rubric": result.scorer_rubric,
        "verdicts_agree": result.scorers_agree,
        "channel_effect": result.effect.model_dump(mode="json"),
        "recognition_deltas": [d.model_dump(mode="json") for d in result.deltas],
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Offline replay
# --------------------------------------------------------------------------- #


def verify_recording(root: Path, manifest: dict[str, Any]) -> None:
    """Check the committed WAV is still the audio the manifest describes.

    The recording is the one artifact a reader can check with their own ears, and
    it is also the one nothing else depends on — every number in this pack is
    computed from the manifest's text, so a WAV that was re-encoded, truncated or
    swapped would keep passing every other test in the suite while no longer being
    the call. Digesting it on the replay path is what stops "here is the audio"
    from becoming an unverified claim.

    The digest is taken over 16-bit PCM (`audio_digest`), which is exactly what a
    `.wav` stores, so a lossless round trip through the file is bit-stable and a
    mismatch means the bytes really did change.
    """
    wav = root / FULL_CALL_WAV.name
    assembly = manifest.get("assembly", {})
    expected = assembly.get("audio_sha256")
    if not expected:
        return
    audio, sample_rate = read_audio(wav)
    actual = audio_digest(audio, sample_rate)
    if actual != expected:
        raise RuntimeError(
            f"{wav} is not the recording this manifest describes: it digests to "
            f"{actual[:12]} where the manifest records {expected[:12]}. The score "
            "cards and the transcript below are computed from the manifest, so they "
            "would still agree with each other while no longer matching the audio a "
            "reader can play. Re-record the call, or restore the committed WAV."
        )
    duration_s = float(audio.size) / float(sample_rate)
    recorded = float(assembly.get("duration_s", duration_s))
    if abs(duration_s - recorded) > 0.01:
        raise RuntimeError(
            f"{wav} is {duration_s:.3f}s but the manifest records {recorded:.3f}s"
        )


def replay_spoken_call(
    *,
    directory: str | Path = SPOKEN_DIR,
) -> SpokenCallResult:
    """Rebuild the committed spoken call with zero keys and re-grade it.

    Recomputes rather than reads back: the notes drive the production loop, so
    the trace, the disclosure ledger, the persona ledgers and the deterministic
    score are produced again by the same code that produced them live. The live
    scorer's answer replays from the committed recording, and its prompt digest
    must still match — a rubric edit makes this raise instead of drifting.
    """
    root = Path(directory)
    manifest = json.loads((root / MANIFEST_PATH.name).read_text(encoding="utf-8"))
    session = manifest["session"]
    notes = [AudioTurnNote.model_validate(entry) for entry in manifest["turns"]]
    profile = load_customer_profiles()[session["persona"]]
    verify_recording(root, manifest)

    conversation = _converse_from_notes(
        notes,
        profile=profile,
        scenario_id=session["scenario_id"],
        jurisdiction=session["jurisdiction"],
        language=session["language"],
        max_turns=int(session["turn_budget"]),
        trainee_stop=session["trainee_stop"],
        customer_leaks=int(session["customer_leaks"]),
        trainee_repr=session["trainee_repr"],
        voice_repr=session["voice_repr"],
        session_id=session["session_id"],
    )
    trace = conversation.trace

    live_scorer = LiveRubricScorer(
        completion=replay_completion(root / SCORER_RECORDING_PATH.name),
        model=session["model_label"],
        rubric_version=session["scorer_rubric"],
        strict=True,
    )
    deterministic, live_card, live_raw = _score_both(trace, live_scorer=live_scorer)

    effect = channel_effect(
        notes,
        profile=profile,
        scenario_id=session["scenario_id"],
        jurisdiction=session["jurisdiction"],
        language=session["language"],
        max_turns=int(session["turn_budget"]),
        trainee_stop=session["trainee_stop"],
        customer_leaks=int(session["customer_leaks"]),
        trainee_repr=session["trainee_repr"],
        voice_repr=session["voice_repr"],
    )
    return SpokenCallResult(
        trace=trace,
        notes=tuple(notes),
        deterministic_card=deterministic,
        live_card=live_card,
        live_raw=live_raw,
        deltas=tuple(recognition_deltas(notes)),
        effect=effect,
        call_duration_s=float(manifest["assembly"]["duration_s"]),
        characters_submitted=int(manifest["spend"]["elevenlabs_characters_submitted"]),
        characters_charged=int(manifest["spend"]["elevenlabs_characters_charged"]),
        tts_credits_spent=int(manifest["spend"]["elevenlabs_credits_charged"]),
        cached_lines=int(manifest["spend"]["elevenlabs_cached_lines"]),
        deepgram_seconds=float(manifest["spend"]["deepgram_audio_seconds"]),
        deepgram_submitted_seconds=float(
            manifest["spend"]["deepgram_submitted_seconds"]
        ),
        deepgram_requests=int(manifest["spend"]["deepgram_requests"]),
        trainee_stop=session["trainee_stop"],
        customer_leaks=int(session["customer_leaks"]),
        scorer_rubric=session["scorer_rubric"],
    )


# --------------------------------------------------------------------------- #
# `python -m roleplay.spoken`
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    """Replay the committed call by default; run it live behind the switch.

    Replay is the default because it is the mode a fresh clone has: free,
    offline, and answering the same questions. `--record` refuses with the full
    list of missing pieces unless `LAB_LIVE_SPOKEN=1` and every key are set.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--record",
        action="store_true",
        help=f"Run the call live (needs {LIVE_SPOKEN_ENV_VAR}=1 and every key).",
    )
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--character-cap", type=int, default=DEFAULT_CHARACTER_CAP)
    parser.add_argument(
        "--trainee-factory",
        default=None,
        metavar="MODULE:CALLABLE",
        help=(
            "With --record: dotted path to a factory that builds the adviser under "
            f"test (default: ${TRAINEE_FACTORY_ENV_VAR}, else the built-in model "
            "trainee). See docs/ADAPTER.md."
        ),
    )
    args = parser.parse_args(argv)

    try:
        trainee_factory = resolve_trainee_factory(args.trainee_factory)
    except TraineeFactoryError as exc:
        print(f"trainee factory: {exc}", file=sys.stderr)
        return 2

    try:
        if args.record:
            result = run_spoken_call(
                max_turns=args.max_turns,
                character_cap=args.character_cap,
                trainee_factory=trainee_factory,
            )
        else:
            result = replay_spoken_call()
    except (NotLiveError, FileNotFoundError, TraineeFactoryError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(result.report())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
