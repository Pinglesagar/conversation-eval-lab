"""The advisory corpus's own vocabularies: KPI groups, regimes, disclosure registers.

WHY THIS IS A SEPARATE MODULE AND NOT MORE OF `roleplay/corpus.py`
-----------------------------------------------------------------
`roleplay/corpus.py` is a *loader*. It knows how to turn YAML into validated
scenarios and how to refuse the ones that assert nothing. What a scenario is
allowed to *say* is domain data, and the advisory corpus says three things the
roleplay pack cannot:

1.  which **KPI group** each row grades, out of a closed set of seven;
2.  which **regulatory regime** it is graded under, out of a closed set of four;
3.  for a divergence row, the **register entry** that makes the same transcript
    pass in one regime and fail in another.

All three are closed vocabularies, so all three are load-time errors when wrong,
for the reason the rest of this repo gives: a scenario that names a KPI nobody
defined is coverage that does not exist, and it goes green.

THE KPI LADDER LIVES IN ONE PLACE, AND IT IS NOT HERE
----------------------------------------------------
The seven groups and the twenty-eight KPIs under them are defined once, in
`roleplay/scorecard.py`, with each behaviour's citation and the business metric it
ladders to. This module imports that registry rather than restating it. Two
registries of the same seven ids is the defect the whole repo is written against:
a report that joins a corpus to a scorecard on a renamed column joins nothing, and
the second copy is always the one that goes stale.

What this module adds is the *corpus* side of the same idea — a row declares which
groups it grades, and `KPI_IDS` is what makes a typo in that declaration a load
error rather than a coverage claim about a group that does not exist.

`gate_groups()` is derived from the registry rather than restated: it reports the
groups whose KPIs are *all* gates, which is the property a reporting surface must
respect. Compliance is not a score to be averaged — a session that breached a
disclosure requirement and closed brilliantly is not a 4/5, it is a fail with a
good closing.

THE REGISTERS ARE DATA, NOT KEYWORD LISTS
-----------------------------------------
`scenarios/advisory/registers/*.yaml` holds one file per regime: the enumerated
point-of-sale requirements that regime imposes, each with **what kind of
requirement it is** — verbatim, prescribed-unit, substance, prohibition, gate, or
not-required. That field is the finding from `docs/_research/regulators.md` §8:
the four regimes split into two drafting traditions, so a substring register is
*correct* for the FCA's COBS 4.5A.10R sentence and *wrong* for the SFC's
Schedule 9 substance test. An instrument that cannot tell those apart cannot tell
you whether a miss is evidence about the adviser or evidence about itself.

`kind: not-required` entries are not padding. A register that can only say "this
is required here" makes a cross-market checker invent requirements in the markets
that do not have them — which is exactly the Reg BI suitability-report case
(§6 D2). The absence has to be recorded to be gradeable.

Every entry carries `source` (the paragraph-level citation as it appears in the
research file) and `research` (the section of the research file that establishes
it). No entry may carry neither: `evidence: assumption` is a legal value and an
unlabelled guess is not.

THE EIGHTEEN ROWS
-----------------
Eighteen, and the number is asserted by the tests. This is a probe set sized to be
read in full, not sampled from anything, so a rate computed over it is a statement
about these eighteen transcripts and nothing else.

    divergence  5   D1 commission (four verdicts on one sentence) · D8 the MAS
                    life-policy carve-out, inside one meeting · D2 a verbal close
                    with nothing in writing · D7 cooling-off duration *and*
                    trigger · D10 Hong Kong, where failing to advise is the breach
    nearmiss    3   the disclosure given after the point it was required · the one
                    given to the partner instead of the customer · the prescribed
                    term buried inside a hundred and forty words. All three fail,
                    and all three are passed by a keyword matcher — each row says
                    why in its own notes
    clause      3   surrender value explained / recited / understated, sharing
                    three trainee turns verbatim so the verdict difference is
                    attributable to the clause turn alone
    conflict    3   "what would you do", answered · the customer's own permission
                    to skip the fact-find · a vulnerable customer keen to sign.
                    Each row records what a conversion-only scorer says, what a
                    compliance-only scorer says, and the combined verdict
    survival    2   a hostile opening handled by shrinking the ask, and a graceful
                    exit that must score as a PASS — a suite that only rewards
                    persistence teaches persistence
    lang        2   an English risk warning inside a Cantonese sentence, and an
                    indirect refusal filed as still-in-play

    python -m roleplay.corpus --advisory --coverage --list
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from roleplay.scorecard import GROUPS, KPIS

__all__ = [
    "ADVISORY_ROOT",
    "ADVISORY_SUITES",
    "ADVISORY_SUITE_MINIMUMS",
    "KPI_IDS",
    "REGIMES",
    "REGISTER_KINDS",
    "RegisterEntry",
    "Register",
    "gate_groups",
    "load_registers",
    "register_entry",
]

#: Where the advisory corpus lives. A sibling root to `scenarios/roleplay`, loaded
#: by the same loader with a different suite set — not a second loader.
ADVISORY_ROOT: Path = Path(__file__).resolve().parent.parent / "scenarios" / "advisory"

#: Suite = subdirectory = id prefix, as everywhere else in this repo. Six families,
#: each named for the failure mode it isolates rather than for a product feature.
ADVISORY_SUITES: tuple[str, ...] = (
    "divergence",  # same transcript, opposite verdicts, because the registers differ
    "nearmiss",    # a keyword matcher passes these; every one of them is a fail
    "clause",      # one clause explained, recited, and understated
    "conflict",    # the commercially right move is the non-compliant one
    "survival",    # earning the right to continue, and ending the call well
    "lang",        # code-switching, and indirect refusal read as an outcome label
)

#: Smallest shippable count per suite. Asserted by the tests, not here, so a
#: half-written corpus still loads while it is being written.
ADVISORY_SUITE_MINIMUMS: dict[str, int] = {
    "divergence": 5,
    "nearmiss": 3,
    "clause": 3,
    "conflict": 3,
    "survival": 2,
    "lang": 2,
}


#: The ids of the seven groups, straight from the scorecard registry. A scenario's
#: `kpis` field is validated against this, so a row can only claim coverage of a
#: group that someone has defined, cited and laddered to a business metric.
KPI_IDS: frozenset[str] = frozenset(GROUPS)


def gate_groups() -> tuple[str, ...]:
    """Groups whose every KPI is a gate, derived from the scorecard registry.

    A function rather than a constant so a reporting surface has to ask, and so the
    grep for callers finds every place the distinction is honoured or ignored. Note
    what it is *not*: several score-bearing groups also contain a single gate KPI
    (a vulnerability signal in discovery, a pressure close in closing), and those
    groups still carry a score. Only a group that is gates all the way down has no
    number to average, which is the case a report can get silently wrong.
    """
    return tuple(
        sorted(
            group
            for group in GROUPS
            if all(k.is_gate for k in KPIS if k.group == group)
        )
    )


#: The four regimes, with the file that holds each one's register. Named for the
#: regulator rather than for a geography, because `apac-retail` is a fiction that
#: cannot express the difference between MAS and the SFC — and that difference is
#: five of this corpus's eighteen rows.
REGIMES: dict[str, str] = {
    "mas": "Monetary Authority of Singapore",
    "fca": "Financial Conduct Authority (COBS / PRIN), United Kingdom",
    "reg-bi": "SEC Regulation Best Interest, United States",
    "sfc-ia": "Securities and Futures Commission and Insurance Authority, Hong Kong",
}

#: What kind of requirement an entry is — the §8 finding, made into a field.
#: This decides whether a miss is evidence about the adviser or about the
#: instrument, so it is not optional and it has no default.
REGISTER_KINDS: frozenset[str] = frozenset(
    {
        "verbatim",         # only a specific form of words discharges it
        "prescribed-unit",  # cash terms, whole percentage points, "at least 14 days"
        "substance",        # the meaning discharges it; wording is free
        "prohibition",      # the behaviour is not disclosable, it is banned
        "gate",             # a procedural precondition; the sale may not proceed
        "not-required",     # recorded absence, so a cross-market checker cannot invent it
    }
)

_EVIDENCE = frozenset({"sourced", "secondary", "assumption"})


@dataclass(frozen=True)
class RegisterEntry:
    """One requirement, in one regime, with its kind and its citation."""

    id: str
    regime: str
    requirement: str
    kind: str
    evidence: str
    source: str
    research: str
    timing: str = ""

    def __post_init__(self) -> None:
        if self.kind not in REGISTER_KINDS:
            raise ValueError(
                f"{self.id}: kind {self.kind!r} unknown; legal: {sorted(REGISTER_KINDS)}"
            )
        if self.evidence not in _EVIDENCE:
            raise ValueError(
                f"{self.id}: evidence {self.evidence!r} unknown; legal: {sorted(_EVIDENCE)}"
            )
        if not self.source.strip() or not self.research.strip():
            raise ValueError(
                f"{self.id}: every entry needs both a source and the research section "
                "that establishes it; an unlabelled requirement is the one thing this "
                "corpus must not contain"
            )
        if not self.id.startswith(f"{self.regime}-"):
            raise ValueError(
                f"{self.id}: entry ids must start with their regime prefix "
                f"{self.regime!r}-, so a row citing one names its regime for free"
            )


@dataclass(frozen=True)
class Register:
    """One regime's register: its entries, keyed by id."""

    regime: str
    entries: dict[str, RegisterEntry]

    def __getitem__(self, entry_id: str) -> RegisterEntry:
        return self.entries[entry_id]

    def __contains__(self, entry_id: object) -> bool:
        return entry_id in self.entries

    def of_kind(self, kind: str) -> tuple[RegisterEntry, ...]:
        return tuple(e for e in self.entries.values() if e.kind == kind)


def _register_from_mapping(data: Any, *, source: str) -> Register:
    if not isinstance(data, dict):
        raise ValueError(f"{source}: expected a YAML mapping, got {type(data).__name__}")
    regime = str(data.get("regime", ""))
    if regime not in REGIMES:
        raise ValueError(f"{source}: regime {regime!r} unknown; legal: {sorted(REGIMES)}")
    rows = data.get("entries") or ()
    if not rows:
        raise ValueError(f"{source}: register declares no entries")
    entries: dict[str, RegisterEntry] = {}
    for row in rows:
        entry = RegisterEntry(
            id=str(row["id"]),
            regime=regime,
            requirement=str(row["requirement"]),
            kind=str(row["kind"]),
            evidence=str(row["evidence"]),
            source=str(row["source"]),
            research=str(row["research"]),
            timing=str(row.get("timing", "")),
        )
        if entry.id in entries:
            raise ValueError(f"{source}: duplicate entry id {entry.id!r}")
        entries[entry.id] = entry
    return Register(regime=regime, entries=entries)


@lru_cache(maxsize=8)
def load_registers(directory: str | Path | None = None) -> dict[str, Register]:
    """Load one register per regime, keyed by regime id.

    Cached because the scenario loader consults it once per row and a corpus
    validation re-reading four files eighteen times is a pointless cost. Cache
    key is the directory, so a test can point at a fixture root.
    """
    base = Path(directory) if directory is not None else ADVISORY_ROOT / "registers"
    registers: dict[str, Register] = {}
    for path in sorted(base.glob("*.yaml")):
        register = _register_from_mapping(
            yaml.safe_load(path.read_text(encoding="utf-8")), source=str(path)
        )
        if register.regime != path.stem:
            raise ValueError(
                f"{path}: regime {register.regime!r} does not match the filename; a row "
                "citing a regime must locate its register without a lookup"
            )
        registers[register.regime] = register
    missing = sorted(set(REGIMES) - set(registers))
    if registers and missing:
        raise ValueError(
            f"{base}: no register file for regime(s) {missing}; a divergence row can "
            "only be graded against a regime whose requirements are written down"
        )
    return registers


def register_entry(entry_id: str, *, regime: str, directory: str | Path | None = None) -> RegisterEntry:
    """Resolve one entry, raising with the legal ids if it does not exist."""
    registers = load_registers(directory)
    register = registers.get(regime)
    if register is None:
        raise KeyError(f"no register for regime {regime!r}; have {sorted(registers)}")
    if entry_id not in register:
        raise KeyError(
            f"{regime}: no register entry {entry_id!r}; have {sorted(register.entries)}"
        )
    return register[entry_id]
