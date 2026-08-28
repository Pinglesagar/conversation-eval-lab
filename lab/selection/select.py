"""Stage 3: join *what changed* to *what each scenario touched*, and say why.

Stage 1 (`lab.selection.diff`) answers "what changed", as symbols rather than
files. Stage 2 (`lab.selection.trace_map`) answers "what did each scenario
actually exercise", derived from committed traces rather than declared by hand.
This module is the join, and it is the only part of the layer that ever says the
word *skip*.

    python -m lab.selection --changed-since HEAD~1

Not an ``evallab`` subcommand: the selection layer is wired into the CLI
separately, once every stage has landed. Until then each stage carries its own
entry point.

Why this needs care
-------------------
A selector is a grader. It grades every scenario pass/fail on the question "can
this change possibly affect you", and a wrong *fail* means a regression ships
with a green run. That is the LLM-as-judge problem wearing different clothes,
and this repository already knows the answer: measure the grader, publish the
number with its denominator, and refuse to gate below threshold. See
``calibrate()`` and ``python -m lab.selection --calibrate``.

The one rule
------------
**When unsure, include.** Every ambiguity in this module resolves toward running
more, and each one is a value in an enum rather than a convention in a comment:

===========================================  ==================================
ambiguity                                    resolution
===========================================  ==================================
git unreachable / unparseable file            stage 1 raises a global trigger
a global trigger of any kind                  whole corpus
a changed symbol at a site no trace observed  whole corpus
a changed file stage 1 accounted for nowhere  whole corpus
the map is degraded, missing or unreadable    whole corpus
a scenario with no usable trace evidence      always selected
an override file that will not parse          whole corpus
an override naming a scenario that is gone    whole corpus
===========================================  ==================================

The mirror-image rule is that an *empty* result is stated, never implied. A diff
that changes nothing selects nothing, and says so in words — it does not hand
back a bare empty set that a caller might read as "the filter is off".

Two joins that are not obvious
------------------------------
*Nested qualnames.* Stage 1 reports a changed method as
``tablemate/agents.py::PolicyAgent.handle``. Stage 2 records the definition site
of the runtime name as ``tablemate/agents.py::PolicyAgent``. A literal string
intersection misses, which would send the commonest change of all — editing a
method body — to the whole corpus every time. ``_resolve_locations`` therefore
matches a changed qualname against an observed one when either is a dotted
ancestor of the other. A method is part of its class; that is not a guess.

*Module-level code.* ``path::<module>`` is import-time code, which runs for
everything defined in the file, so it selects every name observed in that path.
Stage 2 documents the same widening; a test pins the two implementations
together so a divergence fails loudly instead of quietly narrowing.

What this provably cannot catch
-------------------------------
A trace records what one recorded run *did*, so the map is a lower bound on the
dependency set and never an upper bound. It proves "touched X"; it cannot prove
"cannot touch Y". A config value read at run time, a prompt fragment shared
between two agents, a dependency that lives only in data — none of these emit an
event, so none of them appear. Version one is fully deterministic and does not
pretend otherwise: those cases fall back to selecting everything, and
``scenarios/selection_overrides.yaml`` is the documented seam for the ones a
human already knows about.

That file can only ever *widen*. There is no vocabulary in its schema for
removing a scenario, ``extra="forbid"`` turns an invented ``exclude:`` key into a
validation error rather than a silent no-op, ``_apply_overrides`` returns
additions and is never shown the base set, and ``select()`` asserts the
post-condition anyway. Three of those are structural and the fourth is a
backstop. Narrowing has to come from better derivation, never from a human
declaring that something is safe to skip — a declared map goes stale silently,
and a stale selector is worse than no selector.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lab.selection.diff import (
    MODULE_QUALNAME,
    ChangedSymbol,
    ChangeKind,
    ChangeSet,
    FileChange,
    SymbolKind,
    analyse_changes,
)
from lab.selection.trace_map import (
    DEFAULT_MAP_PATH,
    REPO_ROOT,
    TraceMap,
    load_trace_map,
)

__all__ = [
    "DEFAULT_OVERRIDES_PATH",
    "Calibration",
    "Escalation",
    "OverrideRule",
    "OverrideRules",
    "OverrideThen",
    "OverrideWhen",
    "ProbeResult",
    "Reason",
    "ReasonCode",
    "ScenarioDecision",
    "Selection",
    "SelectorInvariantError",
    "Verdict",
    "calibrate",
    "load_overrides",
    "main",
    "runner_corpus_ids",
    "select",
]

#: Where the additive override file lives. Absent is normal: no declared
#: widenings is the healthy state, and the file exists mainly to be documented.
DEFAULT_OVERRIDES_PATH: Path = REPO_ROOT / "scenarios" / "selection_overrides.yaml"

#: Names listed in a reason string before it is truncated.
_NAMES_IN_REASON = 5


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


class ReasonCode(str, Enum):
    """Why one scenario was included, or why it was left out.

    Codes rather than free text so a report can count them, and so a reviewer can
    see at a glance whether the selector is escalating for one good reason or for
    forty bad ones.
    """

    # ---- escalations: the whole corpus runs, and here is why
    GLOBAL_TRIGGER = "global-trigger"
    UNPLACEABLE_CHANGE = "unplaceable-change"
    UNACCOUNTED_FILE = "unaccounted-file"
    MAP_MISSING = "map-missing"
    MAP_UNREADABLE = "map-unreadable"
    MAP_DEGRADED = "map-degraded"
    OVERRIDES_UNREADABLE = "overrides-unreadable"
    OVERRIDE_UNKNOWN_TARGET = "override-unknown-target"
    OVERRIDE_EVERYTHING = "override-everything"
    CORPUS_UNREADABLE = "corpus-unreadable"
    DIRECT_UNKNOWN_SCENARIO = "direct-unknown-scenario"

    # ---- per-scenario inclusions
    WHOLE_CORPUS = "whole-corpus"
    UNMAPPED_SCENARIO = "unmapped-scenario"
    TRACE_DEPENDENCY = "trace-dependency"
    DIRECT_CHANGE = "direct-change"
    OVERRIDE = "override"

    # ---- per-scenario exclusions
    NO_OVERLAP = "no-overlap"
    NO_CHANGES = "no-changes"
    NO_RUNTIME_EFFECT = "no-runtime-effect"


#: The codes that mean "narrowing is not permitted". Membership is the test the
#: rest of the module runs, so adding an escalation reason to the enum without
#: adding it here is a visible omission rather than an invisible one.
ESCALATION_CODES: frozenset[ReasonCode] = frozenset(
    {
        ReasonCode.GLOBAL_TRIGGER,
        ReasonCode.UNPLACEABLE_CHANGE,
        ReasonCode.UNACCOUNTED_FILE,
        ReasonCode.MAP_MISSING,
        ReasonCode.MAP_UNREADABLE,
        ReasonCode.MAP_DEGRADED,
        ReasonCode.OVERRIDES_UNREADABLE,
        ReasonCode.OVERRIDE_UNKNOWN_TARGET,
        ReasonCode.OVERRIDE_EVERYTHING,
        ReasonCode.CORPUS_UNREADABLE,
        ReasonCode.DIRECT_UNKNOWN_SCENARIO,
    }
)


class Verdict(str, Enum):
    """The shape of the answer, before anyone reads the detail."""

    #: Narrowing refused. Every scenario runs.
    EVERYTHING = "everything"
    #: Narrowing permitted, and it narrowed.
    SUBSET = "subset"
    #: Nothing changed that can affect a run. Stated, not implied.
    NOTHING = "nothing"


class SelectorInvariantError(RuntimeError):
    """Raised when the additive-override post-condition is violated.

    Unreachable by construction — overrides are unioned in and are never shown
    the base set — which is exactly why it is checked. A selector that silently
    lost a scenario would be indistinguishable from one that worked.
    """


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Reason:
    """One code plus the sentence a human needs to agree or disagree with it."""

    code: ReasonCode
    detail: str

    def __str__(self) -> str:
        return f"{self.code.value}: {self.detail}"

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "detail": self.detail}


@dataclass(frozen=True)
class Escalation:
    """One reason the selector refuses to narrow.

    `subject` is the thing that caused it — a path, a location, `<map>` — so that
    a reader can go and look at it rather than trusting the sentence.
    """

    code: ReasonCode
    subject: str
    detail: str

    def __str__(self) -> str:
        return f"{self.subject} [{self.code.value}] {self.detail}"

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "subject": self.subject, "detail": self.detail}


@dataclass(frozen=True)
class ScenarioDecision:
    """What was decided about one scenario, and every reason behind it.

    A selected scenario carries at least one reason. An excluded one carries
    exactly one, because there is only ever one basis for exclusion: no evidence
    connected it to anything that changed.
    """

    scenario_id: str
    suite: str | None
    selected: bool
    reasons: tuple[Reason, ...] = ()

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(r.code.value for r in self.reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "suite": self.suite,
            "selected": self.selected,
            "reasons": [r.to_dict() for r in self.reasons],
        }


@dataclass(frozen=True)
class Selection:
    """The answer, with its workings attached.

    Read `verdict` first. `EVERYTHING` means the selector refused to narrow and
    `escalations` says why; `NOTHING` means the diff cannot affect a run and no
    scenario needs to; `SUBSET` is the only case in which anything was skipped,
    and `excluded_ids` plus `exclusion_summary()` are the part a reviewer should
    actually read. A selector that only shows what it included is unreviewable.
    """

    base_ref: str
    head_ref: str
    repo_root: str
    map_path: str
    overrides_path: str | None
    corpus_size: int
    verdict: Verdict
    decisions: tuple[ScenarioDecision, ...] = ()
    escalations: tuple[Escalation, ...] = ()
    matched_names: tuple[str, ...] = ()
    unplaceable_paths: tuple[str, ...] = ()
    overrides_fired: tuple[str, ...] = ()
    change_counts: Mapping[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------ views

    @property
    def selected(self) -> tuple[ScenarioDecision, ...]:
        return tuple(d for d in self.decisions if d.selected)

    @property
    def excluded(self) -> tuple[ScenarioDecision, ...]:
        return tuple(d for d in self.decisions if not d.selected)

    @property
    def selected_ids(self) -> tuple[str, ...]:
        return tuple(d.scenario_id for d in self.selected)

    @property
    def excluded_ids(self) -> tuple[str, ...]:
        return tuple(d.scenario_id for d in self.excluded)

    @property
    def is_everything(self) -> bool:
        return self.verdict is Verdict.EVERYTHING

    @property
    def is_nothing(self) -> bool:
        return self.verdict is Verdict.NOTHING

    def counts(self) -> dict[str, int]:
        """Every number this object reports, each next to its denominator."""
        by_reason: Counter[str] = Counter()
        for decision in self.selected:
            for reason in decision.reasons:
                by_reason[reason.code.value] += 1
        return {
            "corpus_size": self.corpus_size,
            "selected": len(self.selected),
            "excluded": len(self.excluded),
            "escalations": len(self.escalations),
            "matched_names": len(self.matched_names),
            "unplaceable_paths": len(self.unplaceable_paths),
            "overrides_fired": len(self.overrides_fired),
            **{f"selected_{code}": n for code, n in sorted(by_reason.items())},
        }

    def saved_fraction(self) -> tuple[int, int]:
        """`(skipped, corpus_size)`. A pair, never a naked percentage."""
        return len(self.excluded), self.corpus_size

    def exclusion_summary(self) -> list[str]:
        """Why the skipped scenarios were skipped, grouped so it can be read."""
        excluded = self.excluded
        if not excluded:
            if self.is_everything:
                return [
                    (
                        f"nothing excluded: {len(self.escalations)} "
                        "escalation(s) forced the whole corpus"
                    )
                ]
            return ["nothing excluded"]
        by_suite = Counter(d.suite or "<unknown>" for d in excluded)
        totals = Counter(d.suite or "<unknown>" for d in self.decisions)
        basis = Counter(
            r.code.value for d in excluded for r in d.reasons
        ) or Counter({ReasonCode.NO_OVERLAP.value: len(excluded)})
        lines = [
            f"{len(excluded)}/{self.corpus_size} excluded; basis: "
            + ", ".join(f"{code} {n}" for code, n in sorted(basis.items()))
        ]
        lines.append(
            "  by suite: "
            + ", ".join(
                f"{suite} {by_suite[suite]}/{totals[suite]}" for suite in sorted(by_suite)
            )
        )
        if self.matched_names:
            lines.append(
                "  every excluded scenario's committed evidence names none of: "
                + ", ".join(self.matched_names)
            )
        return lines

    # ------------------------------------------------------------- rendering

    def runner_args(self) -> list[str]:
        """Arguments for the runner's existing filters. No new execution path.

        `--scenario` and nothing else. The runner ANDs its filters, so emitting a
        `--suite` next to a `--scenario` could only ever shrink the set, and
        shrinking is the one direction this tool may not err in. Explicit ids are
        also exact: they do not depend on what the runner's default suite list
        happens to be today.

        The empty selection returns `[]`, and the CLI refuses to print it —
        `evallab run $(...)` with no arguments would run the whole suite, which
        is the precise opposite of what a `NOTHING` verdict means.
        """
        args: list[str] = []
        for scenario_id in self.selected_ids:
            args += ["--scenario", scenario_id]
        return args

    def partition_for_runner(
        self, runnable_ids: Collection[str]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Split the selection into what one runner can address, and what it cannot.

        Returns `(addressable, deferred)`, both in the selection's own order.

        `deferred` is *not* a skip. Those ids are outside the target runner's
        corpus, so that runner would not have run them under any arguments,
        including no arguments at all — there is no set of flags that makes
        `evallab run` drive an audio row. Withholding them from its argument list
        therefore removes nothing from that run; it only stops the whole command
        from aborting with "no such scenario(s)", which is what emitting them
        does today.

        What it *would* be is a skip against the audio runner, which is why the
        caller is required to report `deferred` rather than discard it. The
        partition is a fact; announcing it is the caller's obligation.
        """
        runnable = set(runnable_ids)
        addressable = tuple(i for i in self.selected_ids if i in runnable)
        deferred = tuple(i for i in self.selected_ids if i not in runnable)
        assert len(addressable) + len(deferred) == len(self.selected_ids)
        return addressable, deferred

    def explain(self, *, limit: int | None = 40) -> str:
        """The human-readable account: verdict, inclusions, and exclusions.

        `limit` caps the listings only. Counts above each listing are always the
        true totals, so a truncated report can never read as a smaller selection
        than actually happened.
        """
        n_sel, n_all = len(self.selected), self.corpus_size
        lines = [
            f"selection {self.base_ref}..{self.head_ref} in {self.repo_root}",
            f"  map        {self.map_path}",
            f"  corpus     {n_all} scenario(s)",
            "  change     "
            + ", ".join(
                f"{k.replace('_', ' ')} {v}"
                for k, v in sorted(self.change_counts.items())
                if k in {"files_changed", "symbols_changed", "global_triggers"}
            ),
            f"  verdict    {self.verdict.value.upper()} — {n_sel}/{n_all} selected",
        ]
        if self.overrides_path:
            fired = ", ".join(self.overrides_fired) if self.overrides_fired else "none fired"
            lines.append(f"  overrides  {self.overrides_path} ({fired})")

        if self.escalations:
            lines.append("")
            lines.append(f"escalations ({len(self.escalations)}) — narrowing refused:")
            shown = self.escalations if limit is None else self.escalations[:limit]
            lines += [f"  - {e}" for e in shown]
            if limit is not None and len(self.escalations) > limit:
                lines.append(f"  ... {len(self.escalations) - limit} more")

        if self.matched_names:
            lines.append("")
            lines.append(
                f"changed runtime names ({len(self.matched_names)}): "
                + ", ".join(self.matched_names)
            )

        lines.append("")
        lines.append(f"selected {n_sel}/{n_all}:")
        chosen = self.selected if limit is None else self.selected[:limit]
        for decision in chosen:
            why = "; ".join(str(r) for r in decision.reasons) or "no reason recorded"
            lines.append(f"  {decision.scenario_id}  [{decision.suite}]  {why}")
        if limit is not None and n_sel > limit:
            lines.append(f"  ... {n_sel - limit} more")

        lines.append("")
        lines.append("excluded:")
        lines += [f"  {line}" for line in self.exclusion_summary()]
        if self.verdict is Verdict.NOTHING:
            lines.append("")
            lines.append(
                "NOTHING TO RUN: this is an explicit empty selection, not an "
                "absent filter. Do not fall back to running the suite unfiltered."
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_ref": self.base_ref,
            "head_ref": self.head_ref,
            "repo_root": self.repo_root,
            "map_path": self.map_path,
            "overrides_path": self.overrides_path,
            "verdict": self.verdict.value,
            "corpus_size": self.corpus_size,
            "counts": self.counts(),
            "selected_ids": list(self.selected_ids),
            "excluded_ids": list(self.excluded_ids),
            "exclusion_summary": self.exclusion_summary(),
            "escalations": [e.to_dict() for e in self.escalations],
            "matched_names": list(self.matched_names),
            "unplaceable_paths": list(self.unplaceable_paths),
            "overrides_fired": list(self.overrides_fired),
            "change_counts": dict(self.change_counts),
            "runner_args": self.runner_args(),
            "decisions": [d.to_dict() for d in self.decisions],
        }


# --------------------------------------------------------------------------- #
# The override file — additive by construction
# --------------------------------------------------------------------------- #


class OverrideWhen(BaseModel):
    """What makes a rule fire. At least one clause, or the rule is meaningless.

    `paths` and `locations` are fnmatch globs so that a rule can cover a
    directory or a class without listing every member; `symbols` are exact
    runtime names, because a glob over agent names is far too easy to write by
    accident.
    """

    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_least_one(self) -> OverrideWhen:
        if not (self.paths or self.locations or self.symbols):
            raise ValueError("`when` needs at least one of paths, locations, symbols")
        return self


class OverrideThen(BaseModel):
    """What a fired rule ADDS. There is no vocabulary here for removal.

    That absence is the whole design. `extra="forbid"` means an invented
    `exclude:` or `skip:` key is a validation error the operator sees, not a key
    that is quietly ignored — and a rule that cannot be expressed cannot be
    honoured by accident.
    """

    model_config = ConfigDict(extra="forbid")

    scenarios: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    suites: list[str] = Field(default_factory=list)
    everything: bool = False

    @model_validator(mode="after")
    def _at_least_one(self) -> OverrideThen:
        if not (self.scenarios or self.tags or self.suites or self.everything):
            raise ValueError(
                "`then` needs at least one of scenarios, tags, suites, everything"
            )
        return self


class OverrideRule(BaseModel):
    """One declared widening, with the sentence justifying it.

    `reason` is required. A rule nobody can justify in a sentence is a rule
    nobody will dare delete later, and an override file that only grows is a
    selector that slowly becomes "run everything" without anyone deciding to.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    reason: str = Field(min_length=1)
    when: OverrideWhen
    then: OverrideThen


class OverrideRules(BaseModel):
    """The file. Empty is the healthy default."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    rules: list[OverrideRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> OverrideRules:
        seen = Counter(rule.id for rule in self.rules)
        duplicated = sorted(name for name, n in seen.items() if n > 1)
        if duplicated:
            raise ValueError(f"duplicate rule id(s): {', '.join(duplicated)}")
        return self


def load_overrides(
    path: Path | str = DEFAULT_OVERRIDES_PATH,
) -> tuple[OverrideRules, str | None]:
    """Read the override file. Returns `(rules, error)`; never raises.

    A missing file is not an error — no declared widenings is the normal state.
    A file that exists and will not parse *is*, and the caller escalates to the
    whole corpus: a broken widening file must not silently stop widening.
    """
    target = Path(path)
    if not target.is_file():
        return OverrideRules(), None
    try:
        import yaml

        document = yaml.safe_load(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - any read/parse failure is the same failure
        return OverrideRules(), f"could not read: {exc}"
    if document is None:
        return OverrideRules(), None
    if not isinstance(document, dict):
        return OverrideRules(), "top level is not a mapping"
    try:
        return OverrideRules.model_validate(document), None
    except Exception as exc:  # noqa: BLE001 - pydantic's message is the message
        return OverrideRules(), f"invalid: {_one_line(exc)}"


def _one_line(exc: Exception) -> str:
    return " / ".join(part.strip() for part in str(exc).splitlines() if part.strip())


# --------------------------------------------------------------------------- #
# The join
# --------------------------------------------------------------------------- #


def _related(observed: str, changed: str) -> bool:
    """True when a change at `changed` is a change to `observed`, or vice versa.

    Dotted-ancestor in either direction, within one file. `PolicyAgent.handle`
    is part of `PolicyAgent`; a change to `PolicyAgent` covers its methods. The
    prefix test carries the dot so that `check_policy_helper` is not mistaken for
    part of `check_policy`.
    """
    if observed == changed:
        return True
    return changed.startswith(f"{observed}.") or observed.startswith(f"{changed}.")


def _resolve_locations(
    trace_map: TraceMap, locations: Iterable[str]
) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
    """Translate stage 1 locations into stage 2 runtime names.

    Returns `(placed, unplaceable)`. `placed` maps each `path::qualname` that the
    map can attribute to the runtime names defined there; `unplaceable` are the
    locations the map has no evidence about at all.

    "No evidence about" is not "unrelated", which is why the unplaceable set is
    returned rather than dropped: the caller escalates on it.
    """
    by_path: dict[str, list[tuple[str, str]]] = {}
    for symbol in trace_map.symbols:
        for location in symbol.locations:
            path, _, qualname = location.partition("::")
            by_path.setdefault(path, []).append((qualname, symbol.name))

    placed: dict[str, tuple[str, ...]] = {}
    unplaceable: list[str] = []
    for location in locations:
        path, _, qualname = location.partition("::")
        sites = by_path.get(path, ())
        if qualname == MODULE_QUALNAME:
            hits = {name for _, name in sites}
        else:
            hits = {name for observed, name in sites if _related(observed, qualname)}
        if hits:
            placed[location] = tuple(sorted(hits))
        else:
            unplaceable.append(location)
    return placed, tuple(sorted(unplaceable))


def _unaccounted_files(change_set: ChangeSet) -> tuple[str, ...]:
    """Changed files stage 1 neither classified as inert nor attributed to anything.

    Stage 1 is believed to account for every file, so this should always be
    empty. It is checked because "should always be empty" is what an assumption
    sounds like just before it stops being true, and the failure mode of an
    unnoticed unaccounted file is a silently skipped scenario.
    """
    accounted = set(change_set.inert)
    accounted |= {s.path for s in change_set.symbols}
    accounted |= {s.path for s in change_set.scenarios}
    accounted |= {g.path for g in change_set.globals}
    return tuple(
        sorted({f.path for f in change_set.files} - accounted)
    )


def _names_phrase(names: Sequence[str]) -> str:
    if len(names) <= _NAMES_IN_REASON:
        return ", ".join(names)
    head = ", ".join(names[:_NAMES_IN_REASON])
    return f"{head} (+{len(names) - _NAMES_IN_REASON} more)"


def _corpus_tags() -> dict[str, set[str]]:
    """`scenario_id -> tags`, every suite. Imported lazily; offline; no credentials."""
    from scenarios.loader import ALL_SUITES, load_corpus

    corpus = load_corpus(suites=ALL_SUITES)
    return {s.id: set(s.all_tags()) for s in corpus.scenarios}


def runner_corpus_ids() -> frozenset[str] | None:
    """The scenario ids `evallab run` drives by default, or `None` if unknowable.

    This selector reasons over `ALL_SUITES` — every suite, the audio tier
    included — because a scenario the selector cannot see is a scenario it cannot
    protect. The *runner* is narrower: `lab.cli` calls `loader.load_corpus()` with
    no `suites` argument, which is the four text suites. The audio tier is driven
    by its own command and its ids are not addressable through `evallab run` at
    all — `--scenario audio-…` is rejected as "no such scenario", with or without
    a matching `--suite`.

    So a selection is not, in general, expressible as one runner invocation, and
    `--runner-args` has to say which half it is emitting. This function supplies
    the other half's boundary by making the *same call the runner makes*, rather
    than by naming suites here: if the runner's default corpus widens tomorrow,
    this widens with it and nothing needs editing.

    `None` means the corpus could not be loaded. That is not an empty corpus and
    must never be treated as one — the caller declines to emit anything, which
    leaves the runner unfiltered and running more, not less.
    """
    try:
        from scenarios.loader import load_corpus

        return frozenset(s.id for s in load_corpus().scenarios)
    except Exception:  # noqa: BLE001 - any loader failure means the same thing
        return None


@dataclass
class _Widening:
    """Additions only. This type has no way to express a removal."""

    ids: dict[str, list[Reason]] = field(default_factory=dict)
    fired: list[str] = field(default_factory=list)
    escalations: list[Escalation] = field(default_factory=list)

    def add(self, scenario_id: str, reason: Reason) -> None:
        self.ids.setdefault(scenario_id, []).append(reason)


def _apply_overrides(
    rules: OverrideRules,
    *,
    change_set: ChangeSet,
    matched_names: Sequence[str],
    universe: Mapping[str, str | None],
    tags_for: Mapping[str, set[str]] | None,
) -> _Widening:
    """Compute what the override file ADDS. It is never shown the base selection.

    That is the structural half of "an override cannot remove": this function
    cannot subtract from a set it has never seen, and it returns additions rather
    than a replacement. `select()` unions the result and then asserts the
    post-condition, which is the belt to this braces.
    """
    widening = _Widening()
    if not rules.rules:
        return widening

    changed_paths = {f.path for f in change_set.files}
    changed_paths |= {f.old_path for f in change_set.files if f.old_path}
    locations = set(change_set.locations)
    names = set(matched_names)

    for rule in rules.rules:
        trigger = _rule_trigger(rule, changed_paths, locations, names)
        if trigger is None:
            continue
        widening.fired.append(rule.id)
        detail = f"rule {rule.id} ({trigger}): {rule.reason}"

        if rule.then.everything:
            widening.escalations.append(
                Escalation(
                    code=ReasonCode.OVERRIDE_EVERYTHING,
                    subject=f"override:{rule.id}",
                    detail=detail,
                )
            )
            continue

        wanted: set[str] = set(rule.then.scenarios)
        unknown = sorted(wanted - set(universe))
        if unknown:
            widening.escalations.append(
                Escalation(
                    code=ReasonCode.OVERRIDE_UNKNOWN_TARGET,
                    subject=f"override:{rule.id}",
                    detail=(
                        f"names {len(unknown)} scenario(s) not in the corpus "
                        f"({', '.join(unknown[:5])}); a stale widening is still an "
                        "ambiguity, so the whole corpus runs"
                    ),
                )
            )
            continue

        if rule.then.suites:
            wanted |= {
                sid for sid, suite in universe.items() if suite in set(rule.then.suites)
            }
        if rule.then.tags:
            if tags_for is None:
                widening.escalations.append(
                    Escalation(
                        code=ReasonCode.CORPUS_UNREADABLE,
                        subject=f"override:{rule.id}",
                        detail=(
                            "selects by tag, but the corpus could not be loaded to "
                            "resolve tags"
                        ),
                    )
                )
                continue
            wanted |= {
                sid
                for sid, tags in tags_for.items()
                if tags & set(rule.then.tags) and sid in universe
            }

        for scenario_id in sorted(wanted):
            widening.add(scenario_id, Reason(ReasonCode.OVERRIDE, detail))
    return widening


def _rule_trigger(
    rule: OverrideRule,
    changed_paths: set[str],
    locations: set[str],
    names: set[str],
) -> str | None:
    """The first clause that fired, in words, or None."""
    for pattern in rule.when.paths:
        hits = sorted(p for p in changed_paths if fnmatch.fnmatch(p, pattern))
        if hits:
            return f"path {hits[0]} matched {pattern!r}"
    for pattern in rule.when.locations:
        hits = sorted(loc for loc in locations if fnmatch.fnmatch(loc, pattern))
        if hits:
            return f"location {hits[0]} matched {pattern!r}"
    hit_names = sorted(names & set(rule.when.symbols))
    if hit_names:
        return f"runtime name {hit_names[0]} changed"
    return None


def select(
    base_ref: str = "HEAD~1",
    *,
    head_ref: str | None = None,
    repo_root: str | Path | None = None,
    change_set: ChangeSet | None = None,
    trace_map: TraceMap | None = None,
    map_path: Path | str = DEFAULT_MAP_PATH,
    overrides: OverrideRules | None = None,
    overrides_path: Path | str | None = DEFAULT_OVERRIDES_PATH,
    tags_for: Mapping[str, set[str]] | None = None,
) -> Selection:
    """Which scenarios could this change possibly affect, and why each one.

    Deterministic and offline. Reads a git diff, a committed JSON artefact and
    optionally one YAML file; opens no network connection and needs no
    credential, so a clean clone with every key unset behaves identically.

    Every argument that names an input can be passed directly instead, which is
    what makes the fail-safe paths testable: a caller can hand in a deliberately
    broken `ChangeSet` or a degraded `TraceMap` and watch the answer widen.
    """
    escalations: list[Escalation] = []

    # ---- stage 1
    if change_set is None:
        change_set = analyse_changes(base_ref, head_ref=head_ref, repo_root=repo_root)
    head_label = change_set.head_ref

    # ---- did anything change at all?
    #
    # Computed first, and every escalation below is gated on it, because an
    # unreachable git produces a ChangeSet with a global trigger and *no files*.
    # Reading "no files" as "nothing to run" before looking at the triggers would
    # turn the loudest failure stage 1 has into a silent empty selection — the
    # one unrecoverable mistake this tool can make.
    empty_reason: ReasonCode | None
    if change_set.globals or change_set.symbols or change_set.scenarios:
        empty_reason = None
    elif not change_set.files:
        empty_reason = ReasonCode.NO_CHANGES
    elif len(change_set.inert) == len(change_set.files):
        empty_reason = ReasonCode.NO_RUNTIME_EFFECT
    else:
        # files changed that stage 1 attributed to nothing: not empty, ambiguous.
        empty_reason = None

    # ---- stage 2
    map_label = str(map_path)
    if trace_map is None:
        target = Path(map_path)
        if not target.is_file():
            trace_map = TraceMap(
                corpus_size=0,
                mapped_count=0,
                unmapped_count=0,
                session_count=0,
                trace_file_count=0,
                degraded=True,
            )
            if empty_reason is None:
                escalations.append(
                    Escalation(
                        code=ReasonCode.MAP_MISSING,
                        subject=map_label,
                        detail=(
                            "no dependency map, so nothing may be skipped; "
                            "run: python -m lab.selection.trace_map --write"
                        ),
                    )
                )
        else:
            try:
                trace_map = load_trace_map(target)
            except Exception as exc:  # noqa: BLE001 - any failure means the same thing
                trace_map = TraceMap(
                    corpus_size=0,
                    mapped_count=0,
                    unmapped_count=0,
                    session_count=0,
                    trace_file_count=0,
                    degraded=True,
                )
                if empty_reason is None:
                    escalations.append(
                        Escalation(
                            code=ReasonCode.MAP_UNREADABLE,
                            subject=map_label,
                            detail=f"could not be read, so nothing may be skipped: {exc}",
                        )
                    )

    universe: dict[str, str | None] = {
        entry.scenario_id: entry.suite for entry in trace_map.scenarios
    }
    if trace_map.degraded and not escalations and empty_reason is None:
        escalations.append(
            Escalation(
                code=ReasonCode.MAP_DEGRADED,
                subject=map_label,
                detail=(
                    "the map could not read every candidate trace, so the missing "
                    f"evidence may have been the only link to the change: "
                    f"{'; '.join(trace_map.degraded_reasons[:3]) or 'see the artefact'}"
                ),
            )
        )

    # ---- overrides
    overrides_label: str | None = None
    if overrides is None:
        if overrides_path is None:
            overrides = OverrideRules()
        else:
            overrides_label = str(overrides_path)
            overrides, error = load_overrides(overrides_path)
            if error is not None and empty_reason is None:
                escalations.append(
                    Escalation(
                        code=ReasonCode.OVERRIDES_UNREADABLE,
                        subject=overrides_label,
                        detail=(
                            f"{error}; a widening file that will not parse cannot be "
                            "assumed empty"
                        ),
                    )
                )
    elif overrides_path is not None:
        overrides_label = str(overrides_path)

    # ---- the join
    placed, unplaceable = _resolve_locations(trace_map, change_set.locations)
    matched_names = tuple(sorted({n for names in placed.values() for n in names}))

    unplaceable_by_path: Counter[str] = Counter(
        loc.partition("::")[0] for loc in unplaceable
    )
    if empty_reason is None:
        for path, n in sorted(unplaceable_by_path.items()):
            escalations.append(
                Escalation(
                    code=ReasonCode.UNPLACEABLE_CHANGE,
                    subject=path,
                    detail=(
                        f"{n} changed symbol(s) here; no committed trace observed any "
                        "agent or tool defined in this file, so the map has no "
                        "evidence about it either way"
                    ),
                )
            )
        for trigger in change_set.globals:
            escalations.append(
                Escalation(
                    code=ReasonCode.GLOBAL_TRIGGER,
                    subject=trigger.path,
                    detail=f"{trigger.reason.value}: {trigger.detail}",
                )
            )
        for path in _unaccounted_files(change_set):
            escalations.append(
                Escalation(
                    code=ReasonCode.UNACCOUNTED_FILE,
                    subject=path,
                    detail=(
                        "changed, but stage 1 attributed it to no symbol, scenario "
                        "or trigger. Usually this means only comments or formatting "
                        "moved, which an AST comparison cannot see — but a gap in "
                        "the classifier looks identical from here, and the selector "
                        "will not act on a guess about which one it is"
                    ),
                )
            )
        # A directly-touched scenario id the corpus does not contain. In practice
        # this is a per-repeat trace name (`<id>-0`, `<id>-1`) or a scenario the
        # map has not been regenerated for. Either way it is a name the selector
        # cannot resolve to a row, and stripping the suffix to guess which row it
        # meant is the kind of guess that can only ever exclude the wrong thing.
        # It is also unusable downstream: the runner rejects an unknown id
        # outright, so emitting it would turn a selection into a crash.
        unknown_direct = sorted(
            {s.scenario_id for s in change_set.scenarios} - set(universe)
        )
        if unknown_direct:
            escalations.append(
                Escalation(
                    code=ReasonCode.DIRECT_UNKNOWN_SCENARIO,
                    subject="<corpus>",
                    detail=(
                        f"{len(unknown_direct)} directly-changed scenario id(s) are "
                        f"not corpus rows ({', '.join(unknown_direct[:5])}"
                        f"{', ...' if len(unknown_direct) > 5 else ''}); the map may "
                        "be stale — run: python -m lab.selection.trace_map --check"
                    ),
                )
            )

    # ---- overrides fire against the change, not against the selection
    if tags_for is None and any(rule.then.tags for rule in overrides.rules):
        try:
            tags_for = _corpus_tags()
        except Exception as exc:  # noqa: BLE001 - offline, but still fail safe
            tags_for = None
            escalations.append(
                Escalation(
                    code=ReasonCode.CORPUS_UNREADABLE,
                    subject="scenarios/",
                    detail=f"tags could not be resolved: {_one_line(exc)}",
                )
            )
    widening = _apply_overrides(
        overrides,
        change_set=change_set,
        matched_names=matched_names,
        universe=universe,
        tags_for=tags_for,
    )
    if empty_reason is None or widening.fired:
        escalations.extend(widening.escalations)

    # ---- assemble
    if escalations:
        verdict = Verdict.EVERYTHING
        reason = Reason(
            ReasonCode.WHOLE_CORPUS,
            f"{len(escalations)} escalation(s) refused narrowing; see 'escalations'",
        )
        # Only corpus rows. An id the corpus does not contain has already
        # escalated above, and passing it to the runner's `--scenario` would make
        # the runner exit rather than run: the denominator stays the corpus, and
        # every emitted id is one the runner will accept.
        decisions = tuple(
            ScenarioDecision(
                scenario_id=sid,
                suite=universe.get(sid),
                selected=True,
                reasons=(reason,),
            )
            for sid in sorted(universe)
        )
        return Selection(
            base_ref=change_set.base_ref,
            head_ref=head_label,
            repo_root=change_set.repo_root,
            map_path=map_label,
            overrides_path=overrides_label,
            corpus_size=len(universe),
            verdict=verdict,
            decisions=decisions,
            escalations=tuple(escalations),
            matched_names=matched_names,
            unplaceable_paths=tuple(sorted(unplaceable_by_path)),
            overrides_fired=tuple(widening.fired),
            change_counts=change_set.counts(),
        )

    base_reasons: dict[str, list[Reason]] = {}
    if empty_reason is None:
        for scenario_id in sorted(trace_map.always_run_ids()):
            entry = trace_map.by_id(scenario_id)
            base_reasons.setdefault(scenario_id, []).append(
                Reason(
                    ReasonCode.UNMAPPED_SCENARIO,
                    entry.unmapped_reason or "no usable trace evidence; always run",
                )
            )
        wanted = set(matched_names)
        for entry in trace_map.scenarios:
            if not entry.mapped:
                continue
            hit = sorted(wanted & set(entry.symbols()))
            if hit:
                base_reasons.setdefault(entry.scenario_id, []).append(
                    Reason(
                        ReasonCode.TRACE_DEPENDENCY,
                        f"committed evidence names {_names_phrase(hit)}",
                    )
                )
        for touched in change_set.scenarios:
            base_reasons.setdefault(touched.scenario_id, []).append(
                Reason(
                    ReasonCode.DIRECT_CHANGE,
                    f"{touched.path} [{touched.change.value}] {touched.reason}",
                )
            )

    # additive-only: union, then prove the union added and never removed
    final: dict[str, list[Reason]] = {k: list(v) for k, v in base_reasons.items()}
    for scenario_id, reasons in widening.ids.items():
        final.setdefault(scenario_id, []).extend(reasons)
    if not set(base_reasons) <= set(final):  # pragma: no cover - unreachable by design
        raise SelectorInvariantError(
            "overrides removed "
            f"{sorted(set(base_reasons) - set(final))} from the selection; "
            "the override layer is additive and this must never happen"
        )

    known = set(universe)
    rows: list[ScenarioDecision] = []
    for scenario_id in sorted(known | set(final)):
        reasons = tuple(final.get(scenario_id, ()))
        if reasons:
            rows.append(
                ScenarioDecision(
                    scenario_id=scenario_id,
                    suite=universe.get(scenario_id),
                    selected=True,
                    reasons=reasons,
                )
            )
        else:
            detail = {
                ReasonCode.NO_CHANGES: "no file changed between the two refs",
                ReasonCode.NO_RUNTIME_EFFECT: (
                    "every changed file is inert at run time (documentation and text)"
                ),
            }.get(
                empty_reason or ReasonCode.NO_OVERLAP,
                "mapped, and its committed evidence names nothing that changed",
            )
            rows.append(
                ScenarioDecision(
                    scenario_id=scenario_id,
                    suite=universe.get(scenario_id),
                    selected=False,
                    reasons=(Reason(empty_reason or ReasonCode.NO_OVERLAP, detail),),
                )
            )

    selected_any = any(row.selected for row in rows)
    verdict = Verdict.SUBSET if selected_any else Verdict.NOTHING
    return Selection(
        base_ref=change_set.base_ref,
        head_ref=head_label,
        repo_root=change_set.repo_root,
        map_path=map_label,
        overrides_path=overrides_label,
        corpus_size=len(universe),
        verdict=verdict,
        decisions=tuple(rows),
        escalations=(),
        matched_names=matched_names,
        unplaceable_paths=(),
        overrides_fired=tuple(widening.fired),
        change_counts=change_set.counts(),
    )


# --------------------------------------------------------------------------- #
# Calibration — the selector is a grader, so it carries a measured number
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProbeResult:
    """One synthetic single-symbol change, and what the selector did with it."""

    location: str
    name: str
    selected: int
    expected: int
    missing: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "name": self.name,
            "selected": self.selected,
            "expected": self.expected,
            "missing": list(self.missing),
            "ok": self.ok,
        }


@dataclass(frozen=True)
class Calibration:
    """How often the selector keeps a scenario the evidence implicates.

    What this measures, exactly: for every runtime name the map has observed, a
    single-symbol change is synthesised at its definition site — once as the
    plain qualname and once nested one level deeper, which is what stage 1 emits
    for a method body — and the selection is checked against the set of scenarios
    whose committed traces name that symbol. Recall of 1 means the selector never
    dropped a scenario the evidence implicates.

    What it does NOT measure, and the reason the number is published with this
    sentence attached: the evidence itself. The map is a lower bound on the
    dependency set, so a scenario that *would* reach a symbol on some other input
    leaves no event and is in neither the expectation nor the selection. This
    calibration proves the join is sound with respect to what was recorded. It
    cannot prove the recording is complete, and no deterministic stage can.

    `controls` guard the other direction: changes the selector must refuse to
    narrow on. A selector that scored a perfect recall by always selecting
    everything would be useless, so `mean_selected` is reported next to the
    recall and the controls confirm that the whole-corpus answer is reserved for
    the cases that earn it.
    """

    corpus_size: int
    probes: tuple[ProbeResult, ...]
    pairs_total: int
    pairs_preserved: int
    floor_total: int
    floor_preserved: int
    selected_total: int
    controls_total: int
    controls_passed: int
    control_failures: tuple[str, ...]

    @property
    def recall(self) -> float:
        """Preserved evidence pairs over total. 1.0 or the selector must not gate."""
        return 1.0 if self.pairs_total == 0 else self.pairs_preserved / self.pairs_total

    @property
    def mean_selected(self) -> float:
        return 0.0 if not self.probes else self.selected_total / len(self.probes)

    def passed(self, *, min_recall: float = 1.0) -> bool:
        return (
            self.recall >= min_recall
            and self.controls_passed == self.controls_total
            and self.floor_preserved == self.floor_total
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_size": self.corpus_size,
            "probes": len(self.probes),
            "pairs_preserved": self.pairs_preserved,
            "pairs_total": self.pairs_total,
            "recall": self.recall,
            "floor_preserved": self.floor_preserved,
            "floor_total": self.floor_total,
            "controls_passed": self.controls_passed,
            "controls_total": self.controls_total,
            "control_failures": list(self.control_failures),
            "mean_selected": self.mean_selected,
            "corpus_skipped_mean": self.corpus_size - self.mean_selected,
            "probe_detail": [p.to_dict() for p in self.probes],
        }

    def summary_lines(self) -> list[str]:
        """Every rate beside its denominator. A naked percentage is a defect."""
        saved = self.corpus_size - self.mean_selected
        pct = 0.0 if not self.corpus_size else 100.0 * saved / self.corpus_size
        return [
            "selector calibration (deterministic, offline)",
            f"  probes                  {len(self.probes)} synthetic single-symbol changes",
            (
                f"  evidence pairs kept     {self.pairs_preserved}/{self.pairs_total}"
                f"   (recall {self.recall:.3f})"
            ),
            f"  always-run floor kept   {self.floor_preserved}/{self.floor_total}",
            f"  controls passed         {self.controls_passed}/{self.controls_total}",
            (
                f"  mean selection          {self.mean_selected:.1f}/{self.corpus_size}"
                f"   ({saved:.1f}/{self.corpus_size} skipped, {pct:.1f}%)"
            ),
            (
                "  measures the join against the recorded evidence, not the "
                "evidence against reality"
            ),
        ]


def _probe_change_set(location: str, repo_root: str) -> ChangeSet:
    """A ChangeSet holding exactly one changed symbol at `location`."""
    path, _, qualname = location.partition("::")
    kind = SymbolKind.MODULE if qualname == MODULE_QUALNAME else SymbolKind.FUNCTION
    return ChangeSet(
        base_ref="<calibration>",
        head_ref="<calibration>",
        repo_root=repo_root,
        files=(FileChange(path=path, change=ChangeKind.MODIFIED),),
        symbols=(
            ChangedSymbol(
                path=path,
                qualname=qualname,
                kind=kind,
                change=ChangeKind.MODIFIED,
                reason="synthetic calibration probe",
            ),
        ),
    )


def calibrate(
    *,
    trace_map: TraceMap | None = None,
    map_path: Path | str = DEFAULT_MAP_PATH,
) -> Calibration:
    """Backtest the selector against the map's own evidence. Offline and exact.

    Overrides are deliberately excluded from the probes: the number must describe
    the derived join, not whatever widenings a particular checkout happens to
    declare, or a site could paper over a broken selector with one `everything:
    true` rule and still publish a perfect recall.
    """
    if trace_map is None:
        trace_map = load_trace_map(map_path)
    corpus_size = len(trace_map.scenarios)
    floor = trace_map.always_run_ids()

    probes: list[ProbeResult] = []
    pairs_total = pairs_preserved = 0
    floor_total = floor_preserved = 0
    selected_total = 0

    for symbol in trace_map.symbols:
        expected_users = set(trace_map.scenarios_using(symbol.name))
        for site in symbol.locations:
            path, _, qualname = site.partition("::")
            for location in (site, f"{path}::{qualname}.__probe__"):
                selection = select(
                    change_set=_probe_change_set(location, "<calibration>"),
                    trace_map=trace_map,
                    overrides=OverrideRules(),
                    overrides_path=None,
                    map_path=map_path,
                )
                chosen = set(selection.selected_ids)
                expected = expected_users | floor
                missing = tuple(sorted(expected - chosen))
                probes.append(
                    ProbeResult(
                        location=location,
                        name=symbol.name,
                        selected=len(chosen),
                        expected=len(expected),
                        missing=missing,
                    )
                )
                pairs_total += len(expected_users)
                pairs_preserved += len(expected_users & chosen)
                floor_total += len(floor)
                floor_preserved += len(floor & chosen)
                selected_total += len(chosen)

    controls: list[tuple[str, bool]] = []

    everything = set(trace_map.ids())

    unknown = select(
        change_set=_probe_change_set(
            "tablemate/__no_such_file__.py::whatever", "<calibration>"
        ),
        trace_map=trace_map,
        overrides=OverrideRules(),
        overrides_path=None,
        map_path=map_path,
    )
    controls.append(
        ("a change the map cannot place selects everything", set(unknown.selected_ids) == everything)
    )

    empty = select(
        change_set=ChangeSet(
            base_ref="<calibration>", head_ref="<calibration>", repo_root="<calibration>"
        ),
        trace_map=trace_map,
        overrides=OverrideRules(),
        overrides_path=None,
        map_path=map_path,
    )
    controls.append(
        (
            "an empty diff selects nothing and says so",
            empty.verdict is Verdict.NOTHING and not empty.selected_ids,
        )
    )

    degraded_map = trace_map.model_copy(
        update={"degraded": True, "degraded_reasons": ["calibration control"]}
    )
    degraded = select(
        change_set=_probe_change_set("tablemate/tools.py::check_policy", "<calibration>"),
        trace_map=degraded_map,
        overrides=OverrideRules(),
        overrides_path=None,
        map_path=map_path,
    )
    controls.append(
        ("a degraded map selects everything", set(degraded.selected_ids) == everything)
    )

    narrowed = [p for p in probes if p.selected < corpus_size]
    controls.append(
        (
            "at least one probe narrows, so the recall is not bought by selecting all",
            bool(narrowed),
        )
    )

    return Calibration(
        corpus_size=corpus_size,
        probes=tuple(probes),
        pairs_total=pairs_total,
        pairs_preserved=pairs_preserved,
        floor_total=floor_total,
        floor_preserved=floor_preserved,
        selected_total=selected_total,
        controls_total=len(controls),
        controls_passed=sum(1 for _, ok in controls if ok),
        control_failures=tuple(name for name, ok in controls if not ok),
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _emit_runner_args(selection: Selection) -> int:
    """Print `--scenario` arguments for `evallab run`, and account for the rest.

    A selection spans `ALL_SUITES`; `evallab run` drives only its default corpus.
    Emitting an id that runner cannot address does not narrow the run — it aborts
    the whole command with "no such scenario(s)", so *nothing* runs. Emitting the
    full list is therefore not the conservative choice it looks like; it is the
    choice that produces a zero-scenario run and an operator who deletes ids by
    hand until the command starts, which is how an always-run floor gets quietly
    deleted.

    So: emit what this runner can address, and report the remainder loudly on
    stderr with the fact that it needs its own command. Never drop it silently.

    Two refusals, both leaving the runner *unfiltered* — which over-runs, and
    over-running is the safe direction:

    * the corpus will not load, so the boundary is unknown;
    * every selected id is outside this runner's corpus, so there is no honest
      argument list to print.

    Both print nothing to stdout and exit 2.
    """
    runnable = runner_corpus_ids()
    if runnable is None:
        print(
            "refusing to emit arguments: the runner's own corpus would not load, "
            "so which of the "
            f"{len(selection.selected_ids)} selected scenario(s) it can address "
            "is unknown. Run the suite unfiltered.",
            file=sys.stderr,
        )
        return 2

    addressable, deferred = selection.partition_for_runner(runnable)

    if deferred:
        print(
            f"note: {len(deferred)}/{len(selection.selected_ids)} selected "
            "scenario(s) are outside the corpus `evallab run` loads by default "
            "and cannot be addressed by any --scenario argument. They are NOT in "
            "the arguments below and they still need running, by the command that "
            "owns their tier:\n  "
            + _names_phrase(deferred),
            file=sys.stderr,
        )

    if not addressable:
        print(
            "refusing to emit arguments: none of the "
            f"{len(selection.selected_ids)} selected scenario(s) is in this "
            "runner's corpus, so an empty argument list would silently become an "
            "unfiltered run. Run the deferred scenarios named above instead.",
            file=sys.stderr,
        )
        return 2

    print(" ".join(arg for i in addressable for arg in ("--scenario", i)))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m lab.selection --changed-since HEAD~1`.

    Exit status is meaningful, because this is meant to be used in a shell:

    ==  ============================================================
    0   a selection was produced (`EVERYTHING` and `SUBSET` alike)
    1   `--calibrate` measured below threshold: do not gate on this
    2   `NOTHING` to run
    ==  ============================================================

    Status 2 exists so that `evallab run $(python -m lab.selection --runner-args)`
    cannot degrade into an unfiltered run: an empty argument list would select the
    whole suite, which is the opposite of what an empty selection means. In
    `--runner-args` mode nothing is printed to stdout in that case.
    """
    parser = argparse.ArgumentParser(
        prog="python -m lab.selection",
        description=(
            "Which scenarios can this change possibly affect — and why each one."
        ),
        epilog=(
            "Not an evallab subcommand: the selection layer is wired into the CLI "
            "separately, once every stage has landed."
        ),
    )
    parser.add_argument(
        "--changed-since",
        default="HEAD~1",
        metavar="REF",
        help="base ref to compare against (default: HEAD~1)",
    )
    parser.add_argument(
        "--head",
        default=None,
        metavar="REF",
        help="head ref (default: the working tree, untracked files included)",
    )
    parser.add_argument("--repo", default=None, help="repository root (default: cwd)")
    parser.add_argument(
        "--map",
        default=str(DEFAULT_MAP_PATH),
        help="trace-derived dependency map (default: the committed artefact)",
    )
    parser.add_argument(
        "--overrides",
        default=str(DEFAULT_OVERRIDES_PATH),
        help="additive override file (default: scenarios/selection_overrides.yaml)",
    )
    parser.add_argument(
        "--no-overrides", action="store_true", help="ignore the override file entirely"
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="emit the full result as JSON")
    output.add_argument(
        "--runner-args",
        action="store_true",
        help="emit --scenario arguments for the existing runner, one line, shell-safe",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=40,
        help="rows to list in the text report; counts are always the true totals",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="backtest the selector against the map's evidence and report recall",
    )
    parser.add_argument(
        "--min-recall",
        type=float,
        default=1.0,
        help="refuse to pass below this recall (default: 1.0)",
    )
    args = parser.parse_args(argv)

    if args.calibrate:
        measured = calibrate(map_path=args.map)
        if args.json:
            print(json.dumps(measured.to_dict(), indent=2, sort_keys=True))
        else:
            print("\n".join(measured.summary_lines()))
            for probe in measured.probes:
                if not probe.ok:
                    print(f"  MISSED {probe.location}: {', '.join(probe.missing)}")
            for failure in measured.control_failures:
                print(f"  CONTROL FAILED: {failure}")
            print(
                f"  verdict                 "
                f"{'PASS' if measured.passed(min_recall=args.min_recall) else 'FAIL'}"
                f" (threshold {args.min_recall:.3f})"
            )
        return 0 if measured.passed(min_recall=args.min_recall) else 1

    selection = select(
        args.changed_since,
        head_ref=args.head,
        repo_root=args.repo,
        map_path=args.map,
        overrides_path=None if args.no_overrides else args.overrides,
    )

    if args.runner_args:
        if selection.is_nothing:
            print(
                "nothing to run: no changed file can affect a scenario "
                f"(0/{selection.corpus_size} selected)",
                file=sys.stderr,
            )
            return 2
        return _emit_runner_args(selection)

    if args.json:
        print(json.dumps(selection.to_dict(), indent=2, sort_keys=True))
    else:
        print(selection.explain(limit=args.limit))
    return 2 if selection.is_nothing else 0


if __name__ == "__main__":  # pragma: no cover - exercised through main() in tests
    raise SystemExit(main())
