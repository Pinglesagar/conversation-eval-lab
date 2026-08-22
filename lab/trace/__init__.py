"""The trace: schema, codec, builder.

WHAT THIS DEMONSTRATES
----------------------
One typed intermediate representation, shared by every consumer. Adapters are
the only code that knows about vendors; checks, judges, timing and reporting all
read events. See `lab.trace.schema` for the schema and the invariant that every
figure in this repo must be derivable from events alone.
"""

from lab.trace.build import TraceBuilder
from lab.trace.io import iter_jsonl, read_jsonl, read_jsonl_events, write_jsonl
from lab.trace.schema import PAYLOAD_KEYS, Actor, EventKind, Trace, TraceEvent

__all__ = [
    "Actor",
    "EventKind",
    "PAYLOAD_KEYS",
    "Trace",
    "TraceBuilder",
    "TraceEvent",
    "iter_jsonl",
    "read_jsonl",
    "read_jsonl_events",
    "write_jsonl",
]
