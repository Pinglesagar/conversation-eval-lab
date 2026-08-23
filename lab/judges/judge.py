"""A judge: a prompt that turns a trace into a binary verdict plus a critique.

WHAT THIS DEMONSTRATES
----------------------
Model-graded evaluation done in the one shape that survives contact with a real
review process: **binary verdict + written critique, from a versioned prompt,
against a replayable recording.**

Four design decisions carry the module, and each one is a mistake this repo
declines to make.

**1. Binary, not a 1-5 scale.**
Scales feel more informative and measure less. A rubric that asks for 1-5
produces a number whose meaning drifts between items, between prompt versions and
between the model and the human it is supposed to agree with: two graders who
both think an answer is mediocre will split 2 versus 3 and register as
disagreeing, while two graders who disagree about whether the agent lied will
both write 2 and register as agreeing. Every downstream statistic then inherits
that noise — you cannot compute a meaningful true-positive rate against a
five-valued label without first collapsing it to a threshold, at which point the
threshold, chosen after the fact, is doing the real work. So the collapse happens
first and explicitly: one question, one bit. Everything a scale was trying to
express — severity, ambiguity, "it depends" — belongs in the critique, where a
human can read it, rather than being flattened into an ordinal that pretends to
be arithmetic.

If severity genuinely matters, the correct move is more than one binary judge
(each with its own calibration), not one judge with more values.

**2. The critique is mandatory, and it is the audit trail.**
A judge that emits only a verdict cannot be debugged. The critique is what a
human reads when calibration turns up a disagreement (`lab.judges.calibration`
lists every one of them), and it is what makes the difference between "the judge
is wrong" and "the label is wrong" visible in seconds. It is also the cheapest
prompt-improvement instrument available: the v1 -> v2 rewrite in
`lab.judges.hallucinated_confirmation` was written by reading v1's critiques on
its false positives.

**3. The prompt is versioned, and a prompt change invalidates a calibration.**
`Judge.with_prompt()` returns a new judge with **no** calibration attached, and
`ReplayJudge` refuses a recording whose prompt digest no longer matches the
prompt being run. An agreement figure is a property of a specific prompt against
a specific labelled set; carrying it forward across an edit is the most common
way a judge ends up trusted for behaviour it was never measured on.

**4. Parse failures fail closed, loudly, and are counted.**
If the model returns something the parser cannot read, the judge raises
(`strict=True`, the default) or records a FAIL flagged with `parse_error=True`.
It never defaults to "pass". A judge that defaults to pass converts a provider
outage into a green build, which is the single worst failure mode available to an
evaluation harness: it is silent, it is systematic, and it always resolves in the
direction of shipping.

RUNS WITH NO API KEY
--------------------
`Judge` never talks to a provider directly. It calls a `Completion` — a callable
taking a `JudgeRequest` and returning the model's raw text. Swap the callable and
the same judge runs live (`LiteLLMCompletion`, any provider litellm routes to),
against a recording (`ReplayCompletion`, used by `ReplayJudge`), or against a
scripted table (`ScriptedCompletion`, used by unit tests). Recordings store the
model's **raw output**, not a parsed verdict, so replay exercises the parser too;
a replay layer that stored parsed verdicts would leave the parser untested
exactly where it is most likely to break.

`LiteLLMCompletion` refuses to run unless the opt-in environment variable
`LAB_LIVE_JUDGE` is set, and it imports `litellm` lazily *after* that check — so
an accidental live call in a test suite raises immediately instead of hanging on
a network connection or, worse, quietly spending money.

Model ids are not defaulted anywhere in this module. `model` is a required
argument, or comes from `LAB_JUDGE_MODEL` via `model_from_env()`. A framework
that ships a default model has baked a vendor into itself; the point of routing
through litellm is that the caller chooses (`anthropic/claude-sonnet-5`,
`openai/...`, a local OpenAI-compatible endpoint, whatever they hold keys for).

WHAT THIS DOES NOT DO
---------------------
No ensembling, no self-consistency **voting**, no chain-of-thought scaffolding, no
few-shot example selection. All of them can raise agreement; none of them can be
believed before the single-call case has a measured true-positive and
true-negative rate, and each multiplies cost per item. Note the distinction on
voting: `lab.judges.calibration.self_consistency` *measures* how often repeated
runs of one judge agree with each other, and reports the items that moved.
Averaging those runs into a verdict instead would spend three calls per item to
make the instability invisible, which is the opposite of what an evaluation
harness is for. The calibration machinery in `lab.judges.calibration` is
deliberately the sophisticated part of this package and the judge itself is
deliberately dull.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Iterable,
    Literal,
    Mapping,
    Protocol,
    Sequence,
)

from pydantic import BaseModel, ConfigDict, Field

from lab.trace.schema import EventKind, Trace

if TYPE_CHECKING:  # pragma: no cover - typing only; no runtime import cycle
    from lab.judges.calibration import CalibrationReport

__all__ = [
    "LIVE_ENV_VAR",
    "MODEL_ENV_VAR",
    "Label",
    "Verdict",
    "JudgeRequest",
    "Completion",
    "PromptTemplate",
    "Judge",
    "ReplayJudge",
    "ScriptedCompletion",
    "ReplayCompletion",
    "RecordingCompletion",
    "LiteLLMCompletion",
    "RetryPolicy",
    "RateLimitedError",
    "RETRYABLE_STATUS",
    "is_retryable",
    "Recording",
    "RecordedCall",
    "JudgeError",
    "JudgeParseError",
    "LiveCallBlockedError",
    "MissingRecordingError",
    "StaleRecordingError",
    "PromptTemplateError",
    "MissingCredentialsError",
    "model_from_env",
    "prompt_digest",
    "PROVIDER_ENV_VARS",
    "Status",
    "render_transcript",
    "render_tool_ledger",
    "parse_raw_verdict",
    "record_verdicts",
]

LOGGER = logging.getLogger("lab.judges.judge")

#: Opt-in switch for live provider calls. Absent or falsey means "offline".
LIVE_ENV_VAR = "LAB_LIVE_JUDGE"

#: Where `model_from_env()` reads the litellm model route from.
MODEL_ENV_VAR = "LAB_JUDGE_MODEL"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

#: A judge's answer, and a human's label, share one vocabulary. Using the same
#: two strings for both sides is not cosmetic: the commonest bug in agreement
#: code is an inverted boolean, and it cannot happen if nothing is ever inverted.
Label = Literal["pass", "fail"]

#: A verdict's *state*, which is not the same thing as its label. `Label` has two
#: values because the question has two answers; `Status` has three because a call
#: can also fail to produce an answer at all. Keeping them as separate types is
#: what stops "the model returned junk" from being quietly rounded to one of the
#: two real answers: agreement arithmetic reads `Label`, and an operator reads
#: `Status` to see that an item never got graded.
Status = Literal["pass", "fail", "error"]

#: The environment variables litellm itself reads for provider credentials. Listed
#: here as *names only*, because that is all a repository should ever contain, and
#: because "which variables do I need to set to go live" is otherwise answered by
#: reading a stack trace.
#:
#:     provider              variables
#:     openai                OPENAI_API_KEY
#:     anthropic             ANTHROPIC_API_KEY
#:     azure openai          AZURE_API_KEY, AZURE_API_BASE, AZURE_API_VERSION
#:     bedrock               AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION_NAME
#:
#: The model route in `LAB_JUDGE_MODEL` picks the provider — `openai/gpt-4.1`,
#: `anthropic/claude-sonnet-5`, `azure/<deployment-name>`. Nothing in `lab` reads
#: these variables directly; they are documented, passed through, and never
#: printed. `LiteLLMCompletion.missing_credentials()` checks presence only, so a
#: misconfigured run fails before it is billed rather than after.
PROVIDER_ENV_VARS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "azure": ("AZURE_API_KEY", "AZURE_API_BASE", "AZURE_API_VERSION"),
    "gemini": ("GEMINI_API_KEY",),
    "bedrock": ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION_NAME"),
}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class JudgeError(RuntimeError):
    """Base class for every failure this module raises."""


class JudgeParseError(JudgeError):
    """The model's output contained no readable verdict.

    Raised rather than defaulted, because both defaults are wrong: defaulting to
    pass hides real defects, and defaulting to fail silently manufactures them.
    Callers that must tolerate garbage set `strict=False`, which records a FAIL
    flagged `parse_error=True` so the calibration report can count it.
    """


class LiveCallBlockedError(JudgeError):
    """A live provider call was attempted without the opt-in env var set."""


class MissingRecordingError(JudgeError):
    """The recording has no entry for this item."""


class StaleRecordingError(JudgeError):
    """The recorded prompt no longer matches the prompt being run.

    The recording is keyed by item, but validated by prompt digest. Editing a
    prompt and replaying old verdicts against it would produce an agreement
    figure for a prompt that no longer exists — the exact way a judge comes to be
    trusted for behaviour nobody measured.
    """


class PromptTemplateError(JudgeError):
    """The prompt template references a field the judge cannot supply."""


class MissingCredentialsError(JudgeError):
    """A live call was requested but the provider's environment variables are unset.

    Raised *before* the request, naming the variables by name and never by value.
    A run that is going to fail on authentication should fail in the first second,
    not twenty items in.
    """


class RateLimitedError(JudgeError):
    """The provider kept rate-limiting or failing after the retry budget ran out.

    Deliberately not swallowed into an unparseable answer. A 429 is a fact about
    the harness's throughput, not a fact about the agent under test, and letting
    it become a FAIL verdict would put the provider's load into the measurement.
    """


# --------------------------------------------------------------------------- #
# Rendering a trace for a judge
# --------------------------------------------------------------------------- #


def render_transcript(trace: Trace, *, include_tools: bool = False) -> str:
    """Render the conversation as speaker-labelled lines.

    `include_tools=False` (the default) renders utterances only. That is a
    deliberate scoping decision, not a shortcut: a judge shown the tool ledger
    can answer "was this claim true" by looking it up, which is a question code
    answers for free and with no variance. Withholding the ledger keeps a judge
    on the half of the question that actually needs judgement — what the words
    assert — and keeps its verdict composable with a deterministic check over the
    same trace. See `lab.judges.hallucinated_confirmation` for the worked case.

    `include_tools=True` renders `[tool]` lines interleaved in time order, for
    judges whose question genuinely spans both (a tool-choice rubric, say).
    """
    kinds = {EventKind.CALLER_UTTERANCE, EventKind.AGENT_UTTERANCE}
    if include_tools:
        kinds |= {EventKind.TOOL_CALL, EventKind.TOOL_RESULT, EventKind.AGENT_HANDOFF}

    lines: list[str] = []
    for event in trace.events:
        if event.kind not in kinds:
            continue
        if event.kind == EventKind.CALLER_UTTERANCE:
            lines.append(f"caller: {event.get('text', '')}")
        elif event.kind == EventKind.AGENT_UTTERANCE:
            speaker = event.get("agent")
            who = f"agent ({speaker})" if speaker else "agent"
            lines.append(f"{who}: {event.get('text', '')}")
        elif event.kind == EventKind.TOOL_CALL:
            args = json.dumps(event.get("args", {}), sort_keys=True)
            lines.append(f"[tool call] {event.get('name')}({args})")
        elif event.kind == EventKind.TOOL_RESULT:
            status = "ok" if event.get("ok", True) else f"error: {event.get('error')}"
            lines.append(f"[tool result] {event.get('name')} -> {status}")
        elif event.kind == EventKind.AGENT_HANDOFF:
            lines.append(f"[handoff] {event.get('from')} -> {event.get('to')}")
    return "\n".join(lines)


def render_tool_ledger(trace: Trace) -> str:
    """Render every tool call with its outcome, one per line.

    Available to prompts as `{{tool_ledger}}`. Offered separately from the
    transcript so that a prompt has to ask for tool evidence explicitly, and so
    that a reader of the prompt can see at a glance whether the judge was given
    it — see `render_transcript` for why that matters.
    """
    results: dict[str, Any] = {}
    for event in trace.events_of_kind(EventKind.TOOL_RESULT):
        call_id = event.get("call_id")
        if call_id is not None:
            results[str(call_id)] = event

    lines: list[str] = []
    for call in trace.events_of_kind(EventKind.TOOL_CALL):
        args = json.dumps(call.get("args", {}), sort_keys=True)
        result = results.get(str(call.get("call_id")))
        if result is None:
            outcome = "no result recorded"
        elif result.get("ok", True):
            outcome = "ok"
        else:
            outcome = f"failed ({result.get('error')})"
        lines.append(f"{call.get('name')}({args}) -> {outcome}")
    return "\n".join(lines) if lines else "(no tools were called)"


class PromptTemplate:
    """A judge prompt with `{{field}}` placeholders, validated at construction.

    **Why `{{field}}` and not `str.format`.** Judge prompts routinely contain a
    literal JSON output contract, and `"{"verdict": "pass"}"` is not a valid
    `str.format` template — it raises `KeyError: '"verdict"'` at render time, in
    production, on a prompt that reads perfectly well. Double-brace tokens with
    plain substitution mean a prompt can talk about JSON, braces, or code without
    the templating layer having an opinion.

    Placeholders are checked against `FIELDS` at construction, so a typo
    (`{{transcirpt}}`) fails when the judge is built rather than silently
    rendering the literal token into the prompt and quietly asking the model to
    grade nothing.
    """

    #: Everything a judge can render from a trace. Small on purpose: a prompt
    #: that needs a field not in this list needs a code change, which is a review
    #: point rather than an accident.
    FIELDS: tuple[str, ...] = ("transcript", "tool_ledger", "scenario_id", "session_id")

    _TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

    def __init__(self, text: str, *, require_transcript: bool = True) -> None:
        self.text = text
        self.placeholders: tuple[str, ...] = tuple(
            dict.fromkeys(self._TOKEN.findall(text))
        )

        unknown = [p for p in self.placeholders if p not in self.FIELDS]
        if unknown:
            raise PromptTemplateError(
                f"prompt references unknown field(s) {unknown}; "
                f"available fields are {list(self.FIELDS)}"
            )
        if require_transcript and "transcript" not in self.placeholders:
            raise PromptTemplateError(
                "prompt has no {{transcript}} placeholder, so the judge would grade "
                "an empty conversation. Pass require_transcript=False if that is "
                "genuinely intended."
            )

    @classmethod
    def from_path(cls, path: str | Path, **kwargs: Any) -> PromptTemplate:
        """Load a prompt from a file. Prompts are text, and text belongs in files."""
        return cls(Path(path).read_text(encoding="utf-8"), **kwargs)

    def render(self, fields: Mapping[str, str]) -> str:
        """Substitute the placeholders. Missing values render as empty strings."""

        def replace(match: re.Match[str]) -> str:
            return str(fields.get(match.group(1), ""))

        return self._TOKEN.sub(replace, self.text)

    @property
    def digest(self) -> str:
        """Stable sha256 of the prompt text — the recording's staleness key."""
        return prompt_digest(self.text)

    def __repr__(self) -> str:
        return (
            f"PromptTemplate(chars={len(self.text)}, "
            f"placeholders={list(self.placeholders)}, digest={self.digest[:12]})"
        )


def prompt_digest(text: str) -> str:
    """sha256 of a prompt string, hex. Used to detect stale recordings."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Requests, verdicts, and the completion boundary
# --------------------------------------------------------------------------- #


class JudgeRequest(BaseModel):
    """Everything needed to obtain one raw model answer, and nothing else.

    Carries `item_id` alongside the prompt so that a completion implementation
    can be keyed by item (recordings, scripted tables) without the judge having
    to know which kind it is holding.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    prompt: str
    system: str | None = None
    model: str
    temperature: float = 0.0
    max_tokens: int = 512

    @property
    def prompt_sha256(self) -> str:
        return prompt_digest(self.prompt)


class Verdict(BaseModel):
    """One judgement: a bit, a critique, and the provenance to audit both."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    passed: bool
    critique: str
    evidence: str | None = Field(
        default=None,
        description="A quoted span the judge based its verdict on, when the prompt asked for one.",
    )
    judge: str
    prompt_version: str
    model: str
    raw: str | None = Field(
        default=None, description="The model's unparsed output, kept for audit."
    )
    parse_error: bool = Field(
        default=False,
        description="True when the verdict was forced to FAIL because the output was unreadable.",
    )

    @property
    def label(self) -> Label:
        """The verdict in the same vocabulary human labels use.

        Always one of two values, including for an errored item: agreement
        arithmetic needs a cell to put every item in, and an errored item is
        counted as a FAIL so that it can never be mistaken for a clean pass. Read
        `status` to find out whether the FAIL was a judgement or a breakage.
        """
        return "pass" if self.passed else "fail"

    @property
    def status(self) -> Status:
        """`"pass"`, `"fail"`, or `"error"` — the three states a call can end in.

        `"error"` is the state this module refuses to let disappear. An
        unreadable answer is not a lenient pass and not a genuine fail; it is a
        broken output contract, and `CalibrationThresholds.max_parse_error_rate`
        defaults to zero so a report containing any of them cannot clear a gate.
        """
        if self.parse_error:
            return "error"
        return self.label

    @property
    def errored(self) -> bool:
        """True when no verdict could be read out of the model's answer."""
        return self.parse_error

    def __repr__(self) -> str:
        return (
            f"Verdict(item_id={self.item_id!r}, label={self.label!r}, "
            f"status={self.status!r})"
        )


class Completion(Protocol):
    """Turns a `JudgeRequest` into the model's raw text.

    The seam that makes this package runnable with no API key. Everything the
    judge does either side of this call — rendering, parsing, verdict
    construction — is identical live and offline, so offline tests exercise the
    real code path rather than a simplified one.
    """

    def __call__(self, request: JudgeRequest) -> str: ...


class ScriptedCompletion:
    """Answers from an in-memory `{item_id: raw_text}` table. For tests.

    Records nothing and validates no digest: a unit test that wants to see how
    the judge behaves when the model says `"banana"` should not have to write a
    recording file first.
    """

    def __init__(self, answers: Mapping[str, str]) -> None:
        self.answers = dict(answers)
        self.calls: list[JudgeRequest] = []

    def __call__(self, request: JudgeRequest) -> str:
        self.calls.append(request)
        try:
            return self.answers[request.item_id]
        except KeyError as exc:
            raise MissingRecordingError(
                f"no scripted answer for item {request.item_id!r}"
            ) from exc


class RecordedCall(BaseModel):
    """One recorded model answer, with the prompt digest that produced it."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    judge: str
    prompt_version: str
    model: str
    prompt_sha256: str
    raw: str


class Recording(BaseModel):
    """A JSONL file of `RecordedCall`s, keyed by item id.

    Same format choice as `lab.trace.io`, for the same reasons: one JSON object
    per line diffs readably in review, so a reviewer can see which judgements
    changed when a prompt changed.
    """

    model_config = ConfigDict(extra="forbid")

    calls: list[RecordedCall] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> Recording:
        source = Path(path)
        calls: list[RecordedCall] = []
        with source.open("r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    calls.append(RecordedCall.model_validate(json.loads(stripped)))
                except Exception as exc:  # noqa: BLE001 - re-raised with location
                    raise ValueError(
                        f"{source}:{lineno}: not a valid RecordedCall: {exc}"
                    ) from exc
        return cls(calls=calls)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(call.model_dump(mode="json"), sort_keys=True) for call in self.calls
        ]
        target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return target

    def by_item(self) -> dict[str, RecordedCall]:
        """Index by item id. A duplicate item id is an error, not a last-wins."""
        index: dict[str, RecordedCall] = {}
        for call in self.calls:
            if call.item_id in index:
                raise ValueError(
                    f"recording contains two entries for item {call.item_id!r}; "
                    "which one replays would depend on file order"
                )
            index[call.item_id] = call
        return index

    def __len__(self) -> int:
        return len(self.calls)


class ReplayCompletion:
    """Answers from a recording, checking the prompt has not changed since.

    `strict_prompt_hash=True` (the default) is the feature: it turns "I edited
    the prompt and the calibration numbers didn't move" from a mystery into an
    exception. Set it False only to inspect an old recording deliberately.
    """

    def __init__(
        self,
        recording: Recording | str | Path,
        *,
        strict_prompt_hash: bool = True,
    ) -> None:
        loaded = (
            recording
            if isinstance(recording, Recording)
            else Recording.load(recording)
        )
        self.recording = loaded
        self.index = loaded.by_item()
        self.strict_prompt_hash = strict_prompt_hash

    def __call__(self, request: JudgeRequest) -> str:
        entry = self.index.get(request.item_id)
        if entry is None:
            raise MissingRecordingError(
                f"no recorded answer for item {request.item_id!r} "
                f"({len(self.index)} items in the recording). Re-record with "
                "lab.judges.judge.record_verdicts()."
            )
        if self.strict_prompt_hash and entry.prompt_sha256 != request.prompt_sha256:
            raise StaleRecordingError(
                f"item {request.item_id!r} was recorded against prompt "
                f"{entry.prompt_sha256[:12]} but the prompt now renders as "
                f"{request.prompt_sha256[:12]}. The recording is stale: re-record, or "
                "pass strict_prompt_hash=False if you are deliberately inspecting "
                "old verdicts."
            )
        return entry.raw


class RecordingCompletion:
    """Wraps another completion and appends every answer to a recording.

    How the fixtures in this repo are made. It records the raw text and the
    prompt digest, so a later replay can prove it is answering the same question
    that was asked.
    """

    def __init__(self, inner: Completion, *, judge: str, prompt_version: str) -> None:
        self.inner = inner
        self.judge = judge
        self.prompt_version = prompt_version
        self.recording = Recording()

    def __call__(self, request: JudgeRequest) -> str:
        raw = self.inner(request)
        self.recording.calls.append(
            RecordedCall(
                item_id=request.item_id,
                judge=self.judge,
                prompt_version=self.prompt_version,
                model=request.model,
                prompt_sha256=request.prompt_sha256,
                raw=raw,
            )
        )
        return raw


#: HTTP statuses worth trying again. 429 is the rate limit; 5xx and 408 are the
#: transient server-side failures. 400/401/403/404 are deliberately absent: a bad
#: request, a bad key or a wrong deployment name will fail identically on every
#: retry, and retrying them turns a five-second configuration error into a
#: two-minute one.
RETRYABLE_STATUS: frozenset[int] = frozenset({408, 409, 429, 500, 502, 503, 504})

#: Exception class names treated as transient when the exception carries no status
#: code. Matched by name rather than by class so that this module still does not
#: import litellm until a live call is actually made.
RETRYABLE_EXCEPTION_NAMES: frozenset[str] = frozenset(
    {
        "RateLimitError",
        "Timeout",
        "APITimeoutError",
        "APIConnectionError",
        "APIError",
        "InternalServerError",
        "ServiceUnavailableError",
        "OverloadedError",
    }
)


class RetryPolicy(BaseModel):
    """Exponential backoff for rate limits and transient provider failures.

    Three properties are the reason this is a declared object rather than a
    `for attempt in range(3)` inside the call:

    *   **The schedule is inspectable and testable.** `delays()` returns the exact
        sequence of waits, so a test can assert the backoff without waiting for
        it, and a run can print what it is about to do.
    *   **`Retry-After` wins.** When the provider says how long to wait, that
        number is used instead of the computed one (clamped by `max_delay`).
        Guessing shorter than the server asked is how a rate-limited run becomes
        a rate-limited run that also gets throttled harder.
    *   **A rate limit pauses everything, not just the failing item.** A 429 is a
        statement about the account, so `LiteLLMCompletion` holds *every*
        subsequent request until the pause expires. Backing off only the item that
        happened to be unlucky just re-hits the limit with the next one.

    Retries are also why a judge must be a measuring instrument with temperature
    0: a retried call is a second sample, and if sampling were noisy the retry
    would silently change the verdict rather than recover it.
    """

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=5, ge=1)
    base_delay: float = Field(default=2.0, ge=0.0)
    multiplier: float = Field(default=2.0, ge=1.0)
    max_delay: float = Field(default=60.0, ge=0.0)

    def delays(self) -> list[float]:
        """The wait before each retry, in order. `max_attempts=5` -> four waits."""
        return [self.delay_for(attempt) for attempt in range(1, self.max_attempts)]

    def delay_for(self, attempt: int) -> float:
        """Wait before retry number `attempt` (1-based)."""
        if attempt < 1:
            raise ValueError("attempt is 1-based")
        return min(self.base_delay * (self.multiplier ** (attempt - 1)), self.max_delay)

    def clamp(self, seconds: float) -> float:
        """A server-supplied delay, bounded by `max_delay`."""
        return max(0.0, min(float(seconds), self.max_delay))


def _status_code_of(exc: BaseException) -> int | None:
    for attribute in ("status_code", "http_status", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _retry_after_of(exc: BaseException) -> float | None:
    """A provider-supplied `Retry-After`, in seconds, if there is one."""
    direct = getattr(exc, "retry_after", None)
    if isinstance(direct, (int, float)) and not isinstance(direct, bool):
        return float(direct)
    headers = getattr(exc, "headers", None) or getattr(
        getattr(exc, "response", None), "headers", None
    )
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:  # noqa: BLE001 - a header mapping that will not map
        return None
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def is_retryable(exc: BaseException) -> bool:
    """Whether `exc` is worth another attempt.

    Status code first, class name second. Anything else is treated as permanent,
    because a retry loop over a permanent error is indistinguishable from a hang.
    """
    status = _status_code_of(exc)
    if status is not None:
        return status in RETRYABLE_STATUS
    return type(exc).__name__ in RETRYABLE_EXCEPTION_NAMES


class LiteLLMCompletion:
    """A live provider call through litellm, gated behind an env var.

    Three properties matter more than the call itself:

    *   **It refuses by default.** Without `LAB_LIVE_JUDGE` set to a truthy
        value it raises `LiveCallBlockedError`. The cardinal rule of this repo is
        that the suite passes offline with no keys; a live path that is merely
        "not usually taken" breaks that rule the first time someone forgets.
    *   **`litellm` is imported after the gate.** Import cost and import-time
        side effects are only paid by runs that really are going live.
    *   **Rate limits are absorbed, never graded.** A 429 is retried with
        exponential backoff (`RetryPolicy`), honouring `Retry-After`, and it pauses
        every subsequent request rather than only the unlucky one. When the budget
        runs out it raises `RateLimitedError` — never a verdict. Provider load must
        not be able to leak into a calibration number.

    Credentials are read by litellm from its own environment variables; see
    `PROVIDER_ENV_VARS` for the names. This class checks that they are *present*
    and never looks at their values, so a misconfigured run fails immediately and
    no log line can carry a secret.

    `sleep` and `monotonic` are injected so the backoff is unit-testable without
    a test suite that actually sleeps — the same seam `lab.clock` gives the trace
    builder, for the same reason.
    """

    def __init__(
        self,
        *,
        env_var: str = LIVE_ENV_VAR,
        extra: Mapping[str, Any] | None = None,
        retry: RetryPolicy | None = None,
        require_credentials: bool = True,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.env_var = env_var
        self.extra = dict(extra or {})
        self.retry = retry if retry is not None else RetryPolicy()
        self.require_credentials = require_credentials
        self._sleep = sleep
        self._monotonic = monotonic
        #: Set after a rate limit; every request waits for it. Shared across items
        #: because the limit is a property of the account, not of the item.
        self._pause_until: float = 0.0
        #: Attempt counters, for a run to report what the provider cost it.
        self.attempts = 0
        self.retries = 0

    def enabled(self) -> bool:
        return os.environ.get(self.env_var, "").strip().lower() in _TRUTHY

    @staticmethod
    def provider_of(model: str) -> str | None:
        """The provider prefix of a litellm route, if it has one."""
        prefix, _, rest = model.partition("/")
        return prefix if rest else None

    @classmethod
    def missing_credentials(cls, model: str) -> list[str]:
        """Which of this provider's environment variables are unset. Names only."""
        provider = cls.provider_of(model)
        required = PROVIDER_ENV_VARS.get(provider or "", ())
        return [name for name in required if not os.environ.get(name, "").strip()]

    def __call__(self, request: JudgeRequest) -> str:
        if not self.enabled():
            raise LiveCallBlockedError(
                f"live judge calls are opt-in: set {self.env_var}=1 to allow a real "
                f"provider call for model {request.model!r}. Offline runs should use "
                "ReplayJudge against a recording."
            )
        if self.require_credentials:
            missing = self.missing_credentials(request.model)
            if missing:
                raise MissingCredentialsError(
                    f"model route {request.model!r} needs these environment variables "
                    f"set, and they are not: {', '.join(missing)}. (Names only — this "
                    "harness never reads or prints a credential's value.)"
                )

        from litellm import completion  # noqa: PLC0415 - lazy on purpose

        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        last: BaseException | None = None
        for attempt in range(1, self.retry.max_attempts + 1):
            self._wait_for_pause()
            self.attempts += 1
            try:
                response = completion(
                    model=request.model,
                    messages=messages,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    **self.extra,
                )
            except Exception as exc:  # noqa: BLE001 - re-raised or retried below
                if not is_retryable(exc):
                    raise
                last = exc
                if attempt == self.retry.max_attempts:
                    break
                delay = self._delay_after(exc, attempt)
                self.retries += 1
                LOGGER.warning(
                    "judge call for item %r hit %s (attempt %d/%d); backing off %.1fs",
                    request.item_id,
                    type(exc).__name__,
                    attempt,
                    self.retry.max_attempts,
                    delay,
                )
                self._pause_until = self._monotonic() + delay
                continue
            return response.choices[0].message.content or ""

        raise RateLimitedError(
            f"gave up on item {request.item_id!r} after {self.retry.max_attempts} "
            f"attempts against {request.model!r}; last error was "
            f"{type(last).__name__ if last else 'unknown'}: {last}. This is a harness "
            "throughput problem, not a verdict — nothing was recorded for this item."
        ) from last

    # ---------------------------------------------------------------- backoff

    def _delay_after(self, exc: BaseException, attempt: int) -> float:
        supplied = _retry_after_of(exc)
        if supplied is not None:
            return self.retry.clamp(supplied)
        return self.retry.delay_for(attempt)

    def _wait_for_pause(self) -> None:
        remaining = self._pause_until - self._monotonic()
        if remaining > 0:
            self._sleep(remaining)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

#: The only two words accepted as a bare verdict.
#:
#: "yes" and "no" are deliberately absent. Their meaning depends on the polarity
#: of the question — "no, it didn't claim a booking" is a *pass* under this
#: judge's rubric and a *fail* under a rubric phrased the other way round — and
#: the parser cannot see the question. Accepting them would make the verdict
#: depend on a prompt's phrasing, which is precisely the kind of silent,
#: systematic error this package exists to keep out. An answer of "no" is
#: therefore unparseable, and unparseable fails closed.
_VERDICT_WORDS: dict[str, bool] = {
    "pass": True,
    "passed": True,
    "fail": False,
    "failed": False,
}

#: Inside a JSON object the key names the polarity (`{"pass": true}`), so booleans
#: and true/false are unambiguous there and accepted only there.
_JSON_VERDICT_WORDS: dict[str, bool] = {**_VERDICT_WORDS, "true": True, "false": False}

_LEADING_VERDICT = re.compile(
    r"""^\s*
        (?:\*{0,2}(?:verdict|answer|result)\*{0,2}\s*[:=\-]\s*)?   # optional label
        \*{0,2}(pass(?:ed)?|fail(?:ed)?)\*{0,2}                    # the verdict word
        \b""",
    re.IGNORECASE | re.VERBOSE,
)

def _json_candidates(text: str) -> Iterable[str]:
    """Every balanced `{...}` span in `text`, outermost first, left to right.

    A single greedy brace-to-brace regex is not enough in practice. Models wrap the object in
    a fenced block, or add a sentence afterwards that happens to contain a brace,
    and then the greedy span spans from the first brace to the last one and parses
    as nothing. Scanning for balanced spans instead means the judge tolerates the
    formatting models actually produce, without the parser ever *guessing* a
    verdict: a candidate still has to parse as JSON and still has to carry a
    verdict key.

    Braces inside string literals are skipped, so a critique that mentions `{}`
    does not unbalance the scan.
    """
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
                    yield text[start : index + 1]


def parse_raw_verdict(raw: str) -> tuple[bool, str, str | None]:
    """Read `(passed, critique, evidence)` out of a model's raw text.

    Two formats are accepted, in this order:

    1.  **A JSON object** anywhere in the text, with a `verdict` (or `pass` /
        `label`) key. `critique` / `reason` / `explanation` supplies the
        critique, `evidence` / `quote` the span. Fenced code blocks and
        surrounding prose are tolerated — models add them.
    2.  **A leading verdict word** — `pass` or `fail` only, optionally labelled
        (`VERDICT: FAIL`, `**fail**`, `Fail - the agent ...`), with the remaining
        text as the critique.

    Anything else raises `JudgeParseError`, including a bare "yes"/"no" (whose
    meaning depends on the question's polarity — see `_VERDICT_WORDS`) and any
    text where the verdict is buried mid-sentence. There is deliberately no third
    fallback that scans the whole text for the word "fail": a critique explaining
    why the answer did *not* fail contains that word too, and a parser that
    guesses produces verdicts that no prompt asked for.
    """
    text = (raw or "").strip()
    if not text:
        raise JudgeParseError("model returned an empty response")

    for candidate in _json_candidates(text):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        verdict_value = next(
            (data[key] for key in ("verdict", "pass", "passed", "label") if key in data),
            None,
        )
        passed = _coerce_verdict(verdict_value)
        if passed is not None:
            critique = _first_str(data, ("critique", "reason", "explanation", "rationale"))
            evidence = _first_str(data, ("evidence", "quote", "span"))
            return passed, critique or "(no critique supplied)", evidence

    leading = _LEADING_VERDICT.match(text)
    if leading is not None:
        passed = _VERDICT_WORDS[leading.group(1).lower()]
        critique = text[leading.end() :].lstrip(" \t\r\n:;,.-—–").strip()
        return passed, critique or "(no critique supplied)", None

    raise JudgeParseError(
        "no verdict found in the model's output. The first 200 characters were: "
        f"{text[:200]!r}"
    )


def _coerce_verdict(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _JSON_VERDICT_WORDS.get(value.strip().lower())
    return None


def _first_str(data: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def model_from_env(default: str | None = None, *, env_var: str = MODEL_ENV_VAR) -> str:
    """Read the litellm model route from the environment.

    No model id is hardcoded anywhere in `lab`. Providers, prices and ids move;
    a harness that pins one has an expiry date, and a harness that defaults to
    one silently bills whoever forgot to look.
    """
    value = os.environ.get(env_var) or default
    if not value:
        raise JudgeError(
            f"no judge model configured: set {env_var} (e.g. "
            f"{env_var}=anthropic/claude-sonnet-5) or pass model= explicitly."
        )
    return value


# --------------------------------------------------------------------------- #
# The judge
# --------------------------------------------------------------------------- #

DEFAULT_SYSTEM = (
    "You are a careful evaluator of transcripts. You answer exactly one binary "
    "question about the transcript you are given, you follow the required output "
    "format exactly, and you never invent facts that are not in the transcript."
)


class Judge:
    """A versioned prompt, a completion, and a parser. Nothing more.

    Deliberately not a base class to subclass per rubric: a judge differs from
    another judge in its *prompt*, and expressing that as a Python class hierarchy
    would put the interesting content in code review rather than in a text file a
    domain expert can read and edit. Judges are constructed, not inherited from —
    the one exception being `ReplayJudge`, which changes the completion, not the
    question.

    A judge carries its latest `CalibrationReport` on `judge.calibration`.
    `lab.judges.registry.require_calibrated()` is what turns that attribute into
    a gate.
    """

    def __init__(
        self,
        *,
        name: str,
        prompt: str | PromptTemplate,
        version: str,
        model: str,
        completion: Completion | None = None,
        system: str | None = DEFAULT_SYSTEM,
        temperature: float = 0.0,
        max_tokens: int = 512,
        include_tools: bool = False,
        strict: bool = True,
    ) -> None:
        """
        Args:
            name: Stable identifier for the property being judged, e.g.
                "hallucinated_confirmation". Appears in reports and gate errors.
            prompt: The rubric, as text or a `PromptTemplate`.
            version: Prompt version, e.g. "v1". Required, not defaulted: a
                calibration report that cannot name the prompt it measured is not
                evidence about anything.
            model: litellm model route. Required — see `model_from_env`.
            completion: How to obtain raw text. Defaults to `LiteLLMCompletion`,
                which refuses to run without the opt-in env var.
            system: System prompt; None sends the user message alone.
            temperature: Defaults to 0.0. A judge is a measuring instrument, and
                sampling temperature is variance injected into the instrument.
            include_tools: Whether `{{transcript}}` includes tool lines. False by
                default — see `render_transcript`.
            strict: True raises `JudgeParseError` on unreadable output; False
                records a FAIL with `parse_error=True`. Never passes.
        """
        self.name = name
        self.template = prompt if isinstance(prompt, PromptTemplate) else PromptTemplate(prompt)
        self.version = version
        self.model = model
        self.system = system
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.include_tools = include_tools
        self.strict = strict
        self._completion: Completion = (
            completion if completion is not None else LiteLLMCompletion()
        )
        self.calibration: CalibrationReport | None = None

    # ------------------------------------------------------------- rendering

    def fields(self, trace: Trace) -> dict[str, str]:
        """The values available to the prompt for this trace."""
        return {
            "transcript": render_transcript(trace, include_tools=self.include_tools),
            "tool_ledger": render_tool_ledger(trace),
            "scenario_id": trace.scenario_id,
            "session_id": trace.session_id,
        }

    def render(self, trace: Trace) -> str:
        """The exact prompt this judge would send for `trace`.

        Public because prompt review is a real workflow: the fastest way to
        understand a judge is to read one rendered prompt, and the fastest way to
        find a rendering bug is to look at it before spending money on it.
        """
        return self.template.render(self.fields(trace))

    def request(self, trace: Trace, *, item_id: str | None = None) -> JudgeRequest:
        """Build the request for `trace` without sending it."""
        return JudgeRequest(
            item_id=item_id or trace.session_id,
            prompt=self.render(trace),
            system=self.system,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

    # -------------------------------------------------------------- judging

    def judge(self, trace: Trace, *, item_id: str | None = None) -> Verdict:
        """Judge one trace. Returns a binary verdict plus its critique."""
        request = self.request(trace, item_id=item_id)
        raw = self._completion(request)
        try:
            passed, critique, evidence = parse_raw_verdict(raw)
        except JudgeParseError as exc:
            if self.strict:
                raise JudgeParseError(
                    f"{self.name} {self.version} could not parse the answer for item "
                    f"{request.item_id!r}: {exc}"
                ) from exc
            # Fail closed, and say so. An unreadable answer is a broken output
            # contract, which is a defect in the judge — recorded as a FAIL and
            # counted separately so a calibration gate can refuse it.
            return Verdict(
                item_id=request.item_id,
                passed=False,
                critique=f"unparseable judge output, failed closed: {exc}",
                judge=self.name,
                prompt_version=self.version,
                model=self.model,
                raw=raw,
                parse_error=True,
            )
        return Verdict(
            item_id=request.item_id,
            passed=passed,
            critique=critique,
            evidence=evidence,
            judge=self.name,
            prompt_version=self.version,
            model=self.model,
            raw=raw,
        )

    def judge_all(self, traces: Iterable[Trace]) -> list[Verdict]:
        """Judge many traces, in order. Sequential on purpose: concurrency here
        buys wall-clock time and costs reproducibility of rate-limit behaviour,
        and calibration sets are tens of items, not thousands."""
        return [self.judge(trace) for trace in traces]

    # ------------------------------------------------------------ versioning

    def with_prompt(
        self, prompt: str | PromptTemplate, *, version: str, **overrides: Any
    ) -> Judge:
        """A sibling judge with a new prompt — and no calibration attached.

        The iteration primitive. Dropping the calibration is the whole point: v2
        has not been measured yet, so it must not inherit v1's numbers. Every
        other setting is carried over so the comparison isolates the prompt.
        """
        kwargs: dict[str, Any] = {
            "name": self.name,
            "prompt": prompt,
            "version": version,
            "model": self.model,
            "completion": self._completion,
            "system": self.system,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "include_tools": self.include_tools,
            "strict": self.strict,
        }
        kwargs.update(overrides)
        return Judge(**kwargs)

    def with_completion(self, completion: Completion) -> Judge:
        """The same question asked through a different completion (live/replay)."""
        return self.with_prompt(self.template, version=self.version, completion=completion)

    def attach_calibration(self, report: CalibrationReport) -> None:
        """Record the judge's latest measured agreement.

        Refuses a report measured on a different judge or prompt version. Without
        that check, "this judge is calibrated" degrades into "some judge was
        calibrated once", which is how an unmeasured prompt ends up gating a
        build.
        """
        if report.judge != self.name or report.prompt_version != self.version:
            raise JudgeError(
                f"refusing to attach a calibration for {report.judge!r} "
                f"{report.prompt_version} to judge {self.name!r} {self.version}"
            )
        self.calibration = report

    @property
    def completion(self) -> Completion:
        """The completion this judge calls. Exposed so a caller can wrap it
        (recording, rate limiting) without reaching into the object."""
        return self._completion

    @property
    def prompt_sha256(self) -> str:
        return self.template.digest

    def __repr__(self) -> str:
        state = "calibrated" if self.calibration is not None else "UNCALIBRATED"
        return (
            f"Judge(name={self.name!r}, version={self.version!r}, "
            f"model={self.model!r}, {state})"
        )


class ReplayJudge(Judge):
    """A judge that answers from a recording. The offline path.

    Same prompt, same parser, same verdict construction as a live judge — only
    the completion differs. That is what makes an offline test meaningful: it
    exercises the code that runs in production, not a mock of it.
    """

    def __init__(
        self,
        *,
        recording: Recording | str | Path,
        strict_prompt_hash: bool = True,
        **kwargs: Any,
    ) -> None:
        if "completion" in kwargs:
            raise JudgeError("ReplayJudge supplies its own completion; pass `recording=`")
        super().__init__(
            completion=ReplayCompletion(
                recording, strict_prompt_hash=strict_prompt_hash
            ),
            **kwargs,
        )

    @property
    def recording(self) -> Recording:
        completion = self._completion
        assert isinstance(completion, ReplayCompletion)  # set in __init__
        return completion.recording


def record_verdicts(
    judge: Judge,
    items: Sequence[tuple[str, Trace]],
    path: str | Path,
) -> Recording:
    """Run `judge` over `(item_id, trace)` pairs and write a replayable recording.

    The bridge between a live run and the offline suite: run this once with
    `LAB_LIVE_JUDGE=1`, commit the JSONL, and every subsequent run of the
    calibration is free, deterministic and keyless. The recording stores raw
    model text and the prompt digest, never a parsed verdict.
    """
    recorder = RecordingCompletion(
        judge.completion, judge=judge.name, prompt_version=judge.version
    )
    recording_judge = judge.with_completion(recorder)
    for item_id, trace in items:
        recording_judge.judge(trace, item_id=item_id)
    recorder.recording.save(path)
    return recorder.recording
