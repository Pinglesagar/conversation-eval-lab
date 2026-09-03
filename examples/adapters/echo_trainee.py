"""The smallest possible trainee: proves the two-method contract and nothing else.

Run it through the full pipeline, offline, with no key:

    LAB_TRAINEE_FACTORY=examples.adapters.echo_trainee:build_trainee \\
        python -m roleplay.live --scripted-customer --only eu --root /tmp/echo_run

To point this at a real system: replace the body of `reply()` with a call into your
agent and return its text; return None when it has finished and set `stop_reason` to
say why. The register, the persona, the scorecard and the trace run unchanged.
"""

from __future__ import annotations


class EchoTrainee:
    """Opens with a fixed line, repeats the customer back, then stops."""

    def __init__(self, *, turns: int = 3) -> None:
        self.turns, self.said = turns, 0
        self.stop_reason: str | None = None  # read by the harness after the session

    def open(self) -> str | None:
        self.said += 1
        return "Good morning. Before we look at anything, what would you want this money to do for you?"

    def reply(self, customer_turn: str) -> str | None:
        if self.said >= self.turns:
            self.stop_reason = "echo_budget"  # lands in the trace's session_end
            return None
        self.said += 1
        return f"I hear you: {customer_turn.strip()} Tell me more about that."

    def __repr__(self) -> str:  # recorded as `trainee_source` in the trace's session_start
        return f"EchoTrainee(turns={self.turns})"


def build_trainee(context) -> EchoTrainee:  # noqa: ANN001  (a roleplay.live.TraineeContext)
    """Called once per session. `context` carries the scenario id, the customer
    profile, the competence, the jurisdiction, the language and the turn budget."""
    return EchoTrainee(turns=min(3, context.max_turns))
