# The whole corpus, live, kept

Every text-drivable row of the corpus run three times with a model in all three
stochastic seats — the agent's decision seat, the caller's chair and the judge's —
and every exchange recorded so the run replays offline with no key.

    agent            tablemate.runtime.LLMBackend, azure-openai/gpt-4.1, T=0.7
    caller           lab.simulator.LLMCaller, azure-openai/gpt-4.1, T=0.7, budget 12 turns
    judge            hallucinated_confirmation v2, azure-openai/gpt-4.1, T=0
    corpus           47 of 55 rows (8 voice rows need the audio adapter)
    repeats          k=3, each an independent draw
    conversations    141
    model calls      2,056 — 1,301 agent, 717 caller, 38 judge

## Reproduce it

```bash
evallab run -k 3 --live-agent --live-caller --live-judge \
  --baseline fixtures/live_full/run_report.json --ci
```

No key, no network, no spend: `--record` is absent, so each seam replays its
recording. `tests/test_live_run.py` runs exactly that in CI and compares the
findings and the stability verdicts against `run_report.json`, so the numbers in
the README cannot drift from the evidence without a test going red.

The seeded-defect rates come from the same traces through a different reader:

```bash
python -m tablemate --score fixtures/live_full
```

## What is in here, and which question each file answers

| path | question it answers |
| --- | --- |
| `run_report.json` / `.md` | what the contracts and the judge concluded — and the **baseline** the live regression gate diffs against |
| `traces/` | what happened. 141 files, `<scenario>-<repeat>.jsonl`, one per conversation |
| `agent_sessions.json` | *why* it happened on the agent's side: every request the engine made and the answer it got, keyed by a digest of the request |
| `caller/<scenario>/` | one cassette per repeat of the caller, keyed by scenario, persona, prompt digest, model, temperature, turn budget and repeat index |
| `judge_verdicts.jsonl` | the judge's raw output per session, so replay exercises the parser and not a stored verdict |

All k repeats are kept, not just the first. On the deterministic run one trace
stands for all three because they are identical; here they are three different
conversations, and two thirds of the evidence would be thrown away by keeping one.

## Two things to know before quoting anything from here

**Re-recording produces a different report, and that is the measurement.** These
are three draws from one model at one temperature on one day. `k=3` bounds
flakiness loosely: three passes out of three put the 95% Wilson lower bound on the
pass rate at 0.44, so a `STABLE_PASS` row here is consistent with a real failure
rate above one call in two.

**No latency figure from this run is a measurement of the model.** The driver runs
on a `FakeClock` so that fixtures do not depend on how busy a provider was. The
seconds in these traces are `lab.voice`'s simulated latency model; the provider's
real response times were never recorded.
