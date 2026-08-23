# A live run, kept

Thirty conversations between the scripted caller corpus and `tablemate.runtime.LLMBackend`
— the configuration where a model, not `tablemate/agents.py`, decides which tool to
call, which colleague to hand the caller to and when the call is over.

    model            azure/gpt-4.1
    temperature      0.7
    scenarios        10 (six rows that reach a seeded defect, four controls)
    repeats          k=3, each an independent set of model calls
    recorded         one run; the model was called 212 times

Two artefacts, and they answer different questions.

`traces/` is what happened. Re-score it with no model, no key and no network:

    python -m tablemate --score fixtures/live_run

That is the audit path for every rate quoted in `tablemate/SEEDED_BUGS.md`. A
percentage in a README that cannot be recomputed from committed evidence is a
claim, not a measurement.

`../live_sessions.json` is *why* it happened: every request the engine made and the
answer it got back, keyed by a digest of the request. Replay drives the engine
itself rather than reading a trace, so it exercises the prompts, the tool loop, the
projection and the handoff translation:

    python -m tablemate --scenario edge-large-party-of-six

The cassette holds every repeat's answer for each request and replays the first, so
replay is deterministic even though the run that produced it was not. `k=3` above is
therefore *not* reproducible from the cassette — repeat 0 is. The variance is
visible in `traces/`, which is the point of keeping both.
