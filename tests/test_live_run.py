"""The committed live run: it replays, and it replays to the same numbers.

WHAT THIS DEMONSTRATES
----------------------
That the live figures in the README are evidence rather than an anecdote. A number
produced while somebody held an API key is a story; a number that recomputes from
committed fixtures, offline, in CI, with every provider variable unset, is a
measurement. This file is the difference.

WHAT IS PINNED, AND WHAT IS DELIBERATELY NOT
--------------------------------------------
Pinned: the **findings** — the set of `(scenario, contract)` pairs the run
produced — and the stability verdicts behind them, recomputed by re-driving all
141 recorded conversations through the same contracts. That is the same thing the
regression gate compares, so a change to a check or to the corpus that would move
the committed report fails here first, with a diff a human can read.

Not pinned: latency figures (they come from a `FakeClock` and say nothing about any
model) and the exact prose of the report's notes.

WHY THIS TEST RE-DRIVES INSTEAD OF READING THE TRACES
-----------------------------------------------------
Reading `fixtures/live_full/traces/*.jsonl` and re-scoring them would test the
contracts against a frozen input, which `evallab replay` already does. Re-driving
exercises the whole rig — the agent cassette, the caller cassettes, the judge
recording — so a fixture that has gone stale or a code path that has quietly
stopped replaying is a failure here rather than a surprise the next time somebody
tries to reproduce the run. It is slower and it is the point.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab import cli

REPO = Path(__file__).resolve().parents[1]
LIVE_DIR = REPO / "fixtures/live_full"
REPORT = LIVE_DIR / "run_report.json"

pytestmark = pytest.mark.skipif(
    not REPORT.exists(), reason="no committed live run in this checkout"
)


@pytest.fixture(scope="module")
def committed() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_the_committed_live_report_is_internally_consistent(committed: dict) -> None:
    """The stored verdict must agree with the counts stored beside it.

    `load_run_report` recomputes every derived figure from the raw counts and
    raises if the file disagrees with itself, which is what makes a hand-edited
    report detectable.
    """
    report = cli.load_run_report(REPORT)
    assert report.verdict == committed["verdict"]
    assert len(report.stability) == len(committed["stability"])


def test_the_live_run_declares_a_live_agent_in_every_trace() -> None:
    """Provenance is in the traces, not only in the report's prose.

    `build_of` reads the adapter, and the expectations in the corpus are scored per
    build. If these traces claimed to be scripted, every seeded defect would be
    scored against the wrong set of expectations and the gate would be measuring
    the wrong thing.
    """
    from lab.trace.io import read_jsonl

    paths = sorted((LIVE_DIR / "traces").glob("*.jsonl"))
    assert paths, "the committed live run has to carry its conversations"
    for path in paths:
        trace = read_jsonl(path)
        assert cli.build_of(trace) == "live", path.name


def test_the_committed_live_run_replays_offline_and_reproduces_its_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, committed: dict
) -> None:
    """Re-drive all of it from the fixtures, with no key, and compare findings.

    Every provider variable is deleted first, so a machine that happens to have
    credentials in its environment cannot turn this into a live run and quietly
    pass by spending money.
    """
    for name in (
        "LAB_LIVE_AGENT",
        "LAB_LIVE_CALLER",
        "LAB_LIVE_JUDGE",
        "AZURE_OPENAI_API_KEY",
        "AZURE_API_KEY",
        "OPENAI_API_KEY",
        "LAB_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    code = cli.main(
        [
            "run",
            "-k",
            "3",
            "--live-agent",
            "--live-caller",
            "--live-judge",
            "--out",
            str(tmp_path),
            "--baseline",
            str(REPORT),
            "--no-traces",
        ]
    )
    assert code == 0, "the replayed run must not report a new or vanished finding"

    replayed = json.loads((tmp_path / "run_report.json").read_text(encoding="utf-8"))
    assert _findings(replayed) == _findings(committed)
    assert _verdicts(replayed) == _verdicts(committed)
    assert replayed["judges"][0]["flagged"] == committed["judges"][0]["flagged"]
    assert replayed["judges"][0]["judged"] == committed["judges"][0]["judged"]


def _findings(payload: dict) -> set[tuple[str, str]]:
    return {(f["scenario_id"], f["contract"]) for f in payload["failures"]}


def _verdicts(payload: dict) -> dict[str, str]:
    return {row["scenario_id"]: row["verdict"] for row in payload["stability"]}
