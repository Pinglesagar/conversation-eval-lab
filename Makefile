# tablemate-evals — developer entry points.
#
# Every target here must work with ZERO API keys. Anything that talks to a live
# provider is opt-in behind an environment variable and has a recorded fixture
# that replays deterministically.

PYTHON ?= python3
PIP    ?= $(PYTHON) -m pip

# The interpreter check, before anything else can fail obscurely because of it.
#
# `python3` on a stock macOS is 3.9, and this package needs 3.12. Without this
# guard the two entry points a newcomer types first both mislead: `make install`
# reports that setup.py is missing (an ancient pip on an unsupported Python, not
# a packaging problem), and `make test` collapses into a wall of
# `TypeError: unsupported operand type(s)` from PEP 604 annotations. Neither
# names the actual cause. So every target that runs Python depends on `python-ok`
# and stops with a sentence a person can act on.
REQUIRED_PYTHON := 3.12
PY_OK := $(shell $(PYTHON) -c 'import sys; print(1 if sys.version_info[:2] >= (3, 12) else 0)' 2>/dev/null)
PY_HAVE := $(shell $(PYTHON) -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)

.DEFAULT_GOAL := help
.PHONY: help python-ok install test demo calibrate report validate replay errors reference audio-fixtures audio-check audio-setup roleplay-demo roleplay-validate clean

help:  ## Show this help.
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

python-ok:
ifneq ($(PY_OK),1)
	@echo "$(if $(PY_HAVE),$(PYTHON) is Python $(PY_HAVE) and,cannot run $(PYTHON);) this package needs Python $(REQUIRED_PYTHON) or newer." >&2
	@echo "Point make at a newer interpreter, e.g.:" >&2
	@echo "    python3.12 -m venv .venv && . .venv/bin/activate && make install" >&2
	@echo "    make test PYTHON=python3.12" >&2
	@exit 1
endif

install: python-ok  ## Install the package plus dev extras in editable mode.
	$(PIP) install -e ".[dev]"

test: python-ok  ## Run the full offline test suite.
	$(PYTHON) -m pytest

calibrate: python-ok  ## Run the timing and judge calibration gates; non-zero if either fails.
	$(PYTHON) -m lab.cli calibrate

validate: python-ok  ## Validate the scenario corpus against its schema, with coverage.
	$(PYTHON) -m lab.cli validate --coverage

audio-fixtures: python-ok  ## Re-record fixtures/audio from real speech (needs a TTS engine; macOS `say` by default).
	$(PYTHON) -m scripts.make_audio_fixtures

audio-check: python-ok  ## Replay the committed audio fixtures and fail if they no longer match.
	$(PYTHON) -m scripts.make_audio_fixtures --check

audio-setup:  ## Show what the local speech engines would download, then install them.
	./scripts/setup_audio.sh

# The demo is the two-minute tour, so it produces every artefact the README
# discusses rather than only the one the runner happens to write: the report, the
# handoff heatmap, and the Pareto chart of hand-coded failure modes. The two PNGs
# need matplotlib, which `[dev]` installs; without it both steps print what is
# missing and carry on, and the tables they annotate are printed either way.
demo: python-ok  ## Run the case study end to end against the recorded fixtures, into reports/.
	$(PYTHON) -m lab.cli run --out reports --heatmap reports/handoff_heatmap.png
	@echo
	$(PYTHON) -m error_analysis.pareto --out reports/pareto.png

report: python-ok  ## Re-render the committed reference report from its own JSON.
	$(PYTHON) -m lab.cli report

replay: python-ok  ## Re-check every committed trace with no agent and no runner involved.
	$(PYTHON) -m lab.cli replay --failures-only

errors: python-ok  ## Recount the hand-assigned failure modes and redraw error_analysis/pareto.png.
	$(PYTHON) -m error_analysis.pareto --check

reference: python-ok  ## Regenerate the committed reference run. Review the diff before committing it.
	$(PYTHON) -m lab.cli run --out fixtures/replay_run
	@git --no-pager diff --stat -- fixtures/replay_run

# The roleplay pack is a second domain on the same framework: a BFSI sales-coach
# whose scorer is the system under test. It shares `lab/` and nothing else, which
# is the point of it, so it gets its own entry points rather than being folded
# into `demo` — a target that ran both would make it impossible to tell which
# domain a failure came from.
#
# `roleplay-demo` prints red findings and exits zero. Those are two different
# verdicts: the product under test has three real defects and the run reports all
# of them, while the exit code says only whether anything moved since the last
# review. See docs/ADVISORY_DEMO.md.
roleplay-demo: python-ok  ## Run the BFSI sales-roleplay pack: contracts, score consistency, scorer calibration.
	$(PYTHON) -m roleplay.demo

roleplay-validate: python-ok  ## Validate the roleplay corpus against its schema, with coverage.
	$(PYTHON) -m roleplay.corpus --coverage --list

clean:  ## Remove caches and build output.
	rm -rf build dist .pytest_cache .coverage htmlcov *.egg-info
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
