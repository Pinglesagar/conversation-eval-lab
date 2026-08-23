"""The flake band: what pass^k reports when the caller is a real model.

WHY THIS MODULE EXISTS
----------------------
`lab.simulator.passk` was written to make one argument: a single run of a
scenario against a stochastic system is one sample from a distribution, and
reporting that sample as PASS is the commonest way an eval suite lies to its
owner. Until this module, that argument had never been tested. Every k-repeat
this repo had ever run drove a deterministic caller against a deterministic
backend, so every verdict came back 5/5, and `--replay` says so in as many words:
it measures the harness's reproducibility, not the model's variance.

Machinery that has only ever seen unanimity is machinery whose FLAKY branch has
never fired on real data. So this module puts one live variable into the loop —
the caller — and reports what pass^k finds:

    agent      scripted backend, FakeClock, fresh restaurant per repeat
    caller     a model, temperature 0.7, playing the scenario's persona and goal
    k          5 repeats per scenario
    verdict    the same gate the suite uses (`lab.cli.evaluate_trace`)

THE CALLER IS THE ONLY LIVE VARIABLE, AND THAT IS THE WHOLE DESIGN
------------------------------------------------------------------
It would be easier and much less useful to run both sides live. With a live
agent, a FLAKY verdict has two possible causes and no way to choose between
them, and "the suite is flaky" is where that investigation stops. Here the agent
is deterministic by construction — same backend, same seed, same clock — so every
disagreement between repeats is attributable to the caller's wording, which is
also the honest model of production: real callers are the stochastic part of a
phone line, and an agent that only works when the caller phrases things the way
the fixture did is not a working agent.

The consequence to state plainly: **the flake rate this module reports is a
property of the pair**, not of the agent alone. It is the rate at which a
differently-worded caller changes the verdict. That is the number a person
deciding whether to trust a green suite actually needs.

REPRODUCIBLE, WHICH IS NOT THE SAME AS DETERMINISTIC
----------------------------------------------------
Each of the k repeats is recorded as its own cassette, keyed by
`(scenario, persona, prompt digest, repeat)` — see `driver.CassetteKey`, and note
that the repeat index is in the key precisely because sharing one cassette across
k repeats would replay the first conversation five times and report a flake rate
of zero. Once recorded, the whole band replays offline from the committed
fixtures with no key and no network, and recomputes bit-identically. So the
number is auditable: anyone can rerun the exact forty conversations that produced
it, read the transcripts, and disagree with the verdicts.

Re-recording is a deliberate act (`--record`, plus `LAB_LIVE_CALLER=1`), and it
produces a *different* band, because it draws different samples. That is not a
defect in the measurement; it is the measurement.

WHAT THE FIRST RUN ACTUALLY FOUND
--------------------------------
Two bands are committed, at caller turn budgets of 12 and 8. The headline is the
first; the second is kept because of what it says about the first.

    budget 12    STABLE_PASS 7/8, FLAKY 1/8, repeats failing the gate 1/40
    budget  8    STABLE_PASS 5/8, FLAKY 3/8, repeats failing the gate 6/40

Four things came out of those eighty conversations, in descending order of how
much they matter.

**1. Two defects in the agent that 55 scripted scenarios had never reached.**
Neither is one of the three seeded bugs; both are in the deterministic
understanding layer, and both need a caller who chose its own words.

*   `wants_to_end` matches the pattern `that('s| is) (it|all|everything|lovely|
    great)`, which was written for a sign-off and also matches "Oh, that's great
    to hear!" — a caller expressing enthusiasm two turns into a booking. The agent
    replies "Lovely — we will see you then. Thanks for calling Lumen" and ends the
    call, with zero tool calls and nothing booked. It happened in both bands, on
    independent draws.
*   `extract_slots` reads "8pm" and "8 pm" but not "8 o'clock", and not "anything
    after 7". Given either, the agent asks "What time would suit you?" and keeps
    asking: the caller answered three times, in three different phrasings, and the
    same question came back each time. The call was ended by the *caller's*
    repetition guard, not by the agent noticing.

Both are the same shape of failure and it is worth naming: a rules-based
understander is deterministic, which the case study relies on, but determinism is
not coverage. A scripted caller says "8pm" because the fixture's author knew what
the parser accepts. That is the blind spot, and it is invisible from inside the
fixture.

**2. A defect in the instrument, found and fixed.** Two conversations in the first
band appended `[END OF CALL]` to the turn carrying the caller's last answer — the
name the agent had just asked for. Ending the call there meant the agent never got
a turn in which to act on it, so no booking was made and the contract failed
against the agent for something the harness did. `driver._split_sentinel` now
delivers the words and defers the hang-up by one turn. The fix took the band from
2/40 failing repeats to 1/40, and the whole of that improvement was the harness
stopping lying, not the agent improving. Which is the argument for measuring the
instrument: without the audit those two rows were indistinguishable from findings.

**3. One instrument *setting* decided a verdict.** At a budget of 8,
`edge-reluctant-caller-two-asks` scored 1/5 with four of its five repeats stopped
by the budget. That persona stalls on every first ask and its scenario gates all
four facts, so it needs eight caller turns before the last fact is spoken — the
row was not unstable, it was starved. At 12 it is 5/5. A number that moves when
you change a constant in the harness is a number that has to be reported *with*
the constant, which is why `caller_turn_budget` is a field on `FlakeBand` and why
the losing configuration is committed rather than deleted.

**4. The band is itself one sample.** Two independent draws of the same eight
scenarios against the same agent and the same caller model disagreed: 1/8 flaky
and 3/8 flaky. That is the argument `passk` makes about scenarios, turned on the
band — and the most honest single sentence available about this measurement is
that k=5 over 8 scenarios locates the flake rate somewhere in the low tens of
percent and no more precisely than that.

WHAT THIS CANNOT TELL YOU
-------------------------
k=5 bounds the flake rate loosely: five green repeats are consistent with a
scenario that fails one time in twenty. Every row prints its denominator, and
`passk` says the rest. The band is also specific to one model at one temperature
against one build of one agent; it is a reading, not a constant.

The flake rate is a property of the caller-and-agent *pair*, and the leak count is
a floor rather than a total (`LLMCaller.leak_detection_note`). Neither number
should be quoted without the sentence that qualifies it.

USAGE
-----
    python -m lab.simulator.flake_band                 # replay the committed band
    python -m lab.simulator.flake_band --check         # replay, fail if it moved
    LAB_LIVE_CALLER=1 LAB_CALLER_MODEL=... \
      python -m lab.simulator.flake_band --record      # draw a new band (spends money)

LAYERING
--------
`lab` must not depend on the case study, so the corpus, the agent factory and the
caller fixtures are resolved lazily by dotted path, exactly as `lab.cli` resolves
them. Importing this module pulls in neither `scenarios` nor `tablemate`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.simulator.driver import LLMCaller, run_scenario
from lab.simulator.passk import (
    RunOutcome,
    StabilityVerdict,
    format_rate,
    run_pass_k,
    summarise_stability,
)
from lab.trace.schema import EventKind, Trace

__all__ = [
    "DEFAULT_SCENARIOS",
    "DEFAULT_K",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_CASSETTE_ROOT",
    "DEFAULT_SUMMARY_PATH",
    "TIGHT_BUDGET_SUMMARY_PATH",
    "DEFAULT_MODEL_LABEL",
    "CALLER_MAX_UTTERANCES",
    "DRIVER_MAX_TURNS",
    "FlakeBandRow",
    "FlakeBand",
    "run_flake_band",
    "build_parser",
    "main",
]

#: The eight scenarios the band is measured over.
#:
#: Chosen against three criteria, in this order.
#:
#: 1.  **Every one of them is STABLE_PASS under the scripted caller.** A scenario
#:     that already fails deterministically tells you nothing about caller
#:     variance, because it fails whatever the caller says. So a FLAKY verdict
#:     here is new information by construction.
#: 2.  **The disclosure model is exercised.** Four of the eight gate some or all
#:     of their facts behind `on_request_only`, which is where a live caller can
#:     actually differ from a script: whether it waits to be asked, and whether it
#:     answers the question it was asked.
#: 3.  **Five personas, three suites.** A band measured on one persona is a
#:     measurement of that persona.
#:
#: `edge-large-party-of-six` is in the list for a fourth reason: it is the only
#: row here that *declares* a known defect (BUG-1, the phantom confirmation). A
#: live caller that never gets as far as asking for a table for six will not
#: reproduce it, and the gate reports that as a stale expectation rather than a
#: pass. Whether the corpus's known gaps survive a differently-worded caller is
#: worth knowing, and it is the same question as "is the baseline real".
DEFAULT_SCENARIOS: tuple[str, ...] = (
    "happy-two-covers-thursday",
    "happy-availability-then-choice",
    "happy-dietary-note-single-agent",
    "happy-vague-opener-then-details",
    "edge-volunteers-nothing",
    "edge-reluctant-caller-two-asks",
    "edge-large-party-of-six",
    "adversarial-abuse-then-real-booking",
)

#: Repeats per scenario. Five is the smallest k that can distinguish "unanimous"
#: from "mostly" without being able to hide a single dissenter in a rounding.
DEFAULT_K: int = 5

#: Sampling temperature for the caller. Not zero, on purpose: a T=0 caller is a
#: slower `ScriptedCaller` and would measure nothing. 0.7 is ordinary
#: conversational sampling — high enough that wording really varies, low enough
#: that the caller is still playing its persona rather than free-associating.
DEFAULT_TEMPERATURE: float = 0.7

#: Where the recorded conversations live, one directory per scenario.
DEFAULT_CASSETTE_ROOT: str = "fixtures/live_caller"

#: The committed band. `--check` recomputes and compares against this file.
DEFAULT_SUMMARY_PATH: str = "fixtures/live_caller/flake_band.json"

#: The starved first attempt, kept as evidence. See `CALLER_MAX_UTTERANCES`.
TIGHT_BUDGET_SUMMARY_PATH: str = "fixtures/live_caller/flake_band_budget8.json"

#: What the fixtures say the turns came from. A *label*, never the litellm route:
#: a route can name a private deployment inside somebody's cloud account, and
#: these fixtures are public. Overridable with `--model-label`.
DEFAULT_MODEL_LABEL: str = "azure-openai/gpt-4.1"

#: Caller turn budget, and the driver's hard stop above it. The caller's cap is
#: the lower of the two so that a run which goes nowhere ends with a caller-side
#: `stop_reason` naming the guard that fired, rather than with a bare
#: `max_turns` that reads in a report like an agent which could not finish.
#:
#: Twelve, and the number was chosen the hard way. The first band was drawn at
#: eight, and its single FLAKY row turned out to be an artefact of that eight:
#: `edge-reluctant-caller-two-asks` pairs a persona that stalls on every first ask
#: with four facts that all have to be asked for, which costs eight caller turns
#: before the last fact is even spoken. The row was not unstable, it was starved,
#: and a budget that structurally cannot complete one of the personas in the
#: corpus is measuring the harness. `fixtures/live_caller/flake_band_budget8.json`
#: keeps that run, because "the instrument's own setting decided a verdict" is
#: worth being able to show rather than assert.
CALLER_MAX_UTTERANCES: int = 12
DRIVER_MAX_TURNS: int = 14

_DEFAULT_CORPUS_MODULE = "scenarios.loader"
_DEFAULT_AGENT_FACTORY = "tablemate.runtime:build_agent"
_DEFAULT_SCRIPTS = "fixtures/caller_scripts.yaml"


# --------------------------------------------------------------------------- #
# The result
# --------------------------------------------------------------------------- #


class FlakeBandRow(BaseModel):
    """One scenario's k repeats, with the evidence and the instrument's own state.

    The instrument fields — `stop_reasons`, `leaks_detected` — are not decoration.
    A FLAKY verdict caused by a caller that looped, or that blurted a gated fact,
    is a finding about the harness, and a row that reported only the verdict would
    file it against the agent.
    """

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    suite: str
    persona: str
    verdict: str
    passes: int = Field(ge=0)
    total_runs: int = Field(ge=0)
    failed_checks: list[str] = Field(default_factory=list)
    first_evidence: str | None = None
    caller_turns: list[int] = Field(
        default_factory=list, description="Caller utterances per repeat."
    )
    stop_reasons: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Why each call ended, counted across the repeats. `goal_reached` is the "
            "caller judging its own goal met or refused; `turn_budget` and "
            "`repeated_line` are guards in `LLMCaller` firing; an `agent:` prefix "
            "means the caller never stopped and the agent hung up first, which is a "
            "very different result and must not read as the same one."
        ),
    )
    leaks_detected: int = Field(
        default=0,
        ge=0,
        description=(
            "Gated facts said before they were asked for, summed over the repeats. "
            "A lower bound — see `LLMCaller.leak_detection_note`."
        ),
    )

    @property
    def failures(self) -> int:
        return self.total_runs - self.passes

    @property
    def pass_rate_str(self) -> str:
        return format_rate(self.passes, self.total_runs)

    @property
    def flake_rate_str(self) -> str:
        return format_rate(min(self.passes, self.failures), self.total_runs)

    @classmethod
    def from_verdict(
        cls,
        verdict: StabilityVerdict,
        *,
        suite: str,
        persona: str,
        callers: Sequence[LLMCaller],
        traces: Sequence[Trace | None] = (),
    ) -> "FlakeBandRow":
        padded = list(traces) + [None] * (len(callers) - len(traces))
        return cls(
            scenario_id=verdict.scenario_id,
            suite=suite,
            persona=persona,
            verdict=verdict.verdict,
            passes=verdict.passes,
            total_runs=verdict.total_runs,
            failed_checks=verdict.failed_check_names(),
            first_evidence=verdict.first_evidence(),
            caller_turns=[len(c.utterances) for c in callers],
            stop_reasons=dict(
                Counter(
                    _stop_label(c, t) for c, t in zip(callers, padded)
                ).most_common()
            ),
            leaks_detected=sum(len(c.leaks) for c in callers),
        )


def _stop_label(caller: LLMCaller, trace: Trace | None) -> str:
    """Why this call ended, from whichever side ended it.

    A caller with no `stop_reason` did not stop: the agent did, or the driver's
    hard limit did. Collapsing that into "unknown" would put a caller who was
    satisfied and an agent who hung up mid-booking in the same bucket — and the
    second of those is a finding.
    """
    if caller.stop_reason:
        return caller.stop_reason
    if trace is None:
        return "unknown"
    end = trace.first(EventKind.SESSION_END)
    reason = end.get("reason") if end else None
    return f"agent:{reason}" if reason else "unknown"


class FlakeBand(BaseModel):
    """The band: every row, plus the provenance needed to read it.

    Provenance is in the model rather than in a README because the number means
    nothing without it. A flake rate is a reading of one caller model at one
    temperature against one build of one agent, and a bare percentage invites
    exactly the over-reading this repo argues against.
    """

    model_config = ConfigDict(extra="forbid")

    caller_model: str = Field(description="Model label the caller turns came from.")
    temperature: float
    k: int = Field(ge=1)
    agent: str = Field(description="What played the agent, and how deterministic it was.")
    caller_turn_budget: int = Field(
        ge=1,
        description=(
            "The caller's `max_utterances`. Provenance, not trivia: this setting "
            "has been observed to decide a verdict on its own."
        ),
    )
    driver_max_turns: int = Field(ge=1, description="The driver's hard stop.")
    rows: list[FlakeBandRow]

    # ----------------------------------------------------------------- figures

    @property
    def scenarios(self) -> int:
        return len(self.rows)

    @property
    def stable_pass(self) -> int:
        return sum(1 for r in self.rows if r.verdict == "STABLE_PASS")

    @property
    def stable_fail(self) -> int:
        return sum(1 for r in self.rows if r.verdict == "STABLE_FAIL")

    @property
    def flaky(self) -> int:
        return sum(1 for r in self.rows if r.verdict == "FLAKY")

    @property
    def total_runs(self) -> int:
        return sum(r.total_runs for r in self.rows)

    @property
    def run_failures(self) -> int:
        return sum(r.failures for r in self.rows)

    @property
    def leaks_detected(self) -> int:
        return sum(r.leaks_detected for r in self.rows)

    @property
    def scenario_flake_rate_str(self) -> str:
        """Scenarios whose k repeats disagreed with each other, over scenarios.

        The headline. It is a rate over *scenarios* and not over runs, because the
        unit of reporting is the scenario verdict: one scenario that fails 2 of 5
        is one unreliable scenario, and averaging it into a run-level percentage
        is how a suite comes to look 92% healthy while a tenth of its rows cannot
        be trusted.
        """
        return format_rate(self.flaky, self.scenarios)

    @property
    def run_failure_rate_str(self) -> str:
        """Individual repeats that failed the gate, over repeats run."""
        return format_rate(self.run_failures, self.total_runs)

    def describe(self) -> str:
        """The band as text, denominators everywhere."""
        lines = [
            f"caller: {self.caller_model} at T={self.temperature}  |  agent: {self.agent}",
            (
                f"k={self.k} per scenario, {self.total_runs} conversations in "
                f"total, caller turn budget {self.caller_turn_budget}"
            ),
            "",
            f"  STABLE_PASS  {format_rate(self.stable_pass, self.scenarios)} scenarios",
            f"  FLAKY        {self.scenario_flake_rate_str} scenarios  <- the band",
            f"  STABLE_FAIL  {format_rate(self.stable_fail, self.scenarios)} scenarios",
            f"  repeats failing the gate: {self.run_failure_rate_str}",
            f"  gated facts leaked by the caller: {self.leaks_detected} (lower bound)",
            "",
        ]
        width = max(len(r.scenario_id) for r in self.rows) if self.rows else 0
        for row in self.rows:
            lines.append(
                f"  {row.scenario_id:<{width}}  {row.verdict:<11} "
                f"passed {row.pass_rate_str:<14} turns {row.caller_turns} "
                f"stops {row.stop_reasons}"
            )
            if row.first_evidence:
                lines.append(f"      {row.first_evidence}")
        return "\n".join(lines)

    # ------------------------------------------------------------------- io

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"

    @classmethod
    def load(cls, path: str | Path) -> "FlakeBand":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Running it
# --------------------------------------------------------------------------- #


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else _repo_root() / candidate


def _import_object(dotted: str) -> Any:
    """`module:attr`, resolved by `lab.cli`'s importer.

    Reused rather than reimplemented: that importer knows to look beside the
    library as well as on `sys.path`, because the case study ships in the
    checkout and not in the wheel, and a second copy of that logic would drift.
    """
    from lab.cli import _import_object as cli_import_object

    return cli_import_object(dotted)


def run_flake_band(
    *,
    scenario_ids: Sequence[str] = DEFAULT_SCENARIOS,
    k: int = DEFAULT_K,
    root: str | Path = DEFAULT_CASSETTE_ROOT,
    model: str | None = None,
    model_label: str = DEFAULT_MODEL_LABEL,
    temperature: float = DEFAULT_TEMPERATURE,
    record: bool = False,
    max_utterances: int = CALLER_MAX_UTTERANCES,
    max_turns: int = DRIVER_MAX_TURNS,
    corpus_module: str = _DEFAULT_CORPUS_MODULE,
    agent_factory: str = _DEFAULT_AGENT_FACTORY,
    scripts: str = _DEFAULT_SCRIPTS,
    extra_completion_kwargs: Mapping[str, Any] | None = None,
) -> FlakeBand:
    """Run (or replay) k repeats of each scenario with a live-or-recorded caller.

    Args:
        scenario_ids: Which corpus rows to measure.
        k: Repeats per scenario. Each repeat is its own cassette.
        root: Cassette root. One directory per scenario beneath it.
        model: litellm route for the caller. Only needed with `record=True`.
        model_label: What the fixtures say the turns came from.
        temperature: Caller sampling temperature. Part of the cassette key.
        max_utterances: The caller's turn budget. Part of the cassette key, so
            two budgets produce two sets of fixtures rather than one overwriting
            the other.
        max_turns: The driver's hard stop, kept above `max_utterances`.
        record: True permits provider calls and writes the cassettes. Requires
            `LAB_LIVE_CALLER=1` as well — this flag alone is not the switch, so a
            script cannot turn live calls on without the environment agreeing.
        corpus_module, agent_factory, scripts: The case study, by dotted path.
        extra_completion_kwargs: Provider settings (`api_base`, `api_version`)
            passed through to `litellm.completion`. Never recorded.

    Returns:
        The `FlakeBand`. Nothing is written; the caller decides.
    """
    from lab.cli import evaluate_trace, load_caller_scripts
    from lab.clock import FakeClock

    if record and not os.environ.get("LAB_LIVE_CALLER"):
        raise RuntimeError(
            "--record needs LAB_LIVE_CALLER=1 as well. Two switches, because "
            "recording spends money and a flag in a script is easier to set by "
            "accident than an environment variable is."
        )

    loader = _import_object(f"{corpus_module}:load_corpus")
    build_agent = _import_object(agent_factory)
    corpus = loader()
    by_id = {s.id: s for s in corpus.scenarios}
    all_scripts = load_caller_scripts(scripts)

    missing = [sid for sid in scenario_ids if sid not in by_id]
    if missing:
        raise KeyError(f"not in the corpus: {missing}")

    cassette_root = _resolve(root)
    rows: list[FlakeBandRow] = []
    verdicts: list[StabilityVerdict] = []

    for scenario_id in scenario_ids:
        scenario = by_id[scenario_id]
        profile = scenario.caller_profile(corpus.personas)
        script = all_scripts.get(scenario_id)
        callers: list[LLMCaller] = []
        traces: list[Trace | None] = []

        # `callers` and `traces` are bound as defaults rather than closed over.
        # They are per-scenario accumulators, `run` is only ever called within the
        # iteration that created them, and binding says so — a closure over a loop
        # variable is correct here and one refactor away from not being.
        def run(
            index: int,
            scenario: Any = scenario,
            profile: Any = profile,
            script: Any = script,
            callers: list[LLMCaller] = callers,
            traces: list[Trace | None] = traces,
        ) -> Trace:
            clock = FakeClock()
            agent = build_agent(
                clock=clock, seed=script.seed_fn() if script is not None else None
            )
            caller = LLMCaller.for_scenario(
                profile,
                scenario_id=scenario.id,
                root=cassette_root,
                model=model,
                model_label=model_label,
                temperature=temperature,
                variant=index,
                max_utterances=max_utterances,
                extra_completion_kwargs=dict(extra_completion_kwargs or {}),
            )
            callers.append(caller)
            traces.append(None)
            try:
                trace = run_scenario(
                    scenario_id=scenario.id,
                    agent=agent,
                    caller=caller,
                    adapter="text:live-caller",
                    clock=clock,
                    session_id=f"{scenario.id}#{index}",
                    max_turns=max_turns,
                )
                traces[-1] = trace
                return trace
            finally:
                # Save even when the run raised. A conversation that was paid for
                # and then thrown away is money spent to learn nothing, and the
                # turns that did happen are still evidence.
                if record:
                    caller.save()

        def evaluate(trace: Trace, scenario: Any = scenario) -> RunOutcome:
            evaluation = evaluate_trace(scenario, trace)
            return RunOutcome(
                index=0,  # restamped by run_pass_k
                passed=evaluation.gate_passed,
                session_id=trace.session_id,
                evidence=evaluation.gate_evidence(),
                failed_checks=evaluation.failed_check_names(),
            )

        verdict = run_pass_k(
            scenario_id=scenario_id,
            k=k,
            run=run,
            evaluate=evaluate,
            label=scenario.suite,
        )
        verdicts.append(verdict)
        rows.append(
            FlakeBandRow.from_verdict(
                verdict,
                suite=scenario.suite,
                persona=profile.persona.name,
                callers=callers,
                traces=traces,
            )
        )

    # Scored by the same summariser the suite uses, so the two numbers are
    # comparable rather than merely similar.
    summary = summarise_stability(verdicts)
    assert summary.scenarios == len(rows)

    return FlakeBand(
        caller_model=model_label,
        temperature=temperature,
        k=k,
        agent="scripted backend, FakeClock, fresh restaurant per repeat (deterministic)",
        caller_turn_budget=max_utterances,
        driver_max_turns=max_turns,
        rows=rows,
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lab.simulator.flake_band",
        description=(
            "Run pass^k with a live (or recorded) model caller against the "
            "scripted agent, and report the flake band."
        ),
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="draw a new band from a provider (also needs LAB_LIVE_CALLER=1; spends money)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="replay and fail if the band no longer matches the committed summary",
    )
    parser.add_argument(
        "--write-summary",
        action="store_true",
        help=(
            "recompute the summary from the committed cassettes and overwrite it. "
            "Draws no new samples: the cassettes are the measurement and the "
            "summary is derived from them, so this is a rebuild, not a re-run."
        ),
    )
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="repeats per scenario")
    parser.add_argument(
        "--model",
        default=None,
        help="litellm route for the caller; defaults to $LAB_CALLER_MODEL",
    )
    parser.add_argument("--model-label", default=DEFAULT_MODEL_LABEL)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument(
        "--max-utterances",
        type=int,
        default=CALLER_MAX_UTTERANCES,
        help="caller turn budget; part of the cassette key",
    )
    parser.add_argument("--root", default=DEFAULT_CASSETTE_ROOT)
    parser.add_argument("--summary", default=DEFAULT_SUMMARY_PATH)
    parser.add_argument(
        "--scenario",
        action="append",
        default=None,
        help="restrict to a scenario id; repeatable",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    scenario_ids = tuple(args.scenario) if args.scenario else DEFAULT_SCENARIOS

    band = run_flake_band(
        scenario_ids=scenario_ids,
        k=args.k,
        root=args.root,
        model=args.model,
        model_label=args.model_label,
        temperature=args.temperature,
        record=args.record,
        max_utterances=args.max_utterances,
        max_turns=max(args.max_utterances + 2, DRIVER_MAX_TURNS),
    )
    print(band.describe())

    summary_path = _resolve(args.summary)
    if args.record:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(band.to_json(), encoding="utf-8")
        print(f"\nwrote {summary_path}")
        return 0

    if args.write_summary:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(band.to_json(), encoding="utf-8")
        print(f"\nrebuilt {summary_path} from the committed cassettes")
        return 0

    if args.check:
        if not summary_path.exists():
            print(f"no committed band at {summary_path}", file=sys.stderr)
            return 2
        committed = FlakeBand.load(summary_path)
        if committed.model_dump(mode="json") != band.model_dump(mode="json"):
            print(
                "\nthe replayed band does not match the committed one. Either the "
                "agent's behaviour changed or a fixture did; both need a human.",
                file=sys.stderr,
            )
            return 1
        print("\nband reproduces the committed fixture exactly")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
