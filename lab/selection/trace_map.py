"""The trace-derived dependency map: which scenario exercises which code.

WHAT THIS DEMONSTRATES
----------------------
A production suite of thousands of conversational scenarios costs real money and
real wall-clock time to run. Most changes touch a small area — one agent, one
tool, one prompt string. Running everything every time is waste; running a guess
is negligence. The way out is to know which scenarios a given change *can
possibly* affect, run those live, and replay the rest for free.

In a normal test suite that map has to be *declared*: someone writes
`@pytest.mark.affects("booking")` and someone else forgets to update it. That is
the failure mode this module exists to avoid, because the person who writes the
metadata is never the person who breaks it. Here the map is **derived**, from
evidence that already exists on disk.

It is derivable because this repository is trace-first. Every run writes a
`Trace` — an ordered stream of typed events (`lab.trace.schema`) — and those
traces are committed under `fixtures/`. A trace records, per session, which
sub-agent spoke each turn, which tools were called, which handoffs fired, and
which concrete engine produced each event. That is a dependency graph nobody had
to write down. This module reads it back and turns it into a committed artefact.

WHAT A TRACE CAN TELL YOU, AND WHAT IT CANNOT
---------------------------------------------
This is the central limitation and everything downstream is shaped by it.

A trace is a record of what one recorded run **did**. It is not a record of what
the scenario **could** do.

*   It **can** say: on the recorded run, `happy-two-covers-thursday` was handled
    by `BookingAgent`, called `search_tables` and `create_booking`, and never
    touched `PolicyAgent`. That is observed fact, not inference.
*   It **cannot** say: that the same scenario would never reach `PolicyAgent`.
    A branch the recorded run did not take leaves no event. A tool guarded by a
    condition that happened to be false is invisible. A config value read at
    runtime, a prompt fragment shared between two agents, a dependency that only
    exists in data — none of those appear in an event stream at all.

So the map is a **lower bound on the dependency set, never an upper bound**. It
proves "this scenario touched X". It cannot prove "this scenario cannot touch Y".

That asymmetry is why every consumer of this map must fail safe: when the map is
silent, the answer is *run it*, not *skip it*. Skipping a scenario that should
have run is the only unrecoverable error a selector can make — the regression
ships and nobody sees a red line. Running a scenario that did not need to run
costs money and nothing else.

Three things in this module encode that rule as a default rather than a
convention:

    `ScenarioDependencies.mapped`   False for any scenario with no committed
                                    trace, and for any whose trace names no
                                    agent and no tool. Never an empty dependency
                                    set that could be mistaken for "depends on
                                    nothing".
    `TraceMap.always_run_ids()`     the unmapped ids, returned as a set the
                                    selector unions in unconditionally.
    `TraceMap.degraded`             True if *any* trace file could not be read
                                    or attributed. A degraded map is not a
                                    partially-good map; `select_for_symbols`
                                    returns the entire corpus when it is set.

WHY THE MAP IS A COMMITTED, GENERATED FILE
------------------------------------------
`lab/selection/trace_map.json` is written by this module and checked in. It could
have been computed on the fly — it takes under a second — and that would have
been worse.

A committed map turns a change in the dependency graph into a **reviewable
diff**. If a refactor moves `check_policy` out of the path of eleven scenarios,
that shows up as eleven removed lines in a pull request, next to the change that
caused it, where a human can say "yes, that was intended" or "no, that is a
routing bug". Computed on the fly it would be silent drift, and the selector
would quietly start skipping scenarios that used to be covered. Half the value of
this feature is that visibility; the other half is the money saved.

That is also why the rendering is byte-stable by construction: sorted keys,
sorted sets, no timestamp, two-space indent, trailing newline. The only field
that moves without a real change is `_provenance.commit`, which is the point of a
provenance stamp. A generated file that churns on every regeneration cannot be
reviewed, so this one does not churn.

ORDERING
--------
Events are consulted in **event-stream position** — file order — and never by
timestamp. Nothing in this module reads `ts`. Derived collections are then sorted
by name for stability. A trace whose clock ran backwards yields exactly the same
dependency set as one whose clock behaved, because the dependency set is a
question about *what happened*, not *when*.

WHAT IS DERIVED, PER SCENARIO
-----------------------------
    agents        every sub-agent that spoke a turn (`agent_utterance.agent`)
                  or appeared on either side of a handoff (`agent_handoff`)
    tools         every tool named by a `tool_call` or a `tool_result`. Both,
                  because a result without a call is still evidence the tool
                  ran, and dropping it would shrink the dependency set — the
                  one direction this map is not allowed to err in
    handoff_edges the routing pairs actually taken, as "From>To"
    engines       the `engine` field: concrete STT / TTS / transport / replay
                  identities. This is the closest thing a trace carries to a
                  module or prompt identity for the vendor-facing stages
    adapters      the `adapter` recorded at `session_start`
    event_kinds   which kinds the run produced, so a change to an event kind's
                  producer can select the scenarios that observe it

THE SYMBOL TABLE, AND HOW STAGE 1 JOINS TO THIS
-----------------------------------------------
Stage 1 of the selector answers "what changed" with `git diff` plus AST parsing,
and speaks in **source symbols**: this file, this function, this class. A trace
speaks in **runtime names**: `BookingAgent`, `search_tables`. The two vocabularies
have to meet somewhere, so this module resolves them here, once, and publishes
the join key in the artefact.

`symbols` maps each observed agent and tool name to every first-party definition
site found by an AST scan of `SOURCE_ROOTS` (file path plus qualified name; no
line numbers, deliberately, because line numbers would make the artefact churn on
every unrelated edit above them).

A name that resolves to more than one file records *all* of them — ambiguity
widens the dependency set, it does not narrow it. A name that resolves to nothing
is recorded with `resolved: false` and an empty location list; it is not dropped.
An unresolved symbol is a permanent "unknown" that a consumer must treat as
possibly-affected by any change.

`TraceMap.select_for_locations` closes the join in one call: hand it the
`path::qualname` locations stage 1 reports and it returns the scenario ids that
may be affected. The intersection lives here, in one place, next to the
limitations that make it safe — a joiner written elsewhere would eventually
forget one of them. Worked figures from the committed map, each reproducible with
`python -m lab.selection.trace_map`:

    tablemate/tools.py::check_policy    ->  30/73 scenarios
    tablemate/tools.py::<module>        ->  64/73 scenarios
    a path with no observed name in it  ->  73/73 scenarios (fail safe)

The floor of 18/73 in every one of those is the unmapped audio tier, which has no
committed per-scenario trace and therefore always runs.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
No model is involved, at any point, in version one. The whole derivation is
`json.loads` and `ast.parse`. It needs no credential, makes no network call, and
produces the same bytes on a clean clone with every key unset. A selector that
required an API key to decide what to run would have moved the cost, not removed
it.

There is a real gap that stage 1 and stage 2 provably cannot close — a config
value read at runtime, a prompt fragment shared across agents, a dependency that
exists only in data. Those are documented rather than guessed at, and they
resolve the only safe way: `degraded` and `always_run_ids()` push them into
"select everything". A later stage may narrow that; nothing here pretends to.

REGENERATING
------------
    python -m lab.selection.trace_map            # print the coverage summary
    python -m lab.selection.trace_map --write    # rewrite the committed map
    python -m lab.selection.trace_map --check    # exit 1 if the map has drifted

`--check` is what belongs in CI: it re-derives the body and compares it to the
committed file, so a dependency-graph change that nobody regenerated fails the
build instead of rotting.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "FORMAT_VERSION",
    "REGEN_COMMAND",
    "DEFAULT_MAP_PATH",
    "FIXTURE_ROOT",
    "SOURCE_ROOTS",
    "SymbolLocation",
    "ScenarioDependencies",
    "TraceMap",
    "build_trace_map",
    "render_trace_map",
    "write_trace_map",
    "load_trace_map",
    "check_trace_map",
    "MODULE_QUALNAME",
    "main",
]

#: Bumped when the artefact's shape changes in a way a reader must notice.
FORMAT_VERSION: int = 1

#: Repository root: this file is `<root>/lab/selection/trace_map.py`.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]

#: Where the committed traces live.
FIXTURE_ROOT: Path = REPO_ROOT / "fixtures"

#: Where the generated artefact lives.
DEFAULT_MAP_PATH: Path = REPO_ROOT / "lab" / "selection" / "trace_map.json"

#: The command that regenerates the artefact, recorded in the artefact itself so
#: a reader who finds a stale map knows what to run without reading this file.
#:
#: It is `python -m lab.selection.trace_map`, not an `evallab` subcommand,
#: because the selection layer is not wired into the CLI yet — each stage carries
#: its own entry point until it is.
REGEN_COMMAND: str = "python -m lab.selection.trace_map --write"

#: First-party source trees the symbol resolver scans, in search order. The
#: system under test comes first so that a name defined both in the product and
#: in a harness double resolves to the product site first in the recorded list.
SOURCE_ROOTS: tuple[str, ...] = ("tablemate", "roleplay", "lab", "scenarios")

#: Directory names never descended into during the symbol scan.
_SKIP_DIRS: frozenset[str] = frozenset(
    {"__pycache__", ".venv", "venv", "build", "dist", ".git", "node_modules"}
)

_TRACE_SUFFIX = ".jsonl"

#: Qualname stage 1 uses for module-level code (imports, constants, top-level
#: calls). Restated here rather than imported so this module stays independent of
#: stage 1 — the two agree on a string, not on a dependency, and a test pins it.
MODULE_QUALNAME = "<module>"


# --------------------------------------------------------------------------- #
# The artefact's models
# --------------------------------------------------------------------------- #


class SymbolLocation(BaseModel):
    """Where a runtime name observed in a trace is defined in first-party source.

    `resolved` is a field rather than an inference from an empty `locations`
    list, so that "we looked and found nothing" is stated rather than implied.
    An unresolved symbol is not a bug in the map; it is usually a name owned by a
    vendor, a mock, or a data file. It is recorded because a consumer must treat
    it as possibly-affected by any change, and it cannot do that if the name has
    been quietly dropped.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str = Field(description="'agent' or 'tool'.")
    resolved: bool
    locations: list[str] = Field(
        default_factory=list,
        description=(
            "Repo-relative 'path::qualname' for every definition site found. "
            "More than one entry means the name is ambiguous; every site is "
            "listed, because ambiguity must widen the dependency set."
        ),
    )


class ScenarioDependencies(BaseModel):
    """What one scenario's committed traces show it exercised.

    Read `mapped` before anything else. When it is False every collection below
    is empty *because there was no evidence*, which is a different statement from
    "this scenario depends on nothing" and must never be confused with it.
    """

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    suite: str | None = None
    mapped: bool = Field(
        description=(
            "True only if a committed trace was attributed to this scenario AND "
            "that trace named at least one agent or tool. False means UNMAPPED: "
            "always run it."
        )
    )
    unmapped_reason: str | None = Field(
        default=None,
        description="Why the evidence is insufficient, in words, when `mapped` is False.",
    )
    sessions: int = Field(
        default=0, description="How many recorded sessions the evidence came from."
    )
    agents: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    handoff_edges: list[str] = Field(
        default_factory=list, description="Routing pairs actually taken, 'From>To'."
    )
    engines: list[str] = Field(default_factory=list)
    adapters: list[str] = Field(default_factory=list)
    event_kinds: list[str] = Field(default_factory=list)
    trace_files: list[str] = Field(
        default_factory=list, description="Repo-relative paths of the evidence."
    )
    trace_digest: str | None = Field(
        default=None,
        description=(
            "12 hex chars over the (path, sha256) pairs of the evidence files. "
            "Changes when the evidence changes, so a re-recorded fixture is "
            "visible in the diff even if the derived sets happen to be equal."
        ),
    )

    def symbols(self) -> list[str]:
        """Every runtime name this scenario touched: agents first, then tools."""
        return [*self.agents, *self.tools]


class TraceMap(BaseModel):
    """The generated artefact, plus its read side.

    The read side lives here rather than in the selector because the map's own
    limitations are what make the queries safe, and a query written elsewhere
    would sooner or later forget them. `select_for_symbols` is the one function
    the selector needs, and it fails safe by construction.
    """

    model_config = ConfigDict(extra="forbid")

    format_version: int = FORMAT_VERSION
    corpus_size: int
    mapped_count: int
    unmapped_count: int
    session_count: int
    trace_file_count: int
    degraded: bool = Field(
        description=(
            "True if any candidate trace file could not be parsed or attributed "
            "to a scenario. A degraded map is untrusted as a whole: consumers "
            "must select the entire corpus, because the unreadable file may have "
            "been the only evidence that a scenario touches the changed code."
        )
    )
    degraded_reasons: list[str] = Field(default_factory=list)
    skipped_files: list[str] = Field(
        default_factory=list,
        description=(
            "'path: reason' for every candidate file that contributed nothing. "
            "A JSONL fixture that is not an event stream is expected here and "
            "does not degrade the map; an unreadable one does."
        ),
    )
    unmatched_trace_scenario_ids: list[str] = Field(
        default_factory=list,
        description=(
            "scenario_id values found in traces that are not corpus scenario "
            "ids — transport-tier rows, calibration fixtures, and the spoken "
            "roleplay row. Recorded so the count reconciles; they map to no "
            "corpus row and select nothing."
        ),
    )
    scenarios: list[ScenarioDependencies] = Field(default_factory=list)
    symbols: list[SymbolLocation] = Field(default_factory=list)

    # ---------------------------------------------------------------- indexes

    def by_id(self, scenario_id: str) -> ScenarioDependencies:
        for entry in self.scenarios:
            if entry.scenario_id == scenario_id:
                return entry
        raise KeyError(
            f"no scenario {scenario_id!r} in the map; it holds {len(self.scenarios)}"
        )

    def index(self) -> dict[str, ScenarioDependencies]:
        return {entry.scenario_id: entry for entry in self.scenarios}

    def symbol_index(self) -> dict[str, SymbolLocation]:
        return {sym.name: sym for sym in self.symbols}

    def ids(self) -> list[str]:
        return [entry.scenario_id for entry in self.scenarios]

    # -------------------------------------------------------------- the rules

    def always_run_ids(self) -> set[str]:
        """Scenarios that must run whatever changed: the unmapped ones.

        No usable evidence means no basis for exclusion. This set is unioned into every
        selection unconditionally, which is the fail-safe rule expressed as a
        value rather than as a comment somebody can forget to honour.
        """
        return {e.scenario_id for e in self.scenarios if not e.mapped}

    def mapped_ids(self) -> set[str]:
        return {e.scenario_id for e in self.scenarios if e.mapped}

    def scenarios_using(self, name: str) -> list[str]:
        """Scenario ids whose evidence names `name` as an agent or a tool."""
        return sorted(
            e.scenario_id
            for e in self.scenarios
            if name in e.agents or name in e.tools
        )

    def unresolved_symbols(self) -> list[str]:
        """Observed names with no first-party definition site. Always suspect."""
        return sorted(s.name for s in self.symbols if not s.resolved)

    def select_for_symbols(self, symbols: Iterable[str]) -> set[str]:
        """Scenario ids that may be affected by a change to `symbols`.

        `symbols` are runtime names — agent class names and tool function names —
        as produced by joining stage 1's changed source symbols through
        `self.symbols`.

        Three things widen the result, all of them deliberate:

        *   the unmapped scenarios, always;
        *   the entire corpus if the map is `degraded`;
        *   the entire corpus if any requested symbol is unknown to the map,
            because a name the map has never observed could be reached by any
            scenario and the map has no standing to say otherwise.

        The result is therefore a superset of the truth whenever the map is
        unsure, and never a subset. That is the whole contract.
        """
        wanted = set(symbols)
        everything = set(self.ids())
        if self.degraded:
            return everything
        known = {s.name for s in self.symbols}
        if wanted - known:
            return everything
        selected = self.always_run_ids()
        for entry in self.scenarios:
            if wanted & set(entry.symbols()):
                selected.add(entry.scenario_id)
        return selected

    def names_for_locations(
        self, locations: Iterable[str]
    ) -> tuple[set[str], set[str]]:
        """Translate stage 1's `path::qualname` locations into runtime names.

        Returns `(names, unmatched)`. `names` are the agent and tool names this
        map knows to be defined at one of `locations`; `unmatched` are the
        locations that correspond to no runtime name the map has ever observed.

        Two widenings happen here, both matching what stage 1 documents:

        *   `path::<module>` means module-level code changed, which runs on
            import for everything in the file, so it selects every name defined
            anywhere in that path;
        *   a name defined in more than one place is matched by any of its sites.

        `unmatched` is returned rather than swallowed because it is the honest
        answer to "does this change reach the agents?" — the map does not know,
        and the caller has to decide what to do about not knowing.
        """
        by_path: dict[str, set[str]] = {}
        by_location: dict[str, set[str]] = {}
        for symbol in self.symbols:
            for location in symbol.locations:
                path, _, _ = location.partition("::")
                by_path.setdefault(path, set()).add(symbol.name)
                by_location.setdefault(location, set()).add(symbol.name)

        names: set[str] = set()
        unmatched: set[str] = set()
        for location in locations:
            path, _, qualname = location.partition("::")
            if qualname == MODULE_QUALNAME:
                hit = by_path.get(path)
            else:
                hit = by_location.get(location)
            if hit:
                names |= hit
            else:
                unmatched.add(location)
        return names, unmatched

    def select_for_locations(self, locations: Iterable[str]) -> set[str]:
        """Scenario ids that may be affected by changes at `path::qualname` sites.

        The safe default, and the one function a selector should reach for. Any
        location the map cannot translate into a runtime name selects the entire
        corpus, because a file with no observed agent or tool in it is a file
        this map has *no evidence about at all* — and "no evidence" has to mean
        "run it", not "it is unrelated".

        A caller with better information — stage 1 already classifies files that
        cannot affect a run — may use `names_for_locations` and apply its own
        policy to the unmatched set. It must then own that decision explicitly,
        which is the point of returning the unmatched locations rather than
        quietly dropping them.
        """
        names, unmatched = self.names_for_locations(locations)
        if unmatched:
            return set(self.ids())
        return self.select_for_symbols(names)

    # ------------------------------------------------------------- reporting

    def coverage_summary(self) -> dict[str, Any]:
        """Every rate with its denominator. A naked percentage is a defect."""
        agents = {a for e in self.scenarios for a in e.agents}
        tools = {t for e in self.scenarios for t in e.tools}
        engines = {g for e in self.scenarios for g in e.engines}
        resolved = sum(1 for s in self.symbols if s.resolved)
        return {
            "scenarios_mapped": self.mapped_count,
            "scenarios_total": self.corpus_size,
            "scenarios_unmapped": self.unmapped_count,
            "sessions": self.session_count,
            "trace_files": self.trace_file_count,
            "distinct_agents": len(agents),
            "distinct_tools": len(tools),
            "distinct_engines": len(engines),
            "symbols_resolved": resolved,
            "symbols_total": len(self.symbols),
            "degraded": self.degraded,
        }

    def summary_lines(self) -> list[str]:
        """Human-readable coverage, every figure carrying its denominator."""
        s = self.coverage_summary()
        lines = [
            (
                f"scenarios mapped        "
                f"{s['scenarios_mapped']}/{s['scenarios_total']}"
                f"  (unmapped {s['scenarios_unmapped']}/{s['scenarios_total']}"
                " — always run)"
            ),
            (
                f"evidence                {s['sessions']} sessions across "
                f"{s['trace_files']} committed trace files"
            ),
            f"distinct agents         {s['distinct_agents']}",
            f"distinct tools          {s['distinct_tools']}",
            f"distinct engines        {s['distinct_engines']}",
            (
                f"symbols resolved        "
                f"{s['symbols_resolved']}/{s['symbols_total']}"
                "  (unresolved names are always treated as possibly-affected)"
            ),
            f"degraded                {str(s['degraded']).lower()}",
        ]
        if self.unmapped_count:
            lines.append(
                f"unmapped ids            {', '.join(sorted(self.always_run_ids()))}"
            )
        return lines


# --------------------------------------------------------------------------- #
# Reading the evidence
# --------------------------------------------------------------------------- #


def _iter_trace_paths(fixture_root: Path) -> Iterator[Path]:
    """Every `.jsonl` under `fixture_root`, in sorted repo-relative order.

    Sorted so the artefact is reproducible; a glob's filesystem order is not.
    `.json` fixtures are skipped without comment — the recorded caller scripts
    and judge verdicts in this repository are `.json` and are not event streams
    by design. Only JSONL is a trace (`lab.trace.io`).
    """
    if not fixture_root.is_dir():
        return
    yield from sorted(
        p for p in fixture_root.rglob(f"*{_TRACE_SUFFIX}") if p.is_file()
    )


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


class _FileEvidence(BaseModel):
    """One trace file, reduced to the facts the map needs."""

    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str
    scenario_id: str | None
    agents: set[str] = Field(default_factory=set)
    tools: set[str] = Field(default_factory=set)
    handoff_edges: set[str] = Field(default_factory=set)
    engines: set[str] = Field(default_factory=set)
    adapters: set[str] = Field(default_factory=set)
    event_kinds: set[str] = Field(default_factory=set)


def _read_evidence(path: Path) -> tuple[_FileEvidence | None, str | None]:
    """Reduce one file to evidence, or explain why it produced none.

    Returns `(evidence, None)` on success and `(None, reason)` otherwise. The
    reason is carried rather than raised because the caller has to decide whether
    it merely skips the file or degrades the whole map, and that decision belongs
    at the top level where the fail-safe rule is applied once.

    Lines are read in file order and `ts` is never consulted.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:  # pragma: no cover - filesystem failure
        return None, f"unreadable: {exc.__class__.__name__}"

    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "not utf-8"

    evidence = _FileEvidence(path=_relative(path), sha256=digest, scenario_id=None)
    saw_event = False

    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return None, f"line {number} is not JSON"
        if not isinstance(record, dict):
            return None, f"line {number} is not a JSON object"
        kind = record.get("kind")
        if not isinstance(kind, str) or not kind:
            # A JSONL fixture that is not an event stream — judge verdicts, for
            # example. One non-event line is enough to say so; carry on and let
            # the caller see zero events.
            continue
        saw_event = True
        evidence.event_kinds.add(kind)

        engine = record.get("engine")
        if isinstance(engine, str) and engine:
            evidence.engines.add(engine)

        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue

        if kind == "session_start":
            scenario_id = payload.get("scenario_id")
            if isinstance(scenario_id, str) and scenario_id:
                evidence.scenario_id = scenario_id
            adapter = payload.get("adapter")
            if isinstance(adapter, str) and adapter:
                evidence.adapters.add(adapter)
        elif kind == "agent_utterance":
            agent = payload.get("agent")
            if isinstance(agent, str) and agent:
                evidence.agents.add(agent)
        elif kind == "agent_handoff":
            src, dst = payload.get("from"), payload.get("to")
            for side in (src, dst):
                if isinstance(side, str) and side:
                    evidence.agents.add(side)
            if isinstance(src, str) and isinstance(dst, str) and src and dst:
                evidence.handoff_edges.add(f"{src}>{dst}")
        elif kind in ("tool_call", "tool_result"):
            # Both kinds, on purpose. A result whose call was not recorded is
            # still evidence the tool ran, and the one direction this map may not
            # err in is *smaller*.
            name = payload.get("name")
            if isinstance(name, str) and name:
                evidence.tools.add(name)

    if not saw_event:
        return None, "no trace events (not an event stream)"
    if evidence.scenario_id is None:
        return None, "no session_start carrying a scenario_id"
    return evidence, None


# --------------------------------------------------------------------------- #
# Resolving runtime names to source symbols
# --------------------------------------------------------------------------- #


def _iter_source_files(roots: Sequence[str]) -> Iterator[Path]:
    for root in roots:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            yield path


def _definitions(path: Path) -> dict[str, list[str]]:
    """`{name: [qualname, ...]}` for every class and function defined in `path`.

    Parse failures return `{}` rather than raising. A file this module cannot
    parse only ever costs resolution — the name stays unresolved, and an
    unresolved name is treated as possibly-affected by everything, so an
    unparseable source file makes the selector more conservative, never less.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return {}

    found: dict[str, list[str]] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                qualname = f"{prefix}{child.name}"
                found.setdefault(child.name, []).append(qualname)
                walk(child, f"{qualname}.")

    walk(tree, "")
    return found


def _resolve_symbols(
    agents: Iterable[str], tools: Iterable[str], roots: Sequence[str]
) -> list[SymbolLocation]:
    """Locate every observed agent and tool name in first-party source."""
    wanted: dict[str, str] = {}
    for name in agents:
        wanted[name] = "agent"
    for name in tools:
        # A name used as both would be a genuine collision; record it as a tool,
        # which is the narrower and more surprising of the two.
        wanted[name] = "tool"

    hits: dict[str, list[str]] = {name: [] for name in wanted}
    for path in _iter_source_files(roots):
        definitions = _definitions(path)
        if not definitions:
            continue
        rel = _relative(path)
        for name in wanted:
            for qualname in definitions.get(name, ()):
                hits[name].append(f"{rel}::{qualname}")

    return [
        SymbolLocation(
            name=name,
            kind=wanted[name],
            resolved=bool(hits[name]),
            locations=sorted(hits[name]),
        )
        for name in sorted(wanted)
    ]


# --------------------------------------------------------------------------- #
# Building the map
# --------------------------------------------------------------------------- #


def _corpus_rows() -> list[tuple[str, str | None]]:
    """`(scenario_id, suite)` for every scenario in the corpus, id-sorted.

    Every suite, including the audio tier, because a scenario left out of the
    denominator is a scenario the selector would never think to run. The import
    is local so that importing this module costs nothing beyond the standard
    library and pydantic.
    """
    from scenarios.loader import ALL_SUITES, load_corpus

    corpus = load_corpus(suites=ALL_SUITES)
    return sorted((s.id, s.suite) for s in corpus.scenarios)


def _git_commit() -> str:
    """The commit the map was derived at, or 'unknown' outside a git checkout."""
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no git
        return "unknown"
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else "unknown"


def build_trace_map(
    *,
    fixture_root: Path | str = FIXTURE_ROOT,
    source_roots: Sequence[str] = SOURCE_ROOTS,
    corpus: Sequence[tuple[str, str | None]] | None = None,
) -> TraceMap:
    """Derive the dependency map from the committed traces. Pure and offline.

    `corpus` is injectable so a test can pin the denominator; by default it is
    every scenario in every suite. `fixture_root` is injectable so a test can
    point at an empty directory and confirm the whole corpus comes back unmapped
    rather than empty.

    No network, no credential, no model. The result carries no timestamp, so two
    invocations at the same commit over the same evidence render the same bytes.
    """
    fixture_root = Path(fixture_root)
    rows = list(_corpus_rows()) if corpus is None else list(corpus)
    corpus_ids = {scenario_id for scenario_id, _ in rows}

    collected: dict[str, list[_FileEvidence]] = {}
    skipped: list[str] = []
    degraded_reasons: list[str] = []
    file_count = 0

    for path in _iter_trace_paths(fixture_root):
        file_count += 1
        evidence, reason = _read_evidence(path)
        rel = _relative(path)
        if evidence is None:
            assert reason is not None
            skipped.append(f"{rel}: {reason}")
            # "not an event stream" is an expected, benign fixture kind. Anything
            # else means a file that *should* have been evidence was not read,
            # and the map can no longer claim to know what it does not contain.
            if not reason.startswith("no trace events"):
                degraded_reasons.append(f"{rel}: {reason}")
            continue
        assert evidence.scenario_id is not None
        collected.setdefault(evidence.scenario_id, []).append(evidence)

    unmatched = sorted(set(collected) - corpus_ids)

    entries: list[ScenarioDependencies] = []
    sessions = 0
    for scenario_id, suite in rows:
        found = collected.get(scenario_id, [])
        if not found:
            entries.append(
                ScenarioDependencies(
                    scenario_id=scenario_id,
                    suite=suite,
                    mapped=False,
                    unmapped_reason=(
                        "no committed trace names this scenario; there is no "
                        "evidence to exclude it from any change, so always run it"
                    ),
                )
            )
            continue
        sessions += len(found)
        found.sort(key=lambda ev: ev.path)
        digest_source = "\n".join(f"{ev.path} {ev.sha256}" for ev in found)
        agents = sorted({a for ev in found for a in ev.agents})
        tools = sorted({t for ev in found for t in ev.tools})
        # Evidence that names no agent and no tool cannot join to a source change
        # at all, so treating it as mapped would silently exempt the scenario
        # from every selection. A trace exists, but it is not evidence *of a
        # dependency*, and the fail-safe reading of "no dependency observed" is
        # "we do not know", not "there is none". Recorded as unmapped, with the
        # evidence still listed so a reviewer can see what was found.
        insufficient = not agents and not tools
        entries.append(
            ScenarioDependencies(
                scenario_id=scenario_id,
                suite=suite,
                mapped=not insufficient,
                unmapped_reason=(
                    "committed trace(s) name no agent and no tool, so nothing "
                    "here can be joined to a source change; always run it"
                    if insufficient
                    else None
                ),
                sessions=len(found),
                agents=agents,
                tools=tools,
                handoff_edges=sorted({h for ev in found for h in ev.handoff_edges}),
                engines=sorted({g for ev in found for g in ev.engines}),
                adapters=sorted({a for ev in found for a in ev.adapters}),
                event_kinds=sorted({k for ev in found for k in ev.event_kinds}),
                trace_files=[ev.path for ev in found],
                trace_digest=hashlib.sha256(
                    digest_source.encode("utf-8")
                ).hexdigest()[:12],
            )
        )

    symbols = _resolve_symbols(
        {a for e in entries for a in e.agents},
        {t for e in entries for t in e.tools},
        source_roots,
    )

    return TraceMap(
        format_version=FORMAT_VERSION,
        corpus_size=len(entries),
        mapped_count=sum(1 for e in entries if e.mapped),
        unmapped_count=sum(1 for e in entries if not e.mapped),
        session_count=sessions,
        trace_file_count=file_count,
        degraded=bool(degraded_reasons),
        degraded_reasons=sorted(degraded_reasons),
        skipped_files=sorted(skipped),
        unmatched_trace_scenario_ids=unmatched,
        scenarios=entries,
        symbols=symbols,
    )


# --------------------------------------------------------------------------- #
# Rendering, writing, checking
# --------------------------------------------------------------------------- #

#: The provenance block's key. A leading underscore sorts it above every
#: lower-case key under `sort_keys=True`, so the header stays at the top of the
#: file without the renderer having to preserve insertion order.
PROVENANCE_KEY = "_provenance"

_PROVENANCE_NOTE = (
    "GENERATED FILE - do not hand-edit. Derived from the committed traces under "
    "fixtures/ by lab/selection/trace_map.py. A trace records what one recorded "
    "run DID, never what a scenario COULD do, so this map is a lower bound on "
    "each scenario's dependencies: it proves 'touched X', never 'cannot touch "
    "Y'. Scenarios with mapped=false are UNMAPPED and must always run. If "
    "degraded=true the whole map is untrusted and the whole corpus must run."
)


def _body(trace_map: TraceMap) -> dict[str, Any]:
    """The derived part of the artefact — everything except provenance.

    Split out because provenance is the only part that moves without a real
    change. Comparing bodies is what lets a drift check say "the dependency graph
    changed" rather than "the commit changed".
    """
    return trace_map.model_dump(mode="json")


def render_trace_map(trace_map: TraceMap, *, commit: str | None = None) -> str:
    """Render the artefact as deterministic JSON text.

    Byte-stable by construction: sorted keys throughout, sorted collections built
    upstream, two-space indent, one trailing newline, and no timestamp anywhere.
    The only field that can move without a change to the evidence is the commit.
    """
    document = {
        PROVENANCE_KEY: {
            "command": REGEN_COMMAND,
            "commit": _git_commit() if commit is None else commit,
            "format_version": FORMAT_VERSION,
            "fixture_root": _relative(FIXTURE_ROOT),
            "note": _PROVENANCE_NOTE,
        },
        **_body(trace_map),
    }
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_trace_map(
    path: Path | str = DEFAULT_MAP_PATH,
    *,
    trace_map: TraceMap | None = None,
    commit: str | None = None,
) -> Path:
    """Write the artefact and return the path written."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    built = build_trace_map() if trace_map is None else trace_map
    target.write_text(render_trace_map(built, commit=commit), encoding="utf-8")
    return target


def load_trace_map(path: Path | str = DEFAULT_MAP_PATH) -> TraceMap:
    """Read a committed artefact back into a `TraceMap`, provenance dropped.

    Provenance is metadata about the derivation, not part of the map; dropping it
    here means no consumer can accidentally branch on which commit produced the
    file.
    """
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    document.pop(PROVENANCE_KEY, None)
    return TraceMap.model_validate(document)


def check_trace_map(
    path: Path | str = DEFAULT_MAP_PATH, *, trace_map: TraceMap | None = None
) -> list[str]:
    """Compare the committed artefact's body against a fresh derivation.

    Returns a list of human-readable differences; empty means the map is current.
    Bodies are compared, not bytes, so a commit stamp that has moved on is not
    reported as drift — only a real change in the dependency graph is.
    """
    target = Path(path)
    if not target.is_file():
        return [f"{_relative(target)} does not exist; run: {REGEN_COMMAND}"]

    fresh = build_trace_map() if trace_map is None else trace_map
    try:
        committed = load_trace_map(target)
    except (json.JSONDecodeError, ValueError) as exc:
        return [f"{_relative(target)} is not a readable trace map: {exc}"]

    if _body(committed) == _body(fresh):
        return []

    differences: list[str] = []
    committed_index, fresh_index = committed.index(), fresh.index()
    for scenario_id in sorted(set(committed_index) | set(fresh_index)):
        was, now = committed_index.get(scenario_id), fresh_index.get(scenario_id)
        if was is None:
            differences.append(f"{scenario_id}: added to the corpus")
        elif now is None:
            differences.append(f"{scenario_id}: removed from the corpus")
        elif was != now:
            differences.append(
                f"{scenario_id}: dependencies changed "
                f"(mapped {was.mapped}->{now.mapped}, "
                f"agents {len(was.agents)}->{len(now.agents)}, "
                f"tools {len(was.tools)}->{len(now.tools)})"
            )
    if committed.symbols != fresh.symbols:
        differences.append("symbol table changed")
    if not differences:
        differences.append("map body differs in its summary counts")
    differences.append(f"regenerate with: {REGEN_COMMAND}")
    return differences


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m lab.selection.trace_map [--write | --check] [--json]`."""
    parser = argparse.ArgumentParser(
        prog="python -m lab.selection.trace_map",
        description=(
            "Derive, write or verify the trace-derived scenario dependency map."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write", action="store_true", help="rewrite the committed artefact"
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed artefact has drifted from the traces",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the coverage summary as JSON"
    )
    parser.add_argument(
        "--path",
        default=str(DEFAULT_MAP_PATH),
        help="artefact path (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    trace_map = build_trace_map()

    if args.check:
        differences = check_trace_map(args.path, trace_map=trace_map)
        if differences:
            print("trace map has drifted:", file=sys.stderr)
            for line in differences:
                print(f"  {line}", file=sys.stderr)
            return 1
        print(f"trace map is current: {_relative(Path(args.path))}")
        return 0

    if args.write:
        written = write_trace_map(args.path, trace_map=trace_map)
        print(f"wrote {_relative(written)}")

    if args.json:
        print(json.dumps(trace_map.coverage_summary(), indent=2, sort_keys=True))
    else:
        for line in trace_map.summary_lines():
            print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via `python -m`
    raise SystemExit(main())
