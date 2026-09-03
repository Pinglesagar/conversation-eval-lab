"""A trainee behind any HTTP endpoint. Standard library only.

Protocol (edit `_post` if your service speaks a different JSON shape or needs auth):
    POST <url>  {"event": "open",  "scenario_id": "..."}               -> {"reply": "..."}
    POST <url>  {"event": "reply", "customer_turn": "...", "turn": n}  -> {"reply": "..."}
A missing, null or empty "reply" ends the session with stop_reason "agent_ended".

    TRAINEE_HTTP_URL=http://127.0.0.1:8080/reply \\
    LAB_TRAINEE_FACTORY=examples.adapters.http_trainee:build_trainee \\
        python -m roleplay.live --scripted-customer --only eu --root /tmp/http_run

To point this at a real system: set TRAINEE_HTTP_URL. Nothing else needs to change.
"""

from __future__ import annotations

import json
import os
import urllib.request


class HttpTrainee:
    def __init__(self, url: str, *, scenario_id: str, timeout_s: float = 30.0) -> None:
        self.url, self.scenario_id, self.timeout_s, self.turn = url, scenario_id, timeout_s, 0
        self.stop_reason: str | None = None

    def open(self) -> str | None:
        return self._post({"event": "open", "scenario_id": self.scenario_id})

    def reply(self, customer_turn: str) -> str | None:
        self.turn += 1
        return self._post({"event": "reply", "customer_turn": customer_turn, "turn": self.turn})

    def _post(self, payload: dict) -> str | None:
        request = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            text = (json.loads(response.read().decode("utf-8")).get("reply") or "").strip()
        if not text:
            self.stop_reason = "agent_ended"
        return text or None

    def __repr__(self) -> str:  # recorded as `trainee_source` in the trace's session_start
        return f"HttpTrainee(url={self.url!r})"


def build_trainee(context) -> HttpTrainee:  # noqa: ANN001  (a roleplay.live.TraineeContext)
    url = os.environ.get("TRAINEE_HTTP_URL", "http://127.0.0.1:8080/reply")
    return HttpTrainee(url, scenario_id=context.scenario_id)
