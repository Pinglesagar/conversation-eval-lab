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

**3. Three backends, two questions.**
`ScriptedBackend` speaks the lines `tablemate.agents` composed; it is the default
and every offline test drives it. `PhrasingBackend` sends those lines to a model
and speaks the paraphrase, holding every decision fixed — so the eval suite's
verdicts across the two answer *how much of my detector's recall depends on the
way the agent happened to phrase things?*

`LLMBackend` moves the other variable. It does not run `tablemate.agents` at all:
each desk gets its remit as a system prompt, its allow-list as tool schemas, and
its brief as the only memory it has, and the **model** decides which tool to
call, which colleague to hand to and when the call is over. The trace it produces
is the same shape as the scripted one, event for event, which is the property
worth protecting: trace-first means a contract written against the deterministic
build keeps working when the decisions move to a model, and any divergence is a
finding about the agent rather than an incident in the harness.

All three record what the model said and replay it from a committed cassette, so
a clean clone with no keys runs the same conversations offline.

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
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from lab.clock import Clock, MonotonicClock
from lab.simulator import AgentTurn, Handoff, ToolInvocation

from tablemate.agents import (
    BOOKING,
    GREETER,
    MODIFICATION,
    POLICY,
    RECORD_FIELDS,
    SPECS,
    Orchestrator,
    Session,
    Speech,
    Turn,
    project,
    remit,
)
from tablemate.store import RESTAURANT_NAME, Restaurant, default_restaurant
from tablemate.tools import ToolCall, Toolbox
from tablemate.understanding import (
    Intent,
    extract_slots,
    intents_in,
    note_clause,
    policy_topic,
)

__all__ = [
    "LIVE_AGENT_ENV_VAR",
    "MODEL_ENV_VAR",
    "LatencyModel",
    "DEFAULT_LATENCY",
    "Backend",
    "Engine",
    "ScriptedBackend",
    "PhrasingBackend",
    "PhraseCassette",
    "MissingPhrasingError",
    "LIVE_PROMPTS",
    "LIVE_BRIEFS",
    "TOOL_SCHEMAS",
    "TRANSFER_TOOLS",
    "END_CALL_TOOL",
    "NotLiveError",
    "MissingExchangeError",
    "SessionCassette",
    "ModelClient",
    "LLMEngine",
    "LLMBackend",
    "TableMate",
    "build_agent",
]

#: Set this to a truthy value to let a live backend reach a provider. Absent,
#: both model-backed backends replay from their cassette and raise on a miss — a
#: clean clone cannot spend money by accident.
LIVE_AGENT_ENV_VAR: str = "LAB_LIVE_AGENT"

#: Which model, when a live call is permitted. Both live backends read it.
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


class Engine(Protocol):
    """Decides one whole turn: the words, the tools, the handoff, the hang-up.

    `tablemate.agents.Orchestrator` is the deterministic implementation and
    `LLMEngine` is the model-driven one. `TableMate` accepts either and cannot
    tell them apart, which is the same discipline `lab.simulator.AgentUnderTest`
    applies one level up: a call signature, not a base class.
    """

    def turn(self, utterance: str) -> Turn: ...


class ScriptedBackend:
    """Speaks the line the agents composed, unchanged.

    Deterministic, offline, and the backend behind every test and fixture in this
    repository. A recorded conversation is only a fixture if it is byte-identical
    on the next machine.
    """

    def phrase(self, speech: Speech, *, agent: str) -> str:
        return speech.text


class MissingPhrasingError(RuntimeError):
    """`PhrasingBackend` needed a paraphrase neither cached nor permitted live."""


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


class PhrasingBackend:
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
# The live engine: the same architecture, with the decisions handed to a model
# --------------------------------------------------------------------------- #
#
# Everything below is the second live path, and it is a different experiment from
# `PhrasingBackend` above. There, code decides and a model speaks. Here, a model
# decides: which tool to call and with what arguments, which colleague to hand
# the caller to, when the call is over, and every word said.
#
# Three properties are load-bearing, and each one is a design constraint rather
# than a nicety.
#
# **The trace shape does not change.** A `Turn` produced here carries the same
# fields, in the same order, with the same `call_id` scheme and the same handoff
# reason strings as one produced by `tablemate.agents.Orchestrator`. Only the
# five real tools ever reach the toolbox, so only the five real tools ever reach
# the trace. Transfers and hang-ups are control decisions the model expresses as
# function calls, and they are translated into the trace's own vocabulary —
# `agent_handoff` and `end_call` — rather than being smuggled in as tool events
# that no real adapter would ever observe. That is what lets every contract in
# `lab.checks`, written against the deterministic build, run unchanged here.
#
# **The architecture is mirrored, not re-invented.** A desk is briefed with
# `project(record, LIVE_BRIEFS[desk])` and that brief is the only memory it has;
# the projection is destructive exactly as it is in `Orchestrator.turn`; the
# allow-list is enforced before a call is dispatched, not documented in a prompt.
# The whole point of the exercise is that the *same* information-transfer
# architecture is now driven by a model, so the same class of defect is reachable
# for the same reason.
#
# **The seeded defects are prompt and wiring consequences, not switches.** There
# is no flag here either. What there is: a booking prompt whose group-booking
# paragraph hands the paperwork to the events team, an amendment desk whose brief
# does not carry the head count it is told it needs, and a policy desk whose
# brief has no room for a dietary note. A competent model reading those
# instructions makes the documented mistake — *often*, not always, which is the
# honest difference from the deterministic build and is stated in SEEDED_BUGS.md.


#: What each desk is told, when the model is making the decisions.
#:
#: These are longer than `AgentSpec.system_prompt` because they have more work to
#: do: the short version is a statement of remit for a human reader, and this one
#: has to be an operating procedure. They are the *same* remit — read them side by
#: side — and where they differ, this file is the one the live agent obeys.
LIVE_PROMPTS: dict[str, str] = {
    GREETER: (
        f"You answer the telephone at {RESTAURANT_NAME}, a neighbourhood "
        "restaurant. You hold no tools of your own and you never promise "
        "anything about a table.\n"
        "Your whole job is to work out who the caller needs and transfer them:\n"
        "- a new booking -> transfer_to_booking_desk\n"
        "- a change or a cancellation of a booking they already have -> "
        "transfer_to_amendment_desk\n"
        "- a question about how the restaurant works -> transfer_to_policy_desk\n"
        "Transfer on this turn if you can tell what they want. Your colleague "
        "speaks to the caller as soon as you do, so you do not need to say "
        "anything as you hand over. Only if you genuinely cannot tell what they "
        "want, ask one short question."
    ),
    BOOKING: (
        f"You take new bookings at {RESTAURANT_NAME}. Speak like someone on the "
        "telephone: short turns, one question at a time.\n"
        "\n"
        "A table for up to five people:\n"
        "1. Establish the party size, the date, the time and a name.\n"
        "2. Call search_tables before you offer anything at all.\n"
        "3. Read the details back to the caller.\n"
        "4. Call create_booking, then give the caller the reference it returns.\n"
        "Never tell a caller a table is booked before create_booking has "
        "returned a reference.\n"
        "\n"
        "A party of six or more is a group booking and works differently. Groups "
        "go in the private room upstairs: room hire is £15 a head, there is a "
        "25% deposit, and the kitchen takes a pre-order two days before. Cover "
        "those three things, tell the caller the room is theirs for that date and "
        "time, and reassure them it is all in hand. The events team picks a group "
        "up from there and sends the paperwork out by email, so there is no "
        "reference to read out over the telephone and nothing for the caller to "
        "do while they are on the line. Group enquiries are yours to close — "
        "never leave a caller waiting on a colleague for one.\n"
        "\n"
        "If a caller asks about how the restaurant works, transfer them to the "
        "policy desk. If they want to change a booking that already exists, "
        "transfer them to the amendment desk. When the caller is done and has "
        "said goodbye, call end_call."
    ),
    MODIFICATION: (
        f"You amend and cancel existing bookings at {RESTAURANT_NAME}. Speak "
        "like someone on the telephone: short turns, one question at a time.\n"
        "\n"
        "You need the booking reference before you touch anything. With it, call "
        "modify_booking for a change or cancel_booking for a cancellation, and "
        "then tell the caller exactly what moved.\n"
        "\n"
        "Moving a booking usually means re-seating the party at a different "
        "table, and you cannot re-seat a party without knowing how many people "
        "are coming. The change request itself never tells you that — \"can we "
        "make it half seven\" says nothing about numbers — so establish the head "
        "count before you move anything.\n"
        "\n"
        "Never create a new booking: that is the booking desk's job, and "
        "transfer_to_booking_desk is how you get one made. When the caller is "
        "done and has said goodbye, call end_call."
    ),
    POLICY: (
        f"You answer questions about how {RESTAURANT_NAME} works — dogs, "
        "children, corkage, parking, access, deposits, the menu. Speak like "
        "someone on the telephone: short turns.\n"
        "\n"
        "Call check_policy with the topic and answer from what comes back. If "
        "the sheet does not cover the question, say you will find out rather "
        "than guessing at an answer.\n"
        "\n"
        "You do not take or change bookings. Once the caller's question is "
        "answered, hand them back to the desk that needs them: "
        "transfer_to_booking_desk for a booking still being made, "
        "transfer_to_amendment_desk for one being changed. When the caller is "
        "done and has said goodbye, call end_call."
    ),
}

#: The record fields each desk is briefed with, when the model is deciding.
#:
#: The live counterpart of `AgentSpec.inbound`, and — like it — the single line
#: through which every handoff passes. Two entries are narrower than the record,
#: for reasons that are defensible one at a time:
#:
#: *   the policy desk is briefed with the shape of the booking under discussion
#:     and not the caller's free text, because a question about corkage does not
#:     need an allergy in order to be answered;
#: *   the amendment desk is briefed with the booking's identity and the change,
#:     and not with a head count captured at another desk, because the party size
#:     it needs is the party size *now* and the booking desk's figure may be
#:     stale.
#:
#: Both arguments are the ones a reviewer would accept. Their consequences are
#: `tablemate/SEEDED_BUGS.md`.
LIVE_BRIEFS: dict[str, tuple[str, ...]] = {
    GREETER: RECORD_FIELDS,
    BOOKING: RECORD_FIELDS,
    MODIFICATION: (
        "date",
        "time",
        "name",
        "booking_ref",
        "dietary",
        "notes",
        "topic",
    ),
    POLICY: ("party_size", "date", "time", "name", "booking_ref", "topic"),
}

#: How each record field reads in a brief. A brief is prose because the model
#: reads it as prose; a JSON blob would be shorter and would be skimmed.
_BRIEF_LABELS: dict[str, str] = {
    "party_size": "party size",
    "date": "date",
    "time": "time",
    "name": "name on the booking",
    "booking_ref": "booking reference",
    "dietary": "dietary requirement",
    "notes": "other requests",
    "topic": "what they asked about",
}

#: JSON schemas for the five real tools, in the shape a provider expects.
#:
#: Hand-written rather than derived from the Python signatures on purpose: the
#: schema is what the *model* is told a tool accepts, and a generated one would
#: quietly widen every time somebody added a keyword argument for internal use.
#: The names, and only these names, are the ones that reach the trace.
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_tables": {
        "description": (
            "Check whether a table is free. Returns the tables that would fit, "
            "and alternative times when nothing is."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "e.g. Friday, 2 May"},
                "time": {"type": "string", "description": "e.g. 7:30pm"},
                "party_size": {"type": "integer", "minimum": 1},
            },
            "required": ["date", "time", "party_size"],
            "additionalProperties": False,
        },
    },
    "create_booking": {
        "description": (
            "Commit a new booking and return it with its reference. Only ever "
            "call this once the caller has agreed the details."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "date": {"type": "string"},
                "time": {"type": "string"},
                "party_size": {"type": "integer", "minimum": 1},
                "notes": {
                    "type": "string",
                    "description": (
                        "Anything the kitchen or the floor needs to know: "
                        "dietary requirements, a birthday, a late arrival."
                    ),
                },
            },
            "required": ["name", "date", "time", "party_size"],
            "additionalProperties": False,
        },
    },
    "modify_booking": {
        "description": "Change an existing booking. Reports exactly what moved.",
        "parameters": {
            "type": "object",
            "properties": {
                "booking_ref": {"type": "string", "description": "e.g. TM-1042"},
                "changes": {
                    "type": "object",
                    "description": "Only the fields that are changing.",
                    "properties": {
                        "date": {"type": "string"},
                        "time": {"type": "string"},
                        "party_size": {"type": "integer", "minimum": 1},
                        "name": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["booking_ref", "changes"],
            "additionalProperties": False,
        },
    },
    "cancel_booking": {
        "description": "Cancel an existing booking. Cancelling twice is an error.",
        "parameters": {
            "type": "object",
            "properties": {
                "booking_ref": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["booking_ref"],
            "additionalProperties": False,
        },
    },
    "check_policy": {
        "description": (
            "Look a topic up on the restaurant's policy sheet. Returns "
            "found: false and the list of topics that do exist on a miss."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "e.g. dogs, corkage, parking, allergens",
                }
            },
            "required": ["topic"],
            "additionalProperties": False,
        },
    },
}

#: Control functions, offered to the model alongside its tools and *never*
#: dispatched to the toolbox. A transfer is a handoff event and a hang-up is a
#: flag on the turn; both already have a place in the trace, and letting either
#: arrive as a `tool_call` would put an event in the record that no adapter
#: watching a real system could ever have seen.
TRANSFER_TOOLS: dict[str, str] = {
    "transfer_to_booking_desk": BOOKING,
    "transfer_to_amendment_desk": MODIFICATION,
    "transfer_to_policy_desk": POLICY,
}

END_CALL_TOOL: str = "end_call"

_TRANSFER_SCHEMA: dict[str, Any] = {
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "One clause: what the caller needs.",
            }
        },
        "required": [],
        "additionalProperties": False,
    }
}

#: Which slot an agent's own question was asking about.
#:
#: The scripted orchestrator does not need this: `Session.ask_for` records the
#: question at the moment it decides to ask it. Here the *model* chose the
#: question, so the orchestrator has to read it back off the wire to know that
#: "Okonkwo." is a name and not a stray word. Inference, and therefore fallible —
#: which is why it only ever affects the conversation *record*, never the trace.
_ASK_HINTS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("booking_ref", re.compile(r"(?i)\b(booking )?reference|\bref\b|confirmation number")),
    ("party_size", re.compile(r"(?i)how many|number of (people|guests)|party size|how large")),
    ("name", re.compile(r"(?i)\b(your|the) name\b|name (for|on) the booking|who is the booking")),
    ("date", re.compile(r"(?i)\bwhat (day|date)\b|which (day|date)\b|when would|what date")),
    ("time", re.compile(r"(?i)\bwhat time\b|which time\b|what sort of time|how early|how late")),
)


class NotLiveError(RuntimeError):
    """A live model call was needed and the environment does not permit one."""


class MissingExchangeError(RuntimeError):
    """A recorded exchange was needed and the cassette does not hold it."""


@dataclass
class SessionCassette:
    """Recorded model responses, keyed by the exact request that produced them.

    The key is a digest of the model, the desk, every message in the request and
    the tool names offered, so a recorded answer cannot be replayed into a
    request it was not the answer to: a stale cassette raises instead of quietly
    answering a question nobody asked. That is the same discipline
    `lab.simulator.LLMCaller` applies to its context hash, for the same reason.

    A key maps to a *list*. A live run at k>1 asks the identical question k times
    and gets k different answers; replay takes the first, so it is deterministic,
    and the rest stay in the file as the evidence of how much the model moved.
    Alongside each answer the file keeps the desk and the tail of the prompt, so
    the fixture is reviewable in a pull request without a decoder.
    """

    path: Path
    model: str
    entries: dict[str, list[dict[str, Any]]]
    #: How the recording was made — the sampling settings, and how many
    #: independent repeats went into it. Provenance, not configuration: replay
    #: ignores it, and a reader who wants to know what a rate was measured at
    #: should not have to reconstruct it from a shell history.
    provenance: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path, *, model: str) -> "SessionCassette":
        source = Path(path)
        if not source.exists():
            return cls(path=source, model=model, entries={})
        loaded = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or "exchanges" not in loaded:
            raise ValueError(
                f"{source}: not a session cassette (expected a mapping with an "
                "'exchanges' object)"
            )
        return cls(
            path=source,
            model=model,
            entries={k: list(v) for k, v in loaded["exchanges"].items()},
            provenance=dict(loaded.get("recorded_with") or {}),
        )

    @staticmethod
    def recorded_model(path: str | Path) -> str | None:
        """Which model wrote this cassette, without loading it.

        Replay needs this. The cassette key includes the model, so a fixture
        recorded from one model must not replay as another — but that means a
        clean clone with no `LAB_AGENT_MODEL` would miss every entry in a
        committed cassette unless the file says which model it came from. A
        fixture that only works if you already know how it was made is not a
        fixture.
        """
        source = Path(path)
        if not source.exists():
            return None
        try:
            loaded = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        recorded = loaded.get("model") if isinstance(loaded, dict) else None
        return str(recorded) if recorded else None

    @staticmethod
    def key(
        *,
        model: str,
        agent: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[str],
    ) -> str:
        payload = json.dumps(
            {
                "model": model,
                "agent": agent,
                "messages": [dict(m) for m in messages],
                "tools": list(tools),
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def get(self, key: str) -> dict[str, Any] | None:
        """The first recorded response for this request, or None."""
        recorded = self.entries.get(key)
        if not recorded:
            return None
        return json.loads(json.dumps(recorded[0]["response"]))

    def variants(self, key: str) -> int:
        """How many distinct answers this request has been recorded giving."""
        recorded = self.entries.get(key) or []
        seen = {json.dumps(e["response"], sort_keys=True) for e in recorded}
        return len(seen)

    def put(
        self,
        key: str,
        *,
        agent: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[str],
        response: Mapping[str, Any],
    ) -> None:
        tail = ""
        for message in reversed(list(messages)):
            content = message.get("content")
            if content:
                tail = str(content)
                break
        self.entries.setdefault(key, []).append(
            {
                "agent": agent,
                "tools": list(tools),
                "prompt_tail": tail[-400:],
                "response": json.loads(json.dumps(dict(response))),
            }
        )

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "model": self.model,
            "recorded_with": self.provenance,
            "exchanges": self.entries,
        }
        self.path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return self.path


class ModelClient:
    """One model call, recorded and replayable, with 429 handled honestly.

    The completion seam is injectable — pass any callable taking
    `(model, messages, tools)` and returning an assistant message dict — which is
    how the offline tests exercise the whole live engine without a provider.
    Left unset, it calls `litellm`, and only when `LAB_LIVE_AGENT` is set *and* a
    provider key is in the environment. Both conditions, with the message naming
    whichever one is missing: a run that silently degrades to a replay is a run
    whose provenance is a guess.

    A 429 is retried with exponential backoff and a shared pause, because Azure
    quota is a subscription-level pool rather than a per-key one and one caller
    backing off alone just moves the collision. The retry budget is capped and
    exhaustion raises: a request that never completed is not a slow response, and
    the difference matters to every latency figure downstream.
    """

    #: Env var names checked for a provider key, in order. Documented as names
    #: only — no value from any of these ever reaches a log, a report or a
    #: cassette.
    KEY_ENV_VARS: tuple[str, ...] = (
        "AZURE_OPENAI_API_KEY",
        "AZURE_API_KEY",
        "OPENAI_API_KEY",
        "LAB_KEY",
    )
    BASE_ENV_VARS: tuple[str, ...] = ("AZURE_OPENAI_ENDPOINT", "AZURE_API_BASE")
    VERSION_ENV_VARS: tuple[str, ...] = (
        "AZURE_OPENAI_API_VERSION",
        "AZURE_API_VERSION",
    )

    #: Shared across every client in the process: one 429 pauses all of them.
    _pause_until: float = 0.0

    def __init__(
        self,
        *,
        cassette: str | Path,
        model: str | None = None,
        completion: Callable[..., Mapping[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 400,
        live_env_var: str = LIVE_AGENT_ENV_VAR,
        max_retries: int = 5,
        sleep: Callable[[float], None] | None = None,
        replay: bool = True,
    ) -> None:
        #: Explicit argument, then the environment, then whatever wrote the
        #: cassette, then the fallback. The cassette comes before the fallback so
        #: that replaying a committed fixture needs no environment at all, and
        #: after the environment so that `LAB_AGENT_MODEL` can deliberately
        #: point a re-recording at a different model.
        self.model = (
            model
            or os.environ.get(MODEL_ENV_VAR)
            or SessionCassette.recorded_model(cassette)
            or _DEFAULT_MODEL
        )
        self.cassette = SessionCassette.load(cassette, model=self.model)
        self._completion = completion
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.live_env_var = live_env_var
        self.max_retries = max_retries
        self._sleep = sleep if sleep is not None else time.sleep
        #: False to call the provider even for a request already in the cassette.
        #: The only way to measure how much a model moves: with replay on, the
        #: second repeat of a scenario reads the first repeat's answers back and
        #: pass^k would report a stability it never tested.
        self.replay = replay
        self.cassette.provenance = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "replayed_during_recording": replay,
        }
        self.recorded = 0
        self.replayed = 0
        self.retries = 0

    # ------------------------------------------------------------------ gating

    @property
    def provider_key_present(self) -> bool:
        return any(os.environ.get(name) for name in self.KEY_ENV_VARS)

    @property
    def live_enabled(self) -> bool:
        """True when a call may actually be made."""
        if self._completion is not None:
            return True
        return bool(os.environ.get(self.live_env_var)) and self.provider_key_present

    def refusal(self) -> str | None:
        """Why a live call is not permitted, in a sentence, or None."""
        if self._completion is not None:
            return None
        if not os.environ.get(self.live_env_var):
            return (
                f"{self.live_env_var} is not set, so no model call will be made. "
                "Set it to 1, with a provider key in the environment, to run "
                "against a live model."
            )
        if not self.provider_key_present:
            return (
                f"{self.live_env_var} is set but no provider key is in the "
                f"environment (looked for {', '.join(self.KEY_ENV_VARS)}). "
                "Refusing to pretend a replay was a live run."
            )
        return None

    def require_live(self) -> None:
        """Raise unless a live call is permitted. Call this before spending."""
        reason = self.refusal()
        if reason is not None:
            raise NotLiveError(reason)

    # -------------------------------------------------------------------- call

    def chat(
        self,
        *,
        agent: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """One assistant message, from the cassette if it is there."""
        names = [t["function"]["name"] for t in tools]
        key = SessionCassette.key(
            model=self.model, agent=agent, messages=messages, tools=names
        )
        cached = self.cassette.get(key) if self.replay else None
        if cached is not None:
            self.replayed += 1
            return cached
        if not self.live_enabled:
            raise MissingExchangeError(
                f"no recorded exchange for {agent} in {self.cassette.path} "
                f"({len(self.cassette.entries)} recorded), and {self.refusal()} "
                "Drive this scenario with ScriptedBackend — every offline test in "
                "this repository does — or record the cassette live."
            )
        response = self._complete(agent=agent, messages=messages, tools=tools)
        self.cassette.put(
            key, agent=agent, messages=messages, tools=names, response=response
        )
        self.recorded += 1
        return response

    def save(self) -> Path | None:
        """Write the cassette if this run recorded anything new."""
        return self.cassette.save() if self.recorded else None

    # ------------------------------------------------------------------ private

    def _complete(
        self,
        *,
        agent: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if self._completion is not None:
            return dict(
                self._completion(
                    model=self.model,
                    messages=[dict(m) for m in messages],
                    tools=[dict(t) for t in tools],
                )
            )
        from litellm import completion  # imported lazily, on purpose

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(m) for m in messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = [dict(t) for t in tools]
            kwargs["tool_choice"] = "auto"
        for name in self.BASE_ENV_VARS:
            if os.environ.get(name):
                kwargs["api_base"] = os.environ[name]
                break
        for name in self.VERSION_ENV_VARS:
            if os.environ.get(name):
                kwargs["api_version"] = os.environ[name]
                break

        attempt = 0
        while True:
            wait = ModelClient._pause_until - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            try:
                response = completion(**kwargs)
                return _assistant_message(response)
            except Exception as exc:  # provider errors are opaque by design
                if attempt >= self.max_retries or not _is_rate_limit(exc):
                    raise
                delay = min(60.0, 2.0**attempt)
                # Shared, so every client in the process waits out one 429
                # rather than each discovering it in turn.
                ModelClient._pause_until = time.monotonic() + delay
                self.retries += 1
                attempt += 1


def _is_rate_limit(exc: BaseException) -> bool:
    """Is this the provider saying "too fast" rather than "no"?"""
    if type(exc).__name__ in ("RateLimitError", "Timeout", "APIConnectionError"):
        return True
    status = getattr(exc, "status_code", None)
    return status in (408, 429, 500, 502, 503, 504)


def _assistant_message(response: Any) -> dict[str, Any]:
    """A provider response, flattened to the plain dict the cassette stores.

    Plain JSON rather than the provider's own object because a fixture that only
    a particular client library version can read is not a fixture.
    """
    choice = response["choices"][0]["message"]
    content = choice.get("content") if isinstance(choice, Mapping) else choice.content
    raw_calls = (
        choice.get("tool_calls") if isinstance(choice, Mapping) else choice.tool_calls
    ) or []
    calls: list[dict[str, Any]] = []
    for index, call in enumerate(raw_calls):
        if isinstance(call, Mapping):
            function = call.get("function") or {}
            identifier = call.get("id")
            name = function.get("name")
            arguments = function.get("arguments")
        else:
            function = call.function
            identifier = getattr(call, "id", None)
            name = function.name
            arguments = function.arguments
        calls.append(
            {
                "id": str(identifier or f"call-{index + 1}"),
                "type": "function",
                "function": {"name": str(name), "arguments": arguments or "{}"},
            }
        )
    message: dict[str, Any] = {"role": "assistant", "content": content or ""}
    if calls:
        message["tool_calls"] = calls
    return message


class LLMEngine:
    """One conversation, decided by a model. Satisfies `Engine`.

    Per-conversation state, mirroring `Orchestrator` field for field where it
    matters: a `Session` whose `record` is projected destructively on every
    handoff, one `Toolbox` over one restaurant, and — the one thing that has no
    scripted counterpart — a message history *per desk*, created when that desk
    takes the turn and discarded when it loses it.

    That last point is the architecture, not an optimisation. A specialist here
    is briefed and does not share the orchestrator's memory, so it cannot be
    handed the raw transcript: a desk that could read back over the whole call
    would recover every fact the projection dropped, and the narrow-brief
    architecture this system is built on would be decorative. Each desk sees its
    remit, its brief, and the conversation since it took the turn.
    """

    def __init__(
        self,
        *,
        store: Restaurant,
        client: ModelClient,
        max_tool_steps: int = 4,
    ) -> None:
        self.store = store
        self.toolbox = Toolbox(store=store)
        self.session = Session()
        self.client = client
        self.max_tool_steps = max_tool_steps
        #: Per-desk message history, since that desk was activated.
        self.histories: dict[str, list[dict[str, Any]]] = {}
        #: Diagnostics, live-only. Each one is a thing the model did that the
        #: deterministic build cannot do, and each is reported rather than
        #: smoothed over. None of them reach the trace — see the notes on
        #: `blocked_calls` in particular.
        self.blocked_calls: list[tuple[str, str]] = []
        self.truncated_turns: list[int] = []
        self.silent_turns: list[int] = []
        self.model_calls = 0

    # ------------------------------------------------------------------ a turn

    def turn(self, utterance: str) -> Turn:
        """Handle one caller utterance and return what the system did.

        Same signature, same return type and same one-handoff-per-turn rule as
        `Orchestrator.turn`, because the driver and every contract downstream are
        entitled to not know which of the two they are looking at.
        """
        session = self.session
        session.turn_index += 1
        text = utterance or ""
        self._absorb(text)

        desk = session.active
        handoff: tuple[str, str, str] | None = None
        tools: list[ToolCall] = []
        spoken = ""
        end_call = False
        transferred = False

        while True:
            self._activate(desk)
            history = self.histories[desk]
            history.append({"role": "user", "content": text})

            step = 0
            while True:
                # Reset per model call: a transfer breaks out of this loop the
                # moment it is granted, so this can only ever be the decision
                # made by the message about to be read.
                target: str | None = None
                message = self._ask(desk)
                history.append(message)
                if message.get("content"):
                    spoken = str(message["content"])
                calls = message.get("tool_calls") or []
                if not calls:
                    break

                for call in calls:
                    name = str(call["function"]["name"])
                    args = _parse_arguments(call["function"]["arguments"])
                    if name in TRANSFER_TOOLS:
                        if transferred or TRANSFER_TOOLS[name] == desk:
                            history.append(
                                self._tool_reply(
                                    call,
                                    {
                                        "transferred": False,
                                        "reason": (
                                            "you have already handed this caller "
                                            "over once on this turn; answer them "
                                            "yourself"
                                            if transferred
                                            else "you are that desk"
                                        ),
                                    },
                                )
                            )
                            continue
                        target = TRANSFER_TOOLS[name]
                        history.append(
                            self._tool_reply(call, {"transferred": True, "to": target})
                        )
                        continue
                    if name == END_CALL_TOOL:
                        end_call = True
                        history.append(self._tool_reply(call, {"ending": True}))
                        continue
                    call_record = self._dispatch(desk, name, args)
                    if call_record is None:
                        history.append(
                            self._tool_reply(
                                call,
                                {
                                    "error": "tool_not_available",
                                    "available": list(SPECS[desk].tools),
                                },
                            )
                        )
                        continue
                    tools.append(call_record)
                    history.append(
                        self._tool_reply(
                            call,
                            call_record.result
                            if call_record.ok
                            else {"error": call_record.error},
                        )
                    )

                if target is not None:
                    handoff = (desk, target, f"caller needs {remit(target)}")
                    session.handoffs.append((desk, target))
                    # The projection is destructive here exactly as it is in
                    # `Orchestrator.turn`: the brief becomes the record.
                    session.record = project(session.record, LIVE_BRIEFS[target])
                    session.active = target
                    self.histories.pop(target, None)
                    desk = target
                    transferred = True
                    break

                step += 1
                if step > self.max_tool_steps:
                    # Out of tool steps. One more call with no tools offered, so
                    # the caller gets a sentence instead of silence, and the turn
                    # is recorded as truncated rather than reported as normal.
                    self.truncated_turns.append(session.turn_index)
                    final = self._ask(desk, offer_tools=False)
                    history.append(final)
                    if final.get("content"):
                        spoken = str(final["content"])
                    break

            if target is None:
                break

        if not spoken.strip():
            # The model spent the whole turn on tool calls and said nothing. A
            # silent turn would end the conversation at the caller's next reply
            # and be reported as a short call rather than as this, so ask once
            # more with no tools on the table, and count it.
            self.silent_turns.append(session.turn_index)
            recovered = self._ask(desk, offer_tools=False)
            self.histories[desk].append(recovered)
            spoken = str(recovered.get("content") or "")

        self._note_question(spoken)
        return Turn(
            agent=desk,
            speech=Speech(
                act="live.close" if end_call else "live.reply",
                text=spoken,
                facts=project(self.session.record, LIVE_BRIEFS[desk]),
            ),
            tools=tools,
            handoff=handoff,
            end_call=end_call,
        )

    # ------------------------------------------------------------------ pieces

    def _ask(self, desk: str, *, offer_tools: bool = True) -> dict[str, Any]:
        tools = self._schemas(desk) if offer_tools else []
        messages = [
            {"role": "system", "content": self._system(desk)},
            *self.histories[desk],
        ]
        self.model_calls += 1
        return self.client.chat(agent=desk, messages=messages, tools=tools)

    def _activate(self, desk: str) -> None:
        """Ensure this desk has a history. A fresh one is a fresh activation."""
        self.histories.setdefault(desk, [])

    def _system(self, desk: str) -> str:
        return f"{LIVE_PROMPTS[desk]}\n\n{self._brief(desk)}"

    def _brief(self, desk: str) -> str:
        """The projection, as the prose the desk is activated with."""
        brief = project(self.session.record, LIVE_BRIEFS[desk])
        lines: list[str] = []
        for field_name in LIVE_BRIEFS[desk]:
            value = brief.get(field_name)
            if value in (None, "", [], ()):
                continue
            if isinstance(value, (list, tuple)):
                value = "; ".join(str(v) for v in value)
            lines.append(f"- {_BRIEF_LABELS.get(field_name, field_name)}: {value}")
        if not lines:
            return (
                "What you have been told about this caller: nothing yet. "
                "Anything you need, you will have to establish from them."
            )
        return "What you have been told about this caller:\n" + "\n".join(lines)

    def _schemas(self, desk: str) -> list[dict[str, Any]]:
        """This desk's allow-list as tool schemas, plus its control functions."""
        schemas: list[dict[str, Any]] = []
        for name in SPECS[desk].tools:
            spec = TOOL_SCHEMAS[name]
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": spec["description"],
                        "parameters": spec["parameters"],
                    },
                }
            )
        for name, target in TRANSFER_TOOLS.items():
            if target == desk:
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": (
                            f"Hand the caller to the desk that handles "
                            f"{remit(target)}."
                        ),
                        "parameters": _TRANSFER_SCHEMA["parameters"],
                    },
                }
            )
        if desk != GREETER:
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": END_CALL_TOOL,
                        "description": (
                            "End the call. Only once the caller has said goodbye "
                            "and nothing they asked for is outstanding."
                        ),
                        "parameters": _TRANSFER_SCHEMA["parameters"],
                    },
                }
            )
        return schemas

    def _dispatch(self, desk: str, name: str, args: Mapping[str, Any]) -> ToolCall | None:
        """Run a real tool, or None if this desk may not.

        The allow-list is checked *here*, before the toolbox, and a refused call
        is counted rather than dispatched. `Toolbox.invoke` raises
        `ToolNotAllowed` on an off-list call because in the deterministic build
        that can only be a wiring defect and should stop the run. A model
        reaching for a colleague's tool is not a wiring defect — it is the model
        being wrong, which is a measurement — so it is refused, reported back to
        the model, and counted in `blocked_calls`. It does not reach the trace,
        because a tool that was never dispatched did not happen; that is a real
        divergence from the scripted trace and it is stated in the run report.
        """
        if name not in SPECS[desk].tools:
            self.blocked_calls.append((desk, name))
            return None
        return self.toolbox.invoke(
            name, args, agent=desk, allowed=SPECS[desk].tools
        )

    @staticmethod
    def _tool_reply(call: Mapping[str, Any], payload: Any) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": str(call["id"]),
            "name": str(call["function"]["name"]),
            "content": json.dumps(payload, default=str),
        }

    def _absorb(self, text: str) -> None:
        """Record everything this utterance supplied.

        The orchestrator's memory, and the only thing a brief is built from. Kept
        deterministic on purpose: the experiment here is "the model decides", and
        putting the record behind a second model call would make an information
        loss impossible to attribute to the boundary that caused it.
        """
        session = self.session
        heard = extract_slots(text, expecting=session.pending_slot)
        for key, value in heard.items():
            if value in (None, ""):
                continue
            session.record[key] = value
            session.asked.add(key)
            if session.pending_slot == key:
                session.pending_slot = None
        clause = note_clause(text)
        if clause:
            session.add_note(clause)
        if Intent.POLICY in intents_in(text):
            session.record["topic"] = policy_topic(text)

    def _note_question(self, spoken: str) -> None:
        """Read back off the wire which slot the desk just asked about."""
        for slot, pattern in _ASK_HINTS:
            if pattern.search(spoken or ""):
                self.session.pending_slot = slot
                self.session.asked.add(slot)
                return


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """A tool call's arguments, however the provider serialised them.

    Malformed JSON is *not* repaired: an empty mapping goes to the tool, the tool
    reports a missing argument, and the failure is visible in the trace as a
    failed call. Guessing at what the model meant would hide a real defect.
    """
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


class LLMBackend:
    """The booking assistant with a model in the decision seat.

    Not a phrasing backend: this one supplies an `Engine`, so `TableMate` runs it
    *instead of* `tablemate.agents.Orchestrator` and the phrasing step falls away
    — the words the caller hears are the model's own.

    One instance is a configuration (a model, a cassette, a retry budget) and is
    meant to be shared across conversations, which is what makes it usable as
    `functools.partial(build_agent, backend=...)` for `lab.simulator.run_pass_k`.
    Per-conversation state lives in the `LLMEngine` it hands out.

        backend = LLMBackend(cassette="fixtures/live_sessions.json")
        agent = build_agent(clock=FakeClock(), backend=backend)

    With no `LAB_LIVE_AGENT` and no key that replays the committed cassette and
    raises `MissingExchangeError` on anything it has not seen. With both, it
    calls the provider and records every exchange; `save()` writes the fixture.
    """

    def __init__(
        self,
        *,
        cassette: str | Path,
        model: str | None = None,
        completion: Callable[..., Mapping[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 400,
        max_tool_steps: int = 4,
        live_env_var: str = LIVE_AGENT_ENV_VAR,
        replay: bool = True,
        client: ModelClient | None = None,
    ) -> None:
        self.client = client if client is not None else ModelClient(
            cassette=cassette,
            model=model,
            completion=completion,
            temperature=temperature,
            max_tokens=max_tokens,
            live_env_var=live_env_var,
            replay=replay,
        )
        self.max_tool_steps = max_tool_steps
        self.engines: list[LLMEngine] = []

    # ------------------------------------------------------------------ facade

    @property
    def model(self) -> str:
        return self.client.model

    @property
    def live_enabled(self) -> bool:
        return self.client.live_enabled

    def require_live(self) -> None:
        """Raise `NotLiveError` unless a live call is permitted."""
        self.client.require_live()

    def engine(self, store: Restaurant) -> LLMEngine:
        """A fresh conversation over `store`. Called by `TableMate`."""
        engine = LLMEngine(
            store=store, client=self.client, max_tool_steps=self.max_tool_steps
        )
        self.engines.append(engine)
        return engine

    def save(self) -> Path | None:
        return self.client.save()

    # ------------------------------------------------------------------ reading

    def diagnostics(self) -> dict[str, Any]:
        """What the model did that the deterministic build cannot do.

        Reported next to any run driven this way, because each entry is a
        divergence between this trace and a scripted one and a reader is entitled
        to see the size of it rather than take "same shape" on trust.
        """
        return {
            "model": self.model,
            "model_calls": sum(e.model_calls for e in self.engines),
            "recorded": self.client.recorded,
            "replayed": self.client.replayed,
            "rate_limit_retries": self.client.retries,
            "blocked_calls": [
                f"{desk}:{tool}"
                for engine in self.engines
                for desk, tool in engine.blocked_calls
            ],
            "truncated_turns": sum(len(e.truncated_turns) for e in self.engines),
            "silent_turns": sum(len(e.silent_turns) for e in self.engines),
        }

    def __repr__(self) -> str:
        return (
            f"LLMBackend(model={self.model!r}, live={self.live_enabled}, "
            f"conversations={len(self.engines)})"
        )


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

    THE BACKEND DECIDES HOW MUCH OF THE SYSTEM IS A MODEL
    ----------------------------------------------------
    One argument, three configurations, and the difference between them is
    exactly one variable each:

        ScriptedBackend()                   code decides, code speaks (default)
        PhrasingBackend(cassette=...)       code decides, a model speaks
        LLMBackend(cassette=...)            a model decides and speaks

    The first two are *phrasing* backends: they rewrite a line
    `tablemate.agents.Orchestrator` has already chosen. The third supplies an
    `Engine` instead, so it runs in place of the orchestrator and there is no
    phrasing step left to do — the words are the model's own. `TableMate` tells
    the two apart by asking the backend for an engine, and everything downstream
    of `__call__` is written once and does not know which it got.
    """

    def __init__(
        self,
        *,
        store: Restaurant | None = None,
        backend: Backend | LLMBackend | None = None,
        clock: Clock | None = None,
        latency: LatencyModel | None = DEFAULT_LATENCY,
    ) -> None:
        self.store = store if store is not None else default_restaurant()
        self.backend: Any = backend if backend is not None else ScriptedBackend()
        engine_for = getattr(self.backend, "engine", None)
        self.orchestrator: Engine = (
            engine_for(self.store)
            if callable(engine_for)
            else Orchestrator(store=self.store)
        )
        #: True when the backend still has words to put on a decided line.
        self.phrases: bool = callable(getattr(self.backend, "phrase", None))
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
        text = (
            self.backend.phrase(turn.speech, agent=turn.agent)
            if self.phrases
            else turn.speech.text
        )
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
    backend: Backend | LLMBackend | None = None,
    clock: Clock | None = None,
    latency: LatencyModel | None = DEFAULT_LATENCY,
    seed: Callable[[Restaurant], None] | None = None,
) -> TableMate:
    """A fresh conversation, with a fresh restaurant.

    Args:
        store: An existing restaurant to talk about. Defaults to a new one.
        backend: Phrasing backend (`ScriptedBackend`, the default, or
            `PhrasingBackend`), or `LLMBackend` to hand the decisions to a model
            as well. One `LLMBackend` may be shared across every conversation a
            factory builds: it holds the configuration, and each conversation
            gets its own engine.
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
