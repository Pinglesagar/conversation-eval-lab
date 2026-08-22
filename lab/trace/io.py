"""Reading and writing traces as JSONL.

WHAT THIS DEMONSTRATES
----------------------
The on-disk format is JSONL — one JSON object per line, one line per event — for
reasons that are all about review rather than efficiency:

*   **Diffs are readable.** Two runs of the same scenario produce two files whose
    difference a human can read in a pull request. A single pretty-printed JSON
    blob re-indents on every change and hides the one line that matters.
*   **It streams.** A long session can be appended to as it happens and tailed
    while it runs; a truncated file is still parseable up to the last newline.
*   **It grep-lines.** `grep tool_call` over a fixture directory is a perfectly
    good first pass at error analysis, and costs nothing to build.

Round-trip safety is a tested guarantee: `read_jsonl(write_jsonl(t))` returns a
trace equal to `t`. That matters because recorded fixtures are what let this
repo's live paths replay offline — if serialisation lost a field, every offline
test would be quietly checking something other than what was recorded.

WHERE THE TRACE-LEVEL METADATA LIVES
------------------------------------
A `Trace` carries `session_id`, `scenario_id` and `adapter`, but "one event per
line" leaves nowhere to put them. Rather than prepend a header line that is not
an event — which would make every naive `jq` over the file trip on a special
case — the metadata is carried in the `session_start` event's payload, which is
where it belongs anyway: the identifiers of a session are a fact about the
session starting. `write_jsonl` copies them in, and `read_jsonl` reads them back
out. That keeps the file homogeneous: every line is a `TraceEvent`, no
exceptions.

For a fragment with no `session_start` event, `read_jsonl` falls back to
explicit arguments (and finally to the filename stem), so a partial capture is
still loadable instead of being a hard error. Use `read_jsonl_events` when you
want the bare event list and no reconstruction at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from lab.trace.schema import EventKind, Trace, TraceEvent

__all__ = ["write_jsonl", "read_jsonl", "read_jsonl_events", "iter_jsonl"]

_UNKNOWN = "unknown"


def write_jsonl(trace: Trace, path: str | Path) -> Path:
    """Write `trace` to `path` as JSONL, one event per line.

    The trace's `session_id`, `scenario_id` and `adapter` are written into the
    `session_start` event payload so the file is self-describing. The in-memory
    trace is not mutated.

    Returns the path written, so callers can chain.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for event in trace.events:
        if event.kind == EventKind.SESSION_START:
            payload = {
                **event.payload,
                "session_id": trace.session_id,
                "scenario_id": trace.scenario_id,
                "adapter": trace.adapter,
            }
            event = event.model_copy(update={"payload": payload})
        lines.append(json.dumps(event.model_dump(mode="json"), sort_keys=True))

    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return target


def read_jsonl(
    path: str | Path,
    *,
    session_id: str | None = None,
    scenario_id: str | None = None,
    adapter: str | None = None,
) -> Trace:
    """Read a JSONL file back into a `Trace`.

    Trace-level identifiers come from the `session_start` payload when present.
    The keyword arguments override that; if neither is available, `session_id`
    falls back to the filename stem and the rest to `"unknown"`, so a fragment
    still loads.
    """
    source = Path(path)
    events = read_jsonl_events(source)

    header: dict[str, object] = {}
    start = next((e for e in events if e.kind == EventKind.SESSION_START), None)
    if start is not None:
        header = start.payload

    def pick(explicit: str | None, key: str, fallback: str) -> str:
        if explicit is not None:
            return explicit
        value = header.get(key)
        return str(value) if value is not None else fallback

    return Trace(
        session_id=pick(session_id, "session_id", source.stem),
        scenario_id=pick(scenario_id, "scenario_id", _UNKNOWN),
        adapter=pick(adapter, "adapter", _UNKNOWN),
        events=events,
    )


def read_jsonl_events(path: str | Path) -> list[TraceEvent]:
    """Read a JSONL file into a list of events, with no trace reconstruction."""
    return list(iter_jsonl(path))


def iter_jsonl(path: str | Path) -> Iterable[TraceEvent]:
    """Yield events from a JSONL file one at a time.

    Blank lines are skipped. A malformed line raises `ValueError` naming the
    line number — a silent skip would turn a corrupt fixture into a quietly
    wrong result, which is the failure mode this whole repo exists to prevent.
    """
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield TraceEvent.model_validate(json.loads(stripped))
            except Exception as exc:  # noqa: BLE001 - re-raised with location
                raise ValueError(f"{source}:{lineno}: not a valid TraceEvent: {exc}") from exc
