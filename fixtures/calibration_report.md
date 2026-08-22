# Timing calibration report

**Verdict: PASS** — 5/5 delays within tolerance.

- Tolerance: |relative error| <= 5.0% and stdev <= 15.0 ms
- Clock: `FakeClock`
- Repeats per delay: 20 (100 measured turns in total)
- Simulated engine jitter: sigma = 4.0 ms
- Injected harness overhead per turn: 30.0 ms (30% before the boundary, 70% after)
- Seed: 20260822

## Recovered response latency

Measured as `agent_audio_first_byte.ts - caller_utterance.ts`, read back out of the trace by `recover_response_latencies()`.

| nominal | n | mean | p50 | p95 | stdev | abs err | rel err | verdict |
|---|---|---|---|---|---|---|---|---|
| 100 ms | 20 | 100.266 ms | 100.689 ms | 106.336 ms | 4.100 ms | +0.266 ms | +0.266% | PASS |
| 250 ms | 20 | 249.903 ms | 250.167 ms | 256.830 ms | 4.644 ms | -0.097 ms | -0.039% | PASS |
| 500 ms | 20 | 501.180 ms | 501.639 ms | 505.692 ms | 3.435 ms | +1.180 ms | +0.236% | PASS |
| 1000 ms | 20 | 999.531 ms | 999.288 ms | 1006.532 ms | 3.708 ms | -0.469 ms | -0.047% | PASS |
| 2000 ms | 20 | 2000.184 ms | 2001.902 ms | 2005.717 ms | 5.016 ms | +0.184 ms | +0.009% | PASS |

## Control: naive whole-turn wall time

The same turns, measured as `agent_utterance.ts - transcript_in.ts` — the figure a harness gets when it charges its own 30 ms of compute to the agent. Scored against the same tolerance to show what the boundary discipline is worth.

| nominal | naive mean | abs err | rel err | verdict |
|---|---|---|---|---|
| 100 ms | 130.266 ms | +30.266 ms | +30.266% | FAIL |
| 250 ms | 279.903 ms | +29.903 ms | +11.961% | FAIL |
| 500 ms | 531.180 ms | +31.180 ms | +6.236% | FAIL |
| 1000 ms | 1029.531 ms | +29.531 ms | +2.953% | PASS |
| 2000 ms | 2030.184 ms | +30.184 ms | +1.509% | PASS |

**Control verdict: FAIL**

## Notes

- Response latency is agent_audio_first_byte.ts - caller_utterance.ts, recovered from the trace by lab.voice.calibration.recover_response_latencies() — the same function real evaluations use.
- Boundary timestamps are captured as bare floats; the TraceEvents carrying them are constructed after the window closes and back-dated, so the harness never charges its own compute to the agent.
- 30 ms of artificial harness compute is injected per turn and must not move the recovered figure. The naive control table shows what including it would have cost.
- The control's error is a near-constant additive offset, so its relative error shrinks as the delay grows and it can pass the gate at the long end while failing badly at the short end. That is the reason the sweep spans an order of magnitude: a single-delay calibration at 2 s would have certified this broken method.
- Percentiles are linearly interpolated (numpy's default method), implemented in the standard library so the gate never needs an optional dependency.
- Under FakeClock the ground truth is exact and the run is deterministic and offline; under MonotonicClock the nominal delay is time.sleep(), whose own scheduling noise is a few milliseconds and can approach the tolerance at the shortest delay.
