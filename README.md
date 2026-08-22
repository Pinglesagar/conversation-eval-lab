# tablemate-evals

An evaluation harness for conversational AI agents — voice and text — plus an
applied case study that points it at a deliberately imperfect restaurant-booking
agent. The reusable half is the `lab` package: a typed trace schema that every
check, judge and timing metric reads from, so results are reproducible, auditable
and attributable to a component rather than to a vibe. The case study half is
`tablemate`, a multi-agent booking assistant carrying documented defects, because
a harness demonstrated against a working agent proves nothing — green results are
equally consistent with a good agent and a blind test suite.

**Status: under construction.** The foundation is in place — the trace schema and
the timing calibration gate. The system under test, the checks, the judges and the
report renderer land in subsequent steps.

## Runs with zero API keys

That is the cardinal rule, not an aspiration. A clean clone installs and goes
green in under two minutes with no credentials of any kind. Everything that talks
to a live provider is opt-in behind an environment variable, and every live path
has a recorded fixture that replays deterministically in its place.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Optional extras: `[audio]` for word-error-rate scoring, `[charts]` for plots.
Neither is needed to run the test suite.

## The calibration gate

An uncalibrated latency number is decoration. Before any timing figure in this
repo is believed, the harness has to prove it can recover a delay it does not
know about, reading the answer back out of a trace through the same code path a
real evaluation uses:

```bash
make calibrate
```

It drives an agent whose latency is known by construction across delays from
100 ms to 2 s, and reports what it recovered against a stated tolerance — plus a
deliberately naive control that shows what including the harness's own compute
in the measurement would have cost. Output lands in
[`fixtures/calibration_report.md`](fixtures/calibration_report.md), with every
individual sample kept in the JSON so the aggregates can be recomputed. Exit code
is non-zero on failure, so an untrustworthy stopwatch stops a pipeline instead of
quietly publishing decoration.

## Layout

```
lab/          the reusable harness (destined for its own repo)
  clock.py    injectable monotonic clocks — why timing here is testable
  trace/      the trace schema, its JSONL codec, and the builder
  voice/      latency and audio measurement, incl. the calibration gate
  checks/     deterministic assertions over a trace
  judges/     model-graded assertions, for what code cannot check
  simulator/  drives a scenario against an adapter
  report/     rendering results for humans
tablemate/    the system under test: a multi-agent booking assistant
scenarios/    what the simulated caller wants and how they behave
fixtures/     recordings — the reason this runs with no API keys
error_analysis/  hand-read failures behind the aggregate numbers
```

## Make targets

| target | what it does |
| --- | --- |
| `make install` | editable install with dev extras |
| `make test` | the full offline suite |
| `make calibrate` | run the timing calibration gate and write its report |
| `make demo` | the case study end to end (arrives with `tablemate/`) |
| `make report` | render the evaluation report (arrives with `lab/report/`) |

## License

MIT. See [LICENSE](LICENSE).
