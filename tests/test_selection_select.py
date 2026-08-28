"""Tests for the selector — the stage that is allowed to say *skip*.

The bias of this file is deliberate. Most of it is not "does the selector pick
the right scenarios" but "does the selector refuse to narrow when it should",
because a wrong inclusion costs money and a wrong exclusion ships a regression
behind a green run. Every fail-safe path therefore gets its own test on a
synthetic corpus rather than on the tidy committed one, so the rule is exercised
before the day it is needed.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from lab.selection import diff as diff_mod
from lab.selection import select as select_mod
from lab.selection import trace_map as trace_map_mod
from lab.selection.diff import (
    ChangedScenario,
    ChangedSymbol,
    ChangeKind,
    ChangeSet,
    FileChange,
    GlobalReason,
    GlobalTrigger,
    SymbolKind,
)
from lab.selection.select import (
    DEFAULT_OVERRIDES_PATH,
    ESCALATION_CODES,
    OverrideRule,
    OverrideRules,
    OverrideThen,
    OverrideWhen,
    ReasonCode,
    Verdict,
    _emit_runner_args,
    _related,
    _resolve_locations,
    calibrate,
    load_overrides,
    main,
    select,
)
from lab.selection.trace_map import (
    DEFAULT_MAP_PATH,
    ScenarioDependencies,
    SymbolLocation,
    TraceMap,
    load_trace_map,
)

# --------------------------------------------------------------------------- #
# Synthetic fixtures — a corpus small enough to reason about by hand
# --------------------------------------------------------------------------- #

AGENT_PATH = "tablemate/agents.py"
TOOL_PATH = "tablemate/tools.py"


def _map(*, degraded: bool = False) -> TraceMap:
    """Four scenarios: two mapped and distinct, one shared, one with no evidence."""
    scenarios = [
        ScenarioDependencies(
            scenario_id="happy-one",
            suite="happy",
            mapped=True,
            sessions=1,
            agents=["BookingAgent"],
            tools=["create_booking"],
        ),
        ScenarioDependencies(
            scenario_id="edge-one",
            suite="edge",
            mapped=True,
            sessions=1,
            agents=["PolicyAgent"],
            tools=["check_policy"],
        ),
        ScenarioDependencies(
            scenario_id="edge-two",
            suite="edge",
            mapped=True,
            sessions=1,
            agents=["BookingAgent", "PolicyAgent"],
            tools=["check_policy", "create_booking"],
        ),
        ScenarioDependencies(
            scenario_id="audio-one",
            suite="audio",
            mapped=False,
            unmapped_reason="no committed trace names this scenario",
        ),
    ]
    symbols = [
        SymbolLocation(
            name="BookingAgent",
            kind="agent",
            resolved=True,
            locations=[f"{AGENT_PATH}::BookingAgent"],
        ),
        SymbolLocation(
            name="PolicyAgent",
            kind="agent",
            resolved=True,
            locations=[f"{AGENT_PATH}::PolicyAgent"],
        ),
        SymbolLocation(
            name="check_policy",
            kind="tool",
            resolved=True,
            locations=[f"{TOOL_PATH}::check_policy"],
        ),
        SymbolLocation(
            name="create_booking",
            kind="tool",
            resolved=True,
            locations=[f"{TOOL_PATH}::create_booking"],
        ),
    ]
    return TraceMap(
        corpus_size=len(scenarios),
        mapped_count=3,
        unmapped_count=1,
        session_count=3,
        trace_file_count=3,
        degraded=degraded,
        degraded_reasons=["synthetic"] if degraded else [],
        scenarios=scenarios,
        symbols=symbols,
    )


def _change(*locations: str, files: tuple[str, ...] | None = None, **kwargs) -> ChangeSet:
    """A ChangeSet holding exactly the named `path::qualname` symbols."""
    symbols = []
    paths = []
    for location in locations:
        path, _, qualname = location.partition("::")
        paths.append(path)
        symbols.append(
            ChangedSymbol(
                path=path,
                qualname=qualname,
                kind=(
                    SymbolKind.MODULE
                    if qualname == diff_mod.MODULE_QUALNAME
                    else SymbolKind.FUNCTION
                ),
                change=ChangeKind.MODIFIED,
                reason="test",
            )
        )
    named = files if files is not None else tuple(sorted(set(paths)))
    return ChangeSet(
        base_ref="BASE",
        head_ref="HEAD",
        repo_root="/repo",
        files=tuple(FileChange(path=p, change=ChangeKind.MODIFIED) for p in named),
        symbols=tuple(symbols),
        **kwargs,
    )


def _select(change_set: ChangeSet, **kwargs):
    """Always with overrides off unless a test is about overrides."""
    kwargs.setdefault("trace_map", _map())
    kwargs.setdefault("overrides", OverrideRules())
    kwargs.setdefault("overrides_path", None)
    return select(change_set=change_set, **kwargs)


# --------------------------------------------------------------------------- #
# The happy path: it does narrow, and it says why
# --------------------------------------------------------------------------- #


def test_a_tool_change_selects_only_its_users_plus_the_always_run_floor():
    selection = _select(_change(f"{TOOL_PATH}::check_policy"))
    assert selection.verdict is Verdict.SUBSET
    assert set(selection.selected_ids) == {"edge-one", "edge-two", "audio-one"}
    assert selection.excluded_ids == ("happy-one",)


def test_every_selected_scenario_carries_a_reason():
    selection = _select(_change(f"{TOOL_PATH}::check_policy"))
    for decision in selection.selected:
        assert decision.reasons
        assert all(reason.detail for reason in decision.reasons)


def test_the_reason_names_the_symbol_the_evidence_matched():
    selection = _select(_change(f"{TOOL_PATH}::check_policy"))
    reason = next(d for d in selection.selected if d.scenario_id == "edge-one").reasons[0]
    assert reason.code is ReasonCode.TRACE_DEPENDENCY
    assert "check_policy" in reason.detail


def test_the_unmapped_scenario_is_included_for_a_change_it_cannot_touch():
    """The whole point of the floor: no evidence is not evidence of absence."""
    selection = _select(_change(f"{TOOL_PATH}::create_booking"))
    audio = next(d for d in selection.decisions if d.scenario_id == "audio-one")
    assert audio.selected
    assert audio.reasons[0].code is ReasonCode.UNMAPPED_SCENARIO


def test_two_changed_symbols_union_rather_than_intersect():
    selection = _select(
        _change(f"{TOOL_PATH}::check_policy", f"{TOOL_PATH}::create_booking")
    )
    assert set(selection.selected_ids) == {"happy-one", "edge-one", "edge-two", "audio-one"}


def test_the_selection_explains_what_it_excluded_and_on_what_basis():
    selection = _select(_change(f"{TOOL_PATH}::check_policy"))
    summary = " ".join(selection.exclusion_summary())
    assert "1/4 excluded" in summary
    assert ReasonCode.NO_OVERLAP.value in summary
    assert "check_policy" in summary


def test_excluded_scenarios_carry_the_no_overlap_reason():
    selection = _select(_change(f"{TOOL_PATH}::check_policy"))
    excluded = next(d for d in selection.decisions if d.scenario_id == "happy-one")
    assert excluded.reasons[0].code is ReasonCode.NO_OVERLAP


def test_saved_fraction_is_a_pair_not_a_percentage():
    selection = _select(_change(f"{TOOL_PATH}::check_policy"))
    assert selection.saved_fraction() == (1, 4)


# --------------------------------------------------------------------------- #
# Rule A, one test per ambiguity: when unsure, include
# --------------------------------------------------------------------------- #


def test_a_global_trigger_selects_everything():
    change = _change(f"{TOOL_PATH}::check_policy")
    change = ChangeSet(
        base_ref=change.base_ref,
        head_ref=change.head_ref,
        repo_root=change.repo_root,
        files=change.files + (FileChange(path="pyproject.toml", change=ChangeKind.MODIFIED),),
        symbols=change.symbols,
        globals=(
            GlobalTrigger(
                path="pyproject.toml",
                reason=GlobalReason.PACKAGING,
                detail="packaging changed",
            ),
        ),
    )
    selection = _select(change)
    assert selection.verdict is Verdict.EVERYTHING
    assert len(selection.selected_ids) == 4
    assert selection.escalations[0].code is ReasonCode.GLOBAL_TRIGGER


def test_a_symbol_the_map_cannot_place_selects_everything():
    selection = _select(_change("lab/report/render.py::render"))
    assert selection.verdict is Verdict.EVERYTHING
    assert {e.code for e in selection.escalations} == {ReasonCode.UNPLACEABLE_CHANGE}
    assert selection.unplaceable_paths == ("lab/report/render.py",)


def test_an_unplaceable_change_escalates_once_per_path_not_once_per_symbol():
    """Otherwise a 1,400-symbol diff produces a report nobody can read."""
    selection = _select(
        _change(
            "lab/report/render.py::a",
            "lab/report/render.py::b",
            "lab/report/render.py::c",
        )
    )
    assert len(selection.escalations) == 1
    assert "3 changed symbol(s)" in selection.escalations[0].detail


def test_one_placeable_symbol_does_not_rescue_an_unplaceable_one():
    selection = _select(
        _change(f"{TOOL_PATH}::check_policy", "lab/report/render.py::render")
    )
    assert selection.verdict is Verdict.EVERYTHING


def test_a_degraded_map_selects_everything():
    selection = _select(
        _change(f"{TOOL_PATH}::check_policy"), trace_map=_map(degraded=True)
    )
    assert selection.verdict is Verdict.EVERYTHING
    assert selection.escalations[0].code is ReasonCode.MAP_DEGRADED


def test_a_missing_map_selects_everything_and_names_the_regen_command(tmp_path):
    selection = select(
        change_set=_change(f"{TOOL_PATH}::check_policy"),
        map_path=tmp_path / "absent.json",
        overrides=OverrideRules(),
        overrides_path=None,
    )
    assert selection.verdict is Verdict.EVERYTHING
    assert selection.escalations[0].code is ReasonCode.MAP_MISSING
    assert "trace_map --write" in selection.escalations[0].detail


def test_an_unreadable_map_selects_everything(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    selection = select(
        change_set=_change(f"{TOOL_PATH}::check_policy"),
        map_path=broken,
        overrides=OverrideRules(),
        overrides_path=None,
    )
    assert selection.verdict is Verdict.EVERYTHING
    assert selection.escalations[0].code is ReasonCode.MAP_UNREADABLE


def test_a_changed_file_stage_one_accounted_for_nowhere_selects_everything():
    change = _change(
        f"{TOOL_PATH}::check_policy", files=(TOOL_PATH, "mystery/thing.bin")
    )
    selection = _select(change)
    assert selection.verdict is Verdict.EVERYTHING
    assert selection.escalations[0].code is ReasonCode.UNACCOUNTED_FILE
    assert selection.escalations[0].subject == "mystery/thing.bin"


def test_git_unavailable_selects_everything_even_though_it_reports_no_files():
    """The regression this file exists for.

    Stage 1 signals an unreachable git with a global trigger and an EMPTY file
    list. Reading "no files" as "nothing to run" before looking at the triggers
    would turn the loudest failure stage 1 has into a silent empty selection.
    """
    change = ChangeSet(
        base_ref="HEAD~1",
        head_ref="<working tree>",
        repo_root="/repo",
        globals=(
            GlobalTrigger(
                path="<repository>",
                reason=GlobalReason.GIT_UNAVAILABLE,
                detail="could not read the diff",
            ),
        ),
    )
    selection = _select(change)
    assert selection.verdict is Verdict.EVERYTHING
    assert len(selection.selected_ids) == 4


def _direct(scenario_id: str, path: str = "scenarios/happy/x.yaml") -> ChangeSet:
    return ChangeSet(
        base_ref="BASE",
        head_ref="HEAD",
        repo_root="/repo",
        files=(FileChange(path=path, change=ChangeKind.ADDED),),
        scenarios=(
            ChangedScenario(
                scenario_id=scenario_id,
                change=ChangeKind.ADDED,
                path=path,
                reason="scenario file changed",
            ),
        ),
    )


def test_a_scenario_whose_own_file_changed_is_selected_directly():
    selection = _select(_direct("happy-one"))
    assert selection.verdict is Verdict.SUBSET
    decision = next(d for d in selection.decisions if d.scenario_id == "happy-one")
    assert decision.reasons[0].code is ReasonCode.DIRECT_CHANGE


def test_a_directly_touched_id_that_is_not_a_corpus_row_selects_everything():
    """The 103/73 bug.

    Stage 1 recovers scenario ids from committed trace filenames as well as from
    scenario YAML, and a per-repeat trace is named `<id>-0`. Those are not corpus
    rows. Counting them as selections produced a nonsense denominator, and
    passing one to the runner's `--scenario` makes the runner exit rather than
    run. Stripping the `-0` to guess which row was meant is exactly the kind of
    guess that can only ever exclude the wrong thing, so this escalates instead.
    """
    selection = _select(_direct("happy-one-0", path="fixtures/live/traces/x.jsonl"))
    assert selection.verdict is Verdict.EVERYTHING
    assert selection.escalations[0].code is ReasonCode.DIRECT_UNKNOWN_SCENARIO
    assert "happy-one-0" in selection.escalations[0].detail


def test_no_selection_can_ever_name_a_scenario_the_corpus_does_not_hold():
    """`--scenario <unknown>` is a runner crash, not a smaller run."""
    for change in (
        _direct("happy-one-0", path="fixtures/live/traces/x.jsonl"),
        _direct("happy-one"),
        _change(f"{TOOL_PATH}::check_policy"),
    ):
        selection = _select(change)
        assert set(selection.selected_ids) <= {
            "happy-one",
            "edge-one",
            "edge-two",
            "audio-one",
        }
        assert len(selection.selected_ids) <= selection.corpus_size


def test_the_denominator_is_always_the_corpus():
    for change in (
        _direct("happy-one-0", path="fixtures/live/traces/x.jsonl"),
        _change(f"{TOOL_PATH}::check_policy"),
    ):
        selection = _select(change)
        assert selection.corpus_size == 4
        assert len(selection.decisions) == 4


def test_every_escalation_code_is_declared_as_an_escalation():
    """A code that escalates but is missing from ESCALATION_CODES is invisible."""
    produced = {
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
    assert produced == set(ESCALATION_CODES)


# --------------------------------------------------------------------------- #
# The mirror image: an empty answer is stated, never implied
# --------------------------------------------------------------------------- #


def test_an_empty_diff_selects_nothing_and_says_so():
    change = ChangeSet(base_ref="BASE", head_ref="HEAD", repo_root="/repo")
    selection = _select(change)
    assert selection.verdict is Verdict.NOTHING
    assert selection.selected_ids == ()
    assert all(d.reasons[0].code is ReasonCode.NO_CHANGES for d in selection.decisions)
    assert "NOTHING TO RUN" in selection.explain()


def test_a_documentation_only_diff_selects_nothing_for_a_different_stated_reason():
    change = ChangeSet(
        base_ref="BASE",
        head_ref="HEAD",
        repo_root="/repo",
        files=(FileChange(path="README.md", change=ChangeKind.MODIFIED),),
        inert=("README.md",),
    )
    selection = _select(change)
    assert selection.verdict is Verdict.NOTHING
    assert all(
        d.reasons[0].code is ReasonCode.NO_RUNTIME_EFFECT for d in selection.decisions
    )


def test_an_empty_selection_emits_no_runner_arguments():
    """`evallab run $(...)` with no arguments would run the whole suite."""
    change = ChangeSet(base_ref="BASE", head_ref="HEAD", repo_root="/repo")
    assert _select(change).runner_args() == []


# --------------------------------------------------------------------------- #
# The join: nested qualnames and module-level code
# --------------------------------------------------------------------------- #


def test_a_changed_method_body_resolves_to_its_class():
    selection = _select(_change(f"{AGENT_PATH}::PolicyAgent.handle"))
    assert selection.verdict is Verdict.SUBSET
    assert set(selection.selected_ids) == {"edge-one", "edge-two", "audio-one"}


def test_a_changed_nested_function_resolves_to_its_enclosing_function():
    selection = _select(_change(f"{TOOL_PATH}::check_policy.inner"))
    assert set(selection.selected_ids) == {"edge-one", "edge-two", "audio-one"}


def test_a_similarly_named_sibling_is_not_mistaken_for_a_nested_name():
    """`check_policy_helper` is not part of `check_policy`; the dot matters."""
    assert not _related("check_policy", "check_policy_helper")
    assert _related("check_policy", "check_policy.inner")
    assert _related("PolicyAgent.handle", "PolicyAgent")


def test_module_level_code_widens_to_every_name_in_the_file():
    selection = _select(_change(f"{TOOL_PATH}::{diff_mod.MODULE_QUALNAME}"))
    assert set(selection.selected_ids) == {"happy-one", "edge-one", "edge-two", "audio-one"}


def test_the_resolver_agrees_with_the_maps_own_published_translation():
    """Stage 2 publishes `names_for_locations`; a divergence must fail loudly."""
    trace_map = load_trace_map(DEFAULT_MAP_PATH)
    locations = [
        f"{TOOL_PATH}::check_policy",
        f"{TOOL_PATH}::{diff_mod.MODULE_QUALNAME}",
        f"{AGENT_PATH}::PolicyAgent",
        "lab/report/render.py::render",
    ]
    placed, unplaceable = _resolve_locations(trace_map, locations)
    names, unmatched = trace_map.names_for_locations(locations)
    assert {n for group in placed.values() for n in group} == names
    assert set(unplaceable) == unmatched


def test_the_two_stages_agree_on_the_module_qualname_string():
    """Both modules restate it rather than importing; a rename must be caught."""
    assert diff_mod.MODULE_QUALNAME == trace_map_mod.MODULE_QUALNAME == "<module>"


# --------------------------------------------------------------------------- #
# The override file: additive by construction
# --------------------------------------------------------------------------- #


def _rule(**then) -> OverrideRules:
    return OverrideRules(
        rules=[
            OverrideRule(
                id="r1",
                reason="a config value the traces cannot see",
                when=OverrideWhen(paths=[f"{TOOL_PATH}"]),
                then=OverrideThen(**then),
            )
        ]
    )


def test_an_override_widens_by_explicit_scenario_id():
    base = _select(_change(f"{TOOL_PATH}::check_policy"))
    widened = _select(
        _change(f"{TOOL_PATH}::check_policy"), overrides=_rule(scenarios=["happy-one"])
    )
    assert set(base.selected_ids) < set(widened.selected_ids)
    assert "happy-one" in widened.selected_ids
    assert widened.overrides_fired == ("r1",)


def test_an_override_widens_by_suite():
    widened = _select(
        _change(f"{TOOL_PATH}::check_policy"), overrides=_rule(suites=["happy"])
    )
    assert "happy-one" in widened.selected_ids


def test_an_override_widens_by_tag_using_a_supplied_tag_index():
    widened = _select(
        _change(f"{TOOL_PATH}::check_policy"),
        overrides=_rule(tags=["booking"]),
        tags_for={"happy-one": {"booking"}, "edge-one": set()},
    )
    assert "happy-one" in widened.selected_ids


def test_an_override_may_refuse_to_narrow_at_all():
    widened = _select(
        _change(f"{TOOL_PATH}::check_policy"), overrides=_rule(everything=True)
    )
    assert widened.verdict is Verdict.EVERYTHING
    assert widened.escalations[0].code is ReasonCode.OVERRIDE_EVERYTHING


def test_an_override_can_never_shrink_a_selection():
    """The post-condition, checked over every `then:` the schema can express."""
    change = _change(f"{TOOL_PATH}::check_policy")
    base = set(_select(change).selected_ids)
    for then in (
        {"scenarios": ["happy-one"]},
        {"suites": ["happy"]},
        {"scenarios": ["edge-one"]},
        {"suites": ["audio"]},
    ):
        widened = set(_select(change, overrides=_rule(**then)).selected_ids)
        assert base <= widened, then


def test_the_schema_has_no_way_to_express_a_removal():
    """`extra="forbid"`: an invented exclusion key is an error, not a no-op."""
    for key in ("exclude", "skip", "remove", "not_scenarios"):
        with pytest.raises(ValidationError):
            OverrideThen.model_validate({"scenarios": ["a"], key: ["b"]})


def test_a_rule_must_say_when_and_what():
    with pytest.raises(ValidationError):
        OverrideWhen.model_validate({})
    with pytest.raises(ValidationError):
        OverrideThen.model_validate({})


def test_a_rule_must_carry_a_reason():
    with pytest.raises(ValidationError):
        OverrideRule.model_validate(
            {"id": "r", "when": {"paths": ["a"]}, "then": {"suites": ["happy"]}}
        )


def test_duplicate_rule_ids_are_rejected():
    body = {
        "rules": [
            {"id": "r", "reason": "x", "when": {"paths": ["a"]}, "then": {"suites": ["happy"]}},
            {"id": "r", "reason": "y", "when": {"paths": ["b"]}, "then": {"suites": ["edge"]}},
        ]
    }
    with pytest.raises(ValidationError):
        OverrideRules.model_validate(body)


def test_a_rule_that_names_a_scenario_the_corpus_lost_selects_everything():
    """A stale widening is still an ambiguity, and staleness must be expensive."""
    widened = _select(
        _change(f"{TOOL_PATH}::check_policy"), overrides=_rule(scenarios=["gone-away"])
    )
    assert widened.verdict is Verdict.EVERYTHING
    assert widened.escalations[0].code is ReasonCode.OVERRIDE_UNKNOWN_TARGET


def test_a_rule_that_does_not_match_the_change_does_not_fire():
    widened = _select(
        _change(f"{AGENT_PATH}::PolicyAgent"), overrides=_rule(suites=["happy"])
    )
    assert widened.overrides_fired == ()
    assert "happy-one" not in widened.selected_ids


def test_a_rule_can_fire_on_a_runtime_name():
    rules = OverrideRules(
        rules=[
            OverrideRule(
                id="by-name",
                reason="shared prompt fragment",
                when=OverrideWhen(symbols=["check_policy"]),
                then=OverrideThen(suites=["happy"]),
            )
        ]
    )
    widened = _select(_change(f"{TOOL_PATH}::check_policy"), overrides=rules)
    assert widened.overrides_fired == ("by-name",)


def test_a_rule_can_fire_on_a_location_glob():
    rules = OverrideRules(
        rules=[
            OverrideRule(
                id="by-location",
                reason="everything in this class shares state",
                when=OverrideWhen(locations=[f"{AGENT_PATH}::PolicyAgent*"]),
                then=OverrideThen(suites=["happy"]),
            )
        ]
    )
    widened = _select(_change(f"{AGENT_PATH}::PolicyAgent.handle"), overrides=rules)
    assert widened.overrides_fired == ("by-location",)


def test_a_missing_override_file_is_not_an_error(tmp_path):
    rules, error = load_overrides(tmp_path / "absent.yaml")
    assert error is None
    assert rules.rules == []


def test_an_unparseable_override_file_selects_everything(tmp_path):
    broken = tmp_path / "overrides.yaml"
    broken.write_text("rules: [{id: r, then: {suites: [happy]}}]\n", encoding="utf-8")
    selection = select(
        change_set=_change(f"{TOOL_PATH}::check_policy"),
        trace_map=_map(),
        overrides_path=broken,
    )
    assert selection.verdict is Verdict.EVERYTHING
    assert selection.escalations[0].code is ReasonCode.OVERRIDES_UNREADABLE


def test_an_override_file_that_is_not_a_mapping_selects_everything(tmp_path):
    broken = tmp_path / "overrides.yaml"
    broken.write_text("- just\n- a list\n", encoding="utf-8")
    selection = select(
        change_set=_change(f"{TOOL_PATH}::check_policy"),
        trace_map=_map(),
        overrides_path=broken,
    )
    assert selection.verdict is Verdict.EVERYTHING


def test_the_committed_override_file_parses_and_declares_nothing():
    """An empty rule list is the healthy state; the file exists to be documented."""
    rules, error = load_overrides(DEFAULT_OVERRIDES_PATH)
    assert error is None
    assert rules.rules == []


# --------------------------------------------------------------------------- #
# Output modes
# --------------------------------------------------------------------------- #


def test_runner_args_emit_only_scenario_filters():
    """The runner ANDs its filters, so a --suite beside a --scenario could shrink."""
    args = _select(_change(f"{TOOL_PATH}::check_policy")).runner_args()
    assert set(args[::2]) == {"--scenario"}
    assert args[1::2] == ["audio-one", "edge-one", "edge-two"]


def test_runner_args_cover_exactly_the_selected_ids():
    selection = _select(_change(f"{TOOL_PATH}::check_policy"))
    args = selection.runner_args()
    assert args[1::2] == list(selection.selected_ids)


def test_to_dict_is_json_serialisable_and_carries_the_exclusions():
    selection = _select(_change(f"{TOOL_PATH}::check_policy"))
    body = json.loads(json.dumps(selection.to_dict()))
    assert body["verdict"] == "subset"
    assert body["excluded_ids"] == ["happy-one"]
    assert body["counts"]["corpus_size"] == 4
    assert body["exclusion_summary"]


def test_explain_truncates_the_listing_but_never_the_counts():
    selection = _select(_change(f"{TOOL_PATH}::{diff_mod.MODULE_QUALNAME}"))
    text = selection.explain(limit=1)
    assert "selected 4/4" in text
    assert "... 3 more" in text


def test_the_report_is_deterministic():
    change = _change(f"{TOOL_PATH}::check_policy")
    assert _select(change).explain() == _select(change).explain()


# --------------------------------------------------------------------------- #
# Rule B: the grader carries a measured number
# --------------------------------------------------------------------------- #


def test_calibration_of_the_committed_map_keeps_every_evidence_pair():
    measured = calibrate(map_path=DEFAULT_MAP_PATH)
    assert measured.pairs_total > 0
    assert measured.pairs_preserved == measured.pairs_total
    assert measured.recall == 1.0


def test_calibration_keeps_the_always_run_floor_in_every_probe():
    measured = calibrate(map_path=DEFAULT_MAP_PATH)
    assert measured.floor_total > 0
    assert measured.floor_preserved == measured.floor_total


def test_calibration_controls_all_pass():
    measured = calibrate(map_path=DEFAULT_MAP_PATH)
    assert measured.control_failures == ()
    assert measured.controls_passed == measured.controls_total


def test_a_perfect_recall_is_not_bought_by_selecting_everything():
    measured = calibrate(map_path=DEFAULT_MAP_PATH)
    assert measured.mean_selected < measured.corpus_size


def test_calibration_refuses_to_pass_above_an_unreachable_threshold():
    measured = calibrate(map_path=DEFAULT_MAP_PATH)
    assert measured.passed(min_recall=1.0)
    assert not measured.passed(min_recall=1.1)


def test_calibration_reports_every_rate_with_its_denominator():
    lines = "\n".join(calibrate(map_path=DEFAULT_MAP_PATH).summary_lines())
    assert "/" in lines
    for label in ("evidence pairs kept", "always-run floor kept", "controls passed"):
        assert label in lines


def test_calibration_probes_the_nested_qualname_form_too():
    """The commonest real change is a method body, so it must be measured."""
    measured = calibrate(map_path=DEFAULT_MAP_PATH)
    assert any("__probe__" in probe.location for probe in measured.probes)


def test_calibration_ignores_the_override_file():
    """Otherwise one `everything: true` rule buys a perfect score."""
    measured = calibrate(trace_map=_map())
    assert measured.mean_selected < measured.corpus_size


# --------------------------------------------------------------------------- #
# The entry point
# --------------------------------------------------------------------------- #


def test_cli_text_report_runs_against_the_real_repository(capsys):
    assert main(["--changed-since", "HEAD", "--no-overrides"]) in (0, 2)
    assert "selection" in capsys.readouterr().out


def test_cli_json_mode_emits_only_json(capsys):
    main(["--changed-since", "HEAD", "--no-overrides", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert "verdict" in body and "exclusion_summary" in body


def test_cli_calibrate_passes_and_exits_zero(capsys):
    assert main(["--calibrate"]) == 0
    assert "recall 1.000" in capsys.readouterr().out


def test_cli_calibrate_refuses_to_gate_below_threshold(capsys):
    assert main(["--calibrate", "--min-recall", "1.5"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_cli_calibrate_json_mode(capsys):
    main(["--calibrate", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["pairs_preserved"] == body["pairs_total"]


def test_cli_runner_args_mode_refuses_to_print_an_empty_line(capsys, monkeypatch):
    """Exit 2 and an empty stdout, so `$(...)` cannot become an unfiltered run."""
    empty = ChangeSet(base_ref="HEAD", head_ref="HEAD", repo_root="/repo")
    monkeypatch.setattr("lab.selection.select.analyse_changes", lambda *a, **k: empty)
    assert main(["--runner-args", "--no-overrides"]) == 2
    captured = capsys.readouterr()
    assert captured.out.strip() == ""
    assert "nothing to run" in captured.err


def test_cli_runner_args_mode_emits_one_line_of_scenario_filters(capsys, monkeypatch):
    monkeypatch.setattr(
        "lab.selection.select.analyse_changes",
        lambda *a, **k: _change(f"{TOOL_PATH}::check_policy"),
    )
    assert main(["--runner-args", "--no-overrides"]) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith("--scenario ")
    assert "\n" not in line


def test_cli_exits_two_when_there_is_nothing_to_run(capsys, monkeypatch):
    empty = ChangeSet(base_ref="HEAD", head_ref="HEAD", repo_root="/repo")
    monkeypatch.setattr("lab.selection.select.analyse_changes", lambda *a, **k: empty)
    assert main(["--no-overrides"]) == 2


# --------------------------------------------------------------------------- #
# It must work in a clean clone with every key unset
# --------------------------------------------------------------------------- #


def test_selection_needs_no_credential_and_opens_no_socket(monkeypatch):
    for key in list(__import__("os").environ):
        if any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "AZURE", "OPENAI")):
            monkeypatch.delenv(key, raising=False)

    import socket

    def _refuse(*args, **kwargs):  # pragma: no cover - only runs if something tries
        raise AssertionError("the selector opened a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    selection = select(
        change_set=_change(f"{TOOL_PATH}::check_policy"),
        map_path=DEFAULT_MAP_PATH,
        overrides_path=DEFAULT_OVERRIDES_PATH,
    )
    assert selection.verdict is Verdict.SUBSET


def test_a_valid_override_file_is_read_from_disk_and_widens(tmp_path):
    """The YAML path end to end, not just the models."""
    path = tmp_path / "overrides.yaml"
    path.write_text(
        "version: 1\n"
        "rules:\n"
        "  - id: shared-fragment\n"
        "    reason: a prompt fragment no trace records\n"
        "    when:\n"
        f"      paths: ['{TOOL_PATH}']\n"
        "    then:\n"
        "      suites: [happy]\n",
        encoding="utf-8",
    )
    change = _change(f"{TOOL_PATH}::check_policy")
    base = set(select(change_set=change, trace_map=_map(), overrides_path=None).selected_ids)
    widened = select(change_set=change, trace_map=_map(), overrides_path=path)
    assert widened.overrides_fired == ("shared-fragment",)
    assert base < set(widened.selected_ids)
    assert "happy-one" in widened.selected_ids


def test_the_real_map_and_the_real_overrides_narrow_a_real_tool_change():
    """The worked figure quoted in the documentation, reproduced as a test.

    A one-line edit inside `check_policy`: 30/73 selected, 43/73 skipped, of
    which 18 are the always-run audio floor.
    """
    selection = select(
        change_set=_change(f"{TOOL_PATH}::check_policy"),
        map_path=DEFAULT_MAP_PATH,
        overrides_path=DEFAULT_OVERRIDES_PATH,
    )
    assert selection.corpus_size == 73
    assert len(selection.selected_ids) == 30
    assert len(selection.excluded_ids) == 43
    assert selection.counts()["selected_unmapped-scenario"] == 18
    assert selection.counts()["selected_trace-dependency"] == 12


def test_the_real_map_narrows_an_agent_method_change():
    """The second documented figure: `PolicyAgent.handle`, 34/73."""
    selection = select(
        change_set=_change(f"{AGENT_PATH}::PolicyAgent.handle"),
        map_path=DEFAULT_MAP_PATH,
        overrides_path=DEFAULT_OVERRIDES_PATH,
    )
    assert len(selection.selected_ids) == 34
    assert len(selection.excluded_ids) == 39


# --------------------------------------------------------------------------- #
# Emitting arguments a runner can actually accept
#
# The selection spans ALL_SUITES; `evallab run` loads only its default corpus.
# An id outside that corpus is not merely ignored by the runner - it aborts the
# command with "no such scenario(s)", so the whole run produces nothing. Emitting
# the full list is therefore not the cautious choice; it is the choice that ends
# with an operator deleting ids by hand until the command starts, which is how an
# always-run floor gets quietly deleted.
# --------------------------------------------------------------------------- #


def _emit(selection, runnable, monkeypatch):
    """Run the emit path against a stated runner corpus."""
    monkeypatch.setattr(
        select_mod, "runner_corpus_ids", lambda: None if runnable is None else frozenset(runnable)
    )
    return _emit_runner_args(selection)


def _text_half(selection):
    return {i for i in selection.selected_ids if not i.startswith("audio")}


def test_ids_outside_the_runners_corpus_are_withheld_from_its_arguments(
    capsys, monkeypatch
):
    selection = _select(_change(f"{TOOL_PATH}::check_policy"))
    addressable = _text_half(selection)
    assert _emit(selection, addressable, monkeypatch) == 0
    out = capsys.readouterr()
    emitted = {a for a in out.out.split() if a != "--scenario"}
    assert emitted == addressable
    assert not any(i.startswith("audio") for i in emitted)


def test_a_withheld_id_is_always_reported_never_silently_dropped(capsys, monkeypatch):
    """The one thing that would make this a skip is doing it quietly."""
    selection = _select(_change(f"{TOOL_PATH}::check_policy"))
    withheld = [i for i in selection.selected_ids if i.startswith("audio")]
    assert withheld, "fixture must hold an unaddressable id for this to mean anything"
    _emit(selection, _text_half(selection), monkeypatch)
    err = capsys.readouterr().err
    assert f"{len(withheld)}/{len(selection.selected_ids)}" in err
    assert "still need running" in err
    for scenario_id in withheld:
        assert scenario_id in err


def test_an_unloadable_runner_corpus_emits_nothing_and_refuses(capsys, monkeypatch):
    """Unknown boundary -> no arguments -> an unfiltered run. Over-running is safe."""
    selection = _select(_change(f"{TOOL_PATH}::check_policy"))
    assert _emit(selection, None, monkeypatch) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "would not load" in out.err


def test_a_selection_entirely_outside_the_runners_corpus_refuses(capsys, monkeypatch):
    """An empty argument list is an unfiltered run, so it must never be printed."""
    selection = _select(_change(f"{TOOL_PATH}::check_policy"))
    assert _emit(selection, set(), monkeypatch) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "unfiltered run" in out.err


def test_the_partition_loses_nothing_and_invents_nothing():
    selection = _select(_change(f"{TOOL_PATH}::check_policy"))
    addressable, deferred = selection.partition_for_runner(_text_half(selection))
    assert selection.selected_ids
    assert set(addressable) | set(deferred) == set(selection.selected_ids)
    assert not set(addressable) & set(deferred)


def test_an_unknown_id_in_the_runner_corpus_adds_nothing_to_the_selection():
    """The runner's corpus bounds the emit, and may never widen the selection."""
    selection = _select(_change(f"{TOOL_PATH}::check_policy"))
    addressable, _ = selection.partition_for_runner(
        set(selection.selected_ids) | {"not-a-scenario"}
    )
    assert "not-a-scenario" not in addressable


def test_the_real_runner_corpus_is_a_subset_of_the_selectors_corpus():
    """If this ever inverts, the selector is blind to rows the runner can drive."""
    from scenarios.loader import ALL_SUITES, load_corpus

    runnable = select_mod.runner_corpus_ids()
    assert runnable is not None
    everything = {s.id for s in load_corpus(suites=ALL_SUITES).scenarios}
    assert runnable <= everything
    assert runnable, "an empty runner corpus would make --runner-args always refuse"
