"""The live half: a real two-participant WebRTC session, instrumented at both ends.

WHAT THIS DEMONSTRATES
----------------------
Three measurements exist only here, and everything else in this repo runs
in-process because in-process is faster, deterministic and owns its own clock.
This module is what "use the expensive tool only where it is the only tool" looks
like in code: it is the smallest amount of real-time networking that makes the
delivery gap, real degradation and connection lifecycle observable, and nothing
else in `lab/` imports it.

THE SHAPE OF THE SESSION
------------------------
Two participants in one room, both driven by this process:

    publisher   creates an audio source, publishes a track, and pushes 20 ms
                frames of a committed clip in real time
    receiver    subscribes to that track and writes down when each frame arrived

The "agent" is the publisher and the "listener" is the receiver. There is no
model, no speech recognition and no conversation, because none of those are what
this tier measures — a transport does not care what the audio means, and putting
an LLM in the loop would add a second source of variance to a measurement whose
whole value is that its ground truth is known.

WHY BOTH ENDS ARE IN ONE PROCESS
--------------------------------
Because the alternative silently ruins the measurement. The delivery gap is the
difference between two instants; if those instants come from two machines, the
figure carries the two machines' clock offset, which is unbounded and unmeasured
and typically larger than the thing being measured. One process means one
`MonotonicClock`, one origin, and a subtraction with no skew term. The cost is
that the network path is loopback-to-cloud-and-back rather than between two
distant callers, and that is stated in the report rather than hidden: the figure
is a floor on the delivery gap, not an estimate of the worst case.

MEASUREMENT DISCIPLINE, COPIED FROM THE GATE ON PURPOSE
-------------------------------------------------------
`lab.voice.calibration` proves the harness can recover a known delay while
excluding its own compute, and the way it does that is a rule: capture a bare
float at the boundary, do the work, build the record afterwards. Both loops here
obey it.

    push loop     t = clock.now() is the last statement before capture_frame,
                  and the ledger row is appended after the await returns
    receive loop  t = clock.now() is the FIRST statement after a frame arrives,
                  before the energy is computed or anything is appended

Get the receive loop the other way round — compute the RMS, then read the clock —
and every arrival is late by the cost of the arithmetic, which inflates the
delivery gap by the harness's own compute. That is the exact failure the gate
exists to catch, one layer down.

REAL-TIME PACING IS AGAINST A DEADLINE, NOT A SLEEP
---------------------------------------------------
`await asyncio.sleep(0.020)` per frame does not push 50 frames per second; it
pushes 50 minus the loop's overhead, and the shortfall accumulates. Over a
three-second clip that starves the send queue, the receiver hears gaps, and those
gaps are indistinguishable from packet loss in the ledger — so the harness would
be measuring its own pacing and calling it a channel condition. Frames are
therefore scheduled against a running deadline, and the drift is bounded rather
than cumulative.

FAILURE IS A TIMEOUT WITH A NAME
--------------------------------
A room is real time, so anything can hang: signalling, subscription, a track that
is published and never arrives. Every wait here has a cap and every cap raises
`TransportTimeout` naming the phase it gave up in. A test that hangs teaches
people to skip the suite; a test that fails in twenty seconds saying
"subscription never arrived" gets fixed.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import os
import secrets
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from lab.clock import Clock, MonotonicClock
from lab.voice.engines.base import EngineUnavailable
from lab.voice.transport.records import (
    ArrivalLedger,
    PushLedger,
    TransportEventRecord,
    TransportRecording,
    UtteranceRecord,
    url_digest,
)

__all__ = [
    "LIVEKIT_KEY_ENV_VAR",
    "LIVEKIT_SECRET_ENV_VAR",
    "LIVEKIT_URL_ENV_VAR",
    "LIVE_TRANSPORT_ENV_VAR",
    "LiveKitTransport",
    "TransportTimeout",
    "longest_quiet_ms",
    "require_segmentable",
    "livekit_available",
    "livekit_diagnosis",
]

#: Opt-in flag. Nothing in this module touches a network unless it is set, on the
#: same pattern as `LAB_LIVE_TTS` and `LAB_LIVE_STT`: presence of a credential is
#: not permission to spend it.
LIVE_TRANSPORT_ENV_VAR: str = "LAB_LIVE_TRANSPORT"

#: Names only. The values are read at call time, never stored on the instance,
#: never logged, and never written into a recording — see `records.url_digest`.
LIVEKIT_URL_ENV_VAR: str = "LIVEKIT_URL"
LIVEKIT_KEY_ENV_VAR: str = "LIVEKIT_API_KEY"
LIVEKIT_SECRET_ENV_VAR: str = "LIVEKIT_API_SECRET"

#: The extra that installs the client. Named in every refusal, because "no module
#: named livekit" is not a remedy.
TRANSPORT_EXTRA: str = 'pip install -e ".[transport]"'

_IMPORT_ERROR: str | None = None
try:  # pragma: no cover - import shape depends on the environment, both paths trivial
    from livekit import api as _lk_api
    from livekit import rtc as _lk_rtc
except Exception as exc:  # noqa: BLE001 - any import failure means "not installed"
    _lk_api = None  # type: ignore[assignment]
    _lk_rtc = None  # type: ignore[assignment]
    _IMPORT_ERROR = f"{exc.__class__.__name__}: {exc}"


def livekit_available() -> bool:
    """True when the WebRTC client library imported."""
    return _lk_rtc is not None and _lk_api is not None


def livekit_diagnosis() -> str:
    """One line on why the client is or is not importable."""
    if livekit_available():
        return "the livekit client library is importable"
    return f"the livekit client library is not importable ({_IMPORT_ERROR}); {TRANSPORT_EXTRA}"


class TransportTimeout(RuntimeError):
    """A real-time phase did not complete inside its cap.

    Carries the phase separately from the message so a report can group hangs by
    where they happened rather than by parsing prose.
    """

    def __init__(self, phase: str, seconds: float, detail: str = "") -> None:
        self.phase = phase
        self.seconds = seconds
        suffix = f" {detail}" if detail else ""
        super().__init__(
            f"transport phase {phase!r} did not complete within {seconds:.1f}s.{suffix} "
            "A real-time session can hang for reasons outside this process; this is a "
            "timeout with a name rather than a stuck test."
        )


# --------------------------------------------------------------------------- #
# Clip preparation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClipFrames:
    """A committed clip, cut into fixed-length PCM16 frames with their energies.

    Prepared before the session starts, deliberately. Decoding and framing audio
    inside a real-time push loop would put file I/O and numpy on the critical path
    of a latency measurement.
    """

    name: str
    sample_rate: int
    frame_samples: int
    frames: tuple[bytes, ...]
    rms: tuple[float, ...]

    @property
    def frame_ms(self) -> float:
        return self.frame_samples * 1000.0 / self.sample_rate

    @property
    def duration_s(self) -> float:
        return len(self.frames) * self.frame_samples / self.sample_rate


def load_clip_frames(
    path: str | Path, *, frame_ms: float = 20.0, sample_rate: int = 16_000
) -> ClipFrames:
    """Decode a committed clip and cut it into PCM16 frames.

    Needs numpy and soundfile, which is why it is a module-level function called
    before a session rather than a method used during one. Refuses a clip whose
    sample rate is not the session's: resampling here would change the audio the
    measurement is about, and silently.
    """
    from lab.voice.engines.audiofile import read_audio  # noqa: PLC0415 - needs numpy
    from lab.voice.engines.base import quantise_pcm16  # noqa: PLC0415 - needs numpy

    target = Path(path)
    audio, rate = read_audio(target)
    if rate != sample_rate:
        raise EngineUnavailable(
            "transport:clip",
            f"{target.name} is {rate} Hz but the session runs at {sample_rate} Hz",
            "pick a clip recorded at the session rate; resampling here would quietly "
            "change the audio the measurement is about",
        )
    if audio.ndim > 1:  # pragma: no cover - committed clips are mono
        audio = audio[:, 0]
    quantised = quantise_pcm16(audio)
    frame_samples = int(round(sample_rate * frame_ms / 1000.0))
    frames: list[bytes] = []
    energies: list[float] = []
    for start in range(0, len(quantised) - frame_samples + 1, frame_samples):
        block = quantised[start : start + frame_samples]
        frames.append(block.tobytes())
        scaled = block.astype("float64") / 32768.0
        energies.append(float(statistics.fmean(scaled * scaled) ** 0.5))
    if not frames:
        raise EngineUnavailable(
            "transport:clip",
            f"{target.name} is shorter than one {frame_ms:.0f} ms frame",
            "pick a longer clip",
        )
    return ClipFrames(
        name=target.name,
        sample_rate=sample_rate,
        frame_samples=frame_samples,
        frames=tuple(frames),
        rms=tuple(energies),
    )


def longest_quiet_ms(clip: ClipFrames, *, threshold_rms: float) -> float:
    """The longest stretch inside a clip that a segmenter would read as silence.

    A clip published by this tier has to arrive as exactly ONE speech run, because
    that is what lets run `k` be paired with utterance `k` without comparing
    timestamps across streams. A clip containing a pause longer than the
    segmenter's gap tolerance arrives as two runs, the pairing refuses, and the row
    reports nothing — after a live session has already been spent.

    So the property is checked before the room is opened. Measured across this
    repo's committed clips this returns anything from 60 ms to 280 ms, which is
    why the check exists rather than a comment claiming sentences do not have
    pauses in them.
    """
    longest = 0
    current = 0
    for energy in clip.rms:
        current = current + 1 if energy <= threshold_rms else 0
        longest = max(longest, current)
    return longest * clip.frame_ms


def require_segmentable(
    clip: ClipFrames, *, threshold_rms: float, max_gap_ms: float
) -> None:
    """Refuse a clip whose internal pauses would split it into two speech runs."""
    quiet = longest_quiet_ms(clip, threshold_rms=threshold_rms)
    if quiet >= max_gap_ms:
        raise EngineUnavailable(
            "transport:clip",
            f"{clip.name} contains a {quiet:.0f} ms stretch below {threshold_rms} RMS, "
            f"at or beyond the segmenter's {max_gap_ms:.0f} ms gap tolerance, so it "
            "would arrive as two speech runs and the run-to-utterance pairing would "
            "refuse after the session had been spent",
            "pick a clip with no internal pause that long (a single short utterance "
            "rather than two sentences), or raise the tolerance — but not past the "
            "silence pushed between utterances, or two turns will merge instead",
        )


# --------------------------------------------------------------------------- #
# The session
# --------------------------------------------------------------------------- #


class LiveKitTransport:
    """A real WebRTC room, used as a measuring instrument.

    Gated on `$LAB_LIVE_TRANSPORT` *and* three credentials *and* an importable
    client, and `missing_requirements()` names every one that is absent rather
    than stopping at the first — being told about one blocker at a time turns
    setup into four failed runs.

    Nothing here is cached or replayed. The recordings this produces are the
    replayable artefact; see `records.py` for why that split is the only honest
    one available for a real-time session.
    """

    def __init__(
        self,
        *,
        env_var: str = LIVE_TRANSPORT_ENV_VAR,
        url_env_var: str = LIVEKIT_URL_ENV_VAR,
        key_env_var: str = LIVEKIT_KEY_ENV_VAR,
        secret_env_var: str = LIVEKIT_SECRET_ENV_VAR,
        sample_rate: int = 16_000,
        room_prefix: str = "lab-transport",
        connect_timeout_s: float = 20.0,
        subscribe_timeout_s: float = 20.0,
        session_cap_s: float = 120.0,
        token_ttl_s: float = 600.0,
        settle_s: float = 0.6,
    ) -> None:
        """
        Args:
            env_var: Live opt-in flag.
            url_env_var: Name of the variable holding the signalling URL.
            key_env_var: Name of the variable holding the API key.
            secret_env_var: Name of the variable holding the API secret.
            sample_rate: Session sample rate; must match the clips.
            room_prefix: Rooms are `prefix-<random>`, fresh per run, so two runs
                never share a room and a stale participant cannot contaminate a
                measurement.
            connect_timeout_s: Cap on signalling for each participant.
            subscribe_timeout_s: Cap on the receiver subscribing to the track.
            session_cap_s: Hard ceiling on a whole row. Rooms are real time and
                cannot be sped up, so the only way to keep this tier bounded is to
                bound it explicitly.
            token_ttl_s: Access-token lifetime. Minted at run time, never written
                to disk, and short because a token in a log is a credential in a
                log.
            settle_s: Quiet time after subscription before the first utterance, so
                the receiver's jitter buffer has reached steady state. Measuring
                the first frames of a fresh buffer would report a warm-up as a
                delivery gap.
        """
        self.env_var = env_var
        self.url_env_var = url_env_var
        self.key_env_var = key_env_var
        self.secret_env_var = secret_env_var
        self.sample_rate = sample_rate
        self.room_prefix = room_prefix
        self.connect_timeout_s = connect_timeout_s
        self.subscribe_timeout_s = subscribe_timeout_s
        self.session_cap_s = session_cap_s
        self.token_ttl_s = token_ttl_s
        self.settle_s = settle_s
        self.name = f"transport:webrtc/livekit/{sample_rate}"

    # ----------------------------------------------------------- availability

    @property
    def live_enabled(self) -> bool:
        return bool(os.environ.get(self.env_var))

    def credentials_present(self) -> bool:
        """True when all three variables are set. Reads presence; never a value."""
        return all(
            bool(os.environ.get(name))
            for name in (self.url_env_var, self.key_env_var, self.secret_env_var)
        )

    def available(self) -> bool:
        return livekit_available() and self.live_enabled and self.credentials_present()

    def missing_requirements(self) -> list[str]:
        """Everything standing between this instrument and a live room."""
        missing: list[str] = []
        if not livekit_available():
            missing.append("livekit (client library)")
        if not self.live_enabled:
            missing.append(self.env_var)
        missing.extend(
            name
            for name in (self.url_env_var, self.key_env_var, self.secret_env_var)
            if not os.environ.get(name)
        )
        return missing

    def describe(self) -> str:
        missing = self.missing_requirements()
        state = "ready" if not missing else f"unavailable (missing {', '.join(missing)})"
        return (
            f"{self.name}: {state}; rooms named {self.room_prefix}-<random>, "
            f"session capped at {self.session_cap_s:.0f}s, tokens live "
            f"{self.token_ttl_s:.0f}s and are minted per run"
        )

    def _require(self) -> None:
        missing = self.missing_requirements()
        if missing:
            raise EngineUnavailable(
                self.name,
                f"missing {', '.join(missing)}",
                f"export {self.env_var}=1 to permit a real room session, export the "
                f"three LiveKit variables, and {TRANSPORT_EXTRA}",
            )

    # ------------------------------------------------------------------ tokens

    def _token(self, identity: str, room: str) -> str:
        """Mint a short-lived join token. Never returned to a caller, never logged."""
        assert _lk_api is not None
        return (
            _lk_api.AccessToken(
                os.environ[self.key_env_var], os.environ[self.secret_env_var]
            )
            .with_identity(identity)
            .with_name(identity)
            .with_grants(
                _lk_api.VideoGrants(
                    room_join=True, room=room, can_publish=True, can_subscribe=True
                )
            )
            .with_ttl(dt.timedelta(seconds=self.token_ttl_s))
            .to_jwt()
        )

    # -------------------------------------------------------------- the rows

    def record_delivery_gap(
        self,
        clip: ClipFrames,
        *,
        turns: int = 12,
        row: str = "audio-transport-delivery-gap",
        silence_between_ms: float = 400.0,
    ) -> TransportRecording:
        """Row 1. Time `turns` utterances agent-side and receiver-side.

        Utterances are separated by real silence so that the receiver's stream
        segments into exactly `turns` speech runs; that is what lets run `k` be
        paired with utterance `k` by ordinal instead of by timestamp.
        """
        return self._run(
            self._delivery_gap_session(
                clip, turns=turns, silence_between_ms=silence_between_ms, row=row
            )
        )

    def record_degradation(
        self,
        clip: ClipFrames,
        *,
        drop_every: int = 4,
        row: str = "audio-transport-degradation",
        silence_between_ms: float = 400.0,
    ) -> TransportRecording:
        """Row 2. One clean utterance, then the same clip with 1-in-`drop_every` withheld.

        The clean utterance is the control and it is not optional: without it the
        transport's silent-frame fraction under loss cannot be told apart from the
        codec's, the threshold's, or the room's idle behaviour.
        """
        return self._run(
            self._degradation_session(
                clip, drop_every=drop_every, silence_between_ms=silence_between_ms, row=row
            )
        )

    def record_lifecycle(
        self,
        clip: ClipFrames,
        *,
        drop_after_frames: int = 40,
        row: str = "audio-transport-lifecycle",
    ) -> TransportRecording:
        """Row 3. Drop the publisher mid-utterance, rejoin, and push a fresh turn.

        `drop_after_frames` is counted in pushed frames rather than in seconds so
        that the interruption lands at a known point *in the utterance* — "800 ms
        in" is a property of the machine, "frame 40 of 143" is a property of the
        test.
        """
        return self._run(
            self._lifecycle_session(clip, drop_after_frames=drop_after_frames, row=row)
        )

    # --------------------------------------------------------------- plumbing

    def _run(self, coroutine: Any) -> TransportRecording:
        """Drive one row to completion under the session cap."""
        self._require()

        async def guarded() -> TransportRecording:
            try:
                return await asyncio.wait_for(coroutine, self.session_cap_s)
            except asyncio.TimeoutError as exc:
                raise TransportTimeout(
                    "session", self.session_cap_s, "the row did not finish"
                ) from exc

        return asyncio.run(guarded())

    def _room_name(self) -> str:
        return f"{self.room_prefix}-{secrets.token_hex(4)}"

    def _header(self, row: str, room: str, clip: ClipFrames) -> dict[str, Any]:
        return {
            "row": row,
            "room": room,
            "url_digest": url_digest(os.environ[self.url_env_var]),
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "sample_rate": clip.sample_rate,
            "frame_ms": clip.frame_ms,
        }

    @contextlib.asynccontextmanager
    async def _session(self, clip: ClipFrames) -> Any:
        """Open a live session and guarantee it is torn down.

        One implementation for all three rows. The lifecycle row needs to swap
        publishers mid-session, so that is a method on the session object rather
        than a second copy of the connect-and-receive machinery: two receive loops
        would be two chances to get the boundary discipline wrong, and the claim
        that both ends are instrumented identically has to stay true by
        construction.
        """
        session = _Session(self, clip)
        try:
            await session.open()
            yield session
        finally:
            await session.close()

    # ------------------------------------------------------------ the sessions

    async def _delivery_gap_session(
        self, clip: ClipFrames, *, turns: int, silence_between_ms: float, row: str
    ) -> TransportRecording:
        async with self._session(clip) as session:
            for turn in range(1, turns + 1):
                await session.push_silence(silence_between_ms)
                await session.push(turn=turn)
            await session.push_silence(silence_between_ms)
            session.note(
                f"{turns} utterance(s) of {clip.name} ({clip.duration_s:.2f}s each), "
                f"separated by {silence_between_ms:.0f} ms of pushed silence so the "
                "receiver's stream segments into one run per utterance"
            )
            return session.finish(self._header(row, session.room_name, clip))

    async def _degradation_session(
        self, clip: ClipFrames, *, drop_every: int, silence_between_ms: float, row: str
    ) -> TransportRecording:
        withheld = {index for index in range(len(clip.frames)) if index % drop_every == 0}
        async with self._session(clip) as session:
            await session.push_silence(silence_between_ms)
            await session.push(turn=1)
            await session.push_silence(silence_between_ms)
            await session.push(turn=2, withhold=withheld)
            await session.push_silence(silence_between_ms)
            session.note(
                f"turn 1 is the control (nothing withheld); turn 2 withholds every "
                f"{drop_every}th frame of the same clip, a nominal "
                f"{len(withheld) / len(clip.frames):.1%} loss, injected at the sender so "
                "that what is observed is the transport's own concealment of a gap"
            )
            return session.finish(self._header(row, session.room_name, clip))

    async def _lifecycle_session(
        self, clip: ClipFrames, *, drop_after_frames: int, row: str
    ) -> TransportRecording:
        async with self._session(clip) as session:
            # The interrupted turn: pushed part way, then the transport is taken
            # away underneath it.
            await session.push(turn=1, stop_after=drop_after_frames)
            await session.drop(reason="dropped_mid_utterance")
            await asyncio.sleep(0.4)
            await session.rejoin()
            await session.push(turn=2)
            await session.push_silence(300.0)
            session.note(
                f"turn 1 was cut off after {drop_after_frames} of {len(clip.frames)} "
                "frames by disconnecting the publisher mid-push; turn 2 is a fresh "
                "utterance after rejoining with the same identity. Nothing retransmits "
                "the remainder of turn 1, which is the point of the row"
            )
            return session.finish(self._header(row, session.room_name, clip))


class _Session:
    """One live room, both participants, and the ledgers they fill in.

    Holds every piece of state a row needs and exposes exactly four verbs —
    `push`, `push_silence`, `drop`, `rejoin`. The rows read as the thing they
    test, and the networking lives in one place.
    """

    def __init__(self, transport: LiveKitTransport, clip: ClipFrames) -> None:
        assert _lk_rtc is not None
        self.transport = transport
        self.clip = clip
        self.clock: Clock = MonotonicClock()
        self.room_name = transport._room_name()
        self.frame_s = clip.frame_samples / clip.sample_rate

        self.arrival_ts: list[float] = []
        self.arrival_rms: list[float] = []
        self.frame_samples = 160
        self.lifecycle: list[TransportEventRecord] = []
        self.utterances: list[UtteranceRecord] = []
        self.notes: list[str] = []

        self._publisher: Any = None
        self._receiver: Any = _lk_rtc.Room()
        self._source: Any = None
        self._subscribed = asyncio.Event()
        self._drains: list[asyncio.Task[None]] = []
        self._attempt = 0
        self._wire_receiver()

    # ------------------------------------------------------------- recording

    def note(self, text: str) -> None:
        self.notes.append(text)

    def _event(
        self,
        kind: str,
        participant: str,
        observer: str,
        *,
        attempt: int = 1,
        reason: str | None = None,
    ) -> None:
        """Record a lifecycle fact with the receiver's stream position attached.

        `arrival_index` is what lets `measure.lifecycle_observation` order this
        event against delivered audio without comparing two streams' timestamps.
        """
        self.lifecycle.append(
            TransportEventRecord(
                ts_s=self.clock.now(),
                kind=kind,  # type: ignore[arg-type]
                participant=participant,
                observer=observer,  # type: ignore[arg-type]
                attempt=attempt,
                reason=reason,
                arrival_index=len(self.arrival_ts),
            )
        )

    def _wire_receiver(self) -> None:
        assert _lk_rtc is not None

        async def drain(track: Any) -> None:
            stream = _lk_rtc.AudioStream(
                track, sample_rate=self.transport.sample_rate, num_channels=1
            )
            async for event in stream:
                # BOUNDARY IN. The clock is read before anything is computed from
                # the frame, and the ledger rows are appended after. Reversing
                # these lines charges the harness's own arithmetic to the
                # transport and inflates every delivery gap in the run.
                arrived = self.clock.now()
                frame = event.frame
                self.arrival_ts.append(arrived)
                self.arrival_rms.append(_rms_pcm16_bytes(bytes(frame.data)))
                self.frame_samples = frame.samples_per_channel

        @self._receiver.on("track_subscribed")
        def _on_subscribed(track: Any, publication: Any, participant: Any) -> None:
            if track.kind != _lk_rtc.TrackKind.KIND_AUDIO:
                return
            self._event(
                "subscribed", participant.identity, "receiver", attempt=self._attempt
            )
            self._drains.append(asyncio.create_task(drain(track)))
            self._subscribed.set()

        @self._receiver.on("track_unsubscribed")
        def _on_unsubscribed(track: Any, publication: Any, participant: Any) -> None:
            self._event(
                "unsubscribed", participant.identity, "receiver", attempt=self._attempt
            )

        @self._receiver.on("participant_disconnected")
        def _on_left(participant: Any) -> None:
            self._event(
                "disconnected",
                participant.identity,
                "receiver",
                attempt=self._attempt,
                reason="participant_left",
            )

    # -------------------------------------------------------------- lifecycle

    async def open(self) -> None:
        """Connect the receiver, then join the publisher for the first time."""
        await _capped(
            self._receiver.connect(
                os.environ[self.transport.url_env_var],
                self.transport._token("receiver", self.room_name),
            ),
            "connect:receiver",
            self.transport.connect_timeout_s,
        )
        self._event("connected", "receiver", "receiver")
        await self.rejoin()

    async def rejoin(self) -> None:
        """Join (or rejoin) the publisher: connect, publish a track, wait to be heard.

        A *new* Room object each time, with the same identity. That is a rejoin,
        which is what a production agent does after losing signalling — not a
        resumed connection, and the distinction shows up in what the far side
        observes.
        """
        assert _lk_rtc is not None
        self._attempt += 1
        attempt = self._attempt
        self._subscribed.clear()
        self._publisher = _lk_rtc.Room()
        await _capped(
            self._publisher.connect(
                os.environ[self.transport.url_env_var],
                self.transport._token("agent", self.room_name),
                _lk_rtc.RoomOptions(auto_subscribe=False),
            ),
            f"connect:publisher:attempt-{attempt}",
            self.transport.connect_timeout_s,
        )
        self._event("connected", "agent", "publisher", attempt=attempt)
        # queue_size_ms is deliberately small. A deep send queue would absorb the
        # pacing loop's jitter and release it later, so a measured delivery gap
        # would be mostly this harness's own buffering. `queued_s` in the push
        # ledger records what was actually in it, so the claim is checkable
        # rather than asserted.
        self._source = _lk_rtc.AudioSource(
            self.transport.sample_rate, 1, queue_size_ms=100
        )
        track = _lk_rtc.LocalAudioTrack.create_audio_track("agent-voice", self._source)
        await _capped(
            self._publisher.local_participant.publish_track(
                track,
                _lk_rtc.TrackPublishOptions(
                    source=_lk_rtc.TrackSource.SOURCE_MICROPHONE
                ),
            ),
            f"publish:attempt-{attempt}",
            self.transport.connect_timeout_s,
        )
        self._event("published", "agent", "publisher", attempt=attempt)
        await _capped(
            self._subscribed.wait(),
            f"subscribe:attempt-{attempt}",
            self.transport.subscribe_timeout_s,
        )
        # Let the receiver's jitter buffer reach steady state. Measuring the first
        # frames of a fresh buffer would report a warm-up as a delivery gap.
        await asyncio.sleep(self.transport.settle_s)

    async def drop(self, *, reason: str) -> None:
        """Take the publisher's transport away, mid-utterance if that is where we are."""
        await self._publisher.disconnect()
        self._event("disconnected", "agent", "publisher", attempt=self._attempt, reason=reason)

    async def close(self) -> None:
        """Cancel the receive loops and disconnect both participants, best effort."""
        for task in self._drains:
            task.cancel()
        for room in (self._publisher, self._receiver):
            if room is not None:
                with contextlib.suppress(Exception):
                    await room.disconnect()

    # ------------------------------------------------------------------ verbs

    async def push(
        self,
        *,
        turn: int,
        withhold: set[int] | None = None,
        stop_after: int | None = None,
    ) -> UtteranceRecord:
        """Push one utterance in real time, recording every handover.

        Args:
            turn: 1-based turn number, used to pair the record with a speech run.
            withhold: Source indices to prepare and then deliberately not hand
                over. The time slot is still consumed, because losing a packet
                does not make a call shorter.
            stop_after: Stop after this many *pushed* frames, leaving the
                utterance unfinished. Row 3 uses it to be interrupted mid-turn.
        """
        assert _lk_rtc is not None
        withheld = set(withhold or ())
        clip = self.clip
        ts: list[float] = []
        rms: list[float] = []
        indices: list[int] = []
        queued: list[float] = []
        unsent: list[int] = []
        deadline = self.clock.now()

        for index, payload in enumerate(clip.frames):
            if stop_after is not None and len(ts) >= stop_after:
                unsent.extend(range(index, len(clip.frames)))
                break
            deadline += self.frame_s
            if index in withheld:
                await _sleep_until(self.clock, deadline)
                continue
            frame = _lk_rtc.AudioFrame(payload, clip.sample_rate, 1, clip.frame_samples)
            depth = float(self._source.queued_duration)
            # BOUNDARY OUT. Nothing between this read and the handover.
            handed = self.clock.now()
            await self._source.capture_frame(frame)
            ts.append(handed)
            rms.append(clip.rms[index])
            indices.append(index)
            queued.append(depth)
            await _sleep_until(self.clock, deadline)

        record = UtteranceRecord(
            turn=turn,
            clip=clip.name,
            frame_ms=clip.frame_ms,
            sample_rate=clip.sample_rate,
            pushes=PushLedger(ts_s=ts, rms=rms, source_index=indices, queued_s=queued),
            withheld_source_index=sorted(withheld | set(unsent)),
        )
        self.utterances.append(record)
        return record

    async def push_silence(self, ms: float) -> None:
        """Push real digital silence, same frames, same pace.

        Silence is *pushed* rather than waited out: a track that stops producing
        frames is a different channel condition from one carrying quiet audio, and
        the gaps between utterances have to be the second kind for the receiver's
        stream to segment into one run per utterance.
        """
        assert _lk_rtc is not None
        quiet = bytes(self.clip.frame_samples * 2)
        deadline = self.clock.now()
        for _ in range(max(0, int(round(ms / self.clip.frame_ms)))):
            deadline += self.frame_s
            await self._source.capture_frame(
                _lk_rtc.AudioFrame(quiet, self.clip.sample_rate, 1, self.clip.frame_samples)
            )
            await _sleep_until(self.clock, deadline)

    # ----------------------------------------------------------------- output

    def finish(self, header: dict[str, Any]) -> TransportRecording:
        """Assemble the recording. Validated by pydantic on the way out."""
        return TransportRecording(
            **header,
            utterances=self.utterances,
            arrivals=ArrivalLedger(
                ts_s=self.arrival_ts,
                rms=self.arrival_rms,
                frame_samples=self.frame_samples,
                sample_rate=self.transport.sample_rate,
            ),
            lifecycle=self.lifecycle,
            notes=self.notes,
        )


async def _capped(awaitable: Any, phase: str, seconds: float) -> Any:
    """Await with a cap, converting a hang into a named failure."""
    try:
        return await asyncio.wait_for(awaitable, seconds)
    except asyncio.TimeoutError as exc:
        raise TransportTimeout(phase, seconds) from exc


async def _sleep_until(clock: Clock, deadline: float) -> None:
    """Sleep until a deadline on the session clock, never past it.

    Deadline-based rather than fixed-interval so that pacing error is bounded
    instead of cumulative — see the module docstring.
    """
    remaining = deadline - clock.now()
    if remaining > 0:
        await asyncio.sleep(remaining)


def _rms_pcm16_bytes(payload: bytes) -> float:
    """RMS energy of little-endian PCM16 bytes, scaled to [-1, 1].

    Hand-rolled rather than numpy: it runs once per arriving 10 ms frame inside
    the receive loop, and a vectorised path for 160 samples costs more to set up
    than it saves. It is outside the measured window regardless — the clock is
    read before this is called.
    """
    if not payload:
        return 0.0
    total = 0.0
    for offset in range(0, len(payload) - 1, 2):
        sample = int.from_bytes(payload[offset : offset + 2], "little", signed=True)
        scaled = sample / 32768.0
        total += scaled * scaled
    count = len(payload) // 2
    return (total / count) ** 0.5 if count else 0.0


def frame_energies(samples: Sequence[float], frame_samples: int) -> list[float]:
    """Per-frame RMS of a float sample sequence — the file-ladder side's framing.

    Lives beside the transport's own framing on purpose, so both sides of the
    row-2 comparison are cut up by the same code. Two framings of the same audio
    that differ by one sample give two different silent-frame counts, and the
    comparison would then be measuring the framing.
    """
    energies: list[float] = []
    for start in range(0, len(samples) - frame_samples + 1, frame_samples):
        block = samples[start : start + frame_samples]
        energies.append(statistics.fmean(value * value for value in block) ** 0.5)
    return energies
