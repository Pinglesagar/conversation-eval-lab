"""Stage 4: measure the selector, because a selector is a grader.

`lab.selection.select` decides that a scenario *need not run*. That is a graded
verdict on every row of the corpus, and a wrong verdict is not a slow test — it
is a regression that ships with a green run. This repository already refuses to
trust a judge whose agreement with reality has never been measured
(`lab.judges.registry.require_calibrated`). It must refuse to trust a selector
for exactly the same reason, and commercial test-impact tools shipping as
trusted black boxes is the gap this module exists to close.

    python -m lab.selection.calibrate                 # both studies, summary
    python -m lab.selection.calibrate --write         # ... and record the artefact
    python -m lab.selection.calibrate --json          # machine-readable

Not an ``evallab`` subcommand: the selection layer is wired into the CLI
separately, once every stage has landed.

THE NUMBER THAT MATTERS
-----------------------
**Recall of failures.** Run the full suite at a base tree and at a changed tree.
The failures that appear are what the full run catches. Ask the selector which
scenarios it would have run. A *miss* is a scenario that newly failed and that
the selector would have skipped — a regression made invisible. Recall is
caught / (caught + missed), always printed with that denominator.

Recall alone is not enough and this module never prints it alone. A selector
that returns the whole corpus every time has a perfect recall and no value
whatsoever, so every report carries the **selection ratio** beside it: how many
scenarios were selected, out of how many exist. The two numbers together are the
trade; either one alone flatters the tool.

TWO STUDIES, AND WHY THE FIRST ONE CANNOT CARRY THE RESULT
----------------------------------------------------------
*The history study* is the one you actually want: real commits, real changes,
real failures. It is run first and it is reported honestly, including when — as
here — it comes back empty. Every commit in this repository's history is a
curated, green commit. The suite's verdicts are identical either side of every
backtestable commit pair, so the number of historical failures available to be
missed is **zero**, and a recall over an empty denominator is not 1.0. It is
*unknown*, and `require_calibrated_selector` treats unknown exactly as
`require_calibrated` treats a judge with no calibration attached: it refuses.

That is a real finding about the corpus of commits, not a bug, and
`HistoryStudy.limitation()` states what would make the measurement sound rather
than papering over it with a flattering substitute.

*The mutation study* supplies the failures history does not contain, by breaking
the code on purpose and watching what goes red. Its mutants are **derived from
the AST, never hand-written** — the same argument the trace map makes about
dependency metadata, for the same reason. A hand-picked mutant set measures the
taste of whoever picked it; an enumerated one can be counted, sampled with a
stated seed, and re-derived by anyone. The sample is stratified by operator so
that no single kind of edit dominates, and every stratum is reported with its
own denominator.

The mutation number is evidence about the selector's *logic*. It is not evidence
about real developer changes, and it is labelled that way everywhere it appears.

WHAT NEITHER STUDY CAN SEE
--------------------------
The measurable window is the rows the deterministic text harness can drive: 47
of the corpus's 73. The audio tier and the unscripted rows cannot fail here, so
nothing measured here says anything about them — which is precisely why the
selector's always-run floor keeps them, and why that floor is checked separately
by `lab.selection.select.calibrate`.

That other function is a different measurement and the two are not
interchangeable. `select.calibrate` asks *is the join internally sound* — does
the selector keep every scenario its own map implicates. This module asks *does
the selector keep the scenarios that actually broke*. The first can pass while
the second fails, because the map itself can be incomplete; only this one
involves running the code.

EVERYTHING HERE RUNS OFFLINE
----------------------------
No key, no network, no provider. The suite under measurement is the scripted
backend, which reproduces byte-identically, and the mutants are applied inside a
throwaway git repository under a temporary directory. Nothing in this module
writes to the repository it is measuring.
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from lab.selection.diff import ChangeSet, GitUnavailable, analyse_changes
from lab.selection.select import (
    DEFAULT_MAP_PATH,
    OverrideRules,
    Selection,
    select,
)

__all__ = [
    "MIN_RECALL",
    "CalibrationGateError",
    "CaseOutcome",
    "CaseScore",
    "HistoryStudy",
    "Mutant",
    "MutationStudy",
    "SelectorBelowThresholdError",
    "SelectorCalibration",
    "SelectorNotCalibratedError",
    "aggregate",
    "enumerate_mutants",
    "in_ci_mode",
    "main",
    "mutants_at_observed_sites",
    "observed_locations",
    "require_calibrated_selector",
    "run_history_study",
    "run_mutation_study",
    "run_text_suite",
    "sample_mutants",
    "score_case",
]

LOGGER = logging.getLogger(__name__)

#: Repository root, derived from this file's location rather than the working
#: directory, so the module behaves the same however it is invoked.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]

#: Where `--write` records the measurement.
DEFAULT_REPORT_PATH: Path = REPO_ROOT / "lab" / "selection" / "calibration.json"

#: Recall the selector must reach before it may gate. One, and it is not a
#: negotiable default: the whole point of the tool is that it skips work, and a
#: recall of 0.98 means one regression in fifty ships green.
MIN_RECALL: float = 1.0

#: Source trees the mutation study enumerates mutants from. `tablemate` is the
#: system under test; the other two are harness modules every scenario runs
#: through, and they are included on purpose — a shared module is the case the
#: selector is supposed to widen on, so leaving it out would measure only the
#: easy half.
DEFAULT_MUTATION_ROOTS: tuple[str, ...] = ("tablemate", "lab/checks", "lab/simulator")

#: Seed for the stratified sample. Stated, so the sample is reproducible.
DEFAULT_SEED: int = 0

#: How many mutants to draw by default. Each costs one full suite run.
DEFAULT_SAMPLE: int = 60

#: Extra mutants drawn only from sites a trace observed. Without them a study is
#: almost entirely vacuous confirmations — see `run_mutation_study`.
DEFAULT_ENRICH: int = 40

#: Seconds a single suite run may take before it is recorded as unrunnable. A
#: mutant that hangs the harness is a real outcome, not a reason to hang here.
SUITE_TIMEOUT_S: float = 180.0

#: Environment variables consulted for "is this an unattended run".
#:
#: Restated rather than imported from `lab.judges.registry`, which owns the same
#: idea for judges. The two are pinned together by a test instead of by an
#: import, so that the selector layer keeps no build-time dependency on the judge
#: layer while a real divergence in meaning still fails loudly.
CI_ENV_VARS: tuple[str, ...] = ("LAB_SELECTOR_CI", "CI")

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSEY = frozenset({"0", "false", "no", "off", ""})


def in_ci_mode() -> bool:
    """True when the environment says this is an unattended run."""
    for name in CI_ENV_VARS:
        raw = os.environ.get(name)
        if raw is None:
            continue
        value = raw.strip().lower()
        if value in _TRUTHY:
            return True
        if value in _FALSEY:
            return False
    return False


# --------------------------------------------------------------------------- #
# Running the suite, in a tree that is not this one
# --------------------------------------------------------------------------- #

#: The child process that runs one full deterministic suite and prints the
#: verdicts as JSON.
#:
#: A separate interpreter, and not a nicety. The tree being measured contains a
#: deliberately broken copy of `tablemate` or `lab`, and importing that into the
#: calibrator's own process would leave a mutated module in `sys.modules` for
#: every later case to trip over. A subprocess whose `sys.path` starts at the
#: mutant tree is the only isolation that actually holds.
#:
#: It reimplements the row filter `evallab run` applies (drop rows with no
#: committed caller script, drop rows whose point is a perturbed audio channel)
#: rather than importing `lab.cli`. That is deliberate: this must run unchanged
#: against forty-eight historical checkouts of the CLI, so depending on any one
#: version of its internals would make the study measure the CLI's churn.
SUITE_RUNNER_SOURCE = '''
import json, os, sys

root = sys.argv[1]
os.chdir(root)
sys.path.insert(0, root)

try:
    from pathlib import Path

    import yaml

    from lab.clock import FakeClock
    from lab.simulator import ScriptedCaller, run_scenario
    from tablemate.runtime import build_agent
    import scenarios.loader as loader

    corpus = loader.load_corpus()
    blocks = yaml.safe_load(
        Path("fixtures/caller_scripts.yaml").read_text(encoding="utf-8")
    )["scripts"]
    personas = corpus.personas

    verdicts = {}
    for scenario in corpus.scenarios:
        block = blocks.get(scenario.id)
        if block is None:
            continue
        voice = getattr(scenario, "voice", None)
        if voice is not None and voice.perturbations:
            continue
        steps = block.get("seed") or []

        def apply(store, steps=steps):
            for step in steps:
                for action, payload in step.items():
                    getattr(store, action)(**payload)

        clock = FakeClock()
        agent = build_agent(clock=clock, seed=(apply if steps else None))
        caller = ScriptedCaller(
            tuple(block["script"]),
            profile=scenario.caller_profile(personas),
            closing=block.get("closing"),
        )
        trace = run_scenario(
            scenario_id=scenario.id,
            agent=agent,
            caller=caller,
            adapter="text:replay",
            clock=clock,
            session_id=scenario.id + "#0",
            max_turns=24,
        )
        report = scenario.contract_set().run(trace, scenario.check_context())
        verdicts[scenario.id] = sorted(r.name for r in report.results if not r.passed)

    print(json.dumps({"ok": True, "verdicts": verdicts}))
except BaseException as exc:  # noqa: BLE001 - a broken tree is data, not a crash
    print(json.dumps({"ok": False, "error": (type(exc).__name__ + ": " + str(exc))[:300]}))
'''


@dataclass(frozen=True)
class SuiteRun:
    """One full deterministic suite run over one tree.

    `verdicts` maps scenario id to the names of the contracts that failed, so an
    empty tuple is a green row and the mapping's keys are the rows that ran at
    all. `error` is set when the tree could not be driven — an old checkout whose
    harness predates the fixture, or a mutant that broke the import.
    """

    root: str
    verdicts: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def rows(self) -> int:
        return len(self.verdicts)

    @property
    def failing_rows(self) -> tuple[str, ...]:
        return tuple(sorted(k for k, v in self.verdicts.items() if v))


def run_text_suite(
    root: Path | str,
    *,
    python: str | None = None,
    timeout: float = SUITE_TIMEOUT_S,
) -> SuiteRun:
    """Drive the deterministic text suite over the tree at `root`.

    Offline and keyless by construction: the scripted caller and the scripted
    backend are both fixtures, so the whole run is a pure function of the tree.
    """
    root = str(Path(root).resolve())
    interpreter = python or sys.executable
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(SUITE_RUNNER_SOURCE)
        runner = handle.name
    try:
        completed = subprocess.run(
            [interpreter, runner, root],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            # A mutant must not inherit a PYTHONPATH pointing at the pristine
            # tree, or it would quietly import the unmutated module and the whole
            # case would measure nothing.
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        )
    except subprocess.TimeoutExpired:
        return SuiteRun(root=root, error=f"timeout after {timeout:g}s")
    finally:
        Path(runner).unlink(missing_ok=True)

    payload: Any = None
    for line in reversed(completed.stdout.splitlines()):
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        break
    if not isinstance(payload, dict):
        detail = (completed.stderr or completed.stdout or "no output").strip()
        return SuiteRun(root=root, error=f"no verdict json: {detail[:300]}")
    if not payload.get("ok"):
        return SuiteRun(root=root, error=str(payload.get("error", "unknown"))[:300])
    verdicts = {
        str(k): tuple(str(name) for name in v)
        for k, v in (payload.get("verdicts") or {}).items()
    }
    return SuiteRun(root=root, verdicts=verdicts)


# --------------------------------------------------------------------------- #
# Scoring one case. Pure, so it can be tested without running anything.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CaseOutcome:
    """One before/after pair, and what the selector said about it.

    Deliberately inert: it holds observations and no logic, so a test can build
    one by hand and `score_case` can be exercised on failures that no amount of
    real history contains.
    """

    label: str
    kind: str
    base: SuiteRun
    head: SuiteRun
    selection: Selection | None
    detail: str = ""
    selection_error: str | None = None
    stratum: str = "proportional"


@dataclass(frozen=True)
class CaseScore:
    """What one case contributes to the published numbers.

    `regressions` is the brief's definition — a check that passed at base and
    fails at head. `changes` is the wider set, every row whose verdict vector
    moved at all, which also catches a failure that *disappeared*. A vanished
    failure is a silent false fix and is worth knowing about, so both are
    computed; `regressions` is what the gate reads, and both are printed.
    """

    label: str
    kind: str
    usable: bool
    reason: str
    corpus_size: int
    selected: int
    stratum: str = "proportional"
    regressions: tuple[str, ...] = ()
    changes: tuple[str, ...] = ()
    missed_regressions: tuple[str, ...] = ()
    missed_changes: tuple[str, ...] = ()

    @property
    def narrowed(self) -> bool:
        """Did the selector actually skip anything here?

        The distinction the headline recall cannot survive without. A failure in
        a case where the selector returned the whole corpus was never at risk of
        being missed, so counting it as a catch is exactly the vacuous pass
        `lab.checks` refuses to count for contracts.
        """
        return bool(self.corpus_size) and self.selected < self.corpus_size

    @property
    def caught_regressions(self) -> int:
        return len(self.regressions) - len(self.missed_regressions)

    @property
    def caught_changes(self) -> int:
        return len(self.changes) - len(self.missed_changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "usable": self.usable,
            "reason": self.reason,
            "corpus_size": self.corpus_size,
            "selected": self.selected,
            "stratum": self.stratum,
            "narrowed": self.narrowed,
            "regressions": list(self.regressions),
            "changes": list(self.changes),
            "missed_regressions": list(self.missed_regressions),
            "missed_changes": list(self.missed_changes),
        }


def _failing(run: SuiteRun) -> dict[str, frozenset[str]]:
    return {k: frozenset(v) for k, v in run.verdicts.items()}


def score_case(outcome: CaseOutcome) -> CaseScore:
    """Turn one before/after pair into counted evidence.

    A case is *usable* only when both suite runs succeeded and the selector
    produced an answer. Anything else is recorded with its reason and excluded
    from the recall — never silently dropped, and never counted as a pass. An
    unusable case that was quietly treated as "nothing missed" would raise the
    published recall by removing the cases most likely to contain a miss.
    """
    corpus = outcome.selection.corpus_size if outcome.selection else 0
    selected = len(outcome.selection.selected_ids) if outcome.selection else 0

    def unusable(reason: str) -> CaseScore:
        return CaseScore(
            label=outcome.label,
            kind=outcome.kind,
            usable=False,
            reason=reason,
            corpus_size=corpus,
            selected=selected,
            stratum=outcome.stratum,
        )

    if not outcome.base.ok:
        return unusable(f"base tree would not run: {outcome.base.error}")
    if not outcome.head.ok:
        return unusable(f"head tree would not run: {outcome.head.error}")
    if outcome.selection_error is not None:
        return unusable(f"selector failed: {outcome.selection_error}")
    if outcome.selection is None:
        return unusable("no selection was computed")

    base, head = _failing(outcome.base), _failing(outcome.head)
    chosen = set(outcome.selection.selected_ids)

    regressions, changes = [], []
    for scenario_id in sorted(set(base) | set(head)):
        before = base.get(scenario_id, frozenset())
        after = head.get(scenario_id, frozenset())
        if after - before:
            regressions.append(scenario_id)
        if before != after:
            changes.append(scenario_id)

    return CaseScore(
        label=outcome.label,
        kind=outcome.kind,
        usable=True,
        reason="measured",
        corpus_size=corpus,
        selected=selected,
        stratum=outcome.stratum,
        regressions=tuple(regressions),
        changes=tuple(changes),
        missed_regressions=tuple(s for s in regressions if s not in chosen),
        missed_changes=tuple(s for s in changes if s not in chosen),
    )


# --------------------------------------------------------------------------- #
# Mutants, derived from the AST rather than declared
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Mutant:
    """One single-symbol edit, and where it lands.

    `qualname` is the enclosing definition, in the `path::qualname` vocabulary
    stage 1 and stage 2 already share, so a case can state what the change
    *should* have implicated independently of what the selector said.
    """

    path: str
    operator: str
    lineno: int
    start: int
    end: int
    qualname: str
    before: str
    after: str
    #: Which sampling stratum drew this mutant. `proportional` means it came from
    #: an even draw across the operators and is representative of the enumerated
    #: population; anything else was drawn at a deliberately higher rate and its
    #: rates must not be pooled with the proportional ones.
    stratum: str = "proportional"

    @property
    def location(self) -> str:
        return f"{self.path}::{self.qualname}"

    @property
    def label(self) -> str:
        return f"{self.path}:{self.lineno}:{self.operator}"

    def apply_to(self, source: str) -> str:
        """Splice the replacement in at the node's own character offsets."""
        if source[self.start : self.end] != self.before:
            raise ValueError(
                f"{self.label}: source moved under the mutant; "
                f"expected {self.before[:40]!r}"
            )
        return source[: self.start] + self.after + source[self.end :]

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "path": self.path,
            "operator": self.operator,
            "lineno": self.lineno,
            "qualname": self.qualname,
            "location": self.location,
            "stratum": self.stratum,
        }


#: Comparison swaps. Each pair changes behaviour at a boundary, which is where
#: this kind of agent actually breaks (`>=` on a party-size threshold is the
#: repository's own worked example).
_COMPARE_SWAPS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.GtE: ast.Gt,
    ast.Gt: ast.GtE,
    ast.LtE: ast.Lt,
    ast.Lt: ast.LtE,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}


def _offsets(source: str) -> list[int]:
    """Character index at which each 1-based line starts."""
    out, total = [0, 0], 0
    for line in source.splitlines(keepends=True):
        total += len(line)
        out.append(total)
    return out


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """Every string constant that is a module, class or function docstring.

    Recorded rather than skipped. A docstring edit is a real change a developer
    makes, and how much the selector runs for one is a fact worth publishing —
    but it is a different *kind* of change from a threshold flip, so it gets its
    own operator name and its own row in the stratified sample.
    """
    marked: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            marked.add(id(first.value))
    return marked


def enumerate_mutants(root: Path | str, paths: Sequence[str] = DEFAULT_MUTATION_ROOTS) -> list[Mutant]:
    """Every mutant the operators can produce under `paths`, in a stable order.

    Derived, never declared — the same argument `trace_map` makes about the
    dependency map. A hand-written mutant list measures the taste of whoever
    wrote it and goes stale the moment the code moves; an enumerated one can be
    counted, sampled with a stated seed, and re-derived by anybody who doubts the
    number.

    Files that will not parse are skipped and do not raise: this walks whatever
    tree it is given, including historical ones.
    """
    root = Path(root)
    found: list[Mutant] = []
    for entry in paths:
        base = root / entry
        if not base.exists():
            continue
        files = sorted(base.rglob("*.py")) if base.is_dir() else [base]
        for file in files:
            if "__pycache__" in file.parts:
                continue
            try:
                source = file.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            found.extend(
                _mutants_in(
                    source=source,
                    tree=tree,
                    path=file.relative_to(root).as_posix(),
                )
            )
    return found


def _mutants_in(*, source: str, tree: ast.Module, path: str) -> list[Mutant]:
    line_start = _offsets(source)
    docstrings = _docstring_nodes(tree)
    owner: dict[int, str] = {}

    def walk(body: Iterable[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}.{node.name}" if prefix else node.name
                # Innermost first. Claiming this node's descendants before
                # recursing would give every method its class's name and no
                # method would ever carry its own, which is the qualname stage 1
                # actually emits for a changed method body.
                walk(node.body, name)
                for descendant in ast.walk(node):
                    owner.setdefault(id(descendant), name)

    walk(tree.body, "")

    def span(node: ast.AST) -> tuple[int, int] | None:
        if (
            node.lineno is None  # type: ignore[attr-defined]
            or node.end_lineno is None  # type: ignore[attr-defined]
        ):
            return None
        start = line_start[node.lineno] + node.col_offset  # type: ignore[attr-defined]
        end = line_start[node.end_lineno] + node.end_col_offset  # type: ignore[attr-defined]
        return (start, end) if 0 <= start < end <= len(source) else None

    out: list[Mutant] = []

    def emit(node: ast.AST, operator: str, after: str) -> None:
        bounds = span(node)
        if bounds is None:
            return
        start, end = bounds
        out.append(
            Mutant(
                path=path,
                operator=operator,
                lineno=node.lineno,  # type: ignore[attr-defined]
                start=start,
                end=end,
                qualname=owner.get(id(node), "<module>"),
                before=source[start:end],
                after=after,
            )
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool):
                emit(node, "bool", repr(not value))
            elif isinstance(value, int):
                emit(node, "number", repr(value + 1))
            elif isinstance(value, float):
                emit(node, "number", repr(value + 1.0))
            elif isinstance(value, str) and value.strip():
                # Appending never disturbs a format placeholder or an escape,
                # which a substitution would, so the mutant stays syntactically
                # valid on every string in the tree.
                kind = "docstring" if id(node) in docstrings else "string"
                emit(node, kind, repr(value + " qq"))
        elif isinstance(node, ast.Compare) and len(node.ops) == 1:
            swap = _COMPARE_SWAPS.get(type(node.ops[0]))
            if swap is None:
                continue
            replacement = ast.Compare(
                left=node.left, ops=[swap()], comparators=node.comparators
            )
            try:
                emit(node, "compare", ast.unparse(ast.fix_missing_locations(replacement)))
            except (AttributeError, ValueError):  # pragma: no cover - unparse is total here
                continue

    out.sort(key=lambda m: (m.path, m.lineno, m.start, m.operator))
    return out


def _related(observed: str, changed: str) -> bool:
    """Is one qualname a dotted ancestor of the other?

    The join rule stage 3 applies, restated here for the same reason
    `MODULE_QUALNAME` is restated there: this module must not break when the
    selector's internals move, and a test pins the two together.
    """
    if observed == changed:
        return True
    return changed.startswith(observed + ".") or observed.startswith(changed + ".")


def observed_locations(map_path: Path | str = DEFAULT_MAP_PATH) -> set[str]:
    """Every `path::qualname` the trace map resolved to source.

    These are the only sites at which the selector is *able* to narrow: a change
    anywhere else resolves to no observed name and fails safe to the whole
    corpus. Knowing them is what makes it possible to aim the enriched sample at
    the cases that can actually discriminate the selector.
    """
    from lab.selection.trace_map import load_trace_map

    trace_map = load_trace_map(map_path)
    sites: set[str] = set()
    for symbol in trace_map.symbols:
        sites.update(symbol.locations)
    return sites


def mutants_at_observed_sites(
    mutants: Sequence[Mutant], sites: Iterable[str]
) -> list[Mutant]:
    """The mutants that land where a trace actually recorded something."""
    resolved = list(sites)
    out: list[Mutant] = []
    for mutant in mutants:
        path, _, qualname = mutant.location.partition("::")
        for site in resolved:
            site_path, _, site_qualname = site.partition("::")
            if site_path == path and _related(site_qualname, qualname):
                out.append(mutant)
                break
    return out


def sample_mutants(
    mutants: Sequence[Mutant], *, size: int, seed: int = DEFAULT_SEED
) -> list[Mutant]:
    """Draw `size` mutants, stratified by operator, deterministically.

    Stratified because the operators are wildly unequal in population — this
    repository is documentation-heavy, so an unstratified draw would be almost
    entirely docstring edits and the study would measure one operator while
    claiming to measure the selector. Each stratum contributes in proportion to
    the sample, with at least one row wherever the stratum is non-empty, and the
    per-stratum counts are published.
    """
    if size <= 0 or not mutants:
        return []
    strata: dict[str, list[Mutant]] = {}
    for mutant in mutants:
        strata.setdefault(mutant.operator, []).append(mutant)

    names = sorted(strata)
    quota = max(1, size // len(names))
    drawn: list[Mutant] = []
    rng = random.Random(seed)
    for name in names:
        pool = sorted(strata[name], key=lambda m: (m.path, m.lineno, m.start))
        take = min(quota, len(pool))
        drawn.extend(rng.sample(pool, take))

    # Top up from whatever is left, so a small stratum does not shrink the study.
    if len(drawn) < size:
        chosen = {id(m) for m in drawn}
        rest = [m for m in mutants if id(m) not in chosen]
        rest.sort(key=lambda m: (m.path, m.lineno, m.start, m.operator))
        drawn.extend(rng.sample(rest, min(size - len(drawn), len(rest))))

    drawn.sort(key=lambda m: (m.path, m.lineno, m.start, m.operator))
    return drawn[:size]


# --------------------------------------------------------------------------- #
# A throwaway repository to mutate
# --------------------------------------------------------------------------- #


class ScratchRepo:
    """A disposable git checkout of the tree under measurement.

    Stage 1 reads a real `git diff`, so measuring it against a fabricated
    `ChangeSet` would measure something the tool does not do. This gives it a
    genuine two-commit repository to read, built by `git archive` out of the
    repository under study so that nothing — not an index, not a ref, not a
    worktree registration — is written back to it.
    """

    def __init__(self, source: Path | str, ref: str = "HEAD", workdir: Path | str | None = None):
        self.source = Path(source).resolve()
        self.ref = ref
        self._tempdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="selcal-"))
        self._owned = workdir is None
        self.root = self._tempdir / "tree"

    def __enter__(self) -> Self:
        self.root.mkdir(parents=True, exist_ok=True)
        archive = subprocess.run(
            ["git", "-C", str(self.source), "archive", self.ref],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["tar", "-x", "-C", str(self.root)], input=archive.stdout, check=True
        )
        self._git("init", "-q")
        self._commit("base")
        return self

    def __exit__(self, *exc: object) -> None:
        if self._owned:
            shutil.rmtree(self._tempdir, ignore_errors=True)

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    def _commit(self, message: str) -> None:
        self._git("add", "-A")
        self._git(
            "-c",
            "user.email=calibration@localhost",
            "-c",
            "user.name=selector calibration",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            message,
        )

    def apply(self, mutant: Mutant) -> None:
        """Write one mutant and commit it, so stage 1 has a diff to read."""
        target = self.root / mutant.path
        target.write_text(
            mutant.apply_to(target.read_text(encoding="utf-8")), encoding="utf-8"
        )
        self._commit(f"mutant {mutant.label}")

    def revert(self) -> None:
        self._git("reset", "--hard", "-q", "HEAD~1")


# --------------------------------------------------------------------------- #
# The mutation study
# --------------------------------------------------------------------------- #


def _selection_for(
    repo_root: Path | str,
    *,
    base_ref: str,
    head_ref: str,
    map_path: Path | str,
) -> tuple[Selection | None, ChangeSet | None, str | None]:
    """Run stages 1 and 3 exactly as a caller would, and report a failure as data.

    Overrides are switched off. They can only widen, so leaving them on would let
    one `everything: true` rule in a checkout buy a perfect recall and publish it
    as a property of the derivation.
    """
    try:
        change_set = analyse_changes(base_ref, head_ref=head_ref, repo_root=repo_root)
    except (GitUnavailable, OSError, ValueError) as exc:
        return None, None, f"{type(exc).__name__}: {exc}"
    try:
        selection = select(
            change_set=change_set,
            map_path=map_path,
            overrides=OverrideRules(),
            overrides_path=None,
        )
    except Exception as exc:  # noqa: BLE001 - a selector crash is a finding
        return None, change_set, f"{type(exc).__name__}: {exc}"
    return selection, change_set, None


@dataclass(frozen=True)
class MutationStudy:
    """Recall measured against failures created on purpose.

    Read `limitation()` before quoting the number. These are enumerated
    constant-and-comparison edits, not a sample of what developers actually do,
    and the study says so wherever it is printed.
    """

    roots: tuple[str, ...]
    seed: int
    enumerated: int
    strata: Mapping[str, int]
    sampled: tuple[Mutant, ...]
    scores: tuple[CaseScore, ...]
    base_rows: int
    corpus_rows: int = 0
    blind_excludable_rows: int = 0

    def limitation(self) -> list[str]:
        corpus = self.corpus_rows or self.base_rows
        return [
            "Mutants are enumerated from the AST — numbers, strings, docstrings,",
            "booleans and comparison operators — so they are unbiased by taste but",
            "they are not a sample of real developer changes. No mutant here adds a",
            "function, moves a file, or changes a dependency that lives only in data.",
            "The number is evidence about the selector's logic, not about its",
            "behaviour on a real week's commits.",
            "",
            f"THE RECALL'S OBSERVABLE BASE IS {self.base_rows}/{corpus} ROWS.",
            "The deterministic suite drives the runner's default corpus less the",
            "rows with no committed caller script and the rows whose point is a",
            "perturbed audio channel; the selector's denominator is the whole",
            "corpus. A row outside the observable base cannot fail here, so a miss",
            "on one is never counted. Splitting the remainder matters, because only",
            "one half of it is harmless:",
            (
                f"  {corpus - self.base_rows - self.blind_excludable_rows:>3}"
                "  invisible and unmapped — always selected, so never skippable;"
            ),
            (
                f"  {self.blind_excludable_rows:>3}"
                "  invisible and MAPPED — excludable, and a miss on one of"
            ),
            "       these would be invisible to this study. The blind spot.",
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "roots": list(self.roots),
            "seed": self.seed,
            "mutants_enumerated": self.enumerated,
            "mutants_sampled": len(self.sampled),
            "strata": dict(self.strata),
            "base_rows": self.base_rows,
            "corpus_rows": self.corpus_rows,
            "blind_excludable_rows": self.blind_excludable_rows,
            "scores": [s.to_dict() for s in self.scores],
            "limitation": self.limitation(),
        }


def run_mutation_study(
    repo_root: Path | str = REPO_ROOT,
    *,
    sample: int = DEFAULT_SAMPLE,
    seed: int = DEFAULT_SEED,
    roots: Sequence[str] = DEFAULT_MUTATION_ROOTS,
    map_path: Path | str = DEFAULT_MAP_PATH,
    mutants: Sequence[Mutant] | None = None,
    enrich: int = 0,
    progress: bool = False,
) -> MutationStudy:
    """Break the code on purpose, and count what the selector would have skipped.

    One suite run establishes the baseline verdicts; each mutant then costs one
    more. A mutant that changes no verdict is kept in the report rather than
    dropped — it contributes nothing to the recall's denominator but everything
    to the selection ratio, and silently discarding the cases where nothing broke
    is how a mutation study talks itself into a flattering number.
    """
    repo_root = Path(repo_root).resolve()
    with ScratchRepo(repo_root) as repo:
        enumerated = list(enumerate_mutants(repo.root, roots))
        if mutants is not None:
            chosen = list(mutants)
        else:
            chosen = sample_mutants(enumerated, size=sample, seed=seed)
            if enrich:
                # A deliberately disproportionate draw from the only sites at
                # which the selector is able to narrow. Without it the study is
                # almost all vacuous confirmations: a mutant in a shared harness
                # module escalates to the whole corpus, so it can confirm the
                # selector but can never catch it out. The enriched rows are
                # labelled and their selection ratio is reported separately,
                # because they are not representative of the population.
                already = {m.label for m in chosen}
                pool = [
                    m
                    for m in mutants_at_observed_sites(
                        enumerated, observed_locations(map_path)
                    )
                    if m.label not in already
                ]
                chosen.extend(
                    replace(m, stratum="observed-enriched")
                    for m in sample_mutants(pool, size=enrich, seed=seed)
                )
        strata: dict[str, int] = {}
        for mutant in enumerated:
            strata[mutant.operator] = strata.get(mutant.operator, 0) + 1

        base_run = run_text_suite(repo.root)
        scores: list[CaseScore] = []
        for index, mutant in enumerate(chosen, start=1):
            if progress:
                print(
                    f"  [{index}/{len(chosen)}] {mutant.label}",
                    file=sys.stderr,
                    flush=True,
                )
            try:
                repo.apply(mutant)
            except (ValueError, OSError, subprocess.CalledProcessError) as exc:
                scores.append(
                    CaseScore(
                        label=mutant.label,
                        kind="mutation",
                        usable=False,
                        reason=f"mutant would not apply: {exc}",
                        corpus_size=0,
                        selected=0,
                        stratum=mutant.stratum,
                    )
                )
                continue
            try:
                head_run = run_text_suite(repo.root)
                selection, _, error = _selection_for(
                    repo.root, base_ref="HEAD~1", head_ref="HEAD", map_path=map_path
                )
                scores.append(
                    score_case(
                        CaseOutcome(
                            label=mutant.label,
                            kind="mutation",
                            base=base_run,
                            head=head_run,
                            selection=selection,
                            selection_error=error,
                            detail=mutant.location,
                            stratum=mutant.stratum,
                        )
                    )
                )
            finally:
                repo.revert()

    return MutationStudy(
        roots=tuple(roots),
        seed=seed,
        enumerated=len(enumerated),
        strata=strata,
        sampled=tuple(chosen),
        scores=tuple(scores),
        base_rows=base_run.rows,
        **_observability(base_run, map_path=map_path),
    )


def _observability(
    base_run: SuiteRun, *, map_path: Path | str | None = DEFAULT_MAP_PATH
) -> dict[str, int]:
    """How much of the corpus this study can see a failure in, and how much it cannot.

    The deterministic suite drives the runner's default corpus less the rows it
    has no committed caller script for and the rows whose point is a perturbed
    audio channel. The selector's denominator is the whole corpus, which is
    larger. Every row outside the observable base is a row that cannot fail here,
    so a miss on one would not be counted.

    That is only harmless for rows the selector always selects — an unmapped row
    cannot be missed by definition. A *mapped* row outside the observable base is
    a genuine blind spot: the selector is free to exclude it and this study would
    never know. Counting them is the difference between a denominator and a
    naked rate, so the count is computed and published rather than described.
    """
    from lab.selection.trace_map import load_trace_map

    blind = 0
    corpus = 0
    try:
        trace_map = load_trace_map(map_path if map_path is not None else DEFAULT_MAP_PATH)
        corpus = trace_map.corpus_size
        observable = set(base_run.verdicts)
        blind = sum(
            1
            for row in trace_map.scenarios
            if row.mapped and row.scenario_id not in observable
        )
    except Exception as exc:  # noqa: BLE001 - the study still stands without it
        LOGGER.warning(
            "could not size the observable base (%s); the limitation section will "
            "fall back to the observable row count and understate the blind spot",
            exc,
        )
    return {"corpus_rows": corpus, "blind_excludable_rows": blind}


# --------------------------------------------------------------------------- #
# The history study
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HistoryStudy:
    """Recall measured against this repository's own commits.

    The study every test-impact tool should publish and almost none does. It is
    reported here even when — especially when — it produces no evidence at all.
    """

    repo_root: str
    commits_examined: tuple[str, ...]
    commits_runnable: tuple[str, ...]
    unrunnable: Mapping[str, str]
    scores: tuple[CaseScore, ...]

    @property
    def pairs_backtestable(self) -> int:
        return len(self.scores)

    @property
    def pairs_with_evidence(self) -> int:
        return sum(1 for s in self.scores if s.usable and s.regressions)

    def limitation(self) -> list[str]:
        """What this study did and did not establish, in plain words."""
        regressions = sum(len(s.regressions) for s in self.scores if s.usable)
        if regressions:
            return [
                (
                    f"{regressions} historical failure(s) across "
                    f"{self.pairs_with_evidence}/{self.pairs_backtestable} commit pairs."
                ),
            ]
        return [
            (
                f"{self.pairs_backtestable} backtestable commit pair(s), "
                f"{regressions} failures. The denominator is zero, so this study"
            ),
            "measures nothing about the selector and must not be quoted as a pass.",
            "",
            "Every commit in this history is a curated, green commit: the suite's",
            "verdicts are identical either side of every pair, so there is no",
            "failure for a selector to miss. That is a property of how this",
            "repository was written, not of the selector.",
            "",
            "WHAT WOULD MAKE THIS MEASUREMENT SOUND",
            "  * commits that were red when pushed and green after a fix — the",
            "    red/green transition is the only thing that supplies a failure;",
            "  * roughly 30-50 of them, which at this corpus's failure rate is",
            "    where a single miss would move the recall by a visible amount;",
            "  * spread across the kinds of change the map handles differently:",
            "    a single agent, a shared prompt, a tool signature, a check, and",
            "    at least a few config or data-only changes, which are the cases",
            "    stage 1 and stage 2 provably cannot see;",
            "  * each with the suite runnable at both parent and child, which",
            (
                f"    holds for {len(self.commits_runnable)}/"
                f"{len(self.commits_examined)} commits here."
            ),
            "",
            "Until then the mutation study below is the only evidence available,",
            "and it is synthetic.",
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "commits_examined": len(self.commits_examined),
            "commits_runnable": len(self.commits_runnable),
            "pairs_backtestable": self.pairs_backtestable,
            "pairs_with_evidence": self.pairs_with_evidence,
            "unrunnable": dict(self.unrunnable),
            "commits": list(self.commits_examined),
            "scores": [s.to_dict() for s in self.scores],
            "limitation": self.limitation(),
        }


def _git_lines(repo_root: Path | str, *args: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise GitUnavailable(completed.stderr.strip() or "git failed")
    return [line for line in completed.stdout.splitlines() if line.strip()]


def run_history_study(
    repo_root: Path | str = REPO_ROOT,
    *,
    limit: int | None = None,
    map_path: Path | str = DEFAULT_MAP_PATH,
    workdir: Path | str | None = None,
    progress: bool = False,
) -> HistoryStudy:
    """Backtest the selector against real commits, and report what that yields.

    Each commit is extracted once with `git archive` — never a worktree, never a
    checkout — so the repository under study is only ever read.
    """
    repo_root = Path(repo_root).resolve()
    commits = _git_lines(repo_root, "log", "--format=%H")
    if limit is not None:
        commits = commits[: limit + 1]

    holder = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="selhist-"))
    holder.mkdir(parents=True, exist_ok=True)
    runs: dict[str, SuiteRun] = {}
    try:
        for index, commit in enumerate(commits, start=1):
            if progress:
                print(f"  [{index}/{len(commits)}] {commit[:9]}", file=sys.stderr, flush=True)
            tree = holder / commit
            tree.mkdir(parents=True, exist_ok=True)
            archive = subprocess.run(
                ["git", "-C", str(repo_root), "archive", commit],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["tar", "-x", "-C", str(tree)], input=archive.stdout, check=True
            )
            runs[commit] = run_text_suite(tree)

        scores: list[CaseScore] = []
        known = set(commits)
        for commit in commits:
            parents = _git_lines(repo_root, "rev-list", "--parents", "-n", "1", commit)
            fields = parents[0].split() if parents else []
            if len(fields) < 2:
                continue
            parent = fields[1]
            if parent not in known:
                continue
            base_run, head_run = runs[parent], runs[commit]
            if not base_run.ok or not head_run.ok:
                continue
            selection, _, error = _selection_for(
                repo_root, base_ref=parent, head_ref=commit, map_path=map_path
            )
            scores.append(
                score_case(
                    CaseOutcome(
                        label=commit[:9],
                        kind="history",
                        base=base_run,
                        head=head_run,
                        selection=selection,
                        selection_error=error,
                    )
                )
            )
    finally:
        if workdir is None:
            shutil.rmtree(holder, ignore_errors=True)

    return HistoryStudy(
        repo_root=str(repo_root),
        commits_examined=tuple(commits),
        commits_runnable=tuple(c for c in commits if runs.get(c) and runs[c].ok),
        unrunnable={
            c: (runs[c].error or "") for c in commits if runs.get(c) and not runs[c].ok
        },
        scores=tuple(scores),
    )


# --------------------------------------------------------------------------- #
# The published numbers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SelectorCalibration:
    """Every number this module publishes, each beside its denominator.

    `recall` is the headline and `selection_ratio` is the price. Neither is
    meaningful alone: a selector that always returns the whole corpus scores a
    perfect recall and saves nothing, and a selector that returns one scenario
    saves almost everything and catches almost nothing.

    `calibrated` is the question `require_calibrated_selector` actually asks. It
    is false when the denominator is zero, and that is not a technicality: a
    recall of "1.0 out of 0 failures" is not a measurement, and treating it as
    one would let a selector gate on the strength of never having been tested.
    """

    evidence: str
    scores: tuple[CaseScore, ...]
    history: HistoryStudy | None = None
    mutation: MutationStudy | None = None
    generated: str = ""
    command: str = ""
    commit: str = ""
    repo: str = ""

    # ------------------------------------------------------------ denominators

    @property
    def usable(self) -> tuple[CaseScore, ...]:
        return tuple(s for s in self.scores if s.usable)

    @property
    def unusable(self) -> tuple[CaseScore, ...]:
        return tuple(s for s in self.scores if not s.usable)

    @property
    def regressions_total(self) -> int:
        """Failures the full suite caught. The denominator of `recall`."""
        return sum(len(s.regressions) for s in self.usable)

    @property
    def regressions_missed(self) -> int:
        return sum(len(s.missed_regressions) for s in self.usable)

    @property
    def changes_total(self) -> int:
        return sum(len(s.changes) for s in self.usable)

    @property
    def changes_missed(self) -> int:
        return sum(len(s.missed_changes) for s in self.usable)

    @property
    def cases_with_failures(self) -> int:
        return sum(1 for s in self.usable if s.regressions)

    # ------------------------------------------- the non-vacuous denominator

    @property
    def discriminating_cases(self) -> tuple[CaseScore, ...]:
        """Usable cases that both broke a row and skipped one.

        The only cases in which the selector was genuinely at risk of being
        wrong. Everything else confirms it vacuously.
        """
        return tuple(s for s in self.usable if s.regressions and s.narrowed)

    @property
    def discriminating_total(self) -> int:
        return sum(len(s.regressions) for s in self.discriminating_cases)

    @property
    def discriminating_missed(self) -> int:
        return sum(len(s.missed_regressions) for s in self.discriminating_cases)

    @property
    def vacuous_confirmations(self) -> int:
        """Failures that occurred where the selector had skipped nothing.

        Reported rather than hidden. A recall built entirely out of these says
        only that the selector declined to narrow, which is the behaviour of a
        selector that has been switched off.
        """
        return sum(
            len(s.regressions) for s in self.usable if s.regressions and not s.narrowed
        )

    @property
    def discriminating(self) -> bool:
        """Has the selector ever been observed skipping while something broke?"""
        return self.discriminating_total > 0

    @property
    def discriminating_recall(self) -> float | None:
        """Recall over failures that the selector could actually have missed."""
        if not self.discriminating:
            return None
        caught = self.discriminating_total - self.discriminating_missed
        return caught / self.discriminating_total

    # ------------------------------------------------------------------ rates

    @property
    def calibrated(self) -> bool:
        """Is there any failure at all to have been missed?"""
        return self.regressions_total > 0

    @property
    def recall(self) -> float | None:
        """Caught / caught+missed, or None when nothing failed. Never 1.0 by default."""
        if not self.calibrated:
            return None
        caught = self.regressions_total - self.regressions_missed
        return caught / self.regressions_total

    @property
    def change_recall(self) -> float | None:
        """The stricter rate: every row whose verdict moved, in either direction."""
        if self.changes_total == 0:
            return None
        return (self.changes_total - self.changes_missed) / self.changes_total

    @property
    def selection_ratio(self) -> tuple[float, int]:
        """`(mean selected, corpus size)` over the *proportional* stratum only.

        The enriched rows are excluded on purpose. They were drawn at a higher
        rate precisely because they are the cases where the selector narrows, so
        pooling them would quote a saving the tool does not deliver on a
        representative change. Recall pools every stratum; the saving does not.
        """
        rows = [
            s for s in self.usable if s.corpus_size and s.stratum == "proportional"
        ]
        if not rows:
            return (0.0, 0)
        return (sum(s.selected for s in rows) / len(rows), rows[0].corpus_size)

    @property
    def strata(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for score in self.usable:
            counts[score.stratum] = counts.get(score.stratum, 0) + 1
        return counts

    def selection_by_stratum(self) -> dict[str, tuple[float, int, int]]:
        """`stratum -> (mean selected, corpus size, n)`.

        Printed in full because the two strata answer different questions and
        their means are genuinely different numbers. Collapsing them into one
        figure would either overstate the saving (by leaning on the enriched
        rows) or hide that the selector can narrow at all.
        """
        grouped: dict[str, list[CaseScore]] = {}
        for score in self.usable:
            if score.corpus_size:
                grouped.setdefault(score.stratum, []).append(score)
        return {
            name: (
                sum(s.selected for s in rows) / len(rows),
                rows[0].corpus_size,
                len(rows),
            )
            for name, rows in sorted(grouped.items())
        }

    @property
    def missed(self) -> tuple[tuple[str, str], ...]:
        """`(case, scenario)` for every miss — the list a reviewer must read."""
        return tuple(
            (s.label, scenario)
            for s in self.usable
            for scenario in s.missed_regressions
        )

    def passed(self, *, min_recall: float = MIN_RECALL) -> bool:
        """Measured, non-vacuously, and nothing missed in either denominator."""
        return (
            self.calibrated
            and self.discriminating
            and (self.recall or 0.0) >= min_recall
            and (self.discriminating_recall or 0.0) >= min_recall
        )

    # ----------------------------------------------------------------- output

    def summary_lines(self) -> list[str]:
        mean, corpus = self.selection_ratio
        saved = corpus - mean
        pct = 0.0 if not corpus else 100.0 * saved / corpus
        recall = "undefined (no failures to catch)" if self.recall is None else f"{self.recall:.3f}"
        change = (
            "undefined" if self.change_recall is None else f"{self.change_recall:.3f}"
        )
        proportional = [s for s in self.usable if s.stratum == "proportional"]
        discriminating = (
            "undefined (never narrowed on a change that broke a row)"
            if self.discriminating_recall is None
            else f"{self.discriminating_recall:.3f}"
        )
        lines = [
            f"selector calibration — evidence: {self.evidence}",
            f"  cases measured          {len(self.usable)}/{len(self.scores)}",
            f"  cases that broke a row  {self.cases_with_failures}/{len(self.usable)}",
            (
                f"  failures caught         "
                f"{self.regressions_total - self.regressions_missed}/{self.regressions_total}"
                f"   (recall {recall})"
            ),
            (
                f"  verdict changes caught  "
                f"{self.changes_total - self.changes_missed}/{self.changes_total}"
                f"   (recall {change})"
            ),
            (
                f"  of those, non-vacuous   "
                f"{self.discriminating_total - self.discriminating_missed}"
                f"/{self.discriminating_total}"
                f"   (recall {discriminating})"
            ),
            (
                f"  vacuous confirmations   {self.vacuous_confirmations}"
                f"/{self.regressions_total}"
                "   (failures where the selector had skipped nothing)"
            ),
            (
                f"  mean selection          {mean:.1f}/{corpus}"
                f"   ({saved:.1f}/{corpus} skipped, {pct:.1f}%)"
                f"   [proportional stratum, n={len(proportional)}]"
            ),
            (
                f"  gate                    {'PASS' if self.passed() else 'REFUSE'}"
                f" (threshold {MIN_RECALL:.3f})"
            ),
        ]
        for name, (mean_n, corpus_n, count) in self.selection_by_stratum().items():
            saved_n = corpus_n - mean_n
            lines.append(
                f"    {name:<20}{mean_n:>6.1f}/{corpus_n}"
                f"   ({saved_n:.1f}/{corpus_n} skipped, n={count})"
            )
        if self.missed:
            lines.append("  MISSES — a regression the selector would have skipped:")
            lines.extend(f"    {case}  {scenario}" for case, scenario in self.missed[:20])
        if self.unusable:
            lines.append(f"  not measurable          {len(self.unusable)}/{len(self.scores)}")
            seen: dict[str, int] = {}
            for score in self.unusable:
                key = score.reason.split(":")[0]
                seen[key] = seen.get(key, 0) + 1
            lines.extend(f"    {n:>3}  {reason}" for reason, n in sorted(seen.items()))
        return lines

    def to_dict(self) -> dict[str, Any]:
        mean, corpus = self.selection_ratio
        return {
            "_provenance": {
                "command": self.command,
                "commit": self.commit,
                "generated": self.generated,
                "repo": self.repo,
                "note": (
                    "Recall of failures over the failures a full run caught. A "
                    "recall over an empty denominator is reported as null, never "
                    "as 1.0."
                ),
            },
            "evidence": self.evidence,
            "cases_total": len(self.scores),
            "cases_usable": len(self.usable),
            "cases_with_failures": self.cases_with_failures,
            "regressions_total": self.regressions_total,
            "regressions_missed": self.regressions_missed,
            "recall": self.recall,
            "changes_total": self.changes_total,
            "changes_missed": self.changes_missed,
            "change_recall": self.change_recall,
            "discriminating_cases": len(self.discriminating_cases),
            "discriminating_total": self.discriminating_total,
            "discriminating_missed": self.discriminating_missed,
            "discriminating_recall": self.discriminating_recall,
            "vacuous_confirmations": self.vacuous_confirmations,
            "discriminating": self.discriminating,
            "selection_mean": round(mean, 3),
            "selection_mean_stratum": "proportional",
            "strata": self.strata,
            "selection_by_stratum": {
                name: {"mean_selected": round(mean_n, 3), "corpus_size": c, "cases": n}
                for name, (mean_n, c, n) in self.selection_by_stratum().items()
            },
            "corpus_size": corpus,
            "calibrated": self.calibrated,
            "passed": self.passed(),
            "min_recall": MIN_RECALL,
            "missed": [{"case": c, "scenario": s} for c, s in self.missed],
            "history": self.history.to_dict() if self.history else None,
            "mutation": self.mutation.to_dict() if self.mutation else None,
        }


def aggregate(
    *,
    history: HistoryStudy | None = None,
    mutation: MutationStudy | None = None,
    evidence: str = "all",
    command: str = "",
    generated: str | None = None,
    commit: str = "",
    repo: str = "",
) -> SelectorCalibration:
    """Combine the studies the caller asked for into one published result.

    `evidence` names which studies count toward the gate. It exists because the
    two are not the same kind of evidence: history is real and (here) empty,
    mutation is synthetic and plentiful. A site that will only gate on real
    commits passes `evidence="history"` and gets a refusal until its history
    contains some failures — which is the correct answer, not a broken tool.
    """
    if evidence not in {"all", "history", "mutation"}:
        raise ValueError(
            f"evidence must be 'all', 'history' or 'mutation', got {evidence!r}"
        )
    scores: list[CaseScore] = []
    if history is not None and evidence in {"all", "history"}:
        scores.extend(history.scores)
    if mutation is not None and evidence in {"all", "mutation"}:
        scores.extend(mutation.scores)
    return SelectorCalibration(
        evidence=evidence,
        scores=tuple(scores),
        history=history,
        mutation=mutation,
        command=command,
        commit=commit,
        repo=repo,
        generated=generated
        if generated is not None
        else datetime.now(UTC).strftime("%Y-%m-%d"),
    )


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


class CalibrationGateError(RuntimeError):
    """Base class for gate refusals."""


class SelectorNotCalibratedError(CalibrationGateError):
    """No failure was ever available for the selector to miss."""


class SelectorBelowThresholdError(CalibrationGateError):
    """The selector was measured, and it missed something."""


def _warn_override(message: str) -> None:
    """Log the bypass loudly enough to survive a skim of the log."""
    LOGGER.warning(
        "\n"
        "!!! ================================================================ !!!\n"
        "!!! SKIPPING TESTS ON AN UNPROVEN SELECTOR\n"
        "!!! reason  : %s\n"
        "!!! override: allow_uncalibrated=True was passed at the call site.\n"
        "!!! A scenario this selector skips is a scenario nobody ran, and the\n"
        "!!! run will be green either way.\n"
        "!!! ================================================================ !!!",
        message,
    )


def require_calibrated_selector(
    calibration: SelectorCalibration,
    *,
    min_recall: float = MIN_RECALL,
    ci: bool | None = None,
    allow_uncalibrated: bool = False,
) -> SelectorCalibration:
    """Refuse to *gate* on a selector whose miss rate is unknown or non-zero.

    The mirror of `lab.judges.registry.require_calibrated`, and deliberately so:
    both answer "may this instrument's verdict be acted on automatically", and
    both treat *unmeasured* as a refusal rather than as a pass.

    Args:
        calibration: The measurement to read.
        min_recall: Default 1.0. Below it the selector may advise a human, but it
            may not decide what does not run.
        ci: Force strict (True) or advisory (False). Defaults to `in_ci_mode()`.
        allow_uncalibrated: Bypass, keyword-only and nothing else.

    Raises:
        SelectorNotCalibratedError: nothing ever failed, so nothing was measured.
        SelectorBelowThresholdError: measured, and a regression was missed.

    THE BYPASS IS A KEYWORD ARGUMENT AND NOTHING ELSE
    -------------------------------------------------
    No environment variable, no config key, no command-line flag — the rule the
    judge gate follows, for the reason its docstring gives: an override that can
    be set outside the source becomes permanent within a month and nobody
    remembers turning it on. A flag would put it in a shell history; a keyword
    argument puts it in a diff, in front of a reviewer.

    WHY THERE IS A CI SPLIT HERE
    ----------------------------
    A developer narrowing a local run to get feedback in ten seconds is doing
    something sensible even with an unproven selector, because the full suite
    still runs before the change lands. The danger is the unattended run that
    nobody reads, so the gate is strict exactly there.
    """
    strict = in_ci_mode() if ci is None else ci

    if not calibration.calibrated:
        message = (
            "the selector's miss rate has never been measured: across "
            f"{len(calibration.usable)} measured case(s) the full suite reported "
            "no failure at all, so there was nothing for selection to miss. A "
            "recall over an empty denominator is not 1.0, it is unknown. Run "
            "`python -m lab.selection.calibrate` and read the limitation section."
        )
        if allow_uncalibrated:
            _warn_override(message)
            return calibration
        if strict:
            raise SelectorNotCalibratedError(message)
        LOGGER.warning("selector calibration is advisory only: %s", message)
        return calibration

    if not calibration.discriminating:
        message = (
            f"the selector's {calibration.regressions_total} measured failure(s) are "
            "all vacuous confirmations: in every case where something broke, the "
            "selector had skipped nothing, so it was never at risk of missing "
            "anything. A recall built out of those says only that the selector "
            "declined to narrow. Widen the study until at least one case both "
            "skips a scenario and breaks one."
        )
        if allow_uncalibrated:
            _warn_override(message)
            return calibration
        if strict:
            raise SelectorNotCalibratedError(message)
        LOGGER.warning("selector calibration is advisory only: %s", message)
        return calibration

    if calibration.passed(min_recall=min_recall):
        LOGGER.debug(
            "selector cleared the calibration gate: %s",
            "; ".join(calibration.summary_lines()[:4]),
        )
        return calibration

    recall = calibration.recall or 0.0
    message = (
        f"the selector missed {calibration.regressions_missed} of "
        f"{calibration.regressions_total} failure(s) the full suite caught "
        f"(recall {recall:.3f}, threshold {min_recall:.3f}; non-vacuous "
        f"{calibration.discriminating_total - calibration.discriminating_missed}"
        f"/{calibration.discriminating_total}). It may advise, but "
        f"it must not decide what does not run. Missed: "
        + ", ".join(f"{case}/{scenario}" for case, scenario in calibration.missed[:5])
    )
    if allow_uncalibrated:
        _warn_override(message)
        return calibration
    if strict:
        raise SelectorBelowThresholdError(message)
    LOGGER.warning("selector calibration is advisory only: %s", message)
    return calibration


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m lab.selection.calibrate`.

    Exit status is meaningful, because this is meant to gate:

    ==  ============================================================
    0   measured, and the recall cleared the threshold
    1   measured, and it did not — or it could not be measured at all
    ==  ============================================================

    Exit 1 for "not measurable" is the deliberate half. A tool that exits 0 when
    it learned nothing is a tool that gets wired into CI and then believed.
    """
    parser = argparse.ArgumentParser(
        prog="python -m lab.selection.calibrate",
        description="Measure the test selector's miss rate, with denominators.",
    )
    parser.add_argument(
        "--evidence",
        choices=("all", "history", "mutation"),
        default="all",
        help="which study counts toward the gate (default: all)",
    )
    parser.add_argument(
        "--skip-history",
        action="store_true",
        help="do not backtest real commits (the slow half)",
    )
    parser.add_argument(
        "--skip-mutation", action="store_true", help="do not run the mutation study"
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=None,
        help="backtest only the most recent N commit pairs",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=DEFAULT_SAMPLE,
        help=f"mutants to draw (default: {DEFAULT_SAMPLE})",
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="sampling seed, published"
    )
    parser.add_argument(
        "--enrich",
        type=int,
        default=DEFAULT_ENRICH,
        metavar="N",
        help=(
            "extra mutants drawn only from sites a trace observed, where the "
            f"selector is able to narrow (default: {DEFAULT_ENRICH}). Reported as "
            "its own stratum and excluded from the selection ratio."
        ),
    )
    parser.add_argument("--repo", default=str(REPO_ROOT), help="repository to measure")
    parser.add_argument("--map", default=str(DEFAULT_MAP_PATH), help="trace map path")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--write",
        nargs="?",
        const=str(DEFAULT_REPORT_PATH),
        default=None,
        metavar="PATH",
        help=f"record the measurement (default: {DEFAULT_REPORT_PATH})",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="no per-case progress on stderr"
    )
    args = parser.parse_args(argv)

    command = "python -m lab.selection.calibrate " + " ".join(argv or sys.argv[1:])

    history = mutation = None
    if not args.skip_history:
        if not args.quiet:
            print("history study: extracting and running each commit", file=sys.stderr)
        try:
            history = run_history_study(
                args.repo,
                limit=args.history_limit,
                map_path=args.map,
                progress=not args.quiet,
            )
        except GitUnavailable as exc:
            print(f"history study unavailable: {exc}", file=sys.stderr)
    if not args.skip_mutation:
        if not args.quiet:
            print("mutation study: one suite run per mutant", file=sys.stderr)
        mutation = run_mutation_study(
            args.repo,
            sample=args.sample,
            seed=args.seed,
            enrich=args.enrich,
            map_path=args.map,
            progress=not args.quiet,
        )

    head = subprocess.run(
        ["git", "-C", args.repo, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    calibration = aggregate(
        history=history,
        mutation=mutation,
        evidence=args.evidence,
        command=command.strip(),
        commit=head.stdout.strip() if head.returncode == 0 else "",
        repo=str(Path(args.repo).resolve()),
    )

    if args.write:
        target = Path(args.write)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(calibration.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {target}", file=sys.stderr)

    if args.json:
        print(json.dumps(calibration.to_dict(), indent=2, sort_keys=True))
    else:
        print("\n".join(calibration.summary_lines()))
        if history is not None:
            print("\nhistory study — what it established")
            print("\n".join(f"  {line}" for line in history.limitation()))
        if mutation is not None:
            print("\nmutation study — what it cannot establish")
            print("\n".join(f"  {line}" for line in mutation.limitation()))

    return 0 if calibration.passed() else 1


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
