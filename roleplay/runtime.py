"""The adapter: one roleplay session, one trace.

WHY THIS DOMAIN NEEDS ITS OWN LOOP
----------------------------------
`lab.simulator.run_scenario` drives a conversation: caller speaks, agent answers,
repeat until someone stops. That is the right loop for a booking assistant and
the wrong one here, because a roleplay session has a **second stage** the
conversational loop has no notion of — after the talking stops, a scorer reads
the whole transcript and produces a grade and a page of feedback. Two stages, one
session, one trace.

So the loop lives here, in the adapter, which is exactly where domain shape is
supposed to live. What does *not* change is everything downstream: the trace
schema, the contract engine, the judge and calibration machinery, and the pass^k
stability primitives are all used unmodified. `roleplay/` imports from `lab/`;
`lab/` has never heard of it. That asymmetry is the retargeting claim, and this
file is where it is either true or false.

MEASUREMENT DECISIONS WORTH READING
-----------------------------------
**The clock defaults to `FakeClock`.** Every timestamp in a roleplay trace is
then exact and free, which is what makes a fixture byte-reproducible. The latency
model still *spends* time — think time per turn, a cost per tool round trip, a
cost per character spoken — so the durations in the trace are the durations this
system claims to have taken, and a real clock produces real waits from the same
code. Nothing here fakes a number it does not also produce.

**Tool events carry real timestamps, not interpolated ones.** The adapter owns
the loop, so it reads the clock as each call happens rather than spreading calls
evenly across a turn window. That removes a whole class of arguments about
ordering: `lab.checks` compares event *positions*, but a reader comparing `ts`
values in the JSONL should see the same story, and here they do.

**The scoring stage is inside the same session.** It could have been a separate
pass over a stored trace, and that would have been easier. It is not, because the
handoff from the customer to the scorer is a real boundary in the product, and a
boundary that is not in the trace is a boundary no check can assert across.

TWO SPEAKERS, TWO SEAMS, ONE TRACE
----------------------------------
A session has two speaking parts and this file owns neither of them. Both are
injected:

    Trainee        who is under test. `ScriptedTrainee` reads a committed list of
                   turns; `roleplay.live.LiveTrainee` generates them from a model
                   at a declared competence level.
    CustomerVoice  how the customer's chosen move is put into words.
                   `ScriptedVoice` uses the phrasing in the profile;
                   `roleplay.live.LiveCustomerVoice` has a model phrase it.

The seams are `Protocol`s, so neither implementation is a subclass of anything
and this module never imports `litellm`, `lab.simulator` or `roleplay.live`. The
dependency runs one way — `roleplay.live` imports this file — which is what keeps
`pytest` on a fresh clone with no keys from touching a provider SDK at import.

**The customer's voice is not the customer's brain.** `CustomerPersona.respond`
decides which concern surfaces, which objection is raised and whether it is
pressed again; a `CustomerVoice` only chooses words for a decision already made.
That is the line that keeps a live customer usable as an instrument: the trace
events, the concern ledger and the objection ledger are produced by the state
machine either way, so every contract in `roleplay.contracts` reads a live session
exactly as it reads a scripted one, and a trainee who never runs discovery still
fails for the right reason.

**The loop stopped assuming a script.** It used to iterate a list of turns, which
made the number of trainee turns known before the session began. A live trainee
decides when it is finished, so the loop now asks for a turn, runs it, and asks
again until the trainee stops or `max_turns` is reached. For a scripted trainee
the iteration is identical turn for turn — that is what keeps every committed
expectation in the corpus valid — and `session_end` now reports the turns that
actually happened rather than the turns that were planned.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

from lab.clock import Clock, FakeClock
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace

from roleplay.persona import (
    CustomerPersona,
    CustomerProfile,
    PersonaTurn,
    classify_trainee_turn,
)
from roleplay.register import DisclosureRegister
from roleplay.scorer import CUSTOMER_AGENT, SCORER_AGENT, RubricScorer, ScoreCard

__all__ = [
    "ADAPTER",
    "TOOL_NAMES",
    "DEFAULT_MAX_TURNS",
    "LatencyModel",
    "DEFAULT_LATENCY",
    "Trainee",
    "CustomerVoice",
    "ScriptedTrainee",
    "ScriptedVoice",
    "RoleplayConversation",
    "RoleplayResult",
    "RoleplayCoach",
    "run_roleplay",
    "stop_reason_of",
]

#: Hard cap on trainee turns in one session. Reached only by a trainee that
#: decides for itself when to stop — a script ends when it ends. It is a cap and
#: not a target: a session that hits it stops for an *instrument* reason, and
#: `session_end` says so, because "the adviser never closed" and "the harness ran
#: out of turns" are different findings and a report that merges them is not
#: reporting.
DEFAULT_MAX_TURNS: int = 12

#: Recorded in every trace. Free-form by schema, but stable here so a report can
#: separate roleplay rows from the booking case study without a lookup.
ADAPTER: str = "roleplay:text"

#: The product's entire tool surface. Closed, and `roleplay.corpus` validates
#: scenario tool expectations against it — a scenario that expects a tool which
#: does not exist is a constraint that can neither be satisfied nor violated, and
#: it reads as green.
TOOL_NAMES: frozenset[str] = frozenset(
    {
        "load_customer_profile",
        "record_disclosure",
        "flag_compliance_risk",
        "reveal_concern",
        "raise_objection",
        "resolve_objection",
        "score_session",
    }
)


@dataclass(frozen=True)
class LatencyModel:
    """Where a turn's time goes, in seconds.

    Three terms because the three have different shapes in a real system and one
    constant would hide that: the persona thinks once per turn, each tool round
    trip costs the same, and speaking scales with how much there is to say. The
    scoring pass gets its own term because it reads the whole transcript and is
    the slowest thing in the session — which is exactly the number a product
    team argues about, so it should be visible rather than folded into a total.
    """

    think_s: float = 0.28
    per_tool_s: float = 0.14
    per_char_s: float = 0.003
    scoring_s: float = 1.60

    def turn_seconds(self, *, text: str, tool_calls: int) -> float:
        return self.think_s + self.per_tool_s * max(0, tool_calls) + self.per_char_s * len(text or "")


DEFAULT_LATENCY = LatencyModel()


# --------------------------------------------------------------------------- #
# The two speaking seams
# --------------------------------------------------------------------------- #


@runtime_checkable
class Trainee(Protocol):
    """The adviser under test: whatever can produce the next thing they say.

    Two methods, because the first turn of a sales meeting is not a reply to
    anything and a caller-shaped `reply(...)` interface would have to invent a
    customer utterance to prompt it with. `None` from either means "I am done",
    and the reason should be readable afterwards from `stop_reason` — a session
    that ended because the adviser closed the sale and one that ended because the
    turn budget ran out are different results.
    """

    def open(self) -> str | None:
        """The adviser's first turn, or None to decline the session."""
        ...

    def reply(self, customer_turn: str) -> str | None:
        """The next turn given what the customer just said, or None to stop."""
        ...


@runtime_checkable
class CustomerVoice(Protocol):
    """How the customer's already-decided move is put into words.

    Deliberately not "how the customer behaves". The move is decided by
    `CustomerPersona.respond` before this is called, and a voice that ignored the
    move it was handed would be a second, undeclared state machine — the exact
    parallel-machinery mistake this domain is built to avoid.
    """

    def speak(
        self,
        *,
        move: PersonaTurn,
        persona: CustomerPersona,
        trainee_turn: str,
        turn: int,
    ) -> str:
        """The customer's words for `move`. Must convey the move and nothing more."""
        ...


def stop_reason_of(speaker: object, default: str) -> str:
    """Why a speaker stopped, or `default` if it does not say.

    A helper rather than a required protocol member: a hand-written test double
    should be three lines, and demanding a `stop_reason` property from it would
    make the protocol harder to satisfy than the thing it abstracts.
    """
    reason = getattr(speaker, "stop_reason", None)
    return str(reason) if reason else default


def _take_audio_note(speaker: object) -> Any | None:
    """The speaker's audio note for the turn just taken, or None.

    The seam that lets a spoken session tell the trace the truth about its own
    channel. A speaker whose words crossed a real TTS -> STT round trip exposes
    `take_audio_note()` (see `roleplay.spoken`), and the note it returns emits the
    turn's events itself — what was *sent* to the synthesiser, what the recogniser
    *heard*, at what confidence, through which engines. A speaker without the
    method — every scripted and text-live speaker in this repo — takes the
    unchanged path below, so every committed fixture reproduces byte for byte.

    Duck-typed rather than imported, on purpose: the dependency must keep running
    `spoken -> runtime` and never back, for the same reason `roleplay.live` is
    never imported here. The note is trusted to emit the right kinds because the
    contract tests in `tests/test_roleplay_spoken.py` hold it to the same shape
    this loop writes.
    """
    take = getattr(speaker, "take_audio_note", None)
    return take() if callable(take) else None


@dataclass
class ScriptedTrainee:
    """A committed list of turns, delivered in order. The default, and unchanged.

    This is what every offline test and every corpus row uses, and it is why the
    live path is opt-in rather than default: a fixture whose turns come from a
    model is a recording, and a recording is a weaker ground truth than a script a
    human wrote and reviewed. Both belong in the repo; only one belongs in the
    denominator of a claim about the scorer.
    """

    turns: tuple[str, ...]
    spoken: int = 0

    def __post_init__(self) -> None:
        self.turns = tuple(self.turns)

    @property
    def planned_turns(self) -> int:
        return len(self.turns)

    @property
    def stop_reason(self) -> str:
        return "script_exhausted" if self.spoken >= len(self.turns) else "speaking"

    def open(self) -> str | None:
        return self._next()

    def reply(self, customer_turn: str) -> str | None:
        return self._next()

    def _next(self) -> str | None:
        if self.spoken >= len(self.turns):
            return None
        turn = self.turns[self.spoken]
        self.spoken += 1
        return turn

    def __repr__(self) -> str:
        return f"ScriptedTrainee({self.spoken}/{len(self.turns)} turns spoken)"


@dataclass
class ScriptedVoice:
    """The customer speaks the phrasing written in its profile. The default.

    A pure function of the move, so a session driven by a `ScriptedTrainee` and
    this voice is byte-reproducible — which is what `roleplay.consistency` needs,
    and why this stayed the default when the live voice arrived.
    """

    def speak(
        self,
        *,
        move: PersonaTurn,
        persona: CustomerPersona,
        trainee_turn: str,
        turn: int,
    ) -> str:
        return move.text

    def __repr__(self) -> str:
        return "ScriptedVoice()"


@dataclass(frozen=True)
class RoleplayConversation:
    """Stage one only: the talking, with no grade attached.

    Exists because the labelled set in `roleplay.calibration` must hold traces the
    scorer has *not* already written its answer into. It happens to be true that
    `roleplay.scorer.session_view` ignores the score events, so a full trace would
    also work — but a calibration set whose correctness depends on a reader
    verifying that claim is a calibration set nobody will trust. Cheaper to hand
    the instrument an input that cannot contain the answer.
    """

    trace: Trace
    register: DisclosureRegister
    persona: CustomerPersona
    #: Why the talking stopped: `script_exhausted`, `session_closed`,
    #: `turn_budget`, `no_reply`. Carried because a session that ended on the turn
    #: cap is an instrument artefact and a session the adviser closed is a result.
    stop_reason: str = "script_exhausted"

    @property
    def scenario_id(self) -> str:
        return self.trace.scenario_id

    @property
    def trainee_utterances(self) -> tuple[str, ...]:
        """What the trainee said, read back out of the trace.

        Recomputed from the trace rather than stored, so it is the same tuple any
        later reader of the file gets. A convenience that returned a private copy
        could disagree with the artifact, and then two honest people would be
        looking at different transcripts.
        """
        from roleplay.scorer import session_view

        return session_view(self.trace).trainee_turns


@dataclass(frozen=True)
class RoleplayResult:
    """One session: the trace, and the score card that trace already contains.

    The card is returned as well as written into the trace so a caller can read a
    number without re-parsing, but it is never the *only* copy. Anything a report
    says about a score must be recoverable from `result.trace` alone.
    """

    trace: Trace
    card: ScoreCard
    register: DisclosureRegister
    persona: CustomerPersona
    stop_reason: str = "script_exhausted"

    @property
    def scenario_id(self) -> str:
        return self.trace.scenario_id

    @property
    def trainee_utterances(self) -> tuple[str, ...]:
        """The trainee's turns, read back out of the trace. See the conversation type."""
        from roleplay.scorer import session_view

        return session_view(self.trace).trainee_turns

    def keyword_shadow(self) -> Any:
        """Register versus a naive keyword check, over this session's own turns.

        Lives here because the comparison needs exactly two things — the ledger
        and the trainee's utterances — and this object is the only place both are
        already in hand. See `roleplay.register.compare_with_keyword_check` for
        what the control arm is for.
        """
        from roleplay.register import compare_with_keyword_check

        return compare_with_keyword_check(self.register, self.trainee_utterances)


@dataclass(frozen=True)
class _StageOne:
    """What stage one produced, so stage two can finish the session.

    A named object rather than a tuple: the previous three-tuple grew two fields
    the moment a trainee could stop on its own, and a five-tuple is where an
    argument gets transposed silently.
    """

    builder: TraceBuilder
    register: DisclosureRegister
    persona: CustomerPersona
    turns: int
    stop_reason: str
    #: Gated concern topics the customer's voice mentioned before the state
    #: machine released them. `None` for a scripted voice, which cannot leak — and
    #: `None` rather than `0` so the key stays out of a scripted trace entirely
    #: and every committed offline fixture reproduces byte for byte.
    customer_topic_leaks: int | None = None

    def end_payload(self) -> dict[str, Any]:
        """Extra `session_end` keys. Empty for a fully scripted session."""
        payload: dict[str, Any] = {"stop_reason": self.stop_reason}
        if self.customer_topic_leaks is not None:
            payload["customer_topic_leaks"] = self.customer_topic_leaks
        return payload


@dataclass
class RoleplayCoach:
    """The deployed product: one persona engine, one scoring service.

    Constructed once and used for many sessions, which is how a service runs and
    which is what makes the scorer's cross-session state reachable. A harness
    that builds a fresh `RoleplayCoach` per repeat is testing a cold process, not
    a deployment — see `roleplay.consistency` for why that distinction decides
    whether one of the seeded defects is visible at all.
    """

    scorer: RubricScorer
    latency: LatencyModel = DEFAULT_LATENCY

    def converse(
        self,
        *,
        scenario_id: str,
        trainee_turns: Sequence[str] | None = None,
        profile: CustomerProfile,
        clock: Clock | None = None,
        session_id: str | None = None,
        jurisdiction: str | None = None,
        language: str | None = None,
        trainee: Trainee | None = None,
        customer_voice: CustomerVoice | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        adapter: str | None = None,
    ) -> RoleplayConversation:
        """Run stage one only: the roleplay, with no scoring pass.

        The scorer is never consulted, so this method is independent of the
        scoring service's state — two calls with the same arguments produce
        byte-identical traces however many sessions the service has graded.

        `adapter` overrides the trace's adapter label. It exists for
        `roleplay.spoken`, whose sessions run the same loop through a real audio
        channel: a spoken trace stamped `roleplay:text` would misfile itself in
        every report that slices by adapter. Left unset, nothing changes.
        """
        stage = self._stage_one(
            scenario_id=scenario_id,
            trainee_turns=trainee_turns,
            profile=profile,
            clock=clock,
            session_id=session_id,
            jurisdiction=jurisdiction,
            language=language,
            trainee=trainee,
            customer_voice=customer_voice,
            max_turns=max_turns,
            adapter=adapter,
        )
        stage.builder.session_end(
            reason="roleplay_ended", turns=stage.turns, **stage.end_payload()
        )
        return RoleplayConversation(
            trace=stage.builder.build(),
            register=stage.register,
            persona=stage.persona,
            stop_reason=stage.stop_reason,
        )

    def run(
        self,
        *,
        scenario_id: str,
        trainee_turns: Sequence[str] | None = None,
        profile: CustomerProfile,
        clock: Clock | None = None,
        session_id: str | None = None,
        jurisdiction: str | None = None,
        language: str | None = None,
        trainee: Trainee | None = None,
        customer_voice: CustomerVoice | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> RoleplayResult:
        """Run one session end to end and return its trace and score card."""
        stage = self._stage_one(
            scenario_id=scenario_id,
            trainee_turns=trainee_turns,
            profile=profile,
            clock=clock,
            session_id=session_id,
            jurisdiction=jurisdiction,
            language=language,
            trainee=trainee,
            customer_voice=customer_voice,
            max_turns=max_turns,
        )
        builder, register, persona = stage.builder, stage.register, stage.persona
        effective_clock = builder.clock

        # ------------------------------------------------------ stage 2: score
        builder.agent_handoff(
            CUSTOMER_AGENT,
            SCORER_AGENT,
            reason="roleplay ended; scoring pass begins",
        )
        card = self.scorer.score_trace(builder.build())
        effective_clock.sleep(self.latency.scoring_s)
        self._emit_tool(
            builder,
            "score_session",
            card.tool_args(),
            result={"verdict": card.verdict, "total": card.total},
        )
        builder.agent_audio_first_byte(turn=stage.turns + 1)
        builder.agent_utterance(
            card.feedback, agent=SCORER_AGENT, turn=stage.turns + 1
        )
        builder.session_end(reason="scored", turns=stage.turns, **stage.end_payload())

        return RoleplayResult(
            trace=builder.build(),
            card=card,
            register=register,
            persona=persona,
            stop_reason=stage.stop_reason,
        )

    # ---------------------------------------------------------------- stage 1

    def _stage_one(
        self,
        *,
        scenario_id: str,
        trainee_turns: Sequence[str] | None,
        profile: CustomerProfile,
        clock: Clock | None,
        session_id: str | None,
        jurisdiction: str | None,
        language: str | None,
        trainee: Trainee | None = None,
        customer_voice: CustomerVoice | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        adapter: str | None = None,
    ) -> "_StageOne":
        """The roleplay itself. Returns the live builder so stage two can append."""
        if trainee is not None and trainee_turns is not None:
            raise ValueError(
                f"{scenario_id}: pass either trainee_turns or trainee, not both; "
                "a scripted list and a speaking trainee would have to disagree about "
                "who says turn one"
            )
        if trainee is None:
            if not trainee_turns:
                raise ValueError(
                    f"{scenario_id}: a roleplay with no trainee turns scores an empty "
                    "transcript, which every criterion reads as absence rather than as "
                    "no data. Declare at least one turn."
                )
            trainee = ScriptedTrainee(tuple(trainee_turns))
        if max_turns < 1:
            raise ValueError(f"{scenario_id}: max_turns must be at least 1")
        voice: CustomerVoice = customer_voice if customer_voice is not None else ScriptedVoice()
        scripted_voice = isinstance(voice, ScriptedVoice)
        in_engine = "stt:scripted" if isinstance(trainee, ScriptedTrainee) else "text:live"
        out_engine = "tts:scripted" if scripted_voice else "text:live"

        effective_clock: Clock = clock if clock is not None else FakeClock()
        builder = TraceBuilder(
            scenario_id=scenario_id,
            adapter=adapter or ADAPTER,
            session_id=session_id,
            clock=effective_clock,
        )
        persona = CustomerPersona(profile=profile)
        register = DisclosureRegister(
            jurisdiction=jurisdiction or profile.jurisdiction,
            language=language or profile.language,
        )

        builder.session_start(
            profile=profile.key,
            jurisdiction=register.jurisdiction,
            language=register.language,
            trainee_turns=int(getattr(trainee, "planned_turns", max_turns)),
            scorer_cohort_size=len(self.scorer.history),
            scorer_adjustment=self.scorer.adjustment,
            # Provenance for the two speaking seams. A trace that does not say
            # whether its transcript was written by a person or generated by a
            # model cannot be used as evidence about either.
            trainee_source=repr(trainee),
            customer_voice=repr(voice),
            risk_appetite=profile.risk_appetite,
            customer_suspicion=profile.suspicion,
        )

        # ------------------------------------------------------- stage 1: talk
        #
        # Ask, run, ask again. The trainee decides when it has finished and
        # `max_turns` decides when the harness has; the two endings are recorded
        # under different `stop_reason`s because only one of them is a result.
        index = 0
        stop_reason = "no_reply"
        utterance = trainee.open()
        while utterance is not None:
            if index >= max_turns:
                stop_reason = "turn_budget"
                break
            index += 1
            audio_note = _take_audio_note(trainee)
            if audio_note is None:
                builder.transcript_in(utterance, confidence=1.0, engine=in_engine)
                builder.caller_utterance(utterance, turn=index)
            else:
                # The trainee's words crossed a real audio channel. The note
                # emits the honest version of the two events above — the heard
                # text with its real engine and confidence, and the sent text
                # beside it — plus the audio evidence. `utterance` from here on
                # is what the channel delivered, which is the point: recognition
                # errors flow into the register, the persona and the scorer
                # exactly as they would in production.
                audio_note.emit_caller(builder, turn=index, heard_text=utterance)

            tool_calls = 0
            if index == 1:
                self._emit_tool(
                    builder,
                    "load_customer_profile",
                    {
                        "profile": profile.key,
                        "jurisdiction": register.jurisdiction,
                        "language": register.language,
                    },
                    result={"display_name": profile.display_name},
                )
                tool_calls += 1

            # The disclosure register, before the persona speaks: a requirement is
            # discharged by the trainee's words, not by anything the customer says
            # back, and recording it first keeps that causality readable in the
            # event order.
            for record in register.observe(utterance, turn=index):
                self._emit_tool(
                    builder,
                    "record_disclosure",
                    {
                        "code": record.code,
                        "jurisdiction": record.jurisdiction,
                        "language": record.language,
                        "turn": record.turn,
                        "phrasing": record.phrasing,
                    },
                    result={"satisfied": list(register.satisfied_codes())},
                )
                tool_calls += 1

            # The in-session compliance flagger. This is real product output, and
            # the scorer ignoring it is DEFECT-3's second half.
            if classify_trainee_turn(utterance) == "advice":
                self._emit_tool(
                    builder,
                    "flag_compliance_risk",
                    {
                        "kind": "personal_recommendation",
                        "turn": index,
                        "utterance": utterance,
                    },
                    result={"severity": "high"},
                )
                tool_calls += 1

            # The state machine decides the move; the voice only says it. Both
            # orders were possible and this one is the only defensible one: a
            # voice that could change the move would be a second customer.
            move = persona.respond(utterance)
            spoken = voice.speak(
                move=move, persona=persona, trainee_turn=utterance, turn=index
            )
            reply = move if spoken == move.text else replace(move, text=spoken)
            for concern in reply.revealed:
                self._emit_tool(
                    builder,
                    "reveal_concern",
                    {"key": concern.key, "topic": concern.topic, "turn": index},
                )
                tool_calls += 1
            for objection in reply.raised:
                self._emit_tool(
                    builder,
                    "raise_objection",
                    {"key": objection.key, "topic": objection.topic, "turn": index},
                )
                tool_calls += 1
            for objection in reply.handled:
                self._emit_tool(
                    builder,
                    "resolve_objection",
                    {"key": objection.key, "topic": objection.topic, "turn": index},
                )
                tool_calls += 1

            effective_clock.sleep(
                self.latency.turn_seconds(text=reply.text, tool_calls=tool_calls)
            )
            audio_note = _take_audio_note(voice)
            if audio_note is None:
                builder.agent_audio_first_byte(turn=index)
                builder.agent_utterance(reply.text, agent=CUSTOMER_AGENT, turn=index)
                builder.transcript_out(reply.text, engine=out_engine)
                builder.agent_audio_complete(turn=index, num_bytes=len(reply.text) * 320)
            else:
                # The customer's words crossed the audio channel too. See the
                # trainee side above; the note writes the same four kinds with
                # the sent/heard split named, so a reader diffing the two texts
                # is reading the channel's error, not a formatting choice.
                audio_note.emit_agent(
                    builder, turn=index, agent=CUSTOMER_AGENT, heard_text=reply.text
                )

            utterance = trainee.reply(reply.text)
            if utterance is None:
                stop_reason = stop_reason_of(trainee, "no_reply")

        if index == 0:
            # A live trainee that declined to speak at all. Same failure as an
            # empty script, and it must raise for the same reason: an empty
            # transcript scores as absence on every criterion, so a session that
            # never happened would be certified as a session that went badly.
            raise ValueError(
                f"{scenario_id}: the trainee produced no turns, so there is no "
                "transcript to score. An empty session reads as absence on every "
                f"criterion rather than as no data (stop reason: "
                f"{stop_reason_of(trainee, stop_reason)})."
            )

        leaks = int(getattr(voice, "leaks", 0) or 0)
        return _StageOne(
            builder=builder,
            register=register,
            persona=persona,
            turns=index,
            stop_reason=stop_reason,
            customer_topic_leaks=None if scripted_voice else leaks,
        )

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _emit_tool(
        builder: TraceBuilder,
        name: str,
        args: dict[str, Any],
        *,
        result: Any = None,
    ) -> None:
        """Emit the call/result pair for one tool, correlated by `call_id`.

        Always a pair. A call with no result is indistinguishable from a call that
        hung, and a suite that cannot tell those apart will eventually be asked to.

        The `call_id` is derived from the event position rather than from a uuid.
        `TraceBuilder` would generate a random one, which is correct for a live
        adapter and wrong here: two runs of one script must produce byte-identical
        traces, or a fixture cannot be diffed and "the same session scored
        differently" cannot be told from "a different session was scored".
        Position is unique because every call appends at least one event.
        """
        call_id = f"c{len(builder.events):03d}"
        builder.tool_call(name, args, call_id=call_id)
        builder.tool_result(name, result, call_id=call_id, ok=True)

    def __repr__(self) -> str:
        return f"RoleplayCoach(scorer={self.scorer!r})"


def run_roleplay(
    *,
    scenario_id: str,
    trainee_turns: Sequence[str],
    profile: CustomerProfile,
    coach: RoleplayCoach | None = None,
    scorer: RubricScorer | None = None,
    clock: Clock | None = None,
    session_id: str | None = None,
    jurisdiction: str | None = None,
    language: str | None = None,
) -> RoleplayResult:
    """Run one session. Convenience wrapper around `RoleplayCoach.run`.

    Pass `coach` to reuse a warm deployment across sessions — that is the shape
    the score-consistency measurement needs. Pass neither and a cold scorer is
    built for this session alone, which is the shape that hides DEFECT-1.
    """
    if coach is not None and scorer is not None:
        raise ValueError("pass either coach or scorer, not both; they name the same thing")
    effective = coach if coach is not None else RoleplayCoach(scorer=scorer or RubricScorer())
    return effective.run(
        scenario_id=scenario_id,
        trainee_turns=trainee_turns,
        profile=profile,
        clock=clock,
        session_id=session_id,
        jurisdiction=jurisdiction,
        language=language,
    )


#: Where the roleplay corpus lives. Declared here rather than in `roleplay.corpus`
#: so that the runtime, the loader and the demo agree on one path.
CORPUS_ROOT: Path = Path(__file__).resolve().parent.parent / "scenarios" / "roleplay"
