"""Drive the live LLM backend over selected scenarios, and record the fixture.

WHY THIS IS NOT A FLAG ON `evallab run`
---------------------------------------
`lab.cli` is the harness's entry point, and it is deliberately ignorant of what
it is measuring: it takes a corpus module and an agent factory by dotted path and
knows nothing else about TableMate. Teaching it about model-driven backends would
put knowledge of one system under test into the instrument, which is the boundary
this repository spends most of its argument on.

So this runner lives with the system under test, and it does the one thing
`evallab run` cannot: it puts a model in the decision seat, records every
exchange to a cassette so the run is reproducible with no key afterwards, and
reports the two questions a live run raises that a replay run does not.

    1. **Did the seeded defects still fire?** Under `ScriptedBackend` all three
       fire on every run by construction. Under a model they are probabilistic,
       so a rate — with its denominator — is the only honest way to state it.
       Both signals are printed: the corpus's own contract verdicts, and a
       hand-written signature per defect. They can disagree, and when they do
       that is the interesting row: a contract can fail for a reason that is not
       the seeded defect at all, and counting it as the defect would inflate the
       number this whole exercise exists to report.

    2. **Did the live trace come out the same shape as the scripted one?** Every
       divergence the engine knows how to notice is counted and printed rather
       than being left for a reader to trust.

USAGE
-----
    # replay the committed cassette, no key needed
    python -m tablemate --suite edge

    # record a live run (needs LAB_LIVE_AGENT=1 and a provider key)
    LAB_LIVE_AGENT=1 LAB_AGENT_MODEL=azure/<deployment> \
        python -m tablemate --record --scenario edge-large-party-of-six

Environment, by name only — no value read here is ever printed, logged or
written to the cassette:
`LAB_LIVE_AGENT`, `LAB_AGENT_MODEL`, and one of `AZURE_OPENAI_API_KEY` /
`AZURE_API_KEY` / `OPENAI_API_KEY` / `LAB_KEY`, plus `AZURE_OPENAI_ENDPOINT` and
`AZURE_OPENAI_API_VERSION` for an Azure deployment.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from lab.clock import FakeClock
from lab.simulator import ScriptedCaller, run_scenario
from lab.trace.io import write_jsonl
from lab.trace.schema import EventKind, Trace

from tablemate.agents import LARGE_PARTY_THRESHOLD
from tablemate.understanding import extract_slots
from tablemate.runtime import (
    LIVE_AGENT_ENV_VAR,
    LLMBackend,
    MissingExchangeError,
    NotLiveError,
    build_agent,
)

#: The committed cassette. Recording appends to it; replay reads it.
DEFAULT_CASSETTE = "fixtures/live_sessions.json"

#: Which scenarios the three defects are reachable in, and the control rows that
#: must stay clean. Copied from the table at the foot of
#: `tablemate/SEEDED_BUGS.md` — the answer key, and the only place this mapping
#: is allowed to live twice.
BUG_ROWS: dict[str, tuple[str, ...]] = {
    "BUG-1": ("edge-large-party-of-six", "edge-large-party-eight-with-note"),
    "BUG-2": ("edge-modification-after-booking", "edge-modify-party-size-upward"),
    "BUG-3": ("edge-dietary-then-policy-detour", "edge-coeliac-then-menu-policy"),
}
CONTROL_ROWS: dict[str, tuple[str, ...]] = {
    "BUG-1": ("happy-party-of-five-boundary",),
    "BUG-2": ("happy-cancel-then-rebook", "happy-move-booking-later"),
    "BUG-3": (
        "happy-dietary-note-single-agent",
        "happy-parking-question-midbooking",
    ),
}

#: A spoken claim that a table exists. Deliberately generous: the question this
#: answers is "was the caller led to believe they had a booking", and a narrow
#: pattern would answer "did the agent use the word I expected" instead.
_CLAIMED_RE = re.compile(
    r"(?i)\b(all booked|is booked|booked in|booked you|confirmed|reserved|"
    r"all set|you'?re in|room is yours|table is yours|secured|locked in|"
    r"got you (down|in)|penciled in|pencilled in)\b"
)

#: An interrogative about the head count. Asks, not confirmations: see the
#: discussion under BUG-2 in SEEDED_BUGS.md for why the distinction is the whole
#: detector. A sentence that states the number is checking, not asking.
_ASKS_PARTY_SIZE_RE = re.compile(
    r"(?i)(how many (people|guests|of you|will)|number of (people|guests)|"
    r"what(?:'s| is) the party size|how large is)"
)

#: The guard the first version of this detector did not have, added after reading
#: live output rather than before writing it.
#:
#: A real model offers to change things: *"anything else you'd like to change —
#: the date, or the number of people in your party?"* mentions the head count and
#: requests nothing, and the first draft of `_ASKS_PARTY_SIZE_RE` fired on it
#: twice in three repeats of a control row. That is the precise failure
#: SEEDED_BUGS.md predicts under BUG-2: a detector that flags any interrogative
#: mentioning the party size gets called noisy and gets switched off. So a
#: sentence that frames the head count as one option among several things the
#: caller *could* change is an offer, not an ask, and is excluded.
_OFFERS_A_CHANGE_RE = re.compile(
    r"(?i)\b(anything else|would you like to (change|update|amend)|like to change|"
    r"want to change|such as|perhaps the)\b"
)

#: Sentence-ish split. Crude on purpose: the guard above has to be evaluated over
#: the clause the match sits in, not the whole turn, or a courteous closing line
#: elsewhere in the same paragraph would suppress a real ask.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _states_a_head_count(text: str) -> bool:
    """Did the caller actually say how many people are coming?

    The second guard this detector did not start with. The first version asked
    "does this utterance contain a number", which matched the *time* in "move my
    booking TM-2098 to 7:30pm" and so decided the caller had already given a head
    count they had not given — turning a perfectly proper question into a
    re-ask on a control row.

    So the question is put to the same slot extractor the system's own record
    uses (`tablemate.understanding.extract_slots`). One definition of "the caller
    stated the party size", shared between the agent's memory and the detector
    that judges it, which is the only way the two can be talking about the same
    thing.
    """
    return bool(extract_slots(text or "").get("party_size"))
_DIETARY_RE = re.compile(r"(?i)\b(peanut|allerg|coeliac|celiac|gluten|nut)\b")


# --------------------------------------------------------------------------- #
# Reading a trace
# --------------------------------------------------------------------------- #


def _events(trace: Trace, kind: str) -> list[Any]:
    return [e for e in trace.events if e.kind == kind]


def _agent_lines(trace: Trace) -> list[tuple[str | None, str]]:
    return [
        (e.payload.get("agent"), str(e.payload.get("text") or ""))
        for e in _events(trace, EventKind.AGENT_UTTERANCE)
    ]


def _caller_lines(trace: Trace) -> list[str]:
    return [
        str(e.payload.get("text") or "")
        for e in _events(trace, EventKind.CALLER_UTTERANCE)
    ]


def _tool_calls(trace: Trace, name: str) -> list[dict[str, Any]]:
    return [
        dict(e.payload.get("args") or {})
        for e in _events(trace, EventKind.TOOL_CALL)
        if e.payload.get("name") == name
    ]


@dataclass(frozen=True)
class Signal:
    """One defect detector's verdict on one conversation, in three values.

    `applicable` is the field that makes the rate honest. A detector for "the
    allergy did not reach the booking" has nothing to say about a call that never
    reached a booking at all, and counting that call in the denominator reports a
    defect as rarer than it is. Under `ScriptedBackend` every row reaches its
    precondition by construction, so the distinction never arises; under a model
    the agent chooses its own route and it arises constantly. This is the same
    VACUOUS-versus-PASS distinction `lab.checks` draws, applied to the per-defect
    signatures.
    """

    applicable: bool
    fired: bool = False
    evidence: str | None = None


#: Tools that make a claim about the diary true. A spoken commitment with none of
#: these behind it is unbacked, whichever desk said it.
COMMITTING_TOOLS: tuple[str, ...] = (
    "create_booking",
    "modify_booking",
    "cancel_booking",
)


def unbacked_promise(trace: Trace) -> tuple[str, str] | None:
    """The first spoken commitment with no committing tool call before it.

    Decided on event-stream **position**, not timestamps, for the reason
    `lab.checks` decides ordering that way: tool events inside a turn are
    interpolated across the measured window and their timestamps are marked
    estimated, so a comparison of two of them is a comparison of two guesses.
    """
    committed = False
    for event in trace.events:
        if event.kind == EventKind.TOOL_CALL and event.payload.get("name") in COMMITTING_TOOLS:
            committed = True
        if event.kind != EventKind.AGENT_UTTERANCE or committed:
            continue
        text = str(event.payload.get("text") or "")
        if _CLAIMED_RE.search(text):
            return str(event.payload.get("agent") or "?"), text.strip()
    return None


def bug_1_signal(trace: Trace, scenario: Any) -> Signal:
    """BUG-1: the booking desk says a large party has a table it never booked.

    Applicable only on a row that asks for a party of six or more, because that
    is the only branch of the prompt that hands the paperwork to the events team.
    An unbacked promise from anywhere else is a real finding and is reported as an
    emergent one — not folded into this rate, which would make BUG-1's number a
    tally of two different defects.
    """
    wanted = _party_size(scenario)
    if wanted is None or wanted < LARGE_PARTY_THRESHOLD:
        return Signal(applicable=False)
    promise = unbacked_promise(trace)
    if promise is None or promise[0] != "BookingAgent":
        return Signal(applicable=True)
    return Signal(
        applicable=True,
        fired=True,
        evidence=f"BookingAgent said {promise[1][:110]!r} with no create_booking call",
    )


def _asks_for_head_count(text: str) -> str | None:
    """The clause that requests a head count, or None if none does."""
    for clause in _SENTENCE_SPLIT_RE.split(text or ""):
        if _ASKS_PARTY_SIZE_RE.search(clause) and not _OFFERS_A_CHANGE_RE.search(clause):
            return clause.strip()
    return None


def bug_2_signal(trace: Trace, scenario: Any) -> Signal:
    """BUG-2: the amendment desk asks for a head count the caller already gave.

    Applicable only once the amendment desk has actually held the turn *and* the
    caller has already stated a number. On a call the model never routed to that
    desk there is no re-ask to find, and scoring it as a clean pass would credit
    the agent for a flow it skipped.
    """
    stated_at: int | None = None
    asked: str | None = None
    for index, event in enumerate(trace.events):
        text = str(event.payload.get("text") or "")
        if event.kind == EventKind.CALLER_UTTERANCE and _states_a_head_count(text):
            if stated_at is None:
                stated_at = index
            continue
        if event.kind != EventKind.AGENT_UTTERANCE:
            continue
        if event.payload.get("agent") != "ModificationAgent":
            continue
        if stated_at is not None and asked is None:
            asked = _asks_for_head_count(text)
    held_the_turn = any(
        e.kind == EventKind.AGENT_UTTERANCE and e.payload.get("agent") == "ModificationAgent"
        for e in trace.events
    )
    if not held_the_turn or stated_at is None:
        return Signal(applicable=False)
    if asked is None:
        return Signal(applicable=True)
    return Signal(
        applicable=True,
        fired=True,
        evidence=f"ModificationAgent asked {asked[:110]!r} after the caller stated it",
    )


def bug_3_signal(trace: Trace, scenario: Any) -> Signal:
    """BUG-3: a dietary requirement lost across a handoff, then a booking without it.

    Applicable only when all three preconditions really occurred in order: the
    caller stated the requirement, control crossed a boundary afterwards, and a
    booking was committed after that. On this corpus a live model frequently
    answers the policy question and never gets to the booking, and those calls
    have to be excluded rather than counted clean — otherwise the rate measures
    how often the agent finished the call, not how often the note survived.
    """
    stated_at: int | None = None
    handoff_at: int | None = None
    booking: tuple[int, dict[str, Any]] | None = None
    for index, event in enumerate(trace.events):
        if event.kind == EventKind.CALLER_UTTERANCE and _DIETARY_RE.search(
            str(event.payload.get("text") or "")
        ):
            stated_at = index if stated_at is None else stated_at
        elif event.kind == EventKind.AGENT_HANDOFF and stated_at is not None:
            handoff_at = index if handoff_at is None else handoff_at
        elif (
            event.kind == EventKind.TOOL_CALL
            and event.payload.get("name") == "create_booking"
            and handoff_at is not None
            and booking is None
        ):
            booking = (index, dict(event.payload.get("args") or {}))
    if stated_at is None or handoff_at is None or booking is None:
        return Signal(applicable=False)
    notes = str(booking[1].get("notes") or "")
    if _DIETARY_RE.search(notes):
        return Signal(applicable=True)
    return Signal(
        applicable=True,
        fired=True,
        evidence=f"create_booking(notes={notes!r}) after the caller stated a requirement",
    )


SIGNALS = {
    "BUG-1": bug_1_signal,
    "BUG-2": bug_2_signal,
    "BUG-3": bug_3_signal,
}


def emergent_promise(trace: Trace, scenario: Any) -> str | None:
    """An unbacked spoken commitment that BUG-1 does not account for.

    Reported separately and loudly. `SEEDED_BUGS.md` says a fourth finding is
    either a real emergent defect worth writing up on its merits or a false
    positive worth fixing in the check, and that both are more interesting than
    the three planted ones. Folding it into BUG-1's rate would lose it.
    """
    promise = unbacked_promise(trace)
    if promise is None:
        return None
    agent, text = promise
    wanted = _party_size(scenario)
    if agent == "BookingAgent" and wanted is not None and wanted >= LARGE_PARTY_THRESHOLD:
        return None  # that is BUG-1, and it is counted there
    return f"{agent} said {text[:110]!r} with no committing tool call before it"


def _party_size(scenario: Any) -> int | None:
    facts = {**dict(getattr(scenario.goal, "facts", {}) or {}), **dict(scenario.context or {})}
    try:
        return int(str(facts.get("party_size")).strip())
    except (TypeError, ValueError):
        return None


def bugs_for(scenario_id: str) -> list[str]:
    """Which defects this row is meant to reach, and which it must not."""
    return [bug for bug, rows in BUG_ROWS.items() if scenario_id in rows]


def controls_for(scenario_id: str) -> list[str]:
    return [bug for bug, rows in CONTROL_ROWS.items() if scenario_id in rows]


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


def _select(corpus: Any, scripts: dict[str, Any], args: argparse.Namespace) -> list[Any]:
    wanted = set(args.scenario or [])
    suites = set(args.suite or [])
    chosen = []
    for scenario in corpus.scenarios:
        if scenario.id not in scripts:
            continue  # no caller lines: a voice row, not drivable as text
        if wanted and scenario.id not in wanted:
            continue
        if suites and str(scenario.suite) not in suites:
            continue
        chosen.append(scenario)
    return chosen


def _drive(scenario: Any, script: Any, backend: LLMBackend, *, personas: Any, index: int, max_turns: int) -> Trace:
    """One conversation. The clock is shared with the driver, as in `lab.cli`.

    Note what a `FakeClock` means for a live run: the latency in the trace is the
    latency `LatencyModel` simulates, *not* the seconds the provider took. That
    is deliberate — a fixture whose timings depend on how busy Azure was is not a
    fixture — and it is why no latency figure in this repository may be read as a
    measurement of the model.
    """
    clock = FakeClock()
    agent = build_agent(clock=clock, backend=backend, seed=script.seed_fn())
    caller = ScriptedCaller(
        script.script,
        profile=scenario.caller_profile(personas),
        closing=script.closing,
    )
    return run_scenario(
        scenario_id=scenario.id,
        agent=agent,
        caller=caller,
        adapter="text:live-llm",
        clock=clock,
        session_id=f"{scenario.id}#live{index}",
        max_turns=max_turns,
    )


def score(scenario: Any, trace: Trace, *, repeat: int, evaluate: Any) -> dict[str, Any]:
    """One conversation, as the row the rate table is built from.

    Both verdicts, side by side and never merged. The corpus's own contracts say
    whether anything moved against a declared expectation; the per-defect signals
    say whether the *seeded defect* is what moved. A live model fails a contract
    for its own reasons often enough that treating the two as one number would
    report a defect rate that is really a failure rate.
    """
    evaluation = evaluate(scenario, trace)
    signals = {bug: signal(trace, scenario) for bug, signal in SIGNALS.items()}
    return {
        "scenario": scenario.id,
        "repeat": repeat,
        "turns": len(_events(trace, EventKind.AGENT_UTTERANCE)),
        "tools": [str(e.payload.get("name")) for e in _events(trace, EventKind.TOOL_CALL)],
        "handoffs": [
            f"{e.payload.get('from')}->{e.payload.get('to')}"
            for e in _events(trace, EventKind.AGENT_HANDOFF)
        ],
        "known_gaps": [r.name for r in evaluation.known_gaps],
        "unexpected": [r.name for r in evaluation.unexpected],
        "stale": [r.name for r in evaluation.stale],
        "signals": {
            bug: {
                "applicable": signal.applicable,
                "fired": signal.fired,
                "evidence": signal.evidence,
            }
            for bug, signal in signals.items()
        },
        "emergent": emergent_promise(trace, scenario),
    }


def render_row(row: dict[str, Any]) -> str:
    fired = [b for b, s in row["signals"].items() if s["fired"]]
    skipped = [b for b, s in row["signals"].items() if not s["applicable"]]
    return (
        f"  {row['scenario']}#{row['repeat']}: turns={row['turns']} "
        f"tools={','.join(row['tools']) or '-'} "
        f"fired={','.join(fired) or 'none'} "
        f"n/a={','.join(skipped) or '-'} "
        f"known={','.join(row['known_gaps']) or '-'} "
        f"unexpected={','.join(row['unexpected']) or '-'} "
        f"stale={','.join(row['stale']) or '-'}"
        + (f"\n      EMERGENT: {row['emergent']}" if row["emergent"] else "")
    )


def replay(
    scenario_id: str,
    *,
    cassette: str = DEFAULT_CASSETTE,
    index: int = 0,
    max_turns: int = 14,
    scripts_path: str = "fixtures/caller_scripts.yaml",
) -> Trace:
    """Re-drive one recorded live conversation from the committed cassette.

    Offline, deterministic and key-free: the point of recording the cassette in
    the first place. A conversation that only existed while somebody held an API
    key is an anecdote; one that replays in CI is evidence, and this is the
    function that makes the difference checkable rather than claimed.

    Raises `MissingExchangeError` if the cassette does not hold this scenario —
    which is the right failure, because the alternative is a green test over a
    conversation that never happened.
    """
    from lab.cli import load_caller_scripts
    from scenarios.loader import load_corpus

    corpus = load_corpus()
    scripts = load_caller_scripts(scripts_path)
    scenario = next(s for s in corpus.scenarios if s.id == scenario_id)
    backend = LLMBackend(cassette=cassette)
    return _drive(
        scenario,
        scripts[scenario_id],
        backend,
        personas=corpus.personas,
        index=index,
        max_turns=max_turns,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tablemate",
        description="Run TableMate with a model in the decision seat.",
    )
    parser.add_argument("--scenario", action="append", help="scenario id; repeatable")
    parser.add_argument("--suite", action="append", help="suite name; repeatable")
    parser.add_argument("--cassette", default=DEFAULT_CASSETTE)
    parser.add_argument(
        "--record",
        action="store_true",
        help=(
            "call a live provider and write every exchange to the cassette. "
            f"Needs {LIVE_AGENT_ENV_VAR}=1 and a provider key."
        ),
    )
    parser.add_argument("--model", default=None, help="overrides LAB_AGENT_MODEL")
    parser.add_argument("-k", "--repeats", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=14)
    parser.add_argument("--max-tool-steps", type=int, default=4)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help=(
            "call the provider even for a request already in the cassette. "
            "Required with -k>1 to measure variance rather than replay repeat 0."
        ),
    )
    parser.add_argument("--out", default=None, help="write traces and a JSON summary here")
    parser.add_argument("--scripts", default="fixtures/caller_scripts.yaml")
    parser.add_argument("--transcript", action="store_true")
    parser.add_argument(
        "--score",
        default=None,
        metavar="DIR",
        help=(
            "score the traces already written under DIR/traces and print the "
            "rate table. No model, no cassette, no key: this is how the reported "
            "rate stays auditable after the run that produced it is history."
        ),
    )
    args = parser.parse_args(argv)

    from lab.cli import evaluate_trace, load_caller_scripts
    from scenarios.loader import load_corpus

    corpus = load_corpus()
    if args.score:
        return _score_only(Path(args.score), corpus, evaluate_trace, args.transcript)
    scripts = load_caller_scripts(args.scripts)
    selection = _select(corpus, scripts, args)
    if not selection:
        print("no scenarios selected — nothing to run", file=sys.stderr)
        return 2

    backend = LLMBackend(
        cassette=args.cassette,
        model=args.model,
        temperature=args.temperature,
        max_tool_steps=args.max_tool_steps,
        replay=not args.fresh,
    )
    if args.repeats > 1 and not args.fresh and args.record:
        print(
            f"refusing to record k={args.repeats} with replay on: repeats 2..k "
            "would read repeat 1's answers back out of the cassette and the "
            "variance you measured would be zero by construction. Pass --fresh.",
            file=sys.stderr,
        )
        return 2
    if args.record:
        # Refuse before a single scenario is driven. A run that starts, spends,
        # and then discovers it cannot spend is worse than one that never began.
        try:
            backend.require_live()
        except NotLiveError as exc:
            print(f"refusing to record: {exc}", file=sys.stderr)
            return 2
        print(
            f"recording live: model={backend.model}, "
            f"{len(selection)} scenario(s) x k={args.repeats}",
            file=sys.stderr,
        )
    elif backend.live_enabled:
        print(
            "note: a live call is permitted but --record was not passed, so this "
            "is a replay and anything not in the cassette will raise.",
            file=sys.stderr,
        )

    out_dir = Path(args.out) if args.out else None
    if out_dir is not None:
        (out_dir / "traces").mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for scenario in selection:
        script = scripts[scenario.id]
        for index in range(args.repeats):
            row: dict[str, Any] = {
                "scenario": scenario.id,
                "repeat": index,
                "reaches": bugs_for(scenario.id),
                "controls": controls_for(scenario.id),
            }
            try:
                trace = _drive(
                    scenario,
                    script,
                    backend,
                    personas=corpus.personas,
                    index=index,
                    max_turns=args.max_turns,
                )
            except MissingExchangeError as exc:
                row["error"] = f"cassette miss: {exc}"
                rows.append(row)
                print(f"  {scenario.id}#{index}: MISS (not in cassette)")
                continue
            except NotLiveError as exc:
                row["error"] = str(exc)
                rows.append(row)
                print(f"  {scenario.id}#{index}: REFUSED ({exc})")
                continue

            row.update(score(scenario, trace, repeat=index, evaluate=evaluate_trace))
            rows.append(row)
            print(render_row(row))
            if args.transcript:
                for agent, text in _agent_lines(trace):
                    print(f"      {agent}: {text}")
            if out_dir is not None:
                write_jsonl(trace, out_dir / "traces" / f"{scenario.id}-{index}.jsonl")

    if args.record:
        written = backend.save()
        if written is not None:
            print(f"cassette written: {written}", file=sys.stderr)

    summary = {
        "model": backend.model,
        "diagnostics": backend.diagnostics(),
        "rows": rows,
        "rates": _rates(rows),
    }
    print()
    _print_rates(summary)
    if out_dir is not None:
        (out_dir / "live_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0


def _score_only(
    root: Path, corpus: Any, evaluate: Any, transcript: bool
) -> int:
    """Re-score committed traces. The audit path: no model is involved at all."""
    from lab.trace.io import read_jsonl

    by_id = {s.id: s for s in corpus.scenarios}
    paths = sorted((root / "traces").glob("*.jsonl"))
    if not paths:
        print(f"no traces under {root / 'traces'}", file=sys.stderr)
        return 2
    rows: list[dict[str, Any]] = []
    for path in paths:
        trace = read_jsonl(path)
        scenario = by_id.get(trace.scenario_id)
        if scenario is None:
            print(f"  {path.name}: unknown scenario {trace.scenario_id!r}", file=sys.stderr)
            continue
        repeat = int(path.stem.rsplit("-", 1)[-1]) if path.stem[-1].isdigit() else 0
        row = score(scenario, trace, repeat=repeat, evaluate=evaluate)
        row["reaches"] = bugs_for(scenario.id)
        row["controls"] = controls_for(scenario.id)
        rows.append(row)
        print(render_row(row))
        if transcript:
            for agent, text in _agent_lines(trace):
                print(f"      {agent}: {text}")
    print()
    _print_rates(
        {
            "model": f"scored from {len(rows)} committed trace(s) — no model called",
            "diagnostics": {
                "blocked_calls": [],
                "truncated_turns": 0,
                "silent_turns": 0,
                "rate_limit_retries": 0,
                "model_calls": 0,
                "recorded": 0,
                "replayed": 0,
            },
            "rows": rows,
            "rates": _rates(rows),
        }
    )
    return 0


def _rates(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Per defect: how often it fired, over the conversations that could show it.

    Three denominators, and they are not interchangeable:

    *   `selected` — rows the corpus says reach this defect. What was aimed at.
    *   `applicable` — of those, the ones where the detector's preconditions
        actually occurred in the conversation the model chose to have. What could
        be measured.
    *   `fired` — of those, the ones where the defect appeared.

    The rate is `fired/applicable`, and `selected - applicable` is printed beside
    it, because a defect that was unreachable in eleven of twelve conversations is
    a fact about the agent that a percentage would bury.
    """
    out: dict[str, dict[str, int]] = {}
    for bug in BUG_ROWS:
        selected = [r for r in rows if bug in r.get("reaches", []) and "error" not in r]
        controls = [r for r in rows if bug in r.get("controls", []) and "error" not in r]

        applicable = [r for r in selected if r["signals"][bug]["applicable"]]
        out[bug] = {
            "selected": len(selected),
            "applicable": len(applicable),
            "fired": sum(1 for r in applicable if r["signals"][bug]["fired"]),
            # The control column is the *harness's* verdict on the control rows,
            # not this detector's. A control row's job is to stay clean overall —
            # "five books" is `create_booking` present and every contract green —
            # and asking a defect detector about a row where the defect is out of
            # scope by construction yields 0/0, which reads as a passing control
            # and is really no control at all.
            "controls": len(controls),
            "controls_clean": sum(1 for r in controls if not r["unexpected"]),
        }
    return out


def _print_rates(summary: dict[str, Any]) -> None:
    print(f"model: {summary['model']}")
    diagnostics = summary["diagnostics"]
    print(
        "divergences from a scripted trace: "
        f"blocked tool calls {len(diagnostics['blocked_calls'])} "
        f"{diagnostics['blocked_calls'] or ''}, "
        f"truncated turns {diagnostics['truncated_turns']}, "
        f"silent turns {diagnostics['silent_turns']}, "
        f"rate-limit retries {diagnostics['rate_limit_retries']}"
    )
    print(
        f"model calls: {diagnostics['model_calls']} "
        f"({diagnostics['recorded']} recorded, {diagnostics['replayed']} replayed)"
    )
    emergent = [r["emergent"] for r in summary["rows"] if r.get("emergent")]
    if emergent:
        print(f"emergent unbacked promises (not BUG-1): {len(emergent)}")
        for line in dict.fromkeys(emergent):
            print(f"  - {line}")
    print()
    print("defect   fired / applicable          selected  n/a  controls with no unexpected finding")
    for bug, counts in summary["rates"].items():
        applicable = counts["applicable"]
        rate = f"{counts['fired']}/{applicable}"
        if applicable:
            rate += f" ({100.0 * counts['fired'] / applicable:.1f}%)"
        print(
            f"{bug:<8} {rate:<27} {counts['selected']:<9} "
            f"{counts['selected'] - applicable:<4} "
            f"{counts['controls_clean']}/{counts['controls']}"
        )
    print()
    print(
        "fired/applicable is the rate. n/a counts conversations where the "
        "detector's preconditions never occurred — the model took a different "
        "route — and those are excluded rather than counted clean."
    )


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
