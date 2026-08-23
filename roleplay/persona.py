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

WHAT THE LIVE PATH CHANGES, AND WHAT IT MUST NOT
------------------------------------------------
`roleplay.live` can put a model behind this customer's voice. When it does, the
state machine below still decides **what** the customer does — which concern
surfaces, which objection is raised, whether an objection is pressed again — and
the model only decides the **words**. The split is not a convenience. The whole
reason a trainee who never runs discovery can be caught failing is that a concern
is released by `respond()` after an open probe and at no other time; a customer
whose disclosure discipline was only *requested*, in a prompt, would leak a need
in its opening line and quietly make the discovery criterion unmeasurable. The
prompt asks; this file guarantees.

THE COMMERCIAL DIALS
--------------------
`risk_appetite`, `budget` and `suspicion` describe a retail customer rather than
a conversational puppet, and each has a stated consequence:

    risk_appetite   a closed vocabulary; prompt text, and the ground truth a
                    suitability claim would have to match
    budget          what they actually have to invest, in their own words
    suspicion       >= SUSPICIOUS_AT buys every objection one extra press

`Objection.presses` is the deterministic half of that: an objection with
`presses: 2` is raised, and then *raised again* if the trainee talked past it,
which is what a real customer with a live worry does. Mentioning a concern once
and dropping it forever is the behaviour that lets a weak trainee look adequate,
because an objection nobody has to answer twice is an objection nobody has to
answer. Both dials default to the old behaviour (`presses: 1`, `suspicion: 0.0`),
so every committed fixture in this pack still reproduces byte for byte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

__all__ = [
    "TurnKind",
    "RISK_APPETITES",
    "SUSPICIOUS_AT",
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

#: The declared risk appetites. A closed vocabulary, like every other in this
#: pack: `"unstated"` is a member rather than an empty string, because a customer
#: whose appetite was never declared and a customer who is genuinely indifferent
#: are different people, and a suitability claim about the first one is a claim
#: about nothing. Profiles written before this field existed read as `unstated`,
#: and the live prompt then says nothing about appetite rather than inventing one.
RISK_APPETITES: tuple[str, ...] = (
    "unstated",
    "cautious",
    "balanced",
    "adventurous",
)

#: Suspicion at or above this buys every objection one extra press. A single
#: named threshold, for the reason `lab.simulator.persona` names its two: an
#: author raising a dial is entitled to know the value at which behaviour
#: changes, and to know that nothing changes anywhere else.
SUSPICIOUS_AT: float = 0.6

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
    #: How many times the customer will put this objection on the table if it
    #: keeps going unanswered — 1 is "says it once and lets it go", 2 is "asks
    #: again". An objection nobody has to answer twice is an objection a trainee
    #: can simply outlast, and a trainee who outlasts it should not be scoring the
    #: same as one who addressed it.
    presses: int = 1

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
        risk_appetite: One of `RISK_APPETITES`. Prompt text for a live voice, and
            the ground truth any suitability claim about this customer would have
            to be consistent with.
        budget: What they have to invest, in their own words. A string because it
            is a thing a person says out loud, and because "about sixty thousand,
            but I need some of it back in three years" is the answer a real
            discovery question gets and a number is not.
        suspicion: 0.0-1.0. At or above `SUSPICIOUS_AT` every objection gets one
            extra press. Below it, nothing changes.
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
    risk_appetite: str = "unstated"
    budget: str = ""
    suspicion: float = 0.0

    def __post_init__(self) -> None:
        """Validate the closed vocabulary and the dial's range at construction.

        Here rather than in the loader, so a profile built in Python by a test or
        a live matrix row is held to the same vocabulary as one read from YAML. A
        validation that only runs on the file format is a validation with a hole
        in it exactly where the code paths differ.
        """
        if self.risk_appetite not in RISK_APPETITES:
            raise ValueError(
                f"{self.key}: risk_appetite {self.risk_appetite!r} is not one of "
                f"{list(RISK_APPETITES)}"
            )
        if not 0.0 <= self.suspicion <= 1.0:
            raise ValueError(
                f"{self.key}: suspicion must be between 0.0 and 1.0, got {self.suspicion}"
            )

    @property
    def suspicious(self) -> bool:
        """True when this customer presses each objection one extra time."""
        return self.suspicion >= SUSPICIOUS_AT

    def presses_allowed(self, objection: Objection) -> int:
        """Total times `objection` may be put on the table by this customer."""
        return max(1, objection.presses + (1 if self.suspicious else 0))

    def summary(self) -> str:
        return (
            f"{self.display_name} ({self.key}): {len(self.concerns)} hidden concern(s), "
            f"{len(self.objections)} objection(s), {self.jurisdiction}/{self.language}"
            + (", terse" if self.terse else "")
            + (", assertive" if self.assertive else "")
            + (f", {self.risk_appetite}" if self.risk_appetite != "unstated" else "")
            + (f", suspicion {self.suspicion:.1f}" if self.suspicion else "")
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
    #: Set when this turn is a *repeat* of an objection the trainee talked past.
    #: A subset of `raised` rather than a replacement for it: the objection ledger
    #: must still see one raise, and the customer's voice needs to know it is
    #: asking again — a live voice that phrased a second press as a first mention
    #: would erase the only signal that the trainee ignored it.
    pressed: tuple[Objection, ...] = ()

    @property
    def is_press(self) -> bool:
        return bool(self.pressed)

    def spoken_intent(self) -> str:
        """What this turn is doing, for a live voice's per-turn instruction."""
        if self.pressed:
            return f"press the unanswered objection about {self.pressed[0].topic} again"
        if self.raised:
            return f"raise the objection about {self.raised[0].topic}"
        if self.revealed:
            return f"reveal the concern about {self.revealed[0].topic}"
        return "acknowledge without adding anything new"


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
    #: Times each objection has been put on the table, first raise included. Kept
    #: separately from `raised` because that list answers "was it ever raised",
    #: which is what the objection ledger and the scorer read, and this counter
    #: answers "has this customer finished asking", which only the press rule
    #: reads. One list doing both jobs is how a re-raise ends up counted as two
    #: objections in a report.
    press_counts: dict[str, int] = field(default_factory=dict)

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

    def presses_used(self, objection: Objection) -> int:
        """How many times this objection has been put on the table so far."""
        return self.press_counts.get(objection.key, 0)

    def will_press(self, objection: Objection) -> bool:
        """Will the customer raise this unanswered objection again?

        True while the objection's press budget is unspent. An `assertive`
        customer is handled separately in `respond` and keeps its pre-existing
        behaviour — it returns to the oldest unhandled item indefinitely — so this
        rule adds pressing to customers that did not have it and changes nothing
        for the one that did.
        """
        return self.presses_used(objection) < self.profile.presses_allowed(objection)

    def pressed_objections(self) -> tuple[str, ...]:
        """Keys of objections this customer had to raise more than once."""
        return tuple(k for k, n in self.press_counts.items() if n > 1)

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
            objection. With the bank empty, an unhandled objection is put back on
            the table if it still has a press left (`Objection.presses`, plus one
            for a suspicious customer), and an assertive customer returns to the
            oldest unhandled item however many times it takes.
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
                self.press_counts[objection.key] = self.presses_used(objection) + 1
                return PersonaTurn(
                    text=objection.line(terse=terse),
                    kind=kind,
                    raised=(objection,),
                    handled=handled_now,
                )
            stale = self.unhandled_objections()
            pressable = [o for o in stale if self.will_press(o)]
            if stale and (self.profile.assertive or pressable):
                objection = stale[0] if self.profile.assertive else pressable[0]
                self.press_counts[objection.key] = self.presses_used(objection) + 1
                return PersonaTurn(
                    text=f"You still have not answered me on that. {objection.line(terse=terse)}",
                    kind=kind,
                    raised=(objection,),
                    handled=handled_now,
                    pressed=(objection,),
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
        "risk_appetite",
        "budget",
        "suspicion",
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
            presses=int(o.get("presses", 1)),
        )
        for o in data.get("objections", ())
    )
    for objection in objections:
        if objection.presses < 1:
            raise ValueError(
                f"{source}: objection {objection.key!r} declares presses="
                f"{objection.presses}; an objection raised zero times can never be "
                "handled and never be unhandled, so it scores as absent either way"
            )
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
        risk_appetite=str(data.get("risk_appetite", "unstated")),
        budget=str(data.get("budget", "")),
        suspicion=float(data.get("suspicion", 0.0)),
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
