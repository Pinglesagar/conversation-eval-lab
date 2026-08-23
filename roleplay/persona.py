"""The AI customer persona — the half of the product the trainee talks to.

WHAT THIS DEMONSTRATES
----------------------
A roleplay product has two systems under test that fail in completely different
ways, and conflating them is the first mistake available. This module is the
first one: the customer the trainee practises against. It holds concerns it does
not volunteer, an objection bank it works through, and a manner — talkative,
combative, monosyllabic. `roleplay.scorer` is the second one, and it is where
every seeded defect in this pack lives.

The persona is deliberately **clean**. No defect is planted here. That is a
measurement decision, not modesty: with the persona correct, any finding in a run
is attributable to the scorer, and the pack can make claims about the scorer
without first arguing about whether the customer behaved plausibly.

WHY IT IS A STATE MACHINE AND NOT A PROMPT
------------------------------------------
Because the stimulus has to be reproducible before the response can be measured.
A model-driven customer varies its objections run to run, which means a score
that moves between runs has two candidate explanations and the score-consistency
question becomes unanswerable. Here the customer is a pure function of (profile,
trainee turns), so an identical trainee performance produces an identical
conversation, and the *only* remaining source of run-to-run variance is the
scorer. That is what makes `roleplay.consistency` a measurement of one thing.

The cost is honest and worth stating: this customer is not a realistic language
model, and nothing in this pack claims that testing against it substitutes for
testing against the real one. What it substitutes for is the *fixture* — the
recorded, reproducible conversation a real pipeline would replay.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

__all__ = [
    "TurnKind",
    "Concern",
    "Objection",
    "CustomerProfile",
    "PersonaTurn",
    "CustomerPersona",
    "classify_trainee_turn",
    "load_profile",
    "load_profiles",
]

#: What a trainee turn is doing, as far as the customer is concerned. Coarse on
#: purpose: the persona reacts to conversational *moves*, and a finer taxonomy
#: would be a claim about intent that this module has no way to check.
TurnKind = Literal["open_probe", "closed_question", "pitch", "close_attempt", "advice"]

_OPEN_STEMS = (
    r"^what\b",
    r"^how\b",
    r"^why\b",
    r"^when\b",
    r"^which\b",
    r"^tell me\b",
    r"^walk me\b",
    r"^talk me\b",
    r"^help me understand\b",
    r"\bwhat (?:would|do|are|is|matters|brought)\b",
    r"\bhow (?:would|do|does|much|long|are)\b",
    r"\bwhat'?s\b",
)

_CLOSE_STEMS = (
    r"\bshall we (?:get|go|set|start|proceed|put)\b",
    r"\bhappy to proceed\b",
    r"\bget (?:you |that )?(?:set up|started)\b",
    r"\bopen the account\b",
    r"\bsign (?:the|this|here)\b",
    r"\bwe can (?:do|sort) (?:that|this) (?:today|now)\b",
    r"\bpaperwork\b",
)

#: Phrasings that make a turn a personal recommendation. Named here, in the
#: persona, because the *customer* is who hears them — and because the scorer's
#: inability to see most of them is a seeded defect, so the honest list has to
#: live somewhere the scorer does not read.
_ADVICE_STEMS = (
    r"\byou should (?:move|put|switch|transfer|go|be)\b",
    r"\bif i were you\b",
    r"\bi(?:'d| would) put (?:my|my own)\b",
    r"\bthis is the right (?:fund|product|choice) for you\b",
    r"\bthat'?s what i'?d do\b",
    r"\btake my word\b",
    r"\byou can'?t lose\b",
    r"\bguaranteed returns?\b",
    r"\bno real risk\b",
)

_OPEN_RE = tuple(re.compile(p, re.IGNORECASE) for p in _OPEN_STEMS)
_CLOSE_RE = tuple(re.compile(p, re.IGNORECASE) for p in _CLOSE_STEMS)
_ADVICE_RE = tuple(re.compile(p, re.IGNORECASE) for p in _ADVICE_STEMS)


def classify_trainee_turn(text: str) -> TurnKind:
    """Label one trainee turn by the conversational move it makes.

    Order matters and is deliberate. A personal recommendation dressed as a close
    ("you should move your pension into this, shall we get started?") classifies
    as advice, because the compliance consequence outranks the conversational
    one. Everything else falls through to `pitch`, which is the honest default: a
    statement about the product.
    """
    body = (text or "").strip()
    if any(pattern.search(body) for pattern in _ADVICE_RE):
        return "advice"
    if any(pattern.search(body) for pattern in _CLOSE_RE):
        return "close_attempt"
    if body.endswith("?"):
        return "open_probe" if any(p.search(body) for p in _OPEN_RE) else "closed_question"
    return "pitch"


@dataclass(frozen=True)
class Concern:
    """A worry the customer holds and will not volunteer.

    `topic` is the word a scorer's feedback would have to mention for a claim
    about this concern to be grounded, which is why it is a separate field from
    the spoken line: the check reads the topic, the transcript carries the line.
    """

    key: str
    topic: str
    reveal: str
    reveal_terse: str = ""

    def line(self, *, terse: bool) -> str:
        return self.reveal_terse if terse and self.reveal_terse else self.reveal


@dataclass(frozen=True)
class Objection:
    """One item from the customer's objection bank."""

    key: str
    topic: str
    says: str
    says_terse: str = ""
    #: Regexes over a later trainee turn that count as engaging with it. A short,
    #: explicit list rather than a similarity score, so "the objection was
    #: handled" is a claim a reader can check by eye against the transcript.
    handled_by: tuple[str, ...] = ()

    def line(self, *, terse: bool) -> str:
        return self.says_terse if terse and self.says_terse else self.says

    def is_handled_by(self, text: str) -> bool:
        return any(re.search(p, text or "", re.IGNORECASE) for p in self.handled_by)


@dataclass(frozen=True)
class CustomerProfile:
    """Who the trainee is practising against.

    Attributes:
        key: Stable identifier; the scenario refers to the profile by it.
        display_name: What the trainee sees. Invented individuals throughout.
        situation: One line of context the persona states when asked.
        language: Which registered phrasings apply — see `roleplay.register`.
        jurisdiction: Which disclosure codes are required for this customer.
        terse: Monosyllabic mode. Replies are clipped and concerns need two
            probes rather than one.
        assertive: Combative mode. Objections are raised unprompted and an
            unhandled objection is repeated rather than dropped.
        probes_to_reveal: How many open probes it takes to surface a concern.
        concerns: Hidden worries, revealed in order.
        objections: The objection bank, raised in order.
    """

    key: str
    display_name: str
    situation: str
    language: str = "en"
    jurisdiction: str = "eu-retail"
    terse: bool = False
    assertive: bool = False
    probes_to_reveal: int = 1
    concerns: tuple[Concern, ...] = ()
    objections: tuple[Objection, ...] = ()

    def summary(self) -> str:
        return (
            f"{self.display_name} ({self.key}): {len(self.concerns)} hidden concern(s), "
            f"{len(self.objections)} objection(s), {self.jurisdiction}/{self.language}"
            + (", terse" if self.terse else "")
            + (", assertive" if self.assertive else "")
        )


@dataclass(frozen=True)
class PersonaTurn:
    """What the customer said, and the internal moves behind it.

    `revealed` and `raised` are returned rather than logged because the runtime
    turns each of them into a trace event. A concern that surfaced without a
    corresponding `reveal_concern` event would be a customer behaviour no check
    could ever see, which is the shape of an untestable product.
    """

    text: str
    kind: TurnKind
    revealed: tuple[Concern, ...] = ()
    raised: tuple[Objection, ...] = ()
    handled: tuple[Objection, ...] = ()


@dataclass
class CustomerPersona:
    """The customer, as a turn-by-turn state machine.

    One instance per session. State is the whole point — which concerns are out,
    which objections have been raised and which of those have been engaged with —
    and it is all scoped to the instance, so two sessions cannot contaminate each
    other. (`roleplay.scorer` is where that discipline is deliberately broken.)
    """

    profile: CustomerProfile
    open_probes: int = 0
    revealed: list[str] = field(default_factory=list)
    raised: list[str] = field(default_factory=list)
    handled: list[str] = field(default_factory=list)
    turns: int = 0

    # ---------------------------------------------------------------- reading

    def pending_concerns(self) -> tuple[Concern, ...]:
        return tuple(c for c in self.profile.concerns if c.key not in self.revealed)

    def pending_objections(self) -> tuple[Objection, ...]:
        return tuple(o for o in self.profile.objections if o.key not in self.raised)

    def unhandled_objections(self) -> tuple[Objection, ...]:
        return tuple(
            o
            for o in self.profile.objections
            if o.key in self.raised and o.key not in self.handled
        )

    # ---------------------------------------------------------------- speaking

    def respond(self, trainee_turn: str) -> PersonaTurn:
        """React to one trainee turn.

        The rule order below is the persona's whole behavioural contract, and it
        is short on purpose:

        1.  Anything the trainee said that engages a live objection marks it
            handled. This happens first, so a turn can both close an objection
            and open the next one — which is what a real exchange does.
        2.  An open probe surfaces the next concern, once enough probes have
            landed. A terse customer needs two; a talkative one needs one.
        3.  A pitch, a close attempt, or a piece of advice draws the next
            objection. An assertive customer also re-raises anything still
            unhandled instead of letting it go.
        4.  Otherwise the customer acknowledges and says nothing new.
        """
        self.turns += 1
        terse = self.profile.terse
        kind = classify_trainee_turn(trainee_turn)

        handled_now = tuple(o for o in self.unhandled_objections() if o.is_handled_by(trainee_turn))
        for objection in handled_now:
            self.handled.append(objection.key)

        if kind == "open_probe":
            self.open_probes += 1
            pending = self.pending_concerns()
            if pending and self.open_probes >= self.profile.probes_to_reveal:
                concern = pending[0]
                self.revealed.append(concern.key)
                self.open_probes = 0
                return PersonaTurn(
                    text=concern.line(terse=terse),
                    kind=kind,
                    revealed=(concern,),
                    handled=handled_now,
                )
            return PersonaTurn(
                text=self._deflect(terse=terse), kind=kind, handled=handled_now
            )

        if kind in ("pitch", "close_attempt", "advice"):
            pending = self.pending_objections()
            if pending:
                objection = pending[0]
                self.raised.append(objection.key)
                return PersonaTurn(
                    text=objection.line(terse=terse),
                    kind=kind,
                    raised=(objection,),
                    handled=handled_now,
                )
            stale = self.unhandled_objections()
            if self.profile.assertive and stale:
                objection = stale[0]
                return PersonaTurn(
                    text=f"You still have not answered me on that. {objection.line(terse=terse)}",
                    kind=kind,
                    raised=(objection,),
                    handled=handled_now,
                )

        return PersonaTurn(text=self._acknowledge(terse=terse), kind=kind, handled=handled_now)

    # --------------------------------------------------------------- fillers

    def _deflect(self, *, terse: bool) -> str:
        """What the customer says to a probe that has nothing left to surface."""
        if terse:
            return "Not really."
        return f"Nothing much beyond what I said. {self.profile.situation}"

    def _acknowledge(self, *, terse: bool) -> str:
        if terse:
            return "Mm."
        return "Right. Go on."

    def __repr__(self) -> str:
        return (
            f"CustomerPersona(profile={self.profile.key!r}, turns={self.turns}, "
            f"revealed={len(self.revealed)}/{len(self.profile.concerns)}, "
            f"raised={len(self.raised)}/{len(self.profile.objections)}, "
            f"unhandled={len(self.unhandled_objections())})"
        )


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _profile_from_mapping(data: dict[str, Any], *, source: str) -> CustomerProfile:
    unknown = set(data) - {
        "key",
        "display_name",
        "situation",
        "language",
        "jurisdiction",
        "terse",
        "assertive",
        "probes_to_reveal",
        "concerns",
        "objections",
        "notes",
    }
    if unknown:
        raise ValueError(f"{source}: unknown key(s) {sorted(unknown)}")

    concerns = tuple(
        Concern(
            key=str(c["key"]),
            topic=str(c["topic"]),
            reveal=str(c["reveal"]),
            reveal_terse=str(c.get("reveal_terse", "")),
        )
        for c in data.get("concerns", ())
    )
    objections = tuple(
        Objection(
            key=str(o["key"]),
            topic=str(o["topic"]),
            says=str(o["says"]),
            says_terse=str(o.get("says_terse", "")),
            handled_by=tuple(str(p) for p in o.get("handled_by", ())),
        )
        for o in data.get("objections", ())
    )
    for objection in objections:
        for pattern in objection.handled_by:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"{source}: objection {objection.key!r} handled_by {pattern!r} "
                    f"is not a valid regex ({exc})"
                ) from None
        if not objection.handled_by:
            raise ValueError(
                f"{source}: objection {objection.key!r} declares no handled_by patterns, "
                "so it can never be marked handled and the objection-handling score "
                "for this profile is pinned at zero whatever the trainee does"
            )

    return CustomerProfile(
        key=str(data["key"]),
        display_name=str(data["display_name"]),
        situation=str(data["situation"]),
        language=str(data.get("language", "en")),
        jurisdiction=str(data.get("jurisdiction", "eu-retail")),
        terse=bool(data.get("terse", False)),
        assertive=bool(data.get("assertive", False)),
        probes_to_reveal=int(data.get("probes_to_reveal", 1)),
        concerns=concerns,
        objections=objections,
    )


def load_profile(path: str | Path) -> CustomerProfile:
    """Load one customer profile from YAML.

    `safe_load` only: a profile file is data and must never execute. Same rule as
    the scenario corpus, for the same reason.
    """
    resolved = Path(path)
    data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{resolved}: expected a YAML mapping, got {type(data).__name__}")
    profile = _profile_from_mapping(data, source=str(resolved))
    if profile.key != resolved.stem:
        raise ValueError(
            f"{resolved}: profile key {profile.key!r} does not match the filename; "
            "a scenario names the profile by key and the file must be findable from it"
        )
    return profile


def load_profiles(directory: str | Path) -> dict[str, CustomerProfile]:
    """Load every profile in a directory, keyed by profile key."""
    base = Path(directory)
    profiles: dict[str, CustomerProfile] = {}
    for path in sorted(base.glob("*.yaml")):
        profile = load_profile(path)
        profiles[profile.key] = profile
    return profiles
