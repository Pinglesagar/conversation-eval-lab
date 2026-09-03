# Plugging your own agent into the roleplay harness

The adviser under test in `roleplay/` is whatever satisfies one two-method protocol.
Until now both runners hardcoded the built-in model-backed adviser; this page is the
seam that replaces it. **You write the wrapper; the contracts, judges, scorecard and
reports run unchanged.**

## The contract

`roleplay/runtime.py`:

```python
class Trainee(Protocol):
    def open(self) -> str | None: ...                    # the first turn, or None to decline
    def reply(self, customer_turn: str) -> str | None: ...  # the next turn, or None to stop
```

Two optional attributes make the result readable: `stop_reason` (a short string the
harness records when `reply()` returns None — `"handed_off"`, `"agent_ended"`, whatever
is true) and `__repr__` (recorded as `trainee_source` in the trace, so give it one; a
default object repr puts a memory address into a fixture).

A factory turns that into something the runners can import: a callable taking one
`roleplay.live.TraineeContext` — scenario id, customer profile, competence,
jurisdiction, language, turn budget, model label — and returning a `Trainee`. Point the
runner at it with `LAB_TRAINEE_FACTORY=package.module:callable` or `--trainee-factory`.
The flag beats the env var; with neither set, the built-in adviser is built exactly as
before, so every committed cassette and the committed spoken call replay unchanged.

## Three runnable examples (`examples/adapters/`)

| file | wraps | code lines / `wc -l` |
| --- | --- | --- |
| `echo_trainee.py` | nothing — proves the contract | 21 / 41 |
| `http_trainee.py` | any HTTP endpoint: `POST {"event","customer_turn","turn"}` → `{"reply"}`; stdlib only | 28 / 51 |
| `callable_trainee.py` | any Python function `history -> str \| None`; the in-process pattern | 31 / 56 |

"Code lines" are non-blank lines after the module docstring, which is the part you
would keep; the docstring is the part that tells you what to change.

Each docstring says exactly what to change to point it at a real system: the body of
`reply()`, the `TRAINEE_HTTP_URL` env var, or the function passed in `build_trainee`.

## One command

```
LAB_TRAINEE_FACTORY=examples.adapters.echo_trainee:build_trainee \
    python -m roleplay.live --scripted-customer --only live-eu-cautious --root /tmp/echo_run
```

Offline, no key: `--scripted-customer` uses the persona's own wording for the customer,
so no model is anywhere in the loop. Drop it to have the customer voiced by a model
(needs `LAB_LIVE_CUSTOMER=1`, a provider key and `LAB_CUSTOMER_MODEL`). The spoken
runner takes the same flag — `python -m roleplay.spoken --record --trainee-factory ...` —
and your adviser's words then cross the real TTS → STT channel before being graded.

What the command prints (3 of the 10 matrix rows, the echo adviser, this checkout):

```
  stop reasons: echo_budget=3
  turns recorded live this run: 0; replayed from cassette: 0
  pass mark for reference: 14/20
```

Every row is scored `n/20` with its disclosure register `disc k/3` and its stop reason
on one line; the echo adviser fails all three, as it should.

## What the trace looks like after

Same JSONL, same event kinds, same contracts. Two fields tell you who was in the room,
taken from a run of the command above (`run_live_session(...).result.trace`):

```json
{"kind": "session_start", "payload": {"adapter": "roleplay:text", "scenario_id": "live-eu-cautious-competent",
  "profile": "cautious_saver", "jurisdiction": "eu-retail", "language": "en",
  "trainee_source": "EchoTrainee(turns=3)", "customer_voice": "ScriptedVoice()", "trainee_turns": 12, ...}}
{"kind": "caller_utterance", "payload": {"text": "Good morning. Before we look at anything, ...", "turn": 1}}
...
{"kind": "session_end", "payload": {"reason": "scored", "stop_reason": "echo_budget", "turns": 3}}
```

`trainee_source` is your `__repr__`; `stop_reason` is your `stop_reason`, or `no_reply`
if you did not set one. In between, the adviser's lines arrive as `caller_utterance`
(the harness's convention: the trainee is the caller, the customer is the agent), the
disclosure register's `record_disclosure` and the compliance flagger's
`flag_compliance_risk` tool calls are emitted against *your* words, and the six
position-based contracts and the 28-KPI scorecard read the file as they always did.

## When it fails

| you did | you see |
| --- | --- |
| `open()` returned None | `ValueError: <scenario>: the trainee produced no turns ... (stop reason: <yours>)` — an empty session is never scored as a bad one |
| `reply()` returned None | the session ends; `session_end.stop_reason` is your `stop_reason`, else `no_reply` |
| a typo in the dotted path | exit 2, one sentence naming the path and the setting it came from; no traceback |
| the factory returned something without `open()`/`reply()` | `TraineeFactoryError` naming the factory and the two methods |

Tests: `tests/test_roleplay_adapter.py` (36 tests) — including one that replays every
committed cassette through the seam and through the pre-seam construction and asserts
the same utterances and the same card, and one that diffs the committed spoken
scorecard byte for byte.
