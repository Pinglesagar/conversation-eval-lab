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
    lab.simulator.flake_band
                            pass^k with a *live* caller against the scripted
                            agent — the first time the machinery met real
                            variance. Recorded conversation by conversation, so
                            the number replays offline with no key.

The one hard rule across all three: no result is reported that cannot be
recomputed from the trace on disk.
"""

from lab.simulator.driver import (
    CALLER_MODEL_ENV_VAR,
    DEFAULT_MAX_TURNS,
    LIVE_CALLER_ENV_VAR,
    REPEAT_LIMIT,
    VERBOSITY_TOKEN_BUDGET,
    AgentReply,
    AgentTurn,
    AgentUnderTest,
    Caller,
    CassetteKey,
    DisclosureLeak,
    DisclosureLeakError,
    Handoff,
    LLMCaller,
    OnLeak,
    ScriptedCaller,
    ToolInvocation,
    coerce_turn,
    run_scenario,
)
from lab.simulator.persona import (
    CALLER_RULES,
    END_OF_CALL,
    RELUCTANT_BELOW,
    VOLUNTEERS_AT_OR_ABOVE,
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
    "CALLER_MODEL_ENV_VAR",
    "CALLER_RULES",
    "Caller",
    "CallerProfile",
    "CassetteKey",
    "DEFAULT_MAX_TURNS",
    "DisclosureLeak",
    "DisclosureLeakError",
    "END_OF_CALL",
    "Goal",
    "Handoff",
    "LIVE_CALLER_ENV_VAR",
    "LLMCaller",
    "OnLeak",
    "PassKPolicy",
    "Persona",
    "REPEAT_LIMIT",
    "RELUCTANT_BELOW",
    "RunOutcome",
    "ScriptedCaller",
    "Stability",
    "StabilitySummary",
    "StabilityVerdict",
    "ToolInvocation",
    "VERBOSITY_TOKEN_BUDGET",
    "VOLUNTEERS_AT_OR_ABOVE",
    "Verbosity",
    "coerce_outcome",
    "coerce_turn",
    "format_rate",
    "run_pass_k",
    "run_scenario",
    "summarise_stability",
    "verdict_from_outcomes",
]
