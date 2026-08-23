# `evallab` — command reference

One entry point. Every subcommand runs offline with no API keys; the live paths
are opt-in behind an environment variable and each has a recorded fixture that
replays in its place.

```
evallab {run,validate,report,calibrate,replay}
```

Exit codes are meant to be wired into automation: `0` when the thing the command
gates on held, `1` when it did not, `2` when the command could not run at all
(no scenarios selected, `--live` without its environment variable, a missing
report).

---

## `evallab run` — scenarios to a report

```bash
evallab run                                   # all 47 text rows, k=3, into reports/
evallab run --suite happy --transcript -k 1   # read the conversations
evallab run --scenario edge-large-party-of-six --transcript -k 1
evallab run --ci --out reports                # for automation; see the gate below
```

The pipeline: load and validate the corpus → drive each scenario k times with its
committed caller script → run the scenario's contracts over each trace →
classify every verdict → select the sessions the judge cascade would grade →
write `run_report.md`, `run_report.json` and one trace per scenario.

### The two verdicts

`run` prints both, always, and neither is derived from the other:

```
report verdict:   FAIL — the product's own state
regression gate:  PASS — 0 new, 0 vanished, 0 stale expectation(s), 12 finding(s) total (9 declared by the corpus, 3 not)
baseline:         0 new finding(s), 0 vanished, against 12 in fixtures/replay_run/run_report.json
```

The **report verdict** is FAIL while any contract fails. This build has real
defects, and the artefact is not allowed to hide them.

The **regression gate** is what `--ci` returns. It fails on:

- a finding that is not in the baseline (`--baseline`, default
  `fixtures/replay_run/run_report.json`),
- a finding in the baseline that has *disappeared* — a fix and a check that
  stopped applying look identical from outside, so both stop the build until
  somebody says which in a diff,
- a corpus `expected_failure` that stopped reproducing (same argument, stated by
  the corpus rather than by the baseline),
- a scenario whose k repeats were not byte-identical under `--replay`.

`--no-baseline` gates on undeclared failures alone, which is what a first run on
a new system under test wants. `--strict` additionally fails on the known gaps —
the product verdict as an exit code.

### Useful flags

| flag | why you would |
| --- | --- |
| `--transcript` | print each conversation. The first thing to reach for when a row fails, and how the caller scripts were written. |
| `-k N` | repeats. Under `--replay` this measures harness determinism, not model variance — the report says so in its notes. |
| `--suite`, `--tag`, `--scenario` | subset. All repeatable and comma-separable. The baseline diff is scoped to what actually ran. |
| `--agent-factory pkg.mod:factory` | point the harness at a different system under test. Nothing about `lab` knows about TableMate. |
| `--corpus-module`, `--corpus` | point it at a different corpus. |
| `--live` | paraphrase the agent's turns through a provider. Needs `LAB_LIVE_AGENT=1`; refuses otherwise rather than quietly replaying. |
| `--raise-errors` | let a harness exception propagate instead of being recorded as a failed run. For debugging the harness itself. |

---

## `evallab validate` — the corpus is a dataset

```bash
evallab validate --coverage      # summary, then tag/tool/perturbation coverage
evallab validate --strict        # warnings are errors
evallab validate --json          # for a dashboard
```

Exit 1 on any error. A malformed scenario rarely crashes — it quietly asserts
less than its author believed and then passes — so the schema rejects the
patterns that go green for the wrong reason: a tool name with a typo, a tracked
field whose value the caller never says, an `expected_failure` naming a contract
the row does not declare.

---

## `evallab replay` — recompute a verdict from a trace alone

```bash
evallab replay                                          # every committed trace
evallab replay fixtures/replay_run/traces/edge-large-party-of-six.jsonl
evallab replay --failures-only --ci
```

No agent, no scenario runner, no clock: the contracts read the JSONL and produce
the same verdicts. This is the auditability claim made executable — a number in
the report either recomputes from the file on disk or it was never evidence — and
it is how a disagreement gets settled, because the artefact is the trace and not
the summary.

---

## `evallab calibrate` — the two gates

```bash
evallab calibrate            # both
evallab calibrate --timing   # the stopwatch only
evallab calibrate --judges   # the judge only
```

**Timing.** Drives an agent whose latency is known by construction across delays
from 100 ms to 2 s and reports what it recovered against a stated tolerance,
alongside a deliberately naive control that charges the harness's own compute to
the agent. Writes `fixtures/calibration_report.{json,md}`. Non-zero exit on
failure, so an untrustworthy stopwatch stops a pipeline instead of publishing
decoration.

**Judge.** Regenerates the labelled set, the recorded verdicts and both
calibration reports for the `hallucinated_confirmation` judge, prints the v1→v2
delta table, and then puts the judge the run reports through the registry gate
(TPR ≥ 0.85, TNR ≥ 0.85, n ≥ 10, no parse errors). Deterministic: every artefact
is byte-identical between runs, so `git status` after a regeneration tells you
whether anything actually changed.

---

## `evallab report` — re-render from the JSON

```bash
evallab report                                   # the committed reference run
evallab report reports --out /tmp/rendered
evallab report --heatmap /tmp/handoffs.png       # needs .[charts]
```

Reads `run_report.json`, rebuilds the markdown from it, and refuses if the
verdict stored in the file disagrees with the verdict recomputed from the counts
beside it — which is what a hand-edited artefact looks like. If the markdown can
be rebuilt from the JSON alone, then the JSON is the whole report and a dashboard
reading it is not seeing a lossy summary of what a human read.

With `--heatmap` it also prints the handoff matrix: failures over attempts per
transition, with cells that never happened left blank rather than drawn as zero
failures, because "never attempted" and "never failed" are opposite findings.
