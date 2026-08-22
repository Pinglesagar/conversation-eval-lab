"""Dead-air detection and honest attribution.

WHAT THIS DEMONSTRATES
----------------------
Dead air is the failure a caller actually notices. Nobody hangs up because p95
went from 800 ms to 900 ms; they hang up after four seconds of nothing. So this
module finds the silences and then answers the only useful follow-up question:
*what was the system doing during them?*

**What this module does: attribution. What it does not do: timing.**

That distinction is the whole point, and it is worth being blunt about because
almost every dashboard that shows "time spent in tools" is quietly lying about
it. Given a gap of 3.4 s that encloses a `search_tables` call and a handoff to
`BookingAgent`, this module reports exactly that: 3.4 s of silence, with those
two operations inside it. It does **not** report "search_tables took 2.1 s and
the handoff took 1.3 s", because the trace does not contain the evidence for that
split. Two operations inside one gap cannot be apportioned without
per-operation timestamps, and inventing the split would produce a number that
looks more precise than the data.

What the trace *does* support is a partial subtraction, and this module goes
exactly that far and no further. A `tool_call` and its matching `tool_result`
have timestamps, so the interval between them is measured, not guessed. The union
of those intervals (union, not sum — concurrent calls would otherwise double
count) is reported as `accounted_s`, and the remainder as `unaccounted_s`. The
remainder is where model think-time, prompt assembly and TTS startup live, all
mixed together and honestly labelled as unseparated. An adapter that emits
finer-grained events can shrink that remainder; until it does, the remainder is
reported rather than allocated.

HOW SILENCE IS DERIVED
----------------------
From the trace alone, as everything here must be. Audible spans are:

* the agent speaking — `agent_audio_first_byte` to the matching
  `agent_audio_complete`;
* the caller speaking — `caller_utterance`, treated as an instant, because its
  `ts` is the moment the caller's turn *ended* (see `lab.trace.build`). The
  harness does not record when the caller started, so caller speech is a point
  and not an interval. That is a real limitation, and it is conservative: it can
  only ever make a measured gap longer than reality, never shorter.

`session_start` and `session_end` are treated as zero-length spans, so the wait
before the opening greeting and the wait before the line drops are both measured.
Both are real dead air and both are routinely missed by tooling that only looks
between turns.

RELATIONSHIP TO `lab.voice.metrics`
-----------------------------------
The latency metrics measure the gap *after a caller turn* — the one the caller is
waiting through. This module measures every gap, including mid-turn ones the
agent creates on its own (a handoff between two agent utterances, say), which
never appear in a response-latency distribution at all. Neither subsumes the
other; a system can have excellent p95 and still leave five seconds of silence
in the middle of its own answer.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.trace.schema import EventKind, Trace, TraceEvent

__all__ = [
    "AGENT_SPEECH",
    "DEFAULT_GAP_THRESHOLD_S",
    "EnclosedOperation",
    "SilenceGap",
    "SilenceReport",
    "SpeechSpan",
    "StageShare",
    "find_gaps",
    "silence_report",
    "speech_spans",
]

#: Gaps at or below this are not reported. 0.8 s is chosen to sit just above
#: natural conversational turn-taking — humans leave roughly 200 ms and tolerate
#: up to about a second before a pause reads as a problem — so a threshold here
#: filters normal turn boundaries without hiding anything a caller would notice.
#: It is a parameter, and every report prints the value it used.
DEFAULT_GAP_THRESHOLD_S: float = 0.8

#: The coarse attribution vocabulary. Deliberately tiny: four buckets that map to
#: four different engineering responses, rather than a taxonomy nobody can act on.
STAGE_TOOL = "tool"
STAGE_HANDOFF = "handoff"
STAGE_TOOL_AND_HANDOFF = "tool+handoff"
STAGE_UNATTRIBUTED = "unattributed"

#: Span label for a stretch of agent speech. Short on purpose: it lands in the
#: `preceded_by` / `followed_by` fields of every gap, and spelling out both
#: underlying event kinds there made the report line read as two arrows.
#: `speech_spans` documents exactly which events it is derived from.
AGENT_SPEECH = "agent_speech"

STAGES: tuple[str, ...] = (
    STAGE_TOOL,
    STAGE_HANDOFF,
    STAGE_TOOL_AND_HANDOFF,
    STAGE_UNATTRIBUTED,
)


def _fmt_s(seconds: float) -> str:
    return f"{seconds:.3f} s"


class SpeechSpan(BaseModel):
    """An interval during which something audible was happening.

    `start_s == end_s` for a point event (a caller turn ending, or a session
    boundary). Spans are what gaps are computed *between*, so they are kept as a
    first-class object: a surprising gap is almost always a surprising span.
    """

    model_config = ConfigDict(extra="forbid")

    start_s: float
    end_s: float
    actor: str
    source: str = Field(..., description="Event kind(s) this span was derived from.")

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def __repr__(self) -> str:
        return (
            f"SpeechSpan({self.start_s:.3f}->{self.end_s:.3f}s, "
            f"{self.actor}, {self.source})"
        )


class EnclosedOperation(BaseModel):
    """An operation recorded inside a gap.

    `duration_s` is populated only for a `tool_call` whose matching `tool_result`
    is in the trace — that one interval genuinely is measured. Handoffs are
    instantaneous markers with no paired end event, so their duration is None and
    stays None rather than being filled with a plausible-looking guess.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str
    label: str = Field(..., description="Tool name, or 'From->To' for a handoff.")
    ts: float
    duration_s: float | None = None

    def describe(self) -> str:
        if self.duration_s is None:
            return f"{self.label} ({self.kind} at {self.ts:.3f}s, no measured duration)"
        return f"{self.label} ({self.kind} at {self.ts:.3f}s, {_fmt_s(self.duration_s)})"

    def __repr__(self) -> str:
        return f"EnclosedOperation({self.describe()})"


class SilenceGap(BaseModel):
    """One stretch of dead air, with the operations recorded inside it.

    Read `stage` as "which kind of work was in flight", not "what took the time".
    `accounted_s` is the part of the gap covered by measured tool intervals;
    `unaccounted_s` is everything else, and on a healthy trace that remainder is
    model and TTS time that this schema cannot yet separate.
    """

    model_config = ConfigDict(extra="forbid")

    start_s: float
    end_s: float
    preceded_by: str = Field(..., description="Source of the span that ended the gap's left edge.")
    followed_by: str = Field(..., description="Source of the span that opened at the gap's right edge.")
    operations: list[EnclosedOperation] = Field(default_factory=list)
    stage: str
    accounted_s: float = Field(
        ..., ge=0.0, description="Union of measured tool intervals inside the gap."
    )

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def unaccounted_s(self) -> float:
        """Gap time not covered by any measured tool interval.

        Clamped at zero: a tool interval clipped to the gap can only ever be a
        subset of it, so a negative value would indicate a bug rather than a
        finding, and this property is not the place to surface bugs.
        """
        return max(0.0, self.duration_s - self.accounted_s)

    @property
    def tool_names(self) -> list[str]:
        return [op.label for op in self.operations if op.kind == EventKind.TOOL_CALL]

    @property
    def handoff_labels(self) -> list[str]:
        return [op.label for op in self.operations if op.kind == EventKind.AGENT_HANDOFF]

    def attributed_to(self) -> list[str]:
        """Concrete operation labels this gap is attributed to, in time order."""
        return [op.label for op in self.operations]

    def describe(self) -> str:
        head = (
            f"{_fmt_s(self.duration_s)} of silence at {self.start_s:.3f}s "
            f"[{self.preceded_by} -> {self.followed_by}]  stage={self.stage}"
        )
        if not self.operations:
            return (
                head
                + "\n    no operations recorded inside this gap — the silence is "
                "unexplained by the trace"
            )
        lines = [head]
        lines += [f"    contains: {op.describe()}" for op in self.operations]
        lines.append(
            f"    measured tool time {_fmt_s(self.accounted_s)} of "
            f"{_fmt_s(self.duration_s)}; "
            f"{_fmt_s(self.unaccounted_s)} unaccounted (model/TTS, not separable here)"
        )
        if len(self.operations) > 1:
            lines.append(
                "    NOTE: more than one operation in this gap — the gap is "
                "attributed to all of them, not split between them"
            )
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"SilenceGap({self.start_s:.3f}->{self.end_s:.3f}s, "
            f"stage={self.stage!r}, ops={len(self.operations)})"
        )


class StageShare(BaseModel):
    """How much of the total dead air sat in one stage."""

    model_config = ConfigDict(extra="forbid")

    stage: str
    gaps: int = Field(..., ge=0)
    total_s: float = Field(..., ge=0.0)
    of_gaps: int = Field(..., ge=0, description="Denominator: all gaps in the report.")
    of_total_s: float = Field(
        ..., ge=0.0, description="Denominator: total dead air in the report."
    )

    @property
    def time_share(self) -> float | None:
        """Fraction of dead air in this stage, or None when there is none at all."""
        return self.total_s / self.of_total_s if self.of_total_s > 0 else None

    def describe(self) -> str:
        share = (
            f"{100.0 * self.time_share:.1f}%" if self.time_share is not None else "n/a"
        )
        return (
            f"{self.stage:<14} {self.gaps}/{self.of_gaps} gaps, "
            f"{_fmt_s(self.total_s)}/{_fmt_s(self.of_total_s)} ({share})"
        )

    def __repr__(self) -> str:
        return f"StageShare({self.describe()})"


class SilenceReport(BaseModel):
    """Every gap over the threshold in one session, plus the per-stage shares."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    scenario_id: str
    threshold_s: float
    session_duration_s: float
    gaps: list[SilenceGap] = Field(default_factory=list)

    @property
    def total_gap_s(self) -> float:
        return sum(gap.duration_s for gap in self.gaps)

    @property
    def worst_gap(self) -> SilenceGap | None:
        return max(self.gaps, key=lambda g: g.duration_s, default=None)

    def stages(self) -> list[StageShare]:
        """Per-stage counts and time shares, in the fixed `STAGES` order.

        Stages with no gaps are included with zeros. Omitting them would make two
        runs' reports different shapes, which is exactly what a diff cannot cope
        with — and "zero gaps in tools this run" is a result worth seeing.
        """
        total = self.total_gap_s
        shares: list[StageShare] = []
        for stage in STAGES:
            matching = [gap for gap in self.gaps if gap.stage == stage]
            shares.append(
                StageShare(
                    stage=stage,
                    gaps=len(matching),
                    total_s=sum(gap.duration_s for gap in matching),
                    of_gaps=len(self.gaps),
                    of_total_s=total,
                )
            )
        return shares

    def by_tool(self) -> dict[str, tuple[int, float]]:
        """`tool_name -> (gaps containing it, total seconds of those gaps)`.

        The seconds deliberately double count a gap that contains two tools: the
        same 3 s appears under both names, because the trace cannot say which of
        them owned it. Summing this mapping will therefore exceed
        `total_gap_s`, and that is the correct behaviour for an attribution — a
        mapping that summed neatly would be one that had invented a split.
        """
        result: dict[str, tuple[int, float]] = {}
        for gap in self.gaps:
            for name in dict.fromkeys(gap.tool_names):
                count, seconds = result.get(name, (0, 0.0))
                result[name] = (count + 1, seconds + gap.duration_s)
        return dict(sorted(result.items()))

    def to_text(self) -> str:
        lines = [
            f"Silence report — session:{self.session_id} ({self.scenario_id})",
            f"  threshold {_fmt_s(self.threshold_s)}   session "
            f"{_fmt_s(self.session_duration_s)}",
            f"  {len(self.gaps)} gap(s) over threshold, {_fmt_s(self.total_gap_s)} "
            f"of {_fmt_s(self.session_duration_s)} total dead air",
            "",
        ]
        if not self.gaps:
            lines.append(f"  no gap exceeded {_fmt_s(self.threshold_s)}.")
            return "\n".join(lines) + "\n"
        for index, gap in enumerate(self.gaps, start=1):
            lines.append(f"  [{index}] {gap.describe()}")
        lines += ["", "  by stage:"]
        lines += [f"    {share.describe()}" for share in self.stages()]
        lines += [
            "",
            "  Attribution only: a gap is attributed to the operations inside it, "
            "never split between them.",
        ]
        return "\n".join(lines) + "\n"

    def to_markdown(self) -> str:
        header = (
            "| # | start | duration | stage | operations | measured tool time | unaccounted |\n"
            "|---|---|---|---|---|---|---|"
        )
        rows = [
            "| "
            + " | ".join(
                (
                    str(index),
                    f"{gap.start_s:.3f} s",
                    _fmt_s(gap.duration_s),
                    gap.stage,
                    ", ".join(gap.attributed_to()) or "—",
                    _fmt_s(gap.accounted_s),
                    _fmt_s(gap.unaccounted_s),
                )
            )
            + " |"
            for index, gap in enumerate(self.gaps, start=1)
        ]
        stage_rows = [f"- {share.describe()}" for share in self.stages()]
        return "\n".join(
            [
                f"### Silence — session `{self.session_id}` ({self.scenario_id})",
                "",
                f"Threshold {_fmt_s(self.threshold_s)}. "
                f"{len(self.gaps)} gap(s) over threshold totalling "
                f"{_fmt_s(self.total_gap_s)} of a "
                f"{_fmt_s(self.session_duration_s)} session.",
                "",
                header,
                *(rows or ["| — | — | — | — | — | — | — |"]),
                "",
                "**By stage**",
                "",
                *stage_rows,
                "",
                "Gaps are *attributed* to the operations they enclose, not split "
                "between them: two operations in one gap cannot be apportioned "
                "without per-operation timestamps. `measured tool time` is the union "
                "of real `tool_call`->`tool_result` intervals; `unaccounted` is model "
                "and TTS time this schema cannot yet separate.",
            ]
        ).rstrip() + "\n"

    def __repr__(self) -> str:
        return (
            f"SilenceReport(session_id={self.session_id!r}, "
            f"gaps={len(self.gaps)}, total={self.total_gap_s:.3f}s)"
        )


# --------------------------------------------------------------------------- #
# Derivation
# --------------------------------------------------------------------------- #


def speech_spans(trace: Trace, *, include_session_bounds: bool = True) -> list[SpeechSpan]:
    """Audible intervals in the trace, sorted by start time.

    Agent speech spans pair `agent_audio_first_byte` with the following
    `agent_audio_complete`. A first byte with no completion (the session was cut
    off mid-utterance) becomes a point span at the first byte: the trace does not
    say when it stopped, so nothing is assumed about it.

    Caller speech is a point at the `caller_utterance` timestamp — the harness
    records the end of the caller's turn, not its start. Treating it as a point
    is the conservative reading, since any real caller speech before that instant
    would only shorten the preceding gap.
    """
    spans: list[SpeechSpan] = []

    if include_session_bounds:
        start = trace.first(EventKind.SESSION_START)
        if start is not None:
            spans.append(
                SpeechSpan(
                    start_s=start.ts,
                    end_s=start.ts,
                    actor="system",
                    source=EventKind.SESSION_START,
                )
            )

    paired_completes: set[int] = set()
    for opener, closer in trace.event_pairs(
        EventKind.AGENT_AUDIO_FIRST_BYTE, EventKind.AGENT_AUDIO_COMPLETE
    ):
        paired_completes.add(id(opener))
        spans.append(
            SpeechSpan(
                start_s=opener.ts,
                end_s=closer.ts,
                actor="agent",
                source=AGENT_SPEECH,
            )
        )
    for event in trace.events_of_kind(EventKind.AGENT_AUDIO_FIRST_BYTE):
        if id(event) not in paired_completes:
            spans.append(
                SpeechSpan(
                    start_s=event.ts,
                    end_s=event.ts,
                    actor="agent",
                    source=f"{AGENT_SPEECH} (never completed)",
                )
            )

    for event in trace.events_of_kind(EventKind.CALLER_UTTERANCE):
        spans.append(
            SpeechSpan(
                start_s=event.ts,
                end_s=event.ts,
                actor="caller",
                source=EventKind.CALLER_UTTERANCE,
            )
        )

    if include_session_bounds:
        end = trace.last(EventKind.SESSION_END)
        if end is not None:
            spans.append(
                SpeechSpan(
                    start_s=end.ts,
                    end_s=end.ts,
                    actor="system",
                    source=EventKind.SESSION_END,
                )
            )

    return sorted(spans, key=lambda span: (span.start_s, span.end_s))


def _union_length(intervals: Sequence[tuple[float, float]]) -> float:
    """Total length covered by a set of possibly overlapping intervals.

    Union, not sum. Two tool calls issued in parallel occupy overlapping wall
    time, and summing their durations can exceed the gap that contains them,
    producing the classic "97% of a 3 s gap was tools" nonsense where the
    percentage is over 100.
    """
    if not intervals:
        return 0.0
    ordered = sorted(intervals)
    total = 0.0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start > current_end:
            total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    total += current_end - current_start
    return total


def _tool_result_index(trace: Trace) -> dict[str, TraceEvent]:
    """`call_id -> tool_result`. Correlated by id, never by adjacency."""
    index: dict[str, TraceEvent] = {}
    for event in trace.events_of_kind(EventKind.TOOL_RESULT):
        call_id = event.get("call_id")
        if call_id is not None:
            index.setdefault(str(call_id), event)
    return index


def _classify(operations: Sequence[EnclosedOperation]) -> str:
    has_tool = any(op.kind == EventKind.TOOL_CALL for op in operations)
    has_handoff = any(op.kind == EventKind.AGENT_HANDOFF for op in operations)
    if has_tool and has_handoff:
        return STAGE_TOOL_AND_HANDOFF
    if has_tool:
        return STAGE_TOOL
    if has_handoff:
        return STAGE_HANDOFF
    return STAGE_UNATTRIBUTED


def find_gaps(
    trace: Trace, *, threshold_s: float = DEFAULT_GAP_THRESHOLD_S
) -> list[SilenceGap]:
    """Every silence longer than `threshold_s`, with its enclosed operations.

    Spans are walked in start order while carrying the furthest end seen so far,
    so a span nested inside a longer one cannot open a spurious gap. A gap runs
    from that furthest end to the next span's start.

    Args:
        trace: The session to analyse.
        threshold_s: Strictly-greater-than comparison, so a threshold of 0.0
            reports every non-zero gap and a threshold equal to a gap's exact
            duration excludes it.
    """
    if threshold_s < 0:
        raise ValueError(f"threshold_s must be non-negative, got {threshold_s!r}")

    spans = speech_spans(trace)
    if len(spans) < 2:
        return []

    results_index = _tool_result_index(trace)
    tool_calls = trace.events_of_kind(EventKind.TOOL_CALL)
    handoffs = trace.handoffs()

    gaps: list[SilenceGap] = []
    frontier = spans[0].end_s
    frontier_source = spans[0].source
    for span in spans[1:]:
        if span.start_s - frontier > threshold_s:
            gaps.append(
                _build_gap(
                    start_s=frontier,
                    end_s=span.start_s,
                    preceded_by=frontier_source,
                    followed_by=span.source,
                    tool_calls=tool_calls,
                    handoffs=handoffs,
                    results_index=results_index,
                )
            )
        if span.end_s >= frontier:
            frontier = span.end_s
            frontier_source = span.source
    return gaps


def _build_gap(
    *,
    start_s: float,
    end_s: float,
    preceded_by: str,
    followed_by: str,
    tool_calls: Iterable[TraceEvent],
    handoffs: Iterable[TraceEvent],
    results_index: dict[str, TraceEvent],
) -> SilenceGap:
    """Assemble one gap: find the operations inside it and measure what is measurable."""
    operations: list[EnclosedOperation] = []
    measured: list[tuple[float, float]] = []

    for call in tool_calls:
        if not start_s <= call.ts <= end_s:
            continue
        call_id = call.get("call_id")
        result = results_index.get(str(call_id)) if call_id is not None else None
        duration: float | None = None
        if result is not None and result.ts >= call.ts:
            duration = result.ts - call.ts
            # Clip to the gap: a result that lands after speech resumed did not
            # spend that part of its life inside this silence.
            measured.append((max(call.ts, start_s), min(result.ts, end_s)))
        operations.append(
            EnclosedOperation(
                kind=EventKind.TOOL_CALL,
                label=str(call.get("name", "<unnamed tool>")),
                ts=call.ts,
                duration_s=duration,
            )
        )

    for handoff in handoffs:
        if not start_s <= handoff.ts <= end_s:
            continue
        operations.append(
            EnclosedOperation(
                kind=EventKind.AGENT_HANDOFF,
                label=f"{handoff.get('from')}->{handoff.get('to')}",
                ts=handoff.ts,
            )
        )

    operations.sort(key=lambda op: op.ts)
    return SilenceGap(
        start_s=start_s,
        end_s=end_s,
        preceded_by=preceded_by,
        followed_by=followed_by,
        operations=operations,
        stage=_classify(operations),
        accounted_s=min(_union_length(measured), end_s - start_s),
    )


def silence_report(
    trace: Trace, *, threshold_s: float = DEFAULT_GAP_THRESHOLD_S
) -> SilenceReport:
    """Full silence report for one trace."""
    return SilenceReport(
        session_id=trace.session_id,
        scenario_id=trace.scenario_id,
        threshold_s=threshold_s,
        session_duration_s=trace.duration(),
        gaps=find_gaps(trace, threshold_s=threshold_s),
    )
