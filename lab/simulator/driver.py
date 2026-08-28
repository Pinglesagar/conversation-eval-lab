"""The scenario driver: run a caller against an agent, emit a trace.

WHAT THIS DEMONSTRATES
----------------------
Three things, in order of how much they matter.

**1. The measurement boundary is honoured here too.**
`lab.voice.calibration` proves the harness can recover a known delay; that proof
is only worth anything if ordinary runs use the same discipline. So the loop in
`run_scenario` captures `t0` and `t1` as bare floats either side of the single
call into the system under test, and builds every event afterwards, back-dated
with `ts=`. Nothing else sits between the two clock reads — not coercion, not
logging, not the caller's next line. The calibration gate and the real driver are
the same shape on purpose: a gate that validates a code path nobody runs is
theatre.

**2. The agent under test is a callable, not a subclass.**
`AgentUnderTest` is a `Protocol`: anything that takes an utterance and returns a
reply qualifies. No base class to inherit, no registry to join, no import of the
harness inside the system being measured. That is what makes `lab` reusable —
the harness depends on a call signature, and a call signature is something a
wrapper can produce for a framework `lab` has never heard of.

**3. Two callers, one interface.**
`ScriptedCaller` is deterministic and drives every offline test. `LLMCaller`
generates its turns from a model, records them to a cassette on first run, and
replays from that cassette forever after — so a live-generated conversation
becomes a fixture that reproduces with no API key. Both satisfy `Caller`, so
switching between them changes nothing about the driver, the trace, or the
checks. The recorded path additionally verifies the *context* it is replaying
into (a hash of the conversation so far), which means a stale cassette raises
instead of quietly answering a question it was never asked.

TOOL-EVENT TIMESTAMPS ARE ESTIMATED, AND SAY SO
-----------------------------------------------
An `AgentUnderTest` reports its tool calls when it returns, so the harness knows
they happened between `t0` and `t1` but not when. Rather than pretend, the driver
spaces them evenly across the window and stamps every such event with
`ts_estimated: true` in its payload. Two consequences, both deliberate:

*   Ordering is faithful (tools precede the response), so any check that reads
    sequence is correct.
*   No timing figure in this repo may be derived from an event carrying
    `ts_estimated`. Latency comes from `caller_utterance -> agent_audio_first_byte`,
    both of which are real captured instants. A streaming adapter that observes
    each tool call as it happens should pass real timestamps via
    `ToolInvocation.ts`, and then the flag is absent.

Marking the estimate in the data, instead of in a comment, is the difference
between a documented approximation and a lie with a footnote.

WHAT THIS DOES NOT DO
---------------------
No barge-in. Interrupting the agent mid-utterance needs duplex audio and the v1
adapters are turn-based, so this driver emits neither `interruption_started` nor
`interruption_acknowledged`. The constructed measurement — those two kinds
written from timings a scenario hands in, and scored — lives in
`lab.voice.interaction`; discovering an interruption is what nothing here does,
and this driver does not claim to.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from lab.clock import Clock
from lab.simulator.persona import END_OF_CALL_RE, CallerProfile, Verbosity
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace

__all__ = [
    "ToolInvocation",
    "Handoff",
    "AgentTurn",
    "AgentReply",
    "AgentUnderTest",
    "Caller",
    "ScriptedCaller",
    "LLMCaller",
    "CassetteKey",
    "DisclosureLeak",
    "DisclosureLeakError",
    "OnLeak",
    "coerce_turn",
    "run_scenario",
    "DEFAULT_MAX_TURNS",
    "LIVE_CALLER_ENV_VAR",
    "CALLER_MODEL_ENV_VAR",
    "VERBOSITY_TOKEN_BUDGET",
    "REPEAT_LIMIT",
    "RATE_LIMIT_RETRIES",
    "RATE_LIMIT_BASE_DELAY_S",
    "STT_ENGINE",
    "TTS_ENGINE",
]

#: Hard stop on conversation length. A loop between a stubborn caller and a
#: stubborn agent is a real failure mode (and a real cloud bill), so the driver
#: refuses to run forever and records `reason="max_turns"` in `session_end`
#: rather than raising — a truncated conversation is evidence, not an error.
DEFAULT_MAX_TURNS: int = 12

#: Set this to a truthy value to allow `LLMCaller` to reach a live provider.
#: Absent, the caller replays from its cassette and raises on a miss. Opt-in, so
#: a clean clone cannot spend money by accident.
LIVE_CALLER_ENV_VAR: str = "LAB_LIVE_CALLER"

#: Where `LLMCaller` reads its litellm model route from when none is passed. No
#: model id is hardcoded anywhere in `lab` — providers, prices and ids move, and a
#: harness that pins one has an expiry date. Same convention as
#: `lab.judges.judge.MODEL_ENV_VAR`.
CALLER_MODEL_ENV_VAR: str = "LAB_CALLER_MODEL"

#: Output token budget per persona verbosity. `verbosity` is a prompt instruction
#: and prompt instructions are advisory; this is the same instruction expressed as
#: something the API enforces. A terse caller that writes a paragraph is not a
#: terse caller, and the resulting trace measures the agent's handling of a
#: persona that was never in the corpus.
VERBOSITY_TOKEN_BUDGET: dict[Verbosity, int] = {
    "terse": 48,
    "normal": 110,
    "chatty": 220,
}

#: How many times one caller line may be said before it counts as a loop. Two,
#: because a reluctant persona's stall recurs legitimately across a long call and
#: a limit of one would end the very scenarios that test re-asking.
REPEAT_LIMIT: int = 2

#: Rate-limit retries and the first backoff delay, doubling from there. A 429 is
#: an instruction to wait, not a result: retrying it is correct, and *not*
#: retrying it turns a provider's queue depth into a flaky agent.
RATE_LIMIT_RETRIES: int = 5
RATE_LIMIT_BASE_DELAY_S: float = 2.0

#: What to do when the caller volunteers a fact the scenario gated.
OnLeak = Literal["record", "raise"]


class DisclosureLeakError(RuntimeError):
    """The caller said a gated fact before it was asked for, under `on_leak="raise"`."""


_WHITESPACE_RE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """Case-folded, whitespace-collapsed, punctuation-trimmed — for comparison only.

    Used by the repetition guard and the leak audit. Deliberately crude and
    deliberately not a model call: a guard whose own verdict varied between runs
    would report the caller's noise as the agent's.
    """
    return _WHITESPACE_RE.sub(" ", text.strip().casefold()).strip(" .!?,;:")


def _mentions(utterance: str, *values: str) -> bool:
    """Does `utterance` say any of `values` verbatim, on word boundaries?

    Word boundaries matter for the short values that make up most of a booking: a
    plain substring test finds the party size "2" inside "20:00" and reports a
    leak that did not happen, and a leak detector with false positives gets turned
    off, which is worse than one that undercounts.
    """
    haystack = _normalise(utterance)
    for value in values:
        needle = _normalise(value)
        if not needle:
            continue
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
            return True
    return False


def _split_sentinel(text: str) -> tuple[str, bool]:
    """Separate what the caller *said* from its decision to hang up.

    `CALLER_RULES` asks the model to send the sentinel and nothing else. Two runs
    in forty ignored that and appended it to the turn carrying their last answer:

        "Yes, please. My name is Ruth Kelleher. No allergies. [END OF CALL]"

    Treating that as "the call is over" ends the conversation on the caller's own
    turn — so the agent never gets a turn in which to act on the name, no
    `create_booking` is ever made, and the scenario fails. The failure is real and
    it is entirely the instrument's: the caller had said everything the agent
    needed, and the harness hung up on it.

    So the sentinel is *stripped* and the words are delivered normally, with the
    hang-up deferred to the next turn. The caller says its piece, the agent gets
    its turn, and only then does the line go dead — which is also what a person
    does: they answer the last question and wait for the confirmation before
    putting the phone down. Nothing about the agent's behaviour is hidden by
    this. If the agent still fails to book, the contract still fails; what goes
    away is a verdict decided by where the model put a marker.

    Returns `(what to say, whether this was the last thing)`. An empty remainder
    means the model followed the rule and the call ends immediately.
    """
    if not END_OF_CALL_RE.search(text):
        return text, False
    return END_OF_CALL_RE.sub("", text).strip(), True


def _is_rate_limited(exc: BaseException) -> bool:
    """Is this exception a provider telling us to slow down?

    Duck-typed across SDKs on purpose: `litellm` normalises most providers to a
    `RateLimitError`, but not all, and an `isinstance` check against a class
    imported at module scope would drag the provider SDK into offline collection
    for no benefit.
    """
    if getattr(exc, "status_code", None) == 429:
        return True
    name = type(exc).__name__.lower()
    return "ratelimit" in name or "429" in str(exc)

#: Engine tags for the pseudo-STT/TTS legs of a text run. Named so that a text
#: trace and a voice trace carry the same shape of attribution and one latency
#: function serves both.
STT_ENGINE: str = "harness:passthrough-stt"
TTS_ENGINE: str = "harness:passthrough-tts"

_ESTIMATED = {"ts_estimated": True}


# --------------------------------------------------------------------------- #
# What an agent hands back
# --------------------------------------------------------------------------- #


class ToolInvocation(BaseModel):
    """One tool call the agent made during a turn, and what came back.

    Call and result are one object here because a callable agent reports them
    together; the driver still emits them as the two separate trace events the
    schema defines, so a check can distinguish "never called" from "called and
    failed" exactly as it would on a streaming adapter.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    ok: bool = True
    error: str | None = None
    call_id: str | None = None
    ts: float | None = Field(
        default=None,
        description=(
            "Real observed timestamp, session-relative seconds, when the adapter "
            "saw the call happen. Leave None and the driver interpolates and "
            "flags the event as estimated."
        ),
    )


class Handoff(BaseModel):
    """Control passing from one sub-agent to another during a turn."""

    model_config = ConfigDict(extra="forbid")

    from_agent: str = Field(min_length=1)
    to_agent: str = Field(min_length=1)
    reason: str | None = None
    ts: float | None = None


class AgentTurn(BaseModel):
    """Everything the system under test did in response to one caller utterance."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="What the agent said. May be empty for a silent turn.")
    agent: str | None = Field(
        default=None, description="Which sub-agent spoke, for multi-agent systems."
    )
    tools: list[ToolInvocation] = Field(default_factory=list)
    handoff: Handoff | None = None
    end_call: bool = Field(
        default=False, description="True if the agent considers the conversation over."
    )


#: What an `AgentUnderTest` may return. `str` and `(str, tools)` are accepted so
#: that wrapping a third-party agent is a one-line lambda; `coerce_turn`
#: normalises. Convenience at the boundary, one type inside.
AgentReply = AgentTurn | str | tuple[str, Sequence[ToolInvocation | dict[str, Any]]]


@runtime_checkable
class AgentUnderTest(Protocol):
    """Anything that answers an utterance. The entire contract on the SUT.

    Deliberately narrow. A system under test should not have to know it is being
    evaluated, and anything wider (session objects, lifecycle hooks, a base
    class) would make `lab` a framework the SUT has to adopt instead of an
    instrument pointed at it.

    Statefulness is the implementer's business: a multi-turn agent keeps its own
    conversation state across calls. `lab.simulator.passk` therefore takes a
    *factory*, so each repeat of a scenario starts from a clean agent.
    """

    def __call__(self, utterance: str) -> AgentReply: ...


def coerce_turn(reply: AgentReply) -> AgentTurn:
    """Normalise whatever the agent returned into an `AgentTurn`.

    Called strictly *after* the measurement boundary closes — validation of the
    reply is harness compute and must not land inside the measured window.
    """
    if isinstance(reply, AgentTurn):
        return reply
    if isinstance(reply, str):
        return AgentTurn(text=reply)
    if isinstance(reply, tuple) and len(reply) == 2:
        text, tools = reply
        return AgentTurn(
            text=str(text),
            tools=[
                t if isinstance(t, ToolInvocation) else ToolInvocation.model_validate(t)
                for t in tools
            ],
        )
    raise TypeError(
        "an AgentUnderTest must return an AgentTurn, a str, or a (text, tools) "
        f"tuple; got {type(reply).__name__}"
    )


# --------------------------------------------------------------------------- #
# What a caller looks like
# --------------------------------------------------------------------------- #


@runtime_checkable
class Caller(Protocol):
    """A simulated caller: an opening line, then a reply to each agent turn.

    `reply` returning `None` means the caller hung up, which is how a scenario
    ends normally. Two implementations ship: `ScriptedCaller` (deterministic, used
    by every offline test) and `LLMCaller` (opt-in, records and replays).
    """

    def opening(self) -> str: ...

    def reply(self, agent_turn: AgentTurn) -> str | None: ...


class ScriptedCaller:
    """A deterministic caller: a fixed script, plus answers to direct questions.

    WHY A SCRIPT AT ALL
    -------------------
    A model-driven caller is the more realistic instrument and the worse test
    fixture: its variance shows up in the results as agent variance, and the
    pass^k machinery then reports the caller's flakiness as the agent's. So every
    offline test in this repo drives the agent with a script, and the LLM caller
    is reserved for exploration and for generating the fixtures the scripts are
    written against.

    HOW A TURN IS CHOSEN
    --------------------
    1.  If the agent's utterance asks for a fact the goal marks `on_request_only`,
        answer it — a real caller answers the question in front of them rather
        than reading from a list. A reluctant persona (`cooperativeness` below
        `RELUCTANT_BELOW`) stalls once before answering.
    2.  Otherwise say the next line of the script.
    3.  Script exhausted: `closing` if one was given and not yet used, else hang
        up by returning None.

    Rule 1 taking precedence is what makes an agent that re-asks a question
    observable rather than fatal: the caller answers again, the conversation
    continues, and the *trace* records that the same question was asked twice —
    which is where a check, not the caller, is entitled to have an opinion.

    Ask counts are kept per fact for the whole call, so the second ask of a
    reluctant persona can arrive many turns after the first.
    """

    def __init__(
        self,
        script: Sequence[str],
        *,
        profile: CallerProfile | None = None,
        closing: str | None = None,
        stall_line: str = "Sorry, what was that?",
    ) -> None:
        """
        Args:
            script: The caller's lines, in order. The first is the opening.
            profile: Persona and goal. Optional — a bare script is a legitimate
                minimal caller — but gated-fact answering needs it.
            closing: Said once after the script runs out; then the caller hangs
                up. Use it for a sign-off the agent's last turn should get.
            stall_line: What a reluctant persona says on the first ask.
        """
        if not script:
            raise ValueError("a ScriptedCaller needs at least one line to open with")
        self.script: list[str] = list(script)
        self.profile = profile
        self.closing = closing
        self.stall_line = stall_line
        self._index = 0
        self._closed = False
        self._ask_counts: dict[str, int] = {}
        self._released: list[str] = []

    # ---------------------------------------------------------------- reading

    @property
    def released_facts(self) -> list[str]:
        """Gated fact keys this caller has actually spoken, in release order.

        The ground truth for an information-loss check: a fact that was never
        released cannot have been dropped by the agent, and a check that ignores
        this distinction will blame the agent for the scenario's silence.
        """
        return list(self._released)

    @property
    def ask_counts(self) -> dict[str, int]:
        """How many times each gated fact was asked for across the call."""
        return dict(self._ask_counts)

    @property
    def lines_used(self) -> int:
        """How much of the script was consumed. Fewer than `len(script)` means the
        agent ended the call early — worth knowing when reading a short trace."""
        return self._index

    # ---------------------------------------------------------------- speaking

    def opening(self) -> str:
        self._index = 1
        return self.script[0]

    def reply(self, agent_turn: AgentTurn) -> str | None:
        answer = self._answer_to_question(agent_turn.text)
        if answer is not None:
            return answer
        if self._index < len(self.script):
            line = self.script[self._index]
            self._index += 1
            return line
        if self.closing is not None and not self._closed:
            self._closed = True
            return self.closing
        return None

    def _answer_to_question(self, agent_text: str) -> str | None:
        """Answer a direct question about a gated fact, or None if not asked."""
        if self.profile is None or not agent_text:
            return None
        goal = self.profile.goal
        asked = goal.asked_keys(agent_text, among=goal.gated_keys())
        if not asked:
            return None
        # One fact per turn, in declaration order: a caller who answers three
        # questions in one breath makes it impossible to attribute which ask
        # produced which release.
        key = asked[0]
        self._ask_counts[key] = self._ask_counts.get(key, 0) + 1
        if self._ask_counts[key] < self.profile.asks_required:
            return self.stall_line
        if key not in self._released:
            self._released.append(key)
        return goal.spoken(key)

    def __repr__(self) -> str:
        return (
            f"ScriptedCaller(lines={len(self.script)}, used={self._index}, "
            f"released={self._released})"
        )


class CassetteKey(BaseModel):
    """What a recorded caller conversation is *of*. The fixture's identity.

    WHY A KEY AND NOT JUST A PATH
    -----------------------------
    A cassette is a recording of one caller playing one scenario under one
    prompt. Every part of that sentence can change without the file's name
    changing, and each change makes the recording answer for a conversation that
    never happened:

    *   a different **scenario** — the caller is answering another call's
        questions;
    *   a different **persona** — the words are a different person's;
    *   a different **prompt** — the caller was told different rules, so its
        turns are not the turns the current instrument would produce;
    *   a different **model or temperature** — the variance being replayed is
        another distribution's.

    So the identity is declared, written into the file, and checked on load. Four
    of the five fields also go into the filename, which means a prompt edit does
    not corrupt the old fixture — it *misses* it, and a miss offline is a refusal
    (see `LLMCaller._next_utterance`) rather than a wrong answer. The old
    recording stays on disk, still valid for the prompt that produced it.

    `variant` is the field that makes a stability measurement possible. k repeats
    of one scenario at a non-zero temperature are k *different* conversations, and
    a single cassette would replay the first of them k times and report a flake
    rate of zero — the machinery quietly proving the thing it was built to test.
    One cassette per repeat, keyed by index.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1)
    persona: str = Field(min_length=1)
    prompt_sha256: str = Field(min_length=64, max_length=64)
    model: str = Field(
        min_length=1,
        description=(
            "The model *label* — what a reader of the fixture should be told the "
            "turns came from. Not necessarily the litellm route: see "
            "`LLMCaller.model_label`."
        ),
    )
    temperature: float = Field(ge=0.0, le=2.0)
    variant: int | None = Field(
        default=None,
        ge=0,
        description="Repeat index when k conversations share a scenario. None for a single take.",
    )
    turn_budget: int = Field(
        ge=1,
        description=(
            "The caller's `max_utterances` at record time. In the key because it "
            "decides where a conversation stops, and therefore what the recording "
            "contains: the same caller under a tighter budget produces a shorter "
            "call and, demonstrably, a different verdict."
        ),
    )

    @classmethod
    def build(
        cls,
        *,
        scenario_id: str,
        profile: CallerProfile,
        model: str,
        temperature: float,
        turn_budget: int,
        variant: int | None = None,
    ) -> "CassetteKey":
        """Derive the key from the objects that actually determine the recording."""
        return cls(
            scenario_id=scenario_id,
            persona=profile.persona.name,
            prompt_sha256=hashlib.sha256(
                profile.system_prompt().encode("utf-8")
            ).hexdigest(),
            model=model,
            temperature=temperature,
            turn_budget=turn_budget,
            variant=variant,
        )

    @property
    def prompt_digest12(self) -> str:
        """The first 12 hex digits of the prompt digest — what appears in a filename."""
        return self.prompt_sha256[:12]

    def filename(self) -> str:
        """`<persona>-<prompt12>[-r<variant>]-b<turn_budget>.json`.

        Every varying part of the identity except the model label is in the name,
        so two readings of the same scenario under different settings sit side by
        side instead of one silently overwriting the other.
        """
        stem = f"{self.persona}-{self.prompt_digest12}"
        if self.variant is not None:
            stem = f"{stem}-r{self.variant}"
        return f"{stem}-b{self.turn_budget}.json"

    def path_in(self, root: str | Path) -> Path:
        """`<root>/<scenario_id>/<filename>` — one directory per scenario.

        The scenario is a directory rather than a filename prefix so that a
        scenario's k repeats sit together and `git status` reads as one changed
        scenario instead of five unrelated files.
        """
        return Path(root) / self.scenario_id / self.filename()

    def differences(self, other: "CassetteKey") -> list[str]:
        """Field-by-field disagreement with another key, as readable lines."""
        out: list[str] = []
        for field_name in type(self).model_fields:
            mine = getattr(self, field_name)
            theirs = getattr(other, field_name)
            if mine != theirs:
                out.append(f"{field_name}: fixture has {theirs!r}, this run wants {mine!r}")
        return out

    def describe(self) -> str:
        variant = "" if self.variant is None else f", repeat {self.variant}"
        return (
            f"{self.scenario_id} as {self.persona} on {self.model} "
            f"(T={self.temperature}, prompt {self.prompt_digest12}{variant})"
        )


class DisclosureLeak(BaseModel):
    """A gated fact the caller said before anyone asked for it.

    The caller is part of the instrument, so this is a fault in the *measurement*,
    not a finding about the agent — and it is the fault that would most quietly
    ruin a result. `on_request_only` exists so that "did the agent ask?" is
    answerable; a caller that blurts the answer makes every check downstream pass
    for the wrong reason, and nothing in the trace looks unusual.

    A prompt can only *ask* the model not to leak. So the leak is also *measured*,
    per turn, and the count travels with the fixture. See
    `LLMCaller.leak_detection_note` for what the detector can and cannot see —
    the count is a floor, not a total.
    """

    model_config = ConfigDict(extra="forbid")

    turn_index: int = Field(ge=0, description="Which caller utterance leaked it.")
    fact: str = Field(min_length=1, description="The gated fact key. The value is not repeated here.")


class LLMCaller:
    """A model-driven caller that records to a cassette and replays from it.

    WHAT THIS DEMONSTRATES
    ----------------------
    The cardinal rule of this repo is that everything runs with zero API keys, and
    the honest way to keep that rule while still using a model is record/replay.
    First run (with `LAB_LIVE_CALLER` set) calls the provider and appends each
    generated turn to a JSON cassette. Every run afterwards reads the cassette and
    never touches the network, which is why a clean clone reproduces a
    model-generated conversation exactly — including the flake band in
    `lab.simulator.flake_band`, whose forty conversations are forty committed
    fixtures.

    THREE THINGS A PROMPT CANNOT GUARANTEE, SO CODE DOES
    ----------------------------------------------------
    The persona and goal ask the model to behave like a caller. Asking is the
    cheap half. An instrument needs the other half:

    1.  **It must not loop.** `CALLER_RULES` says so; `_stop_for_repetition`
        enforces it. A caller and an agent rephrasing the same exchange at each
        other exhausts `max_turns`, and a `max_turns` stop is indistinguishable in
        a report from an agent that could not finish the job. The guard tolerates
        one repeat (a reluctant persona says "sorry, what?" more than once in a
        long call) and stops on the second, or on any line said twice in a row.
    2.  **It must not leak a gated fact.** Audited per turn; see `DisclosureLeak`.
    3.  **It must not run away with the budget.** `max_utterances` is checked
        before a completion is requested, not after, because the point of the
        check is the money.

    Each of the three sets `stop_reason`, which is written into the cassette. A
    run that ended for an instrument reason and a run that ended because the
    caller was satisfied are different results, and a report that cannot tell them
    apart is not reporting.

    THE CASSETTE VERIFIES ITS CONTEXT AND ITS IDENTITY
    --------------------------------------------------
    Two independent staleness checks, because they catch different lies:

    *   **Identity** (`CassetteKey`): this file is a recording of this scenario,
        this persona, this prompt, this model, this repeat. Checked on load.
    *   **Context** (per turn, sha256 of the conversation so far): turn 3 replays
        only into the conversation turn 3 was recorded in. Positional replay would
        silently keep working after the agent's behaviour changed, and the caller
        would answer a question nobody asked while the suite stayed green.

    On either mismatch this raises, naming the fixture and the env var to
    re-record with. A fixture that cannot go stale loudly is not a fixture, it is
    a decoy.

    `litellm` is imported inside the request method, so importing this module
    costs nothing and offline test collection never touches a provider SDK.
    """

    def __init__(
        self,
        profile: CallerProfile,
        *,
        cassette: str | Path,
        model: str | None = None,
        model_label: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        live_env_var: str = LIVE_CALLER_ENV_VAR,
        key: CassetteKey | None = None,
        max_utterances: int = DEFAULT_MAX_TURNS,
        on_leak: OnLeak = "record",
        max_retries: int = RATE_LIMIT_RETRIES,
        retry_base_s: float = RATE_LIMIT_BASE_DELAY_S,
        sleep: Callable[[float], None] | None = None,
        extra_completion_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """
        Args:
            profile: The persona and goal this caller plays.
            cassette: Where the recording lives. `for_scenario` derives this from
                a `CassetteKey` instead, which is the form to prefer.
            model: The litellm route, e.g. `provider/deployment`. Resolved from
                `$LAB_CALLER_MODEL` at the moment of the first live call if left
                None — no model id is hardcoded in `lab`, and a route is not
                needed at all to replay.
            model_label: What the fixture should say the turns came from. Defaults
                to `model`. It exists because a route can name a private
                deployment inside somebody's cloud account, and a committed
                fixture is public: the label is the model family a reader needs,
                the route is infrastructure that has no business in git.
            temperature: Sampling temperature. Part of the cassette key: replaying
                a T=0.7 recording as if it were T=0 misdescribes the variance.
            max_tokens: Output budget. None derives it from the persona's
                verbosity (`VERBOSITY_TOKEN_BUDGET`), so `terse` is enforced by
                the API and not only requested in prose.
            live_env_var: The opt-in switch. Absent, a cassette miss raises.
            key: The fixture's declared identity, checked against the file on
                load. Set for you by `for_scenario`.
            max_utterances: Hard cap on caller turns, checked before spending.
            on_leak: `"record"` (default) counts a gated-fact leak and carries it
                in the fixture; `"raise"` stops the run. Recording is the default
                because a leak rate is a measurement worth having, and a harness
                that aborts on the first one never produces it.
            max_retries, retry_base_s, sleep: Rate-limit backoff. `sleep` is
                injected so a test can prove the backoff without waiting for it.
            extra_completion_kwargs: Passed through to `litellm.completion` —
                `api_base`, `api_version` and the like for providers that need
                them. Never recorded: it is where credentials-adjacent settings
                live.
        """
        self.profile = profile
        self.cassette_path = Path(cassette)
        self.model = model
        self.model_label = model_label or model or "unspecified-model"
        self.temperature = temperature
        self.max_tokens = (
            max_tokens
            if max_tokens is not None
            else VERBOSITY_TOKEN_BUDGET[profile.persona.verbosity]
        )
        self.live_env_var = live_env_var
        self.key = key
        self.max_utterances = max_utterances
        if on_leak not in ("record", "raise"):
            raise ValueError(f"on_leak must be 'record' or 'raise', got {on_leak!r}")
        self.on_leak: OnLeak = on_leak
        self.max_retries = max_retries
        self.retry_base_s = retry_base_s
        self._sleep = sleep if sleep is not None else time.sleep
        self.extra_completion_kwargs = dict(extra_completion_kwargs or {})

        self._history: list[dict[str, str]] = []
        self._recorded: list[dict[str, Any]] = []
        self._said: list[str] = []
        self._asked: list[str] = []
        self._released: list[str] = []
        self._leaks: list[DisclosureLeak] = []
        self._ending = False
        self._stop_reason: str | None = None
        self._cassette: dict[str, Any] = self._load_cassette()
        self._dirty = False

    # ----------------------------------------------------------- construction

    @classmethod
    def for_scenario(
        cls,
        profile: CallerProfile,
        *,
        scenario_id: str,
        root: str | Path,
        model: str | None = None,
        model_label: str | None = None,
        temperature: float = 0.0,
        variant: int | None = None,
        max_utterances: int = DEFAULT_MAX_TURNS,
        **kwargs: Any,
    ) -> "LLMCaller":
        """Build a caller whose cassette path is derived from its identity.

        The form to use. Choosing a filename by hand is how two scenarios come to
        share a recording, and the failure that produces is a green suite.
        """
        key = CassetteKey.build(
            scenario_id=scenario_id,
            profile=profile,
            model=model_label or model or "unspecified-model",
            temperature=temperature,
            turn_budget=max_utterances,
            variant=variant,
        )
        return cls(
            profile,
            cassette=key.path_in(root),
            model=model,
            model_label=model_label,
            temperature=temperature,
            key=key,
            max_utterances=max_utterances,
            **kwargs,
        )

    # ---------------------------------------------------------------- cassette

    def _load_cassette(self) -> dict[str, Any]:
        if not self.cassette_path.exists():
            return {"model": self.model_label, "turns": []}
        loaded = json.loads(self.cassette_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or "turns" not in loaded:
            raise ValueError(
                f"{self.cassette_path}: not a caller cassette (expected a mapping "
                "with a 'turns' list)"
            )
        self._check_identity(loaded)
        return loaded

    def _check_identity(self, loaded: dict[str, Any]) -> None:
        """Refuse a cassette that is a recording of something else.

        Skipped when this caller declares no key — a hand-written cassette in a
        unit test is a legitimate thing to have, and demanding provenance from it
        would make the strict path unreachable in the tests that matter.
        """
        if self.key is None:
            return
        stored = loaded.get("key")
        if stored is None:
            raise ValueError(
                f"{self.cassette_path}: cassette carries no identity block, so it "
                f"cannot be shown to be a recording of {self.key.describe()}. "
                f"Re-record it with {self.live_env_var}=1."
            )
        recorded_key = CassetteKey.model_validate(stored)
        differences = self.key.differences(recorded_key)
        if differences:
            joined = "\n  ".join(differences)
            raise ValueError(
                f"{self.cassette_path}: this cassette records a different "
                f"conversation from the one being run.\n  {joined}\n"
                f"Replaying it would report another instrument's turns as this "
                f"one's. Re-record with {self.live_env_var}=1."
            )

    @property
    def live_enabled(self) -> bool:
        """True when the opt-in env var permits a real provider call."""
        return bool(os.environ.get(self.live_env_var))

    def _context_digest(self) -> str:
        """sha256 over the conversation so far — the replay key."""
        payload = json.dumps(
            {"system": self.profile.system_prompt(), "history": self._history},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _replay(self, index: int, digest: str) -> str | None:
        turns = self._cassette.get("turns", [])
        if index >= len(turns):
            return None
        entry = turns[index]
        recorded_digest = entry.get("context_sha256")
        if recorded_digest != digest:
            raise ValueError(
                f"{self.cassette_path}: cassette is stale at turn {index}. The "
                "conversation leading into this turn does not match what was "
                f"recorded (context sha256 {digest[:12]} vs {str(recorded_digest)[:12]}). "
                "The agent's behaviour changed, so replaying this turn would answer "
                f"a question that was never asked. Re-record with {self.live_env_var}=1."
            )
        return str(entry["utterance"])

    def save(self) -> Path | None:
        """Write the cassette if this run generated anything new.

        Returns the path written, or None when the run was a pure replay — so a
        test can assert that replaying recorded nothing.
        """
        if not self._dirty:
            return None
        self.cassette_path.parent.mkdir(parents=True, exist_ok=True)
        document: dict[str, Any] = {
            "model": self.model_label,
            "temperature": self.temperature,
            "persona": self.profile.persona.name,
            "goal_intent": self.profile.goal.intent,
            "stop_reason": self._stop_reason,
            "leaks": [leak.model_dump(mode="json") for leak in self._leaks],
            "turns": self._recorded,
        }
        if self.key is not None:
            document["key"] = self.key.model_dump(mode="json")
        self.cassette_path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return self.cassette_path

    # ------------------------------------------------------------- what it did

    @property
    def stop_reason(self) -> str | None:
        """Why the caller stopped: the sentinel, or the guard that fired.

        `"goal_reached"` for the `[END OF CALL]` sentinel — the caller judging its
        own goal met or refused. The others are the instrument intervening:
        `"repeated_line"`, `"turn_budget"`.
        """
        return self._stop_reason

    @property
    def utterances(self) -> list[str]:
        """Everything the caller said, in order, sentinel turn included."""
        return [entry["utterance"] for entry in self._recorded]

    @property
    def leaks(self) -> list[DisclosureLeak]:
        """Gated facts said before they were asked for. A floor, not a total."""
        return list(self._leaks)

    @property
    def released_facts(self) -> list[str]:
        """Gated fact keys the caller has actually spoken, in the order it said them.

        Same contract as `ScriptedCaller.released_facts`, so an information-loss
        check reads either caller without knowing which it has: a fact that was
        never released cannot have been dropped by the agent.
        """
        return list(self._released)

    @property
    def asked_facts(self) -> list[str]:
        """Gated fact keys the agent actually asked for, in first-ask order."""
        return list(self._asked)

    @property
    def leak_detection_note(self) -> str:
        """What the leak audit can and cannot see. Printed next to any leak count.

        Stated as a method rather than a comment because the number is reported,
        and a reported number with an unstated detection limit invites a stronger
        reading than it can carry.
        """
        return (
            "Leak detection matches a gated fact's value, or its declared spoken "
            "form, verbatim and on word boundaries. A paraphrase ('a couple of us' "
            "for a party size of 2) is not detected, so the count is a lower bound "
            "on leaks and the zero case is 'none detected', not 'none occurred'."
        )

    # ---------------------------------------------------------------- speaking

    def _note_asks(self, agent_text: str) -> None:
        """Record which gated facts the agent has now asked for."""
        goal = self.profile.goal
        for key in goal.asked_keys(agent_text, among=goal.gated_keys()):
            if key not in self._asked:
                self._asked.append(key)

    def _audit_disclosure(self, utterance: str, index: int) -> None:
        """Note gated facts spoken in this turn, and flag the unasked-for ones."""
        goal = self.profile.goal
        for key in goal.gated_keys():
            if not _mentions(utterance, goal.fact(key), goal.spoken(key)):
                continue
            if key not in self._released:
                self._released.append(key)
            if key in self._asked:
                continue
            leak = DisclosureLeak(turn_index=index, fact=key)
            self._leaks.append(leak)
            if self.on_leak == "raise":
                raise DisclosureLeakError(
                    f"the caller volunteered the gated fact {key!r} on turn "
                    f"{index} without being asked for it, which makes 'did the "
                    "agent ask?' unanswerable for this run. "
                    f"{self.leak_detection_note}"
                )

    def _stop_for_repetition(self, normalised: str) -> bool:
        """Has the caller started looping?

        Two triggers. The same line twice in a row is a loop immediately — there
        is no reading of a conversation in which that is progress. Otherwise a
        line is allowed `REPEAT_LIMIT` outings, because a reluctant persona's
        stall ("sorry, what was that?") legitimately recurs across a long call,
        and a guard that fired on it would end exactly the scenarios that exist to
        test re-asking.
        """
        if self._said and self._said[-1] == normalised:
            return True
        return self._said.count(normalised) >= REPEAT_LIMIT

    def _next_utterance(self, user_message: str | None) -> str | None:
        if self._ending:
            # The sentinel arrived on the previous turn, alongside the caller's
            # last words. Those words have been delivered and the agent has had
            # its turn; now the line goes dead — without buying a completion for
            # a turn whose content is already decided.
            self._stop_reason = "goal_reached"
            return None
        if len(self._said) >= self.max_utterances:
            self._stop_reason = "turn_budget"
            return None
        if user_message is not None:
            self._history.append({"role": "user", "content": user_message})
        index = sum(1 for m in self._history if m["role"] == "assistant")
        digest = self._context_digest()

        replayed = self._replay(index, digest)
        if replayed is None:
            if not self.live_enabled:
                raise RuntimeError(
                    f"LLMCaller has no recorded turn {index} in {self.cassette_path} "
                    f"and live calls are off. Set {self.live_env_var}=1 to record it, "
                    "or drive this scenario with a ScriptedCaller — every offline "
                    "test in this repo does."
                )
            replayed = self._complete()
            self._dirty = True
        self._recorded.append(
            {"index": index, "context_sha256": digest, "utterance": replayed}
        )
        self._history.append({"role": "assistant", "content": replayed})

        spoken, ending = _split_sentinel(replayed)
        self._ending = ending
        self._audit_disclosure(spoken, index)

        if not spoken:
            # A bare sentinel is the caller following the rule. An empty response
            # with no sentinel is the provider returning nothing, which ends the
            # call just the same but must not be filed as a goal reached — that
            # would put a satisfied caller and a broken completion in one bucket.
            self._stop_reason = "goal_reached" if ending else "empty_utterance"
            return None

        normalised = _normalise(spoken)
        if self._stop_for_repetition(normalised):
            self._said.append(normalised)
            self._stop_reason = "repeated_line"
            return None
        self._said.append(normalised)
        return spoken

    def _complete(self) -> str:
        """One live completion, with rate-limit backoff.

        The only method here that touches the network, and the only place a model
        route is required — which is why it is resolved here rather than in
        `__init__`: replaying a fixture must not need one.
        """
        from litellm import completion  # imported lazily, on purpose

        route = self.model or os.environ.get(CALLER_MODEL_ENV_VAR) or ""
        if not route:
            raise RuntimeError(
                "no caller model configured: pass model= or set "
                f"{CALLER_MODEL_ENV_VAR} (e.g. {CALLER_MODEL_ENV_VAR}="
                "provider/deployment). No model id is hardcoded in this library."
            )
        if not self.live_enabled:
            raise RuntimeError(
                f"refusing to call a provider with {self.live_env_var} unset"
            )

        delay = self.retry_base_s
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = completion(
                    model=route,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    messages=[
                        {"role": "system", "content": self.profile.system_prompt()},
                        *self._history,
                    ],
                    **self.extra_completion_kwargs,
                )
                text = response["choices"][0]["message"]["content"]
                return str(text).strip()
            except Exception as exc:  # noqa: BLE001 - re-raised unless rate-limited
                if attempt >= self.max_retries or not _is_rate_limited(exc):
                    raise
                last = exc
                self._sleep(delay)
                delay *= 2
        raise RuntimeError(  # pragma: no cover - the loop either returns or raises
            f"rate limited after {self.max_retries} retries: {last}"
        )

    def opening(self) -> str:
        first = self._next_utterance(
            "You have just been connected. Say your opening line."
        )
        if first is None:
            raise RuntimeError(
                "the LLM caller ended the call on its opening turn "
                f"({self._stop_reason}); check the cassette at {self.cassette_path}"
            )
        return first

    def reply(self, agent_turn: AgentTurn) -> str | None:
        self._note_asks(agent_turn.text or "")
        return self._next_utterance(agent_turn.text or "(the agent said nothing)")

    def __repr__(self) -> str:
        return (
            f"LLMCaller(model={self.model_label!r}, "
            f"cassette={str(self.cassette_path)!r}, live={self.live_enabled}, "
            f"turns_recorded={len(self._recorded)}, stop={self._stop_reason!r})"
        )


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


def _interpolated(t0: float, t1: float, count: int) -> list[float]:
    """`count` instants evenly spaced strictly inside (t0, t1).

    Strictly inside, so an estimated tool event can never collide with — or
    precede — the real captured boundary instants, and `Trace.is_ordered()` holds.
    A zero-width window (a `FakeClock` run where the agent did not sleep) yields
    t0 repeatedly, which is still non-decreasing and still correctly ordered.
    """
    span = t1 - t0
    return [t0 + span * ((i + 1) / (count + 1)) for i in range(count)]


class _WindowStamper:
    """Hands out in-window timestamps for the events of one turn.

    Two jobs, both about keeping the trace's ordering invariant true no matter
    what an adapter reports:

    *   An event with no observed timestamp gets the next evenly-spaced estimate
        and is flagged `ts_estimated`.
    *   An event *with* an observed timestamp keeps it, clamped into `[t0, t1]`
        and to the running maximum. A mix of observed and estimated instants
        could otherwise emit a late observed call before an early estimate, and
        `Trace.is_ordered()` would fail on a trace the harness produced itself.
        Clamping is silent because the clamp only ever moves an event to the edge
        of the window it is already known to belong to.
    """

    def __init__(self, t0: float, t1: float, slots: int) -> None:
        self._t0 = t0
        self._t1 = t1
        self._estimates = _interpolated(t0, t1, slots)
        self._cursor = 0
        self._last = t0

    def take(self, observed: float | None) -> tuple[float, dict[str, Any]]:
        """Return `(ts, payload_extra)` for one event."""
        if observed is None:
            ts = self._estimates[self._cursor]
            self._cursor += 1
            extra: dict[str, Any] = dict(_ESTIMATED)
        else:
            ts = min(max(observed, self._t0), self._t1)
            extra = {}
        ts = max(ts, self._last)
        self._last = ts
        return ts, extra


def run_scenario(
    *,
    scenario_id: str,
    agent: AgentUnderTest,
    caller: Caller,
    adapter: str = "text",
    clock: Clock | None = None,
    session_id: str | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    profile: CallerProfile | None = None,
    emit_transcripts: bool = True,
    emit_response_boundary: bool = True,
    stt_engine: str = STT_ENGINE,
    tts_engine: str = TTS_ENGINE,
) -> Trace:
    """Drive `caller` against `agent` for up to `max_turns` turns; return the trace.

    The trace is the only output. Everything a check, a judge, a latency figure or
    a report needs is in it, which is the invariant the whole repo rests on: if a
    result cannot be recomputed from the trace on disk, it cannot be audited.

    Args:
        scenario_id: Identifier written into the trace and every report row.
        agent: The system under test. Called exactly once per turn.
        caller: The simulated caller. `ScriptedCaller` for tests.
        adapter: Free-form tag for what drove the run, e.g. "text",
            "voice:replay". Recorded, never interpreted.
        clock: Time source; defaults to `TraceBuilder`'s `MonotonicClock`. Pass a
            `FakeClock` for exact, reproducible timestamps.
        session_id: Defaults to a fresh uuid4 hex.
        max_turns: Hard stop. Reaching it ends the session with
            `reason="max_turns"` rather than raising.
        profile: Caller profile for `session_start` attribution. Defaults to
            `caller.profile` when the caller has one.
        emit_transcripts: Emit `transcript_in` / `transcript_out` around each
            turn. True even for text runs: it keeps text and voice traces the
            same shape, so one analysis works on both, and it distinguishes what
            the agent *heard* from what the caller *said* — which is where a
            voice failure gets attributed to STT instead of to the model.
        emit_response_boundary: Emit `agent_audio_first_byte` at `t1`. This is the
            event `lab.voice.calibration.recover_response_latencies` pairs on. For
            a text adapter it marks the instant the first response byte reached
            the harness; the kind is shared so one latency definition covers both.
        stt_engine, tts_engine: Engine tags for the transcript legs.

    Returns:
        The `Trace`. Turn count and stop reason are in the `session_end` payload.
    """
    if max_turns < 1:
        raise ValueError(f"max_turns must be at least 1, got {max_turns!r}")

    effective_profile = profile if profile is not None else getattr(caller, "profile", None)
    builder = TraceBuilder(
        scenario_id=scenario_id,
        adapter=adapter,
        session_id=session_id,
        clock=clock,
    )
    metadata: dict[str, Any] = {"caller": type(caller).__name__, "max_turns": max_turns}
    if isinstance(effective_profile, CallerProfile):
        metadata.update(effective_profile.trace_metadata())
    builder.session_start(**metadata)

    utterance: str | None = caller.opening()
    turns = 0
    reason = "caller_hung_up"

    while utterance is not None:
        if turns >= max_turns:
            reason = "max_turns"
            break
        turns += 1

        if emit_transcripts:
            # What the agent heard. In a text run this is the caller's words
            # unchanged; a voice adapter puts real STT output here, and the gap
            # between the two is transcription error rather than model error.
            builder.transcript_in(utterance, confidence=1.0, engine=stt_engine)

        t0 = builder.now()  # ---- BOUNDARY OUT
        reply = agent(utterance)  # the system under test, and nothing else
        t1 = builder.now()  # ---- BOUNDARY IN
        # ---- the measured window is closed; everything below is harness compute

        agent_turn = coerce_turn(reply)
        builder.caller_utterance(utterance, ts=t0)

        inner: list[Handoff | ToolInvocation] = []
        if agent_turn.handoff is not None:
            inner.append(agent_turn.handoff)
        inner.extend(agent_turn.tools)
        # Two trace events per tool (call + result), one per handoff.
        slots = sum(2 if isinstance(item, ToolInvocation) else 1 for item in inner)
        stamper = _WindowStamper(t0, t1, slots)

        for item in inner:
            if isinstance(item, Handoff):
                ts, extra = stamper.take(item.ts)
                builder.agent_handoff(
                    item.from_agent, item.to_agent, reason=item.reason, ts=ts, **extra
                )
                continue
            call_ts, call_extra = stamper.take(item.ts)
            result_ts, result_extra = stamper.take(item.ts)
            call_event = builder.tool_call(
                item.name, item.args, call_id=item.call_id, ts=call_ts, **call_extra
            )
            builder.tool_result(
                item.name,
                item.result,
                call_id=call_event.get("call_id"),
                ok=item.ok,
                error=item.error,
                ts=result_ts,
                **result_extra,
            )

        if emit_response_boundary:
            builder.agent_audio_first_byte(turn=turns, ts=t1, engine=tts_engine)
        if emit_transcripts:
            builder.transcript_out(agent_turn.text, ts=t1, engine=tts_engine)
        # Not back-dated: this is the instant the harness finished with the turn,
        # which is what makes `transcript_in -> agent_utterance` the honest
        # whole-turn cost figure (and, under a FakeClock, identical to t1).
        builder.agent_utterance(agent_turn.text, agent=agent_turn.agent)

        if agent_turn.end_call:
            reason = "agent_ended"
            break

        utterance = caller.reply(agent_turn)

    builder.session_end(reason=reason, turns=turns)
    return builder.build()
