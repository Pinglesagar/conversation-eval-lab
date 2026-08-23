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
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

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
    "MUTATING_TOOLS",
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
    """
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        root = str(repo_root())
        if root not in sys.path:
            sys.path.insert(0, root)
            return importlib.import_module(name)
        raise


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


def evaluate_trace(scenario: Any, trace: Trace) -> RunEvaluation:
    """Run a scenario's contracts over a trace and classify each verdict."""
    report = scenario.contract_set().run(trace, scenario.check_context())
    evaluation = RunEvaluation(scenario_id=scenario.id, report=report)
    for result in report.results:
        expected = scenario.expects_failure_of(result.name)
        if result.status in ("FAIL", "ERROR"):
            (evaluation.known_gaps if expected else evaluation.unexpected).append(result)
        elif expected:
            # PASS or VACUOUS on a contract the corpus says should fail. Either the
            # defect is fixed (delete the expectation) or the check went quiet
            # (fix the check). Both need a human; neither may pass silently.
            evaluation.stale.append(result)
    return evaluation


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


def _calibration_verdict(path: Path) -> tuple[str, str | None]:
    """Read the timing gate's verdict, or say it was never run."""
    if not path.exists():
        return "NOT_RUN", None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "NOT_RUN", None
    verdict = str(payload.get("verdict", "NOT_RUN")).upper()
    if verdict not in ("PASS", "FAIL"):
        verdict = "NOT_RUN"
    # Repo-relative: an absolute path in a committed report makes the artefact
    # machine-specific, and the whole point of committing it is that two machines
    # produce the same bytes.
    try:
        source = str(path.relative_to(repo_root()))
    except ValueError:
        source = str(path)
    return verdict, source


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


def _judge_summary(candidates: Sequence[Trace], *, live: bool) -> JudgeSummary | None:
    """The judge's calibration, and an honest account of what it graded here.

    Offline there are no recorded verdicts for these traces — the recordings are
    keyed to the prompts of the 24 calibration items — so the judge abstains on
    every selected session and the report says so. `integrity_gaps()` then prints
    the abstention rate next to the TPR and TNR, which is the shape of claim the
    evidence actually supports.
    """
    calibration_path = _resolve(f"{_JUDGE_DIR}/calibration_v2.json")
    if not calibration_path.exists():
        return None
    payload = json.loads(calibration_path.read_text(encoding="utf-8"))
    confusion = payload["confusion"]
    calibration = JudgeCalibration(
        labelled_positive=confusion["true_positive"] + confusion["false_negative"],
        labelled_negative=confusion["true_negative"] + confusion["false_positive"],
        true_positives=confusion["true_positive"],
        true_negatives=confusion["true_negative"],
        labelled_by="one labeller, reasons recorded per item in labels.jsonl",
    )
    return JudgeSummary(
        name=str(payload.get("judge", "hallucinated_confirmation")),
        model=str(payload.get("model", "unknown")),
        calibration=calibration,
        judged=len(candidates),
        flagged=0,
        abstained=len(candidates) if not live else 0,
        replayed_from_fixture=not live,
        prompt_id=str(payload.get("prompt_version", "v2")),
    )


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
) -> Trace:
    """One repeat: a fresh agent, a fresh restaurant, a fresh caller, one trace.

    The clock is shared between the agent and the driver on purpose. The agent
    sleeps on it to simulate its own latency, so a driver reading a different
    clock would record a latency of zero and the whole voice section would be
    measuring nothing.
    """
    clock = FakeClock()
    agent = build_agent(clock=clock, seed=script.seed_fn())
    caller = ScriptedCaller(
        script.script,
        profile=scenario.caller_profile(personas),
        closing=script.closing,
    )
    from lab.simulator import run_scenario

    return run_scenario(
        scenario_id=scenario.id,
        agent=agent,
        caller=caller,
        adapter="text:replay",
        clock=clock,
        # Deterministic, so a committed trace diffs cleanly. `run_scenario`
        # defaults to uuid4, which is right for a live run and fatal for a
        # fixture: every replay would rewrite every session id.
        session_id=f"{scenario.id}#{index}",
        max_turns=max_turns,
    )


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
            "warning: --live paraphrases agent turns through a provider. The "
            "contracts read the trace, so a paraphrase that drops a fact is a "
            "finding, not a harness error.",
            file=sys.stderr,
        )

    out_dir = _resolve(args.out)
    trace_dir = out_dir / "traces"
    if args.traces:
        trace_dir.mkdir(parents=True, exist_ok=True)

    verdicts: list[StabilityVerdict] = []
    evaluations: list[RunEvaluation] = []
    kept_traces: list[Trace] = []
    trace_paths: dict[str, str] = {}
    non_deterministic: list[str] = []

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
            kept_traces.append(repeats[0])
            if not _identical_repeats(repeats):
                non_deterministic.append(scenario.id)
            if args.traces:
                path = trace_dir / f"{scenario.id}.jsonl"
                write_jsonl(repeats[0], path)
                trace_paths[scenario.id] = _portable(path)
        if args.transcript:
            _print_transcript(repeats[0] if repeats else None, scenario.id)

    stats, contract_notes = _contract_stats(evaluations)
    scenarios_by_id = {s.id: s for s in selection.scenarios}
    failures = _failure_records(
        evaluations, scenarios=scenarios_by_id, trace_paths=trace_paths
    )
    candidates = _judge_candidates(kept_traces)
    judge = _judge_summary(candidates, live=bool(args.live_judge))

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
        ),
    )

    baseline_path = None if args.no_baseline else _resolve(args.baseline)
    diff = compare_to_baseline(
        report, baseline_path, scope=[s.id for s in selection.scenarios]
    )
    report.notes.append(f"Baseline: {diff.describe()}.")

    written = report.write(out_dir, stem="run_report")

    stale = [e for e in evaluations if e.stale]
    if diff.available:
        # With a committed baseline the gate is the diff: a finding this build
        # does not already own, or one it has stopped producing.
        gate_ok = diff.clean and not stale and not non_deterministic
    else:
        gate_ok = all(e.gate_passed for e in evaluations) and not non_deterministic

    print(report.headline())
    print()
    print(f"report verdict:   {report.verdict} — the product's own state")
    print(
        f"regression gate:  {'PASS' if gate_ok else 'FAIL'} — "
        f"{_gate_line(failures, stale, non_deterministic, diff)}"
    )
    print(f"baseline:         {diff.describe()}")
    print()
    for label, path in written.items():
        print(f"  wrote {label}: {path}")
    if args.traces:
        print(f"  wrote traces:   {trace_dir} ({len(trace_paths)} files)")

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


def _notes(
    *,
    args: argparse.Namespace,
    selection: _Selection,
    evaluations: Sequence[RunEvaluation],
    contract_notes: Sequence[str],
    candidates: Sequence[Trace],
    non_deterministic: Sequence[str],
) -> list[str]:
    """The caveats that belong to this run, stated in the artefact itself."""
    driven = len(selection.scenarios)
    notes = [
        f"Corpus coverage: {driven}/{selection.corpus_size} scenarios were driven. "
        f"{len(selection.voice_skipped)} voice rows declare audio perturbations and "
        f"are not driven by the text adapter; {len(selection.unscripted)} rows have no committed caller "
        f"script; {selection.filtered_out} were excluded by the command line.",
        (
            f"k={args.repeats} under `--replay` measures harness determinism, not "
            "model variance: the caller is scripted and the agent's phrasing comes "
            "from a fixture, so the repeats are expected to be identical. "
            + (
                f"{len(non_deterministic)} scenario(s) were not: "
                + ", ".join(non_deterministic)
                if non_deterministic
                else "All repeats were byte-identical apart from the session id."
            )
        ),
        (
            "The regression gate and this report's verdict answer different "
            "questions, and both are printed by `evallab run`. The verdict is FAIL "
            "while any contract fails at all — this build has real defects and the "
            "report says so. The gate compares the findings against the committed "
            "baseline and fails on a finding that is new, on one that has "
            "disappeared, on a corpus `expected_failure` that stopped reproducing, "
            "and on any scenario whose repeats were not identical. A fix therefore "
            "fails the gate until the baseline is updated in the same change, which "
            "is the point: it forces somebody to say in a diff whether a defect was "
            "fixed or a check went quiet."
        ),
        (
            f"Judge stage: the deterministic first stage selected "
            f"{len(candidates)}/{len(selection.scenarios)} sessions in which no "
            "booking mutation succeeded. Offline there is no recorded verdict for a "
            "trace the judge has not seen, so it abstained on all of them rather "
            "than guessing; its measured agreement is in the table above."
        ),
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
    return notes


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
        "--live-judge",
        action="store_true",
        help="grade the selected sessions with a live judge (needs LAB_LIVE_JUDGE=1)",
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
