"""Personas and goals — the caller side of the instrument, as data.

WHAT THIS DEMONSTRATES
----------------------
The simulated caller is part of the measuring instrument, not part of the test
data, and an instrument has to be specified before it can be trusted. So a caller
is split into two declared objects rather than one prompt string:

    Persona   how someone speaks   (style, verbosity, cooperativeness, accent)
    Goal      what they want       (intent, the facts they hold, what they will
                                    only say if asked, what "done" means)

Keeping them separate is what makes a scenario matrix possible: the same `Goal`
run against four personas isolates "does this agent handle a terse caller" from
"does this agent handle a party of eight", and a failure lands on one axis
instead of on a blob of prose. Both are pydantic models loadable from YAML, so
scenarios are reviewable in a pull request by someone who does not read Python.

THE DISCLOSURE MODEL IS THE POINT
---------------------------------
`Goal.on_request_only` lists facts the caller holds but will not volunteer. That
one field is what turns a scenario from a script into a probe: an agent that
never asks about a dietary requirement never learns about it, and the resulting
booking is wrong in a way no happy-path transcript exposes. It also makes the
information-loss failures measurable, because the trace records the exact turn on
which a fact was released, so a check can ask whether it survived to the tool
call.

`ask_patterns` is the machinery behind that: per fact, the substrings that count
as the agent asking for it. Deliberately substring matching and not a model call
— the caller's behaviour has to be identical on every run, or a flaky caller gets
reported as a flaky agent.

WHAT `cooperativeness` ACTUALLY DOES
-----------------------------------
A dial that changes nothing is worse than no dial, so its effect is stated and
deterministic: below `RELUCTANT_BELOW`, a gated fact is released only on the
*second* ask (`Goal.asks_required`), modelling the caller who says "sorry, what?"
before answering. At or above it, the first ask is enough. It additionally shapes
the `LLMCaller` system prompt, which is opt-in. There is no randomness anywhere
in this module: `random` is not imported.

OUT OF SCOPE HERE
-----------------
`accent` is a *tag*, not audio: a label such as "en-GB-north" that a voice adapter
can map to a TTS voice when recording caller audio. This module never synthesises
or reads audio, and nothing in v1 consumes the tag beyond writing it into the
trace's `session_start` payload for attribution.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "Verbosity",
    "RELUCTANT_BELOW",
    "Persona",
    "Goal",
    "CallerProfile",
    "load_yaml_mapping",
]

#: How much the caller says per turn. A label rather than a token budget: it is
#: consumed by prompt construction and by scenario naming, and a number here
#: would imply a precision the LLM caller cannot honour.
Verbosity = Literal["terse", "normal", "chatty"]

#: Cooperativeness below this threshold means gated facts take two asks. Named
#: constant rather than a literal buried in a comparison, because it is a
#: published part of the caller's contract: a scenario author needs to know that
#: 0.4 and 0.6 are behaviourally different and 0.6 and 0.9 are not.
RELUCTANT_BELOW: float = 0.5


def load_yaml_mapping(path: str | Path) -> dict[str, Any]:
    """Load a YAML file that must contain a mapping at the top level.

    PyYAML is imported here rather than at module scope, and `safe_load` is used
    rather than `load`, so that a scenario file is data and can never execute
    code. Scenario files are the part of this repo most likely to be contributed
    by someone else.
    """
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - declared dependency
        raise ModuleNotFoundError(
            "reading scenario YAML needs PyYAML; install this package's "
            "dependencies with `pip install -e \".[dev]\"`"
        ) from exc

    source = Path(path)
    loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
    if loaded is None:
        raise ValueError(f"{source}: file is empty")
    if not isinstance(loaded, dict):
        raise ValueError(
            f"{source}: expected a mapping at the top level, got {type(loaded).__name__}"
        )
    return loaded


class Persona(BaseModel):
    """How a caller speaks. Never what they want — that is `Goal`.

    Splitting the two is what lets one persona be reused across every scenario in
    a suite, so that "the terse caller fails" is a statement about the agent's
    handling of terse callers rather than about one conversation.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="Short identifier, e.g. 'brisk_regular'.")
    style: str = Field(
        min_length=1,
        description=(
            "Free text describing the speaking style, in the second person. Goes "
            "verbatim into the LLM caller's system prompt."
        ),
    )
    verbosity: Verbosity = "normal"
    cooperativeness: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "1.0 answers every question on the first ask; below RELUCTANT_BELOW "
            "the caller needs a second ask before releasing a gated fact."
        ),
    )
    accent: str | None = Field(
        default=None,
        description=(
            "A tag such as 'en-GB-north' for a voice adapter to map to a TTS "
            "voice. A label only: this module never touches audio."
        ),
    )
    notes: str | None = Field(
        default=None, description="Why this persona exists; for humans reading the suite."
    )

    @property
    def is_reluctant(self) -> bool:
        """True when a gated fact takes more than one ask to release."""
        return self.cooperativeness < RELUCTANT_BELOW

    @property
    def asks_required(self) -> int:
        """How many asks it takes to get a gated fact out of this persona."""
        return 2 if self.is_reluctant else 1

    def prompt_block(self) -> str:
        """The persona as prompt text, for `LLMCaller`.

        Assembled here so that the live and replayed callers are prompted by
        identical text — a recorded fixture is only a faithful stand-in for a live
        run if the prompt that produced it is reconstructible.
        """
        lines = [
            f"You are a caller named {self.name}.",
            f"Speaking style: {self.style}",
            f"Verbosity: {self.verbosity} — "
            + {
                "terse": "answer in as few words as possible, often just the value.",
                "normal": "answer in one short natural sentence.",
                "chatty": "answer in two or three sentences, with small talk.",
            }[self.verbosity],
        ]
        if self.is_reluctant:
            lines.append(
                "You are distracted: the first time you are asked for a detail you "
                "have not already given, ask them to repeat the question instead of "
                "answering it."
            )
        else:
            lines.append("You answer direct questions straight away.")
        if self.accent:
            lines.append(f"Accent tag (for voice synthesis only): {self.accent}")
        return "\n".join(lines)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Persona":
        """Load a persona from a YAML file containing its fields at the top level."""
        return cls.model_validate(load_yaml_mapping(path))


class Goal(BaseModel):
    """What the caller wants, and what they know.

    `facts` is everything the caller could say. `on_request_only` names the subset
    they will not volunteer, which is the field that makes a scenario a probe
    rather than a script: whether the agent ever asks is the thing under test.
    """

    model_config = ConfigDict(extra="forbid")

    intent: str = Field(
        min_length=1,
        description="One line: what the caller is ringing about, e.g. 'book a table'.",
    )
    facts: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Everything the caller knows, as string values. Strings because these "
            "are things a person says out loud; type coercion is the agent's job "
            "and getting it wrong is a finding."
        ),
    )
    on_request_only: list[str] = Field(
        default_factory=list,
        description="Fact keys the caller holds back until asked. Must exist in `facts`.",
    )
    ask_patterns: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Per fact key, case-insensitive substrings that count as the agent "
            "asking for it. Substrings, not a model call: the caller's behaviour "
            "must be byte-identical on every run."
        ),
    )
    reply_templates: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Per fact key, a format string containing `{value}` used when the "
            "caller answers. Defaults to the bare value."
        ),
    )
    success_criteria: list[str] = Field(
        default_factory=list,
        description=(
            "Human-readable statements of what a successful call looks like. Not "
            "executed here — `lab.checks` owns assertions. These exist so a "
            "reader can tell whether the checks actually cover the goal."
        ),
    )

    @model_validator(mode="after")
    def _validate_keys(self) -> "Goal":
        """Reject references to facts that do not exist.

        A typo in `on_request_only` would otherwise create a gated fact that can
        never be asked for, and the scenario would pass for the wrong reason —
        the exact class of silent-green failure this repo exists to prevent.
        """
        unknown_gated = [k for k in self.on_request_only if k not in self.facts]
        if unknown_gated:
            raise ValueError(
                f"on_request_only names facts that do not exist: {sorted(unknown_gated)}; "
                f"known facts: {sorted(self.facts)}"
            )
        unknown_patterns = [k for k in self.ask_patterns if k not in self.facts]
        if unknown_patterns:
            raise ValueError(
                f"ask_patterns names facts that do not exist: {sorted(unknown_patterns)}; "
                f"known facts: {sorted(self.facts)}"
            )
        unknown_templates = [k for k in self.reply_templates if k not in self.facts]
        if unknown_templates:
            raise ValueError(
                f"reply_templates names facts that do not exist: {sorted(unknown_templates)}"
            )
        bad_templates = [
            k for k, tpl in self.reply_templates.items() if "{value}" not in tpl
        ]
        if bad_templates:
            raise ValueError(
                f"reply_templates must contain '{{value}}': {sorted(bad_templates)}"
            )
        duplicates = [k for k in self.on_request_only if self.on_request_only.count(k) > 1]
        if duplicates:
            raise ValueError(f"on_request_only contains duplicates: {sorted(set(duplicates))}")
        return self

    # ------------------------------------------------------------- disclosure

    def gated_keys(self) -> list[str]:
        """Fact keys the caller will only give when asked, in declaration order."""
        return list(self.on_request_only)

    def volunteered_keys(self) -> list[str]:
        """Fact keys the caller offers unprompted — everything not gated."""
        gated = set(self.on_request_only)
        return [k for k in self.facts if k not in gated]

    def fact(self, key: str) -> str:
        """The value of one fact. `KeyError` naming the known keys if absent."""
        try:
            return self.facts[key]
        except KeyError:
            raise KeyError(
                f"no such fact {key!r}; the caller knows: {sorted(self.facts)}"
            ) from None

    def is_asked_for(self, key: str, text: str) -> bool:
        """Does `text` ask for fact `key`, by this goal's declared patterns?

        False when the fact has no patterns: silence is the honest answer for an
        undeclared trigger, and inventing one (matching on the key name, say)
        would make the caller's behaviour depend on how the fact was spelled.
        """
        patterns = self.ask_patterns.get(key, [])
        lowered = text.lower()
        return any(p.lower() in lowered for p in patterns)

    def asked_keys(self, text: str, *, among: Iterable[str] | None = None) -> list[str]:
        """Every fact key `text` asks for, in declaration order.

        `among` restricts the search — the caller passes its gated keys, because a
        fact already volunteered does not need releasing again.
        """
        candidates = list(among) if among is not None else list(self.facts)
        return [k for k in candidates if self.is_asked_for(k, text)]

    def spoken(self, key: str) -> str:
        """How the caller says fact `key` out loud."""
        return self.reply_templates.get(key, "{value}").format(value=self.fact(key))

    def summary(self) -> str:
        """The goal as prompt text, with the disclosure rule made explicit."""
        lines = [f"Your goal: {self.intent}."]
        volunteered = self.volunteered_keys()
        if volunteered:
            lines.append("State these up front if they are relevant:")
            lines.extend(f"  - {k}: {self.facts[k]}" for k in volunteered)
        if self.on_request_only:
            lines.append(
                "You also know the following, but you must NOT mention any of it "
                "unless you are asked for it directly:"
            )
            lines.extend(f"  - {k}: {self.facts[k]}" for k in self.on_request_only)
        if self.success_criteria:
            lines.append("You are finished when: " + "; ".join(self.success_criteria))
        return "\n".join(lines)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Goal":
        """Load a goal from a YAML file containing its fields at the top level."""
        return cls.model_validate(load_yaml_mapping(path))


class CallerProfile(BaseModel):
    """One simulated caller: a persona plus the goal they are calling about.

    The pair is the unit a scenario names, and the unit written into the trace's
    `session_start` payload — so a trace on disk records which caller produced
    it without anyone having to keep a run log alongside the fixtures.
    """

    model_config = ConfigDict(extra="forbid")

    persona: Persona
    goal: Goal
    scenario_id: str | None = Field(
        default=None,
        description=(
            "Optional scenario identifier when the profile and the scenario are "
            "declared in one file. `run_scenario` prefers its own argument."
        ),
    )

    @property
    def asks_required(self) -> int:
        """Asks needed before a gated fact is released — read from the persona."""
        return self.persona.asks_required

    def system_prompt(self) -> str:
        """The full LLM-caller system prompt: persona, goal, and the end sentinel."""
        return "\n\n".join(
            [
                self.persona.prompt_block(),
                self.goal.summary(),
                (
                    "You are the CALLER, never the assistant. Say only what the "
                    "caller would say — no narration, no stage directions, no "
                    "quotation marks. When your goal is met or it is clear it "
                    f"cannot be, reply with exactly {END_OF_CALL}."
                ),
            ]
        )

    def trace_metadata(self) -> dict[str, Any]:
        """Caller attribution for the `session_start` payload.

        Facts are recorded as keys only, plus which were gated. The values are
        the scenario's ground truth, and a trace is an artifact that gets pasted
        into bug reports; a fact the agent was supposed to have to *ask* for
        should not be sitting in the file next to the transcript.
        """
        return {
            "persona": self.persona.name,
            "persona_verbosity": self.persona.verbosity,
            "persona_cooperativeness": self.persona.cooperativeness,
            "persona_accent": self.persona.accent,
            "goal_intent": self.goal.intent,
            "goal_fact_keys": sorted(self.goal.facts),
            "goal_gated_keys": list(self.goal.on_request_only),
        }

    @classmethod
    def from_yaml(cls, path: str | Path) -> "CallerProfile":
        """Load a profile from YAML with `persona:` and `goal:` sections.

        A single file per caller, because a persona and a goal that live in
        separate files get mismatched by exactly the kind of edit that looks
        harmless in review.
        """
        mapping = load_yaml_mapping(path)
        return cls.model_validate(mapping)


#: What the LLM caller says when it considers the call over. A sentinel rather
#: than sentiment analysis of "goodbye": the driver's stopping condition must not
#: itself be a fuzzy judgement, or a hang-up becomes a source of flakiness.
END_OF_CALL: str = "[END OF CALL]"

#: Matches the sentinel however the model cases or pads it.
END_OF_CALL_RE = re.compile(r"\[\s*end\s+of\s+call\s*\]", re.IGNORECASE)

__all__ += ["END_OF_CALL", "END_OF_CALL_RE"]
