"""Response-latency metrics derived from a `Trace`, and nothing else.

WHAT THIS DEMONSTRATES
----------------------
Three claims that separate a latency number you can act on from a latency number
you can only quote.

**1. Latency is a pairing over an event stream, not a stopwatch.**
Every figure here comes from `Trace.event_pairs`, so the evidence for the number
ships inside the same file as the number. A reviewer can open the trace, find the
two events, and subtract. Nothing is measured in a place that the artifact cannot
show. The left edge of a response window is the `caller_utterance` event, whose
`ts` is the instant the caller's turn *ended* and the request left the harness
(see `lab.trace.build`); the right edge is `agent_audio_first_byte`.

**2. Time-to-first-byte and time-to-complete are different questions.**
Time-to-first-byte is responsiveness: when the caller starts hearing an answer.
Time-to-complete also contains how *long* the answer is, so a system that gets
more verbose looks slower on it while feeling identical on the phone. They are
computed separately, reported separately, and the difference between them —
agent speaking time — is reported as its own distribution so the reader can see
which of the two moved. Collapsing them into one "latency" column is how a
verbosity regression gets misfiled as a performance regression.

**3. A percentile with too few samples is refused, out loud.**
Any library will hand back `p95` of six samples. It is arithmetic, and it is
meaningless: it is a statement about one observation, not about a tail. This
module states a minimum sample count per quantile — at least one observed sample
must fall above the quantile, so `n >= 1 / (1 - q)` — and when the data does not
meet it, the quantile is reported as *not reported*, carrying `n` and the minimum
it needed. That is the difference between a harness that reports what it knows
and one that reports what it was asked for.

Every percentile is printed with the sample count beside it, and every rate is
printed as numerator over denominator, because a bare percentage hides the one
thing a reader needs to judge it.

WHY POOLING ACROSS SESSIONS IS THE NORMAL CASE
----------------------------------------------
A single restaurant-booking conversation has perhaps four to eight turns. p95
needs twenty samples, p99 needs a hundred. So per-session percentiles are
*expected* to be refused, and the intended usage is `response_latency_report`
over a whole suite of traces. Reporting a per-session p95 across a five-turn
call and then averaging those across sessions would produce a number with no
statistical meaning at all — this module makes that mistake awkward to commit by
pooling samples, never averaging averages.

SCOPE
-----
Latency only. Word error rate lives in `lab.voice.wer`, dead-air attribution in
`lab.voice.silence`. Barge-in latency — the gap between a caller interrupting
and the agent stopping — is deliberately absent *from this module*: it lives in
`lab.voice.interaction.barge_in_report`, which reads the `interruption_*` events
back off a trace. It is kept out of here because it is not the same kind of
number. Every latency in this module is recovered from instants an adapter
observed; a barge-in latency is constructed from timings a scenario handed in,
because no v1 adapter discovers an interruption. Pooling the two in one report
would let a constructed figure be read as a measured one.
"""

from __future__ import annotations

import statistics
from math import ceil
from typing import Iterable, Iterator, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.trace.schema import EventKind, Trace
from lab.voice.calibration import percentile, recover_response_latencies

__all__ = [
    "DEFAULT_QUANTILES",
    "Distribution",
    "Quantile",
    "ResponseLatencyReport",
    "completion_latencies",
    "first_byte_latencies",
    "iter_turn_latencies",
    "latencies_by_engine",
    "min_samples_for_quantile",
    "response_latency_report",
    "speaking_times",
]

#: The quantiles reported by default. p50 for the typical caller, p90/p95 for the
#: ones who notice, p99 for the ones who complain. Nothing above p99: with the
#: sample counts a realistic eval suite produces, p99.9 would always be refused.
DEFAULT_QUANTILES: tuple[float, ...] = (0.50, 0.90, 0.95, 0.99)


def min_samples_for_quantile(q: float) -> int:
    """Smallest sample count at which quantile `q` is worth reporting.

    The rule is `ceil(1 / (1 - q))`: the expected number of samples above the
    q-quantile is `n * (1 - q)`, and we require that to be at least one, so the
    quantile is bracketed by observed data rather than resting on the largest
    sample alone. That gives p50 -> 2, p90 -> 10, p95 -> 20, p99 -> 100.

    `q = 1.0` (the maximum) needs one sample: the maximum is an order statistic
    that always exists, and this module reports it as `max`, never as `p100`,
    precisely so it is not mistaken for a tail estimate.

    This is a reporting-hygiene threshold, not a confidence interval. It is the
    cheapest honest guard available: it costs one comparison and it stops the
    single most common way latency dashboards mislead people.
    """
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"quantile must be in [0, 1], got {q!r}")
    if q >= 1.0 or q <= 0.0:
        return 1
    # Rounded before the ceiling on purpose: `1 / (1 - 0.9)` is 10.000000000000002
    # in float64, and a bare ceil turns the intended 10 into 11. Rounding to nine
    # decimals first snaps the exact cases back to integers while leaving genuinely
    # fractional requirements (q = 0.93 -> 14.28... -> 15) to round up as intended.
    return max(1, ceil(round(1.0 / (1.0 - q), 9)))


def _fmt_ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f} ms"


class Quantile(BaseModel):
    """One quantile of one distribution, with the sample count that produced it.

    `value_s is None` means the quantile was *refused*, not that it was zero.
    The object still carries `n` and `min_n` so the reader can see exactly how
    far short the data fell.
    """

    model_config = ConfigDict(extra="forbid")

    quantile: float = Field(..., ge=0.0, le=1.0)
    n: int = Field(..., ge=0, description="Samples available for this quantile.")
    min_n: int = Field(..., ge=1, description="Samples required to report it.")
    value_s: float | None = Field(
        default=None,
        description="Quantile value in seconds, or None when refused for lack of data.",
    )

    @property
    def reported(self) -> bool:
        """True if there were enough samples for this quantile to be believed."""
        return self.value_s is not None

    @property
    def label(self) -> str:
        """Conventional name: `p50`, `p95`, `p99`."""
        return f"p{self.quantile * 100:g}"

    def describe(self) -> str:
        """One line, always carrying the sample count."""
        if self.value_s is None:
            return (
                f"{self.label} = not reported "
                f"(n={self.n}, needs n>={self.min_n})"
            )
        return f"{self.label} = {_fmt_ms(self.value_s)} (n={self.n})"

    def __repr__(self) -> str:
        return f"Quantile({self.describe()})"


class Distribution(BaseModel):
    """A named set of latency samples plus its quantiles.

    The raw `samples` are kept in the model on purpose. They are the evidence:
    every summary figure below is recomputable from them, so a reader who
    distrusts the arithmetic can redo it, and a later run can be diffed sample
    by sample rather than summary by summary.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    samples: list[float] = Field(default_factory=list)
    quantiles: list[Quantile] = Field(default_factory=list)

    @classmethod
    def from_samples(
        cls,
        name: str,
        samples: Sequence[float],
        *,
        description: str = "",
        quantiles: Sequence[float] = DEFAULT_QUANTILES,
    ) -> "Distribution":
        """Build a distribution, refusing any quantile the sample count cannot support."""
        values = [float(s) for s in samples]
        computed: list[Quantile] = []
        for q in quantiles:
            min_n = min_samples_for_quantile(q)
            enough = len(values) >= min_n
            computed.append(
                Quantile(
                    quantile=q,
                    n=len(values),
                    min_n=min_n,
                    value_s=percentile(values, q) if enough else None,
                )
            )
        return cls(
            name=name, description=description, samples=values, quantiles=computed
        )

    # ------------------------------------------------------------- summaries

    @property
    def n(self) -> int:
        return len(self.samples)

    @property
    def mean_s(self) -> float | None:
        return statistics.fmean(self.samples) if self.samples else None

    @property
    def stdev_s(self) -> float | None:
        """Sample standard deviation; None below two samples, where it is undefined."""
        return statistics.stdev(self.samples) if len(self.samples) > 1 else None

    @property
    def min_s(self) -> float | None:
        return min(self.samples) if self.samples else None

    @property
    def max_s(self) -> float | None:
        """The largest sample. Reported as `max`, never as `p100`."""
        return max(self.samples) if self.samples else None

    def quantile(self, q: float) -> Quantile:
        """The `Quantile` for `q`, computed on demand if it was not pre-computed."""
        for existing in self.quantiles:
            if existing.quantile == q:
                return existing
        min_n = min_samples_for_quantile(q)
        enough = self.n >= min_n
        return Quantile(
            quantile=q,
            n=self.n,
            min_n=min_n,
            value_s=percentile(self.samples, q) if enough else None,
        )

    # ---------------------------------------------------------------- output

    def describe(self) -> str:
        """Multi-line human summary: header, then one line per quantile."""
        if not self.samples:
            return f"{self.name}: no samples (n=0/0) — nothing to report."
        head = (
            f"{self.name}: n={self.n}  "
            f"mean={_fmt_ms(self.mean_s or 0.0)}  "
            f"min={_fmt_ms(self.min_s or 0.0)}  "
            f"max={_fmt_ms(self.max_s or 0.0)}"
        )
        lines = [head] + [f"    {q.describe()}" for q in self.quantiles]
        return "\n".join(lines)

    def table_row(self) -> tuple[str, ...]:
        """`(name, n, mean, p50, p90, p95, p99, max)` as display strings."""

        def cell(q: float) -> str:
            quantile = self.quantile(q)
            return (
                _fmt_ms(quantile.value_s)
                if quantile.value_s is not None
                else f"n/a (n<{quantile.min_n})"
            )

        return (
            self.name,
            str(self.n),
            _fmt_ms(self.mean_s) if self.mean_s is not None else "n/a",
            cell(0.50),
            cell(0.90),
            cell(0.95),
            cell(0.99),
            _fmt_ms(self.max_s) if self.max_s is not None else "n/a",
        )

    def __len__(self) -> int:
        return len(self.samples)

    def __repr__(self) -> str:
        return f"Distribution(name={self.name!r}, n={self.n})"


# --------------------------------------------------------------------------- #
# Sample extraction — one function per measured interval
# --------------------------------------------------------------------------- #


def first_byte_latencies(trace: Trace) -> list[float]:
    """Time to first audio byte, in seconds, one sample per answered turn.

    `caller_utterance` -> `agent_audio_first_byte`. This delegates to
    `lab.voice.calibration.recover_response_latencies` rather than re-deriving
    the pairing, so the figure this module reports is the *same function* the
    timing calibration gate validates. A second implementation that agreed today
    would be a second implementation to keep in agreement forever.
    """
    return recover_response_latencies(trace)


def completion_latencies(trace: Trace) -> list[float]:
    """Time from the caller finishing to the agent finishing, in seconds.

    `caller_utterance` -> `agent_audio_complete`. Contains the length of the
    answer, so it is a measure of turn cost, not of responsiveness. Kept because
    a turn that takes nine seconds to finish speaking is a bad turn even when its
    first byte arrived in 300 ms — but it must never be quoted as latency.
    """
    return [
        b.ts - a.ts
        for a, b in trace.event_pairs(
            EventKind.CALLER_UTTERANCE, EventKind.AGENT_AUDIO_COMPLETE
        )
    ]


def speaking_times(trace: Trace) -> list[float]:
    """How long the agent spoke for, in seconds, per turn.

    `agent_audio_first_byte` -> `agent_audio_complete`: exactly the difference
    between the two distributions above. Reported so that a regression can be
    attributed — if time-to-complete moved and this moved with it, the agent got
    wordier; if this held still, the agent got slower.
    """
    return [
        b.ts - a.ts
        for a, b in trace.event_pairs(
            EventKind.AGENT_AUDIO_FIRST_BYTE, EventKind.AGENT_AUDIO_COMPLETE
        )
    ]


def _as_traces(traces: Trace | Iterable[Trace]) -> list[Trace]:
    if isinstance(traces, Trace):
        return [traces]
    return list(traces)


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


class ResponseLatencyReport(BaseModel):
    """Latency for one session or a pooled suite, with its own coverage figures.

    `caller_turns` and `answered_turns` are on the model because a latency
    distribution is only as trustworthy as the fraction of turns that made it in.
    If the agent never responded to a third of the turns, p95 of what remains is
    a survivorship-biased number, and the report says so on its face.
    """

    model_config = ConfigDict(extra="forbid")

    scope: str = Field(..., description="What was measured, e.g. 'session:ab12' or 'pooled: 12 traces'.")
    sessions: int = Field(..., ge=0)
    caller_turns: int = Field(..., ge=0, description="`caller_utterance` events seen.")
    answered_turns: int = Field(
        ..., ge=0, description="Turns that produced an `agent_audio_first_byte`."
    )
    time_to_first_byte: Distribution
    time_to_complete: Distribution
    agent_speaking_time: Distribution

    @property
    def unanswered_turns(self) -> int:
        return self.caller_turns - self.answered_turns

    def coverage(self) -> str:
        """Answered-turn rate as numerator/denominator, never a naked percentage."""
        if self.caller_turns == 0:
            return "answered 0/0 caller turns"
        pct = 100.0 * self.answered_turns / self.caller_turns
        return (
            f"answered {self.answered_turns}/{self.caller_turns} caller turns "
            f"({pct:.1f}%)"
        )

    def distributions(self) -> list[Distribution]:
        return [
            self.time_to_first_byte,
            self.time_to_complete,
            self.agent_speaking_time,
        ]

    def to_text(self) -> str:
        """Plain-text report, suitable for a terminal or a CI log."""
        lines = [
            f"Response latency — {self.scope}",
            f"  sessions: {self.sessions}   {self.coverage()}",
            "",
        ]
        for dist in self.distributions():
            lines.append(dist.describe())
            if dist.description:
                lines.append(f"    ({dist.description})")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def to_markdown(self) -> str:
        """Markdown table, for pasting into a report or a pull request."""
        header = (
            "| metric | n | mean | p50 | p90 | p95 | p99 | max |\n"
            "|---|---|---|---|---|---|---|---|"
        )
        rows = [
            "| " + " | ".join(dist.table_row()) + " |" for dist in self.distributions()
        ]
        notes = [f"- **{d.name}** — {d.description}" for d in self.distributions() if d.description]
        return "\n".join(
            [
                f"### Response latency — {self.scope}",
                "",
                f"{self.sessions} session(s), {self.coverage()}.",
                "",
                header,
                *rows,
                "",
                "`n/a (n<K)` means the quantile was refused: fewer than K samples, "
                "so the figure would describe one observation rather than a tail.",
                "",
                *notes,
            ]
        ).rstrip() + "\n"

    def __repr__(self) -> str:
        return (
            f"ResponseLatencyReport(scope={self.scope!r}, sessions={self.sessions}, "
            f"ttfb_n={self.time_to_first_byte.n})"
        )


def response_latency_report(
    traces: Trace | Iterable[Trace],
    *,
    quantiles: Sequence[float] = DEFAULT_QUANTILES,
    scope: str | None = None,
) -> ResponseLatencyReport:
    """Build a latency report from one trace or many, pooling samples.

    Pooling — concatenating every turn's sample into one distribution — is the
    only statistically defensible way to get a p95 out of an eval suite made of
    short conversations. The alternative, a p95 per session then averaged, is a
    mean of order statistics computed on five points each; it has no
    interpretation and it is smoother than reality, which is the worst
    combination for a tail metric.

    Args:
        traces: A single `Trace`, or any iterable of them.
        quantiles: Which quantiles to attempt. Each is refused independently if
            the pooled sample count cannot support it.
        scope: Overrides the auto-generated description of what was measured.
    """
    collected = _as_traces(traces)
    ttfb: list[float] = []
    ttc: list[float] = []
    speaking: list[float] = []
    caller_turns = 0
    for trace in collected:
        ttfb.extend(first_byte_latencies(trace))
        ttc.extend(completion_latencies(trace))
        speaking.extend(speaking_times(trace))
        caller_turns += len(trace.events_of_kind(EventKind.CALLER_UTTERANCE))

    if scope is None:
        if len(collected) == 1:
            scope = f"session:{collected[0].session_id} ({collected[0].scenario_id})"
        else:
            scope = f"pooled: {len(collected)} traces"

    return ResponseLatencyReport(
        scope=scope,
        sessions=len(collected),
        caller_turns=caller_turns,
        answered_turns=len(ttfb),
        time_to_first_byte=Distribution.from_samples(
            "time_to_first_byte",
            ttfb,
            description=(
                "caller_utterance -> agent_audio_first_byte: when the caller "
                "starts hearing an answer. The responsiveness figure."
            ),
            quantiles=quantiles,
        ),
        time_to_complete=Distribution.from_samples(
            "time_to_complete",
            ttc,
            description=(
                "caller_utterance -> agent_audio_complete: includes how long the "
                "answer is. A turn-cost figure, not a latency figure."
            ),
            quantiles=quantiles,
        ),
        agent_speaking_time=Distribution.from_samples(
            "agent_speaking_time",
            speaking,
            description=(
                "agent_audio_first_byte -> agent_audio_complete: the difference "
                "between the two above, so verbosity can be told from slowness."
            ),
            quantiles=quantiles,
        ),
    )


def latencies_by_engine(
    traces: Trace | Iterable[Trace],
    *,
    quantiles: Sequence[float] = DEFAULT_QUANTILES,
) -> dict[str, Distribution]:
    """Time-to-first-byte split by the engine tagged on the first-byte event.

    "The agent got slower" is not actionable; "the TTS deployment got slower" is.
    `TraceEvent.engine` exists for exactly this, and grouping on it is what turns
    a swap of one pipeline stage into an attributable regression. Events with no
    engine tag are grouped under `"unattributed"` rather than dropped, so the
    counts still add up to the pooled total.
    """
    buckets: dict[str, list[float]] = {}
    for trace in _as_traces(traces):
        for opener, closer in trace.event_pairs(
            EventKind.CALLER_UTTERANCE, EventKind.AGENT_AUDIO_FIRST_BYTE
        ):
            key = closer.engine or "unattributed"
            buckets.setdefault(key, []).append(closer.ts - opener.ts)
    return {
        engine: Distribution.from_samples(
            f"time_to_first_byte[{engine}]", samples, quantiles=quantiles
        )
        for engine, samples in sorted(buckets.items())
    }


def iter_turn_latencies(trace: Trace) -> Iterator[tuple[int, float]]:
    """`(turn_index, first_byte_latency_s)` per answered turn, in order.

    For error analysis: percentiles tell you a tail exists, this tells you which
    turn was in it, so the slow turn can be read in the transcript.
    """
    for index, (opener, closer) in enumerate(
        trace.event_pairs(EventKind.CALLER_UTTERANCE, EventKind.AGENT_AUDIO_FIRST_BYTE)
    ):
        yield index, closer.ts - opener.ts
