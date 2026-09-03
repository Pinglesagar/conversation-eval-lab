"""The live path: a real model as the trainee, and a real model as the customer.

WHAT THIS MODULE IS FOR
-----------------------
Everything else in this package is deterministic, and that was the right way to
build it: a scored session has to be reproducible before a claim about the scorer
means anything. But a coaching product's trainee is a person and its customer is
a language model, and a scorer that has only ever graded hand-written scripts has
never been shown the input it will actually receive. This module supplies that
input, behind two opt-in switches, and records it so the recording becomes the
fixture everyone else replays.

    LAB_LIVE_TRAINEE=1    the adviser under test is a model
    LAB_LIVE_CUSTOMER=1   the customer's words come from a model

The switches are independent, which is not tidiness. A live trainee against a
scripted customer is the ablation that says whether a finding came from the
trainee's behaviour or from the customer's phrasing, and an instrument you cannot
hold still one half at a time is an instrument that cannot localise anything.

THE CARDINAL RULE STILL HOLDS
-----------------------------
With every environment variable unset, `pytest` on a fresh clone must pass. So:

*   nothing here is imported by `roleplay.runtime`, `roleplay.demo` or any
    default path — the dependency runs `live -> runtime`, never back;
*   `litellm` is imported inside the request method, so a clone with no provider
    SDK can still import, replay and test this module;
*   a cassette miss with the switch off **raises**, naming the switch. It never
    degrades to a scripted turn. A run that silently stopped being live is a run
    whose provenance is a guess, and every number derived from it is unfalsifiable.

WHAT THE CUSTOMER IS, AND WHAT IT IS NOT ALLOWED TO BE
------------------------------------------------------
The simulated customer is the product feature here, so it gets the persona
machinery `lab` already has rather than a second, parallel one: `caller_profile`
maps a `CustomerProfile` onto `lab.simulator.Persona` and `lab.simulator.Goal`,
and the customer's system prompt is `CallerProfile.system_prompt()` — the same
persona block, the same goal block with its gated facts, the same `CALLER_RULES`
that keep a caller from narrating or looping. Its hidden concerns are `Goal`'s
`on_request_only` facts, which is exactly what that field is for.

What the model is *not* allowed to be is the customer's decision-maker. The move
is chosen by `CustomerPersona.respond` — this concern surfaces now, that objection
is raised now, that one is pressed again — and `LiveCustomerVoice` is handed the
move and asked only for words. Two consequences, and both are the reason the
domain still works:

1.  **A trainee who never runs discovery can still fail.** A prompt can ask a
    model not to volunteer its needs; it cannot guarantee it. The state machine
    guarantees it. Every live session in this pack was checked for the opposite
    failure too — `LiveCustomerVoice.leaks` counts the times the customer's words
    mentioned a concern the machine had not released, and the count travels in the
    trace.
2.  **Every existing contract reads a live session unchanged.** The trace shape,
    the tool names, the concern and objection ledgers are produced by the same
    code as before. Only the words differ.

COMPETENCE IS A REAL DIAL
-------------------------
`weak`, `competent`, `exemplary`. It is a dial and not a label because the point
is to give the scorer a genuine spread to grade: a scorer that returns much the
same number for a session with no discovery and no disclosures as for a
by-the-book one is exposed by the spread, and cannot be exposed without it. The
briefs are written as *sales behaviour*, never as rubric criteria — a brief that
said "score well on objection handling" would be teaching to the test, and the
resulting score would measure the prompt.

The one place the brief touches the rubric on purpose is the approved disclosure
wording, which `exemplary` is given and the other two are not. That is what a
compliant firm does — the wording is in the handbook — and it is what makes the
disclosure criterion discriminate instead of reading zero everywhere. See
`roleplay.register.compliance_brief`.

RECORD AND REPLAY, KEYED BY WHAT ACTUALLY DETERMINES THE SESSION
----------------------------------------------------------------
A cassette is keyed by (scenario, persona, prompt digest, model label,
competence) — every one of which changes what the model would say, and none of
which is visible in a filename otherwise. Four of the five are in the name, so a
prompt edit does not corrupt an old recording, it *misses* it, and a miss offline
is a refusal rather than a wrong answer. Inside, each turn additionally carries a
sha256 of the exact message list it was generated from, so turn five replays only
into the conversation turn five was recorded in: a stale cassette raises instead
of answering a question nobody asked.

CONSOLIDATION NOTE
------------------
`tablemate.runtime.ModelClient` is a sibling implementation of the same
record/replay/backoff discipline for a different domain, and `lab.simulator.
LLMCaller` is a third for the caller side. Three homes for one idea is a debt, and
the honest place to say so is here rather than in a commit message: the shape they
share — env-gated live calls, a keyed cassette, a context digest, shared 429
backoff — belongs in `lab` once a fourth domain needs it and the right abstraction
is visible from three examples rather than guessed from one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.simulator.persona import (
    END_OF_CALL_RE,
    CallerProfile,
    Goal,
    Persona,
)

from roleplay.persona import (
    CustomerPersona,
    CustomerProfile,
    PersonaTurn,
    load_profiles,
)
from roleplay.register import (
    ShadowComparison,
    compliance_brief,
    normalise,
    required_codes,
)
from roleplay.runtime import (
    CORPUS_ROOT,
    DEFAULT_MAX_TURNS,
    RoleplayCoach,
    RoleplayResult,
    Trainee,
    stop_reason_of,
)
from roleplay.scorer import PASS_TOTAL, RubricScorer

__all__ = [
    "LIVE_TRAINEE_ENV_VAR",
    "LIVE_CUSTOMER_ENV_VAR",
    "TRAINEE_MODEL_ENV_VAR",
    "CUSTOMER_MODEL_ENV_VAR",
    "MODEL_LABEL_ENV_VAR",
    "CASSETTE_ROOT",
    "Competence",
    "COMPETENCES",
    "TRAINEE_BRIEFS",
    "SESSION_END",
    "NotLiveError",
    "StaleCassetteError",
    "MissingTurnError",
    "ContentFilterError",
    "SessionKey",
    "SessionCassette",
    "ModelSpeaker",
    "LiveTrainee",
    "LiveCustomerVoice",
    "caller_profile",
    "trainee_prompt",
    "customer_prompt",
    "run_live_session",
    "LIVE_MATRIX",
    "main",
    # The trainee seam: how an external agent is plugged in as the adviser.
    "TRAINEE_FACTORY_ENV_VAR",
    "TraineeFactoryError",
    "TraineeContext",
    "model_trainee",
    "resolve_trainee_factory",
    "build_trainee",
]

# --------------------------------------------------------------------------- #
# Switches and routes. Names only — no value from any of these is ever logged,
# printed, or written into a cassette.
# --------------------------------------------------------------------------- #

#: Opt-in for a model-driven trainee. Absent, `LiveTrainee` replays and a miss raises.
LIVE_TRAINEE_ENV_VAR: str = "LAB_LIVE_TRAINEE"

#: Opt-in for a model-voiced customer. Independent of the trainee switch.
LIVE_CUSTOMER_ENV_VAR: str = "LAB_LIVE_CUSTOMER"

#: litellm routes, e.g. `azure/<deployment>`. No model id is hardcoded here.
TRAINEE_MODEL_ENV_VAR: str = "LAB_TRAINEE_MODEL"
CUSTOMER_MODEL_ENV_VAR: str = "LAB_CUSTOMER_MODEL"

#: What the *fixture* should say the turns came from. A route can name a private
#: deployment inside somebody's cloud account and a committed fixture is public,
#: so the label is the model family a reader needs and the route is infrastructure
#: that has no business in git.
MODEL_LABEL_ENV_VAR: str = "LAB_LIVE_MODEL_LABEL"

#: A dotted path — `package.module:callable` — to a factory that builds the
#: trainee under test. The callable receives one `TraineeContext` and returns
#: anything satisfying `roleplay.runtime.Trainee` (two methods: `open()` and
#: `reply(customer_turn)`). Unset, `model_trainee` is used: the model-backed
#: `LiveTrainee` every committed cassette was recorded with, so nothing about an
#: existing run changes. `--trainee-factory` on the two runners overrides it.
TRAINEE_FACTORY_ENV_VAR: str = "LAB_TRAINEE_FACTORY"

#: Env vars checked for a provider key, in order. Presence only is ever read.
KEY_ENV_VARS: tuple[str, ...] = (
    "AZURE_OPENAI_API_KEY",
    "AZURE_API_KEY",
    "OPENAI_API_KEY",
    "LAB_KEY",
)
BASE_ENV_VARS: tuple[str, ...] = ("AZURE_OPENAI_ENDPOINT", "AZURE_API_BASE")
VERSION_ENV_VARS: tuple[str, ...] = ("AZURE_OPENAI_API_VERSION", "AZURE_API_VERSION")

#: Where committed roleplay cassettes live.
CASSETTE_ROOT: Path = Path(__file__).resolve().parent.parent / "fixtures" / "roleplay_live"

#: How a trainee says the meeting is over. A sentinel rather than sentiment
#: analysis of "thanks for your time", for the reason `lab.simulator` uses one: a
#: stopping condition that is itself a fuzzy judgement is a source of flakiness.
SESSION_END: str = "[END OF SESSION]"

#: What a refused turn is recorded as. A sentinel string rather than a null so
#: that the cassette's shape is unchanged and a reader of the JSON can see, in the
#: transcript, exactly where the provider stopped the conversation.
_FILTERED: str = "[CONTENT FILTERED BY PROVIDER]"

#: Output budgets, per role, in tokens. The trainee's is the larger of the two
#: because a truncated adviser turn is not a shorter turn, it is a *changed* one:
#: the first recording made for this pack cut a turn at "Personally," — the exact
#: word a personal recommendation begins with — and a budget that decides whether
#: the harness can see a compliance breach is a measurement setting, not a
#: performance one.
TRAINEE_MAX_TOKENS: int = 420
CUSTOMER_MAX_TOKENS: int = 200

#: Matches the session sentinel however the model cases or pads it.
_SESSION_END_RE = re.compile(r"\[\s*end\s+of\s+session\s*\]", re.IGNORECASE)

Competence = Literal["weak", "competent", "exemplary"]

#: In ascending order, and reports depend on that order being this order.
COMPETENCES: tuple[Competence, ...] = ("weak", "competent", "exemplary")

#: Rate-limit backoff, shared across every speaker in the process: one 429
#: pauses all of them, because Azure quota is a subscription-level pool and a
#: single caller backing off alone just moves the collision.
RATE_LIMIT_RETRIES: int = 5
RATE_LIMIT_BASE_DELAY_S: float = 2.0


class NotLiveError(RuntimeError):
    """A live model call was needed and the environment does not permit one."""


class StaleCassetteError(RuntimeError):
    """A recorded turn exists but was recorded in a different conversation."""


class MissingTurnError(RuntimeError):
    """A recorded turn was needed and the cassette does not hold it."""


class TraineeFactoryError(ValueError):
    """The trainee factory could not be resolved, or built something that is not a trainee.

    Raised with the dotted path in the message, because the only two things a
    reader needs are *which* setting was wrong and *what* it should have looked
    like — a `ModuleNotFoundError` three frames deep names neither.
    """


class ContentFilterError(RuntimeError):
    """The provider's content filter refused the request.

    Its own class, not a generic provider error, because it is a *result* rather
    than an outage: it is deterministic, retrying it is pure waste, and a coaching
    product will meet it in production. See `_is_content_filtered` for how it is
    recognised and `SessionCassette` for how it is recorded, so that a filtered
    session replays as a filtered session offline.
    """


# --------------------------------------------------------------------------- #
# The competence dial
# --------------------------------------------------------------------------- #

#: What each competence level is *told to do*, in the language of selling.
#:
#: Read these as job descriptions, not as rubric rows. `weak` is not told to skip
#: discovery because the rubric scores discovery; it is told to lead with the
#: product because that is what an untrained salesperson does, and skipping
#: discovery is the consequence. The distinction matters: a brief written from the
#: rubric produces a session engineered to score a certain way, and grading it
#: measures the prompt author rather than the grader.
TRAINEE_BRIEFS: dict[Competence, str] = {
    "weak": (
        "You are new to advising and you are behind on target this month. You are "
        "enthusiastic about the fund and you lead with it: you talk about "
        "performance, you reassure, and you try to get to a decision quickly. You "
        "do not really ask what the customer needs — you assume you already know, "
        "because the fund is a good fund. You are comfortable telling the customer "
        "what you would do in their position, and you push for a commitment before "
        "the meeting ends."
    ),
    "competent": (
        "You are a solid adviser two years in. You ask a few questions about what "
        "the customer wants before you talk about any product, and you listen to "
        "the answers. You mention risk and cost when the product comes up because "
        "you know you are supposed to, in your own words. You are careful not to "
        "tell the customer what to do — you present options and let them choose — "
        "and you are willing to leave the meeting without a decision."
    ),
    "exemplary": (
        "You are an experienced adviser and your firm audits your meetings. You do "
        "not mention a product until you understand the customer's objectives, "
        "their timescale, what money they can and cannot tie up, and how they feel "
        "about losing some of it. You take each concern and each objection "
        "seriously and answer it directly rather than moving on. You give the "
        "mandatory disclosures in your firm's approved wording. You never tell a "
        "customer what to do with their money without a completed suitability "
        "assessment; you set out options and the trade-offs and let them decide. "
        "You summarise and agree next steps rather than pushing for a signature."
    ),
}

#: The rules that hold whatever the competence level, and they are about being an
#: instrument rather than about being good at the job. Note what is absent: no
#: instruction to disclose, to probe, or to avoid a recommendation. Those are the
#: behaviours under test, and putting any of them here would apply them to every
#: level and flatten the dial the module exists to provide.
TRAINEE_RULES: str = (
    "You are the ADVISER and you speak only as the adviser. One turn at a time, "
    "two to four sentences and at most ninety words, no narration, no stage "
    "directions, no bullet points, no quotation marks around your own speech.\n"
    "Never write the customer's lines or imagine their answer.\n"
    "Do not repeat a sentence you have already said; move the meeting forward.\n"
    "When the meeting has reached its natural end — you have agreed next steps, "
    "or the customer has declined, or there is nothing left to say — reply with "
    f"exactly {SESSION_END} and nothing else."
)

#: The customer voice's own rules, appended to `lab`'s `CALLER_RULES`. The first
#: of them neutralises the two clauses `lab` writes for a *telephone caller* —
#: how to open, and when to hang up — because in this domain the adviser opens and
#: the adviser closes. Overriding them here rather than editing `lab` is
#: deliberate: the caller rules are shared with another domain, and a prompt block
#: that a second consumer bends to fit is a prompt block that should stay as it is.
#:
#: The fidelity clause is not style guidance. A live voice that embellishes is a
#: stimulus that drifts from the reviewed wording in the profile, and the first
#: live run of this pack found a sharper reason: the customer's paraphrase of the
#: liquidity objection was *refused by the provider's own content filter*, while
#: the profile's reviewed wording of the same objection passed. A voice held close
#: to its direction is both a better-controlled instrument and a less fragile one.
#:
#: The hard one is the last: the voice is given a
#: single move and it may say that move and nothing else. Everything the customer
#: knows is in its prompt, because a persona has to be coherent to be worth
#: talking to, and the only thing standing between the model and blurting the lot
#: is this instruction plus the leak counter that measures how well it held.
VOICE_RULES: str = (
    "You are the CUSTOMER. Speak only as the customer: one turn, one to three "
    "sentences, no narration, no stage directions.\n"
    "You are sitting in the adviser's office and the adviser speaks first, so "
    "ignore any instruction about opening the conversation, and never end the "
    "meeting yourself — that is the adviser's decision.\n"
    "A DIRECTION line tells you what this turn must do. Do exactly that, in your "
    "own words, in your own manner.\n"
    "Stay close to the wording the direction gives you: you are saying it in your "
    "own voice, not rewriting it. Plain, ordinary language — a customer's "
    "sentence, not a paraphrase that drifts.\n"
    "Say nothing the direction did not ask for. In particular, never volunteer a "
    "worry, a plan, a sum of money or a family circumstance the direction has not "
    "told you to raise — even if the adviser's question seems to invite it. If the "
    "direction says to acknowledge, you acknowledge and add nothing."
)


# --------------------------------------------------------------------------- #
# Mapping the customer onto lab's persona machinery
# --------------------------------------------------------------------------- #


def caller_profile(profile: CustomerProfile) -> CallerProfile:
    """Express a `CustomerProfile` as a `lab.simulator` persona and goal.

    Reuse rather than a parallel implementation, and the mapping is where the
    reuse is either real or cosmetic, so it is spelled out:

        situation, budget, risk appetite  ->  Goal.facts, ungated
        hidden concerns                   ->  Goal.facts, in on_request_only
        objection bank                    ->  Goal.facts, in on_request_only
        terse / assertive / suspicion     ->  Persona verbosity, style,
                                              cooperativeness

    The concerns land in `on_request_only` because that field means precisely what
    a hidden concern means: the caller holds it and will not say it unless asked.
    `Goal.summary` then renders the "you must NOT mention any of this unless asked"
    block for free, which is the sentence the whole discovery criterion depends on.

    Cooperativeness is derived rather than invented: a suspicious customer is
    below `RELUCTANT_BELOW`, a terse one below the volunteering band, and an
    ordinary one at the top. It is only read by prompt construction here — the
    *behavioural* gating lives in `CustomerPersona`, which is the thing that
    actually holds the line.
    """
    if profile.suspicious:
        cooperativeness = 0.4
    elif profile.terse:
        cooperativeness = 0.6
    else:
        cooperativeness = 0.7

    style_parts = [
        f"You are {profile.display_name}, a retail customer meeting a financial "
        "adviser at your bank."
    ]
    if profile.terse:
        style_parts.append("You give short answers and you do not fill silences.")
    if profile.assertive:
        style_parts.append(
            "You are blunt and you interrupt a sales pitch to challenge it."
        )
    if profile.suspicion >= 0.6:
        style_parts.append(
            "You do not trust that the adviser is on your side, and it shows."
        )
    elif profile.suspicion:
        style_parts.append("You are polite but a little wary.")

    facts: dict[str, str] = {"situation": profile.situation}
    if profile.budget:
        facts["budget"] = profile.budget
    if profile.risk_appetite != "unstated":
        facts["risk_appetite"] = (
            f"how you feel about risk: {profile.risk_appetite}"
        )
    gated: list[str] = []
    for concern in profile.concerns:
        key = f"concern_{concern.key}"
        facts[key] = f"{concern.topic} — {concern.reveal}"
        gated.append(key)
    for objection in profile.objections:
        key = f"objection_{objection.key}"
        facts[key] = f"{objection.topic} — {objection.says}"
        gated.append(key)

    return CallerProfile(
        persona=Persona(
            name=profile.key,
            style=" ".join(style_parts),
            verbosity="terse" if profile.terse else "normal",
            cooperativeness=cooperativeness,
            notes=(
                "Derived from a roleplay.persona.CustomerProfile; the behavioural "
                "gating lives in CustomerPersona, not in this prompt."
            ),
        ),
        goal=Goal(
            intent=(
                "hear what the adviser is proposing for your money and decide "
                "whether you trust it"
            ),
            facts=facts,
            on_request_only=gated,
            success_criteria=[
                "you understand what would happen to your money and what it costs",
                "the things that worry you have been answered rather than deflected",
            ],
        ),
    )


def trainee_prompt(
    *,
    competence: Competence,
    profile: CustomerProfile,
    jurisdiction: str,
    language: str = "en",
) -> str:
    """The adviser's system prompt: role, competence brief, and the instrument rules.

    Assembled from declared data every time rather than stored, because the
    cassette is keyed on this string's digest — a prompt that could not be rebuilt
    from its inputs would make every fixture unfalsifiable.

    The trainee is told the customer's *name* and nothing else about them. It is
    not told the situation, the budget, the risk appetite or a single concern:
    those are what discovery is for, and an adviser handed the answers cannot be
    caught failing to ask. This is the same rule `lab.simulator.Goal.summary`
    applies from the other side of the table.
    """
    if competence not in TRAINEE_BRIEFS:
        raise ValueError(
            f"unknown competence {competence!r}; known: {list(COMPETENCES)}"
        )
    blocks = [
        "You are a retail investment adviser in a face-to-face meeting with a "
        f"customer, {profile.display_name}. You have never met them before and you "
        "know nothing about their circumstances beyond their name.",
        TRAINEE_BRIEFS[competence],
        "The product you have available is a diversified balanced growth fund: "
        "roughly sixty per cent equities and forty per cent bonds, an annual "
        "management charge of 0.68 per cent, no exit penalty, priced daily, and "
        "last year it fell about four per cent.",
        f"You are advising in the {jurisdiction} market"
        + (f", speaking {language}" if language != "en" else "")
        + ".",
    ]
    if competence == "exemplary":
        blocks.append(compliance_brief(jurisdiction, language))
    blocks.append(TRAINEE_RULES)
    return "\n\n".join(blocks)


def customer_prompt(profile: CustomerProfile) -> str:
    """The customer's system prompt: `lab`'s caller prompt plus the voice rules.

    `CallerProfile.system_prompt()` supplies the persona block, the goal block
    with its gated facts, and `CALLER_RULES`. The roleplay-specific part is only
    the direction discipline — this voice is driven turn by turn.
    """
    return "\n\n".join([caller_profile(profile).system_prompt(), VOICE_RULES])


def _strip_sentinel(text: str) -> tuple[str, bool]:
    """Split a reply into what was said and whether it ended the session."""
    body = (text or "").strip()
    if END_OF_CALL_RE.search(body) or _SESSION_END_RE.search(body):
        spoken = _SESSION_END_RE.sub("", END_OF_CALL_RE.sub("", body)).strip()
        return spoken, True
    return body, False



# --------------------------------------------------------------------------- #
# The fixture's identity
# --------------------------------------------------------------------------- #


class SessionKey(BaseModel):
    """What a recorded live session is *of*.

    Five fields, and each one changes what the models would have said:

    *   **scenario** — a different meeting;
    *   **persona** — a different customer, with different concerns to withhold;
    *   **prompt digest** — different instructions, so the recorded turns are not
        the turns the current instrument would produce;
    *   **model label** — a different distribution;
    *   **competence** — a different adviser entirely. This is the field that
        makes the dial safe: without it in the key, re-recording at `weak` would
        overwrite the `exemplary` reading of the same scenario, and the spread the
        pack is built to measure would quietly become one number twice.

    Temperature and the turn budget are in the key for the same reason they are in
    `lab.simulator.CassetteKey`: they decide the variance and where the
    conversation stops.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1)
    persona: str = Field(min_length=1)
    competence: str = Field(min_length=1)
    prompt_sha256: str = Field(min_length=64, max_length=64)
    trainee_model: str = Field(min_length=1)
    customer_model: str = Field(min_length=1)
    jurisdiction: str = Field(min_length=1)
    language: str = "en"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    turn_budget: int = Field(default=12, ge=1)

    @classmethod
    def build(
        cls,
        *,
        scenario_id: str,
        profile: CustomerProfile,
        competence: Competence,
        jurisdiction: str,
        language: str,
        trainee_model: str,
        customer_model: str,
        temperature: float,
        turn_budget: int,
    ) -> "SessionKey":
        """Derive the key from the objects that actually determine the recording."""
        prompts = "\n\n=====\n\n".join(
            [
                trainee_prompt(
                    competence=competence,
                    profile=profile,
                    jurisdiction=jurisdiction,
                    language=language,
                ),
                customer_prompt(profile),
            ]
        )
        return cls(
            scenario_id=scenario_id,
            persona=profile.key,
            competence=competence,
            prompt_sha256=hashlib.sha256(prompts.encode("utf-8")).hexdigest(),
            trainee_model=trainee_model,
            customer_model=customer_model,
            jurisdiction=jurisdiction,
            language=language,
            temperature=temperature,
            turn_budget=turn_budget,
        )

    @property
    def prompt_digest12(self) -> str:
        return self.prompt_sha256[:12]

    def filename(self) -> str:
        """`<persona>-<competence>-<prompt12>-b<budget>.json`."""
        return (
            f"{self.persona}-{self.competence}-{self.prompt_digest12}"
            f"-b{self.turn_budget}.json"
        )

    def path_in(self, root: str | Path = CASSETTE_ROOT) -> Path:
        """`<root>/<scenario_id>/<filename>` — one directory per scenario."""
        return Path(root) / self.scenario_id / self.filename()

    def differences(self, other: "SessionKey") -> list[str]:
        out: list[str] = []
        for name in type(self).model_fields:
            mine, theirs = getattr(self, name), getattr(other, name)
            if mine != theirs:
                out.append(f"{name}: fixture has {theirs!r}, this run wants {mine!r}")
        return out

    def describe(self) -> str:
        return (
            f"{self.scenario_id}: {self.persona} vs a {self.competence} adviser "
            f"({self.trainee_model}) in {self.jurisdiction}, T={self.temperature}, "
            f"prompt {self.prompt_digest12}"
        )


# --------------------------------------------------------------------------- #
# The cassette
# --------------------------------------------------------------------------- #


@dataclass
class SessionCassette:
    """One recorded live session: both speakers, in order, with context digests.

    One file for the pair rather than one per speaker, because a session is one
    interleaved artifact — the trainee's turn three depends on the customer's turn
    two — and two files that could be updated separately are two files that will
    eventually describe different conversations.

    Each entry carries a sha256 of the exact message list that produced it. That
    is what makes replay honest: positional replay would keep working after the
    other speaker's behaviour changed, and the recording would answer a question
    nobody asked while the suite stayed green.
    """

    path: Path
    identity: SessionKey | None = None
    turns: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    dirty: bool = False

    @classmethod
    def load(cls, path: str | Path, *, identity: SessionKey | None = None) -> "SessionCassette":
        source = Path(path)
        if not source.exists():
            return cls(path=source, identity=identity)
        loaded = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or "turns" not in loaded:
            raise ValueError(
                f"{source}: not a roleplay session cassette (expected a mapping "
                "with a 'turns' list)"
            )
        stored = loaded.get("identity")
        if identity is not None:
            if not isinstance(stored, dict):
                raise StaleCassetteError(
                    f"{source}: cassette carries no identity block, so it cannot be "
                    "shown to be a recording of this session. Delete it and "
                    "re-record."
                )
            recorded = SessionKey.model_validate(stored)
            if recorded != identity:
                raise StaleCassetteError(
                    f"{source}: this cassette records a different session.\n  "
                    + "\n  ".join(identity.differences(recorded))
                )
        return cls(
            path=source,
            identity=identity or (SessionKey.model_validate(stored) if stored else None),
            turns=list(loaded["turns"]),
            provenance=dict(loaded.get("recorded_with") or {}),
        )

    # ------------------------------------------------------------------ replay

    def count(self, role: str) -> int:
        return sum(1 for t in self.turns if t.get("role") == role)

    def lookup(self, *, role: str, index: int, digest: str) -> str | None:
        """The recorded utterance for this role's turn `index`, or None.

        Raises rather than returning a different turn's words when the context
        digest disagrees. A fixture that cannot go stale loudly is not a fixture,
        it is a decoy.
        """
        for entry in self.turns:
            if entry.get("role") == role and int(entry.get("index", -1)) == index:
                recorded = entry.get("context_sha256")
                if recorded != digest:
                    raise StaleCassetteError(
                        f"{self.path}: stale at {role} turn {index}. The "
                        "conversation this turn is being replayed into is not the "
                        "one it was recorded in (context sha256 "
                        f"{digest[:12]} vs {str(recorded)[:12]}). Re-record with "
                        f"{LIVE_TRAINEE_ENV_VAR}=1 {LIVE_CUSTOMER_ENV_VAR}=1, or "
                        "delete the file."
                    )
                return str(entry.get("utterance", ""))
        return None

    def append(
        self, *, role: str, index: int, digest: str, utterance: str, note: str | None = None
    ) -> None:
        entry: dict[str, Any] = {
            "role": role,
            "index": index,
            "context_sha256": digest,
            "utterance": utterance,
        }
        if note:
            entry["direction"] = note
        self.turns.append(entry)
        self.dirty = True

    def save(self) -> Path | None:
        """Write the file if this run recorded anything new."""
        if not self.dirty:
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "identity": self.identity.model_dump() if self.identity else None,
            "recorded_with": self.provenance,
            "turns": self.turns,
        }
        self.path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return self.path

    def transcript(self) -> list[tuple[str, str]]:
        """(role, utterance) in recorded order — for reading the fixture by eye."""
        return [(str(t.get("role")), str(t.get("utterance"))) for t in self.turns]


# --------------------------------------------------------------------------- #
# One speaker
# --------------------------------------------------------------------------- #


def _is_content_filtered(exc: BaseException) -> bool:
    """Is this the provider's content filter, rather than a transport failure?

    Duck-typed across SDKs like the rate-limit check, and for the same reason: the
    class name is a litellm detail and a proxy in front of it may surface only a
    400 with the message. Getting this wrong in either direction is expensive — a
    filter retried five times is five refusals paid for, and an outage mistaken
    for a filter is a session silently cut short.
    """
    if "contentpolicyviolation" in type(exc).__name__.casefold():
        return True
    if getattr(exc, "status_code", None) != 400:
        return False
    return "content management policy" in str(exc).casefold()


def _is_rate_limited(exc: BaseException) -> bool:
    """Is this the provider saying "too fast" rather than "no"?

    Duck-typed across SDKs on purpose: litellm normalises most providers to a
    `RateLimitError`, but a proxy in front of one may only ever produce a status
    code, and retrying the wrong exception is how a real failure becomes a slow
    one.
    """
    if type(exc).__name__ in ("RateLimitError", "Timeout", "APIConnectionError"):
        return True
    return getattr(exc, "status_code", None) in (408, 429, 500, 502, 503, 504)


@dataclass
class ModelSpeaker:
    """One role's model calls: replayed if recorded, live if permitted, else raise.

    The `completion` seam is injectable — any callable taking
    `(model, messages, temperature, max_tokens)` and returning the assistant text
    — which is how every offline test in this pack exercises the whole live engine
    with no provider and no key. Left unset it calls `litellm`, and only when the
    role's switch is set *and* a provider key is in the environment. Both
    conditions, with the message naming whichever is missing.
    """

    role: str
    cassette: SessionCassette
    live_env_var: str
    model_env_var: str
    model: str | None = None
    model_label: str = "unspecified-model"
    temperature: float = 0.0
    max_tokens: int = 320
    completion: Callable[..., str] | None = None
    max_retries: int = RATE_LIMIT_RETRIES
    retry_base_s: float = RATE_LIMIT_BASE_DELAY_S
    sleep: Callable[[float], None] | None = None
    recorded: int = 0
    replayed: int = 0
    retries: int = 0
    filtered: int = 0
    #: Turns this speaker has taken in this run. The replay index, and it must not
    #: be derived from the cassette's length: a loaded cassette already holds every
    #: turn, so counting entries would ask for turn n+1 on the first request and
    #: miss every recording in the file. That bug replays as "no recorded turn",
    #: which reads exactly like a fixture that was never made.
    taken: int = 0

    #: Shared across every speaker in the process: one 429 pauses all of them.
    #: A `ClassVar`, so it is genuinely one value and not a per-instance field
    #: that would let each speaker discover the same rate limit separately.
    _pause_until: ClassVar[float] = 0.0

    # ------------------------------------------------------------------ gating

    @property
    def provider_key_present(self) -> bool:
        return any(os.environ.get(name) for name in KEY_ENV_VARS)

    @property
    def live_enabled(self) -> bool:
        if self.completion is not None:
            return True
        return bool(os.environ.get(self.live_env_var)) and self.provider_key_present

    def refusal(self) -> str | None:
        """Why a live call is not permitted, in a sentence, or None."""
        if self.completion is not None:
            return None
        if not os.environ.get(self.live_env_var):
            return (
                f"{self.live_env_var} is not set, so no model call will be made for "
                f"the {self.role}. Set it to 1, with a provider key in the "
                "environment, to record."
            )
        if not self.provider_key_present:
            return (
                f"{self.live_env_var} is set but no provider key is in the "
                f"environment (looked for {', '.join(KEY_ENV_VARS)}). Refusing to "
                "pretend a replay was a live run."
            )
        return None

    def require_live(self) -> None:
        """Raise unless a live call is permitted. Call before spending."""
        reason = self.refusal()
        if reason is not None:
            raise NotLiveError(reason)

    # -------------------------------------------------------------------- call

    def say(self, messages: Sequence[Mapping[str, str]], *, note: str | None = None) -> str:
        """One utterance for this role: from the cassette if it is there."""
        index = self.taken
        self.taken += 1
        digest = hashlib.sha256(
            json.dumps([dict(m) for m in messages], sort_keys=True).encode("utf-8")
        ).hexdigest()
        replayed = self.cassette.lookup(role=self.role, index=index, digest=digest)
        if replayed is not None:
            self.replayed += 1
            if replayed == _FILTERED:
                # A recorded refusal replays as a refusal. A session the provider
                # cut short must reproduce as a session the provider cut short:
                # replaying it as a normal turn would quietly repair a result.
                self.filtered += 1
                raise ContentFilterError(
                    f"the {self.role}'s turn {index} was refused by the provider's "
                    f"content filter when this cassette was recorded "
                    f"({self.cassette.path})"
                )
            return replayed
        if not self.live_enabled:
            raise MissingTurnError(
                f"no recorded {self.role} turn {index} in {self.cassette.path} "
                f"({self.cassette.count(self.role)} recorded), and {self.refusal()} "
                "Drive this session with the scripted path — every offline test in "
                "this repository does — or record the cassette live."
            )
        try:
            text = self._complete(messages)
        except ContentFilterError:
            self.filtered += 1
            self.cassette.append(
                role=self.role,
                index=index,
                digest=digest,
                utterance=_FILTERED,
                note=note,
            )
            self.recorded += 1
            raise
        self.cassette.append(
            role=self.role, index=index, digest=digest, utterance=text, note=note
        )
        self.recorded += 1
        return text

    def _complete(self, messages: Sequence[Mapping[str, str]]) -> str:
        """One utterance, with rate-limit backoff around whichever seam is in use.

        The retry loop wraps the *injected* seam as well as the provider call,
        which is the only reason the backoff is testable offline: a loop that only
        existed on the litellm path would be a rate-limit policy no test in this
        repo could execute, and an untested retry is a retry that storms.
        """
        naps = self.sleep if self.sleep is not None else time.sleep
        delay = self.retry_base_s
        attempt = 0
        while True:
            wait = ModelSpeaker._pause_until - time.monotonic()
            if wait > 0:
                naps(wait)
            try:
                return self._call_once(messages)
            except Exception as exc:  # provider errors are opaque by design
                if _is_content_filtered(exc):
                    # Deterministic: the same request will be refused every time,
                    # so retrying it only spends money to be told again.
                    raise ContentFilterError(str(exc)) from exc
                if attempt >= self.max_retries or not _is_rate_limited(exc):
                    raise
                # Shared, so every speaker waits out one 429 rather than each
                # discovering it in turn.
                # Set the shared pause and let the top of the loop wait it out.
                # Sleeping here as well would spend the delay twice for one 429 —
                # invisible in a passing test, and a doubled recovery time under a
                # real rate limit.
                ModelSpeaker._pause_until = time.monotonic() + delay
                self.retries += 1
                attempt += 1
                delay *= 2

    def _call_once(self, messages: Sequence[Mapping[str, str]]) -> str:
        """A single attempt: the injected seam if there is one, else the provider."""
        if self.completion is not None:
            return str(
                self.completion(
                    model=self.model or self.model_label,
                    messages=[dict(m) for m in messages],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
            ).strip()

        from litellm import completion  # imported lazily, on purpose

        route = self.model or os.environ.get(self.model_env_var) or ""
        if not route:
            raise NotLiveError(
                f"no {self.role} model configured: pass model= or set "
                f"{self.model_env_var} (e.g. {self.model_env_var}=azure/<deployment>). "
                "No model id is hardcoded in this package."
            )
        self.require_live()

        kwargs: dict[str, Any] = {
            "model": route,
            "messages": [dict(m) for m in messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        for name in BASE_ENV_VARS:
            if os.environ.get(name):
                kwargs["api_base"] = os.environ[name]
                break
        for name in VERSION_ENV_VARS:
            if os.environ.get(name):
                kwargs["api_version"] = os.environ[name]
                break

        response = completion(**kwargs)
        return str(response["choices"][0]["message"]["content"] or "").strip()


# --------------------------------------------------------------------------- #
# The live trainee
# --------------------------------------------------------------------------- #


@dataclass
class LiveTrainee:
    """The adviser under test, played by a model at a declared competence level.

    Satisfies `roleplay.runtime.Trainee` and nothing else — no base class, no
    registry. Three guards, because three things a prompt asks for and cannot
    guarantee would each ruin a session in a way that reads as a product finding:

    1.  **It must not loop.** An adviser and a customer rephrasing the same
        exchange burns the turn budget, and a budget stop is indistinguishable in
        a report from an adviser who never closed. Repetition ends the session
        with `stop_reason="repeated_turn"`.
    2.  **It must not write the customer's lines.** A reply containing a
        "Customer:" cue is truncated at it, and the truncation is counted, because
        a trainee turn that contains the customer's answer makes the discovery
        criterion meaningless.
    3.  **It must not run away with the budget.** Checked before a completion is
        requested, not after, because the point of the check is the money.
    """

    speaker: ModelSpeaker
    system_prompt: str
    max_turns: int = 12
    history: list[dict[str, str]] = field(default_factory=list)
    stop_reason: str | None = None
    said: list[str] = field(default_factory=list)
    truncated_impersonations: int = 0
    filtered_turns: int = 0

    @property
    def planned_turns(self) -> int:
        """What `session_start` should record. A budget, not a promise."""
        return self.max_turns

    def open(self) -> str | None:
        return self._next(
            "You are in the meeting room with the customer. Open the meeting."
        )

    def reply(self, customer_turn: str) -> str | None:
        return self._next(customer_turn or "(the customer said nothing)")

    # ------------------------------------------------------------------ private

    def _next(self, user_message: str) -> str | None:
        if self.stop_reason is not None:
            return None
        if len(self.said) >= self.max_turns:
            self.stop_reason = "turn_budget"
            return None
        self.history.append({"role": "user", "content": user_message})
        messages = [{"role": "system", "content": self.system_prompt}, *self.history]
        try:
            raw = self.speaker.say(messages)
        except ContentFilterError:
            # The provider refused to continue. The session ends here with what it
            # has, labelled, because a filtered meeting is a real outcome for a
            # coaching product and a crash is not a result.
            self.stop_reason = "content_filter"
            self.filtered_turns += 1
            return None
        spoken, ended = _strip_sentinel(raw)
        spoken, cut = _strip_impersonation(spoken)
        if cut:
            self.truncated_impersonations += 1
        if not spoken:
            # A bare sentinel is the trainee following the rule; an empty response
            # with no sentinel is the provider returning nothing. Both end the
            # session and they must not share a bucket.
            self.stop_reason = "session_closed" if ended else "empty_turn"
            return None
        normalised = " ".join(spoken.split()).casefold()
        if normalised in self.said:
            self.stop_reason = "repeated_turn"
            return None
        self.said.append(normalised)
        self.history.append({"role": "assistant", "content": spoken})
        if ended:
            # The sentinel arrived alongside real words: deliver them now and stop
            # on the next ask, without buying a completion for a decided turn.
            self.stop_reason = "session_closed"
        return spoken

    def __repr__(self) -> str:
        return (
            f"LiveTrainee(model={self.speaker.model_label!r}, "
            f"turns={len(self.said)}, stop={self.stop_reason!r})"
        )


#: Role labels a model puts in front of its own line, and the cue that it has
#: started writing the other side of the conversation. Kept narrow on purpose: an
#: over-eager truncator that cut at the first bracket would delete "(0.68 per
#: cent)" from a disclosure and manufacture a compliance failure.
_ROLE_PREFIX_RE = re.compile(r"^\s*(adviser|advisor|me|you|customer|client)\s*:\s*", re.IGNORECASE)
_IMPERSONATION_RE = re.compile(r"\n\s*(customer|client)\s*:", re.IGNORECASE)


def _strip_impersonation(text: str) -> tuple[str, bool]:
    """Drop a leading role label, and cut where the model starts playing the other side.

    Returns the cleaned text and whether anything was cut. The cut is counted by
    the caller rather than silently swallowed: a turn that contained the
    customer's answer is an instrument fault, and an instrument fault that leaves
    no trace is the one that ends up in a result.
    """
    body = _ROLE_PREFIX_RE.sub("", text or "", count=1)
    match = _IMPERSONATION_RE.search(body)
    if match is None:
        return body.strip(), body.strip() != (text or "").strip()
    return body[: match.start()].strip(), True


# --------------------------------------------------------------------------- #
# The live customer's voice
# --------------------------------------------------------------------------- #


@dataclass
class LiveCustomerVoice:
    """Puts the state machine's chosen move into a model's words.

    Satisfies `roleplay.runtime.CustomerVoice`. It receives a `PersonaTurn` that
    already contains the decision and the scripted phrasing, and its only job is
    to say that decision in this customer's voice.

    `leaks` is the measurement that keeps the claim honest. The prompt tells the
    model to say nothing the direction did not ask for; this counts the turns in
    which it mentioned the topic of a concern the state machine had not released
    yet. A prompt can only ask. A count can be reported — and it is, in
    `session_end` as `customer_topic_leaks`, so a reader of the trace can see the
    instrument's own error rate rather than taking this docstring's word for it.

    What the counter can and cannot see: it matches concern *topics* as normalised
    substrings, so "school fees" is caught and "the children's education" is not.
    The number is therefore a floor, not a total, and it is reported as one.
    """

    speaker: ModelSpeaker
    system_prompt: str
    profile: CustomerProfile
    history: list[dict[str, str]] = field(default_factory=list)
    leaks: int = 0
    leaked_topics: list[str] = field(default_factory=list)
    fallbacks: int = 0
    filtered_turns: int = 0

    def speak(
        self,
        *,
        move: PersonaTurn,
        persona: CustomerPersona,
        trainee_turn: str,
        turn: int,
    ) -> str:
        direction = self._direction(move)
        self.history.append(
            {
                "role": "user",
                "content": f"The adviser says: {trainee_turn}\n\nDIRECTION: {direction}",
            }
        )
        messages = [{"role": "system", "content": self.system_prompt}, *self.history]
        try:
            spoken, _ = _strip_sentinel(self.speaker.say(messages, note=direction))
            spoken, _ = _strip_impersonation(spoken)
        except ContentFilterError:
            # The customer's *move* is not negotiable — it is already in the
            # ledger and the trace. Only the wording was refused, so the profile's
            # own wording stands in and the substitution is counted.
            self.filtered_turns += 1
            spoken = ""
        if not spoken:
            # An empty turn from the provider would silently delete a raised
            # objection from the transcript while leaving it in the ledger — a
            # session whose trace and whose events disagree. Fall back to the
            # profile's own wording and count it.
            self.fallbacks += 1
            spoken = move.text
        self.history.append({"role": "assistant", "content": spoken})
        self._audit_leaks(spoken, persona)
        return spoken

    # ------------------------------------------------------------------ private

    def _direction(self, move: PersonaTurn) -> str:
        """The per-turn instruction: what to convey, and in what spirit."""
        if move.pressed:
            objection = move.pressed[0]
            return (
                f"You already raised this and the adviser talked past it. Put it "
                f"back on the table, less patiently this time: {objection.says}"
            )
        if move.raised:
            objection = move.raised[0]
            return f"Raise this objection now, in your own words: {objection.says}"
        if move.revealed:
            concern = move.revealed[0]
            return (
                "The adviser has asked you an open question. Answer it by telling "
                f"them this, and only this: {concern.reveal}"
            )
        return (
            "Acknowledge what the adviser just said and add nothing new. Do not "
            "raise a worry, a plan or a sum of money."
        )

    def _audit_leaks(self, spoken: str, persona: CustomerPersona) -> None:
        haystack = normalise(spoken)
        for concern in persona.pending_concerns():
            topic = normalise(concern.topic)
            if topic and topic in haystack:
                self.leaks += 1
                self.leaked_topics.append(concern.key)

    def __repr__(self) -> str:
        return (
            f"LiveCustomerVoice(model={self.speaker.model_label!r}, "
            f"turns={len(self.history) // 2}, leaks={self.leaks})"
        )


# --------------------------------------------------------------------------- #
# One live session, end to end
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LiveRow:
    """One row of the live matrix: who talks to whom, how well, and under whose rules.

    A declared object rather than a scenario YAML, and the reason is honesty about
    what a live row *is*. A corpus row carries a committed script and a human
    verdict, and `roleplay.corpus` validates both; a live row carries neither,
    because its transcript does not exist until a model writes one. Filing them in
    `scenarios/roleplay/` would put rows with no ground truth in the same
    denominator as rows with one.
    """

    scenario_id: str
    customer: str
    competence: Competence
    jurisdiction: str
    language: str = "en"
    notes: str = ""

    def summary(self) -> str:
        return (
            f"{self.scenario_id}: {self.customer} vs a {self.competence} adviser, "
            f"{self.jurisdiction}/{self.language}"
        )


@dataclass
class LiveOutcome:
    """One live session and everything needed to audit it afterwards.

    The score card is *not* the headline here. `shadow` — the register against a
    naive keyword check — and `leaks` — the customer's own error rate — are what
    say whether the session is admissible evidence at all, so they travel beside
    the grade rather than in a log.
    """

    row: LiveRow
    key: SessionKey
    result: RoleplayResult
    cassette_path: Path
    shadow: ShadowComparison
    trainee_stop: str
    customer_leaks: int
    leaked_topics: tuple[str, ...]
    voice_fallbacks: int
    impersonations: int
    #: Turns the provider's content filter refused, per role. A session with a
    #: non-zero trainee count ended early for a reason that has nothing to do with
    #: the adviser's competence, and a report that folded it into "did not close"
    #: would be reporting the filter as a finding about the trainee.
    trainee_filtered: int
    customer_filtered: int
    recorded_turns: int
    replayed_turns: int

    @property
    def card(self) -> Any:
        return self.result.card

    @property
    def turns(self) -> int:
        return len(self.result.trainee_utterances)

    def row_line(self) -> str:
        """One line for the run table. Every number carries its denominator."""
        card = self.result.card
        return (
            f"{self.row.competence:<10} {self.row.jurisdiction:<12} "
            f"{self.row.customer:<22} {self.turns:>2}t  "
            f"score {card.total:>2}/20 {card.verdict:<4} "
            f"disc {len(self.shadow.recorded)}/{len(self.shadow.required)} "
            f"kw {len(self.shadow.keyword_credited)}/{len(self.shadow.required)} "
            f"leaks {self.customer_leaks} filt {self.trainee_filtered + self.customer_filtered} "
            f"stop={self.trainee_stop}"
        )


# --------------------------------------------------------------------------- #
# The trainee seam
# --------------------------------------------------------------------------- #
#
# `roleplay.runtime.Trainee` is two methods. Everything below exists so that a
# system satisfying those two methods — in this process, over HTTP, anywhere — can
# be the adviser under test without touching the loop, the register, the persona,
# the scorer or the reports. One seam, two callers (`run_live_session` here and
# `roleplay.spoken.run_spoken_call`), and one default that is exactly what both
# callers hardcoded before the seam existed.


@dataclass(frozen=True)
class TraineeContext:
    """What a trainee factory is told about the session it is joining.

    The first seven fields are the session: which scenario, which customer, at
    what competence, under which regulator's register, in which language, with
    what turn budget, and what label the fixture will carry. An external trainee
    may use them or ignore them. The last three are what the *built-in* model
    trainee needs to replay and record — its cassette, its litellm route and the
    injectable completion seam — and an external factory has no reason to read
    them. They are here rather than in a second constructor so that both callers
    build one object and pass it through one function.
    """

    scenario_id: str
    profile: CustomerProfile
    competence: str
    jurisdiction: str
    language: str
    max_turns: int
    model_label: str
    temperature: float = 0.0
    cassette: SessionCassette | None = None
    trainee_model: str | None = None
    completion: Callable[..., str] | None = None


TraineeFactory = Callable[[TraineeContext], Trainee]


def model_trainee(context: TraineeContext) -> LiveTrainee:
    """The default factory: the model-backed adviser, replayed from the cassette.

    This is, line for line, what `run_live_session` and `run_spoken_call` used to
    construct inline. Keeping it as the default means every committed cassette
    still replays: the system prompt is the same text, so the digest each recorded
    turn is keyed on is unchanged.
    """
    if context.cassette is None:
        raise TraineeFactoryError(
            "the built-in model trainee needs a cassette to replay from and record "
            "into; the runners always supply one, so this context was built by hand."
        )
    speaker = ModelSpeaker(
        role="trainee",
        cassette=context.cassette,
        live_env_var=LIVE_TRAINEE_ENV_VAR,
        model_env_var=TRAINEE_MODEL_ENV_VAR,
        model=context.trainee_model,
        model_label=context.model_label,
        temperature=context.temperature,
        max_tokens=TRAINEE_MAX_TOKENS,
        completion=context.completion,
    )
    return LiveTrainee(
        speaker=speaker,
        system_prompt=trainee_prompt(
            competence=context.competence,
            profile=context.profile,
            jurisdiction=context.jurisdiction,
            language=context.language,
        ),
        max_turns=context.max_turns,
    )


def resolve_trainee_factory(spec: str | TraineeFactory | None = None) -> TraineeFactory:
    """The factory to use: an explicit callable or path, else the env var, else the default.

    Resolution order is argument, then `LAB_TRAINEE_FACTORY`, then `model_trainee`,
    so a flag on the command line beats the shell and an unset shell changes
    nothing. A path is imported with `lab.cli`'s importer — the same one
    `--agent-factory` uses for the booking agent — so the two case studies accept
    the same spelling and look in the same places. Every way the import can fail
    is turned into one `TraineeFactoryError` that names the path.
    """
    if spec is not None and not isinstance(spec, str):
        if not callable(spec):
            raise TraineeFactoryError(
                f"trainee factory must be callable, got {type(spec).__name__}"
            )
        return spec
    dotted = spec or os.environ.get(TRAINEE_FACTORY_ENV_VAR) or ""
    if not dotted:
        return model_trainee
    origin = "--trainee-factory" if spec else TRAINEE_FACTORY_ENV_VAR
    from lab.cli import _import_object  # local: `lab.cli` is argparse-heavy

    try:
        factory = _import_object(dotted)
    except (ImportError, AttributeError, ValueError, SystemExit) as exc:
        # `SystemExit` because lab.cli's importer exits with the case-study
        # explanation when a module is missing; here the missing thing is the
        # reader's own adapter, and that is what the message must say.
        cause = exc.__cause__ if isinstance(exc, SystemExit) and exc.__cause__ else exc
        raise TraineeFactoryError(
            f"{origin}={dotted!r} could not be imported as a trainee factory "
            f"({type(cause).__name__}: {cause}). Expected `package.module:callable`, where the "
            "module is importable from this directory and the callable takes one "
            "TraineeContext and returns an object with open() and reply(). "
            "Runnable examples: examples/adapters/."
        ) from exc
    if not callable(factory):
        raise TraineeFactoryError(
            f"{origin}={dotted!r} names {type(factory).__name__}, which is not "
            "callable. Point it at the factory function, not at the module or the "
            "trainee class instance."
        )
    return factory


def build_trainee(
    context: TraineeContext, *, factory: str | TraineeFactory | None = None
) -> Trainee:
    """Resolve the factory, call it, and refuse anything that is not a trainee."""
    resolved = resolve_trainee_factory(factory)
    trainee = resolved(context)
    if not isinstance(trainee, Trainee):
        name = getattr(resolved, "__qualname__", repr(resolved))
        raise TraineeFactoryError(
            f"trainee factory {name} returned {type(trainee).__name__}, which does "
            "not satisfy roleplay.runtime.Trainee: it needs open() -> str | None "
            "and reply(customer_turn: str) -> str | None."
        )
    return trainee


def _speaker_count(trainee: object, counter: str) -> int:
    """A `ModelSpeaker` counter read off a trainee that has one, else 0.

    An external trainee has no cassette and so records and replays nothing; the
    outcome says 0 for it rather than pretending, and the report's "turns
    recorded live this run" line stays a statement about model calls made here.
    """
    speaker = getattr(trainee, "speaker", None)
    return int(getattr(speaker, counter, 0) or 0)


def _model_label(explicit: str | None = None) -> str:
    """What the fixture says the turns came from. Never a route, never a key."""
    return explicit or os.environ.get(MODEL_LABEL_ENV_VAR) or "unspecified-model"


def run_live_session(
    row: LiveRow,
    *,
    profile: CustomerProfile,
    coach: RoleplayCoach | None = None,
    root: str | Path = CASSETTE_ROOT,
    trainee_model: str | None = None,
    customer_model: str | None = None,
    model_label: str | None = None,
    temperature: float = 0.0,
    max_turns: int = DEFAULT_MAX_TURNS,
    trainee_completion: Callable[..., str] | None = None,
    customer_completion: Callable[..., str] | None = None,
    live_customer: bool = True,
    save: bool = True,
    trainee_factory: str | TraineeFactory | None = None,
) -> LiveOutcome:
    """Run one session with a live trainee, and record or replay it.

    `trainee_factory` is the adapter seam: a callable or a `module:callable` path
    that builds the adviser under test from a `TraineeContext`. Left as None it
    falls back to `LAB_TRAINEE_FACTORY`, and with that unset to the model-backed
    `LiveTrainee` — the behaviour every committed cassette was recorded under.

    `live_customer=False` runs the live trainee against the scripted customer
    voice — the ablation that separates "the adviser did this" from "the customer
    was phrased that way". The cassette key is unchanged by it, because the
    customer's *prompt* is unchanged; only the trainee's turns are recorded, and a
    later run with the customer live will find the trainee's turns already there
    and refuse them as stale the moment the conversation diverges. That refusal is
    correct: those are two different conversations.
    """
    label = _model_label(model_label)
    key = SessionKey.build(
        scenario_id=row.scenario_id,
        profile=profile,
        competence=row.competence,
        jurisdiction=row.jurisdiction,
        language=row.language,
        trainee_model=label,
        customer_model=label if live_customer else "scripted-voice",
        temperature=temperature,
        turn_budget=max_turns,
    )
    cassette = SessionCassette.load(key.path_in(root), identity=key)
    cassette.identity = key
    cassette.provenance = {
        "temperature": temperature,
        "max_turns": max_turns,
        "live_customer": live_customer,
        "note": (
            "Generated turns. The trainee is the system under test; the customer's "
            "moves are decided by roleplay.persona and only worded by the model."
        ),
    }

    trainee = build_trainee(
        TraineeContext(
            scenario_id=row.scenario_id,
            profile=profile,
            competence=row.competence,
            jurisdiction=row.jurisdiction,
            language=row.language,
            max_turns=max_turns,
            model_label=label,
            temperature=temperature,
            cassette=cassette,
            trainee_model=trainee_model,
            completion=trainee_completion,
        ),
        factory=trainee_factory,
    )

    voice: Any = None
    customer_speaker: ModelSpeaker | None = None
    if live_customer:
        customer_speaker = ModelSpeaker(
            role="customer",
            cassette=cassette,
            live_env_var=LIVE_CUSTOMER_ENV_VAR,
            model_env_var=CUSTOMER_MODEL_ENV_VAR,
            model=customer_model,
            model_label=label,
            temperature=temperature,
            max_tokens=CUSTOMER_MAX_TOKENS,
            completion=customer_completion,
        )
        voice = LiveCustomerVoice(
            speaker=customer_speaker,
            system_prompt=customer_prompt(profile),
            profile=profile,
        )

    effective_coach = coach if coach is not None else RoleplayCoach(scorer=RubricScorer())
    try:
        result = effective_coach.run(
            scenario_id=row.scenario_id,
            profile=profile,
            trainee=trainee,
            customer_voice=voice,
            jurisdiction=row.jurisdiction,
            language=row.language,
            max_turns=max_turns,
            session_id=f"{row.scenario_id}-{row.competence}",
        )
    finally:
        # Save whatever was generated even if the session then failed. A recording
        # that is thrown away on an exception is money spent twice.
        if save:
            cassette.save()

    return LiveOutcome(
        row=row,
        key=key,
        result=result,
        cassette_path=cassette.path,
        shadow=result.keyword_shadow(),
        trainee_stop=stop_reason_of(trainee, "unknown"),
        customer_leaks=int(getattr(voice, "leaks", 0) or 0),
        leaked_topics=tuple(getattr(voice, "leaked_topics", ()) or ()),
        voice_fallbacks=int(getattr(voice, "fallbacks", 0) or 0),
        impersonations=int(getattr(trainee, "truncated_impersonations", 0) or 0),
        trainee_filtered=int(getattr(trainee, "filtered_turns", 0) or 0),
        customer_filtered=int(getattr(voice, "filtered_turns", 0) or 0),
        recorded_turns=_speaker_count(trainee, "recorded")
        + (customer_speaker.recorded if customer_speaker else 0),
        replayed_turns=_speaker_count(trainee, "replayed")
        + (customer_speaker.replayed if customer_speaker else 0),
    )


# --------------------------------------------------------------------------- #
# The matrix
# --------------------------------------------------------------------------- #

#: Ten sessions: three competence levels, three jurisdictions, four customers.
#:
#: Small on purpose. The claim being supported is "a live trainee produces a
#: spread this scorer can be measured against", and ten sessions with committed
#: cassettes support it at a cost that can be stated. It is not a rate: with n=10
#: no percentage in this pack is quoted without its denominator, and none of these
#: rows carries a human verdict, so they are not in the calibration set.
LIVE_MATRIX: tuple[LiveRow, ...] = (
    LiveRow(
        scenario_id="live-eu-cautious-weak",
        customer="cautious_saver",
        competence="weak",
        jurisdiction="eu-retail",
        notes="The control for the dial's bottom end: does an untrained adviser fail?",
    ),
    LiveRow(
        scenario_id="live-eu-cautious-competent",
        customer="cautious_saver",
        competence="competent",
        jurisdiction="eu-retail",
        notes="The middle of the dial against the cooperative customer.",
    ),
    LiveRow(
        scenario_id="live-eu-cautious-exemplary",
        customer="cautious_saver",
        competence="exemplary",
        jurisdiction="eu-retail",
        notes="The top of the dial, briefed with the approved wording.",
    ),
    LiveRow(
        scenario_id="live-eu-challenger-weak",
        customer="aggressive_challenger",
        competence="weak",
        jurisdiction="eu-retail",
        notes="A weak adviser against a customer who will not let an objection drop.",
    ),
    LiveRow(
        scenario_id="live-eu-challenger-exemplary",
        customer="aggressive_challenger",
        competence="exemplary",
        jurisdiction="eu-retail",
        notes="Same customer, top of the dial: the objection-handling spread.",
    ),
    LiveRow(
        scenario_id="live-apac-cautious-competent",
        customer="cautious_saver",
        competence="competent",
        jurisdiction="apac-retail",
        notes="Four required disclosures instead of three: does suitability get said?",
    ),
    LiveRow(
        scenario_id="live-apac-cautious-exemplary",
        customer="cautious_saver",
        competence="exemplary",
        jurisdiction="apac-retail",
        notes="The jurisdiction dial with the brief that includes suitability.",
    ),
    LiveRow(
        scenario_id="live-apac-terse-competent",
        customer="reluctant_minimal",
        competence="competent",
        jurisdiction="apac-retail",
        notes="A customer who says almost nothing: does the adviser keep probing?",
    ),
    LiveRow(
        scenario_id="live-amer-wary-weak",
        customer="wary_transferer",
        competence="weak",
        jurisdiction="amer-retail",
        notes="Conflict-of-interest market, and a customer who presses twice.",
    ),
    LiveRow(
        scenario_id="live-amer-wary-exemplary",
        customer="wary_transferer",
        competence="exemplary",
        jurisdiction="amer-retail",
        notes="Does an exemplary adviser declare their own remuneration?",
    ),
)


def load_customer_profiles() -> dict[str, CustomerProfile]:
    """The customer profiles, from the same directory the scripted corpus reads."""
    return load_profiles(CORPUS_ROOT / "customers")


# --------------------------------------------------------------------------- #
# `python -m roleplay.live`
# --------------------------------------------------------------------------- #


def _report(outcomes: Sequence[LiveOutcome]) -> str:
    """The run table and what it adds up to. Denominators everywhere."""
    lines = ["", "LIVE SESSIONS", "-" * 78]
    for outcome in outcomes:
        lines.append("  " + outcome.row_line())

    n = len(outcomes)
    lines += ["", f"BY COMPETENCE (n={n})", "-" * 78]
    for level in COMPETENCES:
        rows = [o for o in outcomes if o.row.competence == level]
        if not rows:
            continue
        totals = [o.result.card.total for o in rows]
        passes = sum(1 for o in rows if o.result.card.passed)
        disclosed = sum(len(o.shadow.recorded) for o in rows)
        required = sum(len(o.shadow.required) for o in rows)
        advice = sum(
            1 for o in rows if o.result.trace.tool_names().count("flag_compliance_risk")
        )
        lines.append(
            f"  {level:<10} n={len(rows)}  score {min(totals)}-{max(totals)}/20 "
            f"(mean {sum(totals) / len(rows):.1f})  certified {passes}/{len(rows)}  "
            f"disclosures recorded {disclosed}/{required}  "
            f"sessions with a compliance flag {advice}/{len(rows)}"
        )

    over = [o for o in outcomes if o.shadow.over_credited]
    lines += ["", "REGISTER vs KEYWORD CHECK", "-" * 78]
    lines.append(
        f"  sessions where a keyword check would have credited a disclosure the "
        f"register did not: {len(over)}/{n}"
    )
    for outcome in over:
        lines.append(
            f"    {outcome.row.scenario_id}: {', '.join(outcome.shadow.over_credited)}"
        )

    leaked = [o for o in outcomes if o.customer_leaks]
    lines += ["", "INSTRUMENT HEALTH", "-" * 78]
    lines.append(
        f"  customer turns that named an unreleased concern: "
        f"{sum(o.customer_leaks for o in leaked)} across {len(leaked)}/{n} sessions "
        "(a floor, not a total — the detector matches declared topics only)"
    )
    lines.append(
        f"  trainee turns truncated for writing the customer's line: "
        f"{sum(o.impersonations for o in outcomes)}"
    )
    lines.append(
        f"  customer turns that fell back to the profile's own wording: "
        f"{sum(o.voice_fallbacks for o in outcomes)}"
    )
    filtered = [o for o in outcomes if o.trainee_filtered or o.customer_filtered]
    lines.append(
        f"  turns refused by the provider's content filter: "
        f"{sum(o.trainee_filtered + o.customer_filtered for o in outcomes)} "
        f"across {len(filtered)}/{n} sessions"
        + (
            " (sessions: " + ", ".join(o.row.scenario_id for o in filtered) + ")"
            if filtered
            else ""
        )
    )
    stops: dict[str, int] = {}
    for outcome in outcomes:
        stops[outcome.trainee_stop] = stops.get(outcome.trainee_stop, 0) + 1
    lines.append("  stop reasons: " + ", ".join(f"{k}={v}" for k, v in sorted(stops.items())))
    lines.append(
        f"  turns recorded live this run: {sum(o.recorded_turns for o in outcomes)}; "
        f"replayed from cassette: {sum(o.replayed_turns for o in outcomes)}"
    )
    lines.append(f"  pass mark for reference: {PASS_TOTAL}/20")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the live matrix, recording if permitted and replaying if not.

    Exit code is about the *instrument*, not the grades: a session that could
    neither be replayed nor recorded is a failure, and a red score card is a
    finding. Conflating the two is how a suite ends up either permanently red and
    ignored, or green and blind.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default=None, help="Substring filter on scenario id.")
    parser.add_argument(
        "--competence", default=None, choices=list(COMPETENCES), help="One level only."
    )
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--root", default=str(CASSETTE_ROOT))
    parser.add_argument(
        "--scripted-customer",
        action="store_true",
        help="Live trainee, scripted customer voice — the ablation.",
    )
    parser.add_argument(
        "--warm",
        action="store_true",
        help=(
            "Reuse one coach across the matrix, the way a deployed service is "
            "warm. Off by default so each session is graded by a cold scorer and "
            "the cohort curve cannot move the numbers."
        ),
    )
    parser.add_argument(
        "--trainee-factory",
        default=None,
        metavar="MODULE:CALLABLE",
        help=(
            "Dotted path to a factory that builds the adviser under test from a "
            f"TraineeContext (default: ${TRAINEE_FACTORY_ENV_VAR}, else the built-in "
            "model trainee). See docs/ADAPTER.md and examples/adapters/."
        ),
    )
    args = parser.parse_args(argv)

    try:
        trainee_factory = resolve_trainee_factory(args.trainee_factory)
    except TraineeFactoryError as exc:
        print(f"trainee factory: {exc}", file=sys.stderr)
        return 2

    profiles = load_customer_profiles()
    rows = [
        row
        for row in LIVE_MATRIX
        if (args.only is None or args.only in row.scenario_id)
        and (args.competence is None or row.competence == args.competence)
    ]
    if not rows:
        print("no rows matched the filter", file=sys.stderr)
        return 2

    coach = RoleplayCoach(scorer=RubricScorer()) if args.warm else None
    outcomes: list[LiveOutcome] = []
    failures: list[str] = []
    for row in rows:
        try:
            outcomes.append(
                run_live_session(
                    row,
                    profile=profiles[row.customer],
                    coach=coach,
                    root=args.root,
                    max_turns=args.max_turns,
                    temperature=args.temperature,
                    live_customer=not args.scripted_customer,
                    trainee_factory=trainee_factory,
                )
            )
        except (
            NotLiveError,
            MissingTurnError,
            StaleCassetteError,
            TraineeFactoryError,
        ) as exc:
            failures.append(f"{row.scenario_id}: {type(exc).__name__}: {exc}")

    if outcomes:
        print(_report(outcomes))
    if failures:
        print("\nSESSIONS THAT COULD NOT RUN", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
