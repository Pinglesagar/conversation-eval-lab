"""lab — a reusable evaluation harness for conversational AI agents.

WHAT THIS DEMONSTRATES
----------------------
`lab` is the framework half of this repository; `tablemate` is the system it is
pointed at. The split is deliberate and load-bearing: a harness that can only
evaluate the one agent it was written for has not been shown to evaluate
anything. `lab` knows about traces, checks, judges and timing. It does not know
what a restaurant is.

The subpackages, in the order the data flows:

    lab.clock      injectable monotonic clocks — the reason timing is testable
    lab.trace      the trace schema, its JSONL codec, and the builder that
                   emits it. The one intermediate representation everything
                   else consumes.
    lab.simulator  drives a scenario against an adapter and produces a trace
    lab.checks     deterministic, code-only assertions over a trace
    lab.judges     model-graded assertions, for the things code cannot check
    lab.voice      audio-specific measurement: latency, transcription accuracy,
                   and the timing calibration gate that makes the numbers
                   believable in the first place
    lab.report     rendering results for humans

Intended to be split into its own distribution later; nothing in `lab` imports
from `tablemate`, and that boundary is worth keeping.
"""

__version__ = "0.1.0"
