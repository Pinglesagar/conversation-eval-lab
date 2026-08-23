"""The adapter: TableMate as something the harness can drive.

WHAT THIS DEMONSTRATES
----------------------
Three things a system under test has to get right to be measurable.

**1. One call, one turn, nothing in between.**
`TableMate.__call__` satisfies `lab.simulator.AgentUnderTest` — utterance in,
`AgentTurn` out — and it is the only place in this package that imports the
harness. `tablemate.agents`, `.tools`, `.store` and `.understanding` know nothing
about `lab`. That boundary is the claim the harness makes about itself (an
instrument pointed at a system, not a framework the system must adopt), and this
file is where the claim is either true or false.

**2. Latency is produced, not asserted.**
The backends spend time on the injected clock: a fixed think time, plus a cost per
tool call, plus a per-character speaking cost. Under a `FakeClock` that is exact
and free, which is what makes a voice-latency fixture reproducible; under a real
clock it is a real wait. Either way the number the harness recovers is a number
this system actually spent, and `lab.voice.calibration` is what proves the
recovery is faithful.

**3. Two backends, one set of decisions.**
`ScriptedBackend` speaks the lines `tablemate.agents` composed. `LLMBackend` sends
those lines to a model and speaks the paraphrase, then caches it so the run
replays offline forever. Both drive identical orchestration, tool calls and
handoffs — the model chooses words and nothing else. So switching backends
changes exactly one variable, and the eval suite's verdicts across the two answer
a question worth asking: *how much of my detector's recall depends on the way the
agent happened to phrase things?*

WHAT THIS DOES NOT DO
---------------------
No streaming, so `agent_audio_first_byte` is the instant the whole turn was
returned rather than the instant speech began; a streaming adapter would report
the first token instead, and the trace kind is the same either way. No barge-in,
no async, no retries around the model call: a paraphrase that fails is an error,
not a silently-scripted line, because a backend that quietly falls back would
make the comparison above meaningless.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from lab.clock import Clock, MonotonicClock
from lab.simulator import AgentTurn, Handoff, ToolInvocation

from tablemate.agents import SPECS, Orchestrator, Speech, Turn
from tablemate.store import Restaurant, default_restaurant
from tablemate.tools import ToolCall

__all__ = [
    "LIVE_AGENT_ENV_VAR",
    "MODEL_ENV_VAR",
    "LatencyModel",
    "DEFAULT_LATENCY",
    "Backend",
    "ScriptedBackend",
    "LLMBackend",
    "PhraseCassette",
    "MissingPhrasingError",
    "TableMate",
    "build_agent",
]

#: Set this to a truthy value to let `LLMBackend` reach a live provider. Absent,
#: the backend replays from its cassette and raises on a miss — a clean clone
#: cannot spend money by accident.
LIVE_AGENT_ENV_VAR: str = "LAB_LIVE_AGENT"

#: Which model does the phrasing, when a live call is permitted.
MODEL_ENV_VAR: str = "LAB_AGENT_MODEL"

_DEFAULT_MODEL: str = "gpt-4o-mini"

#: What the model is told. Narrow on purpose: it is a rewriter, not an agent. Any
#: latitude here would let it add a fact, drop a question or invent a booking
#: reference, and the two backends would stop being comparable.
PARAPHRASE_SYSTEM: str = (
    "You rewrite one line of dialogue for a restaurant booking assistant. "
    "Rules, all of them absolute: keep every fact exactly as given, including "
    "numbers, dates, times, names and reference codes; keep every question that "
    "is asked and add none; do not add pleasantries that promise anything; do "
    "not mention that you are rewriting. Reply with the rewritten line and "
    "nothing else."
)


# --------------------------------------------------------------------------- #
# Latency
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LatencyModel:
    """Where a turn's time goes, in seconds.

    Three terms because the three have different shapes in a real system and a
    single constant would hide that: thinking is per turn, tool round trips are
    per call, and speaking scales with how much there is to say. A scenario that
    wants to show a slow agent turns one knob rather than reaching for `sleep`.
    """

    think_s: float = 0.32
    per_tool_s: float = 0.18
    per_char_s: float = 0.0035

    def seconds_for(self, *, text: str, tool_calls: int) -> float:
        return (
            self.think_s
            + self.per_tool_s * max(0, tool_calls)
            + self.per_char_s * len(text or "")
        )


#: The default profile. Roughly a second for a short tool-using turn, which is
#: the order of magnitude a voice assistant lives at.
DEFAULT_LATENCY: LatencyModel = LatencyModel()


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #


class Backend(Protocol):
    """Turns a decided `Speech` into the words the caller hears."""

    def phrase(self, speech: Speech, *, agent: str) -> str: ...


class ScriptedBackend:
    """Speaks the line the agents composed, unchanged.

    Deterministic, offline, and the backend behind every test and fixture in this
    repository. A recorded conversation is only a fixture if it is byte-identical
    on the next machine.
    """

    def phrase(self, speech: Speech, *, agent: str) -> str:
        return speech.text


class MissingPhrasingError(RuntimeError):
    """`LLMBackend` needed a paraphrase that is neither cached nor permitted live."""


@dataclass
class PhraseCassette:
    """Recorded paraphrases, keyed by what was asked for.

    The key is a digest of (model, sub-agent, dialogue act, source line), so a
    cassette entry cannot be replayed into a different line than the one it was
    recorded for. A fixture that silently answers the wrong question is worse
    than no fixture.
    """

    path: Path
    model: str
    entries: dict[str, dict[str, Any]]

    @classmethod
    def load(cls, path: str | Path, *, model: str) -> "PhraseCassette":
        source = Path(path)
        if not source.exists():
            return cls(path=source, model=model, entries={})
        loaded = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or "phrasings" not in loaded:
            raise ValueError(
                f"{source}: not a phrasing cassette (expected a mapping with a "
                "'phrasings' object)"
            )
        return cls(path=source, model=model, entries=dict(loaded["phrasings"]))

    @staticmethod
    def key(*, model: str, agent: str, act: str, text: str) -> str:
        payload = json.dumps(
            {"model": model, "agent": agent, "act": act, "text": text}, sort_keys=True
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def get(self, key: str) -> str | None:
        entry = self.entries.get(key)
        return None if entry is None else str(entry["phrased"])

    def put(self, key: str, *, agent: str, act: str, source: str, phrased: str) -> None:
        self.entries[key] = {
            "agent": agent,
            "act": act,
            "source": source,
            "phrased": phrased,
        }

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {"model": self.model, "phrasings": self.entries}
        self.path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return self.path


class LLMBackend:
    """Rephrases each decided line with a model, and records what it said.

    The completion function is injectable, which is how the offline tests exercise
    this path without a provider: pass any `Callable[[str, str], str]` taking
    (system, user) and returning the rewritten line. Left unset, it calls
    `litellm` — and only when `LAB_LIVE_AGENT` is set.
    """

    def __init__(
        self,
        *,
        cassette: str | Path,
        model: str | None = None,
        completion: Callable[[str, str], str] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 160,
        live_env_var: str = LIVE_AGENT_ENV_VAR,
    ) -> None:
        self.model = model or os.environ.get(MODEL_ENV_VAR) or _DEFAULT_MODEL
        self.cassette = PhraseCassette.load(cassette, model=self.model)
        self._completion = completion
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.live_env_var = live_env_var
        self.recorded = 0

    @property
    def live_enabled(self) -> bool:
        """True when an injected completion or the opt-in env var permits a call."""
        return self._completion is not None or bool(os.environ.get(self.live_env_var))

    def phrase(self, speech: Speech, *, agent: str) -> str:
        key = PhraseCassette.key(
            model=self.model, agent=agent, act=speech.act, text=speech.text
        )
        cached = self.cassette.get(key)
        if cached is not None:
            return cached
        if not self.live_enabled:
            raise MissingPhrasingError(
                f"no recorded phrasing for {agent}/{speech.act} in "
                f"{self.cassette.path}, and live calls are off. Set "
                f"{self.live_env_var}=1 to record it, or drive this run with "
                "ScriptedBackend — every offline test in this repository does."
            )
        phrased = self._complete(speech, agent=agent)
        self.cassette.put(
            key, agent=agent, act=speech.act, source=speech.text, phrased=phrased
        )
        self.recorded += 1
        return phrased

    def save(self) -> Path | None:
        """Write the cassette if this run recorded anything new."""
        return self.cassette.save() if self.recorded else None

    def _complete(self, speech: Speech, *, agent: str) -> str:
        system = f"{PARAPHRASE_SYSTEM}\n\nThe assistant's remit:\n{SPECS[agent].system_prompt}"
        if self._completion is not None:
            return self._completion(system, speech.text).strip()
        from litellm import completion  # imported lazily, on purpose

        response = completion(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": speech.text},
            ],
        )
        return str(response["choices"][0]["message"]["content"]).strip()


# --------------------------------------------------------------------------- #
# The system under test
# --------------------------------------------------------------------------- #


def _as_invocation(call: ToolCall) -> ToolInvocation:
    """The one-line translation at the edge: a tool call in the harness's terms."""
    return ToolInvocation(
        name=call.name,
        args=dict(call.args),
        result=call.result,
        ok=call.ok,
        error=call.error,
        call_id=call.call_id,
    )


class TableMate:
    """The booking assistant, as one callable turn.

    Statefulness lives here: one instance is one conversation, with its own
    restaurant, its own toolbox and its own session. `lab.simulator.run_pass_k`
    therefore wants `build_agent` (a factory), not an instance — otherwise the
    second repeat of a scenario would inherit the first repeat's diary and measure
    history instead of behaviour.
    """

    def __init__(
        self,
        *,
        store: Restaurant | None = None,
        backend: Backend | None = None,
        clock: Clock | None = None,
        latency: LatencyModel | None = DEFAULT_LATENCY,
    ) -> None:
        self.store = store if store is not None else default_restaurant()
        self.orchestrator = Orchestrator(store=self.store)
        self.backend: Backend = backend if backend is not None else ScriptedBackend()
        self.clock: Clock = clock if clock is not None else MonotonicClock()
        self.latency = latency
        self.turns: list[Turn] = []

    # ------------------------------------------------------------------ reading

    @property
    def session(self) -> Any:
        """The live `Session`. Read by tests and by nothing in `lab`."""
        return self.orchestrator.session

    @property
    def toolbox(self) -> Any:
        """The toolbox, whose `calls` list is the full tool ledger for the call."""
        return self.orchestrator.toolbox

    def tool_names(self) -> list[str]:
        return self.orchestrator.toolbox.names()

    def bookings(self) -> list[dict[str, Any]]:
        """The diary as it now stands. Ground truth for "did that actually happen"."""
        return [b.as_dict() for b in self.store.bookings]

    # -------------------------------------------------------------------- turns

    def __call__(self, utterance: str) -> AgentTurn:
        """One turn. Satisfies `lab.simulator.AgentUnderTest`."""
        turn = self.orchestrator.turn(utterance)
        text = self.backend.phrase(turn.speech, agent=turn.agent)
        self.turns.append(turn)

        if self.latency is not None:
            self.clock.sleep(
                self.latency.seconds_for(text=text, tool_calls=len(turn.tools))
            )

        handoff = None
        if turn.handoff is not None:
            source, destination, reason = turn.handoff
            handoff = Handoff(from_agent=source, to_agent=destination, reason=reason)

        return AgentTurn(
            text=text,
            agent=turn.agent,
            tools=[_as_invocation(c) for c in turn.tools],
            handoff=handoff,
            end_call=turn.end_call,
        )

    def __repr__(self) -> str:
        return (
            f"TableMate(backend={type(self.backend).__name__}, "
            f"turns={len(self.turns)}, tools={self.tool_names()})"
        )


def build_agent(
    *,
    store: Restaurant | None = None,
    backend: Backend | None = None,
    clock: Clock | None = None,
    latency: LatencyModel | None = DEFAULT_LATENCY,
    seed: Callable[[Restaurant], None] | None = None,
) -> TableMate:
    """A fresh conversation, with a fresh restaurant.

    Args:
        store: An existing restaurant to talk about. Defaults to a new one.
        backend: Phrasing backend. Defaults to `ScriptedBackend`.
        clock: Time source. Pass a `FakeClock` for exact, reproducible timing.
        latency: Where a turn's time goes. `None` means no simulated delay —
            which collapses every event in the run onto the same instant under a
            `FakeClock`, and any check that compares timestamps then has nothing
            to compare. On by default for that reason. Note that a real clock
            really does wait, so fixtures should pass a `FakeClock`.
        seed: Called with the new restaurant before the conversation starts —
            the hook a scenario uses to put a reference in the diary
            (`Restaurant.ensure_booking`) or to fill a sitting
            (`Restaurant.book_out`).

    Use this rather than the constructor wherever a scenario is run more than
    once: `functools.partial(build_agent, seed=...)` is the factory
    `lab.simulator.run_pass_k` expects.
    """
    restaurant = store if store is not None else default_restaurant()
    if seed is not None:
        seed(restaurant)
    return TableMate(
        store=restaurant, backend=backend, clock=clock, latency=latency
    )
