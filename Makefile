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
.PHONY: help python-ok install test demo calibrate report validate replay errors reference live-replay live-score live-record audio-fixtures audio-check audio-suite audio-suite-plan audio-suite-record audio-suite-evidence audio-setup transport-report transport-record roleplay-demo roleplay-validate advisory-verdicts spoken-replay spoken-record ragcheck clean

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

audio-suite: python-ok  ## Run the 18-row in-process audio tier offline (no keys, no network).
	$(PYTHON) -m pytest tests/test_audio_suite.py -q

audio-suite-plan: python-ok  ## Print what re-recording the audio tier would cost, and spend nothing.
	$(PYTHON) -m scripts.make_audio_suite_fixtures --dry-run

audio-suite-record: python-ok  ## Re-record the audio tier (needs LAB_LIVE_TTS, LAB_LIVE_STT and both keys).
	$(PYTHON) -m scripts.make_audio_suite_fixtures

audio-suite-evidence: python-ok  ## Re-derive the tier's evidence file from the committed cassette. No keys.
	$(PYTHON) -m scripts.make_audio_suite_fixtures --evidence-only

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

# The live run replays committed recordings: no key, no network, no spend. It is a
# separate target from `demo` because it is a different *build* of the system under
# test — a model in the decision seat rather than `tablemate/agents.py` — and it is
# gated against its own baseline for that reason. A live run diffed against the
# scripted baseline would report the difference between two builds as a regression.
live-replay: python-ok  ## Replay the committed live run (agent, caller and judge were models). No key needed.
	$(PYTHON) -m lab.cli run -k 3 --live-agent --live-caller --live-judge \
		--out reports/live --no-traces \
		--baseline fixtures/live_full/run_report.json --ci

live-score: python-ok  ## Recompute the seeded-defect rates from the committed live traces.
	$(PYTHON) -m tablemate --score fixtures/live_full

# Re-recording spends money and needs LAB_LIVE_AGENT / LAB_LIVE_CALLER /
# LAB_LIVE_JUDGE plus a provider key. It draws new samples, so it produces a
# *different* report — review the diff as a new measurement, not as a regression.
live-record: python-ok  ## Draw a new live run from a provider. Spends money; needs the LAB_LIVE_* variables.
	$(PYTHON) -m lab.cli run -k 3 --live-agent --live-caller --live-judge --record \
		--out fixtures/live_full --baseline fixtures/live_full/run_report.json
	@git --no-pager diff --stat -- fixtures/live_full

# The WebRTC transport tier. Three rows, because three things only exist in
# transport; everything else in this harness runs in process.
#
# `transport-report` is offline and needs no key: it recomputes every figure from
# the committed recordings, which is the whole point of the split between
# recording a live session and measuring one. `transport-record` opens real rooms
# and is the only way to produce new recordings — it needs LAB_LIVE_TRANSPORT, the
# three LiveKit variables and `pip install -e ".[transport]"`. It spends no
# synthesis characters at all: the tier publishes a clip this repository already
# committed.
#
# Neither target gates a build. A network test that blocks a merge trains people
# to bypass the gate, so the tier reports and the offline suite gates.
transport-report: python-ok  ## Recompute the transport tier from its committed recordings. No key needed.
	$(PYTHON) -m lab.voice.transport.report --out reports/transport_report.md

transport-record: python-ok  ## Record new live WebRTC sessions. Needs LAB_LIVE_TRANSPORT + the LiveKit variables.
	$(PYTHON) -m scripts.make_transport_fixtures
	@git --no-pager diff --stat -- fixtures/audio/transport

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
ragcheck: python-ok  ## Retrieval + groundedness: recall@k, MRR, nDCG, and per-claim faithfulness.
	$(PYTHON) -m ragcheck

roleplay-demo: python-ok  ## Run the BFSI sales-roleplay pack: contracts, score consistency, scorer calibration.
	$(PYTHON) -m roleplay.demo

roleplay-validate: python-ok  ## Validate the roleplay corpus against its schema, with coverage.
	$(PYTHON) -m roleplay.corpus --coverage --list

# The advisory pack's verdicts, computed from the cited registers rather than read
# off the hand labels: one run per row through the roleplay adapter, then every
# entry in the regime's register decided against the trace. Prints its own
# limitations block first, because the agreement figure it ends with is in-sample
# and a reader needs that next to the number rather than in a document they may
# not open. Zero API keys, like everything else here.
advisory-verdicts: python-ok  ## Compute the 18 advisory rows' regime verdicts from the registers.
	$(PYTHON) -m roleplay.regime_eval --divergence --shadow

# The spoken call: the one place the audio tier and the conversation tier meet.
# Every other audio entry point above scores single utterances; this one runs a
# whole advisory conversation turn by turn through real synthesis and real
# recognition, and grades what the recogniser HEARD.
#
# `spoken-replay` is offline and needs no key. It does not read a summary back:
# the committed per-turn manifest drives the same conversation loop again, so the
# trace, the disclosure register and the deterministic score are recomputed, and
# the live scorer's answer replays from a recording held to its prompt digest.
#
# `spoken-record` is the only way to produce new recordings and it spends real
# ElevenLabs characters. It needs LAB_LIVE_SPOKEN=1, both audio keys, a provider
# key and the three model routes, and it refuses with all of the missing pieces
# named at once. Synthesis is digest-cached, so re-recording an unchanged call
# bills nothing.
spoken-replay: python-ok  ## Replay the committed spoken call and re-grade it. No keys, no spend.
	$(PYTHON) -m roleplay.spoken

spoken-record: python-ok  ## Record a new spoken call. SPENDS ElevenLabs characters; needs LAB_LIVE_SPOKEN.
	$(PYTHON) -m roleplay.spoken --record
	@git --no-pager diff --stat -- fixtures/audio/spoken_call

clean:  ## Remove caches and build output.
	rm -rf build dist .pytest_cache .coverage htmlcov *.egg-info
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
