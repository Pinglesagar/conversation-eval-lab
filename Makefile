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
.PHONY: help start python-ok install gate test coverage demo calibrate report validate replay errors reference live-replay live-score live-record audio-fixtures audio-check audio-suite audio-suite-plan audio-suite-record audio-suite-evidence audio-setup transport-report transport-record roleplay-demo roleplay-validate advisory-verdicts spoken-replay spoken-record ragcheck clean

# The on-ramp, and the first thing anybody should type. One finding, recomputed
# on this machine from the committed spoken call, printed in a screen, plus what
# to read next. It is not a tour and it is deliberately not a summary of the
# repository: a newcomer handed thirty-two targets and seventeen documents reads
# none of them, so this shows one result and then gets out of the way.
start: python-ok  ## start: The on-ramp: one finding, recomputed offline.
	@$(PYTHON) -m scripts.start

# `make help`, grouped — presentation only.
#
# Thirty-two targets printed as one flat alphabetical list is the state this
# replaced, and it had a real cost: `demo` and `audio-suite-record` looked
# equally routine, and one of those two spends money at a vendor. So the list is
# bucketed by what a reader is trying to do, and the four targets that bill for
# something are under a heading that says so and carry a marker of their own.
#
# The grouping lives in the `## <group>: <description>` comment on each target
# rather than in a list here, so a target added later cannot quietly go missing
# from this screen — it appears under its group, or under UNGROUPED, which is
# the visible failure that a silently-dropped line would not be.
#
# Every target name is unchanged. Nothing that CI or a script references moved.
# Note: no `#` may appear in HELP_GROUPS — make truncates a variable assignment
# at the first one, and the label silently loses its tail.
HELP_GROUPS := start:Start here|every:Everyday|evidence:Evidence — committed runs, offline, no keys|record:Recording — the only group where anything spends money or needs a key|maint:Maintenance|:UNGROUPED — this target needs a group prefix on its help comment

help:  ## start: Show this help, grouped.
	@grep -hE '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) \
	| awk -v groups='$(HELP_GROUPS)' 'BEGIN { FS = "## " ; n = split(groups, G, "|") } \
	  { name = $$1 ; sub(/:.*/, "", name) ; \
	    rest = $$2 ; key = "" ; \
	    if (match(rest, /^[a-z]+: /)) { key = substr(rest, 1, RLENGTH - 2) ; \
	                                    rest = substr(rest, RLENGTH + 1) } \
	    D[key] = D[key] sprintf("    \033[36m%-21s\033[0m %s\n", name, rest) } \
	  END { printf "\n" ; \
	        for (i = 1; i <= n; i++) { split(G[i], p, ":") ; \
	          if (D[p[1]] != "") printf "  \033[1m%s\033[0m\n%s\n", p[2], D[p[1]] } \
	        printf "  MONEY+KEY = spends at a vendor and refuses without the named keys.\n" ; \
        printf "  Every other target on this screen is offline and free.\n\n" }'

python-ok:
ifneq ($(PY_OK),1)
	@echo "$(if $(PY_HAVE),$(PYTHON) is Python $(PY_HAVE) and,cannot run $(PYTHON);) this package needs Python $(REQUIRED_PYTHON) or newer." >&2
	@echo "Point make at a newer interpreter, e.g.:" >&2
	@echo "    python3.12 -m venv .venv && . .venv/bin/activate && make install" >&2
	@echo "    make test PYTHON=python3.12" >&2
	@exit 1
endif

install: python-ok  ## start: Editable install, with the dev extras.
	$(PIP) install -e ".[dev]"

test: python-ok  ## start: The full offline test suite. No keys.
	$(PYTHON) -m pytest

# The ordered gate: every offline check this repository has, in cost order,
# stopping at the first failure. What each stage proves and — more usefully —
# what it CANNOT catch is docs/GATES.md. What to do when one of them goes red is
# docs/DEBUGGING.md.
#
# The order is cost order, cheapest first, because a mistake should be named in
# milliseconds rather than after a long run that was misleading all the way
# through. Stages 1-7 are fifteen commands and cost 8.4 s in total on the
# machine this was measured on; stage 8, the test suite, costs 73 s on its own.
# That ratio is the whole argument for the ordering: the entire artefact surface
# is cheaper than deciding whether to run it, and the suite is the one thing here
# worth deferring.
#
# Two stages WRITE the committed artefacts and then diff them, exactly as CI
# does: stage 4 rewrites fixtures/replay_run and stage 5 rewrites
# lab/judges/..., and the `git diff --exit-code` after each is the assertion.
# Run this on a tree that holds your change and nothing else, or the diff will
# report somebody else's work in progress as your regression.
#
# NOT in this gate: anything that needs a key, spends a character or opens a
# socket. Nor `make coverage` (134 s, and deliberately not a threshold) or
# `make audio-suite`, which is a subset of stage 8. And note what no offline
# stage can see: **replay is blind to a prompt change**. If your diff touches a
# prompt, a persona or a rubric, a green gate here is necessary and not
# sufficient — docs/GATES.md says which live tier answers that question instead.
gate: python-ok  ## every: Every offline check, cheapest first, stops at red.
	@echo "== 1/8  lint: syntax errors and undefined names =="
	@if $(PYTHON) -m ruff --version >/dev/null 2>&1; then \
		$(PYTHON) -m ruff check --select E9,F63,F7,F82 --exclude .venv . ; \
	else \
		echo "   ruff is not installed here, so this stage is skipped."; \
		echo "   CI installs it; run 'pip install ruff' to have it locally."; \
	fi
	@echo "== 2/8  the two corpora, against their schemas =="
	$(PYTHON) -m lab.cli validate --coverage
	$(PYTHON) -m roleplay.corpus --coverage --list
	@echo "== 3/8  the committed traces, re-checked with no agent and no runner =="
	$(PYTHON) -m lab.cli replay --failures-only
	@echo "== 4/8  the case study, against its baseline, then byte for byte =="
	$(PYTHON) -m lab.cli run --replay --ci --out fixtures/replay_run
	git diff --exit-code -- fixtures/replay_run
	@echo "== 5/8  the calibration gates, then the artefacts they wrote =="
	@echo "        (this diff and the one above read the WORKING TREE: uncommitted"
	@echo "         work of your own under fixtures/ or lab/judges/ shows up here.)"
	$(PYTHON) -m lab.cli calibrate --ci
	git diff --exit-code -- fixtures lab/judges
	@echo "== 6/8  the error analysis still agrees with the artefacts =="
	$(PYTHON) -m error_analysis.pareto --check --no-chart
	@echo "== 7/8  the other packs and the recorded tiers, all offline =="
	$(PYTHON) -m roleplay.demo
	$(PYTHON) -m roleplay.regime_eval --divergence --shadow
	$(PYTHON) -m ragcheck
	$(PYTHON) -m roleplay.spoken
	$(PYTHON) -m lab.voice.transport.report --out reports/transport_report.md
	$(PYTHON) -m lab.cli run -k 3 --live-agent --live-caller --live-judge \
		--out reports/live --no-traces \
		--baseline fixtures/live_full/run_report.json --ci
	$(PYTHON) -m tablemate --score fixtures/live_full
	$(PYTHON) -m scripts.make_audio_fixtures --check
	@echo "== 8/8  the offline suite =="
	$(PYTHON) -m pytest -q
	@echo
	@echo "gate: all eight stages passed, and none of them asked a model anything."
	@echo "      If this change touched a prompt, a persona or a rubric, that is"
	@echo "      not yet an answer: see docs/GATES.md, stage 9."

# Line and branch coverage over all seven packages, printed twice on purpose.
#
# The first figure is the whole tree. The second omits the five recording scripts
# that need vendor keys and spend money, which no offline run can execute — so
# the difference between the two figures is exactly "code that cannot be covered
# without a bill". Printing both is the only version of this number that carries
# its own denominator, which is the rule everything else here follows.
#
# Deliberately NOT a CI gate and deliberately NOT a threshold. A coverage floor
# fails for reasons that have nothing to do with the change in front of it, and a
# repository whose argument is that instruments must be measured before they are
# trusted should not adopt one it has not calibrated. What line coverage cannot
# tell you is in README.md and WIKI §10.4, next to the number.
coverage: python-ok  ## every: Coverage, twice: whole tree, then offline-executable.
	$(PYTHON) -m pytest -q --cov --cov-report=term:skip-covered
	@echo
	@echo "== omitting the five key-requiring recording scripts =="
	@$(PYTHON) -m coverage report \
	  --omit="scripts/make_audio_fixtures.py,scripts/make_audio_suite_fixtures.py,scripts/make_cloud_fixtures.py,scripts/make_transport_fixtures.py,scripts/run_audio_live.py" \
	  | grep TOTAL

calibrate: python-ok  ## every: The timing and judge calibration gates.
	$(PYTHON) -m lab.cli calibrate

validate: python-ok  ## every: Validate the scenario corpus, with coverage.
	$(PYTHON) -m lab.cli validate --coverage

audio-fixtures: python-ok  ## record: local TTS  Re-record fixtures/audio. No key, no spend.
	$(PYTHON) -m scripts.make_audio_fixtures

audio-check: python-ok  ## evidence: Replay the committed audio fixtures; fail on drift.
	$(PYTHON) -m scripts.make_audio_fixtures --check

audio-suite: python-ok  ## evidence: The 18-row audio tier, in process.
	$(PYTHON) -m pytest tests/test_audio_suite.py -q

audio-suite-plan: python-ok  ## record: free       What re-recording the tier would cost.
	$(PYTHON) -m scripts.make_audio_suite_fixtures --dry-run

audio-suite-record: python-ok  ## record: MONEY+KEY  Re-record the audio tier. Needs both keys.
	$(PYTHON) -m scripts.make_audio_suite_fixtures

audio-suite-evidence: python-ok  ## evidence: Re-derive the tier's evidence from the cassette.
	$(PYTHON) -m scripts.make_audio_suite_fixtures --evidence-only

audio-setup:  ## maint: Install the local speech engines (shows the download).
	./scripts/setup_audio.sh

# The demo is the two-minute tour, so it produces every artefact the README
# discusses rather than only the one the runner happens to write: the report, the
# handoff heatmap, and the Pareto chart of hand-coded failure modes. The two PNGs
# need matplotlib, which `[dev]` installs; without it both steps print what is
# missing and carry on, and the tables they annotate are printed either way.
demo: python-ok  ## every: The case study end to end, into reports/.
	$(PYTHON) -m lab.cli run --out reports --heatmap reports/handoff_heatmap.png
	@echo
	$(PYTHON) -m error_analysis.pareto --out reports/pareto.png

report: python-ok  ## evidence: Re-render the committed report from its own JSON.
	$(PYTHON) -m lab.cli report

replay: python-ok  ## every: Re-check every committed trace, no agent involved.
	$(PYTHON) -m lab.cli replay --failures-only

errors: python-ok  ## maint: Recount the coded failure modes, redraw the chart.
	$(PYTHON) -m error_analysis.pareto --check

reference: python-ok  ## maint: Regenerate the committed baseline; review the diff.
	$(PYTHON) -m lab.cli run --out fixtures/replay_run
	@git --no-pager diff --stat -- fixtures/replay_run

# The live run replays committed recordings: no key, no network, no spend. It is a
# separate target from `demo` because it is a different *build* of the system under
# test — a model in the decision seat rather than `tablemate/agents.py` — and it is
# gated against its own baseline for that reason. A live run diffed against the
# scripted baseline would report the difference between two builds as a regression.
live-replay: python-ok  ## evidence: Replay the committed live run: models in all 3 seats.
	$(PYTHON) -m lab.cli run -k 3 --live-agent --live-caller --live-judge \
		--out reports/live --no-traces \
		--baseline fixtures/live_full/run_report.json --ci

live-score: python-ok  ## evidence: Recompute the seeded-defect rates from live traces.
	$(PYTHON) -m tablemate --score fixtures/live_full

# Re-recording spends money and needs LAB_LIVE_AGENT / LAB_LIVE_CALLER /
# LAB_LIVE_JUDGE plus a provider key. It draws new samples, so it produces a
# *different* report — review the diff as a new measurement, not as a regression.
live-record: python-ok  ## record: MONEY+KEY  Draw a new live run. Needs LAB_LIVE_*.
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
transport-report: python-ok  ## evidence: Recompute the WebRTC tier from its recordings.
	$(PYTHON) -m lab.voice.transport.report --out reports/transport_report.md

transport-record: python-ok  ## record: MONEY+KEY  Record live rooms. Needs the LiveKit vars.
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
ragcheck: python-ok  ## evidence: Retrieval + groundedness, scored and never averaged.
	$(PYTHON) -m ragcheck

roleplay-demo: python-ok  ## evidence: The advisory pack: contracts, consistency, calibration.
	$(PYTHON) -m roleplay.demo

roleplay-validate: python-ok  ## evidence: Validate the roleplay corpus, with coverage.
	$(PYTHON) -m roleplay.corpus --coverage --list

# The advisory pack's verdicts, computed from the cited registers rather than read
# off the hand labels: one run per row through the roleplay adapter, then every
# entry in the regime's register decided against the trace. Prints its own
# limitations block first, because the agreement figure it ends with is in-sample
# and a reader needs that next to the number rather than in a document they may
# not open. Zero API keys, like everything else here.
advisory-verdicts: python-ok  ## evidence: The 18 advisory rows, decided from the registers.
	$(PYTHON) -m roleplay.regime_eval --divergence --shadow

cited-calls: python-ok  ## evidence: Both recorded calls graded against the cited scorecard.
	$(PYTHON) -m roleplay.scorecard_eval fixtures/audio/spoken_call/trace.jsonl fixtures/audio/spoken_call_pass/trace.jsonl

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
spoken-replay: python-ok  ## evidence: Replay the committed spoken call and re-grade it.
	$(PYTHON) -m roleplay.spoken

spoken-record: python-ok  ## record: MONEY+KEY  New spoken call. Needs LAB_LIVE_SPOKEN.
	$(PYTHON) -m roleplay.spoken --record
	@git --no-pager diff --stat -- fixtures/audio/spoken_call

clean:  ## maint: Remove caches and build output.
	rm -rf build dist .pytest_cache .coverage htmlcov *.egg-info
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
