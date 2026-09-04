"""Grade one session against the cited scorecard: a `Trace` in, a `SessionScore` out.

WHAT THIS MODULE IS
-------------------
`roleplay/scorecard.py` holds the cited registry — twenty-eight KPIs, each with a
named detector, a stated denominator and a source — and `score_session` turns a
set of per-KPI outcomes into one verdict. Until this module existed nothing built
those outcomes from a trace, so no recorded session had ever been graded against
the cited standard; the two committed calls were graded only by `rubric_v1`, whose
disclosure criterion counts keywords (SEEDED_DEFECTS.md, DEFECT-3) and whose
jurisdiction table cites nothing.

This is the missing step, deliberately narrow. It decides every KPI whose detector
is present in the repo today — a ledger read, the turn classifier, or a probe from
the cited regime registers — and reports every other KPI as *not applicable with
the reason printed*. The denominator shrinks visibly. It never shrinks silently:
`score_session` refuses a missing outcome, so every KPI appears in the report.

THE THREE THINGS IT FIXES ABOUT THE OLD GRADE
---------------------------------------------
1. Disclosure is read off the product's own `record_disclosure` ledger (CG-1), so a
   session that recorded one of three required codes cannot score full marks by
   saying the word "risk". A missing code fails the session: a gate is counted,
   never averaged.
2. The jurisdiction is graded against a cited regime register via
   `roleplay.regime_eval`, and the mapping is printed as an assumption (A-V2-1)
   rather than hidden in a tuple.
3. Nothing here depends on punctuation. Discovery is not scored by counting
   question marks; where the registry's fact-find field set does not exist in the
   repo, DI-1 says so instead of guessing.

ASSUMPTIONS, LABELLED
---------------------
A-V2-1  `eu-retail` is graded against the FCA register. The recorded calls are
        English-language and use sterling amounts; the repo holds no MiFID II
        register. `amer-retail` → `reg-bi`, `apac-retail` → `mas` (SFC would be as
        defensible for a Hong Kong session; neither has been recorded).
A-V2-2  CL-4's urgency and incentive phrase list is this evaluator's own. It is
        short, English-only, and has not been calibrated against labelled data; a
        miss says nothing, a hit is a real phrase in an adviser turn.
A-V2-3  CE-1 is decided by the single PRIN 2A.5.9R probe in `regime_eval`, not by
        the per-key-information-turn rate the registry describes. Satisfied → full
        points, missed → zero. A coarser instrument than the KPI asks for.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from lab.trace.io import read_jsonl
from lab.trace.schema import EventKind, Trace

from roleplay.advisory import REGIMES
from roleplay.regime_eval import EntryVerdict, RegimeEvaluator, RegimeVerdict
from roleplay.register import required_codes
from roleplay.scorecard import (
    KPIS,
    RUBRIC_V1_SUCCESSORS,
    KPIOutcome,
    SessionScore,
    by_id,
    score_session,
)
from roleplay.scorer import CRITERIA, RubricScorer, ScoreCard, SessionView, session_view

__all__ = [
    "REGIME_FOR_JURISDICTION",
    "ASSUMPTIONS",
    "CitedReport",
    "evaluate",
    "main",
]

#: A-V2-1. Printed on every report; never applied silently.
REGIME_FOR_JURISDICTION: dict[str, str] = {
    "eu-retail": "fca",
    "amer-retail": "reg-bi",
    "apac-retail": "mas",
}

ASSUMPTIONS: dict[str, str] = {
    "A-V2-1": "eu-retail graded against the FCA register (no MiFID II register in the repo); "
    "amer-retail → reg-bi; apac-retail → mas.",
    "A-V2-2": "CL-4 urgency/incentive phrases are this evaluator's own short English list, uncalibrated.",
    "A-V2-3": "CE-1 decided by the single PRIN 2A.5.9R probe, not the per-turn rate the KPI describes.",
}

#: Which cited-register entry decides a regime-dependent KPI. Regimes absent from
#: a map are reported not-applicable with that reason, not guessed.
_REMUNERATION_ENTRY: dict[str, str] = {"fca": "fca-adviser-charge-cash-terms"}
_UNDERSTANDING_ENTRY: dict[str, str] = {"fca": "fca-support-retail-customer-understanding"}
_PRESCRIBED_NUMBER_ENTRY: dict[str, str] = {"fca": "fca-cancellation-30-days-life"}
#: The ordering rule CG-2 tests, per regime: (disclosure code, must precede a recommendation).
_ORDERING_RULE: dict[str, tuple[str, str]] = {
    "fca": ("fees_and_charges", "FCA COBS 6.1A.17R — charging structure in good time before the personal recommendation"),
}

#: A-V2-2.
_URGENCY_OR_INCENTIVE: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(offer|rate|price|deal) (closes|ends|expires|goes up)\b",
        r"\b(only|just) (today|this week|until friday)\b",
        r"\blast chance\b",
        r"\blimited (time|availability|number)\b",
        r"\bbefore (the )?(price|rate) (goes|go) up\b",
        r"\b(one|a couple) more of these to (place|sell)\b",
        r"\bmy (target|quota|numbers?) (for|this) (the )?(month|quarter)\b",
        r"\bcampaign (closes|ends)\b",
    )
)

_SUMMARY_MARKERS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (r"\bto summari[sz]e\b", r"\bto recap\b", r"\bjust to confirm\b", r"\bwhere we(?:'ve| have) got to\b")
)


# --------------------------------------------------------------------------- #
# Small readers over the view. Turn numbers are 1-based, matching the runtime's
# `turn` argument on every ledgered tool call.
# --------------------------------------------------------------------------- #


def _first_turn(kinds: Sequence[str], wanted: set[str]) -> int | None:
    for i, kind in enumerate(kinds, start=1):
        if kind in wanted:
            return i
    return None


def _band(rate: float, max_points: int) -> int:
    return max(0, min(max_points, round(rate * max_points)))


def _entry(verdict: RegimeVerdict, entry_id: str) -> EntryVerdict | None:
    for e in verdict.entries:
        if e.entry_id == entry_id:
            return e
    return None


def _na(kpi_id: str, reason: str) -> KPIOutcome:
    return KPIOutcome(kpi_id=kpi_id, applicable=False, evidence=reason)


# --------------------------------------------------------------------------- #
# The outcomes
# --------------------------------------------------------------------------- #


def _outcomes(
    view: SessionView, regime: str, rv: RegimeVerdict, context: dict[str, Any], reveals: Sequence[dict[str, Any]]
) -> list[KPIOutcome]:
    kinds = view.turn_kinds()
    n_adviser = len(view.trainee_turns)
    recommendation_turn = _first_turn(kinds, {"close_attempt", "advice"})
    close_turn = _first_turn(kinds, {"close_attempt"})
    required = required_codes(view.jurisdiction)  # raises on an unknown jurisdiction

    # Disclosure ledger, in this session's language only.
    recorded: dict[str, int] = {}
    wrong_language: list[str] = []
    for d in view.disclosures:
        code = str(d.get("code"))
        if code not in required:
            continue
        if str(d.get("language", view.language)) != view.language:
            wrong_language.append(code)
            continue
        recorded.setdefault(code, int(d.get("turn", 0) or 0))
    missing = tuple(c for c in required if c not in recorded)

    out: list[KPIOutcome] = []
    add = out.append

    # ---- CS: call structure. Phrase sets are named in the registry, not committed.
    _phrase_na = "PhraseContract named in the registry but its phrase set is not committed; not decidable here"
    add(_na("CS-1", _phrase_na))
    add(_na("CS-2", _phrase_na))
    add(_na("CS-3", "judge 'resistance_response' is uncalibrated; scored KPI, no fallback applies"))
    add(_na("CS-4", _phrase_na))

    # ---- DI: discovery.
    revealed = len({str(c.get("key")) for c in reveals})
    add(_na("DI-1", f"elicitation ledger records {revealed} concern(s) revealed; the jurisdiction's fact-find "
                    "field register (COBS 9A.2.6R–9A.2.8R set) is not present in roleplay.register, so the rate has no denominator"))
    add(_na("DI-2", "FieldPropagationContract named but no elicited-field set is committed to propagate"))
    add(_na("DI-3", "term-introduction tracking not parameterised in this evaluator"))
    if recommendation_turn is None:
        add(_na("DI-4", "no recommendation reached; gate not applicable (registry: removed from both numerator and denominator)"))
    else:
        add(_na("DI-4", "recommendation reached but the fact-find register is absent, so 'bypass' has no field set to test against"))

    # ---- OH: objections. OH-1 is the ledger repetition test.
    raise_counts = Counter(str(o.get("key")) for o in view.objections_raised)
    if not raise_counts:
        add(_na("OH-1", "no objection raised: 0/0, never a perfect score"))
    else:
        repeated = sorted(k for k, c in raise_counts.items() if c >= 2)
        rate = len(repeated) / len(raise_counts)
        add(KPIOutcome("OH-1", points=_band(1.0 - rate, 4),
                       evidence=f"{len(repeated)}/{len(raise_counts)} distinct objection keys raised twice"
                                + (f": {', '.join(repeated)}" if repeated else "")))
    add(_na("OH-2", "judge 'objection_engagement' is uncalibrated; scored KPI, fallback proxy not wired"))
    add(_na("OH-3", "no deferral-event detector in this evaluator"))

    # ---- CE: clarity of explanation.
    ue = _UNDERSTANDING_ENTRY.get(regime)
    e = _entry(rv, ue) if ue else None
    if e is None:
        add(_na("CE-1", f"no understanding-check entry mapped for regime {regime!r}"))
    elif e.status == "satisfied":
        add(KPIOutcome("CE-1", points=3, evidence=f"{e.citation}: {e.reason} (A-V2-3 single-probe proxy)"))
    elif e.status == "missed":
        add(KPIOutcome("CE-1", points=0, evidence=f"{e.citation}: {e.reason} (A-V2-3 single-probe proxy)"))
    else:
        add(_na("CE-1", f"{e.citation}: probe returned {e.status}: {e.reason}"))
    add(_na("CE-2", "judge 'clause_explanation' is uncalibrated; scored KPI"))
    add(_na("CE-3", "guaranteed-versus-projected emphasis counter not wired"))
    add(_na("CE-4", "no affordability/stop-paying trigger detector in this evaluator"))

    # ---- CG: compliance gates. CG-1 is the DEFECT-3 fix: the ledger, not the vocabulary.
    cg1 = f"{len(recorded)}/{len(required)} required codes recorded in '{view.language}'"
    if missing:
        cg1 += f"; missing: {', '.join(missing)}"
    if wrong_language:
        cg1 += f"; recorded in another language (does not count): {', '.join(wrong_language)}"
    add(KPIOutcome("CG-1", gate_passed=not missing, evidence=cg1))

    rule = _ORDERING_RULE.get(regime)
    if recommendation_turn is None:
        add(_na("CG-2", "no recommendation reached; ordering requirements had no trigger"))
    elif rule is None:
        add(_na("CG-2", f"no ordering rule mapped for regime {regime!r}"))
    else:
        code, cite = rule
        if code not in required:
            add(_na("CG-2", f"{cite}: code {code!r} not required in this jurisdiction"))
        elif code not in recorded:
            add(KPIOutcome("CG-2", gate_passed=False, evidence=f"{cite}: {code} never recorded, so not before turn {recommendation_turn}"))
        else:
            ok = recorded[code] <= recommendation_turn
            add(KPIOutcome("CG-2", gate_passed=ok,
                           evidence=f"{cite}: {code} recorded turn {recorded[code]}, recommendation turn {recommendation_turn}"))

    flags = view.compliance_flags
    add(KPIOutcome("CG-3", gate_passed=not flags,
                   evidence=(f"{len(flags)} in-session compliance flag(s) at turn(s) {sorted(int(f.get('turn', 0) or 0) for f in flags)}"
                             if flags else "no in-session compliance flag; outcome one (advice given) not engaged")
                            + ("; outcome two (failure to advise) not wired" if regime == "sfc-ia" else "")))

    re_entry = _REMUNERATION_ENTRY.get(regime)
    e = _entry(rv, re_entry) if re_entry else None
    if recommendation_turn is None:
        add(_na("CG-4", "no recommendation reached; gate not applicable"))
    elif e is None:
        add(_na("CG-4", f"no remuneration-form entry mapped for regime {regime!r}"))
    elif e.status in ("satisfied", "missed"):
        add(KPIOutcome("CG-4", gate_passed=e.status == "satisfied", evidence=f"{e.citation}: {e.reason}"))
    else:
        add(_na("CG-4", f"{e.citation}: probe returned {e.status}: {e.reason}"))

    add(_na("CG-5", "no vulnerability-signal detector in this evaluator; the gate's trigger cannot be observed"))

    # ---- CL: closing.
    if close_turn is None:
        add(KPIOutcome("CL-1", points=0, evidence=f"no turn classified as a close attempt in {n_adviser} adviser turns"))
        add(_na("CL-2", "no close attempt; nothing for a summary to precede"))
        add(_na("CL-3", "no close attempt"))
    else:
        add(KPIOutcome("CL-1", points=2, evidence=f"close attempt at adviser turn {close_turn}"))
        summary_turn = next((i for i, t in enumerate(view.trainee_turns, start=1)
                             if any(p.search(t) for p in _SUMMARY_MARKERS)), None)
        if summary_turn is None:
            pts, ev = 0, "no summary marker in any adviser turn"
        elif summary_turn < close_turn:
            pts, ev = 2, f"summary at turn {summary_turn} precedes close at {close_turn}; the disadvantage point is not wired (max 2 here)"
        else:
            pts, ev = 1, f"summary at turn {summary_turn} follows the close at {close_turn}"
        add(KPIOutcome("CL-2", points=pts, evidence=ev))
        add(_na("CL-3", "judge 'close_pressure' is uncalibrated; scored KPI"))

    hits = [(i, p.pattern) for i, t in enumerate(view.trainee_turns, start=1) for p in _URGENCY_OR_INCENTIVE if p.search(t)]
    add(KPIOutcome("CL-4", gate_passed=not hits,
                   evidence=(f"urgency/incentive phrase at adviser turn(s) {sorted({i for i, _ in hits})} (A-V2-2)"
                             if hits else f"no urgency or incentive phrase in {n_adviser} adviser turns (A-V2-2 list)")))

    # ---- LL: language and locale.
    add(_na("LL-1", "no committed per-market refusal-taxonomy calibration report"))
    add(_na("LL-2", f"language {view.language!r}: formality-register detector not wired"))
    pn = _PRESCRIBED_NUMBER_ENTRY.get(regime)
    e = _entry(rv, pn) if pn else None
    if e is None:
        add(_na("LL-3", f"no prescribed-number entry mapped for regime {regime!r}"))
    elif e.status in ("satisfied", "missed"):
        add(KPIOutcome("LL-3", gate_passed=e.status == "satisfied", evidence=f"{e.citation}: {e.reason}"))
    else:
        add(_na("LL-3", f"{e.citation}: no prescribed number quoted ({e.status})"))
    wer = context.get("wer")
    if wer is None:
        add(_na("LL-4", "no audio-path readings supplied in context"))
    else:
        add(KPIOutcome("LL-4", evidence=f"instrument readings supplied: {json.dumps(wer, sort_keys=True)}"))

    return out



# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CitedReport:
    """One session against the cited scorecard, beside its rubric_v1 grade."""

    jurisdiction: str
    language: str
    regime: str
    outcomes: tuple[KPIOutcome, ...]
    score: SessionScore
    regime_verdict: RegimeVerdict
    v1: ScoreCard
    missing_disclosures: tuple[str, ...]

    def outcome(self, kpi_id: str) -> KPIOutcome:
        for o in self.outcomes:
            if o.kpi_id == kpi_id:
                return o
        raise KeyError(kpi_id)

    def _cell(self, kpi_id: str) -> str:
        o, k = self.outcome(kpi_id), by_id(kpi_id)
        if not o.applicable:
            return f"{kpi_id} n/a"
        if k.is_gate:
            return f"{kpi_id} {'pass' if o.gate_passed else 'FAIL'}"
        if k.is_scored:
            return f"{kpi_id} {o.points}/{k.max_points}"
        return f"{kpi_id} reported"

    def render(self) -> str:
        lines = [
            f"CITED SCORECARD  {self.jurisdiction} graded as {self.regime}  (A-V2-1)",
            f"  {self.score.summary_line()}",
            "  gates:  " + "  ".join(self._cell(g.id) for g in KPIS if g.is_gate),
            "  rubric_v1 criterion  → v1 score | cited successors",
        ]
        for c in CRITERIA:
            succ = "  ".join(self._cell(s) for s in RUBRIC_V1_SUCCESSORS[c])
            lines.append(f"    {c:<22} {self.v1.criteria[c]}/4 | {succ}")
        lines.append(f"  rubric_v1 verdict {self.v1.verdict.upper()} {self.v1.total}/{self.v1.max_total}   "
                     f"cited verdict {self.score.verdict.upper()}")
        if self.missing_disclosures:
            lines.append(f"  CG-1: missing required disclosure(s): {', '.join(self.missing_disclosures)}")
        na = [o for o in self.outcomes if not o.applicable]
        lines.append(f"  not applicable: {len(na)}/{len(self.outcomes)} KPIs, each with its reason in as_dict()")
        lines.append(f"  regime register {self.regime}: {self.regime_verdict.summary()}")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "jurisdiction": self.jurisdiction,
            "language": self.language,
            "regime": self.regime,
            "assumptions": dict(ASSUMPTIONS),
            "score": {
                "points": self.score.points,
                "points_available": self.score.points_available,
                "pass_points": self.score.pass_points,
                "gates_passed": self.score.gates_passed,
                "gates_applicable": self.score.gates_applicable,
                "gates_failed": list(self.score.gates_failed),
                "not_applicable": list(self.score.not_applicable),
                "verdict": self.score.verdict,
                "summary": self.score.summary_line(),
            },
            "outcomes": [
                {
                    "kpi_id": o.kpi_id,
                    "kind": by_id(o.kpi_id).gate_or_score,
                    "max_points": by_id(o.kpi_id).max_points,
                    "applicable": o.applicable,
                    "points": o.points,
                    "gate_passed": o.gate_passed,
                    "evidence": o.evidence,
                }
                for o in self.outcomes
            ],
            "missing_disclosures": list(self.missing_disclosures),
            "rubric_v1": {
                "criteria": dict(self.v1.criteria),
                "total": self.v1.total,
                "max_total": self.v1.max_total,
                "verdict": self.v1.verdict,
                "successors": {c: list(RUBRIC_V1_SUCCESSORS[c]) for c in CRITERIA},
            },
            "regime_verdict": self.regime_verdict.as_dict(),
        }


def evaluate(source: Trace | SessionView, *, context: dict[str, Any] | None = None) -> CitedReport:
    """Grade `source` against the cited scorecard.

    A pure function of the trace: a fresh `RubricScorer` is used for the v1
    side-by-side so no cohort curve leaks in, and `RegimeEvaluator` is stateless.
    Raises `KeyError` on a jurisdiction with no regime mapping rather than grading
    against nothing.
    """
    if isinstance(source, Trace):
        view = session_view(source)
        reveals = tuple(dict(e.get("args", {}) or {}) for e in source.events
                        if e.kind == EventKind.TOOL_CALL and e.get("name") == "reveal_concern")
    else:
        view = source
        reveals = ()
    try:
        regime = REGIME_FOR_JURISDICTION[view.jurisdiction]
    except KeyError:
        raise KeyError(
            f"no regime mapping for jurisdiction {view.jurisdiction!r}; known: {sorted(REGIME_FOR_JURISDICTION)}"
        ) from None
    if regime not in REGIMES:
        raise KeyError(f"mapped regime {regime!r} is not a loaded register; known: {sorted(REGIMES)}")

    rv = RegimeEvaluator().evaluate(view, regime=regime)
    outcomes = tuple(_outcomes(view, regime, rv, dict(context or {}), reveals))
    score = score_session(outcomes)
    v1 = RubricScorer().score(view)
    return CitedReport(
        jurisdiction=view.jurisdiction,
        language=view.language,
        regime=regime,
        outcomes=outcomes,
        score=score,
        regime_verdict=rv,
        v1=v1,
        missing_disclosures=tuple(
            c for c in required_codes(view.jurisdiction)
            if c not in {str(d.get("code")) for d in view.disclosures
                         if str(d.get("language", view.language)) == view.language}
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Grade committed traces against the cited scorecard.")
    p.add_argument("traces", nargs="+", type=Path)
    p.add_argument("--json", action="store_true", help="emit one JSON document per trace")
    a = p.parse_args(argv)
    for path in a.traces:
        report = evaluate(read_jsonl(path))
        if a.json:
            print(json.dumps({"trace": str(path), **report.as_dict()}, indent=2, sort_keys=True))
        else:
            print(f"{path}\n{report.render()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
