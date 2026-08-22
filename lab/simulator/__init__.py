"""Scenario running: drive an agent, produce a trace, repeat it k times.

WHAT THIS DEMONSTRATES
----------------------
The simulated caller is part of the instrument, not part of the test data. It
holds a persona and a goal, decides what to say next, and emits caller-side trace
events at the measurement boundary that `lab.voice.calibration` validates — the
same boundary discipline, in the code path real runs actually take.

    lab.simulator.persona   Persona and Goal as declared, YAML-loadable data.
                            The disclosure model (`on_request_only`) is what
                            turns a scenario from a script into a probe.
    lab.simulator.driver    `AgentUnderTest` (a callable — nothing to subclass),
                            `ScriptedCaller` / `LLMCaller`, and `run_scenario`,
                            whose only output is a `Trace`.
    lab.simulator.passk     Run a scenario k times and score it STABLE_PASS /
                            STABLE_FAIL / FLAKY. FLAKY is not a pass.

The one hard rule across all three: no result is reported that cannot be
recomputed from the trace on disk.
"""

from lab.simulator.driver import (
    DEFAULT_MAX_TURNS,
    LIVE_CALLER_ENV_VAR,
    AgentReply,
    AgentTurn,
    AgentUnderTest,
    Caller,
    Handoff,
    LLMCaller,
    ScriptedCaller,
    ToolInvocation,
    coerce_turn,
    run_scenario,
)
from lab.simulator.persona import (
    END_OF_CALL,
    RELUCTANT_BELOW,
    CallerProfile,
    Goal,
    Persona,
    Verbosity,
)
from lab.simulator.passk import (
    PassKPolicy,
    RunOutcome,
    Stability,
    StabilitySummary,
    StabilityVerdict,
    coerce_outcome,
    format_rate,
    run_pass_k,
    summarise_stability,
    verdict_from_outcomes,
)

__all__ = [
    "AgentReply",
    "AgentTurn",
    "AgentUnderTest",
    "Caller",
    "CallerProfile",
    "DEFAULT_MAX_TURNS",
    "END_OF_CALL",
    "Goal",
    "Handoff",
    "LIVE_CALLER_ENV_VAR",
    "LLMCaller",
    "PassKPolicy",
    "Persona",
    "RELUCTANT_BELOW",
    "RunOutcome",
    "ScriptedCaller",
    "Stability",
    "StabilitySummary",
    "StabilityVerdict",
    "ToolInvocation",
    "Verbosity",
    "coerce_outcome",
    "coerce_turn",
    "format_rate",
    "run_pass_k",
    "run_scenario",
    "summarise_stability",
    "verdict_from_outcomes",
]
