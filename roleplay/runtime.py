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
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from lab.clock import Clock, FakeClock
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace

from roleplay.persona import CustomerPersona, CustomerProfile, classify_trainee_turn
from roleplay.register import DisclosureRegister
from roleplay.scorer import CUSTOMER_AGENT, SCORER_AGENT, RubricScorer, ScoreCard

__all__ = [
    "ADAPTER",
    "TOOL_NAMES",
    "LatencyModel",
    "DEFAULT_LATENCY",
    "RoleplayResult",
    "RoleplayCoach",
    "run_roleplay",
]

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

    @property
    def scenario_id(self) -> str:
        return self.trace.scenario_id


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

    @property
    def scenario_id(self) -> str:
        return self.trace.scenario_id


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
        trainee_turns: Sequence[str],
        profile: CustomerProfile,
        clock: Clock | None = None,
        session_id: str | None = None,
        jurisdiction: str | None = None,
        language: str | None = None,
    ) -> RoleplayConversation:
        """Run stage one only: the roleplay, with no scoring pass.

        The scorer is never consulted, so this method is independent of the
        scoring service's state — two calls with the same arguments produce
        byte-identical traces however many sessions the service has graded.
        """
        builder, register, persona = self._stage_one(
            scenario_id=scenario_id,
            trainee_turns=trainee_turns,
            profile=profile,
            clock=clock,
            session_id=session_id,
            jurisdiction=jurisdiction,
            language=language,
        )
        builder.session_end(reason="roleplay_ended", turns=len(trainee_turns))
        return RoleplayConversation(
            trace=builder.build(), register=register, persona=persona
        )

    def run(
        self,
        *,
        scenario_id: str,
        trainee_turns: Sequence[str],
        profile: CustomerProfile,
        clock: Clock | None = None,
        session_id: str | None = None,
        jurisdiction: str | None = None,
        language: str | None = None,
    ) -> RoleplayResult:
        """Run one session end to end and return its trace and score card."""
        builder, register, persona = self._stage_one(
            scenario_id=scenario_id,
            trainee_turns=trainee_turns,
            profile=profile,
            clock=clock,
            session_id=session_id,
            jurisdiction=jurisdiction,
            language=language,
        )
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
        builder.agent_audio_first_byte(turn=len(trainee_turns) + 1)
        builder.agent_utterance(
            card.feedback, agent=SCORER_AGENT, turn=len(trainee_turns) + 1
        )
        builder.session_end(reason="scored", turns=len(trainee_turns))

        return RoleplayResult(
            trace=builder.build(), card=card, register=register, persona=persona
        )

    # ---------------------------------------------------------------- stage 1

    def _stage_one(
        self,
        *,
        scenario_id: str,
        trainee_turns: Sequence[str],
        profile: CustomerProfile,
        clock: Clock | None,
        session_id: str | None,
        jurisdiction: str | None,
        language: str | None,
    ) -> tuple[TraceBuilder, DisclosureRegister, CustomerPersona]:
        """The roleplay itself. Returns the live builder so stage two can append."""
        if not trainee_turns:
            raise ValueError(
                f"{scenario_id}: a roleplay with no trainee turns scores an empty "
                "transcript, which every criterion reads as absence rather than as "
                "no data. Declare at least one turn."
            )

        effective_clock: Clock = clock if clock is not None else FakeClock()
        builder = TraceBuilder(
            scenario_id=scenario_id,
            adapter=ADAPTER,
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
            trainee_turns=len(trainee_turns),
            scorer_cohort_size=len(self.scorer.history),
            scorer_adjustment=self.scorer.adjustment,
        )

        # ------------------------------------------------------- stage 1: talk
        for index, utterance in enumerate(trainee_turns, start=1):
            builder.transcript_in(utterance, confidence=1.0, engine="stt:scripted")
            builder.caller_utterance(utterance, turn=index)

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

            reply = persona.respond(utterance)
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
            builder.agent_audio_first_byte(turn=index)
            builder.agent_utterance(reply.text, agent=CUSTOMER_AGENT, turn=index)
            builder.transcript_out(reply.text, engine="tts:scripted")
            builder.agent_audio_complete(turn=index, num_bytes=len(reply.text) * 320)

        return builder, register, persona

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
