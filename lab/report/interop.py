"""Export a trace to other people's tools — langfuse and promptfoo.

WHAT THIS DEMONSTRATES
----------------------
That this harness is a layer on the existing ecosystem rather than a rival to it.
The evaluation space already has good observability (langfuse, Phoenix,
LangSmith) and good assertion runners (promptfoo, DeepEval). What it is short of
is a *trace schema honest enough to measure a voice agent from* — which is what
`lab` contributes. So the trace is exportable: the numbers stay reproducible here,
and the traces go and live wherever the team already looks at them.

WHERE THE OBSERVABILITY STORY LIVES, AND WHERE IT STOPS
-------------------------------------------------------
This file is the whole of it. If you came looking for the word *observability*,
this is the seam: `lab.trace` is the schema, and these four functions hand a
trace to the tools a team already watches. What is deliberately absent is
everything else the word usually implies — there is no collector, no agent, no
hosted backend, no sampling, no retention policy, no time series and no alerting.
This is an evaluation harness that can export to an observability tool, not an
observability tool, and the distinction is worth keeping in the one file where
the confusion would start.

NEITHER PACKAGE IS A DEPENDENCY
-------------------------------
Not imported, not declared, not optional-extra'd. These functions emit the
documented JSON shapes and nothing more, which has three consequences worth
stating:

*   The exporters run offline with no API key, like everything else here.
*   `lab` does not inherit a version constraint from a tool a user might not have.
*   The shapes are pinned by *this repo's* tests, so an upstream change breaks a
    test here instead of breaking a silent integration in someone's pipeline.

The cost is that these shapes can drift from upstream, which is why each
constant below names the API surface it targets and each function documents the
fields it fills. Verifying the shape against a live endpoint is the job of an
integration test in whichever repo owns the credentials, and that is stated rather
than pretended.

WHY THE LANGFUSE EXPORT ROUND-TRIPS AND THE PROMPTFOO ONE DOES NOT
------------------------------------------------------------------
`to_langfuse_batch` is a *serialisation*: every `TraceEvent` travels intact inside
`metadata.lab`, so `from_langfuse_batch` reconstructs the original `Trace`
exactly, and that equality is a test. Round-tripping matters because a lossy
export means the copy in the observability tool is a different artifact from the
one the verdicts were computed on, and any disagreement between them becomes
unresolvable.

`to_promptfoo_tests` is a *projection*: it turns "here is what happened" into
"here is what must keep happening" — the tools that were called, the handoffs that
occurred, the latency that was achieved. A projection cannot round-trip, and
claiming otherwise would be the exact species of overclaim this repo is about. It
is one-way by construction, and documented as such.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from lab.trace.schema import EventKind, Trace, TraceEvent

__all__ = [
    "LANGFUSE_API_TARGET",
    "PROMPTFOO_API_TARGET",
    "EPOCH",
    "to_langfuse_batch",
    "from_langfuse_batch",
    "to_promptfoo_tests",
    "to_promptfoo_config",
    "promptfoo_assertions_for",
]

#: The upstream surface `to_langfuse_batch` targets: the public ingestion
#: endpoint, whose body is `{"batch": [...]}` of typed create-events.
LANGFUSE_API_TARGET: str = "langfuse POST /api/public/ingestion (batch of *-create events)"

#: The upstream surface `to_promptfoo_tests` targets: the `tests:` list of a
#: promptfooconfig, each entry `{description, vars, assert: [...]}`.
PROMPTFOO_API_TARGET: str = "promptfoo config `tests[]` with `assert[]` entries"

#: Default wall-clock origin for exports. Trace timestamps are monotonic seconds
#: from session start, so an absolute time has to come from somewhere; the Unix
#: epoch is the choice that is obviously a placeholder rather than a plausible
#: lie, and it keeps the export deterministic. Pass the real session start when
#: you have it — an observability tool sorts by it.
EPOCH: datetime = datetime(1970, 1, 1, tzinfo=timezone.utc)

_LAB_KEY = "lab"


def _iso(origin: datetime, offset_s: float) -> str:
    """Absolute ISO-8601 UTC timestamp for a session-relative offset."""
    return (origin + timedelta(seconds=offset_s)).isoformat().replace("+00:00", "Z")


def _observation_id(session_id: str, index: int) -> str:
    """Deterministic observation id. No uuid4: a re-export must be byte-identical,
    or every re-upload looks like a new set of spans in the observability tool."""
    return f"{session_id}-{index:04d}"


# --------------------------------------------------------------------------- #
# langfuse
# --------------------------------------------------------------------------- #


def to_langfuse_batch(
    trace: Trace,
    *,
    start_time: datetime | None = None,
    tags: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Serialise a `Trace` as a langfuse ingestion batch.

    Mapping:

    *   the `Trace` itself becomes one `trace-create`, carrying `sessionId`,
        `name` (the scenario) and the caller/agent transcript as `input`/`output`;
    *   a `tool_call` immediately followed by its matching `tool_result` becomes
        one `span-create` with a real `startTime` and `endTime` — spans are how an
        observability UI shows duration, and a tool call is the one thing in a v1
        trace that genuinely has a start and an end;
    *   every other event becomes an `event-create` at its own instant.

    Every original `TraceEvent` is preserved verbatim under
    `body.metadata.lab`, which is what makes `from_langfuse_batch` exact. Pairing
    is deliberately conservative — only *adjacent* call/result events with equal
    `call_id` are merged — so the batch order always matches the event order and
    reconstruction cannot reshuffle a trace.

    Args:
        trace: The trace to export.
        start_time: Wall-clock instant of `ts == 0`. Defaults to `EPOCH`; see the
            constant for why a placeholder is preferable to a guess.
        tags: Extra langfuse tags. The adapter is always tagged.

    Returns:
        `{"batch": [...]}` — the documented request body, ready to POST.
    """
    origin = start_time if start_time is not None else EPOCH
    events = trace.events

    trace_body: dict[str, Any] = {
        "id": trace.session_id,
        "name": trace.scenario_id,
        "sessionId": trace.session_id,
        "timestamp": _iso(origin, events[0].ts if events else 0.0),
        "input": {"caller": trace.texts("caller")},
        "output": {"agent": trace.texts("agent")},
        "tags": sorted({trace.adapter, *(tags or ())}),
        "metadata": {
            _LAB_KEY: {
                "session_id": trace.session_id,
                "scenario_id": trace.scenario_id,
                "adapter": trace.adapter,
                "duration_s": trace.duration(),
                "tool_names": trace.tool_names(),
                "handoffs": [
                    {"from": a, "to": b} for a, b in trace.handoff_pairs()
                ],
            }
        },
    }

    batch: list[dict[str, Any]] = [
        {
            "id": f"{trace.session_id}-trace",
            "type": "trace-create",
            "timestamp": trace_body["timestamp"],
            "body": trace_body,
        }
    ]

    index = 0
    position = 0
    while position < len(events):
        event = events[position]
        paired = _adjacent_tool_pair(events, position)

        if paired is not None:
            call, result = paired
            batch.append(
                {
                    "id": _observation_id(trace.session_id, index),
                    "type": "span-create",
                    "timestamp": _iso(origin, call.ts),
                    "body": {
                        "id": _observation_id(trace.session_id, index),
                        "traceId": trace.session_id,
                        "name": f"tool:{call.get('name')}",
                        "startTime": _iso(origin, call.ts),
                        "endTime": _iso(origin, result.ts),
                        "input": call.get("args", {}),
                        "output": result.get("result"),
                        "level": "DEFAULT" if result.get("ok", True) else "ERROR",
                        "statusMessage": result.get("error"),
                        "metadata": {
                            _LAB_KEY: {
                                "events": [
                                    call.model_dump(mode="json"),
                                    result.model_dump(mode="json"),
                                ]
                            }
                        },
                    },
                }
            )
            index += 1
            position += 2
            continue

        batch.append(
            {
                "id": _observation_id(trace.session_id, index),
                "type": "event-create",
                "timestamp": _iso(origin, event.ts),
                "body": {
                    "id": _observation_id(trace.session_id, index),
                    "traceId": trace.session_id,
                    "name": event.kind,
                    "startTime": _iso(origin, event.ts),
                    "input": event.payload,
                    "metadata": {_LAB_KEY: {"events": [event.model_dump(mode="json")]}},
                },
            }
        )
        index += 1
        position += 1

    return {"batch": batch}


def _adjacent_tool_pair(
    events: Sequence[TraceEvent], position: int
) -> tuple[TraceEvent, TraceEvent] | None:
    """A `tool_call` at `position` followed immediately by its own `tool_result`."""
    if position + 1 >= len(events):
        return None
    call, result = events[position], events[position + 1]
    if call.kind != EventKind.TOOL_CALL or result.kind != EventKind.TOOL_RESULT:
        return None
    if call.get("call_id") is None or call.get("call_id") != result.get("call_id"):
        return None
    return call, result


def from_langfuse_batch(payload: dict[str, Any]) -> Trace:
    """Reconstruct the original `Trace` from a batch produced by `to_langfuse_batch`.

    Reads the `lab` metadata rather than the langfuse fields: the ISO timestamps
    are derived, lossy at sub-microsecond scale, and relative to an origin the
    batch does not have to state, whereas the embedded events are the originals.
    Reconstructing from the derived form would silently change the numbers, which
    is the failure mode a round-trip test is supposed to catch rather than commit.

    Raises `ValueError` on a batch that did not come from `to_langfuse_batch` —
    an export from somewhere else has no `lab` metadata to recover, and guessing
    would fabricate a trace.
    """
    batch = payload.get("batch")
    if not isinstance(batch, list) or not batch:
        raise ValueError("not a langfuse ingestion batch: missing a non-empty 'batch' list")

    header = next((item for item in batch if item.get("type") == "trace-create"), None)
    if header is None:
        raise ValueError("langfuse batch has no trace-create entry to read identifiers from")
    meta = header.get("body", {}).get("metadata", {}).get(_LAB_KEY)
    if not isinstance(meta, dict):
        raise ValueError(
            "langfuse batch was not produced by lab.report.interop: no `lab` "
            "metadata on the trace-create entry, so the original events are gone"
        )

    events: list[TraceEvent] = []
    for item in batch:
        if item.get("type") == "trace-create":
            continue
        embedded = item.get("body", {}).get("metadata", {}).get(_LAB_KEY, {}).get("events")
        if not isinstance(embedded, list):
            raise ValueError(
                f"observation {item.get('id')!r} carries no `lab.events`; this batch "
                "cannot be reconstructed losslessly"
            )
        events.extend(TraceEvent.model_validate(raw) for raw in embedded)

    return Trace(
        session_id=str(meta["session_id"]),
        scenario_id=str(meta["scenario_id"]),
        adapter=str(meta["adapter"]),
        events=events,
    )


# --------------------------------------------------------------------------- #
# promptfoo
# --------------------------------------------------------------------------- #


def promptfoo_assertions_for(
    trace: Trace,
    *,
    latency_budget_ms: float | None = None,
    rubric: str | None = None,
) -> list[dict[str, Any]]:
    """The assertion list for one trace: what must keep happening.

    Emits promptfoo's documented assertion shapes:

    *   `javascript` — one per distinct tool called and per distinct handoff
        observed, each with a `metric` name so promptfoo groups them in its
        summary. JavaScript rather than `contains` because a tool call is a fact
        about structured output, and matching it as a substring of the reply text
        is how a suite ends up passing on an agent that merely *mentions* the tool.
    *   `latency` — a threshold in milliseconds, only when the trace actually
        contains the boundary events a latency is defined by. No events, no
        assertion: a default budget invented here would pass or fail on a number
        nobody measured.
    *   `llm-rubric` — only when a rubric is supplied by the caller. This module
        does not invent grading criteria.

    The exported assertions assume the promptfoo provider returns the agent's
    result as JSON with `tool_names` and `handoffs` arrays. That contract is
    stated in each assertion's `metric` and in the config's `description`,
    because an assertion whose input shape is implicit is an assertion that will
    silently pass against the wrong thing.
    """
    assertions: list[dict[str, Any]] = []

    for name in dict.fromkeys(trace.tool_names()):
        assertions.append(
            {
                "type": "javascript",
                "value": f"JSON.parse(output).tool_names.includes({name!r})",
                "metric": f"tool_called:{name}",
            }
        )

    for source, target in dict.fromkeys(trace.handoff_pairs()):
        if not (source and target):
            continue
        assertions.append(
            {
                "type": "javascript",
                "value": (
                    "JSON.parse(output).handoffs.some(h => "
                    f"h.from === {source!r} && h.to === {target!r})"
                ),
                "metric": f"handoff:{source}->{target}",
            }
        )

    latencies = [
        b.ts - a.ts
        for a, b in trace.event_pairs(
            EventKind.CALLER_UTTERANCE, EventKind.AGENT_AUDIO_FIRST_BYTE
        )
    ]
    if latencies:
        budget = (
            latency_budget_ms
            if latency_budget_ms is not None
            # Observed worst case rounded up to the next 100 ms: a regression
            # guard derived from measured behaviour, not a target invented here.
            else math.ceil(max(latencies) * 1000 / 100.0) * 100
        )
        assertions.append({"type": "latency", "threshold": int(budget)})

    if rubric:
        assertions.append({"type": "llm-rubric", "value": rubric})

    return assertions


def to_promptfoo_tests(
    traces: Iterable[Trace],
    *,
    latency_budget_ms: float | None = None,
    rubric: str | None = None,
) -> list[dict[str, Any]]:
    """One promptfoo test case per trace: `{description, vars, assert}`.

    A one-way projection — see the module docstring. `vars.caller_turns` carries
    the caller's utterances in order so the case is replayable by a promptfoo
    provider that drives the same conversation.
    """
    tests: list[dict[str, Any]] = []
    for trace in traces:
        tests.append(
            {
                "description": f"{trace.scenario_id} ({trace.session_id})",
                "vars": {
                    "caller_turns": trace.texts("caller"),
                    "scenario_id": trace.scenario_id,
                    "adapter": trace.adapter,
                },
                "assert": promptfoo_assertions_for(
                    trace, latency_budget_ms=latency_budget_ms, rubric=rubric
                ),
            }
        )
    return tests


def to_promptfoo_config(
    traces: Iterable[Trace],
    *,
    description: str = "Regression suite exported from lab traces",
    latency_budget_ms: float | None = None,
    rubric: str | None = None,
) -> dict[str, Any]:
    """A promptfooconfig-shaped dict: `description` plus `tests`.

    `providers` and `prompts` are deliberately absent. Which model and which
    prompt to run against is the user's decision and their credentials; inventing
    a provider entry here would produce a config that looks runnable and is not.
    """
    return {
        "description": (
            f"{description}. Assertions assume the provider returns JSON with "
            "`tool_names` and `handoffs`; supply `providers` and `prompts` yourself."
        ),
        "tests": to_promptfoo_tests(
            traces, latency_budget_ms=latency_budget_ms, rubric=rubric
        ),
    }
