# tablemate-evals — developer entry points.
#
# Every target here must work with ZERO API keys. Anything that talks to a live
# provider is opt-in behind an environment variable and has a recorded fixture
# that replays deterministically.

PYTHON ?= python3
PIP    ?= $(PYTHON) -m pip

.DEFAULT_GOAL := help
.PHONY: help install test demo calibrate report clean

help:  ## Show this help.
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install the package plus dev extras in editable mode.
	$(PIP) install -e ".[dev]"

test:  ## Run the full offline test suite.
	$(PYTHON) -m pytest

calibrate:  ## Run the timing calibration gate; writes fixtures/calibration_report.{json,md}.
	$(PYTHON) -m lab.voice.calibration --out fixtures

demo:  ## Run the TableMate case study end to end against recorded fixtures.
	@echo "demo: the TableMate system under test and the scenario runner are not part of"
	@echo "      the foundation commit. This target is wired up in the step that adds"
	@echo "      tablemate/ and lab/simulator/."
	@exit 1

report:  ## Build the evaluation report from the latest run.
	@echo "report: lab/report/ is a placeholder package in the foundation commit."
	@echo "        This target is wired up in the step that adds report rendering."
	@exit 1

clean:  ## Remove caches and build output.
	rm -rf build dist .pytest_cache .coverage htmlcov *.egg-info
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
