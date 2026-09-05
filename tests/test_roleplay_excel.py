"""The Excel corpus: a workbook must be able to say exactly what a YAML file says.

The headline test is the round trip. Every committed scenario is exported to a
workbook, read back, and compared field by field against the model the YAML loader
produced. A field this module forgets to carry fails here, loudly, with the field
named — which is the only way a second authoring format stays trustworthy.

Everything else guards the seam: an Excel row gets no validation of its own, so a
bad row must fail with the *model's* error rather than being quietly accepted, and
a workbook must not be able to express something the YAML corpus could not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl", reason="the Excel corpus needs the [excel] extra")

from roleplay import excel_corpus as xl
from roleplay.corpus import Scenario, load_corpus

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def corpus():
    return load_corpus()


@pytest.fixture(scope="module")
def workbook(tmp_path_factory, corpus):
    path = tmp_path_factory.mktemp("excel") / "scenarios.xlsx"
    xl.export_workbook(path, corpus=corpus)
    return path


def test_the_round_trip_preserves_every_field_of_every_scenario(workbook, corpus) -> None:
    original = {s.id: s for s in corpus.scenarios}
    restored = {s.id: s for s in xl.read_scenarios(workbook)}
    assert set(restored) == set(original), "the workbook lost or invented a scenario"

    differences: list[str] = []
    for sid, before in original.items():
        after = restored[sid]
        for field in Scenario.model_fields:
            if field == "source":  # the path it was loaded from, by construction
                continue
            if getattr(before, field) != getattr(after, field):
                differences.append(f"{sid}.{field}: {getattr(before, field)!r} != {getattr(after, field)!r}")
    assert not differences, "fields lost in the round trip:\n  " + "\n  ".join(differences[:20])


def test_the_workbook_covers_every_committed_scenario(workbook, corpus) -> None:
    wb = openpyxl.load_workbook(workbook, read_only=True)
    try:
        rows = wb[xl.SHEET_SCENARIOS].max_row - 1
        turns = wb[xl.SHEET_TURNS].max_row - 1
    finally:
        wb.close()
    assert rows == len(corpus.scenarios)
    assert turns == sum(len(s.trainee.turns) for s in corpus.scenarios)


def test_every_sheet_the_reader_needs_is_present(workbook) -> None:
    wb = openpyxl.load_workbook(workbook, read_only=True)
    try:
        names = set(wb.sheetnames)
    finally:
        wb.close()
    assert {xl.SHEET_SCENARIOS, xl.SHEET_TURNS, xl.SHEET_ASSERTIONS, xl.SHEET_REFERENCE} <= names


def _minimal_rows():
    return (
        {
            "id": "pitch-a-worked-example",
            "suite": "pitch",
            "title": "A worked example",
            "customer": "cautious_saver",
            "tags": "discovery, closing",
            "role": "retail investment adviser",
            "human_verdict": "pass",
            "reason": "because the disclosures were given",
        },
        ["What would you want this money to do?", "Shall we get the paperwork started?"],
        [
            {"scenario_id": "pitch-a-worked-example", "kind": "tool_expected", "tool": "record_disclosure"},
            {"scenario_id": "pitch-a-worked-example", "kind": "min_calls", "tool": "record_disclosure", "value": 3},
        ],
    )


def test_a_row_becomes_the_same_mapping_the_yaml_parser_would_produce() -> None:
    row, turns, assertions = _minimal_rows()
    data = xl._mapping_from_rows(row, turns, assertions)
    scenario = Scenario(**data, suite="pitch", source="test")
    assert scenario.trainee.turns == tuple(turns)
    assert scenario.tags == ("discovery", "closing")
    assert scenario.tools.expected == ("record_disclosure",)
    assert scenario.tools.min_calls == {"record_disclosure": 3}
    assert scenario.expectation.human_verdict == "pass"


def test_an_unknown_assertion_kind_is_refused_by_name() -> None:
    row, turns, _ = _minimal_rows()
    bad = [{"scenario_id": row["id"], "kind": "make_it_pass", "tool": "score_session"}]
    with pytest.raises(ValueError, match="unknown assertion kind 'make_it_pass'"):
        xl._mapping_from_rows(row, turns, bad)


def test_a_bad_row_fails_with_the_models_own_error_not_a_second_rulebook() -> None:
    row, turns, assertions = _minimal_rows()
    row = {**row, "tags": "discovery, not-a-real-tag"}
    data = xl._mapping_from_rows(row, turns, assertions)
    with pytest.raises(Exception) as excinfo:
        Scenario(**data, suite="pitch", source="test")
    assert "not-a-real-tag" in str(excinfo.value)


def test_a_count_that_is_not_a_whole_number_is_refused() -> None:
    row, turns, _ = _minimal_rows()
    bad = [{"scenario_id": row["id"], "kind": "min_calls", "tool": "record_disclosure", "value": "three"}]
    with pytest.raises(ValueError, match="expected a whole number"):
        xl._mapping_from_rows(row, turns, bad)


def test_a_whole_float_from_excel_becomes_an_int() -> None:
    """Excel stores every number as a float. `total: 18.0` must not reach a model."""
    row, turns, _ = _minimal_rows()
    rows = [{"scenario_id": row["id"], "kind": "arg", "tool": "score_session", "arg": "total",
             "op": "eq", "value": 18.0}]
    data = xl._mapping_from_rows(row, turns, rows)
    value = data["tools"]["args"][0]["value"]
    assert value == 18 and isinstance(value, int), f"got {value!r}"


def test_a_value_free_operator_carries_no_value() -> None:
    row, turns, _ = _minimal_rows()
    rows = [{"scenario_id": row["id"], "kind": "arg", "tool": "score_session", "arg": "verdict",
             "op": "present", "value": ""}]
    data = xl._mapping_from_rows(row, turns, rows)
    assert "value" not in data["tools"]["args"][0]


def test_the_extra_json_column_carries_a_block_with_no_column_of_its_own() -> None:
    row, turns, assertions = _minimal_rows()
    row = {**row, "extra_json": json.dumps({"consistency": {"k": 3, "tolerance": 1.0}})}
    data = xl._mapping_from_rows(row, turns, assertions)
    scenario = Scenario(**data, suite="pitch", source="test")
    assert scenario.consistency is not None
    assert scenario.consistency.k == 3 and scenario.consistency.tolerance == 1.0


def test_an_id_that_does_not_match_its_suite_is_refused(tmp_path, corpus) -> None:
    """The same rule the YAML loader enforces through the directory name."""
    path = tmp_path / "wrong.xlsx"
    xl.export_workbook(path, corpus=corpus)
    wb = openpyxl.load_workbook(path)
    ws = wb[xl.SHEET_SCENARIOS]
    ws.cell(2, 2).value = "objection"  # a pitch row, relabelled into another suite
    wb.save(path)
    with pytest.raises(ValueError, match="must start with its suite prefix"):
        xl.read_scenarios(path)


def test_writing_yaml_from_an_unchanged_workbook_changes_nothing(workbook) -> None:
    """The property that makes the review loop work: an untouched round trip is a no-op."""
    result = xl.write_yaml(workbook, dry_run=True)
    changed = {k: v for k, v in result.items() if v != "unchanged"}
    assert not changed, (
        "exporting and re-importing without editing would rewrite "
        f"{len(changed)} file(s); a reviewer's diff must show only real edits:\n  "
        + "\n  ".join(sorted(changed)[:10])
    )
