"""The command line: `evallab`, the one entry point a reviewer actually runs.

WHAT THIS DEMONSTRATES
----------------------
That the pieces compose. Everything else in `lab/` is a library with a test
suite; this module is where a corpus, an agent, a trace, a contract set, a judge
and a calibration verdict are wired into a single command that produces a report
and an exit code. Three decisions in here are the ones worth reading:

**1. The gate is about *change*, not about *correctness*.**
The system under test has real defects, so "did every check pass" is a question
whose answer is already known and therefore useless as a build gate. Two
verdicts are computed and printed, and neither is derived from the other:

    report verdict    FAIL while any contract fails at all — the product's state
    regression gate   PASS while nothing has changed since the committed baseline

The gate fails on a finding that is new, on a finding that has *disappeared*, on
a corpus `expected_failure` that stopped reproducing, and on a scenario whose k
repeats were not identical. The middle two are the ones people leave out. A
suite that only shouts about new failures lets a fixed defect sit in the
baseline for ever as a standing excuse, and — worse — cannot tell a fix from a
check that quietly stopped applying, because both look like one fewer failure.
So a fix fails the gate until the baseline is updated in the same change, which
forces somebody to say in a diff which of the two it was.

**2. `--replay` runs k repeats of a deterministic fixture, and says what that
measures.** It measures the harness, not the model: repeats of a scripted caller
against a scripted backend either come back byte-identical or the harness has a
reproducibility bug. That is worth measuring — it is checked and reported — but
it is not a variance measurement, and calling it one would be the kind of claim
this repo exists to avoid. Model variance needs `--live`.

**3. Offline, the judge abstains rather than guesses.** The recorded judge
verdicts are keyed to the prompts of its 24-item calibration set; there is no
recording for a trace it has never seen, and inventing one would put fabricated
verdicts in a report. So `run` selects the sessions the judge cascade *would*
grade, records that it abstained on all of them, and prints the judge's measured
TPR/TNR next to the abstention. An abstention is visible; a guess is not.

LAYERING
--------
`lab` is meant to be extractable into its own package, and the case study
(`tablemate`, `scenarios`, `fixtures`) is not part of it. So nothing here is
imported at module scope: the corpus loader, the agent factory and the caller
fixtures are resolved lazily, by dotted path, through `--corpus-module` and
`--agent-factory`. `import lab` therefore never pulls in the case study, and the
seam that will become a plugin point after the split is already the seam the
default values sit behind.

OUT OF SCOPE
------------
`run` drives the **text** adapter only. The voice suite's rows are loaded,
validated and counted, and then reported as not driven with their denominator
visible, because a perturbation row run as text produces a verdict that says
nothing about audio. The audio path lives in `lab.voice` and is exercised on its
own terms, not through this command.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from pydantic import ValidationError

from lab import __version__
from lab.checks import CheckReport, CheckResult
from lab.clock import FakeClock
from lab.report import (
    ContractStat,
    FailureRecord,
    JudgeCalibration,
    JudgeSummary,
    RunReport,
    VoiceMetrics,
)
from lab.simulator import RunOutcome, ScriptedCaller, StabilityVerdict, run_pass_k
from lab.trace.io import read_jsonl, write_jsonl
from lab.trace.schema import EventKind, Trace

__all__ = [
    "DEFAULT_CORPUS_MODULE",
    "DEFAULT_AGENT_FACTORY",
    "DEFAULT_SCRIPTS",
    "DEFAULT_OUT_DIR",
    "LIVE_RUN_DIR",
    "LIVE_BASELINE",
    "MUTATING_TOOLS",
    "LIVE_AGENT_ADAPTERS",
    "build_of",
    "LiveRig",
    "CallerScript",
    "RunEvaluation",
    "load_caller_scripts",
    "build_parser",
    "main",
]

# --------------------------------------------------------------------------- #
# Where the case study lives. Overridable, because `lab` must not require it.
# --------------------------------------------------------------------------- #

#: Module exposing `load_corpus(root=None, strict=True)` and `CORPUS_ROOT`.
DEFAULT_CORPUS_MODULE: str = "scenarios.loader"

#: Dotted path to a callable with `build_agent`'s keyword signature.
DEFAULT_AGENT_FACTORY: str = "tablemate.runtime:build_agent"

#: Caller lines, per scenario. A fixture in the same sense as a cassette: the
#: caller is part of the instrument, so its utterances are recorded and reviewed
#: rather than generated afresh on every run.
DEFAULT_SCRIPTS: str = "fixtures/caller_scripts.yaml"

#: Generated output. `reports/` is gitignored; the committed reference run lives
#: in `fixtures/replay_run/` so that a reviewer can read a report without
#: running anything, and so CI can prove the run reproduces it byte for byte.
DEFAULT_OUT_DIR: str = "reports"
REFERENCE_RUN_DIR: str = "fixtures/replay_run"

#: Tools whose success means a reservation really changed. The first stage of the
#: judge cascade keeps only sessions where none of them succeeded.
MUTATING_TOOLS: tuple[str, ...] = ("create_booking", "modify_booking", "cancel_booking")

#: Default repeats. Three, so that determinism is measured rather than assumed;
#: see the module docstring for what k means under `--replay`.
DEFAULT_K: int = 3

_JUDGE_DIR = "lab/judges/hallucinated_confirmation"

#: Where a full-live run keeps its recordings. One directory, three kinds of
#: fixture, because a live run has three stochastic parts and each one has to be
#: replayable on its own terms:
#:
#:     agent_sessions.json   every (agent, message history, tool list) -> reply
#:     caller/<scenario>/    one cassette per repeat of the simulated caller
#:     judge_verdicts.jsonl  the judge's raw output per session it graded
#:
#: Committed, so `evallab run --live-*` with no key replays the exact run that was
#: paid for. That is the difference between a number in a README and evidence.
LIVE_RUN_DIR: str = "fixtures/live_full"
DEFAULT_AGENT_CASSETTE: str = f"{LIVE_RUN_DIR}/agent_sessions.json"
DEFAULT_CALLER_ROOT: str = f"{LIVE_RUN_DIR}/caller"
DEFAULT_JUDGE_RECORDING: str = f"{LIVE_RUN_DIR}/judge_verdicts.jsonl"
LIVE_BASELINE: str = f"{LIVE_RUN_DIR}/run_report.json"

#: Dotted path to the backend `--live-agent` hands the agent factory. The CLI knows
#: two things about it and no more: it is constructed with `cassette=`, `model=` and
#: `temperature=`, and the agent factory accepts it as `backend=`. What it does with
#: a model is the system under test's business, not the harness's.
DEFAULT_AGENT_BACKEND: str = "tablemate.runtime:LLMBackend"

#: Sampling temperature for a live *agent*. Not zero: an agent pinned to greedy
#: decoding is a different system from the one that answers the phone, and the
#: variance is part of what k repeats exist to measure.
DEFAULT_AGENT_TEMPERATURE: float = 0.7

#: Sampling temperature for a live *caller*, matching `lab.simulator.flake_band`
#: so the two measurements are comparable.
DEFAULT_CALLER_TEMPERATURE: float = 0.7

#: The caller's turn budget under `--live-caller`, and the driver's hard stop above
#: it. Twelve is not a guess: `lab.simulator.flake_band` measured the same corpus at
#: eight and found the budget alone decided a verdict on one row.
DEFAULT_CALLER_BUDGET: int = 12


def repo_root() -> Path:
    """The checkout root, for resolving the case study's default paths."""
    return Path(__file__).resolve().parents[1]


def _resolve(path: str | Path) -> Path:
    """Absolute path, interpreting a relative one against the checkout root."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else repo_root() / candidate


def _portable(path: Path) -> str:
    """A path fit to be written into a committed artefact.

    Repo-relative where possible, absolute otherwise. An absolute path in a
    committed report makes the artefact machine-specific, and the point of
    committing it is that two machines produce the same bytes.
    """
    try:
        return str(path.relative_to(repo_root()))
    except ValueError:
        return str(path)


def _import_module(name: str) -> Any:
    """Import `name`, looking in the checkout as well as on `sys.path`.

    `lab` is installed as a package; the case study beside it — `scenarios`,
    `error_analysis`, the fixtures — deliberately is not, because it is not part
    of the library and has no business in a wheel. That layering is right and it
    has one sharp edge: a console script does not put the working directory on
    `sys.path`, so `evallab validate` would fail to find the corpus while
    `python -m lab.cli validate` found it, purely because of how it was invoked.

    So the checkout root is added on the retry, and only on the retry: an
    installed module of the same name still wins, and nothing is put on the path
    when nothing needed it.

    And when the retry fails too, that is the end of the road, so it ends in a
    sentence rather than a stack. The way to get here is a *non-editable* install
    — `pip install .` instead of `pip install -e .` — after which `repo_root()`
    points inside site-packages, where the case study was never copied because
    the packaging deliberately excludes it. The traceback that used to come out
    named `scenarios` and nothing else, which is the least useful half of the
    explanation.
    """
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        root = str(repo_root())
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            raise SystemExit(
                f"cannot import '{name}', and it is not beside the library either "
                f"(looked in {root}).\n"
                "The case study — scenarios/, tablemate/'s fixtures, error_analysis/ — "
                "ships in the checkout, not in the wheel, so an installed copy has "
                "only the library.\n"
                "Install for development from a clone instead:\n"
                '    pip install -e ".[dev]"\n'
                "or point the command at your own corpus with --corpus-module."
            ) from exc


def _import_object(dotted: str) -> Any:
    """Import `pkg.mod:name` (or `pkg.mod.name`) and return the object."""
    module_name, _, attribute = dotted.partition(":")
    if not attribute:
        module_name, _, attribute = dotted.rpartition(".")
    return getattr(_import_module(module_name), attribute)


# --------------------------------------------------------------------------- #
# The caller fixture
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CallerScript:
    """One scenario's caller lines, plus whatever state the call presupposes.

    `seed` exists because "that reference is ours" and "there is nothing free at
    eight" have to be properties of the restaurant, not of a mock. A scenario
    that names `TM-1042` seeds it if the diary does not already hold it, and a
    scenario about a full sitting fills the sitting for real. The alternative —
    stubbing the tool's answer — makes the skip-logic under test untestable,
    because the agent's next move depends on the *shape* of a real refusal.
    """

    scenario_id: str
    script: tuple[str, ...]
    closing: str | None = None
    seed: tuple[Mapping[str, Any], ...] = ()
    note: str | None = None

    def seed_fn(self) -> Callable[[Any], None] | None:
        """A `build_agent(seed=...)` callable applying this scenario's fixture."""
        if not self.seed:
            return None

        steps = self.seed

        def apply(store: Any) -> None:
            for step in steps:
                for action, payload in step.items():
                    if action == "ensure_booking":
                        store.ensure_booking(**payload)
                    elif action == "book_out":
                        store.book_out(**payload)
                    else:  # pragma: no cover - guarded by load_caller_scripts
                        raise ValueError(f"unknown seed action {action!r}")

        return apply


_SEED_ACTIONS = frozenset({"ensure_booking", "book_out"})


def load_caller_scripts(path: str | Path) -> dict[str, CallerScript]:
    """Load the caller fixture, validating it the way the corpus is validated.

    A malformed caller fixture does not crash: it produces a short conversation
    that fails its contracts for a harness reason, and the finding gets filed
    against the agent. So the shape is checked here — a missing `script`, an
    empty line, an unknown seed action and a stray key are all errors naming the
    scenario, not silently tolerated data.
    """
    import yaml  # local: the fixture is data for the case study, not for `lab`

    source = _resolve(path)
    if not source.exists():
        raise FileNotFoundError(
            f"caller fixture not found: {source}. Pass --scripts, or run from a "
            "checkout that contains the case study."
        )
    loaded = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{source}: expected a mapping at the top level")

    raw = loaded.get("scripts")
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{source}: needs a non-empty `scripts:` mapping")

    out: dict[str, CallerScript] = {}
    for scenario_id, block in raw.items():
        where = f"{source.name}: {scenario_id}"
        if not isinstance(block, dict):
            raise ValueError(f"{where}: expected a mapping, got {type(block).__name__}")
        unknown = sorted(set(block) - {"script", "closing", "seed", "note"})
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {unknown}")
        lines = block.get("script")
        if not isinstance(lines, list) or not lines:
            raise ValueError(f"{where}: `script` must be a non-empty list of lines")
        if any(not isinstance(line, str) or not line.strip() for line in lines):
            raise ValueError(f"{where}: every script line must be a non-empty string")
        # `or []` would be wrong here: `seed: {ensure_booking: …}` — a mapping
        # where a list belongs — is falsy-adjacent enough to slip through as "no
        # seed", and the scenario would then run against an unseeded diary and
        # fail for a reason that has nothing to do with the agent. A test caught
        # this; the empty-mapping case is the one that got through.
        seed = block.get("seed")
        if seed is None:
            seed = []
        if not isinstance(seed, list):
            raise ValueError(f"{where}: `seed` must be a list of single-action mappings")
        for step in seed:
            if not isinstance(step, dict) or not step:
                raise ValueError(f"{where}: each seed step must be a non-empty mapping")
            bad = sorted(set(step) - _SEED_ACTIONS)
            if bad:
                raise ValueError(
                    f"{where}: unknown seed action(s) {bad}; known: {sorted(_SEED_ACTIONS)}"
                )
        out[str(scenario_id)] = CallerScript(
            scenario_id=str(scenario_id),
            script=tuple(str(line) for line in lines),
            closing=block.get("closing"),
            seed=tuple(seed),
            note=block.get("note"),
        )
    return out


# --------------------------------------------------------------------------- #
# The live rig: which parts of the loop are a model on this run
# --------------------------------------------------------------------------- #


@dataclass
class LiveRig:
    """Which of the three stochastic parts are live, and where each one records.

    THE THREE SEAMS ARE INDEPENDENT ON PURPOSE
    ------------------------------------------
    Agent, caller and judge can each be a model or a fixture, and the useful
    configurations are not "all off" and "all on". A live caller against a scripted
    agent attributes every disagreement between repeats to the caller's wording
    (that is `lab.simulator.flake_band`, and it is the only way to get a clean flake
    number). A live agent against a scripted caller isolates the agent. Both live is
    the closest thing to production and the least diagnostic, because a FLAKY
    verdict then has two possible causes — which is worth having, and worth saying
    out loud in the report rather than leaving a reader to assume otherwise.

    RECORDING IS A SEPARATE SWITCH FROM LIVENESS
    -------------------------------------------
    `record=False` with `agent=True` means "replay the committed agent cassette":
    the same code path, the same trace shape, no provider, no key, no spend. That is
    the mode CI runs in and the mode a reviewer runs in. `record=True` additionally
    requires the matching `LAB_LIVE_*` environment variable, so a flag in a script
    cannot start spending money on its own.
    """

    agent: bool = False
    caller: bool = False
    judge: bool = False
    record: bool = False
    agent_cassette: str = DEFAULT_AGENT_CASSETTE
    caller_root: str = DEFAULT_CALLER_ROOT
    judge_recording: str = DEFAULT_JUDGE_RECORDING
    agent_temperature: float = DEFAULT_AGENT_TEMPERATURE
    caller_temperature: float = DEFAULT_CALLER_TEMPERATURE
    caller_budget: int = DEFAULT_CALLER_BUDGET
    caller_model_label: str = "azure-openai/gpt-4.1"
    backend: Any = None

    @property
    def any_live(self) -> bool:
        return self.agent or self.caller or self.judge

    @property
    def repeats_should_be_identical(self) -> bool:
        """Whether k identical repeats is a *requirement* on this run.

        It is, and only is, when nothing in the loop can choose its own words. With
        a live caller the repeats are supposed to differ — that is the measurement —
        so reporting the difference as a reproducibility failure would turn the one
        interesting property of the run into a gate failure.

        A live *agent* replaying a cassette is a subtler case and is treated as
        non-identical too: the cassette is content-addressed on the message history,
        so replay is deterministic given identical input, but the run that produced
        it was not, and a fixture that happens to replay identically is not evidence
        that the system does.
        """
        return not self.any_live

    def describe(self) -> str:
        """One line naming what played each part. Goes in the report."""
        source = "live model" if self.record else "recorded model output"
        parts = [
            f"agent: {source}" if self.agent else "agent: scripted backend",
            f"caller: {source}" if self.caller else "caller: committed script",
            f"judge: {source}" if self.judge else "judge: abstains (no recording for these traces)",
        ]
        return "; ".join(parts)

    def build_backend(self) -> Any:
        """Construct the agent backend once, shared across every conversation."""
        if not self.agent:
            return None
        if self.backend is None:
            factory = _import_object(DEFAULT_AGENT_BACKEND)
            self.backend = factory(
                cassette=str(_resolve(self.agent_cassette)),
                temperature=self.agent_temperature,
            )
        return self.backend

    def make_caller(self, *, scenario: Any, personas: Mapping[str, Any], script: CallerScript, index: int) -> Any:
        """The caller for one repeat: a model with its own cassette, or the script."""
        profile = scenario.caller_profile(personas)
        if not self.caller:
            return ScriptedCaller(script.script, profile=profile, closing=script.closing)
        from lab.simulator.driver import LLMCaller

        return LLMCaller.for_scenario(
            profile,
            scenario_id=scenario.id,
            root=_resolve(self.caller_root),
            model_label=self.caller_model_label,
            temperature=self.caller_temperature,
            variant=index,
            max_utterances=self.caller_budget,
        )


def _live_refusals(rig: LiveRig) -> list[str]:
    """Every reason this rig may not record, as sentences. Empty means go ahead.

    Checked before anything runs and reported all at once. The alternative —
    failing on the first missing variable — makes setting up a live run a sequence
    of five separate error messages.
    """
    if not rig.record:
        return []
    wanted = {
        "agent": ("LAB_LIVE_AGENT", rig.agent),
        "caller": ("LAB_LIVE_CALLER", rig.caller),
        "judge": ("LAB_LIVE_JUDGE", rig.judge),
    }
    problems = [
        f"--record with a live {part} needs {var}=1 in the environment"
        for part, (var, wanted_live) in wanted.items()
        if wanted_live and not os.environ.get(var)
    ]
    if not any(live for _, live in wanted.values()):
        problems.append(
            "--record with nothing live records nothing; pass --live-agent, "
            "--live-caller or --live-judge"
        )
    return problems


# --------------------------------------------------------------------------- #
# One run, and the classification of its verdicts
# --------------------------------------------------------------------------- #


@dataclass
class RunEvaluation:
    """A check report, split by whether anybody predicted each failure.

    The split is the whole point of the class: `report.ok` answers "is the agent
    correct", which for a system with three documented defects is a question
    whose answer is already known and therefore useless as a gate. `gate_passed`
    answers "did anything change", which is the question CI can act on.
    """

    scenario_id: str
    report: CheckReport
    unexpected: list[CheckResult] = field(default_factory=list)
    known_gaps: list[CheckResult] = field(default_factory=list)
    stale: list[CheckResult] = field(default_factory=list)
    unreproduced: list[CheckResult] = field(default_factory=list)

    @property
    def gate_passed(self) -> bool:
        return not self.unexpected and not self.stale

    def gate_evidence(self) -> str | None:
        """The quote a failing gate is reported with, or None when it passed."""
        for result in self.unexpected:
            quote = result.evidence[0].render() if result.evidence else result.detail
            return f"UNEXPECTED {result.name}: {quote.strip()}"
        for result in self.stale:
            return (
                f"STALE EXPECTATION {result.name}: declared as a known gap and did "
                f"not reproduce ({result.status.lower()}: {result.detail})"
            )
        return None

    def failed_check_names(self) -> list[str]:
        return [r.name for r in (*self.unexpected, *self.stale)]


#: Adapters in which a *model* held the agent's decision seat. Read off the trace
#: rather than passed in, so a committed fixture carries its own provenance: a trace
#: is evidence about the build that produced it, and six months later the command
#: line that produced it is gone.
LIVE_AGENT_ADAPTERS: frozenset[str] = frozenset({"text:live", "text:live-agent"})


def build_of(trace: Trace) -> str:
    """Which build of the system under test produced this trace.

    Only the *agent* side counts. `text:live-caller` is a live caller against the
    deterministic agent — `lab.simulator.flake_band`'s configuration — and the
    expectations in the corpus are predictions about the agent, so that run is
    scored against the scripted build's expectations. Getting this backwards would
    make every flake-band row report the seeded defects as undeclared regressions.
    """
    return "live" if trace.adapter in LIVE_AGENT_ADAPTERS else "scripted"


def evaluate_trace(scenario: Any, trace: Trace) -> RunEvaluation:
    """Run a scenario's contracts over a trace and classify each verdict.

    STALENESS IS A PROPERTY OF k REPEATS, NOT OF ONE
    -----------------------------------------------
    A declared gap that comes back PASS is either a fixed defect or a check that
    went quiet, and both need a human — that is what `stale` is for. On the
    deterministic build, one repeat settles it: all k are identical, so a gap that
    did not reproduce here did not reproduce at all.

    On a live build it settles nothing. A defect planted in a prompt is a tendency,
    and the first full live run of this corpus has one that fires in **2 of 3**
    repeats of `edge-modification-after-booking`. Classifying the third repeat as a
    stale expectation would mean the corpus's own answer key fails the gate for
    being probabilistic — and, worse, the same run would report the expectation as
    both reproduced and stale, which is not a verdict anybody can act on.

    So a live repeat that does not reproduce its declared gap records
    `unreproduced` instead, and `_scenario_level_stale` decides staleness once per
    scenario, from all k. `unreproduced` is not silence: it is what the rate in the
    report's notes is computed from, and a gap that reproduced 0/k still fails the
    gate.
    """
    report = scenario.contract_set().run(trace, scenario.check_context())
    evaluation = RunEvaluation(scenario_id=scenario.id, report=report)
    build = build_of(trace)
    stochastic = build == "live"
    for result in report.results:
        expected = scenario.expects_failure_of(result.name, build)
        if result.status in ("FAIL", "ERROR"):
            (evaluation.known_gaps if expected else evaluation.unexpected).append(result)
        elif expected:
            evaluation.unreproduced.append(result)
            if not stochastic:
                evaluation.stale.append(result)
    return evaluation


def _scenario_level_stale(
    evaluations: Sequence[RunEvaluation],
) -> list[tuple[str, CheckResult]]:
    """Declared gaps that reproduced in *none* of a scenario's repeats.

    Returned as `(scenario_id, one representative result)` so the gate can print
    the evidence, and computed across repeats so that a gap which fired at least
    once is not also reported as stale. On the deterministic build this returns
    exactly what the per-repeat classification already found, because there all k
    repeats agree by construction.
    """
    reproduced: set[tuple[str, str]] = set()
    candidates: dict[tuple[str, str], CheckResult] = {}
    for evaluation in evaluations:
        for result in evaluation.known_gaps:
            reproduced.add((evaluation.scenario_id, result.name))
        for result in evaluation.unreproduced:
            candidates.setdefault((evaluation.scenario_id, result.name), result)
    return [
        (scenario_id, result)
        for (scenario_id, name), result in sorted(candidates.items())
        if (scenario_id, name) not in reproduced
    ]


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


#: One line per contract type, keyed by the class that produced the verdict, for
#: the report's "what it checks" column. A table of contract names with no
#: statement of what each one asserts is unreadable by anyone who did not write
#: the corpus — which is everyone the report is for.
CONTRACT_DESCRIPTIONS: dict[str, str] = {
    "ToolContract": "expected, forbidden and ordered tool calls, plus argument predicates",
    "PromiseContract": "every spoken commitment is backed by the call that would make it true",
    "NoReAskContract": "a fact the caller has already supplied is never asked for again",
    "FieldPropagationContract": "a supplied value survives the handoffs into the tool call",
    "NoProgressContract": "the same question is not put twice with nothing accomplished between",
    "PhraseContract": "phrases the agent must not say, and any it must",
}


@dataclass
class _ContractTally:
    runs: int = 0
    vacuous: int = 0
    failures: int = 0
    known: int = 0
    kind: str | None = None
    scenarios: list[str] = field(default_factory=list)

    def note_scenario(self, scenario_id: str) -> None:
        if scenario_id not in self.scenarios:
            self.scenarios.append(scenario_id)


def _contract_stats(evaluations: Sequence[RunEvaluation]) -> tuple[list[ContractStat], list[str]]:
    """Per-contract counts across every run, plus notes on the known gaps.

    `failures` counts every failure, declared or not: the contract did fail, and
    a table that hid the declared ones would make the product look better than it
    is. The declared share is stated in the notes, so the two questions — "is it
    broken" and "is it newly broken" — are both answerable from the report.
    """
    tallies: dict[str, _ContractTally] = {}
    for evaluation in evaluations:
        known = {r.name for r in evaluation.known_gaps}
        for result in evaluation.report.results:
            tally = tallies.setdefault(result.name, _ContractTally())
            tally.runs += 1
            tally.kind = tally.kind or result.contract
            if result.status == "VACUOUS":
                tally.vacuous += 1
            if result.status in ("FAIL", "ERROR"):
                tally.failures += 1
                tally.note_scenario(evaluation.scenario_id)
                if result.name in known:
                    tally.known += 1

    stats = [
        ContractStat(
            name=name,
            failures=tally.failures,
            runs=tally.runs,
            vacuous=tally.vacuous,
            description=CONTRACT_DESCRIPTIONS.get(tally.kind or ""),
            failing_scenarios=sorted(tally.scenarios),
        )
        for name, tally in tallies.items()
    ]
    notes = [
        f"contract `{name}`: {tally.known} of its {tally.failures} failures are "
        "declared known gaps in the corpus, not regressions"
        for name, tally in tallies.items()
        if tally.known
    ]
    return stats, notes


def _failure_records(
    evaluations: Sequence[RunEvaluation],
    *,
    scenarios: Mapping[str, Any],
    trace_paths: Mapping[str, str],
) -> list[FailureRecord]:
    """One record per (scenario, contract) failure, deduplicated across repeats.

    Deduplicated because k identical repeats of a deterministic fixture would
    otherwise triple every finding and make the failure list a measure of k.
    """
    seen: set[tuple[str, str]] = set()
    records: list[FailureRecord] = []
    for evaluation in evaluations:
        scenario = scenarios[evaluation.scenario_id]
        for kind, results in (
            ("UNEXPECTED", evaluation.unexpected),
            ("known gap", evaluation.known_gaps),
        ):
            for result in results:
                key = (evaluation.scenario_id, result.name)
                if key in seen:
                    continue
                seen.add(key)
                evidence = (
                    result.evidence[0].render().strip() if result.evidence else result.detail
                )
                note = kind
                if kind == "known gap" and scenario.expected_failure is not None:
                    note = f"declared known gap ({scenario.expected_failure.since})"
                records.append(
                    FailureRecord(
                        scenario_id=evaluation.scenario_id,
                        contract=result.name,
                        evidence=evidence or result.detail,
                        session_id=evaluation.report.session_id,
                        trace_path=trace_paths.get(evaluation.scenario_id),
                        from_agent=_from_agent(result),
                        to_agent=_to_agent(result),
                        note=f"{note} — {result.detail}",
                    )
                )
    return records


def _handoff_evidence(result: CheckResult) -> tuple[str | None, str | None]:
    """The handoff a failure sits on, when its evidence names one.

    Read from the quoted `agent_handoff` event rather than guessed: a failure
    attributed to the wrong transition is worse than one attributed to none,
    because it puts a number on the transition heatmap that nobody can trace.
    """
    for item in result.evidence:
        if item.kind == EventKind.AGENT_HANDOFF and "->" in item.quote:
            source, _, target = item.quote.partition("->")
            return source.strip() or None, target.strip() or None
    return None, None


def _from_agent(result: CheckResult) -> str | None:
    return _handoff_evidence(result)[0]


def _to_agent(result: CheckResult) -> str | None:
    return _handoff_evidence(result)[1]


# --------------------------------------------------------------------------- #
# Voice metrics and the calibration verdict behind them
# --------------------------------------------------------------------------- #


def _is_number(value: object) -> bool:
    """A real numeric measurement. `bool` is an `int` in Python and is not one."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _verdict_from_calibration_samples(payload: object) -> str:
    """Score a timing-calibration artefact from its own recorded samples.

    Returns PASS, FAIL, or NOT_RUN. NOT_RUN covers every way the artefact fails
    to be evidence: wrong shape, no delays, a delay with fewer than two samples
    (no standard deviation exists), a non-positive nominal delay. Silence is the
    honest answer there — the alternative is a verdict derived from nothing.

    The artefact's own tolerance is honoured only where it is at least as strict
    as `CalibrationTolerance()`'s defaults. Otherwise the numbers are real but
    the bar they cleared is not the harness's, and a report that printed PASS
    would be quoting a pass against a bar the artefact chose for itself. Since
    this report prints the verdict without the tolerance beside it, a looser bar
    is unreadable from the report and is refused rather than reported.
    """
    from lab.voice.calibration import CalibrationTolerance

    if not isinstance(payload, dict):
        return "NOT_RUN"
    rows = payload.get("delays")
    if not isinstance(rows, list) or not rows:
        return "NOT_RUN"
    try:
        tolerance = CalibrationTolerance.model_validate(payload.get("tolerance") or {})
    except ValidationError:
        return "NOT_RUN"
    stated = CalibrationTolerance()
    if (
        tolerance.max_rel_error > stated.max_rel_error
        or tolerance.max_stdev_s > stated.max_stdev_s
    ):
        return "NOT_RUN"

    verdict = "PASS"
    for row in rows:
        if not isinstance(row, dict):
            return "NOT_RUN"
        samples = row.get("samples_s")
        nominal = row.get("nominal_delay_s")
        if not isinstance(samples, list) or len(samples) < 2:
            return "NOT_RUN"
        if not all(_is_number(value) for value in samples):
            return "NOT_RUN"
        if not _is_number(nominal) or nominal <= 0:
            return "NOT_RUN"
        mean = statistics.fmean(samples)
        spread = statistics.stdev(samples)
        rel_error = (mean - nominal) / nominal
        if abs(rel_error) > tolerance.max_rel_error or spread > tolerance.max_stdev_s:
            verdict = "FAIL"
    return verdict


def _calibration_verdict(path: Path) -> tuple[str, str | None]:
    """Re-derive the timing gate's verdict from the artefact, or say it was never run.

    The verdict is recomputed from the recorded samples rather than read out of
    the artefact's `verdict` field, for the same reason `_audit_judges_for_ci`
    recomputes the judge's calibration instead of trusting
    `calibration_v2.json`: a stale or hand-edited artefact must not be able to
    put a PASS badge on a report. Reading a one-word claim would make every
    latency figure below it rest on a string somebody could have typed.

    The artefact carries every raw sample, so this needs no measurement — the
    tolerance and the per-delay samples in the file decide the verdict, and a
    file that cannot support one is `NOT_RUN`. (Re-running the gate outright
    costs a couple of milliseconds and would be defensible too, but then `run`
    would report a verdict about the machine it happened to run on rather than
    about the committed evidence a reader can inspect.)
    """
    if not path.exists():
        return "NOT_RUN", None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "NOT_RUN", None
    return _verdict_from_calibration_samples(payload), _portable(path)


def _voice_metrics(traces: Sequence[Trace], *, calibration_json: Path) -> VoiceMetrics:
    """Latency figures, inseparable from the verdict that says they are measured."""
    from lab.voice.metrics import response_latency_report

    report = response_latency_report(traces)
    first_byte = report.time_to_first_byte
    verdict, source = _calibration_verdict(calibration_json)

    def ms(value: float | None) -> float | None:
        return None if value is None else round(value * 1000.0, 3)

    return VoiceMetrics(
        samples=first_byte.n,
        mean_ms=ms(first_byte.mean_s),
        p50_ms=ms(first_byte.quantile(0.50).value_s),
        p95_ms=ms(first_byte.quantile(0.95).value_s),
        calibration_verdict=verdict,
        calibration_report=source,
        wer=None,
        wer_reference_words=None,
        estimated_timestamps_used=_uses_estimated_timestamps(traces),
    )


def _uses_estimated_timestamps(traces: Iterable[Trace]) -> bool:
    """True if any event the latency figures read from was interpolated.

    Checked rather than asserted. The driver stamps `ts_estimated` on tool and
    handoff events whose instant it interpolated inside the measured window; if
    one of those ever became a latency boundary the figures would be describing
    the harness's arithmetic.
    """
    boundaries = (EventKind.CALLER_UTTERANCE, EventKind.AGENT_AUDIO_FIRST_BYTE)
    return any(
        event.get("ts_estimated", False)
        for trace in traces
        for event in trace.events
        if event.kind in boundaries
    )


# --------------------------------------------------------------------------- #
# The judge stage
# --------------------------------------------------------------------------- #


def _judge_candidates(traces: Sequence[Trace]) -> list[Trace]:
    """Sessions the cascade would send to the judge: no mutation succeeded.

    The deterministic stage first, on purpose. A judge asked about every session
    would spend most of its budget on calls where a booking demonstrably exists,
    and its false positives there are the expensive kind — they contradict the
    tool ledger, which is the one thing in the trace that cannot be argued with.
    """
    selected: list[Trace] = []
    for trace in traces:
        mutated = any(
            event.kind == EventKind.TOOL_RESULT
            and event.get("name") in MUTATING_TOOLS
            and event.get("ok", True)
            for event in trace.events
        )
        if not mutated:
            selected.append(trace)
    return selected


@dataclass
class JudgeStage:
    """What the judge actually did on this run, and every verdict it gave.

    Both halves are needed and neither substitutes for the other. `summary` is what
    goes in the report next to the calibration; `flagged` is the list a human reads,
    because a flag rate with no transcripts attached is a number nobody can act on.
    """

    summary: JudgeSummary | None
    verdicts: list[Any] = field(default_factory=list)

    @property
    def flagged(self) -> list[Any]:
        return [v for v in self.verdicts if not v.passed]


def _judge_stage(candidates: Sequence[Trace], *, rig: LiveRig) -> JudgeStage:
    """Grade the selected sessions, or record honestly that nothing graded them.

    WHAT THIS USED TO DO, AND WHY THAT WAS WORSE THAN NOT HAVING IT
    --------------------------------------------------------------
    `--live-judge` used to change the *labels* on this section and nothing else. It
    set `abstained=0`, `replayed_from_fixture=False` and left `flagged=0` — so a
    report produced with the flag claimed the judge had graded every selected
    session and found nothing, without a single call having been made. That is a
    fabricated provenance claim of exactly the kind the rest of this repository is
    an argument against, and it was three lines of plausible-looking code.

    Now the flag does the work: it builds the v2 judge, grades every candidate,
    writes the raw answers to a recording, and puts the real flag count in the
    report. Offline the same recording is replayed through the same prompt and the
    same parser, so the number in the committed report is reproducible with no key.
    With no recording and no live judge, the judge abstains on everything and the
    report says so — an abstention is visible, a guess is not.
    """
    calibration_path = _resolve(f"{_JUDGE_DIR}/calibration_v2.json")
    if not calibration_path.exists():
        return JudgeStage(summary=None)
    payload = json.loads(calibration_path.read_text(encoding="utf-8"))
    confusion = payload["confusion"]
    calibration = JudgeCalibration(
        labelled_positive=confusion["true_positive"] + confusion["false_negative"],
        labelled_negative=confusion["true_negative"] + confusion["false_positive"],
        true_positives=confusion["true_positive"],
        true_negatives=confusion["true_negative"],
        labelled_by="one labeller, reasons recorded per item in labels.jsonl",
    )
    name = str(payload.get("judge", "hallucinated_confirmation"))
    prompt_id = str(payload.get("prompt_version", "v2"))
    model = str(payload.get("model", "unknown"))

    verdicts: list[Any] = []
    recording_path = _resolve(rig.judge_recording)
    if rig.judge and candidates:
        verdicts = _grade(candidates, rig=rig, recording_path=recording_path)

    summary = JudgeSummary(
        name=name,
        model=model,
        calibration=calibration,
        judged=len(candidates),
        flagged=sum(1 for v in verdicts if not v.passed),
        abstained=len(candidates) - len(verdicts),
        replayed_from_fixture=not rig.record,
        prompt_id=prompt_id,
    )
    return JudgeStage(summary=summary, verdicts=verdicts)


def _merge_recording(fresh: Any, path: Path) -> Path:
    """Write `fresh` into `path`, keeping answers already recorded there.

    Recording a corpus one suite at a time is the sane way to spend an hour of
    provider calls, and a plain `save()` per suite would leave only the last one on
    disk — a recording that silently covers a third of the run, and a replay that
    abstains on the rest while looking like it worked. Fresh answers win on a
    collision, because re-recording an item is how a stale one is replaced.
    """
    from lab.judges.judge import Recording

    merged = dict()
    if path.exists():
        for call in Recording.load(path).calls:
            merged[call.item_id] = call
    for call in fresh.calls:
        merged[call.item_id] = call
    return Recording(calls=[merged[key] for key in sorted(merged)]).save(path)


def _grade(
    candidates: Sequence[Trace], *, rig: LiveRig, recording_path: Path
) -> list[Any]:
    """Judge every candidate, live-and-recording or from the committed recording.

    An item id has to be stable across the two, or the recording is unreplayable.
    The session id is that id: it is `<scenario>#<repeat>`, assigned by `_drive` and
    identical on a re-run, which is the same reason `_drive` sets it rather than
    letting the driver generate a uuid.
    """
    from lab.judges import hallucinated_confirmation as judge_pkg
    from lab.judges.judge import (
        MissingRecordingError,
        RecordingCompletion,
        ReplayJudge,
        StaleRecordingError,
    )

    items = [(trace.session_id, trace) for trace in candidates]

    if rig.record:
        live = judge_pkg.judge("v2", replay=False)
        recorder = RecordingCompletion(
            live.completion, judge=live.name, prompt_version=live.version
        )
        judging = live.with_completion(recorder)
        verdicts = [judging.judge(trace, item_id=item) for item, trace in items]
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        _merge_recording(recorder.recording, recording_path)
        return verdicts

    if not recording_path.exists():
        print(
            f"--live-judge without --record needs a recording at {recording_path}; "
            "the judge abstained instead of guessing",
            file=sys.stderr,
        )
        return []

    replay = ReplayJudge(
        recording=recording_path,
        name=judge_pkg.JUDGE_NAME,
        prompt=judge_pkg.prompt("v2"),
        version="v2",
        model=judge_pkg.recorded_model("v2"),
        include_tools=False,
        strict=True,
    )
    verdicts = []
    for item, trace in items:
        try:
            verdicts.append(replay.judge(trace, item_id=item))
        except (MissingRecordingError, StaleRecordingError) as exc:
            # Loud, and not fatal. A trace the recording does not cover is an
            # abstention with a reason; inventing a verdict for it would be the one
            # thing this stage exists to refuse.
            print(f"judge abstained on {item}: {exc}", file=sys.stderr)
    return verdicts


# --------------------------------------------------------------------------- #
# The baseline: what this build was already known to get wrong
# --------------------------------------------------------------------------- #

Finding = tuple[str, str]  # (scenario_id, contract)

#: Keys `RunReport.to_dict()` materialises from properties so the JSON stands on
#: its own, listed here per level because they have to come back off before the
#: model (which forbids extras) will accept the file. Reloading also re-derives
#: them and compares — see `load_run_report`.
_DERIVED_KEYS: dict[str, frozenset[str]] = {
    "": frozenset({"verdict", "headline", "stability_summary", "integrity_gaps"}),
    "stability": frozenset({"pass_rate", "flake_rate", "passed"}),
    "contracts": frozenset({"applicable", "failure_rate"}),
    "judges": frozenset({"flag_rate", "tpr", "tnr"}),
    "voice": frozenset({"trustworthy"}),
}


def load_run_report(path: str | Path) -> RunReport:
    """Load a written report back into a `RunReport`, and check it round-trips.

    Two things are being demonstrated by doing this the hard way. First, that the
    JSON really is the whole report: the markdown is regenerated from it, so if
    anything only existed in the prose it would go missing here. Second, that the
    derived figures in the file agree with the ones recomputed from the counts
    beside them — the file says `verdict` *and* the numbers the verdict comes
    from, and a disagreement between the two means the artefact was edited by
    hand. That is worth an exception rather than a quietly different render.
    """
    source = _resolve(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    stored_verdict = payload.get("verdict")

    def strip(block: Any, level: str) -> Any:
        if isinstance(block, dict):
            return {k: v for k, v in block.items() if k not in _DERIVED_KEYS.get(level, ())}
        if isinstance(block, list):
            return [strip(item, level) for item in block]
        return block

    cleaned = {
        key: strip(value, key)
        for key, value in payload.items()
        if key not in _DERIVED_KEYS[""]
    }
    report = RunReport.model_validate(cleaned)
    if stored_verdict is not None and report.verdict != stored_verdict:
        raise ValueError(
            f"{source}: stored verdict {stored_verdict!r} does not match the "
            f"verdict recomputed from its own counts ({report.verdict!r})"
        )
    return report


@dataclass
class BaselineDiff:
    """New findings and vanished ones, against the committed reference run.

    Both directions gate the build, and the second one is the one people leave
    out. A suite that only shouts about new failures lets a *fixed* defect sit in
    the baseline for ever as a standing excuse, and — worse — cannot tell a fix
    from a check that quietly stopped applying. Both look like "one fewer
    failure". So a finding that disappears fails the gate until somebody says in
    a diff which of the two it was.
    """

    path: Path | None
    added: list[Finding] = field(default_factory=list)
    removed: list[Finding] = field(default_factory=list)
    baseline_size: int = 0

    @property
    def available(self) -> bool:
        return self.path is not None

    @property
    def clean(self) -> bool:
        return not self.added and not self.removed

    def describe(self) -> str:
        if not self.available:
            return "no baseline (gating on undeclared failures only)"
        return (
            f"{len(self.added)} new finding(s), {len(self.removed)} vanished, "
            f"against {self.baseline_size} in {self.path}"
        )


def _findings_of(report: RunReport) -> set[Finding]:
    return {(f.scenario_id, f.contract) for f in report.failures}


def compare_to_baseline(
    report: RunReport, path: Path | None, *, scope: Iterable[str] | None = None
) -> BaselineDiff:
    """Diff this run's findings against a committed report's.

    `scope` restricts the comparison to the scenarios this run actually drove.
    Without it, `--suite happy` would report every finding in the other three
    suites as "vanished", which is the fastest way to teach somebody to ignore a
    gate.
    """
    if path is None or not path.exists():
        return BaselineDiff(path=None)
    baseline = load_run_report(path)
    current = _findings_of(report)
    previous = _findings_of(baseline)
    if scope is not None:
        driven = set(scope)
        previous = {f for f in previous if f[0] in driven}
    try:
        shown = path.relative_to(repo_root())
    except ValueError:
        shown = path
    return BaselineDiff(
        path=Path(shown),
        added=sorted(current - previous),
        removed=sorted(previous - current),
        baseline_size=len(previous),
    )


# --------------------------------------------------------------------------- #
# `evallab run`
# --------------------------------------------------------------------------- #


@dataclass
class _Selection:
    """Which rows of the corpus this run drove, and which it could not."""

    scenarios: list[Any]
    corpus_size: int
    voice_skipped: list[str]
    unscripted: list[str]
    filtered_out: int


def _select_scenarios(corpus: Any, scripts: Mapping[str, CallerScript], args: Any) -> _Selection:
    """Apply the filters, then drop what this adapter genuinely cannot drive."""
    rows = list(corpus.scenarios)
    total = len(rows)

    wanted_suites = _split_list(args.suite)
    wanted_tags = _split_list(args.tag)
    wanted_ids = _split_list(args.scenario)
    if wanted_suites:
        rows = [s for s in rows if s.suite in wanted_suites]
    if wanted_tags:
        rows = [s for s in rows if set(wanted_tags) & set(s.all_tags())]
    if wanted_ids:
        rows = [s for s in rows if s.id in wanted_ids]
        missing = sorted(set(wanted_ids) - {s.id for s in rows})
        if missing:
            raise SystemExit(f"no such scenario(s): {', '.join(missing)}")
    filtered_out = total - len(rows)

    voice_skipped = [s.id for s in rows if s.voice is not None and s.voice.perturbations]
    rows = [s for s in rows if s.id not in set(voice_skipped)]
    unscripted = [s.id for s in rows if s.id not in scripts]
    rows = [s for s in rows if s.id in scripts]

    return _Selection(
        scenarios=rows,
        corpus_size=total,
        voice_skipped=voice_skipped,
        unscripted=unscripted,
        filtered_out=filtered_out,
    )


def _split_list(values: Sequence[str] | None) -> list[str]:
    """`--suite happy --suite edge` and `--suite happy,edge` mean the same thing."""
    out: list[str] = []
    for value in values or ():
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


def _drive(
    *,
    scenario: Any,
    script: CallerScript,
    build_agent: Callable[..., Any],
    personas: Mapping[str, Any],
    index: int,
    max_turns: int,
    rig: LiveRig | None = None,
    callers: list[Any] | None = None,
) -> Trace:
    """One repeat: a fresh agent, a fresh restaurant, a fresh caller, one trace.

    The clock is shared between the agent and the driver on purpose. The agent
    sleeps on it to simulate its own latency, so a driver reading a different
    clock would record a latency of zero and the whole voice section would be
    measuring nothing. That stays true on a live run, and it is the reason no
    latency figure from one may be read as a measurement of the model: the clock
    is fake, so what the trace records is `LatencyModel`'s seconds and not Azure's.

    `callers` is an out-parameter, and it exists because a live caller carries state
    the trace cannot: why it stopped, and which gated facts it leaked. That state is
    a finding about the *instrument*, and dropping it would mean filing the
    instrument's failures against the agent.
    """
    rig = rig or LiveRig()
    clock = FakeClock()
    backend = rig.build_backend()
    kwargs: dict[str, Any] = {"clock": clock, "seed": script.seed_fn()}
    if backend is not None:
        kwargs["backend"] = backend
    agent = build_agent(**kwargs)
    caller = rig.make_caller(
        scenario=scenario, personas=personas, script=script, index=index
    )
    if callers is not None:
        callers.append(caller)
    from lab.simulator import run_scenario

    adapter = "text:replay"
    if rig.agent and rig.caller:
        adapter = "text:live"
    elif rig.agent:
        adapter = "text:live-agent"
    elif rig.caller:
        adapter = "text:live-caller"

    try:
        return run_scenario(
            scenario_id=scenario.id,
            agent=agent,
            caller=caller,
            adapter=adapter,
            clock=clock,
            # Deterministic, so a committed trace diffs cleanly. `run_scenario`
            # defaults to uuid4, which is right for a live run and fatal for a
            # fixture: every replay would rewrite every session id.
            session_id=f"{scenario.id}#{index}",
            max_turns=max_turns,
        )
    finally:
        # Save even when the run raised. A conversation that was paid for and then
        # thrown away is money spent to learn nothing, and the turns that did happen
        # are still evidence.
        if rig.record and rig.caller and hasattr(caller, "save"):
            caller.save()


def _identical_repeats(traces: Sequence[Trace]) -> bool:
    """Do k repeats differ only in their session id?

    The one thing k measures under `--replay`. Session id is excluded because it
    is assigned per repeat by design; everything else — every utterance, every
    tool argument, every timestamp — must match, or the harness is not
    reproducible and no number it prints can be compared with yesterday's.
    """
    if len(traces) < 2:
        return True

    def fingerprint(trace: Trace) -> str:
        return json.dumps(
            [
                [
                    round(e.ts, 9),
                    e.kind,
                    e.actor,
                    e.engine,
                    {k: v for k, v in e.payload.items() if k != "session_id"},
                ]
                for e in trace.events
            ],
            sort_keys=True,
            default=str,
        )

    first = fingerprint(traces[0])
    return all(fingerprint(t) == first for t in traces[1:])


def cmd_run(args: argparse.Namespace) -> int:
    """Scenarios -> traces -> contracts (+ judge stage) -> report -> exit code."""
    loader = _import_module(args.corpus_module)
    build_agent = _import_object(args.agent_factory)

    corpus_root = _resolve(args.corpus) if args.corpus else None
    corpus = loader.load_corpus(corpus_root) if corpus_root else loader.load_corpus()
    scripts = load_caller_scripts(args.scripts)
    selection = _select_scenarios(corpus, scripts, args)

    if not selection.scenarios:
        print("no scenarios selected — nothing to run", file=sys.stderr)
        return 2

    if args.live:
        if not os.environ.get("LAB_LIVE_AGENT"):
            print(
                "--live needs LAB_LIVE_AGENT=1 and a provider key; refusing to "
                "pretend a replay run was a live one",
                file=sys.stderr,
            )
            return 2
        print(
            "warning: --live paraphrases agent turns through a provider. It does "
            "not put a model in the decision seat — that is --live-agent. The "
            "contracts read the trace, so a paraphrase that drops a fact is a "
            "finding, not a harness error.",
            file=sys.stderr,
        )

    rig = LiveRig(
        agent=bool(args.live_agent),
        caller=bool(args.live_caller),
        judge=bool(args.live_judge),
        record=bool(args.record),
        agent_cassette=args.agent_cassette,
        caller_root=args.caller_root,
        judge_recording=args.judge_recording,
        agent_temperature=args.agent_temperature,
        caller_temperature=args.caller_temperature,
        caller_budget=args.caller_budget,
    )
    # Every reason recording is refused, in one message. Recording is the only mode
    # that spends money, so it is the only mode that needs the environment's
    # agreement as well as the flag's — see `_live_refusals`.
    refusals = _live_refusals(rig)
    if refusals:
        for line in refusals:
            print(line, file=sys.stderr)
        return 2

    out_dir = _resolve(args.out)
    trace_dir = out_dir / "traces"
    if args.traces:
        trace_dir.mkdir(parents=True, exist_ok=True)

    verdicts: list[StabilityVerdict] = []
    evaluations: list[RunEvaluation] = []
    kept_traces: list[Trace] = []
    trace_paths: dict[str, str] = {}
    non_deterministic: list[str] = []

    callers: list[Any] = []
    for scenario in selection.scenarios:
        script = scripts[scenario.id]
        repeats: list[Trace] = []

        def run(index: int, scenario: Any = scenario, script: CallerScript = script) -> Trace:
            trace = _drive(
                scenario=scenario,
                script=script,
                build_agent=build_agent,
                personas=corpus.personas,
                index=index,
                max_turns=args.max_turns,
                rig=rig,
                callers=callers,
            )
            repeats.append(trace)
            return trace

        def evaluate(trace: Trace, scenario: Any = scenario) -> RunOutcome:
            evaluation = evaluate_trace(scenario, trace)
            evaluations.append(evaluation)
            return RunOutcome(
                index=0,  # `run_pass_k` restamps this
                passed=evaluation.gate_passed,
                session_id=trace.session_id,
                evidence=evaluation.gate_evidence(),
                failed_checks=evaluation.failed_check_names(),
            )

        verdict = run_pass_k(
            scenario_id=scenario.id,
            k=args.repeats,
            run=run,
            evaluate=evaluate,
            label=scenario.suite,
            catch_errors=not args.raise_errors,
        )
        verdicts.append(verdict)

        if repeats:
            # Offline, repeat 0 stands for all k because they are identical and a
            # committed fixture should not carry three copies of one conversation.
            # On a live run they are three different conversations, and keeping one
            # would throw away two thirds of the evidence the run was paid for —
            # including, on this corpus, the repeats where a seeded defect fired.
            kept_traces.extend(repeats if rig.any_live else repeats[:1])
            if not _identical_repeats(repeats) and rig.repeats_should_be_identical:
                non_deterministic.append(scenario.id)
            if args.traces:
                for index, trace in enumerate(repeats if rig.any_live else repeats[:1]):
                    stem = f"{scenario.id}-{index}" if rig.any_live else scenario.id
                    path = trace_dir / f"{stem}.jsonl"
                    write_jsonl(trace, path)
                    trace_paths.setdefault(scenario.id, _portable(path))
        if args.transcript:
            _print_transcript(repeats[0] if repeats else None, scenario.id)

        # Flush the agent cassette after every scenario, not once at the end.
        # Recording a corpus takes tens of minutes and costs real money; a crash on
        # row 40 that discarded the first 39 rows' exchanges would be paying twice
        # for the same conversations. The caller cassettes are already saved per
        # conversation for the same reason.
        if rig.record and rig.agent and rig.backend is not None:
            rig.backend.save()

    stats, contract_notes = _contract_stats(evaluations)
    scenarios_by_id = {s.id: s for s in selection.scenarios}
    failures = _failure_records(
        evaluations, scenarios=scenarios_by_id, trace_paths=trace_paths
    )
    candidates = _judge_candidates(kept_traces)
    stage = _judge_stage(candidates, rig=rig)
    judge = stage.summary

    report = RunReport(
        title="TableMate evaluation run",
        subject=args.subject,
        run_label=args.label,
        stability=verdicts,
        contracts=stats,
        judges=[judge] if judge else [],
        voice=_voice_metrics(
            kept_traces, calibration_json=_resolve("fixtures/calibration_report.json")
        ),
        failures=failures,
        notes=_notes(
            args=args,
            selection=selection,
            evaluations=evaluations,
            contract_notes=contract_notes,
            candidates=candidates,
            non_deterministic=non_deterministic,
            rig=rig,
            stage=stage,
            callers=callers,
            sessions=len(kept_traces),
        ),
    )

    baseline_path = None if args.no_baseline else _resolve(args.baseline)
    diff = compare_to_baseline(
        report, baseline_path, scope=[s.id for s in selection.scenarios]
    )
    report.notes.append(f"Baseline: {diff.describe()}.")

    written = report.write(out_dir, stem="run_report")

    stale_expectations = _scenario_level_stale(evaluations)
    # `stale` keeps the per-repeat shape the gate line and the printout expect. On a
    # live run it is rebuilt from the scenario-level verdict so that one unlucky
    # repeat cannot report a probabilistic defect as a stale expectation.
    stale = (
        [e for e in evaluations if e.stale]
        if not rig.any_live
        else [
            RunEvaluation(
                scenario_id=scenario_id,
                report=next(
                    e.report for e in evaluations if e.scenario_id == scenario_id
                ),
                stale=[result],
            )
            for scenario_id, result in stale_expectations
        ]
    )
    if diff.available:
        # With a committed baseline the gate is the diff: a finding this build
        # does not already own, or one it has stopped producing.
        gate_ok = diff.clean and not stale_expectations and not non_deterministic
    else:
        gate_ok = (
            all(not e.unexpected for e in evaluations)
            and not stale_expectations
            and not non_deterministic
        )

    if rig.record and rig.agent and rig.backend is not None:
        saved = rig.backend.save()
        if saved is not None:
            print(f"  wrote cassette: {_portable(Path(saved))}")

    print(report.headline())
    print()
    if rig.any_live:
        print(f"live rig:         {rig.describe()}")
        for line in _live_diagnostics(rig, callers):
            print(f"                  {line}")
        print()
    print(f"report verdict:   {report.verdict} — the product's own state")
    print(
        f"regression gate:  {'PASS' if gate_ok else 'FAIL'} — "
        f"{_gate_line(failures, stale, non_deterministic, diff)}"
    )
    print(f"baseline:         {diff.describe()}")
    # The headline's denominator is the number of scenarios *driven*, which is
    # smaller than the corpus. That difference belongs on the terminal and not
    # only in the report's notes: a reviewer who reads "44/47" and never learns
    # the corpus holds 55 rows has been shown a pass rate over a subset chosen
    # by the harness. Printed unconditionally, so the line is there to be
    # noticed when the numbers agree as well as when they do not.
    print(f"corpus coverage:  {_coverage_line(selection)}")
    print()
    for label, path in written.items():
        print(f"  wrote {label}: {path}")
    if args.traces:
        print(f"  wrote traces:   {trace_dir} ({len(trace_paths)} files)")

    if args.heatmap:
        _write_heatmap(report, out_dir, path=_resolve(args.heatmap))

    if not gate_ok:
        print()
        print("gate failures:", file=sys.stderr)
        for scenario_id, contract in diff.added:
            print(f"  NEW      {scenario_id}: {contract}", file=sys.stderr)
        for scenario_id, contract in diff.removed:
            print(
                f"  VANISHED {scenario_id}: {contract} — a fix, or a check that "
                "stopped applying; say which and update the baseline",
                file=sys.stderr,
            )
        for evaluation in stale:
            print(f"  STALE    {evaluation.scenario_id}: {evaluation.gate_evidence()}", file=sys.stderr)
        if not diff.available:
            for evaluation in evaluations:
                if evaluation.gate_passed or evaluation.stale:
                    continue
                print(
                    f"  {evaluation.scenario_id}: {evaluation.gate_evidence()}",
                    file=sys.stderr,
                )
        for scenario_id in non_deterministic:
            print(f"  {scenario_id}: repeats were not identical under --replay", file=sys.stderr)

    if args.ci:
        judges_ok = _audit_judges_for_ci()
        if not judges_ok:
            return 1
        if args.strict and not report.passed:
            return 1
        return 0 if gate_ok else 1
    return 0


def _coverage_line(selection: _Selection) -> str:
    """How much of the corpus this run actually drove, and where the rest went.

    Every row the harness dropped is accounted for by name of reason. A run that
    quietly evaluates a subset and reports a rate over it is the most flattering
    mistake an eval harness can make, so the subset is stated next to the rate.
    """
    driven = len(selection.scenarios)
    reasons = [
        f"{len(selection.voice_skipped)} voice row(s) need the audio adapter",
        f"{len(selection.unscripted)} unscripted",
        f"{selection.filtered_out} filtered out by the command line",
    ]
    return f"{driven}/{selection.corpus_size} scenarios driven — " + ", ".join(reasons)


def _gate_line(
    failures: Sequence[FailureRecord],
    stale: Sequence[RunEvaluation],
    non_deterministic: Sequence[str],
    diff: BaselineDiff,
) -> str:
    """The gate in one line, counted per finding rather than per repeat.

    Per finding, because k repeats of a deterministic fixture would otherwise
    multiply every number by k and the line would be reporting the value of k.
    """
    declared = sum(1 for f in failures if (f.note or "").startswith("declared known gap"))
    parts = [
        f"{len(diff.added)} new",
        f"{len(diff.removed)} vanished",
        f"{len(stale)} stale expectation(s)",
        f"{len(failures)} finding(s) total "
        f"({declared} declared by the corpus, {len(failures) - declared} not)",
    ]
    if non_deterministic:
        parts.append(f"{len(non_deterministic)} scenario(s) not reproducible")
    return ", ".join(parts)


def _live_diagnostics(rig: LiveRig, callers: Sequence[Any]) -> list[str]:
    """What the models did, counted — the instrument's own state on a live run.

    Every line here is a divergence between this run and a deterministic one, and a
    reader is entitled to the size of each rather than to a reassurance. The caller
    lines matter most: a stop reason of `turn_budget` or `repeated_line` is a guard
    in the *harness* firing, and a run whose failures are mostly those has measured
    its own settings.
    """
    lines: list[str] = []
    if rig.agent and rig.backend is not None:
        diagnostics = rig.backend.diagnostics()
        lines.append(
            f"agent: {diagnostics['model_calls']} model call(s), "
            f"{diagnostics['recorded']} recorded, {diagnostics['replayed']} replayed, "
            f"{diagnostics['rate_limit_retries']} rate-limit retr(ies)"
        )
        blocked = diagnostics.get("blocked_calls") or []
        if blocked:
            lines.append(
                f"agent: {len(blocked)} off-allow-list tool call(s) refused: "
                + ", ".join(sorted(set(blocked)))
            )
        if diagnostics.get("truncated_turns") or diagnostics.get("silent_turns"):
            lines.append(
                f"agent: {diagnostics['truncated_turns']} truncated turn(s), "
                f"{diagnostics['silent_turns']} silent turn(s)"
            )
    if rig.caller and callers:
        stops = Counter(getattr(c, "stop_reason", None) or "agent-or-driver ended it" for c in callers)
        leaks = sum(len(getattr(c, "leaks", ())) for c in callers)
        lines.append(
            f"caller: {len(callers)} conversation(s), stop reasons "
            + ", ".join(f"{reason}={count}" for reason, count in stops.most_common())
        )
        lines.append(
            f"caller: {leaks} gated fact(s) said before being asked for (a lower "
            "bound — verbatim match only)"
        )
    return lines


#: The two-sided 95% z. Written once, because every interval quoted anywhere in
#: this repository is a 95% one and a second literal would eventually disagree.
_Z_95 = 1.959963984540054


def _wilson_lower_bound(successes: int, trials: int, z: float = _Z_95) -> float:
    """Wilson score lower bound on a proportion. Closed form, no dependency.

    Used here for exactly one thing: turning "k passes out of k" into the honest
    statement of what k unanimous samples do and do not buy. The naive (Wald)
    interval is degenerate at the only point that matters — at p-hat = 1 it has
    zero width and asserts the true rate is exactly 1.0 — which is the claim the
    caveat exists to refuse, so Wilson it is.

    Reproduces the figures already quoted in prose elsewhere in the tree:
    3/3 -> 0.439, 5/5 -> 0.566, 8/8 -> 0.676, 16/16 -> 0.806.
    """
    if trials <= 0:
        raise ValueError(f"a Wilson bound needs at least one trial, got {trials}")
    if not 0 <= successes <= trials:
        raise ValueError(
            f"{successes} of {trials} is not a proportion: successes must be "
            "between 0 and trials"
        )
    p_hat = successes / trials
    z2 = z * z
    denominator = 1.0 + z2 / trials
    centre = (p_hat + z2 / (2 * trials)) / denominator
    half = (z / denominator) * math.sqrt(
        p_hat * (1.0 - p_hat) / trials + z2 / (4 * trials * trials)
    )
    return max(0.0, centre - half)


def _unanimity_caveat(k: int) -> str:
    """What k identical passes bound the true pass rate to, derived from k.

    This sentence used to interpolate the run's real `k` and then hardcode the
    arithmetic for k=3 four lines later, so at any other k the artefact stated
    two different things about one run. Both committed live reports run at k=3,
    which is exactly why it survived: a latent error that is only wrong on a
    setting nobody happened to use is the kind this repository exists to argue
    against, so the figure is now computed and pinned by a test at k != 3.
    """
    lower = _wilson_lower_bound(k, k)
    passes = "pass" if k == 1 else "passes"
    return (
        f"{k} {passes} out of {k} put the 95% Wilson lower bound on the pass "
        f"rate at {lower:.2f}, so a row that came back STABLE_PASS is consistent "
        f"with a real-world failure rate as high as {1.0 - lower:.2f}"
    )


def _notes(
    *,
    args: argparse.Namespace,
    selection: _Selection,
    evaluations: Sequence[RunEvaluation],
    contract_notes: Sequence[str],
    candidates: Sequence[Trace],
    non_deterministic: Sequence[str],
    rig: LiveRig | None = None,
    stage: Any = None,
    callers: Sequence[Any] = (),
    sessions: int = 0,
) -> list[str]:
    """The caveats that belong to this run, stated in the artefact itself."""
    rig = rig or LiveRig()
    driven = len(selection.scenarios)
    if rig.any_live:
        k_note = (
            f"k={args.repeats} with a live rig ({rig.describe()}) measures model "
            "variance, not harness determinism: the repeats are *supposed* to "
            "differ, and a scenario whose k repeats disagree is FLAKY rather than "
            "irreproducible. Read a STABLE_PASS here as k samples agreeing and "
            "nothing more: " + _unanimity_caveat(args.repeats) + ". "
            f"k={args.repeats} buys the difference between unanimous and not; it "
            "does not buy a reliability estimate."
        )
        if rig.agent and rig.caller:
            k_note += (
                " Both agent and caller are models on this run, so a FLAKY verdict "
                "has two possible causes and this run cannot separate them; "
                "`lab.simulator.flake_band` holds the agent still and can."
            )
    else:
        k_note = (
            f"k={args.repeats} under `--replay` measures harness determinism, not "
            "model variance: the caller is scripted and the agent's phrasing comes "
            "from a fixture, so the repeats are expected to be identical. "
            + (
                f"{len(non_deterministic)} scenario(s) were not: "
                + ", ".join(non_deterministic)
                if non_deterministic
                else "All repeats were byte-identical apart from the session id."
            )
        )
    notes = [
        f"Corpus coverage: {driven}/{selection.corpus_size} scenarios were driven. "
        f"{len(selection.voice_skipped)} voice rows declare audio perturbations and "
        f"are not driven by the text adapter; {len(selection.unscripted)} rows have no committed caller "
        f"script; {selection.filtered_out} were excluded by the command line.",
        k_note,
        (
            "The regression gate and this report's verdict answer different "
            "questions, and both are printed by `evallab run`. The verdict is FAIL "
            "while any contract fails at all — this build has real defects and the "
            "report says so. The gate compares the findings against the committed "
            "baseline and fails on a finding that is new, on one that has "
            "disappeared, "
            + (
                "and on a corpus `expected_failure` that reproduced in none of the "
                "k repeats. It does *not* require the k repeats to be identical: on "
                "a live rig they are supposed to differ, and the baseline for this "
                "run must be another live run — diffing a live run against the "
                "scripted baseline would report the difference between two builds "
                "as a regression."
                if rig.any_live
                else "on a corpus `expected_failure` that stopped reproducing, and "
                "on any scenario whose repeats were not identical."
            )
            + " A fix therefore fails the gate until the baseline is updated in "
            "the same change, which is the point: it forces somebody to say in a "
            "diff whether a defect was fixed or a check went quiet."
        ),
        _judge_note(candidates, sessions=sessions, rig=rig, stage=stage),
        (
            "Latency figures come from a simulated latency model on a fake clock. "
            "They demonstrate the measurement path end to end (and the calibration "
            "gate proves that path recovers a known delay); they are not a "
            "statement about any production system's speed."
        ),
    ]
    notes.extend(contract_notes)
    if selection.unscripted:
        notes.append(
            "Rows with no committed caller script: " + ", ".join(sorted(selection.unscripted))
        )
    if selection.voice_skipped:
        notes.append(
            "Voice rows not driven by the text adapter (they declare audio "
            "perturbations, and a text verdict on one would say nothing about audio): "
            + ", ".join(sorted(selection.voice_skipped))
        )
    if rig.any_live:
        rates = _declared_gap_rates(evaluations)
        if rates:
            notes.append(
                "Declared known gaps, and how often each one actually reproduced "
                "across the k repeats: "
                + "; ".join(rates)
                + ". A seeded defect is a certainty in the deterministic build and a "
                "tendency in this one, so the rate is the finding — a gap that "
                "reproduced 0/k is reported as a stale expectation and fails the "
                "gate, and one that reproduced at least once is not, however "
                "unevenly."
            )
        notes.append(
            "Live rig: " + rig.describe() + ". Recordings for every part are "
            f"committed under `{LIVE_RUN_DIR}/`, so this run replays offline with no "
            "key: the agent cassette is content-addressed on the message history, "
            "each caller repeat has its own cassette, and the judge's raw answers "
            "are stored per session. Re-recording draws different samples and is "
            "expected to produce a different report — that is the measurement, not "
            "a defect in it."
        )
        for line in _live_diagnostics(rig, callers):
            notes.append("Live rig diagnostics — " + line)
        notes.append(
            "No latency figure from this run is a measurement of any model. The "
            "driver runs on a `FakeClock` so that fixtures do not depend on how "
            "busy a provider was, which means the seconds in the trace are "
            "`LatencyModel`'s and the provider's real response times were never "
            "recorded."
        )
    return notes


def _declared_gap_rates(evaluations: Sequence[RunEvaluation]) -> list[str]:
    """`scenario/contract n/k` for every gap the corpus declares, as text.

    Counted over repeats rather than reported as a boolean, because on a live build
    "did the seeded defect fire" has no yes-or-no answer and printing one would be
    the same mistake as reporting a single run as a pass.
    """
    fired: dict[tuple[str, str], int] = {}
    total: dict[tuple[str, str], int] = {}
    for evaluation in evaluations:
        for result in evaluation.known_gaps:
            key = (evaluation.scenario_id, result.name)
            fired[key] = fired.get(key, 0) + 1
            total[key] = total.get(key, 0) + 1
        for result in evaluation.unreproduced:
            key = (evaluation.scenario_id, result.name)
            total[key] = total.get(key, 0) + 1
    return [
        f"{scenario}/{contract} {fired.get((scenario, contract), 0)}/{count}"
        for (scenario, contract), count in sorted(total.items())
    ]


def _judge_note(
    candidates: Sequence[Trace],
    *,
    sessions: int,
    rig: LiveRig,
    stage: Any,
) -> str:
    """The judge paragraph, written from what the stage actually did.

    `sessions` is the number of traces the first stage was offered, counted rather
    than derived from k: on a live run every repeat is kept and on a replay run only
    the first is, so a denominator computed from `k` would be wrong in one of the two
    modes and there would be no way to tell which from the report.
    """
    head = (
        f"Judge stage: the deterministic first stage selected {len(candidates)}/"
        f"{sessions} session(s) in which no booking mutation succeeded."
    )
    verdicts = list(getattr(stage, "verdicts", ()) or ())
    if not verdicts:
        return head + (
            " No verdict was recorded for any of them, so the judge abstained rather "
            "than guessing; its measured agreement is in the table above and applies "
            "to the 24-item calibration set, not to these sessions."
        )
    flagged = [v for v in verdicts if not v.passed]
    parse_errors = sum(1 for v in verdicts if getattr(v, "parse_error", False))
    source = "a live provider" if rig.record else "the committed recording"
    return head + (
        f" It graded {len(verdicts)} of them through {source} and flagged "
        f"{len(flagged)}. {parse_errors} answer(s) were unparseable and failed closed. "
        "Read the flags against the calibration in the table above: it is measured "
        "on 24 hand-labelled items whose rates are 8/8 and 16/16, which is "
        "consistent with true rates as low as 0.68 and 0.81 (95% Wilson lower "
        "bounds), and those items are not these ones."
    )


def _audit_judges_for_ci() -> bool:
    """Refuse to pass CI with an uncalibrated or below-threshold judge.

    The judge whose verdicts the report quotes is registered here with the
    calibration recomputed from the committed labels, and then put through the
    registry's gate in strict mode. Doing it in the CLI rather than trusting the
    committed `calibration_v2.json` is deliberate: the number in the report and
    the number the gate checks come from the same computation, so a stale
    artefact cannot let a judge through.
    """
    from lab.judges import hallucinated_confirmation as judge_pkg
    from lab.judges.registry import (
        CalibrationGateError,
        JudgeRegistry,
    )

    registry = JudgeRegistry()
    ok = True
    try:
        judge = judge_pkg.judge_v2(replay=True)
        registry.register(judge, calibration=judge_pkg.calibrate_version("v2"))
        registry.require_calibrated(judge, ci=True)
    except CalibrationGateError as exc:
        print(f"CI refuses the judge: {exc}", file=sys.stderr)
        ok = False
    for status in registry.audit():
        if not status.ok:
            print(
                f"CI refuses judge {status.name!r} ({status.version}): "
                + "; ".join(status.failures),
                file=sys.stderr,
            )
            ok = False
    return ok


def _print_transcript(trace: Trace | None, scenario_id: str) -> None:
    """The conversation, for authoring caller scripts and reading a failure."""
    print(f"\n--- {scenario_id} " + "-" * max(0, 60 - len(scenario_id)))
    if trace is None:
        print("  (no trace: the run raised before producing one)")
        return
    for event in trace.events:
        if event.kind == EventKind.CALLER_UTTERANCE:
            print(f"  caller  {event.get('text')}")
        elif event.kind == EventKind.AGENT_UTTERANCE:
            print(f"  {event.get('agent', 'agent'):<18} {event.get('text')}")
        elif event.kind == EventKind.TOOL_CALL:
            print(f"      -> {event.get('name')}({json.dumps(event.get('args'), sort_keys=True)})")
        elif event.kind == EventKind.AGENT_HANDOFF:
            print(f"      ~~ handoff {event.get('from')} -> {event.get('to')}")


# --------------------------------------------------------------------------- #
# `evallab validate`
# --------------------------------------------------------------------------- #


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate the corpus. Exit 1 on any error, 1 on a warning under --strict."""
    loader = _import_module(args.corpus_module)
    root = _resolve(args.corpus) if args.corpus else loader.CORPUS_ROOT
    validation = loader.validate_corpus(root)

    if args.json:
        print(_corpus_json(validation))
    else:
        print(validation.summary_line())
        for issue in validation.errors:
            print(f"  ERROR   {issue.render()}")
        for issue in validation.warnings:
            print(f"  warning {issue.render()}")
        if args.coverage:
            print()
            print(
                loader.Corpus(
                    root=validation.root,
                    scenarios=validation.scenarios,
                    personas=validation.personas,
                    issues=validation.issues,
                ).coverage_report()
            )

    if validation.errors:
        return 1
    if args.strict and validation.warnings:
        return 1
    return 0


def _corpus_json(validation: Any) -> str:
    return json.dumps(
        {
            "root": validation.root,
            "files_seen": validation.files_seen,
            "scenarios": len(validation.scenarios),
            "errors": [i.model_dump() for i in validation.errors],
            "warnings": [i.model_dump() for i in validation.warnings],
            "ok": validation.ok,
        },
        indent=2,
        sort_keys=True,
    )


# --------------------------------------------------------------------------- #
# `evallab report`
# --------------------------------------------------------------------------- #


def cmd_report(args: argparse.Namespace) -> int:
    """Re-render a report from its own JSON, and optionally draw the heatmap.

    Worth having as a command rather than a flag on `run`: if the markdown can be
    rebuilt from the JSON alone, the JSON is complete, and a dashboard reading it
    is not seeing a lossy summary of what a human read.
    """
    source = _resolve(args.source)
    json_path = source / "run_report.json" if source.is_dir() else source
    if not json_path.exists():
        print(f"no report JSON at {json_path}", file=sys.stderr)
        return 2

    report = load_run_report(json_path)
    if args.out:
        written = report.write(_resolve(args.out), stem="run_report")
        for label, path in written.items():
            print(f"  wrote {label}: {path}")
    else:
        print(report.to_markdown())

    if args.heatmap:
        return _write_heatmap(report, json_path.parent, path=_resolve(args.heatmap))
    return 0


def _write_heatmap(report: RunReport, trace_root: Path, *, path: Path) -> int:
    """Draw the handoff failure matrix, if the traces and matplotlib are here."""
    from lab.report.heatmap import matrix_from_failures, render_heatmap

    trace_dir = trace_root / "traces"
    if not trace_dir.is_dir():
        print(f"no traces beside the report ({trace_dir}); skipping heatmap", file=sys.stderr)
        return 0
    traces = [read_jsonl(p) for p in sorted(trace_dir.glob("*.jsonl"))]
    matrix = matrix_from_failures(traces, report.failures)
    print()
    print(matrix.to_markdown())
    try:
        written = render_heatmap(matrix, path, title="Handoff failures / attempts")
    except ModuleNotFoundError:
        print("matplotlib not installed; skipping the PNG (install .[charts])", file=sys.stderr)
        return 0
    print(f"  wrote heatmap: {written}")
    return 0


# --------------------------------------------------------------------------- #
# `evallab calibrate`
# --------------------------------------------------------------------------- #


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Run the calibration gates. Both by default; either alone on request."""
    both = not (args.timing or args.judges)
    status = 0

    if args.timing or both:
        from lab.voice import calibration as timing

        print("== timing calibration ==")
        status |= timing.main(["--out", str(_resolve(args.out))])

    if args.judges or both:
        from lab.judges import hallucinated_confirmation as judge_pkg

        print()
        print("== judge calibration ==")
        status |= judge_pkg.main([])
        print()
        print("== judge gate ==")
        if not _audit_judges_for_ci():
            status |= 1
        else:
            print("  the reported judge clears the gate")

    # The exit code is the gate. `--ci` changes nothing about it and is accepted
    # only so the same command line works in both places.
    return 1 if status else 0


# --------------------------------------------------------------------------- #
# `evallab replay`
# --------------------------------------------------------------------------- #


def cmd_replay(args: argparse.Namespace) -> int:
    """Re-check a committed trace, with no agent and no scenario runner involved.

    This is the auditability claim, executable: a verdict in a report either
    recomputes from the trace on disk or it was never evidence. It is also how a
    disagreement gets settled — the trace is the artefact, not the summary.
    """
    loader = _import_module(args.corpus_module)
    corpus = loader.load_corpus()

    paths = [Path(p) for p in args.traces]
    expanded: list[Path] = []
    for path in paths:
        resolved = _resolve(path)
        expanded.extend(sorted(resolved.glob("*.jsonl")) if resolved.is_dir() else [resolved])
    if not expanded:
        print("no trace files given", file=sys.stderr)
        return 2

    failures = 0
    for path in expanded:
        trace = read_jsonl(path)
        scenario_id = args.scenario or trace.scenario_id
        try:
            scenario = corpus.by_id(scenario_id)
        except KeyError:
            print(f"{path.name}: no scenario {scenario_id!r} in the corpus", file=sys.stderr)
            failures += 1
            continue
        evaluation = evaluate_trace(scenario, trace)
        report = evaluation.report
        print(f"{path.name}  ({scenario_id}, {len(trace.events)} events)")
        print(report.render(failures_only=args.failures_only))
        if evaluation.known_gaps:
            print(
                "  declared known gap(s): "
                + ", ".join(r.name for r in evaluation.known_gaps)
            )
        if not evaluation.gate_passed:
            failures += 1
        print()

    print(f"replayed {len(expanded)} trace(s); {failures} with unexpected findings")
    return 1 if failures and args.ci else 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def _add_layering_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--corpus-module",
        default=DEFAULT_CORPUS_MODULE,
        help=f"module exposing load_corpus/validate_corpus (default: {DEFAULT_CORPUS_MODULE})",
    )
    parser.add_argument("--corpus", default=None, help="corpus root directory")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evallab",
        description=(
            "Evaluation harness for conversational agents. Every subcommand runs "
            "offline with no API keys; live paths are opt-in behind an env var."
        ),
    )
    parser.add_argument("--version", action="version", version=f"evallab {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ---------------------------------------------------------------- run
    run = subparsers.add_parser(
        "run",
        help="drive scenarios, check the traces, write a report",
        description=(
            "Scenarios -> traces -> contracts (+ the judge cascade's selection "
            "stage) -> report. Exit code follows the regression gate under --ci."
        ),
    )
    _add_layering_args(run)
    run.add_argument(
        "--agent-factory",
        default=DEFAULT_AGENT_FACTORY,
        help=f"dotted path to the agent factory (default: {DEFAULT_AGENT_FACTORY})",
    )
    run.add_argument("--scripts", default=DEFAULT_SCRIPTS, help="caller-script fixture")
    run.add_argument("--suite", action="append", help="restrict to suite(s); repeatable")
    run.add_argument("--tag", action="append", help="restrict to tag(s); repeatable")
    run.add_argument("--scenario", action="append", help="restrict to scenario id(s)")
    run.add_argument("-k", "--repeats", type=int, default=DEFAULT_K, help="repeats per scenario")
    run.add_argument("--out", default=DEFAULT_OUT_DIR, help="output directory")
    run.add_argument("--label", default=None, help="run label (a sha, a date, a model)")
    run.add_argument(
        "--subject", default="TableMate 0.1.0 (replay fixtures)", help="what was evaluated"
    )
    run.add_argument("--max-turns", type=int, default=14, help="hard stop per conversation")
    run.add_argument(
        "--replay",
        action="store_true",
        default=True,
        help="use recorded fixtures — the default, and the only mode needing no keys",
    )
    run.add_argument(
        "--live",
        action="store_true",
        help="paraphrase agent turns through a provider (needs LAB_LIVE_AGENT=1)",
    )
    run.add_argument(
        "--live-agent",
        action="store_true",
        help=(
            "put a model in the agent's decision seat (it picks the tools, the "
            "handoffs and the words). Replays the committed cassette unless --record"
        ),
    )
    run.add_argument(
        "--live-caller",
        action="store_true",
        help=(
            "let a model play the caller, per persona and goal. Replays the "
            "committed cassettes unless --record"
        ),
    )
    run.add_argument(
        "--live-judge",
        action="store_true",
        help=(
            "grade the selected sessions with the calibrated v2 judge. Replays the "
            "committed verdicts unless --record"
        ),
    )
    run.add_argument(
        "--record",
        action="store_true",
        help=(
            "call the provider and write the fixtures. Needs the matching "
            "LAB_LIVE_* variable for every live part; this flag alone is not the "
            "switch, because recording spends money"
        ),
    )
    run.add_argument("--agent-cassette", default=DEFAULT_AGENT_CASSETTE)
    run.add_argument("--caller-root", default=DEFAULT_CALLER_ROOT)
    run.add_argument("--judge-recording", default=DEFAULT_JUDGE_RECORDING)
    run.add_argument(
        "--agent-temperature", type=float, default=DEFAULT_AGENT_TEMPERATURE
    )
    run.add_argument(
        "--caller-temperature", type=float, default=DEFAULT_CALLER_TEMPERATURE
    )
    run.add_argument(
        "--caller-budget",
        type=int,
        default=DEFAULT_CALLER_BUDGET,
        help=(
            "caller turn budget under --live-caller; part of the cassette key, and "
            "a setting that has been measured deciding a verdict on its own"
        ),
    )
    run.add_argument(
        "--baseline",
        default=f"{REFERENCE_RUN_DIR}/run_report.json",
        help="committed report to diff findings against (the regression gate)",
    )
    run.add_argument(
        "--no-baseline",
        action="store_true",
        help="ignore the baseline and gate on undeclared failures alone",
    )
    run.add_argument("--no-traces", dest="traces", action="store_false", help="do not write traces")
    run.add_argument(
        "--heatmap",
        default=None,
        help="also draw the handoff failure heatmap here (needs the [charts] extra)",
    )
    run.add_argument("--transcript", action="store_true", help="print each conversation")
    run.add_argument(
        "--ci",
        action="store_true",
        help="exit non-zero on a gate failure, and refuse an uncalibrated judge",
    )
    run.add_argument(
        "--strict",
        action="store_true",
        help="with --ci, also fail on the declared known gaps (the product verdict)",
    )
    run.add_argument(
        "--raise-errors",
        action="store_true",
        help="let a harness exception propagate instead of recording it as a failed run",
    )
    run.set_defaults(func=cmd_run, traces=True)

    # ---------------------------------------------------------------- validate
    validate = subparsers.add_parser(
        "validate", help="validate the scenario corpus against its schema"
    )
    _add_layering_args(validate)
    validate.add_argument("--strict", action="store_true", help="treat warnings as errors")
    validate.add_argument("--json", action="store_true", help="machine-readable output")
    validate.add_argument("--coverage", action="store_true", help="print the coverage report")
    validate.set_defaults(func=cmd_validate)

    # ---------------------------------------------------------------- report
    report = subparsers.add_parser(
        "report", help="re-render a report from its committed JSON"
    )
    report.add_argument(
        "source",
        nargs="?",
        default=REFERENCE_RUN_DIR,
        help=f"run directory or run_report.json (default: {REFERENCE_RUN_DIR})",
    )
    report.add_argument("--out", default=None, help="write instead of printing")
    report.add_argument("--heatmap", default=None, help="also draw the handoff heatmap here")
    report.set_defaults(func=cmd_report)

    # ---------------------------------------------------------------- calibrate
    calibrate = subparsers.add_parser(
        "calibrate", help="run the timing and judge calibration gates"
    )
    calibrate.add_argument("--timing", action="store_true", help="timing gate only")
    calibrate.add_argument("--judges", action="store_true", help="judge calibration only")
    calibrate.add_argument("--out", default="fixtures", help="where timing artefacts go")
    calibrate.add_argument("--ci", action="store_true", help="exit non-zero on failure")
    calibrate.set_defaults(func=cmd_calibrate)

    # ---------------------------------------------------------------- replay
    replay = subparsers.add_parser(
        "replay", help="re-check committed traces without running an agent"
    )
    _add_layering_args(replay)
    replay.add_argument(
        "traces",
        nargs="*",
        default=[f"{REFERENCE_RUN_DIR}/traces"],
        help="trace files or a directory of them",
    )
    replay.add_argument("--scenario", default=None, help="force a scenario id")
    replay.add_argument(
        "--failures-only", action="store_true", help="print only the checks that failed"
    )
    replay.add_argument("--ci", action="store_true", help="exit non-zero on any finding")
    replay.set_defaults(func=cmd_replay)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `evallab` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
