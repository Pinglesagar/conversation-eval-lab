"""Transition-failure heatmap: which handoff is losing the conversation.

WHAT THIS DEMONSTRATES
----------------------
In a multi-agent system, most of the interesting failures are not inside an
agent — they are on the seam between two of them. Context that existed before a
handoff is gone after it; a value the caller gave to one agent never reaches the
tool the next agent calls; a specialist re-asks a question that was already
answered. A per-agent pass rate cannot see any of that, because no single agent
is wrong: the *transition* is.

So the aggregate this module builds is a matrix over transitions —
from-agent x to-agent, cell = failures on that handoff — rendered as a PNG. It is
the one chart in this repo that changes what someone does next: a hot cell names
the pair of agents whose contract to go and read.

IT WORKS FROM TRACES ALONE
--------------------------
Both axes and both numbers come out of `agent_handoff` events, and the default
verdict comes from the trace too (`default_failure_predicate`: the session ended
for a bad reason, or a tool failed). Nothing else is required — no side-channel
log, no database. Hand it a directory of JSONL fixtures and it draws.

Two ways to supply the numerator, with an explicit trade-off:

*   `transition_matrix(traces)` — trace-only. A failing conversation attributes
    one failure to *every* transition it crossed, because a turn-based trace
    cannot say which of them lost the information. That over-attributes on
    purpose: it is a map of where to look, not a per-transition probability, and
    every cell is annotated `failures/attempts` so an over-attributed cell shows
    up as a big numerator against a big denominator rather than as a scary colour.
*   `matrix_from_failures(traces, failures)` — precise. Denominators still come
    from the traces, but numerators come from `FailureRecord`s that name
    `from_agent` and `to_agent`, so a check that knows exactly which handoff
    dropped the value gets a matrix with no over-attribution at all.

The docstring for each says which one produced a given chart, and
`TransitionMatrix.attribution` records it in the data so a rendered PNG cannot be
mistaken for the more precise kind.

WHY matplotlib IS OPTIONAL
--------------------------
It lives in the `[charts]` extra and is imported inside `render_heatmap`, so the
cardinal rule holds: `pip install -e ".[dev]" && pytest` needs no plotting
backend. The matrix itself — the part that carries the finding — is computed with
no third-party dependency at all and can be printed as a table by
`TransitionMatrix.to_markdown()` when there is no chart to be had.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.report.report import FailureRecord, Rate, format_rate
from lab.trace.schema import EventKind, Trace

__all__ = [
    "SESSION_END_OK_REASONS",
    "TransitionMatrix",
    "default_failure_predicate",
    "transition_matrix",
    "matrix_from_failures",
    "render_heatmap",
    "transition_key",
]

#: `session_end` reasons that are not, by themselves, failures. Everything else —
#: `max_turns`, an adapter error, a provider timeout — is treated as one. Listed
#: explicitly because "which endings count as fine" is a judgement, and a
#: judgement buried in a comparison is a judgement nobody reviews.
SESSION_END_OK_REASONS: frozenset[str] = frozenset(
    {"completed", "caller_hung_up", "agent_ended"}
)

_UNKNOWN_AGENT = "(unknown)"


def transition_key(from_agent: str, to_agent: str) -> str:
    """`"A->B"`. String keys so the matrix serialises to JSON unchanged."""
    return f"{from_agent}->{to_agent}"


def default_failure_predicate(trace: Trace) -> bool:
    """Did this session fail, judged from the trace and nothing else?

    True when the session ended for a reason outside `SESSION_END_OK_REASONS`, or
    when any tool reported `ok=False`. Both are facts in the event stream, which
    is what lets this module draw a chart from a directory of fixtures with no
    other input.

    This is a deliberately blunt instrument and is not a substitute for
    `lab.checks`: it catches sessions that visibly went wrong, not sessions that
    completed smoothly with the wrong outcome — which is precisely the shape of
    the most interesting bugs. Pass a real per-run verdict when you have one; the
    default exists so the chart is available before the checks are.
    """
    end = trace.last(EventKind.SESSION_END)
    if end is not None and str(end.get("reason", "")) not in SESSION_END_OK_REASONS:
        return True
    return any(
        e.get("ok") is False for e in trace.events_of_kind(EventKind.TOOL_RESULT)
    )


class TransitionMatrix(BaseModel):
    """Failures and attempts per (from-agent, to-agent) handoff.

    Attempts are the denominator and are always carried: three failures out of
    three attempts and three out of ninety are the same number and different
    findings.
    """

    model_config = ConfigDict(extra="forbid")

    agents: list[str] = Field(
        default_factory=list, description="Every agent seen, sorted, for both axes."
    )
    attempts: dict[str, int] = Field(
        default_factory=dict, description="Handoffs observed, keyed `from->to`."
    )
    failures: dict[str, int] = Field(
        default_factory=dict, description="Failures attributed, keyed `from->to`."
    )
    sessions: int = Field(default=0, ge=0, description="Traces the matrix was built from.")
    failing_sessions: int = Field(default=0, ge=0)
    attribution: Literal["whole-session", "per-handoff"] = Field(
        default="whole-session",
        description=(
            "'whole-session' blames every transition a failing conversation "
            "crossed (trace-only, over-attributes). 'per-handoff' uses failures "
            "that named their own transition (precise)."
        ),
    )

    # -------------------------------------------------------------- accessors

    def failure_count(self, from_agent: str, to_agent: str) -> int:
        return self.failures.get(transition_key(from_agent, to_agent), 0)

    def attempt_count(self, from_agent: str, to_agent: str) -> int:
        return self.attempts.get(transition_key(from_agent, to_agent), 0)

    def rate(self, from_agent: str, to_agent: str) -> Rate:
        """Failures over attempts for one transition, counts preserved."""
        attempts = self.attempt_count(from_agent, to_agent)
        failures = min(self.failure_count(from_agent, to_agent), attempts)
        return Rate(numerator=failures, denominator=attempts)

    @property
    def total_attempts(self) -> int:
        return sum(self.attempts.values())

    @property
    def total_failures(self) -> int:
        return sum(self.failures.values())

    @property
    def is_empty(self) -> bool:
        """True when no handoff was ever observed — a single-agent system, or a
        multi-agent one whose adapter is not emitting `agent_handoff`. Those two
        look identical here, which is worth saying out loud before concluding that
        a system has no transition failures."""
        return not self.attempts

    def hottest(self, limit: int = 5) -> list[tuple[str, int, int]]:
        """`(key, failures, attempts)` for the worst transitions, worst first.

        Ordered by failure count, then by failure rate, then by key, so the
        ordering is total and the output is stable across runs.
        """
        rows = [
            (key, count, self.attempts.get(key, 0))
            for key, count in self.failures.items()
            if count
        ]
        rows.sort(key=lambda row: (-row[1], -(row[1] / row[2] if row[2] else 0.0), row[0]))
        return rows[:limit]

    def to_markdown(self) -> str:
        """The matrix as a markdown table — the chart's content without matplotlib."""
        if self.is_empty:
            return (
                "_No handoffs observed in "
                f"{self.sessions} sessions; there is no transition matrix to draw._"
            )
        header = ["from \\ to", *self.agents]
        lines = [
            "| " + " | ".join(header) + " |",
            "|" + "|".join("---" for _ in header) + "|",
        ]
        for source in self.agents:
            cells = [source]
            for target in self.agents:
                attempts = self.attempt_count(source, target)
                cells.append("·" if attempts == 0 else self.rate(source, target).text)
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
        lines.append(
            f"Cells are failures/attempts. {format_rate(self.failing_sessions, self.sessions)} "
            f"sessions failed; attribution: {self.attribution}."
        )
        return "\n".join(lines)


def _handoff_pairs(trace: Trace) -> list[tuple[str, str]]:
    """(from, to) for each handoff, with missing names made explicit not dropped."""
    pairs: list[tuple[str, str]] = []
    for event in trace.handoffs():
        source = event.get("from") or _UNKNOWN_AGENT
        target = event.get("to") or _UNKNOWN_AGENT
        pairs.append((str(source), str(target)))
    return pairs


def _tally(traces: Sequence[Trace]) -> tuple[dict[str, int], list[str]]:
    """Attempts per transition, and the sorted set of agents involved."""
    attempts: dict[str, int] = {}
    agents: set[str] = set()
    for trace in traces:
        for source, target in _handoff_pairs(trace):
            agents.update((source, target))
            key = transition_key(source, target)
            attempts[key] = attempts.get(key, 0) + 1
    return attempts, sorted(agents)


def transition_matrix(
    traces: Sequence[Trace],
    *,
    is_failure: Callable[[Trace], bool] | None = None,
) -> TransitionMatrix:
    """Build a matrix from traces alone.

    Args:
        traces: The sessions to aggregate.
        is_failure: Per-trace verdict. Defaults to `default_failure_predicate`,
            which reads the session-end reason and tool outcomes — so this
            function needs nothing but the JSONL files. Pass real check verdicts
            when they exist; the default is the version that works on day one.

    Returns:
        A `TransitionMatrix` with `attribution="whole-session"`: every transition
        a failing conversation crossed takes one failure. See the module docstring
        for why that is the honest default and what it costs.
    """
    predicate = is_failure if is_failure is not None else default_failure_predicate
    attempts, agents = _tally(traces)
    failures: dict[str, int] = {}
    failing = 0

    for trace in traces:
        if not predicate(trace):
            continue
        failing += 1
        for source, target in _handoff_pairs(trace):
            key = transition_key(source, target)
            failures[key] = failures.get(key, 0) + 1

    return TransitionMatrix(
        agents=agents,
        attempts=attempts,
        failures=failures,
        sessions=len(traces),
        failing_sessions=failing,
        attribution="whole-session",
    )


def matrix_from_failures(
    traces: Sequence[Trace],
    failures: Iterable[FailureRecord],
) -> TransitionMatrix:
    """Build a matrix whose numerators name their own transition.

    Denominators still come from the traces — a failure rate needs to know how
    often the handoff was attempted, and only the traces know that. Numerators
    come from `FailureRecord`s that set both `from_agent` and `to_agent`; records
    that do not name a transition are ignored here rather than spread across the
    matrix, because a failure with no known location does not belong in a chart
    about locations. They are still in the report's failure list.
    """
    attempts, agents = _tally(traces)
    counted: dict[str, int] = {}
    sessions_with_failures: set[str] = set()

    for record in failures:
        if not (record.from_agent and record.to_agent):
            continue
        agents = sorted({*agents, record.from_agent, record.to_agent})
        key = transition_key(record.from_agent, record.to_agent)
        counted[key] = counted.get(key, 0) + 1
        if record.session_id:
            sessions_with_failures.add(record.session_id)

    return TransitionMatrix(
        agents=agents,
        attempts=attempts,
        failures=counted,
        sessions=len(traces),
        failing_sessions=len(sessions_with_failures),
        attribution="per-handoff",
    )


def render_heatmap(
    matrix: TransitionMatrix,
    path: str | Path,
    *,
    title: str | None = None,
    dpi: int = 144,
) -> Path:
    """Render the matrix as a PNG and return the path written.

    Cells are annotated `failures/attempts`, never a bare colour: a heatmap read
    without its counts is a mood board. Transitions that never happened are left
    blank rather than drawn as zero failures, because "never attempted" and "never
    failed" are opposite findings and identical shades of blue.

    `matplotlib` comes from the optional `[charts]` extra and is imported here, so
    importing this module — or running the test suite — needs no plotting backend.
    """
    if matrix.is_empty:
        raise ValueError(
            "cannot render a transition heatmap: no agent_handoff events were "
            "observed in the traces supplied, so the matrix has no axes. Either "
            "the system under test is single-agent, or its adapter is not emitting "
            "agent_handoff."
        )

    try:
        import matplotlib

        matplotlib.use("Agg")  # headless: no display, no interactive backend
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "rendering the heatmap needs matplotlib, which lives in the optional "
            "[charts] extra: pip install -e \".[charts]\". The matrix itself needs "
            "no dependency — TransitionMatrix.to_markdown() prints the same content."
        ) from exc

    agents = matrix.agents
    size = len(agents)
    grid: list[list[float]] = []
    labels: list[list[str]] = []
    for source in agents:
        row_values: list[float] = []
        row_labels: list[str] = []
        for target in agents:
            attempts = matrix.attempt_count(source, target)
            failures = matrix.failure_count(source, target)
            if attempts == 0:
                row_values.append(float("nan"))  # never attempted: left blank
                row_labels.append("")
            else:
                row_values.append(float(failures))
                row_labels.append(f"{failures}/{attempts}")
        grid.append(row_values)
        labels.append(row_labels)

    figure, axes = plt.subplots(figsize=(1.6 + 1.1 * size, 1.4 + 1.0 * size), dpi=dpi)
    # "Never attempted" is drawn grey, not as zero failures: the two are opposite
    # findings and, on a sequential colormap, indistinguishable shades.
    colormap = plt.get_cmap("YlOrRd").with_extremes(bad="#f2f2f2")
    image = axes.imshow(grid, cmap=colormap, vmin=0.0)

    axes.set_xticks(range(size), labels=agents, rotation=30, ha="right")
    axes.set_yticks(range(size), labels=agents)
    axes.set_xlabel("to agent")
    axes.set_ylabel("from agent")
    # Two lines, small: the default title carries the denominator and the
    # attribution mode, and a single long line runs off the edge of the figure at
    # any realistic agent count.
    axes.set_title(
        title
        or (
            "Handoff failures\n"
            f"{format_rate(matrix.failing_sessions, matrix.sessions)} sessions failed"
            f" · {matrix.attribution} attribution"
        ),
        fontsize=11,
    )

    for row in range(size):
        for column in range(size):
            text = labels[row][column]
            if not text:
                continue
            axes.text(
                column,
                row,
                text,
                ha="center",
                va="center",
                fontsize=9,
                color="black",
            )

    figure.colorbar(image, ax=axes, shrink=0.8, label="failures attributed")
    figure.tight_layout()

    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(target_path, format="png")
    plt.close(figure)
    return target_path
