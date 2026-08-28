"""Tests for the trace-derived scenario dependency map.

WHAT THIS DEMONSTRATES
----------------------
This map is a grader. It decides "this scenario need not run", and when a grader
is wrong in the exclude direction a regression ships with a green build. So the
properties worth testing are not "does it return a dict" but the two the whole
feature rests on:

1.  **It reproduces.** The same evidence at the same commit renders the same
    bytes, and the committed artefact matches a fresh derivation. Without that,
    the map drifts silently and the diff — which is half the point of committing
    it — stops meaning anything.
2.  **It fails safe.** Every ambiguity resolves toward running more: a scenario
    with no trace, a trace with no usable evidence, an unreadable file, an
    unknown symbol. Each of those has its own test, because each is a separate
    path that could quietly return "exclude".

The fail-safe tests use synthetic fixture roots rather than the committed ones,
on purpose. A property that only holds because the repository happens to be
tidy today is not a property; it has to hold on a corpus that has gone wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab.selection.trace_map import (
    DEFAULT_MAP_PATH,
    FIXTURE_ROOT,
    MODULE_QUALNAME,
    PROVENANCE_KEY,
    REGEN_COMMAND,
    ScenarioDependencies,
    TraceMap,
    build_trace_map,
    check_trace_map,
    load_trace_map,
    main,
    render_trace_map,
    write_trace_map,
)

# The corpus load and the fixture scan are the slow part; every test that only
# reads the real map shares one build.


@pytest.fixture(scope="module")
def real_map() -> TraceMap:
    return build_trace_map()


@pytest.fixture(scope="module")
def committed_map() -> TraceMap:
    return load_trace_map(DEFAULT_MAP_PATH)


# --------------------------------------------------------------------------- #
# Helpers for synthetic evidence
# --------------------------------------------------------------------------- #


def _event(kind: str, payload: dict | None = None, engine: str | None = None) -> str:
    return json.dumps(
        {"ts": 0.0, "kind": kind, "actor": "system", "payload": payload or {},
         "engine": engine},
        sort_keys=True,
    )


def _write_trace(path: Path, scenario_id: str, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = _event("session_start", {"scenario_id": scenario_id, "adapter": "text"})
    path.write_text("\n".join([head, *lines]) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Reproducibility — the artefact is only reviewable if it is stable
# --------------------------------------------------------------------------- #


class TestRegeneratesIdentically:
    def test_two_builds_render_the_same_bytes(self) -> None:
        """The derivation is pure: same evidence, same commit, same file.

        Rendered with a pinned commit so this asserts the *derived* bytes are
        stable, not that HEAD stood still.
        """
        first = render_trace_map(build_trace_map(), commit="pinned")
        second = render_trace_map(build_trace_map(), commit="pinned")
        assert first == second

    def test_committed_artefact_matches_a_fresh_derivation(
        self, real_map: TraceMap, committed_map: TraceMap
    ) -> None:
        """The checked-in map is current.

        This is the drift alarm. If it fails, the dependency graph moved and
        nobody regenerated: run `python -m lab.selection.trace_map --write` and
        read the diff, which is exactly the review this artefact exists to force.
        """
        assert check_trace_map(DEFAULT_MAP_PATH, trace_map=real_map) == []
        assert committed_map.model_dump() == real_map.model_dump()

    def test_rendering_carries_provenance_but_no_timestamp(
        self, real_map: TraceMap
    ) -> None:
        """A header names the command and the commit — and nothing that churns."""
        document = json.loads(render_trace_map(real_map, commit="deadbeef"))
        provenance = document[PROVENANCE_KEY]
        assert provenance["command"] == REGEN_COMMAND
        assert provenance["commit"] == "deadbeef"
        assert "GENERATED FILE" in provenance["note"]
        # No wall-clock anywhere: a generated file that changes every time it is
        # generated cannot be reviewed.
        for forbidden in ("generated_at", "timestamp", "date", "time"):
            assert forbidden not in provenance

    def test_committed_file_is_byte_stable_under_rewrite(
        self, tmp_path: Path, real_map: TraceMap, committed_map: TraceMap
    ) -> None:
        """Rewriting at the committed commit reproduces the committed bytes."""
        original = DEFAULT_MAP_PATH.read_text(encoding="utf-8")
        commit = json.loads(original)[PROVENANCE_KEY]["commit"]
        target = tmp_path / "trace_map.json"
        write_trace_map(target, trace_map=real_map, commit=commit)
        assert target.read_text(encoding="utf-8") == original
        assert original.endswith("\n")

    def test_load_drops_provenance_so_no_consumer_can_branch_on_it(self) -> None:
        loaded = load_trace_map(DEFAULT_MAP_PATH)
        assert PROVENANCE_KEY not in loaded.model_dump()


# --------------------------------------------------------------------------- #
# The map says true things about the real traces
# --------------------------------------------------------------------------- #


class TestDerivedFromRealTraces:
    def test_every_corpus_scenario_appears_exactly_once(
        self, real_map: TraceMap
    ) -> None:
        ids = real_map.ids()
        assert len(ids) == len(set(ids)) == real_map.corpus_size
        assert real_map.mapped_count + real_map.unmapped_count == real_map.corpus_size

    def test_a_tool_in_a_trace_is_listed_for_that_scenario(
        self, real_map: TraceMap
    ) -> None:
        """Read a committed trace by hand and demand the map agrees.

        Derived independently of the module under test — straight `json.loads`
        over the raw JSONL — so this cannot pass by both sides sharing a bug.
        """
        checked = 0
        for entry in real_map.scenarios:
            if not entry.trace_files:
                continue
            expected: set[str] = set()
            for relative in entry.trace_files:
                for line in (
                    (FIXTURE_ROOT.parent / relative).read_text(encoding="utf-8")
                ).splitlines():
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if record.get("kind") in ("tool_call", "tool_result"):
                        name = record.get("payload", {}).get("name")
                        if name:
                            expected.add(name)
            assert set(entry.tools) == expected, entry.scenario_id
            checked += 1
        assert checked == real_map.mapped_count

    def test_a_known_scenario_lists_the_tools_its_trace_shows(
        self, real_map: TraceMap
    ) -> None:
        """One named row, spelled out, so a reader can check it by eye."""
        entry = real_map.by_id("happy-two-covers-thursday")
        assert entry.mapped is True
        assert "search_tables" in entry.tools
        assert "create_booking" in entry.tools
        assert "BookingAgent" in entry.agents

    def test_handoff_edges_are_pairs_of_agents_the_entry_also_lists(
        self, real_map: TraceMap
    ) -> None:
        for entry in real_map.scenarios:
            for edge in entry.handoff_edges:
                source, _, destination = edge.partition(">")
                assert source in entry.agents
                assert destination in entry.agents

    def test_observed_agents_and_tools_resolve_to_first_party_source(
        self, real_map: TraceMap
    ) -> None:
        """The join key stage 1 needs: every runtime name has a definition site."""
        index = real_map.symbol_index()
        observed = {s for e in real_map.scenarios for s in e.symbols()}
        assert observed == set(index)
        for name in observed:
            symbol = index[name]
            assert symbol.resolved is True, name
            assert symbol.locations, name
            for location in symbol.locations:
                path, _, qualname = location.partition("::")
                assert (FIXTURE_ROOT.parent / path).is_file()
                assert qualname.split(".")[-1] == name

    def test_non_event_jsonl_is_recorded_as_skipped_not_silently_ignored(
        self, real_map: TraceMap
    ) -> None:
        """The repo has JSONL fixtures that are not traces. They are named."""
        assert any(
            "judge_verdicts.jsonl" in line for line in real_map.skipped_files
        )
        assert all("no trace events" in line for line in real_map.skipped_files)
        assert real_map.degraded is False

    def test_trace_ids_outside_the_corpus_are_reconciled_not_dropped(
        self, real_map: TraceMap
    ) -> None:
        """Transport and calibration fixtures carry ids no scenario owns."""
        assert real_map.unmatched_trace_scenario_ids
        assert not set(real_map.unmatched_trace_scenario_ids) & set(real_map.ids())

    def test_coverage_summary_carries_denominators(self, real_map: TraceMap) -> None:
        summary = real_map.coverage_summary()
        assert summary["scenarios_mapped"] + summary["scenarios_unmapped"] == (
            summary["scenarios_total"]
        )
        assert summary["symbols_resolved"] <= summary["symbols_total"]
        joined = "\n".join(real_map.summary_lines())
        # Every reported rate is written as a fraction, never a bare percentage.
        assert f"{summary['scenarios_mapped']}/{summary['scenarios_total']}" in joined
        assert "%" not in joined


# --------------------------------------------------------------------------- #
# Fail safe — the rule that matters most
# --------------------------------------------------------------------------- #


class TestFailsSafe:
    def test_a_scenario_with_no_trace_is_flagged_not_silently_empty(
        self, real_map: TraceMap
    ) -> None:
        """Unmapped is a stated fact with a reason, not an empty list."""
        unmapped = [e for e in real_map.scenarios if not e.mapped]
        assert unmapped, "the corpus should contain rows with no committed trace"
        for entry in unmapped:
            assert entry.unmapped_reason
            assert entry.symbols() == []
            assert entry.scenario_id in real_map.always_run_ids()

    def test_unmapped_scenarios_are_selected_by_every_query(
        self, real_map: TraceMap
    ) -> None:
        always = real_map.always_run_ids()
        assert always
        for symbol in ("search_tables", "BookingAgent", "check_policy"):
            assert always <= real_map.select_for_symbols([symbol])
        assert always <= real_map.select_for_symbols([])

    def test_an_empty_fixture_root_maps_nothing_and_runs_everything(
        self, tmp_path: Path
    ) -> None:
        """No evidence at all must mean *run the suite*, not *skip the suite*."""
        corpus = [("happy-a", "happy"), ("edge-b", "edge")]
        built = build_trace_map(fixture_root=tmp_path, corpus=corpus)
        assert built.mapped_count == 0
        assert built.unmapped_count == 2
        assert built.always_run_ids() == {"happy-a", "edge-b"}
        assert built.select_for_symbols(["anything"]) == {"happy-a", "edge-b"}

    def test_a_trace_naming_no_agent_or_tool_counts_as_unmapped(
        self, tmp_path: Path
    ) -> None:
        """Evidence that cannot join to source is not evidence of independence."""
        _write_trace(
            tmp_path / "hollow.jsonl",
            "happy-a",
            [_event("caller_utterance", {"text": "hello"}),
             _event("session_end", {"reason": "done", "turns": 1})],
        )
        built = build_trace_map(fixture_root=tmp_path, corpus=[("happy-a", "happy")])
        entry = built.by_id("happy-a")
        assert entry.mapped is False
        assert entry.sessions == 1  # the evidence is still shown to a reviewer
        assert entry.trace_files[0].endswith("hollow.jsonl")
        assert entry.unmapped_reason and "no agent and no tool" in entry.unmapped_reason
        assert built.always_run_ids() == {"happy-a"}

    def test_an_unreadable_trace_degrades_the_whole_map(self, tmp_path: Path) -> None:
        """One file we could not read means the map cannot claim completeness."""
        _write_trace(
            tmp_path / "good.jsonl",
            "happy-a",
            [_event("tool_call", {"name": "search_tables", "args": {}, "call_id": "1"})],
        )
        (tmp_path / "broken.jsonl").write_text("{not json at all\n", encoding="utf-8")
        built = build_trace_map(fixture_root=tmp_path, corpus=[("happy-a", "happy")])
        assert built.degraded is True
        assert any("broken.jsonl" in line for line in built.degraded_reasons)
        # Degraded is not "partly usable": every query returns the whole corpus.
        assert built.select_for_symbols(["search_tables"]) == {"happy-a"}
        assert built.select_for_symbols([]) == {"happy-a"}

    def test_degraded_selects_everything_even_for_an_unrelated_symbol(
        self, tmp_path: Path
    ) -> None:
        _write_trace(
            tmp_path / "good.jsonl",
            "happy-a",
            [_event("tool_call", {"name": "search_tables", "args": {}, "call_id": "1"})],
        )
        (tmp_path / "broken.jsonl").write_text("{oops\n", encoding="utf-8")
        corpus = [("happy-a", "happy"), ("edge-b", "edge"), ("edge-c", "edge")]
        built = build_trace_map(fixture_root=tmp_path, corpus=corpus)
        assert built.degraded is True
        assert built.select_for_symbols(["cancel_booking"]) == set(built.ids())

    def test_a_trace_with_no_scenario_id_degrades_rather_than_being_guessed(
        self, tmp_path: Path
    ) -> None:
        """Attribution by filename would be a guess; a guess can only exclude."""
        (tmp_path / "orphan.jsonl").write_text(
            _event("tool_call", {"name": "search_tables"}) + "\n", encoding="utf-8"
        )
        built = build_trace_map(fixture_root=tmp_path, corpus=[("happy-a", "happy")])
        assert built.degraded is True
        assert any("orphan.jsonl" in line for line in built.degraded_reasons)

    def test_an_unknown_symbol_selects_the_whole_corpus(self, tmp_path: Path) -> None:
        """A name the map has never seen could be reached by anything."""
        _write_trace(
            tmp_path / "a.jsonl",
            "happy-a",
            [_event("agent_utterance", {"agent": "BookingAgent", "text": "hi"})],
        )
        corpus = [("happy-a", "happy"), ("edge-b", "edge")]
        built = build_trace_map(fixture_root=tmp_path, corpus=corpus)
        assert built.degraded is False
        assert built.select_for_symbols(["BookingAgent"]) == {"happy-a", "edge-b"}
        # edge-b is unmapped, so it is in both answers; the point is that an
        # unrecognised name cannot narrow anything.
        assert built.select_for_symbols(["SomeNewAgent"]) == set(built.ids())

    def test_selection_never_returns_a_scenario_the_map_does_not_hold(
        self, real_map: TraceMap
    ) -> None:
        everything = set(real_map.ids())
        for query in ([], ["search_tables"], ["PolicyAgent"], ["not_a_symbol"]):
            assert real_map.select_for_symbols(query) <= everything

    def test_selecting_on_a_real_symbol_is_a_superset_of_its_users(
        self, real_map: TraceMap
    ) -> None:
        users = set(real_map.scenarios_using("check_policy"))
        assert users, "check_policy should appear in the committed traces"
        selected = real_map.select_for_symbols(["check_policy"])
        assert users <= selected
        assert real_map.always_run_ids() <= selected

    def test_an_unparseable_source_file_leaves_the_symbol_unresolved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A name with no definition site is recorded, not dropped."""
        _write_trace(
            tmp_path / "a.jsonl",
            "happy-a",
            [_event("tool_call", {"name": "no_such_tool", "args": {}, "call_id": "1"})],
        )
        built = build_trace_map(
            fixture_root=tmp_path,
            corpus=[("happy-a", "happy")],
            source_roots=(),
        )
        assert built.unresolved_symbols() == ["no_such_tool"]
        symbol = built.symbol_index()["no_such_tool"]
        assert symbol.resolved is False
        assert symbol.locations == []
        # Still a known name, so it selects its own scenario rather than the
        # whole corpus — the unresolved-ness is a warning to stage 1, not a
        # licence for this module to guess.
        assert built.select_for_symbols(["no_such_tool"]) == {"happy-a"}


# --------------------------------------------------------------------------- #
# The join with stage 1 — `path::qualname` in, scenario ids out
# --------------------------------------------------------------------------- #


class TestJoinsToChangedSourceLocations:
    """Stage 1 reports `path::qualname`; the map publishes the same key shape.

    These tests are what make the two stages composable without a translation
    layer, so they assert on the key shape as well as the answer.
    """

    def test_a_changed_tool_selects_its_users_plus_the_always_run_floor(
        self, real_map: TraceMap
    ) -> None:
        selected = real_map.select_for_locations(["tablemate/tools.py::check_policy"])
        users = set(real_map.scenarios_using("check_policy"))
        assert users
        assert users <= selected
        assert real_map.always_run_ids() <= selected
        # It narrows: this is the whole point of the feature.
        assert selected < set(real_map.ids())

    def test_a_module_level_change_widens_to_every_name_in_the_file(
        self, real_map: TraceMap
    ) -> None:
        """Module-level code runs on import for everything in the file."""
        one = real_map.select_for_locations(["tablemate/tools.py::check_policy"])
        whole = real_map.select_for_locations(
            [f"tablemate/tools.py::{MODULE_QUALNAME}"]
        )
        assert one < whole <= set(real_map.ids())

    def test_a_location_the_map_knows_nothing_about_selects_everything(
        self, real_map: TraceMap
    ) -> None:
        """No evidence about a file is not evidence the file is unrelated."""
        assert real_map.select_for_locations(["some/new/file.py::helper"]) == set(
            real_map.ids()
        )

    def test_unmatched_locations_are_returned_not_swallowed(
        self, real_map: TraceMap
    ) -> None:
        names, unmatched = real_map.names_for_locations(
            ["tablemate/agents.py::PolicyAgent", "some/new/file.py::helper"]
        )
        assert names == {"PolicyAgent"}
        assert unmatched == {"some/new/file.py::helper"}

    def test_no_locations_still_selects_the_always_run_floor(
        self, real_map: TraceMap
    ) -> None:
        assert real_map.select_for_locations([]) == real_map.always_run_ids()

    def test_published_locations_are_path_double_colon_qualname(
        self, real_map: TraceMap
    ) -> None:
        """The key shape both stages agree on, asserted on this side.

        A silent change to the separator would make every join match nothing,
        and matching nothing is the failure that skips the suite. So the shape
        is pinned rather than left to the reader of two docstrings.
        """
        for symbol in real_map.symbols:
            for location in symbol.locations:
                path, separator, qualname = location.partition("::")
                assert separator == "::"
                assert path.endswith(".py")
                assert qualname
                assert "::" not in qualname

    def test_stage_one_uses_the_same_module_qualname_sentinel(self) -> None:
        """Restated, not imported — so a divergence fails here, loudly.

        `MODULE_QUALNAME` is the one string the two stages must spell the same
        way for module-level changes to widen correctly. This module declares its
        own copy so it carries no import dependency on stage 1; this test is what
        stops the two copies drifting apart in silence.
        """
        from lab.selection import diff

        assert diff.MODULE_QUALNAME == MODULE_QUALNAME
        assert hasattr(diff.ChangedSymbol, "location")


# --------------------------------------------------------------------------- #
# Ordering is positional, never temporal
# --------------------------------------------------------------------------- #


class TestOrderingIsPositional:
    def test_timestamps_are_never_consulted(self, tmp_path: Path) -> None:
        """A trace with a broken clock yields the same dependency set.

        `ts` is not read anywhere in the derivation; events are consulted in
        file position order. This pins that, because a dependency set is a
        question about what happened, not about when.
        """
        payloads = [
            _event("agent_utterance", {"agent": "BookingAgent", "text": "a"}),
            _event("tool_call", {"name": "search_tables", "args": {}, "call_id": "1"}),
        ]
        forwards = [
            json.dumps({**json.loads(line), "ts": index}, sort_keys=True)
            for index, line in enumerate(payloads)
        ]
        backwards = [
            json.dumps({**json.loads(line), "ts": -index}, sort_keys=True)
            for index, line in enumerate(payloads)
        ]
        good = build_trace_map(
            fixture_root=_root(tmp_path / "fwd", "happy-a", forwards),
            corpus=[("happy-a", "happy")],
            source_roots=(),
        )
        broken = build_trace_map(
            fixture_root=_root(tmp_path / "back", "happy-a", backwards),
            corpus=[("happy-a", "happy")],
            source_roots=(),
        )
        assert good.by_id("happy-a").agents == broken.by_id("happy-a").agents
        assert good.by_id("happy-a").tools == broken.by_id("happy-a").tools

    def test_derived_collections_are_sorted(self, real_map: TraceMap) -> None:
        for entry in real_map.scenarios:
            for collection in (
                entry.agents,
                entry.tools,
                entry.engines,
                entry.adapters,
                entry.event_kinds,
                entry.handoff_edges,
            ):
                assert collection == sorted(collection)
        assert real_map.ids() == sorted(real_map.ids())


def _root(directory: Path, scenario_id: str, lines: list[str]) -> Path:
    _write_trace(directory / "t.jsonl", scenario_id, lines)
    return directory


# --------------------------------------------------------------------------- #
# No credential, no network, no model
# --------------------------------------------------------------------------- #


class TestOfflineAndDeterministic:
    def test_building_needs_no_environment(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A clean clone with every key unset must produce the same map."""
        import os

        for key in [k for k in os.environ if "KEY" in k or "TOKEN" in k]:
            monkeypatch.delenv(key, raising=False)
        built = build_trace_map(fixture_root=tmp_path, corpus=[("happy-a", "happy")])
        assert built.unmapped_count == 1

    def test_commit_is_unknown_rather_than_fatal_outside_git(
        self, monkeypatch: pytest.MonkeyPatch, real_map: TraceMap
    ) -> None:
        import lab.selection.trace_map as module

        def explode(*_args: object, **_kwargs: object) -> None:
            raise OSError("no git here")

        monkeypatch.setattr(module.subprocess, "run", explode)
        document = json.loads(render_trace_map(real_map))
        assert document[PROVENANCE_KEY]["commit"] == "unknown"


# --------------------------------------------------------------------------- #
# The entry point
# --------------------------------------------------------------------------- #


class TestEntryPoint:
    def test_default_invocation_prints_coverage_with_denominators(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main([]) == 0
        out = capsys.readouterr().out
        assert "scenarios mapped" in out
        assert "/" in out

    def test_check_passes_against_the_committed_artefact(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["--check"]) == 0
        assert "is current" in capsys.readouterr().out

    def test_check_fails_and_names_the_regeneration_command(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        missing = tmp_path / "absent.json"
        assert main(["--check", "--path", str(missing)]) == 1
        assert REGEN_COMMAND in capsys.readouterr().err

    def test_write_produces_a_loadable_artefact(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = tmp_path / "map.json"
        assert main(["--write", "--path", str(target), "--json"]) == 0
        capsys.readouterr()
        assert load_trace_map(target).corpus_size > 0

    def test_check_reports_a_scenario_whose_dependencies_moved(
        self, tmp_path: Path
    ) -> None:
        """The drift message names the row, so a reviewer knows where to look."""
        built = build_trace_map()
        stale = built.model_copy(deep=True)
        target = tmp_path / "stale.json"
        entry = next(e for e in stale.scenarios if e.mapped and e.tools)
        entry.tools = []
        target.write_text(render_trace_map(stale, commit="x"), encoding="utf-8")
        differences = check_trace_map(target, trace_map=built)
        assert any(entry.scenario_id in line for line in differences)
        assert differences[-1].endswith(REGEN_COMMAND)


# --------------------------------------------------------------------------- #
# Model-level guarantees
# --------------------------------------------------------------------------- #


class TestModels:
    def test_by_id_raises_a_useful_error(self, real_map: TraceMap) -> None:
        with pytest.raises(KeyError, match="no scenario"):
            real_map.by_id("nope")

    def test_symbols_are_agents_then_tools(self) -> None:
        entry = ScenarioDependencies(
            scenario_id="happy-a", mapped=True, agents=["A"], tools=["t"]
        )
        assert entry.symbols() == ["A", "t"]

    def test_artefact_rejects_unknown_fields(self) -> None:
        document = json.loads(DEFAULT_MAP_PATH.read_text(encoding="utf-8"))
        document.pop(PROVENANCE_KEY)
        document["surprise"] = 1
        with pytest.raises(ValueError):
            TraceMap.model_validate(document)
