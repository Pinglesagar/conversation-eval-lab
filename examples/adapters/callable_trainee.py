"""A trainee around any in-process Python function: the pattern for an agent you can import.

The function receives the conversation so far as ("customer" | "adviser", text) pairs —
empty on the opening turn — and returns the adviser's next line, or None to stop.

    LAB_TRAINEE_FACTORY=examples.adapters.callable_trainee:build_trainee \\
        python -m roleplay.live --scripted-customer --only eu --root /tmp/fn_run

To point this at a real system: pass your own function (or a `functools.partial` around
your agent's method) instead of `sample_agent` in `build_trainee`. An agent that keeps
its own history can ignore the argument and read only its last entry.
"""

from __future__ import annotations

from typing import Callable

Agent = Callable[[list[tuple[str, str]]], "str | None"]


def sample_agent(history: list[tuple[str, str]]) -> str | None:
    """Stands in for your agent: two discovery questions, then it is done."""
    asked = sum(1 for role, _ in history if role == "adviser")
    if asked == 0:
        return "Thanks for coming in. What would you want this money to do for you over the next few years?"
    if asked == 1:
        return "And how much of it would you need to reach at short notice?"
    return None


class CallableTrainee:
    def __init__(self, agent: Agent) -> None:
        self.agent, self.history = agent, []
        self.stop_reason: str | None = None

    def open(self) -> str | None:
        return self._next()

    def reply(self, customer_turn: str) -> str | None:
        self.history.append(("customer", customer_turn))
        return self._next()

    def _next(self) -> str | None:
        text = self.agent(list(self.history))
        if not text:
            self.stop_reason = "agent_ended"
            return None
        self.history.append(("adviser", text))
        return text

    def __repr__(self) -> str:  # recorded as `trainee_source` in the trace's session_start
        return f"CallableTrainee({getattr(self.agent, '__name__', repr(self.agent))})"


def build_trainee(context) -> CallableTrainee:  # noqa: ANN001  (a roleplay.live.TraineeContext)
    return CallableTrainee(sample_agent)
