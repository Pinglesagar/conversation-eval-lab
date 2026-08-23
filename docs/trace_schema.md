# The trace

One JSONL file per session, one event per line. Everything else in this
repository — every check, every judge, every latency percentile, every row of
every report — is a function of these files and nothing else. That is the whole
architecture: if a result cannot be recomputed from the trace on disk, it cannot
be audited, so it does not get published.

Canonical definition: `lab/trace/schema.py`. Codec: `lab/trace/io.py`. Builder:
`lab/trace/build.py`. This page is the reference; the code is the authority.

## An event

```json
{"ts": 1.374, "kind": "tool_call", "actor": "agent", "engine": null,
 "payload": {"name": "create_booking", "args": {"party_size": 4}, "call_id": "create_booking-1"}}
```

| field | type | meaning |
| --- | --- | --- |
| `ts` | float | monotonic seconds since `session_start`. Never a wall clock: a trace must be comparable between two machines and two decades. |
| `kind` | str | what happened. The closed vocabulary below. |
| `actor` | `caller` \| `agent` \| `system` | whose event it is. `system` covers the harness and the tool layer. |
| `payload` | object | kind-specific. Keys with a `None` value are dropped rather than written, so absence and null never have to be distinguished. |
| `engine` | str \| null | which TTS/STT/LLM produced it, e.g. `harness:passthrough-stt`. Null when no engine was involved. |

`engine` exists so a finding can be attributed to a component rather than to "the
system". A latency distribution split by engine (`lab.voice.metrics.latencies_by_engine`)
is the difference between "the agent is slow" and "one voice is slow".

## Kinds

| kind | actor | payload keys |
| --- | --- | --- |
| `session_start` | system | `session_id`, `scenario_id`, `adapter`, plus any caller/persona metadata |
| `caller_utterance` | caller | `text` |
| `agent_utterance` | agent | `text`, `agent` (which sub-agent spoke) |
| `tool_call` | agent | `name`, `args`, `call_id` |
| `tool_result` | system | `name`, `call_id`, `ok`, `result`, `error` |
| `agent_handoff` | system | `from`, `to`, `reason` |
| `audio_emitted` | either | `num_bytes`, `duration_s` |
| `agent_audio_first_byte` | agent | `turn` |
| `agent_audio_complete` | agent | `turn`, `num_bytes` |
| `audio_delivered` | system | `turn`, `participant` |
| `transcript_in` | caller | `text`, `confidence` |
| `transcript_out` | agent | `text` |
| `transport_connected` | system | `participant`, `attempt` |
| `transport_disconnected` | system | `participant`, `reason` |
| `session_end` | system | `reason`, `turns` |

Five of those exist for reasons that are easy to miss:

**`transcript_in` is not `caller_utterance`.** One is what the caller said, the
other is what the agent heard. On a text adapter they are identical; on a voice
adapter the gap between them is transcription error. Keeping both is what allows
a failure to be attributed to speech recognition instead of to the model — and it
is why the text adapter emits both anyway, so one analysis works on both shapes.

**`agent_audio_first_byte` is the right edge of the latency window**, not
`agent_utterance`. Time to *first byte* is when the answer exists; time to
completion is mostly a statement about how long the answer was. The reference
run's p50 and p95 are both first-byte figures, and the definition is printed in
the report next to the numbers.

**`audio_delivered` is the right edge of the *delivery* window, and it is a
different instant.** `agent_audio_first_byte` is agent-side — the answer exists at
the harness boundary. `audio_delivered` is receiver-side — it arrived where
somebody is listening. Only an adapter with a real transport under it can emit
the second, and pairing the two is the delivery gap: measured at a mean of 87 ms
over real WebRTC (`docs/AUDIO_TRANSPORT.md`), invisible to every in-process
adapter, and excluded from the agent-side figure a voice framework reports. An
in-process adapter leaves the event out rather than re-emitting the agent-side
instant under it, because a delivery gap of zero reads as good news.

**`call_id` correlates a call with its result** rather than relying on adjacency,
so interleaved or parallel tool calls stay analysable.

**`transport_connected.attempt`** makes a reconnect countable. It is 1 on the
initial join and increments on each recovery, so "did the participant come back?"
is a count rather than a parse of a reason string — and a turn that was in flight
when `transport_disconnected` fired can be shown to have died there rather than
being blamed on the agent.

**`session_end.reason`** records *how* a call ended — `caller_hung_up`,
`agent_ended`, `max_turns`. A trace with a `max_turns` reason is a conversation
that ran out of turns, which looks identical to a finished one if you only count
events.

## Declared but not emitted in v1

`interruption_started` and `interruption_acknowledged` are in the vocabulary and
nothing writes them. Barge-in — the caller talking over the agent — is a
first-class voice failure mode, and the schema names it so that a future adapter
has somewhere to put it and so that its absence is visible rather than forgotten.
Any claim about barge-in handling in this repository would be fiction; there is
none.

## Ordering and timing

`Trace.is_ordered()` asserts non-decreasing timestamps. The driver enforces it by
construction: tool and handoff events that happen inside the measured window get
*interpolated* instants, clamped into the window and to a running maximum, and
are stamped `ts_estimated: true` in their payload.

That flag is a boundary, and it is enforced downstream: interpolated timestamps
are for **ordering only, never for timing**. `VoiceMetrics.estimated_timestamps_used`
is computed by checking whether any event a latency figure read from carries it
(`lab/cli.py`, `_uses_estimated_timestamps`), and the report prints the answer.

The measured window itself is deliberately narrow:

```python
t0 = builder.now()      # ---- BOUNDARY OUT
reply = agent(utterance)   # the system under test, and nothing else
t1 = builder.now()      # ---- BOUNDARY IN
# everything below is harness compute, and is not charged to the agent
```

`lab/voice/calibration.py` exists to prove that discipline recovers a delay it
does not know about, across two orders of magnitude, and its report includes a
deliberately naive control that charges harness compute to the agent — which
passes at 2 s and fails at 100 ms, because a fixed additive bias disappears in
relative terms. That is why the calibration sweep spans a range instead of
checking one delay.

## Reading and writing

```python
from lab.trace.io import read_jsonl, write_jsonl

trace = read_jsonl("fixtures/replay_run/traces/edge-large-party-of-six.jsonl")
trace.tool_names()      # ['search_tables']
trace.handoff_pairs()   # [('GreeterAgent', 'BookingAgent')]
trace.duration()        # seconds from first event to last
[e.get("text") for e in trace.utterances()]
```

Or from a shell, since it is JSONL and nothing more:

```bash
jq -r 'select(.kind=="tool_call") | .payload.name' \
  fixtures/replay_run/traces/*.jsonl | sort | uniq -c
```

## Interoperating

`lab/report/interop.py` converts a trace to a Langfuse ingestion batch and back
(`to_langfuse_batch`, `from_langfuse_batch`), and derives promptfoo assertions
from a contract set (`to_promptfoo_config`). The point is not to compete with
those tools: it is that this schema is a superset for the questions this repo
asks, and a lossy subset for theirs, so a trace can go where the rest of a team's
tooling already lives.
