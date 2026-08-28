"""The live rubric scorer: a real model grading a session against the rubric.

WHY THIS MODULE EXISTS
----------------------
Every live number in this repository was measured on the restaurant-booking
domain. `roleplay/` — the domain that actually resembles the systems a
financial-services coaching product ships — had no live path at all: no
`litellm`, no opt-in flag, no recording. Its scorer was deterministic code
standing in for a model, which is a legitimate way to keep a demonstration pack
runnable and an illegitimate way to claim anything about model-graded scoring.

So this module puts a model behind the rubric, and then measures it the way the
rest of the repo measures instruments: against a labelled set, with the confusion
matrix, the parse-error rate, the self-consistency across repeats, and a gate that
refuses the thing when it does not clear a stated bar.

WHAT IS DIFFERENT FROM `lab.judges.Judge`
-----------------------------------------
`Judge` answers one binary question and is deliberately dull about it. A rubric
scorer answers six at once: five ordinal criteria and one verdict, plus prose and
a quoted span. That is not a judge with more values bolted on — the criteria are
the product's output, they go on a dashboard, and a scorer that returns the right
verdict from the wrong criteria is a scorer whose coaching is wrong.

So this module parses the full score card, and the binary half is handed to
`lab.judges` unmodified. One model call, one recorded raw string, two consumers:

    raw JSON ──┬── parse_live_card()      → the five criteria, the total, the card
               └── lab.judges.parse_raw_verdict() → the bit the confusion matrix needs

That is why the rubric's output contract names `verdict` at the top level: it is
the key `lab.judges.judge.parse_raw_verdict` already looks for, so the entire
calibration machinery — digest pinning, replay, confusion matrix, thresholds,
registry gate — works against these recordings without one line of change in
`lab/`. The seam was already the right shape; it only had to be used.

THREE PROPERTIES THAT ARE NOT NEGOTIABLE
----------------------------------------
**A parse failure is an ERRORED verdict, never a pass.** `ScoreCard.verdict` gains
a third value for exactly this. An unreadable answer is a broken output contract
on the scoring service, and the one thing it must never resolve to is "certified".
`CalibrationThresholds.max_parse_error_rate` defaults to zero, so a run containing
any of them cannot clear a gate — which is the correct outcome, because items
forced to FAIL by a parse error *inflate* recall on a fail-positive set.

**The scripted scorer stays the default.** `RubricScorer` is untouched apart from
widening its verdict type, and it keeps all three seeded defects. Nothing in the
offline suite reaches a provider, and `LAB_LIVE_SCORER` gates the live call the
same way `LAB_LIVE_JUDGE` gates a judge's.

**The verdict and the arithmetic are both recorded, and they are allowed to
disagree.** The rubric says a session fails outright on a missing disclosure
whatever it totals, so `verdict` is genuinely not a function of the total, and
collapsing one into the other would destroy real information. Instead
`LiveScore.self_consistent` reports whether they agree, and the study counts the
items where the model's own verdict contradicts its own numbers. That count is a
finding in its own right: a scorer that says "fail" over 18/20 and cannot say why
is a scorer whose dashboard and whose certification decision tell a manager two
different stories.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from lab.judges.judge import (
    Completion,
    JudgeError,
    JudgeRequest,
    LiteLLMCompletion,
    PromptTemplate,
    Recording,
    RecordingCompletion,
    ReplayCompletion,
    RetryPolicy,
    render_tool_ledger,
    render_transcript,
)
from lab.trace.schema import Trace

from roleplay.scorer import (
    CRITERIA,
    MAX_PER_CRITERION,
    PASS_TOTAL,
    ScoreCard,
    SessionView,
    session_view,
)

__all__ = [
    "LIVE_ENV_VAR",
    "MODEL_ENV_VAR",
    "LIVE_TRAINEE_ENV_VAR",
    "TRAINEE_MODEL_ENV_VAR",
    "require_independent_scorer",
    "SCORER_NAME",
    "RUBRIC_VERSIONS",
    "DEFAULT_SYSTEM",
    "ScoreParseError",
    "ParsedScore",
    "LiveScore",
    "LiveRubricScorer",
    "rubric_path",
    "rubric_prompt",
    "parse_live_card",
    "live_completion",
    "replay_completion",
    "recording_completion",
    "model_from_env",
]

#: Opt-in switch for a real provider call from the scorer. Deliberately *not*
#: `LAB_LIVE_JUDGE`: the judge and the scorer are separate spends against
#: separate rate limits, and a reviewer who wants to re-record one should not be
#: made to re-record both. Same contract, different switch.
LIVE_ENV_VAR: str = "LAB_LIVE_SCORER"

#: Where the litellm model route is read from. No model id is defaulted anywhere
#: in this file, for the reason `lab.judges.judge.model_from_env` gives: a harness
#: that pins a vendor has an expiry date.
MODEL_ENV_VAR: str = "LAB_SCORER_MODEL"

#: The *trainee's* switch and route, mirrored from `roleplay.live` as names rather
#: than imported, because `roleplay.live` is the heavy module and this one is
#: imported by the offline path. `require_independent_scorer` compares the two
#: routes; a test asserts these strings still match `roleplay.live`'s, so renaming
#: one without the other fails rather than silently disarming the check.
LIVE_TRAINEE_ENV_VAR: str = "LAB_LIVE_TRAINEE"
TRAINEE_MODEL_ENV_VAR: str = "LAB_TRAINEE_MODEL"

#: The instrument's name, carried into every calibration report so the scorer's
#: measurement history is one series rather than several. Named for the question
#: ("does this session meet the certification standard?") and not for the
#: implementation, so replacing the model does not orphan the history.
SCORER_NAME: str = "roleplay_rubric_scorer"

#: The rubric versions that exist as files next to this module. A closed tuple:
#: a report that cannot name the rubric it measured is not evidence, and a typo'd
#: version must be an error rather than a silently missing file.
RUBRIC_VERSIONS: tuple[str, ...] = ("v1", "v2")

_DIR = Path(__file__).resolve().parent

#: The system prompt. Says what the model is and what it must not do, and nothing
#: about the rubric — the rubric is the user prompt, versioned as a file, and
#: splitting rubric content across two places is how a prompt edit stops being
#: reviewable.
DEFAULT_SYSTEM = (
    "You are a compliance-aware assessor of recorded sales-roleplay sessions. "
    "You grade only what the transcript and the tool ledger show, you never "
    "invent an utterance, and you follow the required output format exactly."
)


class ScoreParseError(JudgeError):
    """The model's answer could not be read as a score card.

    A subclass of `JudgeError` so a caller that already handles judge breakage
    handles this too, and so `except JudgeError` in a pipeline cannot accidentally
    let a scoring failure through a narrower `except`.
    """


def rubric_path(version: str) -> Path:
    """Where a rubric version lives. Rubrics are text files, editable by anyone.

    Raises on an unknown version rather than returning a path that does not
    exist: a missing rubric discovered at render time is a stack trace halfway
    through a paid run.
    """
    if version not in RUBRIC_VERSIONS:
        raise ValueError(
            f"unknown rubric version {version!r}; have {list(RUBRIC_VERSIONS)}"
        )
    return _DIR / f"rubric_{version}.md"


def rubric_prompt(version: str) -> PromptTemplate:
    """Load a rubric version as a validated prompt template."""
    return PromptTemplate.from_path(rubric_path(version))


def model_from_env(default: str | None = None) -> str:
    """Read the scorer's litellm route from `LAB_SCORER_MODEL`."""
    value = os.environ.get(MODEL_ENV_VAR) or default
    if not value:
        raise JudgeError(
            f"no scorer model configured: set {MODEL_ENV_VAR} (e.g. "
            f"{MODEL_ENV_VAR}=azure/your-deployment) or pass model= explicitly."
        )
    return value


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ParsedScore:
    """What a well-formed model answer contains, before it becomes a ScoreCard."""

    criteria: dict[str, int]
    verdict: Literal["pass", "fail"]
    critique: str
    evidence: str | None

    @property
    def total(self) -> int:
        return sum(self.criteria.values())


_VERDICT_WORDS: dict[str, Literal["pass", "fail"]] = {
    "pass": "pass",
    "passed": "pass",
    "fail": "fail",
    "failed": "fail",
}


def _json_objects(text: str) -> list[Any]:
    """Every balanced `{...}` span in `text` that parses as JSON, outermost first.

    Duplicated in spirit from `lab.judges.judge._json_candidates` and deliberately
    not imported from it: that function is private, and a domain package reaching
    into another package's underscore names is a dependency that breaks silently
    on a refactor. The public seam between the two is the *raw string*, and this
    is the one place the duplication buys that.
    """
    found: list[Any] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        found.append(json.loads(text[start : index + 1]))
                    except json.JSONDecodeError:
                        pass
    return found


def _coerce_criterion(name: str, value: Any) -> int:
    """One criterion as an integer in 0..MAX_PER_CRITERION, or an error.

    Out-of-range is an error rather than a clamp. A model that answered 7 out of 4
    has misread the rubric, and clamping to 4 would record its best possible score
    for the criterion it understood least — which is the direction that certifies
    people.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScoreParseError(
            f"criterion {name!r} is {value!r}, which is not a number"
        )
    if isinstance(value, float) and value != int(value):
        raise ScoreParseError(
            f"criterion {name!r} is {value!r}; the rubric asks for whole points"
        )
    number = int(value)
    if not 0 <= number <= MAX_PER_CRITERION:
        raise ScoreParseError(
            f"criterion {name!r} is {number}, outside the rubric's 0..{MAX_PER_CRITERION}"
        )
    return number


def parse_live_card(raw: str) -> ParsedScore:
    """Read a full score card out of a model's raw text.

    Accepts the JSON object the rubric asks for, anywhere in the answer, with
    surrounding prose or a fenced block — models add both. Requires **all five**
    criteria and a verdict. Everything else raises `ScoreParseError`.

    There is deliberately no partial-credit path. A card missing `closing` could
    be recorded as a four-criterion score, and then the total is out of 16 while
    the threshold is still 14 and every comparison across items is silently
    against a different denominator. A missing criterion is a broken output
    contract, and the honest reading of a broken contract is that this item has no
    score — which is what an ERRORED verdict means.
    """
    text = (raw or "").strip()
    if not text:
        raise ScoreParseError("the model returned an empty response")

    for data in _json_objects(text):
        if not isinstance(data, dict):
            continue
        raw_verdict = data.get("verdict", data.get("label"))
        if not isinstance(raw_verdict, str):
            continue
        verdict = _VERDICT_WORDS.get(raw_verdict.strip().lower())
        if verdict is None:
            raise ScoreParseError(
                f"verdict {raw_verdict!r} is not one of pass/fail. Bare yes/no is "
                "refused on purpose: its polarity depends on the question."
            )

        block = data.get("criteria")
        if not isinstance(block, Mapping):
            raise ScoreParseError(
                "the answer carries a verdict but no `criteria` object, so the five "
                "per-criterion scores the rubric asks for are absent"
            )
        missing = [name for name in CRITERIA if name not in block]
        if missing:
            raise ScoreParseError(
                f"criteria object is missing {missing}; the rubric requires all "
                f"{len(CRITERIA)} and a partial card has no comparable total"
            )
        criteria = {name: _coerce_criterion(name, block[name]) for name in CRITERIA}

        critique = data.get("critique") or data.get("reason") or data.get("explanation")
        evidence = data.get("evidence") or data.get("quote") or data.get("span")
        return ParsedScore(
            criteria=criteria,
            verdict=verdict,
            critique=str(critique).strip() if critique else "(no critique supplied)",
            evidence=str(evidence).strip() if evidence else None,
        )

    raise ScoreParseError(
        "no score card found in the model's output. The first 300 characters were: "
        f"{text[:300]!r}"
    )


# --------------------------------------------------------------------------- #
# The result
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LiveScore:
    """One live grading: the card, its provenance, and whether it hangs together.

    `card` is a `roleplay.scorer.ScoreCard`, the same type the scripted scorer
    produces, so every contract in `roleplay.contracts` and every scenario
    assertion in the corpus points at the live scorer without modification. That
    is the whole reason the live path was built as a second implementation of one
    output shape rather than as a parallel universe.
    """

    card: ScoreCard
    raw: str
    model: str
    rubric_version: str
    evidence: str | None = None
    error: str | None = None

    @property
    def errored(self) -> bool:
        return self.card.verdict == "errored"

    @property
    def self_consistent(self) -> bool:
        """True when the model's verdict agrees with its own arithmetic.

        Not a defect on its own. The rubric explicitly overrides the total — a
        missing disclosure fails the session at any score — so `fail` over a
        passing total is the rubric working. The reverse (`pass` under 14) has no
        licence in the rubric at all, and `disagreement` says which way round it
        went so the two are never counted together.
        """
        if self.errored:
            return False
        return self.card.passed == (self.card.total >= PASS_TOTAL)

    @property
    def disagreement(self) -> str | None:
        """How the verdict and the total disagree, in the rubric's own terms."""
        if self.errored or self.self_consistent:
            return None
        if self.card.total >= PASS_TOTAL:
            return (
                f"failed a session totalling {self.card.total}/{self.card.max_total}, "
                "which the rubric permits only for a missing disclosure or a personal "
                "recommendation"
            )
        return (
            f"passed a session totalling {self.card.total}/{self.card.max_total}, "
            f"below the stated threshold of {PASS_TOTAL}, which the rubric never permits"
        )

    def summary_line(self) -> str:
        return f"{self.rubric_version} {self.card.summary_line()}"


def _errored_card(view: SessionView, *, reason: str) -> ScoreCard:
    """The card an unreadable answer produces: zeros, ERRORED, and the reason.

    Zeros rather than `None` because `ScoreCard.criteria` is typed as integers and
    a nullable score would put an `if score is None` branch into every consumer,
    including the report renderers. The zeros are not a grade and must never be
    read as one, which is what the third verdict value is for: `verdict` is
    `errored`, `passed` is False, and the feedback says so in the first sentence.
    """
    return ScoreCard(
        criteria={name: 0 for name in CRITERIA},
        raw_total=0,
        adjustment=0,
        total=0,
        max_total=MAX_PER_CRITERION * len(CRITERIA),
        verdict="errored",
        claims={
            "mandatory_disclosure_given": None,
            "unlicensed_advice_detected": None,
            "jurisdiction": view.jurisdiction,
            "language": view.language,
        },
        feedback=(
            "This session was not graded: the scoring service returned an answer "
            f"that could not be read ({reason}). No certification decision may be "
            "taken from this record."
        ),
        cohort_size=0,
    )


# --------------------------------------------------------------------------- #
# The scorer
# --------------------------------------------------------------------------- #


@dataclass
class LiveRubricScorer:
    """Grades a session by asking a model, and returns the product's own card.

    A drop-in for `roleplay.scorer.RubricScorer` in `RoleplayCoach`: it exposes
    `score_trace`, `history` and `adjustment`, so a session can be run end to end
    against a model and produce a trace of exactly the same shape. `history` and
    `adjustment` are present and inert — this scorer has no cohort curve, which is
    to say DEFECT-1 does not exist on the live path, and the attributes are kept
    only so the adapter does not need to know which scorer it is holding.

    Attributes:
        completion: The seam. `live_completion()` for a provider,
            `replay_completion()` for a committed recording, any callable for a
            test. Never constructed by default — a scorer that quietly built a
            live completion would make the opt-in gate the *second* line of
            defence rather than the first.
        model: The litellm route, recorded in every card's provenance.
        rubric_version: Which rubric file is being asked.
        strict: When True a parse failure raises. Defaults to False, which
            produces the ERRORED card instead. Both are honest; the default is the
            one that lets a 30-item study finish and report its parse-error rate
            rather than dying on item nine.
    """

    completion: Completion
    model: str
    rubric_version: str = "v1"
    strict: bool = False
    system: str | None = DEFAULT_SYSTEM
    temperature: float = 0.0
    max_tokens: int = 900
    include_tools: bool = True

    #: Present for interface compatibility with the scripted scorer, and inert.
    history: list[bool] = field(default_factory=list)
    adjustment: int = 0

    def __post_init__(self) -> None:
        self.template = rubric_prompt(self.rubric_version)
        #: Every request this instance made, for a run to report what it cost.
        self.requests: list[JudgeRequest] = []

    # ------------------------------------------------------- prompt rendering

    def fields(self, trace: Trace) -> dict[str, str]:
        return {
            "transcript": render_transcript(trace, include_tools=self.include_tools),
            "tool_ledger": render_tool_ledger(trace),
            "scenario_id": trace.scenario_id,
            "session_id": trace.session_id,
        }

    def render(self, trace: Trace) -> str:
        """The exact prompt this scorer would send. Public because reading one
        rendered prompt is the fastest way to find a rendering bug, and finding it
        before spending money is the point."""
        return self.template.render(self.fields(trace))

    def request(self, trace: Trace, *, item_id: str | None = None) -> JudgeRequest:
        return JudgeRequest(
            item_id=item_id or trace.scenario_id or trace.session_id,
            prompt=self.render(trace),
            system=self.system,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

    @property
    def prompt_sha256(self) -> str:
        return self.template.digest

    # -------------------------------------------------------------- scoring

    def score_live(self, trace: Trace, *, item_id: str | None = None) -> LiveScore:
        """Grade one session and return the full live result."""
        request = self.request(trace, item_id=item_id)
        self.requests.append(request)
        raw = self.completion(request)
        view = session_view(trace)

        try:
            parsed = parse_live_card(raw)
        except ScoreParseError as exc:
            if self.strict:
                raise ScoreParseError(
                    f"{SCORER_NAME} {self.rubric_version} could not parse the answer "
                    f"for item {request.item_id!r}: {exc}"
                ) from exc
            return LiveScore(
                card=_errored_card(view, reason=str(exc)),
                raw=raw,
                model=self.model,
                rubric_version=self.rubric_version,
                error=str(exc),
            )

        total = parsed.total
        card = ScoreCard(
            criteria=dict(parsed.criteria),
            raw_total=total,
            # No curve on the live path. Stated as an explicit zero rather than
            # omitted, so a report comparing the two scorers is comparing the same
            # fields and a reader can see that the adjustment is absent by design.
            adjustment=0,
            total=total,
            max_total=MAX_PER_CRITERION * len(CRITERIA),
            verdict=parsed.verdict,
            claims={
                # Read off the model's own criteria, exactly as the scripted
                # scorer reads them off its own. The point of the calibration study
                # is to find out whether these claims survive a diff against the
                # session's ledgers, and pre-correcting them here from the register
                # would answer the question by construction.
                "mandatory_disclosure_given": parsed.criteria["mandatory_disclosure"] >= 3,
                "unlicensed_advice_detected": parsed.criteria["no_unlicensed_advice"] < 4,
                "jurisdiction": view.jurisdiction,
                "language": view.language,
            },
            feedback=parsed.critique,
            cohort_size=0,
        )
        return LiveScore(
            card=card,
            raw=raw,
            model=self.model,
            rubric_version=self.rubric_version,
            evidence=parsed.evidence,
        )

    def score_trace(self, trace: Trace) -> ScoreCard:
        """Grade one session. The `RubricScorer` interface, so `RoleplayCoach`
        can hold either without knowing which."""
        return self.score_live(trace).card

    def score(self, view: SessionView) -> ScoreCard:  # pragma: no cover - refused
        """Refused: a model grades a transcript, not a projection of one.

        `RubricScorer.score` takes a `SessionView` because deterministic code can
        compute every criterion from those fields. A model needs the utterances in
        order and the tool ledger as written, and a `SessionView` has already
        thrown away the interleaving. Raising is better than reconstructing an
        approximation of the transcript and grading that.
        """
        raise NotImplementedError(
            "the live scorer grades a Trace, not a SessionView: call score_trace(). "
            "A SessionView has already lost the turn interleaving the rubric asks "
            "the model to read."
        )

    def reset(self) -> None:
        """No-op. There is no cross-session state on the live path to clear."""
        return None

    def __repr__(self) -> str:
        return (
            f"LiveRubricScorer(rubric={self.rubric_version}, model={self.model!r}, "
            f"graded={len(self.requests)}, prompt={self.prompt_sha256[:12]})"
        )


# --------------------------------------------------------------------------- #
# Completions
# --------------------------------------------------------------------------- #


def require_independent_scorer(*, allow_self_grading: bool = False) -> None:
    """Refuse to score a live trainee's words with the trainee's own model.

    The same exposure `lab.cli` guards on the restaurant side, in the domain that
    actually has two model seats: `LAB_LIVE_TRAINEE` puts a model in the adviser's
    chair and `LAB_LIVE_SCORER` puts one behind the rubric, both routes are read
    from the environment, and on a machine with one provider configured the
    obvious thing to do is point both at it. The rubric would then be asking a
    model whether its own advice was compliant, and every criterion on the card —
    not just the verdict — carries self-enhancement bias that no figure in
    `roleplay/scorer_study/` measures.

    Checked here rather than at each of the five places a live scorer is built,
    because this is the single seam all of them go through, and it is checked
    before the completion exists rather than after the first paid call.

    Three conditions, all required, so the check refuses only what it means to:
    the trainee switch is on, both routes are configured, and they are the same.
    A same route with a *scripted* trainee is not self-grading — nothing the model
    wrote is being graded — and refusing it would break the offline path.

    `allow_self_grading=True` is the bypass, at the call site only. See
    `lab.judges.registry.require_independent_judge` for why it is a keyword
    argument and not a flag.
    """
    from lab.judges.registry import require_independent_judge

    if not os.environ.get(LIVE_TRAINEE_ENV_VAR):
        return
    require_independent_judge(
        judge_route=os.environ.get(MODEL_ENV_VAR),
        subject_route=os.environ.get(TRAINEE_MODEL_ENV_VAR),
        judge_role="rubric scorer",
        subject_role="trainee under assessment",
        judge_env_var=MODEL_ENV_VAR,
        allow_self_grading=allow_self_grading,
    )


def live_completion(
    *,
    retry: RetryPolicy | None = None,
    extra: Mapping[str, Any] | None = None,
    allow_self_grading: bool = False,
) -> LiteLLMCompletion:
    """A real provider call, gated on `LAB_LIVE_SCORER` and on independence.

    `lab.judges.judge.LiteLLMCompletion` already parameterises its env var, so the
    scorer's opt-in switch is a different name over identical code: the same
    refusal without the flag, the same credentials-by-name check, the same
    exponential backoff honouring `Retry-After`, and the same guarantee that a
    rate limit raises rather than becoming a verdict. Reusing it is the point —
    a second implementation of provider-error handling is a second place for a 429
    to turn into a FAIL.

    The second gate is `require_independent_scorer`, which raises
    `lab.judges.registry.SelfGradingError` when the trainee is live and its route
    is the scorer's. It runs first because it costs nothing and needs no
    credential, and because the cheapest place to stop a biased measurement is
    before it is recorded.
    """
    require_independent_scorer(allow_self_grading=allow_self_grading)
    return LiteLLMCompletion(env_var=LIVE_ENV_VAR, retry=retry, extra=extra)


def replay_completion(
    path: str | Path, *, strict_prompt_hash: bool = True
) -> ReplayCompletion:
    """Answers from a committed recording, refusing a stale one.

    `strict_prompt_hash=True` is the feature and not a precaution: it turns "I
    edited the rubric and the numbers did not move" from a mystery into an
    exception. A calibration figure belongs to one rubric text, and carrying it
    across an edit is the most common way a scorer ends up trusted for behaviour
    nobody measured.
    """
    return ReplayCompletion(Recording.load(path), strict_prompt_hash=strict_prompt_hash)


def recording_completion(
    inner: Completion, *, rubric_version: str
) -> RecordingCompletion:
    """Wrap a completion so every raw answer is appended to a recording."""
    return RecordingCompletion(
        inner, judge=SCORER_NAME, prompt_version=rubric_version
    )


def record_scores(
    scorer: LiveRubricScorer,
    traces: Sequence[tuple[str, Trace]],
    path: str | Path,
) -> Path:
    """Grade `(item_id, trace)` pairs through a recording wrapper and save it.

    Returns the written path. The recording stores the model's **raw output**, not
    a parsed card, so a later replay exercises `parse_live_card` too. A replay
    layer that stored parsed cards would leave the parser untested exactly where
    it is most likely to break — on the malformed answer that made someone write
    the ERRORED path in the first place.
    """
    wrapper = recording_completion(scorer.completion, rubric_version=scorer.rubric_version)
    recorded = LiveRubricScorer(
        completion=wrapper,
        model=scorer.model,
        rubric_version=scorer.rubric_version,
        strict=False,
        system=scorer.system,
        temperature=scorer.temperature,
        max_tokens=scorer.max_tokens,
        include_tools=scorer.include_tools,
    )
    for item_id, trace in traces:
        recorded.score_live(trace, item_id=item_id)
    return wrapper.recording.save(path)
