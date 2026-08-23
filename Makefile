# tablemate-evals — developer entry points.
#
# Every target here must work with ZERO API keys. Anything that talks to a live
# provider is opt-in behind an environment variable and has a recorded fixture
# that replays deterministically.

PYTHON ?= python3
PIP    ?= $(PYTHON) -m pip

.DEFAULT_GOAL := help
.PHONY: help install test demo calibrate report validate replay errors reference clean

help:  ## Show this help.
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install the package plus dev extras in editable mode.
	$(PIP) install -e ".[dev]"

test:  ## Run the full offline test suite.
	$(PYTHON) -m pytest

calibrate:  ## Run the timing and judge calibration gates; non-zero if either fails.
	$(PYTHON) -m lab.cli calibrate

validate:  ## Validate the scenario corpus against its schema, with coverage.
	$(PYTHON) -m lab.cli validate --coverage

demo:  ## Run the case study end to end against the recorded fixtures, into reports/.
	$(PYTHON) -m lab.cli run --out reports

report:  ## Re-render the committed reference report from its own JSON.
	$(PYTHON) -m lab.cli report

replay:  ## Re-check every committed trace with no agent and no runner involved.
	$(PYTHON) -m lab.cli replay --failures-only

errors:  ## Recount the hand-assigned failure modes and redraw error_analysis/pareto.png.
	$(PYTHON) -m error_analysis.pareto --check

reference:  ## Regenerate the committed reference run. Review the diff before committing it.
	$(PYTHON) -m lab.cli run --out fixtures/replay_run
	@git --no-pager diff --stat -- fixtures/replay_run

clean:  ## Remove caches and build output.
	rm -rf build dist .pytest_cache .coverage htmlcov *.egg-info
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
